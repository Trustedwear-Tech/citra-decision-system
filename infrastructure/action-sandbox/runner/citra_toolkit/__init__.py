# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""
citra_toolkit — Python helpers baked into the Action Sandbox image.

These modules are how the agent (running under OpenClaw inside the
sandbox) reaches the rest of Citra:

  - files:     bucket-as-catalog (the user's uploaded files + agent outputs)
  - sql:       duckdb (over uploaded blobs / bucket files)
  - vector:    ephemeral per-job index (sqlite-vec) for selective retrieval
  - embed:     embedding-service proxy
  - rerank:    reranker-service proxy
  - ocr:       Qwen3-VL OCR proxy
  - llm:       single large model (configured via LLM_LARGE_MODEL) for synthesis / drafting
  - image:     text-to-image (Runware) for presentation / printable artifacts
  - web:       last-resort web search + fetch (current events / fresh data)
  - discovery: list / query governed enterprise MCPs (HR, finance, CRM)
  - scratch:   durable analyst memory + working-note KV
  - research:  audit / citation builder
  - report:    PDF / PPTX / Excel / CSV / PNG artifact production
  - publish:   surface artifacts to the Action Chat UI
  - html:      emit sanitized inline HTML blocks to the chat
  - email:     email a report to the signed-in user
  - docs:      lightweight PDF / DOCX / image / scanned-PDF extractor
  - analytics: pandas helpers used by report builders

The agent invokes these via OpenClaw's ``exec`` / ``code_execution``,
e.g.::

    python -c "from citra_toolkit import files; print(files.list())"

Everything written by the agent into ``/workspace/.citra/outbox.ndjson``
is picked up by the adapter and translated into SSE events.
"""

from .client import http_client, scoped_token, bearer_headers  # noqa: F401

# Toolkit surface. ``milvus`` and ``folders`` were removed — the legacy
# vault-pre-indexed Milvus path is gone; use ``files`` + ``sql.duckdb``
# for user data and ``vector`` for agent-owned ad-hoc indexes.
from . import (  # noqa: F401
    files, vault, discovery, chat, sql, vector,
    web, embed, rerank, ocr, llm, image,
    docs, research, scratch, analytics, report, publish,
)

__all__ = [
    "http_client",
    "scoped_token",
    "bearer_headers",
    "files",
    "vault",
    "discovery",
    "chat",
    "sql",
    "vector",
    "web",
    "embed",
    "rerank",
    "ocr",
    "llm",
    "image",
    "docs",
    "research",
    "scratch",
    "analytics",
    "report",
    "publish",
]
