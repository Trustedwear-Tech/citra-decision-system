"""Generic REST connector — schema-driven, deterministic (no LLM).

Backs ``DatasetKind.rest``. A REST dataset DECLARES its contract; the caller
supplies structured **params** (e.g. ``{"pan": "ABCPE1234F"}``); this connector
maps params → the upstream HTTP request, fires it, and PROJECTS the JSON response
into typed rows. The MCP does the hard work; the app/UI only supplies params and
renders rows.

Authoring (dataset block) — the request/response mapping lives under
``read_via.extra`` (the passthrough that survives to the catalogue/builder); this
connector is handed a NORMALISED flat block by ``catalogue._run_rest``::

    read_via.extra = {
      "request":  { "method": "GET",
                    "path":   "/v2/credit/{{pan}}",      # {{param}} from input_schema
                    "query":  { "loanId": "{{loan_id}}" },
                    "body":   null },
      "response": { "path":     "data",                   # dotted path to object/list
                    "row_mode": "object",                 # "object" (1 row) | "list" (N)
                    "columns":  { "credit_score": "score", "status": "status" } }
    }
    # dataset-level: input_schema = {"required":["pan"], ...}

Contract (RULE #1 — fail loud): every failure LOGS and returns ``(rows, error)``
with a non-None ``error``; nothing is silently dropped, nothing raises past the
``query_engine._run_rest`` wrapper.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

import httpx

logger = logging.getLogger(__name__)

_MISSING = object()  # sentinel: path key ABSENT (vs present-but-null)
_PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}")


def _fail(msg: str) -> Tuple[List[Dict[str, Any]], str]:
    """Log AND return the error — the fail-loud contract."""
    logger.warning("[rest_connector] %s", msg)
    return [], msg


# ── single-pass interpolation ───────────────────────────────────────────────
def _fill_str(template: str, params: Dict[str, Any], *, encode: bool) -> str:
    """Replace every ``{{name}}`` in ONE pass (a substituted value is never
    re-scanned — no second-order injection). ``encode`` percent-encodes the
    substituted value (for URL path segments, so a value's ``/ ? # ..`` cannot
    alter the request structure); query values are left raw (httpx encodes them)."""
    def repl(m: "re.Match[str]") -> str:
        key = m.group(1)
        if key not in params or params[key] is None:
            return m.group(0)  # leave unresolved → _has_unfilled catches it
        v = params[key]
        s = "true" if v is True else "false" if v is False else str(v)
        return quote(s, safe="") if encode else s
    return _PLACEHOLDER.sub(repl, template)


def _fill(obj: Any, params: Dict[str, Any], *, encode: bool) -> Any:
    if isinstance(obj, str):
        return _fill_str(obj, params, encode=encode)
    if isinstance(obj, dict):
        return {k: _fill(v, params, encode=encode) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_fill(v, params, encode=encode) for v in obj]
    return obj


def _has_unfilled(v: Any) -> bool:
    return isinstance(v, str) and bool(_PLACEHOLDER.search(v))


# ── response extraction / projection ────────────────────────────────────────
def _extract_path(payload: Any, dotted: str) -> Any:
    """Walk a dotted path. Returns ``_MISSING`` when a key is ABSENT (so the
    caller can distinguish a wrong path from a present-but-null value)."""
    if not dotted:
        return payload
    cur = payload
    for part in dotted.split("."):
        if isinstance(cur, dict):
            if part not in cur:
                return _MISSING
            cur = cur[part]
        elif isinstance(cur, list):
            try:
                cur = cur[int(part)]
            except (ValueError, IndexError):
                return _MISSING
        else:
            return _MISSING
    return cur


def _project_row(item: Any, col_map: Dict[str, str]) -> Dict[str, Any]:
    """Map one response object to a row via {column: dotted-field-path}. With no
    mapping a dict passes through; a scalar becomes {"value": x}. A field the API
    omits becomes ``None`` (not the _MISSING sentinel)."""
    if col_map:
        out: Dict[str, Any] = {}
        for col, path in col_map.items():
            v = _extract_path(item, path)
            out[col] = None if v is _MISSING else v
        return out
    if isinstance(item, dict):
        return item
    return {"value": item}


def _resolve_auth(conn: Dict[str, Any]) -> Dict[str, str]:
    """Auth headers from ``connection.auth`` (creds read from env by prefix)."""
    auth = (conn or {}).get("auth") or {}
    kind = (auth.get("type") or "none").lower()
    prefix = (auth.get("env_prefix") or "").strip()
    if not prefix or kind == "none":
        return {}
    if kind == "bearer":
        tok = os.environ.get(f"{prefix}_TOKEN") or os.environ.get(f"{prefix}_API_KEY")
        return {"Authorization": f"Bearer {tok}"} if tok else {}
    if kind == "api_key":
        key = os.environ.get(f"{prefix}_API_KEY")
        return {auth.get("header_name", "X-API-Key"): key} if key else {}
    if kind == "basic":
        user, pwd = os.environ.get(f"{prefix}_USER"), os.environ.get(f"{prefix}_PASSWORD")
        if user and pwd:
            import base64
            enc = base64.b64encode(f"{user}:{pwd}".encode()).decode()
            return {"Authorization": f"Basic {enc}"}
    return {}


def _validate_params(params: Dict[str, Any], input_schema: Optional[Dict[str, Any]]) -> Optional[str]:
    """Guard the required params are present + non-empty (the upstream API is the
    real type-validator; we only stop a missing key from silently building a bad
    request). Returns an error string or None."""
    if not isinstance(input_schema, dict):
        return None
    for req in (input_schema.get("required") or []):
        v = params.get(req)
        if v is None or (isinstance(v, str) and v.strip() == ""):
            return f"missing required parameter '{req}'"
    return None


async def execute_rest(
    connection: Dict[str, Any],
    query: Dict[str, Any],
    read_via: Dict[str, Any],
    row_limit: int = 50,
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """Issue the declared HTTP request for the caller's ``query`` params and
    project the response into rows. ``query`` is the caller's **params** dict
    (e.g. ``{"pan": "..."}``); the request/response mapping lives in ``read_via``."""
    params: Dict[str, Any] = query if isinstance(query, dict) else {}
    request = (read_via or {}).get("request") or {}
    response_cfg = (read_via or {}).get("response") or {}
    input_schema = (read_via or {}).get("input_schema")

    # A REST dataset MUST declare a request mapping. Empty ⇒ misconfigured dataset
    # (or a /run_query with no dataset_id) — fail loud, don't fire a bare GET.
    if not request:
        return _fail("no REST request mapping — dataset read_via.extra.request is missing")

    err = _validate_params(params, input_schema)
    if err:
        return _fail(err)

    # ── build the request ───────────────────────────────────────────────────
    method = (request.get("method") or connection.get("method") or "GET").upper()
    base_url = _fill(connection.get("base_url") or "", params, encode=False)
    path = _fill(request.get("path") or "", params, encode=True)   # encode path params
    if _has_unfilled(base_url) or _has_unfilled(path):
        return _fail("unresolved {{placeholder}} in base_url/path — check input params")
    if path:
        if not path.startswith("/"):
            path = "/" + path
        url = base_url.rstrip("/") + path
    else:
        url = base_url

    # Query params: httpx URL-encodes these, so DON'T encode here. Drop any that
    # stayed unresolved (an omitted optional must not leak "{{loan_id}}").
    raw_q = _fill(request.get("query") or {}, params, encode=False)
    query_params = {k: v for k, v in raw_q.items() if not _has_unfilled(v) and v not in (None, "")}
    body = _fill(request.get("body"), params, encode=False) if request.get("body") is not None else None

    headers = dict(connection.get("headers") or {})
    headers.update(_resolve_auth(connection))

    # ── SSRF guard (default-deny private/loopback/metadata; matches /media) ───
    from rag.api_engine import _ssrf_check
    allow_private = bool(connection.get("allow_private")) or \
        os.getenv("REST_ALLOW_PRIVATE_HOSTS", "").lower() in ("1", "true", "yes")
    if allow_private:
        logger.warning("[rest_connector] allow_private is ON for %s — private/loopback hosts permitted", url)
    ssrf_err = _ssrf_check(url, allow_private=allow_private)
    if ssrf_err:
        return _fail(f"refused REST target: {ssrf_err}")

    # ── fire (redirects NOT followed — redirect-to-private is the classic bypass) ─
    timeout = float(connection.get("timeout_seconds") or 15)
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            resp = await client.request(method, url, params=query_params, json=body, headers=headers)
    except httpx.HTTPError as exc:
        return _fail(f"REST fetch failed: {exc}")
    if resp.status_code >= 300:
        return _fail(f"upstream {resp.status_code}: {resp.text[:200]}")
    try:
        payload = resp.json()
    except Exception as exc:  # noqa: BLE001 — surfaced as an error, not swallowed
        return _fail(f"upstream returned non-JSON body: {resp.text[:120]!r} ({exc})")

    # ── project into rows ─────────────────────────────────────────────────────
    resp_path = response_cfg.get("path") or ""
    extracted = _extract_path(payload, resp_path)
    if extracted is _MISSING:
        return _fail(f"response path '{resp_path}' not found in upstream body")
    if extracted is None:
        return [], None  # path present but null ⇒ a VALID empty result (not an error)

    col_map = response_cfg.get("columns") or {}
    row_mode = (response_cfg.get("row_mode") or "object").lower()
    if row_mode == "list":
        if not isinstance(extracted, list):
            return _fail(f"response.row_mode='list' but path resolved to {type(extracted).__name__}")
        return [_project_row(it, col_map) for it in extracted[:row_limit]], None
    return [_project_row(extracted, col_map)], None
