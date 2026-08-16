#!/usr/bin/env python3
# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""Tests for the licence stamper.

The risk here is not the header text -- it is placement. A shebang, a PEP 263
encoding line, an XML declaration and YAML frontmatter all have to stay on the
first line to keep working, and getting any of them wrong breaks the file
silently: the skill stops loading, the script stops executing, the parser stops
recognising the encoding. Every one of those cases has a test.

    python -m pytest scripts/test_license_headers.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import license_headers as lh  # noqa: E402


def write(tmp_path: Path, name: str, text: str, *, encoding="utf-8") -> Path:
    p = tmp_path / name
    p.write_bytes(text.encode(encoding))
    return p


def do(p: Path, rel: str | None = None) -> str:
    rel = rel or p.name
    style = lh.comment_style(rel)
    assert style is not None, f"no comment style for {rel}"
    lh.stamp(p, rel, style, check=False)
    return p.read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# Placement -- the cases that break files
# --------------------------------------------------------------------------

def test_plain_python_gets_header_first(tmp_path):
    out = do(write(tmp_path, "a.py", "import os\n"))
    assert out.startswith("# Copyright (c) 2026 Trustedwear Tech")
    assert "SPDX-License-Identifier: BUSL-1.1" in out
    assert out.rstrip().endswith("import os")


def test_shebang_stays_on_line_one(tmp_path):
    out = do(write(tmp_path, "s.sh", "#!/usr/bin/env bash\nset -e\n"))
    assert out.splitlines()[0] == "#!/usr/bin/env bash"
    assert "SPDX-License-Identifier" in out.splitlines()[3]


def test_python_encoding_line_survives(tmp_path):
    """PEP 263 only honours the coding line on line 1 or 2."""
    out = do(write(tmp_path, "e.py", "# -*- coding: utf-8 -*-\nx = 1\n"))
    assert out.splitlines()[0] == "# -*- coding: utf-8 -*-"


def test_shebang_then_encoding_both_survive(tmp_path):
    src = "#!/usr/bin/env python3\n# -*- coding: latin-1 -*-\nx = 1\n"
    out = do(write(tmp_path, "b.py", src))
    lines = out.splitlines()
    assert lines[0].startswith("#!")
    assert "coding" in lines[1]


def test_markdown_frontmatter_stays_first(tmp_path):
    """20 skill definitions in this repo parse frontmatter from line 1."""
    src = "---\nname: citra-self-test\ndescription: x\n---\n\n# Title\n"
    out = do(write(tmp_path, "SKILL.md", src))
    lines = out.splitlines()
    assert lines[0] == "---"
    assert lines[3] == "---"
    assert "<!--" in out
    assert out.index("<!--") > out.index("name: citra-self-test")


def test_markdown_without_frontmatter_gets_header_first(tmp_path):
    out = do(write(tmp_path, "d.md", "# Title\n"))
    assert out.startswith("<!--")


def test_unterminated_frontmatter_is_left_alone(tmp_path):
    """Ambiguous input: prepend rather than guess where the block ends."""
    out = do(write(tmp_path, "u.md", "---\nname: broken\n# Title\n"))
    assert out.startswith("<!--")


def test_xml_declaration_stays_first(tmp_path):
    out = do(write(tmp_path, "c.xml", '<?xml version="1.0"?>\n<a/>\n'))
    assert out.splitlines()[0].startswith("<?xml")


# --------------------------------------------------------------------------
# Comment syntax -- wrong choice produces a file that parses until it doesn't
# --------------------------------------------------------------------------

def test_css_uses_block_comment_not_double_slash(tmp_path):
    out = do(write(tmp_path, "a.css", "body { color: red; }\n"))
    assert out.startswith("/*")
    assert "*/" in out
    assert not out.startswith("//")


def test_sql_uses_double_dash(tmp_path):
    out = do(write(tmp_path, "a.sql", "SELECT 1;\n"))
    assert out.startswith("-- Copyright")


def test_dockerfile_recognised_without_extension(tmp_path):
    assert lh.comment_style("Dockerfile") == ("line", "#")
    assert lh.comment_style("Dockerfile.dev") == ("line", "#")


