# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""Entity-link overlay — Phase P2a of docs/fraud-detection-primitives-plan.md §5.

The overlay index for what the SoR CANNOT answer: identifiers extracted from
inside artifacts, cross-source joins, and EXTERNAL parties (garage / payee /
agent). One Mongo doc per (tenant, entity_type, normalized_value) holding
POINTERS to the cases it appeared in — never payload; the SoR record stays the
single source of truth (overlay doctrine, `app-owned-data-plane.md`).

Populated WRITE-THROUGH when a case is screened (`consistency_check` tool) —
no crawler, no cron. Values stored PLAINTEXT (decision 2026-07-02: dedicated
single-tenant deployment; investigator UX wins).

Signals produced (all explainable, with refs):
  * ring / reuse       — same identifier on ≥2 DISTINCT cases (role-tagged)
  * synthetic identity — one identifier co-occurring with many distinct names
  * double-dip         — same invoice/policy/VIN "id"-type value on ≥2 cases
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fraud_checks import (
    detect_field_type,
    normalize_id,
    normalize_name,
    normalize_phone,
)

log = logging.getLogger(__name__)

_COLLECTION = "smartapp_entity_links"

# Only high-precision identifier types are linked (closed vocabulary — never
# "all entities"). Names are NOT linkable keys (fuzzy, false-positive-prone in
# v1); they ride along as COMPANIONS for the synthetic-identity cardinality
# signal. `id` covers policy_no / claim_no / invoice_no / reg_no / serial —
# the same value recurring across cases is the classic double-dip tell.
LINKABLE_TYPES = {
    "phone", "email", "account", "vin", "id",          # global
    "ssn", "ein", "routing",                            # US
    "pan", "gstin", "aadhaar",                          # India
}

_MAX_REFS = 200          # cap stored refs per entity (count keeps incrementing)
_MAX_COMPANION_NAMES = 20


def _col():
    """Env-routed collection handle (lazy-main pattern, see analysis_rubrics)."""
    import main  # deferred — avoids import cycle

    if getattr(main, "_db", None) is None:
        raise RuntimeError("Database not initialised")
    name = _COLLECTION
    try:
        if main.current_env() == "test":
            name = main._test_collection_name(_COLLECTION)
    except Exception:  # noqa: BLE001 — env unknown ⇒ prod collection (safe default)
        pass
    return main._db[name]


# Keyed by ROUTED collection name (env routing is per-request — a plain bool
# would leave the other env's collection unindexed after first use).
_indexes_ensured: set = set()


async def _ensure_indexes() -> None:
    col = _col()
    if col.name in _indexes_ensured:
        return
    await col.create_index(
        [("tenant_id", 1), ("entity_type", 1), ("value", 1)], unique=True
    )
    _indexes_ensured.add(col.name)


def _normalize_for(ftype: str, value: Any) -> Optional[str]:
    if value in (None, ""):
        return None
    if ftype == "phone":
        v = normalize_phone(str(value))
        return v if len(v) == 10 else None  # partial numbers link nothing
    if ftype == "email":
        # Emails must NOT go through normalize_id — hyphens/dots are
        # significant ('john-doe@ex-corp.com' ≠ 'johndoe@excorp.com'); merging
        # them would fabricate ring links between unrelated claimants.
        v = str(value).strip().lower()
        return v if "@" in v and len(v) >= 6 else None
    v = normalize_id(str(value))
    return v if len(v) >= 4 else None  # too-short ids (e.g. "12") link noise


