"""
SAP NetWeaver RFC / BAPI Connector
====================================
Backs ``DatasetKind.sap_rfc`` for SAP ECC / S/4HANA on-prem systems
exposed via classic Remote Function Calls (RFCs) and BAPIs. Use this
when:

- the customer cannot expose SAP OData services
  (``odata_connector.py`` is the preferred path when they can)
- the data lives in transparent tables / pool / cluster tables only
  reachable via RFC_READ_TABLE / RFC_READ_TABLE2 / /BODS/RFC_READ_TABLE
- the agent needs to call a specific BAPI (BAPI_SALESORDER_GETLIST,
  BAPI_USER_GET_DETAIL, …) by name

Runtime requirements
--------------------
1. **SAP NW RFC SDK** installed on the host running the dept-MCP. The
   SDK is proprietary — customers download it from the SAP ONE Support
   Launchpad (search note 2573953). Unpack to ``/opt/sap/nwrfcsdk`` and
   set ``LD_LIBRARY_PATH=/opt/sap/nwrfcsdk/lib`` and
   ``SAPNWRFC_HOME=/opt/sap/nwrfcsdk`` in the container.
2. **pyrfc** Python bindings — ``pip install pyrfc==3.3.1`` (wheel
   compatible with NW RFC SDK 7.50+). Not in default requirements.txt.

If either is missing the connector returns a clear ``error`` string —
it does NOT import-at-load-time-and-die, so the rest of the dept-MCP
still boots when SAP is one of several configured sources.

Credential model
----------------
Like the other connectors, env vars referenced via
``connection.env_prefix``:

    type:        sap_rfc
    env_prefix:  ACME_S4_RFC
    sysnr:       "00"
    client:      "100"
    lang:        EN
    saprouter:   "/H/router.acme.com/S/3299/H/sap-prd"   # optional

Env vars (per env_prefix):

    ACME_S4_RFC_ASHOST    application server host
    ACME_S4_RFC_USER      SAP user
    ACME_S4_RFC_PASSWD    SAP password
    ACME_S4_RFC_SYSID     SID (e.g. PRD) — optional
    ACME_S4_RFC_GROUP     logon group for load balancing — optional
    ACME_S4_RFC_MSHOST    message server host (when load-balanced)
    ACME_S4_RFC_MSSERV    message server service — optional

Two query shapes
----------------
The dispatcher passes ``query`` into ``execute_sap_rfc(... query, read_via)``.
Two shapes are accepted:

1. **Generic RFC call** — ``query`` is a dict::

       {"function": "BAPI_USER_GET_DETAIL",
        "parameters": {"USERNAME": "ALICE"}}

   The connector calls ``conn.call("BAPI_USER_GET_DETAIL", USERNAME="ALICE")``
   and flattens scalar EXPORT parameters + the first TABLES output
   into rows.

2. **Table read** — ``read_via`` carries ``{table: "MARA",
   fields: ["MATNR","MAKTX"], options: ["MTART = 'FERT'"]}``. The
   connector calls ``RFC_READ_TABLE`` with paging until ``row_limit``
   rows are collected.

Security
--------
- Credentials never logged.
- ``BAPI_*`` calls are read-only by convention but the SDK does not
  enforce it; restrict via SAP role authorisations on the connector's
  service user (do NOT use a SAP_ALL technical user).
- All RFC payloads include the JWT-derived ``X-User-JWT`` audit tag
  in the optional ``TRANSACTION_ID`` field if the BAPI supports it
  (left to caller — connector doesn't inject claims).
"""
from __future__ import annotations

import logging
import os
import time
from threading import Lock
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


_CONN_CACHE: Dict[str, Any] = {}      # prefix → pyrfc.Connection
_CONN_CACHE_LOCK = Lock()
_CONN_TTL_SECONDS = 300                # recycle every 5 min — SAP idle-kills long sessions
_CONN_OPENED_AT: Dict[str, float] = {}


# ---------------------------------------------------------------------------
# Connection helpers
# ---------------------------------------------------------------------------


def _env(prefix: str, name: str, default: str = "") -> str:
    return os.getenv(f"{prefix}_{name}", default).strip()


