"""The sources.json INPUT contract — what an author writes.

This is deliberately NOT ``models.py``. That module is the API contract: what the
MCP RETURNS (``DatasetSchema`` says so itself — "Full schema returned by
/datasets/{id}"). The registry INPUT — the file IT hands us — had no model at
all. Not an unused one; a nonexistent one. Everything downstream was read as raw
dicts, which is why authored intent could vanish with no error: a typo'd key was
simply never read, and there was nothing to validate against but prose in
docs/sources-file.md.

Three sources of truth were reconciled to build this:
  1. docs/sources-file.md — the authoring contract.
  2. The code that actually READS the file — router._flatten, catalogue,
     registration, query_engine, the connectors.
  3. The 19 real sources across demo-data/tenants/{acme-power,acme-cement,
     bihar-gov} — 27 datasets, 256 columns. Where the doc and reality disagreed,
     reality won and the doc was corrected (e.g. `type` values, `is_demo`).

Two rules about strictness, chosen per-block rather than globally:

  * ``extra="forbid"`` on source / dataset / column. These carry the ONTOLOGY,
    where a typo fails SILENTLY: `artifact_roles` instead of `artifact_role`
    disables fraud screening on that column and nothing says a word. Unknown key
    ⇒ error, because the alternative is a lie.
  * ``extra="allow"`` on ``connection``. It is backend wiring, and it is
    genuinely polymorphic — the real files carry sysnr/client/lang (SAP),
    project_id/location/max_bytes_billed (BigQuery), default_bucket (duckdb).
    A wrong key there fails LOUDLY at connect time, so forbidding unknowns would
    only block new backends without preventing a silent failure.

Cross-field rules (mongodb needs connection.collection, semantic needs
rag.milvus_collection, fraud needs a primary key) live in validators below and
in validate_sources.py. NOTE: pydantic validators do NOT export to JSON Schema,
so the generated schema file covers structure/types/enums/unknown-keys — the CLI
is what enforces the cross-field rules. That split is documented, not accidental.
"""
from __future__ import annotations

import re
from enum import Enum
from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, RootModel, model_validator

from models import SourceTaxonomy


# ── Enumerations ────────────────────────────────────────────────────────────
# Enums (not bare str) so the generated JSON Schema carries `enum` and an editor
# rejects 'evidance' as you type. Today a bad artifact_role is only caught at
# publish, by a log line in fraud_roles.py that nobody reads.

class SourceType(str, Enum):
    """How the MCP reaches this backend.

    `bigquery`, `sap_rfc` and `duckdb` are NOT speculative: catalogue._source_kind
    branches on them and 3 of the 19 live sources use them. models.SourceType
    lists only the first four, and docs/sources-file.md said the same — so
    validating against that enum would have rejected working sources. The doc is
    fixed; this is the real set.
    """
    semantic = "semantic"
    structured = "structured"
    mongodb = "mongodb"
    rest_api = "rest_api"
    bigquery = "bigquery"
    sap_rfc = "sap_rfc"
    duckdb = "duckdb"


class DatasetKind(str, Enum):
    sql = "sql"
    mongodb = "mongodb"
    rest = "rest"
    odata = "odata"
    soql = "soql"
    duckdb = "duckdb"
    bigquery = "bigquery"
    sap_rfc = "sap_rfc"
    semantic = "semantic"


class ArtifactRole(str, Enum):
    """What an artifact column IS, for fraud screening."""
    identity = "identity"
    evidence = "evidence"
    supporting = "supporting"
    # A submitted proof-of-payment document (receipt / UTR slip / bank
    # acknowledgment). Reuse across cases is suspicious (like evidence), AND
    # it is the ONLY document whose extracted reference/amount/date/party may
    # feed fraud_screening.payment_proof ledger verification — a record's other
    # bills (e.g. a purchase invoice) can never be matched against the payment
    # ledger by mistake.
    payment_proof = "payment_proof"


class ReusePolicy(str, Enum):
    """What re-use of the same artifact MEANS (overrides the role default)."""
    expected = "expected"
    suspicious = "suspicious"
    ignore = "ignore"


class ColumnKind(str, Enum):
    """Whether a column's value is a media reference or plain data."""
    plain = "plain"
    url = "url"
    image_url = "image_url"
    document_url = "document_url"
    file = "file"


class Sensitivity(str, Enum):
    """Advisory only — the platform enforces `pii`, not this. See
    docs/write-actions.md: no classification taxonomy beyond PII."""
    public = "public"
    internal = "internal"
    confidential = "confidential"
    restricted = "restricted"


#: Kinds whose value the runtime can resolve as media (/resolve_media). Mirrors
#: smart-app-service/fraud_roles._MEDIA_COLUMN_KINDS — an artifact_role column
#: must be one of these or it is auto-wired into url_columns and fails at run.
MEDIA_COLUMN_KINDS = frozenset({
    ColumnKind.url, ColumnKind.image_url, ColumnKind.document_url, ColumnKind.file,
})


# ── Domain (vertical / sub-vertical / country) ──────────────────────────────
# The deployment-targeting triple. It SELECTS built-in behavior packs — locale
# validators, date order, vertical defaults and advisories — it never forks
# code, and it never turns screening on by itself (`fraud_screening` stays the
# only on-switch).
#
# OPEN GLOBALLY (2026-08-10). Citra is a horizontal decision system: any
# industry, any country. The previous closed enums (4 verticals, IN|US) described
# the markets we shipped TEMPLATES for, not the markets the product serves — a
# defence, healthcare or logistics deployment had no way to name itself, and
# because the triple was all-or-nothing an in-scope German bank had to drop its
# whole domain block rather than declare an unsupported country.
#
# The enums existed for a real reason: "an open string is how a typo becomes a
# silent no-op". That property is KEPT, by a different mechanism — an unknown
# vertical is accepted but REPORTED with a did-you-mean, never silently ignored.
# Countries and currencies remain genuinely validated, because ISO gives a list
# that is closed AND global.

