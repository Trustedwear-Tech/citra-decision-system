# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""
SAP / OData Connector
=====================
Backs ``DatasetKind.odata`` for SAP S/4HANA, S/4HANA Cloud, ECC + SAP
Gateway, and any OData v2 / v4 service. Implements the full MCP catalogue
contract (list / describe / sample / read / write).

Credential model
----------------
Like ``sql_connector``, credentials live ONLY in env vars referenced via
``connection.env_prefix``::

    type:        sap_odata
    env_prefix:  SAP_S4
    auth_type:   basic | oauth_cc        # default basic
    service_root: /sap/opu/odata/sap/API_BUSINESS_PARTNER  # optional
    odata_version: "4"                   # "2" | "4" — default "4"
    requires_csrf: true                  # SAP Gateway requires X-CSRF on writes

Env vars consumed (per env_prefix):

    SAP_S4_BASE_URL       https://my-s4.sap.com[:port]
    SAP_S4_USER           Basic-auth user                  (auth_type=basic)
    SAP_S4_PASS           Basic-auth password              (auth_type=basic)
    SAP_S4_TOKEN_URL      OAuth /token endpoint            (auth_type=oauth_cc)
    SAP_S4_CLIENT_ID      OAuth client_id                  (auth_type=oauth_cc)
    SAP_S4_CLIENT_SECRET  OAuth client_secret              (auth_type=oauth_cc)
    SAP_S4_SCOPE          OAuth scope (optional)

Notes
-----
* Metadata is parsed from ``$metadata`` (EDMX XML) using stdlib
  ``xml.etree.ElementTree`` — no extra deps.
* CSRF: when ``requires_csrf`` is set, writes do a HEAD with
  ``X-CSRF-Token: Fetch`` against ``$metadata`` to grab a token + cookie
  jar before the actual POST/PATCH/DELETE.
* Pagination: follows ``@odata.nextLink`` (v4) or ``__next`` (v2) until
  ``row_limit`` rows are collected.
* Type mapping: Edm.* types collapsed into the catalogue's coarse type
  vocabulary (string / number / boolean / date / timestamp / binary).
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
import urllib.parse
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional, Tuple

import httpx

logger = logging.getLogger(__name__)


_TOKEN_REFRESH_LEEWAY_SECONDS = 60
_TOKEN_CACHE: Dict[str, Dict[str, Any]] = {}
_CSRF_CACHE: Dict[str, Dict[str, Any]] = {}
# Parsed-$metadata cache, keyed by env_prefix, with a TTL so describe calls
# don't re-download + re-parse the (often large) EDMX document every time.
# Mirrors the token-cache pattern above.
_METADATA_CACHE: Dict[str, Dict[str, Any]] = {}
_METADATA_TTL_SECONDS = int(os.getenv("ODATA_METADATA_TTL_SECONDS", "300"))
# NOTE (lower-priority): a module-level reusable httpx client per prefix would
# save per-request TLS/connection setup, but each call site here uses its own
# auth/headers/cookies and short-lived `with httpx.Client(...)` blocks, and a
# pooled client would need explicit lifecycle management (close on shutdown).
# Left as-is intentionally; the metadata cache above removes the hottest path.
# Per-prefix locks prevent thundering-herd refresh — same rationale as
# soql_connector. Multiple coroutines noticing "expired" used to fire N
# parallel /token POSTs at SAP IAS / xsuaa.
_TOKEN_LOCKS_ASYNC: Dict[str, asyncio.Lock] = {}
_TOKEN_LOCKS_SYNC: Dict[str, threading.Lock] = {}
_LOCK_REGISTRY = threading.Lock()


def _lock_async(prefix: str) -> asyncio.Lock:
    if prefix not in _TOKEN_LOCKS_ASYNC:
        with _LOCK_REGISTRY:
            _TOKEN_LOCKS_ASYNC.setdefault(prefix, asyncio.Lock())
    return _TOKEN_LOCKS_ASYNC[prefix]


def _lock_sync(prefix: str) -> threading.Lock:
    if prefix not in _TOKEN_LOCKS_SYNC:
        with _LOCK_REGISTRY:
            _TOKEN_LOCKS_SYNC.setdefault(prefix, threading.Lock())
    return _TOKEN_LOCKS_SYNC[prefix]

