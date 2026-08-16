"""Smart App Service — Pydantic models for AppSpec, AgentSpec, build sessions."""

import logging
from datetime import datetime
from enum import Enum
from typing import Annotated, Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Common
# ---------------------------------------------------------------------------

# Builder-chosen model tier per decision complexity. Canonical: large |
# medium | small (resolved to LLM_LARGE_*/MEDIUM_*/SMALL_* from env). Legacy
# tier_a/b/c are accepted and all map to LARGE. Default is large (safe) — a
# small model misreading a decision is the dangerous case, so stepping down is
# an explicit builder choice.
ModelTier = Literal["large", "medium", "small", "tier_a", "tier_b", "tier_c"]
ClassificationLevel = Literal["public", "internal", "confidential", "restricted"]
SpecVersion = Literal["v0"]
# Artefact kinds. There is NO 'dashboard' kind — a dashboard is a kind='app'
# with one page whose page.kind == 'dashboard' (see PageKind below). Legacy
# specs carrying kind='dashboard' are coerced to the new shape by
# AppSpec._coerce_legacy_dashboard_kind (logged deprecation).
AppKind = Literal["app"]
# BuildKind controls which path the builder pod takes during a session.
# SmartApps build an 'app' only (a dashboard is an 'app' whose primary page is
# a dashboard page — see primary_page_kind). The legacy single-string field on
# BuildRequest still works and is normalised to a 1-element list.
BuildKind = Literal["app"]


class AppStatus(str, Enum):
    DRAFT = "draft"
    PUBLISHED = "published"
    ARCHIVED = "archived"


class BuildSessionStatus(str, Enum):
    ACTIVE = "active"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"


# ---------------------------------------------------------------------------
# AgentSpec
# ---------------------------------------------------------------------------


class RagBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ref: str
    doc_types: List[str] = Field(default_factory=list)
    classification_max: Optional[ClassificationLevel] = None


class SubAgent(BaseModel):
    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    role: str
    system_prompt: str = Field(min_length=1)
    model_tier: Optional[ModelTier] = None
    tools: List[str] = Field(default_factory=list)
    mcps: List[str] = Field(default_factory=list)
    rag: List[RagBinding] = Field(default_factory=list)
    output_schema: Optional[Dict[str, Any]] = Field(
        default=None,
        description=(
            "Optional JSON schema for the sub-agent's structured return value."
        ),
    )
    max_tool_calls: Optional[int] = Field(
        default=None,
        ge=1,
        le=20,
        description="Hard cap on tool-call iterations inside this sub-agent.",
    )


# --- Post-action / failure / triggers ---------------------------------------


class PostAction(BaseModel):
    """A side-effect to perform after a run resolves (approve / reject /
    auto-decision). Each entry is a tool call into a registered MCP — never
    arbitrary code. The runtime resolves ``tool`` against the executing
    agent's tool list; unknown tools are surfaced as a gap.
    """

    model_config = ConfigDict(extra="forbid")

    tool: str = Field(min_length=1, max_length=200)
    args: Dict[str, Any] = Field(default_factory=dict)
    description: Optional[str] = Field(default=None, max_length=500)


class FailurePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    on_tool_error: Literal["retry", "park", "escalate", "fail"] = "retry"
    max_retries: int = Field(default=2, ge=0, le=5)
    park_collection: Optional[str] = Field(
        default=None,
        description="Reserved: name of the Mongo collection runs are parked in.",
    )


# ---------------------------------------------------------------------------
# Composable input controls — FieldSpec / OptionsSource
# ---------------------------------------------------------------------------
# FieldSpec / OptionsSource is the rich input primitive for the plan-then-apply
# OVERRIDE modal (Action.editable_fields) — the officer edits an LLM-proposed
# write before Approve. It is NOT what form panels use: a FormPanel carries raw
# JSON-Schema (schema_inline / schema_ref) rendered by the form renderer's own
# field-control inference. The two input systems are separate.
#
# Form fields express controls in JSON-Schema (and an `options_source` key for a
# dynamic dropdown — resolved server-side by /apps/{slug}/field-options, same
# resolver OptionsSource uses):
#   text          → type:"string"
#   textarea      → format:"textarea"
#   number        → type:"number" / "integer"
#   checkbox      → type:"boolean"
#   date/datetime → format:"date" / "date-time"
#   currency      → type:"number" + format:"currency"   (rendered)
#   time          → format:"time"                        (rendered)
#   select        → enum:[...]                     (static dropdown)
#   radio         → enum:[...] + format:"radio"
#   multiselect   → type:"array" (items.enum:[...])
#   dynamic combo → options_source:{kind:"data_source", data_source, value_column, label_column, filter?, limit?}
#   file upload   → format:"file" (+ accepted_types, multiple) → base64 blob into the write column
# View/download a stored file: a detail-panel section type="attachment" with
# fields:[<file column>]. Builder emits file controls ONLY when the dataset/
# tool contract advertises a file column — the MCP is the worker that stores/
# serves the blob. NOT yet rendered on forms: lookup, hidden, toggle —
# see citra-app-spec "Files & documents".

ControlType = Literal[
    "text", "textarea", "number", "currency", "date", "datetime", "time",
    "select", "multiselect", "radio", "lookup", "checkbox", "toggle", "hidden",
]


class OptionItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: Any
    label: Optional[str] = None


class OptionsSource(BaseModel):
    """Where a select / multiselect / lookup field gets its choices.

    - ``static``      : the literal ``values`` list (resolved client-side).
    - ``data_source`` : live DISTINCT ``value_column`` (+ optional
      ``label_column``) from a declared ``data_source``, optionally scoped by
      ``filter`` (supports ``${record.<col>}`` / ``${param.<name>}``
      interpolation). Resolved server-side via the options endpoint — this is
      what "prepopulates the combo so the officer can pick any option".
    - ``agent``       : candidate options the LLM returns in the plan output
      under ``_options.<field>`` (e.g. "top 3 JEs for this case").
    """
    model_config = ConfigDict(extra="forbid")

    kind: Literal["static", "data_source", "agent"] = "static"
    values: List[OptionItem] = Field(default_factory=list)
    data_source: Optional[str] = None
    value_column: Optional[str] = None
    label_column: Optional[str] = None
    filter: Optional[Dict[str, Any]] = None
    limit: int = Field(default=200, ge=1, le=1000)
    # Typeahead mode (kind="data_source" only): render a search-as-you-type
    # combobox instead of a plain <select>. The runtime debounces the officer's
    # text to the options endpoint (?q=) so a high-cardinality dimension (every
    # consumer id, every meter no.) stays usable. Ignored for static/agent.
    search: bool = False


class FieldSpec(BaseModel):
    """A composable, schema-driven input control.

    ``control`` may be omitted — the runtime infers it from the action's
    ``input_schema`` (enum→select, date→date, boolean→toggle, …). The action's
    ``input_schema`` stays the HARD validation contract; FieldSpec is the
    PRESENTATION + options contract (spec/render separation).
    """
    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=r"^[a-zA-Z_][a-zA-Z0-9_]*$")
    label: Optional[str] = None
    control: Optional[ControlType] = None
    required: bool = False
    default: Optional[Any] = None
    placeholder: Optional[str] = None
    help: Optional[str] = None
    # For Action.editable_fields: may the officer change this proposed value?
    editable: bool = True
    options: Optional[OptionsSource] = None
    # Light presentation-side validation hints (input_schema is authoritative).
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    pattern: Optional[str] = None
    # Empty = any approver may edit; else restrict the override to these roles.
    editable_by_roles: List[str] = Field(default_factory=list)


class Action(BaseModel):
    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    name: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    description: Optional[str] = None
    # Per-decision model tier (builder-chosen by complexity). None = inherit
    # the AgentSpec default (large). Set 'medium'/'small' ONLY for genuinely
    # simple/deterministic decisions; keep 'large' for anything where a
    # misread would be costly (destructive/financial/legal/ambiguous).
    model_tier: Optional[ModelTier] = None
    delegates_to: List[str] = Field(default_factory=list)
    approval_required: bool = False
    input_schema: Optional[Dict[str, Any]] = None
    output_schema: Optional[Dict[str, Any]] = None
    # Payload fields the officer may OVERRIDE in the plan-then-apply modal
    # (e.g. reassign 'assigned_to' from a prepopulated list). Each must name a
    # field present in input_schema. The LLM's planned value becomes the
    # control default; the officer can change it; Approve re-validates the
    # edited payload via dry_run before committing, and audits the delta.
    editable_fields: List["FieldSpec"] = Field(default_factory=list)
    on_approve: List[PostAction] = Field(default_factory=list)
    on_reject: List[PostAction] = Field(default_factory=list)
    failure_policy: Optional[FailurePolicy] = None
    data_bindings: Optional["DataBindings"] = Field(
        default=None,
        description=(
            "Datasets and write_actions this action is permitted to touch. "
            "Validated against data-discovery-service catalogue at publish time. "
            "At runtime the executor exposes synthetic `query_dataset` and "
            "`perform_action` tools scoped to these bindings."
        ),
    )
    anchor_read: Optional["AnchorRead"] = Field(
        default=None,
        description=(
            "The base record this action's decision is ABOUT, keyed by a "
            "caller-supplied input id. When set, the runtime deterministically "
            "reads that one record (exact-filter, server-side) BEFORE the LLM "
            "loop and injects it as context — so the base record is always "
            "grounded without relying on the agent to fetch it. The "
            "read-before-write guard also enforces it was seen. Leave unset for "
            "create-from-scratch actions that reference no existing record."
        ),
    )


class AnchorRead(BaseModel):
    """Maps a run-input id to the one base record the runtime pre-reads.

    ``key_field`` is BOTH the property name in the action's ``input_schema``
    that carries the id AND the key column of ``dataset_id`` used for the
    exact-filter, single-row read.
    """
    model_config = ConfigDict(extra="forbid")

    source_id: str
    dataset_id: str
    key_field: str
    kind: Literal["sql", "odata", "soql"] = Field(
        default="sql",
        description=(
            "Structured read plane of the source, for the deterministic "
            "read-by-key. Must be a structured kind — the anchor read is never "
            "the NL planner (a decision's base record cannot depend on it)."
        ),
    )


class DataBindingRead(BaseModel):
    """Declares a dataset this action may read from."""
    model_config = ConfigDict(extra="forbid")

    source_id: str
    dataset_id: str
    columns: List[str] = Field(default_factory=list)
    filter: Optional[str] = Field(
        default=None,
        description=(
            "Optional natural-language hint or SQL WHERE fragment the LLM "
            "may use as a default. The runtime treats this as advisory — "
            "the LLM still emits the final query."
        ),
    )
    redact_pii: bool = Field(
        default=True,
        description=(
            "When True, columns flagged `pii=True` in the catalogue are "
            "redacted before rows are returned to the LLM. Set False only "
            "with an explicit policy override."
        ),
    )


class DataBindingWrite(BaseModel):
    """Declares a write_action this action may invoke."""
    model_config = ConfigDict(extra="forbid")

    source_id: str
    dataset_id: str
    action_id: str


class DataBindings(BaseModel):
    """What datasets / write_actions an Action is permitted to touch."""
    model_config = ConfigDict(extra="forbid")

    reads: List[DataBindingRead] = Field(default_factory=list)
    writes: List[DataBindingWrite] = Field(default_factory=list)


class DatasetDirectoryColumn(BaseModel):
    """Trimmed catalogue column projected onto the directory.

    The full ``data-discovery-service`` ``CatalogueColumn`` has 15+ fields
    (mapping_source, mapping_confidence, foreign refs, etc.) that the
    runtime agent does not need. We carry only what the LLM uses to
    decide what to query and how to redact: the display name, the
    semantic / physical type, the PII flag, and (when present) a short
    description / value-set hint.
    """
    model_config = ConfigDict(extra="forbid")

    name: str
    type: str
    semantic_type: Optional[str] = None
    pii: bool = False
    description: Optional[str] = Field(default=None, max_length=300)


class DatasetDirectoryWriteAction(BaseModel):
    """Trimmed catalogue write-action projected onto the directory."""
    model_config = ConfigDict(extra="forbid")

    id: str
    verb: str
    description: Optional[str] = Field(default=None, max_length=300)


class DatasetDirectoryEntry(BaseModel):
    """Resolved dataset ↔ tool relationship, hydrated at publish time.

    Smart-app's primary navigation surface is the dataset catalogue.
    Actions reference ``(source_id, dataset_id)`` via DataBindings; the
    runtime agent needs zero-lookup access to "given this dataset_id:
    which MCP tool serves it, what columns does it have, is any of it
    PII, what write actions are available?"

    The directory is a denormalised snapshot built at publish time from
    TWO upstream registries:

      * **discovery-service** — source-level metadata (source name,
        taxonomy_summary, source_type, query_timeout).
      * **data-discovery-service** — dataset-level metadata (dataset
        name + description, kind, columns, has_pii, row_count_approx,
        write_actions, mcp_base_url).

    Both refresh on every republish, so a column added by the crawler
    or a tool endpoint rotation lands in the next publish — the
    directory ages with the AppSpec, not behind it.
    """
    model_config = ConfigDict(extra="forbid")

    # ── identity ──
    dataset_id: str = Field(
        ...,
        description=(
            "The catalogue dataset key the action references. Matches "
            "DataBindingRead.dataset_id."
        ),
    )
    source_id: str = Field(
        ...,
        description="discovery-service source_id that owns this dataset.",
    )

    # ── source-level (from discovery-service) ──
    source_name: str = Field(
        ...,
        description="Human-readable tool name from discovery-service.",
    )
    source_description: Optional[str] = Field(
        default=None,
        max_length=2000,
        description="Description from the discovery registration.",
    )
    source_type: Optional[str] = Field(
        default=None,
        description="One of: semantic | structured | mongodb | rest_api.",
    )
    tags: List[str] = Field(default_factory=list)
    data_types: List[str] = Field(default_factory=list)
    taxonomy_summary: Optional[str] = Field(
        default=None,
        max_length=500,
        description=(
            "One-line summary of doc_types from the source taxonomy "
            "(e.g. 'invoices, receipts, statements'). Empty for "
            "non-semantic sources."
        ),
    )
    query_timeout_seconds: Optional[int] = None

    # ── dataset-level (from data-discovery-service catalogue) ──
    dataset_name: Optional[str] = Field(
        default=None,
        max_length=200,
        description=(
            "Display name for THIS dataset (e.g. 'Claims Staging Q4 "
            "2025'). Falls back to source_name when the catalogue is "
            "unreachable at publish time."
        ),
    )
    dataset_description: Optional[str] = Field(
        default=None,
        max_length=2000,
        description="Description of this dataset from the catalogue.",
    )
    kind: Optional[str] = Field(
        default=None,
        description=(
            "Dataset kind from data-discovery-service: sql | odata | "
            "soql | rest | semantic | mongodb. Drives which dispatch "
            "shape the runtime uses."
        ),
    )
    columns: List[DatasetDirectoryColumn] = Field(
        default_factory=list,
        description=(
            "Trimmed column list with semantic_type and pii flag. The "
            "runtime LLM picks columns from here without round-tripping "
            "to the catalogue. PII flags drive redaction at "
            "dispatch_query_dataset."
        ),
    )
    has_pii: bool = Field(
        default=False,
        description=(
            "True when any column is flagged pii. Surfaced to the agent "
            "so it knows redaction is in effect for this dataset."
        ),
    )
    row_count_approx: Optional[int] = Field(
        default=None,
        description="Approximate row count from the crawler.",
    )
    write_actions: List[DatasetDirectoryWriteAction] = Field(
        default_factory=list,
        description=(
            "Write actions exposed by this dataset (e.g. 'create_claim', "
            "'close_ticket'). The runtime's synthetic perform_action "
            "tool dispatches against these."
        ),
    )
    mcp_base_url: Optional[str] = Field(
        default=None,
        description=(
            "Dept-MCP base URL (host:port, no path). When present the "
            "runtime hits ``{mcp_base_url}/query`` directly without "
            "needing the catalogue's ``read_via.target``."
        ),
    )

    # ── access (computed from bindings, not upstream) ──
    access: Literal["read", "write", "read_write"] = Field(
        default="read",
        description=(
            "Directionality of the binding(s) that reference this "
            "dataset. read = at least one DataBindingRead; write = at "
            "least one DataBindingWrite; read_write = both."
        ),
    )


class LearningLogEntry(BaseModel):
    timestamp: datetime
    phase: Literal["intern", "expert", "compose", "deploy"]
    note: str


# ---------------------------------------------------------------------------
# Agentic tools (first-class entries in AgentSpec.tools_v2)
# ---------------------------------------------------------------------------
#
# These describe tools the runtime LLM may call during a session. The legacy
# `AgentSpec.tools: List[str]` field is kept for back-compat (it just holds
# tool names), but new builds should populate `tools_v2` instead so the
# runtime can introspect input/output schemas and so publish-time validators
# can enforce cost-gating rules (e.g. vision_ocr requires ocr_enabled,
# upload-capable form panels require vision_ocr).
#
# The discriminator is ``kind``. Adding a new tool kind: add a model class
# below and append it to the AgentTool union.


class _AgentToolBase(BaseModel):
    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    name: str = Field(
        pattern=r"^[a-z][a-z0-9_.]*$",
        max_length=120,
        description="Stable identifier referenced by the agent's system prompt.",
    )
    description: Optional[str] = Field(default=None, max_length=500)


class ValidateFormTool(_AgentToolBase):
    """Deterministic form-completeness check.

    Runs locally in the runtime engine — no LLM, no network. The runtime
    walks the AppSpec FormPanel referenced by ``schema_ref`` and reports
    missing/invalid fields. The agent's system prompt should mandate this
    tool runs first to short-circuit incomplete submissions before any
    paid OCR / LLM call.
    """

    kind: Literal["validate_form"] = "validate_form"
    schema_ref: str = Field(
        min_length=1,
        max_length=120,
        description=(
            "Reference to a FormPanel in the same AppSpec. Either the"
            " panel's `id` or its `schema_ref` value. The runtime resolves"
            " the panel and uses its schema_inline / schema_ref to validate."
        ),
    )


class VisionOcrTool(_AgentToolBase):
    """Vision / OCR via the smart-app-service proxy.

    Args at call time: ``{image_url? | image_b64?, prompt?}``. Returns
    ``{text, tokens_in, tokens_out, model}``. The agent calls this only
    when the goal involves understanding an uploaded image / scanned
    document AND the form-validation tool has already passed (cost gate).
    """

    kind: Literal["vision_ocr"] = "vision_ocr"
    # No schema fields — the proxy contract is fixed. Future extensions
    # (e.g. preferred_model, max_tokens) go here.


class McpTool(_AgentToolBase):
    """Live enterprise action via a registered dept-MCP."""

    kind: Literal["mcp"] = "mcp"
    source_id: str = Field(min_length=1, max_length=120)
    tool_name: str = Field(min_length=1, max_length=120)
    input_schema_ref: Optional[str] = Field(
        default=None,
        description=(
            "Optional reference into the MCP discovery dump for the"
            " input_schema. When unset the runtime resolves the schema"
            " by re-querying discovery on cold start."
        ),
    )
    # PERF (keyed-read fast-path): when BOTH are set, the runtime can serve an
    # EXACT-key lookup (model passes ``filters={col: value}``) via the dept-MCP's
    # STRUCTURED ``/run_query`` (no NL→SQL planner LLM — ~ms vs the 10–30s
    # semantic path) instead of the natural-language ``/query``. Populate from
    # the catalogue: ``dataset_id`` = the entry's source-qualified id
    # (``<source_id>.<table>``); ``dataset_kind`` = its kind (sql | mongodb | …).
    # Both omitted ⇒ the tool stays semantic-only (back-compat, no behaviour change).
    dataset_id: Optional[str] = Field(
        default=None,
        description=(
            "Catalogue dataset_id (<source_id>.<table>) this tool reads. Enables"
            " the keyed-read fast-path; required together with dataset_kind."
        ),
    )
    dataset_kind: Optional[str] = Field(
        default=None,
        description=(
            "Catalogue dataset kind (sql | mongodb | odata | bigquery | rest | …)."
            " NEVER 'semantic'. Enables the keyed-read fast-path with dataset_id."
        ),
    )
    # Policy-mandated read: when True, the read-before-write evidence gate
    # (evidence_guard.required_lookup_tools) refuses to STAGE a write unless this
    # lookup actually RAN for the case under review — e.g. a bureau / KYC /
    # sanctions check that must precede a decision. Opt-IN (default False; a plain
    # read is never silently mandatory). MUST be bound (dataset_id set) — a
    # mandated check has to be a precise keyed dataset lookup, not a fuzzy
    # semantic NL read; publish (W-09) rejects a required tool that is
    # unbound/semantic-only.
    required: bool = False


class McpActionTool(_AgentToolBase):
    """Execute a registered dept-MCP **write action** (UPDATE / INSERT).

    Where ``McpTool`` only reads, this changes record state in the
    source system — route a grievance, flag a muster, record a
    compliance verdict. ``dataset_id`` / ``action_id`` identify one
    ``write_actions`` entry from the source catalogue; ``input_schema``
    is that action's payload schema, copied from ``citra-mcp-discover``
    output, and becomes the tool's argument contract for the LLM.
    """

    kind: Literal["mcp_action"] = "mcp_action"
    source_id: str = Field(min_length=1, max_length=120)
    dataset_id: str = Field(min_length=1, max_length=200)
    action_id: str = Field(min_length=1, max_length=120)
    input_schema: Dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "JSON Schema of the write action's payload, copied verbatim"
            " from the catalogue write_actions[].input_schema."
        ),
    )
    # Payload fields the officer may OVERRIDE in the plan-then-apply modal.
    # Each must name a property in input_schema. See Action.editable_fields.
    editable_fields: List["FieldSpec"] = Field(default_factory=list)


class RagTool(_AgentToolBase):
    """RAG search over a registered dept-MCP's document corpus."""

    kind: Literal["rag"] = "rag"
    source_id: str = Field(min_length=1, max_length=120)
    top_k: int = Field(default=8, ge=1, le=50)
    classification_max: Optional[ClassificationLevel] = None


class LlmSubcallTool(_AgentToolBase):
    """Sub-LLM call with a different prompt / model tier.

    Lighter than a full sub_agent. Use for response formatting, intent
    classification, etc. — anywhere a single non-tool-using completion
    is enough.
    """

    kind: Literal["llm"] = "llm"
    system_prompt: str = Field(min_length=1, max_length=8000)
    model_tier: ModelTier = "large"


class CodeExecTool(_AgentToolBase):
    """Run Python in a sandboxed Docker container to compute or generate
    a file (PDF, XLSX, DOCX, PPTX, CSV, JSON, PNG).

    The BA does NOT write Python. They write a *prescription* in the
    agent's ``system_prompt`` describing what kind of script the runtime
    LLM should author when this tool is called. Examples:
      • "When the user clicks Draft Report, call code_exec with a script
        that uses reportlab to build a 4-page PDF: page 1 claim summary,
        page 2 policy excerpt, page 3 photo grid, page 4 signatures."
      • "For PnL requests, call code_exec with a pandas script that
        reads /workspace/input/*.xlsx, computes FIFO realised PnL, and
        writes pnl.xlsx with one sheet per symbol."

    The proxy (Citra-Service ``/internal/code-exec/run``) returns
    ``{success, stdout, stderr, output_files: [{filename, download_url,
    size, content_type}]}``. The runtime LLM should then mention the
    download_url(s) in its reply so the smart-app UI renders them as
    download buttons.

    Allowed libraries (enforced by the sandbox image): pandas, openpyxl,
    xlrd, python-docx, python-pptx, Pillow, xlsxwriter, reportlab,
    pdfplumber, jsonschema. NO subprocess, os.system, or network.
    """

    kind: Literal["code_exec"] = "code_exec"
    timeout_seconds: int = Field(
        default=60,
        ge=5,
        le=300,
        description=(
            "Per-call execution budget. The sandbox kills the script at"
            " this limit; treat as an upper bound — short scripts return"
            " in seconds."
        ),
    )
    allowed_outputs: List[str] = Field(
        default_factory=lambda: ["pdf", "xlsx", "docx", "pptx", "csv", "json", "png"],
        description=(
            "Output file extensions the BA permits. UI gate only — the"
            " sandbox itself enforces filesystem isolation."
        ),
    )


class NeighborSamplesTool(_AgentToolBase):
    """Filtered + similarity retrieval over the agent's per-app sample corpus.

    Backs the Smart App "few-shot from history" pattern. The same Milvus
    collection ``Historical_Refresh` (rows isolated per agent_id)`` (built by the Refresh-from-History
    workflow) serves two query modes:

      • ``mode="canonical"`` — filter ``is_canonical == True`` only.
        Returns the curated 5–15 always-loaded examples. Same set every
        request. Ignores the input embedding entirely.

      • ``mode="neighbors"`` (default) — vector similarity over the
        per-case input. Optional metadata filters (``decision``,
        ``severity``, ``exclude_canonical``) narrow the search space.

    The runtime typically calls this tool TWICE per agent turn — once
    with ``mode="canonical"`` to seed the prompt with schema, then again
    with ``mode="neighbors"`` per incoming case. The agent stitches both
    blocks into its working context.
    """

    kind: Literal["neighbor_samples"] = "neighbor_samples"
    collection: str = Field(
        default="Historical_Refresh", min_length=1, max_length=255,
        description=(
            "Milvus collection — always 'Historical_Refresh' (one shared "
            "collection for all agents; rows isolated by an agent_id field "
            "the runtime filters on). Enforced by publish rule G-01."
        ),
    )
    mode: Literal["canonical", "neighbors"] = Field(
        default="neighbors",
        description="canonical = always-loaded few-shot; neighbors = per-case retrieval",
    )
    top_k: int = Field(default=3, ge=1, le=20)
    decision: Optional[str] = Field(
        default=None, max_length=64,
        description="Optional decision-class filter (e.g. 'approve' / 'escalate').",
    )
    severity: Optional[str] = Field(
        default=None, max_length=32,
        description="Optional severity filter.",
    )
    exclude_canonical: bool = Field(
        default=True,
        description="In neighbors mode, exclude canonical samples (already loaded separately).",
    )


