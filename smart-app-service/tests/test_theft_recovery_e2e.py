"""End-to-end: the theft-recovery scenario, full feature coverage at the
FastAPI layer (one app, one page, one AI trigger).

Exercises every request path the Citra-UI / runtime UI drives for the
trigger-agent recommendation model — NO live Mongo / discovery / LLM (the
runtime agent run is stubbed at the ``execute_run`` seam, the dept-MCP write
at ``call_dept_mcp_execute_action``):

  1. POST /publish — publish a theft app + triage agent + ONE schedule trigger.
     The trigger publishes DEACTIVATED (officer activates later).
  2. GET  /apps/{slug}/ai-triggers — BA sees the trigger control + its config.
  3. PATCH /apps/{slug}/ai-triggers/{id} — BA changes the agent-trigger config
     (retunes the cron AND enables it).
  4. POST /apps/{slug}/ai-triggers/{id}/run — the "Run now" button fires ONE
     case; the agent's recommendation is staged into the officer inbox.
  5. GET  /workflow-staging — the queue panel's data: the staged recommendation
     is present with source="trigger".
  6. POST /apps/{slug}/run/{cid}/approve — the officer approves; the planned
     write is replayed against the dept-MCP and the row goes ``applied``.
"""
from __future__ import annotations

import asyncio

import os
import time
from contextlib import asynccontextmanager
from typing import Iterator
from unittest.mock import patch

import jwt
import pytest
from fastapi.testclient import TestClient

JWT_SECRET = "smart-app-service-test-secret"
os.environ["JWT_SECRET"] = JWT_SECRET
os.environ.setdefault("JWT_ISSUER", "Citra-AI")

from tests._test_helpers import _MemCol  # type: ignore  # noqa: E402

SLUG = "theft-recovery"
TENANT = "bajaj"


def _mint_admin(user_id: str) -> str:
    """A tenant org_admin — can render / edit / approve apps in their org."""
    return jwt.encode(
        {
            "sub": user_id,
            "user_id": user_id,
            "email": f"{user_id}@example.com",
            "tenant_id": TENANT,
            "org_id": TENANT,
            "roles": ["org_admin"],
            "iat": int(time.time()),
            "exp": int(time.time()) + 600,
            "iss": "Citra-AI",
        },
        JWT_SECRET,
        algorithm="HS256",
    )


def _mint_builder_admin(session_id: str = "bs_theft") -> str:
    """A builder-pod token whose owner is an org_admin (may publish at org audience)."""
    return jwt.encode(
        {
            "sub": "u_owner",
            "user_id": "u_owner",
            "email": "u_owner@example.com",
            "scope": "smart-app-builder",
            "session_id": session_id,
            "tenant_id": TENANT,
            "org_id": TENANT,
            "roles": ["org_admin"],
            "iat": int(time.time()),
            "exp": int(time.time()) + 600,
            "iss": "Citra-AI",
        },
        JWT_SECRET,
        algorithm="HS256",
    )


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    import main

    apps = _MemCol()
    agents = _MemCol()
    staging = _MemCol()
    trigger_state = _MemCol()
    monkeypatch.setattr(main, "_apps_col", apps, raising=False)
    monkeypatch.setattr(main, "_agents_col", agents, raising=False)
    monkeypatch.setattr(main, "_build_sessions_col", _MemCol(), raising=False)
    monkeypatch.setattr(main, "_prompt_packs_col", _MemCol(), raising=False)
    monkeypatch.setattr(main, "_skills_col", _MemCol(), raising=False)
    monkeypatch.setattr(main, "_pending_runs_col", _MemCol(), raising=False)
    monkeypatch.setattr(main, "_workflow_staging_col", staging, raising=False)
    monkeypatch.setattr(main, "_trigger_state_col", trigger_state, raising=False)
    monkeypatch.setattr(main, "_app_run_audit_col", _MemCol(), raising=False)
    # Collections the TRIGGER path touches. The manual-run endpoint used to fire
    # inline; now the job goes through _fire_trigger_job, which consults the
    # kill switch and records trigger runs / decisions. Any of these unwired
    # raises "Database not initialised" mid-job, the job dies, and staging stays
    # empty — which reads as "the trigger did not fire" rather than as a missing
    # fixture.
    monkeypatch.setattr(main, "_control_col", _MemCol(), raising=False)
    monkeypatch.setattr(main, "_trigger_runs_col", _MemCol(), raising=False)
    monkeypatch.setattr(main, "_decision_records_col", _MemCol(), raising=False)
    monkeypatch.setattr(main, "_smart_app_records_col", _MemCol(), raising=False)
    monkeypatch.setattr(main, "_dept_query_audit_col", _MemCol(), raising=False)

    # The item ledger lives in item_records, not main, and its _col() needs
    # main._db — which no fixture sets. The approve path reads it to enforce
    # item_review_gate, so without this every approve dies on "Database not
    # initialised". Default is EMPTY (no item findings); _seed_ledger overrides
    # it per-test to stage findings.
    import item_records as _ir

    items = _MemCol()
    monkeypatch.setattr(_ir, "_col", lambda: items)

    @asynccontextmanager
    async def _noop_lifespan(_app):
        yield

    monkeypatch.setattr(main.app.router, "lifespan_context", _noop_lifespan)

    with TestClient(main.app) as c:
        c._cols = {"apps": apps, "agents": agents, "staging": staging, "items": items}  # type: ignore[attr-defined]
        yield c


@pytest.fixture(autouse=True)
def queued_triggers(monkeypatch):
    """Capture trigger jobs instead of losing them to the queue.

    POST /apps/{slug}/ai-triggers/{id}/run is ASYNCHRONOUS now: it durably
    enqueues via citra_queue and returns {"started": true, "job_id": ...}. The
    citra-worker consumer does the firing. It no longer returns "fired" and it
    writes nothing inline — which is why these tests failed with KeyError:
    'fired' and IndexError on staging.docs[0].

    So capture the enqueue here and let each test drain it explicitly with
    _drain(). Draining calls main._fire_trigger_job — the SAME function the
    worker's consumer invokes — so the test still exercises the real firing
    path rather than a reimplementation of it.
    """
    import trigger_queue

    captured: list[dict] = []

    def _fake_enqueue(*, slug, trigger_id, inputs, env, source, tenant_id=None):
        job_id = f"job_{len(captured) + 1}"
        captured.append({
            "id": job_id,
            "payload": {"slug": slug, "trigger_id": trigger_id,
                        "inputs": inputs, "env": env, "source": source,
                        "tenant_id": tenant_id},
        })
        return job_id

    monkeypatch.setattr(trigger_queue, "enqueue_trigger", _fake_enqueue)
    return captured


class _Job:
    """Minimal stand-in for citra_queue.Job — _fire_trigger_job reads .id and
    .payload only (it uses .id as the idempotency row_key)."""

    def __init__(self, d: dict) -> None:
        self.id = d["id"]
        self.payload = d["payload"]


def _drain(queued: list) -> int:
    """Fire every captured job through the real handler. Returns how many ran."""
    import main

    n = 0
    for d in list(queued):
        asyncio.run(main._fire_trigger_job(_Job(d)))
        n += 1
    queued.clear()
    return n


def _app_spec() -> dict:
    return {
        "spec_version": "v0",
        "kind": "app",
        "slug": SLUG,
        "title": "Theft Recovery",
        "description": "Triage suspected meter-tamper theft cases and recommend a recovery action.",
        "agent_id": "theft-triage",
        "audience": "org",
        "data_sources": [
            {"id": "inbox", "type": "workflow_staging", "ref": SLUG},
        ],
        "pages": [
            {
                "id": "queue",
                "path": "/",
                "title": "Theft Cases",
                "panels": [
                    {
                        "id": "recs",
                        "type": "queue",
                        "title": "AI-recommended actions",
                        "data_source": "inbox",
                        "title_column": "llm_recommendation_text",
                        "badge_column": "status",
                        "columns": ["llm_recommendation_text", "status"],
                    }
                ],
            },
            {
                "id": "case",
                "path": "/case",
                "title": "Case Review",
                "panels": [
                    {
                        "id": "review",
                        "type": "detail",
                        "linked_to": "inbox",
                        "sections": [
                            {"type": "fields"},
                            {"type": "approval", "title": "Officer decision"},
                        ],
                    }
                ],
            },
        ],
        "navigation": {"default_page": "queue"},
        "triggers": [
            {
                "id": "nightly_sweep",
                "type": "schedule.cron",
                "action": "triage_case",
                "cron": "0 6 * * *",
                "enabled": False,
                "input_template": {"id": "$row.case_id"},
            }
        ],
    }


