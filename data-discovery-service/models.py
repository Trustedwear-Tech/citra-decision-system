# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""
data-discovery-service — Persistence models
=============================================
A *catalogue entry* is one dataset (table / OData entity / file collection)
discovered on a registered MCP shim. The catalogue is keyed by
(tenant_id, source_id, dataset_id) — globally unique.

The catalogue document is the LLM's map of the enterprise:
  • columns + inferred semantic_type + pii flags  → smart-app builder
  • read_via                                       → run_query payload shape
  • write_actions                                  → execute_action contract
  • last_refreshed_at / freshness                  → re-crawl scheduling

Two-name model (physical vs. semantic)
--------------------------------------
Legacy enterprise schemas often use cryptic table / column names
(``TBL_X_001``, ``c_dt_42``). The crawler keeps the original name as
``physical_name`` and — when the name fails a simple "is this readable?"
heuristic — asks the on-prem LLM to propose a meaningful ``name`` plus a
one-line ``description`` from sample rows + low-cardinality value lists.

Dispatch ALWAYS uses ``physical_name`` (or ``read_via.target`` at the
table level). The LLM-derived ``name`` is only what consumers see.
``mapping_status`` records provenance:

    physical    — name unchanged from source (was already readable)
    name_hint   — derived from column-name substring heuristic only
    llm         — proposed by the LLM enricher (needs review)
    manual      — set via ``dept_sources.name_overrides`` block (wins always)
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class CatalogueColumn(BaseModel):
    name: str                                      # semantic / display name (what the LLM sees)
    physical_name: Optional[str] = None            # original column identifier in the source DB
    display_name: Optional[str] = None             # BA-authored describe `name` when it differs from
                                                   #   physical (ADDITIVE — `name` stays == physical
                                                   #   because downstream url_columns/role matching keys
                                                   #   on it; UIs may prefer display_name when set)
    type: str
    semantic_type: Optional[str] = None
    column_kind: Optional[str] = None              # plain | url | image_url | document_url | file —
                                                   #   propagated from ColumnSpec or inferred (name+type)
    mime_hint: Optional[str] = None                # coarse content-type hint (e.g. application/pdf);
                                                   #   declared or inferred. A hint, not authoritative —
                                                   #   the fetch-time content-type sniff is the truth.
    nullable: bool = True
    pii: bool = False
    sensitivity: Optional[str] = None              # public | internal | confidential | restricted
    distinct_values: Optional[List[str]] = None
    range: Optional[Dict[str, str]] = None
    description: Optional[str] = None
    is_primary_key: bool = False
    is_foreign_key: bool = False
    foreign_ref: Optional[str] = None              # "to_table.to_column" — physical names
    mapping_source: str = "physical"               # physical | name_hint | llm | manual
    mapping_confidence: Optional[float] = None     # 0..1 when mapping_source == "llm"
    # Fraud ontology (copied from sources.json; drives role-aware reuse detection —
    # see smart-app-service/fraud_roles.py). A repeated artifact means opposite
    # things by role: identity (a headshot) reuse is EXPECTED; evidence (an
    # accident/defect photo) reuse across cases is a double-dip fraud signal.
    artifact_role: Optional[str] = None            # identity | evidence | supporting | payment_proof
    reuse_policy: Optional[str] = None             # expected | suspicious | ignore (overrides role default)


class CatalogueWriteAction(BaseModel):
    id: str
    verb: str
    description: Optional[str] = None
    input_schema: Dict[str, Any] = Field(default_factory=dict)
    # Who may actually EXECUTE this action. The MCP really enforces it
    # (source-mcp-template/auth.py:250-290; empty ⇒ its DEFAULT_WRITE_ROLES,
    # i.e. dept_admin and up). It was dropped in transit, so the builder bound
    # mcp_action tools with no idea who could run them and would publish an app
    # whose users are structurally barred — the failure surfacing only at
    # runtime as an unexplained 403. Empty list = "MCP default", NOT "everyone".
    roles_allowed_write: List[str] = Field(default_factory=list)


class CatalogueDecisionHistory(BaseModel):
    """Marks a catalogued dataset as a record of *completed decisions* —
    usable to ground an agent in the team's past judgments (few-shot
    grounding). Mirrors ``DecisionHistory`` on the dept-MCP dataset model;
    copied through by the crawler. See docs/smart-app-grounding.md.
    """
    is_decision_record: bool = False
    decision_column: Optional[str] = None
    timestamp_column: Optional[str] = None
    terminal_states: List[str] = Field(default_factory=list)
    reasoning_column: Optional[str] = None
    declared: bool = True                          # True = MCP sources.json; False = inferred
    # Outcome-signal contract (mirrors MCP DecisionHistory — pydantic's default
    # extra='ignore' silently STRIPPED these when the crawler coerced the describe
    # dict, so the outcome poller / builder could never derive read-back config
    # from the catalogue even though IT declared it in sources.json).
    outcome_field: Optional[str] = None            # column to judge on
    good_values: List[str] = Field(default_factory=list)
    bad_values: List[str] = Field(default_factory=list)
    neutral_values: List[str] = Field(default_factory=list)
    outcome_hold_field: Optional[str] = None       # decision-written field; later change → overturned
    key_field: Optional[str] = None                # record key for the read-back
    settling_window_days: Optional[float] = None   # days to wait before judging


