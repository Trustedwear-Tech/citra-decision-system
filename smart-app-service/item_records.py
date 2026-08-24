# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""Per-item knowledge ledger for multimodal analyses (images / documents).

The multimodal twin of the DecisionRecord substrate. The aggregated rubric
(analysis_rubrics.py) is the SOP layer — lossy compression of officer feedback
into prompt criteria. This module keeps the ORIGINALS: one row per analyzed
item, carrying the model's verdict, the artifact identity (media_ref +
content_sha256), the officer's disposition — accept AND reject (with reason)
AND cancel — and, stamped later via correlation_id, the parent decision's
outcome. See models.ItemDecisionRecord for the shape.

Why both layers: the rubric teaches "how to look at this kind of image"; the
ledger answers "have we seen THIS artifact / this kind of item before, and what
did the officer say?" — exact-reuse detection (the fraud case), true few-shot
precedents for image/doc verdicts, and the tenant's future multimodal training
set (accepted AND rejected examples — training needs both classes).

Persistence posture: derived store, loud-but-non-fatal (RULE #1 — a miss is
LOGGED as an error; the authoritative audit chain + rubric corrections remain
the recovery source). Reads are best-effort with the same logging.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

log = logging.getLogger("item_records")

_COLLECTION = "item_decision_records"

# Precedent-injection budgets — keep the block small enough to never crowd the
# rubric/prompt (a handful of precedents beats a wall of them).
MAX_EXACT_PRECEDENTS = 3
MAX_NEIGHBOR_PRECEDENTS = 2  # per disposition class (accept / reject)
_REASON_TRIM = 240
_RATIONALE_TRIM = 200


def _col():
    """Env-routed motor collection handle (lazy import to avoid an import cycle
    with main — same pattern as analysis_rubrics._col)."""
    import main  # deferred

    if getattr(main, "_db", None) is None:
        raise RuntimeError("Database not initialised")
    from config import get_settings

    name = get_settings().item_decision_records_collection
    try:
        if main.current_env() == "test":
            name = main._test_collection_name(name)
    except Exception:  # noqa: BLE001 — env unknown ⇒ prod collection (safe default)
        pass
    return main._db[name]


# Keyed by ROUTED collection name (env routing is per-request; a module bool
# would latch after whichever env writes first) — same pattern as fraud_checks.
# The precedent fetch runs in the ANALYSIS HOT PATH on every image/document, so
# these must exist or the ledger growing makes every analysis slower.
_indexes_ensured: set = set()


async def _ensure_indexes(col) -> None:
    if col.name in _indexes_ensured:
        return
    # Write-1 upsert key (also dedupes retried persists).
    await col.create_index([("correlation_id", 1), ("item_id", 1)], unique=True)
    # Exact tier: same artifact anywhere in this app.
    await col.create_index([("tenant_id", 1), ("slug", 1), ("content_sha256", 1),
                            ("created_at", -1)])
    # Neighbor tier: recent dispositions per item type.
    await col.create_index([("tenant_id", 1), ("slug", 1), ("modality", 1),
                            ("task_type", 1), ("disposition", 1),
                            ("disposition_at", -1)])
    # Write-2 latest-row lookup.
    await col.create_index([("tenant_id", 1), ("slug", 1), ("item_id", 1),
                            ("created_at", -1)])
    _indexes_ensured.add(col.name)


# ---------------------------------------------------------------------------
# Write 1 — at run end: persist the model's verdicts (disposition="proposed")
# ---------------------------------------------------------------------------


