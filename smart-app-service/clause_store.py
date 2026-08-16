"""Clause store — atomic, scoped, provenanced learned rules.
Phase C of docs/clause-memory-graph-plan.md.

Replaces the single 1000-word ``smartapp_analysis_rubrics.summary`` blob with N
atomic clauses, each ≤40 words and SCOPED to a facet set. A clause fires for a
case iff ``scope_facets ⊆ case_facets`` (§6), so the injection budget becomes a
per-case SELECTION problem instead of a write-time COMPRESSION problem — the
store is unbounded and lossless while the prompt gets smaller and sharper.

The invariant this module exists to enforce:

    **A clause's text is written ONCE, at birth, from ~3 corrections, and is
    never rewritten by later feedback.**

Reinforcement (`reinforce`) touches provenance and counters ONLY. That is the
entire anti-dilution argument: today every correction triggers an LLM rewrite of
all 1000 words — the Nth lossy re-encode of an already-lossy encode. Here the
text is encoded once.

Storage is Mongo, deliberately NOT a graph database: the hot query is set
containment on an indexed array, traversals are ≤3 hops, and volumes are
thousands of clauses per app (plan §7). Revisit only for variable-length path
queries over ``entity_links``.

Collection: ``smartapp_clauses`` (env-routed, same lazy-main pattern as
analysis_rubrics / corrections / entity_links).
"""
from __future__ import annotations

import logging
import math
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from case_signature import UNKNOWN

log = logging.getLogger(__name__)

_COLLECTION = "smartapp_clauses"

#: One rule, one sentence. A clause longer than this is two clauses.
CLAUSE_MAX_WORDS = int(os.getenv("CLAUSE_MAX_WORDS", "40"))

#: Statuses that may be injected into a run prompt.
#:   active     → TEAM judgement (corroborated) — asserted, cited
#:   candidate  → INDIVIDUAL judgement — injected with honest attribution
#:                ("one officer's judgement, not yet corroborated"). Doctrine
#:                (sop-rules-officer-judgement-plan §0): a lone officer's
#:                experience is used immediately and LABELED, never hidden —
#:                a one-officer branch office must still learn.
#:   dissented  → rendered as a disagreement NOTICE, never as a judgement
#: 'sop_conflict' (contradicts the SOP, under review) and 'quarantined'
#: (suspended by an admin, e.g. taught by a dismissed officer) are excluded
#: from injection entirely; sop_conflict surfaces as a one-line notice.
LIVE_STATUSES = ("active", "candidate", "dissented")
#: 'orphaned' — the clause is scoped to a facet family the app no longer emits,
#: so `scope_facets ⊆ case_facets` can never hold and it cannot fire on any
#: case. Excluded from LIVE_STATUSES deliberately: a clause that cannot match is
#: not live knowledge, and leaving it `active` makes the Memory screen overstate
#: what the app knows. Set by reconcile_scope_families at publish.
#: 'challenged' — a supervisor stopped this judgement pending adjudication.
#: The lever an experienced officer needs and did not have: corroboration is a
#: HEADCOUNT, so three juniors who share a misconception form a team judgement
#: while the one person who knows better contributes 1 dissent in 4 (0.25) —
#: under DISSENT_RATIO, so nothing happened. They had to find a second dissenter
#: before their objection had any effect at all.
#:
#: The fix is deliberately NOT a trust tier. Weighting officers by seniority
#: would encode org hierarchy into an audit trail a regulator reads, and
#: seniority is not correctness. This is a ROLE-held stop: it does not rank
#: anyone, it parks one clause and forces a named human to decide. Every
#: transition carries actor + cause into `history` (set_status), and the
#: challenge itself records who, when and why.
#:
#: 'underperforming' — the clause is measurably wrong more often than
#: PRECISION_FLOOR allows, over at least MIN_FIRED_FOR_PRECISION firings. Set by
#: the consolidation job, never by a person. Evidence, not authority: it catches
#: the three-juniors case without anyone needing to be senior.
ALL_STATUSES = ("candidate", "active", "dissented", "superseded", "retired",
                "sop_conflict", "quarantined", "orphaned",
                "challenged", "underperforming")

#: Cap on INDIVIDUAL judgements per case. A case may consult a few
#: uncorroborated opinions; it must never drown in twelve of them.
MAX_INDIVIDUAL_JUDGEMENTS = int(os.getenv("MAX_INDIVIDUAL_JUDGEMENTS", "3"))

#: A clause whose dissent share reaches this is not a rule — it is an open
#: disagreement. Silently averaging it is exactly the failure being removed.
DISSENT_RATIO = float(os.getenv("CLAUSE_DISSENT_RATIO", "0.34"))

#: Ranking weights. Specificity is the PRIMARY sort (n-gram backoff, §8 step 5);
#: these break ties within a specificity tier.
W_SUPPORT = 1.0
W_PRECISION = 1.5
W_RECENCY = 0.75
#: Precision assumed for a clause that has not fired enough times to measure.
#: Optimistic but not certain — a new clause should be tried, not trusted.
PRECISION_PRIOR = 0.8
MIN_FIRED_FOR_PRECISION = int(os.getenv("CLAUSE_MIN_FIRED_FOR_PRECISION", "10"))
RECENCY_HALFLIFE_DAYS = float(os.getenv("CLAUSE_RECENCY_HALFLIFE_DAYS", "180"))

#: Below this, a clause that HAS fired enough is a refinement candidate.
PRECISION_FLOOR = float(os.getenv("CLAUSE_PRECISION_FLOOR", "0.7"))

_MAX_SUPPORT_OFFICERS = 50
_CLAUSE_ID_RE = re.compile(r"^C-(\d+)$")

_indexes_ensured: set = set()


class ClauseError(RuntimeError):
    """An invariant of the clause store was violated by a caller."""


def _col():
    import main  # deferred — avoids the import cycle through tool dispatch

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
    key = col.name
    if key in _indexes_ensured:
        return
    try:
        # THE hot retrieval index — multikey on scope_facets so the $in
        # prefilter in select_clauses is index-selective (plan §6).
        await col.create_index(
            [("tenant_id", 1), ("app_slug", 1), ("modality", 1),
             ("task_type", 1), ("status", 1), ("scope_facets", 1)],
            name="ix_scope_lookup",
        )
        await col.create_index(
            [("tenant_id", 1), ("app_slug", 1), ("clause_id", 1)],
            unique=True, name="ux_clause_id",
        )
        await col.create_index(
            [("tenant_id", 1), ("app_slug", 1), ("status", 1), ("updated_at", -1)],
            name="ix_memory_screen",
        )
        await col.create_index(
            [("tenant_id", 1), ("app_slug", 1), ("reason_code", 1)],
            name="ix_reason_code",
        )
        _indexes_ensured.add(key)
    except Exception:  # noqa: BLE001 — best-effort; loud
        log.exception("[CLAUSE] index creation failed for %s", key)


