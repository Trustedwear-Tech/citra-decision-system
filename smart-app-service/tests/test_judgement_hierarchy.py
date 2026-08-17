# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""Rules vs Judgements — sop-rules-officer-judgement-plan.md (J1–J7).

Pins the doctrine:
  * SOP is SUPREME — stated in the prompt, enforced at authoring (sop_conflict);
  * learned content is JUDGEMENT: team (corroborated) vs individual (one
    officer, used immediately, honestly labeled) — labeling, not suppression;
  * a one-officer app still learns (the cluster-size floor bends);
  * junk text and named individuals can never become judgement text;
  * a renamed reason code is one lesson, not two (aliases);
  * the corrections context window prefers COMPARABLE cases over recent ones.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

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

    async def insert_many(self, docs):
        for d in docs:
            await self.insert_one(d)

    async def find_one(self, q, _proj=None):
        return next((d for d in self.docs if _matches(d, q)), None)

    async def update_one(self, q, u):
        d = next((x for x in self.docs if _matches(x, q)), None)
        if d is None:
            class _R: modified_count = 0
            return _R()
        d.update(u.get("$set") or {})
        for k, v in (u.get("$push") or {}).items():
            d.setdefault(k, []).append(v)
        for k, v in (u.get("$addToSet") or {}).items():
            if v not in d.setdefault(k, []):
                d[k].append(v)
        class _R: modified_count = 1
        return _R()

    async def update_many(self, q, u):
        n = 0
        for d in self.docs:
            if _matches(d, q):
                d.update(u.get("$set") or {})
                n += 1
        class _R: modified_count = n
        return _R()

    async def distinct(self, field, q=None):
        return sorted({d.get(field) for d in self.docs
                       if _matches(d, q or {}) and d.get(field)})

    async def count_documents(self, q):
        return sum(1 for d in self.docs if _matches(d, q))

    def aggregate(self, pipeline):
        # Only the shape correction_stats uses: match -> group by reason_code.
        docs = self.docs
        match = next((st["$match"] for st in pipeline if "$match" in st), {})
        rows = [d for d in docs if _matches(d, match)]
        groups = {}
        for d in rows:
            k = d.get("reason_code")
            g = groups.setdefault(k, {"_id": k, "n": 0, "pending": 0})
            g["n"] += 1
            if d.get("consumed_by") is None:
                g["pending"] += 1
        out = list(groups.values())

        class _C:
            async def to_list(self, n):
                return out[:n]

        return _C()

    def find(self, q, proj=None):
        rows = [d for d in self.docs if _matches(d, q)]
        for clause in (q.get("$and") or []):
            if "$expr" in clause:
                target = clause["$expr"]["$setIsSubset"][1]
                rows = [r for r in rows
                        if set(r.get("scope_facets") or []) <= set(target)]
        out = [dict(r) for r in rows]
        for c in out:
            c.pop("_id", None)

        class _C:
            def sort(self, *a, **kw):
                return self

            def limit(self, n):
                return self

            async def to_list(self, n):
                return out[:n]

            def __aiter__(self):
                async def gen():
                    for r in out:
                        yield r
                return gen()

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
                provenance=["corr-1"], support_officers=["a@x"],
                text="Verify the tax filing against declared earnings.",
                scope_facets=["product:loan"])
    base.update(kw)
    return asyncio.run(cs.create_clause(**base))


# ═══ J1 — the hierarchy is stated in the prompt ══════════════════════════════
def test_render_states_sop_supremacy_once():
    block = cs.render_block([{"clause_id": "C-1", "text": "Do X.",
                              "support_count": 3, "status": "active"}])
    assert "RULES (the SOP) are SUPREME" in block
    assert "overrode_by_rule" in block          # the model knows how to report
    assert "cite the ids" in block


def test_render_attributes_team_vs_individual():
    team = {"clause_id": "C-1", "text": "Do X.", "support_count": 4,
            "status": "active"}
    solo = {"clause_id": "C-2", "text": "Watch tax-filing mismatches.",
            "support_count": 1, "status": "candidate"}
    block = cs.render_block([team, solo])
    assert "(team judgement — 4 officers)" in block
    assert "one officer's judgement — not yet corroborated" in block
    # the individual judgement is WEIGHED, not asserted
    assert "verify against the record" in block


def test_render_sop_conflict_is_a_notice_never_a_judgement():
    conflicted = {"clause_id": "C-9", "text": "Skip the FIR requirement.",
                  "status": "sop_conflict", "support_count": 3,
                  "dissent_count": 0}
    block = cs.render_block([], [conflicted])
    assert "conflicts with the SOP" in block and "follow the SOP" in block
    assert "Skip the FIR requirement" not in block   # text never shown as guidance


