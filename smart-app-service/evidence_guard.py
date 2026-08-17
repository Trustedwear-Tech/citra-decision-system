# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""Read-before-write evidence guard for the /run decision loop.

The runtime NEVER hydrates the record an action is about — the caller sends only
an id (e.g. ``{"complaint_id": "..."}``), which is dumped verbatim into the LLM
prompt (``runtime._build_messages``). Whether the agent actually READS that
record — and, for a multi-modal app, whether it actually looks at the bound
photo / document — is left to the model's discretion. A model that skips the
read and pattern-matches the write on the id alone produces a plausible but
UNGROUNDED decision, and nothing hard-fails. That is a silent-hallucination
hole in a governed decision loop.

This module makes the guarantee explicit and LOUD (platform rule: fail loud,
fail early — never a silent default):

    A write may not be STAGED unless the agent has actually read the EVIDENCE
    the decision is about — the anchor record, plus every bound-and-required
    media tool (image_analyze / doc_extract) — all keyed to the caller-supplied
    id.

Two collaborating pieces, both driven from this module:

  * ``ReadLedger`` — accumulated during the tool loop. Every read (query_dataset,
    tools_v2 mcp read, image_analyze, doc_extract) is noted here with the values
    it filtered on / returned and, for media, the ``record_id`` it reviewed.

  * ``assert_reads_cover_writes`` — called at plan finalisation, BEFORE the
    ``pending_approval`` response with ``planned_writes`` is returned. Raises
    ``EvidenceNotReviewed`` (caught by ``execute_run`` → run ends ``failed``,
    zero planned_writes leaked) when the ledger does not prove the evidence was
    seen.

Everything here is pure and side-effect free so it can be unit-tested in
isolation from the runtime, the LLM, and the MCP.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Set

# Cap how many returned rows we scan per read when harvesting id-like values, so
# a broad read that returns thousands of rows can't blow up memory / latency in
# the guard. The anchor id, when present in a result set, is overwhelmingly in
# the first rows (exact-filter lookups return 1; list reads are id-ordered).
_MAX_ROWS_SCANNED_PER_READ = 500
# Only short scalar strings can be record ids; skip long free-text / blobs.
_MAX_ID_VALUE_LEN = 128


class EvidenceNotReviewed(Exception):
    """Raised when a write is staged without the required evidence being read.

    ``details`` enumerates each unmet requirement so the run's timeline / the
    caller's error payload names exactly what was missing (which anchor record
    was never read, which required media tool was never called).
    """

    def __init__(self, details: List[str]):
        self.details = details
        super().__init__("; ".join(details))


@dataclass(frozen=True)
class Anchor:
    """A caller-supplied id the decision is about (a required string input)."""

    field: str
    value: str


