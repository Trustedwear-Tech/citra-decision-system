# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""
Docker scheduler for the Action Sandbox Host.

Owns the local Docker daemon. Turns SpawnRequest → running container →
SpawnResponse. Exposes a small async API consumed by main.py (the FastAPI
endpoints).

This module deliberately contains NO HTTP code — that belongs in main.py —
so it is easy to unit-test against a Docker daemon fixture.
"""
from __future__ import annotations

import asyncio
import io
import logging
import random
import tarfile
import time
from datetime import datetime
from typing import Iterable, Set

import docker
import httpx
from docker.errors import APIError, DockerException, NotFound

from config import HostConfig, get_config
from models import (
    CapacityResponse,
    SessionStatusResponse,
    SpawnRequest,
    SpawnResponse,
    TierCapacity,
)
from tiers import CATALOG as TIER_CATALOG, Tier, get_tier

logger = logging.getLogger(__name__)


class Scheduler:
    """Manages the lifecycle of sandbox containers on THIS host."""

    def __init__(self, cfg: HostConfig | None = None) -> None:
        self._cfg = cfg or get_config()
        self._client: docker.DockerClient | None = None
        # Tracks session_id → container_id so /session/{session_id}/...
        # handlers can address the right container without scanning Docker.
        self._sessions: dict[str, str] = {}
        self._lock = asyncio.Lock()

    # ---------------------------------------------------------- docker
    def _docker(self) -> docker.DockerClient:
        if self._client is None:
            # from_env() picks up DOCKER_HOST / socket path naturally.
            self._client = docker.from_env()
        return self._client

    # ---------------------------------------------------------- helpers
    def _pick_host_port(self, in_use: Iterable[int]) -> int:
        """Random free port in the configured range, avoiding collisions.

        Races against `docker run` which also assigns host ports — but we
        hold `self._lock` while choosing + launching, so two concurrent
        spawns from THIS scheduler won't collide. Collisions with unrelated
        host services still surface as Docker errors and we retry.
        """
        lo, hi = self._cfg.host_port_min, self._cfg.host_port_max
        used: Set[int] = set(in_use)
        # Try a handful of random ports before giving up.
        for _ in range(64):
            port = random.randint(lo, hi)
            if port not in used:
                return port
        raise RuntimeError(
            f"no free host port in {lo}-{hi} after 64 attempts"
        )

    def _list_bound_ports(self) -> Set[int]:
        """Host ports currently bound by our sandboxes."""
        ports: Set[int] = set()
        try:
            client = self._docker()
            for c in client.containers.list(
                filters={"label": "citra.action.kind=sandbox"}
            ):
                binding = (c.attrs.get("NetworkSettings") or {}).get("Ports") or {}
                # binding is {"8090/tcp": [{"HostIp":"0.0.0.0","HostPort":"31055"}]}
                for _pname, bindings in binding.items():
                    for b in bindings or []:
                        try:
                            ports.add(int(b["HostPort"]))
                        except (KeyError, TypeError, ValueError):
                            continue
        except (APIError, DockerException) as e:
            logger.warning("could not enumerate port bindings: %s", e)
        return ports

    def _ephemeral_tmpfs(self, tier: Tier | None = None) -> dict[str, str]:
        """Build the `--tmpfs` map for an ephemeral sandbox.

        Each mount is owned by uid/gid 1500 (the `citra` user inside the
        image), mode 0700, with `nosuid,nodev` to drop the obvious escape
        primitives. Sizes come from the resolved tier; legacy callers (no
        tier) fall back to HostConfig values.
        """
        cfg = self._cfg
        tmpfs_opts = "mode=0700,uid=1500,gid=1500,nosuid,nodev"
        ws_size = tier.tmpfs_workspace_str if tier else cfg.tmpfs_workspace_size
        home_size = tier.tmpfs_home_str if tier else cfg.tmpfs_home_size
        return {
            "/workspace":  f"size={ws_size},{tmpfs_opts}",
            "/home/citra": f"size={home_size},{tmpfs_opts}",
            # Small tmpfs for general scratch (used by some weasyprint /
            # matplotlib temp files). Not security-sensitive.
            "/tmp":        "size=64m,mode=1777",
        }

    # ---------------------------------------------------------- API
    async def spawn(self, req: SpawnRequest) -> SpawnResponse:
        async with self._lock:
            return await asyncio.to_thread(self._spawn_blocking, req)

    def _spawn_blocking(self, req: SpawnRequest) -> SpawnResponse:
        client = self._docker()
        cfg = self._cfg
        tier = get_tier(getattr(req, "tier", None))
        profile = (getattr(req, "profile", None) or "sandbox").lower()

        # Running containers (hard cap). Listed once and reused for the
        # per-profile builder check so we don't double-hit the daemon.
        running_containers = client.containers.list(
            filters={"label": "citra.action.kind=sandbox"}
        )
        running = len(running_containers)
        if running >= cfg.max_concurrent:
            raise RuntimeError(
                f"host at capacity: {running}/{cfg.max_concurrent} running"
            )

        # Per-profile builder cap — reserve (max_concurrent - max_builders)
        # slots for chat sandboxes so a flood of builds can't starve chat.
        if profile == "app-builder" and cfg.max_builders > 0:
            builders = sum(
                1 for c in running_containers
                if ((c.attrs.get("Config") or {}).get("Labels") or {}).get(
                    "citra.action.profile"
                ) == "app-builder"
            )
            if builders >= cfg.max_builders:
                raise RuntimeError(
                    f"host at builder capacity: {builders}/{cfg.max_builders} "
                    "app-builder pods running"
                )

        # Per-tier RAM fit check — homogeneous fleet. Refuse the spawn here
        # if the requested tier wouldn't fit in this VM's free RAM. Pool
        # will retry on another host; if every host says no, the caller
        # gets HTTP 503 → translated to 409 at the action-chat-service layer.
        free_bytes = self._free_ram_bytes_blocking()
        if free_bytes >= 0 and free_bytes < tier.mem_limit_bytes:
            raise RuntimeError(
                f"host at capacity for tier={tier.name}: "
                f"need {tier.mem_limit_bytes} bytes, free {free_bytes} bytes"
            )

        host_port = self._pick_host_port(self._list_bound_ports())
        # Resolve profile -> image. Unknown profiles fall back to sandbox
        # so older callers without a profile field keep working. (`profile`
        # was resolved above for the per-profile capacity check.)
        if profile == "app-builder":
            image = cfg.builder_image
            name_prefix = "citra-builder"
        else:
            image = cfg.sandbox_image
            name_prefix = "citra-action"
        container_name = f"{name_prefix}-{req.session_id[:12]}"
        labels = {
            "citra.action.user": req.user_id,
            "citra.action.session": req.session_id,
            "citra.action.kind": "sandbox",
            "citra.action.profile": profile,
            "citra.action.tier": tier.name,
            "citra.action.mem_bytes": str(tier.mem_limit_bytes),
            "citra.action.host": cfg.public_host,
        }
        labels.update(req.labels or {})

        # Caller-supplied env + per-tier DuckDB tuning. Caller env wins
        # on collision (lets the builder pod override).
        container_env = {
            "DUCKDB_MEMORY_LIMIT": tier.duckdb_memory_limit,
            "DUCKDB_THREADS": str(tier.duckdb_threads),
            "DUCKDB_TEMP_DIRECTORY": cfg.duckdb_temp_directory,
            "DUCKDB_MAX_TEMP_DIRECTORY_SIZE": tier.duckdb_max_temp_directory_size,
            "CITRA_AGENT_TIER": tier.name,
            **dict(req.env),
        }
        run_kwargs = dict(
            image=image,
            name=container_name,
            detach=True,
            environment=container_env,
            network=cfg.sandbox_network,
            # Publish adapter :8090 inside the container to the chosen
            # host port so Citra-Service can reach it from other VMs.
            ports={"8090/tcp": host_port},
            # `host-gateway` is a Docker built-in that resolves to the
            # host's bridge gateway on Docker Desktop AND Linux Docker
            # (≥20.10). Lets the sandbox reach action-chat-service /
            # discovery-service / etc. when they run on the host
            # instead of inside the same docker-compose deployment.
            # No-op when the toolkit URLs already point at sibling
            # containers on the same network.
            #
            # Pin LiteLLM's pricing-table host to 127.0.0.1 so the
            # background fetcher fails INSTANTLY (ECONNREFUSED) instead
            # of waiting 30 seconds for an HTTP timeout. The sandbox
            # has no egress to GitHub anyway.
            #
            # We deliberately do NOT block `openrouter.ai` even though
            # OpenClaw fetches `openrouter.ai/api/v1/models` for the
            # same pricing-table reason — because that's also the host
            # the LLM provider uses (`openrouter.ai/api/v1/chat/...`).
            # Blocking openrouter.ai breaks the agent's model call
            # outright with "LLM request failed: Connection error".
            # The OpenRouter pricing fetch can have its 30s timeout;
            # it's async and non-blocking for the gateway-ready signal.
            extra_hosts={
                "host.docker.internal": "host-gateway",
                "raw.githubusercontent.com": "127.0.0.1",
            },
            # Fully ephemeral: no Docker volumes, only tmpfs.
            # /workspace, /home/citra, /tmp are all RAM-backed and
            # die with the container. Sizes come from the tier.
            tmpfs=self._ephemeral_tmpfs(tier),
            cpu_quota=tier.cpu_quota,
            mem_limit=tier.mem_limit_str,
            pids_limit=tier.pids_limit,
            read_only=True,
            security_opt=["no-new-privileges:true"],
            cap_drop=["ALL"],
            labels=labels,
        )

        try:
            container = client.containers.run(**run_kwargs)
        except APIError as e:
            # 409 Conflict — a previous container with the same
            # deterministic name `citra-action-{session_id[:12]}` is
            # squatting the slot. This happens when the prior container
            # exited with non-zero status (config schema error, OOM,
            # SIGKILL) and Docker keeps the dead record. Force-remove
            # the colliding name and retry once.
            if e.status_code == 409 and "is already in use" in str(e):
                logger.warning(
                    "docker run hit 409 name conflict on %s; "
                    "force-removing stale container and retrying",
                    container_name,
                )
                try:
                    stale = client.containers.get(container_name)
                    stale.remove(force=True)
                except NotFound:
                    pass  # already gone (race) — retry will succeed
                except (APIError, DockerException) as rm_err:
                    raise RuntimeError(
                        f"docker run hit 409 but stale-cleanup also failed: "
                        f"{rm_err}; original 409: {e}"
                    ) from e
                try:
                    container = client.containers.run(**run_kwargs)
                except (APIError, DockerException) as retry_err:
                    raise RuntimeError(
                        f"docker run failed after stale-name cleanup: {retry_err}"
                    ) from retry_err
            else:
                raise RuntimeError(f"docker run failed: {e}") from e
        except DockerException as e:
            raise RuntimeError(f"docker run failed: {e}") from e

        # Attach the approved-egress network (outbound to inference-service etc.).
        if cfg.approved_egress_network:
            try:
                net = client.networks.get(cfg.approved_egress_network)
                net.connect(container)
            except NotFound:
                logger.warning(
                    "approved-egress network %s not found; sandbox isolated",
                    cfg.approved_egress_network,
                )
            except (APIError, DockerException) as e:
                logger.warning("failed to attach approved-egress: %s", e)

        adapter_url = f"http://{cfg.public_host}:{host_port}"

        # Wait for adapter /health before returning — caller should not see
        # an adapter URL that isn't up yet.
        if not self._wait_healthy_blocking(adapter_url):
            # Capture container logs before reaping so we can see why the
            # adapter never came up.
            try:
                tail = container.logs(tail=200).decode("utf-8", errors="replace")
            except Exception:
                tail = "<logs unavailable>"
            logger.error(
                "sandbox %s never became healthy; container logs (tail):\n%s",
                container_name, tail,
            )
            # Reap the corpse.
            try:
                container.stop(timeout=3)
                container.remove(force=True)
            except (APIError, DockerException, NotFound):
                pass
            raise RuntimeError("sandbox failed to become healthy within timeout")

        self._sessions[req.session_id] = container.id
        logger.info(
            "spawned session=%s container=%s port=%d user=%s",
            req.session_id, container_name, host_port, req.user_id,
        )
        return SpawnResponse(
            session_id=req.session_id,
            container_id=container.id,
            container_name=container_name,
            host_port=host_port,
            adapter_url=adapter_url,
            public_host=cfg.public_host,
            tier=tier.name,
        )

    def _wait_healthy_blocking(self, adapter_url: str) -> bool:
        deadline = time.time() + self._cfg.startup_timeout_seconds
        # httpx.Client() blocking, run inside to_thread at call site.
        while time.time() < deadline:
            try:
                r = httpx.get(f"{adapter_url}/health", timeout=2.0)
                if r.status_code == 200:
                    return True
            except httpx.HTTPError:
                pass
            time.sleep(1.0)
        return False

    async def stop(self, session_id: str) -> bool:
        async with self._lock:
            return await asyncio.to_thread(self._stop_blocking, session_id)

    def _stop_blocking(self, session_id: str) -> bool:
        client = self._docker()
        container_id = self._sessions.pop(session_id, None)

        # If we lost memory (restart), look it up by label.
        if container_id is None:
            try:
                matches = client.containers.list(
                    all=True,
                    filters={"label": f"citra.action.session={session_id}"},
                )
                if matches:
                    container_id = matches[0].id
            except (APIError, DockerException):
                pass

        if container_id is None:
            return False

        try:
            c = client.containers.get(container_id)
            c.stop(timeout=5)
            c.remove(force=True)
            return True
        except NotFound:
            return False
        except (APIError, DockerException) as e:
            logger.warning("failed to stop %s: %s", session_id, e)
            return False

    # ---------------------------------------------------------- status
    async def session_status(self, session_id: str) -> "SessionStatusResponse":
        return await asyncio.to_thread(self._session_status_blocking, session_id)

    def _session_status_blocking(self, session_id: str) -> "SessionStatusResponse":
        client = self._docker()
        container_id = self._sessions.get(session_id)
        container = None

        # In-memory map may be empty after a scheduler restart — fall back
        # to the session label that we stamp on every spawn.
        #
        # NotFound is a definitive "the pod is gone" → alive=False is the
        # right answer. Any OTHER Docker error means we CANNOT determine
        # liveness; we must NOT report alive=False (the caller would then
        # spawn a duplicate). Raise instead so the endpoint 503s and the
        # caller fails loud.
        if container_id is not None:
            try:
                container = client.containers.get(container_id)
            except NotFound:
                self._sessions.pop(session_id, None)
                container = None
            except (APIError, DockerException) as e:
                raise RuntimeError(
                    f"docker get({session_id}) failed; liveness indeterminate: {e}"
                ) from e
        if container is None:
            try:
                matches = client.containers.list(
                    all=True,
                    filters={"label": f"citra.action.session={session_id}"},
                )
            except (APIError, DockerException) as e:
                raise RuntimeError(
                    f"docker list for {session_id} failed; liveness indeterminate: {e}"
                ) from e
            if matches:
                container = matches[0]

        if container is None:
            return SessionStatusResponse(session_id=session_id, alive=False)

        docker_status = container.status  # running / exited / created / dead
        alive = docker_status == "running"
        adapter_url = self._adapter_url_for(container) if alive else None
        return SessionStatusResponse(
            session_id=session_id,
            alive=alive,
            container_id=container.id,
            container_name=container.name,
            adapter_url=adapter_url,
            docker_status=docker_status,
        )

    def _adapter_url_for(self, container) -> str | None:
        """Reconstruct the public adapter URL from the container's live
        host-port binding for adapter :8090."""
        binding = (container.attrs.get("NetworkSettings") or {}).get("Ports") or {}
        for b in binding.get("8090/tcp") or []:
            hp = b.get("HostPort")
            if hp:
                return f"http://{self._cfg.public_host}:{int(hp)}"
        return None

    # ---------------------------------------------------------- reaper
    async def reap_orphans(self) -> int:
        """Remove leaked sandbox containers. Returns the number reaped.

        Serialised against spawn/stop via the shared lock so it never
        races a container that's mid-spawn (still becoming healthy)."""
        async with self._lock:
            return await asyncio.to_thread(self._reap_orphans_blocking)

    def _reap_orphans_blocking(self) -> int:
        client = self._docker()
        cfg = self._cfg
        try:
            containers = client.containers.list(
                all=True, filters={"label": "citra.action.kind=sandbox"}
            )
        except (APIError, DockerException) as e:
            logger.warning("reaper: could not list containers: %s", e)
            return 0

        now = time.time()
        reaped = 0
        for c in containers:
            status = c.status
            reason = None
            if status in ("exited", "dead", "created"):
                reason = f"terminal state '{status}'"
            elif status == "running":
                # Age backstops per profile. The owning consumer (smart-app
                # idle sweep for builders, action-chat in-process reaper for
                # chat) does the activity-aware reaping; these ceilings only
                # catch pods whose owner died and left a running orphan.
                profile = (
                    (c.attrs.get("Config") or {}).get("Labels") or {}
                ).get("citra.action.profile")
                if profile == "app-builder":
                    ceiling = cfg.builder_max_age_seconds
                    label = "builder"
                else:
                    ceiling = cfg.sandbox_max_age_seconds
                    label = "sandbox"
                if ceiling > 0:
                    age = self._running_age_seconds(c, now)
                    if age is not None and age > ceiling:
                        reason = f"{label} running {int(age)}s > max {ceiling}s"
            if reason is None:
                continue
            try:
                c.remove(force=True)
                reaped += 1
                # Drop any stale in-memory mapping pointing at this corpse.
                sid = (
                    (c.attrs.get("Config") or {}).get("Labels") or {}
                ).get("citra.action.session")
                if sid:
                    self._sessions.pop(sid, None)
                logger.info("reaper removed %s (%s): %s", c.name, c.id[:12], reason)
            except NotFound:
                pass
            except (APIError, DockerException) as e:
                logger.warning("reaper: failed to remove %s: %s", c.name, e)
        return reaped

    @staticmethod
    def _running_age_seconds(container, now: float) -> float | None:
        """Seconds since the container last started, or None if unknown."""
        started = (container.attrs.get("State") or {}).get("StartedAt")
        if not started or started.startswith("0001-01-01"):
            return None
        try:
            # Docker emits RFC3339 with nanoseconds + 'Z'; trim to micros.
            iso = started.replace("Z", "+00:00")
            if "." in iso:
                head, frac = iso.split(".", 1)
                tz = ""
                for marker in ("+", "-"):
                    if marker in frac:
                        frac, tz = frac.split(marker, 1)
                        tz = marker + tz
                        break
                frac = frac[:6]
                iso = f"{head}.{frac}{tz}"
            return now - datetime.fromisoformat(iso).timestamp()
        except (ValueError, TypeError):
            return None

    async def upload(
        self,
        session_id: str,
        *,
        filename: str,
        data: bytes,
        subdir: str = "uploads",
    ) -> dict:
        return await asyncio.to_thread(
            self._upload_blocking,
            session_id,
            filename=filename,
            data=data,
            subdir=subdir,
        )

    def _upload_blocking(
        self, session_id: str, *, filename: str, data: bytes, subdir: str
    ) -> dict:
        container_id = self._sessions.get(session_id)
        if container_id is None:
            raise RuntimeError(f"no such session: {session_id}")
        client = self._docker()
        safe_name = filename.replace("\\", "/").split("/")[-1]
        if not safe_name:
            raise ValueError("invalid filename")

        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tar:
            info = tarfile.TarInfo(name=f"{subdir.strip('/')}/{safe_name}")
            info.size = len(data)
            info.mode = 0o644
            info.mtime = int(time.time())
            info.uid = 1500
            info.gid = 1500
            tar.addfile(info, io.BytesIO(data))
        buf.seek(0)

        try:
            c = client.containers.get(container_id)
            ok = c.put_archive(path="/workspace", data=buf.read())
        except NotFound as e:
            raise RuntimeError("container is gone") from e
        except (APIError, DockerException) as e:
            raise RuntimeError(f"put_archive failed: {e}") from e
        if not ok:
            raise RuntimeError("put_archive returned false")
        return {
            "filename": safe_name,
            "size": len(data),
            "path": f"/workspace/{subdir.strip('/')}/{safe_name}",
        }

    async def capacity(self) -> CapacityResponse:
        return await asyncio.to_thread(self._capacity_blocking)

    # ---------- RAM accounting -----------------------------------------
    def _total_ram_bytes(self) -> int:
        """Total physical RAM on this VM. -1 when psutil is unavailable
        and no fallback is configured."""
        try:
            import psutil  # type: ignore
            n = int(psutil.virtual_memory().total)
            if n > 0:
                return n
        except ImportError:
            pass
        gb = float(self._cfg.fallback_total_ram_gb or 0)
        if gb > 0:
            return int(gb * 1024 * 1024 * 1024)
        return -1

    def _committed_ram_bytes_blocking(self) -> int:
        """Σ(mem_limit) over running sandbox containers, derived from the
        `citra.action.mem_bytes` label we stamp on spawn. Falls back to
        Docker's HostConfig.Memory for legacy containers without the label."""
        client = self._docker()
        try:
            containers = client.containers.list(
                filters={"label": "citra.action.kind=sandbox"}
            )
        except (APIError, DockerException):
            return 0
        total = 0
        for c in containers:
            labels = (c.attrs.get("Config") or {}).get("Labels") or {}
            n = 0
            try:
                n = int(labels.get("citra.action.mem_bytes") or 0)
            except (TypeError, ValueError):
                n = 0
            if n <= 0:
                # Legacy container — read from Docker host config.
                hc = (c.attrs.get("HostConfig") or {})
                try:
                    n = int(hc.get("Memory") or 0)
                except (TypeError, ValueError):
                    n = 0
            if n > 0:
                total += n
        return total

    def _free_ram_bytes_blocking(self) -> int:
        """Bytes still bookable for new sandbox spawns. -1 if total is unknown."""
        total = self._total_ram_bytes()
        if total < 0:
            return -1
        reserved = int(float(self._cfg.reserved_ram_gb) * 1024 * 1024 * 1024)
        committed = self._committed_ram_bytes_blocking()
        return max(total - reserved - committed, 0)

    def _capacity_blocking(self) -> CapacityResponse:
        client = self._docker()
        try:
            running = len(
                client.containers.list(
                    filters={"label": "citra.action.kind=sandbox"}
                )
            )
        except (APIError, DockerException):
            running = -1

        cpu_pct = -1.0
        mem_pct = -1.0
        try:
            import psutil  # type: ignore

            cpu_pct = float(psutil.cpu_percent(interval=None))
            mem_pct = float(psutil.virtual_memory().percent)
        except ImportError:
            pass

        cfg = self._cfg
        running_n = max(running, 0)
        remaining_slots = max(cfg.max_concurrent - running_n, 0)
        free_ram = self._free_ram_bytes_blocking()

        tier_caps: list[TierCapacity] = []
        for tname, tier in TIER_CATALOG.items():
            if free_ram < 0:
                # Unknown free RAM — fall back to slot count, the pool
                # will retry on other hosts if this one OOMs.
                can = remaining_slots
            else:
                can = min(remaining_slots, free_ram // tier.mem_limit_bytes)
            tier_caps.append(TierCapacity(
                tier=tname,
                can_spawn=int(max(can, 0)),
                mem_limit_bytes=tier.mem_limit_bytes,
            ))

        return CapacityResponse(
            public_host=cfg.public_host,
            running=running_n,
            max_concurrent=cfg.max_concurrent,
            remaining=remaining_slots,
            cpu_pct=cpu_pct,
            mem_pct=mem_pct,
            free_ram_bytes=free_ram,
            tier_capacity=tier_caps,
        )


_scheduler: Scheduler | None = None


def get_scheduler() -> Scheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = Scheduler()
    return _scheduler
