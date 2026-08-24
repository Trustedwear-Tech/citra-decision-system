#!/usr/bin/env python3
# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""Stamp every file WE wrote with the Apache-2.0 notice -- and nothing else.

The tool this replaces walked the filesystem with `find`. Run in this tree it
reported 6,079 files missing headers when only 979 were tracked by git: the
rest were `.venv-seed/Lib/site-packages`, and it would have written
"Copyright (c) Trustedwear Tech Private Limited. PROPRIETARY - all rights
reserved" into certifi, boto3, pydantic and every other dependency. Claiming
ownership of other people's Apache and MIT code is the most damaging thing a
licence-enforcement effort can do, because it is the first thing the other side
will point at.

So the file list comes from `git ls-files`. If it is not tracked, it is not
ours, and we do not touch it. That single decision is the point of this script.

The old header was also wrong on its face: it said PROPRIETARY / all rights
reserved / NOT an open-source grant, in a repository licensed Apache-2.0 with an
explicit grant. Two contradictory statements of terms invite an
ambiguity argument, and ambiguity is construed against its author. The header
below states the actual licence, with the registered SPDX id that scanners
recognise.

    python scripts/license_headers.py            # stamp what is missing
    python scripts/license_headers.py --check    # report only; exit 1 if any
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# The copyright HOLDER is the company: work owned by Trustedwear Tech Private
# Limited, which is the entity that can enforce it. The author is named on his
# own line rather than as the holder -- naming an individual as holder would
# contradict company ownership and undercut the CLA every contributor signs.
# The year records when the WORK was created, not when the company was formed.
# Trustedwear Tech Private Limited was incorporated in 2022, but no code in this
# product predates 2026 -- the first commit in the private repository this tree
# was cut from is 2026-01-28, with nothing in 2024 or 2025. The header said
# "2024-2026", claiming two years neither repository evidences.
#
# A notice is not required for protection (India is a Berne signatory, so
# copyright is automatic), which means an early date buys nothing and an
# unsupported one is exactly the detail used to attack the credibility of a
# provenance claim. Accurate beats early.
HEADER = [
    "Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)",
    "Author: Rohit Kumar Chandan",
    "SPDX-License-Identifier: Apache-2.0",
    "",
    "Licensed under the Apache License, Version 2.0 (the \"License\"); you may not",
    "use this file except in compliance with the License. You may obtain a copy of",
    "the License at http://www.apache.org/licenses/LICENSE-2.0",
]

# Detection is by SPDX tag alone, so the script is idempotent across header
# revisions: a file stamped by an older version is recognised and left alone
# rather than gaining a second header.
MARKER = "SPDX-License-Identifier:"
MARKER_SCAN_LINES = 15

# `//` is not a comment in plain CSS, and `#` is not one in SQL. Getting this
# wrong does not fail loudly -- it produces a file that parses until it doesn't.
LINE_COMMENT = {
    ".py": "#", ".sh": "#", ".bash": "#", ".ps1": "#", ".yml": "#",
    ".yaml": "#", ".toml": "#", ".tf": "#",
    ".js": "//", ".mjs": "//", ".cjs": "//", ".ts": "//", ".tsx": "//",
    ".jsx": "//",
    ".sql": "--",
}
BLOCK_COMMENT = {
    ".css": ("/*", " * ", " */"),
    ".md": ("<!--", "  ", "-->"),
    ".html": ("<!--", "  ", "-->"),
    ".xml": ("<!--", "  ", "-->"),
    ".svg": ("<!--", "  ", "-->"),
}
# Extensionless files that are still ours to stamp.
BY_NAME = {"Dockerfile": "#", "Makefile": "#"}

