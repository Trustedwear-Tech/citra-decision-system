# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""Drift guard — builder contract ↔ runtime renderer (panel types).

The runtime (`citra-app-runtime/src/components/PanelRenderer.tsx`) renders a
fixed set of `panel.type` cases and **fails loud** on anything else. The builder
authors specs against `static_checks.KNOWN_PANEL_TYPES` (mirrored in the
`citra-ui-panels` skill + the AppSpec schema). If those two lists drift, the
builder either can't use a real panel type or authors one the runtime rejects at
render — a spec that "publishes" but shows an error.

This is a CODE / CI test ONLY — it runs after a code change to keep the
schema ↔ model ↔ renderer ↔ skill contract honest. It is NOT invoked by the
builder pod or the runtime; nothing in the build/publish/render path calls it.

Skips cleanly when the sibling runtime repo isn't checked out (so the
smart-app-service test suite can run standalone).
"""
import os
import re
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_SVC = os.path.dirname(_HERE)                 # smart-app-service/
_REPO = os.path.dirname(_SVC)                 # repo root
sys.path.insert(0, os.path.join(_SVC, "builder-workspace"))

from static_checks import KNOWN_PANEL_TYPES   # the builder's contract list

_RENDERER = os.path.join(
    _REPO, "citra-app-runtime", "src", "components", "PanelRenderer.tsx",
)


def _runtime_panel_types() -> set:
    """Parse the `switch (panel.type)` cases the runtime actually renders."""
    src = open(_RENDERER, encoding="utf-8").read()
    m = re.search(r"switch\s*\(\s*panel\.type\s*\)\s*\{", src)
    assert m, "could not locate `switch (panel.type)` in PanelRenderer.tsx"
    block = src[m.end():]
    # The panel.type switch ends at its `default:`; scope parsing to that block
    # so unrelated `case` labels elsewhere in the file can't leak in.
    end = block.find("default:")
    block = block[:end] if end != -1 else block[:2000]
    return set(re.findall(r'case\s+"([a-z_]+)"', block))


@pytest.mark.skipif(
    not os.path.exists(_RENDERER),
    reason="citra-app-runtime not checked out alongside smart-app-service",
)
def test_panel_types_match_runtime():
    runtime = _runtime_panel_types()
    builder = set(KNOWN_PANEL_TYPES)
    assert runtime, "no panel-type cases parsed from PanelRenderer.tsx"

    builder_only = builder - runtime   # builder would author it; runtime can't render it
    runtime_only = runtime - builder   # runtime renders it; builder won't author it

    assert not builder_only, (
        "builder KNOWN_PANEL_TYPES has panel types the runtime renderer does NOT "
        f"handle (would fail loud at render): {sorted(builder_only)} — remove them "
        "from static_checks.KNOWN_PANEL_TYPES + the citra-ui-panels skill + schema, "
        "or add the case to PanelRenderer.tsx."
    )
    assert not runtime_only, (
        "runtime PanelRenderer handles panel types the builder does NOT know "
        f"(builder can't author them): {sorted(runtime_only)} — add them to "
        "static_checks.KNOWN_PANEL_TYPES + the citra-ui-panels skill + the AppSpec "
        "schema panel `type` enum."
    )


if __name__ == "__main__":
    test_panel_types_match_runtime()
    print("PASS: builder KNOWN_PANEL_TYPES == runtime PanelRenderer panel types:",
          sorted(KNOWN_PANEL_TYPES))
