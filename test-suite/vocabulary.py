# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""The finite building blocks of a Citra Decision App — the single source of
truth for coverage across all four layers.

An app is a *composition* of this vocabulary. Coverage is measured as "every
element exercised at least once, plus the pairwise combinations that interact" —
NOT "every possible app". Each layer's tests emit which elements/cells they hit;
``coverage_report.py`` diffs that against these registries.

Keep this in sync with the code contracts:
  panel types  → smart-app-service/models.py  (Panel discriminated union)
  tool kinds   → smart-app-service/models.py  (tools_v2 discriminated union)
  source kinds → source-mcp-template/models.py (DatasetKind)
  validators   → smart-app-service/publish_validators.py + main.py (_collect ids)
"""
from __future__ import annotations

from itertools import combinations
from typing import Dict, List, Set, Tuple

# ── Layer 3 (UI) render vocabulary ──────────────────────────────────────────
# The runtime's real Panel discriminated union (PanelRenderer switch). There is
# NO "table" panel — "queue" IS the tabular/list panel (view="table" is a queue
# presentation option, not a panel type). Verified against
# citra-app-runtime/src/components/PanelRenderer.tsx.
PANEL_TYPES: List[str] = [
    "queue", "detail", "form", "dashboard", "chart",
    "agent_chat", "document_view", "markdown", "notice",
    "calendar", "map", "filter_bar", "notifications",
]
# Panels that fetch columnar panel data (GET /apps/{slug}/data|detail|document).
# Only these have the negative data states (source_error/unauthorized/
# non_columnar) — a static/self-fetching panel can't have a data-source error.
DATA_PANELS: Set[str] = {
    "queue", "detail", "chart", "document_view", "dashboard", "calendar", "map",
}
DETAIL_SECTIONS: List[str] = [
    "fields", "attachment", "documents", "agent_timeline",
    "approval", "markdown", "agent_chat", "comments",
]
# The data states a panel must render correctly. The negatives (source_error,
# unauthorized, non_columnar) are the fail-loud guarantees — a broken/empty panel
# must never silently render blank (RULE #1).
DATA_STATES: List[str] = [
    "loading", "empty", "single_row", "many_rows", "truncated",
    "source_error", "unauthorized", "non_columnar",
]
INTERACTIONS: List[str] = [
    "click_row", "submit", "filter", "sort", "paginate",
    "navigate", "approve", "override", "reject", "media_open",
]
# Which interactions are legal on which panel — keeps the matrix to REAL cells.
# Only per-panel affordances that exist IN the panel and are drivable in isolation.
# chart/dashboard have no own filter UI (a page-level filter_bar drives them via
# URL params — covered by the filter_bar cell, not here); calendar month-nav and
# map marker-click aren't row interactions. Verified against PanelRenderer.tsx.
PANEL_INTERACTIONS: Dict[str, List[str]] = {
    # override = edit-a-recommendation-then-apply, which lives in the queue's
    # RunResultModal (a card carrying `_recommendation`), not the detail panel.
    "queue": ["click_row", "filter", "sort", "paginate", "navigate", "override"],
    "detail": ["approve", "reject", "media_open"],
    # auth_carry: a fresh tab's FIRST action is a navigate-only submit — the
    # ?_t= handoff must be mirrored into the SSR cookie before navigation
    # (prod bug 2026-07-04: lazy capture lost auth -> 401 -> error boundary).
    "form": ["submit", "navigate", "auth_carry"],
    "agent_chat": ["submit"],
    "document_view": ["media_open"],
    "notifications": ["click_row", "navigate"],
    "filter_bar": ["filter"],
    "chart": [],
    "dashboard": [],
    "markdown": [],
    "notice": [],
    "calendar": [],
    "map": [],
}

# ── Layer 1 (MCP) contract vocabulary ───────────────────────────────────────
SOURCE_KINDS: List[str] = [
    "sql", "rest", "odata", "soql", "mongodb", "semantic", "bigquery", "sap_rfc", "duckdb",
]
MCP_OPS: List[str] = ["run_query", "execute_action", "media", "describe_dataset", "list_datasets"]
# States every connector op must handle and SURFACE (never swallow).
MCP_STATES: List[str] = [
    "happy", "empty", "missing_param", "upstream_error", "non_json", "ssrf_refused", "timeout",
]

# ── Layer 2 (Builder) tool vocabulary + publish validators ──────────────────
TOOL_KINDS: List[str] = [
    "mcp", "mcp_action", "rag", "llm", "validate_form", "vision_ocr",
    "code_exec", "neighbor_samples", "image_analyze", "doc_extract",
    "consistency_check", "fraud_synthesis",
]
PUBLISH_VALIDATORS: List[str] = [
    "H-04", "T-03", "G-01", "W-01", "W-06", "D-02", "E-03", "F-01", "S-01",
    "V-CHART-01", "update_identifier", "mcp_action_input_schema",
    "data_binding", "layer_b_schema",
]

# ── Layer 4 (Memory / learning) vocabulary ──────────────────────────────────
DECISION_TRANSITIONS: List[str] = [
    "recommend", "approve", "override", "reject", "direct",
    "auto_recommend", "auto_process",
]
OUTCOME_CLASSES: List[str] = ["good", "bad", "neutral"]
RUBRIC_SIGNALS: List[str] = [
    "few_shot_grounding", "outcome_downweight", "threshold_shift",
    "drift_detected", "record_binding_match",
]

# ── Coverage model ──────────────────────────────────────────────────────────
# Each layer defines its "cells" — the atoms coverage is measured over. Pairwise
# where two dimensions interact; single where they don't.
def ui_cells() -> Set[Tuple[str, str, str]]:
    """Legal (panel, state, interaction) cells for the UI rendering matrix.
    ``interaction`` = "-" for the render-only pass (state without an action)."""
    cells: Set[Tuple[str, str, str]] = set()
    for panel in PANEL_TYPES:
        # data panels must render every state (incl. the fail-loud negatives);
        # a static/self-fetching panel only needs to render its content once.
        states = DATA_STATES if panel in DATA_PANELS else ["single_row"]
        for state in states:
            cells.add((panel, state, "-"))            # pure render
        for interaction in PANEL_INTERACTIONS.get(panel, []):
            cells.add((panel, "single_row", interaction))  # interaction on a populated panel
    return cells


def mcp_cells() -> Set[Tuple[str, str, str]]:
    """(kind, op, state). Only ops/states that apply to a kind."""
    cells: Set[Tuple[str, str, str]] = set()
    for kind in SOURCE_KINDS:
        for op in ("run_query", "describe_dataset", "list_datasets"):
            for st in ("happy", "empty", "upstream_error"):
                cells.add((kind, op, st))
        # param + ssrf only meaningful for parameterised/networked kinds
        if kind in ("rest", "odata", "soql"):
            for st in ("missing_param", "non_json", "timeout"):
                cells.add((kind, "run_query", st))
        if kind == "rest":
            cells.add((kind, "media", "ssrf_refused"))
        cells.add((kind, "execute_action", "happy"))
    return cells


def memory_cells() -> Set[Tuple[str, str]]:
    """(rubric signal × outcome class) where relevant, + drift."""
    cells: Set[Tuple[str, str]] = set()
    for sig in RUBRIC_SIGNALS:
        for oc in OUTCOME_CLASSES:
            cells.add((sig, oc))
    return cells


def pairwise(a: List[str], b: List[str]) -> Set[Tuple[str, str]]:
    return {(x, y) for x in a for y in b}


LAYER_TOTALS = {
    "mcp": lambda: len(mcp_cells()),
    "builder_validators": lambda: len(PUBLISH_VALIDATORS),
    "builder_tool_kinds": lambda: len(TOOL_KINDS),
    "ui": lambda: len(ui_cells()),
    "memory": lambda: len(memory_cells()),
}


if __name__ == "__main__":
    print("Vocabulary totals (the finite universe to cover):")
    print(f"  panel types        : {len(PANEL_TYPES)}")
    print(f"  detail sections    : {len(DETAIL_SECTIONS)}")
    print(f"  tool kinds         : {len(TOOL_KINDS)}")
    print(f"  source kinds       : {len(SOURCE_KINDS)}")
    print(f"  publish validators : {len(PUBLISH_VALIDATORS)}")
    print()
    print("Coverage cell counts (the denominators):")
    print(f"  UI rendering matrix cells : {len(ui_cells())}")
    print(f"  MCP contract cells        : {len(mcp_cells())}")
    print(f"  Memory cells              : {len(memory_cells())}")
