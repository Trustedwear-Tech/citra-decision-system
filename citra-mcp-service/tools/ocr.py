# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""citra_ocr — vision OCR via action-chat-service's Qwen3-VL proxy.

Accepts either an inline image (base64) or a public URL. The upstream
route handles PDF rasterization via pdf2image+poppler too, so the same
tool covers single-page images and multi-page PDFs.
"""
from __future__ import annotations

from typing import Any

from auth import CallerContext
from ._forward import call_internal
from .registry import ToolSpec, register_tool


_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "image_b64": {
            "type": "string",
            "description": "Base64 image or PDF bytes (mutually exclusive with image_url).",
        },
        "image_url": {
            "type": "string",
            "description": (
                "Public http(s) URL. The upstream fetches it server-side "
                "with SSRF guards."
            ),
        },
        "mode": {
            "type": "string",
            "enum": ["text", "layout", "tables"],
            "description": (
                "text = plain text, layout = markdown with headings, "
                "tables = GitHub-flavored markdown tables. Default 'text'."
            ),
        },
        "content_type": {
            "type": "string",
            "description": "MIME type when supplying image_b64 (default image/png).",
        },
        "filename": {
            "type": "string",
            "description": "Optional source filename for audit / logging.",
        },
    },
    "additionalProperties": False,
}


async def _exec(args: dict[str, Any], caller: CallerContext) -> dict[str, Any]:
    image_b64 = args.get("image_b64") or ""
    image_url = args.get("image_url") or ""
    if not image_b64 and not image_url:
        raise ValueError("image_b64 or image_url is required")
    if image_b64 and image_url:
        raise ValueError("provide image_b64 OR image_url, not both")
    body: dict[str, Any] = {}
    if image_b64:
        body["image_b64"] = image_b64
    else:
        body["image_url"] = image_url
    if args.get("mode"):
        body["mode"] = str(args["mode"])
    if args.get("content_type"):
        body["content_type"] = str(args["content_type"])
    if args.get("filename"):
        body["filename"] = str(args["filename"])
    # OCR can take many seconds for multi-page PDFs.
    return await call_internal(
        "actionchat/internal/ocr", body, caller, timeout_seconds=180.0
    )


register_tool(ToolSpec(
    name="citra_ocr",
    description=(
        "Vision OCR via Qwen3-VL. Returns {mode, text, model, elapsed_ms}. "
        "Supports single-page images and multi-page PDFs (rasterized). "
        "Mode 'tables' returns markdown tables, 'layout' preserves "
        "headings/lists, 'text' is plain text."
    ),
    schema=_SCHEMA,
    executor=_exec,
    # OCR pipeline reads user-supplied image bytes and routes through the
    # action-chat user-data backend (storage, vision processor, audit).
    # Smart-app builder doesn't need to OCR personal uploads.
    allowed_scopes=("action-sandbox",),
))
