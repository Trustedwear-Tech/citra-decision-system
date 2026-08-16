"""Layer A — Builder-side static cross-spec checks.

Runs locally inside the builder pod BEFORE any LLM self-test or live
probe (Layers B/C). Catches the four classes of structural drift that
today only surface at runtime:

    1. Tool ↔ Catalogue match
       Every mcp_action tool's (source_id, dataset_id, action_id) triple
       must exist in /builder/catalogue. The action's input_schema must
       be the same shape the agent's system prompt is instructed to
       produce — we can only check the field names, not the semantics.

    2. Form ↔ Validator match
       Every validate_form tool's schema_ref must resolve to a real
       FormPanel in the AppSpec, and the panel's fields must be a
       superset of the schema's required[].

    3. Panel ↔ Data source match
       Every queue / dashboard panel's data_source must exist in
       AppSpec.data_sources[], and the source's type must be one the
       runtime knows how to render.

    4. Recommendation-inbox wiring
       If AppSpec declares a workflow_staging data source (the officer
       inbox), SOMETHING must feed it: the app's agent (on-demand /run) or
       an app trigger (schedule/webhook/poll precompute). Flag only when
       there is no producer at all — otherwise the BA sees a permanently
       empty inbox.

    + ADVISORY: queue-action record passing (the re-fetch smell). The runtime
      injects the clicked queue row into the agent's inputs, so an agent action
      fired from a queue button already HAS the record. If the agent also reads
      the same source, it risks re-fetching what it was handed (the #1 cause of
      slow/looping runs). Warning only — `record_passing_review` does NOT flip
      `passed`; the `/builder/smoke-run` gate hard-blocks an actual loop. Its job
      is to make the invisible data-flow visible at compose time.

The Python harness is intentionally dependency-free — runs under the
builder pod's stock interpreter. The skill invokes it via `exec`
(see ``citra-self-test/SKILL.md`` Steps 0a-0d).
"""
from __future__ import annotations

import json
import sys
from typing import Any, Dict, List, Optional, Tuple


