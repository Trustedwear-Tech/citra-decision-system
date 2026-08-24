# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""Unit coverage for the learning-visibility additions:
- durable grounding freshness (GroundingRunStore.put_last/get_last)
- auto-learn on by default (OutcomePollConfig.auto_refresh)
- AppSummary.has_automation (drives the card Pause gating)
"""
from unittest.mock import patch

from models import AppSummary, AppStatus, OutcomePollConfig


# ── durable grounding freshness ──────────────────────────────────────────────
class _FakeCache:
    """Minimal setex/get stand-in for citra_cache (no Redis needed)."""

    def __init__(self):
        self.store = {}

    def setex(self, key, ttl, val):
        self.store[key] = (ttl, val)

    def get(self, key):
        v = self.store.get(key)
        return v[1] if v else None


def _store():
    from grounding_runs import GroundingRunStore
    with patch("citra_cache.get_cache_manager", return_value=_FakeCache()):
        return GroundingRunStore()


def test_grounding_freshness_never_refreshed_is_none():
    s = _store()
    assert s.get_last("acme-app") is None  # never refreshed → None


def test_grounding_freshness_round_trip():
    s = _store()
    summary = {"last_refreshed_at": "2026-07-13T10:00:00Z", "sample_count": 42,
               "canonical_samples": 8, "neighbor_samples": 34, "last_run_id": "gr_x"}
    s.put_last("acme-app", summary)
    got = s.get_last("acme-app")
    assert got == summary
    assert got["sample_count"] == 42


def test_grounding_last_key_is_distinct_from_run_key():
    s = _store()
    # the durable "last" record must not collide with the transient run doc
    assert s._last_key("x") != s._key("x")
    s.put_last("x", {"sample_count": 1})
    assert s.get("x") is None  # run doc still empty; only 'last' was written


# ── auto-learn on by default ─────────────────────────────────────────────────
def test_auto_refresh_defaults_on():
    assert OutcomePollConfig().auto_refresh is True   # auto-learn ON by default
    assert OutcomePollConfig().enabled is True        # tracking on too


def test_auto_refresh_can_be_disabled():
    assert OutcomePollConfig(auto_refresh=False).auto_refresh is False


# ── has_automation (Pause gating) ────────────────────────────────────────────
def test_app_summary_has_automation_field():
    assert "has_automation" in AppSummary.model_fields
    base = dict(app_id="a", slug="s", title="t", kind="app",
                status=AppStatus("published"), version=1, url="http://x")
    assert AppSummary(**base).has_automation is False          # default
    assert AppSummary(**base, has_automation=True).has_automation is True
