# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""Runtime automation halt / pause control — the kill switches.

A single Mongo collection (``smartapp_control``) holds "halt" records at four
scopes, checked before every run / autonomous write / trigger fire:

    scope_type : "global" | "org" | "dept" | "app"
    scope_id   : "*" (global) | <org_id> | <dept_id> | <slug>
    enabled    : bool   (True = HALTED / paused)
    reason, actor, updated_at

Precedence (first match halts): global > org > dept > app. So an org/super
admin flips ``global`` (the RED BUTTON), a dept admin freezes their ``dept``,
an owner pauses their ``app``. Reads/audit are unaffected — only the run /
write / trigger paths consult this.

Checks are cached in-process for a few seconds so per-run latency stays
negligible while remaining near-real-time (a write invalidates the cache).

FAIL-OPEN by design: if the control store can't be read, operations are
ALLOWED and the failure is logged loudly. A flag-store blip must not itself
take down all business operations — an explicit, persisted halt is what stops
things. (The per-write deterministic gates + human approval remain in force
regardless.)
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger("smart_app_service.automation_control")

SCOPE_TYPES = ("global", "org", "dept", "app")

_cache: Dict[str, Any] = {"at": 0.0, "controls": []}
_CACHE_TTL_SECONDS = 5.0


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def invalidate_cache() -> None:
    _cache["at"] = 0.0


async def _active_controls(col) -> List[dict]:
    now = time.time()
    if (now - _cache["at"]) < _CACHE_TTL_SECONDS:
        return _cache["controls"]
    try:
        docs = await col.find({"enabled": True}).to_list(length=2000)
    except Exception as e:  # fail-open — a flag-store outage must not halt ops
        logger.error(
            "automation_control: control-store read FAILED (fail-open, ops ALLOWED): %s", e
        )
        return []
    _cache["at"] = now
    _cache["controls"] = docs
    return docs


async def get_halt(
    col, *, org_id: Optional[str], dept_ids: Optional[List[str]], slug: Optional[str]
) -> Optional[dict]:
    """Return the halting control record (or None) for this app context.

    Precedence: global > org > dept > app.
    """
    controls = await _active_controls(col)
    if not controls:
        return None
    depset = {d for d in (dept_ids or []) if d}
    by_type: Dict[str, List[dict]] = {t: [] for t in SCOPE_TYPES}
    for c in controls:
        by_type.get(c.get("scope_type"), []).append(c)
    for c in by_type["global"]:
        return c
    for c in by_type["org"]:
        if org_id and c.get("scope_id") == org_id:
            return c
    for c in by_type["dept"]:
        if c.get("scope_id") in depset:
            return c
    for c in by_type["app"]:
        if slug and c.get("scope_id") == slug:
            return c
    return None


async def set_control(
    col, *, scope_type: str, scope_id: Optional[str], enabled: bool, actor: str, reason: str = ""
) -> dict:
    """Upsert a halt record. ``enabled=True`` halts; ``False`` clears."""
    if scope_type not in SCOPE_TYPES:
        raise ValueError(f"invalid scope_type {scope_type!r}")
    sid = "*" if scope_type == "global" else (scope_id or "")
    if scope_type != "global" and not sid:
        raise ValueError(f"scope_id required for scope_type={scope_type}")
    key = {"scope_type": scope_type, "scope_id": sid}
    doc = {
        **key,
        "enabled": bool(enabled),
        "reason": reason or "",
        "actor": actor,
        "updated_at": _now_iso(),
    }
    await col.update_one(key, {"$set": doc}, upsert=True)
    invalidate_cache()
    return doc


async def list_controls(col, *, active_only: bool = True) -> List[dict]:
    """All halt records (active by default), newest first, for the admin view."""
    query = {"enabled": True} if active_only else {}
    try:
        docs = await col.find(query).sort("updated_at", -1).to_list(length=2000)
    except Exception as e:
        logger.error("automation_control: list failed: %s", e)
        return []
    for d in docs:
        d.pop("_id", None)
    return docs
