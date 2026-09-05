#!/usr/bin/env python3
# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""Every vendored runtime-reference file must equal its live source.

`smart-app-service/skills/citra-system/runtime-reference/` is a snapshot of the
code that renders, executes and validates a published spec. The app-builder pod
is TAUGHT by SKILL.md that this snapshot is the source of truth, so a stale copy
does not degrade gracefully -- it teaches the builder a contract the runtime no
longer has, and every app authored against it is wrong in the same way.

It has gone stale twice. A 2026-07 review found the snapshot a full
feature-generation behind: no `check_evaluate`, no `modality='api'`, wrong
review-gate logic. Found again on 2026-09-06: `publish_validators.py` was 107
lines behind and missing the M-01 gate entirely.

Both times the snapshot was refreshed by hand, by a PowerShell script, with
nothing checking that anyone had run it. That is the actual defect -- not the
copy, which is deliberate and useful, but the absence of a check on it. Hence
this file.

    python scripts/check_runtime_reference.py

Exits non-zero and names every file that differs. Line endings are normalised
before comparing: the snapshot is committed from Windows and CRLF/LF is not
drift.
"""
from __future__ import annotations

import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
REF = REPO / "smart-app-service" / "skills" / "citra-system" / "runtime-reference"

#: Which live tree each vendored subdirectory mirrors. Must match the `$map` in
#: smart-app-service/vendor-runtime-reference.ps1 -- if you add a subtree there,
#: add it here or this check silently stops covering it.
SOURCE_ROOTS = {
    "executor": REPO / "smart-app-service",
    "validators": REPO / "smart-app-service",
    "renderer": REPO / "citra-app-runtime" / "src",
}

#: Not vendored source: the manifest is generated, bytecode is never vendored.
SKIP_NAMES = {"MANIFEST.md"}
SKIP_PARTS = {"__pycache__"}

FIX = ("pwsh smart-app-service/vendor-runtime-reference.ps1"
       "   # refreshes every vendored file in place")


def main() -> int:
    if not REF.is_dir():
        print(f"::error::runtime-reference not found at {REF.relative_to(REPO)}")
        return 1

    identical: int = 0
    drifted: list[str] = []
    orphaned: list[str] = []
    unmapped: list[str] = []

    for path in sorted(REF.rglob("*")):
        if not path.is_file() or path.name in SKIP_NAMES:
            continue
        if SKIP_PARTS.intersection(path.parts):
            continue
        rel = path.relative_to(REF)
        root = SOURCE_ROOTS.get(rel.parts[0])
        if root is None:
            unmapped.append(rel.as_posix())
            continue
        src = root.joinpath(*rel.parts[1:])
        if not src.exists():
            orphaned.append(rel.as_posix())
            continue
        # CRLF vs LF is not drift -- this snapshot is committed from Windows.
        if src.read_bytes().replace(b"\r\n", b"\n") == path.read_bytes().replace(b"\r\n", b"\n"):
            identical += 1
        else:
            drifted.append(rel.as_posix())

    for rel in drifted:
        print(f"::error file=smart-app-service/skills/citra-system/runtime-reference/{rel}::"
              f"differs from its live source - the builder is being taught a stale contract")
    for rel in orphaned:
        print(f"::error file=smart-app-service/skills/citra-system/runtime-reference/{rel}::"
              f"vendored but the live source is gone - it was retired; remove it from the snapshot")
    for rel in unmapped:
        print(f"::error::{rel} is under no known source root - add it to SOURCE_ROOTS "
              f"here and to $map in vendor-runtime-reference.ps1")

    if drifted or orphaned or unmapped:
        print()
        print(f"{len(drifted)} stale, {len(orphaned)} orphaned, {len(unmapped)} unmapped "
              f"({identical} in sync).")
        print("Refresh the snapshot and commit it:")
        print(f"  {FIX}")
        return 1

    print(f"runtime-reference is in sync with the live runtime ({identical} files).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