#: ISO-3166-1 alpha-2. Complete, so a typo ('IND', 'UK') still fails.
ISO_3166_ALPHA2: frozenset = frozenset("""
AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI BJ BL
BM BN BO BQ BR BS BT BV BW BY BZ CA CC CD CF CG CH CI CK CL CM CN CO CR CU CV
CW CX CY CZ DE DJ DK DM DO DZ EC EE EG EH ER ES ET FI FJ FK FM FO FR GA GB GD
GE GF GG GH GI GL GM GN GP GQ GR GS GT GU GW GY HK HM HN HR HT HU ID IE IL IM
IN IO IQ IR IS IT JE JM JO JP KE KG KH KI KM KN KP KR KW KY KZ LA LB LC LI LK
LR LS LT LU LV LY MA MC MD ME MF MG MH MK ML MM MN MO MP MQ MR MS MT MU MV MW
MX MY MZ NA NC NE NF NG NI NL NO NP NR NU NZ OM PA PE PF PG PH PK PL PM PN PR
PS PT PW PY QA RE RO RS RU RW SA SB SC SD SE SG SH SI SJ SK SL SM SN SO SR SS
ST SV SX SY SZ TC TD TF TG TH TJ TK TL TM TN TO TR TT TV TW TZ UA UG UM US UY
UZ VA VC VE VG VI VN VU WF WS YE YT ZA ZM ZW
""".split())

#: The deployment is not tied to one country (the DEFAULT when none is given).
MULTINATIONAL = "MULTINATIONAL"

#: Common wrong-but-obvious codes, accepted and corrected rather than rejected.
COUNTRY_ALIASES: Dict[str, str] = {"UK": "GB", "EU": MULTINATIONAL, "GLOBAL": MULTINATIONAL}

#: Verticals that ship a behavior pack. NOT a restriction — any industry may be
#: declared; these are the ones with built-in defaults and advisories.
KNOWN_VERTICALS: Dict[str, frozenset] = {
    "insurance": frozenset({"claims", "underwriting"}),
    "banking": frozenset({"loan_origination", "loan_recovery"}),
    "utility": frozenset({"power_recovery", "metering_inspection"}),
    "field_service": frozenset({"equipment_inspection"}),
}

#: Slug shape for vertical / sub_vertical — lowercase, digits, underscore. Shape
#: is enforced so "Banking " or "loan recovery" fail; MEANING is not.
_SLUG_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")

#: ISO-4217 shape. Enumerating 180 currencies buys nothing over a shape check
#: plus the country map below.
_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")


class DateOrder(str, Enum):
    DMY = "DMY"
    MDY = "MDY"
    YMD = "YMD"


#: Currency + date order per country. Anything not listed falls back to
#: DEFAULT_CURRENCY / DEFAULT_DATE_ORDER — a deployment is never blocked for
#: living in a country we have not tabulated.
COUNTRY_DEFAULTS: Dict[str, Dict[str, str]] = {
    "IN": {"currency": "INR", "date_order": "DMY"},
    "US": {"currency": "USD", "date_order": "MDY"},
    "GB": {"currency": "GBP", "date_order": "DMY"},
    "AE": {"currency": "AED", "date_order": "DMY"},
    "SG": {"currency": "SGD", "date_order": "DMY"},
    "AU": {"currency": "AUD", "date_order": "DMY"},
    "CA": {"currency": "CAD", "date_order": "YMD"},
    "JP": {"currency": "JPY", "date_order": "YMD"},
    "CN": {"currency": "CNY", "date_order": "YMD"},
    "ZA": {"currency": "ZAR", "date_order": "YMD"},
    "BR": {"currency": "BRL", "date_order": "DMY"},
    "MX": {"currency": "MXN", "date_order": "DMY"},
    "NG": {"currency": "NGN", "date_order": "DMY"},
    "KE": {"currency": "KES", "date_order": "DMY"},
    "ID": {"currency": "IDR", "date_order": "DMY"},
    "PH": {"currency": "PHP", "date_order": "MDY"},
    "MY": {"currency": "MYR", "date_order": "DMY"},
    "SA": {"currency": "SAR", "date_order": "DMY"},
    "CH": {"currency": "CHF", "date_order": "DMY"},
    "SE": {"currency": "SEK", "date_order": "YMD"},
    "SysEURO": {"currency": "EUR", "date_order": "DMY"},
}
#: The euro zone — one entry each rather than a special case downstream.
for _c in ("DE FR IT ES NL BE AT IE PT FI GR SK SI LT LV EE LU CY MT HR").split():
    COUNTRY_DEFAULTS[_c] = {"currency": "EUR", "date_order": "DMY"}
COUNTRY_DEFAULTS.pop("SysEURO", None)

#: A multinational deployment reports in USD unless it says otherwise.
COUNTRY_DEFAULTS[MULTINATIONAL] = {"currency": "USD", "date_order": "YMD"}

DEFAULT_CURRENCY = "USD"
DEFAULT_DATE_ORDER = "DMY"
#: The platform's UI and prompts are English everywhere. Recorded so the
#: ontology is explicit rather than silent about it.
DEFAULT_LANGUAGE = "en"


def _did_you_mean(value: str, candidates) -> str:
    """Closest known value, for the advisory. Cheap prefix/substring match —
    good enough to catch 'bankng' and not worth a Levenshtein dependency."""
    v = value.lower()
    for c in candidates:
        if c.startswith(v[:4]) or v.startswith(c[:4]) or v in c or c in v:
            return f" (did you mean {c!r}?)"
    return ""