def test_extractor_accepts_overrode_by_rule():
    from runtime import _extract_audit_block

    reply = ('done\n```json\n{"decision":"approve","reasoning":"per SOP",'
             '"citations":[],"cited_clauses":[{"clause_id":"C-3",'
             '"relation":"overrode_by_rule","note":"SOP 4.2 supersedes"}]}\n```')
    *_rest, cited = _extract_audit_block(reply)
    assert cited == [{"clause_id": "C-3", "relation": "overrode_by_rule",
                      "note": "SOP 4.2 supersedes"}]


# ═══ J2 — individual judgements: labeled, bounded, shadowed ══════════════════
def test_candidates_are_retrieved_now(clauses):
    d = _mk(support_officers=["solo@x"], promotion_min_officers=3)
    assert d["status"] == "candidate"
    hits = asyncio.run(cs.candidates_for_facets(
        tenant_id="t", app_slug="app", modality="record", task_type="decision",
        case_facets=["product:loan"]))
    assert [h["clause_id"] for h in hits] == [d["clause_id"]]


def test_team_outranks_individual_regardless_of_specificity():
    team = {"clause_id": "team", "status": "active", "scope_size": 0,
            "support_count": 3, "text_words": 5, "reason_code": "r1",
            "contested_fields": ["f1"]}
    solo = {"clause_id": "solo", "status": "candidate", "scope_size": 4,
            "support_count": 1, "text_words": 5, "reason_code": "r2",
            "contested_fields": ["f2"]}
    picked, _ = cs.rank_and_budget([solo, team], budget_words=1000, now=NOW)
    assert [d["clause_id"] for d in picked] == ["team", "solo"]


def test_individual_judgements_are_capped():
    solos = [{"clause_id": f"s{i}", "status": "candidate", "scope_size": 1,
              "support_count": 1, "text_words": 5, "reason_code": f"r{i}",
              "contested_fields": []} for i in range(8)]
    picked, _ = cs.rank_and_budget(solos, budget_words=1000, now=NOW)
    assert len(picked) == cs.MAX_INDIVIDUAL_JUDGEMENTS


def test_team_shadows_individual_on_the_same_lesson():
    team = {"clause_id": "team", "status": "active", "scope_size": 1,
            "support_count": 3, "text_words": 5,
            "reason_code": "evidence", "contested_fields": ["tax_filing"]}
    solo = {"clause_id": "solo", "status": "candidate", "scope_size": 3,
            "support_count": 1, "text_words": 5,
            "reason_code": "evidence", "contested_fields": ["tax_filing"]}
    picked, _ = cs.rank_and_budget([team, solo], budget_words=1000, now=NOW)
    assert [d["clause_id"] for d in picked] == ["team"]


def test_one_supporter_one_dissenter_is_an_open_question(clauses):
    d = _mk(support_officers=["solo@x"], promotion_min_officers=3)
    assert d["status"] == "candidate"
    asyncio.run(cs.record_dissent(tenant_id="t", app_slug="app",
                                  clause_id=d["clause_id"], officer="other@x"))
    assert clauses.docs[0]["status"] == "dissented"


# ═══ J4 — quality gates ══════════════════════════════════════════════════════
def _c(cid, code, text, officer, facets=("product:loan",), overrides=None):
    return {"correction_id": cid, "tenant_id": "t", "app_slug": "app",
            "modality": "record", "task_type": "decision", "officer": officer,
            "event": "override" if overrides else "reject",
            "reason_code": code, "reason_text": text,
            "case_facets": list(facets), "contested_fields": [],
            "overrides": overrides or [], "injected_clause_ids": [],
            "cited_clause_ids": [], "overruled_clause_ids": [],
            "consumed_by": None, "at": NOW}


async def _author_never(cluster, *, reason_code, **kw):
    raise AssertionError("authoring must not be reached")


def test_as_discussed_x3_authors_nothing(clauses, corr):
    for i, off in enumerate(("a@x", "b@x", "c@x")):
        asyncio.run(cx.record_correction(
            tenant_id="t", app_slug="app", modality="record",
            task_type="decision", event="reject", officer=off,
            reason_code="other_reason", reason_text="as discussed"))
    s = asyncio.run(co.consolidate_bucket(
        tenant_id="t", app_slug="app", modality="record", task_type="decision",
        author_fn=_author_never))
    assert s["insufficient_reason"] == 1 and s["created"] == 0
    assert clauses.docs == []
    # evidence kept (not consumed) AND marked for the coaching counter
    assert all(d["consumed_by"] is None for d in corr.docs)
    assert all(d.get("insufficient_reason") for d in corr.docs)


