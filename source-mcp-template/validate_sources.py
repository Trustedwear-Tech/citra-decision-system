#!/usr/bin/env python3
# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""Validate a sources.json BEFORE it reaches an MCP.

    python validate_sources.py sources.json
    python validate_sources.py demo-data/tenants/*/mcp/sources.json
    python validate_sources.py sources.json --json      # machine-readable (CI)

Why this exists: sources.json was never validated. The loader kept raw dicts and
the models didn't forbid extras, so a typo'd key was simply never read — write
`artifact_roles` instead of `artifact_role` and fraud screening on that column
silently does nothing. Every failure mode this catches was, until now, either a
log line nobody reads or no signal at all.

This is the AUTHORITATIVE gate. It runs the pydantic models in registry_models.py,
which carry both the structure AND the cross-field rules that JSON Schema cannot
express (mongodb needs connection.collection; semantic needs rag.milvus_collection;
fraud_screening.applies=true needs a primary key AND a fingerprint target).

schema/sources.schema.json is the same contract for EDITORS — add
`"$schema": "./schema/sources.schema.json"` to your file and VS Code will
autocomplete and flag structural errors as you type. It is generated from the
same models, and CI fails if it drifts. Use both: the editor for the fast loop,
this for the truth.

Exit codes:  0 = valid   1 = invalid   2 = usage/IO error
"""
from __future__ import annotations

import argparse
import collections
import glob
import json
import pathlib
import sys
from typing import Dict, List

try:
    from pydantic import ValidationError
    from registry_models import RegistrySource, SourcesFile, format_error_path
except ImportError as exc:  # pragma: no cover - import-time environment problem
    print(f"cannot import the models ({exc}). Run from source-mcp-template/, or "
          f"set PYTHONPATH to it.", file=sys.stderr)
    raise SystemExit(2)


Problem = collections.namedtuple("Problem", "where what why")

#: Extra context for pydantic's terse messages. The author needs to know what
#: BREAKS, not just which rule tripped — "extra_forbidden" tells you nothing
#: about why a typo'd ontology key is dangerous.
_WHY = {
    "extra_forbidden": (
        "unknown key — nothing reads it. sources.json used to accept anything, so "
        "a misspelled field silently did nothing (e.g. `artifact_roles` disables "
        "fraud screening on that column with no warning)."
    ),
    "missing": "required field.",
    "enum": "not an allowed value — check the spelling against the schema.",
}


def _problems_from(exc: ValidationError, prefix: str = "") -> List[Problem]:
    out: List[Problem] = []
    for err in exc.errors():
        # format_error_path is shared with router._validate_selected (the boot
        # gate) so the CLI and the MCP name the same path for the same problem.
        # It was written twice and the copies had already diverged.
        loc = format_error_path(err["loc"]) or "<root>"
        where = f"{prefix}{loc}" if prefix else loc
        why = _WHY.get(err["type"], "")
        out.append(Problem(where, err["msg"], why))
    return out


def validate_file(path: pathlib.Path) -> List[Problem]:
    """Every problem in one file. Empty list = valid."""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        return [Problem(str(path), f"cannot read: {exc}", "")]
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError as exc:
        return [Problem(f"{path}:{exc.lineno}:{exc.colno}", f"invalid JSON: {exc.msg}",
                        "the MCP refuses to boot on a malformed registry.")]

    problems: List[Problem] = []

    # 1. File shape.
    srcs = doc.get("sources") if isinstance(doc, dict) else doc
    if not isinstance(srcs, list):
        return [Problem("<root>", 'must be a JSON array of sources or {"sources": [...]}',
                        "the loader accepts either; anything else raises at boot.")]

    # 2. Per source. Validate each one individually rather than the whole file:
    # a union-level dump is unreadable (pydantic reports BOTH branches), and one
    # bad source shouldn't hide the others.
    for i, src in enumerate(srcs):
        sid = (src or {}).get("source_id") if isinstance(src, dict) else None
        label = f"sources[{i}]" + (f" ({sid})" if sid else "")
        try:
            RegistrySource.model_validate(src)
        except ValidationError as serr:
            problems.extend(_problems_from(serr, prefix=f"{label}."))

    # 3. File-level rules no single source can see. Deliberately computed from
    # the RAW doc, not from parsed models: these must still be reported when a
    # sibling source is invalid, and they were silently skipped when anything
    # else in the file failed.
    seen: Dict[str, int] = {}
    for i, src in enumerate(srcs):
        if not isinstance(src, dict):
            problems.append(Problem(f"sources[{i}]", "not a JSON object", ""))
            continue
        sid = src.get("source_id")
        if not isinstance(sid, str) or not sid:
            continue  # the per-source pass already reported the missing id
        if sid in seen:
            problems.append(Problem(
                f"sources[{i}] ({sid})",
                f"duplicate source_id — also at sources[{seen[sid]}]",
                "the registry is keyed by source_id, so one silently shadows the "
                "other and which one wins depends on file order.",
            ))
        seen[sid] = i

    # 4. Envelope shape (unknown top-level keys next to "sources").
    if isinstance(doc, dict):
        try:
            SourcesFile.model_validate({"sources": []} | {
                k: v for k, v in doc.items() if k != "sources"
            })
        except ValidationError as exc:
            for p in _problems_from(exc):
                if "sources" not in p.where:
                    problems.append(p)

    return problems


def capability_advisories(path: pathlib.Path) -> List[str]:
    """What this registry will NOT do, said out loud.

    Every ontology field is optional and running without one is a supported
    choice — so a file can be perfectly VALID and still leave fraud screening,
    grounding or ROI switched off. That used to surface as a publish-time
    advisory inside smart-app-service, i.e. several steps after the moment the
    author could act on it. Reported here instead, while the file is open.

    These are NOT problems: they never fail the run or change the exit code.
    """
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — a parse error is already a hard problem
        return []
    srcs = doc if isinstance(doc, list) else (doc.get("sources") or [])

    # A dataset's PART in the grammar explains most absences (templates/README.md):
    # a verification target, an explicit opt-out and an API lookup are all
    # supposed to lack decision history. Nagging about those is noise, and noise
    # is how advisories get ignored. Collect the roles first.
    referenced: set = set()          # named as another dataset's ledger/target
    for s in srcs:
        for ds in s.get("datasets") or []:
            fs = ds.get("fraud_screening") or {}
            # verify_against is a LIST of checks; payment_proof is a single
            # block. Handle both shapes rather than assuming one.
            for blk_key, ref_key in (("payment_proof", "ledger_dataset"),
                                     ("verify_against", "target_dataset")):
                blk = fs.get(blk_key)
                for one in (blk if isinstance(blk, list) else [blk]):
                    if isinstance(one, dict) and one.get(ref_key):
                        referenced.add(one[ref_key])

    def _exempt(ds: dict) -> bool:
        fs = ds.get("fraud_screening")
        if isinstance(fs, dict) and fs.get("applies") is False:
            return True                       # deliberate opt-out
        if (ds.get("kind") or "").lower() == "rest":
            return True                       # agent-read lookup, not a record
        return ds.get("id") in referenced     # someone's ledger / verify target

    notes: List[str] = []
    for s in srcs:
        sid = s.get("source_id", "?")
        dom = s.get("domain")
        if not dom:
            notes.append(
                f"{sid}: no `domain` — locale validators (ID formats, date order, "
                f"currency) and vertical default packs will not apply. Everything "
                f"else works; declare one only if you want those defaults.")
        elif dom.get("pack_advisory"):
            notes.append(f"{sid}: {dom['pack_advisory']}")

        for ds in s.get("datasets") or []:
            did = ds.get("id", "?")
            cols = ds.get("columns") or []
            has_artifacts = any(c.get("artifact_role") for c in cols)
            fs = ds.get("fraud_screening") or {}

            if has_artifacts and not fs:
                notes.append(
                    f"{did}: has artifact columns but no `fraud_screening` block "
                    f"— no screening runs on them. Add `fraud_screening.applies` "
                    f"to switch it on, or `applies: false` to say so deliberately.")
            if _exempt(ds):
                continue
            if not ds.get("decision_history"):
                notes.append(
                    f"{did}: no `decision_history` — Decision Apps cannot ground "
                    f"in this dataset's past decisions (no few-shot history).")
            if not ds.get("value_semantics"):
                notes.append(
                    f"{did}: no `value_semantics` — Money Impact / ROI will show "
                    f"nothing for this dataset.")
    return notes


def _report_human(path: pathlib.Path, problems: List[Problem]) -> None:
    if not problems:
        print(f"  OK   {path}")
        return
    print(f"  FAIL {path}  ({len(problems)} problem{'s' if len(problems) > 1 else ''})")
    for p in problems:
        print(f"       {p.where}")
        print(f"         -> {p.what}")
        if p.why:
            print(f"            {p.why}")


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Validate a dept-MCP sources.json before it reaches an MCP.",
        epilog="Tip: add \"$schema\": \"./schema/sources.schema.json\" to your file "
               "for live validation in VS Code.",
    )
    ap.add_argument("paths", nargs="+", help="sources.json file(s); globs allowed")
    ap.add_argument("--json", action="store_true", dest="as_json",
                    help="machine-readable output for CI")
    args = ap.parse_args(argv)

    # Expand globs ourselves so the tool behaves the same on Windows, where the
    # shell does not.
    files: List[pathlib.Path] = []
    for pat in args.paths:
        hits = [pathlib.Path(p) for p in glob.glob(pat)]
        if not hits:
            print(f"no such file: {pat}", file=sys.stderr)
            return 2
        files.extend(hits)

    results = {str(f): validate_file(f) for f in files}
    bad = {f: p for f, p in results.items() if p}

    if args.as_json:
        print(json.dumps({
            "valid": not bad,
            "files": {
                f: [{"where": p.where, "what": p.what, "why": p.why} for p in probs]
                for f, probs in results.items()
            },
        }, indent=2))
        return 1 if bad else 0

    print(f"Validating {len(files)} file(s) against registry_models.py\n")
    for f in files:
        _report_human(pathlib.Path(f), results[str(f)])
    print()
    if bad:
        n = sum(len(p) for p in bad.values())
        print(f"INVALID — {n} problem(s) in {len(bad)} of {len(files)} file(s).")
        print("Nothing is silently ignored any more: fix these, or the MCP will "
              "refuse to boot on them.")
        # Honest about the tool's own limit: a field error stops that model's
        # cross-field checks from running at all, so "4 problems" is a floor, not
        # a total. Better to say so than to let a clean second run surprise you.
        print("Then re-run: the cross-field checks (mongodb->collection, "
              "semantic->rag, fraud->primary key) only run once a source's "
              "individual fields are valid, so more may surface.")
        return 1
    print(f"VALID — {len(files)} file(s).")

    # Valid is not the same as fully switched on. Say what is off, here, while
    # the author still has the file open — not at publish time.
    advisories = {f: capability_advisories(pathlib.Path(f)) for f in files}
    total = sum(len(a) for a in advisories.values())
    if total:
        print()
        print(f"{total} capability advisory(ies) — the file is valid; these are "
              f"features it does not switch on:")
        for f, notes in advisories.items():
            if not notes:
                continue
            print(f"  {f}")
            for n in notes:
                print(f"    - {n}")
        print()
        print("  Ontology is opt-in by design; ignore anything you do not want.")
        print("  Worked examples: source-mcp-template/templates/  "
              "(see templates/README.md for the grammar)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
