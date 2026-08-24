# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""Smart App Service \u2014 thin client for data-discovery-service.

Why this lives in smart-app-service (not in capabilities.py):
  \u2022 The builder LLM and the runtime BOTH need the catalogue, but in
    different shapes (trimmed for the prompt, full for binding lookups).
  \u2022 The 60s in-memory cache keeps a single interview from spamming
    data-discovery-service while still letting newly-crawled datasets
    appear within a minute.

The client never persists anything. data-discovery-service is the source
of truth; this is a read-through cache.

Failure policy — fail first, fail loud:
  The catalogue being *unavailable* (no URL configured, discovery
  unreachable, a 5xx/4xx other than 404, malformed JSON) is NOT the same
  as the catalogue being *empty*. The old behaviour returned ``[]`` /
  ``None`` on an outage, so a discovery blip silently looked like "this
  tenant has no datasets" — the builder LLM would then author against an
  empty palette and the runtime would report "dataset not in catalogue"
  for a dataset that exists. We now raise ``DiscoveryError`` (the same
  type ``discovery_cache`` raises) and log at error level. Each caller
  decides explicitly whether to surface or skip — never this layer.

  The two non-error answers that survive:
    • ``fetch_catalogue_list`` returns ``[]`` only on a 200 with no
      entries (a genuinely empty catalogue).
    • ``fetch_catalogue_entry`` returns ``None`` only on a 404 (the
      dataset genuinely isn't registered).
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

import httpx

from config import Settings
from discovery_cache import DiscoveryError

logger = logging.getLogger(__name__)


# (tenant_id, source_id_filter) \u2192 (expires_epoch, payload)
_LIST_CACHE: Dict[tuple, tuple[float, List[Dict[str, Any]]]] = {}
# (tenant_id, dataset_id) \u2192 (expires_epoch, payload)
_DETAIL_CACHE: Dict[tuple, tuple[float, Dict[str, Any]]] = {}

_TTL_SECONDS = 60.0


def _now() -> float:
    return time.time()


def _data_discovery_url(settings: Settings) -> Optional[str]:
    """Resolve the data-discovery-service (catalogue) base URL.

    NOT environment-aware: the catalogue is read-only schema/metadata, identical
    between test and prod (IT owns parity), so the builder always reads the prod
    catalogue. Only the MCP plane (discovery-service) is test↔prod. Returns None
    when no URL is configured, in which case the caller treats it as empty.
    """
    url = getattr(settings, "data_discovery_service_url", None)
    return url.rstrip("/") if url else None


async def fetch_catalogue_list(
    *,
    settings: Settings,
    auth_header: Optional[str],
    tenant_id: str,
    source_id: Optional[str] = None,
    has_pii: Optional[bool] = None,
) -> List[Dict[str, Any]]:
    """Return the trimmed catalogue list for a tenant.

    The result is cached for 60s on (tenant_id, source_id, has_pii). Cache
    misses fan out one HTTP call.

    Raises ``DiscoveryError`` when the catalogue is unavailable (no URL
    configured, discovery unreachable, non-200, or malformed JSON). A 200
    with no entries returns ``[]`` — that is a genuinely empty catalogue,
    not a failure.
    """
    base = _data_discovery_url(settings)
    if not base:
        logger.error(
            "data-discovery URL is unset (tenant=%s) — cannot fetch catalogue",
            tenant_id,
        )
        raise DiscoveryError(
            "no_data_discovery_url", "DATA_DISCOVERY_SERVICE_URL is unset", 503
        )

    key = (tenant_id, source_id or "", has_pii)
    hit = _LIST_CACHE.get(key)
    if hit and hit[0] > _now():
        return hit[1]

    params: Dict[str, Any] = {"limit": 500}
    if source_id:
        params["source_id"] = source_id
    if has_pii is not None:
        params["has_pii"] = "true" if has_pii else "false"

    headers = {"Authorization": auth_header} if auth_header else {}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{base}/catalogue", params=params, headers=headers)
    except httpx.HTTPError as exc:
        logger.error("data-discovery /catalogue unreachable (tenant=%s): %s", tenant_id, exc)
        raise DiscoveryError("catalogue_unreachable", str(exc), 502) from exc

    if resp.status_code != 200:
        logger.error(
            "data-discovery /catalogue returned %s (tenant=%s)",
            resp.status_code,
            tenant_id,
        )
        raise DiscoveryError(
            "catalogue_error", f"data-discovery /catalogue returned {resp.status_code}", 502
        )

    try:
        body = resp.json() or {}
    except ValueError as exc:
        logger.error("data-discovery /catalogue returned malformed JSON (tenant=%s): %s", tenant_id, exc)
        raise DiscoveryError("catalogue_bad_json", str(exc), 502) from exc

    entries = body.get("entries") or []
    # Surface truncation loudly: data-discovery returns the TRUE match count in
    # ``total`` even when ``entries`` is capped. A large org silently losing
    # tables here would make the builder author against an incomplete palette.
    total = body.get("total")
    if isinstance(total, int) and total > len(entries):
        logger.warning(
            "data-discovery catalogue TRUNCATED for tenant=%s: %d of %d datasets "
            "returned — builder palette is incomplete; narrow scope (source_id) "
            "or use query-driven search (docs/catalogue-retrieval-plan.md).",
            tenant_id, len(entries), total,
        )
    _LIST_CACHE[key] = (_now() + _TTL_SECONDS, entries)
    return entries


async def fetch_catalogue_search(
    *,
    settings: Settings,
    auth_header: Optional[str],
    tenant_id: str,
    question: str,
    source_id: Optional[str] = None,
    top_k: int = 150,
) -> List[Dict[str, Any]]:
    """Query-driven dataset RECALL via data-discovery ``GET /catalogue/search``.

    Returns datasets already relevance-ranked + tenant/dept-scoped (ANN over the
    catalogue Milvus collection), so the builder never fetches/ranks the whole
    catalogue. The caller reranks (cross-encoder) for precision.

    Raises ``DiscoveryError`` on unavailability — including a 404 if the endpoint
    isn't deployed (older data-discovery) — so the builder route falls back to
    ``fetch_catalogue_list``. Not cached (query-specific).
    """
    base = _data_discovery_url(settings)
    if not base:
        raise DiscoveryError(
            "no_data_discovery_url", "DATA_DISCOVERY_SERVICE_URL is unset", 503
        )
    params: Dict[str, Any] = {"q": question, "top_k": top_k}
    if source_id:
        params["source_id"] = source_id
    headers = {"Authorization": auth_header} if auth_header else {}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{base}/catalogue/search", params=params, headers=headers
            )
    except httpx.HTTPError as exc:
        raise DiscoveryError("catalogue_search_unreachable", str(exc), 502) from exc
    if resp.status_code != 200:
        raise DiscoveryError(
            "catalogue_search_error",
            f"/catalogue/search returned {resp.status_code}",
            502,
        )
    try:
        body = resp.json() or {}
    except ValueError as exc:
        raise DiscoveryError("catalogue_search_bad_json", str(exc), 502) from exc
    return body.get("entries") or []


