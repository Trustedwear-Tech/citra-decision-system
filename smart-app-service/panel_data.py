# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""Panel data resolution.

Resolves the rows that back a queue / chart / dashboard panel.

Sources:
  * ``static`` — rows embedded in the AppSpec via ``DataSource.filters.rows``.
  * ``mcp`` — ``DataSource.ref`` is ``"<source_id>"`` (or
    ``"<source_id>.<tool>"``); the source_id is resolved against
    discovery-service to its dept-MCP query coordinates. Structured
    sources (mongodb / sql / …) are read via the MCP's ``/run_query``
    catalogue endpoint; pure-document sources via the NL ``/query``
    endpoint. ``DataSource.filters`` (minus renderer-metadata keys) is
    the query predicate.
  * ``rag`` — calls citra-service ``POST /search`` with the user's JWT.
    ``DataSource.ref`` is the corpus / collection id.
  * ``smart_app_records`` — rows from the shared records collection.
  * ``workflow_staging`` — per-case rows the workflow engine writes when a
    source-system mutation needs officer review. Distinct from the queue-
    action plan-then-apply path; backed by ``smartapp_workflow_staging``.
  * ``workflow`` — not yet wired; returns an empty typed result with a note.

Returning a uniform shape ({rows, columns, total, truncated, source_kind})
lets the frontend treat every panel the same way regardless of backend.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

import httpx
from fastapi import HTTPException, status

logger = logging.getLogger(__name__)

from config import Settings
from http_client import get_http_client
from env_context import current_env
from models import (
    AppSpec,
    DataSource,
    DetailDataResponse,
    Panel,
    PanelDataResponse,
    compute_plan_hash,
)


_DEFAULT_LIMIT = 500
# M1: one universal row ceiling (500). Was 5000, which let calendar/map/records/
# staging panels fetch 10× the cap the SQL/MCP path already enforces. _panel_limit
# feeds every reader, so capping here unifies the contract. (Charts cap lower at
# _CHART_BUCKET_CAP=100 independently.)
_MAX_LIMIT = 500
# Max buckets a chart can render legibly. A GROUP-BY chart is capped to this so
# a high-cardinality dimension or a long daily time-series can't dump hundreds
# of points. For time-series we keep the MOST RECENT this-many periods (truncate
# the older tail); for categorical we keep the top-N by value.
_CHART_BUCKET_CAP = 100


def project_scorecard_columns(row: Dict[str, Any]) -> Dict[str, Any]:
    """Lift the frozen scorecard's headline numbers to FLAT, top-level columns.

    A queue has to be sortable and filterable on the grade without teaching the
    panel layer to read nested paths — and that is what makes portfolio ranking
    possible at all, because a grade that only exists once someone opens the
    case cannot rank a queue (docs/factor-scorecard-plan.md).

    ``grade`` is EMPTY on a gated case: a failed policy limit decides the case
    and the composite is suppressed server-side, so there is no grade to show
    and ranking a gated case beside ones that actually cleared policy would be
    wrong. But absence is NOT the signal for that — ``gated`` is its own column.
    A checklist app has no grade by design, and a composite where nothing scored
    has none either; reading an empty grade as "gated" told the officer a policy
    limit had been breached when none had. Copy the values through as they are
    and let ``gated`` speak for itself.

    Mutates and returns ``row`` (a plain projection step in a hot read loop).
    """
    card = row.get("scorecard") or {}
    if not card:
        return row
    row["grade"] = card.get("grade")
    row["score_percent"] = card.get("percent")
    row["gated"] = bool(card.get("gated"))
    return row


async def _resolve_workflow_staging_rows(
    *,
    app_spec: AppSpec,
    ds: DataSource,
    auth_header: Optional[str],
    limit: int,
    viewer_scope: Optional[Dict[str, Any]] = None,
) -> Tuple[List[Dict[str, Any]], int, bool, Optional[str]]:
    """Read rows from ``smartapp_workflow_staging`` (workflow-engine output).

    The collection is the source of truth for officer-reviewable cases the
    workflow engine has staged. We delegate scoping (tenant, dept, role
    visibility) to the collection itself, but the panel still owns the
    filter envelope via ``ds.filters``:

      * ``status`` — exact status match (``pending_je_review`` etc.)
      * ``dept_id`` — visibility narrows to one dept
      * ``role`` — visibility narrows to one role
      * ``max_age_days`` — time-window on created_at
      * ``slug`` — pin to one Smart App (else uses ``app_spec.slug``)

    Returns rows verbatim — the panel renderer picks ``columns`` from the
    AppSpec's queue column list, so no projection happens here.
    """
    try:
        from main import get_workflow_staging_col  # local import to avoid cycle
    except ImportError:  # pragma: no cover — test stubs may bypass main
        return [], 0, False, "workflow_staging collection not available"

    from datetime import datetime, timedelta, timezone

    extra = ds.filters or {}
    query: Dict[str, Any] = {}

    pinned_slug = extra.get("slug") or getattr(app_spec, "slug", None)
    if pinned_slug:
        query["slug"] = pinned_slug

    if "status" in extra and extra["status"]:
        query["status"] = extra["status"]
    if "role" in extra and extra["role"]:
        query["assignable_to.role"] = extra["role"]

    # ── Row-level visibility (RULE: the collection has NO row-level security) ──
    # Enforce the caller's tenant + dept scope, mirroring the dedicated
    # /workflow-staging endpoint, so the in-app staging queue cannot surface
    # another dept's (or tenant's) staged recommendations. _can_render_app
    # authorizes the APP, not the ROWS — this closes that gap.
    scope = viewer_scope or {}
    v_tenant = scope.get("tenant_id")
    v_depts = list(scope.get("dept_ids") or [])
    if v_tenant:
        query["tenant_id"] = v_tenant
    filter_dept = extra.get("dept_id")
    if v_depts:
        # Caller has explicit dept membership → restrict to it. A BA-authored
        # dept_id filter is honoured only if it's within the caller's depts;
        # otherwise fall back to the caller's full dept set (never wider).
        if filter_dept and filter_dept in v_depts:
            query["assignable_to.dept_id"] = filter_dept
        else:
            query["assignable_to.dept_id"] = {"$in": v_depts}
    elif filter_dept:
        # No dept membership on the token (platform admin) → honour the
        # panel's explicit dept filter if any; else see all in tenant.
        query["assignable_to.dept_id"] = filter_dept
    max_age = extra.get("max_age_days")
    if isinstance(max_age, int) and max_age > 0:
        cutoff = datetime.now(timezone.utc) - timedelta(days=max_age)
        query["created_at"] = {"$gte": cutoff}

    col = get_workflow_staging_col()
    cursor = col.find(query).sort("created_at", -1).limit(limit + 1)
    raw = [r async for r in cursor]
    truncated = len(raw) > limit
    raw = raw[:limit]

    rows: List[Dict[str, Any]] = []
    for r in raw:
        r.pop("_id", None)
        # Backfill ``source`` for rows written before the field was added
        # to the schema. The UI's "AI-recommended" chip predicate reads
        # this field; defaulting it at read time covers historical rows
        # without a Mongo migration.
        r.setdefault("source", "workflow")
        # Carry the already-computed recommendation so the UI can show it the
        # INSTANT the officer clicks the card — no agent re-run. correlation_id
        # is the composite the staging-approve path expects ({run_id}:{case_key}).
        r["_recommendation"] = {
            "decision": r.get("llm_recommendation_text"),
            "reasoning": r.get("llm_reasoning"),
            "evidence": r.get("llm_evidence_summary"),
            "planned_writes": r.get("planned_writes") or [],
            # Precedent receipts — chips on the officer's result card.
            "cited_precedents": r.get("cited_precedents") or [],
            "status": r.get("status") or "pending_review",
            "correlation_id": f"{r.get('workflow_execution_id')}:{r.get('case_natural_key')}",
            # echoed back on approve to verify display == commit.
            "plan_hash": compute_plan_hash(r.get("planned_writes") or []),
            # The scorecard computed at /run and frozen on the row. Handed to
            # the officer's card verbatim so the grid renders on the click with
            # no re-score — and so the grade shown is exactly the one the ledger
            # will record.
            "scorecard": r.get("scorecard"),
        }
        project_scorecard_columns(r)
        rows.append(r)
    total = len(rows) + (1 if truncated else 0)
    return rows, total, truncated, None


async def _resolve_decision_ledger_rows(
    *,
    app_spec: "AppSpec",
    ds: "DataSource",
    limit: int,
) -> Tuple[List[Dict[str, Any]], int, bool, Optional[str]]:
    """The app's decision ledger, flattened for KPI/ROI pages
    (docs/money-saved-roi-plan.md V3).

    Row contract (stable — the builder binds tiles/charts to these names):
      decision_id · case · mode · decision · decided_at · outcome_label ·
      value_amount · value_kind · currency · value_error · retrieval_count ·
      definition_version

    Scope: THIS app's decisions, tenant-scoped — the ledger is the canonical
    spine; value_amount is the amount the poller stamped per the ontology's
    frozen definition, so pages aggregate it and never recompute money from
    raw sources."""
    from main import get_decision_records_col

    slug = getattr(app_spec, "slug", None)
    tenant = getattr(app_spec, "tenant_id", None)
    if not slug:
        return [], 0, False, "decision_ledger: app slug unresolved"
    try:
        col = get_decision_records_col()
    except RuntimeError as exc:
        return [], 0, False, f"decision_ledger unavailable: {exc}"
    q: Dict[str, Any] = {"slug": slug}
    if tenant:
        q["tenant_id"] = tenant
    cursor = col.find(
        q,
        {"decision_id": 1, "mode": 1, "created_at": 1, "overrides": 1,
         "recommendation.decision": 1, "record_keys.key_values": 1,
         "outcome.label": 1, "outcome.value": 1, "outcome.value_error": 1,
         "retrieval_count": 1},
    ).sort("created_at", -1).limit(limit + 1)
    raw = [r async for r in cursor]
    truncated = len(raw) > limit
    raw = raw[:limit]
    rows: List[Dict[str, Any]] = []
    for r in raw:
        outcome = r.get("outcome") or {}
        value = outcome.get("value") or {}
        kvs = [kv for rk in (r.get("record_keys") or [])
               for kv in (rk.get("key_values") or [])]
        rows.append({
            "decision_id": r.get("decision_id"),
            "case": kvs[0] if kvs else None,
            "mode": r.get("mode"),
            "overridden": bool(r.get("overrides")),
            "decision": str((r.get("recommendation") or {}).get("decision") or "")[:200],
            "decided_at": str(r.get("created_at"))[:19],
            "outcome_label": outcome.get("label"),
            "value_amount": value.get("amount"),
            "value_kind": value.get("kind"),
            "currency": value.get("currency"),
            "value_error": outcome.get("value_error"),
            "retrieval_count": r.get("retrieval_count"),
            "definition_version": value.get("definition_version"),
        })
    return rows, len(rows) + (1 if truncated else 0), truncated, None


async def _resolve_smart_app_records_rows(
    *,
    app_spec: AppSpec,
    ds: DataSource,
    limit: int,
) -> Tuple[List[Dict[str, Any]], int, bool, Optional[str]]:
    """Read rows from the shared ``smart_app_records`` collection.

    ``ds.ref`` is the row ``kind`` (e.g. ``decision``, ``queue_item``,
    ``approval``). ``ds.filters`` may contain ``status`` to narrow further.
    ``app_id`` is always pinned to the owning AppSpec — no cross-app
    leakage is possible from a panel binding.
    """
    if not app_spec.app_id:
        return (
            [],
            0,
            False,
            "smart_app_records: AppSpec.app_id is not set; panel cannot scope",
        )
    kind = (ds.ref or "").strip()
    if not kind:
        return (
            [],
            0,
            False,
            f"data_source '{ds.id}' (smart_app_records) needs a 'ref' equal to the row kind",
        )

    query: Dict[str, Any] = {
        "app_id": app_spec.app_id,
        "kind": kind,
        "deleted_at": None,
    }
    extra = ds.filters or {}
    if "status" in extra:
        query["status"] = extra["status"]
    # THREAD source: each row is one comment/review anchored to the SoR record
    # by ``thread_of`` — filter on it so the read returns the whole history for
    # the record (newest first, via the created_at sort below). MERGE/queue
    # source: filter by ``record_id`` (one doc per record). Accept either filter
    # key for a thread, since the builder may template ``record_id``.
    if getattr(ds, "mode", "merge") == "thread":
        anchor = extra.get("thread_of", extra.get("record_id"))
        if anchor is None:
            # Fail loud — an unanchored thread read would return EVERY record's
            # comments mixed together (a within-app cross-record leak). A thread
            # source MUST be filtered to one SoR record.
            return (
                [], 0, False,
                f"thread data_source '{ds.id}' needs a 'record_id' or 'thread_of' "
                f"filter anchoring it to a single SoR record",
            )
        query["thread_of"] = anchor
    elif "record_id" in extra:
        query["record_id"] = extra["record_id"]

    try:
        from main import get_smart_app_records_col  # local import to avoid cycle
    except ImportError:  # pragma: no cover — test stubs may bypass main
        return [], 0, False, "smart_app_records collection not available"

    col = get_smart_app_records_col()
    cursor = col.find(query).sort("created_at", -1).limit(limit + 1)
    raw = [r async for r in cursor]
    truncated = len(raw) > limit
    raw = raw[:limit]

    rows: List[Dict[str, Any]] = []
    for r in raw:
        # Surface the BA payload at the top level for panel rendering, but
        # also expose the system fields read-only so the UI can deep-link.
        data = r.get("data") or {}
        if not isinstance(data, dict):
            data = {"value": data}
        flat = dict(data)
        flat["record_id"] = r.get("record_id")
        flat["status"] = r.get("status")
        flat["created_at"] = r.get("created_at")
        flat["updated_at"] = r.get("updated_at")
        rows.append(flat)
    total = len(rows) + (1 if truncated else 0)
    return rows, total, truncated, None