def _agent_spec() -> dict:
    return {
        "spec_version": "v0",
        "agent_id": "theft-triage",
        "name": "Theft Triage",
        "system_prompt": (
            "You triage suspected meter-tamper theft cases. For each case, "
            "recommend whether to assign a recovery officer; never commit a "
            "write yourself — the officer approves your recommendation."
        ),
        "actions": [
            {"name": "triage_case", "description": "Triage one theft case and recommend a recovery action."}
        ],
    }


def _publish(client: TestClient) -> None:
    r = client.post(
        "/publish",
        json={"session_id": "bs_theft", "app_spec": _app_spec(), "agent_spec": _agent_spec()},
        headers={"Authorization": f"Bearer {_mint_builder_admin(session_id='bs_theft')}"},
    )
    assert r.status_code == 200, r.text


async def _canned_recommendation(**kwargs):
    """Stub of runtime.execute_run: the agent reasons and PROPOSES a write
    (captured as planned_writes) but commits nothing — exactly what a
    plan-only triggered run returns."""
    from models import RunResponse

    req = kwargs.get("request")
    cid = getattr(req, "correlation_id", None) or "run_theft_1"
    return RunResponse(
        correlation_id=cid,
        status="pending_approval",
        outputs={"text": "Recommend assigning recovery officer ro_patna to THEFT-001."},
        decision="assign_recovery_officer",
        reasoning="Tamper confirmed on DT-Begusarai-12; arrears > threshold; SLA breached.",
        planned_writes=[
            {
                "source_id": "field_operations",
                "dataset_id": "field_operations.theft_cases",
                "action_id": "assign_recovery_officer",
                "payload": {"case_id": "THEFT-001", "officer": "ro_patna"},
                "idempotency_key": "theft-001-assign",
            }
        ],
    )


def test_theft_recovery_full_e2e(client: TestClient, queued_triggers: list):
    # ── 1. Publish: one app + one triage agent + one (deactivated) trigger ──
    _publish(client)

    apps = client._cols["apps"]  # type: ignore[attr-defined]
    assert len(apps.docs) == 1
    assert apps.docs[0]["slug"] == SLUG

    owner_hdr = {"Authorization": f"Bearer {_mint_admin('u_owner')}"}

    # ── 2. BA sees the trigger control + its config (published DEACTIVATED) ──
    r = client.get(f"/apps/{SLUG}/ai-triggers", headers=owner_hdr)
    assert r.status_code == 200, r.text
    triggers = r.json()["triggers"]
    assert len(triggers) == 1
    trig = triggers[0]
    assert trig["id"] == "nightly_sweep"
    assert trig["enabled"] is False  # publish forces deactivated
    assert trig.get("cron") == "0 6 * * *"

    # ── 3. BA changes the agent-trigger config: retune cron AND enable it ──
    r = client.patch(
        f"/apps/{SLUG}/ai-triggers/nightly_sweep",
        json={"cron": "0 */2 * * *", "enabled": True},
        headers=owner_hdr,
    )
    assert r.status_code == 200, r.text
    assert r.json()["trigger"]["cron"] == "0 */2 * * *"
    assert r.json()["trigger"]["enabled"] is True
    # Persisted on the stored app_spec.
    r = client.get(f"/apps/{SLUG}/ai-triggers", headers=owner_hdr)
    assert r.json()["triggers"][0]["enabled"] is True
    assert r.json()["triggers"][0]["cron"] == "0 */2 * * *"

    # ── 4. "Run now": fire ONE case → recommendation staged into the inbox ──
    with patch("trigger_runner.execute_run", new=_canned_recommendation):
        r = client.post(
            f"/apps/{SLUG}/ai-triggers/nightly_sweep/run",
            json={"inputs": {"case_id": "THEFT-001"}},
            headers=owner_hdr,
        )
        assert r.status_code == 200, r.text
        # The endpoint QUEUES the run; the worker fires it. Assert the handoff,
        # then drive the job so the rest of this e2e has a staged recommendation.
        assert r.json()["started"] is True, r.json()
        # Drain INSIDE the patch: the job fires HERE, not at POST time,
        # so the canned recommendation has to still be patched in. Draining
        # after the with-block let a REAL agent run happen.
        assert _drain(queued_triggers) == 1

    # ── 5. The queue panel's data: the AI recommendation is in the inbox ──
    r = client.get(
        f"/workflow-staging?slug={SLUG}",
        headers=owner_hdr,
    )
    assert r.status_code == 200, r.text
    rows = r.json()["rows"]
    assert len(rows) == 1, rows
    row = rows[0]
    assert row["source"] == "trigger"  # precomputed by the trigger (renamed from "workflow")
    assert row["llm_recommendation_text"] == "assign_recovery_officer"
    assert row["planned_writes"][0]["payload"]["officer"] == "ro_patna"
    assert row["status"].startswith("pending")
    cid = f"{row['workflow_execution_id']}:{row['case_natural_key']}"

    # ── 6. Officer approves → the planned write replays → row goes applied ──
    async def _commit_ok(**_kw):
        return {"ok": True, "status": "completed", "result": {"case_id": "THEFT-001"}}

    with patch("proxy_clients.call_dept_mcp_execute_action", new=_commit_ok):
        r = client.post(
            f"/apps/{SLUG}/run/{cid}/approve",
            json={"decision": "approve"},
            headers={"Authorization": f"Bearer {_mint_admin('u_officer')}"},
        )
    assert r.status_code == 200, r.text

    staging = client._cols["staging"]  # type: ignore[attr-defined]
    assert staging.docs[0]["status"] == "applied", staging.docs[0]


