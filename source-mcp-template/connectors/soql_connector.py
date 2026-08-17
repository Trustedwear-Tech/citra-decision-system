# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""
Salesforce SOQL Connector
=========================
Backs ``DatasetKind.soql`` for Salesforce. Implements the full MCP catalogue
contract (list / describe / sample / read / write) plus OAuth.

Credential model
----------------
Like ``sql_connector``, credentials are NEVER stored in code or YAML — only
environment variables, looked up via ``connection.env_prefix``::

    type:        salesforce
    env_prefix:  SFDC                 # → reads SFDC_INSTANCE_URL, SFDC_CLIENT_ID, ...
    auth_type:   client_credentials   # client_credentials | refresh_token
    api_version: "58.0"               # optional, default 58.0

Env vars consumed (per env_prefix):

    SFDC_INSTANCE_URL    https://mycompany.my.salesforce.com
    SFDC_CLIENT_ID       Connected App consumer key
    SFDC_CLIENT_SECRET   Connected App consumer secret
    SFDC_REFRESH_TOKEN   only for auth_type=refresh_token

Token cache
-----------
Tokens are cached in-process per ``env_prefix`` and refreshed before
expiry. Refresh-token flow auto-refreshes whenever the cached access_token
is < 60s from expiry.

Backwards-compat
----------------
``execute_soql`` is the entry point ``query_engine._run_soql`` already
calls. Catalogue introspection (``extract_table_names`` / ``extract_schema``
/ ``extract_primary_keys`` / ``extract_foreign_keys`` / ``extract_sample_rows``)
mirrors the surface of ``sql_connector`` so ``catalogue.py`` only needs to
pick a connector module, not branch on per-kind APIs.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

import httpx

logger = logging.getLogger(__name__)


_DEFAULT_API_VERSION = "58.0"
_TOKEN_REFRESH_LEEWAY_SECONDS = 60
_TOKEN_CACHE: Dict[str, Dict[str, Any]] = {}
# Per-prefix locks prevent the thundering-herd refresh — under load N
# coroutines all noticing "expired" used to fire N parallel /oauth2/token
# POSTs, each counting against Salesforce daily API limits.
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


# ---------------------------------------------------------------------------
# Connection helpers — env_prefix driven
# ---------------------------------------------------------------------------


def _env(prefix: str, name: str, default: str = "") -> str:
    return os.getenv(f"{prefix}_{name}", default).strip()


def _resolve_connection(connection: Dict[str, Any]) -> Dict[str, str]:
    """Materialise a usable connection dict from the catalogue ``connection``
    block + env vars. Returns a fresh dict with everything we need to call
    Salesforce — never logs secrets."""
    prefix = (connection.get("env_prefix") or "").upper()
    if not prefix:
        raise ValueError("Salesforce connection: env_prefix is required")

    instance_url = _env(prefix, "INSTANCE_URL")
    if not instance_url:
        raise ValueError(f"Salesforce: {prefix}_INSTANCE_URL is not set")

    auth_type = (connection.get("auth_type") or "client_credentials").lower()
    api_version = str(connection.get("api_version") or _DEFAULT_API_VERSION).lstrip("v")

    out: Dict[str, str] = {
        "prefix": prefix,
        "instance_url": instance_url.rstrip("/"),
        "auth_type": auth_type,
        "api_version": api_version,
        "client_id": _env(prefix, "CLIENT_ID"),
        "client_secret": _env(prefix, "CLIENT_SECRET"),
        "refresh_token": _env(prefix, "REFRESH_TOKEN"),
    }
    if auth_type not in {"client_credentials", "refresh_token"}:
        raise ValueError(
            f"Salesforce: unsupported auth_type {auth_type!r} "
            "(client_credentials | refresh_token)"
        )
    if not out["client_id"] or not out["client_secret"]:
        raise ValueError(
            f"Salesforce: {prefix}_CLIENT_ID and {prefix}_CLIENT_SECRET must be set"
        )
    if auth_type == "refresh_token" and not out["refresh_token"]:
        raise ValueError(f"Salesforce: {prefix}_REFRESH_TOKEN required for refresh_token flow")
    return out