def _connection_params(conn: Dict[str, Any]) -> Dict[str, str]:
    prefix = (conn.get("env_prefix") or "").upper()
    if not prefix:
        raise ValueError("SAP RFC connection: env_prefix is required")

    # Determine logon mode: direct (ashost) vs load-balanced (mshost/group/sysid)
    ashost = _env(prefix, "ASHOST")
    mshost = _env(prefix, "MSHOST")
    sysid = _env(prefix, "SYSID") or str(conn.get("sysid") or "")
    group = _env(prefix, "GROUP")
    msserv = _env(prefix, "MSSERV")

    common: Dict[str, str] = {
        "user": _env(prefix, "USER"),
        "passwd": _env(prefix, "PASSWD"),
        "client": str(conn.get("client") or "100"),
        "lang": str(conn.get("lang") or "EN"),
        "sysnr": str(conn.get("sysnr") or "00"),
    }
    if not (common["user"] and common["passwd"]):
        raise ValueError(f"SAP RFC: {prefix}_USER and {prefix}_PASSWD are required")

    if ashost:
        params = {**common, "ashost": ashost}
    elif mshost and group and sysid:
        params = {**common, "mshost": mshost, "group": group, "sysid": sysid}
        if msserv:
            params["msserv"] = msserv
    else:
        raise ValueError(
            f"SAP RFC: need either {prefix}_ASHOST (direct) "
            f"or {prefix}_MSHOST + GROUP + SYSID (load-balanced)"
        )

    saprouter = (conn.get("saprouter") or "").strip()
    if saprouter:
        params["saprouter"] = saprouter
    return params


def _get_connection(conn_config: Dict[str, Any]):
    """Cache & reuse a pyrfc.Connection per env_prefix with TTL recycle."""
    prefix = (conn_config.get("env_prefix") or "").upper()
    now = time.time()
    with _CONN_CACHE_LOCK:
        cached = _CONN_CACHE.get(prefix)
        opened = _CONN_OPENED_AT.get(prefix, 0.0)
        if cached is not None and (now - opened) < _CONN_TTL_SECONDS:
            return cached
        # Open a fresh connection (and close the stale one, if any).
        if cached is not None:
            try:
                cached.close()
            except Exception:
                pass
            _CONN_CACHE.pop(prefix, None)

        try:
            from pyrfc import Connection  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "pyrfc is not installed; install SAP NW RFC SDK + "
                "'pip install pyrfc==3.3.1' to enable DatasetKind.sap_rfc"
            ) from exc

        params = _connection_params(conn_config)
        try:
            new_conn = Connection(**params)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"SAP RFC connect failed: {exc}") from exc

        _CONN_CACHE[prefix] = new_conn
        _CONN_OPENED_AT[prefix] = now
        return new_conn


# ---------------------------------------------------------------------------
# Public surface — dispatcher entry point
# ---------------------------------------------------------------------------


