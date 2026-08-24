# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""
Configuration for the Action Sandbox Host scheduler.

This service runs on each sandbox-host VM. It owns the local Docker daemon
and spawns ephemeral agent-sandbox containers (citra-agent-sandbox-base,
or builder pods like citra-app-builder that layer on top of it) on behalf
of the stateless Citra-Service / smart-app-service fleet.

All settings are env-driven so deployment is `docker compose up` with an env
file â€” no code changes between dev, staging, and prod.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class HostConfig:
    # ---- identity -----------------------------------------------------
    # Publicly-advertised host that OTHER VMs (Citra-Service replicas) use to
    # reach sandboxes spawned here. MUST be a name/IP reachable from those
    # replicas â€” loopback won't work in a multi-VM deployment.
    public_host: str = os.getenv("SANDBOX_HOST_PUBLIC_HOST", "sandbox-host-1")

    # Listen address for this scheduler's own FastAPI service.
    listen_host: str = os.getenv("SANDBOX_HOST_LISTEN_HOST", "0.0.0.0")
    listen_port: int = _int("SANDBOX_HOST_LISTEN_PORT", 7090)

    # ---- auth ---------------------------------------------------------
    # Shared secret between Citra-Service and every sandbox-host. Every
    # protected endpoint requires `X-Sandbox-Host-Secret: <this>`.
    shared_secret: str = os.getenv("SANDBOX_HOST_SECRET", "")

    # ---- capacity -----------------------------------------------------
    # Hard cap on concurrent sandboxes per host. /capacity advertises
    # remaining = max_concurrent - currently running.
    max_concurrent: int = _int("SANDBOX_HOST_MAX_CONCURRENT", 20)

    # Per-profile cap on `app-builder` pods. Both consumers (action-chat
    # sandboxes and smart-app builder pods) draw from the SAME
    # max_concurrent budget, so a burst of builds can otherwise starve
    # chat (and vice-versa). Setting this > 0 reserves
    # (max_concurrent - max_builders) slots for chat sandboxes: a spawn
    # with profile=app-builder is refused (503 → caller retries / fails
    # loud) once `max_builders` builders are already running. 0 disables
    # the split (builders bounded only by max_concurrent — legacy
    # behaviour).
    max_builders: int = _int("SANDBOX_HOST_MAX_BUILDERS", 0)

    # Refuse to start if free-RAM accounting is unavailable (psutil missing
    # AND fallback_total_ram_gb unset) UNLESS this is explicitly set. Without
    # accounting the per-tier RAM fit check silently no-ops and the host will
    # overcommit RAM up to max_concurrent (e.g. 20 × 8 GB Heavy = 160 GB).
    # Fail loud by default; operators who really want slot-cap-only must opt
    # in. See get_config() for the startup assertion.
    allow_unbounded_ram: bool = _bool("SANDBOX_HOST_ALLOW_UNBOUNDED_RAM", False)

    # ---- container defaults ------------------------------------------
    # The two consumer images both layer on the (neutral)
    # citra-agent-sandbox-base. Each owns its own persona under
    # /srv/citra/workspace-seed/. Built by:
    #   - action-chat-service/sandbox/Dockerfile
    #   - smart-app-service/builder-sandbox/Dockerfile
    # The base image is NOT spawned directly in production â€” running it
    # bare would mean no persona, no skills, no useful agent identity.
    # `or default` (not getenv default) so an env var set to EMPTY string
    # falls back to the image tag instead of blanking it â€” an empty image
    # makes docker create fail with "no command specified".
    sandbox_image: str = (
        os.getenv("SANDBOX_HOST_IMAGE") or "citra-action-chat-sandbox:latest"
    )
    # Builder profile (used by smart-app-service to spawn ephemeral
    # builder pods that turn a BA goal into AppSpec+AgentSpec JSON).
    builder_image: str = (
        os.getenv("SANDBOX_HOST_BUILDER_IMAGE") or "citra-app-builder:latest"
    )

    # Primary (internal, no internet) network the sandbox joins.
    sandbox_network: str = os.getenv(
        "SANDBOX_HOST_SANDBOX_NETWORK", "citra-action-egress"
    )
    # Secondary network that gives the sandbox approved egress (discovery,
    # inference, citra-service internal). Set to empty string to disable.
    approved_egress_network: str = os.getenv(
        "SANDBOX_HOST_APPROVED_EGRESS_NETWORK", "citra-action-approved-egress"
    )

    # Resource limits â€” HOMOGENEOUS FLEET MODE.
    #
    # The legacy `mem_limit` / `cpu_quota` / `tmpfs_*` knobs below are now
    # the FALLBACK for any spawn that doesn't specify a tier (back-compat).
    # The primary path is per-spawn tier lookup via tiers.CATALOG â€” every
    # VM can serve every tier, and the pool picks a host with enough free
    # RAM at request time. See tiers.py.
    #
    # Why both: legacy callers (older Citra-Service replicas, the smart-app
    # builder pod, e2e tests) still send `profile="sandbox"` with no tier;
    # they should keep working at the historical Quick-tier sizing.
    cpu_quota: int = _int("SANDBOX_HOST_CPU_QUOTA", 200_000)  # 2 cores @ 100ms
    mem_limit: str = os.getenv("SANDBOX_HOST_MEM_LIMIT", "2g")
    pids_limit: int = _int("SANDBOX_HOST_PIDS_LIMIT", 256)
    tmpfs_workspace_size: str = os.getenv("SANDBOX_HOST_TMPFS_WORKSPACE", "512m")
    tmpfs_home_size: str = os.getenv("SANDBOX_HOST_TMPFS_HOME", "512m")

    # ---- Free-RAM accounting -----------------------------------------
    # The /capacity endpoint reports free_ram_bytes so the SandboxPool can
    # filter hosts that don't fit a Heavy spawn. Computed as:
    #     free = total_ram_bytes - reserved_bytes - Î£(running sandbox mem_limits)
    # where total_ram comes from psutil and reserved_bytes is OS + scheduler
    # process + any non-sandbox services co-located on this VM.
    #
    # If your VM also runs Mongo / Redis / nginx alongside the sandboxes,
    # bump `reserved_ram_gb` so the scheduler doesn't try to pack RAM that
    # other services need.
    reserved_ram_gb: float = float(os.getenv("SANDBOX_HOST_RESERVED_RAM_GB", "4"))
    # When psutil is unavailable (or it reports 0), fall back to this static
    # value. Set to your VM's total RAM in GB if psutil isn't installed.
    fallback_total_ram_gb: float = float(
        os.getenv("SANDBOX_HOST_FALLBACK_TOTAL_RAM_GB", "0")
    )

    # ---- DuckDB temp directory (shared across tiers) -----------------
    # Per-tier DUCKDB_* sizes (memory_limit / threads / max_temp_directory_size)
    # live in tiers.py and are injected per-spawn from the resolved tier.
    # Only the spill-directory PATH is shared across tiers â€” it's a real
    # path on the sandbox's /workspace tmpfs that the agent's
    # ``sql.session()`` helper SETs at connection open.
    duckdb_temp_directory: str = os.getenv(
        "SANDBOX_HOST_DUCKDB_TEMP_DIRECTORY", "/workspace/.duckdb-temp"
    )

    # Port-mapping range for publishing adapter :8090 back to the host.
    # Citra-Service reaches a sandbox via
    #   http://{public_host}:{host_port}
    # Pick a high range that isn't used by system services.
    host_port_min: int = _int("SANDBOX_HOST_PORT_MIN", 31000)
    host_port_max: int = _int("SANDBOX_HOST_PORT_MAX", 31999)

    # ---- startup probe -----------------------------------------------
    startup_timeout_seconds: int = _int("SANDBOX_HOST_STARTUP_TIMEOUT", 45)

    # ---- orphan reaper ------------------------------------------------
    # A background sweep removes leaked sandbox containers so abandoned
    # pods (e.g. a builder whose browser tab was closed without a teardown
    # call) don't accumulate and exhaust capacity. Each sweep removes:
    #   - any sandbox container in a terminal Docker state (exited / dead /
    #     created) â€” corpses left behind by crashes or failed spawns, and
    #   - app-builder pods that have been RUNNING longer than
    #     builder_max_age_seconds â€” a hard backstop for orphans whose UI
    #     never sent a teardown (reuse + reap-on-spawn handle the common
    #     case; this catches the long tail).
    # Set reaper_interval_seconds <= 0 to disable the background sweep.
    # builder_max_age_seconds <= 0 disables the age backstop (corpses are
    # still reaped). The age ceiling is deliberately generous so it never
    # kills an actively-progressing build.
    reaper_interval_seconds: int = _int("SANDBOX_HOST_REAPER_INTERVAL", 300)
    builder_max_age_seconds: int = _int("SANDBOX_HOST_BUILDER_MAX_AGE", 7200)

    # Host-side age backstop for CHAT sandboxes (profile=sandbox). Idle +
    # 12 h max-age are normally enforced in-process by the OWNING
    # action-chat replica. If that replica crashes, the Redis lease TTLs
    # out (so the user re-spawns cleanly) but the container keeps RUNNING
    # and holding RAM — nothing else reaps a still-running orphan. This
    # ceiling catches those orphans. It MUST exceed the chat service's
    # ACTIONCHAT_MAX_SESSION_SECONDS (default 12 h) so it never races a
    # live, in-budget session — it only ever kills pods the owning replica
    # should have reaped but couldn't. Default 24 h gives a wide safety
    # margin over the 12 h chat cap: being too LOW kills live sessions
    # (bad); being too high just lets a rare crash-orphan hold RAM a few
    # extra hours (benign). Raise it if you raise the chat cap. <= 0
    # disables.
    sandbox_max_age_seconds: int = _int("SANDBOX_HOST_SANDBOX_MAX_AGE", 86400)