# OData EDMX namespaces — v4 first, v2 second; we try both.
_NS_V4 = {
    "edmx": "http://docs.oasis-open.org/odata/ns/edmx",
    "edm": "http://docs.oasis-open.org/odata/ns/edm",
}
_NS_V2 = {
    "edmx": "http://schemas.microsoft.com/ado/2007/06/edmx",
    "edm": "http://schemas.microsoft.com/ado/2008/09/edm",
}


# ---------------------------------------------------------------------------
# Connection helpers — env_prefix driven
# ---------------------------------------------------------------------------


def _env(prefix: str, name: str, default: str = "") -> str:
    return os.getenv(f"{prefix}_{name}", default).strip()


def _resolve_connection(connection: Dict[str, Any]) -> Dict[str, Any]:
    prefix = (connection.get("env_prefix") or "").upper()
    if not prefix:
        raise ValueError("OData connection: env_prefix is required")

    base_url = _env(prefix, "BASE_URL")
    if not base_url:
        raise ValueError(f"OData: {prefix}_BASE_URL is not set")

    auth_type = (connection.get("auth_type") or "basic").lower()
    if auth_type not in {"basic", "oauth_cc"}:
        raise ValueError(f"OData: unsupported auth_type {auth_type!r} (basic | oauth_cc)")

    service_root = (connection.get("service_root") or "").strip()
    full_root = f"{base_url.rstrip('/')}{('/' + service_root.strip('/')) if service_root else ''}"

    out: Dict[str, Any] = {
        "prefix": prefix,
        "base_url": base_url.rstrip("/"),
        "service_root": full_root,
        "auth_type": auth_type,
        "odata_version": str(connection.get("odata_version") or "4"),
        "requires_csrf": bool(connection.get("requires_csrf", False)),
        "user": _env(prefix, "USER"),
        "password": _env(prefix, "PASS"),
        "token_url": _env(prefix, "TOKEN_URL"),
        "client_id": _env(prefix, "CLIENT_ID"),
        "client_secret": _env(prefix, "CLIENT_SECRET"),
        "scope": _env(prefix, "SCOPE"),
    }
    if auth_type == "basic" and not (out["user"] and out["password"]):
        raise ValueError(f"OData basic auth requires {prefix}_USER and {prefix}_PASS")
    if auth_type == "oauth_cc":
        for k in ("token_url", "client_id", "client_secret"):
            if not out[k]:
                raise ValueError(
                    f"OData oauth_cc requires {prefix}_TOKEN_URL / CLIENT_ID / CLIENT_SECRET"
                )
    return out


# ---------------------------------------------------------------------------
# Auth — Basic vs OAuth client_credentials
# ---------------------------------------------------------------------------


def _basic_auth(resolved: Dict[str, Any]) -> Tuple[str, str]:
    return (resolved["user"], resolved["password"])


def _token_is_fresh(cached: Dict[str, Any]) -> bool:
    return bool(cached.get("access_token")) and (
        cached.get("expires_at", 0) - _TOKEN_REFRESH_LEEWAY_SECONDS > time.time()
    )


def _build_oauth_body(resolved: Dict[str, Any]) -> Dict[str, str]:
    body = {
        "grant_type": "client_credentials",
        "client_id": resolved["client_id"],
        "client_secret": resolved["client_secret"],
    }
    if resolved.get("scope"):
        body["scope"] = resolved["scope"]
    return body


def _store_token(resolved: Dict[str, Any], data: Dict[str, Any]) -> str:
    access_token = data.get("access_token")
    if not access_token:
        raise RuntimeError("OData OAuth response had no access_token")
    expires_in = int(data.get("expires_in", 1800))
    _TOKEN_CACHE[resolved["prefix"]] = {
        "access_token": access_token,
        "expires_at": time.time() + expires_in,
    }
    return access_token


