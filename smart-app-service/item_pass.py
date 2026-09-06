# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""The deterministic item pass: the RUNTIME reviews every item, not the model.

Until this existed, every document, image and API check was reviewed only
because the system prompt told the model to call the tool. A model that read
three of five documents produced three findings, all reviewable, and no sign of
the two it skipped. The evidence gate could not catch it either: it proved
"opened at least one", and on a child table (documents keyed by document_id,
writes anchored on claim_id) it could not even prove that.

This pass runs BEFORE the model reasons. For every bound media tool it
enumerates the items that belong to the anchor record - the rows of the tool's
dataset whose parent column equals the anchor - and dispatches the tool once
per item, through the same dispatcher the model would have used, so a finding
produced here is indistinguishable from one the model asked for. The findings
are handed to the model as evidence it must reason over, seeded into the
within-run cache so a repeat call is deduped, and recorded on the read ledger
as EXPECTED items, which is what the coverage gate then checks.

What it refuses to guess: if the items cannot be enumerated - the parent
column is not on the dataset, the read fails, the dataset kind has no
structured read - it says so on the ledger and the gate reports it. An
unknown coverage is not a pass.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Set

logger = logging.getLogger(__name__)

#: The projection a finding keeps, shared with the tool loop so a runtime-made
#: finding and a model-made one carry exactly the same fields.
ITEM_FINDING_KEYS = (
    "item_id", "item_type", "modality", "fields",
    "recommendation", "confidence", "rationale",
    "citations", "rubric_version", "subject",
    "artifact_flags",
    "content_sha256", "media_ref",
    "precedents_used",
    "factor_id", "score", "band", "clauses_fired",
    "sop_fingerprint",
)

MEDIA_KINDS = ("image_analyze", "doc_extract")

#: Anchor-row fields worth quoting to a per-item tool as case context. Bounded:
#: the tool's prompt is per item, and the row can be wide.
_CONTEXT_FIELDS = 12
_CONTEXT_VALUE_CHARS = 80


@dataclass
class ItemPassResult:
    findings: List[Dict[str, Any]] = field(default_factory=list)
    #: (tool, args) -> result, in the tool loop's own cache key form.
    cache: Dict[str, Any] = field(default_factory=dict)
    #: System block for the model: what was reviewed, so it reasons over it and
    #: does not call the tools again.
    block: str = ""
    timeline: List[Dict[str, Any]] = field(default_factory=list)
    #: tool -> {"expected": n, "produced": n, "missing": [...], "error": str|None}
    coverage: Dict[str, Dict[str, Any]] = field(default_factory=dict)


def finding_from_tool_result(tool_result: Any) -> Optional[Dict[str, Any]]:
    """The finding a successful item-tool result carries, or None."""
    if not isinstance(tool_result, dict) or tool_result.get("error") or not tool_result.get("item_id"):
        return None
    return {k: tool_result.get(k) for k in ITEM_FINDING_KEYS}


def cache_key(tool_name: str, args: Dict[str, Any]) -> str:
    """Identical to the tool loop's ``_cache_key`` so a repeat call dedupes."""
    return f"{tool_name}|{json.dumps(args, sort_keys=True, default=str)}"


def _tool_attr(tool: Any, name: str, default: Any = None) -> Any:
    if isinstance(tool, dict):
        return tool.get(name, default)
    return getattr(tool, name, default)


def case_context(anchors: Sequence[Any], anchor_row: Optional[Dict[str, Any]], action_name: str) -> str:
    """One paragraph a per-item tool needs to judge an item in context."""
    parts = [f"Case under {action_name}: " + ", ".join(f"{a.field}={a.value}" for a in anchors) + "."]
    if isinstance(anchor_row, dict):
        shown = 0
        bits = []
        for k, v in anchor_row.items():
            if v is None or isinstance(v, (dict, list)):
                continue
            s = str(v)
            bits.append(f"{k}={s[:_CONTEXT_VALUE_CHARS]}")
            shown += 1
            if shown >= _CONTEXT_FIELDS:
                break
        if bits:
            parts.append("Record: " + "; ".join(bits) + ".")
    parts.append("Review this item against the policy and say whether it supports the case as filed.")
    return " ".join(parts)


