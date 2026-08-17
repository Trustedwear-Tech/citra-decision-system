# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""The sources.json INPUT contract.

Ground truth is the 19 real sources across demo-data/tenants/* — if the model
rejects a working file, the MODEL is wrong. That is not hypothetical: building it
this way caught three things the model got wrong and reality got right
(primary_key is a list, created_at is Mongo extended JSON, taxonomy was already
modelled correctly in models.py and must be reused, not re-invented).

The other half of these tests is the inverse: mistakes that today fail SILENTLY
must now fail LOUDLY at authoring time.
"""
import glob
import json
import os

import pytest
from pydantic import ValidationError

from registry_models import (
    ArtifactRole, Connection, RegistryColumn, RegistryDataset,
    RegistrySource, SourceType,
)


def _minimal(**over):
    base = {
        "source_id": "s", "type": "structured", "dept_id": "d", "org_id": "o",
        "name": "n", "description": "desc", "connection": {"env_prefix": "X"},
    }
    base.update(over)
    return base


# ── ground truth: every real file must validate ─────────────────────────────

def _all_real_sources():
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    pattern = os.path.join(here, "..", "demo-data", "tenants", "*", "mcp", "sources.json")
    out = []
    for f in sorted(glob.glob(pattern)):
        tenant = os.path.basename(os.path.dirname(os.path.dirname(f)))
        with open(f, encoding="utf-8") as fh:
            doc = json.load(fh)
        for s in (doc.get("sources") if isinstance(doc, dict) else doc):
            out.append((f"{tenant}:{s.get('source_id')}", s))
    return out


_REAL = _all_real_sources()


@pytest.mark.skipif(not _REAL, reason="demo-data tenants not present")
@pytest.mark.parametrize("label,src", _REAL, ids=[lbl for lbl, _ in _REAL])
def test_every_real_source_validates(label, src):
    """A model that rejects a working source is a broken model, not a finding."""
    RegistrySource.model_validate(src)


# ── the traps: silent today, loud now ───────────────────────────────────────

def test_type_is_required():
    """Omitting `type` defaulted to `semantic`, silently dropping a structured
    source from /query — the nastiest trap in the file. Now impossible."""
    with pytest.raises(ValidationError):
        RegistrySource.model_validate({
            "source_id": "s", "dept_id": "d", "org_id": "o",
            "name": "n", "description": "d",
        })


def test_backend_types_the_code_supports_are_accepted():
    """bigquery/sap_rfc/duckdb are live source types (3 of 19). models.SourceType
    and the doc listed only 4 — validating against that would reject real files."""
    for t in ("bigquery", "sap_rfc", "duckdb", "structured", "mongodb", "rest_api"):
        assert SourceType(t)


def test_unknown_source_key_is_rejected():
    with pytest.raises(ValidationError):
        RegistrySource.model_validate(_minimal(artifact_rolez="identity"))


def test_unknown_column_key_is_rejected():
    """`artifact_roles` instead of `artifact_role` silently disabled fraud
    screening on that column. Now it's an error."""
    with pytest.raises(ValidationError) as e:
        RegistryColumn.model_validate({"name": "photo", "artifact_roles": "evidence"})
    assert "artifact_roles" in str(e.value)


def test_bad_enum_value_is_rejected():
    """'evidance' was only ever a log line nobody read."""
    with pytest.raises(ValidationError):
        RegistryColumn.model_validate({"name": "c", "artifact_role": "evidance"})


def test_provenance_metadata_is_accepted():
    """is_demo/created_at/updated_at ride every real source. Rejecting them would
    force `extra` open and defeat the point."""
    RegistrySource.model_validate(_minimal(
        is_demo=True, created_at={"$date": "2026-07-10T14:56:00Z"}, updated_at=None,
    ))


def test_connection_allows_backend_specific_keys():
    """SAP/BigQuery/duckdb wiring is polymorphic and fails loudly at connect
    time — forbidding unknowns here would block backends without preventing a
    silent failure."""
    Connection.model_validate({"env_prefix": "X", "sysnr": "00", "client": "100"})


def test_connection_primary_key_must_be_a_list():
    """catalogue does `set(conn.get("primary_key") or [])`; set() of a STRING
    explodes into characters and marks NO key, silently."""
    Connection.model_validate({"primary_key": ["kiln_id", "run_date"]})
    with pytest.raises(ValidationError):
        Connection.model_validate({"primary_key": "batch_id"})


# ── cross-field rules ───────────────────────────────────────────────────────

def test_mongodb_requires_collection():
    """Without it EVERY read errors and introspection returns zero columns, so
    the dataset looks schema-less rather than broken. The doc's own example
    omitted it."""
    with pytest.raises(ValidationError) as e:
        RegistrySource.model_validate(_minimal(
            type="mongodb", connection={"env_prefix": "M", "mongo_db": "acme"}))
    assert "collection" in str(e.value)


def test_semantic_requires_a_rag_collection():
    with pytest.raises(ValidationError):
        RegistrySource.model_validate({
            "source_id": "s", "type": "semantic", "dept_id": "d", "org_id": "o",
            "name": "n", "description": "d",
        })


def test_fraud_applies_true_without_a_primary_key_is_rejected():
    """`applies` is NOT the only switch — fraud_roles skips screen creation with
    only a log warning when there's no PK, so an author who follows §10 exactly
    ships an app with NO screening."""
    with pytest.raises(ValidationError) as e:
        RegistryDataset.model_validate({
            "id": "s.t", "fraud_screening": {"applies": True},
            "columns": [{"name": "photo", "artifact_role": "evidence",
                         "column_kind": "image_url"}],
        })
    assert "is_primary_key" in str(e.value)


def test_fraud_applies_true_without_a_fingerprint_target_is_rejected():
    with pytest.raises(ValidationError) as e:
        RegistryDataset.model_validate({
            "id": "s.t", "fraud_screening": {"applies": True},
            "columns": [{"name": "id", "is_primary_key": True}],
        })
    assert "artifact_role" in str(e.value)


def test_fraud_applies_true_with_both_is_accepted():
    RegistryDataset.model_validate({
        "id": "s.t", "fraud_screening": {"applies": True},
        "columns": [
            {"name": "id", "is_primary_key": True},
            {"name": "photo", "artifact_role": "evidence", "column_kind": "image_url"},
        ],
    })


def test_fraud_applies_none_is_hints_only_not_an_optout():
    """Tristate: None = hints-only, so the PK precondition doesn't apply."""
    RegistryDataset.model_validate({
        "id": "s.t", "fraud_screening": {"applies": None},
        "columns": [{"name": "c"}],
    })


def test_artifact_role_on_a_plain_column_is_rejected():
    with pytest.raises(ValidationError) as e:
        RegistryColumn.model_validate({
            "name": "notes", "artifact_role": "evidence", "column_kind": "plain"})
    assert "media" in str(e.value)


def test_artifact_role_without_a_column_kind_is_fine():
    """Absent kind = undeclared, NOT plain. Only 2 of 256 live columns declare
    one; treating absent as non-media would reject the fleet."""
    c = RegistryColumn.model_validate({"name": "photo", "artifact_role": "evidence"})
    assert c.artifact_role is ArtifactRole.evidence
    assert c.column_kind is None


def test_decision_history_field_must_name_a_real_column():
    """An outcome_field naming nothing yields a learning loop that never learns."""
    with pytest.raises(ValidationError) as e:
        RegistryDataset.model_validate({
            "id": "s.t",
            "columns": [{"name": "status"}],
            "decision_history": {"outcome_field": "statuss"},
        })
    assert "statuss" in str(e.value)


def test_duplicate_dataset_ids_are_rejected():
    """The registry is keyed by id — one silently shadows the other."""
    with pytest.raises(ValidationError) as e:
        RegistrySource.model_validate(_minimal(datasets=[
            {"id": "s.t"}, {"id": "s.t"},
        ]))
    assert "duplicate" in str(e.value).lower()


def test_schema_ref_key_is_accepted():
    """Authors point their editor at the schema with "$schema" for live
    validation; extra=forbid would otherwise reject the very line that helps."""
    s = RegistrySource.model_validate(_minimal(**{"$schema": "./sources.schema.json"}))
    assert s.schema_ref == "./sources.schema.json"


# ── Domain (vertical / sub_vertical / country) ──────────────────────────────

def test_domain_derives_currency_and_date_order_from_country():
    """The served catalogue value must always be COMPLETE — derivation happens
    at validation, never silently at read time."""
    from registry_models import Domain
    d = Domain.model_validate({"vertical": "banking",
                               "sub_vertical": "loan_recovery", "country": "IN"})
    assert (d.currency, d.date_order.value, d.locale) == ("INR", "DMY", "in")
    us = Domain.model_validate({"vertical": "insurance",
                                "sub_vertical": "claims", "country": "US"})
    assert (us.currency, us.date_order.value, us.locale) == ("USD", "MDY", "us")
    # An explicit contradicting value is a deliberate override — allowed.
    odd = Domain.model_validate({"vertical": "insurance", "sub_vertical": "claims",
                                 "country": "US", "currency": "CAD"})
    assert odd.currency == "CAD"


def test_domain_sub_vertical_mismatch_is_now_an_advisory():
    """Was a hard reject. Since the domain went global the pairing can only be
    checked for verticals that HAVE a pack, and an industry we do not know is
    legitimate — so a mismatch warns loudly instead of blocking a deployment."""
    from registry_models import Domain
    d = Domain.model_validate({"vertical": "insurance",
                               "sub_vertical": "loan_recovery", "country": "US"})
    assert "not a known line of business" in d.pack_advisory
    assert d.currency == "USD"  # vertical defaults still apply


def test_domain_is_global_but_still_typo_safe():
    """Citra is horizontal: ANY industry, ANY country. The old 4-vertical /
    IN|US enums described the markets we had templates for, not the markets
    the product serves — a defence or healthcare deployment could not name
    itself, and an in-scope German bank had to drop its whole domain block.

    The reason those enums existed still holds ("an open string is how a typo
    becomes a silent no-op") so it is preserved by a DIFFERENT mechanism: an
    unknown industry is accepted but carries a loud advisory, and countries
    stay validated against the full ISO-3166 list."""
    from registry_models import Domain, MULTINATIONAL

    # Any industry — no template needed.
    d = Domain.model_validate({"vertical": "defence", "sub_vertical": "procurement",
                               "country": "IN"})
    assert d.vertical == "defence" and d.currency == "INR"
    assert "no built-in behavior pack" in d.pack_advisory

    # Any country — the case that was previously IMPOSSIBLE.
    de = Domain.model_validate({"vertical": "banking", "sub_vertical": "loan_recovery",
                                "country": "DE"})
    assert de.country == "DE" and de.currency == "EUR" and de.pack_advisory is None

    # A typo is LOUD, not silent, and suggests the fix.
    typo = Domain.model_validate({"vertical": "bankng", "country": "IN"})
    assert "did you mean 'banking'?" in typo.pack_advisory

    # Still rejected: a non-ISO country, a bad slug, an unknown key.
    for bad in (
        {"vertical": "banking", "country": "IND"},
        {"vertical": "Loan Recovery", "country": "IN"},
        {"vertical": "banking", "country": "IN", "verticle": "oops"},
    ):
        with pytest.raises(ValidationError):
            Domain.model_validate(bad)


def test_domain_defaults_multinational_usd_english():
    """No country stated = a multinational deployment reporting in USD.
    India means rupees. The platform is English everywhere."""
    from registry_models import Domain, MULTINATIONAL
    m = Domain.model_validate({"vertical": "logistics"})
    assert m.country == MULTINATIONAL and m.currency == "USD"
    assert m.language == "en" and m.locale == ""
    ind = Domain.model_validate({"vertical": "banking", "country": "IN"})
    assert ind.currency == "INR" and ind.language == "en" and ind.locale == "in"
    # "UK" is not ISO (GB is) — corrected rather than rejected.
    uk = Domain.model_validate({"vertical": "banking", "country": "UK"})
    assert uk.country == "GB" and uk.currency == "GBP"


def test_domain_on_source_and_dataset_override():
    s = RegistrySource.model_validate(_minimal(
        domain={"vertical": "utility", "sub_vertical": "power_recovery",
                "country": "IN"},
        datasets=[{"id": "s.t",
                   "domain": {"vertical": "banking",
                              "sub_vertical": "loan_recovery", "country": "US"}}],
    ))
    assert s.domain.country == "IN"
    assert s.datasets[0].domain.country == "US"


def test_payment_proof_requires_a_tagged_receipt_column():
    """E4 pinning (F1): declaring payment_proof without an
    artifact_role='payment_proof' column would let the ledger check run against
    whichever bill happens to be attached — rejected at authoring time."""
    ds = {
        "id": "s.t",
        "columns": [{"name": "id", "is_primary_key": True},
                    {"name": "doc_url", "artifact_role": "evidence"}],
        "fraud_screening": {"applies": True,
                            "payment_proof": {"ledger_dataset": "b.p",
                                              "match_field": "ref"}},
    }
    with pytest.raises(ValidationError) as e:
        RegistryDataset.model_validate(ds)
    assert "payment_proof" in str(e.value)
    ds["columns"].append({"name": "receipt_url", "artifact_role": "payment_proof"})
    RegistryDataset.model_validate(ds)  # pinned → accepted


def test_verify_against_must_pin_and_be_unique():
    """F4: a verify_against block must pin to a role-tagged artifact column
    (the two-bills rule) and carry a unique slug."""
    base = {
        "id": "s.t",
        "columns": [{"name": "id", "is_primary_key": True},
                    {"name": "bill_url", "artifact_role": "evidence"}],
        "fraud_screening": {"applies": True, "verify_against": [
            {"name": "purchase_vs_registry", "target_dataset": "r.deeds",
             "match_field": "deed_no", "doc_column": "bill_url"}]},
    }
    RegistryDataset.model_validate(base)  # pinned → accepted
    # doc_column naming no column → rejected.
    bad = {**base, "fraud_screening": {"applies": True, "verify_against": [
        {"name": "v", "target_dataset": "r.d", "match_field": "k",
         "doc_column": "ghost_col"}]}}
    with pytest.raises(ValidationError):
        RegistryDataset.model_validate(bad)
    # doc_column with NO artifact_role → rejected (unpinnable).
    bad2 = {**base,
            "columns": [{"name": "id", "is_primary_key": True},
                        {"name": "bill_url"},
                        {"name": "photo", "artifact_role": "evidence"}]}
    with pytest.raises(ValidationError):
        RegistryDataset.model_validate(bad2)
    # duplicate slugs → rejected.
    dup = {**base, "fraud_screening": {"applies": True, "verify_against": [
        {"name": "v", "target_dataset": "r.d", "match_field": "k",
         "doc_column": "bill_url"},
        {"name": "v", "target_dataset": "r.e", "match_field": "k",
         "doc_column": "bill_url"}]}}
    with pytest.raises(ValidationError):
        RegistryDataset.model_validate(dup)


def test_date_rules_validate_columns_and_uniqueness():
    """E6: a date rule naming a ghost column or reusing a slug dies at
    authoring time."""
    base = {
        "id": "s.t",
        "columns": [{"name": "id", "is_primary_key": True},
                    {"name": "work_order_date"}, {"name": "inspection_date"}],
        "fraud_screening": {"date_rules": [
            {"name": "order", "earlier_field": "work_order_date",
             "later_field": "inspection_date"}]},
    }
    RegistryDataset.model_validate(base)
    with pytest.raises(ValidationError):
        RegistryDataset.model_validate({**base, "fraud_screening": {"date_rules": [
            {"name": "x", "earlier_field": "ghost", "later_field": "inspection_date"}]}})
    with pytest.raises(ValidationError):
        RegistryDataset.model_validate({**base, "fraud_screening": {"date_rules": [
            {"name": "dup", "earlier_field": "work_order_date", "later_field": "inspection_date"},
            {"name": "dup", "earlier_field": "inspection_date", "later_field": "work_order_date"}]}})


def test_value_semantics_kind_requirements():
    """The MONEY definition (ROI spine) enforces its own completeness at
    authoring: recovered needs a realization ledger, prevented_loss needs the
    frozen-exposure column + the prevention outcomes."""
    from registry_models import ValueSemantics

    ok = ValueSemantics.model_validate({
        "value_kind": "recovered",
        "realization": {"dataset": "b.payments", "match_field": "acct",
                        "amount_field": "amount", "date_field": "paid_on"}})
    assert ok.realization.window_days == 90
    assert ok.attribution.value == "approved_recommendation"
    for bad in (
        {"value_kind": "recovered"},
        {"value_kind": "prevented_loss"},
        {"value_kind": "prevented_loss", "exposure_field": "amt"},
        {"value_kind": "recovered",
         "realization": {"dataset": "b.p", "match_field": "x",
                         "amount_field": "a"}, "typo": 1},
    ):
        with pytest.raises(ValidationError):
            ValueSemantics.model_validate(bad)


def test_value_semantics_exposure_must_be_a_declared_column():
    ds = {
        "id": "s.t",
        "columns": [{"name": "id", "is_primary_key": True},
                    {"name": "claim_amount"}],
        "value_semantics": {"value_kind": "prevented_loss",
                            "exposure_field": "claim_amount",
                            "prevented_when": ["rejected"]},
    }
    RegistryDataset.model_validate(ds)
    ds["value_semantics"]["exposure_field"] = "ghost_amount"
    with pytest.raises(ValidationError) as e:
        RegistryDataset.model_validate(ds)
    assert "ghost_amount" in str(e.value)


def test_organization_block_validation():
    """`organization` = display identity: name required, hex brand color,
    unknown fields rejected (extra=forbid) — never a loosened string bag."""
    from registry_models import Organization

    ok = Organization.model_validate({
        "name": "Acme Power & Utilities Co.", "short_name": "Acme Power",
        "brand_color": "#0f6b3f"})
    assert ok.short_name == "Acme Power"
    for bad in (
        {},                                        # name required
        {"name": ""},                              # empty name
        {"name": "X", "brand_color": "green"},     # not #rrggbb
        {"name": "X", "org_id": "acme"},           # auth identity does NOT belong here
    ):
        with pytest.raises(ValidationError):
            Organization.model_validate(bad)


def test_flatten_whitelist_carries_organization():
    """router._flatten whitelists top-level source keys — the third occurrence
    of the 'declarable, validated, never served' trap (supports_history, then
    domain, now organization). This pins the carry so the catalogue can stamp
    the display identity onto every dataset entry."""
    from router import _flatten

    flat = _flatten({
        "source_id": "s1", "type": "structured", "dept_id": "d", "org_id": "o",
        "name": "n", "description": "x",
        "connection": {"type": "postgres", "env_prefix": "P"},
        "organization": {"name": "Acme Power & Utilities Co.",
                         "short_name": "Acme Power"},
        "domain": {"vertical": "utility", "sub_vertical": "power_recovery",
                   "country": "US"},
    })
    assert flat["organization"]["short_name"] == "Acme Power"
    assert flat["domain"]["country"] == "US"