async def fetch_catalogue_entry(
    *,
    settings: Settings,
    auth_header: Optional[str],
    tenant_id: str,
    dataset_id: str,
    source_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Return the full catalogue entry for one dataset.

    Used by validators (publish) and runtime (tool dispatch).

    Returns ``None`` ONLY on a 404 — the dataset genuinely isn't
    registered. Raises ``DiscoveryError`` on any other failure (no URL
    configured, discovery unreachable, non-200, malformed JSON) so an
    outage is never mistaken for "dataset not in catalogue".
    """
    base = _data_discovery_url(settings)
    if not base:
        logger.error(
            "data-discovery URL is unset — cannot fetch catalogue entry %s", dataset_id
        )
        raise DiscoveryError(
            "no_data_discovery_url", "DATA_DISCOVERY_SERVICE_URL is unset", 503
        )

    key = (tenant_id, dataset_id)
    hit = _DETAIL_CACHE.get(key)
    if hit and hit[0] > _now():
        return hit[1]

    params: Dict[str, Any] = {}
    if source_id:
        params["source_id"] = source_id

    headers = {"Authorization": auth_header} if auth_header else {}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{base}/catalogue/{dataset_id}",
                params=params,
                headers=headers,
            )
    except httpx.HTTPError as exc:
        logger.error("data-discovery /catalogue/%s unreachable: %s", dataset_id, exc)
        raise DiscoveryError("catalogue_unreachable", str(exc), 502) from exc

    if resp.status_code == 404:
        return None
    if resp.status_code != 200:
        logger.error(
            "data-discovery /catalogue/%s returned %s", dataset_id, resp.status_code
        )
        raise DiscoveryError(
            "catalogue_error",
            f"data-discovery /catalogue/{dataset_id} returned {resp.status_code}",
            502,
        )

    try:
        entry = resp.json()
    except ValueError as exc:
        logger.error(
            "data-discovery /catalogue/%s returned malformed JSON: %s", dataset_id, exc
        )
        raise DiscoveryError("catalogue_bad_json", str(exc), 502) from exc

    _DETAIL_CACHE[key] = (_now() + _TTL_SECONDS, entry)
    return entry


def trim_for_prompt(entries: List[Dict[str, Any]], *, max_entries: int = 80) -> List[Dict[str, Any]]:
    """Project catalogue rows down to a token-cheap shape for the builder LLM.

    One row is ~80\u2013150 tokens. We hard-cap the count so very large tenants
    don't blow the prompt budget; the builder UI can paginate / search if
    needed.
    """
    out: List[Dict[str, Any]] = []
    for e in entries[:max_entries]:
        cols = e.get("columns") or []
        cols_brief = ", ".join(
            (
                f"{c.get('name')}:{c.get('semantic_type') or c.get('type','?')}"
                + ("[PII]" if c.get("pii") else "")
            )
            for c in cols[:20]
        )
        out.append(
            {
                "id": e.get("dataset_id"),
                # `ref` is the EXACT string to copy into an AppSpec
                # data_source.ref — it equals dataset_id (already
                # source-qualified, "<source_id>.<table>"). Emitting it
                # verbatim removes the ambiguity that made the builder LLM
                # concatenate source_id + "/" + id into a bogus path ref.
                # NEVER combine `source_id` with `id`/`ref`.
                "ref": e.get("dataset_id"),
                "kind": e.get("kind"),
                "source_id": e.get("source_id"),
                "name": e.get("name"),
                "description": e.get("description") or "",
                "columns_brief": cols_brief,
                "actions": [w.get("id") for w in (e.get("write_actions") or []) if w.get("id")],
                "has_pii": bool(e.get("has_pii")),
                "row_count_approx": e.get("row_count_approx"),
            }
        )
    return out


def reset_cache() -> None:
    """Test helper \u2014 clear both caches."""
    _LIST_CACHE.clear()
    _DETAIL_CACHE.clear()