def _find_panel(app_spec: AppSpec, panel_id: str) -> Panel:
    for p in app_spec.all_panels:
        if p.id == panel_id:
            return p
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"panel '{panel_id}' not found",
    )


def _find_data_source(app_spec: AppSpec, ds_id: str) -> DataSource:
    for ds in app_spec.data_sources or []:
        if ds.id == ds_id:
            return ds
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=f"data_source '{ds_id}' not declared on app",
    )


def _required_columns(panel: Panel) -> List[str]:
    """Best-effort list of fields the panel needs."""
    cols: List[str] = []
    p_type = getattr(panel, "type", None)
    if p_type == "queue":
        cols.extend(panel.columns or [])
    elif p_type == "chart":
        cols.append(panel.x)
        if isinstance(panel.y, list):
            cols.extend(panel.y)
        else:
            cols.append(panel.y)
        if panel.group_by:
            cols.append(panel.group_by)
    elif p_type == "dashboard":
        for m in panel.metrics:
            if m.field:
                cols.append(m.field)
    elif p_type == "calendar":
        cols.extend([panel.date_field, panel.title_field])
        if panel.color_field:
            cols.append(panel.color_field)
    elif p_type == "map":
        cols.extend([panel.lat_field, panel.lng_field])
        if panel.label_field:
            cols.append(panel.label_field)
    # de-dupe, preserve order
    return list(dict.fromkeys(cols))


def _panel_data_source_id(panel: Panel) -> Optional[str]:
    p_type = getattr(panel, "type", None)
    if p_type in ("queue", "chart", "document_view", "calendar", "map",
                  "timeline"):
        return panel.data_source
    if p_type in ("dashboard", "stat_strip"):
        # dashboards / stat strips may bind per-metric; first declared wins
        for m in panel.metrics:
            if m.data_source:
                return m.data_source
        return None
    if p_type == "hero":
        m = getattr(panel, "metric", None)
        return m.data_source if m is not None else None
    return None


def _panel_limit(panel: Panel) -> int:
    p_type = getattr(panel, "type", None)
    if p_type == "chart" and panel.limit:
        return min(panel.limit, _MAX_LIMIT)
    if p_type in ("calendar", "map", "timeline") and getattr(panel, "limit", None):
        return min(panel.limit, _MAX_LIMIT)
    return _DEFAULT_LIMIT


def _resolve_static_rows(ds: DataSource) -> List[Dict[str, Any]]:
    """Pull rows from ``DataSource.filters.rows`` (inline static data)."""
    raw = (ds.filters or {}).get("rows", [])
    if not isinstance(raw, list):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"data_source '{ds.id}' is static but filters.rows is not a list"
            ),
        )
    rows: List[Dict[str, Any]] = []
    for r in raw:
        if not isinstance(r, dict):
            continue
        rows.append(r)
    return rows


def _dotted_get(row: Dict[str, Any], key: str) -> Any:
    """Resolve a column reference against ``row``. A literal flat key wins; a
    dotted ``a.b.c`` reference walks nested dicts (staging rows nest business
    fields under ``display_context`` — without this they all projected to None
    and the queue rendered blank cells)."""
    if key in row:
        return row[key]
    if "." in key:
        cur: Any = row
        for seg in key.split("."):
            if isinstance(cur, dict) and seg in cur:
                cur = cur[seg]
            else:
                return None
        return cur
    return row.get(key)


def _project_columns(
    rows: List[Dict[str, Any]], cols: List[str]
) -> Tuple[List[Dict[str, Any]], List[str]]:
    if not cols:
        # Derive columns from the union of keys (stable order: first row first).
        seen: List[str] = []
        for r in rows:
            for k in r.keys():
                if k not in seen:
                    seen.append(k)
        return rows, seen
    out: List[Dict[str, Any]] = []
    for r in rows:
        # Project under the (possibly dotted) column name so the renderer's
        # row[c] lookup finds the value, but resolve nested paths on read.
        proj = {c: _dotted_get(r, c) for c in cols}
        # Carry forward reserved keys the renderer needs but never displays as a
        # column: `source` (drives the AI-recommended badge) and `_recommendation`
        # (lets a staged card show its recommendation INSTANTLY on click, no
        # agent re-run). Without this they're dropped whenever the panel pins
        # display columns.
        for k in ("source", "_recommendation"):
            if k in r and k not in proj:
                proj[k] = r[k]
        out.append(proj)
    return out, cols


# dept-MCP source types whose only read path is the NL ``/query``
# endpoint. Everything else (mongodb / sql / duckdb / bigquery / …) is a
# structured source served by ``/run_query``.
_SEMANTIC_SOURCE_TYPES = {"semantic", "rag", ""}

# Marker prefix for a note that represents a real SOURCE FAILURE (MCP down /
# access denied / unresolved / transport) as opposed to a benign empty/info
# note. The user-facing data endpoint raises on a marked note so a failed read
# never renders as an indistinguishable "no rows" empty state (RULE #1).
SOURCE_FAILURE_MARKER = "[source-failure] "
# The resolvers' note slot is a FAILURE-only channel (see _as_source_error). A
# resolver that wants to explain a legitimately-empty result — e.g. "the filter
# excluded every document" — tags it with this instead, so it renders as a benign
# note and never as a 502. Stripped before display.
BENIGN_NOTE_MARKER = "[info] "


def _as_source_error(note: Optional[str]) -> Optional[str]:
    """Tag a resolver failure-note as a source error (idempotent; None→None).

    A note explicitly tagged BENIGN is passed through untouched — it describes a
    valid empty state, not a source failure, and must not become a 502."""
    if not note:
        return None
    if note.startswith(BENIGN_NOTE_MARKER) or note.startswith(SOURCE_FAILURE_MARKER):
        return note
    return f"{SOURCE_FAILURE_MARKER}{note}"


def is_source_failure_note(note: Optional[str]) -> bool:
    return bool(note) and note.startswith(SOURCE_FAILURE_MARKER)


def strip_source_failure_marker(note: str) -> str:
    return note[len(SOURCE_FAILURE_MARKER):] if note.startswith(SOURCE_FAILURE_MARKER) else note


def _run_query_url(query_endpoint: str) -> str:
    """Derive the dept-MCP ``/run_query`` URL from its ``/query`` endpoint.

    discovery advertises each source's ``query_endpoint`` (``…/query``);
    the structured catalogue read lives next to it at ``…/run_query``.
    """
    qe = query_endpoint.rstrip("/")
    if qe.endswith("/query"):
        return qe[: -len("/query")] + "/run_query"
    return qe + "/run_query"


# Structured kinds whose /run_query expects a SELECT string (not a filter
# dict). Everything else structured (mongodb) takes the filter dict as-is.
_SQL_QUERY_KINDS = {"sql", "duckdb", "bigquery"}

# discovery advertises a SOURCE *type*; the MCP /run_query body wants a
# DATASET *kind*. The generic "structured" source type is the SQL family —
# map it to the "sql" dataset kind so the enum + dispatch line up. A REST/API
# source advertises "rest_api" but the MCP DatasetKind is "rest".
_SOURCE_TYPE_TO_KIND = {"structured": "sql", "rest_api": "rest"}


def _sql_literal(v: Any) -> str:
    """Render a Python value as a safe SQL literal (single-quote escaped)."""
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, (int, float)):
        return str(v)
    return "'" + str(v).replace("'", "''") + "'"


# Relative-date tokens a BA (or the builder) may write in a range filter
# instead of a hard-coded date — e.g. ``{"start_time": {"$gte": "today"}}``.
# Resolved DB-side via CURRENT_DATE/CURRENT_TIMESTAMP so they track the real
# calendar (and so a tile like "… (today)" isn't silently null because the
# literal string 'today' matched no row). Only applied inside range operators.
_REL_DATE_TOKENS = {
    "today": "CURRENT_DATE",
    "now": "CURRENT_TIMESTAMP",
    "yesterday": "(CURRENT_DATE - INTERVAL '1 day')",
    "tomorrow": "(CURRENT_DATE + INTERVAL '1 day')",
    "this_week": "date_trunc('week', CURRENT_DATE)",
    "this_month": "date_trunc('month', CURRENT_DATE)",
    "this_year": "date_trunc('year', CURRENT_DATE)",
}
_REL_WINDOW_RE = re.compile(
    r"^-?(\d+)\s*(h|hours?|d|days?|w|weeks?|m|months?|y|years?)$", re.I
)
_REL_WINDOW_UNIT = {
    "h": "hour", "hour": "hour", "hours": "hour",
    "d": "day", "day": "day", "days": "day",
    "w": "week", "week": "week", "weeks": "week",
    "m": "month", "month": "month", "months": "month",
    "y": "year", "year": "year", "years": "year",
}


def _sql_range_operand(v: Any) -> str:
    """SQL operand for a range comparison, resolving relative-date tokens
    ('today', 'now', '-24h', '7d', …) to portable DB date expressions. Falls
    back to a quoted literal for anything unrecognised, so plain dates/strings
    are unaffected. Range-only: a bare 'today' is meaningless as a >=/<=
    operand unless it's a date token, so there's no real-value collision risk
    (unlike '=' where 'today' could be a legitimate category value)."""
    if isinstance(v, str):
        key = v.strip().lower()
        if key in _REL_DATE_TOKENS:
            return _REL_DATE_TOKENS[key]
        m = _REL_WINDOW_RE.match(key)
        if m:
            n = int(m.group(1))
            unit = _REL_WINDOW_UNIT[m.group(2).lower()]
            # "24h" / "-24h" / "7d" → that many units ago, relative to now.
            return f"(CURRENT_TIMESTAMP - INTERVAL '{n} {unit}')"
    return _sql_literal(v)


def _sql_condition(val: Any) -> str:
    """Render the right-hand side of a WHERE condition for one column."""
    if isinstance(val, dict):
        if "$in" in val and isinstance(val["$in"], list):
            items = val["$in"]
            # An empty list must NOT emit `IN ()` (a syntax error that fails the
            # whole query) — `IN (NULL)` is valid and matches nothing.
            if not items:
                return "IN (NULL)"
            return "IN (" + ", ".join(_sql_literal(x) for x in items) + ")"
        if "$ne" in val:
            return "IS NOT NULL" if val["$ne"] is None else "<> " + _sql_literal(val["$ne"])
        if "$gt" in val:
            return "> " + _sql_range_operand(val["$gt"])
        if "$lt" in val:
            return "< " + _sql_range_operand(val["$lt"])
        if "$gte" in val:
            return ">= " + _sql_range_operand(val["$gte"])
        if "$lte" in val:
            return "<= " + _sql_range_operand(val["$lte"])
    # A bare None means an IS NULL check, not `= NULL` (which never matches).
    if val is None:
        return "IS NULL"
    return "= " + _sql_literal(val)


def _order_by_clause(default_sort: Optional[Any]) -> str:
    """`ORDER BY` body for a row SELECT — the panel's sort, else column 1.

    A malformed direction is NOT silently coerced to ASC: a queue that asked for
    the *worst* cases and quietly got the best ones is the failure this whole
    function exists to prevent, so it raises.
    """
    if not default_sort:
        return "1"
    column = (
        default_sort.get("column") if isinstance(default_sort, dict)
        else getattr(default_sort, "column", None)
    )
    if not column:
        return "1"
    direction = (
        default_sort.get("dir") if isinstance(default_sort, dict)
        else getattr(default_sort, "dir", None)
    ) or "asc"
    direction = str(direction).strip().lower()
    if direction not in ("asc", "desc"):
        raise ValueError(
            f"panel default_sort.dir must be 'asc' or 'desc', got {direction!r} "
            f"(column {column!r})"
        )
    return f"{_quote_ident(str(column))} {direction.upper()}"


def _build_select_sql(
    table: str, predicate: Dict[str, Any], limit: int,
    columns: Optional[List[str]] = None,
    default_sort: Optional[Any] = None,
) -> str:
    """Compose a read-only SELECT for a SQL-family catalogue dataset.

    M4: when the panel's required columns are known, PROJECT them (don't `SELECT *`
    — that drags BLOBs / long-text / PII across the wire the panel never renders)
    and add a deterministic `ORDER BY` (the row SELECT was otherwise an arbitrary
    engine-order slice). Falls back to `SELECT *` when columns are unknown.
    The dept-MCP's sql_connector independently re-enforces SELECT-only + LIMIT.

    The ORDER BY is the panel's own ``default_sort`` when it declares one. That
    matters far more than it looks: the row cap is a hard 500, and sorting in the
    browser only reorders the rows that survived the cap. Ordering by column 1
    made "the 500 accounts to work today" mean *the alphabetically first 500
    account numbers* — on acme-bank's collections queue the worst accounts (240
    days overdue) were absent while 1-day-overdue accounts were shown, and no
    amount of clicking the DPD header could bring them back. A prioritised queue
    has to be cut server-side on the priority column. Found by driving the UI.
    """
    if columns:
        cols_sql = ", ".join(_quote_ident(c) for c in columns)
        return (
            f"SELECT {cols_sql} FROM {table}{_where_clause(predicate)} "
            f"ORDER BY {_order_by_clause(default_sort)} LIMIT {int(limit)}"
        )
    return f"SELECT * FROM {table}{_where_clause(predicate)} LIMIT {int(limit)}"


_CHART_AGG_FN = {"count": "COUNT", "sum": "SUM", "avg": "AVG", "min": "MIN", "max": "MAX"}
_TRUNC_GRAINS = {"minute", "hour", "day", "week", "month", "quarter", "year"}


def _chart_x_expr(x_col: str, time_grain: Optional[str]) -> str:
    """Bucket the x column by time_grain when set, else the raw column."""
    q = _quote_ident(x_col)
    return f"date_trunc('{time_grain}', {q})" if time_grain in _TRUNC_GRAINS else q


