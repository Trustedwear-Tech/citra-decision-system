# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""Image-generation proxy — Runware (or whatever IMAGE_GEN_PROVIDER is
configured) via action-chat-service.

The sandbox has no API keys and no egress. ``image.generate(prompt)``
posts to ``{CITRA_AGENT_PROXY_BASE_URL}/image/generate``; the service
performs the actual provider call and returns a base64 data URI. Bytes
are decoded once here so the caller gets a plain ``bytes`` payload
ready to feed into ``BytesIO`` / ``Image.open`` /
``slide.shapes.add_picture`` / ``report.publish``.

Typical use from the presentation skill::

    from citra_toolkit import image
    img = image.generate(
        "boardroom-lit close-up of a hand placing a chess piece on a marble desk",
        width=1344, height=768,
    )
    slide.shapes.add_picture(image.BytesIO(img.data), left, top, width=Inches(6))

The endpoint snaps width/height to the nearest provider-supported preset
and prepends a NO-TEXT prefix server-side, so callers don't need to
sanitize prompts themselves.
"""
from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from io import BytesIO  # re-exported for convenience  # noqa: F401
from pathlib import Path

from ._proxy import proxy_url
from .client import http_client


@dataclass
class GeneratedImage:
    """Result of a single image generation."""
    data: bytes              # raw PNG/JPEG bytes
    format: str              # "PNG" | "JPEG" | "WEBP"
    width: int               # actual rendered width (provider may snap)
    height: int              # actual rendered height
    model: str               # model id the provider billed for

    @property
    def data_uri(self) -> str:
        mime = {
            "PNG": "image/png",
            "JPEG": "image/jpeg",
            "JPG": "image/jpeg",
            "WEBP": "image/webp",
        }.get(self.format.upper(), "image/png")
        return f"data:{mime};base64,{base64.b64encode(self.data).decode()}"

    def save(self, path: str | Path) -> Path:
        """Write the image to disk; returns the resolved Path."""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(self.data)
        return p


def _default_model() -> str | None:
    """Return ``CITRA_AGENT_IMAGE_GEN_MODEL`` if injected (else None)."""
    model = (os.getenv("CITRA_AGENT_IMAGE_GEN_MODEL") or "").strip()
    return model or None


def _parse_data_uri(uri: str) -> tuple[bytes, str]:
    """Decode ``data:image/png;base64,XXX`` -> (bytes, "PNG")."""
    if not uri.startswith("data:"):
        raise ValueError(f"expected a data: URI, got {uri[:32]!r}")
    header, _, payload = uri.partition(",")
    if not payload:
        raise ValueError("data URI has no payload")
    fmt = "PNG"
    h = header.lower()
    if "jpeg" in h or "jpg" in h:
        fmt = "JPEG"
    elif "webp" in h:
        fmt = "WEBP"
    return base64.b64decode(payload), fmt


def generate(
    prompt: str,
    *,
    width: int = 1024,
    height: int = 1024,
    negative_prompt: str | None = None,
    model: str | None = None,
    timeout: float = 90.0,
) -> GeneratedImage:
    """Generate a single image and return the bytes.

    ``prompt`` — describe the visual scene; do NOT ask for text in the
    image, captions, or labels (the provider strips those server-side
    but explicit text requests degrade quality).

    ``width`` / ``height`` — desired output size; provider snaps to the
    nearest supported preset. Common choices for presentations:
        - 1344 x 768  (16:9 — hero / full-bleed)
        - 1152 x 896  (4:3  — content with image)
        - 1024 x 1024 (1:1  — square cards)

    ``negative_prompt`` — comma-separated tokens to avoid (defaults to
    a sensible no-text baseline on the server).

    ``model`` — override the configured default (e.g. ``"runware:400@1"``).
    """
    effective_model = model or _default_model()
    payload: dict = {
        "prompt": prompt,
        "width": int(width),
        "height": int(height),
    }
    if negative_prompt:
        payload["negative_prompt"] = negative_prompt
    if effective_model:
        payload["model"] = effective_model

    with http_client(timeout=timeout) as c:
        r = c.post(proxy_url("image/generate"), json=payload)
        r.raise_for_status()
        body = r.json() or {}

    if not body.get("success"):
        raise RuntimeError(
            f"image generation failed: {body.get('message') or 'unknown error'}"
        )

    data_uri = str(body.get("image_data") or "")
    data, fmt = _parse_data_uri(data_uri)
    return GeneratedImage(
        data=data,
        format=fmt,
        width=int(body.get("width") or width),
        height=int(body.get("height") or height),
        model=str(body.get("model") or effective_model or ""),
    )
