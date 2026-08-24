#!/usr/bin/env python3
# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""Set the BUSL Change Date for a release -- four years from the release date.

The licence converts each released version to Apache-2.0 four years after that
version shipped. That is a ROLLING date: it belongs to the release, not to the
repository, so it has to be rewritten every time a version is cut.

Nothing did that. `release.yml` took a version input, built images and pushed a
tag, and never touched LICENSE -- which still named version 0.1.0 with a Change
Date of 2030-08-09. The failure mode is quiet and it runs one way only: a
release cut in 2028 against an unchanged file would carry a 2030 Change Date and
convert to Apache after two years instead of four. You cannot re-close a version
you have already published as open source, so every missed bump permanently
gives away the remaining years.

    python scripts/set_license_change_date.py --version 1.0.0
    python scripts/set_license_change_date.py --version 1.0.0 --check
"""
from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
LICENSE = REPO_ROOT / "LICENSE"

PRODUCT = "Citra Decision System"
TERM_YEARS = 4

# Trailing whitespace is matched as [^\S\r\n]* -- horizontal only. With plain
# `\s*$` under re.M the class also matches newlines, so on "Change Date: <date>"
# followed by a blank line it consumed the line break and the substitution
# deleted the blank line separating Change Date from Change License. Editing a
# licence must change the two values it claims to change and nothing else.
_EOL = r"[^\S\r\n]*$"
WORK_RE = re.compile(
    r"^(?P<pre>Licensed Work:[^\S\r\n]*)(?P<product>.+?), version (?P<version>\S+)"
    + _EOL, re.M,
)
DATE_RE = re.compile(
    r"^(?P<pre>Change Date:[^\S\r\n]*)(?P<date>\d{4}-\d{2}-\d{2})" + _EOL, re.M,
)


def add_years(d: dt.date, years: int) -> dt.date:
    """29 February has no counterpart in a non-leap year; fall back to the 28th.

    2028-02-29 + 4 lands on another leap year, but 2096-02-29 + 4 is 2100, which
    is not one. Rare, and a crash inside a release pipeline is a bad way to
    discover it.
    """
    try:
        return d.replace(year=d.year + years)
    except ValueError:
        return d.replace(year=d.year + years, day=28)


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--version", required=True, help="the version being released")
    ap.add_argument("--date", default=None,
                    help="release date YYYY-MM-DD (default: today, UTC)")
    ap.add_argument("--years", type=int, default=TERM_YEARS,
                    help=f"conversion term in years (default {TERM_YEARS})")
    ap.add_argument("--check", action="store_true",
                    help="verify only; exit 1 if LICENSE does not already match")
    args = ap.parse_args()

    released = (dt.date.fromisoformat(args.date) if args.date
                else dt.datetime.now(dt.timezone.utc).date())
    change_date = add_years(released, args.years)

    # Read and write the bytes ourselves. Path.read_text/write_text translate
    # newlines through the platform default, so on Windows an LF licence came
    # back CRLF and the whole file showed as modified -- burying the two lines
    # that actually changed. This tree is LF via .gitattributes; whatever the
    # file uses, it keeps.
    raw = LICENSE.read_bytes().decode("utf-8")
    crlf = "\r\n" in raw
    text = raw.replace("\r\n", "\n")
    work, date = WORK_RE.search(text), DATE_RE.search(text)
    if not work or not date:
        # Fail loud: a LICENSE whose parameters cannot be located must never be
        # silently left alone by a release that believes it updated them.
        print("  [FAIL] could not find the 'Licensed Work' and 'Change Date' "
              "parameters in LICENSE. Refusing to guess.", file=sys.stderr)
        return 1

    cur_version, cur_date = work.group("version"), date.group("date")
    want_date = change_date.isoformat()

    if args.check:
        problems = []
        if cur_version != args.version:
            problems.append(f"Licensed Work says version {cur_version}, "
                            f"expected {args.version}")
        if cur_date != want_date:
            problems.append(f"Change Date is {cur_date}, expected {want_date} "
                            f"({args.years} years after {released.isoformat()})")
        if problems:
            for p in problems:
                print(f"  [FAIL] {p}", file=sys.stderr)
            print("\n  Run: python scripts/set_license_change_date.py "
                  f"--version {args.version}", file=sys.stderr)
            return 1
        print(f"  [ok] LICENSE matches v{args.version}, converts {want_date}")
        return 0

    updated = WORK_RE.sub(
        lambda m: f"{m.group('pre')}{m.group('product')}, version {args.version}",
        text, count=1)
    updated = DATE_RE.sub(lambda m: f"{m.group('pre')}{want_date}",
                          updated, count=1)

    if updated == text:
        print(f"  LICENSE already correct: v{args.version}, converts {want_date}")
        return 0

    out = updated.replace("\n", "\r\n") if crlf else updated
    LICENSE.write_bytes(out.encode("utf-8"))
    print(f"  version    {cur_version} -> {args.version}")
    print(f"  ChangeDate {cur_date} -> {want_date}  "
          f"({args.years} years after {released.isoformat()})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
