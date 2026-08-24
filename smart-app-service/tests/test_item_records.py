# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""Unit tests for the per-item multimodal knowledge ledger (item_records.py).

Pin the contract:
  * Write-1 persists model verdicts with artifact identity (disposition=proposed);
  * Write-2 records EVERY disposition — accept AND reject(+reason) AND cancel —
    on the LATEST row, and never drops a judgement when no run row exists;
  * outcome inheritance stamps all items of a correlation;
  * precedent retrieval returns the exact-artifact tier + both neighbor classes,
    and the prompt block excludes the current item from the exact tier;
  * a broken store LOGS and degrades — never raises into the caller (RULE #1:
    loud error-status, not a crash in the analysis/feedback path).

Runs against a purpose-built fake motor collection (only the exact call shapes
item_records uses), monkeypatching item_records._col — no Mongo needed.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

import item_records as ir


# ── fake motor collection (exact call shapes used by item_records) ──────────
def _get_path(doc, path):
    cur = doc
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


_OPS = ("$in", "$gt", "$ne", "$lt")


def _matches(doc, q):
    for k, v in (q or {}).items():
        if k == "$or":
            if not any(_matches(doc, sub) for sub in v):
                return False
            continue
        val = _get_path(doc, k)
        if isinstance(v, dict) and any(op in v for op in _OPS):
            if "$in" in v and val not in v["$in"]:
                return False
            if "$gt" in v and not (val is not None and val > v["$gt"]):
                return False
            if "$lt" in v and not (val is not None and val < v["$lt"]):
                return False
            if "$ne" in v and val == v["$ne"]:
                return False
        elif val != v:
            return False
    return True


class _Cursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def sort(self, key, direction=-1):
        # Supports sort("field", dir) AND sort([(field, dir), ...]) (compound).
        # Stable multi-key: sort by least-significant field first. The
        # (present, value) key keeps None from being compared against a typed
        # value (avoids datetime-vs-str TypeError) — None sorts as absent.
        specs = key if isinstance(key, list) else [(key, direction)]
        for field, dir_ in reversed(specs):
            self._docs.sort(key=lambda d, f=field: (d.get(f) is not None, d.get(f)),
                            reverse=(dir_ == -1))
        return self

    async def to_list(self, n):
        return self._docs[:n]


class FakeCol:
    name = "item_decision_records_fake"

    def __init__(self):
        self.docs = []
        self.indexes = []

    async def create_index(self, keys, **kw):
        self.indexes.append((tuple(keys), kw))

    def find(self, q=None, proj=None):
        rows = [dict(d) for d in self.docs if _matches(d, q)]
        if proj and proj.get("_id") == 0:  # honor the _id exclusion like Mongo
            for r in rows:
                r.pop("_id", None)
        return _Cursor(rows)

    async def find_one(self, q=None, sort=None):
        hits = [d for d in self.docs if _matches(d, q)]
        if sort:
            key, direction = sort[0]
            hits.sort(key=lambda d: d.get(key) or datetime.min.replace(tzinfo=timezone.utc),
                      reverse=(direction == -1))
        return dict(hits[0]) if hits else None

    async def insert_one(self, doc):
        d = dict(doc)
        d.setdefault("_id", f"id{len(self.docs)}")
        self.docs.append(d)

    async def update_one(self, q, update, upsert=False):
        for d in self.docs:
            if _matches(d, q):
                d.update(update.get("$set") or {})
                return
        if upsert:
            d = {**(q or {}), **(update.get("$setOnInsert") or {}), **(update.get("$set") or {})}
            d.setdefault("_id", f"id{len(self.docs)}")
            self.docs.append(d)

    async def update_many(self, q, update):
        matched = 0
        modified = 0
        for d in self.docs:
            if _matches(d, q):
                matched += 1
                before = dict(d)
                d.update(update.get("$set") or {})
                for k in (update.get("$unset") or {}):
                    d.pop(k, None)
                if d != before:
                    modified += 1

        class _R:
            matched_count = matched
            modified_count = modified

        return _R()

    async def count_documents(self, q):
        return sum(1 for d in self.docs if _matches(d, q))

    def aggregate(self, pipeline):
        docs = [dict(d) for d in self.docs]
        for stage in pipeline:
            if "$match" in stage:
                docs = [d for d in docs if _matches(d, stage["$match"])]
            elif "$group" in stage:
                field = stage["$group"]["_id"][1:]  # "$disposition"
                groups = {}
                for d in docs:
                    groups[d.get(field)] = groups.get(d.get(field), 0) + 1
                docs = [{"_id": k, "n": n} for k, n in groups.items()]
        return _Cursor(docs)


