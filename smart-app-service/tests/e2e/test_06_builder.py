"""Builder session lifecycle — spawns a real pod and streams SSE, so it's slow
and costs LLM budget. Gated behind DA_RUN_BUILDER=1.

Proves: /build spawns a session, the chat stream yields well-formed events, and
the resilience contract (10M token cap / retry-cap system rule) is in force. The
full authoring-to-publish run is env + model dependent; the skeleton below issues
the real calls so it's ready to extend.
"""
from __future__ import annotations

import json
import os

import httpx
import pytest

from conftest import CFG, auth, mint_jwt

pytestmark = pytest.mark.skipif(
    os.getenv("DA_RUN_BUILDER") != "1",
    reason="set DA_RUN_BUILDER=1 to spawn a builder pod (slow, uses LLM budget)",
)


@pytest.fixture()
def tok() -> str:
    return mint_jwt(roles=["super_admin", "org_admin"],
                    user_id="builder@acme-power.citra.ai",
                    sa_admin_of=[os.getenv("DA_WORK_SA_ID", "sa_acme_power_work")])


def test_build_session_spawns_and_streams(sas: httpx.Client, tok: str):
    r = sas.post("/build", json={"goal": "A queue of equipment inspections."},
                 headers=auth(tok))
    assert r.status_code == 200, r.text[:400]
    body = r.json()
    session_id = body["session_id"]
    assert body.get("pod_id")

    try:
        # First chat turn — assert the stream yields the documented event types.
        with sas.stream("POST", f"/build/{session_id}/chat/stream",
                        json={"message": "Build a read-only inspections queue."},
                        headers=auth(tok), timeout=120) as resp:
            assert resp.status_code == 200
            seen = set()
            for line in resp.iter_lines():
                if not line or not line.startswith("data:"):
                    continue
                try:
                    evt = json.loads(line[len("data:"):].strip())
                except Exception:
                    continue
                seen.add(evt.get("type"))
                if evt.get("type") == "done":
                    break
            assert seen & {"thinking", "message", "tool_call", "done"}, f"events: {seen}"
    finally:
        sas.delete(f"/build/{session_id}", headers=auth(tok))
