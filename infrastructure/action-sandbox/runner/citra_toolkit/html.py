# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""Emit an inline HTML block to the Action Chat UI.

The UI sanitizes (DOMPurify) before rendering, but the agent should still
avoid scripts and external subresources unless absolutely needed.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from .publish import _outbox_path


_MAX_HTML_BYTES = 256 * 1024  # 256 KB inline cap


def emit_html(html: str, *, title: str | None = None) -> dict[str, object]:
    if not isinstance(html, str) or not html.strip():
        raise ValueError("emit_html(): html must be a non-empty string")
    if len(html.encode("utf-8")) > _MAX_HTML_BYTES:
        raise ValueError(
            f"emit_html(): payload exceeds {_MAX_HTML_BYTES} bytes; "
            "render to PDF and publish() instead"
        )
    entry = {
        "type": "html_block",
        "html": html,
        "title": title or "",
        "ts": time.time(),
    }
    out: Path = _outbox_path()
    with out.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry
