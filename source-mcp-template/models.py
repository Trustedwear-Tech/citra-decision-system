# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""
Dept MCP Server — API Models
==============================
Query-plane only. Ingestion models removed — ``Citra-Service/dept_sources``
owns source definitions and the Workflow Engine owns ingestion runs.
"""

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class SourceType(str, Enum):
    semantic   = "semantic"
    structured = "structured"
    mongodb    = "mongodb"
    rest_api   = "rest_api"


# Canonical document-type vocabulary. Per-source ``taxonomy`` blocks may
# extend or restrict this set; values are kept open (plain str) so customers
# can introduce domain-specific labels without code changes.
CANONICAL_DOC_TYPES: List[str] = [
    "policy",
    "sop",
    "manual",
    "contract",
    "regulation",
    "report",
    "correspondence",
]

# Classification levels — outermost = least sensitive. Used as the upper
# bound a caller is permitted to see.
CLASSIFICATION_LEVELS: List[str] = ["public", "internal", "confidential", "restricted"]


class TaxonomyEntry(BaseModel):
    """One doc_type advertised by a source (see ``dept_sources.taxonomy``)."""
    id: str
    label: Optional[str] = None
    synonyms: List[str] = []
    examples: List[str] = []


class SourceTaxonomy(BaseModel):
    """Optional ``taxonomy`` block on a dept_sources document."""
    doc_types: List[TaxonomyEntry] = []
    classification_levels: List[str] = []


# ── /query — consumed by Citra enterprise_search.py ─────────────────────────

class QueryRequest(BaseModel):
    query: str = Field(..., description="Natural-language query from Citra routing")
    source_id: str = Field(..., description="Which registered source to query")
    max_results: int = Field(default=5, ge=1, le=50)
    dataset_ids: Optional[List[str]] = Field(
        default=None,
        description=(
            "Optional list of catalogue dataset_ids to scope the planner to. "
            "When omitted the planner enumerates datasets from `source_id` (or "
            "vector-searches the catalogue). Lets the dashboard / chat tool "
            "pin specific datasets — the new catalogue-keyed entry point."
        ),
    )
    doc_types: Optional[List[str]] = Field(
        default=None,
        description=(
            "Optional pre-filter on chunk metadata.doc_type. "
            "Common values: policy, sop, manual, contract, regulation. "
            "Omit to search all doc types."
        ),
    )
    classification_max: Optional[str] = Field(
        default=None,
        description=(
            "Optional upper bound on chunk metadata.classification "
            "(public < internal < confidential < restricted)."
        ),
    )


class ChunkResult(BaseModel):
    text: str
    score: float
    source: str
    metadata: Dict[str, Any] = {}


class QueryResponse(BaseModel):
    results: List[ChunkResult]
    source_id: str
    source_type: SourceType
    total: int


# ── /health ─────────────────────────────────────────────────────────────────

class HealthResponse(BaseModel):
    status: str = "ok"
    service: str = "dept-mcp"
    sources: List[str] = []
    discovery_registered: bool = False
    # The org this MCP serves (ORG_ID env). Surfaced so a fleet check can diff
    # it against user-service / data-discovery and catch single-customer drift.
    org_id: Optional[str] = None


# ── Catalogue contract ──────────────────────────────────────────────────────
#
# Five tools that every Citra MCP shim must expose, on top of /query, so the
# data-discovery-service and smart-app builder can interrogate any backend
# uniformly:
#
#   GET  /datasets                 → list_datasets   (DatasetRef[])
#   GET  /datasets/{id}            → describe_dataset (DatasetSchema)
#   GET  /datasets/{id}/sample     → sample_dataset   (SampleResponse)
#   POST /run_query                → run_query        (RunQueryResponse)
#   POST /execute_action           → execute_action   (ExecuteActionResponse)
#
# Backends ("kinds") supported by the dispatch layer:
#   • sql        — SQL Server / Postgres / MySQL / SQLite (real)
#   • odata      — SAP S/4HANA OData v2/v4              (extension point)
#   • soql       — Salesforce SOQL                       (extension point)
#   • rest       — Generic REST endpoint w/ OpenAPI map (extension point)
#   • semantic   — Existing /query semantic backend      (read-only fallback)


class DatasetKind(str, Enum):
    """How a dataset is queried at runtime."""
    sql       = "sql"          # SELECT against an RDBMS (read) / DML (write)
    odata     = "odata"        # SAP / Microsoft OData service (incl. S/4HANA Cloud + on-prem Gateway)
    soql      = "soql"         # Salesforce SOQL
    rest      = "rest"         # OpenAPI-described REST endpoint
    semantic  = "semantic"     # Vector / hybrid retrieval over chunks
    mongodb   = "mongodb"      # Document store
    duckdb    = "duckdb"       # DuckDB-over-files (Excel / CSV / Parquet on disk, s3://, gs://, https://)
    bigquery  = "bigquery"     # Google BigQuery — uses google-cloud-bigquery (optional dep)
    sap_rfc   = "sap_rfc"      # SAP NetWeaver RFC / BAPI via pyrfc (requires SAP NW RFC SDK)


class ColumnSpec(BaseModel):
    """One column / field on a dataset."""
    name: str                                     # semantic / display name (defaults to physical_name)
    physical_name: Optional[str] = None           # original column identifier in the source DB
    type: str                                     # native type as reported by source
    semantic_type: Optional[str] = None           # inferred (email / pan / claim_id / amount / dob …)
    column_kind: Optional[str] = None             # plain | url | image_url | document_url | file —
                                                  #   marks columns whose value references an object
                                                  #   (sign via /resolve_media). Lets the agent
                                                  #   (image_analyze/doc_extract) + runtime display
                                                  #   know "this is an image/doc", not plain text.
    mime_hint: Optional[str] = None               # coarse declared content-type hint (e.g.
                                                  #   application/pdf). Optional; fetch-time sniff wins.
    nullable: bool = True
    is_primary_key: bool = False
    is_foreign_key: bool = False
    foreign_ref: Optional[str] = None             # "to_table.to_column" — references the *physical* names
    pii: bool = False                             # set by classifier in data-discovery-service
    sensitivity: Optional[str] = None             # public | internal | confidential | restricted
    distinct_values: Optional[List[str]] = None   # populated for low-cardinality strings
    range: Optional[Dict[str, str]] = None        # {"min": ..., "max": ...} for numerics/dates
    description: Optional[str] = None
    # Fraud ontology (drives role-aware reuse detection downstream — see
    # smart-app-service/fraud_roles.py). Declared per artifact column in sources.json.
    artifact_role: Optional[str] = None           # identity | evidence | supporting | payment_proof
    reuse_policy: Optional[str] = None             # expected | suspicious | ignore (overrides role default)


class Relationship(BaseModel):
    """An FK / logical relationship between two datasets."""
    from_column: str
    to_dataset: str
    to_column: str
    inferred: bool = False                        # True if the data-discovery-service guessed it


class WriteAction(BaseModel):
    """A registered insert / update / RPC the agent can call via execute_action.

    Each backend kind interprets the fields differently:

      sql      — `sql_template` is a parameterised statement
                 (`INSERT INTO claims (id, amount) VALUES (:id, :amount)`)
      odata    — `endpoint` is the OData entity URL; method = POST/PATCH/MERGE
      rest     — `endpoint` + `method` come from the source's OpenAPI map
      soql     — `endpoint` is the sObject name; method = create/update/upsert
    """
    id: str
    verb: str                                     # create | update | upsert | delete | rpc
    method: Optional[str] = None                  # HTTP verb (POST/PATCH/...)
    endpoint: Optional[str] = None                # OData URL / REST path / sObject name
    sql_template: Optional[str] = None            # parameterised statement for `sql` kind
    input_schema: Dict[str, Any] = Field(default_factory=dict)
    idempotency_key_field: Optional[str] = None
    requires_csrf: bool = False                   # OData S/4 needs X-CSRF-Token handshake
    description: Optional[str] = None
    # mongodb writes: payload fields that identify the target document for
    # update / upsert / delete (the filter key). Ignored for `create`.
    key_fields: List[str] = Field(default_factory=list)
    # Write-authz gate — roles permitted to invoke THIS action, checked on
    # top of the read visibility PDP. Empty = platform default (dept_admin+).
    roles_allowed_write: List[str] = Field(default_factory=list)


class ReadVia(BaseModel):
    """How `run_query` should interpret the agent's query for this dataset."""
    kind: DatasetKind
    target: Optional[str] = None                  # table / OData service / sObject / REST path
    extra: Dict[str, Any] = Field(default_factory=dict)


