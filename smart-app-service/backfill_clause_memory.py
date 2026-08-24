# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""Backfill — promote the legacy embedded corrections into the evidence ledger.
docs/clause-memory-graph-plan.md §12.

The whole migration is cheap because ``analysis_rubrics.corrections[]`` was
NEVER rewritten: the officer evidence is fully intact and nothing has to be
reconstructed from the lossy ``summary``. This script moves it into
``smartapp_corrections`` so it becomes clause-eligible.

Four steps per correction, each degrading HONESTLY rather than guessing:

  1. **Promote** the array entry to its own document.
  2. **Recover the full reason** by joining ``correlation_id`` →
     ``decision_records``. The array copy was truncated to 500 chars by the
     legacy fold; the DecisionRecord kept the original. When the join misses,
     the truncated text is kept as-is (marked ``reason_truncated``) — never
     padded, never invented.
  3. **Backfill facets** by re-deriving from the SoR record when one is
     reachable. Unreachable ⇒ ``case_facets: []``, which means the clause it
     eventually feeds comes out GLOBAL. That is the honest answer: a global
     clause is over-broad but true, whereas a guessed scope silently mis-fires.
  4. **Classify reason_code**, flagged ``reason_inferred: true``. Inferred
     corrections cluster but do NOT count toward the distinct-officer promotion
     gate (consolidation §9.5) — a classifier's opinion is not an officer's.

IDEMPOTENT: a correction already promoted (matched on its legacy identity) is
skipped, so a re-run after a partial failure never double-counts officer
support — the exact thing the promotion gate exists to protect against.

Usage:
    python backfill_clause_memory.py --dry-run
    python backfill_clause_memory.py --app acme-power
    python backfill_clause_memory.py --all --classify
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

log = logging.getLogger("backfill_clause_memory")

#: Deterministic id so a re-run maps the same legacy entry to the same document.
#: Built from the bucket + the entry's own content, because the legacy array
#: carried no id of its own.
def legacy_correction_id(
    *, tenant_id: str, app_slug: str, modality: str, task_type: str,
    entry: Dict[str, Any], index: int,
) -> str:
    at = entry.get("at")
    raw = "|".join([
        str(tenant_id), str(app_slug), str(modality), str(task_type),
        str(index), str(entry.get("actor") or ""), str(entry.get("item_id") or ""),
        str(at.isoformat() if isinstance(at, datetime) else at or ""),
        (entry.get("reason") or "")[:200],
    ])
    return "corr-bf-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


#: The legacy fold composed override deltas into PROSE:
#:     "corrected assigned_to: 'Paula Shaw' -> 'Adam Cole'"
#: The structured from/to was thrown away at that point. Recovering it matters
#: for more than tidiness: the intra-cluster coherence guard detects
#: contradictory evidence by comparing destinations against sources on the same
#: field, and it reads `overrides`. Left unparsed, every backfilled correction
#: looks like a plain reject with nothing to disagree about — so two officers
#: moving one field in opposite directions would sail through and be averaged
#: into a confident wrong rule. Exactly the case this was found on.
_LEGACY_DELTA_RE = re.compile(
    r"corrected\s+([A-Za-z_][A-Za-z0-9_]*)\s*:\s*"
    r"(?P<q1>['\"])?(?P<from>.*?)(?P=q1)?\s*->\s*"
    r"(?P<q2>['\"])?(?P<to>.*?)(?P=q2)?(?=\s*(?:;|—|$))"
)


def parse_legacy_overrides(reason: str) -> List[Dict[str, Any]]:
    """Reconstruct ``overrides`` from a legacy composed reason line.

    Returns [] when nothing parses — a plain reject, or a shape this pattern
    does not cover. Never guesses a delta it did not actually read."""
    out: Dict[str, Any] = {}
    for m in _LEGACY_DELTA_RE.finditer(reason or ""):
        frm = (m.group("from") or "").strip().strip("'\"")
        to = (m.group("to") or "").strip().strip("'\"")
        if not to:
            continue
        out[m.group(1)] = {"from": frm or None, "to": to}
    return [{"override": out}] if out else []


_CLASSIFY_SYSTEM = (
    "Classify a reviewer's rejection reason into ONE code from the list.\n"
    "Answer with the code alone — no punctuation, no explanation.\n"
    "If none fits, answer: other"
)