def _dataset_ref_for(app_spec: Any, ds_id: Optional[str]) -> Optional[str]:
    if not ds_id:
        return None
    for _ds in (getattr(app_spec, "data_sources", None) or []):
        _id = _ds.get("id") if isinstance(_ds, dict) else getattr(_ds, "id", None)
        if _id == ds_id:
            return _ds.get("ref") if isinstance(_ds, dict) else getattr(_ds, "ref", None)
    return None


def _kind_for_ref(agent_spec: Any, ref: str) -> str:
    """The dataset kind, from any mcp tool bound to the same dataset; sql otherwise."""
    for t in (getattr(agent_spec, "tools_v2", None) or []):
        if _tool_attr(t, "kind") == "mcp" and _tool_attr(t, "dataset_id") == ref:
            k = _tool_attr(t, "dataset_kind")
            if k:
                return str(k).lower()
    return "sql"


async def _enumerate_items(
    *, settings: Any, user_jwt: Optional[str], source_id: str, dataset_ref: str,
    kind: str, parent_field: str, parent_value: str, key_field: str, cap: int,
) -> tuple[List[Dict[str, Any]], Optional[str]]:
    """Rows of the item dataset that belong to the anchor. ``(rows, error)``."""
    from panel_data import _SQL_QUERY_KINDS, _build_select_sql
    from proxy_clients import call_dept_mcp_read

    table = dataset_ref.split(".", 1)[1] if "." in dataset_ref else dataset_ref
    query: Any
    if kind in _SQL_QUERY_KINDS:
        query = _build_select_sql(table, {parent_field: parent_value}, cap + 1)
    elif kind == "mongodb":
        query = {parent_field: parent_value}
    else:
        return [], (f"dataset kind {kind!r} has no structured list read; the runtime "
                    f"cannot enumerate its items")
    try:
        resp = await call_dept_mcp_read(
            settings=settings, user_jwt=user_jwt, source_id=source_id,
            dataset_id=dataset_ref, kind=kind, query=query, row_limit=cap + 1,
        )
    except Exception as exc:  # noqa: BLE001 - surfaced as coverage error, never swallowed
        return [], f"could not read {dataset_ref} for {parent_field}={parent_value!r}: {exc}"
    if isinstance(resp, dict) and resp.get("error"):
        return [], f"{dataset_ref} read returned an error: {resp['error']}"
    rows = [r for r in ((resp or {}).get("rows") or []) if isinstance(r, dict)]
    if rows and key_field not in rows[0]:
        return [], (f"{dataset_ref} rows carry no {key_field!r} column - the tool's key_field "
                    f"does not match the dataset")
    return rows, None