def _chart_agg_expr(agg: str, y_col: str) -> str:
    fn = _CHART_AGG_FN.get((agg or "").lower())
    if not fn:
        return _quote_ident(y_col)  # caller guards; defensive
    return "COUNT(*)" if fn == "COUNT" else f"{fn}({_quote_ident(y_col)})"


def _build_chart_agg_sql(
    table: str, panel: Any, predicate: Dict[str, Any], limit: int
) -> str:
    """Compose a GROUP BY aggregate SELECT so a chart shows a TRUE aggregate
    computed AT THE SOURCE — not last-wins over capped raw rows in the browser.

    Deterministic: built from the chart's declared aggregation / time_grain /
    x / y / group_by / filter — NO LLM, NO builder-authored SQL. The MCP's
    sql_connector runs it directly. The returned rows are already one-per-
    (x[,group_by]), so the renderer plots them with zero math.
    """
    agg = (getattr(panel, "aggregation", None) or "").lower()
    grain = getattr(panel, "time_grain", None)
    group_by = getattr(panel, "group_by", None)
    y = panel.y
    y_list = list(y) if isinstance(y, list) else [y]

    x_alias = _quote_ident(panel.x)
    select_parts = [f"{_chart_x_expr(panel.x, grain)} AS {x_alias}"]
    group_parts = ["1"]
    if group_by:
        select_parts.append(_quote_ident(group_by))
        group_parts.append(str(len(select_parts)))  # positional GROUP BY
        # group_by ⇒ one y series split by group (renderer uses ySeries[0]).
        select_parts.append(f"{_chart_agg_expr(agg, y_list[0])} AS {_quote_ident(y_list[0])}")
    else:
        for yc in y_list:
            select_parts.append(f"{_chart_agg_expr(agg, yc)} AS {_quote_ident(yc)}")

    cap = min(int(limit), _CHART_BUCKET_CAP)
    base = (
        f"SELECT {', '.join(select_parts)} FROM {table}"
        f"{_where_clause(predicate)} "
        f"GROUP BY {', '.join(group_parts)}"
    )
    if grain:
        # Time-series: keep the MOST RECENT `cap` periods (truncate the older
        # tail, NOT the recent ones), then display oldest -> newest.
        return f"SELECT * FROM ({base} ORDER BY 1 DESC LIMIT {cap}) _w ORDER BY 1 ASC"
    if not group_by and len(y_list) == 1:
        # Categorical, single series: keep the top `cap` by value (truncate the
        # long tail) so the biggest categories survive, not an alphabetical slice.
        return f"{base} ORDER BY {_quote_ident(y_list[0])} DESC LIMIT {cap}"
    # group_by / multi-series: stable x-ordering, capped.
    return f"{base} ORDER BY 1 LIMIT {cap}"


def _col_condition(col: str, val: Any) -> str:
    """Render one `column condition` clause, tolerant of two common spec
    shorthands that otherwise produce broken SQL:
      • a `<col>_in` key with a LIST value  → `<col> IN (...)`  (strip the `_in`)
      • any other LIST value                → `<col> IN (...)`
    Both previously rendered as `"<col>" = '<stringified list>'` — a guaranteed
    failure (e.g. `WHERE "status_in" = '[...]'` → "column status_in does not
    exist"). The IN form is what the author meant, so this is strictly better.
    Dict operators ($in/$gt/…) and scalars keep the existing handling."""
    if isinstance(val, list):
        target = col[:-3] if (col.endswith("_in") and len(col) > 3) else col
        return f"{_quote_ident(target)} {_sql_condition({'$in': val})}"
    return f"{_quote_ident(col)} {_sql_condition(val)}"


def _where_clause(predicate: Dict[str, Any]) -> str:
    conds = [
        _col_condition(col, val)
        for col, val in (predicate or {}).items()
    ]
    return (" WHERE " + " AND ".join(conds)) if conds else ""


# ---------------------------------------------------------------------------
# Dashboard (KPI) metrics — source-side aggregation
# ---------------------------------------------------------------------------
# A dashboard tile must show a TRUE aggregate over the whole (filtered) table
# — COUNT(*), SUM(field), AVG(field), … — not a count of the capped row
# fetch. Computing it client-side over the first 500 rows makes every count
# saturate at 500 and every sum cover only a slice. So for SQL-family sources
# we push the aggregate down to the source via /run_query.