async def classify_reason(reason: str, codes: List[str]) -> Optional[str]:
    """Best-effort reason_code for a legacy correction. Returns None on failure
    or on an off-list answer — an unusable classification must not become a
    fabricated code that silently partitions consolidation."""
    if not reason or not codes:
        return None
    try:
        from config import get_settings
        from llm_client import get_llm_client_for

        settings = get_settings()
        tier = settings.llm_tier_config("medium")
        client = get_llm_client_for(tier["base_url"], tier["api_key"])
        resp = await client.chat.completions.create(
            model=tier["model"],
            messages=[
                {"role": "system", "content": _CLASSIFY_SYSTEM},
                {"role": "user", "content": f"CODES: {', '.join(codes)}\n\nREASON: {reason[:1500]}"},
            ],
            temperature=0.0,
            # Hybrid-reasoning tier: excluded reasoning tokens still count
            # against max_tokens, so a tight cap yields empty content even for
            # a one-word answer.
            max_tokens=4000,
            timeout=30,
            extra_body=(tier.get("extra_body") or None),
        )
        out = (resp.choices[0].message.content or "").strip().lower().strip(".")
        return out if out in codes else None
    except Exception:  # noqa: BLE001 — classification is optional enrichment
        log.warning("[BACKFILL] reason classification failed — leaving uncoded")
        return None


async def _full_reason(db, correlation_id: Optional[str], fallback: str,
                       decision_col: str = "decision_records") -> tuple:
    """(text, was_recovered). The DecisionRecord kept the untruncated reason."""
    if not correlation_id:
        return fallback, False
    try:
        dr = await db[decision_col].find_one(
            {"correlation_id": correlation_id},
            {"_id": 0, "decision_reason": 1, "note": 1},
        )
        full = ((dr or {}).get("decision_reason") or (dr or {}).get("note") or "").strip()
        if full and len(full) >= len(fallback):
            return full, True
    except Exception:  # noqa: BLE001 — join is enrichment
        log.warning("[BACKFILL] DecisionRecord join failed for %s", correlation_id)
    return fallback, False