# Tracked, ours, and still must not be stamped.
EXCLUDE_EXACT = {
    # Next.js rewrites this on every build and the file says so itself.
    "citra-app-runtime/next-env.d.ts",
}
EXCLUDE_PATTERNS = [
    re.compile(r"(^|/)node_modules/"),
    re.compile(r"(^|/)\.next/"),
    re.compile(r"(^|/)(dist|build)/"),
    re.compile(r"\.min\.(js|css)$"),
    # "Save page as" in a browser writes the assets into a sibling `_files/`
    # directory. docs/ has several of these -- saved renderings of our own
    # documents, but wrapped in someone else's viewer markup and shipping their
    # JavaScript. The document is ours; this HTML is not.
    re.compile(r"_files/"),
    # A generated snapshot, not authored source: vendor-runtime-reference.ps1
    # copies it out of the Citra-AI repo, and its own MANIFEST.md says "VENDORED
    # SNAPSHOT (do not edit by hand)". Stamping it here would be undone by the
    # next refresh -- and CI would then fail on every regeneration. The header
    # belongs on the SOURCE files, so the snapshot inherits it.
    re.compile(r"(^|/)runtime-reference/"),
]

# Belt and braces for the same problem anywhere else in the tree: every browser
# writes this banner into a saved page. Content-based, so it catches saved pages
# that are not in a `_files/` layout at all.
SAVED_PAGE_BANNER = re.compile(r"<!--\s*saved from url=", re.I)


# The header this replaces, in both the entity spellings it shipped with.
# "Citra AI (https://github.com/Citra-AI)" is a product and a GitHub org, not
# the legal entity -- only Trustedwear Tech Private Limited can hold the
# copyright, so those two files were asserting it in a name that cannot enforce.
# Only these two identify the OLD header. The copyright line cannot: it is
# byte-identical in the new header, so keying on it would strip a correctly
# stamped file and re-stamp it, leaving two headers. That is not hypothetical --
# it is what the first version of this function did, and the test for it is
# test_replace_legacy_is_a_noop_on_a_correct_header.
LEGACY_DEFINITIVE = [
    re.compile(r"PROPRIETARY\s*-\s*all rights reserved"),
    re.compile(r"SPDX-License-Identifier:\s*LicenseRef-Citra-AI"),
]
# Removed only once a definitive marker has already proven the header is legacy.
# Year-agnostic: the range has changed once already (2024-2026 -> 2026) and
# pinning it here would silently stop matching the next time it moves.
LEGACY_COMPANION = re.compile(
    r"Copyright \(c\) [0-9]{4}(-[0-9]{4})? (Citra AI|Trustedwear Tech Private Limited)"
)
LEGACY_SCAN_LINES = 12

# Ours, in any vintage. Used to find a header that must be REPLACED rather than
# skipped -- which is what a change to the header text (a corrected year, a
# reworded grant) requires. Gated on our own SPDX values, so a third party's
# header in a file we happen to track is never rewritten.
OURS_RE = re.compile(r"Trustedwear Tech Private Limited|Citra AI \(https://github\.com/Citra-AI\)")
OUR_SPDX_RE = re.compile(r"SPDX-License-Identifier:\s*(Apache-2\.0|BUSL-1\.1|LicenseRef-Citra-AI)")
# The sentinel that marks where a header ENDS. It must be a substring of the
# LAST line of HEADER — when the header body changed to Apache-2.0 this was
# left pointing at the old BSL closing line, so header_span() stopped finding
# the end of a header and --rewrite mangled files instead of no-oping.
HEADER_LAST_LINE = "http://www.apache.org/licenses/LICENSE-2.0"


def header_span(lines: list[str], style) -> tuple[int, int] | None:
    """Locate an existing Citra header, returning [start, end) line indices.

    Deliberately anchored to the exact shapes we have shipped rather than
    "consume adjacent comment lines". Several files open with a banner comment
    directly under the header, and a greedy walk would eat it.
    """
    kind, spec = style
    for i, ln in enumerate(lines[:MARKER_SCAN_LINES]):
        if not OUR_SPDX_RE.search(ln):
            continue
        if not any(OURS_RE.search(x) for x in lines[max(0, i - 3):i + 3]):
            return None  # somebody else's SPDX tag; leave it alone

        start = i
        while start > 0 and "Copyright (c)" not in lines[start]:
            start -= 1
        # .strip() BOTH sides. The CSS closer is " */" with a leading space, so
        # comparing a stripped file line ("*/") against the raw spec (" */")
        # never matched: the closing delimiter was left behind and a fresh block
        # added above it, so every --rewrite run added another orphan "*/". The
        # markdown delimiters have no leading space, which is why the block test
        # passed and this went unnoticed.
        if kind == "block" and start > 0 and lines[start - 1].strip() == spec[0].strip():
            start -= 1

        end = i  # the legacy header ended at the SPDX line
        for j in range(i, min(len(lines), i + 8)):
            if HEADER_LAST_LINE in lines[j]:
                end = j
                break
        if (kind == "block" and end + 1 < len(lines)
                and lines[end + 1].strip() == spec[2].strip()):
            end += 1
        return start, end + 1
    return None