def _key(tenant_id: str, app_slug: str, modality: str, task_type: str) -> Dict[str, str]:
    return {"tenant_id": tenant_id, "app_slug": app_slug,
            "modality": modality, "task_type": task_type}


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def normalize_scope(scope_facets: Optional[Sequence[str]]) -> List[str]:
    """Canonicalise a clause scope, rejecting tokens that must never be scoped.

    ``__unknown`` marks a case whose column carried an UNDECLARED value. Scoping
    a clause to it would turn an ontology-drift alarm into a silently-firing
    rule — the drift signal must stay diagnostic, never become policy."""
    out = sorted({str(f).strip() for f in (scope_facets or []) if str(f).strip()})
    bad = [f for f in out if f.endswith(f":{UNKNOWN}")]
    if bad:
        raise ClauseError(
            f"clause scope may not contain drift tokens {bad} — __unknown means "
            "the ontology did not declare that value; fix the case_signature "
            "instead of learning a rule about the gap"
        )
    return out


def _clip_words(text: str, max_words: int = CLAUSE_MAX_WORDS) -> str:
    words = (text or "").split()
    if len(words) <= max_words:
        return " ".join(words)
    log.warning("[CLAUSE] text clipped %d -> %d words", len(words), max_words)
    return " ".join(words[:max_words]) + " …"


async def next_clause_id(*, tenant_id: str, app_slug: str) -> str:
    """Next ``C-<n>`` for the app. Consolidation is leader-elected and single
    threaded per bucket, so max+1 is safe; the unique index is the backstop."""
    rows = await _col().find(
        {"tenant_id": tenant_id, "app_slug": app_slug}, {"_id": 0, "clause_id": 1},
    ).to_list(100_000)
    highest = 0
    for r in rows:
        m = _CLAUSE_ID_RE.match(str(r.get("clause_id") or ""))
        if m:
            highest = max(highest, int(m.group(1)))
    return f"C-{highest + 1:03d}"


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------


async def create_clause(
    *,
    tenant_id: str,
    app_slug: str,
    modality: str,
    task_type: str,
    text: str,
    scope_facets: Optional[Sequence[str]],
    reason_code: Optional[str],
    provenance: Sequence[str],
    support_officers: Sequence[str],
    contested_fields: Optional[Sequence[str]] = None,
    override_moves: Optional[Dict[str, Dict[str, List[str]]]] = None,
    match_tokens: Optional[Sequence[str]] = None,
    signature_version: Optional[int] = None,
    promotion_min_officers: int = 3,
    authored_by: str = "consolidation",
) -> Dict[str, Any]:
    """Create a clause from a cluster of corrections.

    Born ``candidate`` unless DISTINCT officer support already meets the gate.
    That gate is what stops one prolific officer writing the app's policy —
    it counts distinct officers, never per-officer weights (no trust tiers)."""
    text = _clip_words((text or "").strip())
    if not text:
        raise ClauseError("clause text must not be empty")
    scope = normalize_scope(scope_facets)
    officers = sorted({str(o) for o in support_officers if o})[:_MAX_SUPPORT_OFFICERS]
    prov = sorted({str(p) for p in provenance if p})
    if not prov:
        raise ClauseError(
            "a clause must cite the corrections that taught it — an unprovenanced "
            "clause is LLM-authored policy, which this store does not accept"
        )

    now = datetime.now(timezone.utc)
    cid = await next_clause_id(tenant_id=tenant_id, app_slug=app_slug)
    doc: Dict[str, Any] = {
        "clause_id": cid,
        **_key(tenant_id, app_slug, modality, task_type),
        "text": text,
        "text_words": len(text.split()),
        "scope_facets": scope,
        "scope_size": len(scope),
        "signature_version": signature_version,
        "reason_code": reason_code,
        "contested_fields": sorted({str(f) for f in (contested_fields or []) if f}),
        # {field: {"from": [...], "to": [...]}} across the authoring cluster.
        # The evidence-level record of WHICH WAY officers moved a field, kept so
        # contradiction detection can compare directions instead of guessing
        # from labels. Empty for clauses authored before this existed.
        "override_moves": override_moves or {},
        # Lexical fingerprint of the OFFICER language that taught this clause,
        # kept separate from `text` (an LLM paraphrase). Later corrections are
        # matched against this, not against the paraphrase — officer wording
        # matches officer wording, and a rendered rule may share almost no
        # vocabulary with the complaints that produced it.
        "match_tokens": sorted({str(t) for t in (match_tokens or []) if t}),
        "provenance": prov,
        "support_officers": officers,
        "support_count": len(officers),
        "dissent_officers": [],
        "dissent_count": 0,
        "fired_count": 0,
        "blamed_count": 0,
        "precision": None,
        "status": "active" if len(officers) >= promotion_min_officers else "candidate",
        "version": 1,
        "history": [],
        "refines": [],
        "refined_by": [],
        "contradicts": [],
        "superseded_by": None,
        "merged_from": [],
        "authored_by": authored_by,
        "created_at": now,
        "updated_at": now,
        "last_confirmed_at": now,
    }
    col = _col()
    await _ensure_indexes(col)
    await col.insert_one(dict(doc))
    doc.pop("_id", None)
    log.info(
        "[CLAUSE] created %s %s/%s status=%s scope=%s officers=%d",
        cid, app_slug, reason_code or "-", doc["status"], scope, len(officers),
    )
    return doc


async def reinforce(
    *,
    tenant_id: str,
    app_slug: str,
    clause_id: str,
    correction_ids: Sequence[str],
    officers: Sequence[str],
    match_tokens: Optional[Sequence[str]] = None,
    promotion_min_officers: int = 3,
) -> Optional[Dict[str, Any]]:
    """Fold matching corrections into an EXISTING clause.

    Touches provenance, officers and timestamps ONLY. **The text is not
    rewritten** — that is the anti-dilution invariant (module docstring). Also
    promotes candidate → active once distinct-officer support reaches the gate.
    """
    col = _col()
    await _ensure_indexes(col)
    doc = await col.find_one({"tenant_id": tenant_id, "app_slug": app_slug,
                              "clause_id": clause_id})
    if not doc:
        log.warning("[CLAUSE] reinforce: %s not found for %s", clause_id, app_slug)
        return None

    now = datetime.now(timezone.utc)
    prov = sorted(set(doc.get("provenance") or []) | {str(c) for c in correction_ids if c})
    offs = sorted(set(doc.get("support_officers") or [])
                  | {str(o) for o in officers if o})[:_MAX_SUPPORT_OFFICERS]
    update: Dict[str, Any] = {
        "provenance": prov,
        "support_officers": offs,
        "support_count": len(offs),
        "last_confirmed_at": now,
        "updated_at": now,
    }
    if match_tokens:
        # The fingerprint widens with the evidence — this is NOT the text, so
        # updating it does not violate the write-once invariant.
        update["match_tokens"] = sorted(
            set(doc.get("match_tokens") or []) | {str(t) for t in match_tokens if t}
        )
    if doc.get("status") == "candidate" and len(offs) >= promotion_min_officers:
        update["status"] = "active"
        log.info("[CLAUSE] %s promoted candidate -> active (%d officers)",
                 clause_id, len(offs))
    await col.update_one({"_id": doc["_id"]}, {"$set": update})
    return {**doc, **update}


