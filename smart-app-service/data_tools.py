# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""Smart App Service \u2014 runtime-side data tools.

Two synthetic OpenAI tools are exposed to the executing LLM whenever an
Action has ``data_bindings``:

  query_dataset(dataset_id, query?, columns?, limit?)
        \u2192 POST {dept-mcp}/query        (read; catalogue-keyed NL entry)

  perform_action(dataset_id, action_id, payload, dry_run?)
        \u2192 POST {dept-mcp}/execute_action  (write)

The runtime resolves ``dataset_id`` to the dept-mcp ``base_url`` via the
catalogue (which crawler.py populated for us). The LLM never sees raw URLs.

Contract guarantees enforced here:
  \u2022 dataset_id MUST appear in the action's reads (for query) or writes
    (for perform). No retargeting.
  \u2022 PII columns are redacted in returned rows when ``redact_pii=True``.
  \u2022 Service API key + user JWT are forwarded so the dept-mcp can apply
    its own visibility check.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import httpx

from catalogue_client import fetch_catalogue_entry
from config import Settings
from discovery_cache import DiscoveryError
from models import Action, AppSpec, DatasetDirectoryEntry

logger = logging.getLogger(__name__)


QUERY_TOOL_NAME = "query_dataset"
ACTION_TOOL_NAME = "perform_action"


# ---------------------------------------------------------------------------
# Tool spec generation
# ---------------------------------------------------------------------------


def build_data_tools(action: Action, app_spec: Optional[AppSpec] = None) -> List[Dict[str, Any]]:
    """Return the synthetic OpenAI tool entries for this action's bindings.

    Empty list when there is nothing to expose. The tools are described with
    the *exact* set of allowed dataset_ids so the LLM cannot reference anything
    else. App-owned OVERLAY data_sources (``smart_app_records`` on the AppSpec)
    are added to the write tool's enum so the LLM can actually target them —
    they are written app-locally (see ``dispatch_perform_action``'s overlay
    branch + ``write_app_record``), NOT as catalogued write bindings.
    """
    bindings = action.data_bindings
    overlay_ds_ids = sorted({
        d.id for d in (getattr(app_spec, "data_sources", None) or [])
        if getattr(d, "type", None) == "smart_app_records"
    }) if app_spec is not None else []
    has_reads = bool(bindings and bindings.reads)
    has_writes = bool(bindings and bindings.writes)
    if not has_reads and not has_writes and not overlay_ds_ids:
        return []

    tools: List[Dict[str, Any]] = []

    if has_reads:
        read_ids = sorted({r.dataset_id for r in bindings.reads})
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": QUERY_TOOL_NAME,
                    "description": (
                        "Read rows from a catalogued dataset. Use a natural-"
                        "language `query` describing what to fetch; the dept-"
                        "mcp turns it into the right SQL/OData/SOQL/REST call. "
                        "PII columns are redacted unless the binding explicitly "
                        "opts out."
                    ),
                    "parameters": {
                        "type": "object",
                        "required": ["dataset_id", "query"],
                        "properties": {
                            "dataset_id": {
                                "type": "string",
                                "enum": read_ids,
                                "description": "Which catalogued dataset to read.",
                            },
                            "query": {
                                "type": "string",
                                "description": "Natural-language description of the rows you need.",
                            },
                            "columns": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Optional projection; defaults to binding columns.",
                            },
                            "limit": {
                                "type": "integer",
                                "minimum": 1,
                                "maximum": 1000,
                                "default": 100,
                            },
                        },
                    },
                    "x_synthetic": "data_read",
                },
            }
        )

    if has_writes or overlay_ds_ids:
        write_ds_ids = sorted(
            {w.dataset_id for w in (bindings.writes or [])} | set(overlay_ds_ids)
        )
        action_ids = sorted({w.action_id for w in (bindings.writes or [])})
        _desc = (
            "Execute a catalogued write action against a dataset "
            "(insert/update/upsert/REST verb). Use `dry_run=true` "
            "to validate the payload without committing."
        )
        if overlay_ds_ids:
            action_ids = sorted(set(action_ids) | {"save_record"})
            _desc += (
                f" The app-owned OVERLAY data_source(s) {overlay_ds_ids} take "
                "action_id='save_record' with payload {record_id: <the SoR "
                "record id>, ...your app-owned fields}; that writes the app's "
                "operational overlay (note/status/review/comment), NEVER the "
                "source system."
            )
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": ACTION_TOOL_NAME,
                    "description": _desc,
                    "parameters": {
                        "type": "object",
                        "required": ["dataset_id", "action_id", "payload"],
                        "properties": {
                            "dataset_id": {
                                "type": "string",
                                "enum": write_ds_ids,
                            },
                            "action_id": {
                                "type": "string",
                                "enum": action_ids,
                            },
                            "payload": {
                                "type": "object",
                                "description": "Field map matching the action's input_schema (overlay: record_id + app-owned fields).",
                            },
                            "dry_run": {"type": "boolean", "default": False},
                            "idempotency_key": {"type": "string"},
                            "confidence": {
                                "type": "number", "minimum": 0, "maximum": 1,
                                "description": (
                                    "Your confidence (0-1) that this write is correct given "
                                    "the evidence. Report it honestly: for an auto-process "
                                    "trigger a low value routes the write to HUMAN review "
                                    "instead of auto-committing. When unsure, give a low score."
                                ),
                            },
                        },
                    },
                    "x_synthetic": "data_write",
                },
            }
        )

    return tools


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