class ImageAnalyzeTool(_AgentToolBase):
    """Analyze ONE image with the vision model against a learned rubric and
    return a STRUCTURED ``ItemFinding`` (fields + recommendation + confidence +
    rationale) — not free text like ``vision_ocr``.

    ``task_type`` is the semantic role of the image (e.g.
    ``motor-accident-damage`` vs ``machine-inspection-defect``). It keys the
    per-type **rubric** (criteria distilled from officer reject-reasons, loaded
    server-side from ``smartapp_analysis_rubrics``) and the ``field_schema``.
    The same tool + model serve every domain; only the rubric + schema differ.
    Cost-gated on ``ocr_enabled`` (reuses the vision proxy).
    """

    kind: Literal["image_analyze"] = "image_analyze"
    task_type: str = Field(
        min_length=1, max_length=120,
        description=(
            "Semantic role of the image; the per-type learning bucket + schema"
            " key (e.g. 'motor-accident-damage')."
        ),
    )
    field_schema: Dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Structured fields the model must return: {field_name: 'type/desc'}."
            " e.g. {'severity':'one of none|minor|moderate|major|total',"
            " 'parts_affected':'list of strings', 'airbag_deployed':'true/false'}."
        ),
    )
    # ── Server-side URL resolution (avoid routing signed URLs through the LLM) ──
    # When set, the agent passes a short `record_id` (not the image URL); the tool
    # reads the record server-side and pulls the image URL from `url_column`. This
    # is REQUIRED for signed/expiring URLs (S3/GCS) — a long signed URL copied by
    # the LLM into the call gets its signature corrupted → 403. data_source_id is
    # the app data_source whose record holds the URL; key_field is that dataset's
    # key column.
    data_source_id: Optional[str] = Field(
        default=None,
        description="App data_source id whose record holds the image URL (enables server-side resolution from record_id).",
    )
    url_column: Optional[str] = Field(
        default=None, description="Column on the record that contains the image URL."
    )
    key_field: Optional[str] = Field(
        default=None, description="Key column of the dataset (for the 1-row keyed read by record_id)."
    )
    required: bool = Field(
        default=True,
        description=(
            "When True (default) AND this tool is record-bound (data_source_id "
            "set), the read-before-write guard REQUIRES the agent to analyse this "
            "image for the record under review before any write is staged — a "
            "write without it fails loud. Set False to make the analysis "
            "discretionary (e.g. 'only inspect the photo if the record already "
            "looks suspicious')."
        ),
    )
    sop_source: Optional[str] = Field(
        default=None,
        description=(
            "RAG source id (a policy/SOP corpus) the tool fetches the standing SOP "
            "from at call time and applies as the judgment standard. Fetched "
            "SERVER-SIDE and CACHED per (app, task_type), so N items (e.g. 10 photos "
            "of one claim) share ONE fetch — the agent never carries the SOP. Keeps "
            "the standard fresh (TTL), so NO seed criteria are stored in Mongo; the "
            "learned rubric refines it on top. Leave unset if no SOP governs this "
            "item type (then it runs on the agent's query + learned rubric)."
        ),
    )
    sop_query: Optional[str] = Field(
        default=None,
        description=(
            "Optional retrieval query for the SOP from `sop_source`; defaults to a "
            "task_type-derived query for the evidence / pass-fail criteria."
        ),
    )
    sop_doc_path: Optional[str] = Field(
        default=None,
        description=(
            "Optional. When set, load the ENTIRE SOP document at this doc_path (all "
            "sections, in order) as the standard — instead of top-k passages from a "
            "`sop_query`. Use when one specific SOP governs this item type and you want "
            "its full text, not the best-matching snippets."
        ),
    )
    # Ontology-stamped by the builder (autowire_fraud_roles) from the dataset's
    # sources.json — NOT authored by the LLM. Makes this per-image tool role-aware
    # on its own so a reuse hit on an `identity` artifact (a headshot/ID legitimately
    # reused by the same applicant across cases) is read as verification, not fraud,
    # even when no consistency_check covers the column. Absent ⇒ evidence/suspicious.
    artifact_role: Optional[str] = Field(
        default=None,
        description="Ontology artifact role for url_column (identity|evidence|supporting); autowired from sources.json.",
    )
    reuse_policy: Optional[str] = Field(
        default=None,
        description="Ontology reuse policy for url_column (expected|suspicious|ignore); autowired, overrides the role default.",
    )


class DocExtractTool(_AgentToolBase):
    """Extract STRUCTURED fields from ONE document (scanned image or text PDF)
    against a learned rubric, returning a ``ItemFinding`` (modality="document").

    Sibling of ``image_analyze`` for documents: ``task_type`` (e.g.
    ``police-report``, ``repair-estimate``, ``resume``) keys the per-type rubric
    + ``field_schema``. Scanned doc images and text-layer PDFs are supported
    today (first 20 pages); scanned-PDF OCR (rasterise) is a follow-up. Cost-gated
    on ``ocr_enabled``.
    """

    kind: Literal["doc_extract"] = "doc_extract"
    task_type: str = Field(
        min_length=1, max_length=120,
        description="Document type / role; per-type learning bucket + schema key (e.g. 'police-report').",
    )
    field_schema: Dict[str, str] = Field(
        default_factory=dict,
        description="Fields to extract: {field_name: 'type/desc'} (e.g. {'fir_no':'string','accident_date':'date'}).",
    )
    model_tier: ModelTier = Field(
        default="large",
        description=(
            "Model that REVIEWS / extracts from a TEXT-layer document (the reasoning"
            " over the extracted text). Defaults to 'large' — the document reviewer"
            " should be the strong reasoning model unless the builder downgrades it"
            " for cost. NB: scanned / image documents (no text layer) MUST go"
            " through the multimodal vision model regardless of this tier, since"
            " they require seeing the page pixels."
        ),
    )
    # See ImageAnalyzeTool: when set, the agent passes a short `record_id` and the
    # tool resolves the document URL server-side from `url_column` — required for
    # signed URLs so the LLM never corrupts the signature.
    data_source_id: Optional[str] = Field(
        default=None,
        description="App data_source id whose record holds the document URL (enables server-side resolution from record_id).",
    )
    url_column: Optional[str] = Field(
        default=None, description="Column on the record that contains the document URL."
    )
    key_field: Optional[str] = Field(
        default=None, description="Key column of the dataset (for the 1-row keyed read by record_id)."
    )
    required: bool = Field(
        default=True,
        description=(
            "When True (default) AND this tool is record-bound (data_source_id "
            "set), the read-before-write guard REQUIRES the agent to extract from "
            "this document for the record under review before any write is staged "
            "— a write without it fails loud. Set False to make the extraction "
            "discretionary."
        ),
    )
    sop_source: Optional[str] = Field(
        default=None,
        description=(
            "RAG source id (policy/SOP corpus) the tool fetches the standing SOP "
            "from at call time and applies as the standard. Fetched SERVER-SIDE and "
            "CACHED per (app, task_type) so N documents share ONE fetch — the agent "
            "never carries the SOP. Fresh (TTL), so NO seed criteria in Mongo; the "
            "learned rubric refines it on top. Unset = run on query + learned rubric."
        ),
    )
    sop_query: Optional[str] = Field(
        default=None,
        description="Optional retrieval query for the SOP; defaults to a task_type-derived query.",
    )
    sop_doc_path: Optional[str] = Field(
        default=None,
        description=(
            "Optional. When set, load the ENTIRE SOP document at this doc_path (all "
            "sections, in order) as the standard — instead of top-k passages from a "
            "`sop_query`."
        ),
    )
    # Ontology-stamped by the builder (autowire_fraud_roles) from sources.json —
    # see ImageAnalyzeTool. Makes doc_extract role-aware on its own so a reused
    # `identity` document (an ID scan) is verification, not fraud, independent of a
    # sibling consistency_check. Absent ⇒ evidence/suspicious (safe default).
    artifact_role: Optional[str] = Field(
        default=None,
        description="Ontology artifact role for url_column (identity|evidence|supporting); autowired from sources.json.",
    )
    reuse_policy: Optional[str] = Field(
        default=None,
        description="Ontology reuse policy for url_column (expected|suspicious|ignore); autowired, overrides the role default.",
    )


class CheckEvaluateTool(_AgentToolBase):
    """Judge ONE API / System-of-Record check result against a policy, returning
    a per-check ``ItemFinding`` (modality="api") for individual officer review —
    the structured-data twin of ``image_analyze``. The lead agent fetches the
    data (an ``mcp`` read — a credit-bureau / identity / sanctions lookup) and passes it
    in as ``data``; this tool produces a verdict {recommendation, confidence,
    rationale} that renders as its own accept/reject card and trains the
    per-``(app, task_type)`` rubric. Use ONE per check (one per ``task_type``).

    Two modes:
      * ``llm`` (default) — the model judges ``data`` against the fetched SOP +
        the learned rubric. For grey-area checks: identity/name match, a bureau
        flag officers routinely override, a borderline score with mitigants.
      * ``rule`` — a deterministic boolean over ``data`` (``rule_expr``), NO LLM
        call, for fixed-threshold checks (score ≥ 700). Cheaper; the rubric stays
        thin (little to learn). A rule error fails LOUD to 'flag' (needs review),
        never a silent pass.
    """

    kind: Literal["check_evaluate"] = "check_evaluate"
    task_type: str = Field(
        min_length=1, max_length=120,
        description="Check type; the per-check learning bucket + review-card label (e.g. 'credit-check', 'identity-match').",
    )
    mode: Literal["llm", "rule"] = Field(
        default="llm",
        description="'llm' judges data vs SOP+rubric; 'rule' evaluates rule_expr deterministically (no LLM).",
    )
    rule_expr: Optional[str] = Field(
        default=None,
        description=(
            "For mode='rule': a boolean expression over the check data's fields "
            "(e.g. 'credit_score >= 700 and open_defaults == 0'). True ⇒ 'pass', "
            "False ⇒ 'flag'. Evaluated safely (no builtins/imports); on error the "
            "check is flagged for manual review."
        ),
    )
    field_schema: Dict[str, str] = Field(
        default_factory=dict,
        description="Optional fields to surface on the finding {name: 'type/desc'} (e.g. {'credit_score':'number'}).",
    )
    model_tier: ModelTier = Field(default="large", description="Model tier for mode='llm'.")
    sop_source: Optional[str] = Field(
        default=None,
        description=(
            "RAG source id for the acceptance policy/SOP — fetched SERVER-SIDE and "
            "cached per (app, task_type). Unset ⇒ judge on the learned rubric only "
            "(+ the case context the agent passes)."
        ),
    )
    sop_query: Optional[str] = Field(default=None, description="Optional retrieval query for the SOP.")
    sop_doc_path: Optional[str] = Field(
        default=None,
        description="Optional: load the ENTIRE SOP doc at this path instead of top-k passages.",
    )


class PaymentProofCheck(BaseModel):
    """E4 payment-proof verification config, autowired from sources.json
    fraud_screening.payment_proof. Typed (extra='forbid') so a mistyped key is
    rejected AT PUBLISH. The ledger routing triple (source/dataset/kind) is
    stamped by autowire from the catalogue so the runtime's structured
    read-by-key needs no discovery lookup."""

    model_config = ConfigDict(extra="forbid")

    ledger_source_id: str
    ledger_dataset: str
    ledger_kind: Optional[str] = None
    match_field: str
    amount_field: Optional[str] = None
    date_field: Optional[str] = None
    party_field: Optional[str] = None
    doc_ref_field: str = "transaction_ref"
    doc_amount_field: str = "amount"
    doc_date_field: str = "payment_date"
    doc_party_field: Optional[str] = None
    amount_tolerance_pct: float = Field(default=1.0, ge=0)
    date_window_days: int = Field(default=3, ge=0)
    # F1 — the column(s) tagged artifact_role='payment_proof': the ONLY
    # document(s) whose extracted values may feed the ledger match. The runtime
    # skips the check (visible note) when none is attached to the record.
    doc_columns: Optional[List[str]] = None
    # F2 — the ledger's catalogue description, surfaced in the tool description
    # so the agent knows which attached bill matches this ledger.
    ledger_description: Optional[str] = None


class VerifyCompareCheck(BaseModel):
    """One field-pair comparison inside a VerifyAgainstCheck."""
    model_config = ConfigDict(extra="forbid")

    doc_field: str
    target_field: str
    type: str = Field(default="text", pattern="^(amount|date|id|text)$")
    tolerance_pct: float = Field(default=1.0, ge=0)
    window_days: int = Field(default=3, ge=0)


class VerifyAgainstCheck(BaseModel):
    """Generic cross-dataset verification config (plan F4), autowired from
    sources.json fraud_screening.verify_against with the target routing
    resolved from the catalogue — the E4 shape generalized (purchase bill →
    registry, serial-in-photo → asset master). Typed extra='forbid'."""
    model_config = ConfigDict(extra="forbid")

    name: str
    target_source_id: str
    target_dataset: str
    target_kind: Optional[str] = None
    target_description: Optional[str] = None
    match_field: str
    doc_column: str
    doc_ref_field: str = "reference"
    compare: List[VerifyCompareCheck] = Field(default_factory=list)
    description: Optional[str] = None


class DateRuleCheck(BaseModel):
    """E6 declarative date rule, autowired from sources.json
    fraud_screening.date_rules. Evaluated server-side against the record read
    by key — never against agent-supplied values."""
    model_config = ConfigDict(extra="forbid")

    name: str
    earlier_field: str
    later_field: str
    min_days_between: int = 0
    max_days_between: Optional[int] = Field(default=None, ge=0)


class ClaimContext(BaseModel):
    """EXIF↔claim comparator config (E1), autowired from sources.json
    fraud_screening. Typed (extra='forbid') so a mistyped key or a bad radius
    is rejected AT PUBLISH — an untyped dict would pass validation and surface
    only as a silently skipped runtime check."""

    model_config = ConfigDict(extra="forbid")

    incident_date_field: Optional[str] = None
    location_lat_field: Optional[str] = None
    location_lon_field: Optional[str] = None
    gps_radius_km: Optional[float] = Field(default=None, ge=0)
    dataset_kind: Optional[str] = None


class ConsistencyCheckTool(_AgentToolBase):
    """Deterministic record↔artifact consistency screen (fraud primitive T0).

    Runs LOCALLY — no LLM, no network, zero token cost. The agent passes the
    record's CLAIMED values and the values EXTRACTED from artifacts
    (``doc_extract`` / ``image_analyze`` findings); the tool normalizes per
    field type (phone/amount/date/id/name), compares, runs format+checksum
    validators (PAN, IFSC, GSTIN, VIN check-digit, Aadhaar Verhoeff, email,
    phone) and optional invoice arithmetic, and returns explainable
    ``mismatches[]``. Output is EVIDENCE attached to the recommendation the
    officer reviews — it never auto-rejects (universal-approval invariant).
    See docs/fraud-detection-primitives-plan.md §4.3/§7.
    """

    kind: Literal["consistency_check"] = "consistency_check"
    # Wider than the 500-char base cap: autowire appends the payment-proof and
    # cross-dataset verification sentences (ledger/target names + catalogue
    # descriptions + pinned document columns) so the agent matches the right
    # bill to the right dataset — worth the prompt budget on this one tool.
    description: Optional[str] = Field(default=None, max_length=1600)
    # Locale pack key ('in' | 'us'), stamped by autowire from the dataset's
    # ontology domain.country. Decides which ID validators run and how ambiguous
    # dates parse (03/04 = Apr 3 US, 3 Mar IN). None ⇒ the deployment env
    # (FRAUD_LOCALE) decides — the pre-ontology fallback.
    locale: Optional[str] = Field(default=None, pattern=r"^[a-z]{2}$")
    # The dataset's ontology domain triple (vertical / sub_vertical / country),
    # stamped by autowire. Read-only provenance: feeds the Screening Health
    # admin badge and catalogue ordering — never a behavior fork.
    domain: Optional[Dict[str, str]] = None
    # Generic cross-dataset verifications (plan F4) — autowired from
    # sources.json fraud_screening.verify_against.
    verify_against: Optional[List[VerifyAgainstCheck]] = None
    # E6 declarative date rules — autowired; evaluated against the record row
    # read server-side by key (dataset_kind rides claim_context or is stamped
    # here when claim_context is absent).
    date_rules: Optional[List[DateRuleCheck]] = None
    # The dataset kind for the server-side record read (stamped by autowire
    # whenever date_rules exist; claim_context carries its own copy).
    dataset_kind: Optional[str] = None
    field_types: Dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Optional builder-pinned type per field name (phone|amount|date|"
            "ssn|ein|routing|zip|pan|ifsc|gstin|vin|aadhaar|account|email|"
            "name|id|text). Unpinned"
            " fields are inferred from the field name."
        ),
    )
    identity_fields: List[str] = Field(
        default_factory=list,
        description=(
            "Ontology-autowired from the dataset's sources.json"
            " fraud_screening.identity_fields — the columns the SOURCE declares as"
            " cross-record linkable keys (policy_no / vin / consumer_id …). These"
            " link across cases even when the name heuristic or a field_types pin"
            " doesn't recognise them, so WHICH identifiers join cases comes from the"
            " source, not the LLM. Additive only — it never stops an undeclared"
            " field from linking. Do NOT author this; autowire overwrites it."
        ),
    )
    link_entities: bool = Field(
        default=True,
        description=(
            "Write-through this case's high-precision identifiers (phone/PAN/"
            "GSTIN/VIN/Aadhaar/email/account/id) into the entity-link overlay"
            " and report cross-case signals (rings, double-dip, synthetic"
            " identity). Disable only for apps where cross-case linking is"
            " not wanted."
        ),
    )
    # OPTIONAL artifact fingerprinting (T0, still free — no LLM): RECORD-BIND
    # the tool exactly like image_analyze/doc_extract so it resolves each
    # artifact URL server-side via the dept-MCP (resolve_media), downloads the
    # bytes, and computes SHA-256 exact-duplicate + dHash near-duplicate +
    # metadata flags against prior records. This is how a fraud-screening app
    # detects a REUSED photo/PDF without paying for a vision tool. All three
    # fields must be set together; without them the tool runs the field/entity
    # checks only (no artifact hashing).
    data_source_id: Optional[str] = Field(
        default=None,
        description=(
            "App data_source id holding the record (same binding contract as"
            " image_analyze). Enables artifact fingerprinting."
        ),
    )
    url_columns: List[str] = Field(
        default_factory=list,
        description=(
            "Record columns holding artifact URLs to fingerprint (e.g."
            " ['defect_photo_url', 'inspection_report_url']). Each is resolved"
            " via the dept-MCP and hashed (SHA-256 + dHash) against prior"
            " records for duplicate/reuse detection."
        ),
    )
    url_column_roles: Dict[str, Dict[str, str]] = Field(
        default_factory=dict,
        description=(
            "Per-url-column fraud semantics from the source ontology (sources.json),"
            " auto-wired by the builder from the catalogue: {col: {artifact_role:"
            " identity|evidence|supporting, reuse_policy: expected|suspicious|ignore}}."
            " Reuse of an 'identity' artifact (a headshot reused by the same"
            " applicant) is EXPECTED and never flags; reuse of 'evidence' (an"
            " accident/damage/defect photo) across cases is a double-dip signal."
            " Absent for a column ⇒ treated as evidence/suspicious (unchanged behavior)."
        ),
    )
    key_field: Optional[str] = Field(
        default=None,
        description="Record key column matching the run's record_id (e.g. 'inspection_id').",
    )
    claim_context: Optional[ClaimContext] = Field(
        default=None,
        description=(
            "EXIF↔claim comparator config (E1), auto-wired from sources.json"
            " fraud_screening. When set, the runtime reads the record's CLAIMED"
            " incident date / site coordinates server-side (structured"
            " read-by-key, never the NL planner) and compares each evidence"
            " photo's EXIF capture time / GPS against them. None ⇒ the"
            " comparator never runs (ontology-driven). Do NOT author this;"
            " autowire overwrites it."
        ),
    )
    payment_proof: Optional[PaymentProofCheck] = Field(
        default=None,
        description=(
            "Payment-proof verification config (E4), auto-wired from"
            " sources.json fraud_screening.payment_proof. When set and the"
            " agent passes an extracted payment reference, the runtime looks"
            " the reference up in the declared ledger dataset SERVER-SIDE"
            " (structured read-by-key) and compares amount/date/party —"
            " 'reference not found' is a fact-grade fraud signal; a match is"
            " VERIFICATION. None ⇒ the check never runs (ontology-driven)."
            " Do NOT author this; autowire overwrites it."
        ),
    )


class FraudSynthesisTool(_AgentToolBase):
    """T3 gated fraud cross-examination (fraud plan §4.5/§6). The agent calls
    it LAST with ALL collected screening signals; the TOOL gates server-side —
    below the severity gate it returns instantly at zero cost, at/above it (or
    on a small random audit sample) it runs ONE reasoning pass that weighs the
    signals together against the learned fraud CASE rubric (modality='case')
    and returns structured evidence (fraud_risk / key_indicators /
    benign_explanations / recommended_checks). Output is officer EVIDENCE —
    never a verdict; nothing auto-rejects."""

    kind: Literal["fraud_synthesis"] = "fraud_synthesis"
    model_tier: ModelTier = Field(
        default="large",
        description="Reasoning model for the cross-examination (default large).",
    )
    gate_min_points: int = Field(
        default=2, ge=1,
        description=(
            "Severity points at/above which the reasoning pass runs (mismatch=2,"
            " warn=1, exact/CLIP duplicate=3, pHash dup / cardinality=2,"
            " metadata anomaly / similar=1). Provisional until L3 calibration."
        ),
    )
    sample_rate: float = Field(
        default=0.05, ge=0.0, le=1.0,
        description="Random audit fraction of BELOW-gate cases that still get the reasoning pass.",
    )


class ItemFinding(BaseModel):
    """Per-item structured analysis output (one image or one document).

    Emitted by ``image_analyze`` / (future) ``doc_extract`` and surfaced to the
    officer for per-item accept/reject. ``item_type == task_type``; the
    structured ``fields`` feed the record decision + audit trail.
    """

    model_config = ConfigDict(extra="ignore")

    item_id: str = Field(description="Stable id of the analyzed item (image/doc/row key).")
    item_type: str = Field(description="Semantic role == the tool's task_type.")
    modality: Literal["image", "document", "api"] = "image"
    subject: Optional[str] = Field(
        default=None,
        description="A few words (≤ ~8) naming WHAT this image/document is — its "
        "evidence/subject type (e.g. 'transformer nameplate photo', 'scanned damage "
        "report'), independent of the verdict. Captured with a reject reason so the "
        "rubric learns per subject-type, not just per task_type.",
    )
    fields: Dict[str, Any] = Field(default_factory=dict)
    recommendation: Optional[str] = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    rationale: str = ""
    citations: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Provenance: {page:int} | {bbox:[...]} | {source_url:str}.",
    )
    rubric_version: Optional[str] = None
    # ── Factor scoring (docs/factor-scorecard-plan.md) ──
    # Set ONLY when this finding answers a declared factor. A factor is the
    # structured-data twin of an item finding, so it rides the same object
    # rather than a parallel one.
    #
    # ``score`` is NOT ``confidence``. Confidence is the model's self-reported
    # certainty (0–1); score is a POLICY quantity out of the factor's declared
    # weight (18 of 25). Collapsing them yields a composite that moves when the
    # model gets more or less sure — precisely what model validation rejects.
    # Both are persisted, separately, on purpose.
    factor_id: Optional[str] = None
    score: Optional[float] = Field(
        default=None, ge=0,
        description="Points awarded, out of the factor's declared weight. "
        "Composite mode only; a checklist factor carries a band and no score.",
    )
    band: Optional[str] = Field(
        default=None, max_length=40,
        description="Declared band label. Assigned in CODE for score-based "
        "bands; chosen by the evaluator for label-only bands.",
    )
    #: Learned clauses the model applied or overruled on THIS factor —
    #: [{clause_id, relation: applied|overruled, note}]. Rendered as an
    #: annotation on the row. A clause ANNOTATES a factor and never silently
    #: moves its score: the moment memory adjusts numbers invisibly the
    #: composite becomes unexplainable, which is the whole property we are here
    #: to protect.
    clauses_fired: List[Dict[str, Any]] = Field(default_factory=list)
    #: Hash of the SOP text this verdict was actually judged against, stamped by
    #: ``check_evaluate`` in llm mode. Compared with the factor's declared
    #: ``sop.fingerprint`` to detect that the policy moved under the rubric.
    #: None in rule mode — a deterministic threshold lives in ``rule_expr`` on
    #: the spec, so its drift is a spec edit, visible in the spec's own history.
    sop_fingerprint: Optional[str] = Field(default=None, max_length=64)
    # Artifact identity — what EXACTLY was analyzed. content_sha256 hashes the
    # fetched bytes (exact-reuse detection survives re-uploads and re-signed
    # URLs); media_ref is the stable, unsigned pointer (record-bound key or
    # normalized URL) for human traceability.
    content_sha256: Optional[str] = None
    media_ref: Optional[str] = None


