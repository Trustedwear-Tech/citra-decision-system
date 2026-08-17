# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""Per-tenant LLM token metering — the billing substrate (Wave 2 #8).

Records one usage row per metered LLM call (tenant, model, surface, tokens_in,
tokens_out) into ``token_usage`` and aggregates it for the usage endpoint.
Billing is token-based, so this must be as complete as possible — but it is a
DERIVED store on the serving path: a dropped meter row must NEVER break the LLM
call that served the user, so writes are loud-but-non-fatal (RULE #1: log, don't
crash the request). Reads (the usage summary) fail loud to the caller.

Aggregation is done Mongo-side (``$group``) so totals are accurate regardless of
volume — never a Python rollup over a capped fetch (which would silently
under-bill). A ``day`` string is stored per row so the daily rollup is a plain
single-field group.

Env-routed like the other derived stores: test usage → ``test_token_usage``.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

log = logging.getLogger("token_metering")

_COLLECTION = "token_usage"


def _col():
    """Env-routed motor collection handle (lazy main import; same pattern as
    analysis_rubrics._col)."""
    import main  # deferred

    if getattr(main, "_db", None) is None:
        raise RuntimeError("Database not initialised")
    name = _COLLECTION
    try:
        if main.current_env() == "test":
            name = main._test_collection_name(_COLLECTION)
    except Exception:  # noqa: BLE001 — env unknown ⇒ prod collection (safe default)
        pass
    return main._db[name]


async def record_usage(
    *,
    tenant_id: Optional[str],
    model: Optional[str],
    surface: str,
    tokens_in: Any,
    tokens_out: Any,
    at: Optional[datetime] = None,
) -> None:
    """Meter one LLM call. ``surface`` names WHAT spent the tokens (e.g.
    'rubric_summarize', 'image_analyze', 'doc_extract', 'chat'). No‑op (logged)
    when there is no tenant to bill or the call spent nothing. Loud‑but‑non‑fatal
    on write — never breaks the call it is metering."""
    if not tenant_id:
        log.warning("[TOKENS] usage not billed: no tenant_id (model=%s surface=%s)",
                    model, surface)
        return
    ti = int(tokens_in or 0)
    to = int(tokens_out or 0)
    if ti <= 0 and to <= 0:
        return
    now = at or datetime.now(timezone.utc)
    try:
        await _col().insert_one({
            "tenant_id": tenant_id,
            "model": model or "unknown",
            "surface": surface or "unknown",
            "tokens_in": ti,
            "tokens_out": to,
            "at": now,
            "day": now.strftime("%Y-%m-%d"),
        })
    except Exception:  # noqa: BLE001 — derived store; loud, never fails the request
        log.exception("[TOKENS] failed to record usage tenant=%s model=%s surface=%s "
                      "(%d/%d tok) — billing row dropped", tenant_id, model, surface, ti, to)


async def usage_summary(
    *,
    tenant_ids: List[str],
    since: datetime,
    until: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Aggregate metered usage for the tenant(s) in the window: totals plus
    breakdowns by model, surface, and day. Read-only; RAISES on store failure
    (a billing read must not silently under-report)."""
    tenants = [t for t in (tenant_ids or []) if t]
    empty = {"totals": {"tokens_in": 0, "tokens_out": 0, "calls": 0},
             "by_model": [], "by_surface": [], "by_day": []}
    if not tenants:
        return empty

    at_range: Dict[str, Any] = {"$gte": since}
    if until is not None:
        at_range["$lte"] = until
    match = {"tenant_id": {"$in": tenants}, "at": at_range}
    col = _col()

    async def _group(field: str) -> List[Dict[str, Any]]:
        rows = await col.aggregate([
            {"$match": match},
            {"$group": {
                "_id": f"${field}",
                "tokens_in": {"$sum": "$tokens_in"},
                "tokens_out": {"$sum": "$tokens_out"},
                "calls": {"$sum": 1},
            }},
        ]).to_list(10000)
        return [{field: r.get("_id"), "tokens_in": int(r.get("tokens_in") or 0),
                 "tokens_out": int(r.get("tokens_out") or 0),
                 "calls": int(r.get("calls") or 0)} for r in rows]

    by_model = await _group("model")
    by_surface = await _group("surface")
    by_day = sorted(await _group("day"), key=lambda r: r.get("day") or "")
    totals = {
        "tokens_in": sum(r["tokens_in"] for r in by_model),
        "tokens_out": sum(r["tokens_out"] for r in by_model),
        "calls": sum(r["calls"] for r in by_model),
    }
    return {"totals": totals, "by_model": by_model,
            "by_surface": by_surface, "by_day": by_day}
