# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""SOURCES_REFRESH_SECONDS must RE-REGISTER, not just reload locally.

docs/sources-file.md §13 tells authors: "Restart the MCP (or set
SOURCES_REFRESH_SECONDS) so it re-registers to discovery". The loop only called
load_sources(), so the reload was LOCAL-ONLY: retire a source with
is_active:false and the MCP 404s it while discovery keeps advertising it active
and routable — indefinitely, since deregister-on-shutdown is off and there is no
heartbeat reaper. For a semantic source /tools/source-scope still returned its
rag_collection, so /semantic/search kept serving a retired corpus. Added sources
were never advertised at all.
"""
import asyncio

import pytest

import router


@pytest.mark.asyncio
async def test_refresh_loop_reregisters_after_reload(monkeypatch):
    calls = {"loaded": 0, "registered": []}

    async def _fake_load():
        calls["loaded"] += 1
        return [{"source_id": "s1"}]

    async def _fake_register(sources):
        calls["registered"].append([s.get("source_id") for s in sources])
        raise asyncio.CancelledError  # stop the infinite loop after one pass

    monkeypatch.setattr(router, "load_sources", _fake_load)
    monkeypatch.setattr(router, "get_semantic_sources", lambda: [{"source_id": "rag1"}])
    monkeypatch.setattr("registration.register_all", _fake_register)
    monkeypatch.setattr(router, "get_settings",
                        lambda: type("C", (), {"sources_refresh_seconds": 1})())

    with pytest.raises(asyncio.CancelledError):
        await router._refresh_loop()

    assert calls["loaded"] == 1
    # Structured + semantic, the same set boot registers.
    assert calls["registered"] == [["s1", "rag1"]]


@pytest.mark.asyncio
async def test_refresh_loop_survives_a_reload_failure(monkeypatch):
    """A bad edit to SOURCES_FILE must not kill the loop or re-register a set we
    failed to read."""
    state = {"n": 0, "registered": 0}

    async def _fake_load():
        state["n"] += 1
        if state["n"] == 1:
            raise RuntimeError("malformed sources.json")
        raise asyncio.CancelledError  # second pass: stop

    async def _fake_register(sources):
        state["registered"] += 1

    monkeypatch.setattr(router, "load_sources", _fake_load)
    monkeypatch.setattr(router, "get_semantic_sources", lambda: [])
    monkeypatch.setattr("registration.register_all", _fake_register)
    monkeypatch.setattr(router, "get_settings",
                        lambda: type("C", (), {"sources_refresh_seconds": 1})())

    with pytest.raises(asyncio.CancelledError):
        await router._refresh_loop()

    assert state["n"] == 2, "loop must survive the bad reload and try again"
    assert state["registered"] == 0, "must not register a set it failed to load"


@pytest.mark.asyncio
async def test_reregistration_failure_does_not_kill_the_loop(monkeypatch):
    """Re-registration is hygiene, not a serving dependency: a discovery outage
    must leave the MCP serving its freshly reloaded local registry."""
    state = {"n": 0}

    async def _fake_load():
        state["n"] += 1
        if state["n"] > 1:
            raise asyncio.CancelledError
        return []

    async def _fake_register(sources):
        raise RuntimeError("discovery down")

    monkeypatch.setattr(router, "load_sources", _fake_load)
    monkeypatch.setattr(router, "get_semantic_sources", lambda: [])
    monkeypatch.setattr("registration.register_all", _fake_register)
    monkeypatch.setattr(router, "get_settings",
                        lambda: type("C", (), {"sources_refresh_seconds": 1})())

    with pytest.raises(asyncio.CancelledError):
        await router._refresh_loop()
    assert state["n"] == 2, "a discovery outage must not stop the refresh loop"