def facet_family(token: str) -> str:
    """``'loss_type:theft'`` → ``'loss_type'``."""
    return str(token).split(":", 1)[0]


async def reconcile_scope_families(
    *,
    tenant_id: str,
    app_slug: str,
    families: Sequence[str],
    alias_map: Optional[Dict[str, str]] = None,
    actor: Optional[str] = None,
) -> Dict[str, Any]:
    """Reconcile stored clause scopes against the app's CURRENT facet families.

    Retrieval is ``scope_facets ⊆ case_facets``. A clause scoped to a family the
    app no longer emits can therefore never match a case again — but nothing
    detected that, so it kept its `active` status and simply stopped firing. It
    is the worst failure shape this codebase has: knowledge that looks present
    and is not.

    Two outcomes, and never a third:

    * the family was RENAMED and the new spec declares the old name in
      ``FacetSpec.aliases`` — the scope token is rewritten in place
      (``income_proof:present`` → ``income_proof_type:present``) and the clause
      keeps firing. Value vocabulary is assumed unchanged; that is what an alias
      asserts.
    * the family is simply GONE — the clause is moved to ``orphaned``, out of
      LIVE_STATUSES, so it stops counting as knowledge the app has. Reversible:
      re-add the family (or alias it) and the next reconcile restores nothing
      automatically, but the history entry records exactly what happened and
      why.

    Returns ``{"migrated": n, "orphaned": n, "families_dropped": [...]}``.
    Never raises on a single bad clause — one unreadable document must not stop
    the rest of an app's memory being reconciled.
    """
    known = {str(f) for f in (families or []) if f}
    amap = {str(k): str(v) for k, v in (alias_map or {}).items()}
    col = _col()
    migrated = 0
    orphaned = 0
    dropped: set = set()

    rows = await col.find(
        {"tenant_id": tenant_id, "app_slug": app_slug,
         "status": {"$in": list(LIVE_STATUSES)}},
    ).to_list(100_000)
    for doc in rows:
        scope = list(doc.get("scope_facets") or [])
        if not scope:
            continue  # globally-scoped clause — no family to go stale
        new_scope: List[str] = []
        unknown: List[str] = []
        for tok in scope:
            fam = facet_family(tok)
            if fam in known:
                new_scope.append(tok)
            elif fam in amap and amap[fam] in known:
                new_scope.append(f"{amap[fam]}:{str(tok).split(':', 1)[1]}"
                                 if ":" in str(tok) else amap[fam])
            else:
                unknown.append(tok)
                dropped.add(fam)
        try:
            if unknown:
                await set_status(
                    tenant_id=tenant_id, app_slug=app_slug,
                    clause_id=doc.get("clause_id"), status="orphaned",
                    actor=actor or "system",
                    cause=(f"scope references facet famil"
                           f"{'ies' if len(set(map(facet_family, unknown))) > 1 else 'y'} "
                           f"{sorted(set(map(facet_family, unknown)))} which the app "
                           f"no longer emits — the clause could never match a case"),
                )
                orphaned += 1
            elif new_scope != scope:
                now = datetime.now(timezone.utc)
                await col.update_one(
                    {"tenant_id": tenant_id, "app_slug": app_slug,
                     "clause_id": doc.get("clause_id")},
                    {"$set": {"scope_facets": sorted(new_scope), "updated_at": now,
                              "version": int(doc.get("version") or 1) + 1},
                     "$push": {"history": {
                         "version": doc.get("version"), "text": doc.get("text"),
                         "scope_facets": scope, "status": doc.get("status"),
                         "changed_by": actor or "system",
                         "cause": "facet family renamed — scope migrated through "
                                  "FacetSpec.aliases",
                         "at": now}}},
                )
                migrated += 1
        except Exception:  # noqa: BLE001 — one bad clause must not stop the sweep
            log.exception("[CLAUSE] scope reconcile failed for %s",
                          doc.get("clause_id"))

    if migrated or orphaned:
        log.warning(
            "[CLAUSE] %s/%s scope reconcile: %d migrated, %d ORPHANED%s",
            tenant_id, app_slug, migrated, orphaned,
            f" (families gone: {sorted(dropped)})" if dropped else "")
    return {"migrated": migrated, "orphaned": orphaned,
            "families_dropped": sorted(dropped)}


async def set_status(
    *, tenant_id: str, app_slug: str, clause_id: str, status: str,
    actor: Optional[str] = None, cause: Optional[str] = None,
) -> None:
    """Transition a clause's status, snapshotting the prior state into history.

    Never an in-place silent mutation — the same discipline
    ``edit_rubric_summary`` applies to the legacy blob."""
    if status not in ALL_STATUSES:
        raise ClauseError(f"unknown clause status {status!r}")
    col = _col()
    doc = await col.find_one({"tenant_id": tenant_id, "app_slug": app_slug,
                              "clause_id": clause_id})
    if not doc:
        raise LookupError(f"no clause {clause_id} for {app_slug}")
    now = datetime.now(timezone.utc)
    await col.update_one(
        {"_id": doc["_id"]},
        {"$set": {"status": status, "updated_at": now,
                  "version": int(doc.get("version") or 1) + 1},
         "$push": {"history": {
             "version": doc.get("version"), "text": doc.get("text"),
             "scope_facets": doc.get("scope_facets"), "status": doc.get("status"),
             "changed_by": actor or "system", "cause": cause, "at": now}}},
    )
    log.info("[CLAUSE] %s status %s -> %s (%s)",
             clause_id, doc.get("status"), status, cause or "-")


async def add_edge(
    *, tenant_id: str, app_slug: str, clause_id: str, edge: str, target: str,
) -> None:
    """Add a graph edge (refines / refined_by / contradicts / merged_from)."""
    if edge not in ("refines", "refined_by", "contradicts", "merged_from"):
        raise ClauseError(f"unknown clause edge {edge!r}")
    await _col().update_one(
        {"tenant_id": tenant_id, "app_slug": app_slug, "clause_id": clause_id},
        {"$addToSet": {edge: target},
         "$set": {"updated_at": datetime.now(timezone.utc)}},
    )