class DecisionHistory(BaseModel):
    """Marks a dataset as a record of *completed decisions* — rows that each
    carry the input a team saw AND the decision/outcome they reached.

    Such a dataset can ground an agent in the team's own past judgments
    (few-shot grounding — see docs/smart-app-grounding.md). A live
    transactional table is NOT a decision record; an append-only log of
    closed / adjudicated cases is. The grounding pipeline reads
    ``decision_column`` + ``timestamp_column`` to pull and package samples,
    so declaring those here lets the builder skip guessing field names.

    Authored per-dataset in the MCP's ``sources.json`` (authoritative). A
    future data-discovery enricher pass may *propose* candidates with
    ``declared=False`` — see docs/smart-app-grounding.md.
    """
    is_decision_record: bool = False              # the gate — usable for grounding?
    decision_column: Optional[str] = None         # outcome field, e.g. "decision" / "status"
    timestamp_column: Optional[str] = None        # for date-range pulls, e.g. "closed_at"
    terminal_states: List[str] = Field(default_factory=list)  # values meaning "closed"
    reasoning_column: Optional[str] = None        # reviewer / adjuster notes column, if any
    declared: bool = True                         # True = sources.json; False = inferred

    # ── OUTCOME SIGNAL (self-improving loop) — what means the decision WORKED ──
    # Declared by IT/data HERE (who own the schema), so the builder + runtime
    # DERIVE the outcome-poll config from the catalogue instead of hand-authoring
    # it per app. The read-back poller reads ``outcome_field`` of the record by
    # ``key_field`` after ``settling_window_days`` and classifies good/bad/neutral.
    # See docs/citra-self-improving-loop-plan.md. All optional — when unset, the
    # dataset can still ground (grounding) but won't auto-observe outcomes.
    outcome_field: Optional[str] = None           # column to judge on (often = decision_column / "status")
    good_values: List[str] = Field(default_factory=list)     # values meaning the decision WORKED
    bad_values: List[str] = Field(default_factory=list)      # values meaning it FAILED / was reversed
    neutral_values: List[str] = Field(default_factory=list)  # settled but no signal (e.g. "escalated")
    outcome_hold_field: Optional[str] = None      # a field the decision WROTE; if it later changed → overturned → bad
    key_field: Optional[str] = None               # record key for the read-back (defaults to the dataset id field)
    settling_window_days: Optional[float] = None  # days to wait before judging (default ~7 if unset)