class Domain(BaseModel):
    """The (vertical, sub_vertical, country) targeting triple — declared per
    SOURCE; a dataset may declare its own block to override.

    Only ``vertical`` is required: if you declare a domain at all, say what
    industry it is. ``country`` defaults to MULTINATIONAL and ``sub_vertical``
    may be omitted when the industry has no meaningful sub-line."""
    model_config = ConfigDict(extra="forbid")

    vertical: str
    sub_vertical: Optional[str] = None
    country: str = MULTINATIONAL
    #: Optional state/province code (free-form — advisory only).
    region: Optional[str] = None
    #: Derived from `country` when omitted (COUNTRY_DEFAULTS); an explicit
    #: contradicting value is allowed — a deliberate, visible override.
    currency: Optional[str] = None
    date_order: Optional[DateOrder] = None
    #: Always English today; present so the assumption is declared, not implied.
    language: str = DEFAULT_LANGUAGE
    #: Free text shown in the catalogue ("Bihar DISCOM arrears recovery").
    notes: Optional[str] = None
    #: Filled by validation when the vertical/sub_vertical has no built-in pack.
    #: Advisory only — the deployment runs exactly the same, it just gets no
    #: pack defaults. Surfaced so an unknown value is never a SILENT no-op.
    pack_advisory: Optional[str] = None

    @model_validator(mode="after")
    def _normalise_and_check(self) -> "Domain":
        # -- shape (not meaning) for the industry fields
        for fld in ("vertical", "sub_vertical"):
            val = getattr(self, fld)
            if val is None:
                continue
            norm = str(val).strip().lower()
            if not _SLUG_RE.match(norm):
                raise ValueError(
                    f"domain.{fld}: {val!r} must be a lowercase slug "
                    f"(letters, digits, underscore), e.g. 'banking' or "
                    f"'loan_recovery'.")
            setattr(self, fld, norm)

        # -- country: real ISO code, or MULTINATIONAL
        c = str(self.country).strip().upper()
        c = COUNTRY_ALIASES.get(c, c)
        if c != MULTINATIONAL and c not in ISO_3166_ALPHA2:
            raise ValueError(
                f"domain.country: {self.country!r} is not an ISO-3166-1 alpha-2 "
                f"code (two letters, e.g. 'IN', 'US', 'DE', 'GB') nor "
                f"{MULTINATIONAL!r}.")
        self.country = c

        # -- pack advisory: unknown is fine, but never silent
        notes = []
        if self.vertical not in KNOWN_VERTICALS:
            notes.append(
                f"vertical {self.vertical!r} has no built-in behavior pack"
                f"{_did_you_mean(self.vertical, KNOWN_VERTICALS)}; defaults and "
                f"vertical advisories will not apply")
        elif self.sub_vertical and self.sub_vertical not in KNOWN_VERTICALS[self.vertical]:
            notes.append(
                f"sub_vertical {self.sub_vertical!r} is not a known line of "
                f"business for {self.vertical!r} "
                f"(known: {sorted(KNOWN_VERTICALS[self.vertical])}); "
                f"vertical defaults still apply")
        if self.country != MULTINATIONAL and self.country not in COUNTRY_DEFAULTS:
            notes.append(
                f"country {self.country!r} has no tabulated currency/date "
                f"defaults; using {DEFAULT_CURRENCY}/{DEFAULT_DATE_ORDER}")
        self.pack_advisory = "; ".join(notes) or None
        return self

    @model_validator(mode="after")
    def _fill_country_derivations(self) -> "Domain":
        defaults = COUNTRY_DEFAULTS.get(self.country, {})
        if self.currency is None:
            self.currency = defaults.get("currency", DEFAULT_CURRENCY)
        if self.date_order is None:
            self.date_order = DateOrder(defaults.get("date_order", DEFAULT_DATE_ORDER))
        if not _CURRENCY_RE.match(self.currency):
            raise ValueError(
                f"domain.currency: {self.currency!r} must be a three-letter "
                f"ISO-4217 code, e.g. 'USD', 'INR', 'EUR'.")
        return self

    @property
    def locale(self) -> str:
        """The fraud-check locale pack key ('in' / 'us') — lowercase country.
        MULTINATIONAL has no country-specific pack, so it resolves to ''."""
        return "" if self.country == MULTINATIONAL else self.country.lower()


# ── Leaf blocks ─────────────────────────────────────────────────────────────

class Visibility(BaseModel):
    """Who may READ this source. Enforced for structured reads (auth.py) and —
    as of the semantic-authz fix — for /semantic/search too."""
    model_config = ConfigDict(extra="forbid")

    roles_allowed: List[str] = Field(default_factory=lambda: ["user"])
    cross_org_ids: List[str] = Field(default_factory=list)
    public_within_org: bool = False


class Connection(BaseModel):
    """How to reach the backend. Credentials NEVER inline — always `env_prefix`.

    extra="allow" is deliberate (see module docstring): connection keys are
    backend-specific and a wrong one fails loudly at connect time. The keys below
    are the ones the code reads or the real files author; others pass through.
    """
    model_config = ConfigDict(extra="allow")

    env_prefix: Optional[str] = None
    type: Optional[str] = None
    # mongodb
    mongo_db: Optional[str] = None
    collection: Optional[str] = None      # REQUIRED for mongodb — see RegistrySource
    #: A LIST, always — including a single-column key (["batch_id"]) and a
    #: composite one (["kiln_id","run_date"]). All 6 live sources author a list.
    #: List-only is deliberate: catalogue._introspect_mongo does
    #: `pk = set(conn.get("primary_key") or [])`, and set() of a STRING explodes
    #: it into characters — "batch_id" becomes {'b','a','t',…} and matches no
    #: column, marking no key at all, silently. Rejecting a string here turns
    #: that trap into an authoring error.
    primary_key: Optional[List[str]] = None
    # Enterprise write safety: ANDed into every update/delete filter and merged
    # into every insert, so a write can never escape the source's tenant
    # partition (catalogue.py). Also keys the idempotency index.
    tenant_filter: Optional[Dict[str, Any]] = None
    uri: Optional[str] = None
    table: Optional[str] = None


