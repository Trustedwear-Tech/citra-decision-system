# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""Consolidation — officer corrections → clauses.
Phase C of docs/clause-memory-graph-plan.md §9.

Replaces ``analysis_rubrics._resummarize``, which rewrote all 1000 words of the
rubric on EVERY correction, synchronously inside the officer's approve/reject
request. This job does three things instead, and only ONE of them writes text:

  REINFORCE  correction matches an existing clause  → no LLM, no text change
  CREATE     a cluster matches nothing              → one LLM call, once, ever
  MERGE      two clauses are near-duplicates        → keep the more general

**It consolidates and merges. It does not summarize.** A clause's text is
written once at birth and never rewritten, so the Nth correction cannot degrade
the (N-1)th lesson — the generation-loss loop is structurally gone (plan §18.1).

Runs in a leader-elected background loop, OFF the officer's request path.

Similarity is LEXICAL (content-word Jaccard), not embedding cosine. The hard
partition by ``reason_code`` plus the facet-overlap requirement already separate
lessons; adding a synchronous embedding call would introduce a network failure
mode inside the batch for a marginal gain in clustering. ``similarity_fn`` is
injectable, so swapping in embeddings later is a one-line change at the call
site rather than a rewrite here.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

import clause_store as cs
import corrections as cx

log = logging.getLogger(__name__)

#: Trigger thresholds — whichever fires first.
CONSOLIDATE_MIN_PENDING = int(os.getenv("CONSOLIDATE_MIN_PENDING", "5"))
CONSOLIDATE_MAX_AGE_HOURS = float(os.getenv("CONSOLIDATE_MAX_AGE_HOURS", "6"))

#: Clustering. Two corrections join a cluster when their reason_code matches
#: AND both thresholds are met.
CLUSTER_SIMILARITY = float(os.getenv("CLUSTER_SIMILARITY", "0.34"))
#: Facet gate: OVERLAP COEFFICIENT (|A∩B| / min|A|,|B|), not Jaccard over the
#: union. Jaccard's denominator grows with every facet family the app declares,
#: so two corrections about the SAME lesson that differ on incidental facets
#: (channel, status) score lower the RICHER the signature is — a 6-family app
#: sharing 3 core facets but differing on 3 incidental ones came out 3/9 = 0.33
#: and never clustered. Declaring more context must never make an app slower to
#: learn; the overlap coefficient only asks "of the facets you could share, how
#: many do you?" and is immune to each side's extras.
CLUSTER_FACET_OVERLAP = float(os.getenv("CLUSTER_FACET_OVERLAP", "0.5"))
#: Matching a cluster against an EXISTING clause — stricter than clustering,
#: because a false match silently attributes new evidence to the wrong rule.
MATCH_SIMILARITY = float(os.getenv("CLAUSE_MATCH_SIMILARITY", "0.5"))
#: Merging two clauses — stricter still; a merge is destructive-ish (reversible
#: only via merged_from), so it must be near-certain.
MERGE_SIMILARITY = float(os.getenv("CLAUSE_MERGE_SIMILARITY", "0.75"))

#: Scope inference (§9.3). Keep a facet only when its presence in the cluster
#: is INFORMATIVE relative to how often it appears at all.
LIFT_MIN = float(os.getenv("CLAUSE_SCOPE_LIFT_MIN", "1.3"))
#: Minimum sample before base rates mean anything.
MIN_BASE_RATE_SAMPLE = 20

#: A cluster this small is not yet a lesson... UNLESS the app has fewer
#: distinct officers than the promotion gate — a one-officer branch office must
#: still learn (doctrine: a single judgement is used, labeled, never discarded).
MIN_CLUSTER_SIZE = int(os.getenv("CLAUSE_MIN_CLUSTER_SIZE", "2"))

#: J4 substance gate: a cluster may author a judgement only if its combined
#: reason texts carry at least this many DISTINCT content tokens. "ok" / "see
#: file" / "as discussed" x3 cluster on mutual similarity and would otherwise
#: author a content-free judgement injected forever with three officers' names
#: on it.
MIN_CONTENT_TOKENS = int(os.getenv("CLAUSE_MIN_CONTENT_TOKENS", "6"))

#: J4 pattern-not-person gate: authored text must describe the PATTERN, never a
#: person. Identifier shapes rejected outright; honorific+Name is the cheap
#: reliable tell for named individuals in officer prose.
_PERSON_RE = re.compile(
    r"(?i:\b(?:mr|mrs|ms|shri|smt|sh|dr))\.?\s+(?-i:[A-Z][a-z]+)"
)
_IDENTIFIER_RE = re.compile(r"\d{6,}")

_STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "if", "then", "than", "this", "that",
    "these", "those", "is", "are", "was", "were", "be", "been", "being", "to",
    "of", "in", "on", "at", "for", "with", "without", "by", "from", "as", "it",
    "its", "we", "i", "you", "he", "she", "they", "them", "not", "no", "do",
    "does", "did", "should", "would", "could", "can", "will", "shall", "must",
    "have", "has", "had", "there", "here", "when", "while", "because", "so",
    "rejected", "recommendation", "corrected", "ai", "case", "please",
}
_WORD_RE = re.compile(r"[a-z0-9_]+")


class ConsolidationError(RuntimeError):
    """The job could not complete a bucket. Raised so the caller can log the
    bucket and continue — never swallowed into a silent no-op."""


# ---------------------------------------------------------------------------
# Similarity
# ---------------------------------------------------------------------------


def content_tokens(text: str) -> Set[str]:
    """Content words of a correction/clause, stopwords removed."""
    return {
        w for w in _WORD_RE.findall((text or "").lower())
        if w not in _STOPWORDS and len(w) > 2
    }


def jaccard(a: Iterable[str], b: Iterable[str]) -> float:
    sa, sb = set(a), set(b)
    if not sa and not sb:
        return 1.0
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def text_similarity(a: str, b: str) -> float:
    return jaccard(content_tokens(a), content_tokens(b))


def facet_compatible(
    a: Iterable[str], b: Iterable[str], *, min_overlap: float = CLUSTER_FACET_OVERLAP,
) -> bool:
    """May these two corrections describe the same KIND of case?

    Overlap coefficient, not Jaccard — see CLUSTER_FACET_OVERLAP for why.

    Either side EMPTY passes the gate. A facetless correction (all backfilled
    history, or an app with no case_signature) carries no facet evidence — and
    absence of evidence is not disagreement. Under the old Jaccard gate,
    jaccard([], [x]) = 0.0, so backfilled and live evidence could NEVER combine
    to reach the distinct-officer promotion gate: the entire migrated history
    was silently unable to corroborate anything an officer said after it."""
    sa, sb = set(a or ()), set(b or ())
    if not sa or not sb:
        return True
    return len(sa & sb) / min(len(sa), len(sb)) >= min_overlap


def cluster_tokens(cluster: Sequence[Dict[str, Any]]) -> Set[str]:
    """The union of content tokens across a cluster — the lexical fingerprint
    stored on the clause it produces, and the key later clusters match on."""
    out: Set[str] = set()
    for c in cluster:
        out |= content_tokens(_correction_text(c))
    return out


def _correction_text(c: Dict[str, Any]) -> str:
    """The learnable text of a correction: the officer's reason plus the fields
    they contested (an override's delta is itself a signal about WHAT was wrong)."""
    parts = [str(c.get("reason_text") or "")]
    parts.extend(str(f) for f in (c.get("contested_fields") or []))
    return " ".join(p for p in parts if p)


