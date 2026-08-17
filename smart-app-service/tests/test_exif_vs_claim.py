# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""EXIF↔claim comparator (E1) — deterministic photo-metadata vs claimed context.

Pins the design rules that make this check ship-able at all:
  * fires ONLY when both sides carry a value — absent EXIF is a NON-signal
    (messengers strip it), an absent record value is a NON-signal;
  * ontology-driven end-to-end — no sources.json declaration ⇒ no check;
  * generous tolerances (1-day clock slack, 10 km default GPS gate);
  * every skip is VISIBLE (notes), never silent.
"""
import fraud_checks as fc
import fraud_roles as fr
from fraud_synthesis import severity_points


def _finding(col="damage_photo_url", **meta):
    return {"column": col, "sha256": "x", "metadata": meta}


# ── parse_exif_datetime / haversine ──────────────────────────────────────────
def test_parse_exif_datetime_formats():
    assert fc.parse_exif_datetime("2026:06:14 10:30:00").date().isoformat() == "2026-06-14"
    assert fc.parse_exif_datetime("2026:06:14").date().isoformat() == "2026-06-14"
    assert fc.parse_exif_datetime("2026-06-14T10:30:00").date().isoformat() == "2026-06-14"
    assert fc.parse_exif_datetime(None) is None
    assert fc.parse_exif_datetime("not a date") is None


def test_haversine_sanity():
    # Patna ↔ Delhi ≈ 850–1000 km; same point = 0.
    d = fc.haversine_km(25.594, 85.137, 28.613, 77.209)
    assert 800 < d < 1100
    assert fc.haversine_km(25.5, 85.1, 25.5, 85.1) == 0.0


def test_gps_dms_to_decimal_signs():
    assert abs(fc._gps_dms_to_decimal((25, 35, 38.4), "N") - 25.594) < 0.01
    assert fc._gps_dms_to_decimal((25, 35, 38.4), "S") < 0


# ── capture-before-claim ─────────────────────────────────────────────────────
def test_photo_before_incident_fires():
    sigs, notes = fc.exif_vs_claim(
        [_finding(capture_time="2026:06:01 09:00:00")],
        claimed_incident_date="2026-06-14",
    )
    assert notes == []
    assert len(sigs) == 1
    s = sigs[0]
    assert s["signal"] == fc.SIGNAL_CAPTURE_BEFORE
    assert s["days_before"] == 13
    assert "BEFORE" in s["why"]


def test_clock_slack_tolerance_does_not_fire():
    # 1 day early = camera clock / timezone slack, NOT a signal.
    sigs, _ = fc.exif_vs_claim(
        [_finding(capture_time="2026:06:13 23:00:00")],
        claimed_incident_date="2026-06-14",
    )
    assert sigs == []


def test_photo_on_or_after_incident_never_fires():
    for t in ("2026:06:14 08:00:00", "2026:07:01 08:00:00"):
        sigs, _ = fc.exif_vs_claim(
            [_finding(capture_time=t)], claimed_incident_date="2026-06-14")
        assert sigs == []


def test_absent_exif_is_a_non_signal():
    # The WhatsApp rule: stripped metadata must never read as fraud.
    sigs, notes = fc.exif_vs_claim(
        [_finding()], claimed_incident_date="2026-06-14",
        claimed_lat=25.5, claimed_lon=85.1,
    )
    assert sigs == [] and notes == []


def test_absent_claim_is_a_non_signal():
    sigs, notes = fc.exif_vs_claim(
        [_finding(capture_time="2020:01:01 00:00:00",
                  gps={"lat": 12.9, "lon": 77.6})],
    )
    assert sigs == [] and notes == []


def test_unparseable_claim_date_is_a_visible_skip():
    sigs, notes = fc.exif_vs_claim(
        [_finding(capture_time="2020:01:01 00:00:00")],
        claimed_incident_date="garbage",
    )
    assert sigs == []
    assert any("unparseable" in n for n in notes)


# ── GPS vs claimed site ──────────────────────────────────────────────────────
def test_gps_far_fires_beyond_radius():
    # Bengaluru photo on a Patna-premise case: ~1700 km.
    sigs, _ = fc.exif_vs_claim(
        [_finding(gps={"lat": 12.97, "lon": 77.59})],
        claimed_lat="25.594", claimed_lon="85.137",
    )
    assert len(sigs) == 1
    s = sigs[0]
    assert s["signal"] == fc.SIGNAL_GPS_FAR
    assert s["distance_km"] > 1000
    assert s["radius_km"] == 10.0


def test_gps_within_radius_does_not_fire():
    # ~1 km away — same neighbourhood, no signal at the default 10 km gate.
    sigs, _ = fc.exif_vs_claim(
        [_finding(gps={"lat": 25.60, "lon": 85.14})],
        claimed_lat=25.594, claimed_lon=85.137,
    )
    assert sigs == []


def test_gps_radius_override():
    # 1 km apart fires when the ontology tightens the gate to 0.5 km.
    sigs, _ = fc.exif_vs_claim(
        [_finding(gps={"lat": 25.603, "lon": 85.137})],
        claimed_lat=25.594, claimed_lon=85.137, radius_km=0.5,
    )
    assert len(sigs) == 1 and sigs[0]["signal"] == fc.SIGNAL_GPS_FAR


def test_gps_radius_zero_is_honored_as_strict():
    # Review fix: `or default` used to swallow a declared 0 into the generous
    # 10 km gate — the OPPOSITE of the strict intent. 0 must mean "flag any
    # GPS deviation"; only absent/negative values fall back to the default.
    sigs, _ = fc.exif_vs_claim(
        [_finding(gps={"lat": 25.60, "lon": 85.14})],   # ~1 km away
        claimed_lat=25.594, claimed_lon=85.137, radius_km=0,
    )
    assert len(sigs) == 1 and sigs[0]["radius_km"] == 0.0
    sigs2, _ = fc.exif_vs_claim(
        [_finding(gps={"lat": 25.60, "lon": 85.14})],
        claimed_lat=25.594, claimed_lon=85.137, radius_km=-5,  # invalid → default
    )
    assert sigs2 == []  # 1 km < 10 km default


def test_claim_context_model_rejects_typoed_keys():
    # Review fix: claim_context is now a typed extra='forbid' submodel so a
    # mistyped key fails AT PUBLISH instead of silently no-opping at runtime.
    import pytest as _pytest
    from models import ClaimContext

    ClaimContext(incident_date_field="reported_date", dataset_kind="sql")
    with _pytest.raises(Exception):
        ClaimContext(incident_date_col="reported_date")  # typo'd key
    with _pytest.raises(Exception):
        ClaimContext(gps_radius_km=-1)  # negative radius


def test_unparseable_coordinates_are_a_visible_skip():
    sigs, notes = fc.exif_vs_claim(
        [_finding(gps={"lat": 12.9, "lon": 77.6})],
        claimed_lat="Patna", claimed_lon="Bihar",
    )
    assert sigs == []
    assert any("unparseable" in n for n in notes)


# ── role awareness ───────────────────────────────────────────────────────────
def test_identity_and_supporting_artifacts_are_skipped():
    # An old headshot's capture date has NO relation to the incident.
    findings = [
        _finding(col="headshot_url", capture_time="2020:01:01 00:00:00"),
        _finding(col="brochure_url", capture_time="2020:01:01 00:00:00"),
        _finding(col="damage_url", capture_time="2020:01:01 00:00:00"),
    ]
    sigs, _ = fc.exif_vs_claim(
        findings, claimed_incident_date="2026-06-14",
        roles={"headshot_url": "identity", "brochure_url": "supporting"},
    )
    assert [s["column"] for s in sigs] == ["damage_url"]  # undeclared ⇒ evidence


def test_errored_findings_are_skipped():
    sigs, _ = fc.exif_vs_claim(
        [{"column": "damage_url", "error": "fetch failed"}],
        claimed_incident_date="2026-06-14",
    )
    assert sigs == []


# ── camera flip ──────────────────────────────────────────────────────────────
def test_camera_flip_fires_on_two_models():
    sigs, _ = fc.exif_vs_claim([
        _finding(col="photo_1", camera="Samsung SM-A515F"),
        _finding(col="photo_2", camera="Xiaomi M2101K6G"),
    ])
    assert len(sigs) == 1
    assert sigs[0]["signal"] == fc.SIGNAL_CAMERA_FLIP
    assert "corroborating" in sigs[0]["why"].lower()


def test_single_camera_never_flips():
    sigs, _ = fc.exif_vs_claim([
        _finding(col="photo_1", camera="Samsung SM-A515F"),
        _finding(col="photo_2", camera="Samsung SM-A515F"),
        _finding(col="photo_3"),  # no camera EXIF — not a second model
    ])
    assert sigs == []


# ── T3 gate scoring ──────────────────────────────────────────────────────────
def test_gate_scores_exif_signals():
    sigs, _ = fc.exif_vs_claim(
        [_finding(capture_time="2026:06:01 09:00:00",
                  gps={"lat": 12.97, "lon": 77.59})],
        claimed_incident_date="2026-06-14",
        claimed_lat=25.594, claimed_lon=85.137,
    )
    total, counts = severity_points({"exif_findings": sigs})
    assert counts["exif_capture_before_claim"] == 1
    assert counts["exif_gps_far_from_claim"] == 1
    assert total == 6  # 3 + 3 — each weighted like an exact duplicate


# ── ontology → claim_context (the gate that makes this ontology-driven) ──────
_COLS = {"complaint_id", "reported_date", "premise_lat", "premise_lon", "photo_url"}


def test_claim_context_none_when_nothing_declared():
    assert fr.claim_context_from_screening({"applies": True}, "sql", _COLS) is None
    assert fr.claim_context_from_screening(None, "sql", _COLS) is None


def test_claim_context_full_block():
    ctx = fr.claim_context_from_screening(
        {"applies": True, "incident_date_field": "reported_date",
         "location_lat_field": "premise_lat", "location_lon_field": "premise_lon",
         "gps_radius_km": 5},
        "sql", _COLS,
    )
    assert ctx == {"dataset_kind": "sql", "incident_date_field": "reported_date",
                   "location_lat_field": "premise_lat",
                   "location_lon_field": "premise_lon", "gps_radius_km": 5}


def test_claim_context_half_declared_gps_is_dropped_loudly(caplog):
    ctx = fr.claim_context_from_screening(
        {"location_lat_field": "premise_lat"}, "sql", _COLS)
    assert ctx is None
    assert any("both" in r.getMessage() for r in caplog.records)


def test_claim_context_unknown_column_is_dropped_loudly(caplog):
    ctx = fr.claim_context_from_screening(
        {"incident_date_field": "no_such_col",
         "location_lat_field": "premise_lat", "location_lon_field": "premise_lon"},
        "sql", _COLS,
    )
    assert ctx is not None and "incident_date_field" not in ctx
    assert ctx["location_lat_field"] == "premise_lat"
    assert any("does not exist" in r.getMessage() for r in caplog.records)


def test_autowire_stamps_claim_context_onto_screen():
    spec_tool = {"kind": "consistency_check", "data_source_id": "insp",
                 "url_columns": [], "url_column_roles": {}}

    class _Spec:
        tools_v2 = [spec_tool]
        data_sources = [{"id": "insp", "type": "mcp",
                         "ref": "field_operations.equipment_inspections"}]

    cat = {("field_operations", "field_operations.equipment_inspections"): {
        "kind": "sql",
        "fraud_screening": {
            "applies": True, "incident_date_field": "reported_date",
            "location_lat_field": "premise_lat",
            "location_lon_field": "premise_lon",
        },
        "columns": [
            {"name": "equipment_id", "is_primary_key": True},
            {"name": "reported_date"}, {"name": "premise_lat"},
            {"name": "premise_lon"},
            {"name": "defect_photo_url", "artifact_role": "evidence"},
        ],
    }}
    n = fr.autowire_fraud_roles(_Spec(), cat, data_sources=_Spec.data_sources)
    assert n >= 1
    ctx = spec_tool["claim_context"]
    assert ctx["incident_date_field"] == "reported_date"
    assert ctx["dataset_kind"] == "sql"


def test_autocreated_screen_carries_identity_fields_first_publish():
    """Regression: _make_consistency_tool must stamp identity_fields at CREATE
    time — the reconcile loop only reaches pre-existing tools, so a first
    publish would otherwise ship a screen without the ontology's linkable keys
    until a republish."""
    class _Spec:
        tools_v2 = []
        data_sources = [{"id": "insp", "type": "mcp",
                         "ref": "field_operations.equipment_inspections"}]

    cat = {("field_operations", "field_operations.equipment_inspections"): {
        "kind": "sql",
        "fraud_screening": {"applies": True,
                            "identity_fields": ["consumer_id", "je_id"]},
        "columns": [
            {"name": "equipment_id", "is_primary_key": True},
            {"name": "defect_photo_url", "artifact_role": "evidence"},
        ],
    }}
    fr.autowire_fraud_roles(_Spec(), cat, data_sources=_Spec.data_sources)
    screens = [t for t in _Spec.tools_v2
               if (t.get("kind") if isinstance(t, dict) else t.kind) == "consistency_check"]
    assert len(screens) == 1
    t = screens[0]
    idents = t.get("identity_fields") if isinstance(t, dict) else t.identity_fields
    assert sorted(idents) == ["consumer_id", "je_id"]


def test_autowire_appends_fraud_synthesis_when_screening_on():
    """Regression (prod 2026-07-19): a screened app without fraud_synthesis
    caught fraud but persisted NO screening row — the admin panel showed
    zeros. Screening ON must auto-wire the synthesis tool; the wiring must be
    idempotent; and a hand-authored tool (tuned gate) is never replaced."""
    cat = {("field_operations", "field_operations.equipment_inspections"): {
        "kind": "sql",
        "fraud_screening": {"applies": True},
        "columns": [
            {"name": "equipment_id", "is_primary_key": True},
            {"name": "defect_photo_url", "artifact_role": "evidence"},
        ],
    }}
    data_sources = [{"id": "insp", "type": "mcp",
                     "ref": "field_operations.equipment_inspections"}]

    class _Spec:
        tools_v2 = []

    fr.autowire_fraud_roles(_Spec(), cat, data_sources=data_sources)
    kinds = [t.get("kind") if isinstance(t, dict) else t.kind for t in _Spec.tools_v2]
    assert kinds.count("consistency_check") == 1
    assert kinds.count("fraud_synthesis") == 1

    # Idempotent: a second pass adds nothing.
    n2 = fr.autowire_fraud_roles(_Spec(), cat, data_sources=data_sources)
    kinds2 = [t.get("kind") if isinstance(t, dict) else t.kind for t in _Spec.tools_v2]
    assert n2 == 0 and kinds2.count("fraud_synthesis") == 1


def test_autowire_never_replaces_hand_authored_synthesis():
    hand = {"kind": "fraud_synthesis", "name": "fraud_synthesis",
            "gate_min_points": 5, "sample_rate": 0.2}  # builder-tuned
    cat = {("field_operations", "field_operations.equipment_inspections"): {
        "kind": "sql",
        "fraud_screening": {"applies": True},
        "columns": [
            {"name": "equipment_id", "is_primary_key": True},
            {"name": "defect_photo_url", "artifact_role": "evidence"},
        ],
    }}
    data_sources = [{"id": "insp", "type": "mcp",
                     "ref": "field_operations.equipment_inspections"}]

    class _Spec:
        tools_v2 = [hand]

    fr.autowire_fraud_roles(_Spec(), cat, data_sources=data_sources)
    synths = [t for t in _Spec.tools_v2
              if (t.get("kind") if isinstance(t, dict) else t.kind) == "fraud_synthesis"]
    assert len(synths) == 1 and synths[0] is hand
    assert hand["gate_min_points"] == 5  # tuning untouched


def test_autowire_no_synthesis_when_screening_inactive():
    cat = {("field_operations", "field_operations.equipment_inspections"): {
        "kind": "sql",
        "fraud_screening": {"applies": False},   # hard opt-out
        "columns": [{"name": "equipment_id", "is_primary_key": True}],
    }}
    data_sources = [{"id": "insp", "type": "mcp",
                     "ref": "field_operations.equipment_inspections"}]

    class _Spec:
        tools_v2 = []

    fr.autowire_fraud_roles(_Spec(), cat, data_sources=data_sources)
    assert _Spec.tools_v2 == []


def test_autowire_explicit_optout_clears_claim_context():
    spec_tool = {"kind": "consistency_check", "data_source_id": "insp",
                 "url_columns": ["defect_photo_url"], "url_column_roles": {},
                 "claim_context": {"incident_date_field": "reported_date",
                                   "dataset_kind": "sql"}}

    class _Spec:
        tools_v2 = [spec_tool]
        data_sources = [{"id": "insp", "type": "mcp",
                         "ref": "field_operations.equipment_inspections"}]

    cat = {("field_operations", "field_operations.equipment_inspections"): {
        "kind": "sql",
        "fraud_screening": {"applies": False},
        "columns": [{"name": "equipment_id", "is_primary_key": True}],
    }}
    fr.autowire_fraud_roles(_Spec(), cat, data_sources=_Spec.data_sources)
    assert spec_tool["url_columns"] == []
    assert spec_tool["claim_context"] is None