def _quote_ident(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def _num(v: Any) -> Optional[float]:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _ci_getter(row: Dict[str, Any]):
    """Case-insensitive column accessor (engines vary alias casing)."""
    lower = {str(k).lower(): v for k, v in row.items()}
    return lambda key: lower.get(str(key).lower())


def _fmt_bucket_label(b: Any) -> str:
    """Trend-bucket → 'YYYY-MM-DD' string. The runtime sparkline humanises it
    for hover. Accepts a date/datetime object or a string (driver-dependent);
    strings are trimmed to the date part."""
    if b is None:
        return ""
    iso = getattr(b, "isoformat", None)
    if callable(iso):
        s = iso()  # date/datetime
    else:
        s = str(b)
    return s[:10]  # 'YYYY-MM-DD'


def _agg_expr(metric: Any) -> Optional[str]:
    """SQL aggregate expression for one KPI metric, or None when it can't be
    expressed as a simple column aggregate (e.g. ratio, or a field-aggregate
    with no field) — in which case the caller falls back to row counting."""
    agg = (getattr(metric, "agg", "") or "").lower()
    field = getattr(metric, "field", None)
    if agg == "count":
        return "COUNT(*)"
    if agg in ("sum", "avg", "min", "max") and field:
        return f"{agg.upper()}({_quote_ident(field)})"
    return None


def _metric_source_computable(metric: Any) -> bool:
    """True when the source-side aggregate path can compute this metric — a
    plain column aggregate (count/sum/avg/min/max) OR a ratio (computed as two
    source-side COUNTs). Anything else is genuinely unsupported and forces the
    wholesale row fallback. This is the gate `_resolve_dashboard_metrics` uses;
    a single ratio metric must NOT (as it used to) bail the WHOLE panel and
    leave every tile blank."""
    return _agg_expr(metric) is not None or (
        (getattr(metric, "agg", "") or "").lower() == "ratio"
    )


def _period_predicate(
    date_field: str, grain: str, offset: int, table: str, where: str = ""
) -> str:
    """SQL predicate matching rows in the period ``offset`` grains before the
    latest one (offset=0 → latest period present in the data).

    The anchor is the most recent period PRESENT IN THE METRIC'S FILTERED DATA
    — ``MAX(col)`` over ``{table}{where}`` — NOT the wall clock and NOT the
    whole unfiltered table. Two reasons:

    * Anchoring to the clock (``CURRENT_DATE``) makes the current period empty
      whenever the data lags "today" (demo data / reporting lag) → spurious
      ``-100%``.
    * Anchoring to the WHOLE table (no ``where``) breaks any metric whose
      filter excludes the latest rows: e.g. "SLA breaches" filters
      ``sla_due_at < now``, but ``MAX(sla_due_at)`` over the whole table is a
      *future* deadline (a complaint not yet due), so the current period has
      zero breaches → ``-100%`` again. Applying the same ``where`` makes the
      anchor land on the latest period that actually satisfies the metric, so
      ``curr`` is never spuriously empty."""
    col = _quote_ident(date_field)
    g = grain if grain in ("day", "week", "month", "quarter", "year") else "day"
    anchor = f"date_trunc('{g}', (SELECT MAX({col}) FROM {table}{where}))"
    if offset <= 0:
        rhs = anchor
    else:
        # 'quarter' is valid in date_trunc but NOT as an INTERVAL unit on
        # Postgres/DuckDB (`INTERVAL '1 quarter'` errors) — express it in months.
        if g == "quarter":
            rhs = f"{anchor} - INTERVAL '{int(offset) * 3} month'"
        else:
            rhs = f"{anchor} - INTERVAL '{int(offset)} {g}'"
    return f"date_trunc('{g}', {col}) = {rhs}"


def _compare_expr(metric: Any, cond: str) -> Optional[str]:
    """Period-windowed aggregate via portable CASE WHEN (not FILTER, for
    SQL-engine safety). Only count/sum — a windowed delta is meaningful for
    flow metrics, not avg/min/max."""
    agg = (getattr(metric, "agg", "") or "").lower()
    field = getattr(metric, "field", None)
    if agg == "count":
        return f"SUM(CASE WHEN {cond} THEN 1 ELSE 0 END)"
    if agg == "sum" and field:
        return f"SUM(CASE WHEN {cond} THEN {_quote_ident(field)} ELSE 0 END)"
    return None


def _delta(curr: Optional[float], prev: Optional[float]) -> Optional[Dict[str, Any]]:
    """{dir, pct, text} for the ▲/▼ chip, or None when not derivable."""
    if curr is None or prev is None:
        return None
    if prev == 0:
        return {"dir": "flat", "pct": 0.0, "text": "0%"} if curr == 0 else None
    pct = (curr - prev) / abs(prev) * 100.0
    direction = "up" if curr > prev else ("down" if curr < prev else "flat")
    return {"dir": direction, "pct": pct, "text": f"{pct:+.1f}%"}


async def _run_sql_on_source(
    *,
    settings: Settings,
    ds: DataSource,
    auth_header: Optional[str],
    sql: str,
    row_limit: int = 1,
) -> Tuple[Optional[List[Dict[str, Any]]], Optional[str]]:
    """Resolve a SQL-family source and run one SELECT.

    Returns (rows, None) on success; (None, reason) when the source isn't
    SQL-family or the query fails — the caller then falls back to the
    row-fetch path so non-SQL dashboards keep working.
    """
    from discovery_cache import DiscoveryError, resolve_source

    source_id = (ds.ref or "").split(".", 1)[0].strip()
    if not source_id:
        return None, "empty ref"
    user_jwt = (auth_header or "").removeprefix("Bearer ").strip() or None
    try:
        resolved = await resolve_source(
            discovery_url=settings.discovery_url_for(current_env()),
            user_jwt=user_jwt,
            source_id=source_id,
            cache_ttl_seconds=settings.discovery_cache_ttl_seconds,
        )
    except DiscoveryError as e:
        return None, f"discovery: {e}"
    kind = (resolved.source_type or "").lower()
    kind = _SOURCE_TYPE_TO_KIND.get(kind, kind)
    if kind not in _SQL_QUERY_KINDS:
        return None, f"non-sql source kind '{kind}'"
    headers = {"Content-Type": "application/json"}
    api_key = resolved.api_key or settings.mcp_service_api_key
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    if user_jwt:
        headers["X-User-JWT"] = user_jwt
    body = {
        "source_id": source_id,
        "dataset_id": ds.ref,
        "kind": kind,
        "query": sql,
        # row_limit is capped at 500 by the MCP's RunQueryRequest (le=500).
        "row_limit": max(1, min(int(row_limit), 500)),
    }
    url = _run_query_url(resolved.query_endpoint)
    timeout = resolved.query_timeout_seconds or 30.0
    try:
        client = get_http_client()
        resp = await client.post(url, json=body, headers=headers, timeout=timeout)
    except httpx.HTTPError as e:
        return None, f"transport: {e}"
    if resp.status_code >= 400:
        return None, f"{resp.status_code}: {resp.text[:160]}"
    try:
        payload = resp.json()
    except ValueError:
        return None, "non-JSON"
    if isinstance(payload, dict) and payload.get("error"):
        return None, str(payload["error"])
    return _coerce_rows(payload), None


def _metric_where(ds: DataSource, metric: Any) -> str:
    """Merged WHERE clause: data_source filters + the metric's own filter."""
    pred = {
        k: v
        for k, v in (ds.filters or {}).items()
        if k not in ("tool", "arguments", "rows", "query")
    }
    mf = getattr(metric, "filter", None)
    if isinstance(mf, dict):
        pred = {**pred, **mf}
    return _where_clause(pred)


async def _compute_one_metric(
    *,
    settings: Settings,
    ds: DataSource,
    table: str,
    metric: Any,
    auth_header: Optional[str],
) -> Optional[Dict[str, Any]]:
    """Compute one KPI metric: filtered value, optional prior-period delta,
    optional trend sparkline. Returns the metric dict or None (→ fallback)."""
    agg = (getattr(metric, "agg", "") or "").lower()

    # --- ratio: filtered count / base-population count ------------------
    # A rate (e.g. recovered cases / all cases). Both counts are pushed to the
    # source so the ratio is correct over the WHOLE table, not a capped fetch.
    # Numerator = data_source filters + the metric's own filter; denominator =
    # the data_source filters only (the base population the rate is "of").
    if agg == "ratio":
        num_where = _metric_where(ds, metric)
        base_pred = {
            k: v
            for k, v in (ds.filters or {}).items()
            if k not in ("tool", "arguments", "rows", "query")
        }
        den_where = _where_clause(base_pred)
        rsql = (
            f"SELECT (SELECT COUNT(*) FROM {table}{num_where}) AS num, "
            f"(SELECT COUNT(*) FROM {table}{den_where}) AS den"
        )
        rrows, rerr = await _run_sql_on_source(
            settings=settings, ds=ds, auth_header=auth_header, sql=rsql, row_limit=1,
        )
        if rerr or not rrows:
            logger.warning(
                "KPI ratio metric %r value query FAILED (sql=%r): %s",
                getattr(metric, "name", "?"), rsql, rerr or "no rows returned",
            )
            return {
                "name": getattr(metric, "name", "?"),
                "label": getattr(metric, "label", None),
                "field": getattr(metric, "field", None),
                "value": None, "delta": None, "trend": None, "trend_labels": None,
                "error": str(rerr or "query returned no rows"),
            }
        gr = _ci_getter(rrows[0])
        num, den = _num(gr("num")), _num(gr("den"))
        # value is a FRACTION (0..1); the renderer formats ratio tiles as a %.
        # A delta/trend on a ratio needs period-over-period rates (not derivable
        # from the simple compare/trend exprs) — leave them off rather than show
        # a misleading chip.
        return {
            "name": metric.name,
            "agg": metric.agg,
            "field": metric.field,
            "label": getattr(metric, "label", None),
            "value": (num / den) if (num is not None and den) else None,
            "delta": None, "trend": None, "trend_labels": None,
        }

    base = _agg_expr(metric)
    if base is None:
        return None
    where = _metric_where(ds, metric)

    # --- value (+ compare delta) in one query ---------------------------
    select_parts = [f"{base} AS value"]
    cmp = getattr(metric, "compare", None)
    cmp_curr = cmp_prev = None
    if cmp is not None:
        cmp_curr = _compare_expr(metric, _period_predicate(cmp.date_field, cmp.grain, 0, table, where))
        cmp_prev = _compare_expr(metric, _period_predicate(cmp.date_field, cmp.grain, cmp.periods, table, where))
        if cmp_curr and cmp_prev:
            select_parts.append(f"{cmp_curr} AS curr")
            select_parts.append(f"{cmp_prev} AS prev")
    vsql = f"SELECT {', '.join(select_parts)} FROM {table}{where}"
    rows, err = await _run_sql_on_source(
        settings=settings, ds=ds, auth_header=auth_header, sql=vsql, row_limit=1,
    )
    if err or not rows:
        logger.warning(
            "KPI metric %r value query FAILED (sql=%r): %s",
            getattr(metric, "name", "?"), vsql, err or "no rows returned",
        )
        # M2: surface the failure on the tile — a failed query must NOT render an
        # indistinguishable "—" that reads as a genuine zero. Return an error
        # sentinel (value=None + error) the renderer shows distinctly. (Reached
        # only for a real query failure; unsupported aggregations are pre-checked
        # in _resolve_dashboard_metrics and fall back wholesale.)
        return {
            "name": getattr(metric, "name", "?"),
            "label": getattr(metric, "label", None),
            "field": getattr(metric, "field", None),
            "value": None, "delta": None, "trend": None, "trend_labels": None,
            "error": str(err or "query returned no rows"),
        }
    g = _ci_getter(rows[0])
    value = _num(g("value"))
    delta = _delta(_num(g("curr")), _num(g("prev"))) if (cmp_curr and cmp_prev) else None

    # --- trend sparkline (separate grouped query) -----------------------
    trend: Optional[List[float]] = None
    trend_labels: Optional[List[str]] = None
    tr = getattr(metric, "trend", None)
    if tr is not None:
        col = _quote_ident(tr.date_field)
        tg = tr.grain if tr.grain in ("day", "week", "month") else "day"
        tsql = (
            f"SELECT date_trunc('{tg}', {col}) AS bucket, {base} AS v "
            f"FROM {table}{where} GROUP BY bucket ORDER BY bucket DESC "
            f"LIMIT {int(tr.points)}"
        )
        trows, terr = await _run_sql_on_source(
            settings=settings, ds=ds, auth_header=auth_header,
            sql=tsql, row_limit=tr.points,
        )
        if terr:
            logger.warning(
                "KPI metric %r trend query failed (sql=%r): %s",
                getattr(metric, "name", "?"), tsql, terr,
            )
        elif trows:
            # Keep bucket label aligned with each value so the runtime
            # sparkline can show "<date>: <value>" on hover (not a bare line).
            pairs: List[tuple] = []
            for r in reversed(trows):
                g = _ci_getter(r)
                v = _num(g("v"))
                if v is None:
                    continue
                pairs.append((_fmt_bucket_label(g("bucket")), v))
            if len(pairs) >= 2:
                trend = [v for _, v in pairs]
                trend_labels = [lbl for lbl, _ in pairs]

    return {
        "name": metric.name,
        "agg": metric.agg,
        "field": metric.field,
        "label": getattr(metric, "label", None),
        "value": value,
        "delta": delta,
        "trend": trend,
        "trend_labels": trend_labels,
    }


async def _resolve_dashboard_metrics(
    *,
    settings: Settings,
    app_spec: AppSpec,
    panel: Any,
    auth_header: Optional[str],
) -> Optional[List[Dict[str, Any]]]:
    """Compute every KPI metric via source-side aggregates (filtered value +
    optional prior-period delta + optional trend). Returns the metric list,
    or ``None`` to signal the caller to fall back to the row-fetch path
    (non-SQL source, ratio/unsupported metric, or any failure). Metrics run
    concurrently."""
    import asyncio

    async def _noop_metric():
        return None

    ds_by_id = {d.id: d for d in app_spec.data_sources}
    coros = []
    for m in panel.metrics:
        ds_id = getattr(m, "data_source", None)
        ds = ds_by_id.get(ds_id) if ds_id else None
        # Wholesale-bail to the row path ONLY for a panel the source can't
        # aggregate at all: a metric with no data_source, or a non-SQL source
        # (no dotted table ref → mongo/rag, where the row path does the counting).
        if ds is None:
            return None
        table = (
            ds.ref.split(".", 1)[1].strip() if "." in (ds.ref or "") else None
        )
        if not table:
            return None
        # SQL source, but THIS metric's aggregate can't be expressed source-side
        # (unsupported fn). Placeholder just this tile ("—"/unavailable) — do
        # NOT drag the whole SQL panel into capped client-side counting, which
        # would render a wrong number (saturated at the 500-row fetch) as if
        # genuine. A ratio IS source-computable, so it stays on this path.
        if not _metric_source_computable(m):
            coros.append(_noop_metric())
            continue
        coros.append(
            _compute_one_metric(
                settings=settings, ds=ds, table=table, metric=m,
                auth_header=auth_header,
            )
        )
    results = await asyncio.gather(*coros)
    # Per-metric isolation: a single metric whose value-query failed (e.g. a
    # bad/hallucinated column) must NOT blank the entire tile strip. Emit a
    # placeholder (value=None → the tile renders "—"/unavailable) for the
    # failed metric and keep every metric that DID compute. Only when EVERY
    # metric failed do we return None so the caller falls back to the row path.
    # (The pre-check above already wholesale-falls-back for non-SQL panels.)
    if all(r is None for r in results):
        return None
    out: List[Dict[str, Any]] = []
    for m, r in zip(panel.metrics, results):
        if r is not None:
            out.append(r)
        else:
            out.append({
                "name": getattr(m, "name", "?"),
                "agg": getattr(m, "agg", None),
                "field": getattr(m, "field", None),
                "label": getattr(m, "label", None),
                "value": None,
                "delta": None,
                "trend": None,
                "trend_labels": None,
            })
    return out


async def _resolve_mcp_rows(
    *,
    settings: Settings,
    ds: DataSource,
    auth_header: Optional[str],
    limit: int,
    panel: Optional[Any] = None,
    org_id: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], int, bool, Optional[str]]:
    """Fetch rows from a dept-MCP source.

    ``ds.ref`` is ``"<source_id>"`` or ``"<source_id>.<tool>"`` — the
    ``source_id`` is resolved against discovery to its query coordinates.
    Structured sources (mongodb / sql / …) are read via the dept-MCP
    ``/run_query``; semantic (RAG) sources are short-circuited to the
    Citra-Service platform reader (``/semantic/search``) — the dept-MCP
    serves no RAG.
    """
    from discovery_cache import DiscoveryError, resolve_source

    source_id = (ds.ref or "").split(".", 1)[0].strip()
    if not source_id:
        return [], 0, False, f"data_source '{ds.id}' (mcp) has an empty ref"

    user_jwt = (auth_header or "").removeprefix("Bearer ").strip() or None
    try:
        resolved = await resolve_source(
            discovery_url=settings.discovery_url_for(current_env()),
            user_jwt=user_jwt,
            source_id=source_id,
            cache_ttl_seconds=settings.discovery_cache_ttl_seconds,
        )
    except DiscoveryError as e:
        return [], 0, False, f"discovery could not resolve '{source_id}': {e}"

    kind = (resolved.source_type or "").lower()
    kind = _SOURCE_TYPE_TO_KIND.get(kind, kind)
    api_key = resolved.api_key or settings.mcp_service_api_key
    timeout = resolved.query_timeout_seconds or 30.0
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    # Forward the end-user's JWT to the MCP. Dept-MCPs run /run_query
    # with the running user's identity (for row-level visibility filters
    # and audit), not the smart-app-service's. The service API key in
    # `Authorization` only proves "the smart-app-service is allowed to
    # call you"; `X-User-JWT` answers "on whose behalf". Without this
    # header the MCP returns 401 "missing X-User-JWT".
    if user_jwt:
        headers["X-User-JWT"] = user_jwt

    # Panel ``filters`` minus the keys that are panel-renderer metadata,
    # not query predicates. What remains is the query expression.
    predicate = {
        k: v
        for k, v in (ds.filters or {}).items()
        if k not in ("tool", "arguments", "rows", "query")
    }
    # NOTE: panel-level ``query`` is intentionally NOT merged into the row
    # predicate. Doing so (a prior fix) started honoring filters that prod specs
    # had long left in panel.query and that were never validated as real column
    # predicates — e.g. dt_watchlist's {"status_in": [...]} (a non-existent
    # column + bare list) → a hard SQL error → 502. Filters come from ds.filters
    # (the documented predicate). Re-introducing panel.query honoring needs the
    # specs cleaned + value/column validation first.

    is_chart_agg = False
    _row_cap = min(limit, 500)   # the display cap. MUST stay ≤ 500 — the MCP's
    # RunQueryRequest validates row_limit le=500, so cap+1 would 422. The MCP
    # already fetches +1 internally and returns a `truncated` flag, so we use
    # that rather than over-fetching here.

    if kind in _SEMANTIC_SOURCE_TYPES:
        # Pure-document source — NL query endpoint.
        #
        # Smart query defaults:
        #   1. If the data source's filters carry an explicit `query`, use it.
        #   2. Else if the panel has a title, use that — Milvus + the
        #      embedding model produce real relevance scores for a domain
        #      phrase like "DT failure response policy" (~0.95), but
        #      collapse to noise (~0.0001) for `"*"`. So an empty/star
        #      query produces useless results; the panel title is a
        #      far better default.
        #   3. Else fall back to `"*"`.
        explicit_q = (ds.filters or {}).get("query")
        panel_title = getattr(panel, "title", None) if panel is not None else None
        query_text = (
            explicit_q
            if explicit_q and explicit_q != "*"
            else (panel_title or "*")
        )
        body: Dict[str, Any] = {
            "query": query_text,
            "source_id": source_id,
            "max_results": min(limit, 50),
        }
        # Forward panel-level corpus filters. `doc_types` on a
        # document_view panel scopes which doc categories the MCP
        # returns; without this the MCP returns chunks of any type
        # (sop, protocol, charter, …) and the filter chip UI lies.
        panel_doc_types = getattr(panel, "doc_types", None) if panel is not None else None
        if panel_doc_types:
            body["doc_types"] = list(panel_doc_types)
        elif (ds.filters or {}).get("doc_types"):
            body["doc_types"] = list((ds.filters or {})["doc_types"])
        panel_classification = (
            getattr(panel, "classification_max", None) if panel is not None else None
        )
        if panel_classification:
            body["classification_max"] = panel_classification
        url = resolved.query_endpoint
    else:
        # Structured source — catalogue /run_query. For mongodb the query
        # is a filter dict. SQL-family kinds (sql / duckdb / bigquery) need
        # a SELECT string — build one from the predicate + the table name
        # (the "<source>.<table>" tool segment of the ref).
        if kind in _SQL_QUERY_KINDS:
            table = (
                ds.ref.split(".", 1)[1].strip()
                if "." in (ds.ref or "") else None
            )
            # A chart that declares an aggregation gets a TRUE source-side
            # GROUP BY aggregate (so the panel does zero math); everything else
            # (queues, agg-less charts) gets the plain row SELECT.
            is_chart_agg = (
                table is not None
                and getattr(panel, "type", None) == "chart"
                and bool(getattr(panel, "aggregation", None))
                # A scatter is a raw point cloud — never GROUP BY-aggregate it,
                # even if the panel also declares an aggregation (mirrors the
                # guard in _resolve_chart_aggregated; the two decision sites
                # must agree).
                and (getattr(panel, "chart_type", None) or "").lower() != "scatter"
            )
            if is_chart_agg:
                query_val: Any = _build_chart_agg_sql(
                    table, panel, predicate, min(limit, 500)
                )
            elif table:
                query_val = _build_select_sql(
                    table, predicate, _row_cap,
                    columns=_required_columns(panel),
                    default_sort=getattr(panel, "default_sort", None),
                )
            else:
                query_val = ""
        else:
            query_val = predicate
        body = {
            "source_id": source_id,
            "dataset_id": ds.ref,
            "kind": kind,
            "query": query_val,
            # ≤ 500 always (MCP validates le=500). Truncation comes from the
            # MCP's own `truncated` flag, not an over-limit probe here.
            "row_limit": _row_cap,
        }
        url = _run_query_url(resolved.query_endpoint)

    if kind in _SEMANTIC_SOURCE_TYPES:
        # RAG short-circuit: a semantic corpus is answered by the Citra-Service
        # platform reader (Milvus direct), NEVER the dept-MCP (which serves no RAG
        # and would 404 "Unknown source"). Reuse the query / doc_types /
        # classification already assembled in `body`; chunks keep the
        # {text, score, metadata} shape the MCP path returned, so the row coercion
        # below is unchanged. `doc_path` (ds.filters) reads a WHOLE document, ordered.
        from proxy_clients import call_citra_semantic_search, ProxyError as _PErr
        _dp = (ds.filters or {}).get("doc_path")
        _dp = _dp.strip() if isinstance(_dp, str) and _dp.strip() else None
        _filters: Dict[str, Any] = {}
        if body.get("doc_types"):
            _filters["doc_type"] = list(body["doc_types"])
        # NOTE: classification_max is a CEILING (include every level up to it), not
        # an equality — the platform reader's filter builder only does equality/in,
        # so we do NOT map it here (an equality filter would be wrong AND, since demo
        # collections carry no `classification` field, would blank the panel).
        # Classification-ceiling enforcement is a KNOWN GAP in the short-circuit
        # reader (see review notes) — it must expand the ceiling via the source
        # taxonomy's ordered levels once chunks carry a classification field.
        _q = body.get("query")
        _mr = int(body.get("max_results") or 50)
        try:
            _res = await call_citra_semantic_search(
                settings=settings, user_jwt=user_jwt, source_id=source_id,
                query=("" if _q == "*" else (_q or "")),
                top_k=_mr, filters=_filters or None, doc_path=_dp,
                # A panel loaded by a trigger/agent (or a runtime service token) has
                # no end-user JWT — pass the app's org so a service token can be
                # minted; else a semantic panel 401s where a structured one succeeds.
                org_id=org_id,
            )
        except _PErr as e:
            return [], 0, False, f"semantic {source_id}: {e}"
        _chunks = [c for c in (_res.get("chunks") or []) if isinstance(c, dict)]
        # EMPTY-BECAUSE-FILTERED vs EMPTY-CORPUS — do not let these look identical.
        # A doc_types filter matches nothing when the corpus is UNTAGGED (chunks
        # carry doc_type=""), which renders as a bare "No documents found" and is
        # indistinguishable from "the library is empty". That cost real debugging
        # time in prod (acme Policy Library: 12 retrievable docs, all untagged,
        # behind a panel filtering `charter` → panel blank, error null). So when a
        # filtered query comes back empty, re-ask WITHOUT the filter and say which
        # it is. Costs one extra query only on the already-empty path.
        if not _chunks and _filters:
            try:
                _all = await call_citra_semantic_search(
                    settings=settings, user_jwt=user_jwt, source_id=source_id,
                    query=("" if _q == "*" else (_q or "")), top_k=_mr,
                    filters=None, doc_path=_dp, org_id=org_id,
                )
                _n_all = len(_all.get("chunks") or [])
            except _PErr:
                _n_all = 0
            if _n_all:
                _dts = ", ".join(body.get("doc_types") or [])
                _untagged = sum(
                    1 for c in (_all.get("chunks") or [])
                    if isinstance(c, dict)
                    and not ((c.get("metadata") or {}).get("doc_type") or "").strip()
                )
                # BENIGN-tagged: an over-narrow filter is a valid empty state, NOT a
                # source failure. The note slot is failure-only by contract (the
                # caller wraps it with _as_source_error → 502), so say so explicitly.
                return [], 0, False, BENIGN_NOTE_MARKER + (
                    f"0 of {_n_all} document(s) in '{source_id}' match doc_types "
                    f"[{_dts}]"
                    + (f" — {_untagged} are UNTAGGED (doc_type empty): they were "
                       f"uploaded without a document category, so no doc_types "
                       f"filter can ever match them."
                       if _untagged else
                       " — the corpus is tagged with other categories.")
                )
        payload: Any = {"results": _chunks, "truncated": len(_chunks) >= _mr}
    else:
        try:
            client = get_http_client()
            resp = await client.post(url, json=body, headers=headers, timeout=timeout)
        except httpx.HTTPError as e:
            return [], 0, False, f"mcp transport: {e}"

        if resp.status_code >= 400:
            return (
                [], 0, False,
                f"mcp {source_id} ({kind or 'semantic'}) returned "
                f"{resp.status_code}: {resp.text[:160]}",
            )

        try:
            payload = resp.json()
        except ValueError:
            return [], 0, False, f"mcp {source_id} returned non-JSON"

    # /run_query surfaces backend failures in an ``error`` field.
    if isinstance(payload, dict) and payload.get("error"):
        return [], 0, False, f"mcp {source_id}: {payload['error']}"

    fetched = _coerce_rows(payload)
    if not fetched and _is_unrenderable_object(payload):
        # A non-columnar response (e.g. a REST/API source returning a single
        # JSON object like {"credit_score": 750, ...}). We can't render it as
        # rows — surface a clear note instead of a silent-empty panel (fail
        # loud, not a silent []). Legit empty results carry rows/total/etc. and
        # never reach here.
        keys = list(payload.keys())[:12] if isinstance(payload, dict) else type(payload).__name__
        logger.warning("mcp %s: non-columnar response (keys=%s) — not renderable as rows",
                       source_id, keys)
        return [], 0, False, (
            f"mcp {source_id}: the source returned a single object, not a row list, so it "
            "cannot back a table/queue panel. Read it via an agent tool (NL query) — or have "
            'the dept-MCP wrap the response as {"rows": [...]}.'
        )
    if is_chart_agg:
        # Aggregated chart rows — the bucket cap / truncation is handled by the
        # chart aggregate path, not the row ceiling.
        return fetched, len(fetched), False, None
    # Trust the MCP's own truncation flag (it fetches row_limit+1 internally).
    # Fall back to a row-count heuristic only if the response omits it.
    mcp_truncated = payload.get("truncated") if isinstance(payload, dict) else None
    rows = fetched[:_row_cap]
    truncated = bool(mcp_truncated) if mcp_truncated is not None else (len(fetched) >= _row_cap)
    return rows, len(rows), truncated, None


