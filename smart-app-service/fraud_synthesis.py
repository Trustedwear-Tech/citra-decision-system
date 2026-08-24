# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""T3 gated fraud synthesis + L2 fraud case rubric + L3 calibration.

Phases P2c/P3 of docs/fraud-detection-primitives-plan.md.

The synthesis is the ONLY step in the fraud stack that spends NEW tokens, so
the gate lives HERE, server-side: the agent always calls the tool with the
collected signals, and the TOOL decides whether the case earns a reasoning
pass (severity points ≥ gate, or a small random audit sample). Below the gate
it returns instantly at zero cost.

L2 rubric: the fraud CASE rubric reuses the existing rubric store unchanged —
bucket ``(modality="case", task_type="fraud-screening")`` — trained by officer
flag feedback through the same /items/{id}/feedback endpoint. L1 judges the
artifact; L2 judges the case; L3 (calibration below) judges the judges.

Every screening persists a structured row (``smartapp_fraud_screenings``) so
calibration can later join signals against officer dispositions + outcome
read-back — the flag → confirmed-fraud hit-rate per signal type.
"""
from __future__ import annotations

import json
import logging
import random
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

_SCREENINGS_COLLECTION = "smartapp_fraud_screenings"
FRAUD_RUBRIC_MODALITY = "case"
FRAUD_RUBRIC_TASK_TYPE = "fraud-screening"
#: Suffix of the case ItemFinding's item_id ("<record_id>-fraud"). THE single
#: definition — the feedback endpoint derives the screening record_id by
#: stripping it, so producer and consumer must never carry separate literals.
FRAUD_ITEM_SUFFIX = "-fraud"


def record_id_from_item_id(item_id: str) -> str:
    """Inverse of the case finding's item_id mint (``f"{record_id}{FRAUD_ITEM_SUFFIX}"``)."""
    return item_id[: -len(FRAUD_ITEM_SUFFIX)] if item_id.endswith(FRAUD_ITEM_SUFFIX) else item_id

# Deterministic, explainable severity weights (provisional until L3 calibration
# tunes them with outcome data — see the plan §9). Env-overridable WITHOUT a
# deploy so a calibration report is actionable: FRAUD_SIGNAL_WEIGHTS as a JSON
# object of {signal_key: int} merged over these defaults.
_POINTS_DEFAULTS = {
    "severity_mismatch": 2,
    "severity_warn": 1,
    "exact_duplicate": 3,       # byte-identical artifact on another case
    "phash_near_dup": 2,        # same picture, re-encoded
    # Same DOCUMENT text on another case (re-exported / re-saved / metadata
    # stripped → byte-different, so SHA-256 never sees it). Weighted like an exact
    # duplicate: for a document the text IS the identity, and re-generating one to
    # dodge a byte hash is deliberate, not incidental.
    "doc_text_near_dup": 3,
    "clip_near_duplicate": 3,   # cropped / re-shot copy from another case
    "clip_similar": 1,          # informational
    "metadata_anomaly": 1,      # edited-with / modified-after-creation
    "identity_cardinality": 2,  # one identifier, many names
    # EXIF↔claim comparator (E1) — deterministic, ontology-driven; a photo that
    # predates the incident or was taken far from the claimed site is a
    # near-certain tell, weighted like an exact duplicate.
    "exif_capture_before_claim": 3,
    "exif_gps_far_from_claim": 3,
    "camera_model_flip": 1,     # corroboration only (multi-submitter photosets are legit)
    # Payment-proof verification (E4) — the ledger either has the payment or
    # it doesn't; fact-grade signals. payment_verified is a POSITIVE and is
    # deliberately NOT a weight key (never scored).
    "payment_ref_not_found": 3,
    "payment_party_mismatch": 3,
    "payment_amount_mismatch": 2,
    "payment_date_mismatch": 1,
    # Generic cross-dataset verification (plan F4) — same fact-grade logic as
    # payment-proof, pointed at any declared system of record.
    "verify_ref_not_found": 3,
    "verify_field_mismatch": 2,
    # E6 date rules — deterministic date arithmetic over the record's own
    # (server-read) values; an impossible ordering is fact-grade.
    "date_rule_violation": 2,
    # E5 statement reconciliation — fires only on REPEATED chain breaks
    # (single breaks are OCR noise), so the one signal is already filtered.
    "statement_chain_break": 3,
    # E3 resubmission-after-rejection — bare-key join to a prior denied case:
    # strong corroboration, not proof (the finding quotes the prior decision).
    "resubmitted_after_rejection": 2,
    # E7 pencil-whipping photoset timing — CORROBORATION ONLY (weight 1,
    # excluded from the screen's issue count; can never gate a case alone).
    "photoset_timing_cluster": 1,
    # Cross-industry registry match (ClaimSearch/bureau via a dept-MCP rest_api
    # live-passthrough source — customer's membership + credentials, MCP-side).
    # The agent nests the registry tool's raw matches under the key
    # `external_registry_matches` in the signals it passes; highest-precision
    # signal in the stack.
    "external_registry_match": 4,
}