_config: HostConfig | None = None


def get_config() -> HostConfig:
    """Singleton accessor â€” env is read once at process start."""
    global _config
    if _config is None:
        cfg = HostConfig()
        if not cfg.shared_secret:
            raise RuntimeError(
                "SANDBOX_HOST_SECRET is required â€” Citra-Service and every "
                "sandbox-host must share this value."
            )
        # Fail loud if free-RAM accounting can't be computed: without it the
        # per-tier RAM fit check silently no-ops and the host overcommits RAM
        # up to max_concurrent. Require either psutil (reports total > 0) or
        # an explicit fallback_total_ram_gb, unless the operator opts into
        # unbounded RAM on purpose.
        if not cfg.allow_unbounded_ram and not _ram_accounting_available(cfg):
            raise RuntimeError(
                "free-RAM accounting is unavailable: psutil is not installed "
                "(or reports 0 total RAM) and SANDBOX_HOST_FALLBACK_TOTAL_RAM_GB "
                "is unset. The per-tier RAM fit check would silently no-op and "
                "the host could overcommit RAM up to max_concurrent. Install "
                "psutil, set SANDBOX_HOST_FALLBACK_TOTAL_RAM_GB to this VM's "
                "total RAM, or set SANDBOX_HOST_ALLOW_UNBOUNDED_RAM=true to run "
                "with slot-cap-only enforcement."
            )
        _config = cfg
    return _config


def _ram_accounting_available(cfg: "HostConfig") -> bool:
    """True iff free-RAM math has a real total to work from at boot.

    Mirrors Scheduler._total_ram_bytes(): psutil's reported total, else the
    static fallback. Kept here (not importing the scheduler) so the check can
    run before the Docker client is constructed.
    """
    try:
        import psutil  # type: ignore

        if int(psutil.virtual_memory().total) > 0:
            return True
    except ImportError:
        pass
    return cfg.fallback_total_ram_gb > 0