def test_json_is_not_stampable(tmp_path):
    assert lh.comment_style("package.json") is None


# --------------------------------------------------------------------------
# Safety
# --------------------------------------------------------------------------

def test_idempotent(tmp_path):
    p = write(tmp_path, "i.py", "x = 1\n")
    first = do(p)
    second = do(p)
    assert first == second
    assert first.count("SPDX-License-Identifier") == 1


def test_recognises_an_older_header_and_does_not_double_stamp(tmp_path):
    """Detection is by SPDX tag, so a re-worded header still counts."""
    src = ("# Copyright (c) 2024-2026 Trustedwear Tech Private Limited\n"
           "# SPDX-License-Identifier: LicenseRef-Citra-AI-Proprietary\n"
           "x = 1\n")
    p = write(tmp_path, "old.py", src)
    assert lh.stamp(p, "old.py", ("line", "#"), check=True) is False


def test_crlf_is_preserved(tmp_path):
    p = write(tmp_path, "w.py", "import os\r\nx = 1\r\n")
    do(p)
    raw = p.read_bytes()
    assert b"\r\n" in raw
    assert b"\n\n" not in raw.replace(b"\r\n", b"")


def test_bom_stays_first(tmp_path):
    p = write(tmp_path, "bom.py", "﻿x = 1\n")
    do(p)
    raw = p.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    assert b"Copyright" in raw


def test_empty_file_is_skipped(tmp_path):
    p = write(tmp_path, "empty.py", "")
    assert lh.stamp(p, "empty.py", ("line", "#"), check=False) is False
    assert p.read_bytes() == b""


def test_binary_is_never_rewritten(tmp_path):
    p = tmp_path / "b.py"
    p.write_bytes(b"\xff\xfe\x00\x01binary")
    before = p.read_bytes()
    assert lh.stamp(p, "b.py", ("line", "#"), check=False) is False
    assert p.read_bytes() == before


def test_check_mode_writes_nothing(tmp_path):
    p = write(tmp_path, "c.py", "x = 1\n")
    before = p.read_bytes()
    assert lh.stamp(p, "c.py", ("line", "#"), check=True) is True
    assert p.read_bytes() == before


def test_generated_and_vendored_paths_are_excluded():
    assert lh.is_excluded("citra-app-runtime/next-env.d.ts")
    assert lh.is_excluded("bank-demo/node_modules/x/index.js")
    assert lh.is_excluded("bank-demo/.next/server/page.js")
    assert lh.is_excluded("x/dist/bundle.js")
    assert lh.is_excluded("a/b.min.js")
    assert not lh.is_excluded("smart-app-service/main.py")


def test_browser_saved_page_is_never_stamped(tmp_path):
    """docs/ holds saved renderings wrapped in someone else's viewer markup."""
    src = ('<!DOCTYPE html>\n'
           '<!-- saved from url=(0253)https://example.frame.usercontent.com/x -->\n'
           '<html><body>hi</body></html>\n')
    p = write(tmp_path, "saved_resource.html", src)
    before = p.read_bytes()
    assert lh.stamp(p, "saved_resource.html",
                    ("block", ("<!--", "  ", "-->")), check=False) is False
    assert p.read_bytes() == before


def test_generated_runtime_snapshot_is_excluded():
    """vendor-runtime-reference.ps1 regenerates this; a stamp here is churn."""
    assert lh.is_excluded(
        "smart-app-service/skills/citra-system/runtime-reference/executor/models.py")


def test_browser_saved_asset_directory_is_excluded():
    assert lh.is_excluded("docs/Guide_files/saved_resource.html")


def test_no_trailing_whitespace_in_rendered_header():
    for kind in (("line", "#"), ("block", ("/*", " * ", " */"))):
        for line in lh.render(kind):
            assert line == line.rstrip(), repr(line)


# --------------------------------------------------------------------------
# Phase 2 -- replacing the legacy PROPRIETARY header
# --------------------------------------------------------------------------

