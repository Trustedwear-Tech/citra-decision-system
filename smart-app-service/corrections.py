# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""Officer-correction evidence ledger — Phase A of docs/clause-memory-graph-plan.md.

Every officer reject / override is recorded here as ONE immutable document. This
is the **evidence** layer: it is append-only, never summarized, never rewritten.
The derived layers (the legacy `smartapp_analysis_rubrics.summary`, and the
clause store that supersedes it) are both re-derivable from these rows.

Why a collection rather than the embedded ``corrections[]`` array it replaces:
  * the array lives inside ONE rubric bucket doc, so it cannot be indexed by
    officer / reason_code / facet, and every append rewrites the whole doc;
  * consolidation needs a *pending* scan across buckets (``consumed_by: null``),
    which a positional ``summarized_count`` watermark cannot express robustly —
    a per-document marker is re-runnable and immune to array reordering.

Phase A is INSTRUMENTATION ONLY: this store is written alongside the existing
fold and read by nothing yet. The legacy summary path is untouched, so nothing
an officer sees changes until Phase D.

Collection: ``smartapp_corrections`` (env-routed — a test-env app reads/writes
``test_smartapp_corrections`` with no extra wiring, same as analysis_rubrics).
"""
from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

log = logging.getLogger(__name__)

_COLLECTION = "smartapp_corrections"

# The officer's free-text reason, stored WHOLE. Generous because this text never
# enters a run prompt — it feeds only the consolidation job (plan §18.4). The
# legacy summarizer keeps its own tighter budget (analysis_rubrics.
# REASON_MAX_CHARS = 500) because ITS input does shape a bounded prompt; the two
# caps serve different consumers and must not be collapsed into one.
CORRECTION_REASON_MAX_CHARS = 2000

#: Minimum words before a correction is accepted at all (approve-with-override
#: and reject). Consolidation already refuses to author a judgement from
#: vacuous text (MIN_CONTENT_TOKENS), but by then the officer has gone: the
#: evidence is stored, unusable, and nobody is asked again. Rejecting it at the
#: door is the only moment the person who knows the answer is still present.
#:
#: This is what replaced the reason-code picker. A chip could be satisfied in
#: one click without typing a character, and produced a label that clustering
#: has since stopped using anyway.
MIN_CORRECTION_WORDS = int(os.getenv("CORRECTION_MIN_WORDS", "10"))

#: Officer-visible reason taxonomy fallback. An app whose ``case_signature``
#: declares no ``reason_codes`` yet records corrections with ``reason_code=None``
#: — an honest "not captured", never a synthesized bucket that would pollute
#: clustering. Consolidation skips uncoded corrections for clause CREATION but
#: still counts them for reinforcement matching.
UNCODED = None

_indexes_ensured: set = set()


def _col():
    """Env-routed collection handle (lazy-main pattern — see analysis_rubrics)."""
    import main  # deferred: avoids the import cycle through tool dispatch

    if getattr(main, "_db", None) is None:
        raise RuntimeError("Database not initialised")
    name = _COLLECTION
    try:
        if main.current_env() == "test":
            name = main._test_collection_name(_COLLECTION)
    except Exception:  # noqa: BLE001 — env unknown ⇒ prod collection (safe default)
        pass
    return main._db[name]


async def _ensure_indexes(col) -> None:
    """Create the three access-path indexes once per ROUTED collection name.

    Keyed by routed name, not a bare bool — env routing is per-request, so a
    plain flag would leave the other env's collection unindexed after first use
    (the lesson entity_links already encodes)."""
    key = col.name
    if key in _indexes_ensured:
        return
    try:
        # The consolidation scan: pending rows for one bucket, oldest first.
        await col.create_index(
            [("tenant_id", 1), ("app_slug", 1), ("modality", 1),
             ("task_type", 1), ("consumed_by", 1), ("at", 1)],
            name="ix_bucket_pending",
        )
        # Decision drill-down (Memory screen: which corrections came from this run).
        await col.create_index(
            [("tenant_id", 1), ("app_slug", 1), ("correlation_id", 1)],
            name="ix_correlation",
        )
        await col.create_index("correction_id", unique=True, name="ux_correction_id")
        _indexes_ensured.add(key)
    except Exception:  # noqa: BLE001 — index creation is best-effort; loud
        log.exception("[CORRECTIONS] index creation failed for %s", key)


def new_correction_id() -> str:
    return f"corr-{uuid.uuid4().hex[:16]}"


def _clean_str_list(vals: Any, *, cap: int = 32) -> List[str]:
    """Normalise a list-ish of identifiers to a deduped, sorted, capped list."""
    if not vals:
        return []
    if isinstance(vals, str):
        vals = [vals]
    out = sorted({str(v).strip() for v in vals if v is not None and str(v).strip()})
    return out[:cap]


#: Families the PLATFORM emits from the dataset ontology; an app never declares
#: them, so they must not be treated as undeclared. Mirrors derive_facets.
_PLATFORM_FAMILIES = frozenset({"vertical", "sub_vertical", "country"})


async def declared_families(app_slug: str, tenant_id: Optional[str] = None) -> Optional[Set[str]]:
    """The facet families this app can legitimately emit, or None when unknown.

    None is NOT "nothing declared" — it means we could not read the app (no DB,
    app missing, no case_signature). Callers must treat None as "cannot judge"
    and leave facets alone: silently stripping real evidence because Mongo
    blinked would be far worse than the drift this guards against.
    """
    try:
        import main  # deferred: same lazy-main pattern as _col()

        q: Dict[str, Any] = {"slug": app_slug}
        if tenant_id:
            q["tenant_id"] = tenant_id
        doc = await main.get_apps_col().find_one(q, {"_id": 0, "app_spec": 1})
        if not doc:
            return None
        import case_signature as cs

        sig = cs.signature_of(doc)
        fams = {f.get("family") for f in ((sig or {}).get("facets") or [])}
        fams.discard(None)
        if not fams:
            return None          # app declares no signature — nothing to check
        return fams | _PLATFORM_FAMILIES
    except Exception:  # noqa: BLE001 — a guard must never break the write
        log.exception("[CORRECTIONS] could not read declared facets for %s", app_slug)
        return None


def _guard_facets(
    facets: List[str], declared: Optional[Set[str]], *, app_slug: str,
) -> Tuple[List[str], List[str]]:
    """Split facets into (keep, rejected) against the app's declared families.

    WHY THIS EXISTS: a facet whose family the app never declares can never
    appear on a real case, so any judgement scoped to it fails the
    ``scope ⊆ case_facets`` test forever. The result is a judgement that reads
    "team judgement — 3 officers" on the Memory screen while being incapable of
    ever firing, and ``fired_count: 0`` looks identical to "no matching cases
    yet". Exactly that shipped in the acme-power demo (defect_type / oil_leak
    on an app declaring neither) and went unnoticed until someone asked why the
    app had ignored its own lesson.

    The correction is ALWAYS kept — evidence is sacred and an officer's reason
    must never be dropped. Only the unusable tokens are held back, and they are
    recorded on the row so nothing is lost silently.
    """
    if declared is None:
        return facets, []
    keep, bad = [], []
    for t in facets:
        family = str(t).split(":", 1)[0]
        (keep if family in declared else bad).append(t)
    if bad:
        log.error(
            "[CORRECTIONS] %s: %d facet(s) use families this app does not "
            "declare — held back so they cannot build an unusable judgement: "
            "%s (declared: %s)",
            app_slug, len(bad), bad, sorted(declared - _PLATFORM_FAMILIES),
        )
    return keep, bad


async def record_correction(
    *,
    tenant_id: Optional[str],
    app_slug: str,
    modality: str,
    task_type: str,
    event: str,
    officer: Optional[str] = None,
    officer_role: Optional[str] = None,
    correlation_id: Optional[str] = None,
    case_ref: Optional[Dict[str, Any]] = None,
    case_facets: Optional[List[str]] = None,
    signature_version: Optional[int] = None,
    reason_code: Optional[str] = None,
    reason_inferred: bool = False,
    contested_fields: Optional[List[str]] = None,
    overrides: Optional[List[Dict[str, Any]]] = None,
    reason_text: Optional[str] = None,
    recommendation: Optional[str] = None,
    injected_clause_ids: Optional[List[str]] = None,
    cited_clause_ids: Optional[List[str]] = None,
    overruled_clause_ids: Optional[List[str]] = None,
) -> Optional[str]:
    """Append ONE officer correction. Returns its correction_id, or None if skipped.

    Fully non-raising — a ledger write must never fail an officer's decision
    (the reason is already durable on the DecisionRecord). A missing tenant
    SKIPS LOUDLY rather than synthesizing a '' bucket no reader queries; that
    is the same canonical-key discipline ``rubric_tenant_for_app`` enforces for
    the rubric store, and the two MUST agree or the loop silently never closes.

    ``event`` is 'reject' or 'override'. A clean approve carries no lesson and
    must not be recorded — the caller decides that; this function records what
    it is given so the fold sites keep one definition of "is there a lesson".
    """
    try:
        if not tenant_id:
            log.warning(
                "[CORRECTIONS] SKIPPED for app %s — no tenant key resolvable "
                "(the lesson stays on the DecisionRecord only)", app_slug,
            )
            return None
        if not app_slug:
            log.warning("[CORRECTIONS] SKIPPED — no app_slug")
            return None

        text = (reason_text or "").strip()
        if len(text) > CORRECTION_REASON_MAX_CHARS:
            log.warning(
                "[CORRECTIONS] reason truncated %d -> %d chars for %s/%s "
                "(full text remains on the DecisionRecord)",
                len(text), CORRECTION_REASON_MAX_CHARS, tenant_id, app_slug,
            )
            text = text[: CORRECTION_REASON_MAX_CHARS - 1] + "…"

        # Derive contested fields from the override deltas when the caller did
        # not name them explicitly — the officer changing a field IS the signal
        # for which field was contested, so it should never have to be typed.
        fields = _clean_str_list(contested_fields)
        if not fields and overrides:
            derived: List[str] = []
            for ov in overrides:
                fromto = (ov or {}).get("override")
                if isinstance(fromto, dict):
                    derived.extend(str(f) for f in fromto)
            fields = _clean_str_list(derived)

        # Vocabulary guard: hold back facets whose family the app never declares
        # (they could only ever produce a judgement that cannot fire). The
        # correction itself is always written.
        facets = _clean_str_list(case_facets, cap=64)
        facets, rejected_facets = _guard_facets(
            facets, await declared_families(app_slug, tenant_id), app_slug=app_slug)

        cid = new_correction_id()
        doc: Dict[str, Any] = {
            "correction_id": cid,
            "tenant_id": tenant_id,
            "app_slug": app_slug,
            "modality": modality,
            "task_type": task_type,
            "correlation_id": correlation_id,
            "case_ref": case_ref or None,
            # The signature AT DECISION TIME, frozen. Never recomputed — a later
            # ontology edit must not silently rewrite what past cases looked like.
            "case_facets": facets,
            "signature_version": signature_version,
            "officer": officer,
            "officer_role": officer_role,
            "event": event,
            "recommendation": (recommendation or "").strip()[:240] or None,
            "reason_code": reason_code or UNCODED,
            "reason_inferred": bool(reason_inferred),
            "contested_fields": fields,
            "overrides": overrides or [],
            "reason_text": text or None,
            "injected_clause_ids": _clean_str_list(injected_clause_ids, cap=64),
            "cited_clause_ids": _clean_str_list(cited_clause_ids, cap=64),
            # Clauses the model reported it deliberately SET ASIDE
            # (relation="overruled"). This is the dissent signal: an officer
            # disposing such a case is acting against that rule, which is what
            # moves a clause toward `dissented` instead of silently letting the
            # most recent opinion win.
            "overruled_clause_ids": _clean_str_list(overruled_clause_ids, cap=64),
            # Consolidation watermark. null ⇒ pending. Set to the clause_id that
            # absorbed it (plan §3) — re-runnable, unlike a positional counter.
            "consumed_by": None,
            "at": datetime.now(timezone.utc),
        }
        # Kept out of case_facets but ON the row: an out-of-vocabulary facet is a
        # bug in whatever wrote it (a backfill, a stale signature), and it must
        # be findable rather than vanish.
        if rejected_facets:
            doc["rejected_facets"] = rejected_facets
        col = _col()
        await _ensure_indexes(col)
        await col.insert_one(doc)
        log.info(
            "[CORRECTIONS] +%s %s/%s/%s/%s by=%s code=%s facets=%d",
            event, tenant_id, app_slug, modality, task_type,
            officer, reason_code or "-", len(doc["case_facets"]),
        )
        return cid
    except Exception:  # noqa: BLE001 — evidence ledger; loud, never fatal
        log.exception(
            "[CORRECTIONS] record failed for %s/%s — the reason is still durable "
            "on the DecisionRecord", tenant_id, app_slug,
        )
        return None


async def pending_corrections(
    *,
    tenant_id: str,
    app_slug: str,
    modality: str,
    task_type: str,
    limit: int = 500,
) -> List[Dict[str, Any]]:
    """Un-consolidated corrections for one bucket, OLDEST FIRST.

    RAISES on store failure — the consolidation job must be able to tell "no
    pending work" from "could not read", or a broken store looks like a quiet
    steady state and learning stops without an alarm."""
    col = _col()
    await _ensure_indexes(col)
    return await col.find(
        {
            "tenant_id": tenant_id, "app_slug": app_slug,
            "modality": modality, "task_type": task_type,
            "consumed_by": None,
        },
        {"_id": 0},
    ).sort("at", 1).to_list(limit)


async def pending_buckets(*, limit: int = 200) -> List[Dict[str, Any]]:
    """Buckets that have pending corrections, with count and oldest timestamp.

    Drives the consolidation trigger (count threshold OR age threshold) without
    the worker having to enumerate every app. RAISES on store failure, for the
    same reason as ``pending_corrections``."""
    cur = _col().aggregate([
        {"$match": {"consumed_by": None}},
        {"$group": {
            "_id": {
                "tenant_id": "$tenant_id", "app_slug": "$app_slug",
                "modality": "$modality", "task_type": "$task_type",
            },
            "pending": {"$sum": 1},
            "oldest": {"$min": "$at"},
        }},
        {"$sort": {"pending": -1}},
        {"$limit": limit},
    ])
    rows = await cur.to_list(limit)
    return [{**r["_id"], "pending": r["pending"], "oldest": r["oldest"]} for r in rows]


async def mark_consumed(*, correction_ids: List[str], clause_id: str) -> int:
    """Stamp ``consumed_by`` on folded corrections. Returns the modified count.

    RAISES on failure: silently leaving rows pending would re-fold the same
    evidence on the next pass and double-count officer support, inflating the
    promotion gate that is supposed to protect against exactly that."""
    ids = _clean_str_list(correction_ids, cap=10_000)
    if not ids or not clause_id:
        return 0
    res = await _col().update_many(
        {"correction_id": {"$in": ids}, "consumed_by": None},
        {"$set": {"consumed_by": clause_id}},
    )
    return int(getattr(res, "modified_count", 0) or 0)


async def corrections_for_bucket(
    *, tenant_id: str, app_slug: str, modality: str, task_type: str,
    limit: int = 2000,
) -> List[Dict[str, Any]]:
    """Every correction for a bucket, consumed or not, oldest first.

    Feeds the correction-absorption metric ("taught → stopped recurring").
    Loud-but-empty on failure: this is a metrics read, and a 500 on the loop
    view would hide every OTHER metric on the page."""
    try:
        return await _col().find(
            {"tenant_id": tenant_id, "app_slug": app_slug,
             "modality": modality, "task_type": task_type},
            {"_id": 0},
        ).sort("at", 1).to_list(limit)
    except Exception:  # noqa: BLE001 — metrics read; loud, never raises
        log.exception("[CORRECTIONS] bucket read failed for %s/%s", app_slug, task_type)
        return []


async def corrections_by_ids(
    *, correction_ids: List[str], limit: int = 200
) -> List[Dict[str, Any]]:
    """Fetch corrections by id — the provenance drill-down (clause → the rejects
    that taught it). Read-only; loud-but-empty on failure."""
    ids = _clean_str_list(correction_ids, cap=limit)
    if not ids:
        return []
    try:
        rows = await _col().find(
            {"correction_id": {"$in": ids}}, {"_id": 0},
        ).to_list(limit)
        order = {cid: i for i, cid in enumerate(ids)}
        rows.sort(key=lambda r: order.get(r.get("correction_id"), 1 << 30))
        return rows
    except Exception:  # noqa: BLE001 — read view; loud, never raises to the API
        log.exception("[CORRECTIONS] provenance read failed")
        return []


async def export_corrections(
    *, tenant_ids: List[str], app_slug: str
) -> List[Dict[str, Any]]:
    """Every correction for the app, as-is — the EVIDENCE half of the export.

    Shipping clauses without these would hand the customer conclusions with no
    way to re-derive or audit them, which is the exact opacity the summary blob
    had. RAISES on store failure so the export can be marked partial."""
    tenants = [t for t in (tenant_ids or []) if t]
    if not tenants or not app_slug:
        return []
    return await _col().find(
        {"tenant_id": {"$in": tenants}, "app_slug": app_slug}, {"_id": 0},
    ).to_list(100_000)


async def correction_stats(*, tenant_ids: List[str], app_slug: str) -> Dict[str, Any]:
    """Compact per-app counters for the Memory screen / metrics.

    Returns totals plus a reason_code histogram — the histogram is what tells a
    builder their taxonomy is wrong (a large ``other`` or ``null`` slice means
    officers had no code that fit; plan §9.6). Loud-but-empty on failure."""
    tenants = [t for t in (tenant_ids or []) if t]
    if not tenants or not app_slug:
        return {"total": 0, "pending": 0, "by_reason_code": {}}
    try:
        cur = _col().aggregate([
            {"$match": {"tenant_id": {"$in": tenants}, "app_slug": app_slug}},
            {"$group": {
                "_id": "$reason_code",
                "n": {"$sum": 1},
                "pending": {"$sum": {"$cond": [
                    {"$eq": [{"$ifNull": ["$consumed_by", None]}, None]}, 1, 0]}},
            }},
        ])
        rows = await cur.to_list(64)
        total = sum(r["n"] for r in rows)
        pending = sum(r["pending"] for r in rows)
        hist = {(r["_id"] or "__uncoded"): r["n"] for r in rows}
        # J6 visibility: how many DISTINCT officers this app has ever seen
        # (drives the gate-reachability notice), and how many corrections were
        # too brief to learn from (the coaching counter, J4).
        officers = [o for o in await _col().distinct(
            "officer", {"tenant_id": {"$in": tenants}, "app_slug": app_slug}) if o]
        brief = await _col().count_documents(
            {"tenant_id": {"$in": tenants}, "app_slug": app_slug,
             "insufficient_reason": True})
        return {"total": total, "pending": pending, "by_reason_code": hist,
                "distinct_officers": len(officers), "too_brief": brief}
    except Exception:  # noqa: BLE001 — metrics enrichment; loud, never raises
        log.exception("[CORRECTIONS] stats read failed for %s", app_slug)
        return {"total": 0, "pending": 0, "by_reason_code": {}}