def _service_api_key() -> str:
    """Service-to-service key the dept-mcp expects in `Authorization: Bearer`."""
    return os.getenv("DEPT_MCP_SERVICE_API_KEY", "")


def _mcp_headers(auth_header: Optional[str]) -> Dict[str, str]:
    h: Dict[str, str] = {"Content-Type": "application/json"}
    key = _service_api_key()
    if key:
        h["Authorization"] = f"Bearer {key}"
    if auth_header:
        h["X-User-JWT"] = auth_header.removeprefix("Bearer ").strip()
    return h


def _redact_rows(rows: List[Dict[str, Any]], pii_cols: List[str]) -> List[Dict[str, Any]]:
    if not pii_cols:
        return rows
    out: List[Dict[str, Any]] = []
    for r in rows:
        if not isinstance(r, dict):
            out.append(r)
            continue
        copy = dict(r)
        for c in pii_cols:
            if c in copy and copy[c] is not None:
                copy[c] = "<redacted>"
        out.append(copy)
    return out


def _directory_entry_to_catalogue_dict(
    entry: DatasetDirectoryEntry,
) -> Optional[Dict[str, Any]]:
    """Project a hydrated directory entry into the catalogue dict shape
    that the dispatchers downstream expect.

    Returns None when the entry is incomplete (e.g. data-discovery-service
    was unreachable at publish, so no ``mcp_base_url`` / no ``columns``).
    The caller treats None as "directory miss" and falls back to
    ``fetch_catalogue_entry``.
    """
    if not entry.mcp_base_url:
        return None
    return {
        "source_id": entry.source_id,
        "dataset_id": entry.dataset_id,
        "name": entry.dataset_name or entry.source_name,
        "description": entry.dataset_description or entry.source_description,
        "kind": entry.kind,
        "columns": [
            {
                "name": c.name,
                "type": c.type,
                "semantic_type": c.semantic_type,
                "pii": c.pii,
                "description": c.description,
            }
            for c in entry.columns
        ],
        "has_pii": entry.has_pii,
        "row_count_approx": entry.row_count_approx,
        "write_actions": [
            {
                "id": w.id,
                "verb": w.verb,
                "description": w.description,
            }
            for w in entry.write_actions
        ],
        "mcp_base_url": entry.mcp_base_url,
    }


def _directory_lookup(
    app_spec: Optional[AppSpec],
    source_id: str,
    dataset_id: str,
) -> Optional[Dict[str, Any]]:
    """Find ``(source_id, dataset_id)`` in the AppSpec's directory and
    return it as a catalogue-shape dict, or None on miss."""
    if app_spec is None:
        return None
    directory = getattr(app_spec, "dataset_directory", None) or []
    for entry in directory:
        if entry.source_id == source_id and entry.dataset_id == dataset_id:
            return _directory_entry_to_catalogue_dict(entry)
    return None


