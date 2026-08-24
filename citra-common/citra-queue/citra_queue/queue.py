# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""
Redis-backed durable job queue. Producer + consumer in one module.

Why Redis Streams (and not Celery / Dramatiq, nor the old LIST+BLPOP)?
---------------------------------------------------------------------
We already run Redis, so a broker (Kafka/Pulsar/RabbitMQ) would add a second
stateful cluster for an internal-automation workload that doesn't need
partitioned streaming. But the previous LIST + ``BLPOP`` design was a
*destructive read*: once a worker popped a job, a crash mid-run lost it (only a
Mongo reaper noticed, and only to FAIL it). This module uses **Redis Streams
with a consumer group**, which gives true at-least-once delivery:

  * ``XADD`` enqueues; ``XREADGROUP`` delivers a copy and parks the entry in the
    consumer's Pending Entries List (PEL) until it is ``XACK``-ed.
  * A worker that crashes mid-job leaves its entry pending; ``XAUTOCLAIM``
    reclaims entries idle past a visibility timeout and re-delivers them to a
    live worker — so the job is retried, not lost.
  * Permanently-failed jobs go to a Dead-Letter stream instead of TTL-ing away.

Workflow runs must therefore be idempotent on replay — the pre-created
``execution_id`` + per-node idempotency keys provide that.

Wire format
-----------
Redis keys:
  {prefix}citra:worker:stream:<queue>     STREAM  job entries (field 'data')
  {prefix}citra:worker:dlq:<queue>        STREAM  dead-letter (exhausted/permanent)
  {prefix}citra:worker:job:<job_id>       HASH    job state + result (status polling)
  {prefix}citra:worker:job:<job_id>:result STRING signal for blocking await