async def _get_oauth_token_async(resolved: Dict[str, Any]) -> str:
    cached = _TOKEN_CACHE.get(resolved["prefix"])
    if cached and _token_is_fresh(cached):
        return cached["access_token"]
    async with _lock_async(resolved["prefix"]):
        cached = _TOKEN_CACHE.get(resolved["prefix"])
        if cached and _token_is_fresh(cached):
            return cached["access_token"]
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                resolved["token_url"],
                data=_build_oauth_body(resolved),
                headers={"Accept": "application/json"},
            )
        if resp.status_code != 200:
            raise RuntimeError(f"OData OAuth failed ({resp.status_code}): {resp.text[:300]}")
        return _store_token(resolved, resp.json())


def _get_oauth_token_sync(resolved: Dict[str, Any]) -> str:
    cached = _TOKEN_CACHE.get(resolved["prefix"])
    if cached and _token_is_fresh(cached):
        return cached["access_token"]
    with _lock_sync(resolved["prefix"]):
        cached = _TOKEN_CACHE.get(resolved["prefix"])
        if cached and _token_is_fresh(cached):
            return cached["access_token"]
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(
                resolved["token_url"],
                data=_build_oauth_body(resolved),
                headers={"Accept": "application/json"},
            )
        if resp.status_code != 200:
            raise RuntimeError(f"OData OAuth failed ({resp.status_code}): {resp.text[:300]}")
        return _store_token(resolved, resp.json())


def _client_kwargs_sync(resolved: Dict[str, Any]) -> Dict[str, Any]:
    """Build httpx.Client kwargs honouring the auth scheme."""
    kw: Dict[str, Any] = {"timeout": 60.0}
    if resolved["auth_type"] == "basic":
        kw["auth"] = _basic_auth(resolved)
    return kw