async def _resolve_binding_for_read(
    *,
    settings: Settings,
    auth_header: Optional[str],
    tenant_id: str,
    action: Action,
    dataset_id: str,
    app_spec: Optional[AppSpec] = None,
) -> Tuple[Optional[Dict[str, Any]], Optional[Any], Optional[str]]:
    """Return (catalogue_entry, binding, error). All three may be None.

    Tries ``app_spec.dataset_directory`` first (zero round-trip). Falls
    back to ``fetch_catalogue_entry`` when the directory is empty or the
    dataset isn't in it — legacy AppSpecs that pre-date hydration, or
    bindings whose source was unreachable at publish.
    """
    bindings = action.data_bindings
    if bindings is None:
        return None, None, "action has no data_bindings"
    binding = next((r for r in bindings.reads if r.dataset_id == dataset_id), None)
    if binding is None:
        return None, None, f"dataset '{dataset_id}' not in this action's reads"
    entry = _directory_lookup(app_spec, binding.source_id, dataset_id)
    if entry is None:
        try:
            entry = await fetch_catalogue_entry(
                settings=settings,
                auth_header=auth_header,
                tenant_id=tenant_id,
                dataset_id=dataset_id,
                source_id=binding.source_id,
            )
        except DiscoveryError as exc:
            # Outage != "not in catalogue" — surface it truthfully so the
            # LLM/user sees a transient error, not a missing dataset.
            logger.error(
                "catalogue unavailable resolving read binding (dataset=%s): %s",
                dataset_id, exc,
            )
            return None, binding, f"catalogue unavailable ({exc.code}): {exc}"
    if entry is None:
        return None, binding, f"dataset '{dataset_id}' not in catalogue"
    return entry, binding, None


async def _resolve_binding_for_write(
    *,
    settings: Settings,
    auth_header: Optional[str],
    tenant_id: str,
    action: Action,
    dataset_id: str,
    action_id: str,
    app_spec: Optional[AppSpec] = None,
) -> Tuple[Optional[Dict[str, Any]], Optional[Any], Optional[str]]:
    bindings = action.data_bindings
    if bindings is None:
        return None, None, "action has no data_bindings"
    binding = next(
        (
            w
            for w in bindings.writes
            if w.dataset_id == dataset_id and w.action_id == action_id
        ),
        None,
    )
    if binding is None:
        return None, None, (
            f"write ({dataset_id}, {action_id}) not in this action's writes"
        )
    entry = _directory_lookup(app_spec, binding.source_id, dataset_id)
    if entry is None:
        try:
            entry = await fetch_catalogue_entry(
                settings=settings,
                auth_header=auth_header,
                tenant_id=tenant_id,
                dataset_id=dataset_id,
                source_id=binding.source_id,
            )
        except DiscoveryError as exc:
            # Outage != "not in catalogue" — surface it truthfully.
            logger.error(
                "catalogue unavailable resolving write binding (dataset=%s, action=%s): %s",
                dataset_id, action_id, exc,
            )
            return None, binding, f"catalogue unavailable ({exc.code}): {exc}"
    if entry is None:
        return None, binding, f"dataset '{dataset_id}' not in catalogue"
    return entry, binding, None


