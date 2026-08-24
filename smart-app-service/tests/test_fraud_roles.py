# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""Ontology-driven artifact roles — the reuse signal means opposite things by role.

Pins the two scenarios that motivated this: an identity photo reused across
applications is legitimate; an evidence photo reused across cases is fraud.
"""
import fraud_roles as fr


# ── effective_reuse_policy ────────────────────────────────────────────────────
def test_role_defaults():
    assert fr.effective_reuse_policy("identity") == "expected"
    assert fr.effective_reuse_policy("evidence") == "suspicious"
    assert fr.effective_reuse_policy("supporting") == "ignore"


def test_explicit_policy_overrides_role():
    # A column can override its role's default (e.g. an evidence col we trust).
    assert fr.effective_reuse_policy("evidence", "ignore") == "ignore"
    assert fr.effective_reuse_policy("identity", "suspicious") == "suspicious"


def test_unknown_defaults_to_evidence_suspicious():
    # SAFE DEFAULT: silence in the ontology preserves flag-every-duplicate.
    assert fr.effective_reuse_policy(None, None) == "suspicious"
    assert fr.effective_reuse_policy("nonsense") == "suspicious"


# ── interpret_reuse — the headline behavior ──────────────────────────────────
def test_job_reapplication_identity_photo_is_not_fraud():
    """A student re-applying 6 months later with the same headshot: legitimate."""
    sig = fr.interpret_reuse(artifact_role="identity", exact_duplicate=True)
    assert sig["is_fraud_signal"] is False
    assert sig["label"] == "identity match"
    assert "expected" in sig["why"].lower()
    # This module decides IF a hit counts, not by how much — no points here.
    assert "severity_points" not in sig


def test_insurance_claim_evidence_photo_reuse_is_fraud():
    """An old accident/defect photo reused on a new case: double-dip fraud."""
    sig = fr.interpret_reuse(artifact_role="evidence", exact_duplicate=True)
    assert sig["is_fraud_signal"] is True
    assert sig["signal_key"] == fr.SIGNAL_KEY_EXACT
    assert sig["label"] == "reused evidence"
    # Scoring is the T3 gate's job (count-based + env-tunable); we don't publish a
    # per-artifact points number that could never match it.
    assert "severity_points" not in sig


def test_evidence_exact_and_near_carry_distinct_signal_keys():
    exact = fr.interpret_reuse(artifact_role="evidence", exact_duplicate=True)
    near = fr.interpret_reuse(artifact_role="evidence", near_duplicate=True)
    assert exact["is_fraud_signal"] is True and exact["signal_key"] == fr.SIGNAL_KEY_EXACT
    assert near["is_fraud_signal"] is True and near["signal_key"] == fr.SIGNAL_KEY_NEAR


def test_supporting_and_no_dup_are_never_signals():
    assert fr.interpret_reuse(artifact_role="supporting", exact_duplicate=True)["is_fraud_signal"] is False
    assert fr.interpret_reuse(artifact_role="evidence", exact_duplicate=False, near_duplicate=False)["is_fraud_signal"] is False


def test_unknown_role_still_flags_duplicate():
    # No regression: an un-annotated column still catches reuse.
    sig = fr.interpret_reuse(exact_duplicate=True)
    assert sig["is_fraud_signal"] is True
    assert sig["artifact_role"] == "evidence"


# ── resolvers ────────────────────────────────────────────────────────────────
def test_resolve_column_roles_fills_default():
    roles = fr.resolve_column_roles(
        ["face_url", "damage_url"],
        {"face_url": {"artifact_role": "identity"}},
    )
    assert roles["face_url"]["artifact_role"] == "identity"
    assert roles["damage_url"]["artifact_role"] == "evidence"  # default


def test_roles_from_catalogue_columns_is_sparse():
    cols = [
        {"name": "face_url", "artifact_role": "identity"},
        {"name": "damage_url", "artifact_role": "evidence", "reuse_policy": "suspicious"},
        {"name": "amount", "type": "number"},          # no role → omitted
        {"name": "junk", "artifact_role": "bogus"},     # invalid role → omitted
    ]
    out = fr.roles_from_catalogue_columns(cols)
    assert set(out) == {"face_url", "damage_url"}
    assert out["face_url"] == {"artifact_role": "identity"}
    assert out["damage_url"] == {"artifact_role": "evidence", "reuse_policy": "suspicious"}


def test_invalid_role_is_dropped_but_warned(caplog):
    # A typo (e.g. 'evidance') must not silently opt a column out — it's ignored
    # AND surfaced (fail-loud), so the author learns their sources.json is wrong.
    import logging
    with caplog.at_level(logging.WARNING):
        out = fr.roles_from_catalogue_columns([{"name": "x", "artifact_role": "evidance"}])
    assert out == {}                                   # invalid → not applied
    assert any("invalid artifact_role" in r.message and "evidance" in str(r.args)
               for r in caplog.records), "the typo must be logged, not swallowed"


# ── screening_active — the ontology gate ─────────────────────────────────────
def test_screening_gate():
    assert fr.screening_active({"applies": True}, {}) is True
    assert fr.screening_active({"applies": False},
                               {"x": {"artifact_role": "evidence"}}) is False   # hard opt-out wins
    assert fr.screening_active(None, {"x": {"artifact_role": "evidence"}}) is True  # roles ⇒ intent
    assert fr.screening_active(None, {"x": {"artifact_role": None}}) is False       # nothing declared ⇒ off
    assert fr.screening_active(None, {}) is False
    # A hints-only block (applies omitted) must NOT read as a hard OFF — a role
    # still turns it on; nothing declared still leaves it off.
    assert fr.screening_active({"value_fields": ["amt"]},
                               {"x": {"artifact_role": "evidence"}}) is True
    assert fr.screening_active({"value_fields": ["amt"]}, {}) is False


def test_screening_explicit_off():
    assert fr.screening_explicit_off({"applies": False}) is True
    assert fr.screening_explicit_off({"applies": True}) is False
    assert fr.screening_explicit_off({"value_fields": ["amt"]}) is False  # hints-only ≠ opt-out
    assert fr.screening_explicit_off(None) is False


# ── autowire_fraud_roles — ontology drives capture + match, not the LLM ──────
class _Spec:
    def __init__(self, tools_v2, data_sources=None):
        self.tools_v2 = tools_v2
        self.data_sources = data_sources or [
            {"id": "insp", "type": "mcp", "ref": "field_operations.equipment_inspections"}
        ]


def _catalogue(applies=True, pk=True):
    cols = [
        {"name": "defect_photo_url", "artifact_role": "evidence", "reuse_policy": "suspicious"},
        {"name": "headshot_url", "artifact_role": "identity"},
        {"name": "brochure_url", "artifact_role": "supporting"},   # never fingerprinted
        {"name": "notes", "type": "text"},                          # no role → ignored
    ]
    if pk:
        cols.insert(0, {"name": "equipment_id", "type": "string", "is_primary_key": True})
    return {
        ("field_operations", "field_operations.equipment_inspections"): {
            "fraud_screening": ({"applies": applies} if applies is not None else None),
            "columns": cols,
        },
        ("x", "y"): None,  # unresolved entry must be skipped, not crash
    }


def _ctool(**kw):
    return {"kind": "consistency_check", "data_source_id": "insp",
            "url_columns": [], "url_column_roles": {}, **kw}


def _wire(spec, cat):
    # data_sources live on the AppSpec; autowire takes them explicitly (no
    # AgentSpec fallback). _Spec carries them for the test.
    return fr.autowire_fraud_roles(spec, cat, data_sources=spec.data_sources)


def test_autowire_derives_capture_columns_from_ontology():
    tool = _ctool()
    n = _wire(_Spec([tool]), _catalogue(applies=True))
    assert n == 2  # screen wired + fraud_synthesis auto-appended (screening ON)
    # evidence + identity are captured; supporting + role-less are NOT.
    assert tool["url_columns"] == ["defect_photo_url", "headshot_url"]
    assert tool["url_column_roles"]["defect_photo_url"]["artifact_role"] == "evidence"
    assert tool["url_column_roles"]["headshot_url"]["artifact_role"] == "identity"


def test_autowire_stamps_identity_fields_from_ontology():
    # WHICH identifiers link cases must come from the source, not the LLM.
    tool = _ctool()
    cat = _catalogue(applies=True)
    entry = cat[("field_operations", "field_operations.equipment_inspections")]
    entry["fraud_screening"] = {"applies": True,
                                "identity_fields": ["consumer_id", "equipment_id"]}
    _wire(_Spec([tool]), cat)
    assert tool["identity_fields"] == ["consumer_id", "equipment_id"]   # sorted


def test_autowire_leaves_identity_fields_alone_when_ontology_silent():
    tool = _ctool()
    _wire(_Spec([tool]), _catalogue(applies=True))   # no identity_fields declared
    assert not tool.get("identity_fields")


def test_identity_fields_reader_is_dict_or_model_shaped():
    assert fr._identity_fields({"identity_fields": ["a", "b"]}) == ["a", "b"]
    assert fr._identity_fields({"applies": True}) == []
    assert fr._identity_fields(None) == []

    class _M:
        identity_fields = ["vin"]
    assert fr._identity_fields(_M()) == ["vin"]


def _analyze(kind, col):
    return {"kind": kind, "name": f"{kind}_{col}", "data_source_id": "insp",
            "url_column": col}


def test_autowire_stamps_role_onto_image_and_doc_tools():
    # The per-image tools get the ontology role stamped directly (deep fix): an
    # identity photo tool is exempted even with NO consistency_check present.
    img = _analyze("image_analyze", "headshot_url")
    doc = _analyze("doc_extract", "defect_photo_url")   # evidence
    n = _wire(_Spec([img, doc]), _catalogue(applies=True))
    assert n >= 2                                        # both stamped (+ any screen create)
    assert img["artifact_role"] == "identity"
    assert doc["artifact_role"] == "evidence"


def test_autowire_leaves_unannotated_media_column_unstamped():
    # A column the ontology says nothing about → no stamp → safe default at runtime.
    img = _analyze("image_analyze", "notes")            # 'notes' has no role in _catalogue
    _wire(_Spec([img]), _catalogue(applies=True))
    assert img.get("artifact_role") is None
    assert img.get("reuse_policy") is None


def test_autowire_role_stamp_is_idempotent():
    # Run twice on the SAME spec (so the auto-created screen persists): the second
    # pass changes nothing — the stamp already matches the ontology.
    img = _analyze("image_analyze", "headshot_url")
    spec = _Spec([img])
    _wire(spec, _catalogue(applies=True))
    assert img["artifact_role"] == "identity"
    n2 = _wire(spec, _catalogue(applies=True))
    assert n2 == 0                                       # fully idempotent
    assert img["artifact_role"] == "identity"


def test_autowire_clears_only_on_explicit_opt_out():
    # applies=false is a HARD opt-out → fingerprinting is turned off entirely.
    tool = _ctool(url_columns=["defect_photo_url"],
                  url_column_roles={"defect_photo_url": {"artifact_role": "evidence"}})
    n = _wire(_Spec([tool]), _catalogue(applies=False))
    assert n == 1
    assert tool["url_columns"] == []
    assert tool["url_column_roles"] == {}


def test_autowire_silent_ontology_preserves_manual_columns():
    # Source says NOTHING about fraud (no fraud_screening block, no roles). A
    # hand-authored pre-ontology screen must be LEFT ALONE, not wiped.
    tool = _ctool(url_columns=["something"])
    cat = {("field_operations", "field_operations.equipment_inspections"):
           {"columns": [{"name": "something", "type": "string"}]}}
    _wire(_Spec([tool]), cat)
    assert tool["url_columns"] == ["something"]   # preserved — silence ≠ opt-out


def test_autowire_ontology_wins_conflict_but_keeps_human_columns():
    # Ontology is authority for columns it KNOWS (its role overrides a stale one),
    # but a human-added column the ontology doesn't mention is preserved.
    tool = _ctool(
        url_columns=["defect_photo_url", "signature_url"],
        url_column_roles={"defect_photo_url": {"reuse_policy": "ignore"},   # stale — ontology overrides
                          "signature_url": {"artifact_role": "evidence"}},  # human-added — kept
    )
    _wire(_Spec([tool]), _catalogue(applies=True))
    roles = tool["url_column_roles"]
    assert roles["defect_photo_url"] == {"artifact_role": "evidence", "reuse_policy": "suspicious"}
    assert "signature_url" in tool["url_columns"]          # human column preserved
    assert roles["signature_url"]["artifact_role"] == "evidence"


def test_autowire_drops_column_downgraded_to_supporting():
    # A column previously wired as evidence, now marked `supporting` in the
    # ontology, must be DROPPED (the ontology can opt a column out). It's in
    # col_roles (known) but not in targets → not a preserved human column.
    tool = _ctool(url_columns=["brochure_url", "defect_photo_url"],
                  url_column_roles={"brochure_url": {"artifact_role": "evidence"}})  # stale
    _wire(_Spec([tool]), _catalogue(applies=True))   # catalogue marks brochure_url supporting
    assert "brochure_url" not in tool["url_columns"]        # opted out → dropped
    assert tool["url_columns"] == ["defect_photo_url", "headshot_url"]


def test_autowire_transient_miss_does_not_wipe_existing_screen():
    # Dataset not in the catalogue (outage / ref drift): an existing screen must
    # be LEFT untouched, never cleared — else an infra blip disables fraud screening.
    tool = _ctool(url_columns=["defect_photo_url"],
                  url_column_roles={"defect_photo_url": {"artifact_role": "evidence"}})
    spec = _Spec([tool])
    changed = _wire(spec, {("x", "y"): None})   # ref 'insp' unresolved
    # The SCREEN is untouched (that's the invariant under test); the only
    # change is the additive fraud_synthesis append (the screen is wired, so
    # the app is fraud-active regardless of the catalogue outage).
    assert changed == 1
    assert tool["url_columns"] == ["defect_photo_url"]
    assert sum(1 for t in spec.tools_v2
               if fr._tool_attr(t, "kind") == "fraud_synthesis") == 1


def test_autowire_idempotent_and_skips_non_consistency_tools():
    tool = _ctool()
    other = {"kind": "mcp", "url_columns": ["defect_photo_url"]}
    spec = _Spec([tool, other])
    first = _wire(spec, _catalogue(applies=True))
    second = _wire(spec, _catalogue(applies=True))
    assert first == 2 and second == 0  # screen + synthesis, then nothing
    assert "url_column_roles" not in other


# ── auto-CREATE — the ontology alone wires the screen, no builder-LLM ─────────
def _consistency_tools(spec):
    return [t for t in spec.tools_v2 if fr._tool_attr(t, "kind") == "consistency_check"]


def test_autowire_auto_creates_screen_when_missing():
    spec = _Spec(tools_v2=[])                        # app has NO consistency_check tool
    n = _wire(spec, _catalogue(applies=True))
    created = _consistency_tools(spec)
    assert n == 2 and len(created) == 1  # + the auto-appended fraud_synthesis
    t = created[0]
    assert fr._tool_attr(t, "data_source_id") == "insp"
    assert fr._tool_attr(t, "key_field") == "equipment_id"      # dataset primary key
    assert fr._tool_attr(t, "url_columns") == ["defect_photo_url", "headshot_url"]


def test_autowire_auto_creates_from_hints_only_block():
    # A fraud_screening block with ONLY advisory hints (no `applies`) + declared
    # roles must still opt IN — hints-only must not read as a hard OFF (R6).
    cat = _catalogue(applies=None)   # no fraud_screening block; roles carry the intent
    cat[("field_operations", "field_operations.equipment_inspections")]["fraud_screening"] = {
        "value_fields": ["assessed_amount"]}   # hints only, applies omitted
    spec = _Spec(tools_v2=[])
    _wire(spec, cat)
    assert len(_consistency_tools(spec)) == 1


def test_autowire_no_create_when_screening_off():
    spec = _Spec(tools_v2=[])
    _wire(spec, _catalogue(applies=False))
    assert _consistency_tools(spec) == []


# ── THE DEFAULT: a source that never mentions fraud gets NO fraud at all ──────
# NB _catalogue(applies=None) is NOT this case — its columns still declare roles,
# which is itself an opt-in ("roles ⇒ intent"). Silence means BOTH absent.
def _bare_catalogue():
    """A dataset that says NOTHING about fraud: no fraud_screening block, no
    column artifact_roles — even though it HAS a media column."""
    return {
        ("field_operations", "field_operations.equipment_inspections"): {
            "columns": [
                {"name": "equipment_id", "type": "string", "is_primary_key": True},
                {"name": "defect_photo_url", "column_kind": "image_url"},  # media, no role
                {"name": "consumer_id", "type": "string"},                 # id-ish, not declared
                {"name": "notes", "type": "text"},
            ],
        },
    }


def test_default_is_fraud_OFF_when_ontology_is_silent():
    # The headline default: no screen auto-created, nothing stamped, no fraud
    # surface anywhere — despite a primary key AND a media column being present
    # (i.e. it COULD have been wired; the ontology just never asked for it).
    spec = _Spec(tools_v2=[])
    assert _wire(spec, _bare_catalogue()) == 0
    assert _consistency_tools(spec) == []
    assert fr.fraud_active_for_agent(spec) is False


def test_default_silence_leaves_media_tools_unstamped():
    # A per-image tool on a silent dataset gets no role → read-time default only.
    img = _analyze("image_analyze", "defect_photo_url")
    doc = _analyze("doc_extract", "notes")
    assert _wire(_Spec([img, doc]), _bare_catalogue()) == 0
    assert img.get("artifact_role") is None and img.get("reuse_policy") is None
    assert doc.get("artifact_role") is None


def test_default_silence_declares_no_identity_fields():
    # An id-shaped column is NOT linked by ontology unless the source declares it.
    tool = _ctool()
    _wire(_Spec([tool]), _bare_catalogue())
    assert not tool.get("identity_fields")


def test_autowire_no_create_without_primary_key():
    spec = _Spec(tools_v2=[])
    _wire(spec, _catalogue(applies=True, pk=False))
    assert _consistency_tools(spec) == []            # no record key to bind → skip


def test_autowire_does_not_duplicate_existing_screen():
    spec = _Spec(tools_v2=[_ctool()])                # already has one bound to insp
    _wire(spec, _catalogue(applies=True))
    assert len(_consistency_tools(spec)) == 1        # populated, not duplicated


# ── apply_reuse_signal — reads the REAL fraud_checks.artifact_flags keys ──────
# These pin the raw-key contract (`duplicate` / `phash_near_dups`). The prior bug
# read `exact_duplicate`, a key artifact_flags never emits, so the whole exact-dup
# path was dead — units that hand-built `exact_duplicate` dicts never caught it.
def test_apply_reuse_evidence_exact_is_fraud_and_keeps_raw_key():
    # An evidence photo flagged as a byte-identical duplicate (artifact_flags emits
    # `duplicate: True`). Fraud → the raw key STAYS so the T3 gate scores it.
    a = {"column": "defect_photo_url", "duplicate": True, "prior_refs": ["case-9"]}
    cls = fr.apply_reuse_signal(a, artifact_role="evidence")
    assert cls == "fraud"
    assert a["reuse_is_fraud_signal"] is True
    assert a["duplicate"] is True                 # kept for the gate
    assert a["reuse_signal_key"] == fr.SIGNAL_KEY_EXACT


def test_apply_reuse_identity_exact_is_verification_and_strips_raw_key():
    # Same raw hit on an identity headshot: verification, NOT fraud. The raw
    # `duplicate` key must be MOVED off so the gate can't score it.
    a = {"column": "headshot_url", "duplicate": True, "prior_refs": ["app-3"]}
    cls = fr.apply_reuse_signal(a, artifact_role="identity")
    assert cls == "identity"
    assert a["reuse_is_fraud_signal"] is False
    assert "duplicate" not in a                   # suppressed from the gate walk
    assert a["identity_match_exact"] is True
    assert a["reuse_label"] == "identity match"


def test_apply_reuse_evidence_near_dup_uses_phash_key():
    a = {"column": "defect_photo_url", "phash_near_dups": [{"hamming_bits": 4}]}
    cls = fr.apply_reuse_signal(a, artifact_role="evidence")
    assert cls == "fraud"
    assert a["reuse_signal_key"] == fr.SIGNAL_KEY_NEAR
    assert a["phash_near_dups"]                    # kept for the gate


def test_apply_reuse_supporting_dup_is_dropped():
    a = {"column": "brochure_url", "duplicate": True}
    cls = fr.apply_reuse_signal(a, artifact_role="supporting")
    assert cls == "none"
    assert "duplicate" not in a                    # meaningless reuse, stripped


def test_apply_reuse_no_hit_is_none():
    a = {"column": "defect_photo_url", "duplicate": False}
    assert fr.apply_reuse_signal(a, artifact_role="evidence") == "none"
    assert a["reuse_is_fraud_signal"] is False


def test_apply_reuse_identity_clip_near_dup_is_stripped():
    # The CLIP embedding tier (image_index.near_duplicates / similar) must honour
    # the identity exemption too — else a legitimately reused headshot matched
    # only by embedding still scores at the T3 gate.
    a = {"column": "headshot_url",
         "image_index": {"near_duplicates": [{"score": 0.98}], "similar": [{"score": 0.9}]}}
    cls = fr.apply_reuse_signal(a, artifact_role="identity")
    assert cls == "identity"
    assert not a["image_index"].get("near_duplicates")   # stripped
    assert "similar" not in a["image_index"]
    assert a["identity_match_near"] is True


def test_apply_reuse_evidence_clip_near_dup_is_fraud_and_kept():
    a = {"column": "defect_photo_url", "image_index": {"near_duplicates": [{"score": 0.97}]}}
    cls = fr.apply_reuse_signal(a, artifact_role="evidence")
    assert cls == "fraud"
    assert a["image_index"]["near_duplicates"]           # kept — gate scores clip_near_duplicate
    assert a["reuse_signal_key"] == fr.SIGNAL_KEY_NEAR


def test_apply_reuse_suspicious_clip_similar_only_is_never_stripped():
    # A weak CLIP `similar`-only hit on a default evidence/suspicious column is
    # still a gate signal (clip_similar). It must NOT be stripped — the ontology
    # may only relax screening, never silently weaken the default path.
    a = {"column": "defect_photo_url", "image_index": {"similar": [{"score": 0.85}]}}
    cls = fr.apply_reuse_signal(a, artifact_role="evidence")   # → suspicious policy
    assert cls == "none"                                  # weak: not a hard dup_hit …
    assert a["image_index"]["similar"]                    # … but the marker survives for the gate


# ── role_for_url_column — per-image tools inherit the ontology role ───────────
# image_analyze/doc_extract bind to ONE url_column and carry no url_column_roles;
# they must read the role from the sibling consistency_check so an identity artifact
# reused across cases is not scored as fraud on that path (the former limitation).
class _RSpec:
    def __init__(self, tools_v2):
        self.tools_v2 = tools_v2


def _screen(ds_id, roles):
    return {"kind": "consistency_check", "data_source_id": ds_id,
            "url_columns": list(roles), "url_column_roles": roles}


def test_role_for_url_column_reads_sibling_consistency_check():
    spec = _RSpec([
        _screen("insp", {"headshot_url": {"artifact_role": "identity"},
                         "defect_photo_url": {"artifact_role": "evidence"}}),
    ])
    r = fr.role_for_url_column(spec, data_source_id="insp", url_column="headshot_url")
    assert r["artifact_role"] == "identity"
    r2 = fr.role_for_url_column(spec, data_source_id="insp", url_column="defect_photo_url")
    assert r2["artifact_role"] == "evidence"


def test_role_for_url_column_defaults_when_no_screen_covers_it():
    # No consistency_check on this data_source (or column not captured) ⇒ safe
    # default (None ⇒ evidence/suspicious at read time = flag-everything).
    spec = _RSpec([_screen("other", {"x": {"artifact_role": "identity"}})])
    r = fr.role_for_url_column(spec, data_source_id="insp", url_column="headshot_url")
    assert r == {"artifact_role": None, "reuse_policy": None}
    # column present on the right ds but not in that screen's roles → default too
    spec2 = _RSpec([_screen("insp", {"defect_photo_url": {"artifact_role": "evidence"}})])
    assert fr.role_for_url_column(
        spec2, data_source_id="insp", url_column="headshot_url"
    ) == {"artifact_role": None, "reuse_policy": None}


def test_role_for_url_column_end_to_end_identity_exemption():
    # The whole point: an image_analyze headshot flagged as a raw duplicate, run
    # through the role resolved for its column, is exempted (identity match) — no
    # exact_duplicate key survives for the T3 gate to score.
    spec = _RSpec([_screen("insp", {"headshot_url": {"artifact_role": "identity"}})])
    role = fr.role_for_url_column(spec, data_source_id="insp", url_column="headshot_url")
    flags = {"duplicate": True, "prior_refs": [{"record_ref": "APP-2"}]}
    cls = fr.apply_reuse_signal(flags, artifact_role=role["artifact_role"],
                                reuse_policy=role["reuse_policy"])
    assert cls == "identity"
    assert "duplicate" not in flags            # stripped → cannot score at the gate
    assert flags.get("identity_match_exact") is True


def test_role_for_url_column_end_to_end_evidence_still_scores():
    # An evidence column keeps the raw duplicate key so the gate still scores it.
    spec = _RSpec([_screen("insp", {"defect_photo_url": {"artifact_role": "evidence"}})])
    role = fr.role_for_url_column(spec, data_source_id="insp", url_column="defect_photo_url")
    flags = {"duplicate": True, "prior_refs": [{"record_ref": "CASE-9"}]}
    cls = fr.apply_reuse_signal(flags, artifact_role=role["artifact_role"],
                                reuse_policy=role["reuse_policy"])
    assert cls == "fraud"
    assert flags["duplicate"] is True          # kept for the T3 gate


def test_role_for_url_column_missing_args_is_safe_default():
    # Missing binding args → safe default, never a crash.
    spec = _RSpec([_screen("insp", {"defect_photo_url": {"artifact_role": "evidence"}})])
    assert fr.role_for_url_column(spec, data_source_id=None, url_column=None) == {
        "artifact_role": None, "reuse_policy": None}


def test_role_for_url_column_prefers_own_stamped_role_over_sibling():
    # The tool's OWN stamped role wins — and works with NO sibling consistency_check
    # at all (the pk-less-dataset case the deep fix targets).
    tool = {"kind": "image_analyze", "data_source_id": "insp",
            "url_column": "headshot_url", "artifact_role": "identity"}
    spec = _RSpec([tool])                                   # no consistency_check
    r = fr.role_for_url_column(spec, tool=tool,
                               data_source_id="insp", url_column="headshot_url")
    assert r == {"artifact_role": "identity", "reuse_policy": None}
    # A stamped policy overriding the role is carried through too.
    tool2 = {"kind": "image_analyze", "data_source_id": "insp",
             "url_column": "x", "artifact_role": "evidence", "reuse_policy": "ignore"}
    r2 = fr.role_for_url_column(_RSpec([tool2]), tool=tool2,
                                data_source_id="insp", url_column="x")
    assert r2 == {"artifact_role": "evidence", "reuse_policy": "ignore"}


def test_role_for_url_column_end_to_end_stamped_identity_no_sibling():
    # Deep fix: a pk-less identity dataset has NO consistency_check, but the stamped
    # role on the image_analyze tool still exempts the reuse hit as verification.
    tool = {"kind": "image_analyze", "data_source_id": "insp",
            "url_column": "headshot_url", "artifact_role": "identity"}
    role = fr.role_for_url_column(_RSpec([tool]), tool=tool,
                                  data_source_id="insp", url_column="headshot_url")
    flags = {"duplicate": True, "prior_refs": [{"record_ref": "APP-2"}]}
    cls = fr.apply_reuse_signal(flags, artifact_role=role["artifact_role"],
                                reuse_policy=role["reuse_policy"])
    assert cls == "identity"
    assert "duplicate" not in flags
    assert flags.get("identity_match_exact") is True


# ── fraud_active_for_agent — MUST match the runtime's _app_fraud_active ─────
# (ontology review O6: two divergent predicates meant the app card could offer
# "Calibrate fraud" for a screen the runtime treats as inactive.)

def test_fraud_active_requires_binding_not_just_url_columns():
    # url_columns WITHOUT a data_source_id = unbound screen — the runtime can't
    # resolve artifacts, so the publish-side predicate must call it INACTIVE too.
    unbound = {"kind": "consistency_check", "url_columns": ["photo_url"]}
    assert fr.fraud_active_for_agent(_RSpec([unbound])) is False


def test_fraud_active_true_when_bound():
    bound = {"kind": "consistency_check", "url_columns": ["photo_url"],
             "data_source_id": "insp"}
    assert fr.fraud_active_for_agent(_RSpec([bound])) is True


def test_fraud_active_false_with_no_screen():
    other = {"kind": "image_analyze", "data_source_id": "insp", "url_column": "x"}
    assert fr.fraud_active_for_agent(_RSpec([other])) is False