@pytest.fixture()
def col(monkeypatch):
    fake = FakeCol()
    monkeypatch.setattr(ir, "_col", lambda: fake)
    ir._indexes_ensured.discard(FakeCol.name)  # fresh ensure per test
    return fake


def _finding(item_id="INS-1-photo", sha="aaa", rec="Fail"):
    return {"item_id": item_id, "item_type": "defect-photo", "modality": "image",
            "subject": "defect close-up", "media_ref": f"ref-{item_id}",
            "content_sha256": sha, "fields": {"severity": "high"},
            "recommendation": rec, "confidence": 0.9, "rationale": "crack visible",
            "rubric_version": "v1"}


# ── Write-1 ──────────────────────────────────────────────────────────────────
def test_persist_findings_proposed(col):
    asyncio.run(ir.persist_item_findings(
        [_finding(), _finding("INS-2-photo", sha="bbb")],
        correlation_id="run-1", slug="app", app_id="a1", tenant_id="t1"))
    assert len(col.docs) == 2
    row = col.docs[0]
    assert row["disposition"] == "proposed"
    assert row["content_sha256"] == "aaa" and row["media_ref"] == "ref-INS-1-photo"
    assert row["task_type"] == "defect-photo" and row["tenant_id"] == "t1"


def test_persist_is_idempotent_per_run(col):
    for _ in range(2):
        asyncio.run(ir.persist_item_findings(
            [_finding()], correlation_id="run-1", slug="app", app_id="a1", tenant_id="t1"))
    assert len(col.docs) == 1  # upsert keyed by (correlation_id, item_id)


# ── Write-2: BOTH classes are knowledge ──────────────────────────────────────
def test_accept_is_recorded_not_dropped(col):
    asyncio.run(ir.persist_item_findings(
        [_finding()], correlation_id="run-1", slug="app", app_id="a1", tenant_id="t1"))
    ok = asyncio.run(ir.record_item_disposition(
        tenant_id="t1", slug="app", item_id="INS-1-photo", modality="image",
        task_type="defect-photo", decision="accept", reason=None, actor="officer@x"))
    assert ok and col.docs[0]["disposition"] == "accept"
    assert col.docs[0]["disposition_actor"] == "officer@x"


def test_reject_reason_lands_on_latest_row(col):
    now = datetime.now(timezone.utc)
    # two runs analyzed the same record-artifact; the review targets the LATEST
    for i, cid in enumerate(("run-old", "run-new")):
        asyncio.run(ir.persist_item_findings(
            [_finding()], correlation_id=cid, slug="app", app_id="a1", tenant_id="t1"))
        col.docs[-1]["created_at"] = now + timedelta(minutes=i)
    asyncio.run(ir.record_item_disposition(
        tenant_id="t1", slug="app", item_id="INS-1-photo", modality="image",
        task_type="defect-photo", decision="reject", reason="photo reused", actor="o"))
    by_cid = {d["correlation_id"]: d for d in col.docs}
    assert by_cid["run-new"]["disposition"] == "reject"
    assert by_cid["run-new"]["disposition_reason"] == "photo reused"
    assert by_cid["run-old"]["disposition"] == "proposed"  # untouched


