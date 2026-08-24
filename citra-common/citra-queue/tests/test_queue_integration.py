# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""Real-Redis CONTRACT test for citra_queue — the end-to-end behaviour every
consumer (smart-app triggers, workflow worker) depends on:

  round-trip + ack → retry parks with backoff (off the stream) → not delivered
  before due → promote-when-due redelivers (bumped retries) → exhaust → DLQ →
  permanent failure → DLQ.

This is the guard against a change to citra_queue silently breaking a consumer:
smart-app's own tests MOCK the queue, so only a real-Redis exercise of the public
API catches a contract/mechanics break. Skipped automatically when no Redis is
reachable (local dev without Redis stays green); CI runs it against a redis
service. Uses a unique queue name per run + cleans up.
"""
from __future__ import annotations

import uuid

import pytest

from citra_queue import queue as Q


def _redis_up() -> bool:
    try:
        Q._sync_redis().ping()
        return True
    except Exception:  # noqa: BLE001
        return False


pytestmark = pytest.mark.skipif(not _redis_up(), reason="no Redis reachable")


@pytest.mark.asyncio
async def test_full_durability_contract():
    q = "citest_" + uuid.uuid4().hex[:8]
    rc = await Q._async_redis()

    async def _clean():
        await rc.delete(Q._key_stream(q), Q._key_retry(q), Q._key_dlq(q))

    try:
        await _clean()

        # 1) round-trip + ack: enqueue → consume → done → XACK+XDEL (depth 0)
        jid = Q.enqueue("h", {"n": 1}, queue=q)
        job = await Q.consume_one([q], 3)
        assert job and job.id == jid
        await Q.mark_done(job, {"ok": 1})
        assert await Q.queue_depth(q) == 0
        assert (Q.get_status(jid) or {}).get("status") == "done"

        # 2) transient failure → parked in retry ZSET, OFF the stream
        jid2 = Q.enqueue("h", {"n": 2}, queue=q, max_retries=2)
        job = await Q.consume_one([q], 3)
        assert job and job.id == jid2 and job.retries == 0
        assert await Q.mark_failed(job, "boom", permanent=False) is True
        assert await Q.queue_depth(q) == 0
        assert await rc.zcard(Q._key_retry(q)) == 1

        # 3) NOT delivered while backoff hasn't elapsed
        assert await Q.consume_one([q], 1) is None

        # 4) force it due → promoted back to the stream + redelivered, bumped
        members = await rc.zrange(Q._key_retry(q), 0, -1)
        await rc.zadd(Q._key_retry(q), {members[0]: 0})
        job = await Q.consume_one([q], 3)
        assert job and job.id == jid2 and job.retries == 1

        # 5) second failure exhausts max_retries → DLQ (off stream + retry zset)
        assert await Q.mark_failed(job, "boom2", permanent=False) is False
        assert await Q.queue_depth(q) == 0
        assert await rc.zcard(Q._key_retry(q)) == 0
        assert await Q.dlq_depth(q) == 1

        # 6) permanent failure → DLQ immediately (no retry)
        jid3 = Q.enqueue("h", {"n": 3}, queue=q)
        job = await Q.consume_one([q], 3)
        assert job and job.id == jid3
        assert await Q.mark_failed(job, "bad input", permanent=True) is False
        assert await Q.dlq_depth(q) == 2
    finally:
        await _clean()
        await rc.aclose()