def _load(path: str) -> Optional[Dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except json.JSONDecodeError as e:
        raise RuntimeError(f"{path} is not valid JSON: {e}") from e


# ---------------------------------------------------------------------------
# Check 1 — Tool ↔ Catalogue match
# ---------------------------------------------------------------------------


def check_mcp_action_tools_against_catalogue(
    agent_spec: Dict[str, Any],
    catalogue: Dict[str, Any],
) -> List[Dict[str, str]]:
    """Every mcp_action tool's (source_id, dataset_id, action_id) triple
    must exist in the catalogue. Returns a list of offending entries —
    empty list = pass.

    catalogue shape (from GET /builder/catalogue?full=true) is a FLAT list:
        {entries: [{source_id, dataset_id|ref, write_actions: [{id, ...}]}]}
    (The older nested {sources:[{datasets:[...]}]} form is tolerated too.)

    Each entry is keyed by BOTH its source-qualified ref
    ("field_operations.theft_cases") and its bare table name ("theft_cases"),
    because an mcp_action tool carries the BARE dataset_id while the catalogue
    entry carries the qualified ref — without this they'd never match and the
    check would false-flag every write action.
    """
    def _bare(x: Any) -> Any:
        return str(x).split(".")[-1] if x else x

    by_dataset: Dict[Tuple[str, str], Dict[str, Any]] = {}

    def _register(sid: Any, did: Any, ds: Dict[str, Any]) -> None:
        if sid and did:
            by_dataset[(sid, did)] = ds
            by_dataset[(sid, _bare(did))] = ds

    cat = catalogue or {}
    entries = cat.get("entries")
    if isinstance(entries, list):                      # current flat API shape
        for e in entries:
            if isinstance(e, dict):
                _register(e.get("source_id"), e.get("dataset_id") or e.get("ref"), e)
    else:                                              # legacy nested shape
        for src in cat.get("sources") or []:
            sid = src.get("source_id")
            for ds in (src or {}).get("datasets") or []:
                _register(sid, ds.get("dataset_id"), ds)

    errors: List[Dict[str, str]] = []
    for t in (agent_spec or {}).get("tools_v2") or []:
        if (t or {}).get("kind") != "mcp_action":
            continue
        sid = t.get("source_id")
        did = t.get("dataset_id")
        aid = t.get("action_id")
        if not (sid and did and aid):
            errors.append({
                "tool": t.get("id") or "?",
                "reason": "mcp_action missing source_id/dataset_id/action_id",
            })
            continue
        ds = by_dataset.get((sid, did)) or by_dataset.get((sid, _bare(did)))
        if ds is None:
            errors.append({
                "tool": t.get("id") or "?",
                "reason": f"dataset {sid}.{did} not found in catalogue",
            })
            continue
        actions = {a.get("id"): a for a in (ds.get("write_actions") or []) if isinstance(a, dict)}
        if aid not in actions:
            errors.append({
                "tool": t.get("id") or "?",
                "reason": (
                    f"action {aid!r} not declared on {sid}.{did} — "
                    f"catalogue has: {sorted(actions.keys()) or '[none]'}"
                ),
            })
    return errors


# ---------------------------------------------------------------------------
# Check 2 — Form ↔ Validator match
# ---------------------------------------------------------------------------


def _collect_form_panels(app_spec: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    by_id: Dict[str, Dict[str, Any]] = {}
    panels_top = (app_spec or {}).get("panels") or []
    for p in panels_top:
        if (p or {}).get("type") == "form":
            by_id[p.get("id") or ""] = p
    for page in (app_spec or {}).get("pages") or []:
        for p in (page or {}).get("panels") or []:
            if (p or {}).get("type") == "form":
                by_id[p.get("id") or ""] = p
    by_id.pop("", None)
    return by_id


def check_validate_form_tools_against_panels(
    agent_spec: Dict[str, Any],
    app_spec: Dict[str, Any],
) -> List[Dict[str, str]]:
    """Every validate_form tool's schema_ref must resolve to a real
    FormPanel; the panel's fields must include every schema.required[]
    entry."""
    forms = _collect_form_panels(app_spec)
    errors: List[Dict[str, str]] = []
    for t in (agent_spec or {}).get("tools_v2") or []:
        if (t or {}).get("kind") != "validate_form":
            continue
        ref = t.get("schema_ref") or t.get("id") or ""
        if ref not in forms:
            errors.append({
                "tool": t.get("id") or "?",
                "reason": f"schema_ref {ref!r} does not match any form panel",
            })
            continue
        panel = forms[ref]
        panel_field_names = {
            f.get("name") for f in (panel.get("fields") or [])
            if isinstance(f, dict) and f.get("name")
        }
        required = (t.get("schema") or {}).get("required") or []
        missing = [r for r in required if r not in panel_field_names]
        if missing:
            errors.append({
                "tool": t.get("id") or "?",
                "reason": (
                    f"panel {ref!r} is missing required fields {missing} "
                    f"declared by the validator schema"
                ),
            })
    return errors


# ---------------------------------------------------------------------------
# Check 3 — Panel ↔ Data source match
# ---------------------------------------------------------------------------


# The top-level Panel types (must match models.Panel union + the schema
# Panel oneOf consts). NOT detail-section types — attachment/fields/documents/
# approval/agent_timeline live INSIDE a `detail` panel and are never walked here.
KNOWN_PANEL_TYPES: set = {
    "queue", "form", "detail", "dashboard", "chart", "agent_chat",
    "document_view", "markdown", "notice", "calendar", "map", "filter_bar",
    "notifications",
    # Designed panels (runtime-ui-modernization-plan.md U3).
    "hero", "stat_strip", "timeline",
}
# The real DataSource types (must match models.DataSource.type Literal).
# 'workflow' is retired — there is no workflow engine behind a SmartApp.
KNOWN_DS_TYPES: set = {
    "mcp", "rag", "static", "smart_app_records", "workflow_staging",
    # The app's own decision ledger — ROI/KPI pages bind here (money-saved
    # plan V3); never recompute money from raw sources.
    "decision_ledger",
}


def check_panel_data_sources(app_spec: Dict[str, Any]) -> List[Dict[str, str]]:
    """Every panel that declares a data_source must reference one that
    exists in AppSpec.data_sources, and the source type must be one
    the runtime recognises."""
    sources = {
        (s or {}).get("id"): (s or {}).get("type")
        for s in ((app_spec or {}).get("data_sources") or [])
        if isinstance(s, dict)
    }
    errors: List[Dict[str, str]] = []

    def _walk(panels: List[Dict[str, Any]], page_id: str = "") -> None:
        for p in panels or []:
            if not isinstance(p, dict):
                continue
            ptype = p.get("type")
            if ptype and ptype not in KNOWN_PANEL_TYPES:
                errors.append({
                    "panel": p.get("id") or "?",
                    "page": page_id,
                    "reason": f"unknown panel type {ptype!r}",
                })
            ds = p.get("data_source")
            if not ds:
                continue
            if ds not in sources:
                errors.append({
                    "panel": p.get("id") or "?",
                    "page": page_id,
                    "reason": f"data_source {ds!r} not declared in AppSpec.data_sources",
                })
                continue
            stype = sources[ds]
            if stype not in KNOWN_DS_TYPES:
                errors.append({
                    "panel": p.get("id") or "?",
                    "page": page_id,
                    "reason": f"data_source {ds!r} type {stype!r} is not a known runtime kind",
                })

    _walk((app_spec or {}).get("panels") or [])
    for page in (app_spec or {}).get("pages") or []:
        _walk((page or {}).get("panels") or [], page_id=(page or {}).get("id") or "")
    return errors


# ---------------------------------------------------------------------------
# Check 4 — Workflow ↔ AppSpec wiring
# ---------------------------------------------------------------------------


def check_workflow_staging_wiring(
    app_spec: Dict[str, Any],
) -> List[Dict[str, str]]:
    """If the AppSpec declares a ``workflow_staging`` data source (the officer
    recommendation inbox), SOMETHING must feed it. The inbox is fed by the
    app's own agent — on-demand (`/apps/{slug}/run` stages a recommendation)
    and/or precomputed via an **app trigger** (`app_spec.triggers[]` fires the
    agent ahead of the click). So the panel is wired as long as the app has an
    ``agent_id`` (on-demand always available) OR declares ``triggers``. Only
    flag when there is NO producer at all.
    """
    sources = (app_spec or {}).get("data_sources") or []
    staging_sources = [
        s for s in sources
        if isinstance(s, dict) and s.get("type") == "workflow_staging"
    ]
    if not staging_sources:
        return []

    # Producer 1: the app's agent (on-demand /run stages on every pending plan).
    if (app_spec or {}).get("agent_id"):
        return []
    # Producer 2: an app trigger precomputes recommendations into the inbox.
    if (app_spec or {}).get("triggers"):
        return []

    return [{
        "data_source": staging_sources[0].get("id") or "?",
        "reason": (
            "AppSpec declares a workflow_staging data source but nothing feeds "
            "it — the app has no agent_id (no on-demand recommendation) and no "
            "app trigger to precompute one. Add an agent action, or an app "
            "trigger (schedule/webhook/poll), so the queue panel isn't empty."
        ),
    }]


# ---------------------------------------------------------------------------
# Check 5 — neighbor_samples collection is well-formed
# ---------------------------------------------------------------------------


def check_neighbor_samples_collection_matches_sink(
    agent_spec: Dict[str, Any],
) -> List[Dict[str, str]]:
    """Every ``neighbor_samples`` tool must declare ``collection`` ==
    ``Historical_Refresh`` — the ONE shared grounding collection for all
    agents (rows isolated by an ``agent_id`` field). Mirrors publish rule
    G-01.

    Without the right collection the runtime retrieves from an empty/foreign
    collection and silently loses its few-shot grounding, falling back to its
    generic prior with no error.
    """
    EXPECTED = "Historical_Refresh"
    tools = [
        t for t in (agent_spec or {}).get("tools_v2") or []
        if isinstance(t, dict) and t.get("kind") == "neighbor_samples"
    ]
    if not tools:
        return []

    errors: List[Dict[str, str]] = []
    for t in tools:
        tool_name = t.get("name") or t.get("id") or "?"
        collection = t.get("collection")
        if not collection:
            errors.append({
                "tool": tool_name,
                "reason": f"neighbor_samples tool is missing 'collection' — set it to '{EXPECTED}'",
            })
        elif str(collection) != EXPECTED:
            errors.append({
                "tool": tool_name,
                "reason": (
                    f"neighbor_samples collection {collection!r} must be {EXPECTED!r} — "
                    "grounding uses one shared collection for all agents (rows isolated "
                    "by agent_id), which is also where the refresh writes."
                ),
            })
    return errors


# ---------------------------------------------------------------------------
# Check 6 — code_exec / vision_ocr pre-flight (Layer A mirror of publish gates)
# ---------------------------------------------------------------------------


def check_code_exec_and_ocr_tools(
    agent_spec: Dict[str, Any],
) -> List[Dict[str, str]]:
    """Pre-flight the two cost/UX-sensitive tool kinds at build time.

    code_exec (per citra-code-exec Hard Rules): must carry a 'description'
    (the prescription the runtime LLM authors the script from) and a
    non-empty 'allowed_outputs' list (the UI gate the panel uses to render
    the download).

    vision_ocr (per citra-ocr cost-gate, mirrors the AgentSpec validator):
    a 'validate_form' tool MUST appear in tools_v2 BEFORE vision_ocr, and
    the system_prompt MUST mention 'validate_form' so the runtime registers
    the cheap-check-first sequence that short-circuits expensive OCR calls.
    """
    tools = [
        t for t in (agent_spec or {}).get("tools_v2") or []
        if isinstance(t, dict)
    ]
    errors: List[Dict[str, str]] = []

    for t in tools:
        if t.get("kind") != "code_exec":
            continue
        tool_name = t.get("name") or t.get("id") or "?"
        if not (t.get("description") or "").strip():
            errors.append({
                "tool": tool_name,
                "reason": (
                    "code_exec tool has no 'description' — the runtime LLM has no "
                    "prescription to author the script from. Describe inputs, output "
                    "filename, and that the download_url must be echoed in the reply."
                ),
            })
        outputs = t.get("allowed_outputs")
        if not isinstance(outputs, list) or not outputs:
            errors.append({
                "tool": tool_name,
                "reason": (
                    "code_exec tool is missing a non-empty 'allowed_outputs' list — "
                    "the panel uses it as the UI gate to render the generated file "
                    "(e.g. [\"pdf\"], [\"xlsx\"])."
                ),
            })

    kinds_in_order = [t.get("kind") for t in tools]
    if "vision_ocr" in kinds_in_order:
        if "validate_form" not in kinds_in_order:
            errors.append({
                "tool": "vision_ocr",
                "reason": (
                    "vision_ocr is declared but no 'validate_form' tool precedes it. "
                    "validate_form (free, deterministic) must run first to short-circuit "
                    "before any paid OCR call — add a validate_form tool before vision_ocr."
                ),
            })
        elif kinds_in_order.index("validate_form") > kinds_in_order.index("vision_ocr"):
            errors.append({
                "tool": "vision_ocr",
                "reason": (
                    "'validate_form' appears AFTER 'vision_ocr' in tools_v2 — it must come "
                    "first so the cost-gate sequence (validate cheaply, then OCR) is registered."
                ),
            })
        system_prompt = (agent_spec or {}).get("system_prompt") or ""
        if "validate_form" not in system_prompt.lower():
            errors.append({
                "tool": "vision_ocr",
                "reason": (
                    "agent_spec.system_prompt does not mention 'validate_form'. The prompt "
                    "must instruct the agent to call validate_form FIRST and skip vision_ocr "
                    "when it returns ok=false (see citra-ocr cost gate)."
                ),
            })
    return errors


# ---------------------------------------------------------------------------
# Check 7 (ADVISORY) — queue-action record passing (the re-fetch smell)
# ---------------------------------------------------------------------------


def check_queue_action_record_passing(
    app_spec: Dict[str, Any], agent_spec: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """ADVISORY. Make the invisible data-flow visible at compose time.

    At runtime, clicking a queue-row button sends that ROW to the agent
    (PanelRenderer ``fireAction`` → ``inputs = {...row}``) and the runtime
    injects it into the agent's prompt. So an agent action fired from a queue
    button ALREADY HAS the selected record — it must NOT re-fetch it. The #1
    cause of slow / looping runs is a `system_prompt` that re-reads the record
    it was handed.

    This is a *behavior* the spec doesn't express, so we can't prove it
    statically — but we can flag the structural SMELL: the agent registers an
    `mcp` read tool on the SAME source the queue reads from. That read is at
    risk of re-fetching the provided record. Advisory (warn) only — reading the
    same source for OTHER rows is legitimate; the `/builder/smoke-run` gate
    hard-blocks an actual observed loop. The point here is to make the builder
    SEE, at compose time, that the record is already provided."""
    findings: List[Dict[str, Any]] = []
    action_names = {a.get("name") for a in (agent_spec.get("actions") or [])}
    # mcp (NL) read tools grouped by the source they read.
    reads_by_source: Dict[Any, List[Any]] = {}
    for t in (agent_spec.get("tools_v2") or []):
        if t.get("kind") == "mcp":
            reads_by_source.setdefault(t.get("source_id"), []).append(t.get("name"))
    ds_by_id = {d.get("id"): d for d in (app_spec.get("data_sources") or [])}

    def _source_of(ds_id: Any) -> Optional[str]:
        ref = (ds_by_id.get(ds_id) or {}).get("ref") or ""
        return ref.split(".", 1)[0].strip() if ref else None

    def _scan(panels: Any) -> None:
        for p in (panels or []):
            # queue AND detail: a detail panel can fire an agent action too
            # (DetailPanel.actions), and an embed card does exactly that with no
            # queue anywhere. Scanning queues only would silently drop this
            # warning for every embed — the surface where it matters MOST,
            # because the host hands the record in and a re-fetch is pure waste.
            if p.get("type") not in ("queue", "detail"):
                continue
            qsrc = _source_of(p.get("data_source"))
            if not qsrc or qsrc not in reads_by_source:
                continue
            for a in (p.get("actions") or []):
                an = a.get("agent_action")
                if an and an in action_names:
                    findings.append({
                        "severity": "warn",
                        "panel": p.get("id"),
                        "action": an,
                        "source": qsrc,
                        "msg": (
                            f"{p.get('type')} '{p.get('id')}' passes a '{qsrc}' record into action "
                            f"'{an}' (the runtime injects that record into the agent's "
                            f"inputs), yet the agent also has read tool(s) "
                            f"{reads_by_source[qsrc]} on '{qsrc}' — risk of re-fetching the "
                            f"record it was already handed."
                        ),
                        "likely_fix": (
                            f"use the record already in `inputs`; call the '{qsrc}' read only "
                            f"for OTHER rows (scoped by id) or drop it. Write the system_prompt "
                            f"per citra-agent-spec 'Write FAST system prompts'."
                        ),
                    })
    _scan(app_spec.get("panels"))
    for page in (app_spec.get("pages") or []):
        _scan(page.get("panels"))
    return findings


# ---------------------------------------------------------------------------
# CLI entry — used by the self-test skill via `exec`
# ---------------------------------------------------------------------------


def check_decision_queue_has_a_detail(app_spec: Dict[str, Any]) -> List[Dict[str, str]]:
    """Warn when a queue fires an agent action and the page has no detail panel.

    The officer then decides from the queue's columns plus the run-result modal,
    never seeing the record itself. That is a legitimate app — the modal carries
    the recommendation, the reasoning and the approve/reject — so this WARNS and
    never blocks.

    It exists because the builder kept promising the panel and then not
    authoring it. Twice, verbatim: "a clear layout — queue → detail → agent
    review", then a page with the queue alone. AGENTS.md was given an explicit
    "author every panel you promised" rule and the next run did it again, so
    prose is evidently not the lever. This check is: static_check_results.json
    is written before publish and the builder reads it.

    Deliberately NOT fired for:
      * an embed page — it is detail-only by design and has no queue;
      * a dashboard page — queues and details are not allowed there;
      * a headless app — no panels at all.
    Each of those is a correct shape that a naive "every queue needs a detail"
    rule would flag, and a check that cries wolf is one the builder learns to
    skip.
    """
    findings: List[Dict[str, str]] = []

    def _scan(panels: Any, page_id: Any, page_kind: Any) -> None:
        if page_kind in ("embed", "dashboard"):
            return
        panels = panels or []
        if any(p.get("type") == "detail" for p in panels):
            return
        for p in panels:
            if p.get("type") != "queue":
                continue
            for a in (p.get("actions") or []):
                if not a.get("agent_action"):
                    continue
                findings.append({
                    "severity": "warn",
                    "panel": p.get("id"),
                    "action": a.get("agent_action"),
                    "page": page_id,
                    "msg": (
                        f"queue '{p.get('id')}' runs '{a.get('agent_action')}' but "
                        f"page '{page_id}' has no detail panel — the officer "
                        "decides from the queue columns and the result modal "
                        "without ever seeing the record."
                    ),
                    "likely_fix": (
                        "add a detail panel linked_to this queue (the usual "
                        "queue -> detail drill-down), OR, if a queue-only app is "
                        "genuinely what you described to the BA, leave it and "
                        "say so. Do not describe a detail panel to the BA and "
                        "then omit it."
                    ),
                })
                return  # one finding per page is enough

    _scan(app_spec.get("panels"), "(single-page)", None)
    for page in (app_spec.get("pages") or []):
        _scan(page.get("panels"), page.get("id"), page.get("kind"))
    return findings


def run_all(
    app_spec_path: str = "/workspace/build/app_spec.json",
    agent_spec_path: str = "/workspace/build/agent_spec.json",
    catalogue_path: str = "/workspace/build/catalogue.json",
) -> Dict[str, Any]:
    _app_raw = _load(app_spec_path)
    app_spec = _app_raw or {}
    agent_spec = _load(agent_spec_path) or {}
    catalogue = _load(catalogue_path) or {}

    # SIX of the checks below read the APP spec. `_load` returns None for a
    # missing file and `or {}` turned that into an empty dict, so each of them
    # dutifully found nothing wrong with nothing and the harness reported
    # passed:true — having verified nothing about the app at all.
    #
    # That is not hypothetical. citra-self-test is filed as a PHASE 2 skill
    # (AGENTS.md phase table: Phase 2 emits agent_spec.json; app_spec.json is
    # not authored until Phase 3.5), so running the gate where the skill says to
    # run it means running it BEFORE the app spec exists. Observed across four
    # builder sessions: sometimes the file was there, sometimes it was not, and
    # the results looked equally green either way.
    #
    # Silent-pass is the failure mode RULE #1 exists to prevent, so say it.
    app_spec_missing = _app_raw is None

    results = {
        "tool_catalogue_match": check_mcp_action_tools_against_catalogue(
            agent_spec, catalogue,
        ),
        "form_validator_match": check_validate_form_tools_against_panels(
            agent_spec, app_spec,
        ),
        "panel_data_source_match": check_panel_data_sources(app_spec),
        "workflow_staging_wiring": check_workflow_staging_wiring(app_spec),
        "neighbor_samples_collection_match": check_neighbor_samples_collection_matches_sink(
            agent_spec,
        ),
        "code_exec_ocr_preflight": check_code_exec_and_ocr_tools(
            agent_spec,
        ),
        # ADVISORY (warn) — does NOT flip `passed`; the smoke-run gate is the
        # hard block for an actual loop. Surfaces the re-fetch smell at compose.
        "record_passing_review": check_queue_action_record_passing(
            app_spec, agent_spec,
        ),
        # ADVISORY — a queue-only decision app is legitimate (the modal carries
        # the decision), so this must never block. It is here because the
        # builder repeatedly PROMISED a detail panel and then omitted it, and a
        # rule in AGENTS.md did not change that.
        "decision_queue_detail": check_decision_queue_has_a_detail(app_spec),
    }
    _ADVISORY = {"record_passing_review", "decision_queue_detail"}
    results["passed"] = all(
        len(v) == 0
        for k, v in results.items()
        if isinstance(v, list) and k not in _ADVISORY
    )
    # Say plainly which half actually ran. `passed:true` with no app spec means
    # "the agent-side checks passed and the app was never examined" — six checks
    # short of what the caller thinks it just got.
    results["app_spec_checked"] = not app_spec_missing
    if app_spec_missing:
        results["passed"] = False
        results["app_spec_missing"] = [{
            "severity": "fail",
            "path": app_spec_path,
            "msg": (
                f"{app_spec_path} does not exist, so the six app-spec checks "
                "(panel data sources, form validators, workflow staging, "
                "record passing, decision-queue detail) examined NOTHING. "
                "Re-run this gate AFTER Phase 3.5 has authored the AppSpec."
            ),
            "likely_fix": (
                "run static_checks.py again once app_spec.json exists — before "
                "/publish, not during Phase 2."
            ),
        }]
    return results


if __name__ == "__main__":
    result = run_all()
    print(json.dumps(result, indent=2, ensure_ascii=False))
    sys.exit(0 if result["passed"] else 1)
