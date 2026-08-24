# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""Tests for the Redis Streams queue (Track 1): job round-trip, ack-on-done,
retry backoff (park in the retry ZSET), and dead-letter on exhaustion. Async
Redis is mocked.
"""
import sys
import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from citra_queue import queue as q
from citra_queue.queue import Job


def _fake_redis():
    r = MagicMock()
    for m in ("hset", "expire", "xack", "xdel", "xadd", "set", "aclose",
              "xgroup_create", "zadd", "eval", "zrange", "zcard", "zrem", "delete"):
        setattr(r, m, AsyncMock())
    return r


def _job(retries=0, max_retries=3):
    return Job(id="j1", handler="workflow.run", payload={"x": 1}, queue="default",
               retries=retries, max_retries=max_retries, stream_id="15-0")


class TestJobRoundTrip:
    def test_to_from_json_preserves_fields_and_stream_id(self):
        j = _job()
        j2 = Job.from_json(j.to_json(), stream_id="99-0")
        assert j2.id == "j1" and j2.handler == "workflow.run"
        assert j2.payload == {"x": 1} and j2.max_retries == 3
        assert j2.stream_id == "99-0"
        # stream_id is NOT serialized into the data blob
        assert "stream_id" not in j.to_json()


class TestMarkDone:
    @pytest.mark.asyncio
    async def test_done_acks_and_deletes_entry(self):
        r = _fake_redis()
        with patch.object(q, "_async_redis", AsyncMock(return_value=r)):
            await q.mark_done(_job(), {"ok": True})
        r.xack.assert_awaited_once()      # removed from PEL
        r.xdel.assert_awaited_once()      # trimmed from stream
        assert r.hset.await_count >= 1    # status=done written


class TestMarkFailed:
    @pytest.mark.asyncio
    async def test_transient_with_retries_left_reenqueues(self):
        r = _fake_redis()
        with patch.object(q, "_async_redis", AsyncMock(return_value=r)):
            will_retry = await q.mark_failed(_job(retries=0), "boom", permanent=False)
        assert will_retry is True
        r.xack.assert_awaited_once()          # ack the current entry
        # Parked in the retry ZSET with a backoff delay — NOT immediately re-XADDed
        # onto the stream (a consumer promotes it once the delay elapses).
        r.zadd.assert_awaited_once()
        r.xadd.assert_not_awaited()
        assert "retry:default" in r.zadd.call_args.args[0]

    @pytest.mark.asyncio
    async def test_permanent_goes_to_dlq(self):
        r = _fake_redis()
        with patch.object(q, "_async_redis", AsyncMock(return_value=r)):
            will_retry = await q.mark_failed(_job(retries=0), "bad input", permanent=True)
        assert will_retry is False
        r.xack.assert_awaited_once()
        r.xadd.assert_awaited_once()
        assert "dlq:default" in r.xadd.call_args.args[0]

    @pytest.mark.asyncio
    async def test_exhausted_retries_goes_to_dlq(self):
        r = _fake_redis()
        # retries=2, max=3 → this failure makes it 3 → no more retries → DLQ
        with patch.object(q, "_async_redis", AsyncMock(return_value=r)):
            will_retry = await q.mark_failed(_job(retries=2, max_retries=3),
                                             "boom", permanent=False)
        assert will_retry is False
        assert "dlq:default" in r.xadd.call_args.args[0]