class ItemDecisionRecord(BaseModel):
    """The multimodal twin of ``DecisionRecord`` — one row per analyzed ITEM.

    Captures BOTH sides of the officer's judgement (what worked = accept, what
    didn't = reject + reason, plus cancel), the model's verdict + rationale, and
    the artifact identifiers — so future analyses can be grounded in per-item
    precedents ("this exact photo was rejected on case X because …") instead of
    only the aggregated rubric summary, and so every reviewed item becomes a
    labeled, reasoned, outcome-linked training example the tenant owns.

    Written twice: at run end (``disposition="proposed"``, the model's verdict)
    and at officer feedback (disposition + reason). ``outcome`` is inherited
    from the parent DecisionRecord by the read-back poller via correlation_id.
    Derived + reconstructable (audit chain holds the runs; rubric holds the
    reject reasons) — persistence is loud-but-non-fatal, like decision_records.
    """

    model_config = ConfigDict(extra="ignore")

    item_id: str
    correlation_id: Optional[str] = None
    slug: Optional[str] = None
    app_id: Optional[str] = None
    tenant_id: Optional[str] = None
    modality: Literal["image", "document", "api"] = "image"
    task_type: str = "generic"
    subject: Optional[str] = None
    # artifact identity
    media_ref: Optional[str] = None
    content_sha256: Optional[str] = None
    # the model's verdict (from ItemFinding)
    fields: Dict[str, Any] = Field(default_factory=dict)
    recommendation: Optional[str] = None
    confidence: float = 0.0
    rationale: str = ""
    rubric_version: Optional[str] = None
    # the officer's judgement — BOTH sides are knowledge
    disposition: Literal["proposed", "accept", "reject", "cancel"] = "proposed"
    disposition_reason: Optional[str] = None
    disposition_actor: Optional[str] = None
    disposition_at: Optional[datetime] = None
    # inherited from the parent DecisionRecord by the outcome poller
    outcome: Optional[Dict[str, Any]] = None
    created_at: datetime
    updated_at: Optional[datetime] = None


class LookupJudgementTool(_AgentToolBase):
    """Fetch the EVIDENCE behind a learned judgement, on demand.

    The injection block gives the agent one self-sufficient line per judgement:
    the rule, the concrete move officers made, and how many stand behind it.
    That is deliberately enough to ACT on. This tool is for the other question —
    *why does this judgement exist* — answered from the actual cases and the
    officers' own words.

    Split this way on purpose. Pre-injecting the evidence would bloat every run
    for the two or three judgements the agent actually weighs, and the richest
    material (the source corrections) could not fit at any budget. Making the
    judgements themselves fetch-only would be worse: a matching judgement would
    become optional, the model might not look, and a team's rule would silently
    fail to influence a decision — invisibly, since "injected, not cited" reads
    exactly like "considered and set aside".

    So: presence is infrastructure, depth is agentic. Reads Citra's own clause
    store — no dept-MCP, no new auth surface, no external call.
    """

    kind: Literal["lookup_judgement"] = "lookup_judgement"


AgentTool = Annotated[
    Union[
        ValidateFormTool,
        VisionOcrTool,
        ImageAnalyzeTool,
        DocExtractTool,
        CheckEvaluateTool,
        ConsistencyCheckTool,
        FraudSynthesisTool,
        McpTool,
        McpActionTool,
        RagTool,
        LlmSubcallTool,
        CodeExecTool,
        NeighborSamplesTool,
        LookupJudgementTool,
    ],
    Field(discriminator="kind"),
]


class GroundingContract(BaseModel):
    """Few-shot-from-history grounding contract for a Smart App agent.

    Carries everything the server-side grounding refresh needs to (re)build
    the agent's ``Historical_Refresh` (rows isolated per agent_id)`` Milvus collection that the
    ``neighbor_samples`` tool reads — WITHOUT a workflow. The refresh runs as
    a deterministic smart-app-service operation (see ``grounding_refresh.py``):
    pull history → package (PII scrub + dedupe) → select (canonical few-shots)
    → **Gate B guard** → atomic swap into the collection.

    The guard thresholds + Gate A evidence make grounding non-bypassable at
    /publish: a build cannot ship a grounded agent without the evidence that
    Gate A (``/builder/history-quality``) actually vetted the data, and a
    later degraded pull is rejected before it can replace good samples.
    """
    model_config = ConfigDict(extra="ignore")

    # ── What to pull (the decision-history dataset) ──
    source_id: str = Field(min_length=1, description="Dept source id (e.g. 'field_operations').")
    dataset_id: str = Field(
        min_length=1,
        description="Catalogue dataset id, '<source>.<table>' (e.g. 'claims_db.closed_motor_claims').",
    )
    source_kind: str = Field(
        default="sql",
        description="Source kind for the historical pull (sql/duckdb/bigquery/mongodb).",
    )
    query: Optional[str] = Field(
        default=None, max_length=2000,
        description="Optional explicit pull query/predicate; when unset a SELECT over the dataset table is built.",
    )
    filters: Dict[str, Any] = Field(
        default_factory=dict,
        description="Predicate for the pull (e.g. {'status': 'closed'}). Applied to the SELECT WHERE.",
    )
    max_results: int = Field(default=5000, ge=1, le=5000)

    # ── Field mapping (straight from the dataset's decision_history descriptor) ──
    source_id_field: str = Field(min_length=1, description="Row identity column (e.g. 'claim_id').")
    input_fields: List[str] = Field(
        min_length=1,
        description="Columns the agent sees as the case INPUT (the drivers of the decision).",
    )
    output_fields: List[str] = Field(
        default_factory=list,
        description="Columns forming the recorded OUTCOME (must include the decision field).",
    )
    decision_field: str = Field(min_length=1, description="The decision/outcome column.")
    terminal_states: List[str] = Field(
        default_factory=list,
        description=(
            "The DECIDED states of decision_field — only rows whose decision is "
            "one of these become few-shots. Excludes in-progress states (e.g. "
            "'pending') so the agent grounds on completed decisions, not the "
            "current status. Sourced from the catalogue's "
            "decision_history.terminal_states. Empty = no terminal filter "
            "(treat every non-empty decision as decided)."
        ),
    )
    severity_field: Optional[str] = Field(default=None, description="Optional severity column.")
    reasoning_field: Optional[str] = Field(default=None, description="Optional recorded-rationale column.")

    # ── Canonical few-shot selection ──
    target_count: int = Field(default=8, ge=1, le=50, description="How many canonical few-shots to curate.")
    per_decision_min: int = Field(default=1, ge=0, description="Min canonical examples per decision class.")

    # ── Gate B — refresh guard thresholds (the bar a future pull must clear) ──
    min_samples: int = Field(default=8, ge=1, description="Floor on the total packaged pool.")
    min_canonical: int = Field(default=5, ge=0, description="Floor on the always-loaded canonical set (0 = canonical few-shots disabled; neighbor retrieval unaffected).")
    shrink_floor: float = Field(
        default=0.5, gt=0.0, le=1.0,
        description="A refresh that shrinks the live set below this fraction is rejected.",
    )
    required_decision_classes: List[str] = Field(
        default_factory=list,
        description="Decision classes that MUST be present in a valid refresh.",
    )
    min_decision_fill_rate: float = Field(
        default=0.9, ge=0.0, le=1.0,
        description="Min fraction of pulled rows that carry a non-empty decision.",
    )

    # ── Gate A evidence — REQUIRED so grounding can only ship after vetting ──
    source_profile_baseline: Dict[str, Any] = Field(
        default_factory=dict,
        description="The /builder/history-quality 'signals' captured at build time.",
    )
    evaluation_verdict: Optional[str] = Field(
        default=None, max_length=2000,
        description="The builder's one-paragraph ground/don't-ground rationale (Gate A judgment).",
    )


class OutcomePollConfig(BaseModel):
    """Declarative Stage-4 outcome read-back rule (docs/citra-self-improving-loop-plan.md).

    After a decision settles, the outcome poller reads the source record back BY
    KEY via the structured ``/run_query`` plane (never the NL planner) and labels
    the decision good / bad / unknown. The builder emits this from
    ``/builder/history-quality``'s ``suggested_outcome_poll`` whenever the source
    has a classifiable status field; it can also be hand-authored."""

    model_config = ConfigDict(extra="forbid")

    # Outcome TRACKING is ON by default (disable per-app from the UI). With a valid
    # read-back config the loop labels every settled decision good/bad/neutral; a
    # missing/invalid config FAILS LOUD (logs + skips), never a silent 'good'.
    enabled: bool = True
    # Auto-run (default ON = continuous auto-learning). When True, validated
    # outcomes auto-fold into the vector corpus (good → repeat, bad → avoid) via
    # the delta write-back AND the periodic full rebuild. When False the loop
    # still TRACKS outcomes but only updates memory on a MANUAL refresh (the UI
    # "Refresh grounding" button / POST /grounding/refresh). On by default so an
    # app improves from real outcomes out of the box; disable per-app from the UI
    # if you want manual-only. NOTE: the rubric layer (officer approve/reject)
    # learns at decision time regardless of this flag — this gates only the
    # outcome→few-shot path.
    auto_refresh: bool = True
    # Judge only once this many days have passed since the decision committed,
    # giving the real-world consequence time to settle. Too short → premature,
    # noisy labels; too long → slow loop. Per-app, matched to the decision's
    # natural settling time.
    window_days: float = Field(default=7.0, ge=0)
    # Structured read kind for /run_query — MUST NOT be "semantic" (that falls
    # back to the NL planner; the verdict must be deterministic).
    kind: str = "sql"
    # Physical table / entity to read back from.
    table: Optional[str] = None
    # Source-system key column to filter on (e.g. "complaint_id").
    key_field: Optional[str] = None
    # Which committed-write payload field carries the key value (defaults to
    # key_field when unset).
    payload_key_field: Optional[str] = None
    # Status column to classify on, and the value sets meaning good / bad.
    status_field: Optional[str] = None
    good_values: List[str] = Field(default_factory=list)
    bad_values: List[str] = Field(default_factory=list)
    # Terminal statuses that carry NO routing-quality signal (e.g. "escalated" —
    # the issue left the assigned officer's hands for reasons unrelated to the
    # routing). Stamped as outcome "neutral" so the record stops being polled but
    # write-back ignores it. Distinct from good/bad and from unknown (re-poll).
    neutral_values: List[str] = Field(default_factory=list)
    # A field the decision WROTE (e.g. "assigned_to"); if its current value no
    # longer matches what we wrote, the decision was overturned → bad.
    hold_field: Optional[str] = None


class AgentSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", protected_namespaces=())  # single strict source of truth — legacy fields (inverse_action_id, …) purged pre-prod; root rejects unknowns

    spec_version: SpecVersion = "v0"
    agent_id: str
    name: str = Field(min_length=1, max_length=120)
    description: Optional[str] = Field(default=None, max_length=1000)
    model_tier: ModelTier = "large"
    system_prompt: str = Field(min_length=1)
    input_schema: Optional[Dict[str, Any]] = None
    tools: List[str] = Field(
        default_factory=list,
        description=(
            "Legacy: flat list of tool names. New builds should populate"
            " ``tools_v2`` instead so the runtime can introspect schemas"
            " and the publisher can enforce cost-gating rules."
        ),
    )
    tools_v2: List[AgentTool] = Field(
        default_factory=list,
        description=(
            "Discriminated tool definitions exposed to the runtime LLM."
            " Each entry declares its ``kind`` (vision_ocr, mcp, mcp_action,"
            " rag, validate_form, llm, code_exec, neighbor_samples) and any"
            " kind-specific config."
        ),
    )
    mcps: List[str] = Field(default_factory=list)
    rag: List[RagBinding] = Field(default_factory=list)
    memory_namespace: Optional[str] = None
    sub_agents: List[SubAgent] = Field(default_factory=list)
    actions: List[Action] = Field(default_factory=list)
    hitl_policy: Optional[Dict[str, Any]] = None
    grounding: Optional[GroundingContract] = Field(
        default=None,
        description=(
            "Few-shot-from-history grounding contract. Set when the agent"
            " carries a ``neighbor_samples`` tool grounded in the tenant's"
            " historical decisions. Drives the server-side grounding refresh"
            " that (re)builds ``Historical_Refresh` (rows isolated per agent_id)``."
        ),
    )
    outcome_poll: Optional[OutcomePollConfig] = Field(
        default=None,
        description=(
            "Self-improving loop Stage-4 config: how to read the source record"
            " back and judge the outcome of this agent's decisions. None = the"
            " loop records decisions but does not auto-observe outcomes."
        ),
    )
    learning_log: List[LearningLogEntry] = Field(default_factory=list)
    version: Optional[int] = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _enforce_tool_rules(self) -> "AgentSpec":
        if not self.tools_v2:
            return self

        # Unique names.
        names = [t.name for t in self.tools_v2]
        if len(names) != len(set(names)):
            seen: set = set()
            dup = next(n for n in names if n in seen or seen.add(n))
            raise ValueError(f"AgentSpec.tools_v2 has duplicate tool name: {dup!r}")

        # If validate_form is declared, the system prompt should mention it
        # so the agent runs the cheap check before paid tools. Soft nudge —
        # we accept any case-insensitive substring match.
        has_validate = any(t.kind == "validate_form" for t in self.tools_v2)
        if has_validate:
            sp = (self.system_prompt or "").lower()
            if "validate_form" not in sp and "validate form" not in sp:
                raise ValueError(
                    "AgentSpec.system_prompt must reference 'validate_form'"
                    " when the validate_form tool is declared, so the agent"
                    " runs the cost-free completeness check before invoking"
                    " paid tools (vision_ocr / LLM calls)."
                )

        # If vision_ocr is declared, validate_form should also be declared
        # whenever the agent could reasonably be invoked with a form. We
        # can't always tell here (the AppSpec is separate), so we only
        # enforce: if both are present, validate_form must come first in
        # the list — matches the system-prompt rule above and keeps the
        # docs/example pattern enforceable.
        has_ocr = any(t.kind == "vision_ocr" for t in self.tools_v2)
        if has_ocr and has_validate:
            kinds = [t.kind for t in self.tools_v2]
            if kinds.index("validate_form") > kinds.index("vision_ocr"):
                raise ValueError(
                    "AgentSpec.tools_v2: 'validate_form' must appear before"
                    " 'vision_ocr' so the runtime registers the cost-gate"
                    " before the OCR tool."
                )
        return self


# ---------------------------------------------------------------------------
# AppSpec — panels
# ---------------------------------------------------------------------------


class Theme(BaseModel):
    model_config = ConfigDict(extra="forbid")

    primary: Optional[str] = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    # Accent colour (runtime maps to --citra-accent; KPI accent bar). Optional.
    accent: Optional[str] = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    logo_url: Optional[str] = None
    dark_mode: bool = False
    # Localisation — drives currency/number/date formatting in the runtime
    # (charts + KPI tiles). Inferred by the builder from the tenant + data +
    # the BA's language; default en-US / USD when unspecified.
    locale: Optional[str] = Field(
        default=None,
        description="BCP-47 locale, e.g. 'en-US', 'en-IN', 'en-GB'. Default en-US.",
    )
    currency: Optional[str] = Field(
        default=None,
        description="ISO-4217 currency, e.g. 'USD', 'INR', 'EUR'. Default USD.",
    )
    # ── Theme v2 (docs/runtime-ui-modernization-plan.md Track A) ──────────
    # All optional; unset reproduces the classic look. CLOSED enums — publish
    # rejects unknown values, the runtime maps them to CSS tokens in one place.
    #: Customer display name shown in the app header + browser title. Publish
    #: defaults it from the ontology's `organization` block (Track E); the
    #: builder may override per app.
    company_name: Optional[str] = Field(default=None, max_length=120)
    #: Font-stack preset (bundle-safe stacks, no external font fetch).
    font: Optional[Literal["inter", "source-sans", "ibm-plex", "system"]] = None
    #: Corner-radius scale → --citra-radius.
    radius: Optional[Literal["sharp", "soft", "round"]] = None
    #: Spacing scale (padding/gaps) for data-dense vs airy apps.
    density: Optional[Literal["comfortable", "compact"]] = None
    #: Card surface treatment (shadow/backdrop tokens).
    surface: Optional[Literal["flat", "elevated", "glass"]] = None
    #: Color scheme. Supersedes the legacy ``dark_mode`` bool (which remains
    #: honored when ``mode`` is unset): auto follows the OS preference.
    mode: Optional[Literal["light", "dark", "auto"]] = None
    #: Named ECharts palette; "brand" derives a ramp from ``primary``.
    chart_palette: Optional[Literal["calm", "vivid", "mono", "brand"]] = None


class DataSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    type: Literal[
        "mcp",
        "rag",
        "static",
        "smart_app_records",
        "workflow_staging",
        # The app's own decision ledger (docs/money-saved-roi-plan.md V3):
        # flattened decision_records rows — decision_id, case key, mode,
        # decision text, decided_at, outcome label, and the CANONICAL money
        # value stamped by the ontology's value_semantics. Bind ROI/KPI pages
        # here; never recompute 'recovered' from raw sources in an app.
        "decision_ledger",
    ]
    # For ``smart_app_records`` ``ref`` is the row ``kind`` to filter on
    # (e.g. "decision", "queue_item", "approval"). Ownership scoping is
    # automatic: the resolver pins ``app_id`` to the owning AppSpec and
    # honours the caller's org / dept / SA membership. Use
    # ``filters.status`` to narrow further (e.g. {"status": "pending"}).
    ref: str
    doc_types: List[str] = Field(default_factory=list)
    filters: Dict[str, Any] = Field(default_factory=dict)
    # ``smart_app_records`` only — how an APP-OWNED OVERLAY WRITE to this source
    # behaves (ignored for other types):
    #   "merge"  — one doc per (app_id, record_id); upsert MERGES fields
    #              (per-record overlay: status / assignee / latest note).
    #   "thread" — each write APPENDS a new row anchored to the SoR record via
    #              ``thread_of`` (a comment / review HISTORY). A read of this
    #              source returns the list of rows for the record (newest first).
    mode: Literal["merge", "thread"] = "merge"


class SmartAppRecord(BaseModel):
    """One row in the shared ``smart_app_records`` collection.

    Every BA's queue / decision / approval / audit row lives here.
    There is no per-BA collection — multi-tenant isolation comes from
    the ownership fields stamped onto each row by ``mongo_writer``.
    """
    model_config = ConfigDict(extra="forbid")

    record_id: str
    # For a "thread" overlay (DataSource.mode="thread"): the SoR record this
    # comment/review row is anchored to. ``record_id`` is then a fresh unique
    # row id and ``thread_of`` is the stable anchor a thread read filters on.
    # None for merge / queue / workflow rows.
    thread_of: Optional[str] = None
    app_id: Optional[str] = None
    org_id: Optional[str] = None
    dept_ids: List[str] = Field(default_factory=list)
    owner_type: Optional[Literal["service_account", "dept", "org"]] = None
    owner_id: Optional[str] = None
    author_user_id: Optional[str] = None
    source: Literal["workflow", "user", "agent"] = "workflow"
    source_workflow_id: Optional[str] = None
    source_run_id: Optional[str] = None
    kind: str  # "decision" | "queue_item" | "approval" | "audit" | ...
    status: Optional[str] = None
    data: Dict[str, Any] = Field(default_factory=dict)
    deleted_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class ToolButton(BaseModel):
    """A panel button that invokes a `tools_v2` tool directly, bypassing
    the LLM. Use for deterministic side-effects where the BA already
    knows the tool name and its arguments don't need natural-language
    reasoning (e.g. "Refresh", "Submit for Approval", "Verify Policy").

    The runtime gates each click on:
      1. ``tool_name`` exists in ``agent_spec.tools_v2``
      2. ``tool_name`` is listed in *this* panel's ``tool_buttons[]``
         (a leaked /apps/{slug}/tool/{name} call cannot escape the
         panel's allowlist)
      3. The user's JWT passes the standard panel ``permissions`` check
    """

    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=80)
    tool_name: str = Field(
        pattern=r"^[a-z][a-z0-9_.]*$",
        max_length=120,
        description="Must match a `tools_v2[].name` on the linked AgentSpec.",
    )
    args: Dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Static / templated arguments forwarded to the tool. Field"
            " references like ``{form.policy_number}`` are resolved by"
            " the renderer from the panel's current state before POST."
        ),
    )
    confirm: Optional[str] = Field(
        default=None,
        max_length=200,
        description=(
            "When set, the renderer shows a confirm dialog with this"
            " text before invoking. Required (publish-time) for buttons"
            " bound to a write (kind='mcp_action') tool — always set it"
            " for destructive / irreversible direct writes."
        ),
    )
    roles: Optional[List[str]] = Field(
        default=None,
        description=(
            "Optional per-button role allowlist. When set, only users"
            " holding at least one of these roles may fire the button"
            " (enforced server-side in /apps/{slug}/tool/{name}, on top"
            " of the panel's permissions). Use to restrict a direct write"
            " (e.g. assign / transfer / close) to supervisors without"
            " splitting it onto a separate panel."
        ),
    )


#: The CLOSED icon vocabulary (runtime-ui-modernization-plan.md Track B) —
#: kebab-case lucide names with a static import map in the runtime
#: (citra-app-runtime/src/components/Icon.tsx — keep the two in sync; tsc
#: fails on a name lucide doesn't export). Enforced at PUBLISH (rule I-01) so
#: legacy stored specs with stray names still load; the runtime renders
#: nothing for an unknown name (never a broken glyph).
ICON_NAMES: frozenset = frozenset({
    "activity", "alert-circle", "alert-triangle", "archive", "arrow-down",
    "arrow-right", "arrow-up", "award", "banknote", "bar-chart-3",
    "battery-charging", "bell", "book-open", "briefcase", "building",
    "building-2", "calendar", "calendar-check", "calendar-clock", "camera",
    "check", "check-circle", "circle-dollar-sign", "clipboard-check",
    "clipboard-list", "clock", "coins", "compass", "credit-card", "database",
    "dollar-sign", "download", "droplets", "eye", "factory", "file-check",
    "file-search", "file-text", "file-warning", "filter", "flag", "flame",
    "folder-open", "gauge", "gavel", "globe", "hammer", "hard-hat",
    "heart-pulse", "history", "home", "image", "inbox", "info", "key",
    "landmark", "layers", "layout-dashboard", "lightbulb", "line-chart",
    "list-checks", "lock", "mail", "map", "map-pin", "message-circle",
    "message-square", "monitor", "package", "paperclip", "pause-circle",
    "percent", "phone", "pie-chart", "piggy-bank", "play-circle", "plug",
    "plug-zap", "receipt", "refresh-cw", "route", "scale", "scan-line",
    "search", "send", "settings", "shield", "shield-alert", "shield-check",
    "siren", "sparkles", "star", "sun", "table", "tag", "target",
    "thermometer", "thumbs-down", "thumbs-up", "timer", "traffic-cone",
    "trending-down", "trending-up", "truck", "upload", "user", "user-check",
    "users", "wallet", "wrench", "x-circle", "zap",
})


class _PanelBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    title: Optional[str] = None
    #: Lucide icon shown beside the panel title (closed set — publish rule
    #: I-01 rejects names outside models.ICON_NAMES).
    icon: Optional[str] = Field(default=None, max_length=40)
    permissions: List[str] = Field(default_factory=list)
    tool_buttons: List[ToolButton] = Field(
        default_factory=list,
        description=(
            "UI buttons that invoke tools_v2 entries directly (no LLM)."
            " Each button is rendered alongside the panel's primary"
            " content. Validated at publish time against the bound"
            " AgentSpec's tools_v2 list."
        ),
    )


class NavigateTarget(BaseModel):
    """Client-side navigation directive.

    Used in ``FormPanel.on_submit.navigate`` and ``QueueAction.navigate`` to
    move the user to another page after an action fires. ``params`` values
    may use templates:

    - ``"{row.<field>}"`` on a queue action — substitutes the clicked row's
      field at navigate-time.
    - ``"{result.<field>}"`` on a form on_submit — substitutes the agent's
      action output.
    - ``"{form.<field>}"`` on a form on_submit — substitutes a field the
      user typed into the form.
    """

    model_config = ConfigDict(extra="forbid")

    page: str = Field(
        min_length=1,
        description="Target page id. Must reference AppSpec.pages[].id.",
    )
    params: Dict[str, str] = Field(default_factory=dict)


class FormOnSubmit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_action: Optional[str] = None
    # Direct (no-LLM) submit: fire this tools_v2 tool with the form's field
    # values as the arguments — for classic write forms (add comment, assign
    # with a reason, transfer with a target) where the user's input IS the
    # payload and no agent reasoning is needed. Mutually exclusive with
    # agent_action. Must be a kind="mcp_action" (write) tool.
    tool_name: Optional[str] = Field(
        default=None,
        pattern=r"^[a-z][a-z0-9_.]*$",
        max_length=120,
        description=(
            "Direct-write tools_v2 tool fired on submit with the form fields"
            " as arguments — NO LLM. Use instead of agent_action for"
            " deterministic user writes. Routed to /apps/{slug}/tool/{name}."
        ),
    )
    navigate: Optional[NavigateTarget] = Field(
        default=None,
        description=(
            "Page to navigate to after the form submits. When combined with"
            " agent_action / tool_name the write runs first; navigation fires"
            " only on success, with {result.*} substitution available."
        ),
    )

    @model_validator(mode="after")
    def _require_action_or_navigate(self) -> "FormOnSubmit":
        if self.agent_action and self.tool_name:
            raise ValueError(
                "FormPanel.on_submit cannot set both 'agent_action' (LLM path)"
                " and 'tool_name' (direct-write path) — pick one"
            )
        if not self.agent_action and not self.tool_name and not self.navigate:
            raise ValueError(
                "FormPanel.on_submit must declare at least one of"
                " 'agent_action', 'tool_name', or 'navigate'"
            )
        return self


class FormStep(BaseModel):
    """One step of a multi-step (wizard) form. ``fields`` lists the schema
    property names shown on this step; the runtime paginates the form into
    steps with Back / Next and submits on the last step. Any property not
    named in any step renders on the final step (nothing is silently dropped)."""

    model_config = ConfigDict(extra="forbid")

    title: str
    fields: List[str] = Field(min_length=1)
    description: Optional[str] = None