def _coerce_rows(body: Any) -> List[Dict[str, Any]]:
    """Best-effort conversion of an MCP response into a row list.

    Common shapes accepted:
      * ``[{...}, {...}]``                 — already a list of dicts
      * ``{"rows": [...]}``                — common
      * ``{"items": [...]}``               — common
      * ``{"data": [...]}``                — common
      * ``{"result": {"rows": [...]}}``    — nested envelope
    """
    if isinstance(body, list):
        return [r for r in body if isinstance(r, dict)]
    if isinstance(body, dict):
        for key in ("rows", "items", "data", "results"):
            v = body.get(key)
            if isinstance(v, list):
                return [r for r in v if isinstance(r, dict)]
        result = body.get("result")
        if isinstance(result, dict):
            return _coerce_rows(result)
        if isinstance(result, list):
            return _coerce_rows(result)
    return []


# Keys that mean "this IS a (possibly empty) row/list response" or an error we
# already handle — their presence makes an empty coercion a LEGITIMATE empty
# result, not an unrenderable object. Deliberately EXCLUDES pure metadata like
# total/count/truncated: a single object may legitimately carry a field named
# "count" (e.g. {"credit_score":750,"count":3}), which must still be flagged
# unrenderable rather than silently rendering blank.
_ROW_ENVELOPE_KEYS = ("rows", "items", "data", "results", "result", "error")


def _is_unrenderable_object(payload: Any) -> bool:
    """True when the MCP returned a non-empty object that is NOT a row envelope.

    Distinguishes a genuine empty result (``{"rows": [], "total": 0}``) — which
    must render as an empty panel — from a single JSON object (a REST/API source
    return like ``{"credit_score": 750, "status": "verified"}``) that cannot back
    a columnar panel and should surface a clear note rather than silently vanish.
    """
    if not isinstance(payload, dict) or not payload:
        return False
    return not any(k in payload for k in _ROW_ENVELOPE_KEYS)


async def _resolve_rag_rows(
    *,
    settings: Settings,
    ds: DataSource,
    auth_header: Optional[str],
    limit: int,
    panel: Optional[Any] = None,
    org_id: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], int, bool, Optional[str]]:
    """Resolve a RAG corpus to documents via the Citra-Service platform reader.

    RAG corpora are registered in discovery with ``source_type='semantic'``.
    RAG short-circuit: they are answered by Citra-Service (``POST /semantic/search``,
    Milvus direct), NEVER the dept-MCP (which serves no RAG). Delegating to
    ``_resolve_mcp_rows`` reuses one path — discovery lookup + panel-level filter
    forwarding (doc_types, classification_max, doc_path) — whose semantic branch
    now routes to the platform reader.
    """
    return await _resolve_mcp_rows(
        settings=settings, ds=ds, auth_header=auth_header, limit=limit, panel=panel,
        org_id=org_id,
    )


async def _rows_for_data_source(
    *,
    settings: Settings,
    app_spec: AppSpec,
    ds: DataSource,
    auth_header: Optional[str],
    limit: int,
    panel: Optional[Any] = None,
    viewer_scope: Optional[Dict[str, Any]] = None,
) -> Tuple[List[Dict[str, Any]], int, bool, str, Optional[str]]:
    """Resolve raw rows for one DataSource regardless of panel.

    Returns ``(rows, total, truncated, source_kind, note)``. Shared by
    ``resolve_panel_data`` (queue / chart / dashboard / document_view) and
    ``resolve_detail_data`` (the linked record + ``documents`` sections).

    The optional ``panel`` argument lets semantic sources read panel-level
    hints (title, doc_types, classification_max) so the implicit query
    and the corpus filters are more useful than a bare ``"*"``.
    """
    if ds.type == "static":
        rows = _resolve_static_rows(ds)
        total = len(rows)
        return rows[:limit], total, total > limit, "static", None
    _org_id = getattr(app_spec, "tenant_id", None)
    if ds.type == "mcp":
        rows, total, truncated, note = await _resolve_mcp_rows(
            settings=settings, ds=ds, auth_header=auth_header, limit=limit, panel=panel,
            org_id=_org_id,
        )
        # _resolve_mcp_rows returns a note ONLY on failure (discovery/transport/
        # status/error). Mark it as a SOURCE FAILURE so the user-facing endpoint
        # fails loud (502) instead of laundering "MCP down / access denied" into
        # a benign "no rows" empty state (RULE #1). The marker keeps the original
        # text (and the smoke-test substrings) intact.
        return rows, total, truncated, "mcp", _as_source_error(note)
    if ds.type == "rag":
        rows, total, truncated, note = await _resolve_rag_rows(
            settings=settings, ds=ds, auth_header=auth_header, limit=limit, panel=panel,
            org_id=_org_id,
        )
        return rows, total, truncated, "rag", _as_source_error(note)
    if ds.type == "smart_app_records":
        rows, total, truncated, note = await _resolve_smart_app_records_rows(
            app_spec=app_spec, ds=ds, limit=limit
        )
        return rows, total, truncated, "smart_app_records", note
    if ds.type == "workflow_staging":
        rows, total, truncated, note = await _resolve_workflow_staging_rows(
            app_spec=app_spec, ds=ds, auth_header=auth_header, limit=limit,
            viewer_scope=viewer_scope,
        )
        return rows, total, truncated, "workflow_staging", note
    if ds.type == "decision_ledger":
        rows, total, truncated, note = await _resolve_decision_ledger_rows(
            app_spec=app_spec, ds=ds, limit=limit
        )
        return rows, total, truncated, "decision_ledger", note
    return (
        [],
        0,
        False,
        ds.type,
        (
            f"data_source type '{ds.type}' is not yet wired; embed rows in "
            "a 'static' data_source for now."
        ),
    )


def _chart_agg_func(panel_agg: Optional[str]) -> str:
    a = (panel_agg or "sum").lower()
    return a if a in ("sum", "avg", "min", "max", "count") else "sum"


async def _resolve_chart_aggregated(
    *,
    settings: Settings,
    ds: DataSource,
    panel: Any,
    auth_header: Optional[str],
) -> Optional[Tuple[List[Dict[str, Any]], List[str]]]:
    """Push a chart's GROUP BY aggregate to the source.

    A bar/line/area/pie over a large table must aggregate by ``x`` (and the
    optional ``group_by`` series) at the source — the raw fetch is capped at
    500 rows AND the frontend mapper is last-row-wins, so a client-side chart
    is wrong beyond one row per category. Returns (rows, columns) where each
    row is one (x[, group_by]) bucket with the true aggregate, or ``None`` to
    fall back to the raw-row path (non-SQL source / unaggregatable / error).
    """
    # A scatter is a point cloud — each row is a raw (x, y) point. Aggregating
    # it (GROUP BY x → SUM(y)) collapses the cloud to one point per x, which is
    # wrong. Fall through to the raw-row path for scatter.
    if (getattr(panel, "chart_type", None) or "").lower() == "scatter":
        return None
    table = ds.ref.split(".", 1)[1].strip() if "." in (ds.ref or "") else None
    if not table:
        return None
    x = getattr(panel, "x", None)
    y = getattr(panel, "y", None)
    if not x or not y:
        return None
    agg = _chart_agg_func(getattr(panel, "aggregation", None))
    grain = getattr(panel, "time_grain", None)
    group_by = getattr(panel, "group_by", None)
    y_fields = list(y) if isinstance(y, list) else [y]
    if group_by:
        # The frontend maps a single measure when group_by is set.
        y_fields = y_fields[:1]

    x_expr = (
        f"date_trunc('{grain}', {_quote_ident(x)})"
        if grain in ("minute", "hour", "day", "week", "month", "quarter", "year")
        else _quote_ident(x)
    )

    def _measure(fld: str) -> str:
        if agg == "count":
            return f"COUNT(*) AS {_quote_ident(fld)}"
        return f"{agg.upper()}({_quote_ident(fld)}) AS {_quote_ident(fld)}"

    select_parts = [f"{x_expr} AS {_quote_ident(x)}"]
    group_parts = [x_expr]
    if group_by:
        select_parts.append(f"{_quote_ident(group_by)} AS {_quote_ident(group_by)}")
        group_parts.append(_quote_ident(group_by))
    select_parts.extend(_measure(f) for f in y_fields)

    # Predicate: data_source filters + the panel's own query (the latter was
    # previously dropped — that's why a chart's status='active' filter was a
    # no-op). Strip non-predicate keys (order_by / limit are renderer hints).
    pred = {
        k: v
        for k, v in (ds.filters or {}).items()
        if k not in ("tool", "arguments", "rows", "query")
    }
    pq = getattr(panel, "query", None)
    if isinstance(pq, dict):
        pred = {**pred, **{k: v for k, v in pq.items()
                           if k not in ("order_by", "limit", "tool", "arguments")}}

    # Cap buckets to a chart-legible count (was 200 default / 500 max — far too
    # dense to read). A high-cardinality dimension or a long daily series is
    # truncated to the meaningful slice, not dumped.
    cap = min(int(getattr(panel, "limit", None) or _CHART_BUCKET_CAP), _CHART_BUCKET_CAP)
    # M6: fetch ONE extra bucket so we can tell a COMPLETE distribution from a
    # TRUNCATED one (a high-cardinality x/group_by). Without this we pass a
    # top-N slice off as the whole chart (truncated=False on a huge distribution).
    probe = cap + 1
    inner = (
        f"SELECT {', '.join(select_parts)} FROM {table}{_where_clause(pred)} "
        f"GROUP BY {', '.join(group_parts)}"
    )
    if grain:
        # Time series: keep the MOST RECENT periods, displayed oldest -> newest.
        sql = f"SELECT * FROM ({inner} ORDER BY {x_expr} DESC LIMIT {probe}) _w ORDER BY 1 ASC"
    else:
        # Categorical: keep the top buckets by the first measure (drop the long tail).
        sql = f"{inner} ORDER BY {_quote_ident(y_fields[0])} DESC LIMIT {probe}"
    rows, err = await _run_sql_on_source(
        settings=settings, ds=ds, auth_header=auth_header, sql=sql, row_limit=probe,
    )
    if err or rows is None:
        logger.warning(
            "chart %r source-aggregation fell back to raw rows (sql=%r): %s",
            getattr(panel, "id", "?"), sql, err or "no rows",
        )
        return None
    truncated = len(rows) > cap
    if truncated:
        # time-series rows are ASC (old->new) → keep the most recent cap;
        # categorical rows are value-DESC → keep the top cap.
        rows = rows[-cap:] if grain else rows[:cap]
    columns = [x] + ([group_by] if group_by else []) + list(y_fields)
    return rows, columns, truncated