def extract_linkable(
    values: Dict[str, Any],
    pinned_types: Optional[Dict[str, str]] = None,
    identity_fields: Optional[List[str]] = None,
) -> List[Dict[str, str]]:
    """{field: value} → [{entity_type, value(normalized), field}] for linkable
    types only. Companion names are extracted separately.

    ``identity_fields`` is the SOURCE ONTOLOGY's declaration (sources.json
    ``fraud_screening.identity_fields``, autowired onto the consistency_check) of
    which columns are cross-record linkable keys. It is AUTHORITATIVE for
    INCLUSION: a declared field links even when the name heuristic — or the
    builder-authored ``field_types`` pin — doesn't recognise it as an identifier.
    Without this, WHICH identifiers link cases together would be decided by the
    LLM/heuristic rather than by the source that actually knows its own keys.

    It only ever ADDS, never restricts: an undeclared field still links on the
    heuristic, so the ontology can never silently WEAKEN cross-case linking (same
    doctrine as artifact_role — the ontology may relax scoring, never screening)."""
    declared = {f for f in (identity_fields or []) if f}
    out: List[Dict[str, str]] = []
    for field, raw in (values or {}).items():
        ftype = (pinned_types or {}).get(field) or detect_field_type(field)
        declared_generic = False
        if ftype not in LINKABLE_TYPES:
            if field not in declared:
                continue
            # The ontology says this IS an identity key → link it as the generic
            # double-dip type. A specific pinned/detected type (vin/pan/…) wins.
            # RING KEYS (Phase 6): a declared field the heuristic does NOT
            # recognise is typically a soft key — address, employer, witness,
            # garage — declared exactly to catch rings. Those link fine but are
            # marked so the lookup scores them as CORROBORATION (warn), never
            # as a hard-identifier mismatch: a shared office address must not
            # weigh like a shared bank account.
            ftype = "id"
            declared_generic = True
        norm = _normalize_for(ftype, raw)
        if norm:
            ent = {"entity_type": ftype, "value": norm, "field": field}
            if declared_generic:
                ent["declared_generic"] = True
            out.append(ent)
    return out


def _link_severity(ent: Dict[str, Any]) -> str:
    """Hard identifiers (bank account, VIN, a RECOGNISED id) score as
    mismatch; a declared-generic RING key (address / employer / witness —
    linked only because the ontology listed it) is corroboration: warn, never
    mismatch. A shared office address must not weigh like a shared account."""
    if ent.get("declared_generic"):
        return "warn"
    return "mismatch" if ent.get("entity_type") in ("id", "vin", "account") else "warn"


def _companion_names(values: Dict[str, Any]) -> List[str]:
    names = []
    for field, raw in (values or {}).items():
        if detect_field_type(field) == "name" and raw not in (None, ""):
            n = normalize_name(str(raw))
            if n:
                names.append(n)
    return names