# ---------------------------------------------------------------------------
# Clustering
# ---------------------------------------------------------------------------


def cluster_corrections(
    corrections: Sequence[Dict[str, Any]],
    *,
    similarity_fn: Callable[[str, str], float] = text_similarity,
    min_similarity: float = CLUSTER_SIMILARITY,
    min_facet_overlap: float = CLUSTER_FACET_OVERLAP,
) -> List[List[Dict[str, Any]]]:
    """Group pending corrections into candidate lessons.

    HARD-partitioned by ``contested_fields`` first — the fields the officer
    actually changed. "You got the decision wrong" and "you got the amount
    wrong" are different lessons even when the wording rhymes, so they must
    never share a cluster.

    This used to partition on ``reason_code``, and that was the wrong axis. A
    clause is retrieved iff ``scope_facets ⊆ case_facets``, and scope is the
    cluster's facet INTERSECTION — so whatever partitions the clusters decides
    the SCOPE of every judgement authored. Letting a hand-picked "why" label sit
    above the facet evidence meant a why was choosing a where: two corrections
    on identical cases never even got compared when officers picked different
    labels, each fell below MIN_CLUSTER_SIZE, and both were silently skipped.

    ``contested_fields`` is derived from the override delta — never typed, never
    guessed — so the partition is now evidence the system observed rather than a
    label a human chose from a list. Facet compatibility and text similarity do
    the rest of the work inside each partition.
    """
    by_fields: Dict[Any, List[Dict[str, Any]]] = {}
    for c in corrections:
        # frozenset: order must not create partitions, and a reject with no
        # delta (no contested field) legitimately groups with its own kind.
        key = frozenset(str(f) for f in (c.get("contested_fields") or []))
        by_fields.setdefault(key, []).append(c)

    clusters: List[List[Dict[str, Any]]] = []
    for _fields, group in by_fields.items():
        buckets: List[List[Dict[str, Any]]] = []
        for c in group:
            ctext = _correction_text(c)
            cfacets = c.get("case_facets") or []
            placed = False
            for b in buckets:
                rep = b[0]
                if (similarity_fn(ctext, _correction_text(rep)) >= min_similarity
                        and facet_compatible(cfacets, rep.get("case_facets") or [],
                                             min_overlap=min_facet_overlap)):
                    b.append(c)
                    placed = True
                    break
            if not placed:
                buckets.append([c])
        clusters.extend(buckets)
    return clusters


# ---------------------------------------------------------------------------
# Intra-cluster coherence
# ---------------------------------------------------------------------------


def _override_moves(correction: Dict[str, Any]) -> Dict[str, List[tuple]]:
    """``{field: [(from, to), ...]}`` for one correction's override deltas."""
    out: Dict[str, List[tuple]] = {}
    for ov in (correction.get("overrides") or []):
        fromto = (ov or {}).get("override")
        if not isinstance(fromto, dict):
            continue
        for field, delta in fromto.items():
            if isinstance(delta, dict):
                out.setdefault(str(field), []).append(
                    (str(delta.get("from")), str(delta.get("to"))))
    return out


def conflicting_fields(cluster: Sequence[Dict[str, Any]]) -> Dict[str, set]:
    """Fields the cluster DISAGREES about: ``{field: {contested values}}``.

    A cluster is a candidate "lesson", and reason_code + text similarity is not
    enough to establish that its members are saying the same thing. Two officers
    can share a reason code, contest the same field, and mean OPPOSITE things:

        A: assigned_to  'Paula Shaw'  ->  'Adam Cole'
        B: assigned_to  'demo.je'     ->  'Paula Shaw'

    A moves work AWAY from Paula; B moves work TO her. Averaged by an LLM that
    is only shown the reason text, they produce a confident rule that half the
    evidence contradicts.

    The tell is deterministic and needs no model: a value that is a DESTINATION
    in one correction and a SOURCE in another. If the cluster cannot agree where
    the field should land, it is not one lesson.

    Detected on override deltas only — a plain reject names no destination, so
    there is nothing to disagree about structurally.
    """
    dests: Dict[str, set] = {}
    sources: Dict[str, set] = {}
    for c in cluster:
        for field, moves in _override_moves(c).items():
            for src, dst in moves:
                sources.setdefault(field, set()).add(src)
                dests.setdefault(field, set()).add(dst)
    return {
        f: (dests[f] & sources.get(f, set()))
        for f in dests
        if dests[f] & sources.get(f, set())
    }


def split_by_destination(
    cluster: Sequence[Dict[str, Any]], field: str,
) -> List[List[Dict[str, Any]]]:
    """Break an incoherent cluster into sub-clusters that AGREE on where the
    field should land.

    Splitting rather than discarding is what makes this self-healing: the
    evidence stays whole, and as soon as enough officers agree on ONE
    destination that sub-cluster clears the promotion gate on its own. A cluster
    that merely lacks agreement should produce no clause YET — not a confident
    wrong one, and not a silent deletion.
    """
    by_dest: Dict[str, List[Dict[str, Any]]] = {}
    for c in cluster:
        moves = _override_moves(c).get(field) or []
        # No move on the contested field ⇒ this correction cannot adjudicate it;
        # it rides with every sub-cluster rather than being dropped.
        key = moves[-1][1] if moves else None
        by_dest.setdefault(key, []).append(c)
    neutral = by_dest.pop(None, [])
    if not by_dest:
        return [list(cluster)]
    return [group + neutral for group in by_dest.values()]


# ---------------------------------------------------------------------------
# Scope inference (§9.3) — intersection, then lift
# ---------------------------------------------------------------------------


def base_rates(sample: Sequence[Dict[str, Any]]) -> Dict[str, float]:
    """P(facet) across a sample of facet-stamped cases.

    The sample here is the app's CORRECTIONS, which is rejection-biased. For
    scope inference that bias is acceptable and arguably correct: we want to
    drop facets that carry no discriminating power *among the cases we are
    learning from*. Injectable so a later phase can feed decided cases instead.
    """
    n = len(sample)
    if n == 0:
        return {}
    counts: Dict[str, int] = {}
    for row in sample:
        for f in set(row.get("case_facets") or []):
            counts[f] = counts.get(f, 0) + 1
    return {f: c / n for f, c in counts.items()}


def infer_scope(
    cluster: Sequence[Dict[str, Any]],
    *,
    rates: Optional[Dict[str, float]] = None,
    lift_min: float = LIFT_MIN,
) -> List[str]:
    """Scope = the cluster's facet INTERSECTION, filtered by a lift test.

    Intersection (not union) is what the cases genuinely share. The lift test
    then drops facets that are near-universal anyway: P(f|cluster) is 1.0 by
    construction, so ``lift = 1 / P(f|app)``. ``country:us`` at a 95% base rate
    scores 1.05 and is dropped; ``loss_type:theft`` at 12% scores 8.3 and stays.
    Without this every clause is pointlessly scoped to the deployment's
    constants, which makes the whole scope mechanism inert.

    With too small a sample the base rates are noise, so the lift test is
    SKIPPED and the raw intersection is used — under-scoping (too specific) is
    recoverable by a later merge; over-scoping (too general) mis-fires.
    """
    if not cluster:
        return []
    common: Optional[Set[str]] = None
    for c in cluster:
        f = set(c.get("case_facets") or [])
        common = f if common is None else (common & f)
    common = common or set()
    # Drift tokens can never be scoped — the alarm must stay diagnostic.
    common = {f for f in common if not f.endswith(f":{cs.UNKNOWN}")}

    if not rates or len(rates) == 0:
        return sorted(common)
    return sorted(
        f for f in common
        if (1.0 / rates.get(f, 1e-9)) >= lift_min
    )


