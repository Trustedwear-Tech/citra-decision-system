"""Integration: the fraud screen is auto-wired through the REAL publish path.

The earlier unit tests fed hand-built dict catalogues straight into
``autowire_fraud_roles``, which hid a whole class of wiring bugs (the ontology
fields being dropped upstream, the validator never collecting the dataset ref,
etc.). This test drives a REAL ``AgentSpec`` through ``validate_data_bindings``
— the actual function the publisher calls — with only ``fetch_catalogue_entry``
stubbed to return a catalogue-shaped entry (exactly what the discovery service
serialises). If any hop in validator → autowire regresses, this fails.
"""
import asyncio
from unittest.mock import MagicMock

import data_binding_validator as dbv
from models import AgentSpec, DataSource

# A catalogue entry as the discovery service returns it (CatalogueEntry.model_dump()):
# the ontology fields ride the columns + a dataset-level fraud_screening block.
_ENTRY = {
    "dataset_id": "field_operations.equipment_inspections",
    "source_id": "field_operations",
    "name": "equipment_inspections",
    "kind": "sql",
    "columns": [
        {"name": "equipment_id", "type": "string", "is_primary_key": True},
        {"name": "assessed_amount", "type": "number"},
        {"name": "defect_photo_url", "column_kind": "image_url",
         "artifact_role": "evidence", "reuse_policy": "suspicious"},
        {"name": "inspection_report_url", "column_kind": "document_url",
         "artifact_role": "evidence", "reuse_policy": "suspicious"},
        {"name": "headshot_url", "column_kind": "image_url", "artifact_role": "identity"},
        {"name": "brochure_url", "column_kind": "document_url", "artifact_role": "supporting"},
    ],
    "fraud_screening": {"applies": True, "value_fields": ["assessed_amount"],
                        "identity_fields": ["equipment_id"]},
}


# The data_sources live on the AppSpec, not the AgentSpec — mirror the real
# publish call, which passes app_spec.data_sources through explicitly.
_DATA_SOURCES = [DataSource(id="insp", type="mcp",
                            ref="field_operations.equipment_inspections")]


def _spec_no_screen() -> AgentSpec:
    # A fraud app that carries NO consistency_check tool and NO per-action
    # data_bindings — the case the validator must still wire from the ontology.
    return AgentSpec(
        agent_id="inspector_agent",
        name="Equipment Inspector",
        system_prompt="Screen equipment inspection records for reused evidence.",
        tools_v2=[],
    )


def _run(spec: AgentSpec, entry=None):
    _e = entry if entry is not None else _ENTRY

    async def _fake_fetch(*, dataset_id, source_id, **_kw):
        return _e if dataset_id == _ENTRY["dataset_id"] else None

    import unittest.mock as _m
    with _m.patch.object(dbv, "fetch_catalogue_entry", _fake_fetch):
        return asyncio.run(dbv.validate_data_bindings(
            spec, settings=MagicMock(), auth_header=None, tenant_id="acme",
            data_sources=_DATA_SOURCES))


def _screens(spec: AgentSpec):
    return [t for t in spec.tools_v2 if getattr(t, "kind", None) == "consistency_check"]


def test_validator_auto_creates_screen_from_ontology_end_to_end():
    spec = _spec_no_screen()
    errors, warnings = _run(spec)
    screens = _screens(spec)
    assert len(screens) == 1, "ontology opted in (applies=true) → a screen must be wired"
    t = screens[0]
    assert t.data_source_id == "insp"
    assert t.key_field == "equipment_id"                      # dataset primary key
    # Evidence + identity are captured; supporting + role-less are NOT.
    assert t.url_columns == ["defect_photo_url", "headshot_url", "inspection_report_url"]
    assert t.url_column_roles["defect_photo_url"]["artifact_role"] == "evidence"
    assert t.url_column_roles["headshot_url"]["artifact_role"] == "identity"
    assert "brochure_url" not in t.url_column_roles


def test_validator_opts_out_when_ontology_says_applies_false():
    spec = _spec_no_screen()
    _run(spec, entry={**_ENTRY, "fraud_screening": {"applies": False}})
    assert _screens(spec) == [], "applies=false is a hard opt-out — no screen wired"


def test_autowire_failure_surfaces_as_warning_not_swallowed():
    # Fail-loud: if auto-wiring raises, the publisher must be TOLD (a screen may be
    # missing on an opted-in dataset) — not have it silently logged and dropped.
    import fraud_roles
    import unittest.mock as _m

    async def _fake_fetch(*, dataset_id, source_id, **_kw):
        return _ENTRY if dataset_id == _ENTRY["dataset_id"] else None

    def _boom(*a, **k):
        raise RuntimeError("simulated autowire bug")

    spec = _spec_no_screen()
    with _m.patch.object(dbv, "fetch_catalogue_entry", _fake_fetch), \
         _m.patch.object(fraud_roles, "autowire_fraud_roles", _boom):
        errors, warnings = asyncio.run(dbv.validate_data_bindings(
            spec, settings=MagicMock(), auth_header=None, tenant_id="acme",
            data_sources=_DATA_SOURCES))
    assert not errors                                          # doesn't block publish
    codes = [w.get("code") for w in warnings]
    assert "fraud_autowire_failed" in codes                   # but IS surfaced
    detail = next(w["detail"] for w in warnings if w["code"] == "fraud_autowire_failed")
    assert "simulated autowire bug" in detail


# ── Ontology-review O6b: tool-bound datasets reach the required-lookup map ──
# A builder may pin a keyed-read dataset ONLY on the tools_v2 mcp tool (no
# AppSpec data_source, no action binding). The validator previously never
# fetched that dataset, so required_lookup_autowire silently skipped its
# mandatory_when_used mandate — the CIBIL-style gate simply didn't arm.

def test_tool_bound_mandatory_dataset_arms_required_lookup():
    from models import McpTool

    bureau_entry = {
        "dataset_id": "bureau.credit_reports",
        "source_id": "bureau",
        "name": "credit_reports",
        "kind": "rest",
        "columns": [{"name": "pan", "type": "string"}],
        "mandatory_when_used": True,
    }
    spec = AgentSpec(
        agent_id="loan_agent", name="Loan Officer",
        system_prompt="Check the bureau before any disbursal write.",
        tools_v2=[McpTool(name="read_credit", source_id="bureau",
                          tool_name="query", dataset_id="bureau.credit_reports",
                          dataset_kind="rest")],
    )

    async def _fake_fetch(*, dataset_id, source_id, **_kw):
        return bureau_entry if dataset_id == "bureau.credit_reports" else None

    import unittest.mock as _m
    with _m.patch.object(dbv, "fetch_catalogue_entry", _fake_fetch):
        asyncio.run(dbv.validate_data_bindings(
            spec, settings=MagicMock(), auth_header=None, tenant_id="acme",
            data_sources=[]))          # NO data_source, NO binding — tool-only

    tool = spec.tools_v2[0]
    assert getattr(tool, "required", None) is True, (
        "mandatory_when_used dataset bound only on the tools_v2 mcp tool must "
        "still arm the required-lookup gate"
    )