Consumer group: ``workers`` (shared by all worker processes).
"""
from __future__ import annotations

import json
import logging
import os
import socket
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import redis as _redis
import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

# DEDICATED queue Redis. The durable job queue must NOT share the cache Redis:
# a cache runs an eviction policy (allkeys-lru) that would drop Stream entries
# under memory pressure = silently lost jobs, defeating AOF durability. So the
# queue prefers QUEUE_REDIS_* (a noeviction, AOF-on instance) and only falls
# back to the general REDIS_* when the dedicated vars are unset (single-Redis
# dev). The resolved target is logged once so a mis-route is visible, not silent.
_QUEUE_DEDICATED = bool(os.getenv("QUEUE_REDIS_HOST"))
REDIS_HOST       = os.getenv("QUEUE_REDIS_HOST")     or os.getenv("REDIS_HOST", "localhost")
REDIS_PORT       = int(os.getenv("QUEUE_REDIS_PORT")  or os.getenv("REDIS_PORT", "6379"))
REDIS_DB         = int(os.getenv("QUEUE_REDIS_DB")    or os.getenv("REDIS_DB", "0"))
REDIS_USERNAME   = os.getenv("QUEUE_REDIS_USERNAME")  or os.getenv("REDIS_USERNAME") or None
REDIS_PASSWORD   = os.getenv("QUEUE_REDIS_PASSWORD")  or os.getenv("REDIS_PASSWORD") or None
REDIS_KEY_PREFIX = os.getenv("REDIS_KEY_PREFIX", "")

logger.info(
    "citra-queue → Redis %s:%s/%s [%s]",
    REDIS_HOST, REDIS_PORT, REDIS_DB,
    "dedicated QUEUE_REDIS_*" if _QUEUE_DEDICATED
    else "SHARED REDIS_* — set QUEUE_REDIS_* to a noeviction instance for durability",
)

# Default queue. Workers can subscribe to multiple queues for priority lanes.
DEFAULT_QUEUE = "default"

# Consumer group name — all worker processes join the same group so the queue
# load-balances across them, and each gets its own PEL for crash recovery.
GROUP = "workers"

# Result + state TTL — once a job is terminal, its hash sticks around long
# enough for callers to fetch the result, then auto-evicts.
RESULT_TTL_SECONDS = int(os.getenv("CITRA_WORKER_RESULT_TTL", str(7 * 24 * 3600)))

# Max retries for a transient failure. Permanent failures (raised as
# JobPermanentFailure) bypass retry and go straight to the DLQ.
DEFAULT_MAX_RETRIES = int(os.getenv("CITRA_WORKER_MAX_RETRIES", "3"))

# An in-flight entry idle longer than this is presumed abandoned (worker crash)
# and is reclaimed by XAUTOCLAIM for re-delivery. MUST EXCEED the longest expected
# job runtime, or a slow-but-alive job gets reclaimed and DOUBLE-RUN while the
# first execution is still finalizing. citra-workflow's EXECUTION_TIMEOUT is 3600s,
# so the previous 3600000ms (== that timeout, not >) left a double-run window at
# the boundary. Default now 70min (= 3600s exec + 600s stuck-grace margin); raise
# further if you raise EXECUTION_TIMEOUT_SECONDS.
CLAIM_MIN_IDLE_MS = int(os.getenv("CITRA_WORKER_CLAIM_MIN_IDLE_MS", str(70 * 60 * 1000)))

# Cap the dead-letter stream so it can't grow without bound (approximate trim).
DLQ_MAXLEN = int(os.getenv("CITRA_WORKER_DLQ_MAXLEN", "10000"))

# Retry backoff. A transiently-failed job is parked in a per-queue retry ZSET
# (score = ready-at epoch) with an exponential, jittered, capped delay instead
# of going straight back onto the stream — so a fast-failing dependency can't
# burn all retries in milliseconds and dead-letter prematurely. Consumers
# promote due entries back to the stream on each read (see _promote_due_retries).
RETRY_BACKOFF_BASE_SECONDS = float(os.getenv("CITRA_WORKER_RETRY_BACKOFF_BASE", "5"))
RETRY_BACKOFF_CAP_SECONDS = float(os.getenv("CITRA_WORKER_RETRY_BACKOFF_CAP", "300"))
RETRY_BACKOFF_JITTER = float(os.getenv("CITRA_WORKER_RETRY_BACKOFF_JITTER", "0.2"))


def _key_stream(queue: str) -> str:
    return f"{REDIS_KEY_PREFIX}citra:worker:stream:{queue}"


def _key_dlq(queue: str) -> str:
    return f"{REDIS_KEY_PREFIX}citra:worker:dlq:{queue}"


def _key_retry(queue: str) -> str:
    """Per-queue ZSET of jobs awaiting a backoff delay (score = ready-at epoch)."""
    return f"{REDIS_KEY_PREFIX}citra:worker:retry:{queue}"


def _retry_delay_seconds(retries: int) -> float:
    """Exponential backoff (base * 2^(retries-1), capped) + jitter for the
    1-based retry attempt that just failed."""
    import random
    raw = min(RETRY_BACKOFF_BASE_SECONDS * (2 ** max(0, retries - 1)), RETRY_BACKOFF_CAP_SECONDS)
    if RETRY_BACKOFF_JITTER > 0 and raw > 0:
        spread = raw * RETRY_BACKOFF_JITTER
        raw = max(0.0, raw + random.uniform(-spread, spread))
    return raw


def _key_job(job_id: str) -> str:
    return f"{REDIS_KEY_PREFIX}citra:worker:job:{job_id}"


def _key_result_signal(job_id: str) -> str:
    return f"{REDIS_KEY_PREFIX}citra:worker:job:{job_id}:result"


def default_consumer_name() -> str:
    """A per-process consumer identity. All worker tasks in one process share
    it; on crash the whole process's PEL is reclaimable in one sweep."""
    return f"{socket.gethostname()}:{os.getpid()}"