def scopes_can_co_fire(a: Sequence[str], b: Sequence[str]) -> bool:
    """True when ONE case could satisfy both scopes.

    Two tokens of the same family with different values are mutually exclusive
    (a family emits at most one token per case), so such scopes can never both
    fire and cannot contradict each other."""
    fam_a: Dict[str, str] = {}
    for t in a:
        fam, _, val = str(t).partition(":")
        fam_a[fam] = val
    for t in b:
        fam, _, val = str(t).partition(":")
        if fam in fam_a and fam_a[fam] != val:
            return False
    return True


# ---------------------------------------------------------------------------
# Clause authoring (the ONE LLM call, once per new lesson)
# ---------------------------------------------------------------------------

def _person_violation(text: str) -> str:
    """The offending fragment when authored text names a person/identifier."""
    m = _PERSON_RE.search(text or "") or _IDENTIFIER_RE.search(text or "")
    return m.group(0) if m else ""


_AUTHOR_SYSTEM = (
    "You write ONE decision rule from a set of real reviewer corrections.\n"
    "STRICT RULES:\n"
    "- Output ONE imperative sentence, at most {max_words} words.\n"
    "- Every claim must be supported by the quoted corrections. Introduce NO "
    "facts, thresholds, or conditions that are not in them.\n"
    "- Do NOT restate the case type / scope (that is stored separately) — "
    "state only what to DO or NOT do.\n"
    "- Describe the recurring PATTERN, never the person: no personal names, "
    "honorifics, phone numbers, or ID numbers (person-specific concerns are "
    "handled by entity screening, not judgements).\n"
    "- No preamble, no quotes, no markdown. The sentence only."
)


async def author_clause_text(
    cluster: Sequence[Dict[str, Any]],
    *,
    reason_code: Optional[str],
    max_words: int = cs.CLAUSE_MAX_WORDS,
    extra_constraint: str = "",
) -> str:
    """Phrase the lesson the officers already stated. RAISES on failure.

    Raising (rather than returning a placeholder) leaves the cluster's
    corrections un-consumed, so the next pass retries them — the same
    watermark-does-not-advance discipline ``_resummarize`` uses. A silent
    fallback string would become a permanent, unprovenanced "rule"."""
    quoted = "\n".join(
        f"- {(c.get('reason_text') or '').strip()}"
        for c in cluster if (c.get("reason_text") or "").strip()
    )
    if not quoted:
        raise ConsolidationError("cluster has no reason text to author from")

    from config import get_settings
    from llm_client import get_llm_client_for

    settings = get_settings()
    tier = settings.llm_tier_config("medium")
    client = get_llm_client_for(tier["base_url"], tier["api_key"])
    resp = await client.chat.completions.create(
        model=tier["model"],
        messages=[
            {"role": "system", "content": (
                _AUTHOR_SYSTEM.format(max_words=max_words)
                + ("\n" + extra_constraint if extra_constraint else ""))},
            {"role": "user",
             "content": (f"REASON CATEGORY: {reason_code or 'unspecified'}\n\n"
                         f"REVIEWER CORRECTIONS:\n{quoted}")},
        ],
        temperature=0.0,
        # The medium tier is a hybrid-reasoning model: excluded reasoning tokens
        # still count against max_tokens, so a tight cap starves the model
        # mid-reason and yields empty content. 4000 is the floor here even
        # though the output is one sentence.
        max_tokens=4000,
        timeout=60,
        extra_body=(tier.get("extra_body") or None),
    )
    text = (resp.choices[0].message.content or "").strip()
    if not text:
        raise ConsolidationError("medium model returned empty clause text")

    try:
        from token_metering import record_usage

        _u = getattr(resp, "usage", None)
        await record_usage(
            tenant_id=(cluster[0].get("tenant_id") if cluster else None),
            model=tier["model"], surface="clause_author",
            tokens_in=getattr(_u, "prompt_tokens", 0),
            tokens_out=getattr(_u, "completion_tokens", 0))
    except Exception:  # noqa: BLE001 — metering never breaks consolidation
        log.exception("[TOKENS] clause_author metering failed")

    return text.strip().strip('"').strip()


# ---------------------------------------------------------------------------
# Matching / contradiction
# ---------------------------------------------------------------------------


def find_matching_clause(
    cluster: Sequence[Dict[str, Any]],
    existing: Sequence[Dict[str, Any]],
    *,
    similarity_fn: Callable[[str, str], float] = text_similarity,
    min_similarity: float = MATCH_SIMILARITY,
) -> Optional[Dict[str, Any]]:
    """The existing clause this cluster reinforces, or None.

    Requires the SAME reason_code and a scope that could co-fire with the
    cluster's cases — otherwise the evidence would be attributed to a rule it
    has nothing to do with.

    Similarity is measured against the clause's ``match_tokens`` — the lexical
    fingerprint of the OFFICER language that taught it — not against
    ``clause.text``, which is an LLM paraphrase that may share almost no
    vocabulary with the complaints behind it. Matching a new officer complaint
    against a rendered rule systematically fails to reinforce and instead
    fragments one lesson into many near-duplicate clauses. Clauses with no
    fingerprint (builder-authored, or pre-fingerprint rows) fall back to text.
    """
    if not cluster:
        return None
    tokens = cluster_tokens(cluster)
    cfacets: Set[str] = set()
    for c in cluster:
        cfacets |= set(c.get("case_facets") or [])

    best, best_score = None, 0.0
    for cl in existing:
        # Only a clause that is IN SERVICE may absorb new evidence. Skipping
        # just retired/superseded meant a parked clause — quarantined by an
        # admin, withdrawn on its results, orphaned — kept winning the match and
        # swallowing the corrections that should have formed its REPLACEMENT.
        # Officers went on correcting the same cases, consolidation went on
        # folding those corrections into a judgement nobody was using, and the
        # right rule never got authored. `dissented` stays eligible: it is still
        # live (rendered as a disagreement notice) and more evidence is exactly
        # what settles it.
        if cl.get("status") not in cs.LIVE_STATUSES:
            continue
        # Deliberately NOT gated on an equal reason_code any more. Clauses
        # authored before the taxonomy was removed carry one and new evidence
        # does not, so requiring equality would stop every existing clause from
        # ever being reinforced again — fragmenting each lesson into a coded old
        # clause and an uncoded new twin. Scope co-firing plus the lexical
        # fingerprint are what establish "this is the same rule".
        if not scopes_can_co_fire(cl.get("scope_facets") or [], sorted(cfacets)):
            continue
        fingerprint = set(cl.get("match_tokens") or [])
        s = (jaccard(tokens, fingerprint) if fingerprint
             else similarity_fn(" ".join(sorted(tokens)), str(cl.get("text") or "")))
        if s >= min_similarity and s > best_score:
            best, best_score = cl, s
    return best