class Rag(BaseModel):
    """Semantic (RAG) wiring. The MCP never queries these — the platform reader
    does; the MCP only advertises the collection to discovery."""
    model_config = ConfigDict(extra="forbid")

    milvus_collection: Optional[str] = None
    s3_prefix: Optional[str] = None


class Options(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: Pin which tables auto-discovery exposes.
    tables: Optional[List[str]] = None
    #: rest_api only — appended to the routing description as an invocation hint.
    invocation_template: Optional[str] = None
    #: Legacy nest for the per-source read timeout; prefer the top-level field.
    query_timeout_seconds: Optional[int] = None


# `taxonomy` is REUSED from models.py, not redefined here. TaxonomyEntry
# (id/label/synonyms/examples) + SourceTaxonomy (doc_types/classification_levels)
# already describe the AUTHORED block correctly — SourceTaxonomy's own docstring
# says "Optional taxonomy block on a dept_sources document", i.e. the input — and
# they match all 4 live taxonomy blocks. A second definition here would be the
# drift this whole exercise exists to remove. (I wrote one; reality rejected it.)


class ReadVia(BaseModel):
    """How to read this dataset. `extra` carries kind-specific wiring — notably
    the REST request/response mapping (catalogue.py) and duckdb file lists."""
    model_config = ConfigDict(extra="allow")

    kind: Optional[str] = None
    target: Optional[str] = None
    table: Optional[str] = None
    files: Optional[List[str]] = None
    extra: Optional[Dict[str, Any]] = None


class RegistryColumn(BaseModel):
    """One authored column. The ontology lives here."""
    model_config = ConfigDict(extra="forbid")

    name: str
    physical_name: Optional[str] = None
    type: Optional[str] = None
    description: Optional[str] = None
    nullable: Optional[bool] = None
    # Keys. Declare them when the backend has none to introspect — a view, a
    # Mongo collection, a parquet dataset, a purely logical FK. Fraud screening
    # will not auto-create a screen on a dataset with no primary key.
    is_primary_key: Optional[bool] = None
    is_foreign_key: Optional[bool] = None
    foreign_ref: Optional[str] = None
    # Governance
    pii: Optional[bool] = None
    sensitivity: Optional[Sensitivity] = None
    # Hints
    semantic_type: Optional[str] = None
    distinct_values: Optional[List[str]] = None
    range: Optional[Dict[str, str]] = None
    # Media contract
    column_kind: Optional[ColumnKind] = None
    mime_hint: Optional[str] = None
    # Fraud ontology
    artifact_role: Optional[ArtifactRole] = None
    reuse_policy: Optional[ReusePolicy] = None

    @model_validator(mode="after")
    def _artifact_role_needs_media_kind(self) -> "RegistryColumn":
        """An artifact_role column is auto-wired into the screen's url_columns and
        resolved as media, so an explicit non-media column_kind is a contradiction
        that today only surfaces as a mid-run failure. Absent kind is fine — it
        means undeclared, not plain (only 2 of 256 live columns declare one)."""
        if self.artifact_role and self.column_kind is not None:
            if self.column_kind not in MEDIA_COLUMN_KINDS:
                raise ValueError(
                    f"column {self.name!r}: artifact_role={self.artifact_role.value!r} "
                    f"requires a media column_kind "
                    f"({', '.join(sorted(k.value for k in MEDIA_COLUMN_KINDS))}), "
                    f"got {self.column_kind.value!r}. An artifact column is resolved "
                    f"as media at runtime; drop the role or fix the kind."
                )
        return self


class RegistryWriteAction(BaseModel):
    """A governed write. Reaches the app builder ONLY via the catalogue, i.e.
    only if declared here."""
    model_config = ConfigDict(extra="forbid")

    id: str
    verb: str
    description: Optional[str] = None
    input_schema: Dict[str, Any] = Field(default_factory=dict)
    #: Empty ⇒ the MCP's platform default (dept_admin and up), NOT "everyone".
    roles_allowed_write: List[str] = Field(default_factory=list)
    key_fields: Optional[List[str]] = None
    idempotency_key_field: Optional[str] = None
    sql_template: Optional[str] = None
    method: Optional[str] = None
    endpoint: Optional[str] = None
    requires_csrf: Optional[bool] = None


class PaymentProof(BaseModel):
    """E4 payment-proof verification config (see FraudScreening.payment_proof)."""
    model_config = ConfigDict(extra="forbid")

    #: The ledger dataset id ("<source_id>.<table>") holding real payments.
    ledger_dataset: str
    #: Ledger column matched against the reference extracted from the document.
    match_field: str
    #: Optional ledger columns compared when present on both sides.
    amount_field: Optional[str] = None
    date_field: Optional[str] = None
    party_field: Optional[str] = None
    #: Document-extraction field names (as the app's doc_extract emits them).
    doc_ref_field: str = "transaction_ref"
    doc_amount_field: str = "amount"
    doc_date_field: str = "payment_date"
    doc_party_field: Optional[str] = None
    #: OCR-noise guards: amounts within this percentage match; dates within
    #: this many days match. Deliberately generous — a false "doctored
    #: receipt" flag costs officer trust. OMITTED (None) means "let the
    #: vertical pack decide" (domain.sub_vertical → e.g. insurance claims get
    #: a 7-day window); the platform defaults (1% / 3 days) apply when neither
    #: the ontology nor a pack speaks. An explicit value always wins.
    amount_tolerance_pct: Optional[float] = Field(default=None, ge=0)
    date_window_days: Optional[int] = Field(default=None, ge=0)


class ValueKind(str, Enum):
    """What one unit of realized value IS (docs/money-saved-roi-plan.md §2)."""
    recovered = "recovered"            # money actually collected post-decision
    prevented_loss = "prevented_loss"  # exposure of cases decided bad (fraud denied)
    sanctioned = "sanctioned"          # throughput value (amount sanctioned/settled)
    settled = "settled"


class ValueAttribution(str, Enum):
    """THE attribution rule — pre-agreed at day zero, versioned in git. Which
    decisions' value counts as Citra's. Changing it mid-pilot is a VISIBLE
    ontology change (new definition_version), never a silent re-cut."""
    approved_recommendation = "approved_recommendation"  # officer approved the AI's call as-is
    any_citra_touched = "any_citra_touched"              # any committed decision on the case
    approved_within_window = "approved_within_window"    # approved + realized inside window_days


class ValueRealization(BaseModel):
    """WHERE value is realized — usually a different dataset (the payments
    ledger). Cross-dataset by design, same as payment_proof: the dataset and
    columns are validated against the catalogue when consumed."""
    model_config = ConfigDict(extra="forbid")

    dataset: str                      # "<source_id>.<table>" holding realization rows
    match_field: str                  # realization rows join the case by this column
    amount_field: str
    date_field: Optional[str] = None  # required to honor window_days
    window_days: int = Field(default=90, ge=1)


class ValueSemantics(BaseModel):
    """The MONEY definition for a decision dataset — the canonical spine every
    ROI number is computed from (docs/money-saved-roi-plan.md). Lives beside
    ``decision_history`` (which defines OUTCOMES); this defines their VALUE.

    Because it lives in sources.json, a pilot's metric definitions are frozen
    by a git commit at day zero, and every stamped value carries the hash of
    the block that computed it (definition_version)."""
    model_config = ConfigDict(extra="forbid")

    value_kind: ValueKind
    #: The record's OWN amount column (exposure / claim amount / sanction
    #: amount). Required for prevented_loss (the frozen exposure IS the value).
    exposure_field: Optional[str] = None
    #: Where money is actually realized (recovered/settled kinds).
    realization: Optional[ValueRealization] = None
    attribution: ValueAttribution = ValueAttribution.approved_recommendation
    #: For prevented_loss: committed decision outcomes that count as prevention
    #: (matched against decision_history outcome values / decision text).
    prevented_when: Optional[List[str]] = None

    @model_validator(mode="after")
    def _kind_requirements(self) -> "ValueSemantics":
        if self.value_kind == ValueKind.prevented_loss:
            if not self.exposure_field:
                raise ValueError(
                    "value_semantics: value_kind=prevented_loss requires "
                    "exposure_field — the frozen exposure at decision time IS "
                    "the prevented value.")
            if not self.prevented_when:
                raise ValueError(
                    "value_semantics: value_kind=prevented_loss requires "
                    "prevented_when — which committed outcomes count as "
                    "prevention.")
        if self.value_kind in (ValueKind.recovered, ValueKind.settled) \
                and self.realization is None:
            raise ValueError(
                f"value_semantics: value_kind={self.value_kind.value!r} "
                "requires a realization block — recovered/settled money is "
                "read from the realization dataset, never assumed.")
        return self


class DateRule(BaseModel):
    """E6 — one declarative date rule over the record's OWN columns. Fires
    when (later - earlier) < min_days_between (default 0 = plain ordering:
    an inspection dated before its work order) or > max_days_between when
    set (stale documents). Classic use: claim within days of policy start
    (min_days_between), statement older than N days (max_days_between)."""
    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=r"^[a-z][a-z0-9_]*$", max_length=60)
    earlier_field: str
    later_field: str
    min_days_between: int = Field(default=0)
    max_days_between: Optional[int] = Field(default=None, ge=0)


