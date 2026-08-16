"""Learned-memory dispatch + item-path migration + batch control.
docs/clause-memory-graph-plan.md (Phase D / D2 / D3).

Pins the properties of the SINGLE memory path:
  * all four prompt sites (record, image, document, api) read clauses through
    one function — there is no mode switch and no blob to drift against;
  * an app with no case_signature still learns, its clauses just carry no scope;
  * item sites INHERIT the record's facets, since their own subject is not
    knowable until the model has looked;
  * every site exposes clause ids (the blame edge) and a traceable version tag;
  * item_subject is a SCOPE only where the subject is known before the prompt —
    otherwise consolidation mints clauses that can never fire;
  * pausing the batch is lossless, fails open, and is NOT a kill switch.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import learned_memory as lm

SIG = {
    "version": 2,
    "facets": [{"family": "loss_type", "kind": "enum",
                "from_column": "loss_type", "values": ["theft"]}],
    "reason_codes": [{"code": "a", "label": "A"}, {"code": "b", "label": "B"},
                     {"code": "other", "label": "Other"}],
}


def _app():
    return SimpleNamespace(
        slug="app", org_id="t", tenant_id="t", dataset_directory=[],
        case_signature=SIG)


@pytest.fixture
def stub(monkeypatch):
    calls = {"clauses": 0}

    async def _select(**kw):
        calls["clauses"] += 1
        calls["facets"] = list(kw.get("case_facets") or [])
        return "CLAUSE BLOCK", ["C-001", "C-034"]

    import clause_store as cs

    monkeypatch.setattr(cs, "select_clauses", _select)
    return calls


# ── one path, no switch ──────────────────────────────────────────────────────
def test_every_site_reads_clauses(stub):
    """Record, image, document and api all read the SAME store through the SAME
    function. There is no mode switch, because a switch is a promise that two
    systems stay in step — and the blob failed precisely because nobody could
    see it degrading."""
    for modality in ("record", "image", "document", "api"):
        block, ids, ver = asyncio.run(lm.learned_block(
            app_spec=_app(), tenant_id="t", app_slug="app",
            modality=modality, task_type="x"))
        assert block == "CLAUSE BLOCK"
        assert ids == ["C-001", "C-034"]        # blame edge available everywhere
        assert ver == "clauses/C-001,C-034"     # and traceable everywhere
    assert stub["clauses"] == 4


def test_no_case_signature_still_gets_clauses(stub):
    """No signature is not exclusion — the clauses simply carry no facet scope.
    Declaring a signature buys SCOPING and CODING, not membership."""
    app = SimpleNamespace(slug="app", org_id="t", case_signature=None)
    block, ids, _ver = asyncio.run(lm.learned_block(
        app_spec=app, tenant_id="t", app_slug="app",
        modality="image", task_type="damage"))
    assert stub["clauses"] == 1 and block == "CLAUSE BLOCK" and ids


def test_item_sites_inherit_the_record_facets(stub):
    """An image tool cannot derive its own scope (the model names the subject
    only after looking), so it inherits the case context the runtime already
    computed — otherwise every clause in the bucket would fire."""
    asyncio.run(lm.learned_block(
        app_spec=_app(), tenant_id="t", app_slug="app",
        modality="image", task_type="damage",
        case_facets=["loss_type:theft", "amount_band:big"]))
    assert stub["facets"] == ["loss_type:theft", "amount_band:big"]


def test_missing_tenant_skips_the_store(stub):
    assert asyncio.run(lm.learned_block(
        app_spec=_app(), tenant_id=None, app_slug="app",
        modality="image", task_type="damage")) == ("", [], None)
    assert stub["clauses"] == 0


def test_clause_failure_degrades_to_empty_never_raises(monkeypatch):
    import clause_store as cs

    async def _boom(**kw):
        raise RuntimeError("store down")

    monkeypatch.setattr(cs, "select_clauses", _boom)
    assert asyncio.run(lm.learned_block(
        app_spec=_app(), tenant_id="t", app_slug="app",
        modality="image", task_type="damage")) == ("", [], None)


# ── item subject scoping ─────────────────────────────────────────────────────
def test_subject_scopes_only_where_it_is_known_before_the_prompt():
    # api/case: the tool call names the check up front → usable as a scope.
    assert lm.item_subject_facet("credit-bureau check", "api") == \
        ["item_subject:credit-bureau_check"]
    assert lm.item_subject_facet("fraud screening", "case") != []
    # image/document: the MODEL emits the subject only after looking, so a
    # clause scoped to it could never satisfy the subset test at read time —
    # minting one would be silent dead knowledge that looks exactly like
    # "this app has not learned anything yet".
    assert lm.item_subject_facet("transformer nameplate photo", "image") == []
    assert lm.item_subject_facet("scanned report", "document") == []


def test_missing_subject_is_never_synthesized():
    assert lm.item_subject_facet("", "api") == []
    assert lm.item_subject_facet(None, "api") == []


# ── item folds reach the evidence ledger ─────────────────────────────────────
def test_item_reject_writes_a_correction(monkeypatch):
    """All four item modalities enter clause memory through append_correction —
    ONE wiring point, so image/document/api/case cannot drift apart."""
    import analysis_rubrics as ar
    import corrections as cx

    written = []

    async def _rec(**kw):
        written.append(kw)
        return "corr-x"

    monkeypatch.setattr(cx, "record_correction", _rec)

    asyncio.run(ar.append_correction(
        tenant_id="t", app_slug="app", modality="api", task_type="cibil",
        reason="the bureau score was stale", actor="o@x", item_id="it-1",
        subject="credit-bureau check", reason_code="data_stale_or_wrong"))

    assert len(written) == 1
    ev = written[0]
    assert ev["modality"] == "api" and ev["reason_code"] == "data_stale_or_wrong"
    assert ev["case_facets"] == ["item_subject:credit-bureau_check"]


def test_item_correction_carries_a_reason_code_so_it_can_become_a_clause(monkeypatch):
    """Consolidation refuses to author a clause from an UNCODED cluster (§9.2).
    So if the item endpoint never forwards reason_code, image/document/api
    feedback accumulates forever as evidence that can never become a rule —
    a silently one-way path."""
    import analysis_rubrics as ar
    import corrections as cx

    written = []

    async def _rec(**kw):
        written.append(kw)
        return "corr-x"

    monkeypatch.setattr(cx, "record_correction", _rec)

    asyncio.run(ar.append_correction(
        tenant_id="t", app_slug="app", modality="document", task_type="report",
        reason="the payee name does not match the invoice", actor="o@x",
        subject="scanned invoice", reason_code="verify_field_mismatch"))
    assert written[0]["reason_code"] == "verify_field_mismatch"


def test_image_item_records_evidence_without_a_subject_scope(monkeypatch):
    import analysis_rubrics as ar
    import corrections as cx

    written = []

    async def _rec(**kw):
        written.append(kw)
        return "corr-x"

    monkeypatch.setattr(cx, "record_correction", _rec)

    asyncio.run(ar.append_correction(
        tenant_id="t", app_slug="app", modality="image", task_type="damage",
        reason="the crack is on the breather, not the tank", actor="o@x",
        subject="transformer nameplate photo"))

    assert written[0]["case_facets"] == []        # evidence yes, scope no
    assert written[0]["recommendation"] == "transformer nameplate photo"


def test_record_path_writes_exactly_one_evidence_row(monkeypatch):
    """One officer event, one row. Two writes would inflate distinct-officer
    support and defeat the promotion gate."""
    import analysis_rubrics as ar
    import corrections as cx

    written = []

    async def _rec(**kw):
        written.append(kw)
        return "corr-x"

    monkeypatch.setattr(cx, "record_correction", _rec)

    asyncio.run(ar.fold_decision_feedback(
        tenant_id="t", app_slug="app", actor="o@x",
        reason="needs the police report", reason_code="evidence_insufficient"))
    assert len(written) == 1                       # exactly one, not two


# ── batch control ────────────────────────────────────────────────────────────
def test_pause_is_honoured_and_is_lossless(monkeypatch):
    import consolidation as co
    import corrections as cx

    async def _state():
        return {"paused": True, "actor": "admin", "reason": "maintenance"}

    monkeypatch.setattr(co, "get_control_state", _state)

    async def _buckets(**kw):
        raise AssertionError("a paused pass must not touch the queue")

    monkeypatch.setattr(cx, "pending_buckets", _buckets)

    out = asyncio.run(co.run_consolidation_pass())
    assert out["paused"] is True and out["created"] == 0


def test_control_read_failure_fails_open(monkeypatch):
    """A flag-store blip must not silently stop learning."""
    import consolidation as co

    def _boom():
        raise RuntimeError("mongo down")

    monkeypatch.setattr(co, "_control_col", _boom)
    state = asyncio.run(co.get_control_state())
    assert state["paused"] is False and state["control_read_failed"] is True


def test_status_distinguishes_empty_queue_from_unreadable_queue(monkeypatch):
    import consolidation as co
    import corrections as cx

    async def _state():
        return {"paused": False}

    monkeypatch.setattr(co, "get_control_state", _state)

    async def _boom(**kw):
        raise RuntimeError("mongo down")

    monkeypatch.setattr(cx, "pending_buckets", _boom)
    st = asyncio.run(co.consolidation_status())
    # "we don't know" must never render as "nothing pending"
    assert st["queue_read_failed"] is True and st["pending_total"] is None


def test_pass_sweeps_both_environments(monkeypatch):
    """The collection accessors route off a contextvar defaulting to 'prod'. A
    background loop has no request, so a naive sweep silently processes ONLY
    prod and a test app's clause memory stays permanently empty while looking
    merely 'not learned yet'."""
    import consolidation as co
    from env_context import current_env

    monkeypatch.setattr(co, "_environments_to_sweep", lambda: ["prod", "test"])

    seen = []

    async def _one(**kw):
        seen.append(current_env())
        return {"buckets": 1, "created": 1, "reinforced": 0, "skipped": 0,
                "contradictions": 0, "merges": 0, "performance_updated": 0,
                "errors": 0}

    monkeypatch.setattr(co, "_run_pass_one_env", _one)

    out = asyncio.run(co.run_consolidation_pass())
    assert seen == ["prod", "test"]
    assert out["created"] == 2 and set(out["by_env"]) == {"prod", "test"}
    # the contextvar must be restored, or the caller's env silently changes
    assert current_env() == "prod"


def test_single_environment_pass_restores_the_context(monkeypatch):
    import consolidation as co
    from env_context import current_env

    async def _one(**kw):
        assert current_env() == "test"
        return {"buckets": 0, "created": 0, "reinforced": 0, "skipped": 0,
                "contradictions": 0, "merges": 0, "performance_updated": 0,
                "errors": 0}

    monkeypatch.setattr(co, "_run_pass_one_env", _one)
    asyncio.run(co.run_consolidation_pass(environment="test"))
    assert current_env() == "prod"


def test_force_bypasses_the_thresholds(monkeypatch):
    import consolidation as co
    import corrections as cx

    async def _state():
        return {"paused": False}

    monkeypatch.setattr(co, "get_control_state", _state)

    async def _buckets(**kw):
        return [{"tenant_id": "t", "app_slug": "app", "modality": "record",
                 "task_type": "decision", "pending": 1, "oldest": None}]

    monkeypatch.setattr(cx, "pending_buckets", _buckets)

    seen = []

    async def _bucket(**kw):
        seen.append(kw["app_slug"])
        # Deliberately carries a counter the caller's totals dict does not
        # pre-declare: a new stat key must never make a SUCCESSFUL bucket
        # report as an error (found by the dev smoke run).
        return {"reinforced": 0, "created": 1, "skipped": 0, "pending": 3,
                "clusters": 1, "contradictions": 0, "merges": 0,
                "performance_updated": 2, "some_future_counter": 7}

    async def _settings(t, a):
        return 3, 1, {}

    async def _rec(_t):
        return None

    monkeypatch.setattr(co, "consolidate_bucket", _bucket)
    monkeypatch.setattr(co, "_bucket_settings", _settings)
    monkeypatch.setattr(co, "_record_pass", _rec)

    # 1 pending, no age ⇒ below threshold, skipped on a scheduled pass...
    asyncio.run(co.run_consolidation_pass())
    assert seen == []
    # ...but an operator asking for a run has already decided to pay the cost.
    out = asyncio.run(co.run_consolidation_pass(force=True))
    assert seen == ["app"]
    assert out["errors"] == 0                    # a success must not read as a failure
    assert out["created"] == 1
    assert out["performance_updated"] == 2
    assert out["some_future_counter"] == 7       # unknown counters roll up too
    assert "pending" not in out and "clusters" not in out   # per-bucket, not totals


# ── blame aggregation ────────────────────────────────────────────────────────
def test_only_cited_clauses_are_blamed(monkeypatch):
    """The credit-assignment fix: an uncited reject blames NOTHING. Blaming the
    whole injected set is the bug the clause store exists to remove."""
    import consolidation as co
    import corrections as cx

    rows = [
        # cited C-002 → only C-002 is blamed, though both fired
        {"injected_clause_ids": ["C-001", "C-002"], "cited_clause_ids": ["C-002"],
         "event": "reject"},
        # cited nothing → both fired, neither blamed
        {"injected_clause_ids": ["C-001", "C-002"], "cited_clause_ids": [],
         "event": "reject"},
        # cited a clause that was never injected → not blamed (cited ∩ injected)
        {"injected_clause_ids": ["C-001"], "cited_clause_ids": ["C-099"],
         "event": "override"},
    ]

    class _Col:
        def find(self, *a, **kw):
            class _C:
                async def to_list(self, n):
                    return rows
            return _C()

    monkeypatch.setattr(cx, "_col", lambda: _Col())

    counters = asyncio.run(co.aggregate_clause_performance(
        tenant_id="t", app_slug="app", modality="record", task_type="decision"))
    assert counters["C-001"] == {"fired": 3, "blamed": 0}
    assert counters["C-002"] == {"fired": 2, "blamed": 1}
    assert "C-099" not in counters


def test_dissent_is_collected_from_overruled_clauses(monkeypatch):
    """`record_dissent` had no caller until this loop was closed — without it no
    clause could EVER reach `dissented`, so the disagreement-suppression path in
    retrieval was dead code and the summarizer's silent-winner behaviour
    survived in a new form."""
    import consolidation as co
    import corrections as cx

    rows = [
        {"injected_clause_ids": ["C-007"], "cited_clause_ids": ["C-007"],
         "overruled_clause_ids": ["C-007"], "officer": "maria@x", "event": "reject"},
        {"injected_clause_ids": ["C-007"], "cited_clause_ids": ["C-007"],
         "overruled_clause_ids": ["C-007"], "officer": "dan@x", "event": "override"},
        # same officer twice must not look like two dissenters
        {"injected_clause_ids": ["C-007"], "cited_clause_ids": ["C-007"],
         "overruled_clause_ids": ["C-007"], "officer": "dan@x", "event": "reject"},
        # overruling a clause that never fired is not dissent about it
        {"injected_clause_ids": ["C-008"], "cited_clause_ids": [],
         "overruled_clause_ids": ["C-999"], "officer": "z@x", "event": "reject"},
    ]

    class _Col:
        def find(self, *a, **kw):
            class _C:
                async def to_list(self, n):
                    return rows
            return _C()

    monkeypatch.setattr(cx, "_col", lambda: _Col())

    counters = asyncio.run(co.aggregate_clause_performance(
        tenant_id="t", app_slug="app", modality="record", task_type="decision"))
    assert counters["C-007"]["dissenters"] == {"maria@x", "dan@x"}
    assert "dissenters" not in counters["C-008"]
    assert "C-999" not in counters