class DatasetRef(BaseModel):
    """Lightweight listing entry returned by /datasets."""
    id: str                                       # globally unique, e.g. "<source_id>.<table>"
    source_id: str
    name: str                                     # semantic / display name
    physical_name: Optional[str] = None           # original table / object identifier
    kind: DatasetKind
    description: Optional[str] = None
    row_count_approx: Optional[int] = None
    has_pii: Optional[bool] = None
    last_refreshed_at: Optional[str] = None       # ISO-8601
    decision_history: Optional[DecisionHistory] = None  # set when this dataset is a decision record
    mandatory_when_used: Optional[bool] = None    # policy-mandated read (see DatasetSchema)


class PaymentProof(BaseModel):
    """E4 payment-proof verification config (mirrors registry_models.PaymentProof)."""
    ledger_dataset: str
    match_field: str
    amount_field: Optional[str] = None
    date_field: Optional[str] = None
    party_field: Optional[str] = None
    doc_ref_field: str = "transaction_ref"
    doc_amount_field: str = "amount"
    doc_date_field: str = "payment_date"
    doc_party_field: Optional[str] = None
    # None = "not declared" — the smart-app side resolves ontology-explicit >
    # vertical pack (domain.sub_vertical) > platform default (1% / 3 days).
    # A parse-time default here would make 'omitted' indistinguishable from an
    # explicit value and silence the pack forever.
    amount_tolerance_pct: Optional[float] = Field(default=None, ge=0)
    date_window_days: Optional[int] = Field(default=None, ge=0)


