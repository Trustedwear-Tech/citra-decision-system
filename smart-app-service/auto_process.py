"""Deterministic policy evaluation for AUTO-PROCESS triggers.

NO LLM, NO eval. Given a single proposed write + its decision context, decide
``commit`` vs ``recommend``. **Fails CLOSED**: any malformed policy, unknown
field, or evaluation error returns ``recommend`` (stage for a human) — never
``commit``. See docs/auto-process-plan.md.

Context shape (built by the caller, the trigger runner):
  ctx = {
    "payload": <the agent's proposed write payload dict>,
    "row":     <the source record that triggered the run, or {}>,
    "result":  <the agent's structured output, e.g. {"confidence": 0.9}, or {}>,
  }
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, Tuple

logger = logging.getLogger(__name__)

def _as_dict(obj: Any) -> Dict[str, Any]:
    """Accept a pydantic model or a dict; normalise to a dict with aliases
    (so the Condition ``not_`` field is keyed ``not``)."""
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "model_dump"):
        return obj.model_dump(by_alias=True)
    raise TypeError(f"expected dict or pydantic model, got {type(obj)}")


def _resolve(path: str, ctx: Dict[str, Any]) -> Any:
    """Resolve a dotted path ('payload.amount' / 'row.severity' / 'result.confidence')
    against ctx. Raises KeyError on an unknown field (caller fails closed)."""
    if not path or not isinstance(path, str):
        raise KeyError(f"invalid field path {path!r}")
    cur: Any = ctx
    for seg in path.split("."):
        if isinstance(cur, dict) and seg in cur:
            cur = cur[seg]
        else:
            raise KeyError(f"unknown field '{path}'")
    return cur


def _cmp(op: str, left: Any, right: Any) -> bool:
    if op == "==":
        return left == right
    if op == "!=":
        return left != right
    if op == "in":
        return left in right
    if op == "not_in":
        return left not in right
    if op == "matches":
        return re.search(str(right), str(left)) is not None
    if op == "between":
        lo, hi = right
        return float(lo) <= float(left) <= float(hi)
    if op in ("<", "<=", ">", ">="):
        l, r = float(left), float(right)
        return {"<": l < r, "<=": l <= r, ">": l > r, ">=": l >= r}[op]
    raise ValueError(f"unknown op {op!r}")


def evaluate_condition(cond: Any, ctx: Dict[str, Any]) -> bool:
    """Evaluate a Condition (model or dict) against ctx. Raises on malformed
    input / unknown field — the gate treats a raise as 'recommend' (fail closed)."""
    c = _as_dict(cond)
    if c.get("always") is not None:
        return bool(c["always"])      # unconditional gate (BA opened auto-process for ALL)
    if c.get("all") is not None:
        return all(evaluate_condition(x, ctx) for x in c["all"])
    if c.get("any") is not None:
        return any(evaluate_condition(x, ctx) for x in c["any"])
    if c.get("not") is not None:
        return not evaluate_condition(c["not"], ctx)
    # leaf
    field, op = c.get("field"), c.get("op")
    if not field or not op:
        raise ValueError("leaf condition needs both 'field' and 'op'")
    return _cmp(op, _resolve(field, ctx), c.get("value"))


def gate_planned_write(policy: Any, planned_write: Dict[str, Any], ctx: Dict[str, Any]) -> Tuple[str, str]:
    """Decide a single proposed write. Returns ('commit'|'recommend', reason).

    Fails CLOSED to 'recommend' on ANY error. Confidence below the floor,
    value over the cap, or the deterministic ``auto_commit_when`` not
    satisfied → 'recommend'."""
    try:
        p = _as_dict(policy)

        cmin = p.get("confidence_min")
        if cmin is not None:
            conf = (ctx.get("result") or {}).get("confidence")
            if conf is None or float(conf) < float(cmin):
                return "recommend", f"confidence {conf!r} below min {cmin}"

        vcap = p.get("value_cap")
        if vcap:
            v = _resolve(vcap["field"], ctx)
            if float(v) > float(vcap["max"]):
                return "recommend", f"{vcap['field']}={v} exceeds value_cap {vcap['max']}"

        cond = p.get("auto_commit_when")
        if cond is None:
            return "recommend", "policy has no auto_commit_when"
        if not evaluate_condition(cond, ctx):
            return "recommend", "auto_commit_when not satisfied"

        return "commit", "policy satisfied"
    except Exception as e:  # noqa: BLE001 — FAIL CLOSED: never auto-commit on an error
        logger.warning("[auto-process] policy eval error -> recommend (fail-closed): %s", e)
        return "recommend", f"policy eval error (fail-closed): {e}"


def build_ctx(planned_write: Dict[str, Any], *, row: Any = None, result: Any = None) -> Dict[str, Any]:
    """Assemble the evaluation context. ``payload`` = the proposed write; ``row``
    = the source record that triggered the run (the caller passes the trigger
    inputs, since a planned_write does NOT carry the row); ``result`` = the
    agent's structured output (e.g. ``confidence``) when the agent emits one.

    NOTE: if the agent does not emit a structured ``result``/confidence, any
    ``result.*`` rule resolves to an unknown field and the gate FAILS CLOSED
    (→ recommend). Confidence-gating therefore requires the agent to produce a
    confidence; otherwise rely on deterministic ``payload.*`` / ``row.*`` rules."""
    pw = planned_write or {}
    return {
        "payload": pw.get("payload") or {},
        "row": (row if row is not None else (pw.get("_row") or pw.get("row") or {})),
        "result": (result if result is not None else (pw.get("_result") or pw.get("result") or {})),
    }