async def persist_item_findings(
    findings: List[Dict[str, Any]],
    *,
    correlation_id: str,
    slug: Optional[str],
    app_id: Optional[str],
    tenant_id: Optional[str],
    case_facets: Optional[List[str]] = None,
) -> None:
    """Upsert one ledger row per ItemFinding, keyed by (correlation_id, item_id).

    ``case_facets`` is the run's signature FROZEN onto each row, so precedent
    retrieval can later rank by comparability instead of recency. Stamped at
    write time rather than recomputed at read time: a case must be compared on
    what it looked like when it was decided, not on what a since-edited
    ontology would say about it now.

    Idempotent per run (a retried persist overwrites the same rows). A failure
    is logged LOUD but never fails the run — the findings still reached the
    officer; the ledger is a derived store."""
    if not findings:
        return
    try:
        col = _col()
        await _ensure_indexes(col)
        now = datetime.now(timezone.utc)
        for f in findings:
            if not isinstance(f, dict) or not f.get("item_id"):
                continue
            row = {
                "item_id": f.get("item_id"),
                "correlation_id": correlation_id,
                "slug": slug,
                "app_id": app_id,
                "tenant_id": tenant_id,
                "modality": f.get("modality") or "image",
                "task_type": f.get("item_type") or "generic",
                "subject": f.get("subject"),
                # Frozen signature — the ranking key for future precedent
                # retrieval (rank_by_facets).
                "case_facets": sorted({str(x) for x in (case_facets or []) if x}),
                "media_ref": f.get("media_ref"),
                "content_sha256": f.get("content_sha256"),
                "fields": f.get("fields") or {},
                "recommendation": f.get("recommendation"),
                "confidence": f.get("confidence") or 0.0,
                "rationale": f.get("rationale") or "",
                "rubric_version": f.get("rubric_version"),
                # {"exact","accepted","rejected","total"} — what precedents
                # grounded this analysis (None on pre-feature/failed lookups).
                "precedents_used": f.get("precedents_used"),
                "updated_at": now,
            }
            await col.update_one(
                {"correlation_id": correlation_id, "item_id": row["item_id"]},
                {
                    "$set": row,
                    "$setOnInsert": {"disposition": "proposed", "created_at": now},
                },
                upsert=True,
            )
        log.info(
            "[ITEM-LEDGER] persisted %d finding(s) for %s/%s",
            len(findings), slug, correlation_id,
        )
    except Exception:  # noqa: BLE001 — derived store; loud, never fatal
        log.exception(
            "[ITEM-LEDGER] failed to persist %d finding(s) for %s/%s — "
            "recoverable from the run audit chain",
            len(findings), slug, correlation_id,
        )


# ---------------------------------------------------------------------------
# Write 2 — officer feedback: ALL dispositions are knowledge (accept too)
# ---------------------------------------------------------------------------


async def record_item_disposition(
    *,
    tenant_id: str,
    slug: str,
    item_id: str,
    modality: str,
    task_type: str,
    decision: str,
    reason: Optional[str],
    actor: Optional[str],
    subject: Optional[str] = None,
) -> bool:
    """Stamp the officer's disposition onto the item's ledger row.

    Targets the MOST RECENT row for (tenant, slug, item_id) — the UI reviews the
    current run's findings and item_id is stable per record-artifact, so latest
    is the row being reviewed. If no run row exists (legacy run before Write-1
    shipped), a minimal row is created so the judgement is never dropped.
    Returns True when a row was stamped/created."""
    now = datetime.now(timezone.utc)
    set_fields = {
        "disposition": decision,
        "disposition_reason": (reason or None),
        "disposition_actor": actor,
        "disposition_at": now,
        "updated_at": now,
    }
    if subject:
        set_fields["subject"] = subject
    try:
        col = _col()
        await _ensure_indexes(col)
        latest = await col.find_one(
            {"tenant_id": tenant_id, "slug": slug, "item_id": item_id},
            sort=[("created_at", -1)],
        )
        if latest is not None:
            await col.update_one({"_id": latest["_id"]}, {"$set": set_fields})
        else:
            await col.insert_one({
                "item_id": item_id,
                "correlation_id": None,
                "slug": slug,
                "app_id": None,
                "tenant_id": tenant_id,
                "modality": modality,
                "task_type": task_type,
                "subject": subject,
                "media_ref": None,
                "content_sha256": None,
                "fields": {},
                "recommendation": None,
                "confidence": 0.0,
                "rationale": "",
                "rubric_version": None,
                "created_at": now,
                **set_fields,
            })
        log.info(
            "[ITEM-LEDGER] disposition %s on %s/%s by %s%s",
            decision, slug, item_id, actor,
            f" (reason: {reason[:60]}…)" if reason and len(reason) > 60
            else (f" (reason: {reason})" if reason else ""),
        )
        return True
    except Exception:  # noqa: BLE001 — derived store; loud, never fatal
        log.exception(
            "[ITEM-LEDGER] failed to record disposition %s on %s/%s — the reject "
            "reason (if any) is still durable in the rubric corrections",
            decision, slug, item_id,
        )
        return False