@dataclass
class Job:
    """The blob that travels through the queue."""
    id: str
    handler: str
    payload: Dict[str, Any]
    queue: str = DEFAULT_QUEUE
    enqueued_at: float = field(default_factory=time.time)
    retries: int = 0
    max_retries: int = DEFAULT_MAX_RETRIES
    tenant_id: Optional[str] = None
    request_id: Optional[str] = None
    # Stream delivery id of the CURRENT in-flight entry — set on read, used to
    # XACK/XDEL. Not part of the serialized payload.
    stream_id: Optional[str] = field(default=None, compare=False)

    def to_json(self) -> str:
        return json.dumps({
            "id": self.id,
            "handler": self.handler,
            "payload": self.payload,
            "queue": self.queue,
            "enqueued_at": self.enqueued_at,
            "retries": self.retries,
            "max_retries": self.max_retries,
            "tenant_id": self.tenant_id,
            "request_id": self.request_id,
        })

    @staticmethod
    def from_json(blob: str, *, stream_id: Optional[str] = None) -> "Job":
        d = json.loads(blob)
        return Job(
            id=d["id"],
            handler=d["handler"],
            payload=d.get("payload") or {},
            queue=d.get("queue") or DEFAULT_QUEUE,
            enqueued_at=d.get("enqueued_at") or time.time(),
            retries=int(d.get("retries") or 0),
            max_retries=int(d.get("max_retries") or DEFAULT_MAX_RETRIES),
            tenant_id=d.get("tenant_id"),
            request_id=d.get("request_id"),
            stream_id=stream_id,
        )


class JobPermanentFailure(Exception):
    """Raise from a handler to send a job straight to the DLQ without retrying.

    Use for "input was invalid" / "work doesn't make sense" errors that won't
    resolve on retry. Other exceptions are treated as transient (retry up to
    max_retries, then DLQ)."""


# ── Connection helpers ────────────────────────────────────────────────────


def _sync_redis() -> _redis.Redis:
    socket_timeout = max(15, int(os.getenv("REDIS_SOCKET_TIMEOUT", "15")))
    socket_connect_timeout = max(5, int(os.getenv("REDIS_CONNECT_TIMEOUT", "5")))
    return _redis.Redis(
        host=REDIS_HOST, port=REDIS_PORT, db=REDIS_DB,
        username=REDIS_USERNAME, password=REDIS_PASSWORD,
        decode_responses=True,
        socket_timeout=socket_timeout,
        socket_connect_timeout=socket_connect_timeout,
        retry_on_timeout=True,
    )


async def _async_redis() -> aioredis.Redis:
    socket_timeout = max(15, int(os.getenv("REDIS_SOCKET_TIMEOUT", "15")))
    socket_connect_timeout = max(5, int(os.getenv("REDIS_CONNECT_TIMEOUT", "5")))
    return aioredis.Redis(
        host=REDIS_HOST, port=REDIS_PORT, db=REDIS_DB,
        username=REDIS_USERNAME, password=REDIS_PASSWORD,
        decode_responses=True,
        socket_timeout=socket_timeout,
        socket_connect_timeout=socket_connect_timeout,
        retry_on_timeout=True,
        health_check_interval=30,
    )


def _ensure_group_sync(rc: _redis.Redis, stream: str) -> None:
    """Create the consumer group (and the stream) if absent. Idempotent."""
    try:
        rc.xgroup_create(name=stream, groupname=GROUP, id="$", mkstream=True)
    except _redis.ResponseError as exc:
        if "BUSYGROUP" not in str(exc):
            raise


async def _ensure_group_async(rc: aioredis.Redis, stream: str) -> None:
    try:
        await rc.xgroup_create(name=stream, groupname=GROUP, id="$", mkstream=True)
    except aioredis.ResponseError as exc:
        if "BUSYGROUP" not in str(exc):
            raise


# ── Producer (Citra-Service, citra-workflow) ─────────────────────────────