def test_disposition_without_run_row_creates_minimal_row(col):
    ok = asyncio.run(ir.record_item_disposition(
        tenant_id="t1", slug="app", item_id="legacy-photo", modality="image",
        task_type="defect-photo", decision="cancel", reason=None, actor="o"))
    assert ok and len(col.docs) == 1
    assert col.docs[0]["disposition"] == "cancel" and col.docs[0]["correlation_id"] is None


# ── outcome inheritance ──────────────────────────────────────────────────────
def test_items_inherit_case_outcome(col):
    asyncio.run(ir.persist_item_findings(
        [_finding(), _finding("INS-2-photo", sha="bbb")],
        correlation_id="run-1", slug="app", app_id="a1", tenant_id="t1"))
    n = asyncio.run(ir.stamp_items_outcome("run-1", {"label": "bad", "signal": "reversal"}))
    assert n == 2 and all(d["outcome"]["label"] == "bad" for d in col.docs)


# ── precedent retrieval + prompt ─────────────────────────────────────────────
def test_precedents_exact_and_both_neighbor_classes(col):
    asyncio.run(ir.persist_item_findings(
        [_finding("other-item", sha="aaa")],   # same artifact on ANOTHER item
        correlation_id="run-0", slug="app", app_id="a1", tenant_id="t1"))
    asyncio.run(ir.record_item_disposition(
        tenant_id="t1", slug="app", item_id="other-item", modality="image",
        task_type="defect-photo", decision="reject", reason="stock image", actor="o"))
    asyncio.run(ir.persist_item_findings(
        [_finding("good-item", sha="ccc", rec="Pass")],
        correlation_id="run-2", slug="app", app_id="a1", tenant_id="t1"))
    asyncio.run(ir.record_item_disposition(
        tenant_id="t1", slug="app", item_id="good-item", modality="image",
        task_type="defect-photo", decision="accept", reason=None, actor="o"))

    prec = asyncio.run(ir.fetch_item_precedents(
        tenant_id="t1", slug="app", modality="image", task_type="defect-photo",
        content_sha256="aaa"))
    assert prec["exact"] and prec["exact"][0]["item_id"] == "other-item"
    assert [p["item_id"] for p in prec["accepted"]] == ["good-item"]
    assert [p["item_id"] for p in prec["rejected"]] == ["other-item"]

    block = ir.precedents_to_prompt(prec, current_item_id="INS-1-photo")
    assert "EXACT ARTIFACT" in block and "stock image" in block
    assert "ACCEPTED" in block and "REJECTED" in block


def test_exact_tier_dedupes_by_item_and_prefers_dispositioned(col):
    # the same artifact analyzed on the same OTHER item across two runs: one
    # proposed row + one rejected row must collapse to ONE precedent (the
    # rejected one), not stack as two "prior sightings".
    for cid in ("run-1", "run-2"):
        asyncio.run(ir.persist_item_findings(
            [_finding("other-item", sha="aaa")],
            correlation_id=cid, slug="app", app_id="a1", tenant_id="t1"))
    col.docs[-1]["disposition"] = "reject"
    col.docs[-1]["disposition_reason"] = "reused"
    prec = asyncio.run(ir.fetch_item_precedents(
        tenant_id="t1", slug="app", modality="image", task_type="defect-photo",
        content_sha256="aaa"))
    assert len(prec["exact"]) == 1
    assert prec["exact"][0]["disposition"] == "reject"


def test_indexes_created_on_first_touch(col):
    asyncio.run(ir.persist_item_findings(
        [_finding()], correlation_id="r", slug="app", app_id="a", tenant_id="t"))
    assert col.indexes, "hot-path indexes must be ensured on first use"
    # the Write-1 upsert key must be unique (idempotent retries)
    assert any(kw.get("unique") for _k, kw in col.indexes)


