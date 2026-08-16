"""Publish-time derivation of ``Action.anchor_read`` from the catalogue.

`anchor_read` powers deterministic base-record hydration (runtime Part A). Rather
than make the builder author it (record/write bindings are deliberately NOT
LLM-authored), we DERIVE it at publish from the data-discovery catalogue — the
same place the dataset_directory is hydrated, where the full catalogue entry
(columns + is_primary_key + write_actions) is already fetched.

Key design point (a table may have NO primary key): the KEY FIELD is taken from
the ACTION'S REQUIRED ID INPUT — the declared, caller-facing way to identify the
record — NOT from a DB primary-key constraint. `is_primary_key` is used only to
CORROBORATE / disambiguate which dataset the id keys. So derivation works for
views, logs, and unconstrained tables that carry a logical key column.

Fail-loud (RULE #1) is scoped narrowly to the one genuinely unguardable case: an
action that MUTATES a keyed record (the catalogue write verb is update/delete/…
and the target has a primary-key column) whose key the caller does NOT supply and
for which no anchor could be derived. That write can never be guarded, so — in
``enforce`` mode — publish fails rather than shipping it silently. Everything else
(create-from-scratch, catalogue unreachable, no-PK tables) degrades to a logged
skip; the runtime read-before-write gate remains the backstop.
"""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set, Tuple

from models import AgentSpec, AnchorRead

logger = logging.getLogger(__name__)

# Catalogue write verbs that MUTATE an existing record (vs. create-from-scratch).
_MUTATE_VERBS = {"update", "upsert", "patch", "delete", "set", "modify", "replace"}
_STRUCTURED_KINDS = {"sql", "odata", "soql"}

# async (source_id, dataset_id) -> catalogue entry dict | None
FetchEntry = Callable[[str, str], Awaitable[Optional[Dict[str, Any]]]]


class AnchorDerivationError(Exception):
    """A mutate action operates on a keyed record we cannot anchor — publish must
    fail rather than ship an unguardable state-change."""


def _required_string_ids(action: Any) -> List[str]:
    schema = getattr(action, "input_schema", None) or {}
    props = schema.get("properties") or {}
    out: List[str] = []
    for name in (schema.get("required") or []):
        if (props.get(name) or {}).get("type") in (None, "string"):
            out.append(name)
    return out


def _read_candidates(action: Any, agent_spec: AgentSpec) -> List[Tuple[str, str]]:
    """(source_id, dataset_id) datasets the anchor could live in: the action's
    read bindings, its write targets (you read a record before mutating it), and
    the agent's tools_v2 mcp reads / mcp_action writes."""
    out: List[Tuple[str, str]] = []
    seen: Set[Tuple[str, str]] = set()

    def _add(src: Optional[str], ds: Optional[str]) -> None:
        if src and ds and (src, ds) not in seen:
            seen.add((src, ds))
            out.append((src, ds))

    db = getattr(action, "data_bindings", None)
    if db:
        for r in (db.reads or []):
            _add(r.source_id, r.dataset_id)
        for w in (db.writes or []):
            _add(w.source_id, w.dataset_id)
    for t in (getattr(agent_spec, "tools_v2", None) or []):
        if getattr(t, "kind", None) in ("mcp", "mcp_action"):
            _add(getattr(t, "source_id", None), getattr(t, "dataset_id", None))
    return out


def _write_targets(action: Any, agent_spec: AgentSpec) -> List[Tuple[str, str, Optional[str]]]:
    """(source_id, dataset_id, action_id) each write this action can issue."""
    out: List[Tuple[str, str, Optional[str]]] = []
    db = getattr(action, "data_bindings", None)
    if db:
        for w in (db.writes or []):
            out.append((w.source_id, w.dataset_id, getattr(w, "action_id", None)))
    for t in (getattr(agent_spec, "tools_v2", None) or []):
        if getattr(t, "kind", None) == "mcp_action":
            out.append((getattr(t, "source_id", None), getattr(t, "dataset_id", None),
                        getattr(t, "action_id", None)))
    return out