class VerifyCompare(BaseModel):
    """One field-pair comparison within a verify_against block."""
    model_config = ConfigDict(extra="forbid")

    #: Field name as the app's document extraction emits it.
    doc_field: str
    #: Column in the target dataset it must match.
    target_field: str
    #: How to normalize before comparing. `amount` honors tolerance_pct,
    #: `date` honors window_days, `id` strips separators/case, `text` exact
    #: after trim/casefold.
    type: str = Field(default="text", pattern="^(amount|date|id|text)$")
    #: None ⇒ platform default (1% / 3 days).
    tolerance_pct: Optional[float] = Field(default=None, ge=0)
    window_days: Optional[int] = Field(default=None, ge=0)


class VerifyAgainst(BaseModel):
    """Generic cross-dataset document verification (plan F4 — the E4 shape
    reused): a value extracted from a role-tagged document is looked up BY KEY
    in a declared target dataset, server-side. 'Reference not found' is a
    fact-grade signal; a full match is VERIFICATION. Examples: purchase bill →
    land registry, agent's "cash collected" → ledger, serial-in-photo → asset
    master, repair bill → surveyor estimate."""
    model_config = ConfigDict(extra="forbid")

    #: Slug identifying this verification in findings ("purchase_vs_registry").
    name: str = Field(pattern=r"^[a-z][a-z0-9_]*$", max_length=60)
    #: The dataset id ("<source_id>.<table>") holding ground truth.
    target_dataset: str
    #: Target column matched against the reference extracted from the document.
    match_field: str
    #: The artifact column carrying the document to verify — must exist and
    #: declare an artifact_role, so the check is PINNED to one document.
    doc_column: str
    #: Extraction field carrying the lookup reference.
    doc_ref_field: str = "reference"
    #: Field-pair comparisons run when the target row is found.
    compare: List[VerifyCompare] = Field(default_factory=list)
    #: What this verification is FOR — surfaced to the recommending agent.
    description: Optional[str] = None


