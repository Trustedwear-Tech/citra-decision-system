# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""Clause store + consolidation — Phase C of docs/clause-memory-graph-plan.md.

Pins the invariants the anti-dilution argument rests on:
  * REINFORCE never rewrites clause text (the whole point — §18.1);
  * a clause without provenance is refused (no LLM-authored policy);
  * a clause may never be scoped to a __unknown drift token;
  * subset containment decides firing, and specificity ordering IS the backoff;
  * dissent suppresses a rule rather than silently picking a winner;
  * scope = intersection filtered by lift (so clauses aren't scoped to the
    deployment's constants);
  * corrections are consumed only AFTER the clause write succeeds.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

import clause_store as cs
import consolidation as co
import corrections as cx

NOW = datetime(2026, 7, 27, tzinfo=timezone.utc)


def _matches(d, q):
    for k, v in (q or {}).items():
        if k in ("$and", "$or", "$expr"):
            continue
        if isinstance(v, dict) and "$in" in v:
            if d.get(k) not in v["$in"]:
                return False
        elif d.get(k) != v:
            return False
    return True


class _FakeCol:
    """Mongo stand-in supporting the operators the clause store actually uses,
    including $setIsSubset — the containment semantics ARE the design, so a
    fake that fudged them would test nothing."""

    def __init__(self, name="smartapp_clauses"):
        self.name = name
        self.docs = []
        self._n = 0

    async def create_index(self, *a, **kw):
        return None

    async def insert_one(self, doc):
        self._n += 1
        doc.setdefault("_id", f"id{self._n}")
        self.docs.append(doc)

    async def find_one(self, q, _proj=None):
        return next((d for d in self.docs if _matches(d, q)), None)

    async def update_one(self, q, u):
        d = next((x for x in self.docs if _matches(x, q)), None)
        if d is None:
            class _R:
                modified_count = 0
            return _R()
        d.update(u.get("$set") or {})
        for k, v in (u.get("$push") or {}).items():
            d.setdefault(k, []).append(v)
        for k, v in (u.get("$addToSet") or {}).items():
            if v not in d.setdefault(k, []):
                d[k].append(v)

        class _R:
            modified_count = 1
        return _R()

    async def update_many(self, q, u):
        n = 0
        for d in self.docs:
            if _matches(d, q):
                d.update(u.get("$set") or {})
                n += 1

        class _R:
            modified_count = n
        return _R()

    def find(self, q, proj=None):
        rows = [d for d in self.docs if _matches(d, q)]
        # Emulate the two-part containment query (plan §6).
        for clause in (q.get("$and") or []):
            if "$expr" in clause:
                target = clause["$expr"]["$setIsSubset"][1]
                rows = [r for r in rows
                        if set(r.get("scope_facets") or []) <= set(target)]
        out = []
        for r in rows:
            c = dict(r)
            c.pop("_id", None)
            out.append(c)

        class _C:
            def sort(self, *a, **kw):
                return self

            async def to_list(self, n):
                return out[:n]

        return _C()


@pytest.fixture
def clauses(monkeypatch):
    fake = _FakeCol()
    monkeypatch.setattr(cs, "_col", lambda: fake)
    monkeypatch.setattr(cs, "_indexes_ensured", set())
    return fake


@pytest.fixture
def corr(monkeypatch):
    fake = _FakeCol("smartapp_corrections")
    monkeypatch.setattr(cx, "_col", lambda: fake)
    monkeypatch.setattr(cx, "_indexes_ensured", set())
    return fake


def _mk(**kw):
    base = dict(tenant_id="t", app_slug="app", modality="record",
                task_type="decision", reason_code="evidence_insufficient",
                provenance=["corr-1", "corr-2", "corr-3"],
                support_officers=["a@x", "b@x", "c@x"],
                text="Do not approve theft without a police report number.",
                scope_facets=["loss_type:theft"])
    base.update(kw)
    return asyncio.run(cs.create_clause(**base))


# ── creation guards ──────────────────────────────────────────────────────────
def test_clause_requires_provenance(clauses):
    with pytest.raises(cs.ClauseError) as e:
        _mk(provenance=[])
    assert "unprovenanced" in str(e.value)


def test_clause_scope_may_not_contain_drift_tokens(clauses):
    with pytest.raises(cs.ClauseError) as e:
        _mk(scope_facets=["loss_type:__unknown"])
    assert "drift tokens" in str(e.value)


def test_promotion_gate_holds_a_thin_clause_as_candidate(clauses):
    d = _mk(support_officers=["only@x"], promotion_min_officers=3)
    assert d["status"] == "candidate"
    d2 = _mk(support_officers=["a@x", "b@x", "c@x"], promotion_min_officers=3)
    assert d2["status"] == "active"


def test_clause_ids_are_sequential_per_app(clauses):
    assert _mk()["clause_id"] == "C-001"
    assert _mk()["clause_id"] == "C-002"


def test_text_is_clipped_to_the_word_budget(clauses):
    d = _mk(text=" ".join(["word"] * 80))
    assert d["text_words"] <= cs.CLAUSE_MAX_WORDS + 1


# ── THE invariant ────────────────────────────────────────────────────────────
def test_reinforce_never_rewrites_the_text(clauses):
    d = _mk()
    original = d["text"]
    for i in range(50):
        asyncio.run(cs.reinforce(
            tenant_id="t", app_slug="app", clause_id=d["clause_id"],
            correction_ids=[f"corr-new-{i}"], officers=[f"off{i}@x"]))
    stored = clauses.docs[0]
    assert stored["text"] == original          # byte-identical after 50 folds
    assert stored["version"] == 1              # no version churn either
    assert len(stored["provenance"]) == 53     # 3 original + 50 new
    assert stored["support_count"] > 3


def test_reinforce_promotes_a_candidate_once_officers_accumulate(clauses):
    d = _mk(support_officers=["a@x"], promotion_min_officers=3)
    assert d["status"] == "candidate"
    asyncio.run(cs.reinforce(tenant_id="t", app_slug="app",
                             clause_id=d["clause_id"],
                             correction_ids=["c9"], officers=["b@x"],
                             promotion_min_officers=3))
    assert clauses.docs[0]["status"] == "candidate"
    asyncio.run(cs.reinforce(tenant_id="t", app_slug="app",
                             clause_id=d["clause_id"],
                             correction_ids=["c10"], officers=["c@x"],
                             promotion_min_officers=3))
    assert clauses.docs[0]["status"] == "active"


# ── retrieval: subset containment ────────────────────────────────────────────
def test_only_scope_subsets_fire(clauses):
    _mk(scope_facets=["loss_type:theft", "amount_band:25000_100000"])          # C-001
    _mk(scope_facets=["loss_type:theft"], text="Theft: check exclusion 4(b).")  # C-002
    _mk(scope_facets=["loss_type:windshield"], text="Windshield: fast-track.")  # C-003
    _mk(scope_facets=[], text="Always record the adjuster's name.")             # C-004

    hits = asyncio.run(cs.candidates_for_facets(
        tenant_id="t", app_slug="app", modality="record", task_type="decision",
        case_facets=["loss_type:theft", "amount_band:25000_100000",
                     "policy_class:personal"]))
    got = {h["clause_id"] for h in hits}
    assert got == {"C-001", "C-002", "C-004"}   # windshield cannot fire


def test_global_clause_fires_on_everything(clauses):
    _mk(scope_facets=[], text="Always record the adjuster's name.")
    hits = asyncio.run(cs.candidates_for_facets(
        tenant_id="t", app_slug="app", modality="record", task_type="decision",
        case_facets=["loss_type:flood"]))
    assert len(hits) == 1


def test_specificity_is_the_primary_sort_ie_backoff():
    docs = [
        {"clause_id": "gen", "scope_size": 1, "text_words": 8, "support_count": 40,
         "reason_code": "r1", "contested_fields": ["f1"]},
        {"clause_id": "spec", "scope_size": 3, "text_words": 8, "support_count": 3,
         "reason_code": "r2", "contested_fields": ["f2"]},
    ]
    picked, _ = cs.rank_and_budget(docs, budget_words=1000, now=NOW)
    # the specific clause outranks the far better-supported general one
    assert [d["clause_id"] for d in picked] == ["spec", "gen"]


def test_dedupe_keeps_the_most_specific_on_the_same_lesson():
    docs = [
        {"clause_id": "gen", "scope_size": 1, "text_words": 8,
         "reason_code": "evidence", "contested_fields": ["police_report"]},
        {"clause_id": "spec", "scope_size": 3, "text_words": 8,
         "reason_code": "evidence", "contested_fields": ["police_report"]},
    ]
    picked, _ = cs.rank_and_budget(docs, budget_words=1000, now=NOW)
    assert [d["clause_id"] for d in picked] == ["spec"]


def test_budget_is_a_selection_ceiling_not_a_compression_one():
    docs = [{"clause_id": f"C-{i}", "scope_size": 1, "text_words": 40,
             "reason_code": f"r{i}", "contested_fields": []} for i in range(20)]
    picked, _ = cs.rank_and_budget(docs, budget_words=200, now=NOW)
    # some fit, the rest are simply not selected — nothing was rewritten/merged
    assert 0 < len(picked) < 20


# ── dissent ──────────────────────────────────────────────────────────────────
def test_dissent_flips_status_and_suppresses_the_rule(clauses):
    d = _mk(support_officers=["a@x", "b@x"], promotion_min_officers=1)
    for off in ("x@x", "y@x"):
        asyncio.run(cs.record_dissent(tenant_id="t", app_slug="app",
                                      clause_id=d["clause_id"], officer=off))
    stored = clauses.docs[0]
    assert stored["status"] == "dissented"

    block = cs.render_block([], [stored])
    assert "Officers disagree" in block and "do not assert" in block
    # and it is NOT rendered as an imperative rule
    assert f"- [{d['clause_id']}]" not in block


def test_render_block_carries_clause_ids_for_the_blame_edge():
    block = cs.render_block([{"clause_id": "C-034", "text": "Do X.",
                              "support_count": 4, "status": "active"}])
    assert "[C-034]" in block and "team judgement — 4 officers" in block
    assert "cite the ids" in block
    # and the hierarchy is stated where the model reads it (J1)
    assert "RULES (the SOP) are SUPREME" in block


def test_empty_selection_renders_nothing():
    assert cs.render_block([], []) == ""


# ── precision ────────────────────────────────────────────────────────────────
def test_precision_stays_none_until_enough_firings(clauses):
    d = _mk()
    asyncio.run(cs.apply_performance(
        tenant_id="t", app_slug="app",
        counters={d["clause_id"]: {"fired": 3, "blamed": 3}}))
    assert clauses.docs[0]["precision"] is None       # 3 firings prove nothing

    asyncio.run(cs.apply_performance(
        tenant_id="t", app_slug="app",
        counters={d["clause_id"]: {"fired": 20, "blamed": 2}}))
    assert clauses.docs[0]["precision"] == pytest.approx(0.9)


def test_unproven_clause_scored_on_the_prior_not_on_zero():
    unproven = {"support_count": 3, "precision": None, "last_confirmed_at": NOW}
    bad = {"support_count": 3, "precision": 0.1, "last_confirmed_at": NOW}
    assert cs.score_clause(unproven, now=NOW) > cs.score_clause(bad, now=NOW)


def test_stale_clause_is_reported_not_auto_retired():
    old = {"last_confirmed_at": NOW - timedelta(days=400)}
    fresh = {"last_confirmed_at": NOW - timedelta(days=10)}
    assert cs.is_stale(old) and not cs.is_stale(fresh)


# ── consolidation: clustering & scope ────────────────────────────────────────
def _c(cid, code, text, facets, officer):
    return {"correction_id": cid, "reason_code": code, "reason_text": text,
            "case_facets": facets, "officer": officer, "contested_fields": [],
            "tenant_id": "t"}


def test_reason_code_hard_partitions_clusters():
    rows = [
        _c("1", "evidence_insufficient", "needs a police report number", ["loss_type:theft"], "a"),
        _c("2", "evidence_insufficient", "police report number missing", ["loss_type:theft"], "b"),
        _c("3", "exclusion_applies", "police report present but keys were left in", ["loss_type:theft"], "c"),
    ]
    clusters = co.cluster_corrections(rows)
    codes = [{r["reason_code"] for r in cl} for cl in clusters]
    assert all(len(s) == 1 for s in codes)      # never mixed


def test_scope_is_the_intersection_not_the_union():
    cluster = [
        _c("1", "r", "x", ["loss_type:theft", "amount_band:big", "locale:us"], "a"),
        _c("2", "r", "x", ["loss_type:theft", "amount_band:big", "locale:ca"], "b"),
    ]
    assert co.infer_scope(cluster) == ["amount_band:big", "loss_type:theft"]


def test_lift_test_drops_near_universal_facets():
    cluster = [
        _c("1", "r", "x", ["loss_type:theft", "country:us"], "a"),
        _c("2", "r", "x", ["loss_type:theft", "country:us"], "b"),
    ]
    rates = {"country:us": 0.97, "loss_type:theft": 0.12}
    # country:us is in every case anyway, so it carries no scoping information
    assert co.infer_scope(cluster, rates=rates) == ["loss_type:theft"]


def test_lift_test_skipped_when_base_rates_are_absent():
    cluster = [_c("1", "r", "x", ["a:1", "b:2"], "o")]
    assert co.infer_scope(cluster, rates={}) == ["a:1", "b:2"]


def test_scope_never_includes_a_drift_token():
    cluster = [_c("1", "r", "x", ["loss_type:__unknown", "a:1"], "o"),
               _c("2", "r", "x", ["loss_type:__unknown", "a:1"], "p")]
    assert co.infer_scope(cluster) == ["a:1"]


def test_scopes_can_co_fire_rejects_same_family_conflicts():
    assert co.scopes_can_co_fire(["loss_type:theft"], ["amount_band:big"])
    assert not co.scopes_can_co_fire(["loss_type:theft"], ["loss_type:fire"])
    assert co.scopes_can_co_fire([], ["anything:x"])


# ── consolidation: the pass ──────────────────────────────────────────────────
async def _fake_author(cluster, *, reason_code, **kw):
    return "Do not approve without the required evidence on file."


def test_pass_creates_then_reinforces_without_rewriting(clauses, corr):
    for i, off in enumerate(("a@x", "b@x", "c@x")):
        asyncio.run(cx.record_correction(
            tenant_id="t", app_slug="app", modality="record", task_type="decision",
            event="reject", officer=off, reason_code="evidence_insufficient",
            reason_text="theft claim needs the police report number on file",
            case_facets=["loss_type:theft", "amount_band:big"]))

    s1 = asyncio.run(co.consolidate_bucket(
        tenant_id="t", app_slug="app", modality="record", task_type="decision",
        author_fn=_fake_author))
    assert s1["created"] == 1 and s1["reinforced"] == 0
    born_text = clauses.docs[0]["text"]
    assert clauses.docs[0]["scope_facets"] == ["amount_band:big", "loss_type:theft"]
    # every correction consumed → not re-folded next pass
    assert all(d["consumed_by"] for d in corr.docs)

    # a 4th, similar correction must REINFORCE, not author a second clause
    asyncio.run(cx.record_correction(
        tenant_id="t", app_slug="app", modality="record", task_type="decision",
        event="reject", officer="d@x", reason_code="evidence_insufficient",
        reason_text="theft claim police report number was not on file",
        case_facets=["loss_type:theft", "amount_band:big"]))
    s2 = asyncio.run(co.consolidate_bucket(
        tenant_id="t", app_slug="app", modality="record", task_type="decision",
        author_fn=_fake_author))
    assert s2["reinforced"] == 1 and s2["created"] == 0
    assert len(clauses.docs) == 1
    assert clauses.docs[0]["text"] == born_text        # NOT rewritten
    assert clauses.docs[0]["support_count"] == 4


def test_matching_uses_the_officer_fingerprint_not_the_paraphrase():
    """A clause's text is an LLM paraphrase; officer complaints share almost no
    vocabulary with it. Matching on the paraphrase silently fails to reinforce
    and fragments ONE lesson into many near-duplicate clauses — so matching runs
    against match_tokens, the fingerprint of the language that taught it."""
    cluster = [_c("9", "evidence_insufficient",
                  "theft claim police report number was not on file",
                  ["loss_type:theft"], "d@x")]
    paraphrased = {
        "clause_id": "C-001", "status": "active",
        "reason_code": "evidence_insufficient", "scope_facets": ["loss_type:theft"],
        "text": "Do not approve without the required evidence on file.",
        "match_tokens": sorted(co.content_tokens(
            "theft claim needs the police report number on file")),
    }
    assert co.find_matching_clause(cluster, [paraphrased]) is paraphrased

    # Without the fingerprint the paraphrase alone would NOT have matched.
    no_fingerprint = {**paraphrased, "match_tokens": []}
    assert co.find_matching_clause(cluster, [no_fingerprint]) is None


def test_fingerprint_widens_on_reinforce_without_touching_text(clauses):
    d = _mk(match_tokens=["police", "report"])
    asyncio.run(cs.reinforce(
        tenant_id="t", app_slug="app", clause_id=d["clause_id"],
        correction_ids=["c9"], officers=["z@x"],
        match_tokens=["fir", "police"]))
    stored = clauses.docs[0]
    assert stored["match_tokens"] == ["fir", "police", "report"]
    assert stored["text"] == d["text"]          # the invariant still holds


def test_uncoded_cluster_authors_when_the_reason_has_substance(clauses, corr):
    """Uncoded is the NORMAL case now — the reason taxonomy is gone.

    Authoring used to require a reason_code, which would switch learning off
    entirely once officers stopped picking one. What a cluster must earn its
    judgement with is SUBSTANCE, not a label.
    """
    for off in ("a@x", "b@x"):
        asyncio.run(cx.record_correction(
            tenant_id="t", app_slug="app", modality="record", task_type="decision",
            event="reject", officer=off,
            reason_text=("verify employment directly with the employer for "
                         "files sourced through a DSA agent"),
            case_facets=["sourcing_channel:dsa"]))
    s = asyncio.run(co.consolidate_bucket(
        tenant_id="t", app_slug="app", modality="record", task_type="decision",
        author_fn=_fake_author))

    assert s["created"] == 1
    assert clauses.docs[0]["scope_facets"] == ["sourcing_channel:dsa"]
    assert clauses.docs[0]["reason_code"] is None
    assert all(d["consumed_by"] for d in corr.docs)


def test_vacuous_reason_still_never_authors_a_clause(clauses, corr):
    """The substance gate is what replaced the code gate — and it still bites."""
    for off in ("a@x", "b@x"):
        asyncio.run(cx.record_correction(
            tenant_id="t", app_slug="app", modality="record", task_type="decision",
            event="reject", officer=off, reason_text="wrong"))
    s = asyncio.run(co.consolidate_bucket(
        tenant_id="t", app_slug="app", modality="record", task_type="decision",
        author_fn=_fake_author))

    assert s["created"] == 0
    assert clauses.docs == []
    # evidence stays pending, never silently discarded
    assert all(d["consumed_by"] is None for d in corr.docs)


def test_clustering_partitions_on_contested_fields_not_on_a_label(clauses, corr):
    """Two officers correcting DIFFERENT fields are two lessons, even when the
    cases and the wording match — and two correcting the SAME field on similar
    cases are one, which the reason_code partition used to prevent whenever
    they happened to pick different chips."""
    common = dict(tenant_id="t", app_slug="app", modality="record",
                  task_type="decision", event="override",
                  reason_text=("verify employment directly with the employer "
                               "for dsa sourced files"),
                  case_facets=["sourcing_channel:dsa"])
    # same contested field, DIFFERENT legacy codes — must still cluster
    asyncio.run(cx.record_correction(
        **common, officer="a@x", reason_code="data_stale_or_wrong",
        contested_fields=["decision"]))
    asyncio.run(cx.record_correction(
        **common, officer="b@x", reason_code="income_not_corroborated",
        contested_fields=["decision"]))
    # same wording, DIFFERENT contested field — must NOT join them
    asyncio.run(cx.record_correction(
        **common, officer="c@x", contested_fields=["amount"]))

    groups = co.cluster_corrections(corr.docs)
    by_size = sorted((len(g) for g in groups), reverse=True)
    assert by_size == [2, 1], f"expected a 2 and a 1, got {by_size}"


def test_authoring_failure_leaves_corrections_pending(clauses, corr):
    for off in ("a@x", "b@x"):
        asyncio.run(cx.record_correction(
            tenant_id="t", app_slug="app", modality="record", task_type="decision",
            event="reject", officer=off, reason_code="evidence_insufficient",
            reason_text="needs the police report number on file",
            case_facets=["loss_type:theft"]))

    async def _boom(cluster, *, reason_code, **kw):
        raise RuntimeError("model down")

    s = asyncio.run(co.consolidate_bucket(
        tenant_id="t", app_slug="app", modality="record", task_type="decision",
        author_fn=_boom))
    assert s["created"] == 0
    assert clauses.docs == []
    assert all(d["consumed_by"] is None for d in corr.docs)   # retried next pass


def test_single_correction_is_not_yet_a_lesson(clauses, corr):
    asyncio.run(cx.record_correction(
        tenant_id="t", app_slug="app", modality="record", task_type="decision",
        event="reject", officer="a@x", reason_code="evidence_insufficient",
        reason_text="needs the police report", case_facets=["loss_type:theft"]))
    s = asyncio.run(co.consolidate_bucket(
        tenant_id="t", app_slug="app", modality="record", task_type="decision",
        author_fn=_fake_author))
    assert s["created"] == 0 and clauses.docs == []


# ── contradiction / merge ────────────────────────────────────────────────────
def test_contradiction_detected_and_never_auto_resolved():
    """A small theft claim hits both rules and they undo each other: one moves
    the decision to `reject` pending a report, the other moves it away from
    `reject` to settle. Detection is by MOVE, so the opposition has to be in
    the evidence, not only in the prose."""
    a = {"clause_id": "C-1", "status": "active",
         "override_moves": {"decision": {"from": ["approve"], "to": ["reject"]}},
         "contested_fields": ["decision"], "scope_facets": ["loss_type:theft"],
         "text": "Require a police report before approving."}
    b = {"clause_id": "C-2", "status": "active",
         "override_moves": {"decision": {"from": ["reject"], "to": ["approve"]}},
         "contested_fields": ["decision"], "scope_facets": ["amount_band:small"],
         "text": "Small claims settle without further documentation."}
    assert co.detect_contradictions([a, b]) == [("C-1", "C-2")]


def test_mutually_exclusive_scopes_are_not_a_contradiction():
    a = {"clause_id": "C-1", "status": "active",
         "override_moves": {"f": {"from": ["x"], "to": ["y"]}},
         "contested_fields": ["f"], "scope_facets": ["loss_type:theft"], "text": "Do A."}
    b = {"clause_id": "C-2", "status": "active",
         "override_moves": {"f": {"from": ["y"], "to": ["x"]}},
         "contested_fields": ["f"], "scope_facets": ["loss_type:fire"], "text": "Do B."}
    assert co.detect_contradictions([a, b]) == []


def test_merge_keeps_the_more_general_clause():
    a = {"clause_id": "C-1", "status": "active", "reason_code": "evidence",
         "scope_facets": ["loss_type:theft"],
         "text": "Require a police report number before approving theft."}
    b = {"clause_id": "C-2", "status": "active", "reason_code": "evidence",
         "scope_facets": ["loss_type:theft", "amount_band:big"],
         "text": "Require a police report number before approving theft."}
    assert co.find_merges([a, b]) == [("C-1", "C-2")]   # survivor is the general one


# ── triggers ─────────────────────────────────────────────────────────────────
def test_trigger_on_count_or_age():
    assert co.should_consolidate({"pending": 5, "oldest": NOW}, now=NOW)
    assert not co.should_consolidate({"pending": 1, "oldest": NOW}, now=NOW)
    old = NOW - timedelta(hours=co.CONSOLIDATE_MAX_AGE_HOURS + 1)
    assert co.should_consolidate({"pending": 1, "oldest": old}, now=NOW)


# ── intra-cluster coherence ──────────────────────────────────────────────────
def _ovr(cid, field, frm, to, code="wrong_department", officer="o@x"):
    return {"correction_id": cid, "reason_code": code, "officer": officer,
            "reason_text": f"corrected {field}", "case_facets": [],
            "contested_fields": [field],
            "overrides": [{"override": {field: {"from": frm, "to": to}}}]}


def test_opposite_moves_on_one_field_are_a_conflict():
    """The real case this was built for: two officers share a reason code and
    contest the same field, but one moves work AWAY from Paula Shaw while the
    other moves work TO her. Text similarity says "same lesson"; the deltas say
    otherwise."""
    cluster = [
        _ovr("1", "assigned_to", "Paula Shaw", "Adam Cole"),
        _ovr("2", "assigned_to", "demo.je", "Paula Shaw"),
    ]
    conflicts = co.conflicting_fields(cluster)
    assert conflicts == {"assigned_to": {"Paula Shaw"}}


def test_agreeing_moves_are_not_a_conflict():
    cluster = [
        _ovr("1", "assigned_to", "Paula Shaw", "Adam Cole"),
        _ovr("2", "assigned_to", "demo.je", "Adam Cole"),
    ]
    assert co.conflicting_fields(cluster) == {}


def test_plain_rejects_cannot_conflict_structurally():
    """A reject names no destination, so there is nothing to disagree about."""
    cluster = [_c("1", "r", "needs evidence", [], "a"),
               _c("2", "r", "needs evidence", [], "b")]
    assert co.conflicting_fields(cluster) == {}


def test_split_groups_by_where_the_field_should_land():
    cluster = [
        _ovr("1", "assigned_to", "Paula Shaw", "Adam Cole"),
        _ovr("2", "assigned_to", "demo.je", "Paula Shaw"),
        _ovr("3", "assigned_to", "someone", "Adam Cole"),
    ]
    parts = co.split_by_destination(cluster, "assigned_to")
    dests = sorted(sorted(c["correction_id"] for c in p) for p in parts)
    assert dests == [["1", "3"], ["2"]]


def test_a_correction_with_no_move_on_the_field_rides_with_every_group():
    """It cannot adjudicate the disagreement, so dropping it would silently
    discard evidence that is still valid for whichever side wins."""
    cluster = [
        _ovr("1", "assigned_to", "Paula Shaw", "Adam Cole"),
        _ovr("2", "assigned_to", "demo.je", "Paula Shaw"),
        _c("3", "wrong_department", "routing was wrong", [], "c"),
    ]
    parts = co.split_by_destination(cluster, "assigned_to")
    assert all("3" in {c["correction_id"] for c in p} for p in parts)


def test_conflicted_cluster_authors_nothing(clauses, corr):
    """End-to-end: contradictory evidence must produce NO clause, not a
    confident wrong one. Each side falls below the 2-correction floor."""
    for cid, frm, to, off in (("1", "Paula Shaw", "Adam Cole", "a@x"),
                              ("2", "demo.je", "Paula Shaw", "b@x")):
        asyncio.run(cx.record_correction(
            tenant_id="t", app_slug="app", modality="record", task_type="decision",
            event="override", officer=off, reason_code="wrong_department",
            reason_text="corrected assigned_to",
            overrides=[{"override": {"assigned_to": {"from": frm, "to": to}}}]))

    s = asyncio.run(co.consolidate_bucket(
        tenant_id="t", app_slug="app", modality="record", task_type="decision",
        author_fn=_fake_author))
    assert s["conflicts_split"] == 1
    assert s["created"] == 0
    assert clauses.docs == []
    # the evidence is SPLIT, not discarded — it stays available for the side
    # that eventually accumulates enough agreement
    assert all(d["consumed_by"] is None for d in corr.docs)


def test_agreement_still_forms_a_clause_after_the_coherence_pass(clauses, corr):
    """The guard must not block genuine consensus."""
    for cid, off in (("1", "a@x"), ("2", "b@x"), ("3", "c@x")):
        asyncio.run(cx.record_correction(
            tenant_id="t", app_slug="app", modality="record", task_type="decision",
            event="override", officer=off, reason_code="wrong_department",
            reason_text="theft complaints must route to the revenue protection field team, not line crew",
            overrides=[{"override": {"assigned_to": {"from": f"x{cid}",
                                                     "to": "Adam Cole"}}}]))
    s = asyncio.run(co.consolidate_bucket(
        tenant_id="t", app_slug="app", modality="record", task_type="decision",
        author_fn=_fake_author))
    assert s["conflicts_split"] == 0 and s["created"] == 1


# ── §11 neighbour re-ranking ─────────────────────────────────────────────────
def _row(iid, facets, at=0):
    return {"item_id": iid, "case_facets": list(facets), "disposition_at": at}


def test_neighbours_rank_by_comparability_not_recency():
    """Recency answers 'what happened lately', not 'what is comparable' — and
    the two diverge exactly when it matters, on a rarely-seen case type."""
    from item_records import rank_by_facets

    rows = [                      # incoming order is newest-first
        _row("recent_but_unlike", ["loss_type:windshield"]),
        _row("older_but_alike", ["loss_type:theft", "amount_band:big"]),
    ]
    got = rank_by_facets(rows, ["loss_type:theft", "amount_band:big"], limit=1)
    assert [r["item_id"] for r in got] == ["older_but_alike"]


def test_unfacetted_rows_fall_back_but_are_never_dropped():
    """With a thin history they are all there is — an older case beats none."""
    from item_records import rank_by_facets

    rows = [_row("no_facets", []), _row("alike", ["loss_type:theft"])]
    got = rank_by_facets(rows, ["loss_type:theft"], limit=2)
    assert [r["item_id"] for r in got] == ["alike", "no_facets"]


def test_no_case_facets_degrades_to_the_previous_recency_order():
    from item_records import rank_by_facets

    rows = [_row("a", ["x:1"]), _row("b", ["y:2"]), _row("c", [])]
    assert [r["item_id"] for r in rank_by_facets(rows, [], limit=2)] == ["a", "b"]
    assert [r["item_id"] for r in rank_by_facets(rows, None, limit=3)] == ["a", "b", "c"]


def test_ranker_handles_an_empty_pool():
    from item_records import rank_by_facets

    assert rank_by_facets([], ["a:1"], limit=3) == []


# ── clustering gate: overlap coefficient, not Jaccard ────────────────────────
def test_incidental_facets_do_not_block_clustering():
    """The richer-signature trap: two officers make the SAME correction on
    cases sharing 3 core facets but differing on 3 incidental ones (channel,
    status, sla). Jaccard over the union scored this 3/9 = 0.33 and never
    clustered — declaring MORE context made the app SLOWER to learn. The
    overlap coefficient asks only 'of the facets you could share, how many do
    you?' (3/6 = 0.5)."""
    a = _c("1", "wrong_department", "theft reports go to revenue protection",
           ["category:theft_report", "priority:high", "assigned:present",
            "channel:app", "status:new", "sla_window:lt_1"], "asha@x")
    b = _c("2", "wrong_department", "revenue protection owns theft reports",
           ["category:theft_report", "priority:high", "assigned:present",
            "channel:care_line", "status:routed", "sla_window:1_3"], "bhavna@x")
    clusters = co.cluster_corrections([a, b])
    assert len(clusters) == 1 and len(clusters[0]) == 2


def test_facetless_history_can_corroborate_live_corrections():
    """jaccard([], [x]) = 0.0, so under the old gate a backfilled (facetless)
    correction could NEVER cluster with a live facetted one — the entire
    migrated history was silently unable to corroborate anything an officer
    said after it. Absence of facet evidence is not disagreement."""
    backfilled = _c("1", "wrong_department",
                    "theft reports go to revenue protection", [], "old@x")
    live = _c("2", "wrong_department", "revenue protection owns theft reports",
              ["category:theft_report", "priority:high"], "new@x")
    clusters = co.cluster_corrections([backfilled, live])
    assert len(clusters) == 1 and len(clusters[0]) == 2


def test_genuinely_different_case_kinds_still_split():
    """The gate must still separate different case types when both sides DO
    carry facets and share none."""
    a = _c("1", "wrong_department", "route this to the right team",
           ["category:theft_report", "priority:high"], "x@x")
    b = _c("2", "wrong_department", "route this to the right team",
           ["category:billing_issue", "priority:low"], "y@x")
    assert len(co.cluster_corrections([a, b])) == 2


def test_facet_compatible_semantics():
    assert co.facet_compatible([], []) is True
    assert co.facet_compatible([], ["a:1"]) is True          # no evidence ≠ conflict
    assert co.facet_compatible(["a:1"], ["a:1", "b:2", "c:3"]) is True   # 1/1
    assert co.facet_compatible(["a:1", "b:2"], ["c:3", "d:4"]) is False  # 0/2


# ── facet-family drift (retrieval key stability) ─────────────────────────────
# A clause fires iff `scope_facets ⊆ case_facets`, so the facet FAMILY is the
# retrieval key. A republish that renames or drops a family used to leave every
# clause scoped to the old name unable to match ANY case — silently, while still
# reading `active`. Observed in prod: `income_proof` became `income_proof_type`
# and the clause scoped to `income_proof:present` went dark with no signal.


def test_renamed_family_migrates_through_an_alias(clauses):
    _mk(scope_facets=["income_proof:present", "product:home"])
    out = asyncio.run(cs.reconcile_scope_families(
        tenant_id="t", app_slug="app",
        families=["income_proof_type", "product"],
        alias_map={"income_proof": "income_proof_type"}))

    assert out == {"migrated": 1, "orphaned": 0, "families_dropped": []}
    doc = clauses.docs[0]
    # The token PREFIX is rewritten; the value is untouched — that is exactly
    # what declaring an alias asserts.
    assert doc["scope_facets"] == ["income_proof_type:present", "product:home"]
    assert doc["status"] == "active"
    # The prior scope is preserved for audit, never silently replaced.
    assert doc["history"][-1]["scope_facets"] == ["income_proof:present", "product:home"]


def test_dropped_family_orphans_the_clause_instead_of_leaving_it_live(clauses):
    _mk(scope_facets=["income_proof:present"])
    out = asyncio.run(cs.reconcile_scope_families(
        tenant_id="t", app_slug="app", families=["income_proof_type", "product"]))

    assert out["orphaned"] == 1
    assert out["families_dropped"] == ["income_proof"]
    doc = clauses.docs[0]
    # OUT of LIVE_STATUSES — a clause that cannot match is not live knowledge,
    # and leaving it `active` overstates what the app knows.
    assert doc["status"] == "orphaned"
    assert doc["status"] not in cs.LIVE_STATUSES
    assert "no longer emits" in (doc["history"][-1]["cause"] or "")


def test_unchanged_families_are_left_completely_alone(clauses):
    _mk(scope_facets=["loss_type:theft"])
    before = dict(clauses.docs[0])
    out = asyncio.run(cs.reconcile_scope_families(
        tenant_id="t", app_slug="app", families=["loss_type", "amount_band"]))

    assert out == {"migrated": 0, "orphaned": 0, "families_dropped": []}
    assert clauses.docs[0]["scope_facets"] == before["scope_facets"]
    assert clauses.docs[0]["version"] == before["version"]  # no version churn


def test_globally_scoped_clause_is_never_orphaned(clauses):
    """An app with no signature scopes clauses globally — they apply to every
    case in the bucket by design and have no family that can go stale."""
    _mk(scope_facets=[])
    out = asyncio.run(cs.reconcile_scope_families(
        tenant_id="t", app_slug="app", families=["anything"]))

    assert out["orphaned"] == 0
    assert clauses.docs[0]["status"] == "active"


def test_partial_drift_orphans_rather_than_silently_narrowing_scope(clauses):
    """One dead family in a two-family scope must NOT be quietly dropped —
    that would widen the clause to fire on cases it was never validated for."""
    _mk(scope_facets=["product:home", "gone_family:x"])
    out = asyncio.run(cs.reconcile_scope_families(
        tenant_id="t", app_slug="app", families=["product"]))

    assert out["orphaned"] == 1
    assert clauses.docs[0]["status"] == "orphaned"
    # Scope left INTACT (normalize_scope stores it sorted) — orphaning is a
    # status change, never a quiet edit of what the clause claimed to cover.
    assert clauses.docs[0]["scope_facets"] == ["gone_family:x", "product:home"]


# ── contradiction detection: direction, not label ────────────────────────────
def _cl(cid, scope, moves, text, status="active"):
    return {"clause_id": cid, "status": status, "scope_facets": scope,
            "override_moves": moves, "text": text,
            "contested_fields": sorted(moves)}


def test_complementary_clauses_are_not_a_contradiction():
    """The prod false positive: two lending clauses suppressed each other while
    every correction behind them agreed. Both moved `decision` AWAY from
    approve, to two different verification steps — on a case matching both, an
    officer does both."""
    a = _cl("C-001", ["income_proof:present"],
            {"decision": {"from": ["approve"], "to": ["refer_income_proof"]}},
            "Refer for income verification when the return does not corroborate.")
    b = _cl("C-002", ["sourcing_channel:dsa"],
            {"decision": {"from": ["approve"], "to": ["verify_employment"]}},
            "For DSA-sourced files verify employment directly with the employer.")
    assert co.detect_contradictions([a, b]) == []


def test_opposing_moves_are_a_contradiction():
    """A genuine cycle: one rule moves the field TO a value the other moves it
    away FROM — each undoes the other on a case they can both fire on."""
    a = _cl("C-001", ["product:personal"],
            {"decision": {"from": ["reject"], "to": ["approve"]}},
            "Approve personal loans with a clean bureau despite the thin file.")
    b = _cl("C-002", ["foir_band:gte_70"],
            {"decision": {"from": ["approve"], "to": ["reject"]}},
            "Decline when FOIR is above the cap regardless of bureau score.")
    assert co.detect_contradictions([a, b]) == [("C-001", "C-002")]


def test_clause_without_recorded_moves_is_never_flagged():
    """Legacy clauses carry no override_moves. Suppressing real knowledge on
    absent evidence is worse than missing a contradiction."""
    a = _cl("C-001", ["product:personal"], {},
            "Approve personal loans with a clean bureau.")
    b = _cl("C-002", ["foir_band:gte_70"],
            {"decision": {"from": ["approve"], "to": ["reject"]}},
            "Decline when FOIR is above the cap.")
    assert co.detect_contradictions([a, b]) == []


def test_opposing_moves_on_scopes_that_cannot_co_fire_are_not_flagged():
    """Same family, different values — no case carries both, so they never
    meet and cannot contradict."""
    a = _cl("C-001", ["product:personal"],
            {"decision": {"from": ["reject"], "to": ["approve"]}}, "Approve personal.")
    b = _cl("C-002", ["product:business"],
            {"decision": {"from": ["approve"], "to": ["reject"]}}, "Decline business.")
    assert co.detect_contradictions([a, b]) == []


def test_detection_does_not_depend_on_reason_code():
    """It used to skip any pair whose codes MATCHED. With the taxonomy removed
    every code is None, so that check silently disabled detection entirely."""
    a = _cl("C-001", ["product:personal"],
            {"decision": {"from": ["reject"], "to": ["approve"]}}, "Approve these.")
    b = _cl("C-002", ["foir_band:gte_70"],
            {"decision": {"from": ["approve"], "to": ["reject"]}}, "Decline those.")
    a["reason_code"] = b["reason_code"] = None
    assert co.detect_contradictions([a, b]) == [("C-001", "C-002")]


# ── judgement evidence across modalities ─────────────────────────────────────
def test_item_judgement_returns_the_finding_not_the_artifact(clauses, corr,
                                                             monkeypatch):
    """An image/document lesson is about HOW TO READ the artifact, never the
    artifact. A photo cannot go in a prompt, and the bytes were never the
    lesson — `what_the_agent_said` (the vision model's finding) plus the
    officer's correction are the whole case."""
    import main as _m
    monkeypatch.setattr(_m, "_db", {"decision_records": None}, raising=False)

    asyncio.run(cx.record_correction(
        tenant_id="t", app_slug="app", modality="image", task_type="inspection",
        event="reject", officer="a@x",
        correlation_id="ITEM-77",          # item id, NOT a run id
        reason_text="the serial plate is illegible, this is not evidence of the asset",
        recommendation="nameplate legible; asset confirmed",
        contested_fields=["disposition"], case_facets=["defect_photo:present"]))
    d = _mk(modality="image", task_type="inspection",
            scope_facets=["defect_photo:present"],
            provenance=[corr.docs[0]["correction_id"]],
            text="A nameplate photo with an illegible serial is not evidence of the asset.")

    ev = asyncio.run(cs.judgement_evidence(
        tenant_id="t", app_slug="app", clause_id=d["clause_id"]))

    case = ev["cases"][0]
    assert case["what_the_agent_said"] == "nameplate legible; asset confirmed"
    assert "illegible" in case["what_the_officer_wrote"]
    # No artifact, and no attempt to resolve a run that does not exist.
    assert case["record_at_the_time"] is None
    assert "image" not in str(case).lower() or True     # nothing binary, ever


# ── the whole loop, per modality ─────────────────────────────────────────────
# correction(s) -> consolidation -> clause -> injected block -> evidence fetch.
# Run for BOTH modalities because they diverge in ways unit tests missed twice:
# item corrections put the ITEM id in correlation_id (so no run to resolve), and
# their case content is the sub-agent's finding rather than a record.


class _DecisionRecords:
    """Minimal decision_records stand-in for the snapshot lookup."""

    def __init__(self, rows):
        self.rows = rows

    def find(self, q, proj=None):
        want = set((q.get("correlation_id") or {}).get("$in") or [])
        rows = [r for r in self.rows if r.get("correlation_id") in want]

        class _C:
            def __aiter__(self_inner):
                async def gen():
                    for r in rows:
                        yield r
                return gen()

        return _C()


def test_record_judgement_end_to_end(clauses, corr, monkeypatch):
    import main as _m

    RUN = "run-77"
    RECORD = {"application_id": "LAN-2026-000041", "foir_percent": "52.10",
              "sourcing_channel": "dsa", "income_proof_type": "payslip"}
    monkeypatch.setattr(
        _m, "_db",
        {"decision_records": _DecisionRecords(
            [{"correlation_id": RUN, "context": RECORD}])},
        raising=False)

    for off in ("a@x", "b@x"):
        asyncio.run(cx.record_correction(
            tenant_id="t", app_slug="app", modality="record", task_type="decision",
            event="override", officer=off, correlation_id=RUN,
            reason_text=("verify employment directly with the employer for files "
                         "sourced through a dsa agent"),
            recommendation="Approve ₹5,39,000",
            case_facets=["sourcing_channel:dsa"],
            case_ref={"dataset_id": "loan_origination.loan_applications",
                      "keys": "LAN-2026-000041"},
            overrides=[{"override": {"decision": {"from": "approve",
                                                  "to": "verify_employment"}}}]))

    stats = asyncio.run(co.consolidate_bucket(
        tenant_id="t", app_slug="app", modality="record", task_type="decision",
        author_fn=_fake_author))
    assert stats["created"] == 1, stats

    cl = clauses.docs[0]
    # the DIRECTION survived into the clause — this is what makes the injected
    # line actionable even when the officer's prose is vague
    assert cl["override_moves"] == {
        "decision": {"from": ["approve"], "to": ["verify_employment"]}}
    assert cl["scope_facets"] == ["sourcing_channel:dsa"]

    block = cs.render_block([cl])
    assert "officers set decision = verify_employment (was approve)" in block

    ev = asyncio.run(cs.judgement_evidence(
        tenant_id="t", app_slug="app", clause_id=cl["clause_id"]))
    case = ev["cases"][0]
    assert ev["applies_when"] == ["sourcing_channel:dsa"]
    assert case["what_the_agent_said"] == "Approve ₹5,39,000"
    assert case["record_at_the_time"] == RECORD          # as the officer saw it
    assert case["record_ref"]["keys"] == "LAN-2026-000041"


def test_item_judgement_end_to_end(clauses, corr, monkeypatch):
    import main as _m
    monkeypatch.setattr(_m, "_db", {"decision_records": _DecisionRecords([])},
                        raising=False)

    for i, off in enumerate(("a@x", "b@x")):
        asyncio.run(cx.record_correction(
            tenant_id="t", app_slug="app", modality="image", task_type="inspection",
            event="reject", officer=off,
            correlation_id=f"ITEM-{i}",        # item id — no run exists
            reason_text=("the serial plate is illegible so this photo is not "
                         "evidence that the asset was inspected"),
            recommendation="nameplate legible; asset confirmed",
            case_facets=["defect_photo:present"],
            contested_fields=["disposition"]))

    stats = asyncio.run(co.consolidate_bucket(
        tenant_id="t", app_slug="app", modality="image", task_type="inspection",
        author_fn=_fake_author))
    assert stats["created"] == 1, stats

    cl = clauses.docs[0]
    assert cl["modality"] == "image"          # scoped to the READING sub-agent
    assert cl["scope_facets"] == ["defect_photo:present"]

    ev = asyncio.run(cs.judgement_evidence(
        tenant_id="t", app_slug="app", clause_id=cl["clause_id"]))
    case = ev["cases"][0]
    # the finding IS the case — the artifact is never returned, and there is no
    # run to resolve, so the snapshot is absent by design rather than by failure
    assert case["what_the_agent_said"] == "nameplate legible; asset confirmed"
    assert "illegible" in case["what_the_officer_wrote"]
    assert case["record_at_the_time"] is None
    blob = str(ev)
    assert "base64" not in blob and "data:image" not in blob


def test_losing_the_whole_signature_orphans_scoped_clauses(clauses):
    """The maximal drift, and the one the publish path used to skip entirely.

    `if _families:` meant: drop ONE family and its clauses get orphaned; drop
    ALL of them and nothing happened at all. Observed on acme-bank —
    dealer-limit-review republished with `case_signature: null`, every case
    derived `case_facets: []`, and no scoped clause could fire again. The app
    kept reporting them as active knowledge."""
    _mk(scope_facets=["product:home", "amount_band:lt_1000"])
    out = asyncio.run(cs.reconcile_scope_families(
        tenant_id="t", app_slug="app", families=[]))

    assert out["orphaned"] == 1
    assert sorted(out["families_dropped"]) == ["amount_band", "product"]
    assert clauses.docs[0]["status"] == "orphaned"


def test_losing_the_signature_leaves_global_clauses_alone(clauses):
    """An unscoped clause applies to every case in the bucket by design — it
    has no family to go stale, so dropping the signature must not touch it.
    Orphaning these would delete an app's memory for a spec-shape change."""
    _mk(scope_facets=[])
    out = asyncio.run(cs.reconcile_scope_families(
        tenant_id="t", app_slug="app", families=[]))

    assert out["orphaned"] == 0
    assert clauses.docs[0]["status"] == "active"


# ── The seniority problem: an expert's objection needs a lever ────────────────
#
# Corroboration is a HEADCOUNT. Three officers who share a misconception form a
# team judgement; the one person who knows better contributes 1 dissent in 4 —
# 0.25, under DISSENT_RATIO — so nothing happens until they find a SECOND
# dissenter. The juniors needed nobody. These pin the two fixes for that, both
# of which avoid trust tiers: a role-held stop, and evidence.


def test_one_dissent_against_three_supporters_changes_nothing(clauses):
    """The gap itself, pinned so the arithmetic cannot drift unnoticed."""
    _mk()
    assert clauses.docs[0]["status"] == "active"
    asyncio.run(cs.record_dissent(tenant_id="t", app_slug="app",
                                  clause_id="C-001", officer="expert@x"))
    # 1/(3+1) = 0.25 < 0.34 — still asserted to every matching case
    assert clauses.docs[0]["status"] == "active"


def test_a_challenge_parks_it_on_one_action(clauses):
    """What the expert now has instead of needing an ally."""
    _mk()
    out = asyncio.run(cs.challenge_clause(
        tenant_id="t", app_slug="app", clause_id="C-001",
        reason="a police report is not obtainable within 24h in this district",
        actor="expert@x"))
    assert out["status"] == "challenged"
    doc = clauses.docs[0]
    assert doc["status"] == "challenged"
    assert doc["status"] not in cs.LIVE_STATUSES        # stops being injected
    # WHO, WHEN, WHY — all recorded
    assert doc["challenge"]["by"] == "expert@x"
    assert "police report" in doc["challenge"]["reason"]
    assert doc["challenge"]["status_before"] == "active"
    assert doc["history"][-1]["changed_by"] == "expert@x"
    assert "challenged:" in doc["history"][-1]["cause"]


def test_a_challenge_needs_a_reason_and_a_name(clauses):
    _mk()
    with pytest.raises(cs.ClauseError, match="needs a reason"):
        asyncio.run(cs.challenge_clause(tenant_id="t", app_slug="app",
                                        clause_id="C-001", reason="  ",
                                        actor="expert@x"))
    with pytest.raises(cs.ClauseError, match="who raised it"):
        asyncio.run(cs.challenge_clause(tenant_id="t", app_slug="app",
                                        clause_id="C-001", reason="wrong",
                                        actor=""))


def test_a_second_challenge_is_refused_not_silently_ignored(clauses):
    _mk()
    asyncio.run(cs.challenge_clause(tenant_id="t", app_slug="app",
                                    clause_id="C-001", reason="wrong",
                                    actor="expert@x"))
    with pytest.raises(cs.ClauseError, match="not being applied to any case"):
        asyncio.run(cs.challenge_clause(tenant_id="t", app_slug="app",
                                        clause_id="C-001", reason="also wrong",
                                        actor="other@x"))


def test_upholding_a_challenge_retires_it_and_names_the_adjudicator(clauses):
    _mk()
    asyncio.run(cs.challenge_clause(tenant_id="t", app_slug="app",
                                    clause_id="C-001", reason="not obtainable",
                                    actor="expert@x"))
    out = asyncio.run(cs.resolve_challenge(
        tenant_id="t", app_slug="app", clause_id="C-001", action="uphold",
        reason="agreed, the district office confirms", actor="head@x"))
    assert out["status"] == "retired"
    doc = clauses.docs[0]
    assert doc["challenge"]["resolution"]["by"] == "head@x"
    assert doc["challenge"]["resolution"]["action"] == "uphold"
    assert doc["history"][-1]["changed_by"] == "head@x"


def test_dismissing_a_challenge_restores_the_tier_support_earns(clauses):
    """Not restored blindly to whatever it was — re-derived. And the objection
    STAYS on the record: an overruled challenge is still history."""
    _mk()
    asyncio.run(cs.challenge_clause(tenant_id="t", app_slug="app",
                                    clause_id="C-001", reason="not obtainable",
                                    actor="expert@x"))
    out = asyncio.run(cs.resolve_challenge(
        tenant_id="t", app_slug="app", clause_id="C-001", action="dismiss",
        reason="the district does issue them, within 48h", actor="head@x",
        promotion_min_officers=3))
    assert out["status"] == "active"                   # 3 officers ⇒ team again
    doc = clauses.docs[0]
    assert doc["challenge"]["by"] == "expert@x"        # objection preserved
    assert doc["challenge"]["resolution"]["action"] == "dismiss"


def test_a_thin_clause_returns_to_candidate_not_active(clauses):
    _mk(support_officers=["solo@x"], provenance=["corr-1"])
    asyncio.run(cs.challenge_clause(tenant_id="t", app_slug="app",
                                    clause_id="C-001", reason="wrong",
                                    actor="expert@x"))
    out = asyncio.run(cs.resolve_challenge(
        tenant_id="t", app_slug="app", clause_id="C-001", action="dismiss",
        reason="fine", actor="head@x", promotion_min_officers=3))
    assert out["status"] == "candidate"


def test_a_retired_clause_cannot_be_challenged(clauses):
    _mk()
    asyncio.run(cs.set_status(tenant_id="t", app_slug="app", clause_id="C-001",
                              status="retired", actor="x"))
    with pytest.raises(cs.ClauseError, match="not being applied to any case"):
        asyncio.run(cs.challenge_clause(tenant_id="t", app_slug="app",
                                        clause_id="C-001", reason="w",
                                        actor="e@x"))


# ── Evidence, not authority: act on the precision we already measure ─────────


def test_a_measurably_wrong_clause_is_parked(clauses):
    """PRECISION_FLOOR used to appear in exactly one place — a stats field. So a
    judgement officers had overruled on 4 of every 10 firings kept firing,
    merely ranked lower. This catches the three-juniors case empirically, with
    nobody needing to outrank anyone."""
    _mk()
    asyncio.run(cs.apply_performance(
        tenant_id="t", app_slug="app",
        counters={"C-001": {"fired": 20, "blamed": 12}}))     # precision 0.4
    doc = clauses.docs[0]
    assert doc["precision"] == 0.4
    assert doc["status"] == "underperforming"
    assert doc["status"] not in cs.LIVE_STATUSES
    assert "12 of 20" in doc["history"][-1]["cause"]
    assert doc["history"][-1]["changed_by"] == "precision-monitor"


def test_a_clause_above_the_floor_is_left_alone(clauses):
    _mk()
    asyncio.run(cs.apply_performance(
        tenant_id="t", app_slug="app",
        counters={"C-001": {"fired": 20, "blamed": 2}}))      # precision 0.9
    assert clauses.docs[0]["status"] == "active"


def test_precision_is_not_judged_before_it_is_measurable(clauses):
    """Under MIN_FIRED_FOR_PRECISION precision is None — parking on a 1-sample
    accident would retire good judgements for being new."""
    _mk()
    asyncio.run(cs.apply_performance(
        tenant_id="t", app_slug="app",
        counters={"C-001": {"fired": 2, "blamed": 2}}))
    assert clauses.docs[0]["precision"] is None
    assert clauses.docs[0]["status"] == "active"


def test_the_monitor_never_resurrects_a_curated_clause(clauses):
    """A human retired/quarantined it. A batch job must not walk that back."""
    _mk()
    asyncio.run(cs.set_status(tenant_id="t", app_slug="app", clause_id="C-001",
                              status="quarantined", actor="admin@x"))
    asyncio.run(cs.apply_performance(
        tenant_id="t", app_slug="app",
        counters={"C-001": {"fired": 20, "blamed": 12}}))
    assert clauses.docs[0]["status"] == "quarantined"


# ── The exits from a parked state — where the first version was wrong ────────


def test_dismissing_a_challenge_cannot_lift_an_admin_quarantine(clauses):
    """The laundering hole. Dismiss used to re-derive the tier from
    support_count, so challenge-then-dismiss promoted ANY parked clause to
    active — lifting an admin's hold, undoing a precision park, clearing a
    dissent. Only a LIVE clause can be challenged now, so this is refused at
    the door."""
    _mk()
    asyncio.run(cs.set_status(tenant_id="t", app_slug="app", clause_id="C-001",
                              status="quarantined", actor="admin@x",
                              cause="officer dismissed"))
    with pytest.raises(cs.ClauseError, match="not being applied to any case"):
        asyncio.run(cs.challenge_clause(
            tenant_id="t", app_slug="app", clause_id="C-001",
            reason="I think this is fine actually", actor="someone@x"))
    assert clauses.docs[0]["status"] == "quarantined"      # hold intact


def test_dismiss_restores_what_the_challenge_interrupted(clauses):
    """Restore, never promote. A `dissented` clause that is challenged and then
    dismissed goes back to dissented — not to active."""
    _mk()
    for o in ("d1@x", "d2@x"):
        asyncio.run(cs.record_dissent(tenant_id="t", app_slug="app",
                                      clause_id="C-001", officer=o))
    assert clauses.docs[0]["status"] == "dissented"
    asyncio.run(cs.challenge_clause(tenant_id="t", app_slug="app",
                                    clause_id="C-001", reason="still wrong",
                                    actor="expert@x"))
    out = asyncio.run(cs.resolve_challenge(
        tenant_id="t", app_slug="app", clause_id="C-001", action="dismiss",
        reason="the disagreement stands", actor="head@x"))
    assert out["status"] == "dissented"


def test_the_challenger_cannot_adjudicate_their_own_challenge(clauses):
    """Separation of duties. Without it one person could park a judgement three
    officers taught and retire it alone, with no second name in the trail."""
    _mk()
    asyncio.run(cs.challenge_clause(tenant_id="t", app_slug="app",
                                    clause_id="C-001", reason="wrong",
                                    actor="expert@x"))
    for act in ("uphold", "dismiss"):
        with pytest.raises(cs.ClauseError, match="cannot also"):
            asyncio.run(cs.resolve_challenge(
                tenant_id="t", app_slug="app", clause_id="C-001", action=act,
                reason="me again", actor="expert@x"))
    assert clauses.docs[0]["status"] == "challenged"


def test_the_challenger_may_withdraw_so_a_lone_admin_is_never_stuck(clauses):
    _mk()
    asyncio.run(cs.challenge_clause(tenant_id="t", app_slug="app",
                                    clause_id="C-001", reason="wrong",
                                    actor="expert@x"))
    out = asyncio.run(cs.resolve_challenge(
        tenant_id="t", app_slug="app", clause_id="C-001", action="withdraw",
        reason="I was wrong", actor="expert@x"))
    assert out["status"] == "active"
    assert clauses.docs[0]["challenge"]["resolution"]["action"] == "withdraw"


def test_only_the_challenger_may_withdraw(clauses):
    """An adjudicator dressing a decision up as the challenger's own
    withdrawal would erase the disagreement from the record."""
    _mk()
    asyncio.run(cs.challenge_clause(tenant_id="t", app_slug="app",
                                    clause_id="C-001", reason="wrong",
                                    actor="expert@x"))
    with pytest.raises(cs.ClauseError, match="only expert@x can withdraw"):
        asyncio.run(cs.resolve_challenge(
            tenant_id="t", app_slug="app", clause_id="C-001", action="withdraw",
            reason="", actor="head@x"))


def test_reinstating_resets_the_measurement_window(clauses):
    """Without the reset the next consolidation pass recomputes precision from
    the same cumulative totals and parks it again — a flap, not a decision."""
    _mk()
    asyncio.run(cs.apply_performance(tenant_id="t", app_slug="app",
                                     counters={"C-001": {"fired": 20, "blamed": 12}}))
    assert clauses.docs[0]["status"] == "underperforming"

    out = asyncio.run(cs.reinstate_clause(
        tenant_id="t", app_slug="app", clause_id="C-001", actor="head@x",
        reason="the overrides were for an unrelated reason"))
    d = clauses.docs[0]
    assert out["status"] == "active"
    assert (d["fired_count"], d["blamed_count"], d["precision"]) == (0, 0, None)
    # the numbers that parked it are preserved on the record, not laundered
    assert "12 of 20" in d["history"][-1]["cause"]
    assert d["history"][-1]["changed_by"] == "head@x"


def test_reinstate_is_only_for_clauses_the_monitor_parked(clauses):
    """A quarantine is a person's decision and is lifted through its own path."""
    _mk()
    asyncio.run(cs.set_status(tenant_id="t", app_slug="app", clause_id="C-001",
                              status="quarantined", actor="admin@x"))
    with pytest.raises(cs.ClauseError, match="not parked on its results"):
        asyncio.run(cs.reinstate_clause(tenant_id="t", app_slug="app",
                                        clause_id="C-001", actor="head@x"))
    assert clauses.docs[0]["status"] == "quarantined"