def test_substantive_reasons_still_author(clauses, corr):
    for off, txt in (("a@x", "declared earnings do not match the tax filing identifiers"),
                     ("b@x", "tax filing mismatch against declared earnings, reject"),
                     ("c@x", "earnings look good but tax filing identifiers mismatch")):
        asyncio.run(cx.record_correction(
            tenant_id="t", app_slug="app", modality="record",
            task_type="decision", event="reject", officer=off,
            reason_code="evidence_insufficient", reason_text=txt,
            case_facets=["product:loan"]))

    async def _author(cluster, *, reason_code, **kw):
        return "Cross-check declared earnings against the tax filing identifiers."

    s = asyncio.run(co.consolidate_bucket(
        tenant_id="t", app_slug="app", modality="record", task_type="decision",
        author_fn=_author))
    assert s["created"] == 1 and s.get("insufficient_reason", 0) == 0


def test_person_named_text_is_rejected_then_retried(clauses, corr):
    for off in ("a@x", "b@x", "c@x"):
        asyncio.run(cx.record_correction(
            tenant_id="t", app_slug="app", modality="record",
            task_type="decision", event="reject", officer=off,
            reason_code="fraud_missed",
            reason_text="claims involving Mr. Sharma repeatedly turn out fraudulent",
            case_facets=["product:loan"]))

    calls = []

    async def _author(cluster, *, reason_code, extra_constraint="", **kw):
        calls.append(extra_constraint)
        if not extra_constraint:
            return "Reject claims from Mr. Sharma without review."
        return "Escalate claims from repeat claimants with prior fraud findings."

    s = asyncio.run(co.consolidate_bucket(
        tenant_id="t", app_slug="app", modality="record", task_type="decision",
        author_fn=_author))
    assert s["created"] == 1
    assert len(calls) == 2 and "Sharma" in calls[1]     # retried with violation quoted
    assert "Sharma" not in clauses.docs[0]["text"]      # the PATTERN survived, not the person


def test_person_named_text_twice_leaves_evidence_pending(clauses, corr):
    for off in ("a@x", "b@x", "c@x"):
        asyncio.run(cx.record_correction(
            tenant_id="t", app_slug="app", modality="record",
            task_type="decision", event="reject", officer=off,
            reason_code="fraud_missed",
            reason_text="watch claims from Mr. Sharma, always fraudulent",
            case_facets=["product:loan"]))

    async def _author(cluster, *, reason_code, extra_constraint="", **kw):
        return "Reject anything from Mr. Sharma or phone 9876543210."

    s = asyncio.run(co.consolidate_bucket(
        tenant_id="t", app_slug="app", modality="record", task_type="decision",
        author_fn=_author))
    assert s["created"] == 0 and clauses.docs == []
    assert all(d["consumed_by"] is None for d in corr.docs)   # retried next pass


# ═══ small-team floor (doctrine: one officer still teaches) ══════════════════
def test_one_officer_app_authors_an_individual_judgement(clauses, corr):
    asyncio.run(cx.record_correction(
        tenant_id="t", app_slug="app", modality="record", task_type="decision",
        event="reject", officer="only@x", reason_code="evidence_insufficient",
        reason_text="declared earnings do not match the tax filing identifiers",
        case_facets=["product:loan"]))

    async def _author(cluster, *, reason_code, **kw):
        return "Cross-check declared earnings against the tax filing."

    s = asyncio.run(co.consolidate_bucket(
        tenant_id="t", app_slug="app", modality="record", task_type="decision",
        author_fn=_author, promotion_min_officers=3))
    assert s["created"] == 1
    assert clauses.docs[0]["status"] == "candidate"     # individual, labeled
    assert clauses.docs[0]["support_count"] == 1


def test_three_officer_app_keeps_the_two_correction_floor(clauses, corr):
    # the app HAS seen 3 officers (history), so a lone new correction stays
    # below the floor — plenty of colleagues exist to corroborate it
    for i, off in enumerate(("a@x", "b@x", "c@x")):
        asyncio.run(cx.record_correction(
            tenant_id="t", app_slug="app", modality="record",
            task_type="decision", event="reject", officer=off,
            reason_code="other_hist", reason_text=f"historic lesson {i} text",
            case_facets=["product:card"]))
    for d in corr.docs:
        d["consumed_by"] = "C-000"
    asyncio.run(cx.record_correction(
        tenant_id="t", app_slug="app", modality="record", task_type="decision",
        event="reject", officer="a@x", reason_code="evidence_insufficient",
        reason_text="a brand new lone lesson about the tax filing identifiers",
        case_facets=["product:loan"]))
    s = asyncio.run(co.consolidate_bucket(
        tenant_id="t", app_slug="app", modality="record", task_type="decision",
        author_fn=_author_never, promotion_min_officers=3))
    assert s["created"] == 0