# ---------------------------------------------------------------------------
# Outcome inheritance — the read-back poller stamps items via correlation_id
# ---------------------------------------------------------------------------


async def stamp_items_outcome(correlation_id: str, outcome: Dict[str, Any]) -> int:
    """Copy the parent decision's outcome verdict onto every item of the run.

    An item's ground truth matures with its case: an accepted photo on a
    decision that turned out BAD is a different training example than one on a
    decision that held. Returns the number of items stamped."""
    if not correlation_id:
        return 0
    try:
        col = _col()
        res = await col.update_many(
            {"correlation_id": correlation_id},
            {"$set": {"outcome": outcome,
                      "updated_at": datetime.now(timezone.utc)}},
        )
        if res.modified_count:
            log.info(
                "[ITEM-LEDGER] outcome %r inherited by %d item(s) of %s",
                (outcome or {}).get("label"), res.modified_count, correlation_id,
            )
        return int(res.modified_count or 0)
    except Exception:  # noqa: BLE001 — derived store; loud, never fatal
        log.exception("[ITEM-LEDGER] failed to stamp outcome for %s", correlation_id)
        return 0


# ---------------------------------------------------------------------------
# Read — precedent retrieval for prompt grounding (two tiers)
# ---------------------------------------------------------------------------


async def fetch_item_precedents(
    *,
    tenant_id: Optional[str],
    slug: Optional[str],
    modality: str,
    task_type: str,
    content_sha256: Optional[str] = None,
    media_ref: Optional[str] = None,
    include_exact: bool = True,
    case_facets: Optional[List[str]] = None,
) -> Dict[str, List[Dict[str, Any]]]:
    """Fetch dispositioned precedents for a new analysis.

    exact    — same artifact (content hash, else media_ref) seen before, ANY
               disposition incl. proposed-on-another-record: reuse evidence.
               This tier is a FRAUD signal — skip it (``include_exact=False``)
               when the app's ontology does not enable fraud screening, so a
               non-fraud app pays nothing for reuse detection it never asked for
               (reuse is owned by the role-aware consistency_check when on).
    accepted — recent officer-ACCEPTED items of the same (task_type, modality):
               what good looks like.
    rejected — recent officer-REJECTED items with their reasons: what to
               catch. Both classes ground the verdict, mirroring
               neighbor_samples for records."""
    out: Dict[str, List[Dict[str, Any]]] = {"exact": [], "accepted": [], "rejected": []}
    try:
        import asyncio

        col = _col()
        await _ensure_indexes(col)
        base = {"tenant_id": tenant_id, "slug": slug,
                "modality": modality, "task_type": task_type}
        proj = {"_id": 0, "item_id": 1, "correlation_id": 1, "subject": 1,
                "case_facets": 1,
                "recommendation": 1, "rationale": 1, "disposition": 1,
                "disposition_reason": 1, "outcome": 1, "media_ref": 1,
                "content_sha256": 1, "created_at": 1}

        # Curation gate: rows an admin excluded from retrieval stay in the
        # ledger (audit + training set intact) but never ground a prompt again.
        _not_excluded = {"retrieval_excluded": {"$ne": True}}

        async def _exact() -> List[Dict[str, Any]]:
            if not (content_sha256 or media_ref):
                return []
            ident = ({"content_sha256": content_sha256} if content_sha256
                     else {"media_ref": media_ref})
            # Over-fetch, then keep ONE row per item_id (dispositioned rows win
            # over proposed; newest first) — repeated runs over the same other
            # item must not stack as N "prior sightings" of one artifact.
            rows = await col.find(
                {"tenant_id": tenant_id, "slug": slug, **ident, **_not_excluded}, proj,
            ).sort("created_at", -1).to_list(MAX_EXACT_PRECEDENTS * 8)
            best: Dict[str, Dict[str, Any]] = {}
            for r in rows:
                iid = r.get("item_id") or ""
                cur = best.get(iid)
                if cur is None or (
                    cur.get("disposition") == "proposed"
                    and r.get("disposition") != "proposed"
                ):
                    best[iid] = r
            return list(best.values())[:MAX_EXACT_PRECEDENTS]

        def _neighbors(disp: str):
            # Over-fetch by recency, then re-rank by FACET OVERLAP (plan §11).
            # Recency alone answers "what happened lately", not "what is
            # comparable" — the two diverge exactly when they matter, on a case
            # type the officers have seen rarely. Recency stays the tiebreak, so
            # with no facets on either side the behaviour is what it always was.
            return col.find(
                {**base, "disposition": disp, **_not_excluded}, proj,
            ).sort("disposition_at", -1).to_list(MAX_NEIGHBOR_PRECEDENTS * 12)

        # Analysis hot path: the lookups are independent — one round-trip of
        # wall-clock, not N. The exact (fraud-reuse) tier is only fetched when
        # the app opts into fraud screening (include_exact) — a non-fraud app
        # runs the two neighbor lookups only, and never the reuse query.
        if include_exact:
            out["exact"], out["accepted"], out["rejected"] = await asyncio.gather(
                _exact(), _neighbors("accept"), _neighbors("reject"),
            )
        else:
            out["accepted"], out["rejected"] = await asyncio.gather(
                _neighbors("accept"), _neighbors("reject"),
            )
        for _cls in ("accepted", "rejected"):
            out[_cls] = rank_by_facets(out[_cls], case_facets,
                                       limit=MAX_NEIGHBOR_PRECEDENTS)
    except Exception:  # noqa: BLE001 — enrichment; loud, never blocks analysis
        log.exception(
            "[ITEM-LEDGER] precedent lookup failed for %s/%s/%s — analysis "
            "proceeds rubric-only", slug, modality, task_type,
        )
    return out


