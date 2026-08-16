# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""
Mongo / fake-stub assertions used across scenario files.

Stays small on purpose — most scenarios assert on response payloads
directly. These helpers cover the recurring "did the thing land in
Mongo at all" check and the fake-notify "was a webhook fired" probe.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import httpx


# ── Mongo-side ─────────────────────────────────────────────────────────


async def assert_app_persisted(
    db,
    *,
    slug: str,
    expected_tenant: Optional[str] = None,
    min_version: int = 1,
) -> Dict[str, Any]:
    """Assert the app is in the apps collection; return the doc."""
    doc = await db["smart_apps"].find_one({"slug": slug})
    assert doc is not None, f"apps document for slug={slug} not found"
    assert int(doc.get("version") or 0) >= min_version, (
        f"app {slug} version {doc.get('version')} < {min_version}"
    )
    if expected_tenant is not None:
        assert doc.get("tenant_id") == expected_tenant, (
            f"app {slug} tenant {doc.get('tenant_id')!r} != {expected_tenant!r}"
        )
    return doc


async def assert_agent_persisted(
    db, *, agent_id: str
) -> Dict[str, Any]:
    doc = await db["smart_agents"].find_one({"agent_id": agent_id})
    assert doc is not None, f"agents document for agent_id={agent_id} not found"
    return doc


async def get_workflow_state(
    db, *, workflow_id: str, key: str
) -> Optional[Any]:
    """Read a value from the workflow_state collection (None if missing)."""
    doc = await db["workflow_state"].find_one(
        {"workflow_id": workflow_id, "key": key}
    )
    if doc is None:
        return None
    return doc.get("value")


# ── fake-customer-notify side ───────────────────────────────────────────


async def get_notify_messages(notify_url: str) -> list:
    """Return all messages the fake-notify stub has captured this run."""
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.get(f"{notify_url.rstrip('/')}/admin/messages")
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list):
            return data
        return data.get("messages", [])


async def reset_notify_inbox(notify_url: str) -> None:
    async with httpx.AsyncClient(timeout=5.0) as client:
        await client.post(f"{notify_url.rstrip('/')}/admin/reset")


# ── fake-llm side ───────────────────────────────────────────────────────


async def get_llm_invocations(fake_llm_url: str) -> list:
    """Return every chat-completion call the fake-llm has seen."""
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.get(f"{fake_llm_url.rstrip('/')}/admin/invocations")
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list):
            return data
        return data.get("invocations", [])


async def reset_llm_invocations(fake_llm_url: str) -> None:
    async with httpx.AsyncClient(timeout=5.0) as client:
        await client.post(f"{fake_llm_url.rstrip('/')}/admin/reset")