def test_prompt_excludes_current_item_from_exact_tier():
    prec = {"exact": [{"item_id": "me", "recommendation": "Fail"}],
            "accepted": [], "rejected": []}
    assert ir.precedents_to_prompt(prec, current_item_id="me") == ""


# ── "memory fired" counters + metrics stats ──────────────────────────────────
def test_precedents_counts_excludes_current_item():
    prec = {"exact": [{"item_id": "me"}, {"item_id": "other"}],
            "accepted": [{"item_id": "a"}], "rejected": []}
    c = ir.precedents_counts(prec, current_item_id="me")
    assert c == {"exact": 1, "accepted": 1, "rejected": 0, "total": 2}
    # without exclusion the current item's own prior rows would inflate the count
    assert ir.precedents_counts(prec)["exact"] == 2


def test_persist_carries_precedents_used(col):
    f = _finding()
    f["precedents_used"] = {"exact": 1, "accepted": 2, "rejected": 0, "total": 3}
    asyncio.run(ir.persist_item_findings(
        [f], correlation_id="run-1", slug="app", app_id="a1", tenant_id="t1"))
    assert col.docs[0]["precedents_used"]["total"] == 3


def test_ledger_stats_counts(col):
    # 3 rows: one proposed (precedent-grounded), one accepted, one rejected
    # with a settled outcome — stats must count all three axes.
    f1 = _finding("i1", sha="a")
    f1["precedents_used"] = {"exact": 1, "accepted": 0, "rejected": 0, "total": 1}
    f2, f3 = _finding("i2", sha="b"), _finding("i3", sha="c")
    asyncio.run(ir.persist_item_findings(
        [f1, f2, f3], correlation_id="run-1", slug="app", app_id="a1", tenant_id="t1"))
    asyncio.run(ir.record_item_disposition(
        tenant_id="t1", slug="app", item_id="i2", modality="image",
        task_type="defect-photo", decision="accept", reason=None, actor="o"))
    asyncio.run(ir.record_item_disposition(
        tenant_id="t1", slug="app", item_id="i3", modality="image",
        task_type="defect-photo", decision="reject", reason="reused", actor="o"))
    asyncio.run(ir.stamp_items_outcome("run-1", {"label": "bad"}))

    stats = asyncio.run(ir.ledger_stats(tenant_ids=["t1", "t1-alias"], slug="app"))
    assert stats["total"] == 3
    assert stats["by_disposition"] == {"proposed": 1, "accept": 1, "reject": 1}
    assert stats["precedent_grounded"] == 1
    assert stats["with_outcome"] == 3  # outcome inherited by every run item


def test_ledger_stats_empty_inputs_are_safe(col):
    assert asyncio.run(ir.ledger_stats(tenant_ids=[], slug="app"))["total"] == 0
    assert asyncio.run(ir.ledger_stats(tenant_ids=[None], slug="app"))["total"] == 0


def test_ledger_stats_broken_store_never_raises(monkeypatch):
    def _boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(ir, "_col", _boom)
    stats = asyncio.run(ir.ledger_stats(tenant_ids=["t"], slug="s"))
    assert stats == {"total": 0, "by_disposition": {},
                     "precedent_grounded": 0, "with_outcome": 0}