@dataclass
class ReadLedger:
    """Records what the agent actually read during one run.

    Not thread-safe by design — one ledger per run, mutated only from the
    single tool-dispatch loop.
    """

    # Every id-like scalar the agent filtered on OR that appeared in a returned
    # row. ``record_covers`` checks anchors against this set.
    seen_values: Set[str] = field(default_factory=set)
    # (media tool name, record_id) pairs — one per image_analyze / doc_extract
    # dispatch. ``media_covers`` checks required media tools against these.
    media_calls: Set[tuple] = field(default_factory=set)
    # Names of bound mcp read tools that RAN successfully this run. The
    # required-lookup gate confirms a policy-mandated check (bureau / KYC /
    # sanctions) actually executed. One ledger per run (one case), so a
    # successful run of a required tool means the check was performed for THIS
    # record — no id match needed (and it would be circular: the same read
    # feeds ``seen_values`` too).
    lookup_tools_ran: Set[str] = field(default_factory=set)

    # ── accumulation (called from the runtime tool loop) ────────────────────
    def note_record_read(
        self,
        *,
        args: Optional[Dict[str, Any]] = None,
        rows: Optional[Iterable[Dict[str, Any]]] = None,
    ) -> None:
        """Note a structured read (query_dataset / tools_v2 mcp read).

        Harvests id-like values from BOTH the query arguments (what the agent
        filtered on — the deterministic exact-filter path) and the returned
        rows (what a broad / NL query surfaced), so either read style counts as
        having 'seen' the anchor.
        """
        if args:
            _harvest_scalars(args, self.seen_values)
        if rows:
            for i, row in enumerate(rows):
                if i >= _MAX_ROWS_SCANNED_PER_READ:
                    break
                if isinstance(row, dict):
                    _harvest_scalars(row, self.seen_values)

    def note_media_read(self, *, tool_name: str, record_id: Optional[str]) -> None:
        """Note an image_analyze / doc_extract dispatch and the id it reviewed."""
        rid = _norm(record_id)
        if tool_name and rid is not None:
            self.media_calls.add((tool_name, rid))
            # A media review also proves that record was 'seen' — so a record
            # whose ONLY read is its photo still satisfies the record check.
            self.seen_values.add(rid)

    def note_lookup_read(self, *, tool_name: str) -> None:
        """Record that a bound mcp read tool ran SUCCESSFULLY this run.

        Called from the tool loop only on a non-error result, so a failed lookup
        is not counted (the mandatory check must have actually returned). The
        required-lookup gate then confirms every required tool is in this set.
        """
        if tool_name:
            self.lookup_tools_ran.add(tool_name)

    # ── queries (called from the guard) ─────────────────────────────────────
    def record_covers(self, value: str) -> bool:
        v = _norm(value)
        return v is not None and v in self.seen_values

    def media_covers(self, tool_name: str, anchor_values: Set[str]) -> bool:
        """True if ``tool_name`` was called with a record_id matching any anchor."""
        for name, rid in self.media_calls:
            if name == tool_name and rid in anchor_values:
                return True
        return False

    def lookup_ran(self, tool_name: str) -> bool:
        """True if ``tool_name`` ran successfully this run — i.e. the mandatory
        check was performed for this case (one ledger per run/record)."""
        return bool(tool_name) and tool_name in self.lookup_tools_ran


def resolve_anchor_ids(action: Any, inputs: Dict[str, Any]) -> List[Anchor]:
    """The caller-supplied ids the decision is anchored on.

    An anchor is a REQUIRED, string-typed property of the action's
    ``input_schema`` that is present in ``inputs`` with a non-empty scalar
    value. For ``route_complaint`` that is ``complaint_id``; for
    ``open_case_from_inspection`` it is ``inspection_id``. Non-id scalars a
    builder might mark required (a free-text note, a number) are NOT treated as
    record anchors — only string ids are.
    """
    schema = getattr(action, "input_schema", None) or {}
    required = schema.get("required") or []
    props = schema.get("properties") or {}
    anchors: List[Anchor] = []
    for name in required:
        spec = props.get(name) or {}
        if spec.get("type") not in (None, "string"):
            continue
        raw = (inputs or {}).get(name)
        val = _norm(raw)
        if val is not None:
            anchors.append(Anchor(field=name, value=val))
    return anchors


def required_media_tools(agent_spec: Any) -> List[Any]:
    """Bound media tools whose review is MANDATORY before a write.

    Per the agreed design: a media tool (image_analyze / doc_extract) that is
    BOUND to a record (``data_source_id`` set → server-side url resolution from
    a ``record_id``) is required-review by default. The builder opts a tool out
    of enforcement with ``required = False`` (e.g. 'only analyse the photo if
    the record already looks suspicious'). Unbound media tools (the caller
    passes a raw url / base64) are never enforced — there is no record_id to
    anchor them to.
    """
    out: List[Any] = []
    for t in (getattr(agent_spec, "tools_v2", None) or []):
        if getattr(t, "kind", None) not in ("image_analyze", "doc_extract"):
            continue
        if not getattr(t, "data_source_id", None):
            continue  # unbound → no record anchor → not enforceable
        if getattr(t, "required", True) is False:
            continue  # explicit opt-out
        out.append(t)
    return out


def required_lookup_tools(agent_spec: Any) -> List[Any]:
    """Bound mcp read tools whose execution is MANDATORY before a write.

    A ``kind == "mcp"`` tool marked ``required is True`` and BOUND to a dataset
    (``dataset_id`` set → the keyed ``filters`` path, so the id it ran for is
    captured). This is the data-lookup analogue of ``required_media_tools``: a
    policy-mandated check (bureau / KYC / sanctions) that must run for the case
    before the decision commits. Opt-IN (default off) — a plain read is never
    silently mandatory. Publish (W-09) rejects a required tool that is unbound,
    so an unbound one here is defensively skipped rather than unenforceable.
    """
    out: List[Any] = []
    for t in (getattr(agent_spec, "tools_v2", None) or []):
        if getattr(t, "kind", None) != "mcp":
            continue
        if getattr(t, "required", False) is not True:
            continue
        if not getattr(t, "dataset_id", None):
            continue  # unbound → no keyed id to anchor → not enforceable
        out.append(t)
    return out