class FormPanel(_PanelBase):
    type: Literal["form"]
    schema_ref: Optional[str] = None
    schema_inline: Optional[Dict[str, Any]] = None
    on_submit: Optional[FormOnSubmit] = None
    # EDIT mode (basic CRUD update, outside the agent loop). "create" (default)
    # is a blank form that inserts. "edit" PREFILLS the form from an existing
    # record and saves changes back to it: the runtime reads the record named by
    # the page's ?id= from ``prefill_source`` (a data_source id), seeds each
    # field's value, and on submit re-includes ``key_field`` so on_submit writes
    # an UPDATE (governed: validated + audited like any write), not a new row.
    mode: Literal["create", "edit"] = "create"
    prefill_source: Optional[str] = Field(
        default=None,
        description=(
            "edit mode: the data_source id to read the current record from"
            " (matched by ?id= against key_field). Required when mode='edit'."
        ),
    )
    key_field: Optional[str] = Field(
        default=None,
        description=(
            "edit mode: the record's identifier column, SELECTED FROM THE DATASET"
            " CATALOGUE (whatever the key column is named there — not a fixed name)."
            " Used to fetch current values AND re-sent on submit so the write"
            " targets the existing record. Required when mode='edit'."
        ),
    )
    # Optional wizard: group the form's fields into ordered steps. Omit for a
    # plain single-page form.
    steps: List[FormStep] = Field(default_factory=list)
    accepts_files: bool = Field(
        default=False,
        description=(
            "When True, the panel renders a file-upload widget alongside"
            " the form fields. The runtime forwards uploaded images /"
            " PDFs to the agent as part of the action input. The agent"
            " is then expected to use the vision_ocr tool to read them."
            " Publish-time validators reject AppSpec / AgentSpec pairs"
            " that have accepts_files=True without a vision_ocr tool."
        ),
    )
    accepted_file_types: List[str] = Field(
        default_factory=list,
        description=(
            "Optional MIME-type allowlist (e.g. ['image/jpeg', 'image/png',"
            " 'application/pdf']). Empty = accept any image / PDF. The"
            " runtime enforces this before calling vision_ocr."
        ),
    )

    @model_validator(mode="after")
    def _edit_needs_binding(self) -> "FormPanel":
        if self.mode == "edit" and not (self.prefill_source and self.key_field):
            raise ValueError(
                "FormPanel mode='edit' requires both 'prefill_source' (the"
                " data_source to read current values from) and 'key_field' (the"
                " record key column)"
            )
        return self


class QueueAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str
    #: Lucide icon on the action button (closed set — publish rule I-01).
    icon: Optional[str] = Field(default=None, max_length=40)
    agent_action: Optional[str] = Field(
        default=None,
        description=(
            "Agent action to invoke. Mutually compatible with navigate —"
            " both run, action first, navigation after."
        ),
    )
    navigate: Optional[NavigateTarget] = Field(
        default=None,
        description=(
            "Page to navigate to when the action is clicked. Typical use:"
            " queue-row drill-down to a detail page."
        ),
    )
    args: Dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Constant inputs merged into the agent_action call on top of"
            " the clicked row. Use this to supply a value the action's"
            " input_schema requires but the queue has no column for"
            " (e.g. a fixed record_type / category discriminator). Keys"
            " here override row keys of the same name."
        ),
    )
    is_row_click: bool = Field(
        default=False,
        description=(
            "When True, this action also fires on plain row click (not just"
            " its button). Only one action per queue may have this set."
        ),
    )

    @model_validator(mode="after")
    def _require_action_or_navigate(self) -> "QueueAction":
        if not self.agent_action and not self.navigate:
            raise ValueError(
                "QueueAction must declare at least one of 'agent_action' or"
                " 'navigate' (got neither for label="
                f"'{self.label}')"
            )
        return self


class QueueSort(BaseModel):
    model_config = ConfigDict(extra="forbid")

    column: str
    dir: Literal["asc", "desc"] = "asc"


class QueuePanel(_PanelBase):
    type: Literal["queue"]
    data_source: str
    query: Dict[str, Any] = Field(default_factory=dict)
    columns: List[str] = Field(default_factory=list)
    actions: List[QueueAction] = Field(default_factory=list)
    # Presentation options. All optional — the runtime auto-detects sensible
    # defaults when absent, so older specs render unchanged.
    # "split" (runtime-ui-modernization-plan.md C4) = master-detail two-pane:
    # the record list on the left, the selected record's full fields on the
    # right — modern triage without a page hop.
    view: Literal["cards", "table", "kanban", "split"] = "cards"
    page_size: Optional[int] = Field(default=None, ge=1, le=200)
    searchable_columns: List[str] = Field(default_factory=list)
    filters: List[str] = Field(default_factory=list)
    badge_column: Optional[str] = None
    #: Semantic colors for badge_column VALUES (C5) — e.g.
    #: {"pending": "amber", "recovered": "green", "written_off": "red"}.
    #: Semantic names only (never hex) so apps can't become circus posters and
    #: dark mode keeps working. Unmapped values render the neutral badge.
    badge_colors: Dict[
        str, Literal["green", "amber", "red", "blue", "slate"]
    ] = Field(default_factory=dict)
    #: Columns rendered de-emphasized (smaller, muted) after the main ones —
    #: metadata that should be present but not compete (C5).
    secondary_columns: List[str] = Field(default_factory=list)
    #: Display formatting per column (C7, closed set): status_pill (colored
    #: pill via badge_colors), currency (theme-locale money), relative_time
    #: ("2 days ago"), progress (0-1 or 0-100 bar), grade (the scorecard chip
    #: — an empty value renders "gated" rather than blank, because a case that
    #: failed policy has no grade and must not read as one still being scored).
    #: Unknown columns ignored.
    column_formats: Dict[
        str, Literal["status_pill", "currency", "relative_time", "progress", "grade"]
    ] = Field(default_factory=dict)
    title_column: Optional[str] = None
    default_sort: Optional[QueueSort] = None
    # view="kanban" only: the column whose distinct values become the board's
    # columns (e.g. status). Required when view="kanban"; ignored otherwise.
    group_by: Optional[str] = None

    @model_validator(mode="after")
    def _kanban_needs_group_by(self) -> "QueuePanel":
        if self.view == "kanban" and not self.group_by:
            raise ValueError(
                "QueuePanel with view='kanban' must set 'group_by' (the column"
                " whose values become the board columns)"
            )
        return self


class DetailSection(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: Literal[
        "agent_timeline",
        "agent_chat",
        "approval",
        "attachment",
        "comments",
        "documents",
        "fields",
        "markdown",
    ]
    title: Optional[str] = None
    #: Lucide icon beside the section title (closed set — publish rule I-01).
    icon: Optional[str] = None
    agent_role: Optional[str] = None
    data_source: Optional[str] = None
    # For type="attachment": the record column(s) holding a file the MCP serves
    # (a URL, or a {url|data, content_type, filename} object). Rendered as an
    # image preview / open-document / download per the value's content type.
    fields: Optional[List[str]] = None
    roles: Optional[List[str]] = None
    content: Optional[str] = None
    # When True, the section renders inside a collapsible <details> disclosure
    # (accordion). collapsed=True starts it closed. Lets a long detail page
    # tuck secondary sections away without losing them.
    collapsible: bool = False
    collapsed: bool = False


class DetailAction(BaseModel):
    """A button on a detail panel that runs an agent action on THIS record.

    Why this exists rather than reusing ``QueueAction``: until now the only
    place an ``agent_action`` could hang was a queue action, so a surface that
    shows exactly one record — an embed card — had to carry a one-row queue
    purely to hold the button. That queue then rendered a search box, a
    Cards/Table/Split switcher and a row counter over a data source pinned to
    a single id, none of which can ever do anything, AND repeated every field
    the detail panel below it was already showing.

    ``QueueAction`` is the wrong shape to reuse: ``is_row_click`` is
    meaningless without rows, and silently accepting it would let a builder
    author something that reads as configured and does nothing.
    """

    model_config = ConfigDict(extra="forbid")

    label: str
    #: Lucide icon on the action button (closed set — publish rule I-01).
    icon: Optional[str] = Field(default=None, max_length=40)
    agent_action: str = Field(
        description=(
            "Agent action to invoke, with the panel's resolved record as the"
            " inputs. Required — unlike a queue action there is no navigate"
            " alternative, because a detail panel IS the destination."
        ),
    )
    args: Dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Constant inputs merged on top of the record. Use for a value the"
            " action's input_schema requires but the record has no column for."
            " Keys here override record keys of the same name."
        ),
    )


class DetailPanel(_PanelBase):
    type: Literal["detail"]
    actions: List[DetailAction] = Field(
        default_factory=list,
        description=(
            "Buttons that run an agent action against the record this panel"
            " shows. On an EMBED page this is the trigger — it replaces the"
            " one-row queue that used to exist only to hold the button."
        ),
    )
    linked_to: Optional[str] = Field(
        default=None,
        description=(
            "Queue/table panel this detail reads its record from. The"
            " classic binding: the officer clicks a row, the detail resolves"
            " that record from the queue's data_source. Mutually exclusive"
            " with ``data_source``."
        ),
    )
    data_source: Optional[str] = Field(
        default=None,
        description=(
            "Read the record DIRECTLY from this data source instead of"
            " through a queue. For surfaces where the record id arrives from"
            " outside and there is no list to click — principally an EMBED"
            " page, where the host application already knows which record"
            " the officer is looking at and passes it in. Mutually exclusive"
            " with ``linked_to``."
        ),
    )
    id_field: Optional[str] = Field(
        default=None,
        description=(
            "Column identifying one record, on the linked queue's source or"
            " on ``data_source``. The detail view matches the ?id= page param"
            " against it. When omitted, the runtime auto-detects a column"
            " named id / *_id / record_id."
        ),
    )

    @model_validator(mode="after")
    def _one_record_binding(self) -> "DetailPanel":
        """A detail panel must resolve its record from exactly one place.

        Neither set is the failure that motivated ``data_source``: the panel
        renders its chrome, resolves no record, and shows an empty card. Both
        set is ambiguous — the resolver would silently prefer one and the
        author would never learn which. Fail at publish, where the builder
        sees it, rather than at render, where an officer does.
        """
        if not self.linked_to and not self.data_source:
            raise ValueError(
                f"detail panel '{self.id}' must set either linked_to (read the"
                " record from a queue the officer clicks) or data_source (read"
                " it directly by id — used on embed pages). It sets neither, so"
                " it can never resolve a record."
            )
        if self.linked_to and self.data_source:
            raise ValueError(
                f"detail panel '{self.id}' sets BOTH linked_to and data_source."
                " Pick one: linked_to for a queue-driven page, data_source for"
                " an embed page where the record id arrives from the host."
            )
        return self
    #: "profile" (C6) tops the page with a header card — the record's key
    #: facts (header_fields) + a status pill — before the sections, replacing
    #: the flat label:value wall. "stack" = classic.
    layout: Literal["stack", "profile"] = "stack"
    #: layout="profile" only: 2-5 columns shown large in the header card.
    header_fields: List[str] = Field(default_factory=list)
    #: layout="profile" only: column rendered as the header's status pill
    #: (colored via ``status_colors``, same semantic names as badge_colors).
    status_field: Optional[str] = None
    status_colors: Dict[
        str, Literal["green", "amber", "red", "blue", "slate"]
    ] = Field(default_factory=dict)
    sections: List[DetailSection] = Field(default_factory=list)


class MetricCompare(BaseModel):
    """Prior-period comparison for a KPI tile (the ▲/▼ delta chip).

    The runtime computes the metric over the latest period PRESENT IN THE DATA
    (anchored to ``MAX(date_field)``, not the wall clock) and the period
    ``periods`` grains earlier, both windowed on ``date_field``, and renders
    the percentage change. Anchoring to the data's latest period (not "today")
    keeps the delta meaningful when the data lags the calendar. Use for FLOW
    metrics (assessed latest-day vs prior-day, collected this week vs last) —
    the delta is meaningless for a pure stock count unless you have history.
    """
    model_config = ConfigDict(extra="forbid")

    date_field: str = Field(description="Date/timestamp column to window on.")
    grain: Literal["day", "week", "month", "quarter", "year"] = "day"
    periods: int = Field(default=1, ge=1, le=12)


class MetricTrend(BaseModel):
    """Sparkline series for a KPI tile — a real grouped-by-time aggregate."""
    model_config = ConfigDict(extra="forbid")

    date_field: str = Field(description="Date/timestamp column to bucket by.")
    grain: Literal["day", "week", "month"] = "day"
    points: int = Field(default=14, ge=2, le=60)


class DashboardMetric(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    agg: Literal["count", "sum", "avg", "min", "max", "ratio"]
    field: Optional[str] = None
    window: Optional[str] = None
    data_source: Optional[str] = None
    # Predicate scoping the metric so the number matches its label — e.g.
    # {"status": "active"} so "Active outages" counts only active rows, not
    # the whole table. Same shape as DataSource.filters / ChartPanel.query
    # ({col: value | {$in:[...]} | {$gt: x} | …}). Pushed into the WHERE.
    filter: Optional[Dict[str, Any]] = None
    # Optional ▲/▼ delta vs a prior period, computed server-side.
    compare: Optional[MetricCompare] = None
    # Optional sparkline series (grouped-by-time), computed server-side.
    trend: Optional[MetricTrend] = None
    # Clean display subtitle. When omitted the runtime falls back to the agg
    # + field id — set this to avoid leaking raw field names on the tile.
    label: Optional[str] = None
    #: Lucide icon on the KPI tile (closed set — publish rule I-01). When
    #: omitted the runtime auto-picks by semantics (money → banknote,
    #: date → calendar, count → hash-free neutral).
    icon: Optional[str] = Field(default=None, max_length=40)
    # Progress-to-target: when set, the tile renders a progress bar of value
    # against this target (e.g. SLA quota, collection goal). thresholds are
    # ascending cut points that colour the bar (e.g. [0.5, 0.8] → red/amber/
    # green bands as a FRACTION of target). Both optional + advisory.
    target: Optional[float] = None
    thresholds: Optional[List[float]] = Field(default=None, max_length=4)


class DashboardPanel(_PanelBase):
    type: Literal["dashboard"]
    metrics: List[DashboardMetric] = Field(min_length=1)


class HeroPanel(_PanelBase):
    """Page-header band (runtime-ui-modernization-plan.md C1): icon +
    headline + optional live metric + up to two navigation actions. One per
    page, first panel — it gives the page a designed opening instead of a
    title floating over a grid. Actions are NAVIGATION only (no agent_action
    — a hero is chrome, not a work surface)."""

    type: Literal["hero"]
    headline: str = Field(min_length=1, max_length=120)
    subtitle: Optional[str] = Field(default=None, max_length=240)
    #: Optional live number rendered large in the band (same server-side
    #: aggregation as a dashboard KPI tile).
    metric: Optional[DashboardMetric] = None
    actions: List[QueueAction] = Field(default_factory=list, max_length=2)

    @model_validator(mode="after")
    def _hero_actions_navigate_only(self) -> "HeroPanel":
        for a in self.actions:
            if a.agent_action:
                raise ValueError(
                    "hero actions are navigation-only — wire agent actions on"
                    " a queue or detail panel, not the page header")
        return self


class StatStripPanel(_PanelBase):
    """Compact horizontal KPI band (C2) — the same server-computed metrics as
    a dashboard panel, rendered as a dense strip WITH delta arrows and
    sparklines (give metrics ``compare``/``trend`` or the strip is just small
    numbers). 2-6 metrics; one strip per page reads best."""

    type: Literal["stat_strip"]
    metrics: List[DashboardMetric] = Field(min_length=2, max_length=6)


class TimelinePanel(_PanelBase):
    """Vertical event feed (C3) bound to any tabular data source: one entry
    per row, newest first, positioned by ``date_field``. Case histories,
    decision ledgers, outage logs. Read-only."""

    type: Literal["timeline"]
    data_source: str
    query: Dict[str, Any] = Field(default_factory=dict)
    date_field: str = Field(description="Row column holding the event date/time.")
    title_field: str = Field(description="Row column shown as the entry title.")
    subtitle_field: Optional[str] = None
    #: Optional column whose VALUE is a lucide icon name per entry (falls
    #: back to the panel icon, then a dot).
    icon_field: Optional[str] = None
    #: Optional low-cardinality column rendered as a small badge per entry.
    badge_field: Optional[str] = None
    badge_colors: Dict[
        str, Literal["green", "amber", "red", "blue", "slate"]
    ] = Field(default_factory=dict)
    limit: Optional[int] = Field(default=None, ge=1, le=200)


class ChartPanel(_PanelBase):
    type: Literal["chart"]
    chart_type: Literal["line", "bar", "pie", "area", "funnel", "scatter"]
    data_source: str
    query: Dict[str, Any] = Field(default_factory=dict)
    x: str
    y: Union[str, List[str]]
    group_by: Optional[str] = None
    limit: Optional[int] = Field(default=None, ge=1, le=500)
    stacked: bool = False
    # Optional data-semantics hints (populated by citra-dashboard-spec).
    # time_grain controls time bucketing; aggregation controls how y is
    # rolled up. Both are advisory hints for the chart renderer.
    time_grain: Optional[
        Literal["minute", "hour", "day", "week", "month", "quarter", "year"]
    ] = None
    aggregation: Optional[
        Literal["count", "sum", "avg", "min", "max"]
    ] = None

    @model_validator(mode="before")
    @classmethod
    def _drop_retired_fields(cls, data: Any) -> Any:
        """Tolerate retired ChartPanel fields on stored specs so an old doc
        never 500s on load just because a field was removed. ``time_range``
        was replaced by ``time_grain`` + the panel ``query`` predicate; drop
        it (debug-logged) instead of failing extra='forbid'. Republishing the
        app removes it from storage for good.
        """
        if isinstance(data, dict) and "time_range" in data:
            data = {k: v for k, v in data.items() if k != "time_range"}
            logger.debug("ChartPanel: dropped retired 'time_range' field")
        return data


class AgentChatPanel(_PanelBase):
    type: Literal["agent_chat"]
    agent_role: Optional[str] = None
    starter_prompts: List[str] = Field(default_factory=list)


class DocumentViewPanel(_PanelBase):
    type: Literal["document_view"]
    data_source: str
    doc_types: List[str] = Field(default_factory=list)


class MarkdownPanel(_PanelBase):
    type: Literal["markdown"]
    content: str


class NoticePanel(_PanelBase):
    """A static, non-interactive callout band — info / warning / error /
    success. Surfaces guidance, an SLA caveat, or a procedural notice at the
    top of a page. Carries NO data binding (for a live number use a dashboard
    KPI tile). ``content`` is plain text / inline markdown; the runtime renders
    it safely — never raw HTML."""

    type: Literal["notice"]
    tone: Literal["info", "warn", "error", "success"] = "info"
    content: str = Field(
        min_length=1,
        description="The notice body — plain text or inline markdown.",
    )


class CalendarPanel(_PanelBase):
    """A month-grid calendar of records from a tabular data source. Each row
    with a value in ``date_field`` is placed on its day; ``title_field`` labels
    the event and ``color_field`` (optional, low-cardinality) tints it. Read-
    only; clicking a day's event can navigate via the queue/detail pattern in a
    future revision."""

    type: Literal["calendar"]
    data_source: str
    query: Dict[str, Any] = Field(default_factory=dict)
    date_field: str = Field(description="Row column holding the event date (ISO date or datetime).")
    title_field: str = Field(description="Row column shown as the event label.")
    color_field: Optional[str] = Field(
        default=None,
        description="Optional low-cardinality column whose value tints the event chip.",
    )
    limit: Optional[int] = Field(default=None, ge=1, le=500)


class MapPanel(_PanelBase):
    """A geospatial marker map (Leaflet / OpenStreetMap) of records from a
    tabular data source. Each row with numeric ``lat_field`` + ``lng_field``
    drops a marker; ``label_field`` (optional) names it in the popup. Read-only
    overview — for editing a location use a form."""

    type: Literal["map"]
    data_source: str
    query: Dict[str, Any] = Field(default_factory=dict)
    lat_field: str = Field(description="Row column holding latitude (numeric, -90..90).")
    lng_field: str = Field(description="Row column holding longitude (numeric, -180..180).")
    label_field: Optional[str] = Field(
        default=None,
        description="Optional column shown in the marker popup.",
    )
    limit: Optional[int] = Field(default=None, ge=1, le=500)


class FilterControl(BaseModel):
    """One control in a ``filter_bar`` — binds a page param to a selectable
    value. On change the runtime updates the URL query (``?<param>=…``) and the
    page re-renders server-side, so every panel whose query references
    ``{param.<param>}`` re-queries. View-only: a filter sets the view's params,
    it never writes to a source system."""

    model_config = ConfigDict(extra="forbid")

    param: str = Field(
        pattern=r"^[a-zA-Z_][a-zA-Z0-9_]*$",
        description="The page param this control sets; panels reference it as {param.<param>}.",
    )
    label: str = Field(min_length=1, max_length=60)
    control_type: Literal["dropdown", "segment", "daterange"] = "dropdown"
    options: Optional[OptionsSource] = Field(
        default=None,
        description=(
            "Where the control's choices come from — a static list or live "
            "DISTINCT values from a declared data_source (reuses OptionsSource, "
            "resolved by the /field-options endpoint). Required for "
            "dropdown/segment; ignored for daterange."
        ),
    )
    default: Optional[str] = Field(
        default=None,
        description="Initial value pushed to the URL on first load when the param is absent.",
    )
    all_label: Optional[str] = Field(
        default="All",
        description="Label for the clear/no-filter option (sets the param to empty).",
    )

    @model_validator(mode="after")
    def _options_required_for_select(self) -> "FilterControl":
        if self.control_type in ("dropdown", "segment") and self.options is None:
            raise ValueError(
                f"filter control '{self.param}' is a {self.control_type} and "
                "requires an 'options' source (static values or a data_source)."
            )
        return self


class FilterBarPanel(_PanelBase):
    """A declarative strip of filter controls bound to page params. Allowed on
    BOTH dashboard and standard pages. Each control sets a URL param on change;
    the page re-renders and every panel referencing ``{param.<param>}``
    re-queries. No per-app code, no data writes — purely sets the view's filter
    params, riding the existing param → server-render → re-query plumbing."""

    type: Literal["filter_bar"]
    controls: List[FilterControl] = Field(min_length=1, max_length=6)


class NotificationFeed(BaseModel):
    """One feed in a notification centre — a FULLY builder-defined attention
    list. The platform does NOT hardcode what a notification is: a feed reads
    any ``data_source`` with any ``filters`` predicate and maps catalogue columns
    to the item's title/subtitle. (The one built-in is ``kind='approvals'``,
    which reads the platform's reviewer inbox — not a data_source.)"""

    model_config = ConfigDict(extra="forbid")

    label: str = Field(
        description="group/badge label for items from this feed (e.g. 'Overdue', 'High value', 'Approvals')."
    )
    kind: Literal["data_source", "approvals"] = Field(
        default="data_source",
        description=(
            "'data_source' — read rows of `source` matching `filters`."
            " 'approvals' — the platform reviewer inbox (pending recommendations);"
            " ignores source/filters/title_field/sub_field."
        ),
    )
    source: Optional[str] = Field(
        default=None,
        description="data_source id to read (required when kind='data_source').",
    )
    filters: Dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "predicate selecting which rows are notable — ANY condition, with"
            " columns DERIVED FROM THE DATASET CATALOGUE (no fixed names). Mongo-"
            " style operators are supported ({col:{'$lt':x}}, {'$gte'}, {'$in':[…]},"
            " bare equality). For time-relative conditions use the tokens"
            " {now}, {now-<N><unit>}, {now+<N><unit>} (unit = s|m|h|d|w), e.g."
            " an overdue feed = {<due column>: {'$lt': '{now}'}}; a 'stale > 48h'"
            " feed = {<opened column>: {'$lt': '{now-48h}'}}."
        ),
    )
    title_field: Optional[str] = Field(
        default=None,
        description="catalogue column to title each item (falls back to an id-like column).",
    )
    sub_field: Optional[str] = Field(
        default=None,
        description="catalogue column for the item's secondary line (optional).",
    )
    tone: Optional[Literal["info", "success", "warning", "danger", "neutral"]] = Field(
        default=None,
        description="badge colour for this feed's items (default neutral).",
    )
    navigate: Optional[NavigateTarget] = Field(
        default=None,
        description=(
            "where a click on this feed's item routes; navigate.params are templated"
            " from the item's row via {row.<column>} (a real catalogue column)."
        ),
    )
    limit: int = Field(default=50, ge=1, le=200)

    @model_validator(mode="after")
    def _ds_needs_source(self) -> "NotificationFeed":
        if self.kind == "data_source" and not self.source:
            raise ValueError(
                "NotificationFeed kind='data_source' requires 'source' (a data_source id)"
            )
        return self


class NotificationsPanel(_PanelBase):
    """A notification centre — a read-only list of attention items. Fully
    GENERIC: the builder declares one or more ``feeds`` (see NotificationFeed);
    a notification can surface ANYTHING the builder defines (overdue, flagged,
    high-value, awaiting-review, …), not a fixed SLA/approvals pair. Each feed's
    items render with its label/tone and route via its own ``navigate``."""

    type: Literal["notifications"]
    feeds: List[NotificationFeed] = Field(
        min_length=1,
        max_length=10,
        description="the feeds this centre aggregates — fully builder-defined.",
    )


Panel = Annotated[
    Union[
        FormPanel,
        QueuePanel,
        DetailPanel,
        DashboardPanel,
        HeroPanel,
        StatStripPanel,
        TimelinePanel,
        ChartPanel,
        AgentChatPanel,
        DocumentViewPanel,
        MarkdownPanel,
        NoticePanel,
        CalendarPanel,
        MapPanel,
        FilterBarPanel,
        NotificationsPanel,
    ],
    Field(discriminator="type"),
]