def _data_url(resolved: Dict[str, str], path: str) -> str:
    return f"{resolved['instance_url']}/services/data/v{resolved['api_version']}/{path.lstrip('/')}"


# ---------------------------------------------------------------------------
# Auth / token cache
# ---------------------------------------------------------------------------


async def get_access_token(connection: Dict[str, Any]) -> str:
    """Return a valid Salesforce access token, refreshing if needed."""
    resolved = _resolve_connection(connection)
    return await _get_or_refresh_token_async(resolved)


def _token_is_fresh(cached: Dict[str, Any]) -> bool:
    return bool(cached.get("access_token")) and (
        cached.get("expires_at", 0) - _TOKEN_REFRESH_LEEWAY_SECONDS > time.time()
    )


def _build_oauth_body(resolved: Dict[str, str]) -> Dict[str, str]:
    if resolved["auth_type"] == "refresh_token":
        return {
            "grant_type": "refresh_token",
            "client_id": resolved["client_id"],
            "client_secret": resolved["client_secret"],
            "refresh_token": resolved["refresh_token"],
        }
    return {
        "grant_type": "client_credentials",
        "client_id": resolved["client_id"],
        "client_secret": resolved["client_secret"],
    }


def _store_token(resolved: Dict[str, str], data: Dict[str, Any]) -> str:
    access_token = data.get("access_token")
    if not access_token:
        raise RuntimeError("Salesforce auth response had no access_token")
    # `expires_in` is sometimes absent from client_credentials responses;
    # fall back to a conservative 30 min so we still rotate.
    expires_in = int(data.get("expires_in", 1800))
    _TOKEN_CACHE[resolved["prefix"]] = {
        "access_token": access_token,
        "instance_url": data.get("instance_url", resolved["instance_url"]),
        "expires_at": time.time() + expires_in,
    }
    return access_token


async def _get_or_refresh_token_async(resolved: Dict[str, str]) -> str:
    cached = _TOKEN_CACHE.get(resolved["prefix"])
    if cached and _token_is_fresh(cached):
        return cached["access_token"]

    # Serialise refreshes per env_prefix — first one through the lock fetches,
    # the rest re-check the cache and reuse. Avoids thundering-herd /token POSTs
    # that would each count against the org's API limit.
    async with _lock_async(resolved["prefix"]):
        cached = _TOKEN_CACHE.get(resolved["prefix"])
        if cached and _token_is_fresh(cached):
            return cached["access_token"]
        token_url = f"{resolved['instance_url']}/services/oauth2/token"
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                token_url,
                data=_build_oauth_body(resolved),
                headers={"Accept": "application/json"},
            )
        if resp.status_code != 200:
            raise RuntimeError(f"Salesforce auth failed ({resp.status_code}): {resp.text[:300]}")
        return _store_token(resolved, resp.json())


def _get_or_refresh_token_sync(resolved: Dict[str, str]) -> str:
    """Sync variant for catalogue introspection (sample / describe paths)."""
    cached = _TOKEN_CACHE.get(resolved["prefix"])
    if cached and _token_is_fresh(cached):
        return cached["access_token"]

    with _lock_sync(resolved["prefix"]):
        cached = _TOKEN_CACHE.get(resolved["prefix"])
        if cached and _token_is_fresh(cached):
            return cached["access_token"]
        token_url = f"{resolved['instance_url']}/services/oauth2/token"
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(
                token_url,
                data=_build_oauth_body(resolved),
                headers={"Accept": "application/json"},
            )
        if resp.status_code != 200:
            raise RuntimeError(f"Salesforce auth failed ({resp.status_code}): {resp.text[:300]}")
        return _store_token(resolved, resp.json())