def required_lookup_violations(
    *,
    planned_writes: List[Dict[str, Any]],
    anchors: List[Anchor],
    ledger: ReadLedger,
    agent_spec: Any,
) -> List[str]:
    """Unmet REQUIRED-LOOKUP requirements ('' → all met).

    A SEPARATE dimension from ``evidence_violations`` (record + media) so it can
    dark-launch behind its own flag. Same guard scope: nothing to check unless a
    write was staged AND the caller supplied a record id. Every required lookup
    must have RUN successfully this run (the mandatory check was performed).
    """
    if not planned_writes or not anchors:
        return []
    unmet: List[str] = []
    for tool in required_lookup_tools(agent_spec):
        tname = getattr(tool, "name", None) or getattr(tool, "tool_name", "?")
        if not ledger.lookup_ran(tname):
            ds = getattr(tool, "dataset_id", None) or "?"
            unmet.append(
                f"required lookup {tname!r} ({ds}) was never run for the record "
                f"under review — the agent staged a write without performing the "
                f"mandatory check"
            )
    return unmet


def evidence_violations(
    *,
    planned_writes: List[Dict[str, Any]],
    anchors: List[Anchor],
    ledger: ReadLedger,
    agent_spec: Any,
) -> List[str]:
    """Return the list of unmet read-before-write requirements ('' → all met).

    The non-raising core shared by the plan-mode gate (``assert_reads_cover_writes``)
    and the auto-process per-write check. Empty list when there is nothing to
    guard:
      * no planned_writes → no decision was staged / about to commit;
      * no anchors → the caller supplied no record id, so there is nothing to
        prove was read (a pure create-from-scratch action).

    Otherwise every anchor record must have been read, and every
    bound-and-required media tool must have been called for an anchor id.
    """
    if not planned_writes or not anchors:
        return []

    anchor_values = {a.value for a in anchors}
    unmet: List[str] = []

    for a in anchors:
        if not ledger.record_covers(a.value):
            unmet.append(
                f"anchor record {a.field}={a.value!r} was never read — the agent "
                f"staged a write without reading the record the decision is about"
            )

    for tool in required_media_tools(agent_spec):
        tname = getattr(tool, "name", None) or getattr(tool, "kind", "?")
        if not ledger.media_covers(tname, anchor_values):
            kind = getattr(tool, "kind", "media")
            unmet.append(
                f"required {kind} tool {tname!r} was never called for the record "
                f"under review — the agent staged a write without looking at the "
                f"bound evidence"
            )

    return unmet


def assert_reads_cover_writes(
    *,
    planned_writes: List[Dict[str, Any]],
    anchors: List[Anchor],
    ledger: ReadLedger,
    agent_spec: Any,
) -> None:
    """Raise ``EvidenceNotReviewed`` if a staged write is not backed by reads.

    Thin raising wrapper over ``evidence_violations`` — used by the plan-mode
    gate, where a violation ends the run before anything is offered to the
    officer."""
    unmet = evidence_violations(
        planned_writes=planned_writes, anchors=anchors,
        ledger=ledger, agent_spec=agent_spec,
    )
    if unmet:
        raise EvidenceNotReviewed(unmet)


# ── internals ───────────────────────────────────────────────────────────────
def _norm(value: Any) -> Optional[str]:
    """A value's canonical id form, or None if it can't be a record id."""
    if isinstance(value, bool):
        return None  # bools are not ids even though they're not str
    if isinstance(value, (int,)):
        value = str(value)
    if not isinstance(value, str):
        return None
    v = value.strip()
    if not v or len(v) > _MAX_ID_VALUE_LEN:
        return None
    return v


def _harvest_scalars(obj: Any, sink: Set[str], _depth: int = 0) -> None:
    """Add every id-like scalar reachable in ``obj`` to ``sink`` (bounded depth)."""
    if _depth > 6:
        return
    if isinstance(obj, dict):
        for v in obj.values():
            _harvest_scalars(v, sink, _depth + 1)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _harvest_scalars(v, sink, _depth + 1)
    else:
        s = _norm(obj)
        if s is not None:
            sink.add(s)
