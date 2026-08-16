"""Smart App runtime executor.

Phase 7 minimum-viable: synchronous LLM invocation for an action defined in an
AgentSpec. The runtime:

1. Loads the published AppSpec + AgentSpec from Mongo.
2. Validates the requested action exists.
3. Validates inputs against the action's `input_schema` (when defined).
4. If the action `delegates_to` a sub-agent, uses that sub-agent's system
   prompt + tier; otherwise uses the root AgentSpec's.
5. Calls OpenRouter (or the configured ``LLM_LARGE_BASE_URL`` endpoint)
   via the OpenAI SDK with a single user message composed from goal +
   structured inputs. On-prem GPU clusters can swap the base URL to point
   at the in-house ``inference-service`` without any code change.
6. Returns a `RunResponse` with the assistant text + a minimal timeline.

Deferred (later phases):
- Tool / MCP invocation
- Streaming responses
- Multi-turn timelines (only one LLM call per /run today)
- Real HITL pause for approvals (we surface `pending_approval` if the action
  has `approval_required=True` and inputs cross any HITL threshold, but the
  approve/reject loop itself is phase 8)
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import time
import uuid
from typing import Any, Dict, List, Optional

import httpx
from fastapi import HTTPException, status
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError as JsonSchemaValidationError
from openai import APIConnectionError, APIStatusError

import llm_rate_limit
from case_signature import signature_of as _case_signature_of
from config import Settings
from env_context import current_env
from data_tools import (
    ACTION_TOOL_NAME,
    QUERY_TOOL_NAME,
    build_data_tools,
    dispatch_perform_action,
    dispatch_query_dataset,
)
from evidence_guard import (
    ReadLedger,
    evidence_violations,
    required_lookup_tools,
    required_lookup_violations,
    resolve_anchor_ids,
)
from llm_client import get_llm_client, get_llm_client_for
from factor_scoring import build_scorecard
from models import AgentSpec, AppSpec, RunRequest, RunResponse
from tools_v2_dispatch import (
    build_openai_tools_from_tools_v2,
    dispatch_tools_v2_call,
)

logger = logging.getLogger(__name__)


# A decision's model tier is BUILDER-CHOSEN per AgentSpec/Action complexity.
# Canonical tiers ``large`` | ``medium`` | ``small`` resolve to LLM_LARGE_* /
# LLM_MEDIUM_* / LLM_SMALL_* in config.Settings.llm_tier_config. Legacy
# ``tier_a/b/c`` are accepted and all map to LARGE (they always ran large).
# Default is LARGE — small/medium are opt-in for genuinely simple decisions,
# because a small model misreading a decision is the dangerous case.
_KNOWN_TIERS = {"large", "medium", "small", "tier_a", "tier_b", "tier_c"}
_DEFAULT_TIER = "large"


# ─────────────────────────────────────────────────────────────────────────────
# Auditability
# ─────────────────────────────────────────────────────────────────────────────
# Every /run must be auditable. We instruct the executing LLM to end its
# reply with a fenced ```json block (the "audit block") carrying a
# structured decision + reasoning + citations. The runtime parses that
# block out, strips it from the human-facing text, and the /run endpoint
# persists it to the ``app_run_audit`` collection. Apps whose LLM ignores
# the instruction still work — the runtime falls back to decision=None and
# reasoning=<full reply>.

_AUDIT_INSTRUCTION = (
    "## DECISION AUDIT (required)\n"
    "After your normal answer, end your reply with a fenced code block "
    "tagged `json` containing exactly this shape:\n"
    "```json\n"
    "{\n"
    '  "decision": "<a concise label that states YOUR decision or '
    "recommendation for THIS case, phrased in this app's own terms — use "
    "whatever wording best summarises it; do NOT force a fixed vocabulary "
    '(it is shown verbatim to the user as the headline)>",\n'
    '  "reasoning": "<concise why — the rule or evidence that decided it>",\n'
    '  "citations": [\n'
    '    {"type": "<the kind of source you relied on, in your own words>", '
    '"ref": "<id or clause>", "detail": "<quote or summary>"}\n'
    "  ],\n"
    '  "cited_precedents": [\n'
    '    {"decision_id": "<the `source_id` of a PAST CASE from the SIMILAR '
    'PAST CASES / REPRESENTATIVE PAST DECISIONS blocks that you relied on or '
    'deliberately deviated from>", "relation": "similar" | "differs", '
    '"note": "<one line: what matched, or why this case differs>"}\n'
    "  ],\n"
    '  "cited_clauses": [\n'
    '    {"clause_id": "<the id in [brackets] of a JUDGEMENT you actually '
    'relied on or set aside, e.g. C-034>", '
    '"relation": "applied" | "overruled" | "overrode_by_rule", '
    '"note": "<one line: how it applied, why you set it aside, or which '
    'rule/SOP overrode it>"}\n'
    "  ]\n"
    "}\n"
    "```\n"
    "Cite anything you relied on — a similar past record, a rule or clause "
    "from your instructions, or a tool/document that supplied a fact. "
    "`cited_precedents` is the past-case list SPECIFICALLY: when past cases "
    "were provided and informed your recommendation (followed OR deviated "
    "from), name them there — an officer sees these as the recommendation's "
    "receipts; omit entries you did not actually use (empty list is fine). "
    "`cited_clauses` is the same idea for the JUDGEMENTS block: name the "
    "[C-nnn] judgements you applied, deliberately overruled, or followed a "
    "rule/SOP INSTEAD of (relation \"overrode_by_rule\" — that report is how "
    "the team finds judgements that have gone stale against the SOP). Naming "
    "a judgement you did not use, or omitting one you did, corrupts the "
    "record of which learned judgement drove this decision. "
    "This block is recorded for compliance — be precise."
)

# Platform-injected into EVERY agent's system prompt (the builder never authors
# this — it is a system-level guarantee). Bounds retry loops so a persistent
# error (a failing tool, a 429 session-budget/rate limit, an unreachable source)
# can never spin forever burning the LLM session budget. On repeated failure the
# agent STOPS and escalates to the human + IT instead of looping.
_RESILIENCE_INSTRUCTION = (
    "## ERROR HANDLING (required)\n"
    "If a tool call or any operation FAILS, retry it at most 3 times TOTAL "
    "for the same error. If it still fails with the same error after 3 "
    "attempts, STOP: do not retry again, do not loop, and do not silently "
    "work around it. End your turn and tell the user plainly — state the "
    "exact error message and ask them to contact their IT administrator with "
    "that error. Never retry the same failing operation indefinitely."
)


def _accumulate_usage(acc: Dict[str, int], msg: Dict[str, Any]) -> None:
    """Fold one LLM call's token usage into the running total."""
    usage = msg.get("_usage") or {}
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        val = usage.get(key)
        if isinstance(val, (int, float)):
            acc[key] = acc.get(key, 0) + int(val)


def _digest_tool_result(tool_result: Any) -> str:
    """A short, audit-safe summary of a tool-call result.

    The full result is never persisted (it may be large or carry PII
    rows); we keep a bounded digest so the audit shows *what* a tool
    returned without storing everything.
    """
    import json as _json

    if isinstance(tool_result, dict):
        # Truthiness, NOT membership. The MCP's RunQueryResponse always carries
        # an `error` key and sets it to None on success, so `"error" in result`
        # is true for every successful read — see the note at the tool-status
        # checks below for what that cost.
        if tool_result.get("error"):
            return f"error: {str(tool_result.get('error'))[:200]}"
        if "samples" in tool_result:
            return f"{len(tool_result.get('samples') or [])} sample(s)"
        if "results" in tool_result:
            return f"{len(tool_result.get('results') or [])} result(s)"
        if "rows" in tool_result:
            return f"{len(tool_result.get('rows') or [])} row(s)"
    try:
        return _json.dumps(tool_result, default=str)[:240]
    except (TypeError, ValueError):
        return str(tool_result)[:240]


_WRITE_EVENT_RESULT_CAP = 8000


def _build_write_event(
    *,
    tool: str,
    kind: str,
    args: Dict[str, Any],
    result: Any,
    status: str,
    dataset_id: Optional[str] = None,
    action_id: Optional[str] = None,
    source_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Structured record of one LLM-issued write — what was sent, what came back.

    Unlike ``_digest_tool_result`` (a 240-char preview shared by every tool
    kind), write events are the forensic record: they store the full
    payload the LLM emitted and the MCP's response (capped to 8 KB to
    protect Mongo), so an auditor can answer "what did the LLM actually
    write into the source system, and what did the source say back?"
    without having to replay the run.
    """
    import json as _json
    from datetime import datetime as _dt, timezone as _tz

    if not isinstance(args, dict):
        args = {}
    ds = dataset_id or args.get("dataset_id")
    act = action_id or args.get("action_id")
    payload = args.get("payload")
    if not isinstance(payload, dict):
        # tools_v2 mcp_action lets the LLM emit payload fields flat — keep
        # whatever non-routing keys it sent so the audit shows the intent.
        payload = {
            k: v
            for k, v in args.items()
            if k not in ("dataset_id", "action_id", "source_id", "dry_run", "idempotency_key")
        } or None
    try:
        result_blob = _json.dumps(result, default=str)
        if len(result_blob) > _WRITE_EVENT_RESULT_CAP:
            result_blob = result_blob[:_WRITE_EVENT_RESULT_CAP] + "…[truncated]"
        result_field: Any = _json.loads(result_blob) if not result_blob.endswith(
            "[truncated]"
        ) else result_blob
    except (TypeError, ValueError):
        result_field = str(result)[:_WRITE_EVENT_RESULT_CAP]
    return {
        "tool": tool,
        "kind": kind,
        "source_id": source_id or args.get("source_id"),
        "dataset_id": ds,
        "action_id": act,
        "dry_run": bool(args.get("dry_run")),
        "idempotency_key": args.get("idempotency_key"),
        "payload": payload,
        "result": result_field,
        "status": status,
        "occurred_at": _dt.now(_tz.utc).isoformat(),
    }


def _extract_audit_block(
    text: str,
) -> tuple[str, Optional[str], Optional[str], list[dict], list[dict], list[dict]]:
    """Split an assistant reply into (human_text, decision, reasoning,
    citations, cited_precedents, cited_clauses).

    Looks for the last ```json fenced object and parses it as the
    structured audit block. On success the fence is stripped from the
    human-facing text. On any failure the run still completes and is still
    audited — ``decision`` is None, ``reasoning`` falls back to the full
    reply, ``citations`` is empty.

    ``cited_clauses`` is the BLAME EDGE (docs/clause-memory-graph-plan.md §10):
    the learned clauses the model says it relied on. When an officer then
    rejects, only these are blamed — never the whole injected set, which is the
    credit-assignment bug the clause design exists to fix. Additive, so an agent
    that omits the key still parses.
    """
    import json as _json
    import re as _re

    if not text:
        return "", None, None, [], [], []

    fences = list(
        _re.finditer(r"```(?:json)?\s*(\{.*?\})\s*```", text, _re.DOTALL)
    )
    for m in reversed(fences):
        try:
            obj = _json.loads(m.group(1))
        except _json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        if "decision" not in obj and "reasoning" not in obj:
            continue
        human = (text[: m.start()] + text[m.end():]).strip()
        decision = obj.get("decision")
        reasoning = obj.get("reasoning")
        citations = obj.get("citations")
        clean = [c for c in citations if isinstance(c, dict)] if isinstance(
            citations, list
        ) else []
        # Precedent receipts (adoption plan §4): keep only well-formed entries
        # that actually NAME a case — a citation without a decision_id is not
        # verifiable and would render as an empty chip.
        precs_raw = obj.get("cited_precedents")
        precedents = [
            {"decision_id": str(p.get("decision_id")),
             "relation": p.get("relation") if p.get("relation") in ("similar", "differs") else "similar",
             "note": (str(p.get("note") or "").strip()[:300] or None)}
            for p in (precs_raw if isinstance(precs_raw, list) else [])
            if isinstance(p, dict) and p.get("decision_id")
        ]
        # Clause receipts — same shape discipline as precedents: an entry with
        # no clause_id names nothing and cannot carry blame, so it is dropped.
        clauses_raw = obj.get("cited_clauses")
        cited_clauses = [
            {"clause_id": str(c.get("clause_id")),
             "relation": (c.get("relation")
                          if c.get("relation") in ("applied", "overruled",
                                                   "overrode_by_rule")
                          else "applied"),
             "note": (str(c.get("note") or "").strip()[:300] or None)}
            for c in (clauses_raw if isinstance(clauses_raw, list) else [])
            if isinstance(c, dict) and c.get("clause_id")
        ]
        return (
            human or text.strip(),
            str(decision) if decision is not None else None,
            str(reasoning) if reasoning is not None else None,
            clean,
            precedents,
            cited_clauses,
        )

    # No COMPLETE audit block parsed. The model FREQUENTLY truncates the block
    # (a long narrative + the json exceeds the output budget), leaving an
    # unparseable {...}. But `decision` / `reasoning` are simple leading string
    # fields — recover them by regex so the decision HEADLINE still shows
    # instead of degrading to the run status ("Pending Approval"). Applies to
    # every app. The human narration keeps its newlines (the result modal renders
    # it as markdown); the json fence is stripped from it.
    def _grab(field: str) -> Optional[str]:
        m = _re.search(r'"' + field + r'"\s*:\s*"((?:[^"\\]|\\.)*)"', text, _re.DOTALL)
        if not m:
            return None
        v = (
            m.group(1)
            .replace('\\"', '"')
            .replace("\\n", "\n")
            .replace("\\t", "\t")
            .replace("\\\\", "\\")
            .strip()
        )
        return v or None

    _stripped = _re.sub(r"```(?:json)?\s*\{.*?\}\s*```\s*$", "", text, flags=_re.DOTALL)  # closed audit fence
    _stripped = _re.sub(r"```(?:json)?\s*\{.*$", "", _stripped, flags=_re.DOTALL)          # truncated audit fence
    _stripped = _stripped.strip()
    decision = _grab("decision")
    reasoning = _grab("reasoning") or (_stripped or None)
    return text.strip(), decision, reasoning, [], [], []


def _resolve_action(agent_spec: AgentSpec, action_name: str):
    for action in agent_spec.actions:
        if action.name == action_name:
            return action
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=f"action '{action_name}' not defined in agent spec",
    )


def _resolve_sub_agent(agent_spec: AgentSpec, sub_agent_id: str):
    for sa in agent_spec.sub_agents:
        if sa.id == sub_agent_id:
            return sa
    return None


def _validate_inputs(action, inputs: Dict[str, Any]) -> None:
    if not action.input_schema:
        return
    schema = action.input_schema
    # A form file field arrives here as a blob DESCRIPTOR (an object
    # {filename, content_type, data}), NOT yet a string — the platform stores
    # the blob and substitutes a durable ref string downstream
    # (data_tools._store_upload_blobs). So for any input declared
    # ``format:"file"``, accept a string OR an object at arg-validation instead
    # of failing its ``type:"string"``. Without this the blob is rejected here,
    # before the store ever runs (the cause of "input validation failed at
    # ['<file_field>']: {...} is not of type 'string'" on file-upload forms).
    props = (schema.get("properties") or {}) if isinstance(schema, dict) else {}
    file_fields = [k for k, v in props.items()
                   if isinstance(v, dict) and v.get("format") == "file"]
    if file_fields:
        import copy
        schema = copy.deepcopy(schema)
        for k in file_fields:
            schema["properties"][k] = {"type": ["string", "object"]}
    try:
        Draft202012Validator(schema).validate(inputs)
    except JsonSchemaValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"input validation failed at {list(e.path)}: {e.message}",
        )


def _render_fraud_screen_block(agent_spec) -> str:
    """A near-mandatory-invocation stanza for any auto-wired fraud screen, so the
    ontology doesn't just WIRE the consistency_check tool — it drives the agent to
    RUN it. Empty when the app has no active screen. Runtime-derived (not
    persisted), so it always names exactly the screen tools currently present
    (fraud_roles.autowire_fraud_roles creates/populates them from the ontology).

    Wording is CONDITIONAL on purpose: this block is appended to every action's
    prompt (tools are agent-level, actions are not), but a screen must fire only
    when the decision actually concerns a record from the screened dataset — an
    unrelated cheap action must not be pushed into a spurious record-bound call.
    NB: this is prompt-level guidance, not a hard server-side guarantee; a model
    that skips the call still produces an un-screened recommendation."""
    tools = [
        t for t in (getattr(agent_spec, "tools_v2", None) or [])
        if getattr(t, "kind", None) == "consistency_check"
        and getattr(t, "url_columns", None)
        and getattr(t, "data_source_id", None)  # both needed for artifact fingerprinting
    ]
    if not tools:
        return ""
    names = ", ".join(f"`{getattr(t, 'name', 'consistency_check')}`" for t in tools)
    return (
        "FRAUD SCREEN. Whenever your decision concerns a record from a screened "
        f"dataset, you MUST call {names} with that record's key BEFORE finalizing, "
        "and fold the result into your reasoning: reused evidence (an artifact "
        "already on file for a DIFFERENT case), identity verification, and any "
        "field mismatches. These are EVIDENCE — cite and weigh them, but NEVER "
        "auto-reject on them; the officer decides. (If this particular decision "
        "does not involve such a record, no call is needed.)"
    )


def _render_required_lookups_block(agent_spec) -> str:
    """A mandatory-invocation stanza for policy-required data lookups — a bound
    mcp tool marked ``required: true`` (a bureau / KYC / sanctions check). The
    read-before-write gate ENFORCES these (a write staged without them is
    rejected), but enforcement alone makes a non-compliant run simply FAIL; this
    drives the agent to run them on turn 1, so the gate is a backstop rather than
    the only signal. Uses the SAME selection as the gate
    (``required_lookup_tools``) so prompt and enforcement can never diverge.
    Empty when the agent has no required lookups."""
    tools = required_lookup_tools(agent_spec)
    if not tools:
        return ""
    names = ", ".join(f"`{getattr(t, 'name', '?')}`" for t in tools)
    return (
        "MANDATORY CHECKS. Before you finalize a recommendation or stage ANY "
        f"write, you MUST call {names} with this record's key and fold the "
        "results into your reasoning. These are policy-required checks — a "
        "decision staged without them is REJECTED, so run them first."
    )