def detect_contradictions(
    clauses: Sequence[Dict[str, Any]],
    *,
    similarity_fn: Callable[[str, str], float] = text_similarity,
) -> List[Tuple[str, str]]:
    """Pairs of ACTIVE clauses that could both fire on one case and pull the same
    field in OPPOSING directions.

    Both flagged clauses move to ``dissented`` — suppressed, not asserted — so
    this must be precise. Two rules about the same field on overlapping cases
    are usually COMPLEMENTARY, and treating that as a clash silences real
    knowledge.

    Observed in prod, and the reason this was rewritten: two lending clauses
    were suppressed for "contradicting" each other while every correction
    behind them agreed. One moved `decision: approve -> refer_income_proof`,
    the other `approve -> verify_employment`. Both said "do not just approve,
    check further" — on a case that is both DSA-sourced and has an income
    mismatch an officer would do both. Neither had a single dissenting officer.
    They were flagged only because their reason CODES differed, which is a
    label, not a disagreement.

    The signal now is the move itself: A is opposed to B when A moves the field
    TO a value B moves it away FROM (or the reverse). That is a genuine cycle —
    one rule undoing the other — and needs no understanding of what the values
    mean, which matters for a product that has to work for lending, claims and
    outages alike.

    A clause with no recorded moves (authored before override_moves existed) is
    never flagged. Suppressing real knowledge on absent evidence is the worse
    error of the two.
    """
    out: List[Tuple[str, str]] = []
    live = [c for c in clauses if c.get("status") == "active"]
    for i, a in enumerate(live):
        ma = a.get("override_moves") or {}
        if not ma:
            continue
        for b in live[i + 1:]:
            mb = b.get("override_moves") or {}
            if not mb:
                continue
            shared = set(ma) & set(mb)
            if not shared:
                continue
            if not scopes_can_co_fire(a.get("scope_facets") or [],
                                      b.get("scope_facets") or []):
                continue
            # Near-identical text is a duplicate, not a contradiction — the
            # merge pass owns it.
            if similarity_fn(str(a.get("text") or ""), str(b.get("text") or "")) >= MERGE_SIMILARITY:
                continue
            opposed = False
            for field in shared:
                a_to = set(ma[field].get("to") or [])
                a_from = set(ma[field].get("from") or [])
                b_to = set(mb[field].get("to") or [])
                b_from = set(mb[field].get("from") or [])
                if (a_to & b_from) or (b_to & a_from):
                    opposed = True
                    break
            if opposed:
                out.append((str(a.get("clause_id")), str(b.get("clause_id"))))
    return out


def find_merges(
    clauses: Sequence[Dict[str, Any]],
    *,
    similarity_fn: Callable[[str, str], float] = text_similarity,
    min_similarity: float = MERGE_SIMILARITY,
) -> List[Tuple[str, str]]:
    """``(survivor_id, absorbed_id)`` pairs — near-identical text and one scope
    a SUBSET of the other. The more GENERAL clause survives (a subset scope
    fires more often), and ``merged_from`` keeps it reversible.

    No longer requires an equal reason_code: with the taxonomy removed a legacy
    coded clause and its uncoded successor would never merge, leaving the app
    holding two copies of one lesson forever."""
    out: List[Tuple[str, str]] = []
    live = [c for c in clauses if c.get("status") in ("active", "candidate")]
    for i, a in enumerate(live):
        for b in live[i + 1:]:
            if similarity_fn(str(a.get("text") or ""), str(b.get("text") or "")) < min_similarity:
                continue
            sa, sb = set(a.get("scope_facets") or []), set(b.get("scope_facets") or [])
            if sa <= sb:
                out.append((str(a.get("clause_id")), str(b.get("clause_id"))))
            elif sb <= sa:
                out.append((str(b.get("clause_id")), str(a.get("clause_id"))))
    return out


# ---------------------------------------------------------------------------
# The pass
# ---------------------------------------------------------------------------


def should_consolidate(bucket: Dict[str, Any], *, now: Optional[datetime] = None) -> bool:
    """Count threshold OR age threshold — whichever fires first."""
    if int(bucket.get("pending") or 0) >= CONSOLIDATE_MIN_PENDING:
        return True
    oldest = bucket.get("oldest")
    if isinstance(oldest, datetime):
        ref = oldest if oldest.tzinfo else oldest.replace(tzinfo=timezone.utc)
        now = now or datetime.now(timezone.utc)
        return (now - ref) >= timedelta(hours=CONSOLIDATE_MAX_AGE_HOURS)
    return False


