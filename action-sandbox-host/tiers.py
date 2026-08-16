# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""
Tier catalog — homogeneous fleet sizing definitions.

Every sandbox-host can serve every tier. A spawn request carries the
requested ``tier`` name; the scheduler looks up the resource recipe in
this catalog and applies it as Docker run-time options + DUCKDB_* env
vars. This is how we get three concurrent sandbox "shapes" on the same
VM without needing dedicated chat-tier vs analytics-tier hosts.

Why centralised
---------------
- Single source of truth between sandbox-host (enforcer), SandboxPool
  (picker — needs to know mem_bytes for free-RAM math), action-chat-
  service (validator — role-gates Heavy), and Citra-UI (presenter —
  shows the three options to the end user). The Pydantic model can be
  shared via the wire contract; the UI hard-codes copy + the byte
  values in its own constants because we don't want every UI render
  to do a network call.
- Easy to add a fourth tier ("Mega": 16 GB / 8 cores) — drop another
  entry in ``CATALOG`` and bump the API contract minor version.

Tiers
-----
- **quick** (default): 2 GB / 1 core. 99% of chats. Q&A, retrieval,
  web research, small file lookups. Spawns in ~1s. Also the tier the
  smart-app builder pod runs at.
- **standard**: 4 GB / 3 cores. Analytics-ready. Uploads up to ~200 MB,
  multi-source joins on hundreds of thousands of rows, BigQuery
  results up to ~1M rows.
- **heavy**: 8 GB / 4 cores. SAP-scale analytics. Full fact-table
  aggregations via push-down filters, wide GCS Parquet scans, joins
  on millions of rows. Slower to spawn (sandbox-host needs to verify
  free RAM), role-gated to users with the ``analyst`` claim.