def _columns(entry: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [c for c in ((entry or {}).get("columns") or []) if isinstance(c, dict)]


def _write_verb(entry: Optional[Dict[str, Any]], action_id: Optional[str]) -> Optional[str]:
    for w in ((entry or {}).get("write_actions") or []):
        if isinstance(w, dict) and w.get("id") == action_id:
            return (w.get("verb") or "").strip().lower() or None
    return None


async def _derive_one(
    action: Any, agent_spec: AgentSpec, fetch_entry: FetchEntry, mode: str,
) -> Optional[str]:
    """Derive+set anchor_read for one action. Returns a warning string, or None.
    Raises AnchorDerivationError in enforce mode for an unguardable mutate."""
    if getattr(action, "anchor_read", None) is not None:
        return None  # respect explicit authoring
    ids = _required_string_ids(action)
    if not ids:
        return None  # no caller-supplied record id → nothing to anchor

    entry_cache: Dict[Tuple[str, str], Optional[Dict[str, Any]]] = {}

    async def _entry(src: str, ds: str) -> Optional[Dict[str, Any]]:
        key = (src, ds)
        if key not in entry_cache:
            try:
                entry_cache[key] = await fetch_entry(src, ds)
            except Exception as exc:  # noqa: BLE001 — treat as unreachable, log
                logger.warning("[anchor] catalogue fetch failed (%s,%s): %s", src, ds, exc)
                entry_cache[key] = None
        return entry_cache[key]

    # Match a required id input to a candidate dataset's column (PK preferred).
    best: Optional[AnchorRead] = None
    for src, ds in _read_candidates(action, agent_spec):
        entry = await _entry(src, ds)
        cols = _columns(entry)
        if not cols:
            continue
        kind = (entry or {}).get("kind")
        kind = kind if kind in _STRUCTURED_KINDS else "sql"
        pk_match = next((c for c in cols
                         if c.get("name") in ids and c.get("is_primary_key")), None)
        name_match = pk_match or next((c for c in cols if c.get("name") in ids), None)
        if name_match:
            best = AnchorRead(source_id=src, dataset_id=ds,
                              key_field=name_match["name"], kind=kind)
            if pk_match:
                break  # PK match is authoritative; stop looking
    if best is not None:
        action.anchor_read = best
        logger.info("[anchor] derived anchor_read for action %s → %s.%s(%s)",
                    getattr(action, "name", "?"), best.source_id, best.dataset_id,
                    best.key_field)
        return None

    # No anchor. Fail-loud ONLY for a definitively unguardable MUTATE: the write
    # target has a PK column the caller does not supply as an input.
    for src, ds, action_id in _write_targets(action, agent_spec):
        entry = await _entry(src, ds)
        if entry is None:
            continue  # catalogue unreachable → cannot judge; do not fail on an outage
        verb = _write_verb(entry, action_id)
        if verb is not None and verb not in _MUTATE_VERBS:
            continue  # a create-from-scratch write needs no anchor
        pk_cols = [c.get("name") for c in _columns(entry) if c.get("is_primary_key")]
        if pk_cols and not (set(pk_cols) & set(ids)):
            msg = (
                f"action {getattr(action, 'name', '?')!r} mutates keyed record(s) in "
                f"{ds} (pk={pk_cols}) but none of its required inputs {ids} supply that "
                f"key, and no anchor record could be derived — the read-before-write "
                f"guard cannot protect this write"
            )
            if mode == "enforce":
                raise AnchorDerivationError(msg)
            return msg
    return None


async def derive_anchor_reads(
    agent_spec: Optional[AgentSpec],
    *,
    fetch_entry: FetchEntry,
    mode: str = "enforce",
) -> List[str]:
    """Derive+set ``anchor_read`` on every action of ``agent_spec`` in place.

    ``mode``: 'enforce' (raise on an unguardable mutate), 'warn' (collect the
    reason as a warning), or 'off' (skip entirely). Returns the warning strings.
    """
    if mode == "off" or agent_spec is None or not agent_spec.actions:
        return []
    warnings: List[str] = []
    for action in agent_spec.actions:
        warn = await _derive_one(action, agent_spec, fetch_entry, mode)
        if warn:
            warnings.append(warn)
    return warnings
