"""Unit tests for the scheduled memory export (memory_export.py).

Pins the export contract without live Mongo/S3 — the orchestrator's I/O
(fetch / writer / state) is injected, so fakes drive it:
  * JSONL.gz round-trips and encodes datetimes as ISO;
  * incremental advances the per-collection watermark to the MAX updated_at and
    a second run with nothing new writes nothing;
  * snapshot writes every collection (even empty) and never touches the
    incremental watermark;
  * one collection failing is isolated + reported, never aborts the others.
"""
from __future__ import annotations

import asyncio
import pytest
import gzip
import json
from datetime import datetime, timedelta, timezone

import memory_export as mx

T0 = datetime(2026, 7, 6, 12, 0, 0, tzinfo=timezone.utc)


# ── pure helpers ─────────────────────────────────────────────────────────────
def test_serialize_jsonl_gz_roundtrips_with_iso_dates():
    docs = [{"a": 1, "updated_at": T0}, {"a": 2}]
    blob = mx.serialize_jsonl_gz(docs)
    lines = gzip.decompress(blob).decode("utf-8").strip().split("\n")
    assert len(lines) == 2
    assert json.loads(lines[0])["updated_at"] == T0.isoformat()
    assert json.loads(lines[1])["a"] == 2


def test_serialize_empty_is_valid_gzip():
    assert gzip.decompress(mx.serialize_jsonl_gz([])) == b""


def test_key_builders():
    assert mx.batch_key("citra-memory/app", "decision_records", T0) == \
        "citra-memory/app/decision_records/2026/07/06/batch-000.jsonl.gz"
    assert mx.snapshot_key("p", "smartapp_clauses", T0) == \
        "p/snapshot/smartapp_clauses/2026/07/06/full.jsonl.gz"


def test_s3_writer_uses_explicit_export_creds(monkeypatch):
    # The export bucket is customer-owned: an explicit write-only key
    # (MEMORY_EXPORT_ACCESS_KEY/_SECRET_KEY) must build the client, separate from
    # the platform's own S3 identity. boto3 client construction is offline, so
    # this verifies the credential path without any network call.
    boto3 = pytest.importorskip("boto3")
    captured = {}

    def _fake_client(service, **kwargs):
        captured.update(service=service, **kwargs)
        return type("C", (), {"put_object": lambda self, **k: None})()

    monkeypatch.setattr(boto3, "client", _fake_client)
    monkeypatch.setenv("MEMORY_EXPORT_ACCESS_KEY", "AKIA_TEST")
    monkeypatch.setenv("MEMORY_EXPORT_SECRET_KEY", "secret_test")
    monkeypatch.setenv("MEMORY_EXPORT_REGION", "ap-south-1")

    write = mx._s3_writer("customer-bucket")
    assert callable(write)
    assert captured["service"] == "s3"
    assert captured["aws_access_key_id"] == "AKIA_TEST"
    assert captured["aws_secret_access_key"] == "secret_test"
    assert captured["region_name"] == "ap-south-1"


def test_next_watermark_takes_max_else_prev():
    docs = [{"updated_at": T0}, {"updated_at": T0 + timedelta(hours=1)},
            {"created_at": T0 - timedelta(days=1)}]  # falls back to created_at
    assert mx.next_watermark(docs, None) == T0 + timedelta(hours=1)
    assert mx.next_watermark([], T0) == T0                       # no docs → prev
    assert mx.next_watermark([{"x": 1}], T0) == T0               # no ts fields → prev


# ── orchestrator (fakes) ─────────────────────────────────────────────────────
class _Harness:
    """Fake fetch/writer/state for run_export. Seed rows per collection."""

    def __init__(self, rows):
        self.rows = rows                 # {collection: [docs]}
        self.written = {}                # key -> decompressed docs
        self.watermarks = {}             # collection -> datetime

    async def fetch(self, collection, since):
        rows = self.rows.get(collection, [])
        if since is None:
            return list(rows)
        return [r for r in rows if (r.get("updated_at") or r.get("created_at")) > since]

    def writer(self, key, data):
        text = gzip.decompress(data).decode("utf-8").strip()
        self.written[key] = [json.loads(l) for l in text.split("\n")] if text else []

    async def state_get(self, collection):
        return self.watermarks.get(collection)

    async def state_set(self, collection, mark):
        self.watermarks[collection] = mark


def _run(h, **kw):
    return asyncio.run(mx.run_export(
        prefix="p", fetch=h.fetch, writer=h.writer,
        state_get=h.state_get, state_set=h.state_set, now=T0, **kw))


def test_incremental_writes_and_advances_watermark():
    h = _Harness({"decision_records": [
        {"decision_id": "d1", "updated_at": T0 - timedelta(hours=2)},
        {"decision_id": "d2", "updated_at": T0 - timedelta(hours=1)},
    ]})
    rep = _run(h)
    assert rep["mode"] == "incremental"
    assert rep["collections"]["decision_records"]["exported"] == 2
    # watermark advanced to the max updated_at
    assert h.watermarks["decision_records"] == T0 - timedelta(hours=1)
    key = mx.batch_key("p", "decision_records", T0)
    assert [d["decision_id"] for d in h.written[key]] == ["d1", "d2"]
    # collections with nothing are not written
    assert mx.batch_key("p", "item_decision_records", T0) not in h.written


def test_second_incremental_run_exports_only_new():
    h = _Harness({"decision_records": [
        {"decision_id": "d1", "updated_at": T0 - timedelta(hours=2)},
    ]})
    _run(h)                                   # first run exports d1, advances mark
    written_after_first = dict(h.written)
    # a new mutation lands AFTER the watermark
    h.rows["decision_records"].append(
        {"decision_id": "d2", "updated_at": T0 - timedelta(minutes=5)})
    h.written = {}
    rep = _run(h)
    assert rep["collections"]["decision_records"]["exported"] == 1  # only d2
    assert [d["decision_id"] for d in list(h.written.values())[0]] == ["d2"]
    assert written_after_first  # sanity: first run had written something


def test_snapshot_writes_all_collections_and_leaves_watermark():
    h = _Harness({"decision_records": [{"decision_id": "d1", "updated_at": T0}]})
    rep = _run(h, snapshot=True)
    assert rep["mode"] == "snapshot"
    # every collection written, even the empty ones (full reconcile)
    for coll in mx.EXPORT_COLLECTIONS:
        assert mx.snapshot_key("p", coll, T0) in h.written
    # snapshot must NOT advance the incremental watermark
    assert h.watermarks == {}


def test_one_collection_failure_is_isolated():
    class _Boom(_Harness):
        async def fetch(self, collection, since):
            if collection == "item_decision_records":
                raise RuntimeError("mongo down")
            return await super().fetch(collection, since)

    h = _Boom({"decision_records": [{"decision_id": "d1", "updated_at": T0}]})
    rep = _run(h)
    assert rep["collections"]["item_decision_records"] == {"error": True}
    # the healthy collection still exported despite the sibling's failure
    assert rep["collections"]["decision_records"]["exported"] == 1