# ---------------------------------------------------------------------------
# Pages — multi-page apps
# ---------------------------------------------------------------------------
#
# AppSpec supports two layout modes (mutually exclusive at v0):
#
#   1) Single-page (legacy): top-level ``panels[]`` is non-empty and the
#      runtime synthesises one implicit Page {id="main", path="/",
#      panels=panels[]}. ``pages[]`` MUST be empty in this mode.
#
#   2) Multi-page: top-level ``pages[]`` is non-empty. Each page is rendered
#      at /{slug}/{page.id} and the runtime ignores top-level ``panels[]``.
#      The first page (or the page whose ``path`` is "/") is the landing
#      page.
#
# The runtime + builder skill (citra-app-ui-design) prefer mode 2 for any
# non-trivial app; mode 1 stays for trivial single-screen tools and edit-
# flow back-compat with older specs.


PageLayout = Literal["grid", "stack", "split", "tabs"]
# PageKind is the PURPOSE of a page, distinct from PageLayout (panel
# arrangement). 'standard' = an ordinary app page (queues / forms / detail /
# documents / chat). 'dashboard' = the executive treatment: KPI + chart grid
# rendered with the ECharts exec theme, topped by the hero-brief copilot
# (the app's narrator agent run in read-only chat_mode). A "dashboard" is
# simply an app whose primary page has kind='dashboard'.
# 'embed' = a single decision card rendered INSIDE a customer's own
# application by the citra.js bundle. Same panels, same recommend→approve
# loop, no Citra chrome: no navigation, no queue (the host's screen owns the
# list and passes the record id in). An "embeddable card" is simply an app
# whose primary page has kind='embed'. See docs/embeddable-decision-ui-plan.md
# and the citra-embed-spec skill.
PageKind = Literal["standard", "dashboard", "embed"]


class PageParam(BaseModel):
    """Declared URL query param for a page.

    Panels inside the page can reference these via ``{param.<name>}`` in
    ``query`` filters or ``data_source`` template fields. The runtime
    substitutes them server-side when fetching panel data.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    type: Literal["string", "number", "boolean"] = "string"
    required: bool = False
    description: Optional[str] = Field(default=None, max_length=200)


class Page(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(
        pattern=r"^[a-z][a-z0-9_-]*$",
        description="Stable page id. Used in URL (/{slug}/{id}) and as the target of navigate actions.",
    )
    path: Optional[str] = Field(
        default=None,
        pattern=r"^/[a-zA-Z0-9/_:-]*$",
        description=(
            "Optional explicit URL path under /{slug}. Defaults to '/{id}'."
            " Use '/' for the landing page."
        ),
    )
    title: Optional[str] = Field(default=None, max_length=80)
    icon: Optional[str] = Field(
        default=None,
        max_length=40,
        description="Lucide icon name rendered in the page nav (e.g. 'home', 'inbox').",
    )
    hide_in_nav: bool = Field(
        default=False,
        description=(
            "When True, the page is reachable via navigate() but does not"
            " appear in the sidebar/topbar — typical for detail pages."
        ),
    )
    permissions: List[str] = Field(default_factory=list)
    kind: PageKind = Field(
        default="standard",
        description=(
            "Page purpose. 'standard' (default) = ordinary app page."
            " 'dashboard' = executive treatment: KPI + chart grid rendered"
            " with the ECharts exec theme, topped by the hero-brief copilot"
            " (the app's narrator agent in read-only chat_mode). A dashboard"
            " page allows only chart / dashboard (KPI) / agent_chat / markdown"
            " panels and requires the app to declare agent_id."
        ),
    )
    layout: PageLayout = Field(
        default="grid",
        description=(
            "How panels in this page are arranged. grid = 12-col responsive"
            " (default), stack = full-width single column, split = 50/50"
            " two-column, tabs = each panel becomes a tab."
        ),
    )
    panels: List[Panel] = Field(min_length=1)
    params: List[PageParam] = Field(default_factory=list)


class AppNavigation(BaseModel):
    """Top-level nav chrome for multi-page apps. Ignored when pages[] empty."""

    model_config = ConfigDict(extra="forbid")

    style: Literal["sidebar", "topbar", "none"] = "sidebar"
    default_page: Optional[str] = Field(
        default=None,
        description=(
            "Page id that '/' resolves to. Defaults to the first page in pages[]."
        ),
    )
    show_chat_globally: bool = Field(
        default=False,
        description=(
            "When True, an agent_chat panel (if any) is rendered as a"
            " floating, persistent chat across every page rather than being"
            " placed inside one page."
        ),
    )


class CustomModule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    url: str
    integrity: Optional[str] = None



# ---------------------------------------------------------------------------
# Triggers — how a Smart App run gets started
# ---------------------------------------------------------------------------


TriggerType = Literal[
    "user_action",       # default — fired from a panel action / button click
    "webhook",           # external POST with HMAC, fires once per call
    "schedule.cron",     # cron expression in the trigger's tz
    "schedule.interval", # every N seconds
    "poll",              # tick periodically; call an MCP and fire once per new row
]


class Condition(BaseModel):
    """A deterministic predicate evaluated by the runtime for auto-process —
    NO LLM, NO eval. Exactly ONE form:
      - LEAF: ``field`` + ``op`` (+ ``value``)
      - COMBINATOR: one of ``all`` / ``any`` / ``not_``
      - ``always: true`` — UNCONDITIONAL: auto-commit ALL of the agent's
        decisions that pass the safety bounds (severity ceiling / confidence /
        value_cap). This is the BA explicitly opening auto-process with NO
        business gate — the highest-autonomy mode; the builder must confirm it
        and flag that there is no deterministic business bound.

    The gate BOUNDS which of the agent's decisions auto-commit (blast radius:
    value / severity / scope); it does NOT verify the agent is correct.
    ``field`` addresses the decision context by dotted path:
      - ``payload.<k>``  — the agent's proposed write payload
      - ``row.<k>``      — the source record that triggered the run
      - ``result.<k>``   — the agent's structured output (e.g. ``confidence``)
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    field: Optional[str] = Field(default=None, max_length=200)
    op: Optional[Literal["==", "!=", "<", "<=", ">", ">=", "in", "not_in", "between", "matches"]] = None
    value: Any = None
    all: Optional[List["Condition"]] = None
    any: Optional[List["Condition"]] = None
    not_: Optional["Condition"] = Field(default=None, alias="not")
    always: Optional[bool] = None

    @model_validator(mode="after")
    def _exactly_one_form(self):
        forms = [
            self.field is not None and self.op is not None,
            self.all is not None,
            self.any is not None,
            self.not_ is not None,
            self.always is not None,
        ]
        if sum(1 for f in forms if f) != 1:
            raise ValueError(
                "Condition must be exactly one of: a leaf (field+op[+value]), "
                "one combinator (all / any / not), or always:true"
            )
        return self


class ValueCap(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field: str = Field(max_length=200, description="payload.<k> / row.<k> numeric field to cap.")
    max: float = Field(description="Auto-commit only when the field's value is <= max.")


class AutoProcessPolicy(BaseModel):
    """The per-trigger policy that gates autonomous commit. See
    docs/auto-process-plan.md. The human approves THIS policy (build +
    governance time); the agent then executes it per instance, deterministically.
    """

    model_config = ConfigDict(extra="forbid")

    auto_commit_when: Condition
    # The BA chooses the autonomy bound for THEIR domain. `value_cap`,
    # `confidence_min`, `max_auto_per_run`, `rate_limit_per_hour` and
    # `auto_commit_when` are the BA's optional bounding tools; a write
    # auto-commits only when every bound the BA set is satisfied.
    value_cap: Optional[ValueCap] = None
    confidence_min: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    max_auto_per_run: int = Field(default=50, ge=1, le=1000)
    rate_limit_per_hour: Optional[int] = Field(default=None, ge=1, le=100_000)
    # A non-passing case falls back to auto-recommend (staged for a human);
    # never dropped, never blind-committed. This is robustness on policy
    # misses/errors — NOT a cap on what the BA may choose to auto-commit.
    on_miss: Literal["recommend"] = "recommend"


class Trigger(BaseModel):
    """How an action is invoked outside of a user click.

    All non-user triggers run as the **app's tenant service principal**
    (``user_id = "system:<slug>"``), never as an end-user. HITL gates,
    audit, and tenant scoping all still apply.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    type: TriggerType
    action: str = Field(
        pattern=r"^[a-z][a-z0-9_]*$",
        description="Name of the AgentSpec action this trigger invokes.",
    )
    enabled: bool = True

    # --- execution mode: auto-recommend (default) vs policy-gated auto-process ---
    # recommend     → fire → agent proposes → STAGE for officer approval (today).
    # auto_process  → fire → agent proposes → POLICY GATE → commit autonomously
    #                 when the deterministic rule passes, else fall back to stage.
    execution_mode: Literal["recommend", "auto_process"] = "recommend"
    auto_process_policy: Optional[AutoProcessPolicy] = None
    use_case: Optional[str] = Field(
        default=None, max_length=80,
        description="Friendly label for this trigger/agent in the roster UI.",
    )

    @model_validator(mode="after")
    def _auto_process_needs_policy(self):
        if self.execution_mode == "auto_process" and self.auto_process_policy is None:
            raise ValueError("execution_mode 'auto_process' requires auto_process_policy")
        if self.execution_mode == "recommend" and self.auto_process_policy is not None:
            raise ValueError(
                "auto_process_policy is only valid when execution_mode='auto_process'"
            )
        return self

    # Webhook
    secret_ref: Optional[str] = Field(
        default=None,
        description=(
            "Reference to the HMAC secret used to verify the webhook signature."
            " Format: 'env:NAME' or 'vault://...'. The runtime resolves it"
            " from environment / vault — never inline."
        ),
    )

    # Schedule
    cron: Optional[str] = Field(
        default=None,
        description="5-field cron expression (m h d M w). Tenant tz unless overridden.",
    )
    every_seconds: Optional[int] = Field(
        default=None, ge=30, le=86_400,
        description="Interval triggers fire every N seconds. Min 30s.",
    )
    tz: Optional[str] = Field(default=None, description="IANA tz, e.g. 'America/New_York'.")

    # Poll
    tool: Optional[str] = Field(
        default=None,
        description="MCP tool name to call on each tick (poll triggers).",
    )
    args: Dict[str, Any] = Field(
        default_factory=dict,
        description="Args passed to the poll tool. Use '$last_cursor' as a placeholder.",
    )
    dedup_key: Optional[str] = Field(
        default=None,
        description="Field on each returned row used to dedup vs prior runs.",
    )
    input_template: Dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Template applied to each new row to produce the action's inputs."
            " Use '$row.<path>' to reference fields on the row."
        ),
    )

    # Common
    max_concurrency: int = Field(default=2, ge=1, le=20)


# ---------------------------------------------------------------------------
# Enterprise sharing
# ---------------------------------------------------------------------------


AudienceLevel = Literal["owner", "team", "dept", "org"]


def parse_audience(audience: str) -> tuple[AudienceLevel, Optional[str]]:
    """Split an audience string into (level, target_id).

    Audience grammar:
      'owner'           → ('owner', None)   — only owner_sa_id members
      'team:<sa_id>'    → ('team',  sa_id)  — members of that team SA
      'dept:<dept_id>'  → ('dept',  dept_id) — everyone in that dept
      'org'             → ('org',   None)   — everyone in the tenant
    """
    if audience == "owner":
        return ("owner", None)
    if audience == "org":
        return ("org", None)
    if audience.startswith("team:") and len(audience) > 5:
        return ("team", audience[5:])
    if audience.startswith("dept:") and len(audience) > 5:
        return ("dept", audience[5:])
    raise ValueError(
        "audience must be 'owner', 'org', 'team:<sa_id>', or 'dept:<dept_id>'"
    )


def format_audience(level: AudienceLevel, target_id: Optional[str] = None) -> str:
    if level in ("owner", "org"):
        return level
    if not target_id:
        raise ValueError(f"audience level '{level}' requires a target_id")
    return f"{level}:{target_id}"


# Ordering used by the higher-of(current, target) RBAC rule on audience
# transitions. A change to audience requires the role for the *higher* of
# {current, target}, so narrowing org→dept needs org_admin, not dept_admin.
AUDIENCE_RANK: Dict[AudienceLevel, int] = {
    "owner": 0,
    "team":  1,
    "dept":  2,
    "org":   3,
}


# ---------------------------------------------------------------------------
# Case signature — the closed facet vocabulary that routes clause memory.
# See docs/clause-memory-graph-plan.md §2. Derivation: case_signature.py.
# ---------------------------------------------------------------------------


class FacetSpec(BaseModel):
    """One facet FAMILY. Emits at most one ``family:value`` token per case.

    Most families are a column that ALREADY carries a closed vocabulary in the
    dataset ontology — the builder is not designing a taxonomy, they are marking
    which columns are decision-relevant."""

    model_config = ConfigDict(extra="forbid")

    family: str = Field(
        pattern=r"^[a-z][a-z0-9_]{0,31}$",
        description="Token prefix, e.g. 'loss_type' → 'loss_type:theft'.",
    )
    #: Former names of this family. RENAME BY ALIASING, never by deletion —
    #: the same discipline ReasonCodeSpec.aliases enforces, and it matters MORE
    #: here. A clause is retrieved iff ``scope_facets ⊆ case_facets``, so a
    #: family renamed without an alias leaves every clause scoped to the old
    #: name unable to match ANY case, forever. It is not disabled or flagged —
    #: it sits in the store reading `active` and never fires again. Observed in
    #: prod: a rebuild renamed `income_proof` to `income_proof_type` and the
    #: clause scoped to `income_proof:present` went dark silently.
    #:
    #: Aliasing migrates the TOKEN PREFIX only. If the rename also changed the
    #: value vocabulary (presence `present|absent` → enum `payslip|itr`), the
    #: old values cannot be mapped and those clauses are retired as `orphaned`
    #: rather than rewritten into a scope that never matches.
    aliases: List[str] = Field(default_factory=list, max_length=8)
    kind: Literal["enum", "band", "presence", "age_band", "signal"]
    dataset_id: Optional[str] = Field(
        default=None,
        description="Dataset the column belongs to. Defaults to the app's primary dataset.",
    )

    # kind=enum / band / presence
    from_column: Optional[str] = None
    # kind=enum — the DECLARED closed set. An undeclared value at run time
    # becomes 'family:__unknown' and is counted as ontology drift, never
    # silently absorbed into a new bucket.
    values: Optional[List[str]] = Field(default=None, max_length=40)
    value_map: Optional[Dict[str, str]] = Field(
        default=None, description="Raw value → canonical value, for legacy encodings."
    )
    # kind=band / age_band — ordered, strictly increasing.
    edges: Optional[List[float]] = Field(default=None, min_length=1, max_length=6)
    # kind=age_band — [start_column, end_column]; banded on the day difference.
    from_columns: Optional[List[str]] = Field(default=None, min_length=2, max_length=2)
    # kind=signal — one of case_signature.PLATFORM_SIGNAL_IDS (validated at
    # publish by validate_case_signature; the set is defined next to the
    # derivation that consumes it so a renamed signal breaks loudly).
    signal_id: Optional[str] = Field(default=None, max_length=64)


class ReasonCodeSpec(BaseModel):
    """One entry in the app's closed reject-reason taxonomy. The officer picks
    a code; the code is what aggregates. Free text alone cannot cluster."""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(pattern=r"^[a-z][a-z0-9_]{0,39}$")
    label: str = Field(min_length=1, max_length=60)
    hint: Optional[str] = Field(default=None, max_length=160)
    #: Former names of this code. RENAME BY ALIASING, never by deletion:
    #: clustering hard-partitions corrections by code, so a bare rename splits
    #: one lesson across two generations that can never reach the promotion
    #: gate together. Consolidation normalizes old codes through this list.
    aliases: List[str] = Field(default_factory=list, max_length=8)


class LearningControls(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: DISTINCT officers required before a candidate clause becomes active.
    #: The guard against one prolific officer writing the app's policy.
    promotion_min_officers: int = Field(default=3, ge=1, le=20)
    #: Injection budget. A SELECTION ceiling, not a compression ceiling — the
    #: store is unbounded; this bounds how many relevant clauses fit per case.
    clause_budget_words: int = Field(default=1000, ge=100, le=4000)


class CaseSignature(BaseModel):
    """Per-app facet vocabulary + reason taxonomy + learning controls.

    Lives on the APP, not on the source: two apps bound to the same dataset care
    about different facets, and app_spec changes publish without an MCP image
    rebuild (sources.json changes do not).

    OPTIONAL. Clause memory is the only memory path either way — an app without
    a signature still learns, its clauses simply carry no facet scope (they are
    global within their (modality, task_type) bucket) and its officers pick from
    no reason taxonomy. Declaring one buys SCOPING and CODING, not membership."""

    model_config = ConfigDict(extra="forbid")

    version: int = Field(default=1, ge=1)
    facets: List[FacetSpec] = Field(min_length=1, max_length=24)
    #: DEPRECATED — do not author. Kept only so specs published before the
    #: taxonomy was removed still load; new apps declare none.
    #:
    #: A reason code answered "why was the recommendation wrong", and it could
    #: not carry that weight. It was app-specific vocabulary an LLM had to
    #: invent, and it invented decline reasons: a lending app offered "FOIR
    #: above policy cap" to an officer who was APPROVING a loan the agent had
    #: rejected — every option on screen argued for the decision being
    #: overturned.
    #:
    #: Worse than useless, it was harmful. A clause is retrieved iff
    #: ``scope_facets ⊆ case_facets`` and scope is the cluster's facet
    #: INTERSECTION, so whatever partitions the clusters decides the SCOPE of
    #: every judgement. Partitioning on the code let a hand-picked why choose a
    #: where: two corrections on identical cases never got compared when the
    #: officers picked different labels, each fell below the cluster minimum,
    #: and both were silently dropped.
    #:
    #: Replaced by what the system already observes — facets for the category,
    #: ``contested_fields`` for what changed, and the officer's own sentences
    #: for the lesson.
    reason_codes: List[ReasonCodeSpec] = Field(default_factory=list, max_length=20)
    learning: LearningControls = Field(default_factory=LearningControls)

    # ── Human confirmation of the facet families (publish rule CS-04) ────────
    #
    # The families decide the SCOPE of every judgement this app will ever
    # learn — a clause fires iff ``scope_facets ⊆ case_facets``. They were
    # chosen by the builder agent, from the SOP and the bound columns, and
    # until now nobody signed off on them: the skill told the agent to
    # "narrate them in the summary", which is chat prose that evaporates.
    #
    # Two live incidents made that unacceptable rather than untidy. A rebuild
    # published ``case_signature: null`` and every clause the app had learned
    # went dark (CS-03 now blocks that). A single null in one band column
    # emptied the facets for a whole case. Both were invisible, and both are
    # about a structure no human had ever been asked to look at.
    #
    # So the same move as RubricFinding: turn "a human confirms it" from an
    # instruction in a skill file into a stored field that either exists or
    # does not.
    confirmed_by: Optional[str] = Field(default=None, max_length=200)
    confirmed_at: Optional[datetime] = None
    #: The family names as CONFIRMED. Compared against ``facets`` at publish so
    #: the agent cannot show the BA one set and ship another — the same evasion
    #: FS-05 closed for the rubric. Order-insensitive; it is a set.
    confirmed_families: Optional[List[str]] = Field(default=None, max_length=24)


# ---------------------------------------------------------------------------
# Factor set — the customer's declared rubric (docs/factor-scorecard-plan.md)
#
# A factor is the STRUCTURED-DATA TWIN of an ItemFinding: the same review
# substrate (modality="api", one check_evaluate per factor) with a number
# attached. It is NOT a second review mechanism — an inspection app already has
# its grid and it is made of photos.
#
# We execute the customer's rubric; we never author one. Nothing here is
# inferred at run time: the structure is extracted once at build time, confirmed
# by a human, and frozen. Only the EVIDENCE is per case.
# ---------------------------------------------------------------------------


class FactorBand(BaseModel):
    """One named outcome for a factor.

    Two flavours, and a factor must not mix them (enforced in FactorSpec):

      * SCORE-BASED — every band but the last carries ``max``, an upper bound
        (inclusive) on the factor's own score. Code assigns the band; the model
        never picks it.
      * LABEL-ONLY  — no band carries ``max``. The evaluator picks from this
        closed set, because the threshold lives in the SOP's own units (days
        late, a ratio, a torque figure) and not in score space.

    The last band is always the catch-all and carries no ``max``."""

    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=40)
    max: Optional[float] = Field(
        default=None,
        description="Inclusive upper bound on the factor's SCORE. Omit on the "
        "final (catch-all) band. Omit on every band for a label-only factor.",
    )
    hint: Optional[str] = Field(default=None, max_length=160)


class FactorReads(BaseModel):
    """WHERE this factor's evidence comes from — declared, never discovered.

    Load-bearing. Without it the model hunts for its own data at run time and
    the same case scores differently on consecutive days, which is exactly the
    property a model-validation team tests for and rejects.

    ``where`` may interpolate anchor-record fields as ``{record.<field>}``. The
    anchor read resolves FIRST and every factor read fans out from what it
    returned — a factor cannot be evaluated before the base record is known."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["dataset", "document", "lookup"] = "dataset"
    source_id: Optional[str] = Field(
        default=None, description="MCP source. Defaults to the app's primary source."
    )
    dataset_id: Optional[str] = Field(
        default=None,
        description="Dataset / table for kind='dataset'. For kind='document' "
        "this is the attachment column on the anchor record.",
    )
    where: Optional[str] = Field(
        default=None,
        max_length=400,
        description="Predicate, e.g. \"dealer_id == {record.dealer_id} AND "
        "invoice_date >= today-365d\".",
    )
    tool_name: Optional[str] = Field(
        default=None,
        description="For kind='lookup': the agent-spec tool that performs the "
        "external call (bureau / KYC / sanctions).",
    )


class FactorSopRef(BaseModel):
    """The passage this factor is judged against.

    Provenance, on the same principle as a clause: a reviewer can check the
    extraction, and when the policy document changes we know which factors it
    affects."""

    model_config = ConfigDict(extra="forbid")

    source: str = Field(min_length=1, max_length=120)
    query: Optional[str] = Field(default=None, max_length=300)
    doc_path: Optional[str] = Field(default=None, max_length=400)
    #: Hash of the SOP TEXT this factor was extracted from, stamped at build
    #: time (``factor_scoring.sop_fingerprint``). At run time the fetched SOP is
    #: hashed again and compared: a mismatch means the policy document changed
    #: since a human confirmed this factor's weight and bands.
    #:
    #: Drift FLAGS, it does not block. Halting a portfolio because someone fixed
    #: a typo would make the check unusable, and we cannot tell a material edit
    #: from a cosmetic one — so the card says the factor was extracted against a
    #: different version, the app is marked for re-extraction, and the officer
    #: decides. What must never happen is scoring silently against last year's
    #: policy.
    #:
    #: Whitespace-insensitive (see the hash helper): reflowing a paragraph is
    #: not a policy change, and a fingerprint that cried wolf would be ignored.
    #: None = never stamped; no comparison is made and nothing is claimed.
    fingerprint: Optional[str] = Field(default=None, max_length=64)
    #: The clause/section the builder extracted this factor FROM, verbatim
    #: enough for a human to find it. Not used at run time — audit only.
    excerpt: Optional[str] = Field(default=None, max_length=600)


class FactorSpec(BaseModel):
    """One declared factor. The customer's, not ours."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,47}$")
    label: str = Field(min_length=1, max_length=80)
    #: Composite only: the factor's MAXIMUM SCORE. A score is always expressed
    #: out of its weight (18/25), so weight doubles as the scale — there is no
    #: separate max to keep in sync. REQUIRED under mode='composite', FORBIDDEN
    #: under mode='checklist' (see FactorSet).
    weight: Optional[float] = Field(default=None, gt=0, le=1000)
    #: Which side of the case this measures. 'entity' = the counterparty's own
    #: history (dealer vintage, payment record); 'case' = this application
    #: (requested increase, utilisation). Display + analysis only — both are
    #: scored identically. Recorded because the distinction is invisible in the
    #: data and someone always asks why a dealer's score moved.
    scope: Literal["entity", "case"] = "entity"
    reads: FactorReads
    sop: Optional[FactorSopRef] = None
    bands: List[FactorBand] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def _validate_bands(self) -> "FactorSpec":
        if self.bands[-1].max is not None:
            raise ValueError(
                f"factor '{self.id}': the LAST band is the catch-all and must "
                "not declare 'max'"
            )
        head = self.bands[:-1]
        with_max = [b for b in head if b.max is not None]
        if with_max and len(with_max) != len(head):
            raise ValueError(
                f"factor '{self.id}': bands must be either ALL score-based "
                "(every band but the last carries 'max', assigned in code) or "
                "ALL label-only (no band carries 'max', chosen by the "
                "evaluator). Mixing them means nobody can say who decided the "
                "band."
            )
        edges = [b.max for b in with_max]
        if edges != sorted(edges) or len(set(edges)) != len(edges):
            raise ValueError(
                f"factor '{self.id}': band 'max' edges must be strictly "
                f"increasing, got {edges}"
            )
        labels = [b.label for b in self.bands]
        if len(set(labels)) != len(labels):
            raise ValueError(f"factor '{self.id}': duplicate band labels {labels}")
        return self

    @property
    def score_based_bands(self) -> bool:
        """True when code assigns the band from the score."""
        return len(self.bands) > 1 and self.bands[0].max is not None


class GateSpec(BaseModel):
    """A hard pass/fail policy condition, evaluated BEFORE any factor.

    Gates and scored factors are two objects and must never blend. "Single-
    dealer exposure cannot exceed X% of anchor turnover" is pass/fail — it
    short-circuits. If a gate fails the composite is irrelevant, and rendering
    "68/100 — declined" beneath it is actively confusing.

    A gate is always deterministic: it is served by a ``check_evaluate`` tool in
    ``mode='rule'``, which runs no LLM and fails LOUD to 'flag' on error rather
    than passing silently."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,47}$")
    label: str = Field(min_length=1, max_length=120)
    reads: Optional[FactorReads] = None
    sop: Optional[FactorSopRef] = None