def _auth_headers(token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Accept": "application/json"}


# ---------------------------------------------------------------------------
# Type mapping (Salesforce field type → catalogue type)
# ---------------------------------------------------------------------------


_SFDC_TYPE_MAP = {
    "string": "string", "textarea": "string", "email": "string",
    "phone": "string", "url": "string", "encryptedstring": "string",
    "id": "string", "reference": "string", "picklist": "string",
    "multipicklist": "string", "combobox": "string",
    "boolean": "boolean",
    "int": "number", "double": "number", "currency": "number",
    "percent": "number", "long": "number",
    "date": "date", "datetime": "timestamp", "time": "time",
    "base64": "binary", "anyType": "string",
}


def _map_field(field: Dict[str, Any]) -> Dict[str, Any]:
    """Convert a Salesforce describe `fields[i]` entry into our column dict."""
    f_type = str(field.get("type") or "").lower()
    out: Dict[str, Any] = {
        "name": field.get("name"),
        "type": _SFDC_TYPE_MAP.get(f_type, f_type or "string"),
        "nullable": bool(field.get("nillable", True)),
        "is_primary_key": (field.get("name") == "Id"),
        "is_foreign_key": (f_type == "reference"),
    }
    refers = field.get("referenceTo") or []
    if f_type == "reference" and refers:
        # Salesforce FKs always target the standard Id column.
        out["foreign_ref"] = f"{refers[0]}.Id"
    # Picklist values double as low-cardinality enum values.
    pick = field.get("picklistValues") or []
    if pick:
        out["distinct_values"] = [
            str(p.get("value")) for p in pick[:50] if p.get("value") is not None
        ]
    if field.get("inlineHelpText"):
        out["description"] = str(field["inlineHelpText"])[:200]
    return out


# ---------------------------------------------------------------------------
# Catalogue introspection — sync surface mirroring sql_connector
# ---------------------------------------------------------------------------


def extract_table_names(connection: Dict[str, Any]) -> List[str]:
    """List queryable sObjects. Filters out non-queryable / system objects."""
    resolved = _resolve_connection(connection)
    token = _get_or_refresh_token_sync(resolved)
    url = _data_url(resolved, "sobjects/")
    with httpx.Client(timeout=30.0) as client:
        resp = client.get(url, headers=_auth_headers(token))
    if resp.status_code != 200:
        logger.warning("[SOQL] global describe failed (%s): %s", resp.status_code, resp.text[:200])
        return []
    sobjects = (resp.json() or {}).get("sobjects") or []
    return [
        s["name"] for s in sobjects
        if s.get("queryable") and not s.get("deprecatedAndHidden")
    ]


def extract_schema(connection: Dict[str, Any], sobject: str) -> Dict[str, Any]:
    """Describe one sObject. Returns ``{table_name, columns, row_count}``.

    `row_count` is best-effort via ``SELECT COUNT() FROM <Object>``; falls
    back to -1 when the user lacks COUNT permission on the object."""
    resolved = _resolve_connection(connection)
    token = _get_or_refresh_token_sync(resolved)
    url = _data_url(resolved, f"sobjects/{sobject}/describe/")
    with httpx.Client(timeout=30.0) as client:
        resp = client.get(url, headers=_auth_headers(token))
    if resp.status_code != 200:
        raise RuntimeError(f"Salesforce describe({sobject}) failed: {resp.status_code} {resp.text[:200]}")
    body = resp.json() or {}
    fields = body.get("fields") or []
    columns = [_map_field(f) for f in fields]

    # Approx row count — small extra round-trip, but very useful for the catalogue.
    row_count = -1
    try:
        with httpx.Client(timeout=30.0) as client:
            cnt = client.get(
                _data_url(resolved, "query/"),
                params={"q": f"SELECT COUNT() FROM {sobject}"},
                headers=_auth_headers(token),
            )
        if cnt.status_code == 200:
            row_count = int((cnt.json() or {}).get("totalSize") or 0)
    except Exception as exc:
        logger.debug("[SOQL] COUNT() skipped for %s: %s", sobject, exc)

    return {"table_name": sobject, "columns": columns, "row_count": row_count}


def extract_primary_keys(connection: Dict[str, Any], sobject: str) -> List[str]:
    """Salesforce sObjects always use ``Id`` as the primary key."""
    return ["Id"]


def extract_foreign_keys(connection: Dict[str, Any], sobject: str) -> List[Dict[str, str]]:
    """Return reference fields as the same shape sql_connector emits.

    Each item: ``{from_table, from_column, to_table, to_column}``.
    """
    schema = extract_schema(connection, sobject)
    out: List[Dict[str, str]] = []
    for col in schema.get("columns") or []:
        if not col.get("is_foreign_key"):
            continue
        ref = col.get("foreign_ref")
        if not ref or "." not in ref:
            continue
        to_table, to_col = ref.split(".", 1)
        out.append({
            "from_table": sobject,
            "from_column": col["name"],
            "to_table": to_table,
            "to_column": to_col,
        })
    return out


def extract_sample_rows(
    connection: Dict[str, Any],
    sobject: str,
    n: int = 10,
) -> List[Dict[str, Any]]:
    """Return up to `n` representative rows.

    Uses ``SELECT FIELDS(STANDARD)`` so we don't need to enumerate every
    column up-front (Salesforce caps FIELDS(STANDARD) at 200 columns and
    requires LIMIT ≤ 200 — both fine for sampling)."""
    resolved = _resolve_connection(connection)
    token = _get_or_refresh_token_sync(resolved)
    n = max(1, min(int(n), 200))
    soql = f"SELECT FIELDS(STANDARD) FROM {sobject} LIMIT {n}"
    with httpx.Client(timeout=30.0) as client:
        resp = client.get(
            _data_url(resolved, "query/"),
            params={"q": soql},
            headers=_auth_headers(token),
        )
    if resp.status_code != 200:
        logger.warning("[SOQL] sample failed for %s: %s", sobject, resp.text[:200])
        return []
    records = (resp.json() or {}).get("records") or []
    # Salesforce annotates each record with `attributes` — drop it.
    return [{k: v for k, v in r.items() if k != "attributes"} for r in records]


# ---------------------------------------------------------------------------
# Read execution — used by query_engine._run_soql
# ---------------------------------------------------------------------------


_SELECT_RE = re.compile(r"^\s*SELECT\b", re.IGNORECASE)


def _validate_soql(soql: str) -> Optional[str]:
    """Defence-in-depth: never trust the planner. Returns an error string
    when SOQL is rejected, ``None`` when it's safe to ship."""
    if not soql or not soql.strip():
        return "Empty SOQL"
    if ";" in soql:
        return "Multi-statement SOQL is not allowed"
    if not _SELECT_RE.match(soql):
        return "Only SELECT SOQL queries are allowed"
    return None


async def execute_soql(
    connection: Dict[str, Any],
    soql: str,
    row_limit: int = 50,
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """Run a SELECT-only SOQL string and page through results until
    ``row_limit`` rows are collected. Returns ``(rows, error)`` — never
    raises on Salesforce-side failures."""
    err = _validate_soql(soql)
    if err:
        return [], err

    try:
        resolved = _resolve_connection(connection)
        token = await _get_or_refresh_token_async(resolved)
    except Exception as exc:
        return [], f"Salesforce auth: {exc}"

    rows: List[Dict[str, Any]] = []
    next_url: Optional[str] = None
    headers = _auth_headers(token)

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            # Initial page.
            resp = await client.get(
                _data_url(resolved, "query/"),
                params={"q": soql},
                headers=headers,
            )
            if resp.status_code != 200:
                return [], f"Salesforce query failed ({resp.status_code}): {resp.text[:300]}"
            payload = resp.json() or {}
            rows.extend(_strip_attributes(payload.get("records") or []))
            next_url = payload.get("nextRecordsUrl")
            # Subsequent pages until row_limit.
            while next_url and len(rows) < row_limit:
                page = await client.get(
                    f"{resolved['instance_url']}{next_url}", headers=headers
                )
                if page.status_code != 200:
                    return rows[:row_limit], (
                        f"Salesforce pagination failed ({page.status_code}): {page.text[:300]}"
                    )
                page_body = page.json() or {}
                rows.extend(_strip_attributes(page_body.get("records") or []))
                next_url = page_body.get("nextRecordsUrl")
    except Exception as exc:
        logger.error("[SOQL] execute_soql failed: %s", exc)
        return rows[:row_limit], str(exc)

    return rows[:row_limit], None


def _strip_attributes(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [{k: v for k, v in r.items() if k != "attributes"} for r in records]


# ---------------------------------------------------------------------------
# Write execution — used by catalogue.execute_action for kind=soql
# ---------------------------------------------------------------------------


async def execute_action(
    connection: Dict[str, Any],
    action: Dict[str, Any],
    payload: Dict[str, Any],
    idempotency_key: Optional[str] = None,
) -> Dict[str, Any]:
    """Execute a registered Salesforce write action.

    Action contract (lives in dept_sources ``catalogue.datasets[].write_actions``)::

        {
          "id":    "create_account",
          "verb":  "create" | "update" | "upsert" | "delete",
          "endpoint": "Account",                  # sObject name
          "external_id_field": "ExternalId__c",   # only for upsert
          "input_schema": { ... }
        }

    Field values come from ``payload``; for update/upsert/delete the row's
    ``Id`` (or external id value) must be in ``payload``.
    """
    verb = (action.get("verb") or "").lower()
    sobject = action.get("endpoint")
    if not sobject:
        raise ValueError("Salesforce action requires `endpoint` (sObject name)")

    resolved = _resolve_connection(connection)
    token = await _get_or_refresh_token_async(resolved)
    headers = {**_auth_headers(token), "Content-Type": "application/json"}
    if idempotency_key:
        # Salesforce honours `Sforce-Auto-Assign: false`-style headers but
        # has no native idempotency key; we forward as a custom header for
        # downstream auditing.
        headers["X-Idempotency-Key"] = idempotency_key

    base = _data_url(resolved, f"sobjects/{sobject}")
    payload_no_id = {k: v for k, v in payload.items() if k != "Id"}

    async with httpx.AsyncClient(timeout=60.0) as client:
        if verb == "create":
            resp = await client.post(f"{base}/", json=payload_no_id, headers=headers)
        elif verb == "update":
            record_id = payload.get("Id")
            if not record_id:
                raise ValueError("Salesforce update requires `Id` in payload")
            resp = await client.patch(f"{base}/{record_id}", json=payload_no_id, headers=headers)
        elif verb == "upsert":
            ext_field = action.get("external_id_field")
            if not ext_field:
                raise ValueError("Salesforce upsert requires action.external_id_field")
            ext_value = payload.get(ext_field)
            if ext_value is None:
                raise ValueError(f"Salesforce upsert payload missing {ext_field!r}")
            resp = await client.patch(
                f"{base}/{ext_field}/{ext_value}",
                json={k: v for k, v in payload.items() if k != ext_field},
                headers=headers,
            )
        elif verb == "delete":
            record_id = payload.get("Id")
            if not record_id:
                raise ValueError("Salesforce delete requires `Id` in payload")
            resp = await client.delete(f"{base}/{record_id}", headers=headers)
        else:
            raise ValueError(f"Unsupported Salesforce verb: {verb!r}")

    if resp.status_code >= 400:
        raise RuntimeError(
            f"Salesforce {verb} on {sobject} failed ({resp.status_code}): {resp.text[:300]}"
        )

    if resp.status_code == 204 or not resp.content:
        return {"verb": verb, "status": resp.status_code}
    try:
        body = resp.json()
    except Exception:
        body = {"raw": resp.text[:500]}
    return {"verb": verb, "status": resp.status_code, "body": body}