async def consolidate_bucket(
    *,
    tenant_id: str,
    app_slug: str,
    modality: str,
    task_type: str,
    promotion_min_officers: int = 3,
    signature_version: Optional[int] = None,
    author_fn: Optional[Callable[..., Any]] = None,
    similarity_fn: Callable[[str, str], float] = text_similarity,
    alias_map: Optional[Dict[str, str]] = None,
    sop_checker: Optional[Callable[..., Any]] = None,
) -> Dict[str, Any]:
    """One consolidation pass over a bucket. Returns a summary of what changed.

    Ordering matters: corrections are marked consumed ONLY after the clause
    write succeeds, so a crash mid-pass leaves them pending and the next pass
    retries — never a lost lesson, never a double-count."""
    author = author_fn or author_clause_text
    stats = {"pending": 0, "clusters": 0, "reinforced": 0, "created": 0,
             "skipped": 0, "contradictions": 0, "merges": 0,
             "performance_updated": 0, "conflicts_split": 0}

    pending = await cx.pending_corrections(
        tenant_id=tenant_id, app_slug=app_slug,
        modality=modality, task_type=task_type)
    stats["pending"] = len(pending)
    if not pending:
        return stats

    # How many distinct officers has this app EVER seen? Governs whether the
    # 2-correction cluster floor applies (small teams: it does not).
    try:
        distinct_officers = len(await cx._col().distinct(
            "officer", {"tenant_id": tenant_id, "app_slug": app_slug}))
    except Exception:  # noqa: BLE001 — conservative fallback
        distinct_officers = promotion_min_officers

    # Every clause in the bucket, not just those matching some facet set —
    # matching is by reason_code + text, and a cluster may reinforce a clause
    # whose scope its cases do not currently satisfy.
    existing = await _all_clauses(tenant_id, app_slug, modality, task_type)

    # Base rates over the app's whole facet-stamped history, not just the
    # pending slice — a 5-row sample would call every facet informative.
    all_seen = await _facet_sample(tenant_id, app_slug, modality, task_type)
    rates = base_rates(all_seen) if len(all_seen) >= MIN_BASE_RATE_SAMPLE else {}

    # J7: normalize RENAMED reason codes through the app's alias map before
    # anything partitions by code — a bare rename otherwise splits one lesson
    # across two code generations that can never reach the gate together.
    if alias_map:
        for c in pending:
            code = c.get("reason_code")
            if code in alias_map:
                c["reason_code"] = alias_map[code]

    clusters = cluster_corrections(pending, similarity_fn=similarity_fn)

    # Coherence pass: reason_code + text similarity groups corrections that are
    # ABOUT the same thing, not necessarily that AGREE about it. Split any
    # cluster whose officers move the same field in opposite directions, before
    # a model is ever asked to phrase it as one rule.
    coherent: List[List[Dict[str, Any]]] = []
    for cluster in clusters:
        conflicts = conflicting_fields(cluster)
        if not conflicts:
            coherent.append(cluster)
            continue
        field = max(conflicts, key=lambda f: len(conflicts[f]))
        parts = split_by_destination(cluster, field)
        stats["conflicts_split"] = stats.get("conflicts_split", 0) + 1
        log.warning(
            "[CONSOLIDATE] %s/%s: %d correction(s) disagree on where %r should "
            "land (contested: %s) — split into %d coherent group(s) rather than "
            "averaged into one rule",
            app_slug, task_type, len(cluster), field,
            sorted(conflicts[field])[:5], len(parts),
        )
        coherent.extend(parts)

    clusters = coherent
    stats["clusters"] = len(clusters)

    for cluster in clusters:
        ids = [c["correction_id"] for c in cluster if c.get("correction_id")]
        officers = [c.get("officer") for c in cluster if c.get("officer")]
        match = find_matching_clause(cluster, existing, similarity_fn=similarity_fn)

        if match is not None:
            await cs.reinforce(
                tenant_id=tenant_id, app_slug=app_slug,
                clause_id=match["clause_id"], correction_ids=ids,
                officers=officers, match_tokens=sorted(cluster_tokens(cluster)),
                promotion_min_officers=promotion_min_officers)
            await cx.mark_consumed(correction_ids=ids, clause_id=match["clause_id"])
            stats["reinforced"] += 1
            await _sop_check_one(
                tenant_id=tenant_id, app_slug=app_slug, clause=match,
                sop_checker=sop_checker, stats=stats)
            continue

        # A new judgement needs a named lesson and enough evidence to be one.
        # The size floor bends for small teams: when the app has seen fewer
        # distinct officers than the promotion gate, ONE officer's corroborated
        # wording may author an INDIVIDUAL judgement (labeled as such at
        # injection) — a one-officer branch must still learn.
        min_size = MIN_CLUSTER_SIZE if distinct_officers >= promotion_min_officers else 1
        # Was: `reason_code is None or len(...) < min_size`. The code gate is
        # gone with the taxonomy — an uncoded cluster is now the NORMAL case,
        # and refusing to author from one would switch learning off entirely.
        # What a cluster must earn its judgement with is SUBSTANCE, and that
        # gate is the MIN_CONTENT_TOKENS check immediately below.
        if len(cluster) < min_size:
            stats["skipped"] += 1
            continue

        # J4 substance gate: refuse to author from vacuous text. NOT consumed —
        # if an officer later writes a real reason for the same lesson, these
        # ride along as corroboration (they still are that). Marked so the
        # Memory screen can show a coaching counter.
        if len(cluster_tokens(cluster)) < MIN_CONTENT_TOKENS:
            stats["insufficient_reason"] = stats.get("insufficient_reason", 0) + 1
            ids_brief = [c["correction_id"] for c in cluster if c.get("correction_id")]
            try:
                await cx._col().update_many(
                    {"correction_id": {"$in": ids_brief}},
                    {"$set": {"insufficient_reason": True}})
            except Exception:  # noqa: BLE001 — marker is best-effort
                log.exception("[CONSOLIDATE] could not mark brief reasons")
            log.warning(
                "[CONSOLIDATE] %s/%s: %d correction(s) too brief to learn from "
                "(<%d content tokens) — ask officers for one concrete sentence",
                app_slug, cluster[0].get("reason_code"), len(cluster),
                MIN_CONTENT_TOKENS)
            continue

        try:
            text = await author(cluster, reason_code=cluster[0].get("reason_code"))
            # J4 pattern-not-person gate: a judgement describes the PATTERN,
            # never the person. "Mr. Sharma is a repeat fraudster" x3 would
            # otherwise become a judgement naming an individual, injected into
            # every matching case — person-specific concerns belong to entity
            # screening (entity_links), not judgements. One retry with the
            # violation quoted; still dirty => leave the evidence pending.
            violation = _person_violation(text)
            if violation:
                log.warning(
                    "[CONSOLIDATE] %s: authored text names a person/identifier "
                    "(%r) — retrying with the pattern-not-person constraint",
                    app_slug, violation)
                text = await author(
                    cluster, reason_code=cluster[0].get("reason_code"),
                    extra_constraint=(
                        "Your previous attempt contained "
                        f"{violation!r}. NEVER include personal names, "
                        "honorifics, phone numbers, or ID numbers — describe "
                        "the recurring PATTERN only."))
                if _person_violation(text):
                    raise ConsolidationError(
                        "authored text still names a person/identifier")
        except Exception:  # noqa: BLE001 — leave pending; next pass retries
            log.exception(
                "[CONSOLIDATE] clause authoring failed for %s/%s — %d correction(s) "
                "stay pending", app_slug, cluster[0].get("reason_code"), len(cluster))
            stats["skipped"] += 1
            continue

        contested: Set[str] = set()
        for c in cluster:
            contested |= set(c.get("contested_fields") or [])
        # WHICH WAY the cluster moved each field. Contradiction detection needs
        # direction, not a category label — see detect_contradictions.
        moves: Dict[str, Dict[str, Set[str]]] = {}
        for c in cluster:
            for field, pairs in _override_moves(c).items():
                slot = moves.setdefault(field, {"from": set(), "to": set()})
                for frm, to in pairs:
                    slot["from"].add(frm)
                    slot["to"].add(to)
        moves_out = {f: {"from": sorted(v["from"]), "to": sorted(v["to"])}
                     for f, v in moves.items()}

        try:
            doc = await cs.create_clause(
                tenant_id=tenant_id, app_slug=app_slug,
                modality=modality, task_type=task_type,
                text=text,
                scope_facets=infer_scope(cluster, rates=rates),
                reason_code=cluster[0].get("reason_code"),
                provenance=ids, support_officers=officers,
                contested_fields=sorted(contested),
                override_moves=moves_out,
                match_tokens=sorted(cluster_tokens(cluster)),
                signature_version=signature_version,
                promotion_min_officers=promotion_min_officers)
        except cs.ClauseError:
            log.exception("[CONSOLIDATE] clause rejected for %s — corrections stay "
                          "pending", app_slug)
            stats["skipped"] += 1
            continue

        await cx.mark_consumed(correction_ids=ids, clause_id=doc["clause_id"])
        existing.append(doc)
        stats["created"] += 1
        await _sop_check_one(
            tenant_id=tenant_id, app_slug=app_slug, clause=doc,
            sop_checker=sop_checker, stats=stats)

    # ── Close the feedback loop: performance + dissent ──────────────────────
    # Without this the clause store learns WHAT the officers said but never
    # whether its own rules were any good — precision stays None forever and the
    # ranker runs blind on the prior. Batched here, so no clause write ever sits
    # in an officer's request.
    try:
        perf = await aggregate_clause_performance(
            tenant_id=tenant_id, app_slug=app_slug,
            modality=modality, task_type=task_type)
        dissenters = {cid: c.pop("dissenters", set()) for cid, c in perf.items()}
        stats["performance_updated"] = await cs.apply_performance(
            tenant_id=tenant_id, app_slug=app_slug, counters=perf)
        for cid, offs in dissenters.items():
            for officer in offs:
                await cs.record_dissent(tenant_id=tenant_id, app_slug=app_slug,
                                        clause_id=cid, officer=officer)
    except Exception:  # noqa: BLE001 — derived metrics; loud, never fails the pass
        log.exception("[CONSOLIDATE] performance/dissent aggregation failed for %s",
                      app_slug)

    # J3 recheck: judgements the MODEL reported setting aside for a rule
    # (cited relation "overrode_by_rule" on DecisionRecords). A judgement
    # collecting those has drifted against the live SOP since it was checked —
    # re-examine it without waiting for reinforcement.
    if sop_checker is not None:
        try:
            overridden = await _overridden_by_rule_ids(tenant_id, app_slug)
            for cl in await _all_clauses(tenant_id, app_slug, modality, task_type):
                if (cl.get("clause_id") in overridden
                        and cl.get("status") in ("active", "candidate")
                        and not cl.get("sop_ack")):
                    await _sop_check_one(
                        tenant_id=tenant_id, app_slug=app_slug, clause=cl,
                        sop_checker=sop_checker, stats=stats, force=True)
        except Exception:  # noqa: BLE001 — recheck is enrichment
            log.exception("[CONSOLIDATE] overrode_by_rule recheck failed for %s",
                          app_slug)

    # Post-pass graph maintenance over the bucket's clauses.
    refreshed = await _all_clauses(tenant_id, app_slug, modality, task_type)
    for a_id, b_id in detect_contradictions(refreshed, similarity_fn=similarity_fn):
        await cs.add_edge(tenant_id=tenant_id, app_slug=app_slug,
                          clause_id=a_id, edge="contradicts", target=b_id)
        await cs.add_edge(tenant_id=tenant_id, app_slug=app_slug,
                          clause_id=b_id, edge="contradicts", target=a_id)
        for cid in (a_id, b_id):
            await cs.set_status(tenant_id=tenant_id, app_slug=app_slug,
                                clause_id=cid, status="dissented",
                                actor="consolidation",
                                cause=f"contradicts:{b_id if cid == a_id else a_id}")
        stats["contradictions"] += 1

    for survivor, absorbed in find_merges(refreshed, similarity_fn=similarity_fn):
        await cs.add_edge(tenant_id=tenant_id, app_slug=app_slug,
                          clause_id=survivor, edge="merged_from", target=absorbed)
        await cs.set_status(tenant_id=tenant_id, app_slug=app_slug,
                            clause_id=absorbed, status="superseded",
                            actor="consolidation", cause=f"merged_into:{survivor}")
        stats["merges"] += 1

    log.info("[CONSOLIDATE] %s/%s/%s: %s", app_slug, modality, task_type, stats)
    return stats