class CataloguePaymentProof(BaseModel):
    """E4 payment-proof config carried from sources.json (mirrors the MCP model)."""
    ledger_dataset: str
    match_field: str
    amount_field: Optional[str] = None
    date_field: Optional[str] = None
    party_field: Optional[str] = None
    doc_ref_field: str = "transaction_ref"
    doc_amount_field: str = "amount"
    doc_date_field: str = "payment_date"
    doc_party_field: Optional[str] = None
    # None = "not declared" — resolved downstream as ontology-explicit >
    # vertical pack > platform default (1% / 3 days).
    amount_tolerance_pct: Optional[float] = Field(default=None, ge=0)
    date_window_days: Optional[int] = Field(default=None, ge=0)


class CatalogueFraudScreening(BaseModel):
    """Per-dataset fraud-screening declaration from sources.json. ``applies`` is
    the only switch that drives behavior — when true the screen runs on every
    recommendation for this dataset (the agent recommends; the officer still
    decides). ``value_fields`` / ``identity_fields`` are OPTIONAL advisory hints
    for the severity + cross-record layers, not yet consumed by the auto-wiring.

    ``applies`` is TRISTATE (mirrors the MCP FraudScreening): true = screen,
    false = hard opt-out, None/omitted = screen iff a column declares a role — so
    a block carrying only advisory hints must not coerce to a hard false."""
    applies: Optional[bool] = None
    value_fields: List[str] = Field(default_factory=list)    # monetary / asset-value columns (severity hint)
    identity_fields: List[str] = Field(default_factory=list) # R↔R linkable keys (policy_no / vin / …)
    # EXIF↔claim comparator (E1) — claimed incident context columns, from
    # sources.json. Drive the builder autowire's claim_context stamp; each check
    # runs only when its column(s) are declared (ontology-driven end-to-end).
    incident_date_field: Optional[str] = None   # claimed incident/report date column
    location_lat_field: Optional[str] = None    # claimed site latitude (decimal degrees)
    location_lon_field: Optional[str] = None    # claimed site longitude (decimal degrees)
    gps_radius_km: Optional[float] = Field(default=None, ge=0)  # GPS gate km; 0 = strict, negatives rejected
    # E4 payment-proof: verify extracted receipt refs against the named ledger dataset.
    payment_proof: Optional[CataloguePaymentProof] = None
    # Generic cross-dataset verification blocks (plan F4) — already validated
    # at the MCP (registry_models.VerifyAgainst); carried verbatim.
    verify_against: Optional[List[Dict[str, Any]]] = None
    # E6 declarative date rules — validated at the MCP; carried verbatim.
    date_rules: Optional[List[Dict[str, Any]]] = None


class CatalogueDomain(BaseModel):
    """The deployment-targeting triple (vertical / sub_vertical / country) —
    the dataset's EFFECTIVE value as served by the MCP (already validated and
    derivation-filled there). Drives locale-correct fraud validators, vertical
    default packs and the admin domain badge downstream."""
    vertical: Optional[str] = None       # insurance | banking | utility | field_service
    sub_vertical: Optional[str] = None   # e.g. loan_recovery, claims, metering_inspection
    country: Optional[str] = None        # ISO-3166 alpha-2: IN | US
    region: Optional[str] = None
    currency: Optional[str] = None
    date_order: Optional[str] = None     # DMY | MDY | YMD
    notes: Optional[str] = None