class GradeStep(BaseModel):
    """One rung of the grade scale. ``min`` is a PERCENTAGE of the maximum
    attainable score (0–100), never a raw total — so a rubric whose weights sum
    to 60 or 120 grades correctly without anyone having to normalise by hand.
    The final step is the catch-all and carries no ``min``."""

    model_config = ConfigDict(extra="forbid")

    grade: str = Field(min_length=1, max_length=24)
    min: Optional[float] = Field(default=None, ge=0, le=100)
    hint: Optional[str] = Field(default=None, max_length=160)


class FactorTerminology(BaseModel):
    """What the SCREEN calls these things.

    The engine says 'factor' forever. A bank says Scorecard/factor/Grade; an
    airline says Evaluation criteria/check/Disposition. Nothing in the renderer,
    the aggregator or the validator may contain domain vocabulary — if it does,
    the abstraction has already failed."""

    model_config = ConfigDict(extra="forbid")

    panel: str = Field(default="Assessment", min_length=1, max_length=40)
    row: str = Field(default="factor", min_length=1, max_length=24)
    band: str = Field(default="Band", min_length=1, max_length=24)
    composite: str = Field(
        default="Grade", min_length=1, max_length=24,
        description="What the overall result is called. Composite mode only.",
    )


FactorSetMode = Literal["composite", "checklist"]


class FactorSet(BaseModel):
    """The app's declared rubric. OPTIONAL, and absent is the common case —
    most Decision Apps have prose reasons and no grid, which is correct.

    Two shapes, and the choice is PERMANENT for a published app version:

      * ``composite`` — weights, a total, a grade. Credit, dealer finance,
        limit review. Approval authority usually keys off the grade.
      * ``checklist`` — judged criteria with citations and NO total. Aviation
        inspection, KYC, claim triage. Summing them would be meaningless and
        unsafe: a hull crack and a scuffed placard do not average.

    ``checklist`` is a first-class mode, not a degraded composite. A checklist
    app is never prompted for weights, because there are none to give.

    Why permanent: an app that silently grew a total one day would change how
    every one of its past outputs should be read — the same class of harm as
    memory quietly moving a score. Changing mode is a NEW VERSION with its own
    human confirmation (enforced at publish, not here: this model cannot see
    the previously published spec)."""

    model_config = ConfigDict(extra="forbid")

    mode: FactorSetMode
    terminology: FactorTerminology = Field(default_factory=FactorTerminology)
    factors: List[FactorSpec] = Field(min_length=1, max_length=32)
    gates: List[GateSpec] = Field(default_factory=list, max_length=12)
    #: Composite only. REQUIRED under mode='composite', FORBIDDEN under
    #: mode='checklist'.
    grade_scale: List[GradeStep] = Field(default_factory=list, max_length=12)

    @model_validator(mode="after")
    def _validate_mode(self) -> "FactorSet":
        ids = [f.id for f in self.factors]
        if len(set(ids)) != len(ids):
            raise ValueError(f"duplicate factor ids: {sorted(ids)}")
        gate_ids = [g.id for g in self.gates]
        if len(set(gate_ids)) != len(gate_ids):
            raise ValueError(f"duplicate gate ids: {sorted(gate_ids)}")
        clash = set(ids) & set(gate_ids)
        if clash:
            raise ValueError(
                f"gate and factor share an id: {sorted(clash)} — they are "
                "different objects and are reported separately"
            )

        if self.mode == "composite":
            missing = [f.id for f in self.factors if f.weight is None]
            if missing:
                raise ValueError(
                    f"mode='composite' requires a weight on every factor; "
                    f"missing on {missing}. If this rubric has no weights it is "
                    "a checklist — declare mode='checklist' instead of "
                    "inventing them."
                )
            if not self.grade_scale:
                raise ValueError(
                    "mode='composite' requires a grade_scale — a total with no "
                    "band is a number nobody can act on."
                )
            if self.grade_scale[-1].min is not None:
                raise ValueError(
                    "the LAST grade_scale step is the catch-all and must not "
                    "declare 'min' — otherwise a low score maps to no grade at all"
                )
            mins = [s.min for s in self.grade_scale[:-1]]
            if any(m is None for m in mins):
                raise ValueError(
                    "every grade_scale step except the last must declare 'min'"
                )
            if mins != sorted(mins, reverse=True) or len(set(mins)) != len(mins):
                raise ValueError(
                    f"grade_scale must be ordered by strictly DECREASING min, "
                    f"got {mins}"
                )
            grades = [s.grade for s in self.grade_scale]
            if len(set(grades)) != len(grades):
                raise ValueError(f"duplicate grade labels: {grades}")
        else:
            weighted = [f.id for f in self.factors if f.weight is not None]
            if weighted:
                raise ValueError(
                    f"mode='checklist' forbids weights, found on {weighted}. A "
                    "weight here means someone intended a total that will never "
                    "be computed — declare mode='composite' or drop the weights."
                )
            if self.grade_scale:
                raise ValueError(
                    "mode='checklist' forbids grade_scale for the same reason "
                    "it forbids weights: there is no composite to grade."
                )
        return self

    @property
    def max_score(self) -> Optional[float]:
        """Maximum attainable total. None for a checklist."""
        if self.mode != "composite":
            return None
        return sum(f.weight or 0.0 for f in self.factors)


class RubricEvidence(BaseModel):
    """What the builder actually saw, so a human can check the verdict without
    re-reading the whole document."""

    model_config = ConfigDict(extra="forbid")

    factors_named: Optional[int] = Field(default=None, ge=0, le=200)
    weights_present: Optional[bool] = None
    grade_scale_present: Optional[bool] = None
    #: Verbatim enough to find the passage. Not used at run time — audit only.
    excerpt: Optional[str] = Field(default=None, max_length=600)


class RubricFinding(BaseModel):
    """What the builder found when it read the policy — recorded as a FACT.

    Why this exists (docs/factor-scorecard-plan.md, phase 6). FS-05 originally
    inferred "this app scores" from the app's own prose: weight words plus
    aggregate words. It worked, and then the builder walked around it — the
    second build described the same assessment without the trigger vocabulary
    and sailed through. That is not a bug in the regex; it is the wrong thing to
    watch. Prose is a RENDERING of intent, and a language model can re-render on
    demand. Hardening the pattern is an arms race fought on the model's home
    ground.

    So the check moved onto something the builder must STATE. It already reads
    the SOP and reaches a conclusion; that conclusion used to live in the chat
    and evaporate. Written down, publish can compare it against what was
    actually declared, with no vocabulary to paraphrase away.

    This does NOT make evasion impossible: a builder can still record
    ``verdict="none"`` over a policy that plainly carries a rubric. What changes
    is the act required — from rephrasing a sentence, invisibly, to stating a
    false fact about a NAMED document, attributed, timestamped, and put in front
    of a human who can object. The same principle as the rest of this feature:
    not preventing wrongness, making it visible and attributable.

    Deliberately carries NO fingerprint. Drift detection is a separate concern
    and a low-value one: the SOP passage itself is re-read on every run, so the
    evidence is always current, and only the weights are frozen — which change
    through a committee, not silently. This record is about what was FOUND and
    DECLARED, not about watching a document.
    """

    model_config = ConfigDict(extra="forbid")

    #: The SOP/policy source that was read (a discovery source_id).
    source: str = Field(min_length=1, max_length=120)
    #: The specific document, when the read was whole-document. Required to
    #: carry a fingerprint — see FS-04.
    doc_path: Optional[str] = Field(default=None, max_length=400)

    verdict: Literal["weighted_rubric", "criteria_checklist", "none"]

    #: REQUIRED when verdict="none". "I read it and found no rubric" is only
    #: checkable when it says why — eligibility gates only, narrative guidance,
    #: a rating sheet held elsewhere. It is also the sentence a BA can most
    #: easily contradict, which is the point.
    reason: Optional[str] = Field(default=None, max_length=600)

    evidence: Optional[RubricEvidence] = None

    #: The human who confirmed this reading. "A human confirms it" was, until
    #: now, an instruction in a skill file with nothing behind it; a stored
    #: field either exists or does not.
    confirmed_by: Optional[str] = Field(default=None, max_length=200)
    confirmed_at: Optional[datetime] = None

    @model_validator(mode="after")
    def _validate(self) -> "RubricFinding":
        if self.verdict == "none":
            if not (self.reason or "").strip():
                raise ValueError(
                    "verdict='none' requires a reason — 'I read it and found no "
                    "rubric' is only checkable, and only contestable by the BA, "
                    "when it says why"
                )
        else:
            if self.evidence is None:
                raise ValueError(
                    f"verdict='{self.verdict}' requires evidence — what was "
                    "seen is what makes the verdict reviewable without "
                    "re-reading the document"
                )
        return self


class AppSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")  # single strict source of truth — legacy root fields (owner/visibility/lifecycle_stage/shared_with/superset/workflow_bindings) were purged pre-prod, so the root rejects unknowns like every nested model

    spec_version: SpecVersion = "v0"
    app_id: Optional[str] = None
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{1,62}$")
    title: str = Field(min_length=1, max_length=120)
    description: Optional[str] = Field(default=None, max_length=1000)
    # Headless / decision-API mode: the app has NO Citra-rendered UI (no panels/
    # pages) — it is an agent + governance/loop wrapper exposed ONLY via the
    # decision API (/run + /approve). An external/custom UI plugs into it. The
    # SmartApp runtime renders nothing; the list card surfaces the API URL +
    # contract instead of a launch URL. Governance + the self-learning loop are
    # identical to a UI-backed app (same /run → /approve → DecisionRecord path).
    headless: bool = False

    # ── Per-item review gate (image_analyze / doc_extract / check_evaluate) ──
    # When a run produces per-item findings (analyzed images / documents /
    # API-check verdicts), this governs whether the officer may commit the
    # RECORD-level decision (Apply) before every item has been reviewed. ONLY
    # enforced when item findings are present — a run with no findings is never
    # gated. Case (fraud-screening) findings are evidence-only and NEVER gate.
    # Set by the builder per the business analyst's instruction. Enforced BOTH
    # client-side (PanelRenderer) and server-side on approve (_approve_workflow_
    # staging → 409), so headless integrators cannot bypass it.
    #   'hard' — Apply is blocked until EVERY image, document AND api check is
    #            dispositioned (accept / reject / cancel). Default: nothing
    #            slips through unreviewed.
    #   'soft' — Apply is allowed, but the officer is warned about un-reviewed
    #            items first.
    #   'none' — items are available to review but never gate the decision.
    item_review_gate: Literal["hard", "soft", "none"] = "hard"

    # ── Case signature (docs/clause-memory-graph-plan.md §2) ──
    # The closed facet vocabulary that routes learned clause memory, plus the
    # reject-reason taxonomy officers pick from. Absent ⇒ the app keeps the
    # legacy single-blob rubric and records corrections with no facets; nothing
    # breaks, the app simply does not participate in clause learning yet.
    case_signature: Optional[CaseSignature] = None

    # ── Factor set (docs/factor-scorecard-plan.md) ──
    # The customer's DECLARED rubric, extracted from their SOP once at build
    # time and confirmed by a human. Absent ⇒ the app produces prose reasons and
    # no grid, which is the correct shape for most apps and the default. Never
    # authored by us: no rubric in the SOP means no factor_set, the same way an
    # empty catalogue stops a build.
    factor_set: Optional[FactorSet] = None

    # ── Rubric finding (docs/factor-scorecard-plan.md phase 6) ──
    # What the builder found when it read the policy. Publish compares this
    # against factor_set (rule FS-05): claim a rubric and you must declare it.
    # Absent = nothing was claimed, and nothing is checked.
    rubric_finding: Optional[RubricFinding] = None

    # ── Layer 1: AUTHOR (immutable audit truth) ──
    # The user who FIRST published this app. Never changes. Survives
    # every transfer / reassign. Used for audit + provenance.
    author_user_id: Optional[str] = None
    author_email: Optional[str] = None
    author_at: Optional[datetime] = None

    # ── Layer 2: OWNER (SA / dept / org only) ──
    # Authoritative owner. Can transition over time:
    #   service_account → dept → org → archived
    # owner_type="user" is no longer supported. SmartApps are always owned
    # by a Service Account (or, after a transfer, a dept/org).
    owner_type: Literal["service_account", "dept", "org"] = "service_account"
    owner_id: Optional[str] = None
    owner_changed_at: Optional[datetime] = None
    owner_changed_by: Optional[str] = None
    # Audit trail of past owners.
    previous_owners: List[Dict[str, Any]] = Field(default_factory=list)

    # ── Scope ──
    tenant_id: Optional[str] = None  # legacy org marker
    org_id: Optional[str] = None
    dept_ids: List[str] = Field(default_factory=list)

    # ── Audience (who can SEE and RUN this app) ──
    # Edit access is controlled separately by owner_sa_id membership.
    #   'owner'          — only owner_sa_id members (default)
    #   'team:<sa_id>'   — members of a specific team SA
    #   'dept:<dept_id>' — everyone in a department
    #   'org'            — everyone in the tenant
    # Audience changes go through the higher-of(current, target) RBAC rule
    # (see /apps/{slug}/audience). Narrowing requires the role for the
    # current level; widening requires the role for the target level.
    audience: str = Field(default="owner")

    # ── Inheritance (what happens when owner-user is deactivated) ──
    inheritance_policy: Literal[
        "archive",
        "transfer_to_sa",
        "transfer_to_dept",
        "transfer_to_org",
        "delete_after_grace",
    ] = "archive"
    inheritance_target: Optional[str] = None
    inheritance_grace_days: int = 30
    kind: AppKind = Field(
        default="app",
        description=(
            "Artefact kind. 'app' = a Power AI App with an agent (one or"
            " more pages; any page may set page.kind='dashboard' for the"
            " executive KPI/chart + hero-brief treatment — a 'dashboard' is"
            " just an app whose primary page is a dashboard page). The"
            " legacy 'dashboard' kind is retired and coerced to this shape."
        ),
    )
    agent_id: Optional[str] = Field(
        default=None,
        description=(
            "References the AgentSpec that powers this app. Required for"
            " kind='app' (and specifically required when any page has"
            " kind='dashboard', to power the hero-brief copilot)."
        ),
    )
    theme: Optional[Theme] = None
    data_sources: List[DataSource] = Field(default_factory=list)
    dataset_directory: List[DatasetDirectoryEntry] = Field(
        default_factory=list,
        description=(
            "Resolved dataset ↔ tool relationships for every (source_id, "
            "dataset_id) pair this app references. Hydrated at publish "
            "time from discovery-service; the runtime agent reads from "
            "here instead of hitting discovery per tool call. Refreshed "
            "on every republish; sources that disappear from discovery "
            "are pruned. Never written by the builder."
        ),
    )
    panels: List[Panel] = Field(
        default_factory=list,
        description=(
            "Single-page shorthand. When set and pages[] is empty, the"
            " runtime synthesises one implicit page at path='/' containing"
            " these panels. Mutually exclusive with pages[]."
        ),
    )
    pages: List[Page] = Field(
        default_factory=list,
        description=(
            "Multi-page layout. When non-empty, panels[] MUST be empty."
            " Each page is rendered at /{slug}/{page.id}. The first page"
            " (or the page whose path='/') is the landing page."
        ),
    )
    navigation: Optional[AppNavigation] = Field(
        default=None,
        description=(
            "Top-level nav chrome for multi-page apps. Ignored when"
            " pages[] is empty."
        ),
    )
    permissions: Dict[str, List[str]] = Field(default_factory=dict)
    triggers: List[Trigger] = Field(
        default_factory=list,
        description=(
            "Non-user invocation paths (webhook / schedule / poll). Empty"
            " means the app is only ever started by user actions."
        ),
    )
    requirements_unmet: List[str] = Field(
        default_factory=list,
        description=(
            "Capabilities the BA requested that the platform doesn't"
            " currently support. Recorded by the builder during discovery so"
            " admins can see what apps are blocked on missing features."
        ),
    )
    design_dossier: Optional[str] = Field(
        default=None,
        max_length=20_000,
        description=(
            "Markdown summary produced by the discovery skill: trigger,"
            " inputs, tools, RAG, HITL thresholds, post-actions, schedule,"
            " failure policy. Stored alongside the spec for sign-off & audit."
        ),
    )
    custom_modules: List[CustomModule] = Field(default_factory=list)
    # RETIRED (2026-07-01): the "preview app" mechanism — a BA-only dry-run
    # shadow published against PROD data under a ``_preview`` slug — was removed
    # in favour of the test→prod environment model (publish to test_ collections,
    # promote to prod). These four fields are KEPT ONLY as inert tombstones so
    # existing prod app_spec docs (which carry ``preview_mode: false`` and are
    # validated under this model's ``extra="forbid"``) still load. Nothing sets
    # them True anymore; a future Mongo ``$unset`` migration can drop them.
    preview_mode: bool = False
    preview_until: Optional[datetime] = None
    promoted_to_slug: Optional[str] = None
    promoted_at: Optional[datetime] = None
    version: Optional[int] = Field(default=None, ge=1)
    deployed_at: Optional[datetime] = None
    status: AppStatus = AppStatus.DRAFT

    @property
    def all_panels(self) -> List[Panel]:
        """Flat panel list across panels[] and pages[].panels[].

        Backwards-compatible accessor: validators, publishers, and the
        dataset_directory hydrator should use this instead of ``self.panels``
        directly so multi-page apps work without each consumer learning
        about pages[].
        """
        if self.pages:
            out: List[Panel] = []
            for page in self.pages:
                out.extend(page.panels)
            return out
        return list(self.panels)

    def resolve_default_page(self) -> Optional[str]:
        """Return the id of the page that '/' should resolve to.

        Resolution order: ``navigation.default_page`` → page whose path is
        ``"/"`` → first page in pages[]. Returns None for single-page mode.
        """
        if not self.pages:
            return None
        if self.navigation and self.navigation.default_page:
            return self.navigation.default_page
        for page in self.pages:
            if page.path == "/":
                return page.id
        return self.pages[0].id

    @model_validator(mode="before")
    @classmethod
    def _coerce_legacy_dashboard_kind(cls, data: Any) -> Any:
        """Migrate the retired kind='dashboard' to the new shape.

        A legacy dashboard becomes kind='app' with its primary page marked
        page.kind='dashboard'. Panel-only (single-page) dashboards are
        wrapped into a one-page pages[] so the page can carry kind. This is
        a logged deprecation path, NOT a silent fallback — every coercion
        emits a warning so un-migrated specs are visible. Delete once all
        persisted specs + demo JSONs are migrated (run
        scripts/migrate_dashboard_kind.py).
        """
        if not isinstance(data, dict) or data.get("kind") != "dashboard":
            return data
        data = dict(data)
        logger.warning(
            "AppSpec kind='dashboard' is retired; coercing to kind='app' with "
            "a dashboard page (slug=%s, title=%s). Migrate this spec.",
            data.get("slug"),
            data.get("title"),
        )
        data["kind"] = "app"
        if data.get("pages"):
            pages = [dict(p) for p in data["pages"]]
            pages[0].setdefault("kind", "dashboard")
            data["pages"] = pages
        elif data.get("panels"):
            data["pages"] = [
                {
                    "id": "overview",
                    "path": "/",
                    "title": data.get("title") or "Overview",
                    "kind": "dashboard",
                    "panels": data["panels"],
                }
            ]
            data["panels"] = []
        return data

    @model_validator(mode="after")
    def _validate_audience(self) -> "AppSpec":
        parse_audience(self.audience)
        return self

    @model_validator(mode="after")
    def _enforce_kind_rules(self) -> "AppSpec":

        # Mode check: panels[] and pages[] are mutually exclusive. Exactly
        # one must be non-empty for kind='app'. There is no cap on the number
        # of pages, nor any limit on how many may be dashboard pages.
        if self.panels and self.pages:
            raise ValueError(
                "AppSpec.panels and AppSpec.pages are mutually exclusive."
                " Use pages[] for multi-page apps (panels live inside each"
                " page); use panels[] for single-page shorthand. Got both"
                " non-empty."
            )
        if not self.panels and not self.pages and not self.headless:
            raise ValueError(
                f"AppSpec must declare at least one panel (via panels[] or"
                f" pages[].panels[]) for kind='{self.kind}'"
                " — or set headless=true for an agent-only (decision-API) app"
            )

        # In multi-page mode, validate cross-references: every page id is
        # unique; navigate.page references hit a real page; default_page
        # exists if specified.
        if self.pages:
            page_ids = [p.id for p in self.pages]
            duplicates = {p for p in page_ids if page_ids.count(p) > 1}
            if duplicates:
                raise ValueError(
                    f"AppSpec.pages contains duplicate page id(s): {sorted(duplicates)}"
                )
            page_id_set = set(page_ids)
            if self.navigation and self.navigation.default_page:
                if self.navigation.default_page not in page_id_set:
                    raise ValueError(
                        f"AppSpec.navigation.default_page='{self.navigation.default_page}'"
                        f" does not match any page id (have: {sorted(page_id_set)})"
                    )
            # Walk every navigate target inside form/queue panels.
            for page in self.pages:
                for panel in page.panels:
                    if panel.type == "form" and panel.on_submit and panel.on_submit.navigate:
                        target = panel.on_submit.navigate.page
                        if target not in page_id_set:
                            raise ValueError(
                                f"FormPanel '{panel.id}' on_submit.navigate.page"
                                f" '{target}' is not a declared page"
                            )
                    if panel.type == "queue":
                        for action in panel.actions:
                            if action.navigate and action.navigate.page not in page_id_set:
                                raise ValueError(
                                    f"QueuePanel '{panel.id}' action '{action.label}'"
                                    f" navigate.page '{action.navigate.page}' is not"
                                    f" a declared page"
                                )
                        # At most one row-click action per queue.
                        row_clicks = [a for a in panel.actions if a.is_row_click]
                        if len(row_clicks) > 1:
                            raise ValueError(
                                f"QueuePanel '{panel.id}' has multiple is_row_click"
                                f" actions; at most one is allowed"
                            )

        # kind == "app"
        if not self.agent_id:
            raise ValueError(
                "AppSpec.agent_id is required for kind='app'"
            )

        # Dashboard PAGE contract (replaces the retired kind='dashboard').
        # ────────────────────────────────────────────────────────────────
        # A page with kind='dashboard' renders the executive treatment: a
        # KPI + chart grid with the ECharts exec theme, topped by the
        # hero-brief copilot. The brief runs the app's narrator agent in
        # chat_mode (read-only at runtime via the chat tool filter), so a
        # dashboard page imposes NO write restriction on the rest of the app
        # — an app may freely mix a dashboard page with action pages.
        dash_pages = [
            p for p in self.pages
            if getattr(p, "kind", "standard") == "dashboard"
        ]
        if dash_pages and not self.agent_id:
            raise ValueError(
                "A page with kind='dashboard' requires AppSpec.agent_id — the "
                "hero-brief copilot needs a narrator agent. Author one "
                "(citra-dashboard-spec emits a narrator) and reference it via "
                "agent_id."
            )
        # A dashboard page stays KPI/chart focused. Queues / forms / detail /
        # document_view belong on standard pages so the executive surface
        # reads cleanly.
        allowed_dashboard_panels = {
            "chart", "dashboard", "agent_chat", "markdown", "filter_bar",
            # Designed-header + compact-KPI panels are exec-surface material.
            "hero", "stat_strip",
        }
        ds_by_id = {d.id: d for d in self.data_sources}
        for page in dash_pages:
            for panel in page.panels:
                if panel.type not in allowed_dashboard_panels:
                    raise ValueError(
                        f"panel '{panel.id}' on dashboard page '{page.id}' has "
                        f"type='{panel.type}'; a dashboard page allows only "
                        "'chart', 'dashboard' (KPI cards), 'agent_chat', "
                        "'markdown', and 'filter_bar' panels. Move queues / "
                        "forms / detail / document_view panels to a standard page."
                    )
                ds_ref = getattr(panel, "data_source", None)
                if panel.type == "chart" and not ds_ref:
                    raise ValueError(
                        f"chart panel '{panel.id}' on dashboard page "
                        f"'{page.id}' must declare data_source"
                    )
                if ds_ref and ds_ref not in ds_by_id:
                    raise ValueError(
                        f"panel '{panel.id}' references unknown data_source "
                        f"'{ds_ref}'"
                    )

        # An EMBED page renders inside a customer's own application through the
        # citra.js bundle. WHICH panels serve a decision card is guidance the
        # builder gets from the citra-embed-spec skill, not a rule — the skill
        # can say what good looks like, a validator can only say no.
        #
        # Two panels are different: they CANNOT render at all. The embed bundle
        # aliases echarts and leaflet away (they are several times the weight of
        # everything else and no embed surface charts), so a chart or map panel
        # resolves to a "can't be shown here" notice. That is deliberate and
        # loud, but it belongs at PUBLISH time where the builder sees it — not
        # at render time in front of a customer's officer.
        for page in self.pages:
            if getattr(page, "kind", "standard") != "embed":
                continue
            for panel in page.panels:
                if panel.type in ("chart", "map"):
                    raise ValueError(
                        f"panel '{panel.id}' on embed page '{page.id}' has "
                        f"type='{panel.type}', which cannot render in an "
                        "embedded card — the embed bundle excludes the charting "
                        "and mapping libraries. Move it to a standard or "
                        "dashboard page, or drop it from the embed."
                    )
            # An embed card with no way to run the agent is a read-only viewer
            # of a record the host application is already showing — it produces
            # no recommendation and captures no reason, so it cannot learn. That
            # is a silent failure: it renders perfectly and is simply pointless.
            # The trigger may sit on a queue action or (preferred, and the only
            # option once the queue is dropped) a detail action.
            if not any(
                getattr(a, "agent_action", None)
                for panel in page.panels
                for a in (getattr(panel, "actions", None) or [])
            ):
                raise ValueError(
                    f"embed page '{page.id}' has no agent_action — nothing on "
                    "the card can run the agent, so it shows a record the host "
                    "already has and never produces a recommendation. Add an "
                    "action to the detail panel, e.g. "
                    '{"label": "Review", "agent_action": "<your action>"}.'
                )
        return self