async def dispatch_query_dataset(
    *,
    settings: Settings,
    auth_header: Optional[str],
    tenant_id: str,
    action: Action,
    args: Dict[str, Any],
    app_spec: Optional[AppSpec] = None,
) -> Dict[str, Any]:
    """Execute a synthetic ``query_dataset`` tool call. Errors return as
    ``{"error": ...}`` so the LLM can recover.

    Reads catalogue metadata from ``app_spec.dataset_directory`` when
    present (zero-RTT — directory was hydrated at publish), falling back
    to ``fetch_catalogue_entry`` per call only for legacy AppSpecs.
    """
    dataset_id = str(args.get("dataset_id") or "")
    query = str(args.get("query") or "")
    columns = args.get("columns")
    limit = int(args.get("limit") or 100)
    if not dataset_id or not query:
        return {"error": "dataset_id and query are required"}

    entry, binding, err = await _resolve_binding_for_read(
        settings=settings,
        auth_header=auth_header,
        tenant_id=tenant_id,
        action=action,
        dataset_id=dataset_id,
        app_spec=app_spec,
    )
    if err or entry is None or binding is None:
        return {"error": err or "binding/catalogue resolution failed"}

    base = entry.get("mcp_base_url")
    if not base:
        return {"error": f"dataset '{dataset_id}' has no mcp_base_url in catalogue"}

    # Phase B2 — runtime targets the catalogue-keyed /query endpoint.
    # /query takes the NL ``query`` string + ``dataset_ids[]`` and runs it
    # through the dept-mcp planner (NL → SQL/DuckDB/...) → execute pipeline,
    # so any backend kind (sql, duckdb, soql, odata, rest) works through the
    # same shape. The legacy /run_query path silently failed for non-SQL
    # backends because the LLM passes NL, not pre-generated SQL.
    body = {
        "query": query,
        "source_id": binding.source_id,
        "dataset_ids": [dataset_id],
        "max_results": min(max(limit, 1), 50),
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{base.rstrip('/')}/query",
                json=body,
                headers=_mcp_headers(auth_header),
            )
    except httpx.HTTPError as exc:
        # dept-MCP is DOWN/unreachable. Surface to the LLM (it tells the user) AND
        # log it — a swallowed/unlogged outage hides the failure (RULE #1).
        logger.error("dept-mcp /query transport failure (%s): %s", base, exc)
        return {"error": f"dept-mcp transport: {exc}"}

    if resp.status_code >= 400:
        logger.error("dept-mcp /query returned HTTP %s (%s)", resp.status_code, base)
        return {
            "error": f"dept-mcp /query returned {resp.status_code}",
            "body": resp.text[:500],
        }
    try:
        result = resp.json() or {}
    except Exception as exc:  # noqa: BLE001
        logger.error("dept-mcp /query returned non-JSON (%s): %s", base, exc)
        return {"error": "dept-mcp returned non-JSON"}

    # /query returns ``{results: ChunkResult[], source_id, source_type, total}``.
    # For structured kinds the planner attaches the raw rows on
    # ``results[0].metadata.rows`` (see dept-mcp query_planner._rows_to_chunks);
    # for semantic kinds there are no rows, only chunk text. Surface both
    # shapes so the LLM tool consumer can choose.
    chunks = result.get("results") or []
    primary = chunks[0] if chunks else {}
    primary_meta = (primary.get("metadata") or {}) if isinstance(primary, dict) else {}
    rows = primary_meta.get("rows") or []
    err = primary_meta.get("error")

    if binding.redact_pii and rows:
        pii_cols = [
            c.get("name")
            for c in (entry.get("columns") or [])
            if c.get("pii") and c.get("name")
        ]
        rows = _redact_rows(rows, pii_cols)

    out: Dict[str, Any] = {
        "rows": rows,
        "row_count": primary_meta.get("row_count", len(rows)),
        "sql": primary_meta.get("sql"),
        "kind": primary_meta.get("kind") or entry.get("kind") or "sql",
        "results": chunks,                 # full chunk list for semantic / multi-result paths
        "source_id": result.get("source_id"),
        "source_type": result.get("source_type"),
        "total": result.get("total"),
    }
    # Only surface ``error`` when there's a REAL error. A null ``error`` key
    # still trips the tool-loop's ``"error" in result`` heuristic, which would
    # stamp every SUCCESSFUL read as status="error" in the timeline + audit
    # ledger + digest. (Mirrors the tools_v2_dispatch normalization.)
    if err:
        out["error"] = err
    return out


def _is_blob_descriptor(v: Any) -> bool:
    """A runtime file-upload payload value: {filename, content_type, data(b64)}."""
    return (isinstance(v, dict) and isinstance(v.get("data"), str)
            and "filename" in v and "content_type" in v)


async def _store_upload_blobs(
    payload: Dict[str, Any],
    props: Dict[str, Any],
    *,
    settings: Settings,
    auth_header: Optional[str],
    app_id: str,
    dataset: str,
) -> Dict[str, Any]:
    """Guard: Citra-stored media columns are DISABLED (publish rule F-01).

    File bytes are never uploaded into Citra's own storage — SoR media is read
    from the source system, streamed through the dept-MCP. If a payload still
    carries a file blob (a ``format:"file"`` field slipped past publish), fail
    loud rather than silently storing it. ``props``/``settings``/``app_id``/
    ``dataset`` are retained so the signature matches its call sites."""
    if not isinstance(payload, dict):
        return payload
    blob_fields = [k for k, v in payload.items() if _is_blob_descriptor(v)]
    if not blob_fields:
        return payload
    raise RuntimeError(
        "Citra-stored media columns are disabled — file-upload field(s) "
        f"{blob_fields} are no longer supported. Media must be read from the "
        "source system via an mcp data_source (streamed through the MCP), not "
        "uploaded into Citra storage."
    )