def strip_legacy(lines: list[str]) -> tuple[list[str], bool]:
    """Drop the old PROPRIETARY header so the correct one can replace it.

    Scoped to the top of the file on purpose: CONTRIBUTING.md quotes the old
    header inside a fenced code block as the format to use, and a blind
    whole-file match would silently gut that documentation instead of letting
    it be rewritten deliberately.
    """
    head = lines[:LEGACY_SCAN_LINES]
    if not any(p.search(ln) for ln in head for p in LEGACY_DEFINITIVE):
        return lines, False

    keep = []
    for i, ln in enumerate(lines):
        if i < LEGACY_SCAN_LINES and (
            any(p.search(ln) for p in LEGACY_DEFINITIVE)
            or LEGACY_COMPANION.search(ln)
        ):
            continue
        keep.append(ln)
    return keep, True


def tracked_files() -> list[str]:
    """The file list, from git. Untracked means not ours to claim."""
    out = subprocess.run(
        ["git", "ls-files", "-z"], cwd=REPO_ROOT,
        capture_output=True, check=True,
    ).stdout.decode("utf-8")
    return [p for p in out.split("\0") if p]


def comment_style(rel: str):
    """Return ('line', prefix) or ('block', (open, mid, close)), or None."""
    name = rel.rsplit("/", 1)[-1]
    if name in BY_NAME:
        return "line", BY_NAME[name]
    if name.startswith("Dockerfile"):
        return "line", "#"
    suffix = Path(rel).suffix
    if suffix in LINE_COMMENT:
        return "line", LINE_COMMENT[suffix]
    if suffix in BLOCK_COMMENT:
        return "block", BLOCK_COMMENT[suffix]
    return None


def is_excluded(rel: str) -> bool:
    if rel in EXCLUDE_EXACT:
        return True
    return any(p.search(rel) for p in EXCLUDE_PATTERNS)


def render(style) -> list[str]:
    kind, spec = style
    if kind == "line":
        # No trailing whitespace on the blank line -- it trips linters and
        # shows up as a diff artefact in every stamped file.
        return [(f"{spec} {ln}".rstrip()) for ln in HEADER]
    open_tok, mid, close_tok = spec
    return [open_tok] + [(mid + ln).rstrip() for ln in HEADER] + [close_tok]


_CODING = re.compile(r"^#.*coding[:=]\s*[-\w.]+")


def insertion_point(lines: list[str], rel: str) -> int:
    """Where the header may go WITHOUT breaking the file.

    Four things must stay on the first line to keep working: a shebang, a
    Python encoding declaration, an XML declaration, and YAML frontmatter.
    Twenty of this repo's markdown files are skill definitions whose frontmatter
    is parsed from line 1 -- prepend to those and the skill stops loading.
    """
    i = 0
    if lines and lines[0].startswith("#!"):
        i = 1
        # PEP 263 allows the encoding line on line 1 or 2 -- i.e. after a shebang.
        if i < len(lines) and _CODING.match(lines[i]):
            i += 1
        return i
    if lines and _CODING.match(lines[0]):
        return 1
    if lines and lines[0].lstrip().startswith("<?xml"):
        return 1
    if rel.endswith(".md") and lines and lines[0].strip() == "---":
        for j in range(1, len(lines)):
            if lines[j].strip() == "---":
                return j + 1
        # Unterminated frontmatter: leave it alone rather than guess.
        return 0
    return 0