# Forward-ref resolution for AppSpec's forward-referenced field types.
AppSpec.model_rebuild()


# ---------------------------------------------------------------------------
# Build session + publish payloads
# ---------------------------------------------------------------------------


class BuildRequest(BaseModel):
    """Request body for POST /build — kicks off a builder session.

    There is no "goal" mechanism: the builder is a conversational agent.
    The BA opens the chat however they like (a greeting, a half-formed idea,
    a full description) and the builder discovers intent through dialogue.
    ``goal`` is therefore optional and, when omitted, nothing is pre-seeded —
    the first chat turn drives the build.
    """

    goal: Optional[str] = Field(default=None, max_length=4000)
    tenant_id: Optional[str] = None
    starter_template: Optional[str] = Field(
        default=None, description="Optional seed (e.g. 'claims_triage')."
    )
    build_kind: Optional[BuildKind] = Field(
        default=None,
        description=(
            "DEPRECATED: legacy single-kind field. Prefer ``build_kinds``."
            " If both are set, ``build_kinds`` wins."
        ),
    )
    build_kinds: Optional[List[BuildKind]] = Field(
        default=None,
        description=(
            "Artefact the builder session should produce. Always ['app'] —"
            " a dashboard is an app with a dashboard primary page"
            " (primary_page_kind='dashboard'); automation is an app trigger."
            " Legacy 'dashboard'/'workflow' values are coerced to 'app'."
        ),
    )
    primary_page_kind: Optional[PageKind] = Field(
        default=None,
        description=(
            "Purpose of the app's primary page. Set to 'dashboard' when the"
            " BA asked for a dashboard — the builder produces a kind='app'"
            " whose primary page has page.kind='dashboard' (the executive"
            " KPI/chart + hero-brief treatment). The retired 'dashboard'"
            " build intent is coerced into this field."
        ),
    )
    build_headless: bool = Field(
        default=False,
        description=(
            "Seed a HEADLESS (decision-API) build: the app has no UI — no"
            " panels/pages — and is exposed only as /run + /approve for an"
            " external front-end. Set from the build-kind picker's 'API' tile."
            " The builder pod reads this as BUILD_HEADLESS to skip the"
            " UI-design phases. A seed the agent can still change in"
            " conversation, not a hard lock."
        ),
    )

    @model_validator(mode="before")
    @classmethod
    def _coerce_legacy_build_intent(cls, data: Any) -> Any:
        """Map retired build intents ('dashboard', 'workflow') to 'app'.

        The UI's "New Dashboard" affordance historically sent
        build_kind='dashboard'; older callers sent 'workflow'. Neither is an
        artefact kind any more — SmartApps build an 'app' (a dashboard is an
        app with a dashboard primary page; automation is an app trigger, not a
        workflow). Rewrite both to 'app' (recording primary_page_kind for the
        dashboard case) so legacy callers don't 422. Logged, not silent.
        """
        if not isinstance(data, dict):
            return data
        bk = data.get("build_kind")
        bks = data.get("build_kinds")
        if bk == "dashboard" or (isinstance(bks, list) and "dashboard" in bks):
            data.setdefault("primary_page_kind", "dashboard")
            logger.warning(
                "build_kind='dashboard' is retired; building a kind='app' with "
                "a dashboard primary page instead."
            )
        if bk in ("dashboard", "workflow"):
            data["build_kind"] = "app"
        if isinstance(bks, list) and any(k != "app" for k in bks):
            if "workflow" in bks:
                logger.warning(
                    "build_kinds contained 'workflow' (retired); SmartApps "
                    "build an 'app' — automation is an app trigger."
                )
            data["build_kinds"] = ["app" for _ in bks]
        return data

    @model_validator(mode="after")
    def _normalise_kinds(self) -> "BuildRequest":
        kinds: List[str] = list(self.build_kinds or [])
        if not kinds:
            kinds = [self.build_kind or "app"]
        # de-dup, preserve order
        seen: set = set()
        deduped: List[BuildKind] = []
        for k in kinds:
            if k in seen:
                continue
            seen.add(k)
            deduped.append(k)  # type: ignore[arg-type]
        if not deduped:
            raise ValueError("build_kinds must contain at least one kind")
        self.build_kinds = deduped
        self.build_kind = deduped[0]  # type: ignore[assignment]
        return self


class BuildSession(BaseModel):
    session_id: str
    app_id: Optional[str] = None
    tenant_id: Optional[str] = None
    owner: Optional[str] = None
    goal: str
    pod_id: Optional[str] = None
    status: BuildSessionStatus = BuildSessionStatus.ACTIVE
    started_at: datetime
    transcript: List[Dict[str, Any]] = Field(default_factory=list)
    q_and_a: List[Dict[str, Any]] = Field(default_factory=list)


class PublishRequest(BaseModel):
    """Request body for POST /publish — called from the builder pod."""

    session_id: str
    app_spec: AppSpec = Field(
        description=(
            "AppSpec for the app being published. A 'dashboard' is an app"
            " whose primary page has page.kind='dashboard'. The publish"
            " handler enforces the app/agent pairing rules below."
        ),
    )
    agent_spec: Optional[AgentSpec] = Field(
        default=None,
        description=(
            "AgentSpec for the app. Required for kind='app'; the same agent"
            " powers a dashboard page's hero-brief narrator in chat_mode."
        ),
    )
    prompt_packs: List[Dict[str, Any]] = Field(default_factory=list)
    skills: List[Dict[str, Any]] = Field(default_factory=list)
    # NOTE: the ``mode`` field ("live" | "preview") was removed 2026-07-01 with
    # the preview-app mechanism. Publishing always targets the test environment
    # (test_ collections); promotion to prod is POST /apps/{slug}/promote-to-prod.

    @model_validator(mode="after")
    def _check_agent_spec_pairing(self) -> "PublishRequest":
        # kind='app' requires an AgentSpec (the action agent — and, for an
        # app with a dashboard page, the same agent narrates the hero brief
        # in chat_mode).
        if self.app_spec.kind == "app" and self.agent_spec is None:
            raise ValueError(
                "PublishRequest.agent_spec is required for kind='app' AppSpec"
                " (the action agent; for an app with a dashboard page the same"
                " agent powers the hero-brief narrator)"
            )

        # Cost-gate cross-validation between AppSpec panels and
        # AgentSpec.tools_v2. Applies to kind='app'.
        if self.app_spec.kind == "app" and self.agent_spec is not None:
            tools_v2 = self.agent_spec.tools_v2 or []
            tool_kinds = {t.kind for t in tools_v2}
            all_app_panels = self.app_spec.all_panels
            form_panels = [
                p for p in all_app_panels if p.type == "form"
            ]
            upload_panels = [
                p for p in form_panels if getattr(p, "accepts_files", False)
            ]

            # Rule 1: any FormPanel ⇒ at least one validate_form tool.
            # Builders that opt into tools_v2 must declare the cost gate.
            # If the spec uses only legacy `tools: List[str]` (tools_v2
            # empty), we don't enforce this — back-compat with existing
            # apps. New builds populate tools_v2 → must include the gate.
            if form_panels and tools_v2 and "validate_form" not in tool_kinds:
                raise ValueError(
                    "PublishRequest: AppSpec has FormPanel(s) and"
                    " AgentSpec.tools_v2 is populated, but no"
                    " 'validate_form' tool is declared. The cheap"
                    " completeness check is mandatory so incomplete"
                    " submissions short-circuit before paid tools run."
                )

            # Rule 2: any FormPanel.accepts_files=True ⇒ a tool that consumes the
            # upload — vision_ocr (raw text) OR a structured analyzer
            # (image_analyze / doc_extract). An upload-driven structured-analysis
            # app must not be forced to also declare vision_ocr it never calls.
            _upload_consumers = {"vision_ocr", "image_analyze", "doc_extract"}
            if upload_panels and not (_upload_consumers & set(tool_kinds)):
                upload_ids = ", ".join(p.id for p in upload_panels)
                raise ValueError(
                    "PublishRequest: AppSpec form panel(s) "
                    f"[{upload_ids}] have accepts_files=True but AgentSpec.tools_v2"
                    " has no tool to consume the upload (vision_ocr, image_analyze,"
                    " or doc_extract). Either drop accepts_files or add one."
                )

            # Rule 3: every validate_form tool's schema_ref must resolve
            # to a FormPanel id (or schema_ref) in the AppSpec.
            form_ids = {p.id for p in form_panels}
            form_schema_refs = {
                getattr(p, "schema_ref", None) for p in form_panels
            } - {None}
            for t in tools_v2:
                if t.kind != "validate_form":
                    continue
                if (
                    t.schema_ref not in form_ids
                    and t.schema_ref not in form_schema_refs
                ):
                    raise ValueError(
                        f"PublishRequest: validate_form tool {t.name!r}"
                        f" references schema_ref={t.schema_ref!r} which"
                        " does not match any FormPanel.id or"
                        " FormPanel.schema_ref in the AppSpec."
                    )

            # Rule 4: every panel.tool_buttons[*].tool_name must match
            # a tools_v2 entry. Without this, the runtime route
            # /apps/{slug}/tool/{name} would 404 and the BA would never
            # know until a user clicked.
            tool_names = {t.name for t in tools_v2}
            action_names = {
                a.name for a in (self.agent_spec.actions or [])
            }
            for panel in all_app_panels:
                for btn in getattr(panel, "tool_buttons", []) or []:
                    if btn.tool_name in tool_names:
                        continue
                    if btn.tool_name in action_names:
                        # The most common builder mistake: wiring an agent
                        # *action* (approve / reject / submit) as a
                        # tool_button. tool_buttons are LLM-bypassing
                        # tools_v2 calls only. Point at the right shape so
                        # the builder fixes it instead of deleting the
                        # button and silently shipping a feature-less app.
                        raise ValueError(
                            f"PublishRequest: panel {panel.id!r} declares"
                            f" tool_button -> {btn.tool_name!r}, but that is"
                            " an AgentSpec *action*, not a tools_v2 tool."
                            " tool_buttons are only for deterministic"
                            " tools_v2 entries. To surface an agent action"
                            " (e.g. approve / reject), use a"
                            " QueueAction with agent_action set on a queue"
                            " panel, or a DetailSection of type='approval'"
                            " on a detail panel — see the citra-app-spec"
                            " skill's 'Canonical approval shape' section."
                        )
                    raise ValueError(
                        f"PublishRequest: panel {panel.id!r} declares"
                        f" tool_button -> {btn.tool_name!r} which is"
                        " not present in AgentSpec.tools_v2 (nor is it an"
                        " AgentSpec action)."
                    )

            # Rule 5: every type="mcp" data source must carry a
            # resolvable ref. panel_data.py parses an mcp ref as
            # "server.tool"; a ref with no "." and no filters.tool can
            # never resolve and the panel renders a permanent
            # "must reference 'server.tool'" error. Catch the dead
            # source at publish so the builder fixes it (real ref,
            # type='smart_app_records', or requirements_unmet) instead
            # of shipping a broken panel. App-kind only.
            for ds in (self.app_spec.data_sources or []):
                if ds.type != "mcp":
                    continue
                resolvable = "." in (ds.ref or "") or bool(
                    (ds.filters or {}).get("tool")
                )
                if not resolvable:
                    raise ValueError(
                        f"PublishRequest: data_source {ds.id!r} is"
                        f" type='mcp' but ref={ds.ref!r} is not"
                        " resolvable. Use ref='<server>.<tool>'"
                        " (dot-separated, copied from citra-mcp-discover"
                        " output) or set filters.tool. If no MCP exists"
                        " for this data, use type='smart_app_records'"
                        " (ref = the row kind) or drop the source and"
                        " list the need in requirements_unmet — do not"
                        " ship a dead mcp source."
                    )
        return self


class PublishResponse(BaseModel):
    app_id: Optional[str] = None
    slug: Optional[str] = None
    url: Optional[str] = None
    version: Optional[int] = None
    published_summary: Optional[str] = Field(
        default=None,
        description=(
            "Human-readable, BA-facing summary of exactly WHAT was published —"
            " the app's pages, each panel/action surface, and every app trigger."
            " The builder relays this verbatim so the BA knows what went live."
        ),
    )
    warnings: List[Dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "Non-blocking soft warnings raised during publish. The builder"
            " surfaces these to the BA so they can clean up before the next"
            " edit pass; ignored by the runtime."
        ),
    )


class BuildResponse(BaseModel):
    """Returned from POST /build and POST /apps/{slug}/edit."""

    session_id: str
    pod_id: Optional[str] = None
    builder_url: Optional[str] = Field(
        default=None,
        description="Adapter URL of the spawned builder pod.",
    )
    pod_session_token: Optional[str] = Field(
        default=None,
        description=(
            "One-time browser session token. The web UI passes this to the"
            " builder pod's adapter on the WebSocket handshake; the adapter"
            " verifies it (signed with the shared JWT secret, scope="
            "smart-app-builder, bound to session_id) and rejects connections"
            " without it. Prevents an attacker who knows the public adapter"
            " URL from hijacking another user's builder pod."
        ),
    )
    status: BuildSessionStatus = BuildSessionStatus.ACTIVE
    started_at: datetime
    expires_at: Optional[datetime] = None
    reused: bool = Field(
        default=False,
        description=(
            "True when this response reattached to an already-running builder"
            " pod for the same (owner, app_id, build_kind) target rather than"
            " spawning a fresh one. The UI can use it to indicate the prior"
            " build conversation was resumed."
        ),
    )


class ApproveRequest(BaseModel):
    """POST /apps/{slug}/run/{correlation_id}/approve body."""

    decision: Literal["approve", "reject", "cancel"]
    note: Optional[str] = Field(default=None, max_length=2000)
    # Officer overrides for the plan-then-apply editable fields, aligned to
    # planned_writes by index: overrides[i] = {field: new_value} applied to
    # planned_writes[i]'s payload. Only fields declared in that write's
    # editable_fields may be changed; the apply re-validates via dry_run.
    overrides: Optional[List[Dict[str, Any]]] = None
    # WHY the officer rejected/overrode (the causal signal fed back to the model).
    # A short structured reason (UI: a reason chip + optional free text). Distinct
    # from ``note`` (free-form audit text); this is the learning signal.
    decision_reason: Optional[str] = None
    # ── Structured reason capture (docs/clause-memory-graph-plan.md §3) ──
    # `decision_reason` is prose; these two are the aggregatable signal. The
    # code comes from the app's case_signature.reason_codes closed set (the UI
    # renders it as a picker); contested_fields names WHAT was wrong when the
    # officer rejected without editing a field (an override's deltas already
    # imply it, so it is derived server-side in that case). Both optional —
    # older clients and apps without a case_signature simply record neither,
    # which reads as "not captured" rather than a synthesized bucket.
    #: DEPRECATED — no client sends this any more. Kept so an older embed
    #: bundle still posts successfully; the value is recorded and ignored.
    #: Clustering partitions on contested_fields, which is derived, so a code
    #: no longer influences anything. See CaseSignature.reason_codes.
    reason_code: Optional[str] = Field(default=None, max_length=40)
    contested_fields: Optional[List[str]] = None
    # Integrity check (display==commit): the content hash of the proposal the
    # officer was shown (RunResponse.plan_hash). When present, approve verifies
    # it equals the CURRENT staged plan's hash and rejects (409) if the plan
    # changed since it was displayed — so an officer can never approve a stale
    # recommendation. None = skip the check (older clients).
    expected_plan_hash: Optional[str] = None


# ---------------------------------------------------------------------------
# Audience API payloads
# ---------------------------------------------------------------------------


class SetAudienceRequest(BaseModel):
    """POST /apps/{slug}/audience body."""

    model_config = ConfigDict(extra="forbid")

    audience: str = Field(
        min_length=1, max_length=200,
        description="One of: 'owner', 'team:<sa_id>', 'dept:<dept_id>', 'org'.",
    )
    reason: Optional[str] = Field(default=None, max_length=500)


class PublishOption(BaseModel):
    """One entry in GET /apps/{slug}/publish-options."""
    model_config = ConfigDict(extra="forbid")

    value: str
    label: str
    level: AudienceLevel
    target_id: Optional[str] = None
    allowed: bool
    reason: Optional[str] = None


class PublishOptionsResponse(BaseModel):
    slug: str
    current: str
    options: List[PublishOption]


class SetAudienceResponse(BaseModel):
    slug: str
    audience: str
    previous_audience: Optional[str] = None


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------


class AppSummary(BaseModel):
    """Row in the My Apps list view."""

    app_id: str
    slug: str
    title: str
    description: Optional[str] = None
    owner_type: Optional[str] = None
    owner_id: Optional[str] = None
    tenant_id: Optional[str] = None
    kind: AppKind = "app"
    # A dashboard is a kind='app' with a page.kind='dashboard'. The list UI
    # uses this flag (not kind) to count / filter / badge dashboards, since
    # 'dashboard' is no longer an artefact kind.
    has_dashboard_page: bool = False
    # An embeddable card is a kind='app' with a page.kind='embed', rendered in a
    # CUSTOMER's own application by citra.js. Same treatment as dashboards: the
    # list badges/filters on this flag, not on an artefact kind.
    has_embed_page: bool = False
    # Present only for externally-consumed apps (embed page or headless). The
    # card's Export action needs to know an app HAS a key before offering the
    # snippet, so the button never leads to a 409.
    has_embed_key: bool = False
    # Automation surface for the list-card button label: "auto_process" when any
    # trigger commits autonomously (card shows "Auto-Process"), else "recommend"
    # (card shows "Auto-Recommend" — also the default when no trigger is configured
    # yet). None only for legacy rows; the UI falls back to "Auto-Recommend".
    automation_mode: Optional[str] = None
    # Whether the app has any scheduled/automated execution jobs (cron / interval /
    # poll / webhook triggers). The card shows the "Pause" (app kill-switch) button
    # only when true — an on-demand-only app has no background automation to pause
    # (it can still be halted fleet-wide from Automation Control).
    has_automation: bool = False
    status: AppStatus
    version: int
    deployed_at: Optional[datetime] = None
    url: str
    # Audience controls who can SEE/RUN. UI groups list results by this.
    audience: str = "owner"
    # Whether the app is grounded on history (agent carries a grounding
    # contract). The UI shows a manual "Refresh grounding" action only when
    # true. Stamped on the doc at publish so the list needs no agent lookup.
    grounded: bool = False
    # Whether the ontology enabled fraud screening for this app (a bound dataset
    # opted into fraud_screening → the autowire wired a consistency_check with
    # url_columns). The card shows the "Calibrate fraud" action ONLY when true;
    # no fraud surface renders otherwise. Stamped on the doc at publish.
    fraud_enabled: bool = False
    # Headless / decision-API app — no Citra UI. Managed + URL-copyable as a card,
    # but the card surfaces the decision API URL (/run) + contract, not a launch URL.
    headless: bool = False
    # Whether THIS caller may edit/archive/re-publish the app (resolved via
    # _can_edit_app with the caller's claims). The list UI gates the
    # Edit / Publish / Archive actions on this so it never shows a button the
    # caller can't actually use (which would 403 on click). Defaults True for
    # back-compat when a caller context isn't supplied.
    can_edit: bool = True


class AppListResponse(BaseModel):
    apps: List[AppSummary]
    total: int


class EmbedSpecResponse(BaseModel):
    """GET /embed/{key}/spec — what citra.js needs to render a card.

    Deliberately NOT AppDetailResponse: this is served to a page on a customer's
    origin, so it carries only what the renderer uses. `slug` is included
    because the runtime's own /api/* routes are slug-addressed; the embed key
    stays the customer-facing identifier.
    """

    slug: str
    app_spec: AppSpec
    agent_spec: Optional[AgentSpec] = None
    #: The embed page to render. None → the renderer picks the first page whose
    #: kind is "embed", else the first page.
    page_id: Optional[str] = None
    #: Which store served this — an embed key's prefix already implies it, but
    #: echoing it makes a mis-pasted test key obvious in the network tab.
    environment: Literal["test", "prod"] = "prod"


class EmbedSnippetResponse(BaseModel):
    """GET /apps/{slug}/embed/snippet — the copy-paste handoff for My Apps."""

    embed_key: str
    script_url: str
    environment: Literal["test", "prod"]
    snippet: str
    #: What `recordId` must be — ``{"key_field": ..., "dataset": ...}``. The one
    #: value the host supplies, and the one thing the snippet could not tell the
    #: developer: a screen showing a loan has an application id, a customer id
    #: and an account number on it, and passing the wrong one renders an empty
    #: card with no explanation. None when it cannot be derived from the spec —
    #: omitted rather than guessed, because a wrong contract would be trusted.
    record_contract: Optional[Dict[str, str]] = None
    #: Filled by the RUNTIME, which is the service that actually serves the
    #: bundle and therefore the only one that knows its version. smart-app-service
    #: leaves it None rather than reporting a guess that could go stale.
    version: Optional[str] = None


class AppDetailResponse(BaseModel):
    app_spec: AppSpec
    agent_spec: Optional[AgentSpec] = None
    # Which store served this app — "test" (test_ collections) or "prod".
    # The runtime renders a prominent TEST banner on "test" so a reviewer can
    # never mistake a rehearsal against test systems for real, live work.
    environment: Literal["test", "prod"] = "prod"
    # The customer's display identity resolved from the ontology at publish
    # (runtime-ui-modernization-plan.md Track E) — surfaces beyond the theme
    # (e.g. a personalized Money-impact card) read the full block here.
    organization: Optional[Dict[str, Any]] = None


# ---------------------------------------------------------------------------
# Runtime
# ---------------------------------------------------------------------------


class RunRequest(BaseModel):
    action: str = Field(min_length=1)
    inputs: Dict[str, Any] = Field(default_factory=dict)
    user_id: Optional[str] = None
    correlation_id: Optional[str] = None
    # Labels the caller surface. ``queue_action`` = a UI click; ``chat`` = the
    # copilot; ``unknown`` is the safe default for callers that don't set it.
    # Every run is plan-then-apply regardless of mode (universal approval).
    mode: Optional[Literal["queue_action", "chat", "unknown"]] = None