def rank_by_facets(
    rows: List[Dict[str, Any]],
    case_facets: Optional[List[str]],
    *,
    limit: int,
) -> List[Dict[str, Any]]:
    """Keep the `limit` most COMPARABLE rows, by facet overlap.

    Jaccard over the frozen ``case_facets`` each row carried at decision time.
    Rows with no facets (pre-facet history, or an app with no case_signature)
    score 0 and therefore fall to the back — but they are NOT dropped: with a
    thin history they are all there is, and showing an older case beats showing
    none. Ties keep the incoming recency order, so behaviour degrades exactly to
    the previous recency-only ranking when nothing is facet-stamped.
    """
    if not rows:
        return []
    want = {str(f) for f in (case_facets or []) if f}
    if not want:
        return rows[:limit]

    def _score(r: Dict[str, Any]) -> float:
        have = {str(f) for f in (r.get("case_facets") or []) if f}
        if not have:
            return 0.0
        return len(want & have) / len(want | have)

    return sorted(rows, key=_score, reverse=True)[:limit]


def _trim(s: Any, n: int) -> str:
    t = str(s or "").strip()
    return t if len(t) <= n else t[: n - 1] + "…"


async def set_precedent_exclusion(
    *,
    tenant_ids: List[str],
    slug: str,
    item_id: str,
    excluded: bool,
    actor: Optional[str],
) -> int:
    """Curation, not deletion: flag EVERY ledger row of this item so it never
    grounds a precedent prompt again (or lift the flag). Rows stay intact —
    audit chain and training set are untouched; only retrieval changes.
    Attributed + timestamped. Returns the number of ledger rows that MATCHED
    (i.e. exist for this item) — NOT the number modified: re-applying an
    exclusion that is already set matches the row but modifies nothing, and the
    caller must read that as "the item exists" (idempotent), never as "no such
    item". 0 means genuinely no matching row → the caller may 404.

    This is a CURATION WRITE, not the analysis hot path — so a store failure
    RAISES (RULE #1: fail loud, propagate) rather than returning a 0 the caller
    can't distinguish from 'no such item'. The caller maps the exception to a
    503 (store unavailable), distinct from a 404."""
    tenants = [t for t in (tenant_ids or []) if t]
    if not tenants or not slug or not item_id:
        log.error("[ITEM-LEDGER] exclusion skipped: tenant/slug/item required "
                  "(tenants=%r slug=%r item=%r)", tenants, slug, item_id)
        return 0
    now = datetime.now(timezone.utc)
    update = (
        {"$set": {"retrieval_excluded": True,
                  "retrieval_excluded_by": actor,
                  "retrieval_excluded_at": now,
                  "updated_at": now}}
        if excluded else
        {"$set": {"retrieval_excluded": False, "updated_at": now},
         "$unset": {"retrieval_excluded_by": "", "retrieval_excluded_at": ""}}
    )
    try:
        col = _col()
        res = await col.update_many(
            {"tenant_id": {"$in": tenants}, "slug": slug, "item_id": item_id},
            update,
        )
        n = int(res.matched_count or 0)
        log.info("[ITEM-LEDGER] retrieval_excluded=%s on %s/%s (%d matched) by %s",
                 excluded, slug, item_id, n, actor)
        return n
    except Exception:  # curation write — propagate loudly (RULE #1); caller → 503
        log.exception("[ITEM-LEDGER] exclusion write failed for %s/%s", slug, item_id)
        raise