def stamp(path: Path, rel: str, style, *, check: bool,
          replace_legacy: bool = False, rewrite: bool = False) -> bool:
    """Return True if the file needed a header. Writes unless check."""
    raw = path.read_bytes()
    if not raw.strip():
        return False
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        # Not text we can safely rewrite. Never guess an encoding on a file we
        # are about to overwrite.
        return False

    # A page someone saved from a browser is not authored source, wherever it
    # sits. Claiming copyright over the viewer markup and third-party bundles
    # inside it is the same mistake as stamping site-packages, just smaller.
    if SAVED_PAGE_BANNER.search(text[:2000]):
        return False

    bom = ""
    if text.startswith("﻿"):
        bom, text = "﻿", text[1:]

    lines = text.split("\n")
    removed = False
    if rewrite:
        # Replace an existing header of any vintage with the canonical one. The
        # normal path SKIPS a file that already has a header, which is correct
        # for stamping and useless when the header text itself changes.
        span = header_span(lines, style)
        if span:
            s, e = span
            if e < len(lines) and lines[e].strip() == "":
                e += 1
            lines = lines[:s] + lines[e:]
            removed = True
    elif replace_legacy:
        lines, removed = strip_legacy(lines)
    if not removed and any(MARKER in ln for ln in lines[:MARKER_SCAN_LINES]):
        return False
    if check:
        return True

    # Preserve the file's existing line endings. This tree is LF via
    # .gitattributes, but rewriting a CRLF file to LF would show up as a
    # whole-file diff and bury the one change that matters.
    crlf = text.count("\r\n") > text.count("\n") // 2 and "\r\n" in text
    body = [ln.rstrip("\r") for ln in lines]

    at = insertion_point(body, rel)
    block = render(style)
    # A blank line after the header, unless the file already starts with one.
    if at < len(body) and body[at].strip():
        block = block + [""]
    out = body[:at] + block + body[at:]

    eol = "\r\n" if crlf else "\n"
    # Compare before writing. --rewrite strips the existing header and re-adds
    # it, so on an already-canonical file the result is byte-identical; without
    # this it would rewrite all 1,300 files and report them as changed every
    # single run.
    new = (bom + eol.join(out)).encode("utf-8")
    if new == raw:
        return False
    path.write_bytes(new)
    return True


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--check", action="store_true",
                    help="report files missing headers and exit 1; write nothing")
    ap.add_argument("--list", action="store_true",
                    help="print every file that would be stamped, then exit")
    ap.add_argument("--replace-legacy", action="store_true",
                    help="rewrite an older PROPRIETARY / LicenseRef / BUSL header to Apache-2.0")
    ap.add_argument("--rewrite", action="store_true",
                    help="replace an existing header with the current one "
                         "(use when the header TEXT changes, e.g. the year)")
    # Restricting scope keeps a targeted repair from turning into a whole-tree
    # rewrite, and is what a pre-commit hook passes (the staged files).
    ap.add_argument("paths", nargs="*",
                    help="limit to these paths; default is every tracked file")
    args = ap.parse_args()

    selected = None
    if args.paths:
        selected = set()
        for raw in args.paths:
            p = Path(raw).resolve()
            try:
                selected.add(p.relative_to(REPO_ROOT).as_posix())
            except ValueError:
                print(f"  [skip] outside the repository: {raw}", file=sys.stderr)

    candidates = []
    for rel in tracked_files():
        if selected is not None and rel not in selected:
            continue
        if is_excluded(rel):
            continue
        style = comment_style(rel)
        if style is None:
            continue
        p = REPO_ROOT / rel
        if p.is_file():
            candidates.append((p, rel, style))

    if args.list:
        for _, rel, _ in candidates:
            print(rel)
        print(f"\n{len(candidates)} tracked file(s) in scope", file=sys.stderr)
        return 0

    missing = [rel for p, rel, st in candidates
               if stamp(p, rel, st, check=args.check,
                        replace_legacy=args.replace_legacy,
                        rewrite=args.rewrite)]

    scope = f"{len(candidates)} tracked file(s) in scope"
    if args.check:
        if missing:
            for rel in missing[:40]:
                print(f"  {rel}")
            if len(missing) > 40:
                print(f"  ... and {len(missing) - 40} more")
            print(f"\n{len(missing)} of {scope} are missing a licence header.")
            print("Run: python scripts/license_headers.py")
            return 1
        print(f"All {scope} carry a licence header.")
        return 0

    print(f"Stamped {len(missing)} file(s); {scope}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