async def backfill(
    *,
    app_slug: Optional[str] = None,
    dry_run: bool = False,
    classify: bool = False,
    limit: int = 100_000,
) -> Dict[str, Any]:
    """Promote legacy corrections. Returns a summary; raises on store failure."""
    import main
    from corrections import CORRECTION_REASON_MAX_CHARS
    from learned_memory import item_subject_facet

    db = main._db
    if db is None:
        raise RuntimeError("Database not initialised")

    # ENV-ROUTED names, not raw db[...]: the collections are per-environment
    # (test_ prefixed) and reaching past the routing would make --env test read
    # and write the PROD store while reporting success — a silent no-op that
    # leaves the test environment unmigrated and the purge guard blocking.
    def _routed(name: str) -> str:
        try:
            return main._test_collection_name(name) if main.current_env() == "test" else name
        except Exception:  # noqa: BLE001 — env unknown ⇒ prod (safe default)
            return name

    rubrics = db[_routed("smartapp_analysis_rubrics")]
    target = db[_routed("smartapp_corrections")]

    q: Dict[str, Any] = {}
    if app_slug:
        q["app_slug"] = app_slug
    buckets = await rubrics.find(q, {"_id": 0}).to_list(5000)

    stats = {"buckets": 0, "seen": 0, "promoted": 0, "skipped_existing": 0,
             "reason_recovered": 0, "classified": 0, "uncoded": 0}

    # Reason taxonomy per app, read once — a code outside the app's declared
    # set would silently partition consolidation into clusters of one.
    codes_cache: Dict[str, List[str]] = {}

    async def _codes_for(slug: str) -> List[str]:
        if slug in codes_cache:
            return codes_cache[slug]
        from case_signature import reason_codes, signature_of

        doc = await db[_routed("smartapp_apps")].find_one(
            {"slug": slug}, {"_id": 0, "app_spec": 1})
        codes_cache[slug] = reason_codes(signature_of(doc)) if doc else []
        return codes_cache[slug]

    for b in buckets:
        entries = b.get("corrections") or []
        if not entries:
            continue
        stats["buckets"] += 1
        tenant_id = b.get("tenant_id")
        slug = b.get("app_slug")
        modality = b.get("modality")
        task_type = b.get("task_type")

        for i, e in enumerate(entries):
            if stats["seen"] >= limit:
                break
            stats["seen"] += 1
            cid = legacy_correction_id(
                tenant_id=tenant_id, app_slug=slug, modality=modality,
                task_type=task_type, entry=e, index=i)

            if await target.find_one({"correction_id": cid}, {"_id": 1}):
                stats["skipped_existing"] += 1
                continue

            raw_reason = (e.get("reason") or "").strip()
            text, recovered = await _full_reason(
                db, e.get("item_id"), raw_reason, _routed("decision_records"))
            if recovered:
                stats["reason_recovered"] += 1
            if len(text) > CORRECTION_REASON_MAX_CHARS:
                text = text[: CORRECTION_REASON_MAX_CHARS - 1] + "…"

            # Recover the structured deltas before anything downstream needs
            # them (see parse_legacy_overrides).
            _legacy_overrides = parse_legacy_overrides(raw_reason)
            if _legacy_overrides and not (e.get("fields") or []):
                # `fields` was only added late; derive it from what we parsed so
                # the coherence guard and the absorption metric both see it.
                e = {**e, "fields": sorted(_legacy_overrides[0]["override"])}

            code = None
            if classify:
                code = await classify_reason(text, await _codes_for(slug))
            if code:
                stats["classified"] += 1
            else:
                stats["uncoded"] += 1

            doc = {
                "correction_id": cid,
                "tenant_id": tenant_id, "app_slug": slug,
                "modality": modality, "task_type": task_type,
                "correlation_id": e.get("item_id"),
                "case_ref": None,
                # No SoR read here: re-deriving facets needs the record as it was
                # at decision time, which is not recoverable for historic rows.
                # [] ⇒ any clause built from this evidence comes out GLOBAL —
                # over-broad but TRUE, where a guessed scope would mis-fire.
                "case_facets": item_subject_facet(e.get("subject"), modality),
                "signature_version": None,
                "officer": e.get("actor"),
                "officer_role": None,
                "event": "override" if (e.get("fields") or _legacy_overrides) else "reject",
                "recommendation": (e.get("subject") or None),
                "reason_code": code,
                # A classifier's opinion is not an officer's: inferred rows
                # cluster but never count toward the promotion gate (§9.5).
                "reason_inferred": bool(code),
                "contested_fields": list(e.get("fields") or []),
                "overrides": _legacy_overrides,
                "reason_text": text or None,
                "reason_truncated": (not recovered) and len(raw_reason) >= 499,
                "injected_clause_ids": [], "cited_clause_ids": [],
                "overruled_clause_ids": [],
                "consumed_by": None,
                "backfilled": True,
                "at": e.get("at") or datetime.now(timezone.utc),
            }
            if not dry_run:
                await target.insert_one(doc)
            stats["promoted"] += 1

    log.info("[BACKFILL] %s", stats)
    return stats


async def _main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--app", help="single app_slug (default: every app)")
    ap.add_argument("--all", action="store_true", help="explicit opt-in for every app")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--classify", action="store_true",
                    help="infer reason_code with the medium model (costs tokens)")
    ap.add_argument("--limit", type=int, default=100_000)
    ap.add_argument("--env", choices=["prod", "test"], default="prod")
    args = ap.parse_args()

    if not args.app and not args.all:
        print("refusing to run fleet-wide without --all (or pass --app <slug>)")
        return 2

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    import main
    from env_context import set_current_env

    set_current_env(args.env)
    await main._init_db() if hasattr(main, "_init_db") else None
    if main._db is None:
        from motor.motor_asyncio import AsyncIOMotorClient

        client = AsyncIOMotorClient(os.environ["MONGO_URI"])
        main._db = client[os.environ.get("MONGO_DB", "dev")]

    stats = await backfill(app_slug=args.app, dry_run=args.dry_run,
                           classify=args.classify, limit=args.limit)
    print(f"\n{'DRY RUN — nothing written' if args.dry_run else 'BACKFILL COMPLETE'}")
    for k, v in stats.items():
        print(f"  {k:20} {v}")
    if stats["uncoded"]:
        print(f"\n  NOTE: {stats['uncoded']} correction(s) have no reason_code. "
              "Consolidation will NOT author clauses from them (§9.2) — re-run "
              "with --classify, or let officers code new rejects going forward.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