async def _all_clauses(tenant_id, app_slug, modality, task_type) -> List[Dict[str, Any]]:
    col = cs._col()
    await cs._ensure_indexes(col)
    return await col.find(
        {"tenant_id": tenant_id, "app_slug": app_slug,
         "modality": modality, "task_type": task_type},
        {"_id": 0},
    ).to_list(5000)


async def _facet_sample(tenant_id, app_slug, modality, task_type,
                        limit: int = 500) -> List[Dict[str, Any]]:
    """Facet-stamped corrections for base-rate estimation (consumed included)."""
    col = cx._col()
    return await col.find(
        {"tenant_id": tenant_id, "app_slug": app_slug,
         "modality": modality, "task_type": task_type},
        {"_id": 0, "case_facets": 1},
    ).to_list(limit)


# ---------------------------------------------------------------------------
# Operator control (plan §19 / admin console)
# ---------------------------------------------------------------------------

_CONTROL_COLLECTION = "smartapp_learning_control"
_CONTROL_KEY = {"control": "consolidation"}


def _control_col():
    """Deliberately NOT ``smartapp_control`` (the kill switches).

    Those halt BUSINESS operations — runs, autonomous writes, trigger fires —
    and everything in that collection surfaces in the operations halt banner.
    Pausing a learning batch is a different concern with a different blast
    radius: nothing an officer does is affected, corrections simply accumulate
    unfolded. Filing it under the kill switches would make "learning paused"
    read as "the system is halted"."""
    import main

    if getattr(main, "_db", None) is None:
        raise RuntimeError("Database not initialised")
    return main._db[_CONTROL_COLLECTION]


async def get_control_state() -> Dict[str, Any]:
    """Pause flag + last-pass telemetry. FAIL-OPEN: an unreadable control store
    means consolidation RUNS (a flag-store blip must not silently stop
    learning), and the failure is logged loudly."""
    try:
        doc = await _control_col().find_one(_CONTROL_KEY, {"_id": 0})
        return doc or {**_CONTROL_KEY, "paused": False}
    except Exception:  # noqa: BLE001 — fail-open, loud
        log.exception("[CONSOLIDATE] control read failed — proceeding UNPAUSED")
        return {**_CONTROL_KEY, "paused": False, "control_read_failed": True}


async def set_paused(*, paused: bool, actor: str, reason: str = "") -> Dict[str, Any]:
    """Pause/resume the consolidation batch. RAISES on failure — an operator's
    explicit pause must never silently no-op."""
    doc = {**_CONTROL_KEY, "paused": bool(paused), "reason": reason or "",
           "actor": actor, "updated_at": datetime.now(timezone.utc)}
    await _control_col().update_one(_CONTROL_KEY, {"$set": doc}, upsert=True)
    log.warning("[CONSOLIDATE] %s by=%s reason=%s",
                "PAUSED" if paused else "RESUMED", actor, reason)
    return doc


async def _record_pass(totals: Dict[str, Any]) -> None:
    try:
        await _control_col().update_one(
            _CONTROL_KEY,
            {"$set": {"last_pass_at": datetime.now(timezone.utc),
                      "last_pass": totals}},
            upsert=True)
    except Exception:  # noqa: BLE001 — telemetry; never fails the pass
        log.exception("[CONSOLIDATE] could not record pass telemetry")


async def consolidation_status(*, max_buckets: int = 200) -> Dict[str, Any]:
    """Everything the admin console needs: pause state, last pass, and the
    pending queue with which buckets are over threshold."""
    from env_context import current_env, set_current_env

    state = await get_control_state()
    rows: List[Dict[str, Any]] = []
    failed = False
    previous = current_env()
    try:
        # Same both-environments sweep the job does — an admin looking at the
        # queue must see the test-env backlog too, or a test app that stopped
        # learning looks fine from here.
        for env in _environments_to_sweep():
            set_current_env(env)
            try:
                for b in await cx.pending_buckets(limit=max_buckets):
                    rows.append({**b, "env": env, "due": should_consolidate(b)})
            except Exception:  # noqa: BLE001 — surface it, never fake an empty queue
                log.exception("[CONSOLIDATE] pending-bucket read failed (%s)", env)
                failed = True
    finally:
        set_current_env(previous)

    if failed and not rows:
        return {**state, "queue_read_failed": True, "pending_total": None,
                "buckets": []}
    return {
        "paused": bool(state.get("paused")),
        "reason": state.get("reason") or "",
        "actor": state.get("actor"),
        "updated_at": state.get("updated_at"),
        "last_pass_at": state.get("last_pass_at"),
        "last_pass": state.get("last_pass"),
        "interval_seconds": int(os.getenv("CONSOLIDATION_INTERVAL_SECONDS", "900")),
        "min_pending": CONSOLIDATE_MIN_PENDING,
        "max_age_hours": CONSOLIDATE_MAX_AGE_HOURS,
        "pending_total": sum(int(b.get("pending") or 0) for b in rows),
        "due_buckets": sum(1 for b in rows if b["due"]),
        "environments": _environments_to_sweep(),
        # Partial read: some environments answered, some did not. Reporting the
        # totals without this flag would understate the backlog as if it were
        # complete.
        "queue_partial": failed,
        "buckets": rows,
    }


def _environments_to_sweep() -> List[str]:
    """Which environments this pass must cover.

    The collection accessors are env-ROUTED off a contextvar whose default is
    'prod' (env_context). A background loop has no request, so a naive sweep
    would silently process ONLY prod — test-env apps would pile up corrections
    that are never folded, and their clause memory would stay permanently empty
    while looking merely "not learned yet". Test is included only when the
    deployment actually provisioned a test plane.
    """
    envs = ["prod"]
    try:
        from config import get_settings

        if get_settings().test_environment_available:
            envs.append("test")
    except Exception:  # noqa: BLE001 — settings unreadable ⇒ prod only (safe)
        log.warning("[CONSOLIDATE] could not read settings — sweeping prod only")
    return envs


