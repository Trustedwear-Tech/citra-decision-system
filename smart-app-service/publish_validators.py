"""Publish-time validators that need to run BEFORE persistence.

Extracted from ``main.py`` so they can be unit-tested in isolation
without importing the FastAPI app and its full dependency chain.
"""

from __future__ import annotations

import logging
import math
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

from config import Settings
from discovery_cache import DiscoveryError, resolve_source
from env_context import current_env

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Layer B publish-validator constants (rule_id catalogue)
# ---------------------------------------------------------------------------
#
# Each constant below backs one of the W-/H-/L-/P-/T-/D-/C-/A-/S- rule
# validators further down. Hoisted to module scope so unit tests can patch
# them and so the rule list is grep-able in one place.

# W-01: write verbs that must never appear in a write action.
# Hard-delete is replaced by soft-delete (status='deleted') so the audit
# trail survives and a wrong call is reversible.
_DELETE_VERBS = frozenset({"delete"})

# W-02: identifier field names the engine accepts as the "which row do
# I update" key. At least one of these MUST be in input_schema.required
# for any verb that mutates an existing row.
_IDENTIFIER_FIELD_NAMES = frozenset({"id", "_id", "pk", "key"})
_IDENTIFIER_SUFFIX = "_id"
_MUTATING_VERBS = frozenset({"update", "upsert", "delete-soft"})

# D-02 (narrator mandatory): a dashboard page requires the app to declare a
# narrator agent (app_spec.agent_id) — the hero-brief copilot runs it in
# chat_mode (read-only enforced at runtime). An app may legitimately mix a
# dashboard page with action pages whose agent has write tools, so we do NOT
# reject write tools at publish. (D-01 — read-only data sources — is a
# separate rule in citra-safety-rules; this check is D-02 there.)

# S-01: the only audience values a SmartApp may carry. "public" is
# explicitly rejected — SmartApps are internal officer tools.
_INTERNAL_AUDIENCE_LITERALS = frozenset({"owner", "org"})
_INTERNAL_AUDIENCE_PREFIXES = ("team:", "dept:")

# S-03: secret-token prefixes scanned recursively across the publish
# payload. The high-entropy fallback catches rotating keys whose
# prefix we don't yet know.
_SECRET_PREFIXES = ("sk-", "AKIA", "ghp_", "hvs.", "xoxb-")
_SECRET_ENTROPY_THRESHOLD = 4.5
_SECRET_MIN_SEGMENT_LEN = 32


# Discovery error codes that mean "this source genuinely doesn't exist /
# is unusable for the BA's tenant" — block publish on these.
_BLOCKING_DISCOVERY_CODES = frozenset(
    {
        "source_not_found",
        "discovery_no_endpoint",
        "discovery_unauthorised",
    }
)


async def validate_tool_sources_resolvable(
    agent_spec,
    *,
    settings: Settings,
    auth_header: Optional[str],
) -> List[Dict[str, Any]]:
    """Resolve every (mcp / rag) tool's ``source_id`` against discovery.

    Returns a list of unresolved entries (empty when everything resolves).
    Each entry: ``{"tool_name", "kind", "source_id", "reason", "detail"}``.

    Best-effort: if discovery itself is unreachable (502/503), or no
    user JWT is present (service-to-service publish), or no discovery
    URL is configured, the check is skipped rather than blocking.
    Definitive ``source_not_found`` / equivalent errors are surfaced.
    """
    if agent_spec is None:
        return []

    user_jwt = _strip_bearer(auth_header)
    if not user_jwt:
        return []

    # Env-aware: a publish from the builder runs in "test", so tool resolution
    # validates against the TEST discovery plane (the same one the app will use
    # at test run-time). Empty → skip (fail-soft, advisory check).
    discovery_url = (settings.discovery_url_for(current_env()) or "").strip()
    if not discovery_url:
        return []

    seen: set = set()
    unresolved: List[Dict[str, Any]] = []

    for tool in (agent_spec.tools_v2 or []):
        kind = getattr(tool, "kind", None)
        if kind not in ("mcp", "rag"):
            continue
        source_id = getattr(tool, "source_id", None)
        if not source_id:
            continue
        # Dedup: same source_id touched by multiple tools resolves once.
        key = f"{kind}:{source_id}"
        if key in seen:
            continue
        seen.add(key)

        try:
            await resolve_source(
                discovery_url=discovery_url,
                user_jwt=user_jwt,
                source_id=source_id,
                cache_ttl_seconds=settings.discovery_cache_ttl_seconds,
            )
        except DiscoveryError as exc:
            if exc.code in _BLOCKING_DISCOVERY_CODES:
                unresolved.append({
                    "tool_name": getattr(tool, "name", "(unnamed)"),
                    "kind": kind,
                    "source_id": source_id,
                    "reason": exc.code,
                    "detail": str(exc),
                })
            else:
                logger.warning(
                    "discovery resolution failed for source_id=%s (%s) — "
                    "infrastructure issue, allowing publish",
                    source_id, exc.code,
                )
    return unresolved