def _async_headers(resolved: Dict[str, Any], extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """Build headers for an async call (Authorization for OAuth, Accept JSON)."""
    h: Dict[str, str] = {"Accept": "application/json"}
    if extra:
        h.update(extra)
    return h


# ---------------------------------------------------------------------------
# CSRF token dance — required by SAP Gateway for write operations
# ---------------------------------------------------------------------------


async def fetch_csrf_token(connection: Dict[str, Any]) -> Tuple[str, Dict[str, str]]:
    """Return ``(token, cookies)`` to use on the next write request.

    SAP Gateway issues the token via a HEAD on any service endpoint when
    the request carries ``X-CSRF-Token: Fetch``. The session cookies that
    came back must be forwarded on the actual write — they're how the
    server links the token to the session.
    """
    resolved = _resolve_connection(connection)
    headers = {"X-CSRF-Token": "Fetch", "Accept": "application/json"}
    if resolved["auth_type"] == "oauth_cc":
        token = await _get_oauth_token_async(resolved)
        headers["Authorization"] = f"Bearer {token}"
    auth = _basic_auth(resolved) if resolved["auth_type"] == "basic" else None

    url = f"{resolved['service_root']}/$metadata"
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.head(url, headers=headers, auth=auth)
    csrf = resp.headers.get("X-CSRF-Token") or resp.headers.get("x-csrf-token") or ""
    cookies = {k: v for k, v in resp.cookies.items()}
    if not csrf:
        raise RuntimeError(
            f"OData CSRF fetch returned no token (status={resp.status_code}). "
            "Confirm SAP Gateway accepts X-CSRF-Token: Fetch on $metadata."
        )
    _CSRF_CACHE[resolved["prefix"]] = {
        "token": csrf,
        "cookies": cookies,
        "fetched_at": time.time(),
    }
    return csrf, cookies


# ---------------------------------------------------------------------------
# $metadata parser (EDMX → list of EntitySets / Properties)
# ---------------------------------------------------------------------------


def _parse_metadata(xml_text: str) -> Dict[str, Any]:
    """Parse EDMX. Returns::

        {
          "entity_types": {<TypeName>: {"properties": [...], "keys": [...], "navs": [...]}},
          "entity_sets":  [{"name": str, "type": str}, ...]
        }
    """
    root = ET.fromstring(xml_text)
    # Try v4 first, then v2.
    for ns in (_NS_V4, _NS_V2):
        schema = root.find(".//edm:Schema", ns)
        if schema is not None:
            break
    else:
        raise RuntimeError("OData $metadata: no Schema element found")

    entity_types: Dict[str, Dict[str, Any]] = {}
    for et in schema.findall("edm:EntityType", ns):
        name = et.attrib.get("Name")
        if not name:
            continue
        props: List[Dict[str, Any]] = []
        for p in et.findall("edm:Property", ns):
            props.append({
                "name": p.attrib.get("Name"),
                "type": p.attrib.get("Type", ""),
                "nullable": p.attrib.get("Nullable", "true").lower() != "false",
                "max_length": p.attrib.get("MaxLength"),
            })
        keys = [
            kr.attrib.get("Name")
            for kr in et.findall("edm:Key/edm:PropertyRef", ns)
            if kr.attrib.get("Name")
        ]
        navs: List[Dict[str, str]] = []
        for nav in et.findall("edm:NavigationProperty", ns):
            target = nav.attrib.get("Type") or nav.attrib.get("ToRole") or ""
            # v4 Type looks like "Collection(Schema.Address)" — extract last segment.
            target = target.replace("Collection(", "").rstrip(")")
            target_entity = target.split(".")[-1] if target else ""
            navs.append({
                "name": nav.attrib.get("Name") or "",
                "to_type": target_entity,
            })
        entity_types[name] = {"properties": props, "keys": keys, "navs": navs}

    entity_sets: List[Dict[str, str]] = []
    container = schema.find("edm:EntityContainer", ns)
    if container is not None:
        for es in container.findall("edm:EntitySet", ns):
            es_name = es.attrib.get("Name")
            es_type = es.attrib.get("EntityType", "")
            es_type_short = es_type.split(".")[-1]
            if es_name:
                entity_sets.append({"name": es_name, "type": es_type_short})
    return {"entity_types": entity_types, "entity_sets": entity_sets}


def _fetch_metadata_sync(resolved: Dict[str, Any]) -> Dict[str, Any]:
    # Serve from the per-prefix parsed-metadata cache while fresh — avoids
    # re-downloading + re-parsing the full $metadata document on every describe.
    prefix = resolved["prefix"]
    cached = _METADATA_CACHE.get(prefix)
    if cached and (cached.get("fetched_at", 0) + _METADATA_TTL_SECONDS > time.time()):
        return cached["meta"]

    headers = {"Accept": "application/xml"}
    auth = None
    if resolved["auth_type"] == "basic":
        auth = _basic_auth(resolved)
    else:
        headers["Authorization"] = f"Bearer {_get_oauth_token_sync(resolved)}"
    url = f"{resolved['service_root']}/$metadata"
    with httpx.Client(timeout=60.0) as client:
        resp = client.get(url, headers=headers, auth=auth)
    if resp.status_code != 200:
        raise RuntimeError(f"OData $metadata fetch failed ({resp.status_code}): {resp.text[:300]}")
    meta = _parse_metadata(resp.text)
    _METADATA_CACHE[prefix] = {"meta": meta, "fetched_at": time.time()}
    return meta


# ---------------------------------------------------------------------------
# Type mapping (Edm.* → catalogue coarse type)
# ---------------------------------------------------------------------------


_EDM_TYPE_MAP = {
    "edm.string": "string", "edm.guid": "string",
    "edm.byte": "number", "edm.sbyte": "number",
    "edm.int16": "number", "edm.int32": "number", "edm.int64": "number",
    "edm.decimal": "number", "edm.double": "number", "edm.single": "number",
    "edm.boolean": "boolean",
    "edm.date": "date", "edm.datetime": "timestamp",
    "edm.datetimeoffset": "timestamp", "edm.time": "time",
    "edm.binary": "binary", "edm.stream": "binary",
}


def _map_edm_type(edm_type: str) -> str:
    return _EDM_TYPE_MAP.get((edm_type or "").lower(), edm_type or "string")


# ---------------------------------------------------------------------------
# Catalogue introspection — sync surface mirroring sql_connector
# ---------------------------------------------------------------------------


def extract_table_names(connection: Dict[str, Any]) -> List[str]:
    """List all entity sets exposed by the service."""
    resolved = _resolve_connection(connection)
    # RULE #1 fail-loud: returning [] on a metadata fetch failure is
    # indistinguishable from "this service exposes no entity sets". Log and
    # re-raise so the caller sees the real failure.
    try:
        meta = _fetch_metadata_sync(resolved)
    except Exception as exc:
        logger.error("❌ [OData] $metadata fetch failed: %s", exc)
        raise
    return [es["name"] for es in meta.get("entity_sets") or []]


def extract_schema(connection: Dict[str, Any], entity_set: str) -> Dict[str, Any]:
    """Return ``{table_name, columns, row_count}`` for one entity set."""
    resolved = _resolve_connection(connection)
    meta = _fetch_metadata_sync(resolved)
    es = next((e for e in meta["entity_sets"] if e["name"] == entity_set), None)
    if not es:
        raise RuntimeError(f"OData entity_set {entity_set!r} not found in $metadata")
    et = meta["entity_types"].get(es["type"])
    if not et:
        raise RuntimeError(f"OData entity_type {es['type']!r} for {entity_set!r} not found")

    keys = set(et.get("keys") or [])
    nav_map = {n["name"]: n["to_type"] for n in et.get("navs") or []}

    columns: List[Dict[str, Any]] = []
    for p in et.get("properties") or []:
        col: Dict[str, Any] = {
            "name": p.get("name"),
            "type": _map_edm_type(p.get("type") or ""),
            "nullable": bool(p.get("nullable", True)),
            "is_primary_key": p.get("name") in keys,
            "is_foreign_key": False,
        }
        if p.get("max_length"):
            col["range"] = {"max_length": str(p["max_length"])}
        # Properties whose name matches a navigation property point at the
        # target entity type — flag them as FKs for the catalogue.
        if p.get("name") in nav_map:
            col["is_foreign_key"] = True
            col["foreign_ref"] = f"{nav_map[p['name']]}.{p.get('name')}"
        columns.append(col)

    # Row count via $count — not all OData services expose it, so we degrade
    # gracefully rather than fail.
    row_count = -1
    try:
        row_count = _entity_count_sync(resolved, entity_set)
    except Exception as exc:
        logger.debug("[OData] $count skipped for %s: %s", entity_set, exc)

    return {"table_name": entity_set, "columns": columns, "row_count": row_count}


def _entity_count_sync(resolved: Dict[str, Any], entity_set: str) -> int:
    headers = _async_headers(resolved)
    auth = None
    if resolved["auth_type"] == "basic":
        auth = _basic_auth(resolved)
    else:
        headers["Authorization"] = f"Bearer {_get_oauth_token_sync(resolved)}"
    url = f"{resolved['service_root']}/{entity_set}/$count"
    with httpx.Client(timeout=30.0) as client:
        resp = client.get(url, headers=headers, auth=auth)
    if resp.status_code != 200:
        return -1
    try:
        return int(resp.text.strip())
    except Exception:
        return -1


def extract_primary_keys(connection: Dict[str, Any], entity_set: str) -> List[str]:
    """Return the Key elements declared on the entity type."""
    resolved = _resolve_connection(connection)
    meta = _fetch_metadata_sync(resolved)
    es = next((e for e in meta["entity_sets"] if e["name"] == entity_set), None)
    if not es:
        return []
    et = meta["entity_types"].get(es["type"])
    return list((et or {}).get("keys") or [])


def extract_foreign_keys(connection: Dict[str, Any], entity_set: str) -> List[Dict[str, str]]:
    """Return navigation properties as relationship rows."""
    resolved = _resolve_connection(connection)
    meta = _fetch_metadata_sync(resolved)
    es = next((e for e in meta["entity_sets"] if e["name"] == entity_set), None)
    if not es:
        return []
    et = meta["entity_types"].get(es["type"]) or {}
    out: List[Dict[str, str]] = []
    for nav in et.get("navs") or []:
        # Look up the target's entity_set name via meta (best-effort).
        target_set = next(
            (e["name"] for e in meta["entity_sets"] if e["type"] == nav["to_type"]),
            nav["to_type"],
        )
        out.append({
            "from_table": entity_set,
            "from_column": nav["name"],
            "to_table": target_set,
            "to_column": "Id",  # OData navs target the keyed entity; Id is conventional
        })
    return out


def extract_sample_rows(
    connection: Dict[str, Any],
    entity_set: str,
    n: int = 10,
) -> List[Dict[str, Any]]:
    """Fetch up to `n` rows via ``?$top=n``. Stays read-only, no pagination."""
    resolved = _resolve_connection(connection)
    n = max(1, min(int(n), 1000))
    headers = _async_headers(resolved)
    auth = None
    if resolved["auth_type"] == "basic":
        auth = _basic_auth(resolved)
    else:
        headers["Authorization"] = f"Bearer {_get_oauth_token_sync(resolved)}"
    url = f"{resolved['service_root']}/{entity_set}"
    with httpx.Client(timeout=60.0) as client:
        resp = client.get(url, params={"$top": n}, headers=headers, auth=auth)
    if resp.status_code != 200:
        logger.warning("[OData] sample failed for %s: %s", entity_set, resp.text[:200])
        return []
    return _extract_records(resp.json() or {})


def _extract_records(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """OData v2 wraps in {d: {results: [...]}}; v4 returns {value: [...]}."""
    if "value" in payload:
        return list(payload.get("value") or [])
    d = payload.get("d") or {}
    if isinstance(d, dict) and "results" in d:
        return list(d.get("results") or [])
    if isinstance(d, list):
        return list(d)
    return []


def _next_link(payload: Dict[str, Any]) -> Optional[str]:
    """v4 → @odata.nextLink, v2 → d.__next."""
    if payload.get("@odata.nextLink"):
        return str(payload["@odata.nextLink"])
    d = payload.get("d") or {}
    if isinstance(d, dict) and d.get("__next"):
        return str(d["__next"])
    return None


# ---------------------------------------------------------------------------
# Read execution — used by query_engine._run_odata
# ---------------------------------------------------------------------------


_SAFE_ENTITY = __import__("re").compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


async def execute_odata(
    connection: Dict[str, Any],
    query: Dict[str, Any],
    read_via: Dict[str, Any],
    row_limit: int = 50,
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """Execute an OData GET. ``query`` shape::

        {
          "entity":    str,           # required (or read_via.target)
          "$filter":   str | None,
          "$select":   str | None,
          "$top":      int | None,    # capped at row_limit
          "$orderby":  str | None,
        }
    """
    if not isinstance(query, dict):
        return [], "OData query must be a dict (entity / $filter / $select / $top / $orderby)"
    entity = query.get("entity") or (read_via or {}).get("target")
    if not entity or not _SAFE_ENTITY.match(str(entity)):
        return [], f"OData query: invalid or missing entity name {entity!r}"

    try:
        resolved = _resolve_connection(connection)
    except Exception as exc:
        return [], f"OData connection: {exc}"

    headers = _async_headers(resolved)
    auth = None
    if resolved["auth_type"] == "basic":
        auth = _basic_auth(resolved)
    else:
        try:
            headers["Authorization"] = f"Bearer {await _get_oauth_token_async(resolved)}"
        except Exception as exc:
            return [], f"OData auth: {exc}"

    params: Dict[str, Any] = {}
    if query.get("$filter"):
        params["$filter"] = str(query["$filter"])
    if query.get("$select"):
        params["$select"] = str(query["$select"])
    if query.get("$orderby"):
        params["$orderby"] = str(query["$orderby"])
    page_top = int(query.get("$top") or row_limit)
    params["$top"] = max(1, min(page_top, row_limit))

    url = f"{resolved['service_root']}/{entity}"
    rows: List[Dict[str, Any]] = []
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.get(url, params=params, headers=headers, auth=auth)
            if resp.status_code != 200:
                return [], f"OData query failed ({resp.status_code}): {resp.text[:300]}"
            payload = resp.json() or {}
            rows.extend(_extract_records(payload))
            next_url = _next_link(payload)
            while next_url and len(rows) < row_limit:
                # nextLink may be absolute or relative; if relative, resolve against base.
                if next_url.startswith("http"):
                    page_url = next_url
                    page_params = None
                else:
                    page_url = urllib.parse.urljoin(resolved["service_root"] + "/", next_url)
                    page_params = None
                page = await client.get(page_url, params=page_params, headers=headers, auth=auth)
                if page.status_code != 200:
                    return rows[:row_limit], (
                        f"OData pagination failed ({page.status_code}): {page.text[:300]}"
                    )
                page_body = page.json() or {}
                rows.extend(_extract_records(page_body))
                next_url = _next_link(page_body)
    except Exception as exc:
        logger.error("[OData] execute_odata failed: %s", exc)
        return rows[:row_limit], str(exc)

    return rows[:row_limit], None


async def execute_batch(
    connection: Dict[str, Any],
    requests: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """``$batch`` is an extension point. Most read-side workloads do not
    need it; dept subclasses override to enable bundled reads."""
    raise NotImplementedError("odata_connector.execute_batch: implement in dept subclass")


# ---------------------------------------------------------------------------
# Write execution — used by catalogue.execute_action for kind=odata
# ---------------------------------------------------------------------------


async def execute_action(
    connection: Dict[str, Any],
    action: Dict[str, Any],
    payload: Dict[str, Any],
    idempotency_key: Optional[str] = None,
) -> Dict[str, Any]:
    """Execute a registered OData write action.

    Action contract::

        {
          "id":       "create_business_partner",
          "verb":     "create" | "update" | "delete",
          "endpoint": "A_BusinessPartner",   # entity set name
          "key_field": "BusinessPartner",    # required for update/delete
          "input_schema": { ... }
        }
    """
    verb = (action.get("verb") or "").lower()
    entity = action.get("endpoint")
    if not entity:
        raise ValueError("OData action requires `endpoint` (entity set name)")

    resolved = _resolve_connection(connection)
    headers = _async_headers(resolved, {"Content-Type": "application/json"})
    auth = None
    if resolved["auth_type"] == "basic":
        auth = _basic_auth(resolved)
    else:
        headers["Authorization"] = f"Bearer {await _get_oauth_token_async(resolved)}"

    cookies: Dict[str, str] = {}
    if resolved["requires_csrf"]:
        csrf, cookies = await fetch_csrf_token(connection)
        headers["X-CSRF-Token"] = csrf
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key

    base = f"{resolved['service_root']}/{entity}"

    async with httpx.AsyncClient(timeout=60.0, cookies=cookies) as client:
        if verb == "create":
            resp = await client.post(base, json=payload, headers=headers, auth=auth)
        elif verb in {"update", "delete"}:
            # WriteAction model carries `key_fields` (list); older callers may pass
            # `key_field` (singular). Accept either.
            key_field = action.get("key_field") or next(iter(action.get("key_fields") or []), None)
            if not key_field:
                raise ValueError(f"OData {verb} requires action.key_field / key_fields")
            key_value = payload.get(key_field)
            if key_value is None:
                raise ValueError(f"OData {verb} payload missing {key_field!r}")
            url = f"{base}({_format_key(key_value)})"
            if verb == "update":
                # PATCH = OData v4 partial update; v2 services use MERGE.
                method = "PATCH" if resolved["odata_version"] == "4" else "MERGE"
                resp = await client.request(
                    method, url,
                    json={k: v for k, v in payload.items() if k != key_field},
                    headers=headers, auth=auth,
                )
            else:
                resp = await client.delete(url, headers=headers, auth=auth)
        else:
            raise ValueError(f"Unsupported OData verb: {verb!r}")

    if resp.status_code >= 400:
        raise RuntimeError(
            f"OData {verb} on {entity} failed ({resp.status_code}): {resp.text[:300]}"
        )

    if resp.status_code == 204 or not resp.content:
        return {"verb": verb, "status": resp.status_code}
    try:
        body = resp.json()
    except Exception:
        body = {"raw": resp.text[:500]}
    return {"verb": verb, "status": resp.status_code, "body": body}


def _format_key(value: Any) -> str:
    """Format an OData key segment. Strings are quoted, numbers are bare."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    s = str(value).replace("'", "''")
    return f"'{s}'"