def _stage_pending(client: TestClient, queued: list) -> str:
    """Fire the trigger to stage a real recommendation; return its composite cid."""
    with patch("trigger_runner.execute_run", new=_canned_recommendation):
        client.post(
            f"/apps/{SLUG}/ai-triggers/nightly_sweep/run",
            json={"inputs": {"case_id": "THEFT-001"}},
            headers={"Authorization": f"Bearer {_mint_admin('u_owner')}"},
        )
        # Drain INSIDE the patch: the job fires HERE, not at POST time,
        # so the canned recommendation has to still be patched in. Draining
        # after the with-block let a REAL agent run happen.
        _drain(queued)
    row = client._cols["staging"].docs[0]  # type: ignore[attr-defined]
    return f"{row['workflow_execution_id']}:{row['case_natural_key']}"


# ── Server-enforced item_review_gate ─────────────────────────────────────────
# REGRESSION: the gate queried the item ledger on `exec_id` (the pre-":" half),
# but persist_item_findings stamps the FULL composite correlation_id — so the
# query matched nothing, which is indistinguishable from "nothing pending", and
# a headless caller committed straight past the gate (found in prod 2026-07-16).
# These tests seed the ledger with the composite EXACTLY as production does, so a
# regression to `exec_id` makes them fail instead of silently passing.
def _seed_ledger(monkeypatch, cid: str, rows: list[dict]):
    from tests._test_helpers import _MemCol  # type: ignore

    import item_records as ir

    col = _MemCol()
    for r in rows:
        col.docs.append({"correlation_id": cid, "slug": SLUG, **r})
    monkeypatch.setattr(ir, "_col", lambda: col)
    return col


def test_approve_is_BLOCKED_while_an_item_finding_is_unreviewed(
    client: TestClient, queued_triggers: list, monkeypatch: pytest.MonkeyPatch
):
    _publish(client)
    cid = _stage_pending(client, queued_triggers)
    _seed_ledger(monkeypatch, cid, [
        {"item_id": "THEFT-001-photo", "modality": "image", "disposition": "proposed"},
        {"item_id": "THEFT-001-doc", "modality": "document", "disposition": "accept"},
    ])

    async def _commit_ok(**_kw):
        return {"ok": True, "status": "completed"}

    with patch("proxy_clients.call_dept_mcp_execute_action", new=_commit_ok):
        r = client.post(f"/apps/{SLUG}/run/{cid}/approve", json={"decision": "approve"},
                        headers={"Authorization": f"Bearer {_mint_admin('u_officer')}"})
    assert r.status_code == 409, r.text
    assert "item_review_gate=hard" in r.text
    assert "THEFT-001-photo" in r.text          # names the pending item
    # …and NOTHING was committed.
    assert client._cols["staging"].docs[0]["status"] != "applied"  # type: ignore[attr-defined]