def encode_ledger_cursor(row: Dict[str, Any]) -> Optional[str]:
    """Opaque pagination cursor from a ledger row: (created_at, item_id).
    created_at alone is NOT unique — a batch persisted in one run shares one
    timestamp — so a bare `created_at < before` cursor drops rows on the page
    boundary. The item_id tiebreaker makes the sort a total order (item_ids are
    distinct within one created_at instant). Returns None if the row has no
    created_at (can't anchor a cursor → treat as end)."""
    import base64
    import json as _json

    ts = row.get("created_at")
    if not isinstance(ts, datetime):
        return None
    payload = _json.dumps({"c": ts.isoformat(), "i": row.get("item_id") or ""})
    return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii")


def decode_ledger_cursor(cursor: str) -> Optional[Dict[str, Any]]:
    """Inverse of encode_ledger_cursor → {'c': datetime, 'i': item_id}, or None
    if the token is malformed (caller then starts from the top, never crashes)."""
    import base64
    import json as _json

    try:
        payload = _json.loads(base64.urlsafe_b64decode(cursor.encode("ascii")))
        return {"c": datetime.fromisoformat(payload["c"]), "i": payload.get("i") or ""}
    except Exception:  # noqa: BLE001 — bad cursor → start from the top, loud
        log.warning("[ITEM-LEDGER] ignoring malformed browse cursor %r", cursor)
        return None