def _render_dataset_directory_block(app_spec) -> str:
    """Render AppSpec.dataset_directory as a per-dataset detail block.

    The directory carries the full dataset shape that was resolved at
    publish time from discovery-service (source-level metadata) and
    data-discovery-service (dataset-level metadata + columns + write
    actions). We project it into the system prompt so the agent has the
    full landscape — what datasets exist, what columns they hold, what
    write actions are available, what's PII — without any runtime
    discovery / catalogue round-trip.

    Empty when the AppSpec has no directory (legacy app, or publish-time
    hydration failed). The runtime data tools still fall back to
    ``fetch_catalogue_entry`` in that case.
    """
    if app_spec is None:
        return ""
    directory = getattr(app_spec, "dataset_directory", None) or []
    if not directory:
        return ""

    lines: list[str] = [
        "",
        "## Datasets you can read/write",
        "",
        "Each entry below is a dataset you bound at design time. The "
        "runtime routes `query_dataset(dataset_id=...)` and "
        "`perform_action(dataset_id=..., action_id=...)` to the upstream "
        "MCP source listed under **source**. Columns flagged `[PII]` are "
        "redacted before rows reach you. Pick datasets by description, "
        "not by guessing — the catalogue here is authoritative.",
        "",
    ]
    for entry in directory:
        display_name = entry.dataset_name or entry.source_name
        lines.append(f"### `{entry.dataset_id}` — {display_name}")
        # one-line summary row
        meta_bits: list[str] = [f"**access**: {entry.access}"]
        if entry.kind:
            meta_bits.append(f"**kind**: {entry.kind}")
        meta_bits.append(
            f"**source**: {entry.source_name} (`{entry.source_id}`)"
        )
        if entry.row_count_approx is not None:
            meta_bits.append(f"**rows ≈** {entry.row_count_approx:,}")
        if entry.has_pii:
            meta_bits.append("**PII**: yes (redaction on)")
        lines.append(" · ".join(meta_bits))
        # description
        desc = (entry.dataset_description or entry.source_description or "").strip()
        if desc:
            if len(desc) > 400:
                desc = desc[:397] + "…"
            lines.append("")
            lines.append(desc)
        # taxonomy summary (semantic sources)
        if entry.taxonomy_summary:
            lines.append(f"_doc types: {entry.taxonomy_summary}_")
        # columns
        if entry.columns:
            col_parts: list[str] = []
            for c in entry.columns[:30]:
                stype = c.semantic_type or c.type or "?"
                tag = "[PII]" if c.pii else ""
                col_parts.append(f"`{c.name}`:{stype}{tag}")
            extra = ""
            if len(entry.columns) > 30:
                extra = f" … (+{len(entry.columns) - 30} more)"
            lines.append("")
            lines.append(f"**columns**: {', '.join(col_parts)}{extra}")
        # write actions
        if entry.write_actions:
            wa_parts: list[str] = []
            for wa in entry.write_actions:
                bit = f"`{wa.id}` ({wa.verb})"
                wa_parts.append(bit)
            lines.append("")
            lines.append(f"**write_actions**: {', '.join(wa_parts)}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _build_messages(
    *,
    agent_spec: AgentSpec,
    action,
    inputs: Dict[str, Any],
    app_spec=None,
) -> tuple[list[dict], str]:
    """Return (messages, model_tier_string)."""

    # Pick the executor: delegated sub-agent if any, else root.
    sub_agent = None
    if action.delegates_to:
        # Use the first delegate. Multi-delegate orchestration is phase 8.
        sub_agent = _resolve_sub_agent(agent_spec, action.delegates_to[0])

    # Tier precedence: the ACTION's tier (builder set it per this decision's
    # complexity) > the delegate sub-agent's > the agent default. So a single
    # agent can run cheap classification actions on a small tier and the
    # high-stakes write action on large.
    action_tier = getattr(action, "model_tier", None)
    if sub_agent is not None:
        system_prompt = sub_agent.system_prompt
        tier = action_tier or sub_agent.model_tier or agent_spec.model_tier
    else:
        system_prompt = agent_spec.system_prompt
        tier = action_tier or agent_spec.model_tier

    # Append the persisted dataset_directory so the agent has zero-lookup
    # knowledge of which MCP source serves each dataset. Hydrated at
    # publish time; empty for legacy apps that pre-date hydration.
    directory_block = _render_dataset_directory_block(app_spec)
    if directory_block:
        system_prompt = (system_prompt or "").rstrip() + "\n" + directory_block

    # Always require the structured audit block so every run is auditable, and
    # the platform ERROR-HANDLING guarantee so no run loops on a failing op.
    system_prompt = (
        (system_prompt or "").rstrip() + "\n\n" + _AUDIT_INSTRUCTION
        + "\n\n" + _RESILIENCE_INSTRUCTION
    )

    # Guarantee the ontology-wired fraud screen is RUN, not just available.
    _fraud_block = _render_fraud_screen_block(agent_spec)
    if _fraud_block:
        system_prompt = system_prompt.rstrip() + "\n\n" + _fraud_block

    # Drive the agent to RUN any policy-required data lookup (bureau/KYC) up
    # front — the read-before-write gate enforces them; this makes a compliant
    # run the default instead of a failed one.
    _lookups_block = _render_required_lookups_block(agent_spec)
    if _lookups_block:
        system_prompt = system_prompt.rstrip() + "\n\n" + _lookups_block

    # Compose a single structured user message. Inference-service understands
    # JSON-in-text fine; we'll graduate to tool-calls in phase 8.
    import json as _json

    def _redact_blobs(obj: Any) -> Any:
        """Strip file-upload base64 out of the prompt. A 12MB image is ~16MB of
        base64 ≈ millions of tokens — dumping it verbatim guarantees a context
        overflow / runaway cost on the first LLM call (and the LLM can't use it
        anyway). Summarise the blob to {filename, content_type, size}; the bytes
        reach an image tool via the storage ref, not the prompt."""
        if isinstance(obj, dict):
            if isinstance(obj.get("data"), str) and "filename" in obj and "content_type" in obj:
                return {
                    "_file": obj.get("filename"),
                    "content_type": obj.get("content_type"),
                    "size_b64": len(obj.get("data") or ""),
                }
            return {k: _redact_blobs(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_redact_blobs(x) for x in obj]
        return obj

    user_content = (
        f"Action: {action.name}\n"
        f"Description: {action.description or '(none)'}\n\n"
        f"Inputs:\n```json\n{_json.dumps(_redact_blobs(inputs), indent=2)}\n```\n\n"
        "Respond with your decision and reasoning."
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]
    requested_tier = tier if tier in _KNOWN_TIERS else _DEFAULT_TIER
    return messages, requested_tier


# ─────────────────────────────────────────────────────────────────────────────
# Few-shot pre-injector
# ─────────────────────────────────────────────────────────────────────────────
# When AgentSpec.tools_v2 carries one or more ``neighbor_samples`` entries
# (the BA-built or Refresh-from-History pattern), the runtime prefetches
# their results from Milvus and folds them into the system prompt BEFORE the
# first inference call. This collapses what would otherwise be 3 LLM
# round-trips (canonical-ask → result → neighbors-ask → result → final
# answer) into 1.
#
# Two blocks are produced:
#   • "REPRESENTATIVE PAST DECISIONS" from canonical mode
#   • "SIMILAR PAST CASES" from neighbors mode
#
# The original ``neighbor_samples`` tools stay registered in tools_v2 so the
# LLM CAN call them again with custom filters (e.g. "show me only ESCALATE
# decisions"), but it doesn't HAVE to — the typical case is one inference
# call with both blocks already present.


_FEWSHOT_HEADER_CANONICAL = (
    "## REPRESENTATIVE PAST DECISIONS (curated by your team's history)\n"
    "These are canonical examples of how this team has decided cases in the "
    "past. Use them to anchor the schema and tone of your decision."
)
_FEWSHOT_HEADER_NEIGHBORS = (
    "## SIMILAR PAST CASES (most similar to the incoming input)\n"
    "These are the {n} past cases most similar to the new input you're about "
    "to decide. Treat them as precedent — cite the matching `source_id` in "
    "your reasoning when you rely on one."
)


def _format_sample_for_prompt(sample: Dict[str, Any], idx: int) -> str:
    """One past case → a compact markdown block for prompt injection."""
    import json as _json
    sid = sample.get("source_id") or f"#{idx}"
    decision = sample.get("decision") or "(no decision)"
    severity = sample.get("severity")
    inp = sample.get("input") or {}
    out = sample.get("output") or {}
    reasoning = sample.get("reasoning_trace") or ""

    inp_str = _json.dumps(inp, indent=None, separators=(",", ":"), default=str)[:600]
    out_str = _json.dumps(out, indent=None, separators=(",", ":"), default=str)[:300]
    sev_str = f" · severity={severity}" if severity else ""

    block = (
        f"### Example {idx} — `{sid}` → **{decision}**{sev_str}\n"
        f"  input: {inp_str}\n"
        f"  outcome: {out_str}\n"
    )
    if reasoning:
        block += f"  reasoning: {reasoning[:300]}\n"
    return block


_FEWSHOT_COLDSTART_NOTE = (
    "## HISTORICAL SAMPLES NOT YET INDEXED\n"
    "This agent is configured to ground decisions in past historical "
    "samples (`neighbor_samples` tool registered), but the Milvus "
    "collection is empty or unreachable right now — the "
    "Refresh-from-History workflow likely hasn't completed its first run "
    "yet. Reason from the system prompt + RAG only for this request, and "
    "explicitly note in your reasoning that no past precedent was "
    "available so the BA knows to re-run the request after the workflow "
    "finishes."
)


def _lookup_judgement_tool_name(agent_spec: Any) -> Optional[str]:
    """The agent's lookup_judgement tool name, or None if it has none."""
    if agent_spec is None:
        return None
    tools = (getattr(agent_spec, "tools_v2", None)
             or getattr(agent_spec, "tools", None) or [])
    for t in tools:
        if getattr(t, "kind", None) == "lookup_judgement":
            return getattr(t, "name", None) or "lookup_judgement"
    return None


async def _prefetch_decision_clauses(
    app_spec: Any, record: Optional[Dict[str, Any]] = None, *,
    signals: Any = None, signals_ran: bool = False,
    agent_spec: Any = None,
) -> tuple[str, list[str], list[str]]:
    """Clause-memory block for this case: ``(block, injected_ids, case_facets)``.

    An app with NO ``case_signature`` still gets clauses — they simply carry no
    facet scope (global within the record/decision bucket). Declaring a
    signature buys scoping, not membership.

    Facets are derived DETERMINISTICALLY here and returned so the caller can
    freeze them on the staging row: the correction recorded at approve/reject
    must carry the signature the model actually saw, and the item tools inherit
    them as their only routable scope.

    Enrichment path — every failure logs loudly and yields an empty block. A
    degraded learning store must never take a decision down with it."""
    try:
        from analysis_rubrics import (
            RECORD_MODALITY, RECORD_RUBRIC_HEADER, RECORD_TASK_TYPE,
            rubric_tenant_for_app,
        )
        from case_signature import derive_facets, learning_config, signature_of
        from clause_store import select_clauses

        sig = signature_of(app_spec)
        cfg = learning_config(sig)

        tenant_id = rubric_tenant_for_app(app_spec)
        slug = getattr(app_spec, "slug", None)
        if not tenant_id or not slug:
            logger.warning(
                "[clause-memory] app %s has no org identity — clause read "
                "skipped (folds for this app are skipped too)", slug)
            return "", [], [], {}

        domain = None
        for entry in (getattr(app_spec, "dataset_directory", None) or []):
            domain = getattr(entry, "domain", None) or domain
        facets, unknown = derive_facets(
            record, sig, signals=signals, signals_ran=signals_ran, domain=domain)
        if unknown:
            logger.warning(
                "[clause-memory] app %s: %d facet famil(ies) drifted to __unknown "
                "for this case: %s", slug, len(unknown), unknown)

        block, injected = await select_clauses(
            tenant_id=tenant_id, app_slug=slug,
            modality=RECORD_MODALITY, task_type=RECORD_TASK_TYPE,
            case_facets=facets, budget_words=cfg["clause_budget_words"],
            header=RECORD_RUBRIC_HEADER,
            # Name the evidence tool ONLY when this agent actually has it.
            # The hint costs tokens on every run whether or not it is used, and
            # pointing a model at a tool it was never given invites a
            # hallucinated call it then has to recover from.
            lookup_tool=_lookup_judgement_tool_name(agent_spec))
        # Display metadata for the officer's screen, fetched HERE because
        # tenant/slug are resolved in this scope and the ids are already in hand.
        # Without it the citation receipt reaches the UI as bare ids and the
        # "what your team has taught" block renders with no sentence and nobody's
        # name on it.
        from clause_store import clause_display_meta
        meta = await clause_display_meta(
            tenant_id=tenant_id, app_slug=slug, clause_ids=injected)
        return block, injected, facets, meta
    except Exception as exc:  # noqa: BLE001 — enrichment; loud, never blocks
        logger.warning(
            "[clause-memory] load failed: %s — run proceeds without learned "
            "clauses", exc)
        return "", [], [], {}


async def _prefetch_few_shot_blocks(
    *,
    agent_spec: AgentSpec,
    inputs: Dict[str, Any],
) -> tuple[str, list[dict]]:
    """Prefetch canonical + neighbor sample blocks for the system prompt.

    Returns ``(prompt_block, references)`` where ``references`` is a list
    of ``{source_id, mode, similarity, decision}`` dicts naming exactly
    which past-case samples were injected — persisted into the audit trail
    so a reviewer can see what precedent drove the decision.

    Walks ``agent_spec.tools_v2`` looking for ``neighbor_samples`` tools.
    For each one:
      * mode='canonical'  → filter is_canonical=true, ignores ``inputs``
      * mode='neighbors'  → vector search on JSON-serialized inputs

    Returns a concatenated markdown string ready to append to the system
    prompt. When ``neighbor_samples`` tools ARE registered but every
    Milvus query returns empty (cold-start path: workflow hasn't
    indexed yet, or Milvus is unreachable), returns a single
    "samples not yet indexed" note so the agent knows to mention it
    rather than silently behaving as if it never had samples.

    Empty string only when no ``neighbor_samples`` tools are registered
    on this agent at all (i.e. the agent never had few-shot context to
    begin with).
    """
    from tools_v2_dispatch import _query_neighbor_samples  # type: ignore

    blocks: list[str] = []
    refs: list[dict] = []
    seen: set = set()
    saw_neighbor_tool = False  # any neighbor_samples tool registered?
    successful_query = False   # at least one Milvus query returned samples?

    for entry in (agent_spec.tools_v2 or []):
        d = entry.model_dump()
        if d.get("kind") != "neighbor_samples":
            continue
        saw_neighbor_tool = True

        collection = d.get("collection")
        if not collection:
            continue
        mode = d.get("mode") or "neighbors"
        try:
            top_k = int(d.get("top_k") or (12 if mode == "canonical" else 3))
        except (TypeError, ValueError):
            top_k = 12 if mode == "canonical" else 3

        # Dedup so the same (collection, mode) doesn't render twice.
        key = f"{collection}:{mode}"
        if key in seen:
            continue
        seen.add(key)

        # Neighbors mode is meaningless without case input — skip silently.
        if mode == "neighbors" and not inputs:
            continue

        try:
            res = await _query_neighbor_samples(
                collection=collection,
                mode=mode,
                top_k=top_k,
                case_input=inputs if mode == "neighbors" else None,
                decision_filter=d.get("decision"),
                severity_filter=d.get("severity"),
                exclude_canonical=bool(d.get("exclude_canonical", True)),
                agent_id=getattr(agent_spec, "agent_id", None),
                input_fields=(getattr(getattr(agent_spec, "grounding", None), "input_fields", None)),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "few-shot prefetch failed (collection=%s mode=%s): %s",
                collection, mode, exc,
            )
            continue

        samples = res.get("samples") or []
        if not samples:
            continue

        successful_query = True
        if mode == "canonical":
            header = _FEWSHOT_HEADER_CANONICAL
        else:
            header = _FEWSHOT_HEADER_NEIGHBORS.format(n=len(samples))
        body = "\n".join(
            _format_sample_for_prompt(s, i + 1) for i, s in enumerate(samples)
        )
        blocks.append(f"{header}\n\n{body}")
        for s in samples:
            refs.append(
                {
                    "source_id": s.get("source_id"),
                    "mode": mode,
                    "similarity": s.get("similarity"),
                    "decision": s.get("decision"),
                }
            )

    # Cold-start: tool registered but nothing came back. Inject a
    # one-paragraph note so the agent acknowledges the gap rather than
    # behaving as if the BA never asked for grounding.
    if saw_neighbor_tool and not successful_query:
        return _FEWSHOT_COLDSTART_NOTE, []

    return "\n\n".join(blocks), refs


# ── RAG prefetch ──────────────────────────────────────────────────────────
# An agent with a ``rag`` tool (a policy / reference corpus) otherwise re-queries
# it INSIDE the loop — often the SAME static clauses, several times, each a full
# LLM turn. Prefetch each RAG source ONCE before the loop and inject the result
# as context, so the agent has the reference in hand from turn 1. RAG stays the
# live source of truth (no inlining into the spec) — this only front-loads the
# retrieval. The rag tool stays registered for genuine targeted follow-ups.
_RAG_PREFETCH_HEADER = (
    "## REFERENCE / POLICY (prefetched for this case — USE THIS FIRST)\n"
    "The passages below were retrieved from your reference source(s) for THIS "
    "decision. Treat them as your PRIMARY reference and reason directly from "
    "them — even if your instructions mention a search / rag tool, that content "
    "is ALREADY HERE, so do NOT call the tool to re-fetch what is below. Call "
    "the tool ONLY for something specific you still need that is genuinely not "
    "present here.\n"
    "This reference does NOT change your output format: still give your normal "
    "answer AND end with the required decision-audit ```json block."
)

# Chat variant: same "use-this-first" contract, but the retrieval was keyed on
# the operator's CURRENT question (resolved against recent conversation), so the
# wording speaks to a question rather than a decision case.
_RAG_PREFETCH_HEADER_CHAT = (
    "## REFERENCE / POLICY (prefetched for this question — USE THIS FIRST)\n"
    "The passages below were retrieved from your reference source(s) for the "
    "operator's CURRENT question. Treat them as your PRIMARY reference and answer "
    "directly from them — even if your instructions mention a search / rag tool, "
    "that content is ALREADY HERE, so do NOT call the tool to re-fetch what is "
    "below. Call the tool ONLY for something specific you still need (e.g. a "
    "record/figure) that is genuinely not present here."
)


def _rag_prefetch_enabled() -> bool:
    return os.getenv("RAG_PREFETCH_ENABLED", "1").strip().lower() in ("1", "true", "yes", "on")


async def _prefetch_rag_blocks(
    *,
    settings: Settings,
    agent_spec: AgentSpec,
    auth_header: Optional[str],
    action: Optional["Action"] = None,
    inputs: Optional[Dict[str, Any]] = None,
    query_text: Optional[str] = None,
    header: str = _RAG_PREFETCH_HEADER,
    top_k_floor: Optional[int] = None,
    max_chars_per_source: Optional[int] = None,
    tenant_id: Optional[str] = None,
) -> tuple[str, list[dict]]:
    """Prefetch each registered RAG source once and return an injectable context
    block + refs. Covers tools_v2 ``kind=rag`` AND legacy ``AgentSpec.rag``.

    Two callers key the retrieval differently:
    - RECOMMEND path passes ``action`` (+ ``inputs``): the query is the action's
      purpose narrowed by a compact case summary.
    - CHAT path passes ``query_text`` directly — the operator's current question,
      already resolved against recent conversation — plus the chat ``header``.

    Fail-open: any error → empty (the rag tool is still registered and behaves
    exactly as today). Default-on; disable with ``RAG_PREFETCH_ENABLED=0``."""
    if not _rag_prefetch_enabled():
        return "", []
    import json
    # RAG short-circuit: prefetch sources are unstructured corpora = semantic
    # sources, answered by the Citra-Service platform reader, NEVER the dept-MCP
    # (pure disconnect). Requires an end-user JWT (Citra-Service enforces dept
    # scope server-side); a service-token agent/trigger run has none, so its
    # prefetch fails-open to no context until the service-auth path exists.
    from proxy_clients import call_citra_semantic_search  # type: ignore
    user_jwt = (auth_header or "").removeprefix("Bearer ").strip() or None

    # Comprehensiveness knobs. A POLICY/SOP source is small + bounded, and a
    # decision reasons over the WHOLE document (cause classification, crew matrix,
    # parts approval, SAIDI rules — different sections each). A narrow top-12 +
    # 4 000-char slice left sections out, so the agent drilled them back one per
    # LLM turn (≈5 wasted turns). Default to a comprehensive pull so the full SOP
    # is in context; chat overrides to lean values. Env-tunable per deployment.
    try:
        _tk_floor = int(top_k_floor) if top_k_floor is not None \
            else int(os.getenv("RAG_PREFETCH_TOP_K", "40"))
    except (TypeError, ValueError):
        _tk_floor = 40
    try:
        _max_chars = int(max_chars_per_source) if max_chars_per_source is not None \
            else int(os.getenv("RAG_PREFETCH_MAX_CHARS", "12000"))
    except (TypeError, ValueError):
        _max_chars = 12000

    # ONE wide query per source. Chat supplies the query verbatim; the recommend
    # path builds it from the action's purpose + a compact case summary.
    if query_text is not None:
        query = query_text.strip() or "policy and reference relevant to this question"
    else:
        desc = (getattr(action, "description", None) or getattr(action, "name", "") or "").strip()
        try:
            case = json.dumps(inputs, default=str)[:400] if inputs else ""
        except Exception:
            case = ""
        query = (desc + ((" | case: " + case) if case else "")).strip() \
            or "policy and reference relevant to this decision"

    # Collect (source_id, top_k) from both rag surfaces, dedup by source_id.
    sources: list[tuple] = []
    for entry in (agent_spec.tools_v2 or []):
        d = entry.model_dump()
        if d.get("kind") == "rag" and d.get("source_id"):
            sources.append((d["source_id"], d.get("top_k") or 8))
    for rb in (getattr(agent_spec, "rag", None) or []):
        sid = getattr(rb, "source_id", None)
        if sid:
            sources.append((sid, getattr(rb, "top_k", 8) or 8))

    blocks: list[str] = []
    refs: list[dict] = []
    seen: set = set()
    for source_id, tk in sources:
        if source_id in seen:
            continue
        seen.add(source_id)
        try:
            top_k = min(max(int(tk or 8), _tk_floor), 100)  # comprehensive
        except (TypeError, ValueError):
            top_k = _tk_floor
        try:
            res = await call_citra_semantic_search(
                settings=settings, user_jwt=user_jwt, source_id=source_id,
                query=query, top_k=top_k, org_id=tenant_id,
            )
        except Exception as exc:  # noqa: BLE001 — prefetch is best-effort
            logger.warning("rag prefetch failed (source=%s): %s", source_id, exc)
            continue
        # Render the retrieved chunks as readable context — Citra-Service returns
        # {source_id, dept_id, count, chunks:[{text, score, metadata}]}.
        chunks = (res or {}).get("chunks") if isinstance(res, dict) else None
        text = "\n\n".join(
            str(c.get("text") or "") for c in (chunks or []) if isinstance(c, dict) and c.get("text")
        )
        if not text.strip():
            continue
        blocks.append(f"### From `{source_id}`\n{text[:_max_chars]}")
        refs.append({"source_id": source_id, "kind": "rag_prefetch"})

    if not blocks:
        return "", refs
    return header + "\n\n" + "\n\n".join(blocks), refs


async def _prefetch_corrections_block(
    *, slug: Optional[str], limit: int = 8,
    case_facets: Optional[List[str]] = None,
) -> str:
    """Render recent OFFICER CORRECTIONS (rejections + overrides, WITH the
    officer's stated reason) for THIS app as a context block, so the model learns
    *why* past recommendations were corrected — the causal half of "self-
    improving" (generalise the correction, don't just memorise case→decision).
    Reads the review queue (``smartapp_workflow_staging``). Best-effort, fail-open.

    Ranked by COMPARABILITY to the current case (facet overlap against the
    frozen ``case_facets`` each staging row carries), recency as tiebreak — not
    recency alone. An app-global recency window has a surge failure: 400
    monsoon flood corrections evict the lone theft lesson within hours,
    silencing the rare case type's advisor exactly when a theft claim arrives
    mid-surge. Rows with no facets keep recency order (honest degradation, same
    rule as the §11 precedent ranking).
    """
    if not slug:
        return ""
    try:
        from main import get_workflow_staging_col
    except ImportError:  # pragma: no cover
        return ""
    try:
        cur = (
            get_workflow_staging_col()
            .find(
                {"slug": slug, "status": {"$in": ["rejected", "cancelled", "applied"]}},
                {"_id": 0, "llm_recommendation_text": 1, "status": 1,
                 "audit_trail": 1, "case_facets": 1},
            )
            .sort("resolved_at", -1)
            .limit(40)
        )
        rows = [r async for r in cur]
    except Exception as exc:  # noqa: BLE001 — context is best-effort
        logger.warning("corrections prefetch failed (slug=%s): %s", slug, exc)
        return ""
    want = {str(f) for f in (case_facets or []) if f}
    if want:
        def _overlap(r: dict) -> float:
            have = {str(f) for f in (r.get("case_facets") or []) if f}
            if not have:
                return 0.0
            return len(want & have) / min(len(want), len(have))
        # Stable sort: comparable cases first, recency (the incoming order)
        # preserved inside each overlap band.
        rows.sort(key=_overlap, reverse=True)
    lines: list[str] = []
    for r in rows:
        if len(lines) >= limit:
            break
        # Collapse whitespace/newlines + cap length on ALL free text before it
        # enters the system prompt — both the officer reason and the prior model
        # text are untrusted-ish; un-stripped newlines/markdown could spoof this
        # block's own structure (prompt injection).
        reco = " ".join((r.get("llm_recommendation_text") or "").split())[:160] or "(no summary)"
        entry = next(
            (e for e in reversed(r.get("audit_trail") or [])
             if e.get("decision") in ("rejected", "cancelled", "approved")),
            None,
        )
        if not entry:
            continue
        # ONLY the structured decision_reason is a learning signal — never the
        # generic audit ``note`` ("user rejected"), so a reason-less reject is
        # correctly skipped below instead of injecting noise.
        reason = " ".join((entry.get("decision_reason") or "").split())[:200]
        deltas: list[str] = []
        for ev in (entry.get("write_events") or []):
            ov = ev.get("override") if isinstance(ev, dict) else None
            if isinstance(ov, dict):
                for f, d in ov.items():
                    if isinstance(d, dict):
                        deltas.append(f"{f}: {d.get('from')}→{d.get('to')}")
        dec = entry.get("decision")
        if dec in ("rejected", "cancelled"):
            if not reason:
                continue  # a reason-less reject teaches nothing — skip
            lines.append(f"- REJECTED: proposed “{reco}” — why: {reason}")
        elif dec == "approved" and deltas:
            why = f" — why: {reason}" if reason else ""
            lines.append(
                f"- CHANGED: proposed “{reco}” — officer corrected "
                f"{', '.join(deltas[:5])}{why}"
            )
        # clean accept (approved, no delta) is not a correction → skip
    if not lines:
        return ""
    return (
        "## OFFICER CORRECTIONS (learn from these)\n"
        "On THIS app, officers rejected or changed past recommendations as below, "
        "with their reason. Treat them as the team's corrected judgement and align "
        "your recommendation where the case is similar:\n" + "\n".join(lines)
    )


def _inject_few_shot_into_messages(
    messages: list[dict],
    few_shot_block: str,
) -> None:
    """Append the few-shot block to the system message in-place.

    Lands on the system message (not the user message) so it's tagged as
    instruction context — better for safety filters and prompt caching
    (system prompts are typically cache-stable across requests).
    """
    if not few_shot_block:
        return
    for m in messages:
        if m.get("role") == "system":
            existing = (m.get("content") or "").rstrip()
            sep = "\n\n---\n\n" if existing else ""
            m["content"] = f"{existing}{sep}{few_shot_block}"
            return
    # No system message present — prepend one.
    messages.insert(0, {"role": "system", "content": few_shot_block})


def _parse_text_tool_calls(content: str) -> list[dict]:
    """Recover tool calls a model emitted as TEXT markup into OpenAI structured
    ``tool_calls``. Hybrid-reasoning models sometimes put the call in ``content``
    instead of the structured ``tool_calls`` field; the dispatch loop reads only
    ``tool_calls``, so an unparsed emission renders to the user as raw markup and
    no tool runs. Two dialects are handled:

    1. GLM-style (GLM-4.5+/5.1)::

         <tool_call>outage_query<arg_key>query</arg_key><arg_value>…</arg_value>
         <arg_key>max_results</arg_key><arg_value>50</arg_value></tool_call>

    2. DeepSeek "invoke"/"parameter" style (DeepSeek-V4; any ``<|DSML|…>``-style
       wrapper)::

         <|DSML|invoke name="consumer_query">
           <|DSML|parameter name="max_results" string="false">5</|DSML|parameter>
           <|DSML|parameter name="query" string="true">…</|DSML|parameter>
         </|DSML|invoke>

    Returns ``[]`` when neither dialect is present.
    """
    import json as _json
    import re as _re

    if not content:
        return []

    def _coerce(v: str):
        # JSON scalars ("50" → 50, "true" → True, quoted strings); fall back to
        # the raw trimmed string for free-text args.
        v = v.strip()
        try:
            return _json.loads(v)
        except Exception:  # noqa: BLE001
            return v

    calls: list[dict] = []

    # Dialect 1 — ``<tool_call …>`` blocks. Covers the GLM arg_key/arg_value
    # body AND the ``name="…"`` + JSON-body variant DeepSeek also emits, e.g.::
    #   <tool_call name="theft_cases_query">{"case_id": "THC-001"}</tool_call>
    if "<tool_call" in content:
        block_re = _re.compile(r"<tool_call\b([^>]*)>(.*?)</tool_call>", _re.DOTALL)
        arg_re = _re.compile(
            r"<arg_key>(.*?)</arg_key>\s*<arg_value>(.*?)</arg_value>", _re.DOTALL
        )
        for i, (attrs, block) in enumerate(block_re.findall(content)):
            m = _re.search(r"name=[\"']([^\"']+)[\"']", attrs)
            if m:
                name = m.group(1).strip()
            else:
                name = block.split("<arg_key>", 1)[0].split("{", 1)[0].strip().strip(":").strip()
            if not name:
                continue
            args = {k.strip(): _coerce(v) for k, v in arg_re.findall(block)}
            if not args:  # no arg_key/value pairs → try a JSON-object body
                jm = _re.search(r"\{.*\}", block, _re.DOTALL)
                if jm:
                    try:
                        args = _json.loads(jm.group(0))
                    except Exception:  # noqa: BLE001
                        args = {}
            calls.append({
                "id": f"call_text_{i}", "type": "function",
                "function": {"name": name, "arguments": _json.dumps(args)},
            })
        if calls:
            return calls

    # Dialect 2 — DeepSeek "invoke"/"parameter" blocks. Anchor on the
    # invoke/parameter tag structure (not the model-specific ``<|DSML|…>``
    # wrapper) so any wrapper variant is tolerated.
    if _re.search(r"invoke\s+name=", content):
        invoke_re = _re.compile(
            r"invoke\s+name=[\"']([^\"']+)[\"']\s*>(.*?)</[^>]*?invoke\s*>", _re.DOTALL
        )
        param_re = _re.compile(
            r"parameter\s+name=[\"']([^\"']+)[\"'][^>]*?>(.*?)</[^>]*?parameter\s*>", _re.DOTALL
        )
        for i, (name, body) in enumerate(invoke_re.findall(content)):
            args = {k.strip(): _coerce(v) for k, v in param_re.findall(body)}
            calls.append({
                "id": f"call_text_{i}", "type": "function",
                "function": {"name": name.strip(), "arguments": _json.dumps(args)},
            })

    return calls


async def _call_llm(
    *,
    settings: Settings,
    messages: list[dict],
    tier: str,
    tools: Optional[list[dict]] = None,
    tool_choice: str = "auto",
    tenant_id: Optional[str] = None,
    surface: str = "agent_run",
) -> dict:
    """Call the configured LLM endpoint and return the assistant message.

    Today the same LLM_LARGE_* model is used for every tier; ``tier`` is
    accepted so callers / timelines stay tier-aware and so a future
    SMALL/MEDIUM ladder can be introduced without touching call sites.

    Returns the full assistant message dict (not just ``content``) so the
    tool-dispatch loop can read ``tool_calls``.
    """
    # Per-user LLM-call rate limit — count this call against the bound user's
    # rolling window and raise LLMRateLimitError once over. Every model call
    # (run / chat / sub-agent / trigger) routes through here, so this is the
    # single chokepoint. No-op when no user is bound.
    llm_rate_limit.record_call()
    if not settings.llm_large_model:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="LLM_LARGE_MODEL not configured",
        )
    # Resolve the builder-chosen tier → concrete endpoint (large/medium/small).
    # Falls back to LARGE (never a smaller model) when a tier is unconfigured.
    cfg = settings.llm_tier_config(tier)
    if cfg["fell_back"]:
        logger.warning(
            "[LLM] tier=%s requested but its model is unconfigured — using LARGE "
            "(safe). Set LLM_%s_MODEL to enable it.", tier, str(tier).upper(),
        )
    client = get_llm_client_for(cfg["base_url"], cfg["api_key"])
    kwargs: Dict[str, Any] = {
        "model": cfg["model"],
        "messages": messages,
        "temperature": 0.2,
        # GLM-class reasoning models spend output tokens on chain-of-thought
        # BEFORE emitting the tool call / answer, and on OpenRouter those
        # reasoning tokens count against max_tokens. At the old 1024 cap the
        # model could exhaust the budget mid-reason → finish_reason=length,
        # empty content, no tool_calls → the loop returned "(no response)".
        # 4096 leaves headroom for reason + tool call + a concise answer.
        "max_tokens": settings.llm_agent_max_tokens,
        "stream": False,
    }
    # Provider-specific knobs (OpenRouter ``reasoning`` / ``provider`` routing).
    # Critical for GLM-class models: without a ``reasoning`` directive they
    # leak chain-of-thought + native ``<tool_call>`` markup into ``content``
    # rather than returning structured ``tool_calls``. Per-tier extra_body.
    if cfg["extra_body"]:
        kwargs["extra_body"] = cfg["extra_body"]
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = tool_choice

    logger.info(
        "[LLM] → %s tier=%s(%s) msgs=%d tools=%d extra_body=%s",
        cfg["model"],
        tier,
        cfg["tier"],
        len(messages),
        len(tools or []),
        bool(cfg["extra_body"]),
    )
    try:
        resp = await client.chat.completions.create(**kwargs)
    except APIConnectionError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"LLM endpoint unreachable: {e}",
        ) from e
    except APIStatusError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"LLM endpoint returned {e.status_code}: {str(e)[:500]}",
        ) from e

    try:
        msg = resp.choices[0].message.model_dump(exclude_none=True)
    except (IndexError, AttributeError) as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"LLM endpoint returned unexpected shape: {e}",
        ) from e
    try:
        _finish = resp.choices[0].finish_reason
    except Exception:  # noqa: BLE001
        _finish = None
    # Truncation guard — surface it loudly. ``finish_reason == "length"`` means
    # the model hit max_tokens. For a reasoning model that often means it spent
    # the whole budget thinking and emitted no usable tool call / answer (the
    # "(no response)" symptom). Visible here rather than silently degrading.
    if _finish == "length":
        logger.warning(
            "[LLM] response TRUNCATED at max_tokens=%d (model=%s) — raise "
            "LLM_AGENT_MAX_TOKENS if this recurs; reasoning may be eating the "
            "budget before the tool call/answer.",
            settings.llm_agent_max_tokens,
            getattr(resp, "model", None),
        )
    # Recover tool calls the model emitted as text markup instead of the
    # structured ``tool_calls`` field (see _parse_text_tool_calls). The
    # extra_body reasoning config above should prevent this, but GLM is not
    # 100% reliable — without this guard a text-format call is rendered to the
    # user as raw ``<tool_call>`` markup and no tool runs. If markup is present
    # but unparseable, fail loud rather than pass garbage off as an answer.
    if tools and not msg.get("tool_calls"):
        import re as _re
        _content = msg.get("content") or ""
        # A tool call emitted as TEXT markup in either known dialect:
        # GLM ``<tool_call>…`` or DeepSeek ``<|DSML|invoke name="…">…``.
        _has_markup = ("<tool_call" in _content) or bool(_re.search(r"invoke\s+name=", _content))
        if _has_markup:
            recovered = _parse_text_tool_calls(_content)
            if recovered:
                logger.warning(
                    "LLM emitted %d tool call(s) as text markup instead of "
                    "structured tool_calls (model=%s); recovered via text parser. "
                    "Check LLM_LARGE_EXTRA_BODY reasoning config.",
                    len(recovered),
                    getattr(resp, "model", None),
                )
                msg["tool_calls"] = recovered
                # Strip every recognised markup dialect so it can never render as
                # the visible reply; keep any narration that preceded it.
                _c = _re.sub(r"<tool_call\b[^>]*>.*?</tool_call>", "", _content, flags=_re.DOTALL)
                _c = _re.sub(r"<[^>]*?tool_calls\s*>.*?</[^>]*?tool_calls\s*>", "", _c, flags=_re.DOTALL)
                _c = _re.sub(r"<[^>]*?invoke\s+name=.*?</[^>]*?invoke\s*>", "", _c, flags=_re.DOTALL)
                _c = _re.sub(r"</?[^>]*?DSML[^>]*?>", "", _c)
                msg["content"] = _c.strip()
            else:
                # Unparseable markup — most often a block truncated by max_tokens
                # (no closing tag). Fail loud with the likely cause rather than
                # rendering raw markup as an answer.
                _why = (
                    " (response truncated at max_tokens — raise LLM_AGENT_MAX_TOKENS)"
                    if _finish == "length"
                    else ""
                )
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=(
                        "LLM returned malformed tool-call markup that could not "
                        "be parsed into a tool call; refusing to render raw markup "
                        f"as an answer.{_why}"
                    ),
                )
    # Attach token usage + resolved model under private keys for the audit
    # trail. The tool-dispatch loop reads only ``content`` / ``tool_calls``
    # so these are inert, and we never echo them back to the LLM.
    try:
        usage = getattr(resp, "usage", None)
        msg["_usage"] = usage.model_dump() if usage is not None else None
    except Exception:  # noqa: BLE001
        msg["_usage"] = None
    msg["_model"] = getattr(resp, "model", None)
    # Surface the finish_reason so the run path can fail loud on a truncated
    # final turn (finish_reason == "length" + empty content = the silent
    # no-op symptom). Inert to the dispatch loop like the other private keys.
    msg["_finish_reason"] = _finish
    _usage = msg.get("_usage") or {}
    logger.info(
        "[LLM] ← finish=%s tool_calls=%d content=%dchars tokens(in/out)=%s/%s model=%s",
        _finish,
        len(msg.get("tool_calls") or []),
        len(msg.get("content") or ""),
        _usage.get("prompt_tokens"),
        _usage.get("completion_tokens"),
        msg.get("_model"),
    )
    # Meter the agent-loop spend (billing) — this is the bulk of token usage.
    # Non-fatal: metering never breaks a run. Skips when no tenant to bill.
    if tenant_id and (_usage.get("prompt_tokens") or _usage.get("completion_tokens")):
        try:
            from token_metering import record_usage
            await record_usage(
                tenant_id=tenant_id, model=msg.get("_model"), surface=surface,
                tokens_in=_usage.get("prompt_tokens"),
                tokens_out=_usage.get("completion_tokens"))
        except Exception:  # noqa: BLE001 — metering never breaks the run
            logger.exception("[TOKENS] %s metering failed", surface)
    return msg


