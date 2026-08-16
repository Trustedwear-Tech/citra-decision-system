# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""Draft-then-review catalogue descriptions (Wave 2 #9).

The crawl itself calls no LLM — it carries names and descriptions through
verbatim. This module is the on-demand path that adds drafted descriptions
with a human in the loop: a DBA drafts (enricher.enrich_dataset), reviews and
edits, then APPLIES approved descriptions to the catalogue — which flow
straight into the NL→SQL planner's system prompt
(runtime._render_dataset_directory_block), so better descriptions = better
answers. This shrinks the onboarding curation clock: the DBA corrects a draft
instead of writing from scratch.

This module holds the PURE cores (shape a draft for review; merge approved
descriptions onto a catalogue entry). The LLM draft is enricher.enrich_dataset
(reused); the Mongo read/write + auth live in main.py's endpoints.

INVARIANT preserved from the crawl: a description edit NEVER renames a column —
``name``/``physical_name`` are what the runtime binds and queries by; only
``description`` changes. Approved edits are marked mapping_status="approved",
mapping_source="manual" so they win over future LLM re-drafts.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

DESC_TABLE_MAX = 400   # runtime truncates the table desc at 400 in the prompt
DESC_COL_MAX = 240


def _trim(s: Any, n: int) -> str:
    t = str(s or "").strip()
    return t if len(t) <= n else t[: n - 1] + "…"


def draft_to_response(dataset_id: str, draft: Dict[str, Any]) -> Dict[str, Any]:
    """Shape enrich_dataset output ({table:{...}, columns:{physical:{...}}}) into
    a flat review payload: the proposed table description + per-column proposals,
    each with the model's confidence so the DBA can triage low-confidence ones."""
    table = draft.get("table") or {}
    cols = draft.get("columns") or {}
    return {
        "dataset_id": dataset_id,
        "table_description": table.get("description") or "",
        "table_confidence": table.get("confidence"),
        "columns": [
            {"physical_name": pname,
             "description": (v or {}).get("description") or "",
             "confidence": (v or {}).get("confidence")}
            for pname, v in cols.items()
        ],
    }


def merge_descriptions(
    entry: Dict[str, Any],
    *,
    table_description: Optional[str],
    column_descriptions: Dict[str, str],
    actor: str,
    at: datetime,
) -> Tuple[Dict[str, Any], List[str], List[str]]:
    """Pure: apply DBA-approved descriptions onto a catalogue entry dict.

    Returns ``(update, matched, unmatched)`` where ``update`` is the ``$set``
    body, ``matched`` are the column keys applied, and ``unmatched`` are approved
    keys that matched no column (the caller surfaces these — a typo'd column
    must not be silently dropped). Columns are matched by ``physical_name`` then
    ``name``; descriptions are trimmed to the catalogue budget. Marks the entry
    approved + manually-sourced. Column identifiers are never changed."""
    cols = [dict(c) for c in (entry.get("columns") or [])]
    remaining = dict(column_descriptions or {})
    matched: List[str] = []
    for c in cols:
        for key in (c.get("physical_name"), c.get("name")):
            if key and key in remaining:
                c["description"] = _trim(remaining.pop(key), DESC_COL_MAX)
                matched.append(key)
                break
    unmatched = list(remaining.keys())

    update: Dict[str, Any] = {
        "columns": cols,
        "mapping_status": "approved",
        "mapping_source": "manual",
        "descriptions_edited_by": actor,
        "descriptions_edited_at": at,
    }
    if table_description is not None:
        update["description"] = _trim(table_description, DESC_TABLE_MAX)
    return update, matched, unmatched
