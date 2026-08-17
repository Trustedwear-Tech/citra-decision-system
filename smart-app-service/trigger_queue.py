# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""Durable webhook / Run-now trigger jobs via the shared ``citra_queue`` (Redis
Streams) — the same package citra-workflow + Citra-Worker use.

Why: webhook and Run-now used to fire in-process (synchronous fire / an
``asyncio.create_task``); a restart mid-run lost the work. Routing them through
``citra_queue`` makes them durable: ``XADD`` on accept, ``XREADGROUP`` delivery
parked in the consumer PEL until ``XACK``, ``XAUTOCLAIM`` re-delivery on worker
crash, retry-with-backoff, and a dead-letter stream — all provided by the package.

Replay safety: jobs fire with ``row_key=job_id`` so a redelivered job reuses the
same auto-process idempotency key (no double-commit). See
``commit_auto_process_writes`` in main.py.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable, Dict, Optional

import citra_queue as cq

logger = logging.getLogger(__name__)

QUEUE = "smartapp"
HANDLER = "smartapp_trigger"

# async (job) -> result dict. Raise cq.JobPermanentFailure for a non-retryable
# input error (straight to DLQ); any other exception is transient (retried).
FireFn = Callable[[cq.Job], Awaitable[Dict[str, Any]]]


def enqueue_trigger(
    *,
    slug: str,
    trigger_id: str,
    inputs: Optional[Dict[str, Any]],
    env: str,
    source: str,
    tenant_id: Optional[str] = None,
) -> str:
    """Durably enqueue one trigger fire (XADD). Returns the ``job_id`` — the
    route hands it back so the UI can poll the run outcome."""
    return cq.enqueue(
        HANDLER,
        {
            "slug": slug,
            "trigger_id": trigger_id,
            "inputs": inputs or {},
            "env": env,
            "source": source,
        },
        queue=QUEUE,
        tenant_id=tenant_id,
    )


async def _process(job: "cq.Job", fire_fn: FireFn) -> None:
    await cq.mark_running(job)
    try:
        result = await fire_fn(job)
        await cq.mark_done(job, result)
    except cq.JobPermanentFailure as e:  # non-retryable → DLQ (loud: a give-up)
        logger.error("trigger job %s permanent-failure → DLQ: %s", job.id, e)
        await cq.mark_failed(job, str(e), permanent=True)
    except Exception as e:  # noqa: BLE001 — transient → retry, else DLQ
        retried = await cq.mark_failed(job, str(e), permanent=False)
        if retried:
            logger.warning("trigger job %s failed, will retry: %s", job.id, e)
        else:
            # Exhausted retries → dead-lettered. Loud + full stack to the log file.
            logger.error("trigger job %s exhausted retries → DLQ: %s", job.id, e, exc_info=True)


async def run_trigger_consumer(
    stop_event: asyncio.Event,
    fire_fn: FireFn,
    *,
    reclaim_interval_seconds: int = 300,
) -> None:
    """Drain loop (XREADGROUP) + reclaim loop (XAUTOCLAIM) until ``stop_event``.
    Scoped to the ``smartapp`` queue; mirrors Citra-Worker's worker + reclaim
    loops so behaviour (at-least-once, crash recovery, DLQ) is identical."""
    consumer = cq.default_consumer_name()
    logger.info("🟢 smartapp trigger consumer active (queue=%s, consumer=%s)", QUEUE, consumer)

    async def _drain() -> None:
        while not stop_event.is_set():
            try:
                job = await cq.consume_one([QUEUE], block_seconds=5, consumer=consumer)
                if job is None:
                    continue
                if job.handler != HANDLER:
                    # A foreign handler on OUR queue is a misroute/bug — fail loud:
                    # dead-letter it for inspection (don't silently ack it "done").
                    logger.error(
                        "trigger consumer got foreign handler %r on queue %s (job %s) — dead-lettering",
                        job.handler, QUEUE, job.id,
                    )
                    await cq.mark_failed(job, f"foreign handler {job.handler} on queue {QUEUE}", permanent=True)
                    continue
                await _process(job, fire_fn)
            except Exception as e:  # noqa: BLE001 — a Redis blip or unexpected error
                # must NOT silently kill the consumer: log loud (with stack), retry.
                logger.error("trigger drain iteration failed: %s", e, exc_info=True)
                await asyncio.sleep(1.0)

    async def _reclaim() -> None:
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=reclaim_interval_seconds)
                break  # stopped
            except asyncio.TimeoutError:
                pass
            try:
                for job in await cq.claim_stale([QUEUE], consumer=consumer):
                    if stop_event.is_set():
                        break
                    logger.warning("♻️  re-processing reclaimed trigger job %s (%s)", job.id, job.handler)
                    await _process(job, fire_fn)
            except Exception as e:  # noqa: BLE001 — log loud; the loop continues
                logger.error("trigger reclaim failed: %s", e, exc_info=True)

    try:
        await asyncio.gather(_drain(), _reclaim())
    except Exception:  # noqa: BLE001 — the consumer must never die silently
        logger.error("smartapp trigger consumer exited abnormally", exc_info=True)
        raise