def test_approve_RELEASES_once_every_non_case_item_is_dispositioned(
    client: TestClient, queued_triggers: list, monkeypatch: pytest.MonkeyPatch
):
    _publish(client)
    cid = _stage_pending(client, queued_triggers)
    _seed_ledger(monkeypatch, cid, [
        {"item_id": "THEFT-001-photo", "modality": "image", "disposition": "accept"},
        {"item_id": "THEFT-001-doc", "modality": "document", "disposition": "reject"},
        # Fraud case findings are EVIDENCE-only — proposed forever, never gate.
        {"item_id": "THEFT-001-fraud", "modality": "case", "disposition": "proposed"},
    ])

    async def _commit_ok(**_kw):
        return {"ok": True, "status": "completed"}

    with patch("proxy_clients.call_dept_mcp_execute_action", new=_commit_ok):
        r = client.post(f"/apps/{SLUG}/run/{cid}/approve", json={"decision": "approve"},
                        headers={"Authorization": f"Bearer {_mint_admin('u_officer')}"})
    assert r.status_code == 200, r.text
    assert client._cols["staging"].docs[0]["status"] == "applied"  # type: ignore[attr-defined]


def test_reject_is_never_gated(client: TestClient, queued_triggers: list,
                               monkeypatch: pytest.MonkeyPatch):
    # reject/cancel commit nothing, so an unreviewed item must not block them.
    # NB: the reason still has to carry substance — that is a SEPARATE gate
    # (correction quality), and this test exists to prove the ITEM-REVIEW gate
    # does not fire on reject.
    _publish(client)
    cid = _stage_pending(client, queued_triggers)
    _seed_ledger(monkeypatch, cid, [
        {"item_id": "THEFT-001-photo", "modality": "image", "disposition": "proposed"},
    ])
    r = client.post(f"/apps/{SLUG}/run/{cid}/approve",
                    json={"decision": "reject",
                          "note": ("the recovery claim is not warranted because the "
                                   "meter was already replaced under the prior work order")},
                    headers={"Authorization": f"Bearer {_mint_admin('u_officer')}"})
    assert r.status_code == 200, r.text


def test_reject_without_a_substantive_reason_is_refused(
        client: TestClient, queued_triggers: list):
    """The correction-quality gate, distinct from the item-review gate above.

    Rejecting a recommendation is a correction, and a correction the app cannot
    learn from is not worth storing. Enforced server-side because the embed
    bundle runs on the customer's own page — a browser-only rule is advice."""
    _publish(client)
    cid = _stage_pending(client, queued_triggers)
    r = client.post(f"/apps/{SLUG}/run/{cid}/approve",
                    json={"decision": "reject", "note": "not warranted"},
                    headers={"Authorization": f"Bearer {_mint_admin('u_officer')}"})
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["code"] == "correction_reason_too_brief"


def test_cancel_needs_no_reason(client: TestClient, queued_triggers: list):
    """Cancel is the ABSENCE of a judgement ("not now, wrong queue"), not a
    correction. Demanding prose there would manufacture junk evidence from
    people closing a tab."""
    _publish(client)
    cid = _stage_pending(client, queued_triggers)
    r = client.post(f"/apps/{SLUG}/run/{cid}/approve",
                    json={"decision": "cancel"},
                    headers={"Authorization": f"Bearer {_mint_admin('u_officer')}"})
    assert r.status_code == 200, r.text


def test_run_now_works_while_trigger_deactivated(client: TestClient, queued_triggers: list):
    """The BA can test a trigger BEFORE activating it (Run-now uses force_fire).
    No PATCH/enable first — the trigger is still enabled=False."""
    _publish(client)
    owner_hdr = {"Authorization": f"Bearer {_mint_admin('u_owner')}"}

    with patch("trigger_runner.execute_run", new=_canned_recommendation):
        r = client.post(
            f"/apps/{SLUG}/ai-triggers/nightly_sweep/run",
            json={"inputs": {"case_id": "THEFT-002"}},
            headers=owner_hdr,
        )
        assert r.status_code == 200, r.text
        assert r.json()["started"] is True, r.json()
        # Drain INSIDE the patch: the job fires HERE, not at POST time,
        # so the canned recommendation has to still be patched in. Draining
        # after the with-block let a REAL agent run happen.
        assert _drain(queued_triggers) == 1
    staging = client._cols["staging"]  # type: ignore[attr-defined]
    assert len(staging.docs) == 1