class FraudScreening(BaseModel):
    """Per-dataset fraud opt-in."""
    model_config = ConfigDict(extra="forbid")

    #: Tristate. None = hints-only (not an opt-out); True = screen every
    #: recommendation; False = off.
    applies: Optional[bool] = None
    #: Accepted, documented, and NOT consumed by the wiring today.
    value_fields: Optional[List[str]] = None
    identity_fields: Optional[List[str]] = None
    #: EXIF↔claim comparator (E1) — the record columns that carry the CLAIMED
    #: incident context, so photo metadata can be checked against the claim
    #: deterministically. All optional; each check runs only when its column(s)
    #: are declared AND the record + EXIF both carry a value (absence of either
    #: is a non-signal, never an alarm).
    #: Column holding the claimed incident/report date — an evidence photo whose
    #: EXIF capture time predates this date cannot show the incident.
    incident_date_field: Optional[str] = None
    #: Columns holding the claimed site/premise coordinates (decimal degrees).
    #: Declare BOTH or neither; an evidence photo's EXIF GPS is compared by
    #: haversine distance.
    location_lat_field: Optional[str] = None
    location_lon_field: Optional[str] = None
    #: GPS mismatch fires only BEYOND this distance (km). Default 10 — generous
    #: on purpose: a false "wrong location" flag costs officer trust. 0 is
    #: valid and means STRICT (flag any deviation); negatives are rejected
    #: at validation rather than silently falling back to the default.
    gps_radius_km: Optional[float] = Field(default=None, ge=0)
    #: Payment-proof verification (E4) — when this dataset's cases carry
    #: payment-proof documents (receipts / transfer screenshots), verify the
    #: extracted reference against the ledger DATASET named here. Cross-dataset
    #: by design: the case dataset declares WHICH ledger can verify its
    #: receipts. "Claimed reference not found in the ledger" is a fact, not a
    #: suspicion; a found-and-matching payment renders as VERIFICATION.
    payment_proof: Optional[PaymentProof] = None
    #: Generic cross-dataset verification blocks (plan F4). payment_proof is
    #: the first, named instance of the same shape; use verify_against for
    #: everything else (registry lookups, asset masters, estimates).
    verify_against: Optional[List[VerifyAgainst]] = None
    #: E6 — declarative date rules over the record's own columns (evaluated
    #: server-side against the record read by key, never agent-supplied).
    date_rules: Optional[List[DateRule]] = None


class DecisionHistory(BaseModel):
    """Marks a dataset as the decision record that feeds the learning loop."""
    model_config = ConfigDict(extra="forbid")

    outcome_field: Optional[str] = None
    good_values: Optional[List[str]] = None
    bad_values: Optional[List[str]] = None
    neutral_values: Optional[List[str]] = None
    outcome_hold_field: Optional[str] = None
    key_field: Optional[str] = None
    settling_window_days: Optional[int] = None
    decision_field: Optional[str] = None
    decided_at_field: Optional[str] = None
    decided_by_field: Optional[str] = None
    rationale_field: Optional[str] = None
    #: Distinguishes an authored history block from an inferred one.
    declared: Optional[bool] = None