LEGACY = (
    "# Copyright (c) 2024-2026 Trustedwear Tech Private Limited (https://citra-ai.com)\n"
    "# PROPRIETARY - all rights reserved. See LICENSE.md. NOT an open-source grant.\n"
    "# SPDX-License-Identifier: LicenseRef-Citra-AI-Proprietary\n"
)


def test_legacy_header_is_replaced_not_duplicated(tmp_path):
    p = write(tmp_path, "l.py", LEGACY + "x = 1\n")
    lh.stamp(p, "l.py", ("line", "#"), check=False, replace_legacy=True)
    out = p.read_text(encoding="utf-8")
    assert "PROPRIETARY" not in out
    assert "LicenseRef" not in out
    assert out.count("SPDX-License-Identifier") == 1
    assert "BUSL-1.1" in out
    assert out.rstrip().endswith("x = 1")


def test_legacy_replacement_keeps_the_shebang(tmp_path):
    src = "#!/usr/bin/env python3\n" + LEGACY + "x = 1\n"
    p = write(tmp_path, "ls.py", src)
    lh.stamp(p, "ls.py", ("line", "#"), check=False, replace_legacy=True)
    out = p.read_text(encoding="utf-8")
    assert out.splitlines()[0] == "#!/usr/bin/env python3"
    assert "PROPRIETARY" not in out


def test_wrong_entity_is_replaced(tmp_path):
    """Only Trustedwear Tech Private Limited can hold the copyright."""
    src = ("#!/usr/bin/env bash\n"
           "# Copyright (c) 2024-2026 Citra AI (https://github.com/Citra-AI)\n"
           "# PROPRIETARY - all rights reserved. See LICENSE.md. NOT an open-source grant.\n"
           "# SPDX-License-Identifier: LicenseRef-Citra-AI-Proprietary\n"
           "echo hi\n")
    p = write(tmp_path, "w.sh", src)
    lh.stamp(p, "w.sh", ("line", "#"), check=False, replace_legacy=True)
    out = p.read_text(encoding="utf-8")
    assert "github.com/Citra-AI" not in out
    assert "Trustedwear Tech Private Limited" in out


def test_documentation_quoting_the_old_header_is_not_gutted(tmp_path):
    """CONTRIBUTING.md quotes the old header in a fenced block far down the file.

    A blind whole-file match would delete those lines and silently destroy the
    documentation instead of letting it be rewritten on purpose.
    """
    body = "\n".join(f"line {i}" for i in range(30))
    src = f"# Contributing\n\n{body}\n\n```python\n{LEGACY}```\n"
    p = write(tmp_path, "CONTRIBUTING.md", src)
    lh.stamp(p, "CONTRIBUTING.md", ("block", ("<!--", "  ", "-->")),
             check=False, replace_legacy=True)
    out = p.read_text(encoding="utf-8")
    assert "PROPRIETARY - all rights reserved" in out, "code block was gutted"


def test_replace_legacy_is_a_noop_on_a_correct_header(tmp_path):
    p = write(tmp_path, "ok.py", "x = 1\n")
    do(p)
    before = p.read_bytes()
    lh.stamp(p, "ok.py", ("line", "#"), check=False, replace_legacy=True)
    assert p.read_bytes() == before


def test_copyright_year_matches_when_the_work_was_created():
    """2026 -- not 2024-2026, and not the company's 2022 incorporation year.

    The year records authorship, not incorporation. The first commit in the
    private repository this tree was cut from is 2026-01-28, with nothing in
    2024 or 2025, so any earlier range claimed years nothing evidences.
    """
    text = "\n".join(lh.HEADER)
    assert "Copyright (c) 2026 Trustedwear Tech Private Limited" in text
    assert "2024" not in text
    assert "2022" not in text


OLD_YEAR_HEADER = (
    "# Copyright (c) 2024-2026 Trustedwear Tech Private Limited (https://citra-ai.com)\n"
    "# Author: Rohit Kumar Chandan\n"
    "# SPDX-License-Identifier: BUSL-1.1\n"
    "#\n"
    "# Licensed under the Business Source License 1.1. Non-production use is granted;\n"
    "# production use requires a commercial licence until the Change Date, after\n"
    "# which this file converts to Apache-2.0. See LICENSE at the repository root.\n"
    "\n"
)