async def link_and_lookup(
    *,
    tenant_id: Optional[str],
    app_slug: Optional[str],
    record_ref: str,
    values: Dict[str, Any],
    pinned_types: Optional[Dict[str, str]] = None,
    identity_fields: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Write-through upsert of this case's identifiers + cross-case lookup.

    Returns one signal per identifier that links to OTHER cases (or shows a
    cardinality anomaly). Silent on identifiers seen for the first time.

    ``identity_fields`` — the source ontology's linkable-key declaration; see
    ``extract_linkable`` (ontology-authoritative for inclusion).
    """
    import asyncio

    await _ensure_indexes()
    col = _col()
    now = datetime.now(timezone.utc)
    entities = extract_linkable(values, pinned_types, identity_fields)
    companions = _companion_names(values)
    signals: List[Dict[str, Any]] = []

    async def _upsert(ent: Dict[str, str]):
        key = {"tenant_id": tenant_id, "entity_type": ent["entity_type"], "value": ent["value"]}
        ref = {"record_ref": record_ref, "app_slug": app_slug,
               "field": ent["field"], "seen_at": now}
        # Pre-update doc → its refs/names are the PRIORS for this lookup.
        # Projection: the signal only cites the last few refs — never drag a
        # hot entity's full 200-ref history over the wire.
        return await col.find_one_and_update(
            key,
            {
                "$setOnInsert": {**key, "first_seen": now},
                "$set": {"last_seen": now},
                "$inc": {"ref_count": 1},
                "$push": {"refs": {"$each": [ref], "$slice": -_MAX_REFS}},
                "$addToSet": {"names_seen": {"$each": companions[:_MAX_COMPANION_NAMES]}},
            },
            upsert=True,
            return_document=False,
            projection={"refs": {"$slice": -8}, "names_seen": 1, "ref_count": 1},
        )

    # Independent keys — upsert/lookup all identifiers concurrently.
    priors = await asyncio.gather(*(_upsert(e) for e in entities))

    for ent, prior in zip(entities, priors):
        if not prior:
            continue  # first sighting — nothing to report
        # "Other case" = a DIFFERENT record. Compared via same_record_ref so a
        # dataset-qualified ref and a legacy bare one for the SAME record don't read
        # as two cases (which would invent a shared-identifier signal out of one
        # record being re-screened). See fraud_checks.qualify_record_ref.
        from fraud_checks import same_record_ref

        other_refs = [
            r for r in (prior.get("refs") or [])
            if r.get("record_ref") and not same_record_ref(r["record_ref"], record_ref)
        ]
        prior_names = set(prior.get("names_seen") or [])
        new_names = [n for n in companions if n not in prior_names]
        distinct_names = len(prior_names | set(companions))

        sig: Dict[str, Any] = {}
        if other_refs:
            # refs is $slice-projected (last 8) — cite samples from it, but
            # count from ref_count (total prior sightings) so a hot entity's
            # magnitude isn't understated by the projection.
            total_prior = int(prior.get("ref_count") or len(other_refs))
            sig["signal"] = "shared_identifier"
            sig["severity"] = _link_severity(ent)
            sig["note"] = (
                f"{ent['entity_type']} '{ent['value']}' (field '{ent['field']}') also "
                f"appears on other case(s) — {total_prior} prior sighting(s)"
            )
            sig["other_cases"] = [
                {"record_ref": r.get("record_ref"), "app_slug": r.get("app_slug"),
                 "field": r.get("field"), "seen_at": str(r.get("seen_at"))}
                for r in other_refs[-5:]
            ]
            sig["prior_sightings"] = total_prior
        if prior_names and new_names and distinct_names >= 3:
            # One identifier, many names → synthetic-identity cardinality tell.
            sig.setdefault("signal", "identity_cardinality")
            # A declared-generic RING key is a natural many-names hub (every
            # customer of one garage / employer) — cardinality must not
            # escalate it back to hard-identifier severity.
            if not ent.get("declared_generic"):
                sig["severity"] = "mismatch"
            sig["cardinality_note"] = (
                f"{ent['entity_type']} '{ent['value']}' has now appeared with "
                f"{distinct_names} distinct claimant/payee names"
            )
        if sig:
            sig["entity_type"] = ent["entity_type"]
            sig["field"] = ent["field"]
            signals.append(sig)
    return signals


# ── E3: resubmission-after-rejection join ────────────────────────────────────
# A shared identifier alone says "same actor, other cases". The DECISION on
# those prior cases is what upgrades it: the same phone/account on a case that
# was DENIED, now back on a fresh one, is the resubmission pattern. Only prior
# decisions whose COMMITTED decision reads as a denial count:
#   * mode=human_rejected (officer rejected the AI's RECOMMENDATION) is NOT a
#     denied case — excluded by the query;
#   * an approval WITH overrides means the officer changed the writes, so the
#     AI's decision text no longer describes what was committed — excluded;
#   * negated phrasing ("should not be rejected") must not read as a denial.
_DENIAL_RX = re.compile(
    r"\b(reject|denied|deny|declin|dismiss|refus|fail)\w*", re.IGNORECASE)
_NEGATION_RX = re.compile(
    r"\b(not|never|no|n't|don't|doesn't|shouldn't|won't|cannot|can't|"
    r"isn't|wasn't|without)\s+(be\s+|being\s+)?$", re.IGNORECASE)
_E3_MAX_KEYS = 25
_E3_MAX_PRIORS = 50


def _reads_as_denial(text: str) -> bool:
    """True when the decision text carries a NON-negated denial stem."""
    for m in _DENIAL_RX.finditer(text or ""):
        # Look back a short window for a negation immediately preceding the
        # stem ("should not be rejected", "don't decline") — those are not
        # denials. Any other match is.
        if not _NEGATION_RX.search(text[max(0, m.start() - 24):m.start()]):
            return True
    return False


def _bare_key(record_ref: Optional[str]) -> Optional[str]:
    """The record key part of a record_ref — delegated to the ref grammar's
    owner (fraud_checks) so the parse can never diverge from qualify/same."""
    from fraud_checks import bare_record_key

    return bare_record_key(record_ref)


async def rejected_priors(
    *,
    tenant_id: Optional[str],
    entity_signals: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """E3 — for the OTHER cases each shared identifier cites, look up their
    decision records and fire when a prior case was decided as a denial.

    Matching is by the prior case's write-target key candidates (DecisionRecord
    record_keys.key_values — the write payload's scalar values, stamped at
    commit) against the entity ref's record key — bare-key grain, so treat the
    result as CORROBORATION (weight 2), not proof; the finding cites the prior
    decision verbatim so the officer can judge."""
    if not (tenant_id and entity_signals):
        return []
    keys: set = set()
    ref_by_key: Dict[str, Dict[str, Any]] = {}
    for sig in entity_signals:
        for oc in (sig.get("other_cases") or []):
            k = _bare_key(oc.get("record_ref"))
            if k:
                keys.add(k)
                ref_by_key.setdefault(k, {**oc, "entity_type": sig.get("entity_type"),
                                           "field": sig.get("field")})
            if len(keys) >= _E3_MAX_KEYS:
                break
    if not keys:
        return []

    import main  # deferred — avoids import cycle (lazy-main pattern)

    if getattr(main, "_db", None) is None:
        raise RuntimeError("Database not initialised")
    col = main.get_decision_records_col()
    out: List[Dict[str, Any]] = []
    cursor = col.find(
        {
            "tenant_id": tenant_id,
            "mode": {"$in": ["human_approved", "auto_process"]},
            "record_keys.key_values": {"$in": sorted(keys)},
        },
        {"decision_id": 1, "slug": 1, "mode": 1, "created_at": 1,
         "recommendation.decision": 1, "record_keys": 1, "overrides": 1},
    ).sort("created_at", -1).limit(_E3_MAX_PRIORS)
    async for rec in cursor:
        # Officer overrides mean the committed writes differ from the AI's
        # decision text — that text is no longer evidence of what was decided.
        if rec.get("overrides"):
            continue
        decision = str((rec.get("recommendation") or {}).get("decision") or "")
        if not _reads_as_denial(decision):
            continue
        matched = next(
            (kv for rk in (rec.get("record_keys") or [])
             for kv in (rk.get("key_values") or []) if str(kv) in keys), None)
        if matched is None:
            continue
        matched = str(matched)
        oc = ref_by_key.get(matched) or {}
        out.append({
            "signal": "resubmitted_after_rejection",
            "severity": "warn",
            "prior_record": matched,
            "prior_app_slug": rec.get("slug"),
            "prior_decision_id": rec.get("decision_id"),
            "prior_decided_at": str(rec.get("created_at")),
            "prior_decision": decision[:200],
            "entity_type": oc.get("entity_type"),
            "field": oc.get("field"),
            "note": (
                f"Shared {oc.get('entity_type') or 'identifier'} "
                f"(field '{oc.get('field')}') links this case to prior case "
                f"'{matched}' ({rec.get('slug')}), which was decided: "
                f"\"{decision[:120]}\" on {str(rec.get('created_at'))[:10]} — "
                "the resubmission-after-denial pattern."
            ),
        })
    return out