def _points() -> Dict[str, int]:
    import os

    weights = dict(_POINTS_DEFAULTS)
    raw = os.getenv("FRAUD_SIGNAL_WEIGHTS", "")
    if raw:
        try:
            weights.update({str(k): int(v) for k, v in json.loads(raw).items()})
        except (ValueError, TypeError) as exc:
            # Fail loud — a typo'd weights env must not silently zero the gate.
            log.error("[FRAUD-T3] FRAUD_SIGNAL_WEIGHTS unparseable (%s) — using defaults", exc)
    return weights


# Traversal budget for the agent-supplied signals blob — bounds both a
# degenerate deeply-nested payload (would RecursionError a recursive walk)
# and a multi-hundred-KB wide one.
_WALK_MAX_NODES = 5000


def severity_points(signals: Any) -> Tuple[int, Dict[str, int]]:
    """Walk the signals JSON the agent collected and produce a deterministic
    severity score + per-signal-type breakdown (the explainable gate input).

    ITERATIVE with a node budget — the input is a raw LLM tool argument, so it
    must not be able to RecursionError/DoS the screening."""
    counts: Dict[str, int] = {}

    def bump(key: str, n: int = 1) -> None:
        counts[key] = counts.get(key, 0) + n

    stack = [signals]
    visited = 0
    while stack:
        visited += 1
        if visited > _WALK_MAX_NODES:
            bump("walk_truncated")  # visible in the breakdown, weight 0
            log.warning("[FRAUD-T3] signals walk truncated at %d nodes", _WALK_MAX_NODES)
            break
        node = stack.pop()
        if isinstance(node, dict):
            sev = node.get("severity")
            if sev == "mismatch":
                bump("severity_mismatch")
            elif sev == "warn":
                bump("severity_warn")
            if node.get("signal") == "identity_cardinality" or node.get("cardinality_note"):
                bump("identity_cardinality")
            if node.get("signal") in (
                "exif_capture_before_claim", "exif_gps_far_from_claim",
                "camera_model_flip",
                "payment_ref_not_found", "payment_amount_mismatch",
                "payment_date_mismatch", "payment_party_mismatch",
                "verify_ref_not_found", "verify_field_mismatch",
                "date_rule_violation", "statement_chain_break",
                "resubmitted_after_rejection", "photoset_timing_cluster",
            ):
                bump(node["signal"])
            if node.get("duplicate") is True:
                bump("exact_duplicate")
            for k, v in node.items():
                if k == "external_registry_matches" and isinstance(v, list):
                    bump("external_registry_match", min(len(v), 3))
                elif k == "phash_near_dups" and isinstance(v, list):
                    bump("phash_near_dup", min(len(v), 3))
                elif k == "text_near_dups" and isinstance(v, list):
                    bump("doc_text_near_dup", min(len(v), 3))
                elif k == "near_duplicates" and isinstance(v, list):
                    bump("clip_near_duplicate", min(len(v), 3))
                elif k == "similar" and isinstance(v, list):
                    bump("clip_similar", min(len(v), 2))
                elif k == "anomalies" and isinstance(v, list):
                    bump("metadata_anomaly", min(len(v), 3))
                else:
                    stack.append(v)
        elif isinstance(node, list):
            stack.extend(node)

    weights = _points()
    total = sum(weights.get(k, 0) * n for k, n in counts.items())
    return total, counts


def _extract_json(text: str) -> Optional[Dict[str, Any]]:
    """Fence-tolerant JSON extraction — SHARED with the dispatcher (lazy import;
    tools_v2_dispatch only imports this module inside its handler, so there is
    no cycle). One parser to maintain, identical behavior on both paths."""
    from tools_v2_dispatch import _extract_json as _shared

    return _shared(text)


def _screenings_col():
    """Env-routed collection handle (lazy-main pattern, see analysis_rubrics)."""
    import main  # deferred — avoids import cycle

    if getattr(main, "_db", None) is None:
        raise RuntimeError("Database not initialised")
    name = _SCREENINGS_COLLECTION
    try:
        if main.current_env() == "test":
            name = main._test_collection_name(_SCREENINGS_COLLECTION)
    except Exception:  # noqa: BLE001 — env unknown ⇒ prod (safe default)
        pass
    return main._db[name]