def _is_pending_approval(action, agent_spec: AgentSpec, inputs: Dict[str, Any]) -> bool:
    """An action is pending_approval if it declares ``approval_required=True``,
    OR if ``agent_spec.hitl_policy.thresholds`` has a numeric threshold that
    a top-level input value exceeds.

    Writes are NOT auto-gated. Approval is a pattern the BA designs per
    action (via ``action.approval_required`` or via ``hitl_policy.thresholds``);
    the platform doesn't second-guess writes. Audit, reversal, and the
    chat-write block remain in force regardless.
    """
    if action.approval_required:
        return True
    policy = agent_spec.hitl_policy or {}
    thresholds = policy.get("thresholds") or {}
    if isinstance(thresholds, dict):
        for field, limit in thresholds.items():
            v = inputs.get(field)
            if isinstance(v, (int, float)) and isinstance(limit, (int, float)):
                if v > limit:
                    return True
    return False


def _current_trace_id() -> Optional[str]:
    """Return the current OpenTelemetry trace_id (32-char hex), or None.

    Graceful no-op when ``opentelemetry`` is not installed in this
    environment — the audit row simply omits the field. Once the
    platform-wide ledger lands (``docs/llm-governance.md`` step 1) the
    same call will start filling ``decision_id == trace_id``.
    """
    try:
        from opentelemetry import trace as _otel_trace  # type: ignore
    except ImportError:
        return None
    try:
        span = _otel_trace.get_current_span()
        ctx = span.get_span_context() if span is not None else None
        if ctx is None or not getattr(ctx, "trace_id", 0):
            return None
        return format(ctx.trace_id, "032x")
    except Exception:  # noqa: BLE001
        return None