class RegistryDataset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: Optional[str] = None
    physical_name: Optional[str] = None
    kind: Optional[DatasetKind] = None
    description: Optional[str] = None
    columns: List[RegistryColumn] = Field(default_factory=list)
    read_via: Optional[ReadVia] = None
    write_actions: List[RegistryWriteAction] = Field(default_factory=list)
    relationships: Optional[List[Dict[str, Any]]] = None
    #: Read-parameter contract (REST / parameterised reads).
    input_schema: Optional[Dict[str, Any]] = None
    #: Per-dataset override of the SOURCE's domain triple — a COMPLETE block
    #: (vertical + sub_vertical + country are required), never a partial merge.
    #: Rare: one MCP serving two lines of business.
    domain: Optional[Domain] = None
    fraud_screening: Optional[FraudScreening] = None
    decision_history: Optional[DecisionHistory] = None
    #: The MONEY definition (ROI spine) — what a realized/prevented amount is
    #: for decisions on this dataset. See docs/money-saved-roi-plan.md.
    value_semantics: Optional[ValueSemantics] = None
    #: Policy-mandated read — the required-lookup gate refuses the write without it.
    mandatory_when_used: Optional[bool] = None
    last_refreshed_at: Optional[str] = None
    samples_redacted: Optional[bool] = None
    row_count_approx: Optional[int] = None

    @model_validator(mode="after")
    def _fraud_needs_a_key_and_a_target(self) -> "RegistryDataset":
        """`applies: true` is NOT the only switch — it is one of three
        preconditions. fraud_roles.py skips screen creation (log line only) when
        the dataset has no primary key or no evidence/identity column, so an
        author who follows §10 exactly can publish and get NO screening at all.
        Say it here, at authoring time."""
        if not (self.fraud_screening and self.fraud_screening.applies is True):
            return self
        if not any(c.is_primary_key for c in self.columns):
            raise ValueError(
                f"dataset {self.id!r}: fraud_screening.applies=true but no column "
                f"declares is_primary_key. The screen is bound to the record key; "
                f"without one the autowire creates NO screen (a log warning only)."
            )
        if not any(c.artifact_role in (ArtifactRole.evidence, ArtifactRole.identity,
                                       ArtifactRole.payment_proof)
                   for c in self.columns):
            raise ValueError(
                f"dataset {self.id!r}: fraud_screening.applies=true but no column "
                f"declares artifact_role evidence|identity|payment_proof. There is "
                f"nothing to fingerprint, so the autowire creates NO screen."
            )
        return self

    @model_validator(mode="after")
    def _payment_proof_needs_a_tagged_document(self) -> "RegistryDataset":
        """`payment_proof` ledger verification is only safe when it is PINNED to
        a specific receipt column: a record can carry several documents (a
        payment receipt AND a purchase bill), and matching the wrong one against
        the payment ledger yields a false 'reference not found'. The autowire
        DROPS an unpinned payment_proof config (loudly) — say it here, at
        authoring time, where it can be fixed."""
        if not (self.fraud_screening and self.fraud_screening.payment_proof):
            return self
        if not any(c.artifact_role is ArtifactRole.payment_proof for c in self.columns):
            raise ValueError(
                f"dataset {self.id!r}: fraud_screening.payment_proof is declared "
                f"but no column has artifact_role='payment_proof'. Tag the column "
                f"that holds the submitted receipt/proof document — the ledger "
                f"check only reads references extracted from THAT document, so "
                f"other attached bills can never be matched by mistake."
            )
        return self

    @model_validator(mode="after")
    def _value_semantics_names_real_columns(self) -> "RegistryDataset":
        """The exposure column must exist — a ghost exposure_field would make
        every prevented_loss stamp silently zero. Realization columns live in
        ANOTHER dataset and are validated where the catalogue is in hand
        (consumption), same as payment_proof's ledger."""
        vs = self.value_semantics
        if not vs or not vs.exposure_field:
            return self
        cols = {c.name for c in self.columns}
        if cols and vs.exposure_field not in cols:
            raise ValueError(
                f"dataset {self.id!r}: value_semantics.exposure_field "
                f"{vs.exposure_field!r} is not a declared column.")
        return self

    @model_validator(mode="after")
    def _date_rules_name_real_columns(self) -> "RegistryDataset":
        """E6: a date rule naming a ghost column would silently never fire —
        say it at authoring time, where it can be fixed."""
        rules = (self.fraud_screening.date_rules
                 if self.fraud_screening else None) or []
        if not rules:
            return self
        names = [r.name for r in rules]
        if len(names) != len(set(names)):
            raise ValueError(
                f"dataset {self.id!r}: duplicate date_rules name(s) — each "
                f"rule needs a unique slug.")
        cols = {c.name for c in self.columns}
        if cols:  # columns may be auto-discovered (empty here) — then skip
            for r in rules:
                for f in (r.earlier_field, r.later_field):
                    if f not in cols:
                        raise ValueError(
                            f"dataset {self.id!r}: date_rules[{r.name!r}] names "
                            f"column {f!r} which is not declared on the dataset.")
        return self

    @model_validator(mode="after")
    def _verify_against_is_pinned_and_unique(self) -> "RegistryDataset":
        """Each verify_against block must pin to a REAL artifact column (same
        two-bills reasoning as payment_proof) and carry a unique name — a
        duplicate slug would make two checks' findings indistinguishable."""
        blocks = (self.fraud_screening.verify_against
                  if self.fraud_screening else None) or []
        if not blocks:
            return self
        names = [b.name for b in blocks]
        if len(names) != len(set(names)):
            raise ValueError(
                f"dataset {self.id!r}: duplicate verify_against name(s) — "
                f"each block needs a unique slug.")
        cols = {c.name: c for c in self.columns}
        for b in blocks:
            col = cols.get(b.doc_column)
            if col is None:
                raise ValueError(
                    f"dataset {self.id!r}: verify_against[{b.name!r}].doc_column "
                    f"{b.doc_column!r} names no declared column.")
            if col.artifact_role is None:
                raise ValueError(
                    f"dataset {self.id!r}: verify_against[{b.name!r}].doc_column "
                    f"{b.doc_column!r} has no artifact_role — the check must be "
                    f"pinned to a screened document column (declare a role, e.g. "
                    f"'evidence'), so it can never read a different attached bill."
                )
        return self

    @model_validator(mode="after")
    def _decision_history_fields_exist(self) -> "RegistryDataset":
        """An outcome_field naming a column that doesn't exist yields a learning
        loop that silently never learns."""
        dh = self.decision_history
        if not dh or not self.columns:
            return self
        known = {c.name for c in self.columns} | {
            c.physical_name for c in self.columns if c.physical_name
        }
        for field_name in ("outcome_field", "key_field", "outcome_hold_field",
                           "decision_field", "decided_at_field", "decided_by_field",
                           "rationale_field"):
            val = getattr(dh, field_name, None)
            if val and val not in known:
                raise ValueError(
                    f"dataset {self.id!r}: decision_history.{field_name}={val!r} "
                    f"names no declared column."
                )
        return self


class Organization(BaseModel):
    """The customer's DISPLAY identity (docs/runtime-ui-modernization-plan.md
    Track E) — declared once, per source, by IT at connection time; every app
    built on the source inherits it (app headers, browser titles, agent
    prompts, the Money-impact card). This is presentation identity ONLY —
    auth/tenancy (`org_id`) is untouched. When a tenant's sources declare
    conflicting blocks, consumption is first-wins (deterministic + visible)."""
    model_config = ConfigDict(extra="forbid")

    #: Full legal/display name — "Acme Power & Utilities Co."
    name: str = Field(min_length=1)
    #: Compact form for headers/nav ("Acme Power"). Defaults to ``name``.
    short_name: Optional[str] = None
    logo_url: Optional[str] = None
    #: Seed for the app theme's primary color (builder may override per app).
    brand_color: Optional[str] = Field(
        default=None, pattern=r"^#[0-9a-fA-F]{6}$")