def enqueue(
    handler: str,
    payload: Dict[str, Any],
    *,
    queue: str = DEFAULT_QUEUE,
    tenant_id: Optional[str] = None,
    request_id: Optional[str] = None,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> str:
    """Queue a job durably (XADD). Returns the job_id callers can poll on."""
    job = Job(
        id=uuid.uuid4().hex, handler=handler, payload=payload, queue=queue,
        tenant_id=tenant_id, request_id=request_id, max_retries=max_retries,
    )
    rc = _sync_redis()
    stream = _key_stream(queue)
    _ensure_group_sync(rc, stream)
    pipe = rc.pipeline()
    pipe.hset(_key_job(job.id), mapping={
        "id": job.id, "handler": job.handler, "status": "queued", "queue": queue,
        "enqueued_at": str(job.enqueued_at), "tenant_id": tenant_id or "",
        "request_id": request_id or "",
    })
    pipe.expire(_key_job(job.id), RESULT_TTL_SECONDS)
    pipe.xadd(stream, {"data": job.to_json()})
    pipe.execute()
    logger.info(f"📥 enqueued job {job.id} ({handler}) on stream '{queue}'")
    return job.id


def get_status(job_id: str) -> Optional[Dict[str, Any]]:
    """Read the current state of a job. Returns None if unknown/expired."""
    rc = _sync_redis()
    raw = rc.hgetall(_key_job(job_id))
    if not raw:
        return None
    if "result" in raw:
        try:
            raw["result"] = json.loads(raw["result"])
        except json.JSONDecodeError:
            pass
    return raw


# ── Consumer (Citra-Worker) ───────────────────────────────────────────────


def _parse_entries(entry_list: Any, queue: str) -> List[Job]:
    """Turn a list of (id, {field: val}) stream entries into Jobs."""
    jobs: List[Job] = []
    for msg_id, fields in entry_list or []:
        blob = (fields or {}).get("data")
        if not blob:
            continue
        try:
            jobs.append(Job.from_json(blob, stream_id=msg_id))
        except Exception as exc:  # noqa: BLE001 — poison entry; log + skip
            logger.error("queue: undecodable stream entry %s on %s: %s", msg_id, queue, exc)
    return jobs


# Atomically promote due retries: for each retry-ZSET member with score <= now,
# XADD it back onto the stream then ZREM it — in one script so a crash can't lose
# it (XADD precedes ZREM) and concurrent consumers can't double-promote it.
_PROMOTE_LUA = """
local due = redis.call('ZRANGEBYSCORE', KEYS[1], '-inf', ARGV[1], 'LIMIT', 0, tonumber(ARGV[2]))
for i = 1, #due do
  redis.call('XADD', KEYS[2], '*', 'data', due[i])
  redis.call('ZREM', KEYS[1], due[i])
end
return #due
"""


async def _promote_due_retries(rc, queues: List[str], *, limit: int = 256) -> int:
    """Move retry-ZSET entries whose backoff has elapsed back onto their stream
    (atomic per queue via Lua). Cheap when nothing is due. Returns count promoted."""
    now = time.time()
    promoted = 0
    for q in queues:
        try:
            n = await rc.eval(_PROMOTE_LUA, 2, _key_retry(q), _key_stream(q), str(now), str(limit))
            promoted += int(n or 0)
        except Exception as exc:  # noqa: BLE001 — never let promotion break the read path
            logger.error("queue: promote due retries failed for %s: %s", q, exc, exc_info=True)
    if promoted:
        logger.info("queue: promoted %d due retr%s back to stream", promoted, "y" if promoted == 1 else "ies")
    return promoted


async def consume_one(
    queues: List[str],
    block_seconds: int = 5,
    consumer: Optional[str] = None,
) -> Optional[Job]:
    """Read one NEW job via XREADGROUP across the given queues (priority order).

    The entry is delivered to ``consumer`` and parked in its PEL until the job
    is acked (on done) or re-delivered (on crash). Returns a Job or None on
    timeout."""
    consumer = consumer or default_consumer_name()
    rc = await _async_redis()
    try:
        streams = {}
        for q in queues:
            stream = _key_stream(q)
            await _ensure_group_async(rc, stream)
            streams[stream] = ">"
        # Promote any retries whose backoff has elapsed back onto the stream so
        # the XREADGROUP below picks them up (delayed retry, no busy-loop).
        await _promote_due_retries(rc, queues)
        resp = await rc.xreadgroup(
            groupname=GROUP, consumername=consumer,
            streams=streams, count=1, block=block_seconds * 1000,
        )
    finally:
        await rc.aclose()
    if not resp:
        return None
    for stream_key, entries in resp:
        queue = stream_key.rsplit(":", 1)[-1]
        jobs = _parse_entries(entries, queue)
        if jobs:
            return jobs[0]
    return None


async def claim_stale(
    queues: List[str],
    consumer: Optional[str] = None,
    *,
    min_idle_ms: int = CLAIM_MIN_IDLE_MS,
    count: int = 16,
) -> List[Job]:
    """Reclaim entries abandoned by a crashed worker (idle past ``min_idle_ms``)
    via XAUTOCLAIM and return them as Jobs assigned to ``consumer`` for
    re-processing. This is the true at-least-once safety net."""
    consumer = consumer or default_consumer_name()
    rc = await _async_redis()
    claimed: List[Job] = []
    dead_lettered = 0
    try:
        # Promote due retries here too, so a quiet queue (no new reads driving
        # consume_one) still drains its backoff-parked jobs via the reclaim loop.
        await _promote_due_retries(rc, queues)
        for q in queues:
            stream = _key_stream(q)
            await _ensure_group_async(rc, stream)
            # XAUTOCLAIM returns (next_cursor, [(id, fields)...], deleted_ids)
            res = await rc.xautoclaim(
                name=stream, groupname=GROUP, consumername=consumer,
                min_idle_time=min_idle_ms, start_id="0-0", count=count,
            )
            entries = res[1] if isinstance(res, (list, tuple)) and len(res) >= 2 else []
            # A crash that kills the WORKER PROCESS (OOM/segfault) is not a handler
            # exception, so mark_failed never runs and retries never increment — a
            # job that reliably crashes its worker would be reclaimed and re-run
            # FOREVER, never reaching the DLQ. Redis tracks a per-entry delivery
            # count in the PEL (XAUTOCLAIM bumps it); use it to dead-letter a
            # crash-looping job once it has been delivered more than max_retries.
            try:
                pend = await rc.xpending_range(
                    name=stream, groupname=GROUP, min="-", max="+", count=count,
                )
                delivered = {p["message_id"]: int(p.get("times_delivered") or 1) for p in (pend or [])}
            except Exception as exc:  # noqa: BLE001 — never let the count probe break reclaim
                logger.warning("queue: xpending probe failed on %s: %s", q, exc)
                delivered = {}
            for job in _parse_entries(entries, q):
                times = delivered.get(job.stream_id, 1)
                if times > max(1, job.max_retries):
                    # Crash-looping past the retry budget → dead-letter + ack so it
                    # stops being re-delivered. This is the at-least-once safety net
                    # for failures that bypass the handler-level retry path.
                    try:
                        await rc.xadd(
                            _key_dlq(q),
                            {"data": job.to_json(),
                             "error": f"reclaimed {times}x (worker-crash loop) > max_retries={job.max_retries}",
                             "failed_at": str(time.time())},
                            maxlen=DLQ_MAXLEN, approximate=True,
                        )
                        await rc.xack(stream, GROUP, job.stream_id)
                        await rc.xdel(stream, job.stream_id)
                        await rc.hset(_key_job(job.id), mapping={
                            "status": "failed", "retries": str(times),
                            "last_error": "worker-crash loop exceeded max_retries",
                            "finished_at": str(time.time()),
                        })
                        await rc.set(_key_result_signal(job.id), "1", ex=60)
                        dead_lettered += 1
                        logger.error(
                            "queue: dead-lettered crash-looping job %s on %s "
                            "(delivered %dx > max_retries=%d)",
                            job.id, q, times, job.max_retries,
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.error("queue: failed to DLQ crash-loop job %s: %s", job.id, exc)
                        claimed.append(job)  # fall back to re-delivery rather than drop
                    continue
                claimed.append(job)
    finally:
        await rc.aclose()
    if claimed or dead_lettered:
        logger.warning(
            "queue: reclaimed %d stale job(s) for re-delivery (%d dead-lettered as crash-loops)",
            len(claimed), dead_lettered,
        )
    return claimed


async def queue_depth(queue: str) -> int:
    """Number of entries currently in the stream (XLEN — pending + unread)."""
    rc = await _async_redis()
    try:
        return int(await rc.xlen(_key_stream(queue)))
    finally:
        await rc.aclose()


async def dlq_depth(queue: str) -> int:
    """Number of dead-lettered jobs awaiting inspection/replay. Returns 0 when
    the DLQ stream doesn't exist yet (XLEN on a missing key is 0); any OTHER
    Redis error is logged loud — never silently reported as 0, which would hide
    a backlog from the monitoring gauge."""
    rc = await _async_redis()
    try:
        return int(await rc.xlen(_key_dlq(queue)))
    except aioredis.ResponseError as exc:
        logger.warning("queue: dlq_depth(%s) failed: %s", queue, exc)
        return 0
    finally:
        await rc.aclose()


async def mark_running(job: Job) -> None:
    rc = await _async_redis()
    try:
        await rc.hset(_key_job(job.id), mapping={
            "status": "running", "retries": str(job.retries),
            "started_at": str(time.time()),
        })
        await rc.expire(_key_job(job.id), RESULT_TTL_SECONDS)
    finally:
        await rc.aclose()


async def _ack(rc: aioredis.Redis, job: Job) -> None:
    """XACK + XDEL the current in-flight entry so the stream stays bounded."""
    if not job.stream_id:
        return
    stream = _key_stream(job.queue)
    try:
        await rc.xack(stream, GROUP, job.stream_id)
        await rc.xdel(stream, job.stream_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("queue: ack/del failed for %s on %s: %s", job.stream_id, job.queue, exc)


async def mark_done(job: Job, result: Any) -> None:
    rc = await _async_redis()
    try:
        await rc.hset(_key_job(job.id), mapping={
            "status": "done", "finished_at": str(time.time()),
            "result": json.dumps(result, default=str),
        })
        await rc.expire(_key_job(job.id), RESULT_TTL_SECONDS)
        await _ack(rc, job)
        await rc.set(_key_result_signal(job.id), "1", ex=60)
    finally:
        await rc.aclose()


async def mark_failed(job: Job, error: str, *, permanent: bool) -> bool:
    """Record a failure. For a transient error with retries left, ack the
    current entry and park a fresh copy in the retry ZSET with an exponential
    backoff delay (promoted back to the stream once the delay elapses). Otherwise
    ack and push to the dead-letter stream. Returns True if the job will retry,
    False if it was dead-lettered."""
    job.retries += 1
    will_retry = (not permanent) and job.retries < job.max_retries

    rc = await _async_redis()
    try:
        await rc.hset(_key_job(job.id), mapping={
            "status": "queued" if will_retry else "failed",
            "retries": str(job.retries),
            "last_error": error[:1000],
            "finished_at": "" if will_retry else str(time.time()),
        })
        await rc.expire(_key_job(job.id), RESULT_TTL_SECONDS)
        # Remove the current in-flight entry from the PEL first…
        await _ack(rc, job)
        if will_retry:
            # …then park it in the retry ZSET with an exponential backoff delay.
            # A consumer promotes it back onto the stream once the delay elapses
            # (see _promote_due_retries) — so a fast-failing dependency can no
            # longer exhaust all retries in milliseconds and dead-letter early.
            ready_at = time.time() + _retry_delay_seconds(job.retries)
            await rc.zadd(_key_retry(job.queue), {job.to_json(): ready_at})
        else:
            # Dead-letter: keep it for inspection/replay (does not TTL away).
            await rc.xadd(
                _key_dlq(job.queue),
                {"data": job.to_json(), "error": error[:1000],
                 "failed_at": str(time.time())},
                maxlen=DLQ_MAXLEN, approximate=True,
            )
            await rc.set(_key_result_signal(job.id), "1", ex=60)
    finally:
        await rc.aclose()
    return will_retry