# ── MCP tool-SCHEMA cache ─────────────────────────────────────────────────────
# Caches the per-server tool schema list (GET /mcp/{server}/tools) so we don't
# re-fetch it on every /run and every chat turn. This is a SCHEMA cache only —
# it never caches MCP DATA. Live rows are always read fresh on the data path
# (panel_data / dispatch_query_dataset never cache), so a transactional source
# is always current. Keyed by (server, caller-token) with a short TTL; only
# successful fetches are cached so a discovery outage retries next turn.
_TOOLS_SCHEMA_CACHE: Dict[str, tuple] = {}
_TOOLS_SCHEMA_TTL = float(os.getenv("SMART_APP_TOOLS_SCHEMA_TTL_SECONDS", "300"))


def _tools_schema_cache_key(server: str, auth_header: Optional[str]) -> str:
    # Include the caller token so per-user tool visibility differences can't
    # leak across users; hashed so the raw token never sits in a dict key.
    h = hashlib.sha256((auth_header or "").encode("utf-8")).hexdigest()[:16]
    return f"{server}:{h}"


async def _action_tools_to_openai(
    *,
    settings: Settings,
    agent_spec: AgentSpec,
    action,
    auth_header: Optional[str],
    chat_mode: bool = False,
) -> list[dict]:
    """Translate the executing agent's bound MCP tools into OpenAI tool spec.

    The AgentSpec stores tool *names* (``tools: List[str]``) and MCP server
    names (``mcps: List[str]``). To produce real OpenAI tool entries we need
    each tool's JSON schema, which lives in discovery-service. We call
    ``GET {discovery}/mcp/{server}/tools`` for each bound server, then filter
    to the names declared on the executing agent.

    A discovery-service outage degrades to "no tools" so the run still
    completes (LLM answers from the system prompt alone) instead of erroring.

    ``chat_mode`` (Rule H-02 / K-01 / K-02): legacy MCP tool entries returned
    by discovery-service may carry an ``annotations.kind`` / ``x_kind`` /
    ``kind`` field that classifies them as a write. When ``chat_mode=True``,
    any tool whose kind is in the structural-write set is dropped (with a
    log line so the audit shows what the chat path refused to expose). The
    primary chat-side write surface is ``tools_v2`` mcp_action — see
    ``build_openai_tools_from_tools_v2`` for the symmetric filter.
    """
    _CHAT_BLOCKED_KINDS = {"mcp_action", "workflow.invoke", "smart_app_invoke"}
    sub_agent = None
    if action.delegates_to:
        sub_agent = _resolve_sub_agent(agent_spec, action.delegates_to[0])

    tool_names = set(
        list(agent_spec.tools or [])
        + list((sub_agent.tools if sub_agent else []) or [])
    )
    server_names = list(
        dict.fromkeys(
            list(agent_spec.mcps or [])
            + list((sub_agent.mcps if sub_agent else []) or [])
        )
    )
    if not tool_names or not server_names:
        return []

    headers = {"Accept": "application/json"}
    if auth_header:
        headers["Authorization"] = auth_header

    out: list[dict] = []
    for server in server_names:
        ck = _tools_schema_cache_key(server, auth_header)
        _now = time.monotonic()
        _cached = _TOOLS_SCHEMA_CACHE.get(ck)
        if _cached and _cached[0] > _now:
            tools_in_catalog = _cached[1]
        else:
            url = (
                f"{settings.discovery_url_for(current_env()).rstrip('/')}"
                f"/mcp/{server}/tools"
            )
            try:
                # Per-call client (bound to the current event loop). The TTL
                # cache above is what removes the repeated per-turn round-trip;
                # connection pooling is intentionally NOT used here to avoid a
                # process-shared client outliving its loop.
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.get(url, headers=headers)
                if resp.status_code >= 500:
                    # discovery-service OUTAGE → fail loud. Swallowing this to
                    # `continue` (empty tools) lets the app run with NO query/
                    # write tools and answer as if the source had none (RULE #1).
                    logger.error(
                        "discovery-service %s OUTAGE (HTTP %s) — failing loud, not degrading to zero tools",
                        server, resp.status_code,
                    )
                    raise RuntimeError(
                        f"discovery-service unavailable for '{server}' (HTTP {resp.status_code}) — "
                        f"cannot load this app's data tools; the data service is down"
                    )
                if resp.status_code >= 400:
                    # 4xx (e.g. 404 server not registered) = config, not outage.
                    logger.warning(
                        "discovery-service %s returned %s",
                        server,
                        resp.status_code,
                    )
                    continue
                catalog = resp.json()
            except (httpx.HTTPError, ValueError) as e:
                # Unreachable / timeout / malformed response = real failure → throw.
                logger.error("discovery-service %s unreachable — failing loud: %s", server, e)
                raise RuntimeError(
                    f"discovery-service unreachable for '{server}': {e} — cannot load this "
                    f"app's data tools; the data service is down"
                ) from e
            tools_in_catalog = (
                catalog.get("tools") if isinstance(catalog, dict) else catalog
            ) or []
            # Cache the SCHEMA list only (never MCP data); successful fetch only.
            _TOOLS_SCHEMA_CACHE[ck] = (_now + _TOOLS_SCHEMA_TTL, tools_in_catalog)
        for t in tools_in_catalog:
            if not isinstance(t, dict):
                continue
            tname = t.get("name")
            if not tname or tname not in tool_names:
                continue
            # Rule H-02 / K-01 / K-02: chat path strips write tools.
            # Discovery returns a kind hint either at the top level or
            # nested under ``annotations``; we check both so a Bihar-style
            # tool ({"kind":"mcp_action"}) and a more annotated MCP tool
            # ({"annotations":{"kind":"mcp_action"}}) are both caught.
            if chat_mode:
                _kind = (
                    t.get("kind")
                    or t.get("x_kind")
                    or (t.get("annotations") or {}).get("kind")
                    or ""
                )
                if _kind in _CHAT_BLOCKED_KINDS:
                    logger.info(
                        "[CHAT-FILTER] legacy mcp tool %s.%s dropped "
                        "(kind=%s) — chat path is structurally read-only",
                        server, tname, _kind,
                    )
                    continue
            out.append(
                {
                    "type": "function",
                    "function": {
                        "name": tname,
                        "description": t.get("description") or "",
                        "parameters": t.get("input_schema")
                        or t.get("parameters")
                        or {"type": "object", "properties": {}},
                        "x_mcp_server": server,
                    },
                }
            )
    return out


async def _dispatch_mcp_tool(
    *,
    settings: Settings,
    server: str,
    tool_name: str,
    arguments: Dict[str, Any],
    auth_header: Optional[str],
) -> Dict[str, Any]:
    """Invoke an MCP tool via discovery-service.

    discovery-service exposes ``POST /mcp/{server}/call`` (same convention
    used by action-chat-service). Failures are returned as ``{"error": ...}``
    so the LLM can recover rather than the whole run blowing up.
    """
    url = (
        f"{settings.discovery_url_for(current_env()).rstrip('/')}"
        f"/mcp/{server}/call"
    )
    headers = {"Content-Type": "application/json"}
    if auth_header:
        headers["Authorization"] = auth_header
    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            resp = await client.post(
                url,
                json={"tool": tool_name, "arguments": arguments},
                headers=headers,
            )
        except httpx.HTTPError as e:
            return {"error": f"mcp transport: {e}"}
    if resp.status_code >= 400:
        return {
            "error": f"mcp {server}.{tool_name} returned {resp.status_code}",
            "body": resp.text[:500],
        }
    try:
        return resp.json()
    except Exception:  # noqa: BLE001
        return {"result": resp.text}


# Tool-call rounds the root agent gets before a forced synthesis turn.
# Raised from 5 → 12 so a decide-then-write action (query data + cite a
# policy clause + call an mcp_action write tool) has room to finish: a
# read-only agent rarely needs more than 3-4 rounds, but a write-back
# action spends rounds gathering evidence before it can act.
_MAX_TOOL_ITERATIONS = 12
# Rounds the CHAT narrator may spend calling tools before it must answer. Named
# so the loop and the no-reply error below can't drift apart — the error quotes
# this number to tell the caller the agent ran out of budget mid-investigation.
_MAX_CHAT_ROUNDS = 8


class ChatProducedNoReply(Exception):
    """The chat turn ended with no answer at all — no prose AND no chart blocks.

    Raised instead of returning ``reply="(no response)"``. That placeholder was a
    200 that no caller could tell apart from a real answer: the UI printed the
    literal string, the decision API's consumers saw success, and the only way to
    learn a turn had failed was to read it. Observed in prod on a row-level
    question that burned all _MAX_CHAT_ROUNDS rounds and synthesised nothing.

    The endpoint maps this to 502 with the detail, so a failed turn is a failed
    HTTP call. A chart-only answer (blocks, no prose) is NOT this — it is a
    legitimate reply and must not raise.
    """
_DELEGATE_TOOL_NAME = "delegate_to_sub_agent"
_SUB_AGENT_MAX_DEPTH = 2


def _editable_fields_for(
    agent_spec: Any, source_id: Any, dataset_id: Any, action_id: Any
) -> list:
    """The declared officer-overridable FieldSpecs for an mcp_action write,
    serialised — attached to a planned_write so the plan modal can render
    editable controls without an extra agent-spec lookup.

    ENUM DERIVATION: when a FieldSpec declares neither ``control`` nor
    ``options`` but the tool's own pinned ``input_schema`` constrains that
    field with an ``enum``, derive a static select from it. The legal values
    are already in the spec three lines up — without this, the officer gets a
    free-text box for a field the write will only accept 3 values for (found
    in prod: update_inspection_status.status enum[pass,repair,fail] rendered
    as text). Builder-authored options always win; this only fills the gap."""
    for t in (getattr(agent_spec, "tools_v2", None) or []):
        if getattr(t, "kind", None) != "mcp_action":
            continue
        if getattr(t, "action_id", None) != action_id:
            continue
        if source_id and getattr(t, "source_id", None) != source_id:
            continue
        if dataset_id and getattr(t, "dataset_id", None) != dataset_id:
            continue
        ef = getattr(t, "editable_fields", None) or []
        props = ((getattr(t, "input_schema", None) or {}).get("properties") or {})
        out = []
        for f in ef:
            d = f.model_dump(mode="json", exclude_none=True)
            if not d.get("options") and not d.get("control"):
                enum = (props.get(d.get("name")) or {}).get("enum")
                if isinstance(enum, list) and enum:
                    d["control"] = "select"
                    d["options"] = {
                        "kind": "static",
                        "values": [
                            {"value": v, "label": str(v).replace("_", " ").title()}
                            for v in enum
                        ],
                    }
            out.append(d)
        return out
    return []


def _strip_internal_keys(value: Any) -> Any:
    """Drop underscore-prefixed keys from a tool_result before it goes
    back to the LLM as a tool-role message. Internal channels (like the
    ``_source_id`` data_tools.dispatch_perform_action surfaces for the
    /approve replay path) are noise for the model and violate the
    "underscore prefix = internal" convention if they leak into context.
    Recursive — dicts inside lists / nested dicts are stripped too.
    """
    if isinstance(value, dict):
        return {
            k: _strip_internal_keys(v)
            for k, v in value.items()
            if not (isinstance(k, str) and k.startswith("_"))
        }
    if isinstance(value, list):
        return [_strip_internal_keys(v) for v in value]
    return value


def _build_delegate_tool(agent_spec: AgentSpec) -> Optional[dict]:
    """Synthetic ``delegate_to_sub_agent`` tool exposed to the root LLM.

    Returned only when the agent has at least one sub-agent and depth is 0.
    Sub-agents themselves don't see this tool — that prevents A→B→C chains.
    """
    if not agent_spec.sub_agents:
        return None
    sub_ids = [sa.id for sa in agent_spec.sub_agents]
    return {
        "type": "function",
        "function": {
            "name": _DELEGATE_TOOL_NAME,
            "description": (
                "Hand a focused sub-task to a specialist sub-agent. The "
                "sub-agent runs in a fresh LLM context with its own scoped "
                "tools and returns a structured result you can use to "
                "compose the final decision."
            ),
            "parameters": {
                "type": "object",
                "required": ["sub_agent_id", "task"],
                "properties": {
                    "sub_agent_id": {
                        "type": "string",
                        "enum": sub_ids,
                        "description": "Which sub-agent to invoke.",
                    },
                    "task": {
                        "type": "string",
                        "description": "Plain-language task description.",
                    },
                    "context": {
                        "type": "object",
                        "description": "Structured context the sub-agent will see.",
                    },
                },
            },
            "x_synthetic": "delegate",
        },
    }


async def _execute_sub_agent(
    *,
    settings: Settings,
    agent_spec: AgentSpec,
    sub_agent,
    task: str,
    context: Dict[str, Any],
    auth_header: Optional[str],
    depth: int,
    tenant_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Run one sub-agent as a scoped LLM call.

    Returns a dict ``{"result": str, "tool_calls": int, "error": str?}``
    that the root LLM receives as the tool-call response.
    """
    if depth >= _SUB_AGENT_MAX_DEPTH:
        return {"error": "sub_agent_max_depth_reached"}

    # Tool list is restricted: must be a subset of the root agent's tools.
    root_tool_names = set(agent_spec.tools or [])
    sub_tool_names = set(sub_agent.tools or [])
    illegal = sub_tool_names - root_tool_names
    if illegal:
        # Refuse rather than silently strip — this is a privilege boundary.
        return {
            "error": "sub_agent_tools_exceed_root",
            "illegal_tools": sorted(illegal),
        }

    tier = sub_agent.model_tier or agent_spec.model_tier
    requested_tier = tier if tier in _KNOWN_TIERS else _DEFAULT_TIER

    # Build a single-shot pseudo-action so the existing tool plumbing
    # (_action_tools_to_openai) can be reused.
    class _PseudoAction:
        name = f"_sub.{sub_agent.id}"
        description = sub_agent.role
        delegates_to = []
        approval_required = False
        input_schema = None
        output_schema = sub_agent.output_schema

    # We re-use _action_tools_to_openai by passing a temp agent_spec where
    # the root tools/mcps are the sub-agent's. This keeps the schema fetch
    # logic in one place.
    scoped_agent = agent_spec.model_copy(update={
        "tools": list(sub_agent.tools or []),
        "mcps": list(sub_agent.mcps or []),
        "rag": list(sub_agent.rag or []),
        "sub_agents": [],  # no nested delegation
    })
    tools = await _action_tools_to_openai(
        settings=settings,
        agent_spec=scoped_agent,
        action=_PseudoAction(),
        auth_header=auth_header,
    )

    import json as _json
    user_msg = (
        f"Task from parent agent:\n{task}\n\n"
        f"<sub_agent_context>\n{_json.dumps(context or {}, indent=2)}\n"
        "</sub_agent_context>\n\n"
        "Respond with your result. If your spec has an output_schema, "
        "match it exactly."
    )
    messages = [
        {"role": "system", "content": sub_agent.system_prompt},
        {"role": "user", "content": user_msg},
    ]

    max_iters = sub_agent.max_tool_calls or 3
    tool_calls_used = 0
    assistant_msg: Dict[str, Any] = {}
    name_to_server = {
        t["function"]["name"]: t["function"].get("x_mcp_server") for t in tools
    }
    for _ in range(max_iters):
        assistant_msg = await _call_llm(
            settings=settings,
            messages=messages,
            tier=requested_tier,
            tools=tools or None,
            tenant_id=tenant_id,
            surface="sub_agent",
        )
        tcs = assistant_msg.get("tool_calls") or []
        if not tcs:
            break
        messages.append(
            {
                "role": "assistant",
                "content": assistant_msg.get("content") or "",
                "tool_calls": tcs,
            }
        )
        for tc in tcs:
            tool_calls_used += 1
            fn = tc.get("function", {})
            tname = fn.get("name", "")
            try:
                targs = (
                    _json.loads(fn.get("arguments") or "{}")
                    if isinstance(fn.get("arguments"), str)
                    else (fn.get("arguments") or {})
                )
            except _json.JSONDecodeError:
                targs = {}
            server = name_to_server.get(tname) or ""
            if not server:
                tool_result = {"error": f"unknown tool '{tname}'"}
            else:
                tool_result = await _dispatch_mcp_tool(
                    settings=settings,
                    server=server,
                    tool_name=tname,
                    arguments=targs,
                    auth_header=auth_header,
                )
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.get("id", ""),
                    "name": tname,
                    "content": _json.dumps(tool_result)[:4000],
                }
            )

    return {
        "result": assistant_msg.get("content") or "",
        "tool_calls": tool_calls_used,
        "sub_agent_id": sub_agent.id,
    }


# Dashboard-narrator system-prompt addendum. Injected only for agents whose
# app has a dashboard PAGE (page.kind == "dashboard") so the hero-brief
# copilot can return inline charts using a
# fenced ```chart block. The block carries spec fields that mirror
# ChartPanel (chart_type/x/y/group_by/stacked) plus the rows the narrator
# already fetched via its tools. The narrator picks chart_type ONLY.
_DASHBOARD_CHART_ADDENDUM = (
    "\n\nCHART OUTPUT (dashboard copilot):\n"
    "- When the user asks to SEE, COMPARE, RANK or TREND data, append a "
    "fenced ```chart block AFTER your prose answer. Put the rows you "
    "already fetched from your tools directly into the block's \"data\".\n"
    "- The block is a single fenced ```chart ... ``` containing ONE JSON "
    "object with these keys:\n"
    '  {"chart_type":"bar"|"line"|"area"|"pie","title":"<short title>",'
    '"x":"<column name>","y":"<column>"|["<col>",...],'
    '"group_by":"<column>"(optional),"stacked":true|false(optional),'
    '"data":[{"<col>":<value>,...},...]}\n'
    "- chart_type meaning: bar = compare categories, line/area = trend over "
    "time, pie = share of a whole. Pick chart_type ONLY.\n"
    "- NEVER emit colors, sizes, axes styling, or any other rendering hint "
    "— only the keys above. The renderer owns all styling.\n"
    "- x/y must be column names that exist in your data rows. Emit at most "
    "one chart block per turn, and only when a chart genuinely helps.\n"
    "- Keep your text answer clean; do NOT restate the raw rows in prose."
)

# Formatting discipline for the hero brief / dashboard copilot. Fixes three
# real defects seen in prod briefs:
#  - snake_case identifiers (theft_cases, tamper_events) rendered as plain text
#    collide with the markdown italic rule and get mangled → wrap in backticks;
#  - a "230 total" headline followed by a breakdown that only sums to 199
#    silently drops 31 cases → breakdowns must reconcile to the stated total;
#  - briefs that trail off mid-sentence read as broken → always finish.
_DASHBOARD_BRIEF_FORMAT = (
    "\n\nBRIEF FORMATTING (dashboard copilot):\n"
    "- Wrap EVERY dataset, table, column or source identifier in backticks — "
    "e.g. `theft_cases`, `tamper_events`, `recovery_status`. Bare underscores "
    "in prose get parsed as italics and corrupt the text.\n"
    "- RECONCILE breakdowns to the total: if you state a total and then split "
    "it, the parts must add up to that total — either list every bucket, or "
    "name the remainder explicitly (e.g. '230 total: 148 open, 51 recovered, "
    "31 in other statuses'). NEVER present a partial breakdown as if it were "
    "the whole; a subset that doesn't sum to the headline is a defect.\n"
    "- Finish every sentence. Prefer a complete, slightly shorter brief over a "
    "longer one that trails off mid-thought."
)

# Matches a fenced ```chart ... ``` block. The language tag is "chart"
# (optionally followed by whitespace/newline); body is captured lazily.
_CHART_BLOCK_RE = re.compile(
    r"```chart[ \t]*\r?\n(.*?)```",
    re.DOTALL | re.IGNORECASE,
)


def _parse_chart_blocks(text: str) -> tuple[str, List[Dict[str, Any]]]:
    """Extract ```chart fenced blocks from assistant text.

    Returns ``(clean_text, blocks)`` where ``blocks`` is a list of
    ``{"type":"chart","spec":{...},"data":[...]}`` dicts ready for the
    ChatResponse contract, and ``clean_text`` is the prose with the chart
    blocks stripped out.

    A block that fails JSON parse or is missing the minimum spec fields is
    **stripped AND logged** (warning) — never left inline. Leaving raw chart
    JSON in the prose dumps an unreadable code block into the user-facing brief
    (the renderer shows it verbatim). For a user surface, an omitted chart is
    strictly better than a wall of JSON; the failure is still surfaced loudly in
    the logs (fail-loud) rather than to the officer.
    """
    import json as _json

    blocks: List[Dict[str, Any]] = []
    _spec_keys = {"chart_type", "title", "x", "y", "group_by", "stacked"}

    def _replace(match: "re.Match[str]") -> str:
        raw = match.group(1).strip()
        try:
            payload = _json.loads(raw)
        except _json.JSONDecodeError as e:
            logger.warning("narrator emitted an unparseable ```chart block "
                           "(dropped from brief, not shown): %s", e)
            return ""  # strip — do NOT leak raw JSON to the user
        if not isinstance(payload, dict):
            logger.warning("narrator ```chart block was not a JSON object "
                           "(dropped): %r", type(payload).__name__)
            return ""
        data = payload.get("data")
        if not isinstance(data, list):
            data = []
        spec = {k: v for k, v in payload.items() if k in _spec_keys}
        # Minimum viable spec — chart_type + x + y must be present.
        if not (spec.get("chart_type") and spec.get("x") and spec.get("y")):
            logger.warning("narrator ```chart block missing chart_type/x/y "
                           "(dropped): keys=%s", sorted(spec.keys()))
            return ""
        blocks.append({"type": "chart", "spec": spec, "data": data})
        return ""  # strip the block from prose

    clean = _CHART_BLOCK_RE.sub(_replace, text)
    # Belt-and-suspenders: if an UNCLOSED ```chart fence slipped through (the
    # regex needs a closing fence), strip from that fence to end-of-text so raw
    # chart JSON can never reach the user.
    if "```chart" in clean.lower():
        logger.warning("narrator left an unclosed ```chart fence — stripping tail")
        idx = clean.lower().rfind("```chart")
        clean = clean[:idx].rstrip()
    # Collapse the blank lines left where blocks were removed.
    clean = re.sub(r"\n{3,}", "\n\n", clean).strip()
    return clean, blocks