async def resolve_field_options(
    *,
    settings: Settings,
    ds: DataSource,
    value_column: str,
    label_column: Optional[str] = None,
    field_filter: Optional[Dict[str, Any]] = None,
    limit: int = 200,
    auth_header: Optional[str] = None,
    search: Optional[str] = None,
) -> Optional[List[Dict[str, Any]]]:
    """Resolve a select/lookup field's choices from a data_source — the live
    DISTINCT values of ``value_column`` (with an optional ``label_column``),
    scoped by ``field_filter``. Returns ``[{value, label}]`` or None on a
    non-SQL source / error. This is what prepopulates an override combo so the
    officer can pick any valid option.

    ``search`` (typeahead): when non-empty, narrows to rows whose value or
    label CONTAINS the term (case-insensitive), pushed down as a portable
    LOWER(..) LIKE so a high-cardinality dimension stays usable.
    """
    table = ds.ref.split(".", 1)[1].strip() if "." in (ds.ref or "") else None
    if not table or not value_column:
        return None
    vcol = _quote_ident(value_column)
    select = vcol + (
        f", {_quote_ident(label_column)}"
        if label_column and label_column != value_column else ""
    )
    conds = [f"{vcol} IS NOT NULL"]
    for col, val in (field_filter or {}).items():
        conds.append(_col_condition(col, val))
    if search and search.strip():
        # Escape LIKE wildcards + the SQL string literal, then match
        # case-insensitively on value (and label, if distinct).
        esc = (
            search.strip()
            .replace("\\", "\\\\")
            .replace("%", "\\%")
            .replace("_", "\\_")
            .replace("'", "''")
        )
        like_cols = [f"LOWER({vcol}) LIKE LOWER('%{esc}%') ESCAPE '\\'"]
        if label_column and label_column != value_column:
            like_cols.append(
                f"LOWER({_quote_ident(label_column)}) LIKE LOWER('%{esc}%') ESCAPE '\\'"
            )
        conds.append("(" + " OR ".join(like_cols) + ")")
    where = " WHERE " + " AND ".join(conds)
    n = max(1, min(int(limit or 200), 500))
    sql = (
        f"SELECT DISTINCT {select} FROM {table}{where} "
        f"ORDER BY {vcol} LIMIT {n}"
    )
    rows, err = await _run_sql_on_source(
        settings=settings, ds=ds, auth_header=auth_header, sql=sql, row_limit=n,
    )
    if err or rows is None:
        logger.warning("field-options query failed (sql=%r): %s", sql, err)
        return None
    out: List[Dict[str, Any]] = []
    for r in rows:
        g = _ci_getter(r)
        v = g(value_column)
        if v is None:
            continue
        lbl = g(label_column) if label_column else None
        out.append({"value": v, "label": str(lbl) if lbl is not None else None})
    return out


# ---------------------------------------------------------------------------
# Page-param interpolation — a filter_bar control (or a navigated detail page)
# sets URL params; a data_source's ``filters`` may reference them as
# ``{param.<name>}`` (``${param.<name>}`` also accepted). We substitute the
# live value before the predicate is pushed to the source. A filter whose
# value is ENTIRELY one param ref AND the param is empty/absent (the "All"
# selection) is DROPPED — empty selection means no filter, never an empty-string
# match. RULE #1: an embedded-but-missing token resolves to "" and is logged.
# ---------------------------------------------------------------------------

_PARAM_TOKEN = re.compile(r"\$?\{param\.([a-zA-Z0-9_]+)\}")


def _interp_scalar(val: Any, page_params: Dict[str, str]) -> Any:
    """Substitute every ``{param.x}`` token in a string. A non-string passes
    through. A missing/empty param resolves to "" (and is logged) so a literal
    ``{param.x}`` token can NEVER survive into the query (silent wrong-data
    guard, RULE #1)."""
    if not isinstance(val, str):
        return val

    def _sub(m: "re.Match[str]") -> str:
        name = m.group(1)
        v = page_params.get(name)
        if v is None or v == "":
            logger.warning("panel filter references unset param %r", name)
            return ""
        return str(v)

    return _PARAM_TOKEN.sub(_sub, val)


def _interp_deep(val: Any, page_params: Dict[str, str]) -> Any:
    """Recurse through dict/list filter structures (e.g. range operators like
    ``{"$gte": "{param.from}"}``) so a nested token is substituted too — never
    left as a literal that would query the wrong rows."""
    if isinstance(val, dict):
        return {k: _interp_deep(v, page_params) for k, v in val.items()}
    if isinstance(val, list):
        return [_interp_deep(v, page_params) for v in val]
    return _interp_scalar(val, page_params)


def _interp_filters(
    filters: Optional[Dict[str, Any]], page_params: Dict[str, str]
) -> Optional[Dict[str, Any]]:
    if not filters:
        return filters
    out: Dict[str, Any] = {}
    for k, v in filters.items():
        # Top-level "All" semantics: a condition whose whole value is a single
        # param ref AND the param is empty/absent is DROPPED — unselected means
        # no filter, never ``= ''``. (Nested tokens substitute to "" instead.)
        if isinstance(v, str):
            whole = _PARAM_TOKEN.fullmatch(v.strip())
            if whole and (page_params.get(whole.group(1)) in (None, "")):
                continue
        out[k] = _interp_deep(v, page_params)
    return out


def _interp_app_spec_params(
    app_spec: AppSpec, page_params: Dict[str, str]
) -> AppSpec:
    """Return ``app_spec`` with every data_source's ``filters`` interpolated
    against ``page_params``. Unchanged when no filter references a param."""
    new_sources = []
    changed = False
    for ds in (app_spec.data_sources or []):
        if ds.filters:
            ni = _interp_filters(ds.filters, page_params)
            if ni != ds.filters:
                changed = True
                new_sources.append(ds.model_copy(update={"filters": ni}))
                continue
        new_sources.append(ds)
    if not changed:
        return app_spec
    return app_spec.model_copy(update={"data_sources": new_sources})


async def _attach_staged_recommendations(
    app_spec: "AppSpec", panel: Any, rows: List[Dict[str, Any]]
) -> None:
    """Join each SoR-backed queue row to its PENDING staged recommendation.

    The run result used to live only in the React state of the tab that
    clicked Review — a fresh tab (or another officer) saw a bare card and had
    to open the detail page to discover a recommendation was waiting. The
    staging rows are the durable truth, so attach them here under the
    established ``_recommendation`` contract (decision / planned_writes /
    composite correlation_id / plan_hash) and the existing card UX renders the
    chip + instant Apply/Reject modal.

    Match: on the row's ID COLUMN ONLY (``_detect_id_field`` — the same
    detection the detail panel uses). ``display_context`` is the run's full
    input row, so any-shared-key matching would join on value columns too —
    live-hit: every card matched the first staging row via ``status: fail``.
    Newest staging row wins per record. Best-effort enrich — a join failure
    degrades VISIBLY in the log, never the panel.
    """
    if getattr(panel, "type", None) != "queue" or not rows:
        return
    # Only queues that can fire (or navigate to) agent work carry reviews.
    _acts = list(getattr(panel, "actions", None) or [])
    if not any(getattr(a, "agent_action", None) or getattr(a, "navigate", None)
               for a in _acts):
        return
    slug = getattr(app_spec, "slug", None)
    if not slug:
        return
    if any("_recommendation" in r for r in rows):
        return  # staging-backed source — already carries the real thing
    try:
        import main as _main_mod

        col = _main_mod.get_workflow_staging_col()
        staged = await col.find(
            {"slug": slug, "status": "pending_review"},
            {"display_context": 1, "llm_recommendation_text": 1,
             "llm_reasoning": 1, "llm_evidence_summary": 1,
             "planned_writes": 1, "cited_precedents": 1, "status": 1,
             "workflow_execution_id": 1, "case_natural_key": 1,
             "created_at": 1},
        ).sort("created_at", -1).limit(200).to_list(length=200)
    except Exception as exc:  # noqa: BLE001 — enrich failure must not kill the panel
        logger.warning("[panel] staged-recommendation join failed: %s", exc)
        return
    if not staged:
        return
    id_col = _detect_id_field(list(rows[0].keys()), None)
    if not id_col:
        return
    for row in rows:
        rid = row.get(id_col)
        if rid in (None, ""):
            continue
        for s in staged:
            dc = s.get("display_context") or {}
            if str(dc.get(id_col)) != str(rid):
                continue
            row["_recommendation"] = {
                "decision": s.get("llm_recommendation_text"),
                "reasoning": s.get("llm_reasoning"),
                "evidence": s.get("llm_evidence_summary"),
                "planned_writes": s.get("planned_writes") or [],
                "cited_precedents": s.get("cited_precedents") or [],
                "status": s.get("status") or "pending_review",
                "correlation_id": (
                    f"{s.get('workflow_execution_id')}:{s.get('case_natural_key')}"
                ),
                "plan_hash": compute_plan_hash(s.get("planned_writes") or []),
            }
            break  # newest-first → first hit is the latest recommendation


async def resolve_panel_data(
    *,
    settings: Settings,
    app_spec: AppSpec,
    panel_id: str,
    auth_header: Optional[str] = None,
    viewer_scope: Optional[Dict[str, Any]] = None,
    page_params: Optional[Dict[str, str]] = None,
) -> PanelDataResponse:
    # filter_bar / navigate params flow into the data_source predicate here, so
    # every backend path (dashboard metrics, chart aggregate, raw rows) filters
    # on the current selection.
    if page_params:
        app_spec = _interp_app_spec_params(app_spec, page_params)
    panel = _find_panel(app_spec, panel_id)

    # Dashboard (KPI) + stat_strip panels: compute aggregates at the source so
    # COUNT/SUM reflect the WHOLE table, not the capped row fetch. Falls
    # through to the row path for non-SQL sources or ratio metrics. A hero's
    # single headline metric rides the same path via a metrics shim.
    _p_type = getattr(panel, "type", None)
    if _p_type in ("dashboard", "stat_strip") or (
        _p_type == "hero" and getattr(panel, "metric", None) is not None
    ):
        _metric_panel = panel
        if _p_type == "hero":
            from types import SimpleNamespace
            _metric_panel = SimpleNamespace(metrics=[panel.metric])
        metrics = await _resolve_dashboard_metrics(
            settings=settings,
            app_spec=app_spec,
            panel=_metric_panel,
            auth_header=auth_header,
        )
        if metrics is not None:
            return PanelDataResponse(
                panel_id=panel_id,
                data_source=_panel_data_source_id(panel) or "",
                columns=[],
                rows=[],
                total=len(metrics),
                truncated=False,
                source_kind="mcp",
                metrics=metrics,
                note=None,
            )
    # A hero with no metric binds no data at all — static chrome.
    if _p_type == "hero" and getattr(panel, "metric", None) is None:
        return PanelDataResponse(
            panel_id=panel_id, data_source="", columns=[], rows=[],
            total=0, truncated=False, source_kind="static", metrics=None,
            note=None,
        )

    ds_id = _panel_data_source_id(panel)
    if not ds_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"panel '{panel_id}' has no data_source binding",
        )
    ds = _find_data_source(app_spec, ds_id)

    # Chart panels: push the GROUP BY aggregate to the source so the viz is
    # correct over the WHOLE table (the raw fetch is capped at 500 and the
    # client mapper is last-row-wins). Falls back to raw rows for non-SQL
    # sources or if the aggregate can't be built.
    if getattr(panel, "type", None) == "chart":
        agg = await _resolve_chart_aggregated(
            settings=settings, ds=ds, panel=panel, auth_header=auth_header,
        )
        if agg is not None:
            rows, columns, agg_truncated = agg
            return PanelDataResponse(
                panel_id=panel_id,
                data_source=ds.id,
                columns=columns,
                rows=rows,
                total=len(rows),
                truncated=agg_truncated,  # M6: honest — True when buckets were cut
                source_kind="mcp",
                note=(
                    f"Showing the top {len(rows)} groups — more exist; narrow the "
                    f"filter or use a coarser time grain for a complete view."
                    if agg_truncated else None
                ),
            )

    limit = _panel_limit(panel)
    cols = _required_columns(panel)

    rows, total, truncated, source_kind, note = await _rows_for_data_source(
        settings=settings,
        app_spec=app_spec,
        ds=ds,
        auth_header=auth_header,
        limit=limit,
        panel=panel,
        viewer_scope=viewer_scope,
    )
    rows, columns = _project_columns(rows, cols)
    # PERSISTENT pending-review badges: a queue row whose case has a staged
    # (not-yet-decided) AI recommendation carries it as `_recommendation` —
    # the SAME contract the workflow-staging panel emits — so ANY officer in
    # ANY tab sees the chip and gets the instant Apply/Reject modal, instead
    # of the run result living only in the tab that fired it.
    await _attach_staged_recommendations(app_spec, panel, rows)
    # A source FAILURE note (MCP down / unresolved / transport) is surfaced in
    # the explicit ``error`` channel so the endpoint can fail loud — never
    # laundered into a benign empty-state ``note`` (RULE #1).
    error: Optional[str] = None
    if is_source_failure_note(note):
        error = strip_source_failure_marker(note)
        note = None
    elif note and note.startswith(BENIGN_NOTE_MARKER):
        # A resolver-explained empty state (e.g. every doc filtered out) — show it
        # as a note, not an error.
        note = note[len(BENIGN_NOTE_MARKER):]
    return PanelDataResponse(
        panel_id=panel_id,
        data_source=ds.id,
        columns=columns,
        rows=rows,
        total=total,
        truncated=truncated,
        source_kind=source_kind,  # type: ignore[arg-type]
        note=note,
        error=error,
    )