async def write_app_record(
    *,
    ds: Any,
    payload: Dict[str, Any],
    auth_header: Optional[str],
    tenant_id: str,
    app_spec: Optional[AppSpec],
    plan_only: bool = False,
) -> Dict[str, Any]:
    """APP-LOCAL OVERLAY write to the ``smart_app_records`` store (owner=citra_app).

    This is NOT a governed source-of-record write — it is the app's own
    operational overlay, ALWAYS anchored to a SoR record by ``record_id``. One
    merged document per ``(app_id, record_id)`` (the existing unique index): the
    overlay's app-owned fields live in ``data`` and are MERGED on upsert (a new
    note doesn't wipe the assignee). ``kind`` = the data_source ``ref``.

    Env-routed via ``get_smart_app_records_col()`` so a TEST app's overlay lands
    in ``test_smart_app_records`` (the handler must have bound env first). The
    runtime audits the returned write event like any other write. Respects
    ``plan_only`` (dry-run: validate + report, do NOT commit) so the
    form-submission gate exercises this path without writing.

    Boundary (enforced by the builder, declared here for the runtime): this
    stores app-owned fields only — never SoR business fields (no shadowing), and
    must never be used to change SoR business state (no laundering)."""
    record_id = str(payload.get("record_id") or "").strip()
    if not record_id:
        return {"error": (
            "overlay write requires 'record_id' — the system-of-record row this "
            "overlay annotates (app-owned data is always anchored to a SoR record)"
        )}
    kind = (str(getattr(ds, "ref", "") or "").strip()) or "overlay"
    mode = getattr(ds, "mode", "merge") or "merge"
    status = payload.get("status")
    # data = the app-owned fields only (drop the system keys). Guard against
    # Mongo dotted/operator keys so a hostile/odd field name can't corrupt the doc.
    data = {
        k: v for k, v in payload.items()
        if k not in ("record_id", "status", "thread_of")
        and "." not in k and not k.startswith("$")
    }
    # MUST key by the real app_id (app_<uuid>) — that is what panel_data reads
    # by, what the workflow MongoWriterNode writes by (ctx.linked_smart_app_id),
    # and what the unique (app_id, record_id) index is on. Slug is only a
    # last-resort fallback for stubs/tests that lack app_id.
    app_id = str(
        getattr(app_spec, "app_id", None) or getattr(app_spec, "slug", None) or "app"
    )

    # org_id is the TRUSTED tenant from the server-resolved app scope (caller
    # passes app_spec.tenant_id) — NEVER the JWT's org_id claim, which a token
    # could mismatch. Only the author/dept/owner-SA identity comes from the
    # (already-signature-verified upstream) forwarded JWT, read-only.
    org_id, dept_ids, owner_sa, author = tenant_id, [], None, None
    tok = (auth_header or "").removeprefix("Bearer ").strip()
    if tok:
        try:
            import jwt as _jwt  # local import — only the overlay path needs it
            claims = _jwt.decode(tok, options={"verify_signature": False})
            dept_ids = list(claims.get("dept_ids") or [])
            owner_sa = claims.get("work_sa_id")
            author = claims.get("user_id") or claims.get("email")
        except Exception as e:  # noqa: BLE001 — stamp what we can; surface, don't crash
            logger.warning("[overlay-write] could not read JWT claims for ownership: %s", e)

    if plan_only:
        return {
            "ok": True, "planned": True, "mode": mode, "kind": kind,
            "record_id": record_id, "fields": sorted(data.keys()),
            "note": "plan_only: overlay write validated, not committed",
        }

    try:
        from main import get_smart_app_records_col  # local import to avoid cycle
        col = get_smart_app_records_col()
        now = datetime.now(timezone.utc)
        _owner_type = "service_account" if owner_sa else None
        if mode == "thread":
            # THREAD: each write APPENDS a new row anchored to the SoR record via
            # ``thread_of``; ``record_id`` is a fresh unique row id (fits the
            # unique (app_id, record_id) index). A read returns the list per
            # record (a comment / review history).
            import uuid as _uuid
            row_id = f"{kind}-{_uuid.uuid4().hex[:20]}"
            await col.insert_one({
                "record_id": row_id, "thread_of": record_id,
                "app_id": app_id, "org_id": org_id, "dept_ids": dept_ids,
                "owner_type": _owner_type, "owner_id": owner_sa,
                "author_user_id": author, "source": "user",
                "kind": kind, "status": status, "data": data,
                "deleted_at": None, "created_at": now, "updated_at": now,
            })
            logger.info("[overlay-write] THREAD app=%s kind=%s thread_of=%s row=%s",
                        app_id, kind, record_id, row_id)
            return {"ok": True, "written": 1, "mode": "thread", "kind": kind,
                    "thread_of": record_id, "record_id": row_id, "created": True}

        # MERGE (default): one doc per (app_id, record_id); upsert merges fields.
        # Read the prior values of the fields being written so the change ledger
        # can show old → new. One cheap projected read per merge.
        _proj: Dict[str, Any] = {"_id": 0, "status": 1}
        for _k in data:
            _proj[f"data.{_k}"] = 1
        _prev = await col.find_one({"app_id": app_id, "record_id": record_id}, _proj)
        _prev_data = (_prev or {}).get("data") or {}
        delta: Dict[str, Any] = {
            k: {"from": _prev_data.get(k), "to": v} for k, v in data.items()
        }
        if status is not None and (_prev or {}).get("status") != status:
            delta["status"] = {"from": (_prev or {}).get("status"), "to": status}
        set_fields: Dict[str, Any] = {f"data.{k}": v for k, v in data.items()}
        set_fields["author_user_id"] = author
        set_fields["updated_at"] = now
        # Only set status when the write provides one — a partial overlay update
        # (e.g. adding a note) must NOT wipe an existing status to null.
        if status is not None:
            set_fields["status"] = status
        res = await col.update_one(
            {"app_id": app_id, "record_id": record_id},
            {
                "$set": set_fields,
                "$setOnInsert": {
                    "created_at": now, "kind": kind, "source": "user",
                    "org_id": org_id, "dept_ids": dept_ids,
                    "owner_type": _owner_type, "owner_id": owner_sa,
                    # deleted_at only on INSERT — a normal write must never
                    # resurrect a row that was deliberately soft-deleted.
                    "deleted_at": None,
                },
            },
            upsert=True,
        )
    except Exception as e:  # noqa: BLE001 — surface the write failure to the caller
        logger.error("[overlay-write] failed for app=%s record=%s: %s", app_id, record_id, e)
        return {"error": f"overlay write failed: {e}"}

    logger.info("[overlay-write] MERGE app=%s kind=%s record=%s fields=%s (env-routed)",
                app_id, kind, record_id, sorted(data.keys()))
    return {
        "ok": True, "written": 1, "mode": "merge", "kind": kind, "record_id": record_id,
        "created": bool(getattr(res, "upserted_id", None)),
        # old → new per field, for the change ledger ("who changed what").
        "delta": delta,
    }