async def run_consolidation_pass(
    *, max_buckets: int = 50, force: bool = False, ignore_pause: bool = False,
    environment: Optional[str] = None,
) -> Dict[str, Any]:
    """Sweep one environment, or BOTH when ``environment`` is None."""
    if environment is None:
        from env_context import current_env, set_current_env

        merged: Dict[str, Any] = {"buckets": 0, "reinforced": 0, "created": 0,
                                  "skipped": 0, "contradictions": 0, "merges": 0,
                                  "performance_updated": 0, "errors": 0,
                                  "by_env": {}}
        previous = current_env()
        try:
            for env in _environments_to_sweep():
                set_current_env(env)
                one = await _run_pass_one_env(
                    max_buckets=max_buckets, force=force, ignore_pause=ignore_pause)
                merged["by_env"][env] = one
                if one.get("paused"):
                    merged["paused"] = True
                # Roll up whatever counters the pass produced — a fixed key list
                # here silently drops any new stat (and the same pattern one
                # level down turned a successful bucket into a reported error).
                for k, v in one.items():
                    if k in ("paused", "by_env"):
                        continue
                    merged[k] = merged.get(k, 0) + int(v or 0)
        finally:
            set_current_env(previous)
        return merged

    from env_context import current_env, set_current_env

    previous = current_env()
    try:
        set_current_env(environment)
        return await _run_pass_one_env(
            max_buckets=max_buckets, force=force, ignore_pause=ignore_pause)
    finally:
        set_current_env(previous)


async def _run_pass_one_env(
    *, max_buckets: int = 50, force: bool = False, ignore_pause: bool = False,
) -> Dict[str, Any]:
    """Sweep every bucket that crossed a trigger threshold.

    A failure in ONE bucket is logged and the sweep continues — one broken app
    must not stop every other app from learning."""
    totals = {"buckets": 0, "reinforced": 0, "created": 0, "skipped": 0,
              "contradictions": 0, "merges": 0, "errors": 0}

    if not ignore_pause:
        state = await get_control_state()
        if state.get("paused"):
            log.info("[CONSOLIDATE] pass skipped — paused by %s (%s)",
                     state.get("actor"), state.get("reason") or "no reason given")
            return {**totals, "paused": True}

    try:
        buckets = await cx.pending_buckets(limit=max_buckets)
    except Exception:  # noqa: BLE001 — loud; the sweep cannot run blind
        log.exception("[CONSOLIDATE] could not list pending buckets — pass aborted")
        return {**totals, "errors": 1}

    for b in buckets:
        # `force` (operator "Run now") bypasses the count/age thresholds — the
        # thresholds exist to batch cost, and an operator asking for a run has
        # already decided to pay it.
        if not force and not should_consolidate(b):
            continue
        try:
            promo, sigver, aliases = await _bucket_settings(
                b["tenant_id"], b["app_slug"])
            s = await consolidate_bucket(
                tenant_id=b["tenant_id"], app_slug=b["app_slug"],
                modality=b["modality"], task_type=b["task_type"],
                promotion_min_officers=promo, signature_version=sigver,
                alias_map=aliases)
            totals["buckets"] += 1
            # setdefault, not totals[k]: a new counter added to
            # consolidate_bucket's stats must not KeyError here and report a
            # SUCCESSFUL bucket as a failure — which is how this was found.
            for k, v in s.items():
                if k in ("pending", "clusters", "paused"):
                    continue
                totals[k] = totals.get(k, 0) + int(v or 0)
        except Exception:  # noqa: BLE001 — one bad bucket must not stop the sweep
            totals["errors"] += 1
            log.exception("[CONSOLIDATE] bucket failed: %s/%s",
                          b.get("app_slug"), b.get("task_type"))
    if totals["buckets"] or totals["errors"]:
        log.info("[CONSOLIDATE] pass complete: %s", totals)
        await _record_pass(totals)
    return totals


#: Once per clause per pass at most; sop_ack (supervisor said "the SOP is the
#: stale one") suppresses re-checks — the disagreement is recorded, not re-run.
async def _sop_check_one(
    *, tenant_id: str, app_slug: str, clause: Dict[str, Any],
    sop_checker, stats: Dict[str, Any], force: bool = False,
) -> None:
    """Check ONE judgement against the SOP; suspend it on contradiction (J3).

    SOP is king (sop-rules-officer-judgement-plan §0): a judgement that
    CONTRADICTS the standard must not be injected as a judgement. It is moved
    to `sop_conflict` — surfaced as a one-line notice and a supervisor flag
    with a two-tap resolution: retire it (the SOP is right) or acknowledge it
    (the officers are right and the SOP is stale; it returns to service with
    the disagreement recorded). Judgements that merely FILL GAPS are the whole
    point and pass untouched.

    Best-effort by design: the checker can miss conflicts (retrieval recall,
    model judgement) — the runtime supremacy instruction is the primary
    defense; this is the early-warning system. Never blocks consolidation."""
    if sop_checker is None or not clause:
        return
    if clause.get("sop_ack") and not force:
        return
    if clause.get("status") not in ("active", "candidate"):
        return
    try:
        verdict = await sop_checker(
            tenant_id=tenant_id, app_slug=app_slug,
            text=str(clause.get("text") or ""))
    except Exception:  # noqa: BLE001 — checker outage must not stop learning
        log.exception("[CONSOLIDATE] SOP check failed for %s/%s",
                      app_slug, clause.get("clause_id"))
        return
    if not verdict or not verdict.get("contradicts"):
        return
    stats["sop_conflicts"] = stats.get("sop_conflicts", 0) + 1
    try:
        col = cs._col()
        await col.update_one(
            {"tenant_id": tenant_id, "app_slug": app_slug,
             "clause_id": clause["clause_id"]},
            {"$set": {"sop_conflict": {
                "passage_ref": verdict.get("passage_ref"),
                "note": (verdict.get("note") or "")[:300],
                "at": datetime.now(timezone.utc),
            }}})
        await cs.set_status(
            tenant_id=tenant_id, app_slug=app_slug,
            clause_id=clause["clause_id"], status="sop_conflict",
            actor="consolidation",
            cause=f"contradicts SOP: {(verdict.get('note') or '')[:120]}")
        log.warning("[CONSOLIDATE] %s/%s SUSPENDED — contradicts SOP: %s",
                    app_slug, clause["clause_id"], verdict.get("note"))
    except Exception:  # noqa: BLE001
        log.exception("[CONSOLIDATE] could not suspend sop-conflicted %s",
                      clause.get("clause_id"))


async def _overridden_by_rule_ids(tenant_id: str, app_slug: str) -> Set[str]:
    """Clause ids the model reported overriding in favour of a rule/SOP."""
    import main

    out: Set[str] = set()
    col = main.get_decision_records_col()
    cur = col.find(
        {"tenant_id": tenant_id, "slug": app_slug,
         "cited_clauses.relation": "overrode_by_rule"},
        {"_id": 0, "cited_clauses": 1}).limit(500)
    async for r in cur:
        for c in (r.get("cited_clauses") or []):
            if isinstance(c, dict) and c.get("relation") == "overrode_by_rule":
                if c.get("clause_id"):
                    out.add(str(c["clause_id"]))
    return out