# ---------------------------------------------------------------------------
# Detail panel — resolve one record + per-section data
# ---------------------------------------------------------------------------

# Column-name patterns, most-specific first, used to auto-detect which
# column on the linked queue identifies a record when DetailPanel.id_field
# is not set explicitly.
_ID_FIELD_PATTERNS = (
    r"^record_id$",
    r"^id$",
    r".*_id$",
    r".*_no$",
    r".*_number$",
    r".*_code$",
)


def _detect_id_field(columns: List[str], explicit: Optional[str]) -> Optional[str]:
    if explicit:
        return explicit
    for pat in _ID_FIELD_PATTERNS:
        for c in columns:
            if re.match(pat, c, re.IGNORECASE):
                return c
    return columns[0] if columns else None


def _match_record(
    rows: List[Dict[str, Any]],
    record_id: Optional[str],
    id_field: Optional[str],
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Find the row identified by ``record_id``.

    Matches on the (detected or explicit) id column first; falls back ONLY to
    other id-like columns (``_ID_FIELD_PATTERNS``) so a slightly mis-named
    ``id_field`` still resolves. It deliberately does NOT match against
    arbitrary value columns: that old fallback could return a DIFFERENT record
    whose unrelated column (a priority, an amount, a foreign key) happened to
    equal the id string — silently mixing one card's details into another.
    A genuine miss returns ``(None, field)`` → the UI shows "Record not found".
    Returns ``(record, id_field_used)``.
    """
    if not rows:
        return None, id_field
    columns = list(rows[0].keys())
    field = _detect_id_field(columns, id_field)
    if record_id is None:
        return rows[0], field
    rid = str(record_id)
    if field:
        for r in rows:
            if str(r.get(field)) == rid:
                return r, field
    # Lenient fallback — id-like columns ONLY (never arbitrary values).
    id_cols = [
        c for c in columns
        if c != field and any(re.match(p, c) for p in _ID_FIELD_PATTERNS)
    ]
    for r in rows:
        for c in id_cols:
            if str(r.get(c)) == rid:
                return r, c
    return None, field


def _inputs_contains(inputs: Any, record_id: str) -> bool:
    """True when ``record_id`` appears anywhere in a run's ``inputs`` dict."""
    if not isinstance(inputs, dict):
        return False
    return any(str(v) == record_id for v in inputs.values())


_RUN_TIMELINE_FIELDS = {
    "_id": 0,
    "correlation_id": 1,
    "created_at": 1,
    "action": 1,
    "status": 1,
    "decision": 1,
    "reasoning": 1,
    "duration_ms": 1,
    "model": 1,
    "inputs": 1,
    "requested_by": 1,
}


def _trim_reasoning(text: str, cap: int = 400) -> str:
    """Keep a timeline entry's reasoning concise.

    A run whose model skipped (or truncated) the audit block stored its ENTIRE
    raw reply as ``reasoning`` — markdown tables plus the internal ```json audit
    block. Strip fenced code blocks and cap length so the history shows a short
    'why', not the whole recommendation. (New runs are trimmed at source in the
    runtime's ``_extract_audit_block`` fallback; this also cleans older rows.)
    """
    import re as _re
    if not isinstance(text, str):
        return text
    s = _re.sub(r"```.*?```", "", text, flags=_re.DOTALL)   # closed fenced blocks
    s = _re.sub(r"```.*$", "", s, flags=_re.DOTALL)          # unterminated fence
    s = _re.sub(r"\s+", " ", s).strip()
    return (s[:cap].rstrip() + "…") if len(s) > cap else s


async def _fetch_record_runs(
    app_id: Optional[str], record_id: Optional[str], limit: int = 25
) -> List[Dict[str, Any]]:
    """Audit-trail runs for the agent_timeline section.

    Returns runs for this record when ``record_id`` matches a run's
    inputs; otherwise the app's most-recent runs (so the section is never
    empty for a freshly-opened record). Scoped by ``app_id`` only — the
    detail endpoint already gated app access, and ``app_id`` is globally
    unique, so a tenant filter (which an end-user-less runtime run never
    carries) would wrongly hide rows.
    """
    if not app_id:
        return []
    try:
        from main import get_app_run_audit_col
    except ImportError:  # pragma: no cover
        return []
    cursor = (
        get_app_run_audit_col()
        .find({"app_id": app_id}, _RUN_TIMELINE_FIELDS)
        .sort("created_at", -1)
        .limit(80)
    )
    raw = [r async for r in cursor]
    if record_id:
        # Always scope the timeline to the SELECTED record. The old behaviour
        # fell back to the app's all-records runs when this record had none,
        # which made a freshly-opened record (e.g. OUT-00000004) show every
        # OTHER record's history. An empty timeline is correct for a record with
        # no runs (the UI renders "No agent runs recorded yet").
        raw = [r for r in raw if _inputs_contains(r.get("inputs"), str(record_id))]
    out = raw[:limit]
    for r in out:
        if r.get("reasoning"):
            r["reasoning"] = _trim_reasoning(r["reasoning"])
    return out


async def _fetch_pending_runs(
    app_id: Optional[str], record_id: Optional[str], limit: int = 25
) -> List[Dict[str, Any]]:
    """Pending-approval runs for the approval section."""
    if not app_id:
        return []
    try:
        from main import get_pending_runs_col
    except ImportError:  # pragma: no cover
        return []
    query: Dict[str, Any] = {
        "app_id": app_id,
        "status": "pending_approval",
    }
    cursor = (
        get_pending_runs_col()
        .find(
            query,
            {
                "_id": 0,
                "correlation_id": 1,
                "action": 1,
                "inputs": 1,
                "requested_by": 1,
                "created_at": 1,
                "approver_roles": 1,
                # Plan-then-apply surface: the runtime persisted the
                # agent's recommendation + the captured writes when the
                # queue action ran in plan_only mode. Surface them here
                # so the approval panel can show "what will be applied"
                # before the approver clicks Apply.
                "decision": 1,
                "reasoning": 1,
                "planned_writes": 1,
            },
        )
        .sort("created_at", -1)
        .limit(60)
    )
    raw = [r async for r in cursor]
    if record_id:
        # Same scoping fix as the timeline: a record with no pending run shows an
        # empty approval list, not every OTHER record's pending runs. (No
        # reasoning-trim here — the approver needs the full recommendation.)
        raw = [r for r in raw if _inputs_contains(r.get("inputs"), str(record_id))]
    out = raw[:limit]
    # Integrity hash of the proposed writes — the UI echoes it on approve so the
    # server can verify display == commit (see ApproveRequest.expected_plan_hash).
    for r in out:
        r["plan_hash"] = compute_plan_hash(r.get("planned_writes") or [])
    return out


_COMMENT_KIND = "comment"


async def _fetch_record_comments(
    app_id: Optional[str], record_id: Optional[str], limit: int = 100
) -> List[Dict[str, Any]]:
    """Human notes/comments threaded to a record (the ``comments`` detail
    section). Reads the app's overlay store for ``kind='comment'`` rows whose
    ``thread_of`` is this record, oldest-first (a thread reads top→bottom).
    Scoped by ``app_id`` (globally unique; the detail endpoint already gated app
    access). Returns ``[]`` for an unbound/record-less section — never raises so
    a comment-store hiccup can't blank the whole detail page."""
    if not (app_id and record_id):
        return []
    try:
        from main import get_smart_app_records_col
    except ImportError:  # pragma: no cover
        return []
    cursor = (
        get_smart_app_records_col()
        .find(
            {
                "app_id": app_id,
                "kind": _COMMENT_KIND,
                "thread_of": str(record_id),
                "deleted_at": None,
            },
            {"_id": 0, "record_id": 1, "data": 1, "author_user_id": 1, "created_at": 1},
        )
        .sort("created_at", 1)
        .limit(max(1, int(limit)))
    )
    out: List[Dict[str, Any]] = []
    async for r in cursor:
        data = r.get("data") or {}
        text = data.get("text") or data.get("note") or data.get("comment")
        if not text:
            continue
        out.append({
            "id": r.get("record_id"),
            "text": str(text),
            "author": r.get("author_user_id"),
            "created_at": r.get("created_at"),
        })
    return out


_SMARTAPP_BLOB_PREFIX = "smartapp-blob://"


async def _resolve_blob_refs(
    record: Dict[str, Any], *, settings: Any, auth_header: Optional[str],
) -> Dict[str, Any]:
    """Read-back for the file-upload fallback: swap any ``smartapp-blob://`` ref
    on a detail record for a FRESH short-lived presigned URL, minted at view
    time via Citra-Service (runtime-proxied retrieval — durable + access-gated,
    never a long-lived link). A presign failure leaves the ref in place and is
    logged, so a broken retrieval is visible, not a silent blank."""
    if not isinstance(record, dict):
        return record
    base = (getattr(settings, "citra_service_url", "") or "").rstrip("/")
    refs = [(k, v) for k, v in record.items()
            if isinstance(v, str) and v.startswith(_SMARTAPP_BLOB_PREFIX)]
    if not base or not refs:
        return record
    out = dict(record)
    async with httpx.AsyncClient(timeout=20.0) as client:
        for k, ref in refs:
            try:
                r = await client.get(
                    f"{base}/api/v2/smartapp-blob/presign", params={"ref": ref},
                    headers={"Authorization": auth_header} if auth_header else {},
                )
                if r.status_code < 400:
                    out[k] = r.json().get("url") or ref
                else:
                    logger.warning("[file-fallback] presign %s → %s", ref, r.status_code)
            except Exception as e:  # noqa: BLE001
                logger.warning("[file-fallback] presign %s error: %s", ref, e)
    return out


async def resolve_one_record(
    *,
    settings: Settings,
    app_spec: AppSpec,
    source_id: str,
    record_id: str,
    key_field: Optional[str] = None,
    auth_header: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Read ONE record's current field values for an edit-mode form prefill.

    Pushes ``WHERE key_field = record_id`` to an MCP source (a 1-row read, not a
    500-row scan) when ``key_field`` is given, else fetches and matches
    client-side. A source failure FAILS LOUD as a 502 (RULE #1) — an edit form
    must never silently prefill from an empty/errored read. Blob refs are
    resolved so file fields show their stored value."""
    ds = _find_data_source(app_spec, source_id)
    fetch_ds = ds
    fetch_limit = _DEFAULT_LIMIT
    if key_field and record_id is not None and getattr(ds, "type", None) == "mcp":
        merged = dict(getattr(ds, "filters", None) or {})
        merged[key_field] = record_id
        fetch_ds = ds.model_copy(update={"filters": merged})
        fetch_limit = 5
    rows, _t, _tr, _sk, ds_note = await _rows_for_data_source(
        settings=settings,
        app_spec=app_spec,
        ds=fetch_ds,
        auth_header=auth_header,
        limit=fetch_limit,
    )
    if ds_note and is_source_failure_note(ds_note):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=strip_source_failure_marker(ds_note),
        )
    record, _field = _match_record(rows, record_id, key_field)
    if record:
        record = await _resolve_blob_refs(
            record, settings=settings, auth_header=auth_header
        )
    return record


def _best_record_key(row: Dict[str, Any]) -> Optional[str]:
    """Best-effort record identifier from a row/inputs dict (for navigate +
    dedup). Generic heuristic only — no domain column names: the platform's own
    ``record_id``/``case_natural_key`` system keys, the universal ``id``, then
    ANY ``*_id`` column (which covers app-specific keys like ``case_id``)."""
    if not isinstance(row, dict):
        return None
    for k in ("record_id", "case_natural_key", "id"):
        if row.get(k):
            return str(row[k])
    for k, v in row.items():
        if isinstance(k, str) and k.endswith("_id") and v:
            return str(v)
    return None


_TIME_TOKEN = re.compile(r"^\{now(?:([+-]\d+)([smhdw]))?\}$")
_TIME_UNIT = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days", "w": "weeks"}


def _subst_time_tokens(val: Any, now: "datetime") -> Any:
    """Recursively replace ``{now}`` / ``{now-<N><unit>}`` / ``{now+<N><unit>}``
    tokens (unit ∈ s|m|h|d|w) in a filter value with a concrete ISO timestamp,
    so a builder can express time-relative conditions (overdue, stale > 48h,
    due within 7d) without knowing the query time. Non-token values pass through
    untouched."""
    from datetime import timedelta

    if isinstance(val, dict):
        return {k: _subst_time_tokens(v, now) for k, v in val.items()}
    if isinstance(val, list):
        return [_subst_time_tokens(v, now) for v in val]
    if isinstance(val, str):
        m = _TIME_TOKEN.match(val.strip())
        if m:
            if m.group(1):
                amount = int(m.group(1))
                return (now + timedelta(**{_TIME_UNIT[m.group(2)]: amount})).isoformat()
            return now.isoformat()
    return val


async def _fetch_approvals_feed(
    slug: str,
    user_dept_ids: List[str],
    user_tenant: Optional[str],
    feed: Any,
) -> List[Dict[str, Any]]:
    """Built-in ``approvals`` feed: pending recommendations in THIS app's officer
    inbox the caller can act on. Reads the SAME store as the reviewer queue —
    ``smartapp_workflow_staging`` (env-routed), ``status='pending_review'`` —
    mirroring ``list_workflow_staging``'s scoping (tenant + assignable_to.dept_id
    ∈ the user's dept_ids; no dept_ids ⇒ platform admin sees all in tenant).
    (NOT ``pending_runs`` — that's a back-compat/test-only fallback.)"""
    if not slug:
        return []
    try:
        from main import get_workflow_staging_col
    except ImportError:  # pragma: no cover
        return []
    query: Dict[str, Any] = {"slug": slug, "status": "pending_review"}
    if user_tenant:
        query["tenant_id"] = user_tenant
    if user_dept_ids:
        query["assignable_to.dept_id"] = {"$in": list(user_dept_ids)}
    cursor = (
        get_workflow_staging_col()
        .find(
            query,
            {
                "_id": 0, "case_natural_key": 1, "llm_recommendation_text": 1,
                "display_context": 1, "created_at": 1, "workflow_execution_id": 1,
            },
        )
        .sort("created_at", -1)
        .limit(max(1, int(getattr(feed, "limit", 50))))
    )
    nav = feed.navigate.model_dump() if getattr(feed, "navigate", None) else None
    tone = getattr(feed, "tone", None) or "warning"
    label = getattr(feed, "label", None) or "Approvals"
    out: List[Dict[str, Any]] = []
    async for r in cursor:
        ctx = r.get("display_context")
        key = r.get("case_natural_key")
        row = dict(ctx) if isinstance(ctx, dict) else {}
        if key is not None:
            row.setdefault("case_natural_key", key)
        out.append({
            "type": "approval",
            "label": label,
            "tone": tone,
            "id": str(key) if key is not None else None,
            "title": str(key) if key is not None else "case",
            "sub": str(r.get("llm_recommendation_text") or "awaiting review"),
            "created_at": r.get("created_at"),
            "correlation_id": r.get("workflow_execution_id"),
            "row": row,
            "navigate": nav,
        })
    return out


async def _fetch_data_source_feed(
    *,
    settings: Settings,
    app_spec: AppSpec,
    feed: Any,
    auth_header: Optional[str],
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """Generic, builder-defined feed: rows of ``feed.source`` matching
    ``feed.filters`` (any predicate; ``{now…}`` tokens resolved). Maps
    ``title_field``/``sub_field`` (catalogue columns) onto each item. Returns
    ``(items, error)`` — a source failure surfaces as a user-facing note (RULE
    #1) without 502-ing, so other feeds still render."""
    from datetime import datetime, timezone

    ds = _find_data_source(app_spec, feed.source)
    now = datetime.now(timezone.utc)
    flt = _subst_time_tokens(dict(feed.filters or {}), now)
    fetch_ds = ds
    if flt:
        merged = dict(getattr(ds, "filters", None) or {})
        merged.update(flt)
        fetch_ds = ds.model_copy(update={"filters": merged})
    rows, _t, _tr, _sk, ds_note = await _rows_for_data_source(
        settings=settings,
        app_spec=app_spec,
        ds=fetch_ds,
        auth_header=auth_header,
        limit=int(getattr(feed, "limit", 50)),
    )
    if ds_note and is_source_failure_note(ds_note):
        return [], strip_source_failure_marker(ds_note)
    nav = feed.navigate.model_dump() if getattr(feed, "navigate", None) else None
    tone = getattr(feed, "tone", None) or "neutral"
    tf = getattr(feed, "title_field", None)
    sf = getattr(feed, "sub_field", None)
    out: List[Dict[str, Any]] = []
    for row in rows:
        title = (
            str(row.get(tf)) if (tf and row.get(tf) is not None)
            else (_best_record_key(row) or feed.label)
        )
        sub = str(row.get(sf)) if (sf and row.get(sf) is not None) else None
        out.append({
            "type": "feed",
            "label": feed.label,
            "tone": tone,
            "id": _best_record_key(row),
            "title": title,
            "sub": sub,
            "created_at": None,
            "row": row,
            "navigate": nav,
        })
    return out, None


async def resolve_notifications(
    *,
    settings: Settings,
    app_spec: AppSpec,
    panel_id: str,
    slug: str,
    user_dept_ids: List[str],
    user_tenant: Optional[str],
    auth_header: Optional[str] = None,
) -> Dict[str, Any]:
    """Aggregate a ``notifications`` panel's builder-defined ``feeds`` into one
    attention list. Each feed is either the built-in ``approvals`` inbox or a
    generic data_source query — there is no hardcoded notification type."""
    panel = _find_panel(app_spec, panel_id)
    if getattr(panel, "type", None) != "notifications":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"panel '{panel_id}' is not a notifications panel",
        )
    items: List[Dict[str, Any]] = []
    errors: List[str] = []
    for feed in (getattr(panel, "feeds", None) or []):
        try:
            if getattr(feed, "kind", "data_source") == "approvals":
                items += await _fetch_approvals_feed(
                    slug, list(user_dept_ids or []), user_tenant, feed
                )
            else:
                feed_items, ferr = await _fetch_data_source_feed(
                    settings=settings,
                    app_spec=app_spec,
                    feed=feed,
                    auth_header=auth_header,
                )
                items += feed_items
                if ferr:
                    errors.append(f"{getattr(feed, 'label', 'feed')}: {ferr}")
        except HTTPException as exc:  # bad source id, etc. — surface, keep going
            errors.append(f"{getattr(feed, 'label', 'feed')}: {exc.detail}")
        except Exception as exc:  # noqa: BLE001 — one bad feed must not 500 the panel
            errors.append(f"{getattr(feed, 'label', 'feed')}: {exc}")
    return {
        "panel_id": panel_id,
        "notifications": items,
        "count": len(items),
        "error": "; ".join(errors) if errors else None,
    }


async def resolve_detail_data(
    *,
    settings: Settings,
    app_spec: AppSpec,
    panel_id: str,
    record_id: Optional[str],
    auth_header: Optional[str] = None,
    app_id: Optional[str] = None,
) -> DetailDataResponse:
    """Resolve everything a detail panel renders, in one round trip.

    The record is matched from the linked queue's data source by
    ``record_id``; ``documents`` sections resolve their own data source;
    ``agent_timeline`` / ``approval`` sections are filled from the audit
    and pending-run collections. ``fields`` / ``markdown`` / ``agent_chat``
    sections carry no server data — the runtime renders them from the
    record / spec directly.

    ``app_id`` is the owning app's id (the caller passes it from the app
    document); it falls back to ``app_spec.app_id`` when not given.
    """
    app_id = app_id or app_spec.app_id
    panel = _find_panel(app_spec, panel_id)
    if getattr(panel, "type", None) != "detail":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"panel '{panel_id}' is not a detail panel",
        )

    note: Optional[str] = None
    record: Optional[Dict[str, Any]] = None
    record_columns: List[str] = []
    # The column the record was actually matched by (e.g. inspection_id) — the
    # authoritative key for re-reading the record's media through the MCP. Column
    # order can put a DIFFERENT *_id column first (asset_id), so the runtime must
    # not guess; we hand it the real key.
    matched_id_field: Optional[str] = None

    # Two ways a detail panel names its record source, enforced mutually
    # exclusive by DetailPanel's model validator:
    #
    #   linked_to   — the classic binding. The officer clicks a queue row; the
    #                 record comes from THAT queue's data_source.
    #   data_source — read directly by id, with no list to click. This is what
    #                 an EMBED page uses: the host application already knows
    #                 which record the officer has open and passes the id in,
    #                 so composing a queue purely to satisfy the binding would
    #                 mean fetching a list nobody sees.
    own_ds_id = getattr(panel, "data_source", None)
    if own_ds_id:
        linked_ds_id = own_ds_id
    else:
        linked = None
        for p in app_spec.all_panels:
            if p.id == panel.linked_to:
                linked = p
                break
        linked_ds_id = getattr(linked, "data_source", None) if linked else None
    if linked_ds_id:
        ds = _find_data_source(app_spec, linked_ds_id)
        # Push the id predicate to the SOURCE instead of fetching the whole
        # queue and matching client-side: when the detail panel names an
        # id_field, query WHERE id_field = record_id (a handful of rows) so
        # opening a case is a 1-row read, not a 500-row scan. No caching —
        # the row is read live every open (transactional data can change).
        id_field = getattr(panel, "id_field", None)
        fetch_ds = ds
        fetch_limit = _DEFAULT_LIMIT
        if id_field and record_id is not None and getattr(ds, "type", None) == "mcp":
            merged = dict(getattr(ds, "filters", None) or {})
            merged[id_field] = record_id
            fetch_ds = ds.model_copy(update={"filters": merged})
            fetch_limit = 5  # expect exactly one; small cap as a safety net
        rows, _t, _tr, _sk, ds_note = await _rows_for_data_source(
            settings=settings,
            app_spec=app_spec,
            ds=fetch_ds,
            auth_header=auth_header,
            limit=fetch_limit,
        )
        # A source failure here is surfaced to the user as the section NOTE
        # (the detail endpoint returns 200 with this note — it does NOT 502 like
        # the panel-data path; DetailDataResponse has no error channel). Drop the
        # internal marker prefix from that user-facing note.
        # TODO(parity): add an `error` channel to DetailDataResponse + 502 here.
        if ds_note:
            ds_note = strip_source_failure_marker(ds_note)
        if rows:
            record_columns = list(rows[0].keys())
        record, matched_id_field = _match_record(rows, record_id, id_field)
        if record is None and record_id:
            # Name the FIELD and DATASET that were searched, not just the id.
            # On an embed the id comes from the host application, whose screen
            # carries several plausible identifiers (application id, customer
            # id, account number). "no record matching id 'CUS-0006005'" gives
            # the integrator nothing to act on; naming the key column tells them
            # immediately that they passed the wrong one. Costs nothing — both
            # values are already in hand here.
            _where = f" in {getattr(ds, 'ref', None)}" if getattr(ds, "ref", None) else ""
            _by = f" on {id_field}" if id_field else ""
            note = ds_note or (
                f"no record matching{_by} = '{record_id}'{_where}"
            )
        elif ds_note:
            note = ds_note
    else:
        note = (
            f"detail panel '{panel_id}' linked_to '{panel.linked_to}' which is "
            "not a queue with a data_source"
        )

    # File-upload fallback read-back: resolve platform blob refs → fresh
    # presigned URLs (after the can-render check above), so attachment sections
    # render the stored photo/PDF.
    if record:
        record = await _resolve_blob_refs(record, settings=settings, auth_header=auth_header)

    sections_out: List[Dict[str, Any]] = []
    for s in panel.sections or []:
        st = s.type
        block: Dict[str, Any] = {"type": st, "title": s.title}
        if st == "documents":
            docs: List[Dict[str, Any]] = []
            d_note: Optional[str] = None
            if s.data_source:
                try:
                    dds = _find_data_source(app_spec, s.data_source)
                    docs, _dt, _dtr, _dsk, d_note = await _rows_for_data_source(
                        settings=settings,
                        app_spec=app_spec,
                        ds=dds,
                        auth_header=auth_header,
                        limit=50,
                    )
                except HTTPException as exc:  # unknown data_source
                    d_note = str(exc.detail)
            else:
                d_note = "documents section has no data_source"
            block["documents"] = docs
            block["note"] = strip_source_failure_marker(d_note) if d_note else d_note
            # Expose the section's data_source so the runtime can sign an
            # "Open original" URL for detail-section documents (the standalone
            # document_view panel already could; this brings parity).
            if s.data_source:
                block["data_source"] = s.data_source
        elif st == "agent_timeline":
            block["runs"] = await _fetch_record_runs(app_id, record_id)
        elif st == "approval":
            block["roles"] = s.roles or []
            block["pending"] = await _fetch_pending_runs(app_id, record_id)
        elif st in ("fields", "attachment"):
            # attachment renders FileView over these columns; without them the
            # runtime falls back to ALL columns (treats every field as a file).
            block["fields"] = s.fields or []
            if st == "attachment":
                # The runtime streams each column's media THROUGH the dept-MCP
                # (browser never touches storage); it needs the data_source to
                # build the same-origin /api/media URL. Default to the LINKED
                # QUEUE's source — the record came from there, so its media key
                # belongs to that same source — so an author need not restate it.
                ds_for_media = s.data_source or linked_ds_id
                if ds_for_media:
                    block["data_source"] = ds_for_media
                # Hand the runtime the AUTHORITATIVE record key (the column the
                # record was matched by) so it re-reads media by the right field
                # — not the first *_id column it finds (which may be a foreign
                # key like asset_id).
                if matched_id_field:
                    block["key_field"] = matched_id_field
        elif st == "markdown":
            block["content"] = s.content
        elif st == "agent_chat":
            block["agent_role"] = s.agent_role
        elif st == "comments":
            block["comments"] = await _fetch_record_comments(app_id, record_id)
        sections_out.append(block)

    return DetailDataResponse(
        panel_id=panel_id,
        linked_to=panel.linked_to,
        record_id=record_id,
        record=record,
        record_columns=record_columns,
        sections=sections_out,
        note=note,
    )