# ── curation: exclude-from-retrieval (never delete) ─────────────────────────
def test_excluded_item_stops_grounding_precedents(col):
    # a rejected artifact grounds precedents…
    asyncio.run(ir.persist_item_findings(
        [_finding("bad-item", sha="aaa")],
        correlation_id="run-0", slug="app", app_id="a1", tenant_id="t1"))
    asyncio.run(ir.record_item_disposition(
        tenant_id="t1", slug="app", item_id="bad-item", modality="image",
        task_type="defect-photo", decision="reject", reason="mistake", actor="o"))
    prec = asyncio.run(ir.fetch_item_precedents(
        tenant_id="t1", slug="app", modality="image", task_type="defect-photo",
        content_sha256="aaa"))
    assert prec["exact"] and prec["rejected"]

    # …until an admin excludes it: gone from BOTH tiers, row still in the ledger
    n = asyncio.run(ir.set_precedent_exclusion(
        tenant_ids=["t1"], slug="app", item_id="bad-item",
        excluded=True, actor="admin@x"))
    assert n == 1
    prec = asyncio.run(ir.fetch_item_precedents(
        tenant_id="t1", slug="app", modality="image", task_type="defect-photo",
        content_sha256="aaa"))
    assert prec["exact"] == [] and prec["rejected"] == []
    row = col.docs[0]
    assert row["retrieval_excluded"] is True
    assert row["retrieval_excluded_by"] == "admin@x"
    assert row["disposition"] == "reject"  # record untouched — curation ≠ deletion

    # lifting the flag restores retrieval and clears the attribution
    asyncio.run(ir.set_precedent_exclusion(
        tenant_ids=["t1"], slug="app", item_id="bad-item",
        excluded=False, actor="admin@x"))
    prec = asyncio.run(ir.fetch_item_precedents(
        tenant_id="t1", slug="app", modality="image", task_type="defect-photo",
        content_sha256="aaa"))
    assert prec["exact"] and prec["rejected"]
    assert "retrieval_excluded_by" not in col.docs[0]


def test_exclusion_touches_all_rows_of_the_item(col):
    for cid in ("run-1", "run-2"):
        asyncio.run(ir.persist_item_findings(
            [_finding("dup-item", sha="aaa")],
            correlation_id=cid, slug="app", app_id="a1", tenant_id="t1"))
    n = asyncio.run(ir.set_precedent_exclusion(
        tenant_ids=["t1"], slug="app", item_id="dup-item",
        excluded=True, actor="a"))
    assert n == 2 and all(d["retrieval_excluded"] for d in col.docs)


def test_exclusion_requires_identity(col):
    assert asyncio.run(ir.set_precedent_exclusion(
        tenant_ids=[], slug="app", item_id="i", excluded=True, actor="a")) == 0
    assert asyncio.run(ir.set_precedent_exclusion(
        tenant_ids=["t"], slug="", item_id="i", excluded=True, actor="a")) == 0


def test_exclusion_returns_matched_not_modified(col):
    # Re-applying an exclusion that is already set MODIFIES nothing but the item
    # EXISTS — must return matched>0 (idempotent OK), never 0 (which the endpoint
    # would 404). Regression for the modified_count vs matched_count bug.
    asyncio.run(ir.persist_item_findings(
        [_finding("x", sha="a")], correlation_id="r", slug="app",
        app_id="a1", tenant_id="t1"))
    first = asyncio.run(ir.set_precedent_exclusion(
        tenant_ids=["t1"], slug="app", item_id="x", excluded=True, actor="a"))
    again = asyncio.run(ir.set_precedent_exclusion(   # no-op modify, still matches
        tenant_ids=["t1"], slug="app", item_id="x", excluded=True, actor="a"))
    assert first == 1 and again == 1
    # a genuinely absent item still returns 0 (→ caller 404)
    assert asyncio.run(ir.set_precedent_exclusion(
        tenant_ids=["t1"], slug="app", item_id="ghost", excluded=True, actor="a")) == 0


def test_exclusion_store_failure_raises(monkeypatch):
    # A curation WRITE must propagate a store failure (RULE #1) so the caller can
    # 503 — NOT return 0 which is indistinguishable from 'no such item' (→ 404).
    def _boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(ir, "_col", _boom)
    with pytest.raises(RuntimeError):
        asyncio.run(ir.set_precedent_exclusion(
            tenant_ids=["t"], slug="app", item_id="x", excluded=True, actor="a"))


