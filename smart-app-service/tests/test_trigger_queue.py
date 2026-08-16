"""Durable trigger-queue dispatch (trigger_queue._process): success → mark_done,
permanent failure → DLQ, transient failure → retry. citra_queue's Stream
internals (XADD/XREADGROUP/XAUTOCLAIM/XACK) are covered by citra-queue's own
tests; here we only verify our handler→outcome mapping.
"""
from __future__ import annotations

import asyncio

import citra_queue as cq
import trigger_queue as tq


def _job() -> cq.Job:
    return cq.Job(id="j1", handler=tq.HANDLER,
                  payload={"slug": "a", "trigger_id": "t", "inputs": {}, "env": "test"})


def _spies(monkeypatch, *, will_retry: bool):
    calls: dict = {}

    async def _running(job):
        calls["running"] = True

    async def _done(job, result):
        calls["done"] = result

    async def _failed(job, err, *, permanent):
        calls["failed"] = {"error": err, "permanent": permanent}
        return will_retry

    monkeypatch.setattr(cq, "mark_running", _running)
    monkeypatch.setattr(cq, "mark_done", _done)
    monkeypatch.setattr(cq, "mark_failed", _failed)
    return calls


def test_success_marks_done(monkeypatch):
    calls = _spies(monkeypatch, will_retry=False)

    async def fire(job):
        return {"ok": True}

    asyncio.run(tq._process(_job(), fire))
    assert calls.get("done") == {"ok": True}
    assert "failed" not in calls


def test_permanent_failure_dead_letters(monkeypatch):
    calls = _spies(monkeypatch, will_retry=False)

    async def fire(job):
        raise cq.JobPermanentFailure("bad input")

    asyncio.run(tq._process(_job(), fire))
    assert calls.get("failed", {}).get("permanent") is True
    assert "done" not in calls


def test_transient_failure_retries(monkeypatch):
    calls = _spies(monkeypatch, will_retry=True)

    async def fire(job):
        raise RuntimeError("redis blip / LLM timeout")

    asyncio.run(tq._process(_job(), fire))
    assert calls.get("failed", {}).get("permanent") is False  # transient
    assert "done" not in calls


def test_enqueue_uses_smartapp_queue(monkeypatch):
    captured: dict = {}

    def _enqueue(handler, payload, *, queue, tenant_id=None):
        captured.update(handler=handler, payload=payload, queue=queue, tenant_id=tenant_id)
        return "job-xyz"

    monkeypatch.setattr(cq, "enqueue", _enqueue)
    jid = tq.enqueue_trigger(slug="s1", trigger_id="t1", inputs={"x": 1},
                             env="prod", source="webhook", tenant_id="ten")
    assert jid == "job-xyz"
    assert captured["queue"] == tq.QUEUE and captured["handler"] == tq.HANDLER
    assert captured["payload"]["slug"] == "s1" and captured["payload"]["env"] == "prod"
    assert captured["tenant_id"] == "ten"
