# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""An invalid registry must not boot.

A registry is CONFIG: written deliberately, reviewed before deploy, and
validate_sources.py exists so a bad one never reaches a running MCP. Serving a
half-understood registry is how a typo'd `artifact_roles` silently disabled fraud
screening — the source LOOKED fine and did the wrong thing.

SOURCES_STRICT=false is the documented escape hatch: an emergency where a live
MCP must come back now, with one bad source, rather than not at all. It is not a
quiet fallback — the source is SKIPPED and the log says exactly why.

The scoping rule is deliberate too: only sources this MCP actually SERVES are
validated. Another dept's malformed source in a shared registry is not ours to
fail on.
"""
import logging

import pytest

import router
from router import SourceRegistryInvalid, _select_sources


def _settings(strict=True):
    return type("C", (), {"sources_strict": strict})()


def _valid(source_id="ok", **over):
    d = {
        "source_id": source_id, "type": "structured", "dept_id": "d", "org_id": "o",
        "name": "n", "description": "x", "connection": {"env_prefix": "P"},
    }
    d.update(over)
    return d


def _invalid(source_id="bad"):
    # A typo'd ontology key — the canonical silent failure.
    return {
        "source_id": source_id, "type": "structured", "dept_id": "d", "org_id": "o",
        "name": "n", "description": "x", "connection": {"env_prefix": "P"},
        "datasets": [{"id": "bad.t", "columns": [
            {"name": "photo", "artifact_roles": "evidence"},
        ]}],
    }


def test_strict_refuses_to_boot_on_an_invalid_source(monkeypatch):
    monkeypatch.setattr(router, "get_settings", lambda: _settings(strict=True))
    with pytest.raises(SourceRegistryInvalid) as e:
        _select_sources([_valid(), _invalid()], "o", ["d"])
    msg = str(e.value)
    assert "artifact_roles" in msg
    assert "validate_sources.py" in msg, "the error must say how to fix it"
    assert "SOURCES_STRICT=false" in msg, "and name the escape hatch"


def test_strict_reports_EVERY_invalid_source_not_just_the_first(monkeypatch):
    """An author fixing a registry should see the whole list once, not discover
    it one boot at a time."""
    monkeypatch.setattr(router, "get_settings", lambda: _settings(strict=True))
    with pytest.raises(SourceRegistryInvalid) as e:
        _select_sources([_invalid("bad1"), _invalid("bad2")], "o", ["d"])
    assert "bad1" in str(e.value) and "bad2" in str(e.value)


def test_strict_boots_clean_when_every_source_is_valid(monkeypatch):
    monkeypatch.setattr(router, "get_settings", lambda: _settings(strict=True))
    out = _select_sources([_valid("a"), _valid("b")], "o", ["d"])
    assert sorted(out) == ["a", "b"]


def test_escape_hatch_boots_but_SKIPS_the_invalid_source(monkeypatch, caplog):
    """Not a silent fallback: the bad source is absent and the log says why."""
    monkeypatch.setattr(router, "get_settings", lambda: _settings(strict=False))
    with caplog.at_level(logging.ERROR):
        out = _select_sources([_valid("good"), _invalid("bad")], "o", ["d"])
    assert sorted(out) == ["good"], "an invalid source must never be served"
    assert "bad" in caplog.text
    assert "artifact_roles" in caplog.text
    assert "SOURCES_STRICT=false" in caplog.text


def test_only_sources_we_serve_are_validated(monkeypatch):
    """A shared registry can carry another dept's source. Its problems are not
    ours to fail on — we filter by org/dept BEFORE validating."""
    monkeypatch.setattr(router, "get_settings", lambda: _settings(strict=True))
    other_dept = _invalid("theirs")
    other_dept["dept_id"] = "not-ours"
    out = _select_sources([_valid("ours"), other_dept], "o", ["d"])
    assert sorted(out) == ["ours"]


def test_inactive_invalid_source_does_not_block_boot(monkeypatch):
    """is_active:false is how a source is retired. A retired source that is also
    malformed must not hold the MCP hostage."""
    monkeypatch.setattr(router, "get_settings", lambda: _settings(strict=True))
    retired = _invalid("retired")
    retired["is_active"] = False
    out = _select_sources([_valid("live"), retired], "o", ["d"])
    assert sorted(out) == ["live"]


def test_validation_defaults_to_strict_when_the_setting_is_absent(monkeypatch):
    """Fail closed: a Settings object without the flag must not silently serve an
    invalid registry."""
    monkeypatch.setattr(router, "get_settings", lambda: type("C", (), {})())
    with pytest.raises(SourceRegistryInvalid):
        _select_sources([_invalid()], "o", ["d"])


def test_settings_default_is_strict():
    from config import Settings
    assert Settings().sources_strict is True