# ── browse listing (Memory screen) ───────────────────────────────────────────
def test_list_ledger_rows_newest_first_with_filters_and_cursor(col):
    now = datetime.now(timezone.utc)
    for i in range(3):
        asyncio.run(ir.persist_item_findings(
            [_finding(f"i{i}", sha=f"s{i}")],
            correlation_id=f"run-{i}", slug="app", app_id="a1", tenant_id="t1"))
        col.docs[-1]["created_at"] = now + timedelta(minutes=i)
    asyncio.run(ir.record_item_disposition(
        tenant_id="t1", slug="app", item_id="i1", modality="image",
        task_type="defect-photo", decision="reject", reason="r", actor="o"))

    rows = asyncio.run(ir.list_ledger_rows(tenant_ids=["t1"], slug="app"))
    assert [r["item_id"] for r in rows] == ["i2", "i1", "i0"]  # newest first

    rej = asyncio.run(ir.list_ledger_rows(
        tenant_ids=["t1"], slug="app", disposition="reject"))
    assert [r["item_id"] for r in rej] == ["i1"]

    # paginate via the opaque cursor of the first row → the rest follow
    cur = ir.encode_ledger_cursor(rows[0])
    page2 = asyncio.run(ir.list_ledger_rows(tenant_ids=["t1"], slug="app", cursor=cur))
    assert [r["item_id"] for r in page2] == ["i1", "i0"]

    assert asyncio.run(ir.list_ledger_rows(tenant_ids=[], slug="app")) == []


def test_browse_cursor_never_drops_rows_sharing_a_timestamp(col):
    # A batch persisted in one run shares ONE created_at. A page boundary landing
    # mid-batch must not skip the rest (the created_at<before bug). Compound
    # (created_at,item_id) cursor + tiebreaker prevents the drop.
    ts = datetime.now(timezone.utc)
    for name in ("a", "b", "c", "d"):
        asyncio.run(ir.persist_item_findings(
            [_finding(f"item-{name}", sha=name)],
            correlation_id=f"run-{name}", slug="app", app_id="a1", tenant_id="t1"))
        col.docs[-1]["created_at"] = ts          # ALL four share one timestamp

    page1 = asyncio.run(ir.list_ledger_rows(tenant_ids=["t1"], slug="app", limit=2))
    assert len(page1) == 2
    cur = ir.encode_ledger_cursor(page1[-1])
    page2 = asyncio.run(ir.list_ledger_rows(
        tenant_ids=["t1"], slug="app", limit=2, cursor=cur))
    seen = [r["item_id"] for r in page1] + [r["item_id"] for r in page2]
    # every row appears exactly once across the two same-timestamp pages
    assert sorted(seen) == ["item-a", "item-b", "item-c", "item-d"]
    assert len(set(seen)) == 4


def test_browse_ignores_malformed_cursor(col):
    asyncio.run(ir.persist_item_findings(
        [_finding("i0", sha="a")], correlation_id="r", slug="app",
        app_id="a1", tenant_id="t1"))
    # a garbage cursor must not crash — starts from the top
    rows = asyncio.run(ir.list_ledger_rows(
        tenant_ids=["t1"], slug="app", cursor="not-a-real-cursor"))
    assert [r["item_id"] for r in rows] == ["i0"]


# ── RULE #1 posture: broken store degrades loudly, never raises ──────────────
def test_broken_store_never_raises(monkeypatch):
    def _boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(ir, "_col", _boom)
    asyncio.run(ir.persist_item_findings(
        [_finding()], correlation_id="r", slug="s", app_id="a", tenant_id="t"))
    assert asyncio.run(ir.record_item_disposition(
        tenant_id="t", slug="s", item_id="i", modality="image",
        task_type="tt", decision="accept", reason=None, actor="o")) is False
    assert asyncio.run(ir.stamp_items_outcome("r", {"label": "good"})) == 0
    prec = asyncio.run(ir.fetch_item_precedents(
        tenant_id="t", slug="s", modality="image", task_type="tt"))
    assert prec == {"exact": [], "accepted": [], "rejected": []}
