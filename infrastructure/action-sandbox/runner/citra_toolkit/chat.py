# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""Chat-history accessor for the Action Chat sandbox.

The current session's user/assistant transcript is stored in
action-chat-service's Redis (one entry per turn — user message and
final assistant reply only; intermediate tool-call chatter is not
persisted). The agent reads it via this proxy when the user
references something earlier in the conversation that's not in the
agent's own ``scratch.memory`` notes.

Use cases:
- User says *"the report I asked for"* / *"that file I uploaded
  yesterday"* / *"the FIFO question I asked earlier"* and you can't
  resolve the reference from the current turn alone.
- Investigating what the user has previously asked to plan a
  follow-up Tier-3 brief.
- Recovering context after a container respawn when ``kb-index``
  doesn't carry the verbatim phrasing.

This is **not** the agent's durable memory — that's
``citra_toolkit.scratch.memory``. Chat history is the verbatim
transcript; ``scratch.memory`` is the agent's own structured notes
(workflow briefs, method decisions, kb-index). Use them together:
``scratch.memory`` to see what *you* committed to; ``chat.history``
to see what *the user* actually said.

Each entry::

    {
      "role": "user" | "assistant" | "system",  # system = artifact metadata
      "content": str,                           # the message text
      "ts": float,                              # unix epoch
      "meta": dict,                             # extra (e.g. artifact url)
    }

The ring is capped at 200 entries; the latest ``limit`` are returned,
oldest first.
"""
from __future__ import annotations

from typing import Any

from ._proxy import proxy_url
from .client import http_client


def history(*, limit: int = 50) -> list[dict[str, Any]]:
    """Return the most recent ``limit`` turns from this session's
    Redis-backed chat transcript, chronological (oldest first).

    Filters cross-user automatically — the scoped JWT pins the
    session_id server-side; you cannot read another user's transcript
    even if you fabricate a session id.
    """
    body = {"limit": int(limit)}
    with http_client(timeout=15.0) as c:
        r = c.post(proxy_url("chat/history"), json=body)
        r.raise_for_status()
        payload = r.json() or {}
    return list(payload.get("turns") or [])


def clear() -> int:
    """Wipe the current session's Redis transcript ring.

    Use when the user explicitly asks to forget / clear the chat
    ("delete this conversation", "clear my chat", "start fresh").
    Returns the number of turns that were removed (0 if empty).

    This affects ONLY the verbatim chat transcript. Durable memory
    notes are unaffected — use ``scratch.memory.delete(slug)`` (or
    iterate ``scratch.memory.list()`` and delete each) for those.
    Uploaded files are unaffected — use ``files.delete(key)`` for
    those.
    """
    with http_client(timeout=15.0) as c:
        r = c.post(proxy_url("chat/clear"), json={})
        r.raise_for_status()
        payload = r.json() or {}
    return int(payload.get("removed") or 0)


def search(query: str, *, limit: int = 10) -> list[dict[str, Any]]:
    """Convenience: pull recent history, filter for entries containing
    the query (case-insensitive substring), return up to ``limit``.

    Cheap and good-enough for resolving most user references like
    *"the trade book question"*. For precise text retrieval over the
    user's vault docs, use ``vault.search`` instead.
    """
    if not query or not query.strip():
        return []
    needle = query.lower().strip()
    turns = history(limit=200)
    matches: list[dict[str, Any]] = []
    for t in turns:
        content = (t.get("content") or "").lower()
        if needle in content:
            matches.append(t)
            if len(matches) >= limit:
                break
    return matches