async def list_ledger_rows(
    *,
    tenant_ids: List[str],
    slug: str,
    disposition: Optional[str] = None,
    modality: Optional[str] = None,
    limit: int = 50,
    cursor: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Read-only ledger browse for the Memory screen — newest first, cursor
    pagination via an OPAQUE ``cursor`` (created_at + item_id of the last row
    seen; see encode_ledger_cursor). The compound (created_at, item_id) sort +
    cursor is a total order, so rows sharing a created_at are never skipped
    across a page boundary. Never ships Mongo _id; caps the page size."""
    tenants = [t for t in (tenant_ids or []) if t]
    if not tenants or not slug:
        return []
    q: Dict[str, Any] = {"tenant_id": {"$in": tenants}, "slug": slug}
    if disposition:
        q["disposition"] = disposition
    if modality:
        q["modality"] = modality
    cur = decode_ledger_cursor(cursor) if cursor else None
    if cur is not None:
        # Continue strictly after (created_at, item_id) in the descending order.
        q["$or"] = [
            {"created_at": {"$lt": cur["c"]}},
            {"created_at": cur["c"], "item_id": {"$lt": cur["i"]}},
        ]
    try:
        col = _col()
        rows = await col.find(q, {"_id": 0}).sort(
            [("created_at", -1), ("item_id", -1)],
        ).to_list(max(1, min(int(limit), 200)))
        return rows
    except Exception:  # noqa: BLE001 — read view; loud, never raises to the API
        log.exception("[ITEM-LEDGER] browse failed for %s", slug)
        return []


async def export_ledger(*, tenant_ids: List[str], slug: str) -> List[Dict[str, Any]]:
    """Full item-ledger export for the app (Memory screen manual export).

    Every row as-is (no Mongo _id), newest first — the customer's multimodal
    training set: model verdicts + artifact identity + officer dispositions +
    inherited outcomes. Read-only. RAISES on store failure — an export is an
    authoritative asset, so a failed fetch must propagate (the caller marks the
    export partial), NOT be swallowed to an empty list that looks whole."""
    tenants = [t for t in (tenant_ids or []) if t]
    if not tenants or not slug:
        return []
    col = _col()
    # Bounded but generous — a single app's ledger is dept-scale; the cap
    # is a safety rail, and we log LOUD if we hit it (partial export).
    rows = await col.find(
        {"tenant_id": {"$in": tenants}, "slug": slug}, {"_id": 0},
    ).sort("created_at", -1).to_list(100000)
    if len(rows) >= 100000:
        log.error("[ITEM-LEDGER] export hit the 100k cap for %s — PARTIAL "
                  "export; the bucket workflow (§4) is the unbounded path", slug)
    return rows


async def ledger_stats(*, tenant_ids: List[str], slug: str) -> Dict[str, Any]:
    """Cheap cumulative counts over the item ledger, for metrics.

    total + by_disposition (one aggregation), precedent_grounded (rows whose
    analysis was grounded by ≥1 injected precedent — the 'memory fired'
    signal), with_outcome (rows that inherited a settled good/bad verdict).
    Read-only; loud-but-empty on failure."""
    out: Dict[str, Any] = {"total": 0, "by_disposition": {},
                           "precedent_grounded": 0, "with_outcome": 0}
    tenants = [t for t in (tenant_ids or []) if t]
    if not tenants or not slug:
        return out
    try:
        col = _col()
        base = {"tenant_id": {"$in": tenants}, "slug": slug}
        rows = await col.aggregate([
            {"$match": base},
            {"$group": {"_id": "$disposition", "n": {"$sum": 1}}},
        ]).to_list(50)
        for r in rows:
            out["by_disposition"][r.get("_id") or "proposed"] = int(r.get("n") or 0)
        out["total"] = sum(out["by_disposition"].values())
        out["precedent_grounded"] = await col.count_documents(
            {**base, "precedents_used.total": {"$gt": 0}})
        out["with_outcome"] = await col.count_documents(
            {**base, "outcome.label": {"$in": ["good", "bad"]}})
    except Exception:  # noqa: BLE001 — metrics enrichment; loud, never raises
        log.exception("[ITEM-LEDGER] ledger_stats failed for %s", slug)
    return out


def precedents_counts(
    precedents: Dict[str, List[Dict[str, Any]]],
    *,
    current_item_id: Optional[str] = None,
) -> Dict[str, int]:
    """Count what precedents_to_prompt would actually RENDER (same exact-tier
    exclusion of the current item) — the 'memory fired' signal stamped onto the
    finding and persisted with the ledger row, so metrics can count how often
    precedents grounded an analysis rather than merely existed."""
    exact = [p for p in precedents.get("exact") or []
             if not current_item_id or p.get("item_id") != current_item_id]
    n_exact = len(exact)
    n_acc = len(precedents.get("accepted") or [])
    n_rej = len(precedents.get("rejected") or [])
    return {"exact": n_exact, "accepted": n_acc, "rejected": n_rej,
            "total": n_exact + n_acc + n_rej}


def precedents_to_prompt(
    precedents: Dict[str, List[Dict[str, Any]]],
    *,
    current_item_id: Optional[str] = None,
) -> str:
    """Render precedents as a prompt block (empty string when nothing useful).

    The exact tier EXCLUDES rows for the current item_id — re-analyzing the same
    record's photo is not reuse; the signal is the same artifact appearing on a
    DIFFERENT item/record."""
    lines: List[str] = []
    exact = [p for p in precedents.get("exact") or []
             if not current_item_id or p.get("item_id") != current_item_id]
    if exact:
        lines.append(
            "PRIOR SIGHTINGS OF THIS EXACT ARTIFACT (same content hash on a "
            "different item — treat as possible reuse/fraud evidence):"
        )
        for p in exact:
            lines.append(
                f"- item {p.get('item_id')}: model said "
                f"{p.get('recommendation') or '—'}; officer "
                f"{p.get('disposition') or 'proposed'}"
                + (f" — {_trim(p.get('disposition_reason'), _REASON_TRIM)}"
                   if p.get("disposition_reason") else "")
            )
    acc = precedents.get("accepted") or []
    rej = precedents.get("rejected") or []
    if acc or rej:
        lines.append("PAST OFFICER REVIEWS of this item type (ground your verdict):")
        for p in acc:
            lines.append(
                f"- ACCEPTED ({_trim(p.get('subject') or 'item', 60)}): model said "
                f"{p.get('recommendation') or '—'} — "
                f"{_trim(p.get('rationale'), _RATIONALE_TRIM)}"
            )
        for p in rej:
            lines.append(
                f"- REJECTED ({_trim(p.get('subject') or 'item', 60)}): "
                f"{_trim(p.get('disposition_reason') or 'no reason recorded', _REASON_TRIM)}"
            )
    return "\n".join(lines)