# Keyed by ROUTED collection name (env routing is per-request — a plain bool
# would leave the other env's collection unindexed after first use).
_scr_indexes_ensured: set = set()


async def _ensure_scr_indexes() -> None:
    col = _screenings_col()
    if col.name in _scr_indexes_ensured:
        return
    await col.create_index([("tenant_id", 1), ("app_slug", 1), ("record_id", 1)])
    # Serves calibration's filtered created_at sort (index-ordered — never an
    # in-memory sort that grows toward Mongo's 32MB limit).
    await col.create_index([("tenant_id", 1), ("app_slug", 1), ("created_at", -1)])
    # Serves the ORG-scope screening_stats query (tenant-only filter +
    # created_at sort) — the 3-key index above can't order it because
    # app_slug sits between the equality prefix and the sort key.
    await col.create_index([("tenant_id", 1), ("created_at", -1)])
    _scr_indexes_ensured.add(col.name)


async def run_synthesis(
    *,
    settings,
    tenant_id: Optional[str],
    app_slug: Optional[str],
    record_id: str,
    context: str,
    signals: Any,
    model_tier: str = "large",
    gate_min_points: int = 2,
    sample_rate: float = 0.05,
    user_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Gate → (maybe) one reasoning pass → persist the screening row.

    Returns either ``{gated: true, points, breakdown}`` (below gate — zero
    cost) or the structured cross-examination the agent cites as evidence.
    """
    points, breakdown = severity_points(signals)
    sampled = False
    escalate = points >= max(1, int(gate_min_points))
    if not escalate and sample_rate > 0:
        sampled = random.random() < float(sample_rate)
        escalate = sampled

    row: Dict[str, Any] = {
        "tenant_id": tenant_id, "app_slug": app_slug, "record_id": record_id,
        "points": points, "breakdown": breakdown, "gated": not escalate,
        "sampled": sampled, "created_at": datetime.now(timezone.utc),
    }

    if not escalate:
        try:
            await _ensure_scr_indexes()
            await _screenings_col().insert_one(dict(row))
        except Exception as exc:  # noqa: BLE001 — visible, non-blocking
            log.warning("[FRAUD-T3] screening persist failed: %s", exc)
        return {
            "gated": True, "points": points, "breakdown": breakdown,
            "note": (
                f"below the escalation gate (points={points} < {gate_min_points}) — "
                "no reasoning pass spent. Cite any individual signals directly."
            ),
        }

    # L2 fraud case rubric — same store as artifact rubrics, case-level bucket.
    rubric_block = ""
    rubric_version = None
    if tenant_id and app_slug:
        try:
            # Clause memory, same as every other learned-memory site. This
            # module has no app_spec in scope, so the injection budget falls
            # back to the platform default — the bucket
            # (case / fraud-screening) is itself the scope here.
            from learned_memory import learned_block

            rubric_block, _clause_ids, rubric_version = await learned_block(
                app_spec=None, tenant_id=tenant_id, app_slug=app_slug,
                modality=FRAUD_RUBRIC_MODALITY, task_type=FRAUD_RUBRIC_TASK_TYPE,
            )
        except Exception as exc:  # noqa: BLE001 — never block synthesis on rubric
            log.warning("[FRAUD-T3] learned-memory load failed: %s", exc)

    prompt = (
        "You are a fraud analyst CROSS-EXAMINING one case. You are given the "
        "case context and the deterministic screening signals (field mismatches, "
        "duplicate/near-duplicate artifacts across cases, entity links to other "
        "cases, metadata anomalies, format failures).\n\n"
        f"CASE CONTEXT:\n{context.strip()}\n\n"
        f"SCREENING SIGNALS (points={points}, breakdown={json.dumps(breakdown)}):\n"
        f"{json.dumps(signals, default=str)[:12000]}\n\n"
        + (rubric_block + "\n\n" if rubric_block else "")
        + "Weigh the signals TOGETHER: which combinations are genuinely "
        "suspicious, which have benign explanations, what is missing. Your "
        "output is EVIDENCE for a human officer — never a verdict; the officer "
        "decides.\n"
        "Return ONLY one JSON object (no prose, no fences):\n"
        '{\n  "fraud_risk": <"low"|"medium"|"high">,\n'
        '  "key_indicators": [<the signals that genuinely matter, each with WHY>],\n'
        '  "benign_explanations": [<plausible innocent readings of the signals>],\n'
        '  "recommended_checks": [<concrete next verifications for the officer>],\n'
        '  "confidence": <0.0-1.0>,\n'
        '  "rationale": <2-4 sentences>\n}'
    )

    from llm_client import get_llm_client_for

    tier = settings.llm_tier_config(model_tier or "large")
    client = get_llm_client_for(tier["base_url"], tier["api_key"])
    chat = await client.chat.completions.create(
        model=tier["model"],
        messages=[{"role": "user", "content": prompt}],
        temperature=0.0,
        # Reasoning-safe, cost-neutral upper bound (see llm max_tokens doctrine).
        max_tokens=16000,
        extra_body=(tier.get("extra_body") or None),
    )
    text = chat.choices[0].message.content or ""
    parsed = _extract_json(text)
    usage = getattr(chat, "usage", None)

    out: Dict[str, Any] = {
        "gated": False, "points": points, "breakdown": breakdown,
        "sampled_audit": sampled, "rubric_version": rubric_version,
        "model": tier["model"],
        "tokens_in": getattr(usage, "prompt_tokens", 0) or 0,
        "tokens_out": getattr(usage, "completion_tokens", 0) or 0,
    }
    if parsed is None:
        out["error"] = "synthesis model returned non-JSON"
        out["raw"] = text[:800]
    else:
        out.update({
            "fraud_risk": parsed.get("fraud_risk"),
            "key_indicators": parsed.get("key_indicators") or [],
            "benign_explanations": parsed.get("benign_explanations") or [],
            "recommended_checks": parsed.get("recommended_checks") or [],
            "confidence": parsed.get("confidence"),
            "rationale": parsed.get("rationale"),
        })
        # L2 — surface the synthesis as a dispositionable CASE finding so the
        # officer can Confirm/Dismiss it in the run UI. The runtime collects any
        # tool result carrying ``item_id`` into ``item_findings``; ``item_type``
        # is the task_type the UI submits back (→ folds the fraud CASE rubric).
        # Fraud is EVIDENCE only — the recommendation says REVIEW, never reject.
        # Only surface a card for medium/high risk — a "low" verdict is a clean
        # bill, not something to put in front of the officer (avoids review noise;
        # the full assessment is still returned to the agent for its reasoning).
        if str(out.get("fraud_risk") or "").lower() in ("medium", "high"):
            out.update({
                "item_id": f"{record_id}{FRAUD_ITEM_SUFFIX}",
                "item_type": FRAUD_RUBRIC_TASK_TYPE,   # UI submits as task_type
                "modality": FRAUD_RUBRIC_MODALITY,     # "case"
                "subject": "fraud screening",
                "fields": {
                    "fraud_risk": out.get("fraud_risk"),
                    "points": points,
                    # Render-friendly (the UI stringifies dicts as [object Object]).
                    "signals": ", ".join(
                        f"{k}×{v}" for k, v in (breakdown or {}).items()
                    ) or "none",
                    "key_indicators": out.get("key_indicators") or [],
                },
                "recommendation": (
                    f"{out.get('fraud_risk')} fraud risk — review the flagged "
                    "signals. Evidence only; the officer decides."
                ),
            })

    row.update({
        "fraud_risk": out.get("fraud_risk"), "confidence": out.get("confidence"),
        "rubric_version": rubric_version,
    })
    try:
        await _ensure_scr_indexes()
        await _screenings_col().insert_one(dict(row))
    except Exception as exc:  # noqa: BLE001 — visible, non-blocking
        log.warning("[FRAUD-T3] screening persist failed: %s", exc)
        out["screening_persist_error"] = f"{type(exc).__name__}: {exc}"
    return out


# ---------------------------------------------------------------------------
# L3 calibration — IT-triggered (no cron): flag → disposition/outcome hit-rate
# ---------------------------------------------------------------------------

async def run_calibration(
    *, tenant_id: Optional[str], app_slug: str, limit: int = 2000
) -> Dict[str, Any]:
    """Join fraud screenings against DecisionRecords for one app and score each
    signal type's hit-rate against the officer's disposition (and the SoR
    outcome read-back when stamped). v1 ground truth = ``mode`` on the
    DecisionRecord (human_rejected ≈ the screen's suspicion was warranted).

    IT-triggered on demand — never a background job (plan §9)."""
    import main  # env-routed handle to DecisionRecords

    await _ensure_scr_indexes()
    screenings = await _screenings_col().find(
        {"tenant_id": tenant_id, "app_slug": app_slug},
        {"record_id": 1, "gated": 1, "breakdown": 1, "created_at": 1},
    ).sort("created_at", -1).to_list(length=limit)

    dr_col = main._route_col(
        main._db[main.get_settings().decision_records_collection],
        main.get_settings().decision_records_collection,
    )
    decisions = await dr_col.find(
        {"tenant_id": tenant_id, "slug": app_slug},
        {"mode": 1, "outcome": 1, "record_keys": 1, "created_at": 1},
    ).sort("created_at", -1).to_list(length=limit)

    # record_id → disposition/outcome, matched through record_keys values.
    disposition: Dict[str, Dict[str, Any]] = {}
    for d in decisions:
        keys = set()
        for rk in (d.get("record_keys") or []):
            if isinstance(rk, dict):
                keys.update(str(v) for v in rk.values() if v not in (None, ""))
        for k in keys:
            disposition.setdefault(k, {
                "mode": d.get("mode"),
                "outcome": (d.get("outcome") or {}) if isinstance(d.get("outcome"), dict) else {},
            })

    per_signal: Dict[str, Dict[str, int]] = {}
    matched = flagged_total = 0
    for s in screenings:
        disp = disposition.get(str(s.get("record_id")))
        if disp is None:
            continue
        matched += 1
        rejected = disp["mode"] == "human_rejected"
        was_flagged = not s.get("gated", True)
        if was_flagged:
            flagged_total += 1
        for sig, n in (s.get("breakdown") or {}).items():
            if not n:
                continue
            bucket = per_signal.setdefault(sig, {"cases": 0, "officer_rejected": 0})
            bucket["cases"] += 1
            if rejected:
                bucket["officer_rejected"] += 1

    report = {
        "app_slug": app_slug,
        "screenings_considered": len(screenings),
        "matched_to_decisions": matched,
        "flagged_and_matched": flagged_total,
        "per_signal_hit_rate": {
            sig: {
                **v,
                "rejection_rate": round(v["officer_rejected"] / v["cases"], 3) if v["cases"] else None,
            }
            for sig, v in sorted(per_signal.items())
        },
        "notes": (
            "v1 ground truth = officer disposition (human_rejected). Signals with "
            "a LOW rejection_rate over many cases are candidates for weight "
            "reduction / rubric pruning; HIGH ones justify raising their weight. "
            "Outcome read-back (DecisionRecord.outcome) refines this as it accrues."
        ),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    return report


# ---------------------------------------------------------------------------
# Officer verdict stamp + screening stats (admin Screening Health panel)
# See docs/fraud-screening-admin-panel-plan.md — §2 gap 1 (the stamp) and §1/§3
# (the aggregations + the deterministic false-alarm advisory matrix).
# ---------------------------------------------------------------------------

async def stamp_officer_verdict(
    *,
    tenant_id: Optional[str],
    app_slug: str,
    record_id: str,
    verdict: str,
    reason: Optional[str],
    actor: Optional[str],
) -> bool:
    """Stamp the officer's confirm/dismiss verdict onto the LATEST screening
    row for this case. Without this, case-modality feedback is rubric-only and
    the false-alarm rate is uncomputable (plan §2 gap 1).

    Returns True iff a screening row was found and stamped. A miss is logged
    loudly (feedback without a screening row means the agent never called
    fraud_synthesis for this record) but never fails the feedback request —
    the rubric write is the primary effect and has already happened."""
    if verdict not in ("confirmed", "dismissed"):
        raise ValueError(f"verdict must be confirmed|dismissed, got {verdict!r}")
    await _ensure_scr_indexes()
    doc = await _screenings_col().find_one_and_update(
        {"tenant_id": tenant_id, "app_slug": app_slug, "record_id": record_id},
        {"$set": {
            "officer_verdict": verdict,
            "verdict_reason": reason or None,
            "verdict_by": actor or None,
            "verdict_at": datetime.now(timezone.utc),
        }},
        sort=[("created_at", -1)],
    )
    if doc is None:
        log.warning(
            "[FRAUD] officer verdict %r for %s/%s has NO screening row to stamp "
            "— fraud_synthesis was never called for this record; verdict kept "
            "in the rubric only", verdict, app_slug, record_id)
        return False
    return True


#: signal_key → plain-English label + the §3 turn-off advisory. STATIC by
#: design (no LLM): the panel maps a noisy signal to its exact lever. Keys are
#: the closed set the gate scores (_POINTS_DEFAULTS + walker specials).
SIGNAL_ADVISORIES: Dict[str, Dict[str, str]] = {
    "exact_duplicate": {
        "label": "Reused evidence (byte-identical file)",
        "advisory": (
            "If this column legitimately repeats (headshot / ID scan / meter "
            "nameplate), declare it in sources.json: artifact_role: \"identity\" "
            "(reuse becomes verification) or \"supporting\" / reuse_policy: "
            "\"ignore\" (reuse ignored). Then re-crawl the catalogue and "
            "republish the app."),
    },
    "phash_near_dup": {
        "label": "Reused photo (re-encoded/resized)",
        "advisory": (
            "Same lever as exact duplicates: if reuse is expected for this "
            "column, set artifact_role: \"identity\" (or reuse_policy: "
            "\"ignore\") in sources.json → re-crawl → republish."),
    },
    "doc_text_near_dup": {
        "label": "Reused document (same text, re-exported)",
        "advisory": (
            "If this document is a shared template rather than case evidence, "
            "set artifact_role: \"supporting\" on the column in sources.json "
            "→ re-crawl → republish."),
    },
    "clip_near_duplicate": {
        "label": "Reused photo (cropped/re-shot copy)",
        "advisory": (
            "Same lever as exact duplicates (artifact_role / reuse_policy in "
            "sources.json). If only THIS tier is noisy platform-wide, reduce "
            "its weight via FRAUD_SIGNAL_WEIGHTS after an L3 calibration run."),
    },
    "clip_similar": {
        "label": "Visually similar photo (weak)",
        "advisory": (
            "Informational tier (1 point). If noisy, zero it via "
            "FRAUD_SIGNAL_WEIGHTS={\"clip_similar\": 0} (env, restart only)."),
    },
    "metadata_anomaly": {
        "label": "File metadata anomaly (edited-with / modified-after-creation)",
        "advisory": (
            "If a legitimate tool in your document flow triggers this (e.g. a "
            "scanner that stamps an editor), reduce the weight via "
            "FRAUD_SIGNAL_WEIGHTS={\"metadata_anomaly\": 0} after confirming "
            "with an L3 calibration run."),
    },
    "identity_cardinality": {
        "label": "One identifier, many names",
        "advisory": (
            "If an identifier is legitimately shared (family phone, office "
            "line), remove it from fraud_screening.identity_fields in "
            "sources.json → re-crawl → republish."),
    },
    "external_registry_match": {
        "label": "External registry match",
        "advisory": (
            "Highest-precision signal — investigate before tuning. If the "
            "registry itself is noisy, that is a source-data problem, not a "
            "screening knob."),
    },
    "severity_mismatch": {
        "label": "Claimed vs extracted value mismatch",
        "advisory": (
            "If one field mismatches persistently, check its type pin "
            "(field_types on the screen) and the document extraction quality "
            "before tuning anything."),
    },
    "severity_warn": {
        "label": "Field warning (minor inconsistency)",
        "advisory": "Low-weight by design; usually needs no action.",
    },
    "exif_capture_before_claim": {
        "label": "Photo predates the claimed incident",
        "advisory": (
            "If flags are wrong because the configured date column is the "
            "REPORT date (photos legitimately older), point "
            "fraud_screening.incident_date_field at the true incident-date "
            "column in sources.json — or remove it to drop this check. "
            "Re-crawl → republish."),
    },
    "exif_gps_far_from_claim": {
        "label": "Photo taken away from the claimed site",
        "advisory": (
            "If premise coordinates are imprecise, raise "
            "fraud_screening.gps_radius_km in sources.json (default 10); or "
            "remove location_lat_field/location_lon_field to drop the GPS "
            "check. Re-crawl → republish."),
    },
    "payment_ref_not_found": {
        "label": "Payment proof references a payment that doesn't exist",
        "advisory": (
            "Highest-precision signal — the ledger either has the reference "
            "or it doesn't. Repeated false alarms almost always mean the "
            "ontology points at the WRONG ledger dataset or match column: "
            "check fraud_screening.payment_proof.ledger_dataset/match_field "
            "in sources.json → re-crawl → republish."),
    },
    "payment_amount_mismatch": {
        "label": "Payment exists but the amount on the proof differs",
        "advisory": (
            "If legitimate partial payments or fees cause noise, raise "
            "payment_proof.amount_tolerance_pct in sources.json → re-crawl → "
            "republish."),
    },
    "payment_date_mismatch": {
        "label": "Payment exists but the date on the proof differs",
        "advisory": (
            "If settlement delays cause noise (value date vs booking date), "
            "raise payment_proof.date_window_days in sources.json → re-crawl "
            "→ republish."),
    },
    "payment_party_mismatch": {
        "label": "Genuine payment, but it belongs to someone else",
        "advisory": (
            "A real receipt reused by another party — investigate before "
            "tuning. If the party column legitimately differs (joint "
            "accounts, agents paying on behalf), re-point or remove "
            "payment_proof.party_field in sources.json."),
    },
    "camera_model_flip": {
        "label": "Multiple camera models in one photoset",
        "advisory": (
            "Corroboration-only (never counted as an issue). If photosets "
            "legitimately mix submitters' phones, zero it via "
            "FRAUD_SIGNAL_WEIGHTS={\"camera_model_flip\": 0}."),
    },
    "verify_ref_not_found": {
        "label": "Document references a record that doesn't exist",
        "advisory": (
            "Fact-grade: the declared system of record either has the "
            "reference or it doesn't. Repeated false alarms mean the "
            "verify_against block points at the wrong target_dataset or "
            "match_field in sources.json → re-crawl → republish."),
    },
    "verify_field_mismatch": {
        "label": "Record exists but a document value differs from it",
        "advisory": (
            "If legitimate variance causes noise, raise that comparison's "
            "tolerance_pct / window_days (or drop the compare entry) in the "
            "verify_against block in sources.json → re-crawl → republish."),
    },
    "date_rule_violation": {
        "label": "A declared date rule is violated",
        "advisory": (
            "Deterministic date arithmetic on the record's own values. If a "
            "rule keeps firing on legitimate cases, tune its min_days_between/"
            "max_days_between (or remove the rule) in "
            "fraud_screening.date_rules in sources.json → re-crawl → "
            "republish."),
    },
    "statement_chain_break": {
        "label": "Bank statement's running balance doesn't add up",
        "advisory": (
            "Fires only on REPEATED per-row balance breaks (single breaks are "
            "treated as OCR noise automatically). Repeated false alarms "
            "usually mean the extraction maps debit/credit columns wrongly — "
            "fix the document extraction, not the check."),
    },
    "resubmitted_after_rejection": {
        "label": "Linked to a previously DENIED case",
        "advisory": (
            "A shared identifier links this case to a prior case whose "
            "decision read as a denial — the finding quotes that decision "
            "verbatim. If it misfires, the usual cause is an over-broad "
            "identity_fields declaration (e.g. a shared office phone) — "
            "narrow it in sources.json."),
    },
    "photoset_timing_cluster": {
        "label": "Photos on several cases captured minutes apart",
        "advisory": (
            "Corroboration-only (weight 1 — can never gate a case alone, and "
            "never counts as an issue). App-scoped, not per-inspector: teams "
            "that legitimately batch-upload multiple sites' photos will "
            "cluster — if so, zero it via "
            "FRAUD_SIGNAL_WEIGHTS={\"photoset_timing_cluster\": 0}."),
    },
}

#: Advisory trigger thresholds (plan §3): enough verdicts to mean something,
#: and a dismissal rate high enough that the advice is near-certainly right.
_ADVISORY_MIN_VERDICTS = 5
_ADVISORY_DISMISS_RATE = 0.70
_STATS_MAX_ROWS = 5000
_REASONS_LIMIT = 25


def _latest_per_record(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Rows arrive created_at-DESC; keep the newest row per (app, record) so a
    re-screened case counts once, at its latest state."""
    seen: set = set()
    out: List[Dict[str, Any]] = []
    for r in rows:
        key = (r.get("app_slug"), str(r.get("record_id")))
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


async def screening_stats(
    *,
    tenant_id: Any,
    app_slug: Optional[str] = None,
    since: Optional[datetime] = None,
) -> Dict[str, Any]:
    """The Screening Health aggregation (plan §1). One function, two shapes:

      * org scope (``app_slug=None``) → totals + a per-app row table
        (the HomePanel card);
      * app scope → adds the signal-type breakdown, per-signal officer
        verdicts, verbatim dismissal reasons, and §3 advisories
        (the drill-down page).

    Python-side aggregation over a bounded, index-ordered fetch — same pattern
    as run_calibration; no cron, no new collections.

    ``tenant_id`` may be one tenant key (str/None) or a LIST of candidate keys:
    rows are written under the APP tenant, but an admin JWT may carry that
    value in either its org_id or tenant_id claim, so the org rollup matches
    on all of the caller's plausible keys rather than guessing one."""
    await _ensure_scr_indexes()
    match: Dict[str, Any] = {
        "tenant_id": {"$in": list(tenant_id)}
        if isinstance(tenant_id, (list, tuple, set)) else tenant_id
    }
    if app_slug:
        match["app_slug"] = app_slug
    if since is not None:
        match["created_at"] = {"$gte": since}
    rows = await _screenings_col().find(
        match,
        {"app_slug": 1, "record_id": 1, "points": 1, "breakdown": 1,
         "gated": 1, "officer_verdict": 1, "verdict_reason": 1,
         "verdict_by": 1, "verdict_at": 1, "created_at": 1},
    ).sort("created_at", -1).to_list(length=_STATS_MAX_ROWS)
    latest = _latest_per_record(rows)
    truncated = len(rows) >= _STATS_MAX_ROWS

    def _bucket() -> Dict[str, int]:
        return {"screened": 0, "warned": 0, "confirmed": 0, "dismissed": 0}

    totals = _bucket()
    per_app: Dict[str, Dict[str, int]] = {}
    per_signal: Dict[str, Dict[str, int]] = {}
    reasons: List[Dict[str, Any]] = []

    for r in latest:
        slug_key = r.get("app_slug") or "?"
        # A verdict counts toward confirmed/dismissed (and hence the
        # false-alarm RATE, defined as "false alarms ÷ warnings officers
        # judged") only when the case actually WARNED (points > 0). A
        # points=0 case escalated by the random audit sample can still carry
        # an officer verdict — counting its dismissal would inflate the rate
        # with cases that never raised a warning.
        _warned = (r.get("points") or 0) > 0
        for b in (totals, per_app.setdefault(slug_key, _bucket())):
            b["screened"] += 1
            if _warned:
                b["warned"] += 1
                if r.get("officer_verdict") == "confirmed":
                    b["confirmed"] += 1
                elif r.get("officer_verdict") == "dismissed":
                    b["dismissed"] += 1
        if app_slug:
            verdict = r.get("officer_verdict")
            for sig, n in (r.get("breakdown") or {}).items():
                if not n:
                    continue
                sb = per_signal.setdefault(sig, {"cases": 0, "confirmed": 0, "dismissed": 0})
                sb["cases"] += 1
                # Same warned-only rule as the totals above.
                if _warned and verdict in ("confirmed", "dismissed"):
                    sb[verdict] += 1
            if verdict == "dismissed" and r.get("verdict_reason") and len(reasons) < _REASONS_LIMIT:
                reasons.append({
                    "record_id": r.get("record_id"),
                    "reason": r.get("verdict_reason"),
                    "by": r.get("verdict_by"),
                    "at": r.get("verdict_at").isoformat() if r.get("verdict_at") else None,
                })

    def _rate(b: Dict[str, int]) -> Optional[float]:
        judged = b["confirmed"] + b["dismissed"]
        return round(b["dismissed"] / judged, 3) if judged else None

    out: Dict[str, Any] = {
        "tenant_id": tenant_id,
        "since": since.isoformat() if since else None,
        "totals": {**totals, "false_alarm_rate": _rate(totals)},
        "apps": [
            {"app_slug": s, **b, "false_alarm_rate": _rate(b)}
            for s, b in sorted(per_app.items())
        ],
    }
    if truncated:
        # No silent caps: the window covered only the newest _STATS_MAX_ROWS
        # screenings — say so rather than present a partial count as the whole.
        out["truncated_at"] = _STATS_MAX_ROWS
    if app_slug:
        out["app_slug"] = app_slug
        out["signals"] = {
            sig: {
                **sb,
                "label": SIGNAL_ADVISORIES.get(sig, {}).get("label", sig),
                "false_alarm_rate": _rate({"confirmed": sb["confirmed"],
                                           "dismissed": sb["dismissed"]}),
            }
            for sig, sb in sorted(per_signal.items())
        }
        out["dismissal_reasons"] = reasons
        advisories = []
        for sig, sb in sorted(per_signal.items()):
            judged = sb["confirmed"] + sb["dismissed"]
            if judged < _ADVISORY_MIN_VERDICTS:
                continue
            rate = sb["dismissed"] / judged
            if rate < _ADVISORY_DISMISS_RATE:
                continue
            meta = SIGNAL_ADVISORIES.get(sig)
            if not meta:
                continue
            advisories.append({
                "signal": sig,
                "label": meta["label"],
                "dismissed": sb["dismissed"],
                "judged": judged,
                "dismissal_rate": round(rate, 3),
                "advisory": meta["advisory"],
            })
        out["advisories"] = advisories
    out["generated_at"] = datetime.now(timezone.utc).isoformat()
    return out