async def run_item_pass(
    *,
    settings: Any,
    agent_spec: Any,
    app_spec: Any,
    dispatch_table: Dict[str, Dict[str, Any]],
    anchors: Sequence[Any],
    anchor_row: Optional[Dict[str, Any]],
    action_name: str,
    auth_header: Optional[str],
    ledger: Any,
    correlation_id: str,
) -> ItemPassResult:
    """Enumerate and review every item of every bound media tool."""
    from tools_v2_dispatch import dispatch_tools_v2_call

    out = ItemPassResult()
    if not anchors:
        return out
    user_jwt = (auth_header or "").removeprefix("Bearer ").strip() or None
    cap = int(getattr(settings, "item_pass_max_items", 25) or 25)
    ctx = case_context(anchors, anchor_row, action_name)
    lines: List[str] = []

    for tname, entry in dispatch_table.items():
        if not isinstance(entry, dict) or entry.get("kind") not in MEDIA_KINDS:
            continue
        ds_alias = entry.get("data_source_id")
        key_field = entry.get("key_field")
        if not ds_alias or not key_field:
            continue  # unbound: nothing to enumerate, the model may still call it
        ref = _dataset_ref_for(app_spec, ds_alias)
        if not ref:
            ledger.note_enumeration_failed(tname, f"data_source {ds_alias!r} has no catalogue ref")
            out.coverage[tname] = {"expected": 0, "produced": 0, "missing": [],
                                   "error": f"data_source {ds_alias!r} has no catalogue ref"}
            continue
        source_id = ref.split(".", 1)[0]
        kind = _kind_for_ref(agent_spec, ref)
        parent_field = entry.get("parent_key") or anchors[0].field
        parent_value = anchors[0].value

        rows, err = await _enumerate_items(
            settings=settings, user_jwt=user_jwt, source_id=source_id, dataset_ref=ref,
            kind=kind, parent_field=parent_field, parent_value=parent_value,
            key_field=key_field, cap=cap,
        )
        if err:
            logger.error("[RUN %s] item pass: %s could not enumerate items - %s",
                         correlation_id, tname, err)
            ledger.note_enumeration_failed(tname, err)
            out.coverage[tname] = {"expected": 0, "produced": 0, "missing": [], "error": err}
            out.timeline.append({"step": "item_pass", "status": "error", "tool": tname, "detail": err})
            continue

        keys = []
        for r in rows:
            v = r.get(key_field)
            if v is not None and str(v).strip():
                keys.append(str(v).strip())
        over_cap = len(keys) > cap
        if over_cap:
            keys = keys[:cap]
        ledger.note_expected_items(tname, keys)
        produced: Set[str] = set()

        for k in keys:
            args = {"record_id": k, "item_id": k, "query": ctx}
            try:
                res = await dispatch_tools_v2_call(
                    settings=settings, agent_spec=agent_spec, app_spec=app_spec,
                    dispatch_table=dispatch_table, tool_name=tname, arguments=args,
                    auth_header=auth_header,
                )
            except Exception as exc:  # noqa: BLE001 - one bad item must not hide the rest
                res = {"error": f"item tool raised: {exc}"}
            out.cache[cache_key(tname, args)] = res
            ledger.note_media_read(tool_name=tname, record_id=k)
            f = finding_from_tool_result(res)
            if f is None:
                why = (res or {}).get("error") if isinstance(res, dict) else "no finding"
                logger.error("[RUN %s] item pass: %s(%s) produced no finding - %s",
                             correlation_id, tname, k, why)
                lines.append(f"- {tname} on {k}: FAILED - {str(why)[:160]}")
                continue
            produced.add(k)
            ledger.note_item_finding(tool_name=tname, item_id=k)
            out.findings.append(f)
            lines.append(
                f"- {tname} on {k}: {f.get('recommendation') or 'no verdict'} "
                f"(confidence {float(f.get('confidence') or 0):.0%}) - "
                f"{str(f.get('rationale') or '')[:200]}"
            )

        missing = [k for k in keys if k not in produced]
        out.coverage[tname] = {
            "expected": len(keys), "produced": len(produced), "missing": missing,
            "error": (f"more than {cap} items; the first {cap} were reviewed" if over_cap else None),
        }
        out.timeline.append({
            "step": "item_pass", "status": "ok" if not missing and not over_cap else "partial",
            "tool": tname, "expected": len(keys), "produced": len(produced),
            "missing": missing, **({"detail": out.coverage[tname]["error"]} if over_cap else {}),
        })

    if lines:
        out.block = (
            "## ITEMS ALREADY REVIEWED BY THE RUNTIME\n"
            "Every item below was reviewed deterministically before you were asked. "
            "These findings ARE the evidence: reason over them, cite them, and do NOT "
            "call the item tools again for these ids - a repeat call returns the same "
            "result.\n" + "\n".join(lines)
        )
    return out


def coverage_from_timeline(timeline: Any) -> Dict[str, Dict[str, Any]]:
    """Per-tool coverage, read back from the run's own timeline steps, so the
    response and the staged row derive it identically."""
    out: Dict[str, Dict[str, Any]] = {}
    for step in (timeline or []):
        if not isinstance(step, dict) or step.get("step") != "item_pass" or not step.get("tool"):
            continue
        out[str(step["tool"])] = {
            "expected": int(step.get("expected") or 0),
            "produced": int(step.get("produced") or 0),
            "missing": [str(m) for m in (step.get("missing") or [])],
            "error": step.get("detail") if step.get("status") in ("error", "partial") else None,
        }
    return out