def compute_plan_hash(planned_writes: Optional[List[Dict[str, Any]]]) -> str:
    """Stable content hash of a recommendation's PROPOSED writes — the exact
    values the officer is shown. Hashes only the semantic content (dataset_id,
    action_id, payload), canonically, so the SAME proposal always yields the
    SAME hash on display and at approve-time. Used to verify, at approval, that
    what the officer saw is what will commit (a re-run that overwrote the staged
    plan yields a different hash → the approve is rejected as stale)."""
    import hashlib
    import json as _json

    canon = [
        {
            "dataset_id": (pw or {}).get("dataset_id"),
            "action_id": (pw or {}).get("action_id"),
            "payload": (pw or {}).get("payload") or {},
        }
        for pw in (planned_writes or [])
    ]
    blob = _json.dumps(canon, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


class GateResult(BaseModel):
    """One gate's verdict. Deterministic — no LLM in this path."""

    model_config = ConfigDict(extra="forbid")

    gate_id: str
    label: str
    #: 'pass' | 'fail' | 'flag'. 'flag' is what a rule ERROR degrades to — a
    #: gate that could not be evaluated is never a silent pass.
    status: Literal["pass", "fail", "flag"]
    rationale: str = ""
    citations: List[Dict[str, Any]] = Field(default_factory=list)


class FactorScoreRow(BaseModel):
    """One rendered row of the grid. Every field here is either declared in the
    spec or reported by the evaluator — nothing is inferred at render time."""

    model_config = ConfigDict(extra="forbid")

    factor_id: str
    label: str
    scope: Literal["entity", "case"] = "entity"
    score: Optional[float] = None
    weight: Optional[float] = None
    band: Optional[str] = None
    rationale: str = ""
    citations: List[Dict[str, Any]] = Field(default_factory=list)
    clauses_fired: List[Dict[str, Any]] = Field(default_factory=list)
    confidence: Optional[float] = None
    #: True when no finding came back for a declared factor. The row still
    #: renders — a silently missing factor would change the total's meaning
    #: without changing its appearance.
    unscored: bool = False

    # ── Officer override (docs/factor-scorecard-plan.md phase 4) ──
    # ``score`` above is the EFFECTIVE score. When an officer overrides it, the
    # model's own score is preserved in ``original_score`` rather than being
    # overwritten (the name avoids Pydantic's protected ``model_`` namespace) — a
    # scorecard
    # that cannot show what the model said before a human moved it has lost the
    # audit property this feature exists for. All four fields are set together.
    original_score: Optional[float] = None
    override_reason: Optional[str] = None
    overridden_by: Optional[str] = None
    overridden_at: Optional[datetime] = None

    #: The policy passage behind this factor changed since the rubric was
    #: extracted and confirmed. Advisory — see FactorSopRef.fingerprint.
    sop_drift: bool = False


class FactorScorecard(BaseModel):
    """The computed scorecard for one case.

    The MODEL scores factors; this object is assembled by CODE. Weights are
    never shown to the model and the arithmetic never passes through it — a
    composite that is not reproducible gets killed by model validation, and
    rightly.

    Carried identically by the app, the embed card and the Decision API: they
    read one spec and one payload, so anything missing here is missing from all
    three equally."""

    model_config = ConfigDict(extra="forbid")

    mode: FactorSetMode
    terminology: FactorTerminology
    #: Gates first. If any gate FAILS the composite is suppressed (``total``,
    #: ``percent`` and ``grade`` are None) because "68/100 — declined" is
    #: confusing, not informative. Rows are still returned as supporting detail.
    gates: List[GateResult] = Field(default_factory=list)
    gated: bool = False
    rows: List[FactorScoreRow] = Field(default_factory=list)
    #: Composite mode only, and only when not gated.
    total: Optional[float] = None
    max_total: Optional[float] = None
    percent: Optional[float] = None
    grade: Optional[str] = None
    #: Declared factors that produced no finding this run. Non-empty means the
    #: composite is computed over less than the full rubric — surfaced, never
    #: silently absorbed.
    unscored_factor_ids: List[str] = Field(default_factory=list)

    # ── Officer override ──
    #: True when any row carries an officer's score. The composite below is the
    #: POST-override figure — it is what the officer is acting on, so it is what
    #: the ledger must record. The pre-override pair is kept beside it so the
    #: two are always comparable and an override can never be invisible.
    #:
    #: NOTE for whoever wires approval authority to the grade: an override can
    #: move the grade, and therefore could let an officer widen their own
    #: signing limit. This model deliberately does NOT decide that — inventing
    #: an authority rule here would put industry policy in the engine. It
    #: records everything such a rule needs: who overrode, when, why, the
    #: model's original score, and both grades.
    overridden: bool = False
    grade_before_override: Optional[str] = None
    percent_before_override: Optional[float] = None
    #: Bumped on every accepted override. The write is conditional on the
    #: revision that was read, so two officers correcting different factors on
    #: the same case cannot silently clobber each other — the second gets a
    #: 409 and re-reads, instead of losing a correction that appeared to save.
    revision: int = 0

    #: Factors whose SOP passage changed since extraction. Non-empty means part
    #: of this rubric was confirmed against a policy document that has since
    #: moved — the app needs re-extraction, and the officer should know before
    #: relying on the grade.
    sop_drift_factor_ids: List[str] = Field(default_factory=list)


class RunResponse(BaseModel):
    correlation_id: str
    status: Literal["completed", "pending_approval", "failed"]
    outputs: Dict[str, Any] = Field(default_factory=dict)
    timeline: List[Dict[str, Any]] = Field(default_factory=list)
    error: Optional[str] = None

    # ---- audit surface --------------------------------------------------
    # Populated by the runtime executor and persisted verbatim by the
    # ``/run`` endpoint into the append-only ``app_run_audit`` collection.
    # ``decision`` / ``reasoning`` / ``citations`` come from the structured
    # audit block the LLM is instructed to emit (see runtime._AUDIT_*).
    # ``references`` is what the runtime *actually* retrieved (matched
    # few-shot samples + tool-call digests) — kept separate from the LLM's
    # self-reported ``citations`` so the two can be cross-checked.
    decision: Optional[str] = None
    reasoning: Optional[str] = None
    citations: List[Dict[str, Any]] = Field(default_factory=list)
    # Precedent receipts (adoption plan §4): the PAST CASES the model says
    # informed this recommendation — [{decision_id, relation: similar|differs,
    # note}]. Self-reported like citations; cross-check against
    # references.few_shot_samples (what was actually retrieved).
    cited_precedents: List[Dict[str, Any]] = Field(default_factory=list)
    # Clause receipts (clause-memory plan §10): the LEARNED RULES the model says
    # it applied or overruled — [{clause_id, relation: applied|overruled, note}].
    # On a reject this is the BLAME EDGE: only cited clauses are penalised, never
    # the whole injected set (blaming everything is the credit-assignment bug the
    # clause store exists to fix).
    cited_clauses: List[Dict[str, Any]] = Field(default_factory=list)
    references: Dict[str, Any] = Field(default_factory=dict)
    # Structured per-item findings collected from image_analyze / doc_extract
    # tool calls during the run. The runtime surfaces these to the officer for
    # per-item accept/reject (each is an ``ItemFinding``); reject-reasons train
    # the (app, modality, task_type) rubric. Empty for runs with no such tools.
    item_findings: List[Dict[str, Any]] = Field(default_factory=list)
    # The computed scorecard, when the app declares a factor_set. Assembled in
    # code from the factor findings above — the model never sees the weights and
    # never does the arithmetic. None when the app declares no rubric, which is
    # the common case.
    scorecard: Optional[FactorScorecard] = None
    # Structured record of every LLM-issued source-system write that happened
    # during the run. One entry per write tool call, with the full payload
    # the LLM emitted and the (capped) MCP response — forensic ground truth
    # so an auditor can reconstruct what the LLM actually mutated without
    # having to replay the run.
    write_events: List[Dict[str, Any]] = Field(default_factory=list)
    usage: Dict[str, Any] = Field(default_factory=dict)
    model: Optional[str] = None
    # OTel trace_id captured at execute_run start, when the SDK is present.
    # The platform-wide ledger plan (docs/llm-governance.md step 1) sets
    # decision_id == trace_id so one click jumps from the audit row to the
    # full distributed trace in Tempo. None when otel isn't installed.
    trace_id: Optional[str] = None
    # Captured-but-not-applied writes from a plan-only run. Each entry is
    # `{source_id, dataset_id, action_id, payload, idempotency_key, mcp_result}`
    # — the full intent the LLM proposed plus the MCP's dry-run validation
    # response. The /approve endpoint replays exactly these against
    # /execute_action with dry_run=False, so apply ≡ plan and the LLM
    # cannot drift between the two steps. Empty when the run wasn't
    # plan-only or when the LLM proposed no writes.
    planned_writes: List[Dict[str, Any]] = Field(default_factory=list)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def plan_hash(self) -> str:
        """Content hash of ``planned_writes`` — the UI echoes this back on
        approve so the server can verify the officer approved exactly the
        proposal that was shown (see ApproveRequest.expected_plan_hash)."""
        return compute_plan_hash(self.planned_writes)


# ---------------------------------------------------------------------------
# Recommendation inbox — per-case rows staged when the app's agent proposes a
# source-system write that needs officer review (no AI agent writes to source
# systems unattended). Fed by the app's agent either on-demand (/apps/{slug}/run)
# or precomputed by an app trigger. The UI's Approve step replays
# ``planned_writes`` against dept-MCP /execute_action with dry_run=False. The
# collection is historically named ``smartapp_workflow_staging``.
# ---------------------------------------------------------------------------


WorkflowStagingStatus = Literal[
    "pending_review",       # generic initial state (on-demand / non-laddered apps)
    "pending_je_review",
    "pending_ae_review",
    "pending_ee_review",
    "applied",
    "rejected",
    "cancelled",            # officer dismissed the recommendation without applying
    "expired",
    "stale",
]


class WorkflowStagingRow(BaseModel):
    """One per-case row in the recommendation inbox.

    Idempotent on ``(workflow_execution_id, case_natural_key)`` — retries
    upsert the same row (``workflow_execution_id`` is the staging run id; the
    name is retained for collection compatibility). ``status`` starts at the
    first stage's ``pending_*_review`` value derived from ``planned_writes``
    ordering and advances through escalation / approval / rejection.

    ``planned_writes`` mirrors the structure ``RunResponse.planned_writes``
    uses on the queue-action path: each entry is
    ``{source_id, dataset_id, action_id, payload, idempotency_key}``. The
    Approve handler replays these one-for-one against the dept-MCP so the
    LLM cannot drift between propose and apply.

    ``display_context`` is a denormalized snapshot of everything the
    reviewer's screen needs (case fields, customer name, claim amount,
    document URLs, related history rows) so the UI does not have to
    re-resolve panel data sources at review time.
    """

    model_config = ConfigDict(extra="forbid")

    workflow_execution_id: str = Field(min_length=1)
    case_natural_key: str = Field(min_length=1)
    tenant_id: Optional[str] = None
    org_id: Optional[str] = None
    dept_ids: List[str] = Field(default_factory=list)
    slug: str = Field(min_length=1, description="Smart App slug this case belongs to.")
    llm_evidence_summary: Optional[str] = None
    llm_recommendation_text: Optional[str] = None
    llm_reasoning: Optional[str] = None
    planned_writes: List[Dict[str, Any]] = Field(default_factory=list)
    display_context: Dict[str, Any] = Field(default_factory=dict)
    # Memory-lift carrier: grounding samples retrieved for the recommendation
    # (from RunResponse.references.retrieval_count); rides to the
    # DecisionRecord at approve/reject. None = pre-stamp row.
    retrieval_count: Optional[int] = None
    # Precedent receipts the model cited for this recommendation — rendered as
    # chips on the officer's result card and carried to the DecisionRecord.
    cited_precedents: List[Dict[str, Any]] = Field(default_factory=list)
    # The scorecard computed at /run, frozen onto the row. Denormalized for the
    # same reason display_context is: the reviewer's screen must not re-run the
    # rubric to render, and a grade recomputed later against edited weights
    # would silently disagree with the one the officer acted on.
    #
    # This is also what makes a GRADE COLUMN on the queue possible — a queue
    # cannot be ranked by a grade that only exists once someone opens the case
    # (docs/factor-scorecard-plan.md, "When factors are computed"). None when
    # the app declares no factor_set.
    scorecard: Optional[Dict[str, Any]] = None
    # Clause memory (plan §10.1): what the model SAW (injected_clause_ids, the
    # denominator for fired_count) and what it SAID it used (cited_clauses, the
    # blame edge), plus the case signature FROZEN at /run so the correction
    # recorded at approve/reject carries the facets the model actually had —
    # never a recomputed set a later ontology edit would change.
    injected_clause_ids: List[str] = Field(default_factory=list)
    cited_clauses: List[Dict[str, Any]] = Field(default_factory=list)
    case_facets: List[str] = Field(default_factory=list)
    signature_version: Optional[int] = None
    status: WorkflowStagingStatus = "pending_je_review"
    assignable_to: Dict[str, Any] = Field(
        default_factory=dict,
        description="Visibility / routing claims: {role, dept_id, district, …}.",
    )
    created_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    resolved_at: Optional[datetime] = None
    applied_by: Optional[str] = None
    audit_trail: List[Dict[str, Any]] = Field(default_factory=list)
    # Source channel the row was produced by:
    #   "trigger"      — eager precompute by an app trigger (schedule/webhook/poll
    #                    firing the app's agent ahead of the officer's click).
    #   "queue_action" — lazy on-demand recommendation written by /apps/{slug}/run
    #                    when the agent produced a plan for the officer to approve.
    # Both land in the SAME queue collection — this field only drives the UI
    # chip ("AI-recommended (precomputed)" vs "(on-demand)"). Server-side
    # default so a producer cannot accidentally omit it. (Legacy rows may carry
    # the historical value "workflow"; the UI treats anything != "queue_action"
    # as AI-recommended.)
    source: Literal["trigger", "queue_action"] = "trigger"
    # Provenance for audit: who published the app that produced this row, and
    # which spec version was active. Optional because legacy rows pre-dating
    # the field have neither.
    published_by_user_id: Optional[str] = None
    published_workflow_version: Optional[int] = None


# ── Audit read API ──────────────────────────────────────────────────────


class AuditRunSummary(BaseModel):
    """One row in the Audit run-list (``GET /apps/{slug}/runs``)."""

    audit_id: str
    correlation_id: str
    created_at: datetime
    requested_by: Optional[str] = None
    action: str
    status: str
    decision: Optional[str] = None
    duration_ms: Optional[int] = None
    model: Optional[str] = None
    agent_spec_version: Optional[int] = None
    # True when a source-system write happened but the LLM omitted the
    # structured audit block (decision is None). Surfaces to the audit
    # screen as a red flag — every write must come with a decision.
    audit_missing: bool = False
    # Convenience count so the run-list can badge "3 writes".
    write_count: int = 0


class AuditRunListResponse(BaseModel):
    slug: str
    total: int
    limit: int
    offset: int
    runs: List[AuditRunSummary] = Field(default_factory=list)


class AuditRunDetailResponse(BaseModel):
    """Full audit trail for one ``correlation_id``.

    Usually one row; two when a run was ``pending_approval`` and later
    resolved (the approval re-run appends a second row). Newest first.
    """

    slug: str
    correlation_id: str
    runs: List[Dict[str, Any]] = Field(default_factory=list)
    # Auto-process (auto-approve) commits made under this correlation_id —
    # the DecisionRecords from ``auto_process_decisions`` (policy rule + payload
    # + ok/fail). Empty for human-driven runs. Surfaces the "what was
    # auto-written, under which rule" that lives outside ``app_run_audit``.
    auto_commits: List[Dict[str, Any]] = Field(default_factory=list)


class AutoCommitListResponse(BaseModel):
    """The app-wide auto-approve ledger (``GET /apps/{slug}/auto-commits``).

    One row per auto-process commit attempt from ``auto_process_decisions`` —
    the highest-scrutiny path (no human in the loop), made visible to reviewers.
    """

    slug: str
    total: int
    limit: int
    offset: int
    rows: List[Dict[str, Any]] = Field(default_factory=list)


class ChangeLedgerResponse(BaseModel):
    """The Change Ledger (``GET /apps/{slug}/changes``) — what actually CHANGED
    and who caused it, newest first. NOT a log of AI recommendations: a
    recommendation that no one acted on changed nothing and is excluded.

    Each entry is a committed change from one of two actors:
      • ``ai_auto`` — an auto-process commit (``auto_process_decisions``)
      • ``user``    — a human-caused change (approval / queue action / overlay
                       edit) carried on a write-bearing ``app_run_audit`` row.
    """

    slug: str
    total: int
    limit: int
    offset: int
    changes: List[Dict[str, Any]] = Field(default_factory=list)


class DecisionRecord(BaseModel):
    """The canonical, loop-facing record of one committed decision — the OPEN
    SCHEMA that powers the self-improving loop and the portable, enterprise-owned
    decision ledger (see docs/citra-self-improving-loop-plan.md and
    docs/citra-core-open-source-thesis.md).

    DERIVED + MUTABLE: reconstructable from the immutable hash-chained
    ``smartapp_run_audit`` ledger and ``auto_process_decisions``; carries a
    mutable ``outcome`` that the read-back poller stamps once the decision has
    settled. The authoritative compliance record stays the audit chain — this is
    the learning substrate written alongside it, never a replacement for it.

    Lifecycle:
      context + recommendation + overrides + action_result  — written at commit
      outcome                                                — stamped later by the
                                                               read-back poller (Stage 4)
    """

    decision_id: str
    correlation_id: str
    app_id: Optional[str] = None
    agent_id: Optional[str] = None
    slug: Optional[str] = None
    tenant_id: Optional[str] = None
    # human_approved = an officer approved/overrode an LLM recommendation;
    # human_rejected = an officer rejected it ("wrong") — immediate NEGATIVE label;
    # human_direct   = an officer acted DIRECTLY with no LLM recommendation (the
    #   no-recommend path) — the purest expert signal; outcome validated by poll;
    # auto_process   = a policy-gated autonomous commit (no human in the loop).
    mode: Literal["human_approved", "human_rejected", "human_direct", "auto_process"]
    # Provenance pointer to the authoritative immutable record:
    # {"collection": <name>, "audit_id": <id>}.
    audit_ref: Dict[str, Any] = Field(default_factory=dict)
    # The "question" — the run inputs the recommendation was made against.
    context: Dict[str, Any] = Field(default_factory=dict)
    # What the model recommended: {action, decision, reasoning, planned_writes}.
    recommendation: Dict[str, Any] = Field(default_factory=dict)
    # The GOLD LABEL — per-write officer edit vs the recommendation:
    # [{dataset_id, action_id, source_id, override: {field: {from, to}}}].
    # Empty when the officer accepted the recommendation as-is (or auto-process).
    overrides: List[Dict[str, Any]] = Field(default_factory=list)
    # WHY the officer rejected or overrode — the causal signal the model needs to
    # GENERALISE a correction (not just memorise "this case → that decision").
    # Captured from the approve modal; surfaced back into future runs' context
    # (the OFFICER CORRECTIONS prefetch). None when accepted as-is / no reason.
    decision_reason: Optional[str] = None
    # What the read-back poller looks up (Stage 4): the write targets + payloads
    # so the per-app key_field/key_value can be resolved at poll time.
    record_keys: List[Dict[str, Any]] = Field(default_factory=list)
    # Did the governed write land: {committed, applied_count, status, idempotency_key}.
    action_result: Dict[str, Any] = Field(default_factory=dict)
    # The model that produced the recommendation — for the model-swap proof.
    model: Optional[Any] = None
    # Memory-lift instrumentation: how many GROUNDING samples (past decisions,
    # not RAG clauses) informed the recommendation. 0 = a cold run; None = a
    # record written before the stamp existed (excluded from lift cohorts —
    # never coerced to 0, which would poison the cold cohort with old rows).
    retrieval_count: Optional[int] = None
    # Which LEARNED CLAUSES were in front of the model for this decision. The
    # memory-impact cohorts split on this (rules applied vs none did), so it has
    # to ride the DecisionRecord — reading it only off the staging row would
    # make every closed decision look cold and report a confident zero lift.
    injected_clause_ids: List[str] = Field(default_factory=list)
    # What the model SAID about those judgements — including relation
    # "overrode_by_rule" (a judgement set aside because the SOP pointed the
    # other way). Consolidation scans this to re-check such judgements against
    # the SOP without any extra hot-path write.
    cited_clauses: List[Dict[str, Any]] = Field(default_factory=list)
    # Stage-4 verdict, stamped later by the read-back poller; None until observed.
    # {label: "good"|"bad"|"unknown", signal: "mcp_readback", observed_at, evidence}.
    outcome: Optional[Dict[str, Any]] = None
    created_at: datetime
    updated_at: Optional[datetime] = None


class SelfLearningRequest(BaseModel):
    """Toggle per-app outcome tracking + auto-learning (POST /apps/{slug}/self-learning)."""
    auto_refresh: bool = Field(
        description="True = continuous auto-learning (memory updates on outcomes);"
        " False = manual refresh only (outcomes still tracked)."
    )
    enabled: Optional[bool] = Field(
        default=None,
        description="Outcome TRACKING on/off. Default ON; set False to disable the"
        " outcome loop for this app (also forces auto_refresh off). None = leave as-is.",
    )


class SelfLearningResponse(BaseModel):
    slug: str
    enabled: bool        # outcome tracking on?
    auto_refresh: bool   # auto-fold into memory (vs manual-only)?


class RuntimeTokenResponse(BaseModel):
    """Returned by ``POST /apps/{slug}/runtime/token``.

    The runtime engine (or any caller authorised to launch the app)
    receives a short-lived HMAC bearer that lets it call the smart-app
    internal proxy (``/smart-app/internal/ocr``, ``/smart-app/internal/...``)
    on behalf of this specific app instance. The bearer's tool-scope is
    derived from the app's published AgentSpec.tools_v2 — the runtime
    cannot call tools the BA didn't declare.
    """

    secret: str = Field(
        description=(
            "Opaque HMAC bearer. Send as ``Authorization: Bearer <secret>``"
            " when calling endpoints under ``SMART_APP_PROXY_BASE_URL``."
        )
    )
    proxy_base_url: str = Field(
        description=(
            "Internal proxy base URL the runtime should target,"
            " e.g. ``https://smart-app-service.internal/smart-app/internal``."
        )
    )
    expires_at: int = Field(
        description="Unix epoch seconds when the bearer expires."
    )
    tools: List[str] = Field(
        default_factory=list,
        description=(
            "Tool ``kind`` values this bearer is authorised to call"
            " (mirrors AgentSpec.tools_v2[].kind)."
        ),
    )


# ---------------------------------------------------------------------------
# Panel data binding
# ---------------------------------------------------------------------------


class PanelDataResponse(BaseModel):
    panel_id: str
    data_source: str
    columns: List[str] = Field(default_factory=list)
    rows: List[Dict[str, Any]] = Field(default_factory=list)
    total: int = 0
    truncated: bool = False
    source_kind: Literal[
        "static",
        "mcp",
        "rag",
        "smart_app_records",
        "workflow_staging",
        "decision_ledger",
    ]
    # Server-computed KPI metric values for dashboard panels. When present,
    # the runtime renders these directly instead of aggregating ``rows`` —
    # so COUNT/SUM reflect the WHOLE table, not the capped row fetch. Each
    # entry is {name, agg, field, value}. None for non-dashboard panels or
    # when aggregation falls back to row-counting.
    metrics: Optional[List[Dict[str, Any]]] = None
    note: Optional[str] = None
    # A real source FAILURE (MCP down / unresolved / transport / access denied),
    # distinct from a benign empty-state ``note``. The runtime renders this with
    # the error affordance so a failed read is never mistaken for "no data".
    error: Optional[str] = None


class DetailDataResponse(BaseModel):
    """Returned by ``GET /apps/{slug}/detail/{panel_id}``.

    One round trip backing a detail panel: the resolved record (matched
    from the linked queue by the ``?id=`` param) plus any per-section
    data the runtime can't compute on its own — linked documents, the
    agent-run timeline, and pending approvals for the record.
    """

    panel_id: str
    #: The queue this detail read from, or None when it binds directly via
    #: ``DetailPanel.data_source`` (an embed page has no queue to link to).
    linked_to: Optional[str] = None
    record_id: Optional[str] = None
    record: Optional[Dict[str, Any]] = None
    record_columns: List[str] = Field(default_factory=list)
    sections: List[Dict[str, Any]] = Field(default_factory=list)
    note: Optional[str] = None


# ---------------------------------------------------------------------------
# Chat (dashboard copilot) response
# ---------------------------------------------------------------------------


class ChatChartSpec(BaseModel):
    """Chart rendering spec emitted by a dashboard narrator.

    Field names mirror ``ChartPanel`` so a single front-end renderer handles
    both authored panels and copilot-emitted charts. The narrator picks
    ``chart_type`` only — it NEVER emits colors or sizes.
    """

    model_config = ConfigDict(extra="forbid")

    chart_type: Literal["bar", "line", "area", "pie"]
    title: str
    x: str
    y: Union[str, List[str]]
    group_by: Optional[str] = None
    stacked: bool = False


class ChatBlock(BaseModel):
    """A structured block attached to a chat reply. Currently only charts.

    ``data`` is the inline rows the narrator already fetched via its MCP
    tools — the block is self-contained, no separate data fetch.
    """

    model_config = ConfigDict(extra="forbid")

    type: Literal["chart"] = "chart"
    spec: ChatChartSpec
    data: List[Dict[str, Any]] = Field(default_factory=list)


class ChatResponse(BaseModel):
    """Returned by ``POST /apps/{slug}/chat``.

    ``reply`` is ALWAYS present (markdown text). ``blocks`` is optional —
    omitted/empty when the turn produced no chart. ``tool_calls`` is the
    number of data-tool invocations the narrator made this turn.
    """

    reply: str
    blocks: List[ChatBlock] = Field(default_factory=list)
    tool_calls: int = 0


# ---------------------------------------------------------------------------
# Generic
# ---------------------------------------------------------------------------


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    service: str = "smart-app-service"
    environment: str
    mongo_connected: bool


class SuccessResponse(BaseModel):
    success: bool = True
    message: Optional[str] = None


# ---------------------------------------------------------------------------
# Capabilities (builder ↔ BA contract)
# ---------------------------------------------------------------------------


class CapabilityLimits(BaseModel):
    max_run_seconds: int
    max_tool_calls_per_run: int
    max_input_bytes: int
    max_attachments: int
    rag_top_k_max: int
    min_poll_interval_seconds: int
    min_cron_granularity_seconds: int
    max_concurrent_runs_per_trigger: int
    max_runs_per_tenant_per_minute: int
    webhook_payload_max_bytes: int
    sub_agent_max_depth: int


class PlatformFeatures(BaseModel):
    hitl_approvals: bool
    audit_trail: bool
    schedules: bool
    polling: bool
    webhooks_inbound: bool
    webhooks_outbound: bool
    outbound_email: bool
    outbound_sms: bool
    pdf_generation: bool
    sub_agent_routing: bool
    enterprise_sharing: bool


class CapabilitiesResponse(BaseModel):
    """What the builder pod is allowed to promise the BA.

    Loaded by the ``citra-app-discovery`` skill at the start of an
    interview. The builder's system prompt is constrained to only design
    flows using ``tools_available`` + features set to ``true``. Anything
    else surfaces as a gap in ``AppSpec.requirements_unmet[]``.
    """

    platform_features: PlatformFeatures
    limits: CapabilityLimits
    tools_available: List[str] = Field(
        default_factory=list,
        description=(
            "Live list of tool names registered in discovery-service that"
            " the BA's tenant may use. Empty list means discovery-service"
            " was unreachable — the builder should warn rather than design"
            " around no tools."
        ),
    )
    discovery_reachable: bool = True
    tools_total: int = Field(
        default=0,
        description=(
            "Total RBAC-visible tool count BEFORE rerank/top_k truncation."
            " When ``needs_scope`` is true the discovery skill should ask"
            " the BA for a target area (e.g. claims, procurement) before"
            " continuing rather than design against a truncated list."
        ),
    )
    needs_scope: bool = Field(
        default=False,
        description=(
            "True when the RBAC-visible tool list is large enough that"
            " even a reranker top_k may miss the relevant tool. Surface"
            " this to the BA and ask them to narrow scope."
        ),
    )


class TriggerFireResponse(BaseModel):
    """Returned from POST /apps/{slug}/trigger/{trigger_id}."""

    correlation_id: str
    trigger_id: str
    fired: bool
    status: Literal["completed", "pending_approval", "failed", "skipped"]
    reason: Optional[str] = None