async def dispatch_perform_action(
    *,
    settings: Settings,
    auth_header: Optional[str],
    tenant_id: str,
    action: Action,
    args: Dict[str, Any],
    app_spec: Optional[AppSpec] = None,
    plan_only: bool = False,
) -> Dict[str, Any]:
    """Execute a synthetic ``perform_action`` tool call.

    Reads catalogue metadata from ``app_spec.dataset_directory`` when
    present (zero-RTT), falling back to ``fetch_catalogue_entry`` per
    call only for legacy AppSpecs.

    When ``plan_only`` is True, the outgoing /execute_action call is
    forced into ``dry_run=True`` regardless of what the LLM passed —
    the MCP runs its full preflight (write-authz + schema validation)
    but does not mutate the source. The caller (the runtime tool loop)
    captures the intent into a ``planned_writes`` list and returns
    ``status=pending_approval`` to the UI."""
    dataset_id = str(args.get("dataset_id") or "")
    action_id = str(args.get("action_id") or "")
    payload = args.get("payload") or {}
    # plan_only wins over whatever the LLM emitted — a queue-action click
    # NEVER commits in the tool loop, only via the /approve replay.
    dry_run = True if plan_only else bool(args.get("dry_run") or False)
    idempotency_key = args.get("idempotency_key")
    if not dataset_id or not action_id:
        return {"error": "dataset_id and action_id are required"}
    if not isinstance(payload, dict):
        return {"error": "payload must be an object"}

    # APP-OWNED OVERLAY write: if dataset_id names a smart_app_records
    # data_source on this AppSpec (owner=citra_app), this is an APP-LOCAL write
    # to the overlay store — NOT a governed MCP write. Resolve it here, before
    # the catalogue/MCP binding (which a citra_app source intentionally lacks).
    _overlay_ds = next(
        (d for d in (getattr(app_spec, "data_sources", None) or [])
         if getattr(d, "id", None) == dataset_id
         and getattr(d, "type", None) == "smart_app_records"),
        None,
    ) if app_spec is not None else None
    if _overlay_ds is not None:
        return await write_app_record(
            ds=_overlay_ds, payload=payload, auth_header=auth_header,
            tenant_id=tenant_id, app_spec=app_spec, plan_only=plan_only,
        )

    entry, binding, err = await _resolve_binding_for_write(
        settings=settings,
        auth_header=auth_header,
        tenant_id=tenant_id,
        action=action,
        dataset_id=dataset_id,
        action_id=action_id,
        app_spec=app_spec,
    )
    if err or entry is None or binding is None:
        return {"error": err or "binding/catalogue resolution failed"}

    base = entry.get("mcp_base_url")
    if not base:
        return {"error": f"dataset '{dataset_id}' has no mcp_base_url in catalogue"}

    # File-upload store: every uploaded file blob is stored by the platform
    # (Citra-Service) and the write column receives a durable ref string — incl.
    # format:"file" inputs (the MCP takes a string, not a blob object; see
    # _store_upload_blobs). Runs on dry_run too so the form-submission gate
    # exercises the same path.
    try:
        _wa = next((w for w in (entry.get("write_actions") or [])
                    if str(w.get("id")) == action_id), None)
        _props = ((_wa or {}).get("input_schema") or {}).get("properties") or {}
        _app_id = str(getattr(app_spec, "slug", None)
                      or getattr(app_spec, "app_id", None) or "app")
        payload = await _store_upload_blobs(
            payload, _props, settings=settings, auth_header=auth_header,
            app_id=_app_id, dataset=dataset_id,
        )
    except Exception as e:  # noqa: BLE001 — surface, never swallow the file
        logger.error("[file-fallback] %s", e)
        return {"error": f"file upload storage failed: {e}"}

    body: Dict[str, Any] = {
        "source_id": binding.source_id,
        "dataset_id": dataset_id,
        "action_id": action_id,
        "payload": payload,
        "dry_run": dry_run,
    }
    if idempotency_key:
        body["idempotency_key"] = str(idempotency_key)

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{base.rstrip('/')}/execute_action",
                json=body,
                headers=_mcp_headers(auth_header),
            )
    except httpx.HTTPError as exc:
        # A WRITE to a DOWN dept-MCP must be loud — log it (the officer must not
        # believe a record was updated when the source was unreachable) AND
        # surface to the LLM so it tells the user the write failed (RULE #1).
        logger.error("dept-mcp /execute_action transport failure (%s): %s", base, exc)
        return {"error": f"dept-mcp transport: {exc}"}

    if resp.status_code >= 400:
        logger.error("dept-mcp /execute_action returned HTTP %s (%s)", resp.status_code, base)
        return {
            "error": f"dept-mcp /execute_action returned {resp.status_code}",
            "body": resp.text[:500],
        }
    try:
        out = resp.json()
    except Exception:  # noqa: BLE001
        return {"error": "dept-mcp returned non-JSON"}
    # Surface the resolved source_id so the runtime can persist it into
    # planned_writes — /approve needs source_id at replay time to route
    # the apply call to the right dept-MCP. Underscore prefix marks it
    # as an internal channel; LLM never sees it (we strip _-prefixed
    # keys before serialising tool_result for the model).
    if isinstance(out, dict):
        out.setdefault("_source_id", binding.source_id)
    return out