class FraudScreening(BaseModel):
    """Per-dataset fraud-screening opt-in from sources.json. ``applies`` is the
    switch. It is TRISTATE on purpose:
      * true  → screen every recommendation for this dataset;
      * false → hard opt-out (never screen, even if columns declare roles);
      * omitted (None) → screen iff ≥1 column declares an artifact_role.
    Hence Optional[bool] with None default — a block carrying ONLY the advisory
    hints (value_fields/identity_fields) must NOT read as a hard false.
    Defined before DatasetSchema — this module does not use PEP 563 annotations."""
    applies: Optional[bool] = None
    value_fields: List[str] = Field(default_factory=list)     # asset-value cols (severity hint)
    identity_fields: List[str] = Field(default_factory=list)  # cross-record linkable keys
    # EXIF↔claim comparator (E1): record columns carrying the CLAIMED incident
    # context — capture-before-claim + GPS-vs-site checks run only when declared.
    incident_date_field: Optional[str] = None   # claimed incident/report date column
    location_lat_field: Optional[str] = None    # claimed site latitude column (decimal degrees)
    location_lon_field: Optional[str] = None    # claimed site longitude column (decimal degrees)
    gps_radius_km: Optional[float] = Field(default=None, ge=0)  # GPS gate km; 0 = strict, negatives rejected
    # E4 payment-proof: verify extracted receipt refs against the named ledger dataset.
    payment_proof: Optional[PaymentProof] = None
    # Generic cross-dataset verification blocks (plan F4) — validated by
    # registry_models.VerifyAgainst at load; carried verbatim here.
    verify_against: Optional[List[Dict[str, Any]]] = None
    # E6 declarative date rules — validated by registry_models.DateRule at
    # load; carried verbatim.
    date_rules: Optional[List[Dict[str, Any]]] = None


class Domain(BaseModel):
    """The deployment-targeting triple (vertical / sub_vertical / country) from
    sources.json — the EFFECTIVE value per dataset (dataset override, else the
    source's). Validation (closed enums, pairing, derivation fill) happened in
    registry_models at load; this describe layer carries the result verbatim."""
    vertical: Optional[str] = None       # insurance | banking | utility | field_service
    sub_vertical: Optional[str] = None   # e.g. loan_recovery, claims, metering_inspection
    country: Optional[str] = None        # ISO-3166 alpha-2: IN | US
    region: Optional[str] = None
    currency: Optional[str] = None       # filled from country at load when omitted
    date_order: Optional[str] = None     # DMY | MDY | YMD — filled likewise
    notes: Optional[str] = None


class DatasetSchema(BaseModel):
    """Full schema returned by /datasets/{id}.

    This is the document the LLM (and the smart-app builder UI) reads to
    plan reads/writes. It carries everything in one place so callers never
    need to hit two endpoints to compose a query.
    """
    id: str
    source_id: str
    name: str                                     # semantic / display name
    physical_name: Optional[str] = None           # original table / object identifier
    kind: DatasetKind
    description: Optional[str] = None
    columns: List[ColumnSpec] = []
    relationships: List[Relationship] = []
    read_via: ReadVia
    # Read-parameter contract (REST / parameterised sources): the JSON Schema of
    # the params a caller must supply to invoke this read (e.g. {"required":["pan"]}).
    # The builder wires a form/filter to these; the MCP validates them. Empty for
    # plain table reads. Mirror of write_actions[].input_schema, for the read side.
    input_schema: Dict[str, Any] = Field(default_factory=dict)
    write_actions: List[WriteAction] = []
    samples_redacted: bool = True                 # whether sample rows are PII-masked
    row_count_approx: Optional[int] = None
    last_refreshed_at: Optional[str] = None
    decision_history: Optional[DecisionHistory] = None  # set when this dataset is a decision record
    fraud_screening: Optional[FraudScreening] = None    # opt-in fraud screening (sources.json)
    # Effective domain triple (dataset override, else source) — locale packs,
    # vertical defaults and admin badges all key off this downstream.
    domain: Optional[Domain] = None
    # The MONEY definition (ROI spine) — validated by
    # registry_models.ValueSemantics at load; carried verbatim. See
    # docs/money-saved-roi-plan.md in the main repo.
    value_semantics: Optional[Dict[str, Any]] = None
    # The customer's display identity (source-level, stamped onto every
    # dataset entry) — validated by registry_models.Organization at load.
    # Apps inherit company name / logo / brand seed at publish.
    organization: Optional[Dict[str, Any]] = None
    # Policy-mandated read: when true, a decision app that reads this dataset MUST
    # run the check before staging a write (a bureau / KYC / sanctions lookup).
    # The smart-app builder defaults the mcp read tool's ``required`` flag to true
    # from this; the app can still override per its own policy. IT declares intent
    # here once, every consuming app inherits it.
    mandatory_when_used: Optional[bool] = None