async def default_sop_checker(
    *, tenant_id: str, app_slug: str, text: str,
) -> Optional[Dict[str, Any]]:
    """Real SOP contradiction check.

    Fetches the app's SOP through the SAME cached fetch the item tools use
    (`tools_v2_dispatch._fetch_sop_cached`), authenticating with the system JWT
    the trigger/poller paths already mint (`_mint_system_auth`) — a background
    job has no officer JWT. The sop_source is taken from the first agent tool
    that declares one; an app with no sop_source is simply not checkable and
    returns None (no conflict claimed).

    Then one bounded model call. Returns None (no SOP / no conflict) or
    {contradicts: True, passage_ref, note}. Best-effort by design — the runtime
    supremacy instruction (J1) is the primary defense; this is early warning.
    """
    try:
        import main as _main
        from config import get_settings
        from trigger_runner import _mint_system_auth
        from tools_v2_dispatch import _fetch_sop_cached

        settings = get_settings()
        app = await _main.get_apps_col().find_one({"slug": app_slug})
        agent = None
        agent_id = ((app or {}).get("app_spec") or {}).get("agent_id")
        if agent_id:
            agent = await _main.get_agents_col().find_one({"agent_id": agent_id})
        sop_source = sop_query = task_type = None
        for t in (((agent or {}).get("agent_spec") or {}).get("tools") or []):
            if isinstance(t, dict) and t.get("sop_source"):
                sop_source = t.get("sop_source")
                sop_query = t.get("sop_query")
                task_type = t.get("task_type") or t.get("name")
                break
        if not sop_source or not app:
            return None

        from models import AppSpec

        try:
            app_spec = AppSpec.model_validate(app.get("app_spec") or {})
        except Exception:  # noqa: BLE001 — legacy spec shapes
            return None
        header = _mint_system_auth(settings, app_spec, app)
        sop = await _fetch_sop_cached(
            settings=settings, user_jwt=header,
            sop_source=sop_source, sop_query=sop_query,
            tenant_id=tenant_id, app_slug=app_slug,
            modality="record", task_type=task_type or "decision")
        if not (sop or "").strip():
            return None
    except ImportError:
        return None
    except Exception:  # noqa: BLE001 — no SOP reachable = no check, loudly
        log.exception("[CONSOLIDATE] SOP fetch failed for %s", app_slug)
        return None

    from config import get_settings
    from llm_client import get_llm_client_for

    settings = get_settings()
    tier = settings.llm_tier_config("medium")
    client = get_llm_client_for(tier["base_url"], tier["api_key"])
    resp = await client.chat.completions.create(
        model=tier["model"],
        messages=[
            {"role": "system", "content": (
                "You check ONE learned judgement against an organization's "
                "SOP. Judgements that FILL GAPS the SOP does not cover are "
                "expected and fine - answer none. Answer contradicts ONLY "
                "when the judgement instructs something the SOP forbids or "
                "forbids something the SOP requires. Answer as one line: "
                '"none" OR "contradicts: <the SOP point> - <one-line why>"')},
            {"role": "user",
             "content": "SOP:\n" + sop[:12000] + "\n\nJUDGEMENT:\n" + text},
        ],
        temperature=0.0, max_tokens=4000, timeout=60,
        extra_body=(tier.get("extra_body") or None),
    )
    out = (resp.choices[0].message.content or "").strip()
    if out.lower().startswith("contradicts"):
        return {"contradicts": True, "passage_ref": None,
                "note": out.split(":", 1)[-1].strip()[:300]}
    return None


async def aggregate_clause_performance(
    *, tenant_id: str, app_slug: str, modality: str, task_type: str,
) -> Dict[str, Dict[str, int]]:
    """Recompute fired/blamed counters from the correction ledger (plan §10.4).

    ``fired``  — the clause was injected into a run that reached a disposition.
    ``blamed`` — the officer rejected/overrode AND the model CITED that clause.

    The blame rule is the point: only ``cited ∩ injected`` is penalised. When
    the model cites nothing, NOTHING is blamed — punishing the whole injected
    set is exactly the credit-assignment bug this design removes. The share of
    rejections that cite nothing is returned as ``__uncited`` so a degraded
    citation habit is a visible metric rather than silent decay.

    Runs in the batch, off the hot path: /run stamps ids on the staging row and
    /approve copies them onto the correction, so no clause write ever sits in an
    officer's request.
    """
    col = cx._col()
    rows = await col.find(
        {"tenant_id": tenant_id, "app_slug": app_slug,
         "modality": modality, "task_type": task_type},
        {"_id": 0, "injected_clause_ids": 1, "cited_clause_ids": 1, "event": 1},
    ).to_list(20_000)

    counters: Dict[str, Dict[str, int]] = {}
    uncited = 0
    negative = 0
    for r in rows:
        injected = list(r.get("injected_clause_ids") or [])
        cited = set(r.get("cited_clause_ids") or [])
        for cid in injected:
            counters.setdefault(cid, {"fired": 0, "blamed": 0})["fired"] += 1
        # Dissent: the model set this clause aside AND the officer still
        # rejected/overrode — the officer is acting against the rule. Recorded
        # per distinct officer so a clause crosses into `dissented` only on real
        # disagreement, never because one person disposed several cases.
        for cid in (set(r.get("overruled_clause_ids") or []) & set(injected)):
            counters.setdefault(cid, {"fired": 0, "blamed": 0})
            counters[cid].setdefault("dissenters", set()).add(r.get("officer") or "?")
        # Every correction row IS a reject or an override — a clean approve is
        # never recorded — so any row with injected clauses is a negative signal.
        if injected:
            negative += 1
            if not cited:
                uncited += 1
        for cid in (cited & set(injected)):
            counters.setdefault(cid, {"fired": 0, "blamed": 0})["blamed"] += 1

    if negative:
        rate = uncited / negative
        log.info("[CONSOLIDATE] %s uncited-reject rate %.2f (%d/%d)",
                 app_slug, rate, uncited, negative)
        if rate > 0.5:
            log.warning(
                "[CONSOLIDATE] %s: %.0f%% of corrections cite NO clause — the "
                "blame edge is not being recorded, so clause precision is "
                "unmeasurable and learning is degraded", app_slug, rate * 100)
    return counters


def alias_map_of(signature: Optional[Dict[str, Any]]) -> Dict[str, str]:
    """old_code -> canonical_code from the app's reason-code aliases."""
    out: Dict[str, str] = {}
    for rc in ((signature or {}).get("reason_codes") or []):
        if not isinstance(rc, dict):
            continue
        for alias in (rc.get("aliases") or []):
            if alias and rc.get("code"):
                out[str(alias)] = str(rc["code"])
    return out


async def _bucket_settings(tenant_id: str, app_slug: str) -> Tuple[int, Optional[int], Dict[str, str]]:
    """(promotion_min_officers, signature_version) from the app's case_signature.

    Falls back to the platform default when the app has no signature — such an
    app still accumulates corrections and can still form clauses, it just has no
    facets to scope them by (they come out global)."""
    try:
        import main
        from case_signature import learning_config, signature_of

        app = await main.get_apps_col().find_one(
            {"slug": app_slug}, {"_id": 0, "app_spec": 1})
        sig = signature_of(app) if app else None
        cfg = learning_config(sig)
        return (cfg["promotion_min_officers"], (sig or {}).get("version"),
                alias_map_of(sig))
    except Exception:  # noqa: BLE001 — settings lookup is enrichment
        log.warning("[CONSOLIDATE] could not read case_signature for %s — using "
                    "platform defaults", app_slug)
        return 3, None, {}