class RegistrySource(BaseModel):
    """One source: one backend system, for one department."""
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    #: Lets an author point their editor at the schema for live validation.
    schema_ref: Optional[str] = Field(default=None, alias="$schema")

    source_id: str
    #: REQUIRED. Omitting it defaulted to `semantic`, which silently drops a
    #: structured source from /query — the single nastiest trap in the file.
    #: Required here so the mistake is impossible rather than merely documented.
    type: SourceType
    dept_id: str
    org_id: str
    name: str
    description: str

    is_active: bool = True
    tags: List[str] = Field(default_factory=list)
    visibility: Optional[Visibility] = None
    connection: Optional[Connection] = None
    options: Optional[Options] = None
    #: The deployment-targeting triple (vertical / sub_vertical / country) —
    #: every dataset inherits it unless the dataset declares its own block.
    domain: Optional[Domain] = None
    #: The customer's display identity (company name/logo/brand seed) — rides
    #: the catalogue onto every dataset entry; apps inherit it at publish.
    organization: Optional[Organization] = None
    datasets: List[RegistryDataset] = Field(default_factory=list)
    #: Source-level fallbacks — only used by the non-tabular single-dataset path.
    write_actions: List[RegistryWriteAction] = Field(default_factory=list)
    columns: List[RegistryColumn] = Field(default_factory=list)
    rag: Optional[Rag] = None
    taxonomy: Optional[SourceTaxonomy] = None
    query_timeout_seconds: Optional[int] = None
    #: Tells the builder this source can back a "Refresh from History" workflow.
    supports_history: Optional[bool] = None
    workflow_id: Optional[str] = None
    #: Accepted, copied by the loader, consumed by NOTHING. Reserved.
    structured_detection: Optional[Dict[str, Any]] = None
    #: Vestigial, from the retired central dept_sources registry.
    connection_ref: Optional[str] = None
    catalogue: Optional[Dict[str, Any]] = None
    #: Authoring / provenance metadata — carried by every real source, read by
    #: nothing. Declared so a real file validates without `extra` being loosened.
    #: Typed Any, not str: the live files carry Mongo extended JSON here
    #: ({"$date": "2026-07-10T…"}) from whatever exported them. Nothing reads
    #: these, so pinning a shape would reject real files to no purpose.
    is_demo: Optional[bool] = None
    created_at: Optional[Any] = None
    updated_at: Optional[Any] = None

    @model_validator(mode="after")
    def _backend_requirements(self) -> "RegistrySource":
        if self.type == SourceType.semantic:
            if not (self.rag and self.rag.milvus_collection):
                raise ValueError(
                    f"source {self.source_id!r}: type=semantic requires "
                    f"rag.milvus_collection (the platform reader needs a corpus)."
                )
            return self
        if self.type in (SourceType.structured, SourceType.mongodb):
            if not self.connection:
                raise ValueError(
                    f"source {self.source_id!r}: type={self.type.value} requires a "
                    f"`connection` block."
                )
        if self.type == SourceType.mongodb:
            if not (self.connection and self.connection.collection):
                raise ValueError(
                    f"source {self.source_id!r}: type=mongodb requires "
                    f"connection.collection. Without it EVERY read errors and "
                    f"introspection returns zero columns — the dataset looks "
                    f"schema-less rather than broken."
                )
        return self

    @model_validator(mode="after")
    def _dataset_ids_unique(self) -> "RegistrySource":
        seen, dupes = set(), []
        for ds in self.datasets:
            if ds.id in seen:
                dupes.append(ds.id)
            seen.add(ds.id)
        if dupes:
            raise ValueError(
                f"source {self.source_id!r}: duplicate dataset id(s) {sorted(set(dupes))} "
                f"— the registry is keyed by id, so one silently shadows the other."
            )
        return self


# ── The file itself ─────────────────────────────────────────────────────────

class SourcesEnvelope(BaseModel):
    """The ``{"sources": [ ... ]}`` form. Both this and a bare array are accepted
    by the loader (router.py), and every real tenant file uses this one."""
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_ref: Optional[str] = Field(default=None, alias="$schema")
    sources: List[RegistrySource]


class SourcesFile(RootModel[Union[SourcesEnvelope, List[RegistrySource]]]):
    """The whole sources.json: an envelope OR a bare array.

    Envelope FIRST in the union so a dict validates against it (and reports the
    envelope's own errors) rather than failing the array branch with a confusing
    "input should be a valid list".
    """

    def sources(self) -> List[RegistrySource]:
        root = self.root
        return root.sources if isinstance(root, SourcesEnvelope) else root


def format_error_path(loc: tuple) -> str:
    """A pydantic error `loc` tuple as a JSON-ish path an author can find.

    ("datasets", 0, "columns", 1, "artifact_role") -> datasets[0].columns[1].artifact_role

    Lives here, beside the models, because BOTH consumers of a ValidationError
    need it: validate_sources.py (the CLI) and router._validate_selected (the
    boot gate). It was written twice — and the two copies had already diverged on
    the empty-loc fallback. In a change whose whole thesis is "two definitions
    drift", that is not a detail to leave.
    """
    out = ""
    for part in loc:
        if isinstance(part, int):
            out += f"[{part}]"
        else:
            out += f".{part}" if out else str(part)
    return out