def test_rewrite_replaces_an_older_header_in_place(tmp_path):
    """A change to the header TEXT must replace it, not be skipped.

    The normal path detects an SPDX tag and moves on, which is right for
    stamping and useless the day the wording or the year changes.
    """
    p = write(tmp_path, "r.py", OLD_YEAR_HEADER + "import os\n")
    assert lh.stamp(p, "r.py", ("line", "#"), check=False, rewrite=True) is True
    out = p.read_text(encoding="utf-8")
    assert out.count("SPDX-License-Identifier") == 1
    assert "2024-2026" not in out
    assert out.startswith("# Copyright (c) 2026 Trustedwear Tech")
    assert out.rstrip().endswith("import os")


def test_rewrite_keeps_the_shebang_and_the_body(tmp_path):
    src = "#!/usr/bin/env python3\n" + OLD_YEAR_HEADER + "import os\n"
    p = write(tmp_path, "rs.py", src)
    lh.stamp(p, "rs.py", ("line", "#"), check=False, rewrite=True)
    out = p.read_text(encoding="utf-8")
    assert out.splitlines()[0] == "#!/usr/bin/env python3"
    assert out.count("SPDX-License-Identifier") == 1
    assert "2024-2026" not in out


def test_rewrite_is_a_noop_when_already_canonical(tmp_path):
    p = write(tmp_path, "n.py", "import os\n")
    do(p)
    before = p.read_bytes()
    assert lh.stamp(p, "n.py", ("line", "#"), check=False, rewrite=True) is False
    assert p.read_bytes() == before


def test_rewrite_leaves_a_third_partys_header_alone(tmp_path):
    """The rewrite is gated on OUR SPDX values. Someone else's notice stands."""
    src = ("# Copyright (c) 2019 Some Other Company\n"
           "# SPDX-License-Identifier: Apache-2.0\n"
           "x = 1\n")
    p = write(tmp_path, "third.py", src)
    lh.stamp(p, "third.py", ("line", "#"), check=False, rewrite=True)
    out = p.read_text(encoding="utf-8")
    assert "Some Other Company" in out
    assert "Apache-2.0" in out
    assert "Trustedwear" not in out


def test_rewrite_of_css_does_not_orphan_the_closing_delimiter(tmp_path):
    """CSS closes with " */" -- a leading space the markdown case does not have.

    Comparing a stripped file line against the unstripped spec never matched, so
    the closer survived, a fresh block was inserted above it, and every rerun
    added another orphan "*/". Seven stylesheets picked up two each before this
    was caught by reading the diff.
    """
    p = write(tmp_path, "a.css", "body { color: red; }\n")
    style = ("block", ("/*", " * ", " */"))
    do(p)
    for _ in range(3):
        lh.stamp(p, "a.css", style, check=False, rewrite=True)
    out = p.read_text(encoding="utf-8")
    assert out.count("*/") == 1, out[:400]
    assert out.count("/*") == 1
    assert out.count("SPDX-License-Identifier") == 1
    assert out.rstrip().endswith("body { color: red; }")


def test_rewrite_handles_a_block_comment_header(tmp_path):
    """Markdown and CSS headers have delimiter lines to remove as well."""
    p = write(tmp_path, "d.md", "# Title\n")
    do(p)
    # already canonical after do(p)
    assert lh.stamp(p, "d.md", ("block", ("<!--", "  ", "-->")),
                    check=False, rewrite=True) is False
    out = p.read_text(encoding="utf-8")
    assert out.count("SPDX-License-Identifier") == 1
    assert out.count("<!--") == 1 and out.count("-->") == 1


def test_header_states_busl_not_proprietary():
    """The defect this rewrite exists to fix."""
    text = "\n".join(lh.HEADER)
    assert "BUSL-1.1" in text
    assert "Trustedwear Tech Private Limited" in text
    assert "Rohit Kumar Chandan" in text
    assert "PROPRIETARY" not in text.upper()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