class SampleResponse(BaseModel):
    """Response from /datasets/{id}/sample."""
    id: str
    rows: List[Dict[str, Any]]
    redacted: bool = True
    truncated: bool = False                       # True when source had more than `n`


class ListDatasetsResponse(BaseModel):
    datasets: List[DatasetRef]
    total: int


# ── /run_query ──────────────────────────────────────────────────────────────


class RunQueryRequest(BaseModel):
    """Execute a generated read query against a source.

    The shape of `query` depends on the dataset's `kind`:
      • sql      — string (SELECT-only, validated server-side)
      • odata    — {entity, $filter?, $select?, $top?, $orderby?}
      • soql     — string (e.g. "SELECT Id,Name FROM Account WHERE …")
      • rest     — the caller's PARAMS dict, e.g. {"pan":"…"} (or {"filters":{…}}).
                   The request/response mapping lives on the dataset
                   (read_via.extra.request/response); the MCP interpolates the
                   params into it — the caller does NOT send a pre-built request.
      • semantic — string (NL query; falls back to existing /query)
    """
    source_id: str
    dataset_id: Optional[str] = None              # when None, uses source's default
    kind: DatasetKind
    query: Any                                    # str | dict (validated per kind)
    row_limit: int = Field(default=50, ge=1, le=500)


class RunQueryResponse(BaseModel):
    rows: List[Dict[str, Any]] = []
    total: int = 0
    truncated: bool = False
    error: Optional[str] = None
    elapsed_ms: Optional[int] = None


# ── /execute_action ─────────────────────────────────────────────────────────


class ExecuteActionRequest(BaseModel):
    """Invoke a named write action registered on a dataset."""
    source_id: str
    dataset_id: str
    action_id: str
    payload: Dict[str, Any] = Field(default_factory=dict)
    idempotency_key: Optional[str] = None
    dry_run: bool = False                         # validates payload but does not execute


class ExecuteActionResponse(BaseModel):
    ok: bool
    action_id: str
    result: Dict[str, Any] = Field(default_factory=dict)
    error: Optional[str] = None
    elapsed_ms: Optional[int] = None


# ── /resolve_media ──────────────────────────────────────────────────────────


class ResolveMediaRequest(BaseModel):
    """Resolve a record's media column to a short-lived fetchable URL.

    The agent/runtime passes a logical REFERENCE — (dataset, key, column) — never a
    URL or bytes. The MCP reads the row by key under the caller's visibility, takes
    the column's NATIVE ref (``s3://bucket/key``, a bare bucket key, or an
    already-fetchable ``http(s)://`` URL), and returns a freshly-minted short-lived
    URL. Source credentials + network reach live HERE, at the one governed boundary,
    so the runtime stays source-agnostic and the SoR never stores a baked (expiring)
    presigned URL."""
    source_id: str
    dataset_id: str
    key_field: str
    key_value: str
    column: str


class ResolveMediaResponse(BaseModel):
    url: Optional[str] = None         # short-lived fetchable URL (presigned / passthrough)
    mode: str = "direct"              # "direct" today; "proxy" (MCP-streamed) is a future phase
    content_type: Optional[str] = None
    expires_at: Optional[int] = None
    error: Optional[str] = None
