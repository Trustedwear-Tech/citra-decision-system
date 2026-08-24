# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""The BA's surface pick must reach the builder IN THE CONVERSATION.

Citra-UI shows a surface picker before the build starts, and the pick used to
travel only as env (`BUILD_PRIMARY_PAGE_KIND` / `BUILD_HEADLESS`). Env is one
paragraph in a 400-line AGENTS.md, and it was being missed: a BA who clicked
"Embedded card" still got asked, two turns later, which of three surfaces they
wanted — by a question that argues for a particular answer.

So the pick is now prefixed onto the FIRST forwarded chat turn. These cover the
three properties that make that safe.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main  # noqa: E402
from tests._test_helpers import _MemCol  # type: ignore  # noqa: E402


# ── which picks say something ───────────────────────────────────────────────

def test_embed_pick_names_the_shape_and_the_skill():
    n = main._surface_note(primary_page_kind="embed", build_headless=False)
    assert "Embedded card" in n
    assert "citra-embed-spec" in n
    # The shape itself, so the builder does not have to go and look it up.
    assert "no queue" in n
    assert "detail.actions" in n


def test_dashboard_and_headless_picks_say_so():
    d = main._surface_note(primary_page_kind="dashboard", build_headless=False)
    assert "Dashboard" in d and "citra-dashboard-spec" in d
    h = main._surface_note(primary_page_kind=None, build_headless=True)
    assert "headless" in h.lower() and "no panels" in h


def test_headless_wins_over_page_kind():
    """An API build has no pages; if both arrive, the headless instruction is
    the one that must not be contradicted."""
    n = main._surface_note(primary_page_kind="standard", build_headless=True)
    assert "headless" in n.lower()


def test_standard_says_NOTHING():
    """THE case that matters. `_builder_env` defaults an absent pick to
    "standard", so "standard" is what an explicit App pick AND the
    "Let's talk it through" option both send — they are indistinguishable here.

    Announcing "the BA picked App" would silently kill the one option whose
    whole purpose is to be asked. Saying nothing lets AGENTS.md keep asking.
    """
    assert main._surface_note(primary_page_kind="standard", build_headless=False) is None
    assert main._surface_note(primary_page_kind=None, build_headless=False) is None


def test_note_is_marked_as_context_not_as_the_BA():
    """The build transcript is an audit artefact. The note must never read as
    something the human typed — they may switch surface two turns later, and a
    fabricated 'I want an embedded card' would outlive the decision."""
    for kind, headless in (("embed", False), ("dashboard", False), (None, True)):
        n = main._surface_note(primary_page_kind=kind, build_headless=headless)
        assert n.startswith("[Citra context — not typed by the BA:"), n[:60]


# ── consumed exactly once ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_note_is_taken_once_then_gone():
    col = _MemCol([{"session_id": "bs_1", "surface_note": "NOTE"}])
    assert await main._take_surface_note(col, "bs_1") == "NOTE"
    # Second turn (and any racing turn) gets nothing — the note is a greeting,
    # not a preamble on every message.
    assert await main._take_surface_note(col, "bs_1") is None


@pytest.mark.asyncio
async def test_no_note_is_not_an_error():
    col = _MemCol([{"session_id": "bs_2", "surface_note": None}])
    assert await main._take_surface_note(col, "bs_2") is None
    assert await main._take_surface_note(_MemCol([]), "missing") is None
