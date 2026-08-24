#!/usr/bin/env python3
# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""Tests for the release-time BUSL Change Date bump.

This edits the licence, so the bar is that it changes the two values it claims
to change and nothing else. Both regressions below were real, and both were
found by reading the diff rather than by the script complaining:

  - `\\s*$` under re.M also matches newlines, so the substitution swallowed the
    blank line between Change Date and Change License.
  - Path.write_text translates newlines through the platform default, so on
    Windows an LF licence came back CRLF and every line showed as modified.

    python -m pytest scripts/test_set_license_change_date.py -q
"""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import set_license_change_date as mod  # noqa: E402

LICENSE_TEXT = """Business Source License 1.1

Parameters

Licensor:             Trustedwear Tech Private Limited
Licensed Work:        Citra Decision System, version 0.1.0
                      The Licensed Work is (c) 2024-2026 Trustedwear Tech
                      Private Limited.

Change Date:          2030-08-09

Change License:       Apache License, Version 2.0
"""


@pytest.fixture
def lic(tmp_path, monkeypatch):
    p = tmp_path / "LICENSE"
    p.write_bytes(LICENSE_TEXT.encode("utf-8"))
    monkeypatch.setattr(mod, "LICENSE", p)
    return p


def _run(*argv) -> int:
    import sys as _s
    old = _s.argv
    _s.argv = ["set_license_change_date.py", *argv]
    try:
        return mod.main()
    finally:
        _s.argv = old


def test_bump_changes_exactly_two_lines(lic):
    before = lic.read_text(encoding="utf-8").split("\n")
    assert _run("--version", "1.0.0", "--date", "2026-08-12") == 0
    after = lic.read_text(encoding="utf-8").split("\n")
    assert len(before) == len(after), "line count changed"
    differing = [i for i, (a, b) in enumerate(zip(before, after)) if a != b]
    assert len(differing) == 2, [after[i] for i in differing]


def test_blank_line_before_change_license_survives(lic):
    """The regex regression: `\\s*$` ate the blank line."""
    _run("--version", "1.0.0", "--date", "2026-08-12")
    lines = lic.read_text(encoding="utf-8").split("\n")
    i = next(n for n, l in enumerate(lines) if l.startswith("Change Date:"))
    assert lines[i + 1].strip() == "", "blank line after Change Date was consumed"
    assert lines[i + 2].startswith("Change License:")


def test_lf_file_stays_lf(lic):
    """The newline regression: write_text turned an LF licence into CRLF."""
    _run("--version", "1.0.0", "--date", "2026-08-12")
    assert b"\r\n" not in lic.read_bytes()


def test_crlf_file_stays_crlf(lic):
    lic.write_bytes(LICENSE_TEXT.replace("\n", "\r\n").encode("utf-8"))
    _run("--version", "1.0.0", "--date", "2026-08-12")
    raw = lic.read_bytes()
    assert b"\r\n" in raw
    assert b"version 1.0.0" in raw


def test_change_date_is_four_years_out(lic):
    _run("--version", "1.0.0", "--date", "2026-08-12")
    assert "Change Date:          2030-08-12" in lic.read_text(encoding="utf-8")


def test_check_fails_on_a_stale_licence(lic, capsys):
    """The whole point: a 2028 release against an unchanged file."""
    assert _run("--version", "2.0.0", "--date", "2028-03-01", "--check") == 1
    err = capsys.readouterr().err
    assert "2032-03-01" in err


def test_check_passes_after_a_bump(lic):
    _run("--version", "1.0.0", "--date", "2026-08-12")
    assert _run("--version", "1.0.0", "--date", "2026-08-12", "--check") == 0


def test_idempotent(lic):
    _run("--version", "1.0.0", "--date", "2026-08-12")
    first = lic.read_bytes()
    _run("--version", "1.0.0", "--date", "2026-08-12")
    assert lic.read_bytes() == first


def test_fails_loud_when_parameters_are_missing(lic):
    lic.write_bytes(b"Business Source License 1.1\n\nno parameters here\n")
    assert _run("--version", "1.0.0") == 1


def test_leap_day_rolls_back_when_target_year_is_not_leap():
    assert mod.add_years(dt.date(2028, 2, 29), 4) == dt.date(2032, 2, 29)
    assert mod.add_years(dt.date(2096, 2, 29), 4) == dt.date(2100, 2, 28)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
