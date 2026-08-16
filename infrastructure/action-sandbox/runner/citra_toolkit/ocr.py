# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""Vision/OCR proxy — Qwen3-VL-32B via Citra-Service.

The sandbox has no tesseract and no local OCR model. ``ocr.extract()``
sends image bytes (or a URL) to the proxy which calls the configured
``VISION_BASE_URL`` (Qwen VL by default) and returns extracted text.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Literal

from ._proxy import proxy_url
from .client import http_client


Mode = Literal["text", "layout", "tables"]


@dataclass
class OCRResult:
    text: str
    mode: Mode
    model: str
    elapsed_ms: int


def extract(image: bytes | str, *, mode: Mode = "text",
            content_type: str = "image/png", filename: str | None = None,
            timeout: float = 120.0) -> OCRResult:
    """Run OCR on an image.

    ``image`` may be raw bytes or an ``http(s)://`` URL. ``content_type``
    is used when bytes are provided; ignored for URLs.
    """
    if isinstance(image, (bytes, bytearray)):
        payload = {
            "image_b64": base64.b64encode(bytes(image)).decode("ascii"),
            "content_type": content_type,
            "mode": mode,
        }
        if filename:
            payload["filename"] = filename
    elif isinstance(image, str):
        payload = {"image_url": image, "mode": mode}
    else:
        raise TypeError("image must be bytes or a URL string")

    with http_client(timeout=timeout) as c:
        r = c.post(proxy_url("ocr"), json=payload)
        r.raise_for_status()
        data = r.json() or {}
    return OCRResult(
        text=str(data.get("text") or ""),
        mode=mode,
        model=str(data.get("model") or ""),
        elapsed_ms=int(data.get("elapsed_ms") or 0),
    )
