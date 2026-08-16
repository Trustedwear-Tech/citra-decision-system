# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""citra_image_generate — text-to-image via the configured cloud provider.

Mirrors the env-var contract of action-chat-service so a single .env
line works across services. Default provider is Runware; the MCP
service holds the API key and the sandbox only ever sees the resulting
data: URL.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any

import httpx

from auth import CallerContext
from config import get_config
from .registry import ToolSpec, register_tool

logger = logging.getLogger(__name__)


_IMAGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "prompt": {
            "type": "string",
            "description": "Text prompt describing the desired image.",
        },
        "width": {
            "type": "integer",
            "description": "Image width in pixels (multiple of 64, default 1024).",
            "minimum": 256,
            "maximum": 2048,
        },
        "height": {
            "type": "integer",
            "description": "Image height in pixels (multiple of 64, default 1024).",
            "minimum": 256,
            "maximum": 2048,
        },
        "negative_prompt": {
            "type": "string",
            "description": "Optional negative prompt.",
        },
    },
    "required": ["prompt"],
    "additionalProperties": False,
}


def _round64(v: int, default: int) -> int:
    if v <= 0:
        v = default
    v = max(256, min(2048, v))
    return (v // 64) * 64 or 256


async def _exec_image_generate(args: dict[str, Any], caller: CallerContext) -> dict[str, Any]:
    cfg = get_config()
    prompt = (args.get("prompt") or "").strip()
    if not prompt:
        raise ValueError("prompt is required")
    width = _round64(int(args.get("width") or 1024), 1024)
    height = _round64(int(args.get("height") or 1024), 1024)
    negative = (args.get("negative_prompt") or "").strip() or None

    provider = (cfg.image_gen_provider or "runware").lower()
    if provider != "runware":
        raise RuntimeError(
            f"citra_image_generate: provider {provider!r} not supported by this service"
        )
    if not cfg.image_gen_api_key:
        raise RuntimeError("IMAGE_GEN_API_KEY is not configured on citra-mcp-service")

    task_uuid = str(uuid.uuid4())
    body = [{
        "taskType": "imageInference",
        "taskUUID": task_uuid,
        "positivePrompt": prompt,
        "width": width,
        "height": height,
        "model": cfg.image_gen_model or "runware:400@1",
        "numberResults": 1,
        "outputType": "base64Data",
        "outputFormat": "PNG",
    }]
    if negative:
        body[0]["negativePrompt"] = negative

    base = (cfg.image_gen_base_url or "https://api.runware.ai/v1").rstrip("/")
    headers = {
        "Authorization": f"Bearer {cfg.image_gen_api_key}",
        "Content-Type": "application/json",
    }
    started = time.time()
    # Hard total deadline. httpx's numeric timeout is per-operation
    # (connect / read-between-chunks), so a provider that dribbles the
    # response can blow well past it — observed 70s on a 60s setting.
    # OpenClaw's MCP client abandons the call around ~60s, and a
    # timed-out MCP call wedges the WHOLE agent session
    # (state=processing, never recovers). Bounding the call with
    # asyncio.wait_for guarantees this tool returns fast with either a
    # result or a clean error the agent can react to — never long
    # enough to trip OpenClaw's MCP timeout.
    deadline = float(cfg.image_gen_timeout_seconds or 45)
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(deadline)) as client:
            r = await asyncio.wait_for(
                client.post(base, headers=headers, json=body),
                timeout=deadline,
            )
        r.raise_for_status()
        data = r.json() or {}
    except (asyncio.TimeoutError, httpx.TimeoutException) as e:
        raise RuntimeError(
            f"image provider did not respond within {deadline:.0f}s "
            f"(model={cfg.image_gen_model or 'runware:400@1'}) — the turn "
            f"can proceed without this image"
        ) from e

    results = data.get("data") or []
    if not results:
        raise RuntimeError(f"image provider returned no results: {data!r}")
    first = results[0]
    image_b64 = first.get("imageBase64Data") or first.get("imageBase64") or ""
    if not image_b64:
        raise RuntimeError("image provider returned no base64 payload")

    elapsed_ms = int((time.time() - started) * 1000)
    logger.info(
        "[tool=citra_image_generate] user=%s w=%d h=%d ms=%d",
        caller.user_id, width, height, elapsed_ms,
    )
    return {
        "image_data": f"data:image/png;base64,{image_b64}",
        "width": width,
        "height": height,
        "model": cfg.image_gen_model or "runware:400@1",
        "elapsed_ms": elapsed_ms,
    }


register_tool(ToolSpec(
    name="citra_image_generate",
    description=(
        "Generate an image from a text prompt via the configured cloud "
        "provider (Runware). Returns {image_data: 'data:image/png;base64,...', "
        "width, height, model}. Use when the caller wants a new picture; "
        "for editing existing images, prefer the dedicated edit tools."
    ),
    schema=_IMAGE_SCHEMA,
    executor=_exec_image_generate,
    # End-user content generation — belongs to the chat surface, not
    # the enterprise app builder.
    allowed_scopes=("action-sandbox",),
))