async def _dashboard_ground_truth(
    settings: Settings,
    app_spec: "AppSpec",
    auth_header: Optional[str],
) -> str:
    """Resolve the dashboard page's KPI tile values — the SAME source-side,
    whole-table COUNT/SUM aggregates the tiles render — and format them as an
    authoritative figures block for the hero-brief narrator.

    Why: the narrator otherwise states totals by counting the CAPPED row SAMPLE
    its tools return (so 227 cases reads as 75, ₹4 Cr as the ₹1.45 Cr pending
    bucket). Feeding it the deterministic tile aggregates makes the brief's
    headline numbers match the panels exactly. Returns "" when there's nothing
    to inject (no dashboard KPI panel / no resolved metrics).
    """
    from panel_data import resolve_panel_data  # local import — avoid import cycle

    lines: List[str] = []
    for pg in (getattr(app_spec, "pages", None) or []):
        if getattr(pg, "kind", "standard") != "dashboard":
            continue
        for panel in (getattr(pg, "panels", None) or []):
            if getattr(panel, "type", None) != "dashboard":
                continue
            pid = getattr(panel, "id", None)
            if not pid:
                continue
            resp = await resolve_panel_data(
                settings=settings,
                app_spec=app_spec,
                panel_id=pid,
                auth_header=auth_header,
            )
            for m in (resp.metrics or []):
                nm, val = m.get("name"), m.get("value")
                if nm is not None and val is not None:
                    delta = (m.get("delta") or {}).get("text") if m.get("delta") else None
                    lines.append(f"- {nm}: {val}" + (f" ({delta})" if delta else ""))
    if not lines:
        return ""
    return (
        "\n\nAUTHORITATIVE DASHBOARD FIGURES — the exact whole-table aggregates "
        "the KPI tiles display:\n" + "\n".join(lines) +
        "\n\nNUMBER DISCIPLINE — this is a hard rule:\n"
        "- State ONLY a number that is (a) in the FIGURES list above, or (b) a "
        "value a tool returned to you THIS turn. Quote it verbatim.\n"
        "- NEVER estimate, round-guess, interpolate, or COMPUTE a derived number "
        "you did not directly get — especially period-over-period deltas "
        "('up from N', 'X→Y', '+P%') and per-category splits. If you did not "
        "query the exact windowed/grouped count, DO NOT state it.\n"
        "- If a comparison or breakdown isn't in the figures and you didn't "
        "query it precisely, describe what the figures DO show instead — never "
        "invent the missing number. A brief with no fabricated figure is correct; "
        "a brief with a plausible-but-wrong figure is a defect."
    )


def _looks_like_tool_markup(content: str) -> bool:
    """True if text contains a tool-call markup dialect (GLM <tool_call>,
    DeepSeek <|DSML|invoke>, *_tool_calls wrappers) that must never reach the user."""
    if not content:
        return False
    return (
        "<tool_call" in content
        or "invoke name=" in content
        or "DSML" in content
        or "｜tool" in content  # DeepSeek ｜tool▁calls token
    )


def _strip_tool_markup(content: str) -> str:
    """Remove every tool-call markup dialect (closed OR truncated) so leaked
    markup can never render as the visible reply; keep any narration around it."""
    if not content:
        return content
    import re as _re
    c = _re.sub(r"<tool_call\b[^>]*>.*?</tool_call>", "", content, flags=_re.DOTALL)
    c = _re.sub(r"<[^>]*?tool_calls\s*>.*?</[^>]*?tool_calls\s*>", "", c, flags=_re.DOTALL)
    c = _re.sub(r"<[^>]*?invoke\s+name=.*?</[^>]*?invoke\s*>", "", c, flags=_re.DOTALL)
    c = _re.sub(r"</?[^>]*?DSML[^>]*?>", "", c)
    # truncated / unclosed markup tail (model cut off mid-call by max_tokens)
    c = _re.sub(r"<[^>]*?tool_calls\s*>.*$", "", c, flags=_re.DOTALL)
    c = _re.sub(r"<[^>]*?invoke\s+name=.*$", "", c, flags=_re.DOTALL)
    return c.strip()