async def record_dissent(
    *, tenant_id: str, app_slug: str, clause_id: str, officer: str,
) -> None:
    """Register an officer who acted AGAINST a clause that fired.

    Dissent is stored, never resolved — a clause whose dissent share crosses
    DISSENT_RATIO stops being injected as a rule and becomes a disagreement
    notice plus a builder adjudication flag."""
    col = _col()
    doc = await col.find_one({"tenant_id": tenant_id, "app_slug": app_slug,
                              "clause_id": clause_id})
    if not doc:
        return
    dis = sorted(set(doc.get("dissent_officers") or []) | {officer})[:_MAX_SUPPORT_OFFICERS]
    sup = len(doc.get("support_officers") or [])
    status = doc.get("status")
    # Individual judgements flip too: one supporter vs one dissenter is an OPEN
    # QUESTION between two officers, not anyone's judgement — presenting it as
    # either would silently pick a winner.
    if status in ("active", "candidate") and (len(dis) / max(1, sup + len(dis))) >= DISSENT_RATIO:
        status = "dissented"
        log.info("[CLAUSE] %s -> dissented (%d support vs %d dissent)",
                 clause_id, sup, len(dis))
    await col.update_one(
        {"_id": doc["_id"]},
        {"$set": {"dissent_officers": dis, "dissent_count": len(dis),
                  "status": status, "updated_at": datetime.now(timezone.utc)}},
    )


async def apply_performance(
    *, tenant_id: str, app_slug: str, counters: Dict[str, Dict[str, int]],
) -> int:
    """Write aggregated fired/blamed counters (plan §10.4).

    Called by the consolidation job from stamps on decision rows — NEVER on the
    hot path. precision stays None until MIN_FIRED_FOR_PRECISION, so an unproven
    clause is ranked on its prior rather than on a 1-sample accident."""
    col = _col()
    n = 0
    now = datetime.now(timezone.utc)
    for cid, c in (counters or {}).items():
        fired = int(c.get("fired") or 0)
        blamed = int(c.get("blamed") or 0)
        precision = (1.0 - blamed / fired) if fired >= MIN_FIRED_FOR_PRECISION else None
        res = await col.update_one(
            {"tenant_id": tenant_id, "app_slug": app_slug, "clause_id": cid},
            {"$set": {"fired_count": fired, "blamed_count": blamed,
                      "precision": precision, "updated_at": now}},
        )
        n += int(getattr(res, "modified_count", 0) or 0)

        # ACT on the number, don't just rank by it. PRECISION_FLOOR used to
        # appear in exactly one place — a stats field called `needs_refinement`
        # — so a judgement officers had overruled on 4 of every 10 cases it
        # fired kept firing, merely ranked a little lower. Measured wrongness
        # with no consequence is the same failure as knowledge that looks
        # present and is not.
        #
        # This is the evidence-based half of the seniority problem: three
        # juniors who agree can still form a team judgement, but the moment the
        # cases show it is wrong it stops being applied — nobody has to outrank
        # anyone. Parked, never deleted: the evidence stays, the clause goes to
        # a human, and a corrected version can re-form from the same
        # corrections.
        if precision is not None and precision < PRECISION_FLOOR:
            doc = await col.find_one(
                {"tenant_id": tenant_id, "app_slug": app_slug, "clause_id": cid},
                {"status": 1})
            if (doc or {}).get("status") in ("active", "candidate"):
                await set_status(
                    tenant_id=tenant_id, app_slug=app_slug, clause_id=cid,
                    status="underperforming", actor="precision-monitor",
                    cause=(f"officers overruled this on {blamed} of {fired} "
                           f"cases it fired (precision {precision:.2f}, floor "
                           f"{PRECISION_FLOOR:.2f}) — parked pending review"),
                )
                log.warning(
                    "[CLAUSE] %s PARKED as underperforming — blamed on %d of %d "
                    "firings (precision %.2f < floor %.2f). It stops being "
                    "injected; a supervisor should retire or refine it.",
                    cid, blamed, fired, precision, PRECISION_FLOOR)
    return n


# ---------------------------------------------------------------------------
# Read / retrieval
# ---------------------------------------------------------------------------


async def candidates_for_facets(
    *,
    tenant_id: str,
    app_slug: str,
    modality: str,
    task_type: str,
    case_facets: Sequence[str],
    statuses: Sequence[str] = LIVE_STATUSES,
    limit: int = 200,
) -> List[Dict[str, Any]]:
    """Clauses whose scope is a SUBSET of the case's facets (plan §6).

    The ``$in`` clause drives the multikey index (any clause sharing ≥1 facet);
    ``$setIsSubset`` is the exact containment filter applied as a residual on
    that small candidate set. Globally-scoped clauses (``scope_facets: []``)
    always match. This is the query a graph database was not needed for."""
    facets = sorted({str(f) for f in (case_facets or []) if f})
    col = _col()
    await _ensure_indexes(col)
    q: Dict[str, Any] = {
        **_key(tenant_id, app_slug, modality, task_type),
        "status": {"$in": list(statuses)},
        "$and": [
            {"$or": [{"scope_facets": {"$size": 0}},
                     {"scope_facets": {"$in": facets}}]},
            {"$expr": {"$setIsSubset": ["$scope_facets", facets]}},
        ],
    }
    return await col.find(q, {"_id": 0}).to_list(limit)


def score_clause(doc: Dict[str, Any], *, now: Optional[datetime] = None) -> float:
    """Tie-break score WITHIN a specificity tier. Specificity itself is the
    primary sort key and is applied by the caller (n-gram backoff)."""
    now = now or datetime.now(timezone.utc)
    support = int(doc.get("support_count") or 0)
    precision = doc.get("precision")
    precision = PRECISION_PRIOR if precision is None else float(precision)
    last = doc.get("last_confirmed_at") or doc.get("updated_at")
    age_days = 0.0
    if isinstance(last, datetime):
        ref = last if last.tzinfo else last.replace(tzinfo=timezone.utc)
        age_days = max(0.0, (now - ref).total_seconds() / 86400.0)
    recency = math.exp(-age_days / max(1.0, RECENCY_HALFLIFE_DAYS))
    return (W_SUPPORT * math.log1p(support)
            + W_PRECISION * precision
            + W_RECENCY * recency)


