# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""
Drive build / publish / run flows from tests without spawning the
full builder pod stack.

The real flagship build session is multi-phase (Internship → Expertise
→ Compose → Deploy) and requires:
  * A Kubernetes pod scheduler
  * The builder image with /workspace/AGENTS.md
  * SSE streaming back to the browser

Setting all of that up for every test is impractical. What the tests
actually want to assert is the *contract surface*:
  * POST /publish persists the AppSpec/AgentSpec correctly
  * POST /apps/{slug}/run dispatches actions, calls LLM, returns
    {decision, ok, error}
  * Publish-time guards reject typo'd source_ids
  * /run honours cross-tenant isolation

So we skip the pod and POST hand-built specs straight to /publish.
This is the same path the builder pod takes after authoring the spec —
the Pydantic validation, source-id resolver, cross-tenant guard, and
Mongo persister all run unchanged.
"""
from __future__ import annotations

import os
from typing import Any, Dict, Optional

import httpx


DEFAULT_TIMEOUT = float(os.getenv("SMART_APP_TEST_TIMEOUT", "20.0"))


async def publish_spec(
    smart_app_url: str,
    *,
    app_spec: Optional[Dict[str, Any]] = None,
    agent_spec: Optional[Dict[str, Any]] = None,
    workflow_spec: Optional[Dict[str, Any]] = None,
    skills: Optional[list] = None,
    headers: Optional[Dict[str, str]] = None,
    expect_status: int = 200,
) -> httpx.Response:
    """POST /publish with the given specs and assert HTTP status.

    Returns the raw httpx.Response so the caller can inspect the body.
    """
    body: Dict[str, Any] = {
        "session_id": f"test_session_{os.urandom(4).hex()}",
        "skills": skills or [],
    }
    if app_spec is not None:
        body["app_spec"] = app_spec
    if agent_spec is not None:
        body["agent_spec"] = agent_spec
    if workflow_spec is not None:
        body["workflow_spec"] = workflow_spec

    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
        resp = await client.post(
            f"{smart_app_url.rstrip('/')}/publish",
            json=body,
            headers=headers or {},
        )
    if expect_status is not None:
        assert resp.status_code == expect_status, (
            f"publish expected {expect_status}, got {resp.status_code}: {resp.text}"
        )
    return resp


async def run_app(
    smart_app_url: str,
    *,
    slug: str,
    action: str,
    inputs: Dict[str, Any],
    headers: Optional[Dict[str, str]] = None,
    correlation_id: Optional[str] = None,
    timeout: float = DEFAULT_TIMEOUT,
    expect_status: int = 200,
) -> httpx.Response:
    body: Dict[str, Any] = {"action": action, "inputs": inputs}
    if correlation_id:
        body["correlation_id"] = correlation_id
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(
            f"{smart_app_url.rstrip('/')}/apps/{slug}/run",
            json=body,
            headers=headers or {},
        )
    if expect_status is not None:
        assert resp.status_code == expect_status, (
            f"run expected {expect_status}, got {resp.status_code}: {resp.text}"
        )
    return resp


async def get_app(
    smart_app_url: str,
    *,
    slug: str,
    headers: Optional[Dict[str, str]] = None,
) -> httpx.Response:
    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
        return await client.get(
            f"{smart_app_url.rstrip('/')}/apps/{slug}",
            headers=headers or {},
        )
