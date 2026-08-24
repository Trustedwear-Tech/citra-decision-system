# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""Vault-wide chunk retrieval over the user's already-embedded prose corpus.

Citra-Service chunks and embeds every unstructured upload (PDF / DOCX /
PPTX / HTML / TXT / transcribed audio / OCR'd images) at upload time
into ONE main Milvus collection. ``vault.search(query)`` runs a vector
search against that collection — scoped to the current user + selected
folder server-side — reranks the top-N candidates, and returns ranked
chunks with filename + page citations.

There is **no per-file step** — one call covers the entire vault. If
the agent picks the wrong sub-question, change the sub-question and
search again, don't try to narrow to a specific file.

Use this for any prose / knowledge question:

    from citra_toolkit import vault, llm

    chunks = vault.search("hedging policy second quarter", top_k=8)
    for c in chunks:
        print(c["filename"], "p", c["page_number"], c["text"][:120])

    # Synthesize over the chunks (citations carry filename + page).
    context = "\\n\\n".join(f'[{i}] {c["text"]}' for i, c in enumerate(chunks))
    answer = llm.complete([
        {"role": "system", "content": "Cite sources by [N]."},
        {"role": "user", "content": f"{question}\\n\\n{context}"},
    ]).text
"""
from __future__ import annotations

from typing import Any

from ._proxy import proxy_url
from .client import http_client


def search(query: str, *, top_k: int = 10, rerank: bool = True) -> list[dict[str, Any]]:
    """Rank vault chunks by relevance to ``query``.

    Returns up to ``top_k`` chunks, highest reranker score first. Each
    chunk carries::

        {
          "text": "...",        # canonical key
          "content": "...",     # alias of text (so chunk["content"] works too)
          "score": 0.78,
          "filename": "bp-energy-outlook-2025.pdf",
          "key": "personal/<folder>/documents/bp-energy-outlook-2025.pdf",
          "kind": "unstructured",
          "page_number": 7,
          "paragraph_number": 3,
          "document_id": "...",
          "chunk_index": 12,
          "chunk_id": "<doc>_12",
        }

    ``rerank=False`` skips the cross-encoder pass and returns by cosine
    score only — only set when latency is critical and you don't need
    the precision boost.
    """
    if not query or not query.strip():
        return []
    body = {
        "query": query,
        "top_k": int(top_k),
        "rerank": bool(rerank),
    }
    with http_client(timeout=30.0) as c:
        r = c.post(proxy_url("vault/search"), json=body)
        r.raise_for_status()
        payload = r.json() or {}
    chunks = list(payload.get("chunks") or [])
    # Surface ``text`` under both ``text`` and ``content`` keys.
    # LLM-generated Python often instinctively reaches for ``content``
    # (matching OpenAI's message shape); failing silently there sends
    # the agent on a debug detour. Cheap to provide both — same value,
    # both keys point at the same string.
    for c in chunks:
        if isinstance(c, dict):
            t = c.get("text") or ""
            c.setdefault("content", t)
            c.setdefault("text", t)
    return chunks