def rank_and_budget(
    docs: Sequence[Dict[str, Any]],
    *,
    budget_words: int,
    now: Optional[datetime] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Order, dedupe and fill to the injection budget.

    Returns ``(rules, dissented)``. Sort is (scope_size DESC, score DESC) —
    specificity first, which IS the backoff: a thin
    (theft ∧ photo ∧ us ∧ >25k) cell falls through to (theft ∧ photo) then
    (theft) with no special-casing and no cold-start cliff.

    Dedupe keeps the MOST SPECIFIC survivor per (reason_code, contested_fields):
    a general clause is redundant once a narrower one on the same lesson fires.
    """
    now = now or datetime.now(timezone.utc)
    team: List[Dict[str, Any]] = []
    individual: List[Dict[str, Any]] = []
    notices: List[Dict[str, Any]] = []
    for d in docs:
        st = d.get("status")
        if st in ("dissented", "sop_conflict"):
            notices.append(d)
        elif st == "candidate":
            individual.append(d)
        else:
            team.append(d)

    def _order(d):
        return (-int(d.get("scope_size") or 0), -score_clause(d, now=now))

    team.sort(key=_order)
    individual.sort(key=_order)

    # TIER is the primary key: every team judgement outranks every individual
    # one, however specific the individual is — corroboration beats precision
    # of scope. Within a tier, specificity then score (the n-gram backoff).
    # Dedupe across tiers keeps the FIRST seen per lesson, so a team judgement
    # silently SHADOWS an individual one on the same (reason, fields) — by the
    # time a team judgement exists, the individual's evidence is usually
    # already inside it.
    seen: set = set()
    picked: List[Dict[str, Any]] = []
    used = 0
    individuals_used = 0
    for d in team + individual:
        is_individual = d.get("status") == "candidate"
        if is_individual and individuals_used >= MAX_INDIVIDUAL_JUDGEMENTS:
            continue
        sig = (d.get("reason_code"), tuple(d.get("contested_fields") or ()))
        if sig != (None, ()) and sig in seen:
            continue
        w = int(d.get("text_words") or len(str(d.get("text") or "").split())) + 6
        if used + w > budget_words:
            continue
        seen.add(sig)
        picked.append(d)
        used += w
        if is_individual:
            individuals_used += 1
    return picked, notices


async def clause_display_meta(
    *,
    tenant_id: str,
    app_slug: str,
    clause_ids: Sequence[str],
) -> Dict[str, Dict[str, Any]]:
    """``{clause_id: {text, support_count, support_officers, status}}`` for the
    officer's screen.

    ``select_clauses`` returns only ids, and the model's audit block carries only
    ``{clause_id, relation, note}``. Nothing joined the two, so the decision
    card's "what your team has taught" block rendered a bordered line with no
    sentence and no names on it — the attribution the whole memory feature exists
    to show. One indexed read over ids already in hand.

    Enrichment: a failure here must never take the decision down, so it logs and
    yields an empty map — the caller degrades to ids without prose."""
    ids = [str(c) for c in (clause_ids or []) if c]
    if not ids:
        return {}
    try:
        cur = _col().find(
            {"tenant_id": tenant_id, "app_slug": app_slug,
             "clause_id": {"$in": ids}},
            {"_id": 0, "clause_id": 1, "text": 1, "support_count": 1,
             "support_officers": 1, "status": 1},
        )
        out: Dict[str, Dict[str, Any]] = {}
        async for doc in cur:
            cid = str(doc.get("clause_id") or "")
            if cid:
                out[cid] = doc
        return out
    except Exception as exc:  # noqa: BLE001 — enrichment, loud but never fatal
        log.warning("[CLAUSE] display metadata unavailable for %s: %s", ids, exc)
        return {}


async def judgement_evidence(
    *, tenant_id: str, app_slug: str, clause_id: str, max_cases: int = 5,
) -> Optional[Dict[str, Any]]:
    """The evidence behind one judgement — what `lookup_judgement` returns.

    Everything here is ALREADY stored; none of it was reachable by the agent.
    Deliberately includes the officers' own sentences: a clause text is one
    LLM's compression of three corrections, and the originals often carry the
    condition or the exception the compression dropped.

    Returns None for an unknown id rather than raising — a model citing a
    clause that does not exist should get a clean "not found", not a tool error
    that costs it an iteration to interpret.
    """
    col = _col()
    doc = await col.find_one(
        {"tenant_id": tenant_id, "app_slug": app_slug, "clause_id": clause_id},
        {"_id": 0},
    )
    if not doc:
        return None
    cases: List[Dict[str, Any]] = []
    try:
        import corrections as cx

        rows = await cx._col().find(
            {"correction_id": {"$in": list(doc.get("provenance") or [])}},
            {"_id": 0},
        ).to_list(max_cases)
        # The record AS THE OFFICER SAW IT, from the DecisionRecord written at
        # the time. Not a live read of the row today: the decision was applied
        # TO that row, so fetching it now returns the aftermath — status already
        # flipped — and would teach the model the opposite of what the officer
        # was looking at. This snapshot costs one indexed lookup and cannot go
        # stale by construction.
        import main as _m

        _snap: Dict[str, Any] = {}
        # Record corrections carry a RUN correlation_id; item (image/document)
        # corrections carry the ITEM id instead, which no DecisionRecord will
        # ever match. Filtering here keeps that an intentional no-op rather
        # than a query that quietly returns nothing for half the modalities.
        _cids = ([r.get("correlation_id") for r in rows if r.get("correlation_id")]
                 if str(doc.get("modality") or "") == "record" else [])
        if _cids:
            try:
                async for d in _m._db["decision_records"].find(
                    {"correlation_id": {"$in": _cids}},
                    {"_id": 0, "correlation_id": 1, "context": 1},
                ):
                    if d.get("context"):
                        _snap[d["correlation_id"]] = d["context"]
            except Exception:  # noqa: BLE001 — the case list is still useful
                log.exception("[CLAUSE] could not load decision context for %s",
                              clause_id)
        for r in rows:
            cases.append({
                "what_the_officer_wrote": r.get("reason_text"),
                # What the AGENT had concluded, which is what the officer was
                # correcting. For a record decision this is the recommendation
                # ("Approve ₹5,39,000"). For an IMAGE or DOCUMENT it is the
                # finding the vision / doc-reader sub-agent produced — and it is
                # the whole case content, because the artifact itself is never
                # returned: a photo or a PDF cannot go in a prompt, and the
                # lesson was never about those bytes. It is about HOW TO READ
                # them — "a nameplate photo where the serial is illegible is not
                # evidence of the asset" — which lives in this pair of fields,
                # not in the pixels.
                "what_the_agent_said": r.get("recommendation"),
                "case": r.get("case_facets") or [],
                "changed": r.get("overrides") or [],
                "event": r.get("event"),
                # WHICH record, and WHAT IT LOOKED LIKE when the officer
                # decided. The pointer alone was a half-answer: it only helped
                # if the app happened to wire a read tool for that dataset, and
                # dereferencing it cost the agent another round trip inside a
                # loop an officer is waiting on.
                "record_ref": r.get("case_ref"),
                "record_at_the_time": _snap.get(r.get("correlation_id")),
            })
    except Exception:  # noqa: BLE001 — evidence is enrichment; the clause is not
        log.exception("[CLAUSE] could not load provenance for %s", clause_id)

    fired = int(doc.get("fired_count") or 0)
    upheld = doc.get("upheld_count")
    return {
        "clause_id": clause_id,
        "text": doc.get("text"),
        "applies_when": doc.get("scope_facets") or [],
        "officers_set": doc.get("override_moves") or {},
        "standing": doc.get("status"),
        "officers_behind_it": int(doc.get("support_count") or 0),
        "officers_against_it": int(doc.get("dissent_count") or 0),
        "times_applied": fired,
        "times_upheld": upheld,
        "cases": cases,
    }


def _moves_phrase(moves: Dict[str, Any]) -> str:
    """" — officers set decision = verify_employment (was approve)".

    Empty for a clause with no recorded moves (authored before override_moves
    existed, or from rejects that changed no field), so nothing is invented."""
    if not isinstance(moves, dict) or not moves:
        return ""
    parts = []
    for field, io_ in sorted(moves.items()):
        to = [str(t) for t in (io_ or {}).get("to") or [] if t]
        frm = [str(f) for f in (io_ or {}).get("from") or [] if f]
        if not to:
            continue
        seg = f"{field} = {' / '.join(to[:3])}"
        if frm:
            seg += f" (was {' / '.join(frm[:2])})"
        parts.append(seg)
    return f" — officers set {'; '.join(parts)}" if parts else ""


def render_block(
    rules: Sequence[Dict[str, Any]],
    dissented: Sequence[Dict[str, Any]] = (),
    *,
    header: Optional[str] = None,
    lookup_tool: Optional[str] = None,
) -> str:
    """Render the injection block. Empty string when there is nothing to apply.

    Every rule carries its ``clause_id`` because the model is asked to cite the
    ids it relied on — that citation is the blame edge, and without it feedback
    lands on the whole set again (the bug this design removes)."""
    if not rules and not dissented:
        return ""
    # The authority hierarchy, stated where the model reads it (doctrine:
    # sop-rules-officer-judgement-plan §0 — SOP is king; these are JUDGEMENTS).
    head = header or (
        "JUDGEMENTS learned from this app's officers — their experience, "
        "covering what the rules/SOP do not spell out."
    )
    lines = [
        head,
        "Your RULES (the SOP) are SUPREME: a judgement can never override a "
        "rule. If a judgement conflicts with a rule, FOLLOW THE RULE and cite "
        'the judgement with relation "overrode_by_rule" so the team can '
        "review it. Apply the judgements that fit this case; cite the ids you "
        "relied on.",
    ]
    # Mentioned ONLY when the app actually wires the tool. Telling a model to
    # call a tool it does not have wastes tokens on every run and invites a
    # hallucinated call. Kept to one line because it is paid on every run
    # whether or not the tool is ever used — the per-case block replacing a
    # 1000-word blob is the whole point, and test_reports_the_real_prompt_cost
    # holds it under 100 words.
    if lookup_tool:
        lines.append(
            f"Call {lookup_tool}(<id>) for the cases behind a judgement — "
            "worth it before overruling one."
        )
    for d in rules:
        n = int(d.get("support_count") or 0)
        if d.get("status") == "candidate":
            who = ("one officer's judgement" if n <= 1
                   else f"{n} officers' judgement")
            tag = (f" ({who} — not yet corroborated by the team; weigh it and "
                   "verify against the record before relying on it)")
        else:
            tag = f" (team judgement — {n} officer{'s' if n != 1 else ''})"
        # THE ACTION, not just the sentence. A judgement is authored from
        # officer prose, and prose can be vague — "check this more carefully"
        # passes every quality gate we have and tells the model nothing it can
        # execute. The move is unambiguous and already recorded: N officers set
        # THIS field to THIS value on cases like this one. It is one short
        # clause, and it is what makes the injected line self-sufficient — the
        # model can act correctly on it WITHOUT calling lookup_judgement.
        act = _moves_phrase(d.get("override_moves") or {})
        lines.append(f"- [{d.get('clause_id')}] {d.get('text')}{act}{tag}")
    for d in dissented:
        if d.get("status") == "sop_conflict":
            lines.append(
                "⚠ A learned judgement on this kind of case conflicts with the "
                "SOP and is under review — follow the SOP."
            )
            continue
        sup = int(d.get("support_count") or 0)
        dis = int(d.get("dissent_count") or 0)
        lines.append(
            f"⚠ Officers disagree on this point ({sup} vs {dis}): "
            f"{d.get('text')} Surface both readings; do not assert."
        )
    return "\n".join(lines)


async def select_clauses(
    *,
    tenant_id: str,
    app_slug: str,
    modality: str,
    task_type: str,
    case_facets: Sequence[str],
    budget_words: int = 1000,
    max_dissent_lines: int = 2,
    header: Optional[str] = None,
    lookup_tool: Optional[str] = None,
) -> Tuple[str, List[str]]:
    """The run-time entry point: ``(prompt_block, injected_clause_ids)``.

    Enrichment path — a store failure logs loudly and returns an empty block so
    the run proceeds, exactly as the legacy rubric read does. Learning degrading
    must never take a decision down with it."""
    try:
        docs = await candidates_for_facets(
            tenant_id=tenant_id, app_slug=app_slug, modality=modality,
            task_type=task_type, case_facets=case_facets,
        )
    except Exception:  # noqa: BLE001 — enrichment; loud, never blocks a run
        log.exception(
            "[CLAUSE] retrieval failed for %s/%s/%s — run proceeds without "
            "learned clauses", app_slug, modality, task_type,
        )
        return "", []

    rules, dissented = rank_and_budget(docs, budget_words=budget_words)
    dissented = list(dissented)[:max_dissent_lines]
    block = render_block(rules, dissented, header=header, lookup_tool=lookup_tool)
    injected = [d["clause_id"] for d in rules if d.get("clause_id")]
    log.info(
        "[CLAUSE] selected %d/%d clause(s) (+%d dissent) for %s — %d facets",
        len(rules), len(docs), len(dissented), app_slug, len(case_facets or []),
    )
    return block, injected


async def list_clauses(
    *,
    tenant_ids: Sequence[str],
    app_slug: str,
    statuses: Optional[Sequence[str]] = None,
    limit: int = 500,
) -> List[Dict[str, Any]]:
    """Memory-screen listing, most-fired first. Loud-but-empty on failure."""
    tenants = [t for t in (tenant_ids or []) if t]
    if not tenants or not app_slug:
        return []
    q: Dict[str, Any] = {"tenant_id": {"$in": tenants}, "app_slug": app_slug}
    if statuses:
        q["status"] = {"$in": list(statuses)}
    try:
        rows = await _col().find(q, {"_id": 0}).to_list(limit)
        rows.sort(key=lambda r: (-int(r.get("fired_count") or 0),
                                 -int(r.get("support_count") or 0)))
        return rows
    except Exception:  # noqa: BLE001 — read view; loud, never raises to the API
        log.exception("[CLAUSE] listing failed for %s", app_slug)
        return []


async def export_clauses(*, tenant_ids: Sequence[str], app_slug: str) -> List[Dict[str, Any]]:
    """Every clause for the app, as-is. RAISES on store failure so the caller
    can mark the export partial — a swallowed failure would look like 'this app
    learned nothing', which is a very different claim."""
    tenants = [t for t in (tenant_ids or []) if t]
    if not tenants or not app_slug:
        return []
    return await _col().find(
        {"tenant_id": {"$in": tenants}, "app_slug": app_slug}, {"_id": 0},
    ).to_list(20_000)


async def clause_inventory(*, tenant_ids: Sequence[str], app_slug: str) -> Dict[str, Any]:
    """Counters for the memory-impact card (plan §19.2)."""
    rows = await list_clauses(tenant_ids=tenant_ids, app_slug=app_slug)
    by_status: Dict[str, int] = {}
    for r in rows:
        by_status[r.get("status") or "?"] = by_status.get(r.get("status") or "?", 0) + 1
    fired = sum(int(r.get("fired_count") or 0) for r in rows)
    measured = [r for r in rows if r.get("precision") is not None]
    return {
        "total": len(rows),
        "by_status": by_status,
        "fired_total": fired,
        "precision_measured": len(measured),
        "precision_p50": (
            sorted(float(r["precision"]) for r in measured)[len(measured) // 2]
            if measured else None
        ),
        "needs_refinement": [
            r["clause_id"] for r in measured
            if float(r["precision"]) < PRECISION_FLOOR
        ],
        "dissented": [r["clause_id"] for r in rows if r.get("status") == "dissented"],
    }


async def resolve_sop_conflict(
    *, tenant_id: str, app_slug: str, clause_id: str, action: str,
    actor: Optional[str], promotion_min_officers: int = 3,
) -> Dict[str, Any]:
    """The supervisor's two-tap resolution for an sop_conflict judgement (J3).

    action='retire'      — the SOP is right; the judgement retires (evidence
                           stays, so a compliant version can re-form).
    action='acknowledge' — the officers are right and the SOP is stale. The
                           judgement returns to service (its tier re-derived
                           from officer support) carrying `sop_ack` with the
                           actor and the recorded disagreement — the org's
                           signal that the SOP itself needs updating.
    RAISES on bad input / unknown clause — a supervisor's explicit action must
    never silently no-op."""
    if action not in ("retire", "acknowledge"):
        raise ClauseError(f"unknown sop resolution {action!r}")
    col = _col()
    doc = await col.find_one({"tenant_id": tenant_id, "app_slug": app_slug,
                              "clause_id": clause_id})
    if not doc:
        raise LookupError(f"no clause {clause_id} for {app_slug}")
    if doc.get("status") != "sop_conflict":
        raise ClauseError(
            f"{clause_id} is not in sop_conflict (status={doc.get('status')})")
    if action == "retire":
        await set_status(tenant_id=tenant_id, app_slug=app_slug,
                         clause_id=clause_id, status="retired", actor=actor,
                         cause="sop_conflict resolved: SOP is right")
        return {"clause_id": clause_id, "status": "retired"}
    tier = ("active" if int(doc.get("support_count") or 0) >= promotion_min_officers
            else "candidate")
    await col.update_one(
        {"_id": doc["_id"]},
        {"$set": {"sop_ack": {"by": actor,
                              "at": datetime.now(timezone.utc),
                              "conflict": doc.get("sop_conflict")}}})
    await set_status(tenant_id=tenant_id, app_slug=app_slug,
                     clause_id=clause_id, status=tier, actor=actor,
                     cause="sop_conflict acknowledged: SOP needs updating")
    return {"clause_id": clause_id, "status": tier, "sop_ack": True}


def is_stale(doc: Dict[str, Any], *, days: int = 365) -> bool:
    """A clause no correction has reinforced in `days` — a fossil, not knowledge.
    Reported, never auto-retired: retiring is a builder decision."""
    last = doc.get("last_confirmed_at")
    if not isinstance(last, datetime):
        return False
    ref = last if last.tzinfo else last.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - ref > timedelta(days=days)


async def challenge_clause(
    *, tenant_id: str, app_slug: str, clause_id: str, reason: str,
    actor: str,
) -> Dict[str, Any]:
    """Stop a judgement pending adjudication — the experienced officer's lever.

    Corroboration is a HEADCOUNT. Three officers who share a misconception form
    a team judgement; the one person who knows better contributes one dissent in
    four (0.25), under DISSENT_RATIO, so nothing happens until they find a
    second dissenter. Their objection had no effect on its own, while the
    juniors needed nobody's agreement.

    This is the missing lever, and deliberately NOT a trust tier. Weighting
    officers by seniority would encode org hierarchy into an audit trail a
    regulator reads, and seniority is not correctness. A challenge ranks nobody:
    it parks ONE clause and forces a named human to decide, and it is fully
    attributed — actor and cause land in ``history`` via set_status, and the
    ``challenge`` block records who, when and why in the officer's own words.

    Parked, never deleted. The corrections that taught it are untouched, so an
    adjudicator can dismiss the challenge and put it straight back.

    RAISES on an unknown clause or an already-terminal one — an explicit human
    action must never silently no-op."""
    reason = (reason or "").strip()
    if not reason:
        raise ClauseError(
            "a challenge needs a reason — the adjudicator is being asked to "
            "decide between two officers and cannot do that from a flag alone")
    if not (actor or "").strip():
        raise ClauseError("a challenge must record who raised it")

    col = _col()
    doc = await col.find_one({"tenant_id": tenant_id, "app_slug": app_slug,
                              "clause_id": clause_id})
    if not doc:
        raise LookupError(f"no clause {clause_id} for {app_slug}")
    # Only something IN SERVICE can be stopped. Allowing a challenge against an
    # already-parked clause (quarantined by an admin, parked on evidence,
    # orphaned) achieved nothing as a control and opened a laundering route:
    # challenge it, then "dismiss" the challenge, and it comes back live.
    if doc.get("status") not in LIVE_STATUSES:
        raise ClauseError(
            f"{clause_id} is {doc.get('status')} — it is not being applied to "
            "any case, so there is nothing to stop. If it should be removed "
            "for good, retire it.")
    if doc.get("status") == "challenged":
        raise ClauseError(
            f"{clause_id} is already challenged by "
            f"{(doc.get('challenge') or {}).get('by') or 'someone'} and is "
            "waiting on adjudication")

    now = datetime.now(timezone.utc)
    await col.update_one(
        {"_id": doc["_id"]},
        {"$set": {"challenge": {"by": actor, "at": now, "reason": reason[:600],
                                "status_before": doc.get("status")}}},
    )
    await set_status(
        tenant_id=tenant_id, app_slug=app_slug, clause_id=clause_id,
        status="challenged", actor=actor,
        cause=f"challenged: {reason[:300]}",
    )
    log.warning(
        "[CLAUSE] %s CHALLENGED by %s — %s. It stops being injected until "
        "adjudicated.", clause_id, actor, reason[:160])
    return {"clause_id": clause_id, "status": "challenged",
            "challenged_by": actor, "challenged_at": now.isoformat()}


async def resolve_challenge(
    *, tenant_id: str, app_slug: str, clause_id: str, action: str,
    reason: Optional[str], actor: str, promotion_min_officers: int = 3,
) -> Dict[str, Any]:
    """Adjudicate a challenge. Every outcome is recorded, none is silent.

    action='uphold'   — the challenger is right; the judgement retires. The
                        corrections that taught it stay, so a corrected version
                        can re-form rather than the evidence being erased.
    action='dismiss'  — the objection is overruled and the judgement goes back
                        to EXACTLY the state the challenge interrupted.
    action='withdraw' — the CHALLENGER taking their own objection back. Same
                        restore, different meaning, and it is the only
                        resolution they may make themselves.

    Two rules this had wrong, both of which turned a governance control into a
    governance bypass:

    SEPARATION OF DUTIES. The challenger may not uphold or dismiss their own
    challenge. Without this one person could park a judgement three officers
    taught and then retire it alone, with no second name anywhere in the trail
    — the opposite of "forces a named human to decide". They can still
    `withdraw`, so a single-admin org is never stuck; a withdrawal simply is
    not dressed up as an adjudication.

    RESTORE, DO NOT PROMOTE. Dismiss used to re-derive the tier from
    support_count, which meant challenge-then-dismiss lifted an admin's
    quarantine, un-parked a clause the evidence had retired from service, or
    cleared a dissent — laundering anything into `active`. It now restores
    ``challenge.status_before``. Only when that was itself a live tier is the
    tier re-derived, because officer support may have moved while it was
    parked."""
    if action not in ("uphold", "dismiss", "withdraw"):
        raise ClauseError(f"unknown challenge resolution {action!r}")
    if not (actor or "").strip():
        raise ClauseError("an adjudication must record who made it")

    col = _col()
    doc = await col.find_one({"tenant_id": tenant_id, "app_slug": app_slug,
                              "clause_id": clause_id})
    if not doc:
        raise LookupError(f"no clause {clause_id} for {app_slug}")
    if doc.get("status") != "challenged":
        raise ClauseError(
            f"{clause_id} is not challenged (status={doc.get('status')})")

    challenge = dict(doc.get("challenge") or {})
    raised_by = (challenge.get("by") or "").strip()
    same_person = raised_by and raised_by == actor.strip()
    if same_person and action != "withdraw":
        raise ClauseError(
            f"{actor} raised this challenge and cannot also {action} it — an "
            "adjudication needs a second person. Withdraw it instead if you no "
            "longer want it stopped.")
    if action == "withdraw" and not same_person:
        raise ClauseError(
            f"only {raised_by or 'the officer who raised it'} can withdraw this "
            f"challenge. As the adjudicator, uphold or dismiss it.")

    now = datetime.now(timezone.utc)
    resolution = {"by": actor, "at": now, "action": action,
                  "reason": (reason or "").strip()[:600]}
    # Write the whole sub-document rather than a dotted path: the original
    # challenge (who raised it, when, why) must survive the resolution — an
    # objection that was overruled is still part of the record.
    challenge["resolution"] = resolution
    await col.update_one({"_id": doc["_id"]}, {"$set": {"challenge": challenge}})

    _why = f": {resolution['reason'][:200]}" if resolution["reason"] else ""
    if action == "uphold":
        await set_status(
            tenant_id=tenant_id, app_slug=app_slug, clause_id=clause_id,
            status="retired", actor=actor,
            cause=f"challenge upheld by {actor}{_why}")
        return {"clause_id": clause_id, "status": "retired", "action": action}

    # Back to whatever the challenge interrupted — never a promotion.
    before = challenge.get("status_before") or "candidate"
    if before in ("active", "candidate"):
        before = ("active"
                  if int(doc.get("support_count") or 0) >= promotion_min_officers
                  else "candidate")
    elif before not in ALL_STATUSES:
        before = "candidate"
    await set_status(
        tenant_id=tenant_id, app_slug=app_slug, clause_id=clause_id,
        status=before, actor=actor,
        cause=f"challenge {action}n by {actor}{_why}"
        if action == "withdraw" else f"challenge dismissed by {actor}{_why}")
    return {"clause_id": clause_id, "status": before, "action": action}


async def reinstate_clause(
    *, tenant_id: str, app_slug: str, clause_id: str, actor: str,
    reason: Optional[str] = None, promotion_min_officers: int = 3,
) -> Dict[str, Any]:
    """Put a clause parked on EVIDENCE back into service, with a fresh window.

    Without this, ``underperforming`` is a one-way door. A parked clause is out
    of LIVE_STATUSES, so it never fires again; ``fired_count`` stops growing and
    ``precision`` is frozen at the value that parked it, forever. The only exit
    would be retire — which is wrong for a judgement that dipped during a bad
    fortnight, or one whose `blamed` count is inflated because officers
    overrode those cases for an unrelated reason.

    **Resets the counters.** Reinstating without doing so is pointless: the next
    consolidation pass recomputes precision from the same cumulative totals,
    sees it still under the floor, and parks it again — a flap, not a decision.
    Zeroing them gives the judgement a genuine re-measurement window, which is
    what a human reinstating it is actually asking for. The counters that
    parked it are preserved on the history entry, so the reset is not a
    laundering of its record.

    Only for clauses the MONITOR parked. A quarantine or a retirement is a
    person's decision and is lifted through its own path, not this one."""
    if not (actor or "").strip():
        raise ClauseError("a reinstatement must record who made it")
    col = _col()
    doc = await col.find_one({"tenant_id": tenant_id, "app_slug": app_slug,
                              "clause_id": clause_id})
    if not doc:
        raise LookupError(f"no clause {clause_id} for {app_slug}")
    if doc.get("status") != "underperforming":
        raise ClauseError(
            f"{clause_id} is {doc.get('status')}, not parked on its results — "
            "reinstating applies only to a judgement the precision monitor "
            "withdrew.")

    was = (int(doc.get("blamed_count") or 0), int(doc.get("fired_count") or 0),
           doc.get("precision"))
    tier = ("active" if int(doc.get("support_count") or 0) >= promotion_min_officers
            else "candidate")
    await col.update_one(
        {"_id": doc["_id"]},
        {"$set": {"fired_count": 0, "blamed_count": 0, "precision": None,
                  "updated_at": datetime.now(timezone.utc)}},
    )
    await set_status(
        tenant_id=tenant_id, app_slug=app_slug, clause_id=clause_id,
        status=tier, actor=actor,
        cause=(f"reinstated by {actor} (was overruled on {was[0]} of {was[1]} "
               f"firings, precision {was[2]}); counters reset for a fresh "
               f"measurement window"
               + (f": {str(reason).strip()[:200]}" if reason else "")),
    )
    log.info("[CLAUSE] %s reinstated by %s -> %s (counters reset from %d/%d)",
             clause_id, actor, tier, was[0], was[1])
    return {"clause_id": clause_id, "status": tier,
            "previous_precision": was[2]}