class CatalogueEntry(BaseModel):
    """One row in the `data_catalogue` Mongo collection."""
    tenant_id: str
    source_id: str
    dataset_id: str                                # full id, e.g. "claims_db.policies"
    name: str                                      # semantic / display name for the dataset
    physical_name: Optional[str] = None            # original table / object name in the source
    display_name: Optional[str] = None             # BA-authored describe `name` when it differs from
                                                   #   physical (additive; `name` stays == physical)
    kind: str                                      # sql | odata | soql | rest | semantic | mongodb
    description: Optional[str] = None
    columns: List[CatalogueColumn] = []
    relationships: List[Dict[str, Any]] = []
    read_via: Dict[str, Any] = Field(default_factory=dict)
    # Read-parameter contract for parameterised sources (REST/API): the JSON
    # Schema of params a caller must supply to invoke the read (e.g. a CIBIL
    # dataset needs {"required":["pan"]}). The builder wires a form/filter to it;
    # empty for plain table reads. Copied from the MCP DatasetSchema by the crawler.
    input_schema: Dict[str, Any] = Field(default_factory=dict)
    write_actions: List[CatalogueWriteAction] = []
    has_pii: bool = False
    # Dept scoping (RBAC) — copied from the owning ``dept_sources`` doc by the
    # crawler so the catalogue can be filtered by the caller's depts.
    # ``public_within_org`` datasets (shared corpora, policy libraries) are
    # visible to any user in the org regardless of dept.
    dept_id: Optional[str] = None
    public_within_org: bool = False
    # Set when this dataset is a record of completed decisions — drives the
    # smart-app few-shot grounding offer. Copied from the MCP by the crawler.
    decision_history: Optional[CatalogueDecisionHistory] = None
    fraud_screening: Optional[CatalogueFraudScreening] = None   # from sources.json; drives builder auto-wiring
    domain: Optional[CatalogueDomain] = None                    # effective vertical/sub_vertical/country triple
    # The MONEY definition (ROI spine) — validated at the MCP
    # (registry_models.ValueSemantics); carried verbatim. Consumed by the
    # smart-app publish path (value stamping) + value-stats.
    value_semantics: Optional[Dict[str, Any]] = None
    # The customer's DISPLAY identity (company name / logo / brand seed) —
    # declared once in sources.json, validated at the MCP
    # (registry_models.Organization); consumed by publish to default the app
    # theme's company identity. Presentation only — never auth/tenancy.
    organization: Optional[Dict[str, Any]] = None
    # Policy-mandated read: when true, a decision app reading this dataset must run
    # the check before staging a write (bureau/KYC/sanctions). Drives the builder
    # to default the mcp tool's ``required`` flag. From sources.json via the MCP.
    mandatory_when_used: Optional[bool] = None
    row_count_approx: Optional[int] = None
    # Whether the sample values the MCP returned were PII-masked. Dropped at
    # persistence before, so a consumer reasoning over samples could not tell
    # masked values from raw ones.
    samples_redacted: Optional[bool] = None
    # Dept-mcp routing — populated by the crawler so downstream consumers
    # (smart-app runtime) can hit /run_query and /execute_action directly
    # without re-resolving via discovery-service on every call.
    mcp_base_url: Optional[str] = None
    mcp_tool_id: Optional[str] = None
    # When WE last crawled (drives re-crawl scheduling; indexed). NOT the age of
    # the underlying data — see source_last_refreshed_at.
    last_refreshed_at: datetime = Field(default_factory=lambda: datetime.utcnow())
    # The DATA's freshness as the source declares it (describe's
    # DatasetSchema.last_refreshed_at, ISO-8601). The crawler used to overwrite
    # the identical field name with utcnow(), so the authored value was
    # unrecoverable and no consumer could tell "this data is six months stale"
    # from "we crawled it five minutes ago". Two facts, two fields.
    source_last_refreshed_at: Optional[str] = None
    last_crawl_status: str = "ok"                  # ok | error | skipped
    last_crawl_error: Optional[str] = None
    # Two-name model + LLM enrichment cache.
    mapping_status: str = "pending"                # pending | proposed | approved | overridden | n/a
    mapping_source: str = "physical"               # physical | llm | manual
    schema_fingerprint: Optional[str] = None       # hash(physical_name + column physicals + types) — drives enrichment cache


class CatalogueListResponse(BaseModel):
    entries: List[CatalogueEntry]
    total: int


class CrawlReport(BaseModel):
    """One row of the crawl summary returned by /crawl/run."""
    mcp_tool_id: str
    source_id: Optional[str] = None
    datasets_seen: int = 0
    datasets_written: int = 0
    datasets_enriched: int = 0
    errors: List[str] = []
    elapsed_ms: int = 0
    #: ``source_id -> dataset_ids`` this pass actually saw served. Internal
    #: plumbing for the orphan prune in ``crawl_all``: a dataset absent here on
    #: an error-free pass no longer exists. Excluded from the API response —
    #: it's an implementation detail, not part of the crawl contract.
    seen_dataset_ids: Dict[str, List[str]] = Field(default_factory=dict, exclude=True)


class CrawlSummary(BaseModel):
    started_at: datetime
    finished_at: datetime
    reports: List[CrawlReport] = []
    total_datasets: int = 0
    #: Rows deleted because the registry no longer backs them (see
    #: ``crawler._prune_orphaned_entries``).
    total_pruned: int = 0


# ---------------------------------------------------------------------------
# Draft-then-review descriptions (Wave 2 #9)
# ---------------------------------------------------------------------------


class ColumnDescriptionProposal(BaseModel):
    physical_name: str
    description: str
    confidence: Optional[float] = None


class DraftDescriptionsResponse(BaseModel):
    """LLM-drafted descriptions for a dataset, for the DBA to review/edit before
    applying. Nothing is written by the draft call."""
    dataset_id: str
    table_description: str = ""
    table_confidence: Optional[float] = None
    columns: List[ColumnDescriptionProposal] = []


class ApplyDescriptionsRequest(BaseModel):
    """DBA-approved descriptions to write. ``column_descriptions`` is keyed by a
    column's physical_name (preferred) or semantic name."""
    table_description: Optional[str] = None
    column_descriptions: Dict[str, str] = Field(default_factory=dict)


class ApplyDescriptionsResponse(BaseModel):
    dataset_id: str
    mapping_status: str
    updated_columns: List[str] = []
    unmatched: List[str] = []          # approved keys that matched no column