# ═══ J7 — reason-code aliasing ═══════════════════════════════════════════════
def test_alias_map_reunites_a_renamed_lesson(clauses, corr):
    texts = ["theft reports go to revenue protection not line crew",
             "theft report to revenue protection, never the line crew",
             "revenue protection owns theft reports not line crew"]
    codes = ["wrong_department", "wrong_department", "misrouted"]
    for i, (off, txt, code) in enumerate(zip(("a@x", "b@x", "c@x"), texts, codes)):
        asyncio.run(cx.record_correction(
            tenant_id="t", app_slug="app", modality="record",
            task_type="decision", event="reject", officer=off,
            reason_code=code, reason_text=txt,
            case_facets=["category:theft_report"]))

    async def _author(cluster, *, reason_code, **kw):
        assert reason_code == "misrouted"      # the canonical name
        return "Route theft reports to revenue protection."

    s = asyncio.run(co.consolidate_bucket(
        tenant_id="t", app_slug="app", modality="record", task_type="decision",
        author_fn=_author, alias_map={"wrong_department": "misrouted"}))
    assert s["created"] == 1
    assert clauses.docs[0]["support_count"] == 3       # 2+1 reunited
    assert clauses.docs[0]["reason_code"] == "misrouted"


def test_alias_map_of_builds_from_the_signature():
    sig = {"reason_codes": [
        {"code": "misrouted", "label": "x", "aliases": ["wrong_department", "wrong_team"]},
        {"code": "other", "label": "y"}]}
    assert co.alias_map_of(sig) == {"wrong_department": "misrouted",
                                    "wrong_team": "misrouted"}


def test_cs01_rejects_alias_collisions():
    from models import AppSpec
    from publish_validators import validate_case_signature

    sig = {"version": 1,
           "facets": [{"family": "f", "kind": "presence", "from_column": "c"}],
           "reason_codes": [
               {"code": "a", "label": "A", "aliases": ["b"]},   # collides with code b
               {"code": "b", "label": "B"},
               {"code": "other", "label": "O"}]}
    app = AppSpec.model_validate({
        "spec_version": "v0", "slug": "alias-x", "title": "T",
        "headless": True, "agent_id": "ag", "case_signature": sig})
    errs = validate_case_signature(app)
    assert any("collide" in e["reason"] for e in errs)


# ═══ J3 — SOP conflict lifecycle ═════════════════════════════════════════════
def test_sop_conflict_suspends_and_two_tap_resolves(clauses, corr):
    for off, txt in (("a@x", "theft claims do not need an FIR under the new circular"),
                     ("b@x", "theft claims need no FIR now, new circular applies"),
                     ("c@x", "under the new circular theft claims need no FIR")):
        asyncio.run(cx.record_correction(
            tenant_id="t", app_slug="app", modality="record",
            task_type="decision", event="reject", officer=off,
            reason_code="evidence_insufficient", reason_text=txt,
            case_facets=["product:loan"]))

    async def _author(cluster, *, reason_code, **kw):
        return "Do not require an FIR for theft claims."

    async def _checker(*, tenant_id, app_slug, text):
        return {"contradicts": True, "passage_ref": "SOP 4.2",
                "note": "SOP 4.2 requires an FIR for every theft claim"}

    s = asyncio.run(co.consolidate_bucket(
        tenant_id="t", app_slug="app", modality="record", task_type="decision",
        author_fn=_author, sop_checker=_checker))
    assert s["created"] == 1 and s["sop_conflicts"] == 1
    doc = clauses.docs[0]
    assert doc["status"] == "sop_conflict"
    assert doc["sop_conflict"]["note"].startswith("SOP 4.2")

    # suspended judgements never fire as judgements
    hits = asyncio.run(cs.candidates_for_facets(
        tenant_id="t", app_slug="app", modality="record", task_type="decision",
        case_facets=["product:loan"]))
    assert hits == []

    # tap 2a: the officers are right — the SOP is the stale one
    out = asyncio.run(cs.resolve_sop_conflict(
        tenant_id="t", app_slug="app", clause_id=doc["clause_id"],
        action="acknowledge", actor="supervisor@x", promotion_min_officers=3))
    assert out["status"] == "active"           # 3 officers ⇒ team tier restored
    assert clauses.docs[0]["sop_ack"]["by"] == "supervisor@x"


