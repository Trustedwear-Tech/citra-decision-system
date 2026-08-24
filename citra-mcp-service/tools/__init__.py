# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""Tool implementations exposed via MCP.

Each module here registers its tools into the shared ``TOOL_REGISTRY``.
The MCP endpoint in ``api.mcp`` dispatches incoming ``tools/call``
requests against this registry.

Adding a new tool:
    1. Create / extend a module under tools/ (e.g. ``tools/vault.py``).
    2. Inside, build a ``ToolSpec(name=..., description=..., schema=...,
       executor=async_fn)`` and call ``register_tool(spec)`` at module
       load time.
    3. Import the module from ``tools/__init__.py`` so it loads at
       service start.
"""
from __future__ import annotations

from .registry import (  # noqa: F401  re-export
    TOOL_REGISTRY,
    ToolSpec,
    list_tool_schemas,
    register_tool,
    resolve_tool,
)

# Side-effectful imports — each module registers its tools at load time.
from . import web  # noqa: F401
from . import rerank  # noqa: F401
# tools/sql.py (citra_sql_duckdb) is intentionally NOT registered.
# It used to wrap action-chat-service's /actionchat/internal/duckdb but the
# sandbox image already ships duckdb==1.1.3 — the agent gets a stateful
# connection inside code_execution that survives across turns, which is
# strictly better than the stateless proxy call. The module is kept on
# disk for reference / quick re-enable if a future caller (e.g. a smart-app
# runtime without code_execution) needs it.
from . import discovery  # noqa: F401
from . import embed  # noqa: F401
# tools/image.py (citra_image_generate) is intentionally NOT registered.
# It returned the generated image to the agent as an inline base64 data:
# URI; the agent then had to re-emit that multi-hundred-KB blob into a
# write/tool argument, which truncates and corrupts the call (observed
# 2026-05-22: a presentation turn failed with `write` missing its `path`).
# The action-chat skills now generate images via citra_toolkit.image
# inside `exec` — bytes stay in Python, saved to disk and referenced by
# path. Module kept on disk for reference / quick re-enable.
from . import vault  # noqa: F401
from . import files  # noqa: F401
from . import ocr  # noqa: F401
from . import visual_review  # noqa: F401
from . import spec_validate  # noqa: F401
