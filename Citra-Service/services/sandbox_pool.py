# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""
Warm container pool for the quick-chat sandbox image.

Maintains ``min_size`` long-running ``sleep infinity`` containers that we
reuse across many script runs. A single sandbox invocation is now:

    pool.acquire() ─► put_archive(script + input)
                    ─► exec_run python script.py
                    ─► get_archive(/workspace/output)
                    ─► reset workspace
                    ─► pool.release()

Containers keep all the same security guards as before
(``network_mode=none``, mem/pid/cpu limits, non-root user, tmpfs).

This eliminates the ~1–3s docker-create + start latency on every request
and lets us pre-warm the path at service startup.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
import uuid
from typing import Optional

import docker
from docker.errors import DockerException, NotFound

logger = logging.getLogger(__name__)

SANDBOX_IMAGE = os.getenv("QUICK_CHAT_SANDBOX_IMAGE", "quick-chat-sandbox")
POOL_MIN_SIZE = int(os.getenv("QUICK_CHAT_SANDBOX_POOL_MIN", "1"))
POOL_MAX_SIZE = int(os.getenv("QUICK_CHAT_SANDBOX_POOL_MAX", "4"))
ACQUIRE_TIMEOUT = float(os.getenv("QUICK_CHAT_SANDBOX_ACQUIRE_TIMEOUT", "30"))
# Periodic orphan-reap interval (seconds). 0 disables. Default: 5 minutes.
REAP_INTERVAL = float(os.getenv("QUICK_CHAT_SANDBOX_REAP_INTERVAL", "300"))
# How long warmup waits for the Docker daemon to become reachable before
# giving up (seconds). Covers the common dev case where the service boots a
# few seconds after Docker Desktop and the engine VM isn't accepting API
# calls yet. 0 disables the wait (fail fast).
DAEMON_WAIT_TIMEOUT = float(os.getenv("QUICK_CHAT_SANDBOX_DAEMON_WAIT", "60"))
DAEMON_WAIT_INTERVAL = float(os.getenv("QUICK_CHAT_SANDBOX_DAEMON_WAIT_INTERVAL", "2"))

# Shared role label identifies every quick-chat warm container across all
# worker processes. The per-worker ``citra.owner`` label (set in __init__)
# distinguishes this process's containers from its siblings' so the reaper
# never destroys a live sibling's container.
_WARM_ROLE = "quick-chat-sandbox-warm"
_WARM_ROLE_LABEL = {"citra.role": _WARM_ROLE}


class PooledContainer:
    """Handle returned by :meth:`WarmContainerPool.acquire`."""

    def __init__(self, container, pool: "WarmContainerPool"):
        self.container = container
        self._pool = pool
        self.id = container.id[:12]

    async def release(self) -> None:
        await self._pool.release(self)


class WarmContainerPool:
    """Async-safe pool of long-running sleep containers."""

    _instance: Optional["WarmContainerPool"] = None
    _instance_lock = threading.Lock()

    def __init__(self, min_size: int = POOL_MIN_SIZE, max_size: int = POOL_MAX_SIZE):
        self.min_size = max(0, min_size)
        self.max_size = max(self.min_size, max_size)
        self._idle: "asyncio.Queue[PooledContainer]" = asyncio.Queue()
        self._created = 0
        self._grow_lock = asyncio.Lock()
        self._client = None
        self._reaper_task: Optional[asyncio.Task] = None
        # Unique per process instance. Tags every container this worker
        # creates (``citra.owner`` label) so a multi-worker deployment's
        # reapers don't fight over each other's containers.
        self._worker_id = f"{os.getpid()}-{uuid.uuid4().hex[:8]}"
        # Full ids of containers currently handed out (acquired, not yet
        # released). Protected from the reaper alongside the idle set.
        self._in_use: set = set()

    # ------------------------------------------------------------- singleton --

    @classmethod
    def get(cls) -> "WarmContainerPool":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
        return cls._instance

    def _docker(self):
        if self._client is None:
            self._client = docker.from_env()
        return self._client

    def _wait_for_daemon(self, timeout: float, interval: float) -> bool:
        """Block until ``docker ping`` succeeds or ``timeout`` elapses.

        Runs in a worker thread (called via ``asyncio.to_thread``). Returns
        True once the daemon answers, False if it never did. A half-built
        client is dropped between attempts so a later attempt can rebuild it.
        """
        deadline = time.monotonic() + timeout
        last_err: Optional[Exception] = None
        attempt = 0
        while time.monotonic() < deadline:
            try:
                if self._docker().ping():
                    if attempt:
                        logger.info(
                            f"🔌 [SANDBOX_POOL] Docker daemon ready after {attempt} attempt(s)"
                        )
                    return True
            except Exception as e:  # daemon not up yet / client build failed
                last_err = e
                self._client = None
            attempt += 1
            time.sleep(interval)
        logger.error(
            f"[SANDBOX_POOL] Docker daemon not reachable after {timeout}s: {last_err}"
        )
        return False

    # ------------------------------------------------------------- lifecycle --

    def _create_container_sync(self):
        client = self._docker()
        return client.containers.run(
            image=SANDBOX_IMAGE,
            entrypoint=["sleep", "infinity"],
            detach=True,
            network_mode="none",
            mem_limit="512m",
            cpu_count=1,
            pids_limit=100,
            tmpfs={"/tmp": "size=64m,noexec"},
            user="sandbox",
            working_dir="/workspace",
            name=f"qc-warm-{uuid.uuid4().hex[:8]}",
            labels={**_WARM_ROLE_LABEL, "citra.owner": self._worker_id},
            auto_remove=False,
        )

    async def warmup(self) -> int:
        """Pre-create up to ``min_size`` warm containers. Returns count created."""
        # Wait for the Docker daemon to be reachable. On dev machines the
        # service often boots seconds after Docker Desktop, before the engine
        # VM accepts API calls; without this the first warmup throws and the
        # pool stays empty until something forces a lazy create.
        if DAEMON_WAIT_TIMEOUT > 0:
            ready = await asyncio.to_thread(
                self._wait_for_daemon, DAEMON_WAIT_TIMEOUT, DAEMON_WAIT_INTERVAL
            )
            if not ready:
                logger.error(
                    "❌ [SANDBOX_POOL] Docker daemon not reachable; warm pool "
                    "stays empty (containers will be created lazily on demand)"
                )
                return 0

        # Best-effort: prune any leftover warm containers from a previous run
        # only on the first call, so repeated warmup() calls are idempotent.
        if self._created == 0 and self._idle.empty():
            await asyncio.to_thread(self._reap_orphans, set())

        created_now = 0
        async with self._grow_lock:
            while (
                self._idle.qsize() + created_now < self.min_size
                and self._created < self.max_size
            ):
                try:
                    c = await asyncio.to_thread(self._create_container_sync)
                except DockerException as e:
                    logger.error(f"❌ [SANDBOX_POOL] warmup failed: {e}")
                    break
                self._created += 1
                created_now += 1
                await self._idle.put(PooledContainer(c, self))
                logger.info(
                    f"🔥 [SANDBOX_POOL] warm container ready id={c.id[:12]} "
                    f"(idle={self._idle.qsize()} target_min={self.min_size})"
                )

        # Start the periodic reaper once the first warmup completes. It only
        # destroys *unknown* warm-labelled containers (i.e. orphans from prior
        # crashes), never anything tracked by this pool instance.
        if REAP_INTERVAL > 0 and self._reaper_task is None:
            self._reaper_task = asyncio.create_task(self._periodic_reaper())

        return created_now

    async def acquire(self, timeout: float = ACQUIRE_TIMEOUT) -> PooledContainer:
        # Fast path: take an already-idle container without locking. Skip any
        # that died while idle (e.g. a Docker restart left exited containers in
        # the queue) — discard them and try the next.
        while True:
            try:
                pc = self._idle.get_nowait()
            except asyncio.QueueEmpty:
                break
            if await self._validate_alive(pc):
                self._in_use.add(pc.container.id)
                return pc

        # Slow path: serialize size checks under the grow lock so the
        # ``_created < max_size`` invariant cannot be violated by concurrent
        # acquirers. Re-check the idle queue inside the lock too, since
        # another acquirer could have released between our get_nowait() and
        # acquiring the lock.
        async with self._grow_lock:
            while True:
                try:
                    pc = self._idle.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if await self._validate_alive(pc):
                    self._in_use.add(pc.container.id)
                    return pc

            if self._created < self.max_size:
                try:
                    c = await asyncio.to_thread(self._create_container_sync)
                    self._created += 1
                    logger.info(
                        f"🔥 [SANDBOX_POOL] grew pool: created id={c.id[:12]} "
                        f"(total={self._created}/{self.max_size})"
                    )
                    pc = PooledContainer(c, self)
                    self._in_use.add(pc.container.id)
                    return pc
                except DockerException as e:
                    logger.error(f"❌ [SANDBOX_POOL] grow failed: {e}")

        # Pool is at max; wait for somebody to release, validating liveness of
        # whatever we get and retrying within the remaining deadline.
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise asyncio.TimeoutError("sandbox pool acquire timed out")
            pc = await asyncio.wait_for(self._idle.get(), timeout=remaining)
            if await self._validate_alive(pc):
                self._in_use.add(pc.container.id)
                return pc

    async def release(self, pc: PooledContainer) -> None:
        self._in_use.discard(pc.container.id)
        ok = await asyncio.to_thread(self._reset_workspace, pc.container)
        if not ok:
            logger.warning(
                f"⚠️ [SANDBOX_POOL] reset failed for id={pc.id} — destroying & replacing"
            )
            await asyncio.to_thread(self._destroy, pc.container)
            self._created = max(0, self._created - 1)
            asyncio.create_task(self.warmup())
            return
        await self._idle.put(pc)

    async def shutdown(self) -> None:
        if self._reaper_task is not None:
            self._reaper_task.cancel()
            try:
                await self._reaper_task
            except (asyncio.CancelledError, Exception):
                pass
            self._reaper_task = None

        while not self._idle.empty():
            try:
                pc = self._idle.get_nowait()
            except asyncio.QueueEmpty:
                break
            await asyncio.to_thread(self._destroy, pc.container)
        # Sweep any remaining warm-labelled containers (e.g. ones acquired
        # but not yet released) to avoid leaks on shutdown.
        await asyncio.to_thread(self._reap_orphans, set())
        self._in_use.clear()
        self._created = 0

    # ----------------------------------------------------------------- helpers --

    def _reset_workspace(self, container) -> bool:
        try:
            ec, _ = container.exec_run(
                cmd=[
                    "sh", "-c",
                    "rm -rf /workspace/input /workspace/output /workspace/script.py "
                    "&& mkdir -p /workspace/input /workspace/output",
                ],
                user="sandbox",
            )
            return ec == 0
        except Exception as e:
            logger.warning(f"⚠️ [SANDBOX_POOL] reset_workspace exception: {e}")
            return False

    def _destroy(self, container) -> None:
        cid = getattr(container, "id", "?")
        cid_short = cid[:12] if isinstance(cid, str) else "?"
        try:
            container.stop(timeout=2)
        except NotFound:
            # Already gone — nothing to remove.
            return
        except Exception as e:
            logger.warning(f"[SANDBOX_POOL] stop failed for id={cid_short}: {e}")
        try:
            container.remove(force=True)
        except NotFound:
            return
        except Exception as e:
            # Don't silently swallow — repeated remove failures are how the
            # pool ends up over its declared MAX_SIZE.
            logger.warning(f"[SANDBOX_POOL] remove failed for id={cid_short}: {e}")

    def _is_alive(self, container) -> bool:
        """Return True if ``container`` still exists and is running."""
        try:
            container.reload()
            return getattr(container, "status", None) == "running"
        except NotFound:
            return False
        except Exception as e:
            logger.warning(f"[SANDBOX_POOL] liveness check failed: {e}")
            return False

    async def _validate_alive(self, pc: PooledContainer) -> bool:
        """True if the pooled container is still running; otherwise destroy it
        and decrement the created count so the pool rebuilds capacity."""
        if await asyncio.to_thread(self._is_alive, pc.container):
            return True
        logger.warning(f"⚠️ [SANDBOX_POOL] discarding dead idle container id={pc.id}")
        await asyncio.to_thread(self._destroy, pc.container)
        self._in_use.discard(pc.container.id)
        self._created = max(0, self._created - 1)
        return False

    def _reap_orphans(self, known_ids: set) -> None:
        """Destroy stale warm containers without touching live siblings.

        ``known_ids`` is the set of full container ids this pool currently
        owns (idle or in-use); those are never destroyed. For the rest:

        * containers tagged with THIS worker's ``citra.owner`` are our own
          orphans (e.g. leaked on a crash) and are destroyed in any state;
        * containers owned by another worker (or a previous run) are only
          destroyed when NOT running — a running one belongs to a live
          sibling process and must be left alone.
        """
        try:
            client = self._docker()
            labelled = client.containers.list(
                all=True, filters={"label": f"citra.role={_WARM_ROLE}"},
            )
            orphans = []
            for c in labelled:
                if getattr(c, "id", None) in known_ids:
                    continue
                owner = (getattr(c, "labels", None) or {}).get("citra.owner")
                if owner == self._worker_id:
                    orphans.append(c)        # our own orphan — any state
                elif getattr(c, "status", "") != "running":
                    orphans.append(c)        # other worker's, but not running
                # else: running container owned by a live sibling — leave it.
            if orphans:
                logger.info(
                    f"🧹 [SANDBOX_POOL] reaping {len(orphans)} stale warm container(s) "
                    f"(total_labelled={len(labelled)} known={len(known_ids)} "
                    f"worker={self._worker_id})"
                )
            for c in orphans:
                self._destroy(c)
        except Exception:
            logger.exception("[SANDBOX_POOL] orphan reap failed")

    async def _periodic_reaper(self) -> None:
        """Long-running task that periodically reaps orphan warm containers.

        Only containers NOT currently tracked by this pool are destroyed.
        Idle and in-use containers owned by ``self`` are protected.
        """
        logger.info(
            f"🧹 [SANDBOX_POOL] periodic reaper started (interval={REAP_INTERVAL}s)"
        )
        try:
            while True:
                await asyncio.sleep(REAP_INTERVAL)
                # Snapshot ids the pool currently knows about. We can only
                # see idle ones directly; in-use containers must be inferred
                # from ``_created - idle_count`` so we just protect by id
                # when present in the idle queue. In-use ids are tracked by
                # PooledContainer instances; iterate the queue's internal
                # list non-destructively.
                known: set = set(self._in_use)
                # asyncio.Queue exposes an internal deque — stable enough for
                # a read-only snapshot of idle containers.
                try:
                    for pc in list(self._idle._queue):  # type: ignore[attr-defined]
                        cid = getattr(pc.container, "id", None)
                        if cid:
                            known.add(cid)
                except Exception:
                    pass
                await asyncio.to_thread(self._reap_orphans, known)
        except asyncio.CancelledError:
            logger.info("🧹 [SANDBOX_POOL] periodic reaper stopped")
            raise
        except Exception:
            logger.exception("[SANDBOX_POOL] periodic reaper crashed")