async def chat_with_agent(
    *,
    settings: Settings,
    app_spec: AppSpec,
    agent_spec: AgentSpec,
    messages: list,
    auth_header: Optional[str] = None,
) -> Dict[str, Any]:
    """Free-form conversational turn with an app's root agent.

    Powers the runtime ``agent_chat`` panel. Reuses the same tool plumbing
    as ``/run`` (MCP / RAG tools from ``tools_v2``) but is driven by a chat
    transcript instead of a structured action — and emits no audit block.

    ``messages`` is a list of ``{"role": "user"|"assistant", "content": str}``.
    Returns ``{"reply": str, "tool_calls": int}``.
    """
    import json as _json

    # Pseudo-action so _action_tools_to_openai can build the tool list from
    # the agent's tools_v2 / mcps without inventing a separate code path.
    class _ChatAction:
        name = "_chat"
        description = "Conversational Q&A with the operator."
        delegates_to: list = []
        approval_required = False
        input_schema = None
        output_schema = None

    # Rule H-02 / K-01 / K-02: chat is structurally read-only. Both legacy
    # MCP tool plumbing and tools_v2 receive chat_mode=True so any tool
    # whose kind is mcp_action / workflow / smart_app_invoke is dropped
    # before the manifest ever reaches the LLM.
    legacy_tools = await _action_tools_to_openai(
        settings=settings,
        agent_spec=agent_spec,
        action=_ChatAction(),
        auth_header=auth_header,
        chat_mode=True,
    )
    # `tools_v2` is the modern tool field (mcp / rag / …). Bihar-style agents
    # declare their tools ONLY here — `_action_tools_to_openai` reads the
    # legacy `tools[]`/`mcps[]` string lists and would return []. Build both
    # and merge, exactly as execute_run does.
    tools_v2_openai, tools_v2_dispatch_table = build_openai_tools_from_tools_v2(
        agent_spec=agent_spec,
        app_spec=app_spec,
        settings=settings,
        chat_mode=True,
    )
    tools = list(legacy_tools) + list(tools_v2_openai)

    directory_block = _render_dataset_directory_block(app_spec)
    system_prompt = (agent_spec.system_prompt or "").rstrip()
    if directory_block:
        system_prompt = system_prompt + "\n" + directory_block
    tool_names = [
        t.get("function", {}).get("name")
        for t in (tools or [])
        if t.get("function", {}).get("name")
    ]
    tool_hint = (
        f" Your data tools: {', '.join(tool_names)}."
        if tool_names
        else " (No data tools are wired to this app.)"
    )
    system_prompt += (
        f"\n\nYou are answering an operator's question inside the "
        f"\"{app_spec.title}\" app. Be concise and factual.{tool_hint}\n"
        "TOOL-USE RULES (strict):\n"
        "- For ANY question about specific records, counts, lists, totals, "
        "filtering or 'show me…', you MUST call a data tool to get the "
        "answer. Never answer such a question from memory.\n"
        "- Never narrate that you 'will' query or 'let me check' and then "
        "stop — actually emit the tool call in the same turn.\n"
        "- Only after a tool returns results may you state figures; name "
        "the source dataset.\n"
        "- If no available tool can answer, say so plainly.\n"
        "This is an interactive chat — do NOT emit an audit block."
    )
    # Platform ERROR-HANDLING guarantee — bound retries on the chat path too.
    system_prompt += "\n\n" + _RESILIENCE_INSTRUCTION
    # An app with a DASHBOARD PAGE narrates the hero brief; give that narrator
    # the chart-block convention so the copilot can return inline,
    # self-contained charts alongside its prose answer.
    _pages = getattr(app_spec, "pages", None) or []
    _has_dashboard_page = any(
        getattr(p, "kind", "standard") == "dashboard" for p in _pages
    )
    # H3: only the FIRST turn (the auto-brief) needs the ground-truth figures
    # injected — that's what anchors the hero brief. Re-resolving every KPI
    # aggregate on every follow-up turn is pure duplicate source load (the
    # figures don't change mid-conversation, and follow-ups get correct totals
    # from the count-first agentic /query). Gate it to the opening turn.
    _user_turns = sum(1 for m in (messages or []) if m.get("role") == "user")
    if _has_dashboard_page:
        system_prompt += _DASHBOARD_CHART_ADDENDUM
        system_prompt += _DASHBOARD_BRIEF_FORMAT
    # GROUND-TRUTH FIGURE INJECTION — OFF by default. It was a crutch for the
    # capped-row-sample bug, but it OVERRODE the agent's own (correct) prompt:
    # it forced whole-table tile aggregates ("Open theft cases: 146") even when
    # the question's framing differs ("TODAY's briefing" → 0, because the demo's
    # case dates aren't today). The copilot should behave like the main chat —
    # free-form, answering from its own canonical DATA SEMANTICS + the
    # aggregate-query discipline already in its system prompt. Re-enable per
    # deployment only if an app's agent prompt lacks that discipline.
    if (
        _has_dashboard_page
        and _user_turns <= 1
        and os.getenv("DASHBOARD_GROUND_TRUTH_INJECT", "0").strip().lower()
        in ("1", "true", "yes", "on")
    ):
        try:
            _gt = await _dashboard_ground_truth(settings, app_spec, auth_header)
            if _gt:
                system_prompt += _gt
        except Exception as _gt_err:  # noqa: BLE001 — never let it break the chat
            logger.error(
                "[CHAT] dashboard ground-truth injection FAILED for app=%s: %s",
                getattr(app_spec, "slug", "?"), _gt_err,
            )

    convo: list = [{"role": "system", "content": system_prompt}]
    for m in (messages or [])[-12:]:
        role = m.get("role")
        content = m.get("content")
        if role in ("user", "assistant") and content:
            convo.append({"role": role, "content": str(content)})
    if len(convo) < 2 or convo[-1]["role"] != "user":
        return {"reply": "Ask me a question to begin.", "blocks": [], "tool_calls": 0}

    # Query-driven RAG prefetch (hybrid): retrieve reference passages for the
    # operator's CURRENT question BEFORE the first LLM turn, so direct Q&A is
    # answered without spending a round-trip on a search tool call. Chat has no
    # upfront case (unlike the recommend path), so we key the retrieval on the
    # last user message, prefixed with a short window of recent conversation so a
    # terse follow-up ("and rural feeders?") still resolves to the right policy.
    # The full chat history still precedes this block in `convo`, and the live
    # rag tool stays registered as the fallback for anything not covered here.
    try:
        _last_user = convo[-1]["content"]
        _prior = [str(m.get("content") or "") for m in convo[1:-1]
                  if m.get("role") in ("user", "assistant")]
        _hist_ctx = " ".join(_prior[-4:]).strip()
        _pf_query = ((_hist_ctx + " ")[-500:] + _last_user).strip() if _hist_ctx else _last_user
        _rag_block, _ = await _prefetch_rag_blocks(
            settings=settings,
            agent_spec=agent_spec,
            auth_header=auth_header,
            query_text=_pf_query,
            header=_RAG_PREFETCH_HEADER_CHAT,
            # Chat is per-turn + interactive — keep the injected block lean rather
            # than dumping the whole SOP into every message; the live rag tool
            # backs up anything the moderate slice misses.
            top_k_floor=16,
            max_chars_per_source=6000,
            tenant_id=getattr(app_spec, "tenant_id", None),
        )
        if _rag_block:
            # Adjacent to the question it supports: a system turn inserted right
            # before the last user message (history stays ahead of it).
            convo.insert(len(convo) - 1, {"role": "system", "content": _rag_block})
            logger.info("[CHAT] rag prefetch injected (%d chars)", len(_rag_block))
    except Exception as _pf_err:  # noqa: BLE001 — prefetch is best-effort
        logger.warning("[CHAT] rag prefetch skipped: %s", _pf_err)

    tier = agent_spec.model_tier if agent_spec.model_tier in _KNOWN_TIERS else _DEFAULT_TIER
    name_to_server = {
        t["function"]["name"]: t["function"].get("x_mcp_server")
        for t in (tools or [])
    }
    chat_id = uuid.uuid4().hex[:8]
    logger.info(
        "[CHAT %s] start app=%s agent=%s tools=%d [%s] history=%d tier=%s",
        chat_id,
        getattr(app_spec, "slug", "?"),
        getattr(agent_spec, "agent_id", "?"),
        len(tools or []),
        ", ".join(tool_names) or "none",
        len(convo) - 1,
        tier,
    )
    tool_calls_used = 0
    chat_write_events: list[dict] = []
    assistant_msg: Dict[str, Any] = {}
    for _round in range(_MAX_CHAT_ROUNDS):
        logger.info("[CHAT %s] round %d → calling LLM", chat_id, _round + 1)
        assistant_msg = await _call_llm(
            settings=settings,
            messages=convo,
            tier=tier,
            tools=tools or None,
            tenant_id=getattr(app_spec, "tenant_id", None),
            surface="chat",
        )
        tcs = assistant_msg.get("tool_calls") or []
        if not tcs:
            logger.info(
                "[CHAT %s] round %d ← no tool calls; final text reply (%d chars)",
                chat_id,
                _round + 1,
                len(assistant_msg.get("content") or ""),
            )
            break
        logger.info(
            "[CHAT %s] round %d ← LLM requested %d tool call(s): %s",
            chat_id,
            _round + 1,
            len(tcs),
            ", ".join(tc.get("function", {}).get("name", "?") for tc in tcs),
        )
        convo.append(
            {
                "role": "assistant",
                "content": assistant_msg.get("content") or "",
                "tool_calls": tcs,
            }
        )
        for tc in tcs:
            tool_calls_used += 1
            fn = tc.get("function", {})
            tname = fn.get("name", "")
            try:
                raw_args = fn.get("arguments")
                targs = (
                    _json.loads(raw_args or "{}")
                    if isinstance(raw_args, str)
                    else (raw_args or {})
                )
            except _json.JSONDecodeError:
                logger.warning(
                    "[CHAT %s] tool %s: could not JSON-parse arguments %r",
                    chat_id, tname, fn.get("arguments"),
                )
                targs = {}
            logger.info(
                "[CHAT %s] dispatch %s args=%s",
                chat_id, tname, _json.dumps(targs, default=str)[:300],
            )
            if tname in tools_v2_dispatch_table:
                _entry = tools_v2_dispatch_table[tname]
                # Writes are UNCONDITIONALLY blocked from chat. Chat has no
                # approval UI, so a source-system write here would bypass the
                # /run HITL gate. The deprecated
                # ``hitl_policy.allow_writes_in_chat`` flag is NOT honored
                # (publish rejects it too) — chat is structurally read-only.
                # mcp_action is also stripped from the chat tool manifest
                # (build_openai_tools_from_tools_v2 / _action_tools_to_openai,
                # chat_mode=True); this is the runtime fail-loud floor for a
                # stored/migrated spec that still carries a write tool.
                if _entry.get("kind") == "mcp_action":
                    tool_result = {
                        "error": (
                            "writes are not allowed from chat — submit the "
                            "action via the run endpoint so the HITL gate "
                            "and audit trail apply"
                        ),
                        "code": "writes_disabled_in_chat",
                    }
                    logger.info(
                        "[CHAT %s] tool %s BLOCKED (chat is read-only; source "
                        "writes go through /run + HITL approval)",
                        chat_id, tname,
                    )
                    chat_write_events.append(
                        _build_write_event(
                            tool=tname,
                            kind="mcp_action",
                            args=targs if isinstance(targs, dict) else {},
                            result=tool_result,
                            status="blocked",
                            dataset_id=_entry.get("dataset_id"),
                            action_id=_entry.get("action_id"),
                            source_id=_entry.get("source_id"),
                        )
                    )
                else:
                    tool_result: Any = await dispatch_tools_v2_call(
                        settings=settings,
                        agent_spec=agent_spec,
                        app_spec=app_spec,
                        dispatch_table=tools_v2_dispatch_table,
                        tool_name=tname,
                        arguments=targs if isinstance(targs, dict) else {},
                        auth_header=auth_header,
                    )
            else:
                server = name_to_server.get(tname) or ""
                if not server:
                    tool_result = {"error": f"unknown tool '{tname}'"}
                else:
                    tool_result = await _dispatch_mcp_tool(
                        settings=settings,
                        server=server,
                        tool_name=tname,
                        arguments=targs,
                        auth_header=auth_header,
                    )
            # Result summary — error verbatim, otherwise a row-count so the
            # console shows whether the tool actually returned data.
            if isinstance(tool_result, dict) and tool_result.get("error"):
                logger.warning(
                    "[CHAT %s] tool %s ← ERROR: %s",
                    chat_id, tname, str(tool_result.get("error"))[:300],
                )
            else:
                if isinstance(tool_result, dict):
                    _rows = tool_result.get("rows")
                    _n = len(_rows) if isinstance(_rows, list) else "n/a"
                elif isinstance(tool_result, list):
                    _n = len(tool_result)
                else:
                    _n = "n/a"
                logger.info("[CHAT %s] tool %s ← ok (rows=%s)", chat_id, tname, _n)
            convo.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.get("id", ""),
                    "name": tname,
                    "content": _json.dumps(tool_result)[:4000],
                }
            )

    # If the loop hit its iteration cap while still mid-tool-call, the last
    # assistant message carries no final text. Force one tool-free synthesis
    # turn so the agent must answer from the tool results it already has.
    if (
        assistant_msg.get("tool_calls")
        or not (assistant_msg.get("content") or "").strip()
        or _looks_like_tool_markup(assistant_msg.get("content") or "")
    ):
        logger.info(
            "[CHAT %s] forcing tool-free synthesis turn (hit iter cap, no final "
            "text, or leaked tool-call markup)",
            chat_id,
        )
        try:
            final = await _call_llm(
                settings=settings, messages=convo, tier=tier, tools=None,
                tenant_id=getattr(app_spec, "tenant_id", None), surface="chat",
            )
            if (final.get("content") or "").strip():
                assistant_msg = final
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[CHAT %s] synthesis turn failed: %s", chat_id, exc
            )

    # Final safety net: strip any tool-call markup that survived recovery +
    # synthesis, so the user NEVER sees raw <tool_call>/<|DSML|invoke> text.
    reply = _strip_tool_markup((assistant_msg.get("content") or "").strip())
    # Pull any ```chart fenced blocks out of the prose into structured
    # blocks[] (spec + inline data) and strip them from the user-visible
    # reply. No-op for non-dashboard agents (they never emit chart blocks).
    reply, blocks = _parse_chart_blocks(reply)
    # Nothing to say AND nothing to draw = the turn failed. Fail loud: this used
    # to return 200 with reply="(no response)", which the UI printed verbatim and
    # every API consumer read as success. A chart-only answer (blocks, no prose)
    # is legitimate and must NOT raise — hence the `and not blocks`.
    if not reply and not blocks:
        logger.error(
            "[CHAT %s] NO REPLY after %d tool call(s) — model returned empty "
            "content and the tool-free synthesis retry did too",
            chat_id, tool_calls_used,
        )
        raise ChatProducedNoReply(
            f"the agent produced no answer after {tool_calls_used} tool call(s). "
            f"The model returned empty content and the tool-free synthesis retry "
            f"did too — it likely spent its budget investigating without "
            f"converging (round cap {_MAX_CHAT_ROUNDS}), or the reasoning ate the "
            f"output tokens. Retry, or narrow the question."
        )
    logger.info(
        "[CHAT %s] done: tool_calls=%d reply=%dchars blocks=%d write_events=%d",
        chat_id, tool_calls_used, len(reply), len(blocks), len(chat_write_events),
    )
    return {
        "reply": reply,
        "blocks": blocks,
        "tool_calls": tool_calls_used,
        "write_events": chat_write_events,
        "trace_id": _current_trace_id(),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Read-before-write: deterministic anchor-record hydration (Part A)
# ─────────────────────────────────────────────────────────────────────────────
class AnchorRecordUnavailable(Exception):
    """The action's anchor record could not be read — the run must fail loud.

    The decision is ABOUT this record; proceeding with only the id in the prompt
    would let the agent reason over a record it never saw (or one that doesn't
    exist). Surfaced as a failed run with a precise reason, never a silent skip.
    """


def _sql_quote_literal(value: Any) -> str:
    """Single-quote + escape a literal for an inlined SQL equality (SELECT-only is
    enforced server-side by the dept-MCP; we still escape quotes)."""
    return "'" + str(value).replace("'", "''") + "'"


def _build_anchor_query(kind: str, dataset_id: str, key_field: str, key_value: Any):
    """Per-kind STRUCTURED read-by-key for /run_query. Mirrors main._build_readback_query."""
    if kind == "sql":
        return f"SELECT * FROM {dataset_id} WHERE {key_field} = {_sql_quote_literal(key_value)}"
    if kind == "odata":
        return {"entity": dataset_id, "$filter": f"{key_field} eq {_sql_quote_literal(key_value)}", "$top": 1}
    if kind == "soql":
        return f"SELECT FIELDS(ALL) FROM {dataset_id} WHERE {key_field} = {_sql_quote_literal(key_value)} LIMIT 1"
    return None


def _rows_from_tool_result(res: Any) -> Optional[list]:
    """Best-effort extract the row list from a read tool's result (any shape).

    Used only to feed the read-ledger id harvest, so an over-broad guess is
    harmless — we only ever read id-like scalars out of it."""
    if isinstance(res, list):
        return res
    if isinstance(res, dict):
        for k in ("rows", "results", "data", "records", "items"):
            v = res.get(k)
            if isinstance(v, list):
                return v
    return None


async def _prefetch_record(
    *,
    settings: Settings,
    action,
    inputs: Dict[str, Any],
    auth_header: Optional[str],
) -> tuple[str, Optional[Dict[str, Any]]]:
    """Deterministically read the action's ANCHOR record and return (prompt_block, row).

    Runtime-owned first read: when the action declares ``anchor_read`` and the
    caller supplied its key, we do ONE exact-filter, SELECT-only read via the
    dept-MCP structured plane and inject the row into the prompt — so the base
    record the decision is about is always grounded, without depending on the
    agent to fetch it.

    Fail-loud (unlike enrichment prefetches, which fail open): a missing/
    unreadable anchor record raises ``AnchorRecordUnavailable`` — the whole
    decision hinges on this record existing and being read.

    Returns ("", None) when the action declares no ``anchor_read`` or the caller
    supplied no key (nothing to hydrate; the read-before-write guard still
    enforces the agent read it via its own tools)."""
    ar = getattr(action, "anchor_read", None)
    if ar is None:
        return "", None
    key_value = (inputs or {}).get(ar.key_field)
    if key_value is None or (isinstance(key_value, str) and not key_value.strip()):
        return "", None

    # The structured read-by-key needs the PHYSICAL table, not the catalogue
    # dataset_id. dataset_id is "{source_id}.{table}" (e.g.
    # "field_operations.complaints") — strip the source prefix so the FROM clause
    # is a real table ("complaints"), matching the outcome-poller's read-back.
    _table = ar.dataset_id
    if _table.startswith(ar.source_id + "."):
        _table = _table[len(ar.source_id) + 1:]

    query = _build_anchor_query(ar.kind, _table, ar.key_field, key_value)
    if query is None:
        raise AnchorRecordUnavailable(
            f"anchor_read.kind={ar.kind!r} is not a supported structured read plane"
        )

    from proxy_clients import call_dept_mcp_read, ProxyError

    user_jwt = (auth_header or "").removeprefix("Bearer ").strip() or None
    try:
        resp = await call_dept_mcp_read(
            settings=settings, user_jwt=user_jwt,
            source_id=ar.source_id, dataset_id=ar.dataset_id,
            kind=ar.kind, query=query, row_limit=1,
        )
    except ProxyError as exc:
        raise AnchorRecordUnavailable(
            f"could not read anchor record {ar.key_field}={key_value!r} from "
            f"{ar.dataset_id}: {exc}"
        ) from exc

    rows = (resp or {}).get("rows") or []
    if not rows:
        raise AnchorRecordUnavailable(
            f"anchor record {ar.key_field}={key_value!r} not found in {ar.dataset_id} "
            f"— the decision references a record that does not exist"
        )
    row = rows[0] if isinstance(rows[0], dict) else {}
    import json as _json
    block = (
        "## ANCHOR RECORD (pre-loaded — the record this decision is about)\n"
        f"Dataset `{ar.dataset_id}`, keyed `{ar.key_field}={key_value}`:\n"
        f"```json\n{_json.dumps(row, indent=2, default=str)[:8000]}\n```\n"
        "This is the authoritative current state of the record. You still MUST "
        "make the reads your decision needs (related datasets, policy, precedent), "
        "but you do not need to re-fetch this base record."
    )
    return block, row


async def execute_run(
    *,
    settings: Settings,
    app_spec: AppSpec,
    agent_spec: AgentSpec,
    request: RunRequest,
    auth_header: Optional[str] = None,
    skip_approval_gate: bool = False,
    plan_only: bool = False,
) -> RunResponse:
    """Execute one /run invocation.

    ``auth_header`` is the user's ``Authorization: Bearer ...`` value; when
    set we forward it to discovery-service so it can apply org/dept
    visibility filtering. It is NOT forwarded to the LLM endpoint —
    OpenRouter / inference-service calls run under this service's own
    credential.

    ``skip_approval_gate`` lets a caller that has ALREADY satisfied approval
    skip the plan-only pin below. No production caller sets it today (the
    approve endpoint replays ``planned_writes`` directly rather than re-running
    the loop); it is kept as the explicit opt-out so the pin is never bypassed
    by accident.

    ``plan_only`` runs the LLM tool loop in plan-then-apply mode: every
    write-capable tool (perform_action + tools_v2 mcp_action) is forced
    to dry_run, and the intents are accumulated into ``planned_writes``.
    If any writes were proposed, the response carries
    ``status='pending_approval'`` and the UI shows the plan + Apply /
    Cancel buttons. The /approve endpoint then replays exactly those
    planned_writes against /execute_action with dry_run=False — no
    second LLM round-trip, so apply == plan. UI-triggered queue actions
    pass plan_only=True; workflow-triggered (SmartAppInvokerNode) calls
    leave it at False so unattended batch runs keep auto-applying.
    """
    # Bind this run's user for the per-user LLM-call rate limit, then pre-flight
    # check so an over-limit user gets a clean 429 BEFORE any work/tokens spent
    # (each _call_llm below also counts + enforces).
    llm_rate_limit.set_current_user(request.user_id)
    llm_rate_limit.check_current_user()
    trace_id = _current_trace_id()
    correlation_id = (
        request.correlation_id
        or trace_id
        or f"run_{uuid.uuid4().hex[:12]}"
    )
    action = _resolve_action(agent_spec, request.action)
    _validate_inputs(action, request.inputs)
    timeline: list[dict] = [{"step": "validate_inputs", "status": "ok"}]

    # Audit accumulators — surfaced on the RunResponse, persisted by the
    # /run endpoint into the append-only app_run_audit collection.
    few_shot_refs: list[dict] = []
    tool_refs: list[dict] = []
    write_events: list[dict] = []
    item_findings: list[dict] = []  # image_analyze / doc_extract structured findings

    def _scorecard():
        """Assemble the declared rubric from this run's factor findings.

        Deliberately NOT wrapped in a try/except. A malformed factor finding
        means the rubric was not executed correctly, and the officer must not be
        handed a recommendation card that quietly lost its grade — a scorecard
        that silently omits a factor renders with exactly the same authority as
        a complete one. Returns None when the app declares no factor_set, which
        is the common case and not an error.

        Only called on the non-failure paths: a run that already failed reports
        its error, and scoring a partial run adds nothing."""
        return build_scorecard(getattr(app_spec, "factor_set", None), item_findings)
    # Plan-only accumulator: every dry-run intent captured during the
    # tool loop. The /approve endpoint replays these against
    # /execute_action with dry_run=False — empty list means the LLM
    # proposed no writes, which collapses to a regular "completed" run.
    planned_writes: list[dict] = []
    usage_acc: Dict[str, int] = {}
    model_used: Optional[str] = None

    # APPROVAL REQUIRED ⇒ PLAN IT, never auto-apply.
    #
    # This branch used to RETURN here — "surface approval-needed without burning
    # an LLM call" — so an approval-gated action produced no recommendation, no
    # narrative and planned_writes=[]. The officer got a card headed "review the
    # plan below and Apply to commit" with no plan in it, and Apply replayed
    # nothing. Marking an action as needing approval made it strictly LESS
    # capable, which is backwards, and it meant learned judgements could never
    # reach these apps at all: the model they were meant to steer never ran.
    # (The docstring claimed skip_approval_gate would be True from the approve
    # endpoint; nothing in the repo ever set it, so the early return was
    # unconditional.)
    #
    # Forcing plan_only is what makes running the loop safe: every write-capable
    # tool is dry-run only, the intents accumulate into planned_writes, and the
    # officer's Apply replays exactly those. It also closes a real hole on the
    # unattended path — a workflow-triggered run passes plan_only=False to
    # auto-apply, so an approval_required action reached by a trigger must be
    # pinned to planning here or approval would be silently skipped.
    if not skip_approval_gate and _is_pending_approval(
        action, agent_spec, request.inputs
    ):
        plan_only = True
        timeline.append(
            {
                "step": "approval_gate",
                "status": "plan_then_apply",
                "detail": "approval_required",
            }
        )

    messages, requested_tier = _build_messages(
        agent_spec=agent_spec,
        action=action,
        inputs=request.inputs,
        app_spec=app_spec,
    )

    # ── READ-BEFORE-WRITE (Part A + ledger init) ────────────────────────────
    # The caller sent only an id; the runtime pre-loads the anchor record it
    # names (deterministic, exact-filter) so the base record is always grounded,
    # and starts the read ledger the write-guard (Part B) checks at plan time.
    ledger = ReadLedger()
    anchors = resolve_anchor_ids(action, request.inputs)
    try:
        anchor_block, anchor_row = await _prefetch_record(
            settings=settings, action=action,
            inputs=request.inputs, auth_header=auth_header,
        )
    except AnchorRecordUnavailable as exc:
        logger.error("[RUN %s] anchor record unavailable — %s", correlation_id, exc)
        timeline.append({"step": "anchor_prefetch", "status": "failed", "detail": str(exc)})
        return RunResponse(
            correlation_id=correlation_id,
            status="failed",
            outputs={},
            timeline=timeline,
            error=str(exc),
            trace_id=trace_id,
        )
    if anchor_block:
        messages.append({"role": "system", "content": anchor_block})
        # Seed the ledger: the runtime itself has now read this record, so the
        # base-record half of the guard is satisfied even if the agent never
        # re-queries it.
        ledger.note_record_read(rows=[anchor_row] if isinstance(anchor_row, dict) else None)
        for a in anchors:
            ledger.seen_values.add(a.value)
        timeline.append(
            {"step": "anchor_prefetch", "status": "ok", "block_chars": len(anchor_block)}
        )

    # DECISION CRITERIA — the record-level rules this app's officers taught it,
    # selected for THIS case by facet scope. Few-shot below retrieves PRECEDENTS
    # (past cases, k-limited); this block carries the PRINCIPLES that apply to
    # every case, including novel ones. Enrichment: loud, never blocking.
    #
    # The facets computed here are also what the item tools (image/document)
    # inherit — their own subject is not knowable until the model has looked, so
    # the record's context is the only scope they can route on.
    # The case record is the anchor row when the action declares an anchor, and
    # otherwise the run's own inputs. A queue action carries the row the officer
    # clicked as its inputs — that row IS the case, and it is in the prompt, so
    # facets derived from it are still "what the model saw", not a recomputation.
    # Without this fallback every action-approval correction landed with
    # case_facets=[]: consolidation can reinforce an existing judgement from
    # uncoded evidence but can never AUTHOR one, so those apps recorded officer
    # feedback forever and learned no new rule. derive_facets reads only the
    # columns the signature declares, so unrelated action parameters are ignored
    # rather than mistaken for case context.
    _case_record = (
        anchor_row if isinstance(anchor_row, dict)
        else (request.inputs if isinstance(request.inputs, dict) else None)
    )
    _clause_block, _injected_clause_ids, _case_facets, _clause_meta = (
        await _prefetch_decision_clauses(
            app_spec, _case_record, agent_spec=agent_spec)
    )
    if _clause_block:
        _inject_few_shot_into_messages(messages, _clause_block)
        timeline.append({"step": "decision_clauses", "status": "ok",
                         "clauses": len(_injected_clause_ids),
                         "facets": len(_case_facets),
                         "block_chars": len(_clause_block)})

    # Pre-inject canonical + neighbor few-shot blocks (when this AgentSpec
    # carries neighbor_samples tools). Collapses what would otherwise be
    # 3 LLM round-trips into 1. The tools stay registered in tools_v2 for
    # opt-in custom-filter searches but the typical case never re-fires
    # them because the pre-injected context is already there.
    few_shot_block, few_shot_refs = await _prefetch_few_shot_blocks(
        agent_spec=agent_spec,
        inputs=request.inputs,
    )
    # Memory-lift instrumentation (adoption plan §2.1): how many GROUNDING
    # samples informed this run — captured BEFORE rag refs are appended so the
    # count isolates memory (past decisions), not policy retrieval. Rides the
    # references dict → run audit → staging row → DecisionRecord, where
    # "acceptance with memory vs cold" becomes one group key.
    grounding_retrieval_count = len(few_shot_refs)
    if few_shot_block:
        _inject_few_shot_into_messages(messages, few_shot_block)
        # Surface cold-start in the timeline so the API caller (and
        # any UI inspecting timeline events) can render a "samples
        # still indexing" hint to the BA.
        is_coldstart = few_shot_block == _FEWSHOT_COLDSTART_NOTE
        timeline.append(
            {
                "step": "few_shot_prefetch",
                "status": "coldstart" if is_coldstart else "ok",
                "block_chars": len(few_shot_block),
            }
        )

    # Prefetch the agent's RAG/policy source(s) ONCE and inject as context, so
    # the agent doesn't burn in-loop turns re-fetching the same static clauses.
    rag_block, rag_refs = await _prefetch_rag_blocks(
        settings=settings,
        agent_spec=agent_spec,
        action=action,
        inputs=request.inputs,
        auth_header=auth_header,
        # Recommend/agent runs often carry no end-user JWT — pass the app's org
        # so a trusted service token can be minted for the semantic read.
        tenant_id=getattr(app_spec, "tenant_id", None),
    )
    if rag_block:
        _inject_few_shot_into_messages(messages, rag_block)
        few_shot_refs.extend(rag_refs)
        timeline.append(
            {"step": "rag_prefetch", "status": "ok", "block_chars": len(rag_block)}
        )
        logger.info("[RUN %s] rag prefetch injected (%d chars)", correlation_id, len(rag_block))

    # ONE references dict for every RunResponse below. few_shot_refs/tool_refs
    # are mutated in place through the tool loop, so the final returns see the
    # complete lists; retrieval_count stays the pre-RAG grounding count.
    run_references = {
        "few_shot_samples": few_shot_refs,
        "tool_calls": tool_refs,
        "retrieval_count": grounding_retrieval_count,
        # Clause memory (plan §10.1). `injected_clause_ids` is the DENOMINATOR
        # for fired_count and the set a citation is checked against; the frozen
        # facets travel with it so the correction recorded at disposition
        # carries the signature the model actually saw — recomputing later would
        # let an ontology edit rewrite history.
        "injected_clause_ids": _injected_clause_ids,
        "case_facets": _case_facets,
        "signature_version": (
            (_case_signature_of(app_spec) or {}).get("version")
        ),
    }

    # Prefetch OFFICER CORRECTIONS — past rejections/overrides + the officer's
    # stated reason — so the model sees WHY similar recommendations were corrected
    # and can generalise the fix (the causal half of the self-improving loop).
    corrections_block = await _prefetch_corrections_block(
        slug=getattr(app_spec, "slug", None),
        case_facets=_case_facets,
    )
    if corrections_block:
        _inject_few_shot_into_messages(messages, corrections_block)
        timeline.append(
            {"step": "corrections_prefetch", "status": "ok", "block_chars": len(corrections_block)}
        )
        logger.info(
            "[RUN %s] officer-corrections prefetch injected (%d chars)",
            correlation_id, len(corrections_block),
        )

    tools = await _action_tools_to_openai(
        settings=settings,
        agent_spec=agent_spec,
        action=action,
        auth_header=auth_header,
    )
    # Phase 9: BA-declared tools_v2 (validate_form / vision_ocr / mcp /
    # rag / workflow). These live alongside the legacy mcps[]+tools[]
    # path until BAs migrate. Both lists merge into one OpenAI tool list.
    tools_v2_openai, tools_v2_dispatch_table = build_openai_tools_from_tools_v2(
        agent_spec=agent_spec,
        app_spec=app_spec,
        settings=settings,
    )
    if tools_v2_openai:
        tools = list(tools) + tools_v2_openai
    # Inject the synthetic ``delegate_to_sub_agent`` tool when sub-agents
    # exist. Sub-agents themselves never see this tool (depth limit).
    delegate_tool = _build_delegate_tool(agent_spec)
    if delegate_tool is not None:
        tools = list(tools) + [delegate_tool]
    # Inject synthetic data tools (`query_dataset`, `perform_action`) when
    # the action declares data_bindings. The dataset_id enum constrains the
    # LLM to exactly the datasets the BA approved at design time.
    data_tools_list = build_data_tools(action, app_spec)
    if data_tools_list:
        tools = list(tools) + data_tools_list
    # FORCE THE WRITE CONTRACT: a write-capable action run must RECORD its decision
    # by CALLING its write tool. Some models (notably GLM) otherwise narrate a
    # recommendation in prose and call NO tool, so nothing is staged/committed — a
    # silent no-op that breaks the recommend / auto-process trigger flows (confirmed:
    # trigger agents returning finish=stop tool_calls=0, pure narrative). The write
    # tool is either perform_action (catalogued data_bindings) OR a tools_v2
    # mcp_action named after its action_id (e.g. update_recovery_status). Name it
    # explicitly so the model calls it. plan_only still dry-runs the call and the
    # gate/approval still apply — this only ensures the tool IS actually called.
    _write_tool_names = [
        t.action_id for t in (getattr(agent_spec, "tools_v2", None) or [])
        if getattr(t, "kind", None) == "mcp_action" and getattr(t, "action_id", None)
    ]
    if any((t.get("function") or {}).get("name") == "perform_action" for t in (data_tools_list or [])):
        _write_tool_names.append("perform_action")
    # EFFICIENCY (#3) — batch reads into ONE turn. Each LLM turn in the dispatch
    # loop is a full round-trip, so one-query-per-turn is the dominant latency
    # cost of a multi-source decision. Tell the model to fetch everything it can
    # at once, then reason. Universal — works for BOTH read mechanisms
    # (query_dataset and tools_v2 mcp/rag), so it helps every agent. The
    # "only chain if dependent" clause preserves genuine query→query joins
    # (e.g. fetch the outage row, THEN look up its DT/feeder by the ids it
    # returned), so it never forces a wrong batch.
    if tools:
        messages.append({
            "role": "system",
            "content": (
                "EFFICIENCY — each turn is a full LLM round-trip and is the dominant "
                "latency cost, so minimise turns:\n"
                "- When a decision needs several datasets, emit those reads as MULTIPLE "
                "SEPARATE tool calls in ONE response (one TARGETED query per dataset), "
                "then reason over the results together.\n"
                "- ANCHOR-THEN-FAN-OUT: it is fine to fetch ONE anchor record first when "
                "later lookups need ids from it (e.g. the outage row, to learn its "
                "feeder/DT ids). But THEN fire ALL the dependent lookups — feeder, DT, "
                "tamper/theft history, precedent — in a SINGLE turn, because they depend "
                "on the anchor, NOT on each other. Do NOT spread them across turns.\n"
                "- Never re-query a dataset you already fetched this run; reuse the result "
                "you already have.\n"
                "- Keep each query as NARROW as the decision needs — do NOT widen a query "
                "or merge datasets to 'get everything'. Only take a further turn when a "
                "query genuinely DEPENDS on a value an earlier one returned that is not "
                "yet in hand."
            ),
        })

    if _write_tool_names:
        # In plan_only mode the tool only DRY-RUNS — say "stage your recommendation"
        # (not "record your decision") so the model doesn't narrate as if committed.
        _contract_verb = "stage your recommendation" if plan_only else "record your decision"
        messages.append({
            "role": "system",
            "content": (
                f"ACTION CONTRACT — to {_contract_verb} you MUST CALL the write "
                f"tool ({' or '.join(_write_tool_names)}) with its required fields. Do "
                "any reads (query_dataset) you need first, then ALWAYS finish by calling "
                "it. A prose reply that calls no write tool is DISCARDED and produces no "
                "recommendation for the officer."
            ),
        })
        if plan_only:
            # PLAN-MODE NARRATIVE FRAMING. The write tool only dry-runs/validates here;
            # nothing is committed until the officer clicks Apply. Without this the model
            # echoes the dry-run's success and narrates "Dispatch confirmed / recorded /
            # assigned" — confusing and unsafe in a human-approval flow (the user thinks
            # it already happened). Force proposal-tense, pending-approval framing.
            messages.append({
                "role": "system",
                "content": (
                    "PLAN MODE — your output is a RECOMMENDATION, NOT a committed action. "
                    "Calling the write tool only DRY-RUNS and validates it; NOTHING is "
                    "written to any system yet, and it will not be until the officer "
                    "reviews and clicks Apply. Phrase your final summary as a PROPOSAL "
                    "pending approval — use 'Recommended', 'Proposed', or 'pending your "
                    "approval'. Do NOT use 'confirmed', 'recorded', 'dispatched', "
                    "'assigned', 'done', or any past tense that implies the change has "
                    "already happened — it has NOT."
                ),
            })
    tenant_id = (app_spec.tenant_id if app_spec is not None else None) or ""
    # Rule C-02 — per-session token budget. A runaway LLM that keeps
    # spawning tool calls would otherwise burn the tier's per-run budget
    # silently. We enforce a hard cap on cumulative total_tokens; when
    # exceeded, we break the tool loop and force ONE final tool-free
    # synthesis call so the user still gets a coherent reply (and the
    # timeline narrates the budget event — no silent truncation).
    try:
        _token_budget = int(os.getenv("SMART_APP_RUN_MAX_TOKENS", "200000"))
    except ValueError:
        _token_budget = 200_000
    _budget_exceeded = False
    # EFFICIENCY (#5) — within-run READ memoization: (tool, args) → result, so a
    # model that re-issues an identical read across turns gets the cached result
    # instead of a fresh 3–30s NL→SQL round-trip. Reads only; writes never cached.
    _tool_result_cache: Dict[str, Any] = {}
    try:
        assistant_msg: Dict[str, Any] = {}
        # One-shot in-loop retry for the evidence gate: when the model tries to
        # finish with staged writes the gate would block, it gets the violation
        # fed BACK as a corrective turn (so it can read the anchor and re-stage)
        # before the post-loop gate fails the run for the officer.
        _evidence_retry_used = False
        logger.info(
            "[RUN %s] start action=%s agent=%s tools=%d tier=%s budget=%d",
            correlation_id,
            getattr(action, "name", "?"),
            getattr(agent_spec, "agent_id", "?"),
            len(tools or []),
            requested_tier,
            _token_budget,
        )
        for iteration in range(_MAX_TOOL_ITERATIONS):
            logger.info(
                "[RUN %s] iteration %d → calling LLM", correlation_id, iteration + 1
            )
            # Force a tool call on the first iteration of a write-capable action
            # run so the model can't return a prose-only "recommendation" that
            # records nothing. GATED on a setting because not every model/provider
            # supports tool_choice="required" — notably z-ai/glm-5.1 on OpenRouter
            # 404s ("No endpoints found") when it's sent. Enable this ONLY with a
            # tool-call-reliable model (Claude / GPT-class) for the action tier.
            _force = (
                getattr(settings, "force_action_tool_call", False)
                and _write_tool_names
                and iteration == 0
            )
            assistant_msg = await _call_llm(
                settings=settings,
                messages=messages,
                tier=requested_tier,
                tools=tools or None,
                tool_choice=("required" if _force else "auto"),
                tenant_id=tenant_id or None,
                surface="agent_run",
            )
            _accumulate_usage(usage_acc, assistant_msg)
            if assistant_msg.get("_model"):
                model_used = assistant_msg["_model"]
            tool_calls = assistant_msg.get("tool_calls") or []
            if not tool_calls:
                # READ-BEFORE-WRITE self-correction (in-loop). The model is
                # about to FINISH with staged writes — if the evidence gate
                # would block them, tell the MODEL (once) exactly what it
                # skipped so it can read the evidence and re-stage, instead of
                # failing post-hoc where only the officer sees the error. The
                # post-loop gate remains the fail-loud backstop if the model
                # ignores the correction.
                logger.info(
                    "[RUN %s] finish-gate check: plan_only=%s writes=%d "
                    "retry_used=%s enforce=%s plan_mode=%r lookup_mode=%r",
                    correlation_id, plan_only, len(planned_writes),
                    _evidence_retry_used,
                    getattr(settings, "enforce_read_before_write", True),
                    getattr(settings, "read_before_write_plan_mode", "enforce"),
                    getattr(settings, "required_reads_mode", "log"),
                )
                if (
                    plan_only and planned_writes and not _evidence_retry_used
                    and getattr(settings, "enforce_read_before_write", True)
                ):
                    _early_viol: list = []
                    if getattr(settings, "read_before_write_plan_mode", "enforce") == "enforce":
                        _early_viol += evidence_violations(
                            planned_writes=planned_writes, anchors=anchors,
                            ledger=ledger, agent_spec=agent_spec,
                        )
                    if getattr(settings, "required_reads_mode", "log") == "enforce":
                        _early_viol += required_lookup_violations(
                            planned_writes=planned_writes, anchors=anchors,
                            ledger=ledger, agent_spec=agent_spec,
                        )
                    if _early_viol:
                        _evidence_retry_used = True
                        logger.warning(
                            "[RUN %s] evidence gate would block — feeding the "
                            "violation back to the agent for ONE corrective "
                            "turn: %s", correlation_id, "; ".join(_early_viol),
                        )
                        timeline.append({
                            "step": "evidence_gate",
                            "status": "self_correction",
                            "detail": _early_viol,
                        })
                        messages.append({
                            "role": "assistant",
                            "content": assistant_msg.get("content") or "",
                        })
                        messages.append({
                            "role": "user",
                            "content": (
                                "STOP — your staged write was BLOCKED by the "
                                "evidence gate:\n- " + "\n- ".join(_early_viol)
                                + "\nCorrect this NOW, in this same turn "
                                "sequence: (1) call the required read tool(s) "
                                "to actually review the record/evidence named "
                                "above; (2) re-stage your write with the "
                                "action tool; (3) then give your final answer "
                                "with the audit block. Do not skip the reads "
                                "— an unreviewed write will be rejected."
                            ),
                        })
                        # The blocked staging is void — the corrective turns
                        # re-stage from scratch (plain lists, no dedupe keys).
                        planned_writes.clear()
                        write_events.clear()
                        continue
                logger.info(
                    "[RUN %s] iteration %d ← no tool calls; final text (%d chars)",
                    correlation_id,
                    iteration + 1,
                    len(assistant_msg.get("content") or ""),
                )
                break
            # Rule C-02 — token budget gate. Check AFTER accumulating this
            # round's usage. If we're over budget and the model still wants
            # more tool calls, we break here and let the forced-synthesis
            # turn below produce the final reply. We must not skip emitting
            # the budget event — the timeline is the audit trail.
            _used_tokens = int(usage_acc.get("total_tokens", 0))
            if _used_tokens >= _token_budget:
                logger.warning(
                    "[RUN %s] token budget exceeded at iteration %d "
                    "(used=%d limit=%d) — breaking tool loop to force "
                    "tool-free synthesis",
                    correlation_id, iteration + 1, _used_tokens, _token_budget,
                )
                timeline.append({
                    "step": "token_budget",
                    "status": "exceeded",
                    "limit": _token_budget,
                    "used": _used_tokens,
                })
                _budget_exceeded = True
                break
            logger.info(
                "[RUN %s] iteration %d ← %d tool call(s): %s",
                correlation_id,
                iteration + 1,
                len(tool_calls),
                ", ".join(tc.get("function", {}).get("name", "?") for tc in tool_calls),
            )
            # Append the assistant turn so the model can see what it asked for.
            messages.append(
                {
                    "role": "assistant",
                    "content": assistant_msg.get("content") or "",
                    "tool_calls": tool_calls,
                }
            )
            # Map tool name -> server so we can route to the right MCP.
            name_to_server = {
                t["function"]["name"]: t["function"].get("x_mcp_server")
                for t in tools
            }
            import json as _json

            for tc in tool_calls:
                fn = tc.get("function", {})
                tname = fn.get("name", "")
                try:
                    targs = (
                        _json.loads(fn.get("arguments") or "{}")
                        if isinstance(fn.get("arguments"), str)
                        else (fn.get("arguments") or {})
                    )
                except _json.JSONDecodeError:
                    logger.warning(
                        "[RUN %s] tool %s: could not JSON-parse arguments %r",
                        correlation_id, tname, fn.get("arguments"),
                    )
                    targs = {}
                logger.info(
                    "[RUN %s] dispatch %s args=%s",
                    correlation_id, tname, _json.dumps(targs, default=str)[:300],
                )
                server = name_to_server.get(tname) or ""
                # EFFICIENCY (#5) — serve an identical READ from the within-run
                # cache instead of re-dispatching it (the model re-asks the same
                # record across turns despite the EFFICIENCY prompt). Writes
                # (perform_action / mcp_action) are NEVER cached — every write is
                # intentional and must actually run.
                _is_write_tool = (tname == ACTION_TOOL_NAME) or (tname in _write_tool_names)
                try:
                    _cache_key = (
                        f"{tname}|{_json.dumps(targs, sort_keys=True, default=str)}"
                        if not _is_write_tool else None
                    )
                except Exception:  # noqa: BLE001 — unserialisable args ⇒ skip caching
                    _cache_key = None
                _cached = _tool_result_cache.get(_cache_key) if _cache_key is not None else None
                # READ-BEFORE-WRITE (Part B — auto-process). The plan_only path is
                # guarded post-loop (nothing commits until /approve). The
                # auto-process path (plan_only=False) commits this write LIVE, so
                # enforcement must happen BEFORE dispatch. Dark-launch "log"
                # observes only; "enforce" refuses the write and tells the model to
                # read first (self-correct within the iteration budget).
                _entry_pw = tools_v2_dispatch_table.get(tname)
                _is_write_dispatch = _is_write_tool or (
                    isinstance(_entry_pw, dict) and _entry_pw.get("kind") == "mcp_action"
                )
                _rbw_mode = getattr(settings, "read_before_write_autoprocess_mode", "log")
                _lookup_mode = getattr(settings, "required_reads_mode", "log")
                _blocked_write = False
                _block_viol: list[str] = []
                if (
                    _is_write_dispatch and not plan_only
                    and getattr(settings, "enforce_read_before_write", True)
                    and (_rbw_mode in ("log", "enforce")
                         or _lookup_mode in ("log", "enforce"))
                ):
                    # Record/media dimension + required data-lookup dimension, each
                    # behind its OWN rollout mode (mirrors the plan-mode gate).
                    _rm_viol = evidence_violations(
                        planned_writes=[{"tool": tname}],
                        anchors=anchors, ledger=ledger, agent_spec=agent_spec,
                    ) if _rbw_mode in ("log", "enforce") else []
                    _lk_viol = required_lookup_violations(
                        planned_writes=[{"tool": tname}],
                        anchors=anchors, ledger=ledger, agent_spec=agent_spec,
                    ) if _lookup_mode in ("log", "enforce") else []
                    _block_viol = (
                        (_rm_viol if _rbw_mode == "enforce" else [])
                        + (_lk_viol if _lookup_mode == "enforce" else [])
                    )
                    _wb_viol = (
                        (_rm_viol if _rbw_mode != "enforce" else [])
                        + (_lk_viol if _lookup_mode != "enforce" else [])
                    )
                    if _block_viol:
                        _blocked_write = True
                        logger.error(
                            "[RUN %s] auto-process write %s BLOCKED "
                            "(read-before-write) — %s",
                            correlation_id, tname, "; ".join(_block_viol),
                        )
                        timeline.append({
                            "step": "evidence_gate_autoprocess",
                            "status": "blocked", "tool": tname, "detail": _block_viol,
                        })
                    if _wb_viol:  # dark-launch: observe, do not block
                        logger.warning(
                            "[RUN %s] auto-process write %s WOULD be blocked "
                            "(read-before-write, dark-launch log-only) — %s",
                            correlation_id, tname, "; ".join(_wb_viol),
                        )
                        timeline.append({
                            "step": "evidence_gate_autoprocess",
                            "status": "would_block", "tool": tname, "detail": _wb_viol,
                        })
                if _blocked_write:
                    # Refuse the write; feed the reason back so the model reads the
                    # record (and any required media / lookup) first, then retries.
                    tool_result = {"error":
                        "READ_BEFORE_WRITE: " + "; ".join(_block_viol) + " — you MUST "
                        "read the record this decision is about (and any required "
                        "image/doc/lookup) BEFORE writing. Do the read now, then "
                        "retry this write."}
                    timeline.append(
                        {"step": "tool_call", "status": "blocked", "tool": tname}
                    )
                elif _cached is not None:
                    tool_result = _cached
                    logger.info(
                        "[RUN %s] tool %s ← within-run cache hit (deduped re-query; "
                        "skipped re-dispatch)", correlation_id, tname,
                    )
                    timeline.append(
                        {"step": "tool_call_deduped", "status": "ok", "tool": tname}
                    )
                elif tname == QUERY_TOOL_NAME:
                    tool_result = await dispatch_query_dataset(
                        settings=settings,
                        auth_header=auth_header,
                        tenant_id=tenant_id,
                        action=action,
                        args=targs if isinstance(targs, dict) else {},
                        app_spec=app_spec,
                    )
                    timeline.append(
                        {
                            "step": "data_read",
                            "status": "error" if tool_result.get("error") else "ok",
                            "dataset_id": (targs or {}).get("dataset_id"),
                        }
                    )
                elif tname == ACTION_TOOL_NAME:
                    tool_result = await dispatch_perform_action(
                        settings=settings,
                        auth_header=auth_header,
                        tenant_id=tenant_id,
                        action=action,
                        args=targs if isinstance(targs, dict) else {},
                        app_spec=app_spec,
                        plan_only=plan_only,
                    )
                    _wstatus = "error" if (
                        isinstance(tool_result, dict) and tool_result.get("error")
                    ) else "ok"
                    timeline.append(
                        {
                            "step": "data_write_plan" if plan_only else "data_write",
                            "status": _wstatus,
                            "dataset_id": (targs or {}).get("dataset_id"),
                            "action_id": (targs or {}).get("action_id"),
                        }
                    )
                    write_events.append(
                        _build_write_event(
                            tool=tname,
                            kind="perform_action_plan" if plan_only else "perform_action",
                            args=targs if isinstance(targs, dict) else {},
                            result=tool_result,
                            status=_wstatus,
                        )
                    )
                    # Capture the intent for /approve replay. We trust
                    # the LLM's payload because the MCP's dry-run path
                    # already ran the full preflight (write-authz +
                    # input_schema validation) before returning ok.
                    if plan_only and _wstatus == "ok" and isinstance(targs, dict):
                        # source_id comes from the catalogue binding the
                        # dispatch already resolved — surfaced as _source_id
                        # so /approve can route the apply to the same
                        # dept-MCP without re-resolving.
                        _src = (
                            tool_result.get("_source_id")
                            if isinstance(tool_result, dict)
                            else None
                        )
                        planned_writes.append({
                            "tool": tname,
                            "kind": "perform_action",
                            "source_id": _src,
                            "dataset_id": targs.get("dataset_id"),
                            "action_id": targs.get("action_id"),
                            "payload": targs.get("payload") or {},
                            "idempotency_key": targs.get("idempotency_key"),
                            "mcp_result": tool_result if isinstance(tool_result, dict) else {},
                            # the agent's self-reported confidence for this write
                            # (auto-process `confidence_min` gates on result.confidence)
                            "_result": {"confidence": (targs or {}).get("confidence")},
                        })
                elif tname == _DELEGATE_TOOL_NAME:
                    sub_id = (
                        targs.get("sub_agent_id")
                        if isinstance(targs, dict)
                        else None
                    )
                    sub = (
                        _resolve_sub_agent(agent_spec, sub_id)
                        if sub_id
                        else None
                    )
                    if sub is None:
                        tool_result = {"error": f"unknown sub_agent '{sub_id}'"}
                    else:
                        tool_result = await _execute_sub_agent(
                            settings=settings,
                            agent_spec=agent_spec,
                            sub_agent=sub,
                            task=str(targs.get("task") or ""),
                            context=targs.get("context") or {},
                            auth_header=auth_header,
                            depth=0,
                            tenant_id=tenant_id,
                        )
                    timeline.append(
                        {
                            "step": "delegate",
                            "status": "error" if tool_result.get("error") else "ok",
                            "sub_agent_id": sub_id,
                        }
                    )
                elif tname in tools_v2_dispatch_table:
                    entry = tools_v2_dispatch_table[tname]
                    tool_result = await dispatch_tools_v2_call(
                        settings=settings,
                        agent_spec=agent_spec,
                        app_spec=app_spec,
                        dispatch_table=tools_v2_dispatch_table,
                        tool_name=tname,
                        arguments=targs if isinstance(targs, dict) else {},
                        auth_header=auth_header,
                        plan_only=plan_only,
                        # The record's facets, computed once for this run. An
                        # image/document tool cannot derive its own scope (the
                        # model names the subject only AFTER looking), so the
                        # case context is the only thing its clauses can route
                        # on — inherit it rather than fire every clause in the
                        # bucket.
                        case_facets=_case_facets,
                    )
                    _tstatus = "error" if (
                        isinstance(tool_result, dict) and tool_result.get("error")
                    ) else "ok"
                    _kind = entry.get("kind")
                    # image_analyze / doc_extract / check_evaluate / fraud_synthesis
                    # return a structured ItemFinding — collect it so the run
                    # surfaces per-item review to the officer (per-image, per-doc,
                    # per-API-check e.g. CIBIL/Aadhaar, or the case-level fraud
                    # screening for Confirm/Dismiss → L2 rubric learning).
                    if (
                        _kind in ("image_analyze", "doc_extract", "check_evaluate",
                                  "fraud_synthesis")
                        and isinstance(tool_result, dict)
                        and tool_result.get("item_id")
                        and not tool_result.get("error")
                    ):
                        item_findings.append(
                            {
                                k: tool_result.get(k)
                                for k in (
                                    "item_id", "item_type", "modality", "fields",
                                    "recommendation", "confidence", "rationale",
                                    "citations", "rubric_version", "subject",
                                    # Fraud evidence (duplicate / near-dup /
                                    # metadata anomalies) MUST reach the
                                    # officer's per-item review payload
                                    # structurally — not only via agent prose.
                                    "artifact_flags",
                                    # Artifact identity — feeds the per-item
                                    # knowledge ledger (item_records.py):
                                    # exact-reuse precedents survive re-uploads.
                                    "content_sha256", "media_ref",
                                    # "Memory fired" counts — how many precedents
                                    # actually grounded THIS analysis (metrics).
                                    "precedents_used",
                                    # Factor scoring (docs/factor-scorecard-plan.md).
                                    # This projection is an ALLOWLIST: a key absent
                                    # here is silently dropped between the tool and
                                    # build_scorecard, which then reports the
                                    # finding as carrying no score — pointing at
                                    # the evaluator when the loss happened right
                                    # here. Every field the scorecard reads must be
                                    # listed.
                                    "factor_id", "score", "band", "clauses_fired",
                                    "sop_fingerprint",
                                )
                            }
                        )
                    # mcp_action tools are catalogue-pinned writes — record
                    # them in the timeline and write_events so the audit row
                    # carries the full intent + outcome of each LLM-issued
                    # write, not just a 240-char digest.
                    if _kind == "mcp_action":
                        timeline.append(
                            {
                                "step": "data_write_plan" if plan_only else "data_write",
                                "status": _tstatus,
                                "dataset_id": entry.get("dataset_id"),
                                "action_id": entry.get("action_id"),
                                "tool": tname,
                            }
                        )
                        write_events.append(
                            _build_write_event(
                                tool=tname,
                                kind="mcp_action_plan" if plan_only else "mcp_action",
                                args=targs if isinstance(targs, dict) else {},
                                result=tool_result,
                                status=_tstatus,
                                dataset_id=entry.get("dataset_id"),
                                action_id=entry.get("action_id"),
                                source_id=entry.get("source_id"),
                            )
                        )
                        # Capture the intent for /approve replay. The
                        # catalogue entry (source_id / dataset_id /
                        # action_id) is the safety boundary — replay
                        # cannot reach a different action than the LLM
                        # proposed because /approve uses these stored
                        # fields verbatim.
                        # _tstatus gates this, which is why the membership-vs-
                        # truthiness bug above mattered: an envelope carrying
                        # `error: None` scored as an error, so a write the model
                        # proposed would have been dropped from the plan — the
                        # officer would see a recommendation with nothing to
                        # Apply and no error explaining why.
                        if plan_only and _tstatus == "ok" and isinstance(targs, dict):
                            _payload = (
                                targs.get("payload")
                                if isinstance(targs.get("payload"), dict)
                                else targs
                            )
                            planned_writes.append({
                                "tool": tname,
                                "kind": "mcp_action",
                                "source_id": entry.get("source_id"),
                                "dataset_id": entry.get("dataset_id"),
                                "action_id": entry.get("action_id"),
                                "payload": _payload or {},
                                "idempotency_key": targs.get("idempotency_key"),
                                "mcp_result": tool_result if isinstance(tool_result, dict) else {},
                                "editable_fields": _editable_fields_for(
                                    agent_spec,
                                    entry.get("source_id"),
                                    entry.get("dataset_id"),
                                    entry.get("action_id"),
                                ),
                            })
                    else:
                        timeline.append(
                            {
                                "step": "tool_call",
                                "status": _tstatus,
                                "tool": tname,
                                "kind": _kind,
                            }
                        )
                elif not server:
                    tool_result = {"error": f"unknown tool '{tname}'"}
                    timeline.append(
                        {
                            "step": "tool_call",
                            "status": "error",
                            "tool": tname,
                        }
                    )
                else:
                    tool_result = await _dispatch_mcp_tool(
                        settings=settings,
                        server=server,
                        tool_name=tname,
                        arguments=targs,
                        auth_header=auth_header,
                    )
                    timeline.append(
                        {
                            "step": "tool_call",
                            "status": "error" if tool_result.get("error") else "ok",
                            "tool": tname,
                            "server": server,
                        }
                    )
                # Cache a fresh, successful READ for within-run dedup (#5).
                if (
                    _cache_key is not None and _cached is None
                    and not (isinstance(tool_result, dict) and tool_result.get("error"))
                ):
                    _tool_result_cache[_cache_key] = tool_result
                # READ-BEFORE-WRITE ledger (Part B accumulation). Note every
                # successful READ so the write-guard can prove the anchor record
                # + required media were actually seen. Writes are EXCLUDED — a
                # write's own payload carries the id, so counting it would let an
                # unread write self-satisfy the guard.
                if not (isinstance(tool_result, dict) and tool_result.get("error")):
                    _entry_kb = tools_v2_dispatch_table.get(tname)
                    _is_write_kb = _is_write_tool or (
                        isinstance(_entry_kb, dict)
                        and _entry_kb.get("kind") == "mcp_action"
                    )
                    if not _is_write_kb:
                        if (
                            isinstance(_entry_kb, dict)
                            and _entry_kb.get("kind") in ("image_analyze", "doc_extract")
                        ):
                            ledger.note_media_read(
                                tool_name=tname,
                                record_id=(targs or {}).get("record_id")
                                if isinstance(targs, dict) else None,
                            )
                        else:
                            ledger.note_record_read(
                                args=targs if isinstance(targs, dict) else None,
                                rows=_rows_from_tool_result(tool_result),
                            )
                            # Also record that this tool RAN, so the required-
                            # lookup gate can confirm a mandatory check (bureau/
                            # KYC) executed for this case.
                            ledger.note_lookup_read(tool_name=tname)
                # Result summary for the console — error verbatim, else row-count.
                if isinstance(tool_result, dict) and tool_result.get("error"):
                    logger.warning(
                        "[RUN %s] tool %s ← ERROR: %s",
                        correlation_id, tname, str(tool_result.get("error"))[:300],
                    )
                else:
                    if isinstance(tool_result, dict):
                        _rows = tool_result.get("rows")
                        _n = len(_rows) if isinstance(_rows, list) else "n/a"
                    elif isinstance(tool_result, list):
                        _n = len(tool_result)
                    else:
                        _n = "n/a"
                    logger.info(
                        "[RUN %s] tool %s ← ok (rows=%s)", correlation_id, tname, _n
                    )
                tool_refs.append(
                    {
                        "tool": tname,
                        "status": (
                            "error"
                            if isinstance(tool_result, dict)
                            and tool_result.get("error")
                            else "ok"
                        ),
                        "digest": _digest_tool_result(tool_result),
                    }
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.get("id", ""),
                        "name": tname,
                        "content": _json.dumps(_strip_internal_keys(tool_result))[:8000],
                    }
                )
        else:
            # Exhausted iterations without a final assistant message.
            timeline.append(
                {
                    "step": "tool_loop",
                    "status": "error",
                    "detail": f"exceeded {_MAX_TOOL_ITERATIONS} iterations",
                }
            )

        # Rule C-02 — forced synthesis on budget exit. When the tool loop
        # broke because we crossed SMART_APP_RUN_MAX_TOKENS, the last
        # assistant message still carries tool_calls (no final text). One
        # tool-free LLM call forces the model to compose a coherent reply
        # from the tool results it already has, instead of leaving the
        # caller with nothing. Token cost of this turn is accumulated too.
        if _budget_exceeded:
            try:
                logger.info(
                    "[RUN %s] forcing tool-free synthesis turn after token "
                    "budget exit", correlation_id,
                )
                final = await _call_llm(
                    settings=settings,
                    messages=messages,
                    tier=requested_tier,
                    tools=None,
                    tenant_id=tenant_id or None,
                    surface="agent_run",
                )
                _accumulate_usage(usage_acc, final)
                if final.get("_model"):
                    model_used = final["_model"]
                if (final.get("content") or "").strip():
                    assistant_msg = final
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "[RUN %s] forced synthesis after budget exit failed: %s",
                    correlation_id, exc,
                )

        assistant_text = assistant_msg.get("content") or ""
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        logger.exception("inference call failed for action=%s", action.name)
        timeline.append({"step": "llm_call", "status": "error", "detail": str(e)})
        return RunResponse(
            correlation_id=correlation_id,
            status="failed",
            outputs={},
            timeline=timeline,
            error=str(e),
            references=run_references,
            write_events=write_events,
            item_findings=item_findings,
            usage=usage_acc,
            model=model_used,
            trace_id=trace_id,
        )

    timeline.append(
        {
            "step": "llm_call",
            "status": "ok",
            "model_tier": requested_tier,
            "delegates_to": action.delegates_to,
        }
    )

    # Split the structured audit block out of the reply: ``human_text``
    # is what the UI renders; ``decision`` / ``reasoning`` / ``citations``
    # are persisted to the audit trail. ``references`` is what the runtime
    # actually retrieved — kept separate so it can be cross-checked
    # against the LLM's self-reported citations.
    (human_text, decision, reasoning, citations, cited_precedents,
     cited_clauses) = _extract_audit_block(assistant_text)
    # A clause receipt is only meaningful for a clause the model was actually
    # SHOWN. The audit block is parsed from model text, so an id it invents was
    # being accepted verbatim, stamped relation="applied", and carried to the
    # officer's screen, the DecisionRecord and the correction ledger as though a
    # team judgement had been applied. Observed: a run with NOTHING injected
    # cited "C-034", which has never existed in this tenant — fabricated
    # provenance under the one mechanism whose entire purpose is to show an
    # officer where a recommendation came from.
    #
    # Consolidation already intersects citations with injected ids when
    # apportioning blame, so the LEARNING was never corrupted. The receipt was.
    if cited_clauses:
        _allowed = set(_injected_clause_ids or [])
        _kept = [c for c in cited_clauses if c.get("clause_id") in _allowed]
        if len(_kept) != len(cited_clauses):
            _bogus = [c.get("clause_id") for c in cited_clauses
                      if c.get("clause_id") not in _allowed]
            logger.error(
                "[RUN %s] model cited clause(s) it was never shown: %s "
                "(injected: %s) — dropped from the receipt",
                correlation_id, _bogus, sorted(_allowed) or "none",
            )
            timeline.append({"step": "clause_citation_guard", "status": "dropped",
                             "detail": _bogus})
        cited_clauses = _kept

    # Join the model's receipt (clause_id / relation / note) to what the clause
    # actually SAYS and who stands behind it. The receipt alone is a bare id, so
    # the officer's "what your team has taught" block rendered an empty line —
    # the attribution this feature exists to show. Enrichment only: an id whose
    # metadata is missing keeps its receipt and simply shows no prose.
    for _c in cited_clauses:
        _m = (_clause_meta or {}).get(_c.get("clause_id"))
        if not _m:
            continue
        _c.setdefault("text", _m.get("text"))
        _c.setdefault("support_count", _m.get("support_count"))
        _c.setdefault("support_officers", _m.get("support_officers") or [])
        _c.setdefault("status", _m.get("status"))

    # "What your team has taught" must list what FIRED, not only what the model
    # chose to name. A judgement that was put in front of the agent and left
    # unused is exactly what an officer needs to see — it is the case where the
    # agent ignored the team — so injected-but-uncited clauses are carried with
    # no relation, which the UI renders as "available, not cited".
    _named = {c.get("clause_id") for c in cited_clauses}
    for _cid in (_injected_clause_ids or []):
        if _cid in _named:
            continue
        _m = (_clause_meta or {}).get(_cid) or {}
        cited_clauses.append({
            "clause_id": _cid,
            "text": _m.get("text"),
            "support_count": _m.get("support_count"),
            "support_officers": _m.get("support_officers") or [],
            "status": _m.get("status"),
        })
    # Plan-then-apply: if the run was plan-only AND the LLM actually
    # proposed at least one write, hand the captured intents back to the
    # UI as pending_approval. The /approve endpoint will replay these
    # against /execute_action with dry_run=False — no second LLM pass.
    # A plan-only run with zero proposed writes collapses to "completed"
    # (nothing to confirm; the LLM just produced text).
    if plan_only and planned_writes:
        # READ-BEFORE-WRITE guard (Part B, plan mode). A write may not be STAGED
        # unless the agent actually read the anchor record + every bound-and-
        # required media tool for the record under review. "enforce" fails the
        # run (RULE #1); "log" dark-launches (observe would_block, still stage);
        # "off" skips. Gated by the master ENFORCE_READ_BEFORE_WRITE flag.
        _plan_mode = getattr(settings, "read_before_write_plan_mode", "enforce")
        _lookup_mode = getattr(settings, "required_reads_mode", "log")
        if getattr(settings, "enforce_read_before_write", True) and (
            _plan_mode != "off" or _lookup_mode != "off"
        ):
            # Two independent dimensions, each behind its OWN rollout mode so
            # they dark-launch separately: the record/media gate (_plan_mode) and
            # the required data-lookup gate (_lookup_mode). A violation blocks the
            # run only when its dimension is in "enforce"; otherwise it is
            # recorded as would_block and the write still stages.
            _plan_viol = evidence_violations(
                planned_writes=planned_writes,
                anchors=anchors,
                ledger=ledger,
                agent_spec=agent_spec,
            ) if _plan_mode != "off" else []
            _lookup_viol = required_lookup_violations(
                planned_writes=planned_writes,
                anchors=anchors,
                ledger=ledger,
                agent_spec=agent_spec,
            ) if _lookup_mode != "off" else []
            _blocking = (
                (_plan_viol if _plan_mode == "enforce" else [])
                + (_lookup_viol if _lookup_mode == "enforce" else [])
            )
            _would_block = (
                (_plan_viol if _plan_mode != "enforce" else [])
                + (_lookup_viol if _lookup_mode != "enforce" else [])
            )
            if _blocking:
                logger.error(
                    "[RUN %s] evidence gate blocked staging — %s",
                    correlation_id, "; ".join(_blocking),
                )
                timeline.append({
                    "step": "evidence_gate",
                    "status": "blocked",
                    "detail": _blocking,
                })
                return RunResponse(
                    correlation_id=correlation_id,
                    status="failed",
                    outputs={},
                    timeline=timeline,
                    error=(
                        "the agent staged a decision without reviewing the "
                        "evidence it is about: " + "; ".join(_blocking)
                    ),
                    references=run_references,
                    write_events=write_events,
                    item_findings=item_findings,
                    usage=usage_acc,
                    model=model_used,
                    trace_id=trace_id,
                )
            if _would_block:  # dark-launch: observe, still stage
                logger.warning(
                    "[RUN %s] evidence gate WOULD block staging "
                    "(dark-launch log-only) — %s",
                    correlation_id, "; ".join(_would_block),
                )
                timeline.append({
                    "step": "evidence_gate",
                    "status": "would_block",
                    "detail": _would_block,
                })
        timeline.append({
            "step": "plan_then_apply",
            "status": "pending_approval",
            "planned_writes": len(planned_writes),
        })
        return RunResponse(
            correlation_id=correlation_id,
            status="pending_approval",
            outputs={"text": human_text},
            timeline=timeline,
            decision=decision,
            reasoning=reasoning,
            citations=citations,
            cited_precedents=cited_precedents,
            cited_clauses=cited_clauses,
            references=run_references,
            write_events=write_events,
            item_findings=item_findings,
            scorecard=_scorecard(),
            planned_writes=planned_writes,
            usage=usage_acc,
            model=model_used,
            trace_id=trace_id,
        )
    # Fail loud on a silent no-op (RULE #1). The tool loop ended with an empty
    # final turn AND the model neither produced a verdict nor proposed a write.
    # The usual cause is a TRUNCATED final synthesis turn (finish_reason ==
    # "length"): the reasoning model spent its whole output budget thinking and
    # emitted 0 chars (prod run 9d3409a1…, 2026-06-22). Returning a hollow
    # "completed" here tells the officer the action succeeded when the agent
    # actually failed — surface it as a failure with the reason instead.
    if not (human_text or "").strip() and decision is None and not planned_writes:
        _final_finish = assistant_msg.get("_finish_reason")
        _truncated = _final_finish == "length"
        _err = (
            "the agent produced no answer: its final turn was truncated at "
            f"max_tokens ({settings.llm_agent_max_tokens}) before it could emit "
            "a verdict — raise LLM_AGENT_MAX_TOKENS"
            if _truncated
            else "the agent produced no answer (empty final turn — no verdict, "
            "no proposed action)"
        )
        logger.error("[RUN %s] silent no-op — %s", correlation_id, _err)
        timeline.append(
            {
                "step": "empty_result",
                "status": "error",
                "finish_reason": _final_finish,
                "detail": _err,
            }
        )
        return RunResponse(
            correlation_id=correlation_id,
            status="failed",
            outputs={},
            timeline=timeline,
            error=_err,
            references=run_references,
            write_events=write_events,
            item_findings=item_findings,
            usage=usage_acc,
            model=model_used,
            trace_id=trace_id,
        )
    return RunResponse(
        correlation_id=correlation_id,
        status="completed",
        outputs={"text": human_text},
        timeline=timeline,
        decision=decision,
        reasoning=reasoning,
        citations=citations,
        cited_precedents=cited_precedents,
        cited_clauses=cited_clauses,
        references=run_references,
        write_events=write_events,
        # A recommend-only run (image/doc analysis with NO mcp_action write) ends
        # here with status="completed" — it MUST still surface its per-item
        # findings for officer review, or a no-write multimodal app shows nothing.
        item_findings=item_findings,
        scorecard=_scorecard(),
        usage=usage_acc,
        model=model_used,
        trace_id=trace_id,
    )
