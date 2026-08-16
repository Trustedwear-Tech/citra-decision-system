# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""
Pydantic request / response models for the Action Sandbox Host API.

These are the wire contracts Citra-Service codes against. Keep them
backwards-compatible when evolving — add new optional fields, don't rename.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel, Field


# -------------------------------------------------------------- spawn
class SpawnRequest(BaseModel):
    """Payload Citra-Service sends to POST /spawn.

    env is applied verbatim to `docker run -e ...`. Citra-Service MUST
    include at least:
      CITRA_USER_ID, CITRA_SESSION_ID, CITRA_SCOPED_TOKEN,
      CITRA_CONTROL_SECRET, CITRA_LLM_BASE_URL, CITRA_LLM_API_KEY,
      CITRA_ACTION_MODEL

    Note: ``volume_name`` was removed in the ephemeral migration. If a
    legacy client still sends it the field is accepted and ignored.
    """
    user_id: str = Field(..., description="User owning the sandbox")
    session_id: str = Field(..., description="Globally unique session id")
    env: Dict[str, str] = Field(
        ..., description="Environment variables forwarded to the container"
    )
    labels: Dict[str, str] = Field(
        default_factory=dict,
        description="Extra Docker labels to merge onto the container",
    )
    profile: str = Field(
        default="sandbox",
        description=(
            "Container profile. 'sandbox' (default) spawns the action sandbox "
            "used by Citra-Service. 'app-builder' spawns the ephemeral builder "
            "pod used by smart-app-service. Profiles map to images via "
            "HostConfig."
        ),
    )
    tier: str = Field(
        default="quick",
        description=(
            "Resource tier ('quick' | 'standard' | 'heavy'). Looked up in "
            "tiers.CATALOG to derive mem_limit / cpu_quota / tmpfs sizes / "
            "DUCKDB_* env. Unknown / empty falls back to the default tier."
        ),
    )
    # Deprecated — kept so old Citra-Service replicas (pre-ephemeral) don't
    # 422 against a freshly upgraded sandbox-host. Ignored at spawn time.
    volume_name: Optional[str] = Field(default=None, deprecated=True)


class SpawnResponse(BaseModel):
    session_id: str
    container_id: str
    container_name: str
    host_port: int = Field(
        ..., description="Port on the sandbox-host that maps to adapter :8090"
    )
    adapter_url: str = Field(
        ..., description="Full URL Citra-Service uses to reach the adapter"
    )
    public_host: str
    tier: str = Field(
        default="quick",
        description="Tier this sandbox was spawned at — echoed back so the pool can record it in the lease.",
    )


# -------------------------------------------------------------- capacity
class TierCapacity(BaseModel):
    """How many additional spawns of one tier the host can still accept.

    Derived from (free_ram_bytes // tier.mem_limit_bytes) bounded by the
    remaining slot count. The pool picker uses this per-tier number to
    filter hosts before ranking by free RAM.
    """
    tier: str
    can_spawn: int
    mem_limit_bytes: int


class CapacityResponse(BaseModel):
    public_host: str
    running: int
    max_concurrent: int
    remaining: int
    # 0..100 percent. Filled when psutil is available; else -1.
    cpu_pct: float = -1.0
    mem_pct: float = -1.0
    # Bytes of RAM still available for new sandbox spawns. Computed as
    # total_ram_bytes - reserved_bytes - sum(mem_limit of running sandboxes).
    # -1 when psutil is unavailable; in that case the pool can only filter
    # by slot count, not by RAM fit.
    free_ram_bytes: int = -1
    # Per-tier spawn capacity. Pool reads this so it can pick a host that
    # can actually accept a "heavy" without having to do its own RAM math.
    tier_capacity: List[TierCapacity] = Field(default_factory=list)


# -------------------------------------------------------------- status
class SessionStatusResponse(BaseModel):
    """Returned from GET /session/{session_id}.

    ``alive`` is the single bit callers (smart-app-service builder reuse)
    act on: True only when a container for this session exists AND is in
    Docker's ``running`` state. ``adapter_url`` is reconstructed from the
    live port binding so a caller that lost its record can still reattach.
    """
    session_id: str
    alive: bool
    container_id: Optional[str] = None
    container_name: Optional[str] = None
    adapter_url: Optional[str] = None
    docker_status: Optional[str] = Field(
        default=None,
        description="Raw Docker container state (running/exited/dead/...) or null if no container.",
    )


# -------------------------------------------------------------- generic
class OkResponse(BaseModel):
    ok: bool = True
    detail: Optional[str] = None


class ErrorResponse(BaseModel):
    ok: bool = False
    detail: str