Sizing math per tier
--------------------
For tier T: mem_limit_bytes = container RAM cap (Docker --memory).
            cpu_quota = Docker --cpu-quota (period=100ms → 100_000=1 core).
            tmpfs_workspace_bytes = /workspace tmpfs size — counts
              against mem_limit (it's RAM-backed).
            duckdb_memory_limit = string DuckDB SET; typically ~50%
              of (mem_limit - tmpfs_workspace) so the Python runtime
              has room.
            duckdb_max_temp_directory_size = DuckDB spill cap, must
              fit inside tmpfs_workspace_bytes.

All values are env-overridable per-host via ``SANDBOX_HOST_TIER_<NAME>_*``
for unusual deployments; see config.py.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict


_GB = 1024 * 1024 * 1024
_MB = 1024 * 1024


def _env_str(key: str, default: str) -> str:
    return os.getenv(key, default).strip() or default


def _env_int(key: str, default: int) -> int:
    raw = os.getenv(key)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_bytes(key: str, default_bytes: int) -> int:
    """Parse '2g' / '512m' / '8g' style strings. Falls back to default on bad input."""
    raw = (os.getenv(key) or "").strip().lower()
    if not raw:
        return default_bytes
    try:
        if raw.endswith("g") or raw.endswith("gb"):
            return int(float(raw.rstrip("gb").rstrip("g")) * _GB)
        if raw.endswith("m") or raw.endswith("mb"):
            return int(float(raw.rstrip("mb").rstrip("m")) * _MB)
        return int(raw)  # raw byte count
    except ValueError:
        return default_bytes


@dataclass(frozen=True)
class Tier:
    """One sandbox shape. Wire-stable — DON'T rename fields."""
    name: str                                # "quick" | "standard" | "heavy"
    display_name: str                        # "Quick" | "Standard" | "Heavy"
    description: str                         # one-liner for the UI tooltip
    mem_limit_bytes: int                     # Docker --memory (bytes)
    cpu_quota: int                           # Docker --cpu-quota (period=100_000)
    pids_limit: int                          # Docker --pids-limit
    tmpfs_workspace_bytes: int               # /workspace tmpfs cap (bytes)
    tmpfs_home_bytes: int                    # /home/citra tmpfs cap (bytes)
    duckdb_memory_limit: str                 # DuckDB SET memory_limit
    duckdb_threads: int                      # DuckDB SET threads
    duckdb_max_temp_directory_size: str      # DuckDB SET max_temp_directory_size
    requires_role: str = ""                  # JWT role required to pick this tier ("" = any user)

    # ------ derived helpers ------
    @property
    def mem_limit_str(self) -> str:
        """Render bytes back to Docker's 'Xg'/'Xm' string format."""
        n = self.mem_limit_bytes
        if n % _GB == 0:
            return f"{n // _GB}g"
        if n % _MB == 0:
            return f"{n // _MB}m"
        return str(n)

    @property
    def tmpfs_workspace_str(self) -> str:
        n = self.tmpfs_workspace_bytes
        if n % _GB == 0:
            return f"{n // _GB}g"
        if n % _MB == 0:
            return f"{n // _MB}m"
        return str(n)

    @property
    def tmpfs_home_str(self) -> str:
        n = self.tmpfs_home_bytes
        if n % _GB == 0:
            return f"{n // _GB}g"
        if n % _MB == 0:
            return f"{n // _MB}m"
        return str(n)


def _build_catalog() -> Dict[str, Tier]:
    """Construct the three-tier catalog. Each value is env-overridable."""
    return {
        "quick": Tier(
            name="quick",
            display_name="Quick",
            description=(
                "2 GB / 1 core. Q&A, retrieval, web research, small file lookups. "
                "The right choice for almost every conversation."
            ),
            mem_limit_bytes=_env_bytes("SANDBOX_HOST_TIER_QUICK_MEM", 2 * _GB),
            cpu_quota=_env_int("SANDBOX_HOST_TIER_QUICK_CPU_QUOTA", 100_000),
            pids_limit=_env_int("SANDBOX_HOST_TIER_QUICK_PIDS", 256),
            tmpfs_workspace_bytes=_env_bytes("SANDBOX_HOST_TIER_QUICK_TMPFS_WORKSPACE", 512 * _MB),
            tmpfs_home_bytes=_env_bytes("SANDBOX_HOST_TIER_QUICK_TMPFS_HOME", 512 * _MB),
            duckdb_memory_limit=_env_str("SANDBOX_HOST_TIER_QUICK_DUCKDB_MEMORY_LIMIT", "1GB"),
            duckdb_threads=_env_int("SANDBOX_HOST_TIER_QUICK_DUCKDB_THREADS", 2),
            duckdb_max_temp_directory_size=_env_str(
                "SANDBOX_HOST_TIER_QUICK_DUCKDB_MAX_TEMP_DIRECTORY_SIZE", "256MB"
            ),
        ),
        "standard": Tier(
            name="standard",
            display_name="Standard",
            description=(
                "4 GB / 3 cores. Mid-size analytics: uploads up to ~200 MB, "
                "multi-source joins on hundreds of thousands of rows, BigQuery "
                "results up to a million rows."
            ),
            mem_limit_bytes=_env_bytes("SANDBOX_HOST_TIER_STANDARD_MEM", 4 * _GB),
            cpu_quota=_env_int("SANDBOX_HOST_TIER_STANDARD_CPU_QUOTA", 300_000),
            pids_limit=_env_int("SANDBOX_HOST_TIER_STANDARD_PIDS", 384),
            tmpfs_workspace_bytes=_env_bytes("SANDBOX_HOST_TIER_STANDARD_TMPFS_WORKSPACE", _GB),
            tmpfs_home_bytes=_env_bytes("SANDBOX_HOST_TIER_STANDARD_TMPFS_HOME", 512 * _MB),
            duckdb_memory_limit=_env_str("SANDBOX_HOST_TIER_STANDARD_DUCKDB_MEMORY_LIMIT", "2GB"),
            duckdb_threads=_env_int("SANDBOX_HOST_TIER_STANDARD_DUCKDB_THREADS", 3),
            duckdb_max_temp_directory_size=_env_str(
                "SANDBOX_HOST_TIER_STANDARD_DUCKDB_MAX_TEMP_DIRECTORY_SIZE", "768MB"
            ),
        ),
        "heavy": Tier(
            name="heavy",
            display_name="Heavy",
            description=(
                "8 GB / 4 cores. Full SAP-scale aggregations (10s of GB via "
                "push-down filters), wide GCS Parquet scans, multi-source "
                "joins on millions of rows. Slower to spawn; available to "
                "users with the analyst role only."
            ),
            mem_limit_bytes=_env_bytes("SANDBOX_HOST_TIER_HEAVY_MEM", 8 * _GB),
            cpu_quota=_env_int("SANDBOX_HOST_TIER_HEAVY_CPU_QUOTA", 400_000),
            pids_limit=_env_int("SANDBOX_HOST_TIER_HEAVY_PIDS", 512),
            tmpfs_workspace_bytes=_env_bytes("SANDBOX_HOST_TIER_HEAVY_TMPFS_WORKSPACE", 4 * _GB),
            tmpfs_home_bytes=_env_bytes("SANDBOX_HOST_TIER_HEAVY_TMPFS_HOME", 512 * _MB),
            duckdb_memory_limit=_env_str("SANDBOX_HOST_TIER_HEAVY_DUCKDB_MEMORY_LIMIT", "4GB"),
            duckdb_threads=_env_int("SANDBOX_HOST_TIER_HEAVY_DUCKDB_THREADS", 4),
            duckdb_max_temp_directory_size=_env_str(
                "SANDBOX_HOST_TIER_HEAVY_DUCKDB_MAX_TEMP_DIRECTORY_SIZE", "3GB"
            ),
            # Role-gated. action-chat-service rejects spawn with 403 when the
            # caller's JWT roles claim doesn't include this. Set to "" to
            # open Heavy to everyone (not recommended in multi-tenant prod).
            requires_role=_env_str("SANDBOX_HOST_TIER_HEAVY_REQUIRES_ROLE", "analyst"),
        ),
    }


CATALOG: Dict[str, Tier] = _build_catalog()
DEFAULT_TIER_NAME: str = _env_str("SANDBOX_HOST_DEFAULT_TIER", "quick")


def get_tier(name: str | None) -> Tier:
    """Resolve a tier name, falling back to the default on unknown / empty input.

    Wire shape is forgiving: an old client that doesn't send ``tier`` at all
    gets the default; a typo gets the default (logged at the call site).
    """
    key = (name or DEFAULT_TIER_NAME).strip().lower()
    return CATALOG.get(key, CATALOG[DEFAULT_TIER_NAME])


def list_tier_names() -> list[str]:
    """Stable display order: lightest → heaviest. UI uses this for the picker."""
    return ["quick", "standard", "heavy"]