def test_sop_resolution_retire_path(clauses):
    d = _mk(support_officers=["a@x", "b@x", "c@x"], promotion_min_officers=3)
    asyncio.run(cs.set_status(tenant_id="t", app_slug="app",
                              clause_id=d["clause_id"], status="sop_conflict",
                              actor="consolidation", cause="test"))
    out = asyncio.run(cs.resolve_sop_conflict(
        tenant_id="t", app_slug="app", clause_id=d["clause_id"],
        action="retire", actor="supervisor@x"))
    assert out["status"] == "retired"


def test_sop_checker_outage_never_stops_learning(clauses, corr):
    for off, txt in (("a@x", "declared earnings do not match tax filing"),
                     ("b@x", "tax filing mismatch with earnings, reject"),
                     ("c@x", "earnings vs tax filing identifiers mismatch")):
        asyncio.run(cx.record_correction(
            tenant_id="t", app_slug="app", modality="record",
            task_type="decision", event="reject", officer=off,
            reason_code="evidence_insufficient", reason_text=txt,
            case_facets=["product:loan"]))

    async def _author(cluster, *, reason_code, **kw):
        return "Cross-check declared earnings against the tax filing."

    async def _boom(**kw):
        raise RuntimeError("sop corpus down")

    s = asyncio.run(co.consolidate_bucket(
        tenant_id="t", app_slug="app", modality="record", task_type="decision",
        author_fn=_author, sop_checker=_boom))
    assert s["created"] == 1
    assert clauses.docs[0]["status"] == "active"   # unchecked, not suspended


# ═══ J5 — comparability-ranked corrections window ════════════════════════════
def test_corrections_window_prefers_comparable_cases(monkeypatch):
    """The monsoon vignette: 39 flood corrections must not evict the one theft
    lesson when a THEFT case is being decided."""
    import main as _main
    from runtime import _prefetch_corrections_block

    rows = [{"slug": "app", "llm_recommendation_text": f"route flood complaint {i}",
             "status": "rejected", "case_facets": ["category:flood"],
             "audit_trail": [{"decision": "rejected",
                              "decision_reason": f"flood routing wrong {i}"}]}
            for i in range(39)]
    rows.append({"slug": "app", "llm_recommendation_text": "approve theft claim",
                 "status": "rejected",
                 "case_facets": ["category:theft_report", "priority:high"],
                 "audit_trail": [{"decision": "rejected",
                                  "decision_reason": "theft needs revenue protection"}]})

    fake = _FakeCol("smartapp_workflow_staging")
    fake.docs = rows
    monkeypatch.setattr(_main, "get_workflow_staging_col", lambda: fake)

    block = asyncio.run(_prefetch_corrections_block(
        slug="app", limit=8,
        case_facets=["category:theft_report", "priority:high"]))
    first_line = [l for l in block.split("\n") if l.startswith("- ")][0]
    assert "revenue protection" in first_line     # the comparable case leads


def test_corrections_window_degrades_to_recency_without_facets(monkeypatch):
    import main as _main
    from runtime import _prefetch_corrections_block

    fake = _FakeCol("smartapp_workflow_staging")
    fake.docs = [{"slug": "app", "llm_recommendation_text": f"case {i}", "status": "rejected",
                  "audit_trail": [{"decision": "rejected",
                                   "decision_reason": f"reason {i} text"}]}
                 for i in range(5)]
    monkeypatch.setattr(_main, "get_workflow_staging_col", lambda: fake)
    block = asyncio.run(_prefetch_corrections_block(slug="app", case_facets=[]))
    assert "reason 0 text" in block               # plain recency order kept


# ═══ J6 — visibility ═════════════════════════════════════════════════════════
def test_correction_stats_reports_officers_and_brief_count(corr):
    for off in ("a@x", "a@x", "b@x"):
        asyncio.run(cx.record_correction(
            tenant_id="t", app_slug="app", modality="record",
            task_type="decision", event="reject", officer=off,
            reason_text="a real lesson about the tax filing"))
    corr.docs[0]["insufficient_reason"] = True
    stats = asyncio.run(cx.correction_stats(tenant_ids=["t"], app_slug="app"))
    assert stats["distinct_officers"] == 2
    assert stats["too_brief"] == 1