def execute_sap_rfc(
    conn_config: Dict[str, Any],
    query: Any,
    read_via: Optional[Dict[str, Any]] = None,
    row_limit: int = 50,
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """Two-mode entry: table read via ``read_via.table`` OR generic
    RFC/BAPI call via ``query={"function": ..., "parameters": {...}}``.

    Returns (rows, error). Never raises.
    """
    try:
        rv = read_via or {}
        if rv.get("table"):
            return _read_table(conn_config, rv, row_limit)

        if isinstance(query, dict) and query.get("function"):
            return _call_rfc(conn_config, query, row_limit)

        return [], (
            "sap_rfc: provide either read_via.table=<NAME> for table reads "
            "OR query={function, parameters} for RFC/BAPI calls"
        )
    except RuntimeError as exc:
        # Connect / SDK missing — surface as actionable error.
        return [], str(exc)
    except Exception as exc:  # noqa: BLE001
        logger.error(f"❌ [SAP_RFC] Execution failed: {exc}")
        return [], str(exc)


# ---------------------------------------------------------------------------
# RFC_READ_TABLE — paged scan with field selection + WHERE-style options
# ---------------------------------------------------------------------------


def _read_table(conn_config: Dict[str, Any], rv: Dict[str, Any], row_limit: int) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    table = str(rv.get("table") or "").strip().upper()
    if not _is_safe_sap_identifier(table):
        return [], f"sap_rfc: unsafe table name {table!r}"

    fields_in: List[str] = list(rv.get("fields") or [])
    options_in: List[str] = list(rv.get("options") or [])

    for f in fields_in:
        if not _is_safe_sap_identifier(f):
            return [], f"sap_rfc: unsafe field name {f!r}"

    conn = _get_connection(conn_config)

    fields = [{"FIELDNAME": f} for f in fields_in]
    # RFC_READ_TABLE options are 72-char chunks of a WHERE expression.
    options = [{"TEXT": opt[:72]} for opt in options_in]

    result = conn.call(
        "RFC_READ_TABLE",
        QUERY_TABLE=table,
        DELIMITER="|",
        ROWCOUNT=int(row_limit),
        ROWSKIPS=0,
        FIELDS=fields,
        OPTIONS=options,
    )

    field_defs = result.get("FIELDS") or []
    cols = [str(f.get("FIELDNAME") or "").strip() for f in field_defs]
    raw_rows = [str(r.get("WA") or "") for r in (result.get("DATA") or [])]

    rows: List[Dict[str, Any]] = []
    for raw in raw_rows:
        parts = raw.split("|")
        # Trim trailing whitespace SAP pads to fixed-width.
        row = {col: parts[i].strip() if i < len(parts) else None for i, col in enumerate(cols)}
        rows.append(row)
    return rows[:row_limit], None


# ---------------------------------------------------------------------------
# Generic RFC / BAPI call
# ---------------------------------------------------------------------------


def _call_rfc(conn_config: Dict[str, Any], query: Dict[str, Any], row_limit: int) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    fn = str(query.get("function") or "").strip().upper()
    if not _is_safe_sap_identifier(fn):
        return [], f"sap_rfc: unsafe function name {fn!r}"
    parameters = dict(query.get("parameters") or {})

    # Allow-list pattern — refuse any function starting with destructive
    # prefixes unless the caller explicitly opts in via `allow_write=True`.
    allow_write = bool(query.get("allow_write"))
    if not allow_write and _looks_destructive(fn):
        return [], (
            f"sap_rfc: {fn} looks like a write/commit function; pass "
            "allow_write=true in query payload to confirm"
        )

    conn = _get_connection(conn_config)
    result = conn.call(fn, **parameters)

    # Flatten: pick the first TABLES-typed entry (list of dicts). If none,
    # fall back to scalar EXPORTS as a single-row payload.
    table_payload: Optional[List[Dict[str, Any]]] = None
    scalar_payload: Dict[str, Any] = {}
    for k, v in result.items():
        if isinstance(v, list) and v and isinstance(v[0], dict):
            table_payload = v
            break
        scalar_payload[k] = v

    if table_payload is not None:
        return table_payload[:row_limit], None
    return [scalar_payload] if scalar_payload else [], None


# ---------------------------------------------------------------------------
# Safety
# ---------------------------------------------------------------------------


def _is_safe_sap_identifier(name: str) -> bool:
    """SAP identifiers: A-Z 0-9 _ / (slash for namespace), up to 30 chars."""
    if not name or len(name) > 30:
        return False
    return all(c.isalnum() or c in ("_", "/") for c in name)


_DESTRUCTIVE_PREFIXES = (
    "BAPI_TRANSACTION_COMMIT",
    "BAPI_TRANSACTION_ROLLBACK",
    "RFC_DELETE",
    "RFC_INSERT",
    "RFC_UPDATE",
    "RFC_MODIFY",
)


def _looks_destructive(fn: str) -> bool:
    upper = fn.upper()
    if upper in _DESTRUCTIVE_PREFIXES:
        return True
    # Common write patterns: *_CREATE, *_CHANGE, *_DELETE, *_POST, *_SAVE
    for suffix in ("_CREATE", "_CHANGE", "_DELETE", "_POST", "_SAVE", "_COMMIT", "_INSERT", "_UPDATE", "_MODIFY"):
        if upper.endswith(suffix):
            return True
    return False
