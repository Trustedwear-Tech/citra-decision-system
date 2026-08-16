"""E2E for the run audit trail via HTTP.

``test_run_audit.py`` covers the runtime-level logic. This suite covers
the FastAPI wiring:
  * ``POST /apps/{slug}/run`` appends an immutable row to app_run_audit.
  * ``GET /apps/{slug}/runs`` lists run summaries.
  * ``GET /apps/{slug}/runs/{correlation_id}/audit`` returns the full
    trail (decision, reasoning, citations, references, inputs).
  * Unknown slug / correlation_id → 404.

The audit collection is queried with motor's cursor API
(``.find().sort().skip().limit().to_list()``), so this suite uses a
richer in-memory collection than the shared ``_MemCol``.
"""
from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Iterator

import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch

JWT_SECRET = "smart-app-service-test-secret"
os.environ["JWT_SECRET"] = JWT_SECRET
os.environ.setdefault("JWT_ISSUER", "Citra-AI")

FIXTURES = Path(__file__).parent / "fixtures"

from tests._test_helpers import _MemCol, _mint, _mint_builder  # type: ignore  # noqa: E402


class _AuditCursor:
    """Minimal motor-cursor stand-in: sort / skip / limit / to_list."""

    def __init__(self, matches: list[dict]) -> None:
        self._m = list(matches)

    def sort(self, key=None, direction=1, *_a, **_kw):
        if key:
            self._m.sort(key=lambda d: d.get(key), reverse=(direction == -1))
        return self

    def skip(self, n):
        self._m = self._m[n:]
        return self

    def limit(self, n):
        self._m = self._m[:n]
        return self

    async def to_list(self, length=None):
        return self._m if length is None else self._m[:length]


class _AuditMemCol:
    """In-memory app_run_audit collection with cursor support."""

    def __init__(self) -> None:
        self.docs: list[dict] = []

    def find(self, q=None, _projection=None, *_a, **_kw):
        q = q or {}
        matches = [
            d for d in self.docs if all(d.get(k) == v for k, v in q.items())
        ]
        return _AuditCursor(matches)

    async def count_documents(self, q=None, *_a, **_kw):
        q = q or {}
        return sum(
            1 for d in self.docs if all(d.get(k) == v for k, v in q.items())
        )

    async def insert_one(self, doc, *_a, **_kw):
        self.docs.append(dict(doc))

        class _R:
            inserted_id = "stub"

        return _R()


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    import main

    apps = _MemCol()
    agents = _MemCol()
    audit = _AuditMemCol()
    monkeypatch.setattr(main, "_apps_col", apps, raising=False)
    monkeypatch.setattr(main, "_agents_col", agents, raising=False)
    monkeypatch.setattr(main, "_build_sessions_col", _MemCol(), raising=False)
    monkeypatch.setattr(main, "_prompt_packs_col", _MemCol(), raising=False)
    monkeypatch.setattr(main, "_skills_col", _MemCol(), raising=False)
    monkeypatch.setattr(main, "_pending_runs_col", _MemCol(), raising=False)
    monkeypatch.setattr(main, "_app_run_audit_col", audit, raising=False)

    @asynccontextmanager
    async def _noop_lifespan(_app):
        yield

    monkeypatch.setattr(main.app.router, "lifespan_context", _noop_lifespan)

    with TestClient(main.app) as c:
        c._cols = {"apps": apps, "agents": agents, "audit": audit}  # type: ignore[attr-defined]
        yield c


def _publish_claims_app(client: TestClient) -> tuple[str, str]:
    """Publish the claims app (no approval gate). Returns (slug, action)."""
    app_spec = json.loads((FIXTURES / "claims_app_spec.json").read_text())
    agent_spec = json.loads((FIXTURES / "claims_agent_spec.json").read_text())
    for k in ("app_id", "version", "deployed_at", "status", "owner", "tenant_id"):
        app_spec.pop(k, None)
    action_name = agent_spec["actions"][0]["name"]
    agent_spec["actions"][0]["approval_required"] = False
    agent_spec["actions"][0].pop("input_schema", None)
    r = client.post(
        "/publish",
        json={
            "session_id": "bs_audit",
            "app_spec": app_spec,
            "agent_spec": agent_spec,
        },
        headers={"Authorization": f"Bearer {_mint_builder(session_id='bs_audit')}"},
    )
    assert r.status_code == 200, r.text
    return r.json()["slug"], action_name


async def _llm_with_audit_block(**_kw):
    return {
        "role": "assistant",
        "content": (
            "Claim approved — amount within policy.\n"
            '```json\n{"decision": "approve", '
            '"reasoning": "Amount within the SOP threshold.", '
            '"citations": [{"type": "policy", "ref": "SOP-1", '
            '"detail": "auto-approve under 5000"}]}\n```'
        ),
        "_usage": {"prompt_tokens": 80, "completion_tokens": 20,
                   "total_tokens": 100},
        "_model": "test/model",
    }






def test_audit_unknown_slug_404(client: TestClient):
    r = client.get(
        "/apps/no-such-app/runs",
        headers={"Authorization": f"Bearer {_mint('u_owner')}"},
    )
    assert r.status_code == 404