def _strip_bearer(auth_header: Optional[str]) -> str:
    if not auth_header:
        return ""
    parts = auth_header.split(None, 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    return auth_header.strip()


# ===========================================================================
# Layer B publish validators (W-/H-/L-/P-/T-/D-/C-/A-/S- rule set)
# ===========================================================================
#
# Conventions for every function below:
#   * Pure function over the spec dicts/models — no I/O, no async.
#   * Returns ``List[Dict[str, Any]]``. Empty = pass. Non-empty = block.
#   * Each offender entry carries ``{"rule_id", "location", "reason"}``.
#   * The caller in ``main.publish_app`` raises HTTPException(422) with
#     ``detail={"code": rule_id, "message": ..., "errors": offenders}``.
#
# Order of registration matters for first-fail messaging — the wire-in
# in main.py runs these in declaration order.


def _iter_actions(agent_spec) -> Iterable[Any]:
    """Yield every Action on an AgentSpec, tolerating None / partial specs."""
    if agent_spec is None:
        return []
    return list(getattr(agent_spec, "actions", None) or [])


def _iter_tools_v2(agent_spec) -> Iterable[Any]:
    """Yield every tools_v2 entry on an AgentSpec."""
    if agent_spec is None:
        return []
    return list(getattr(agent_spec, "tools_v2", None) or [])


# ── W-01 ────────────────────────────────────────────────────────────────
def validate_no_delete_verbs(app_spec, agent_spec) -> List[Dict[str, Any]]:
    """Reject any write action whose verb is "delete".

    Hard deletes obliterate audit history and are not reversible from a
    SmartApp. The runtime expects sources to expose a soft-delete
    write_action (verb in {"delete-soft","update"} setting status="deleted")
    instead. This guard scans the agent's mcp_action tools (the catalogue verb
    is mirrored in the tool name / action_id).
    """
    out: List[Dict[str, Any]] = []
    for tool in _iter_tools_v2(agent_spec):
        if getattr(tool, "kind", None) != "mcp_action":
            continue
        verb = (getattr(tool, "action_id", "") or "").split(".")[-1].lower()
        # action_id is opaque; we conservatively also check the tool name.
        candidates = {verb, (getattr(tool, "name", "") or "").lower()}
        if candidates & _DELETE_VERBS:
            out.append({
                "rule_id": "W-01",
                "location": f"agent_spec.tools_v2[{getattr(tool, 'name', '?')}]",
                "reason": (
                    "verb='delete' is forbidden — replace with a soft-delete "
                    "write_action that sets status='deleted' on the row."
                ),
            })
    return out


# ── H-04 ────────────────────────────────────────────────────────────────
def reject_allow_writes_in_chat(agent_spec) -> List[Dict[str, Any]]:
    """Reject any AgentSpec whose hitl_policy carries ``allow_writes_in_chat``.

    Even a ``False`` value is rejected — the field's mere presence proves
    the BA's spec was templated from a draft that contemplated the switch.
    Forcing removal closes the door on a one-line edit later enabling it.
    """
    if agent_spec is None:
        return []
    policy = getattr(agent_spec, "hitl_policy", None) or {}
    if isinstance(policy, dict) and "allow_writes_in_chat" in policy:
        return [{
            "rule_id": "H-04",
            "location": "agent_spec.hitl_policy.allow_writes_in_chat",
            "reason": (
                "remove the `allow_writes_in_chat` field entirely. Chat "
                "surfaces are read-only by platform policy; the field "
                "must not exist in the spec (even =False) so a future "
                "edit can't silently flip it."
            ),
        }]
    return []


# ── W-05 ────────────────────────────────────────────────────────────────
def _get_attr_or_extra(obj, key):
    """Read ``key`` off a Pydantic model OR a plain dict, falling back to
    model_extra. Lets the validators run identically on a parsed model
    (publish path) or a raw dict (test stubs)."""
    if isinstance(obj, dict):
        return obj.get(key)
    extras = getattr(obj, "model_extra", None) or {}
    if key in extras:
        return extras[key]
    return getattr(obj, key, None)


# ── W-06 — direct-write tool_buttons require confirm ─────────────────────
def validate_direct_write_buttons_confirm(app_spec, agent_spec) -> List[Dict[str, Any]]:
    """A panel ``tool_button`` bound to a write tool (kind='mcp_action') MUST
    set ``confirm``.

    The direct (no-LLM) ``/apps/{slug}/tool/{name}`` path commits to the
    source system immediately on click — a write must never be a silent
    one-click. Read / refresh buttons (rag, mcp query) don't need confirm.
    """
    if app_spec is None or agent_spec is None:
        return []
    kinds: Dict[str, Any] = {}
    for t in (getattr(agent_spec, "tools_v2", None) or []):
        nm = getattr(t, "name", None)
        if nm:
            kinds[nm] = getattr(t, "kind", None)
    out: List[Dict[str, Any]] = []
    for p in (getattr(app_spec, "all_panels", None) or []):
        for b in (getattr(p, "tool_buttons", None) or []):
            tn = getattr(b, "tool_name", None)
            if kinds.get(tn) == "mcp_action" and not getattr(b, "confirm", None):
                out.append({
                    "rule_id": "W-06",
                    "location": f"app_spec.panel[{getattr(p, 'id', '?')}].tool_buttons[{tn}]",
                    "reason": (
                        "a direct-write tool_button (kind='mcp_action') must set "
                        "`confirm` — a no-LLM source write cannot be a silent "
                        "one-click."
                    ),
                })
    return out




# ── T-03 ────────────────────────────────────────────────────────────────
def validate_no_admin_actions(
    agent_spec, catalogue_index: Optional[Dict[str, Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """Reject mcp_action tools whose catalogue entry is admin_only.

    ``catalogue_index`` maps ``(source_id, dataset_id, action_id) -> entry``
    or just ``action_id -> entry``. Each entry's ``admin_only=True`` flag
    means only platform admins may run it — a BA SmartApp cannot expose
    it. Defensive: when the catalogue is unavailable or the flag is
    absent, the validator passes (no false positives).
    """
    if agent_spec is None or not catalogue_index:
        return []
    out: List[Dict[str, Any]] = []
    for tool in _iter_tools_v2(agent_spec):
        if getattr(tool, "kind", None) != "mcp_action":
            continue
        action_id = getattr(tool, "action_id", None)
        ds_id = getattr(tool, "dataset_id", None)
        src_id = getattr(tool, "source_id", None)
        entry = (
            catalogue_index.get((src_id, ds_id, action_id))
            or catalogue_index.get(action_id)
        )
        if not entry:
            continue
        if entry.get("admin_only") is True:
            out.append({
                "rule_id": "T-03",
                "location": f"agent_spec.tools_v2[{getattr(tool, 'name', '?')}]",
                "reason": (
                    f"write action {action_id!r} is admin_only in the "
                    "catalogue and cannot be exposed from a SmartApp."
                ),
            })
    return out


# ── D-02 ────────────────────────────────────────────────────────────────
def validate_dashboard_page_has_narrator(
    app_spec, agent_spec,
) -> List[Dict[str, Any]]:
    """A dashboard page requires the app to declare a narrator agent.

    A page with ``page.kind == "dashboard"`` is topped by the hero-brief
    copilot, which runs the app's agent (``app_spec.agent_id``) in chat_mode
    (read-only at runtime). Without an agent_id the brief has nothing to
    narrate, so the page is rejected. Read-only safety is enforced by the
    runtime chat tool filter, not here — an app may freely mix a dashboard
    page with action pages whose agent carries write tools.
    """
    if app_spec is None:
        return []
    pages = getattr(app_spec, "pages", None) or []
    has_dashboard_page = any(
        getattr(p, "kind", "standard") == "dashboard" for p in pages
    )
    if not has_dashboard_page:
        return []
    if getattr(app_spec, "agent_id", None):
        return []
    return [{
        "rule_id": "D-02",
        "location": "app_spec.agent_id",
        "reason": (
            "a page with kind='dashboard' requires app_spec.agent_id — the "
            "hero-brief copilot needs a narrator agent. Author one "
            "(citra-dashboard-spec emits a narrator) and reference it via "
            "agent_id."
        ),
    }]


# ── V-CHART-01 ──────────────────────────────────────────────────────────
def validate_chart_axes(app_spec, agent_spec=None) -> List[Dict[str, Any]]:
    """An aggregated chart plots a metric (y) against a category/time (x), so
    x and y MUST be different columns.

    When they are the same column the source-side aggregate SELECT aliases
    collide (``date_trunc(...) AS event_time`` AND ``count(...) AS event_time``)
    and the renderer reads ONE column for both axes — producing a degenerate
    ``y = x`` diagonal instead of real data. (The classic miss: a "count over
    time" chart authored as x=event_time, y=event_time.) For a count, y is the
    column being counted — set it to a distinct column (typically the row id).
    """
    if app_spec is None:
        return []
    errors: List[Dict[str, Any]] = []
    panels: List[Any] = []
    for pg in (getattr(app_spec, "pages", None) or []):
        panels.extend(getattr(pg, "panels", None) or [])
    panels.extend(getattr(app_spec, "panels", None) or [])  # legacy top-level
    for panel in panels:
        if getattr(panel, "type", None) != "chart":
            continue
        if not getattr(panel, "aggregation", None):
            continue
        x = getattr(panel, "x", None)
        y = getattr(panel, "y", None)
        y_cols = y if isinstance(y, list) else ([y] if isinstance(y, str) else [])
        if x and x in y_cols:
            label = getattr(panel, "title", None) or getattr(panel, "id", None) or "?"
            errors.append({
                "rule_id": "V-CHART-01",
                "location": f"chart '{label}'",
                "reason": (
                    f"x and y are the same column ('{x}'). An aggregated chart "
                    f"plots a metric (y) against a category/time (x) — they must "
                    f"differ, or the chart renders a degenerate y=x diagonal. "
                    f"For a count, set y to the column being counted (e.g. the "
                    f"row id), distinct from x. Time series: x=<date>, y=<id>, "
                    f"aggregation=count, time_grain=day."
                ),
            })
    return errors


# ── E-01 / E-02 ─────────────────────────────────────────────────────────
def validate_editable_fields(app_spec, agent_spec) -> List[Dict[str, Any]]:
    """An mcp_action's editable_fields (officer-overridable in plan-then-apply)
    must be RENDERABLE and ENFORCEABLE:
      * E-01 — every field must be a property of the action's input_schema
        (else the officer edits a field the write can't accept);
      * E-02 — a data_source OptionsSource must point at a declared data source
        with a value_column (else the combo renders empty);
      * E-03 — an editable ENUM field must ship a static options list mirroring
        the enum (else it renders LOCKED — governed override is allow-list-only,
        never free text — and the officer cannot flip the verdict).
    Closes "if it publishes, it renders/enforces" for the override feature.
    """
    if agent_spec is None:
        return []
    out: List[Dict[str, Any]] = []
    ds_ids = {
        d.id for d in (getattr(app_spec, "data_sources", None) or [])
    } if app_spec is not None else set()
    for tool in _iter_tools_v2(agent_spec):
        if getattr(tool, "kind", None) != "mcp_action":
            continue
        ef = getattr(tool, "editable_fields", None) or []
        if not ef:
            continue
        props_map = (
            (getattr(tool, "input_schema", None) or {}).get("properties") or {}
        )
        props = set(props_map.keys())
        tname = getattr(tool, "name", "?")

        # E-04 — if the officer can change the DECISION, they must also be able
        # to change the sentence that justifies it.
        #
        # Otherwise an override commits a self-contradicting record: the officer
        # flips status rejected -> approved and the row keeps the agent's
        # rejection prose ("FOIR above policy cap …") as its decision_reason.
        # On a credit file that is the text a regulator reads, arguing for the
        # opposite of the decision recorded beside it. Observed on acme-bank.
        #
        # A NAME-BASED lint, deliberately, and its limits are honest: it can
        # only recognise a justification field by convention (*reason*, *note*,
        # *justification*, *remarks*, *comment*). A field named something else
        # slips through. It is still worth having — it catches the common
        # shape at publish, where the fix is one line, instead of at a bank's
        # audit.
        _editable_names = {f.name for f in ef}
        _JUSTIFY = ("reason", "justification", "remark", "note", "comment")
        if _editable_names:
            for pname in sorted(props):
                if pname in _editable_names:
                    continue
                low = pname.lower()
                if not any(k in low for k in _JUSTIFY):
                    continue
                spec = props_map.get(pname) or {}
                if spec.get("x-citra-fill"):
                    continue      # server-filled, never the officer's to write
                out.append({
                    "rule_id": "E-04",
                    "location": f"agent_spec.tools_v2[{tname}].editable_fields",
                    "reason": (
                        f"'{pname}' looks like the field that justifies this "
                        f"write, but it is not officer-editable while "
                        f"{sorted(_editable_names)} are. An officer who "
                        f"overrides the decision would commit it with the "
                        f"agent's justification for the OPPOSITE decision. Add "
                        f"'{pname}' to editable_fields (control: 'textarea')."
                    ),
                })

        for f in ef:
            loc = f"agent_spec.tools_v2[{tname}].editable_fields[{f.name}]"
            if f.name not in props:
                out.append({
                    "rule_id": "E-01",
                    "location": loc,
                    "reason": (
                        f"editable field '{f.name}' is not a property of the "
                        f"action's input_schema — the officer cannot override a "
                        f"field the write does not accept."
                    ),
                })
            opts = getattr(f, "options", None)
            if opts is not None and getattr(opts, "kind", None) == "data_source":
                if not opts.data_source or opts.data_source not in ds_ids:
                    out.append({
                        "rule_id": "E-02",
                        "location": f"{loc}.options.data_source",
                        "reason": (
                            f"options.data_source '{opts.data_source}' is not a "
                            f"declared app_spec.data_source; the combo would "
                            f"render empty."
                        ),
                    })
                if not opts.value_column:
                    out.append({
                        "rule_id": "E-02",
                        "location": f"{loc}.options.value_column",
                        "reason": "data_source options require a value_column.",
                    })

            # ── E-03: an editable ENUM field MUST ship a usable options source.
            # An enum property has a FIXED allow-list. Governed override is
            # allow-list-only (free-text is deliberately disallowed), and the
            # override combo resolves from editable_fields[].options — NOT from
            # input_schema.enum. So an editable enum field with no options
            # renders LOCKED ("set by the agent") and the officer cannot
            # override it — the exact opposite of declaring it editable. This
            # bites the DISPOSITION/VERDICT field hardest (status pass/fail,
            # decision approve/reject) — the one an officer most needs to flip
            # (e.g. AI flags fraud → 'fail', officer approves claim → 'pass').
            prop = props_map.get(f.name) or {}
            enum_vals = prop.get("enum")
            is_enum = isinstance(enum_vals, list) and len(enum_vals) > 0
            is_editable = getattr(f, "editable", True) is not False
            if is_enum and is_editable:
                kind = getattr(opts, "kind", None) if opts is not None else None
                static_vals = (
                    [getattr(v, "value", None) for v in (getattr(opts, "values", None) or [])]
                    if kind == "static" else []
                )
                # No usable options at all → locked verdict.
                if opts is None or (kind == "static" and not static_vals):
                    _enum_preview = ", ".join(str(v) for v in enum_vals)
                    out.append({
                        "rule_id": "E-03",
                        "location": f"{loc}.options",
                        "reason": (
                            f"editable field '{f.name}' is an input_schema enum "
                            f"({_enum_preview}) but declares NO options — it will "
                            f"render LOCKED and the officer cannot override it. "
                            f"Add \"control\": \"select\" and a static options "
                            f"source MIRRORING the enum, e.g. options: {{\"kind\": "
                            f"\"static\", \"values\": [" +
                            ", ".join(f'{{\"value\": \"{v}\"}}' for v in enum_vals) +
                            f"]}}. (The combo resolves from editable_fields[].options, "
                            f"not from input_schema.enum.)"
                        ),
                    })
                # Static options that offer values the schema forbids → those
                # picks fail input_schema validation at apply time.
                elif kind == "static":
                    _bad = [str(v) for v in static_vals if v not in enum_vals]
                    if _bad:
                        out.append({
                            "rule_id": "E-03",
                            "location": f"{loc}.options.values",
                            "reason": (
                                f"static options for enum field '{f.name}' offer "
                                f"value(s) {_bad} that are NOT in the input_schema "
                                f"enum ({', '.join(str(v) for v in enum_vals)}) — "
                                f"selecting them would be rejected by the write. "
                                f"Options values must be a subset of the enum."
                            ),
                        })
    return out


# ── F-01 ────────────────────────────────────────────────────────────────
def validate_no_media_columns(app_spec, agent_spec=None) -> List[Dict[str, Any]]:
    """Citra-stored media columns are DISABLED.

    A form field with ``format:"file"`` uploads bytes into Citra's own S3
    bucket (a Mongo-overlay media column). That conflicts with the sovereign
    posture: SoR-record media (photos, PDFs) must live in the SOURCE system and
    be read via an ``mcp`` data_source — the dept-MCP resolves the reference and
    STREAMS the bytes; the browser never touches storage. Reject any
    ``format:"file"`` field at publish so the builder cannot author one.
    """
    if app_spec is None:
        return []
    out: List[Dict[str, Any]] = []
    try:
        panels = list(getattr(app_spec, "all_panels", None) or [])
    except Exception:  # noqa: BLE001 — malformed spec fails elsewhere
        panels = []
    for p in panels:
        schema = getattr(p, "schema_inline", None)
        if not isinstance(schema, dict):
            continue
        for fname, fdef in (schema.get("properties") or {}).items():
            if isinstance(fdef, dict) and fdef.get("format") == "file":
                out.append({
                    "rule_id": "F-01",
                    "location": (
                        f"app_spec.panel[{getattr(p, 'id', '?')}]"
                        f".schema_inline.properties[{fname}]"
                    ),
                    "reason": (
                        f"field '{fname}' uses format:\"file\" — Citra-stored media "
                        f"columns are DISABLED. Media must live in the source system "
                        f"and be read via an `mcp` data_source (the dept-MCP streams "
                        f"the bytes); do not upload files into Citra storage. Remove "
                        f"the file field."
                    ),
                })
    return out


# ── W-02 ────────────────────────────────────────────────────────────────
def validate_update_has_identifier(agent_spec) -> List[Dict[str, Any]]:
    """Mutating verbs MUST require an identifier in their input_schema.

    Without a row key the LLM can issue ``UPDATE table SET x=1`` which
    hits every row. Required identifiers: ``id``, ``_id``, ``pk``,
    ``key``, or any ``*_id``.
    """
    if agent_spec is None:
        return []
    out: List[Dict[str, Any]] = []
    for tool in _iter_tools_v2(agent_spec):
        if getattr(tool, "kind", None) != "mcp_action":
            continue
        action_id = (getattr(tool, "action_id", "") or "").lower()
        verb = action_id.split(".")[-1]
        if verb not in _MUTATING_VERBS:
            continue
        schema = getattr(tool, "input_schema", None) or {}
        required = schema.get("required") if isinstance(schema, dict) else None
        required = list(required or [])
        ok = any(
            (name in _IDENTIFIER_FIELD_NAMES)
            or name.endswith(_IDENTIFIER_SUFFIX)
            for name in required
        )
        if not ok:
            out.append({
                "rule_id": "W-02",
                "location": f"agent_spec.tools_v2[{getattr(tool, 'name', '?')}].input_schema.required",
                "reason": (
                    f"verb={verb!r} mutates rows but input_schema.required "
                    "does not include an identifier (id / _id / pk / key / "
                    "*_id). Without one the LLM can mutate every row."
                ),
            })
    return out


# ── W-08 ────────────────────────────────────────────────────────────────
def validate_mcp_action_has_input_schema(agent_spec) -> List[Dict[str, Any]]:
    """Every ``mcp_action`` tool MUST declare a non-empty ``input_schema``
    with at least one ``required`` field.

    The catalogued ``write_action`` the tool mirrors always carries an
    ``input_schema`` (the row identifier + the fields the write sets), and
    ``citra-agent-spec`` mandates copying it VERBATIM. An empty/absent
    ``input_schema`` means the runtime has NO fields to send, so the dept-MCP
    rejects every invocation with ``422 missing required fields`` and the
    action button can never succeed.

    W-02 only inspects schemas whose mutating verb it can parse from
    ``action_id``; a custom-named action (e.g. ``update_recovery_status``)
    carrying an EMPTY schema is skipped there (its derived verb isn't in the
    mutating set), so it slips through. This rule is the verb-agnostic
    backstop. We do NOT fabricate the schema — fail loud so the builder
    re-authors the tool from the citra-mcp-discover output.
    """
    if agent_spec is None:
        return []
    out: List[Dict[str, Any]] = []
    for tool in _iter_tools_v2(agent_spec):
        if getattr(tool, "kind", None) != "mcp_action":
            continue
        schema = getattr(tool, "input_schema", None) or {}
        props = (schema.get("properties") if isinstance(schema, dict) else None) or {}
        required = (schema.get("required") if isinstance(schema, dict) else None) or []
        if props and required:
            continue
        name = getattr(tool, "name", "?")
        out.append({
            "rule_id": "W-08",
            "location": f"agent_spec.tools_v2[{name}].input_schema",
            "reason": (
                f"mcp_action {name!r} (action_id="
                f"{getattr(tool, 'action_id', '?')!r}) has an empty input_schema. "
                "Copy the source write_action's input_schema VERBATIM from the "
                "citra-mcp-discover output — it declares the required row "
                "identifier and the fields the write sets. Without it the "
                "dept-MCP rejects every call with 'missing required fields' and "
                "the action can never run."
            ),
        })
    return out


# ── W-09 ────────────────────────────────────────────────────────────────
def validate_required_lookup_is_bound(agent_spec) -> List[Dict[str, Any]]:
    """A ``required: true`` mcp read tool MUST be bound (``dataset_id`` set).

    A required lookup is enforced by the read-before-write evidence gate
    (evidence_guard.required_lookup_tools): the write can't stage unless the
    lookup RAN for the case under review. A policy-mandated check has to be a
    precise, keyed dataset lookup — an unbound / semantic-only tool would run as
    a fuzzy NL query that could match anything, so "the mandatory check ran" is
    neither unambiguous nor auditable. Fail loud so the builder binds it (set
    ``dataset_id`` + ``dataset_kind`` from the catalogue) or drops ``required``.
    """
    if agent_spec is None:
        return []
    out: List[Dict[str, Any]] = []
    for tool in _iter_tools_v2(agent_spec):
        if getattr(tool, "kind", None) != "mcp":
            continue
        if getattr(tool, "required", False) is not True:
            continue
        if getattr(tool, "dataset_id", None):
            continue
        name = getattr(tool, "name", "?")
        out.append({
            "rule_id": "W-09",
            "location": f"agent_spec.tools_v2[{name}].dataset_id",
            "reason": (
                f"mcp tool {name!r} is marked required:true but is not bound to a "
                "dataset (dataset_id unset). A required lookup is enforced by the "
                "read-before-write gate, which anchors on the id the lookup ran "
                "with — only captured on the keyed path. Set dataset_id + "
                "dataset_kind (from the catalogue) so the tool uses the keyed "
                "read, or drop required:true."
            ),
        })
    return out


# ── S-01 ────────────────────────────────────────────────────────────────
def validate_internal_audience(app_spec) -> List[Dict[str, Any]]:
    """Audience must be one of: owner | team:<sa> | dept:<id> | org.

    SmartApps are internal officer tools; ``audience='public'`` (or any
    unrecognised form) is rejected. The shape mirrors the AppSpec
    docstring contract; this validator is the runtime gate.
    """
    if app_spec is None:
        return []
    aud = (getattr(app_spec, "audience", None) or "").strip()
    if not aud:
        return [{
            "rule_id": "S-01",
            "location": "app_spec.audience",
            "reason": "audience is required; pick one of owner|team:*|dept:*|org.",
        }]
    if aud in _INTERNAL_AUDIENCE_LITERALS:
        return []
    if any(aud.startswith(p) and len(aud) > len(p) for p in _INTERNAL_AUDIENCE_PREFIXES):
        return []
    return [{
        "rule_id": "S-01",
        "location": "app_spec.audience",
        "reason": (
            f"audience={aud!r} is not a recognised internal scope. "
            "Allowed: owner | team:<sa_id> | dept:<dept_id> | org. "
            "Public audience is not supported — SmartApps are internal."
        ),
    }]


# ── S-03 ────────────────────────────────────────────────────────────────
def _shannon_entropy(s: str) -> float:
    if not s:
        return 0.0
    freq: Dict[str, int] = {}
    for ch in s:
        freq[ch] = freq.get(ch, 0) + 1
    n = float(len(s))
    return -sum((c / n) * math.log2(c / n) for c in freq.values())


def _walk_strings(obj: Any, path: str = "$") -> Iterable[tuple]:
    if isinstance(obj, str):
        yield path, obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield from _walk_strings(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _walk_strings(v, f"{path}[{i}]")


def scan_for_secrets(payload_dict: Any) -> List[Dict[str, Any]]:
    """Recursive secret-token scan over the whole publish payload.

    Two detectors:
      • exact-prefix match against known credential issuers
        (OpenAI sk-, AWS AKIA, GitHub ghp_, Vault hvs., Slack xoxb-)
      • Shannon entropy > 4.5 over any 32+ char segment — catches
        unknown rotating keys.

    A hit blocks publish — the BA must move the secret to Vault and
    reference it via ``env:NAME``.
    """
    out: List[Dict[str, Any]] = []
    seen_paths: set = set()
    for path, value in _walk_strings(payload_dict):
        for prefix in _SECRET_PREFIXES:
            if prefix in value:
                if path in seen_paths:
                    break
                seen_paths.add(path)
                out.append({
                    "rule_id": "S-03",
                    "location": path,
                    "reason": (
                        f"value contains credential prefix {prefix!r}. "
                        "Move the secret to Vault and reference it via "
                        "env:NAME — never inline it in the spec."
                    ),
                })
                break
        else:
            # No prefix hit → entropy check on 32+ char tokens.
            for token in re.findall(r"[A-Za-z0-9+/=_\-]{32,}", value):
                if _shannon_entropy(token) > _SECRET_ENTROPY_THRESHOLD:
                    if path in seen_paths:
                        break
                    seen_paths.add(path)
                    out.append({
                        "rule_id": "S-03",
                        "location": path,
                        "reason": (
                            "high-entropy token detected (looks like a "
                            "rotating credential). Move the value to "
                            "Vault and reference via env:NAME."
                        ),
                    })
                    break
    return out


# ── G-01 ────────────────────────────────────────────────────────────────
def validate_grounding_contract(agent_spec) -> List[Dict[str, Any]]:
    """Gate B (publish-time): a grounded agent must carry a vetted contract.

    Any AgentSpec with a ``neighbor_samples`` tool grounds the LLM in the
    tenant's historical decisions. That is only safe if Gate A
    (``/builder/history-quality``) actually vetted the data, so the
    ``AgentSpec.grounding`` contract is REQUIRED and must carry the Gate A
    evidence (``source_profile_baseline`` + ``evaluation_verdict``) plus the
    refresh-guard thresholds. We also require the tool's ``collection`` to
    be ``Historical_Refresh`` (rows isolated per agent_id) so the server-side
    refresh writes exactly where the runtime reads. Missing/incomplete → block publish (fail loud);
    a future degraded refresh is then caught by the same guard at runtime.
    """
    if agent_spec is None:
        return []
    neighbor_tools = [
        t for t in _iter_tools_v2(agent_spec)
        if _get_attr_or_extra(t, "kind") == "neighbor_samples"
    ]
    if not neighbor_tools:
        return []

    out: List[Dict[str, Any]] = []
    grounding = _get_attr_or_extra(agent_spec, "grounding")
    if not grounding:
        return [{
            "rule_id": "G-01",
            "location": "agent_spec.grounding",
            "reason": (
                "AgentSpec declares a `neighbor_samples` tool but has no "
                "`grounding` contract. Grounding can only ship after Gate A "
                "(/builder/history-quality) vetted the data — add the "
                "grounding contract (source/dataset, field mapping, guard "
                "thresholds, source_profile_baseline + evaluation_verdict)."
            ),
        }]

    baseline = _get_attr_or_extra(grounding, "source_profile_baseline")
    verdict = _get_attr_or_extra(grounding, "evaluation_verdict")
    if not baseline:
        out.append({
            "rule_id": "G-01",
            "location": "agent_spec.grounding.source_profile_baseline",
            "reason": (
                "missing Gate A evidence: set `source_profile_baseline` to the "
                "/builder/history-quality `signals` captured at build time."
            ),
        })
    if not (isinstance(verdict, str) and verdict.strip()):
        out.append({
            "rule_id": "G-01",
            "location": "agent_spec.grounding.evaluation_verdict",
            "reason": (
                "missing Gate A judgment: set `evaluation_verdict` to your "
                "one-paragraph ground/don't-ground rationale."
            ),
        })

    # All agents' grounding lives in ONE shared collection; rows are isolated
    # by an agent_id field. The tool's collection must be that shared name so
    # the refresh writes (and the runtime reads, filtered by agent_id) the
    # same place.
    expected = "Historical_Refresh"
    for t in neighbor_tools:
        coll = _get_attr_or_extra(t, "collection")
        if coll and coll != expected:
            out.append({
                "rule_id": "G-01",
                "location": "agent_spec.tools_v2[neighbor_samples].collection",
                "reason": (
                    f"collection {coll!r} must equal {expected!r} — grounding "
                    "uses one shared collection for all agents (rows isolated "
                    "by agent_id)."
                ),
            })
    return out


# ── I-01 — icons come from the closed vocabulary ─────────────────────────
def validate_icons(app_spec, agent_spec=None) -> List[Dict[str, Any]]:
    """Every icon name in the spec must be in models.ICON_NAMES — the runtime
    has a static import map for exactly that set, so an unknown name renders
    NOTHING (worse than no icon: it looks broken only sometimes). Enforced at
    publish, not model-load, so legacy stored specs still open."""
    from models import ICON_NAMES

    out: List[Dict[str, Any]] = []

    def _check(name, location):
        if name and name not in ICON_NAMES:
            out.append({
                "rule_id": "I-01",
                "location": location,
                "reason": (
                    f"icon {name!r} is not in the icon vocabulary — pick from "
                    "models.ICON_NAMES (see citra-ui-panels 'Icons')."
                ),
            })

    pages = list(getattr(app_spec, "pages", None) or [])
    for pi, page in enumerate(pages):
        _check(getattr(page, "icon", None), f"pages[{pi}].icon")
    panel_sets = [(f"pages[{pi}].panels", list(p.panels or []))
                  for pi, p in enumerate(pages)]
    if getattr(app_spec, "panels", None):
        panel_sets.append(("panels", list(app_spec.panels)))
    for loc, panels in panel_sets:
        for i, panel in enumerate(panels):
            _check(getattr(panel, "icon", None), f"{loc}[{i}].icon")
            for j, m in enumerate(getattr(panel, "metrics", None) or []):
                _check(getattr(m, "icon", None), f"{loc}[{i}].metrics[{j}].icon")
            metric = getattr(panel, "metric", None)
            if metric is not None:
                _check(getattr(metric, "icon", None), f"{loc}[{i}].metric.icon")
            for j, a in enumerate(getattr(panel, "actions", None) or []):
                _check(getattr(a, "icon", None), f"{loc}[{i}].actions[{j}].icon")
            for j, s in enumerate(getattr(panel, "sections", None) or []):
                _check(getattr(s, "icon", None), f"{loc}[{i}].sections[{j}].icon")
    return out


# ---------------------------------------------------------------------------
# CS-01 — case signature (docs/clause-memory-graph-plan.md §2.4)
# ---------------------------------------------------------------------------

#: Column types a numeric band may be computed from.
_NUMERIC_COL_TYPES = {"number", "numeric", "integer", "int", "float", "double",
                      "decimal", "bigint", "long", "money", "currency"}
#: Column types an age_band may be computed from.
_DATE_COL_TYPES = {"timestamp", "date", "datetime", "time", "timestamptz"}

#: Facet cardinality guardrail. NOT about clause support — clustering uses an
#: overlap coefficient, so incidental families do not keep corrections apart
#: (consolidation.facet_compatible). High cardinality is a smell that the
#: builder is declaring CONTEXT rather than decision factors: it widens the
#: __unknown drift surface and pads every signature for no routing gain.
_MAX_SIGNATURE_CELLS = 20_000
_WARN_SIGNATURE_CELLS = 5_000


def _facet_cardinality(spec: Any) -> int:
    """How many distinct tokens this family can emit (incl. __unknown)."""
    kind = getattr(spec, "kind", None)
    if kind == "enum":
        return len(getattr(spec, "values", None) or []) + 1      # + __unknown
    if kind == "band":
        return len(getattr(spec, "edges", None) or []) + 1
    if kind == "age_band":
        return len(getattr(spec, "edges", None) or []) + 2       # bands + __unknown
    return 2                                                      # presence / signal


def validate_case_signature(app_spec) -> List[Dict[str, Any]]:
    """The facet vocabulary must resolve against the app's bound datasets.

    A facet referencing a column the dataset does not have would emit
    ``__unknown`` for EVERY case — silently routing no clause while looking
    exactly like "this app has not learned anything yet". Caught at publish,
    where it is a one-line fix instead of a month of missing learning.

    Column checks are SKIPPED (not failed) when ``dataset_directory`` is not
    hydrated — validating against an empty directory would reject every valid
    spec. Structural checks always run.
    """
    from case_signature import PLATFORM_SIGNAL_IDS

    sig = getattr(app_spec, "case_signature", None)
    if sig is None:
        return []                                     # opt-in; absent is valid

    out: List[Dict[str, Any]] = []

    def _err(location: str, reason: str, code: str) -> None:
        out.append({"rule_id": "CS-01", "location": location,
                    "reason": reason, "code": code})

    # Column index from the publish-time dataset directory.
    columns: Dict[str, Dict[str, str]] = {}
    for entry in (getattr(app_spec, "dataset_directory", None) or []):
        did = getattr(entry, "dataset_id", None)
        by_name = {
            str(getattr(c, "name", "")): str(getattr(c, "type", "") or "").lower()
            for c in (getattr(entry, "columns", None) or [])
        }
        if did:
            columns[str(did)] = by_name
    have_directory = bool(columns)
    any_cols: Dict[str, str] = {}
    for by_name in columns.values():
        any_cols.update(by_name)

    def _resolve(dataset_id: Optional[str], col: str) -> Optional[str]:
        """Column type, or None when the column does not exist."""
        if dataset_id and dataset_id in columns:
            return columns[dataset_id].get(col)
        return any_cols.get(col)

    seen_families: set = set()
    cells = 1

    for i, f in enumerate(list(getattr(sig, "facets", None) or [])):
        loc = f"case_signature.facets[{i}]"
        family = getattr(f, "family", None)
        kind = getattr(f, "kind", None)
        dsid = getattr(f, "dataset_id", None)

        if family in seen_families:
            _err(loc, f"duplicate facet family {family!r} — one family emits one "
                      "token per case, so two specs would conflict.",
                 "case_signature_duplicate_family")
        seen_families.add(family)

        edges = list(getattr(f, "edges", None) or [])
        if kind in ("band", "age_band"):
            if not edges:
                _err(loc, f"{kind} facet {family!r} needs `edges`.",
                     "case_signature_bad_bands")
            elif any(b <= a for a, b in zip(edges, edges[1:])):
                _err(loc, f"facet {family!r} edges must be strictly increasing, "
                          f"got {edges}.", "case_signature_bad_bands")

        if kind == "signal":
            sid = getattr(f, "signal_id", None)
            if not sid:
                _err(loc, f"signal facet {family!r} needs `signal_id`.",
                     "case_signature_unknown_signal")
            elif sid not in PLATFORM_SIGNAL_IDS:
                _err(loc, f"signal_id {sid!r} is not a platform signal — pick "
                          f"from: {', '.join(sorted(PLATFORM_SIGNAL_IDS))}.",
                     "case_signature_unknown_signal")

        elif kind == "age_band":
            cols = list(getattr(f, "from_columns", None) or [])
            if len(cols) != 2:
                _err(loc, f"age_band facet {family!r} needs exactly two "
                          "`from_columns` [start, end].",
                     "case_signature_unknown_column")
            elif have_directory:
                for c in cols:
                    ctype = _resolve(dsid, c)
                    if ctype is None:
                        _err(loc, f"column {c!r} not found on the bound dataset"
                                  f"{f' {dsid!r}' if dsid else ''}.",
                             "case_signature_unknown_column")
                    elif ctype not in _DATE_COL_TYPES:
                        _err(loc, f"age_band facet {family!r} needs date columns; "
                                  f"{c!r} is {ctype!r}.",
                             "case_signature_type_mismatch")

        else:
            col = getattr(f, "from_column", None)
            if not col:
                _err(loc, f"{kind} facet {family!r} needs `from_column`.",
                     "case_signature_unknown_column")
            elif have_directory:
                ctype = _resolve(dsid, col)
                if ctype is None:
                    _err(loc, f"column {col!r} not found on the bound dataset"
                              f"{f' {dsid!r}' if dsid else ''} — this facet would "
                              "emit __unknown for every case.",
                         "case_signature_unknown_column")
                elif kind == "band" and ctype not in _NUMERIC_COL_TYPES:
                    _err(loc, f"band facet {family!r} needs a numeric column; "
                              f"{col!r} is {ctype!r}.",
                         "case_signature_type_mismatch")

            if kind == "enum" and not (getattr(f, "values", None) or []):
                _err(loc, f"enum facet {family!r} must DECLARE its `values` — "
                          "without them every value is accepted and ontology "
                          "drift becomes invisible.",
                     "case_signature_unknown_column")

        cells *= max(1, _facet_cardinality(f))

    if cells > _MAX_SIGNATURE_CELLS:
        _err("case_signature.facets",
             f"facet space is {cells} cells (max {_MAX_SIGNATURE_CELLS}) — a "
             "signature this wide is declaring context, not decision factors. "
             "Keep only families an officer's correction could turn on; drop "
             "the rest or widen their bands.",
             "case_signature_cardinality")
    elif cells > _WARN_SIGNATURE_CELLS:
        logger.warning(
            "[CS-01] app %s has a %d-cell facet space — likely declaring "
            "context rather than decision factors; families whose value never "
            "changes a correction only widen the drift surface.",
            getattr(app_spec, "slug", "?"), cells,
        )

    codes = [getattr(rc, "code", None)
             for rc in (getattr(sig, "reason_codes", None) or [])]
    if len(set(codes)) != len(codes):
        _err("case_signature.reason_codes", "reason codes must be unique.",
             "case_signature_thin_taxonomy")
    # J7: aliases must not collide with any live code or another alias — a
    # collision would silently merge two different lessons at consolidation.
    all_names = list(codes)
    for rc in (getattr(sig, "reason_codes", None) or []):
        for a in (getattr(rc, "aliases", None) or []):
            all_names.append(a)
    if len(set(all_names)) != len(all_names):
        _err("case_signature.reason_codes",
             "aliases must be unique and must not collide with any code — a "
             "collision silently merges two different lessons.",
             "case_signature_thin_taxonomy")
    # NO minimum-taxonomy check any more. reason_codes is DEPRECATED: an
    # officer's correction is free text plus the fields they changed, and
    # clustering partitions on contested_fields, so an app declaring no codes
    # learns exactly as well as one that does. Requiring codes here would fail
    # every newly built app. Legacy specs may still carry them — the uniqueness
    # and alias-collision checks above still apply to those.

    return out


def _panel_projections(app_spec) -> Tuple[List[List[str]], bool]:
    """``(column lists declared by panels, any_panel_selects_everything)``.

    A panel with no ``columns`` list is treated as selecting the whole row, so
    its presence makes the projection check inconclusive — we do not fail on a
    guess."""
    projections: List[List[str]] = []
    wildcard = False
    for page in (getattr(app_spec, "pages", None) or []):
        for panel in (getattr(page, "panels", None) or []):
            cols = getattr(panel, "columns", None)
            if cols is None and isinstance(panel, dict):
                cols = panel.get("columns")
            if cols:
                projections.append([str(c) for c in cols])
            elif _panel_reads_rows(panel):
                wildcard = True
    return projections, wildcard


def _panel_reads_rows(panel) -> bool:
    """Does this panel fetch record rows at all? Charts and static panels do
    not, so their lack of a ``columns`` list must not count as a wildcard."""
    get = panel.get if isinstance(panel, dict) else lambda k, d=None: getattr(panel, k, d)
    return bool(get("data_source_id") or get("dataset_id") or get("panel_query"))


def validate_case_signature_projection(app_spec) -> List[Dict[str, Any]]:
    """CS-02 — every facet column must survive the PANEL PROJECTION.

    CS-01 checks the column exists on the bound dataset. That is necessary and
    not sufficient: facets are derived from the row the runtime actually holds,
    which is the panel's projection, not the table. acme-bank shipped a
    ``sourcing_channel`` facet whose column existed on the dataset (CS-01 passed)
    but was absent from the review panel's ``columns``. Every case therefore
    derived ``sourcing_channel:__unknown``, and the DSA judgement scoped to
    ``sourcing_channel:dsa`` — the one the evidence pack is built on — could
    never be retrieved. It fired 19/19 over the API and 0/1 through the app, and
    the only signal was a drift warning that looked routine.

    Conservative by construction: it fails ONLY when at least one panel declares
    a projection and NO panel could supply the column. A panel that selects the
    whole row makes the check inconclusive and it stays silent.
    """
    sig = getattr(app_spec, "case_signature", None)
    if sig is None:
        return []

    projections, wildcard = _panel_projections(app_spec)
    if wildcard or not projections:
        return []

    selected = {c for cols in projections for c in cols}
    selected |= {c.rsplit(".", 1)[-1] for c in selected}   # physical/logical split

    out: List[Dict[str, Any]] = []
    for i, f in enumerate((getattr(sig, "facets", None) or [])):
        kind = str(getattr(f, "kind", "") or "")
        if kind == "signal":
            continue                       # derived from signals, not a column
        cols = ([str(c) for c in (getattr(f, "from_columns", None) or [])]
                if kind == "age_band"
                else [str(getattr(f, "from_column", "") or "")])
        for col in [c for c in cols if c]:
            if col in selected or col.rsplit(".", 1)[-1] in selected:
                continue
            out.append({
                "rule_id": "CS-02",
                "location": f"case_signature.facets[{i}].from_column",
                "code": "case_signature_column_not_projected",
                "reason": (
                    f"facet {getattr(f, 'family', '?')!r} reads column {col!r}, "
                    "which no panel selects. The runtime derives facets from the "
                    "panel's projection, so this family would resolve to "
                    "__unknown on EVERY case and every clause scoped to it would "
                    f"be dead. Add {col!r} to the columns of the panel that feeds "
                    "review."
                ),
            })
    return out


# ---------------------------------------------------------------------------
# FS-01 / FS-02 — factor set (docs/factor-scorecard-plan.md)
# ---------------------------------------------------------------------------


def validate_factor_set(app_spec) -> List[Dict[str, Any]]:
    """The declared rubric must resolve against the app's bound datasets.

    Shape rules (weights required under ``composite``, forbidden under
    ``checklist``, band ordering, grade-scale ordering) are enforced by the
    Pydantic model and never reach here. What this adds is the part the model
    cannot see: whether a factor's declared ``reads`` points at a dataset the
    app is actually bound to.

    A factor reading a dataset that does not exist would produce no finding on
    every case. Under ``checklist`` that is a visibly empty row; under
    ``composite`` it is worse — the factor drops out of the denominator, so the
    case scores over a partial rubric and still renders a confident grade.
    Caught at publish, where it is a one-line fix.

    Dataset checks are SKIPPED (not failed) when ``dataset_directory`` is not
    hydrated — validating against an empty directory would reject every valid
    spec. Structural checks always run.
    """
    fset = getattr(app_spec, "factor_set", None)
    if fset is None:
        return []                                     # opt-in; absent is valid

    out: List[Dict[str, Any]] = []

    def _err(location: str, reason: str, code: str) -> None:
        out.append({"rule_id": "FS-01", "location": location,
                    "reason": reason, "code": code})

    known: set = set()
    for entry in (getattr(app_spec, "dataset_directory", None) or []):
        did = getattr(entry, "dataset_id", None)
        if did:
            known.add(str(did))

    for i, factor in enumerate(getattr(fset, "factors", None) or []):
        loc = f"factor_set.factors[{i}]"
        reads = getattr(factor, "reads", None)
        if reads is None:
            continue                                  # model requires it; belt-and-braces
        kind = str(getattr(reads, "kind", "dataset") or "dataset")
        dataset_id = getattr(reads, "dataset_id", None)

        if kind == "lookup":
            if not getattr(reads, "tool_name", None):
                _err(f"{loc}.reads.tool_name",
                     f"factor {getattr(factor, 'id', '?')!r} reads kind='lookup' "
                     "but names no tool. An external check with no tool to call "
                     "produces no finding, and the factor silently never scores.",
                     "factor_lookup_without_tool")
            continue

        if not dataset_id:
            _err(f"{loc}.reads.dataset_id",
                 f"factor {getattr(factor, 'id', '?')!r} declares no dataset. A "
                 "factor whose evidence is not bound is one the model has to go "
                 "hunting for, which is how the same case scores differently on "
                 "consecutive days.",
                 "factor_reads_unbound")
            continue

        if kind == "document":
            # dataset_id here is the ATTACHMENT COLUMN on the anchor record, not
            # a dataset — see FactorReads. Checking it against the bound dataset
            # ids rejected every document-reading factor (a bank statement, an
            # attached PDF) the moment the directory was hydrated.
            continue

        if known and str(dataset_id) not in known:
            _err(f"{loc}.reads.dataset_id",
                 f"factor {getattr(factor, 'id', '?')!r} reads dataset "
                 f"{str(dataset_id)!r}, which this app is not bound to. It would "
                 "produce no finding on every case — under mode='composite' the "
                 "factor drops out of the denominator and the case still renders "
                 "a confident grade over a partial rubric.",
                 "factor_unknown_dataset")

        # FS-04 — a fingerprint is only checkable against a WHOLE document.
        # Declared without sop.doc_path it can never match what the runtime
        # observes (which is None in query mode), so it would sit in the spec
        # looking like a live guarantee while checking nothing.
        sop = getattr(factor, "sop", None)
        if sop is not None and getattr(sop, "fingerprint", None) and not getattr(sop, "doc_path", None):
            _err(f"{loc}.sop.fingerprint",
                 f"factor {getattr(factor, 'id', '?')!r} declares a SOP "
                 "fingerprint but no sop.doc_path. Drift is only detectable "
                 "against the WHOLE document: in query mode the runtime hashes a "
                 "top-k retrieval that changes when the index is rebuilt or an "
                 "unrelated document is added, so the fingerprint would either "
                 "never be compared or cry wolf forever. Set sop.doc_path, or "
                 "drop the fingerprint and accept that drift is not detected.",
                 "factor_fingerprint_without_doc_path")

    for i, gate in enumerate(getattr(fset, "gates", None) or []):
        reads = getattr(gate, "reads", None)
        if reads is None:
            continue                                  # a gate may be computed from the anchor record alone
        dataset_id = getattr(reads, "dataset_id", None)
        if dataset_id and known and str(dataset_id) not in known:
            _err(f"factor_set.gates[{i}].reads.dataset_id",
                 f"gate {getattr(gate, 'id', '?')!r} reads dataset "
                 f"{str(dataset_id)!r}, which this app is not bound to. An "
                 "unevaluated gate flags every case for a human — the policy "
                 "limit stops being enforced without anything looking broken.",
                 "gate_unknown_dataset")

    return out


def validate_factor_set_mode_stable(app_spec, previous_app_spec) -> List[Dict[str, Any]]:
    """``factor_set.mode`` is PERMANENT for a published app.

    An app that silently grew a total one day would change how every one of its
    past outputs should be read — a checklist row that meant "judged, not
    scored" retroactively reads as a component of a grade nobody computed. That
    is the same class of harm as memory quietly moving a score, and the audit
    property is the entire point of this feature.

    Adding a factor set to an app that had none is allowed (nothing to
    reinterpret). REMOVING one is also allowed — the app stops producing a grid,
    which is visible rather than silent. Only a mode FLIP is rejected: that is a
    new app version with its own human confirmation.

    ``previous_app_spec`` is the stored spec dict (or None for a first publish).
    """
    if not previous_app_spec:
        return []                                     # first publish; nothing to preserve

    new_set = getattr(app_spec, "factor_set", None)
    new_mode = getattr(new_set, "mode", None) if new_set is not None else None

    prev = previous_app_spec
    prev_set = prev.get("factor_set") if isinstance(prev, dict) else getattr(prev, "factor_set", None)
    if prev_set is None:
        return []
    prev_mode = (prev_set.get("mode") if isinstance(prev_set, dict)
                 else getattr(prev_set, "mode", None))

    if not prev_mode or new_mode is None or new_mode == prev_mode:
        return []

    return [{
        "rule_id": "FS-02",
        "location": "factor_set.mode",
        "code": "factor_set_mode_changed",
        "reason": (
            f"factor_set.mode cannot change on a published app: it is "
            f"{prev_mode!r} and this spec declares {new_mode!r}. Every past "
            f"decision this app made was recorded under {prev_mode!r}, and "
            "flipping the mode changes how all of them should be read — a "
            "checklist row that meant 'judged, not scored' would retroactively "
            "look like a component of a grade nobody computed. Publish this as a "
            "new app instead, so the old one's history keeps its meaning."
        ),
    }]

# ---------------------------------------------------------------------------
# FS-05 — the rubric you FOUND must be the rubric you DECLARE
# ---------------------------------------------------------------------------


def validate_rubric_finding_matches_declaration(app_spec) -> List[Dict[str, Any]]:
    """Compare what the builder recorded reading the policy against what it
    declared in the spec.

    This rule used to be a heuristic over the app's own prose: weight words
    ("25 marks", "weighted") plus aggregate words ("total score", "Grade B")
    with no ``factor_set`` meant the model was being told to compute a
    composite in a sentence. It fired correctly on the spec that prompted it —
    and then the builder, whose skill file documented the trigger, described
    the same assessment in different words and sailed through.

    That is not a regex that needs hardening. Prose is a RENDERING of intent
    and a language model re-renders on demand, so every tightening buys one
    round and costs precision, until the rule fires on honest apps and gets
    muted. Muted rules protect nothing.

    So the comparison moved onto two things the builder must STATE — the
    ``rubric_finding`` record and the ``factor_set`` declaration — with no
    vocabulary in between to paraphrase.

    | verdict             | factor_set        | outcome |
    |---------------------|-------------------|---------|
    | weighted_rubric     | absent            | BLOCK   |
    | weighted_rubric     | composite         | pass    |
    | weighted_rubric     | checklist         | BLOCK — shape mismatch |
    | criteria_checklist  | absent            | BLOCK   |
    | criteria_checklist  | checklist         | pass    |
    | criteria_checklist  | composite         | BLOCK — invents weights |
    | none                | absent            | pass — the honest case |
    | none                | present           | pass — the rubric came from the BA, not the document |
    | (no record)         | anything          | pass — nothing was claimed |

    The last row is the honest limit: an app that never reads a policy makes no
    claim, so there is nothing to check. See the plan's open questions.
    """
    finding = getattr(app_spec, "rubric_finding", None)
    if finding is None:
        return []

    verdict = getattr(finding, "verdict", None)
    fset = getattr(app_spec, "factor_set", None)
    mode = getattr(fset, "mode", None) if fset is not None else None
    src = getattr(finding, "source", "?")

    def _err(reason: str, code: str) -> List[Dict[str, Any]]:
        return [{"rule_id": "FS-05", "location": "factor_set",
                 "code": code, "reason": reason}]

    if verdict == "none":
        # Recorded "no rubric in the document". Declaring one anyway is fine —
        # the weights came from the BA rather than the policy, which is a real
        # and legitimate situation.
        return []

    want = "composite" if verdict == "weighted_rubric" else "checklist"
    human = ("a WEIGHTED RUBRIC" if verdict == "weighted_rubric"
             else "a CRITERIA CHECKLIST")

    if fset is None:
        return _err(
            f"the build recorded finding {human} in {src!r}, and this spec "
            "declares no factor_set. Then the scoring exists only as prose in "
            "the agent's prompt: the model does the arithmetic (so the result "
            "is not reproducible), no factor can be reviewed on its own, and "
            "there is nothing to open for evidence — which is exactly the "
            f"artefact factor_set replaces. Declare it with mode={want!r}, one "
            "check_evaluate per factor (task_type == factor id). If the "
            "document does NOT carry a rubric, correct the finding instead: "
            "set verdict='none' with a reason, and say so to the BA.",
            "rubric_found_but_not_declared")

    if mode != want:
        return _err(
            f"the build recorded finding {human} in {src!r}, but factor_set "
            f"declares mode={mode!r}. Those disagree. "
            + ("A checklist has no weights and produces no total, so declaring "
               "one over a weighted policy silently drops the grade the policy "
               "defines."
               if want == "composite" else
               "A composite invents weights the document does not carry, and a "
               "total nobody in the policy agreed to.")
            + " Fix whichever is wrong — the reading or the declaration.",
            "rubric_finding_mode_mismatch")

    return []

# ---------------------------------------------------------------------------
# FS-06 — a declared factor needs a check that can produce a number
# ---------------------------------------------------------------------------


def validate_factor_checks_can_score(app_spec, agent_spec) -> List[Dict[str, Any]]:
    """A ``check_evaluate`` in ``mode="rule"`` cannot score a composite factor.

    Rule mode is deterministic and returns a VERDICT — pass / flag / fail — with
    no number, which is exactly right for a gate and useless for a weighted
    factor. Wired to a factor it produced a finding with no score; that used to
    take the whole run down and now degrades the factor to `unscored`, so every
    case silently loses that factor's weight from the denominator.

    Neither outcome is something the builder can see, so say it at publish. The
    fix is either ``mode="llm"`` (the model judges and returns a fraction) or
    moving the check to ``gates`` if it really is pass/fail.

    Checklist mode is exempt: a checklist row carries a band, not a number, and
    a rule verdict can reasonably stand in for one.
    """
    fset = getattr(app_spec, "factor_set", None)
    if fset is None or getattr(fset, "mode", None) != "composite":
        return []
    if agent_spec is None:
        return []

    factor_ids = {getattr(f, "id", None) for f in (getattr(fset, "factors", None) or [])}
    out: List[Dict[str, Any]] = []
    for tool in (getattr(agent_spec, "tools_v2", None) or []):
        if getattr(tool, "kind", None) != "check_evaluate":
            continue
        if (getattr(tool, "mode", None) or "llm") != "rule":
            continue
        task_type = getattr(tool, "task_type", None)
        if task_type not in factor_ids:
            continue
        out.append({
            "rule_id": "FS-06",
            "location": f"agent_spec.tools_v2[{getattr(tool, 'name', '?')}]",
            "code": "rule_mode_check_cannot_score_a_factor",
            "reason": (
                f"tool {getattr(tool, 'name', '?')!r} is mode='rule' and its "
                f"task_type {task_type!r} is a DECLARED FACTOR. Rule mode returns "
                "a verdict (pass/flag/fail) and no number, so this factor would "
                "come back scoreless on every case and be dropped from the "
                "composite's denominator — silently, and differently from the "
                "rubric the customer signed off. Use mode='llm' so the model "
                "returns a score_fraction, or move it to factor_set.gates if it "
                "is genuinely a pass/fail limit."
            ),
        })
    return out


# ---------------------------------------------------------------------------
# CS-03 — a published case_signature cannot vanish on rebuild
# ---------------------------------------------------------------------------


def validate_case_signature_stable(app_spec, previous_app_spec) -> List[Dict[str, Any]]:
    """A rebuild may not silently drop a ``case_signature`` the app already had.

    Facet families ARE the retrieval key: a clause fires iff
    ``scope_facets ⊆ case_facets``. Lose the signature and every case derives
    ``case_facets: []``, so no scoped clause can ever match again — the app's
    learned knowledge is still sitting in the store reading `active`, and it is
    dead. Observed on acme-bank: ``dealer-limit-review`` v2 republished with
    ``case_signature: null`` and clause memory stopped, reported nowhere.

    This cannot live in Layer B — it needs the STORED spec, which only the
    publish handler has in hand. It is also not something the schema can catch:
    ``case_signature`` is legitimately optional, and an app that never had one
    is fine. What is not fine is HAVING one and then omitting it, because the
    builder re-authors the spec from scratch on a rebuild and an omitted
    optional field is indistinguishable from a deliberate removal.

    Deliberately removing it is still possible — through the hand-edit path,
    which is a human action. The agent cannot do it by forgetting. That
    asymmetry is the point: dropping a signature orphans everything the app has
    learned, so it should cost someone a decision, not a missing key.

    Only TOTAL loss is rejected. Changing the family set is left alone: publish
    already reconciles it, migrating a renamed family through
    ``FacetSpec.aliases`` and orphaning one that vanished, both visibly.
    """
    if not previous_app_spec:
        return []                                    # first publish; nothing to lose

    prev = (previous_app_spec or {}).get("case_signature") or {}
    prev_families = [f.get("family") for f in (prev.get("facets") or [])
                     if isinstance(f, dict) and f.get("family")]
    if not prev_families:
        return []                                    # never had one

    new_sig = getattr(app_spec, "case_signature", None)
    new_facets = getattr(new_sig, "facets", None) if new_sig is not None else None
    if new_facets:
        return []

    return [{
        "rule_id": "CS-03",
        "location": "app_spec.case_signature",
        "code": "case_signature_dropped_on_rebuild",
        "previous_families": sorted(set(prev_families)),
        "reason": (
            f"the published version declares a case_signature with "
            f"{len(set(prev_families))} facet famil(ies) "
            f"({', '.join(sorted(set(prev_families)))}) and this one declares "
            "none. A clause is retrieved iff its scope_facets are a subset of "
            "the case's facets, so every case would derive case_facets=[] and "
            "NO clause this app has learned could ever fire again. If you are "
            "rebuilding, copy case_signature across from SEED_APP_SPEC "
            "unchanged — it is load-bearing, not decoration. If a facet column "
            "moved, rename the family and declare the old name in that facet's "
            "`aliases` so the existing clauses migrate instead of going dark."
        ),
    }]


# ---------------------------------------------------------------------------
# CS-04 — the facet families must be confirmed by a human
# ---------------------------------------------------------------------------


def validate_case_signature_confirmed(app_spec) -> List[Dict[str, Any]]:
    """The families the BA agreed to must be the families that shipped.

    The families decide the SCOPE of every judgement the app will ever learn —
    retrieval is ``scope_facets ⊆ case_facets``, so whatever they are is what
    "cases like this one" means, permanently. The builder agent picks them from
    the SOP and the bound columns, and the BA is the only person who knows how
    their team actually groups cases. So the agent PROPOSES them in the build
    chat, the BA accepts or edits, and the agent implements the edit.

    What this rule checks is the last step of that: ``confirmed_families``
    records the list the BA accepted, and it must match the list actually
    declared. Proposing four families and shipping six certifies something
    nobody agreed to — and that is the failure worth catching, because the
    difference is invisible afterwards.

    Deliberately NOT an identity check. Who built an app is already answered by
    RBAC and the audit trail, and it is not the governed question anyway — who
    changes DATA is. Requiring the agent to also prove which human it spoke to
    added machinery around a problem the platform had already solved.

    So this is a light guard, not a gate, and it is honest about that: an agent
    determined to skip the conversation can set ``confirmed_families`` to match.
    What it cannot do is have the conversation, hear "drop LTV", and then ship
    LTV anyway. The behaviour that matters — propose, listen, implement — lives
    in the skill; this stops the one divergence that would otherwise be silent.

    An app with NO ``case_signature`` is unaffected: nothing to confirm.
    """
    sig = getattr(app_spec, "case_signature", None)
    if sig is None:
        return []
    declared = sorted({f.family for f in (getattr(sig, "facets", None) or [])
                       if getattr(f, "family", None)})
    if not declared:
        return []

    loc = "app_spec.case_signature"
    seen = getattr(sig, "confirmed_families", None)
    if not seen:
        return [{
            "rule_id": "CS-04",
            "location": f"{loc}.confirmed_families",
            "code": "case_signature_unconfirmed",
            "declared_families": declared,
            "reason": (
                f"this app declares {len(declared)} facet famil(ies) "
                f"({', '.join(declared)}) and there is no record that the BA "
                "agreed to them. These decide what 'cases like this one' means "
                "for every judgement the app will ever learn, so propose them "
                "in chat, let the BA accept or edit, implement whatever they "
                "change, and set confirmed_families to the list they accepted."
            ),
        }]

    confirmed = sorted({str(f) for f in seen if f})
    if confirmed != declared:
        added = sorted(set(declared) - set(confirmed))
        removed = sorted(set(confirmed) - set(declared))
        return [{
            "rule_id": "CS-04",
            "location": f"{loc}.confirmed_families",
            "code": "confirmed_families_mismatch",
            "declared_families": declared,
            "confirmed_families": confirmed,
            "reason": (
                f"the BA accepted {confirmed} but this spec declares "
                f"{declared}"
                + (f" — added since: {added}" if added else "")
                + (f" — dropped since: {removed}" if removed else "")
                + ". What shipped is not what was agreed. If the BA asked for "
                "the change, re-propose the new list and update "
                "confirmed_families; if they did not, put the families back."
            ),
        }]
    return []


# ---------------------------------------------------------------------------
# M-01 — an item tool must say what KIND of item it analyses
# ---------------------------------------------------------------------------

# The tool kinds whose findings are bucketed by ``task_type``. Each one analyses
# ONE item per call and folds the officer's verdict into memory keyed
# (tenant, app, modality, task_type) — see tools_v2_dispatch.py, where the same
# value is also written to the item ledger as ``item_type``.
_ITEM_TOOL_KINDS = ("image_analyze", "doc_extract", "check_evaluate")

# What the runtime falls back to when the field is absent (three sites in
# tools_v2_dispatch.py: `entry.get("task_type") or "generic"`). The models make
# the field required, so this value is only reachable by authoring it — which
# opts every item in the app into one shared bucket, deliberately.
_TASK_TYPE_FALLBACK = "generic"


def validate_item_tools_declare_task_type(agent_spec) -> List[Dict[str, Any]]:
    """Two item tools must not share one learning bucket, and none may claim the
    runtime's fallback name.

    ``task_type`` is not a label. It is the bucket key for everything the app
    learns about a KIND of item: the learned rubric, the cached SOP, the clause
    memory scope, and ``item_type`` on the item ledger are all keyed
    (tenant, app, modality, task_type). One tool per kind is the whole design —
    ten accident photos on one claim are ten calls sharing ONE bucket, which is
    why they also share one SOP fetch.

    PRESENCE IS ALREADY ENFORCED, and not here. ImageAnalyzeTool, DocExtractTool
    and CheckEvaluateTool each declare ``task_type: str`` with ``min_length=1``,
    so a spec that omits or empties it is rejected by the model layer before any
    of this runs. The empty-value branch below is a backstop for callers that
    reach a validator holding plain dicts, not the main event.

    What the model layer CANNOT see is the two failures this rule exists for:

      * ``"generic"`` — a perfectly valid non-empty string, and precisely the
        value the runtime substitutes when the field is absent
        (``entry.get("task_type") or "generic"``, three sites in
        tools_v2_dispatch.py). Authoring it by hand opts into the merged bucket
        deliberately, which is almost never what was meant.
      * a COLLISION — two tools carrying the same task_type. Pydantic validates
        each tool on its own and cannot compare across the list, so nothing else
        notices. Sharing is occasionally right (front and back of one form,
        taught together); usually it is a copy-paste, and the cost is silent:
        judgement learned about bank statements is retrieved when reviewing an
        accident photo, with no error anywhere and nothing an officer could
        report.

    The collision is reported on the SECOND tool and names the first, so the
    message describes the clash rather than blaming an arbitrary half of it.
    """
    if agent_spec is None:
        return []

    out: List[Dict[str, Any]] = []
    seen: Dict[str, str] = {}          # task_type -> first tool name that used it

    for tool in (getattr(agent_spec, "tools_v2", None) or []):
        kind = getattr(tool, "kind", None)
        if kind not in _ITEM_TOOL_KINDS:
            continue
        name = getattr(tool, "name", None) or "?"
        raw = getattr(tool, "task_type", None)
        task_type = (raw or "").strip()

        if not task_type or task_type.lower() == _TASK_TYPE_FALLBACK:
            out.append({
                "rule_id": "M-01",
                "location": f"agent_spec.tools_v2[{name}]",
                "code": "item_tool_missing_task_type",
                "reason": (
                    f"tool {name!r} ({kind}) does not declare a task_type"
                    + (f" (it is {task_type!r}, the runtime's fallback)" if task_type else "")
                    + ". task_type is the bucket key for this app's learned rubric, "
                    "its cached SOP, its clause-memory scope and item_type on the "
                    "item ledger. Without it every item this app analyses folds "
                    "into one bucket, so judgement learned about one kind of "
                    "document is retrieved when reviewing another — quietly, with "
                    "no error and no way for an officer to report it. Name the "
                    "kind of item this tool reads, e.g. 'bank-statement', "
                    "'accident-photo', 'damage-closeup'."
                ),
            })
            continue

        prior = seen.get(task_type)
        if prior and prior != name:
            out.append({
                "rule_id": "M-01",
                "location": f"agent_spec.tools_v2[{name}]",
                "code": "item_tools_share_task_type",
                "reason": (
                    f"tool {name!r} declares task_type {task_type!r}, already used "
                    f"by {prior!r}. They will share one rubric, one SOP and one "
                    "precedent pool. That is correct only if they genuinely read "
                    "the same KIND of item (front and back of one form); if they "
                    "read different kinds, give each its own task_type so what is "
                    "learned about one does not steer the other."
                ),
            })
        else:
            seen.setdefault(task_type, name)

    return out
