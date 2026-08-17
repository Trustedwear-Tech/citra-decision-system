# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""Fraud framework — NO-FALSE-ALARM contract.

The fraud stack is EVIDENCE-only; its one job it must never botch is raising a
fraud signal where there is none. This suite pins every place a false alarm
could enter, per item type, and proves the framework does NOT itself introduce
fraud. It exercises the REAL decision code — the deterministic scorer
(`fraud_synthesis.severity_points`, the single gate input), the role-aware
stripping (`fraud_roles.apply_reuse_signal` + `role_for_url_column`), the
ontology gate (`screening_active` / `fraud_active_for_agent`), the same-record
fingerprint scoping (`fraud_checks.record_and_check_fingerprint`), and the
below-gate short-circuit (`fraud_synthesis.run_synthesis`).

False-alarm scenarios covered:

  A. Same APPLICATION re-evaluated — the same evidence on the SAME record must
     never read as a duplicate (record_ref scoping); only cross-record reuse does.
  B. Role-aware reuse per ITEM TYPE — an `identity` image/doc reused by the same
     applicant is verification, a `supporting` artifact's reuse is meaningless;
     neither may score. Only `evidence` reuse scores. (And the inverse: a tampered
     identity doc STILL scores on metadata — no false CLEARANCE.)
  C. Deterministic scorer hygiene — empty / benign / adversarial signal blobs
     never manufacture points.
  D. Ontology gate — an app whose sources.json did not enable fraud captures and
     scores NOTHING.
  E. Below-gate / low-risk — no fraud card is surfaced to the officer.
  F. Capture matrix — only evidence + identity columns are fingerprinted.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

import fraud_checks as fc
import fraud_roles as fr
from fraud_synthesis import run_synthesis, severity_points


# ─────────────────────────────────────────────────────────────────────────────
# A. Same application re-evaluated ≠ fabrication  (record_ref scoping)
# ─────────────────────────────────────────────────────────────────────────────
class _FakeCursor:
    def __init__(self, docs):
        self._docs = docs

    async def to_list(self, length=None):
        return self._docs[:length] if length else self._docs


class _FakeFpCol:
    """Minimal in-memory stand-in for the fingerprint collection — only the call
    shapes record_and_check_fingerprint uses: find_one_and_update (BEFORE-image +
    $push/$slice) and the band-prefilter find ($in on *_bands, $ne on sha256)."""
    name = "fp_test"

    def __init__(self):
        self.store = {}

    async def create_index(self, *a, **k):
        return None

    def find(self, filt, proj=None):
        out = []
        for d in self.store.values():
            if "tenant_id" in filt and d.get("tenant_id") != filt["tenant_id"]:
                continue
            ok = True
            for band_field in ("text_bands", "dhash_bands"):
                spec = filt.get(band_field)
                if isinstance(spec, dict) and "$in" in spec:
                    if not (set(d.get(band_field) or []) & set(spec["$in"])):
                        ok = False
            sha_spec = filt.get("sha256")
            if isinstance(sha_spec, dict) and "$ne" in sha_spec:
                if d.get("sha256") == sha_spec["$ne"]:
                    ok = False
            if ok:
                out.append(dict(d))
        return _FakeCursor(out)

    async def find_one_and_update(self, filt, update, upsert=False, return_document=False):
        key = (filt.get("tenant_id"), filt.get("sha256"))
        pre = self.store.get(key)
        doc = dict(pre) if pre else dict(update.get("$setOnInsert") or {})
        doc.update(update.get("$set") or {})
        for field, spec in (update.get("$push") or {}).items():
            arr = list(doc.get(field) or [])
            each = spec.get("$each", []) if isinstance(spec, dict) else [spec]
            arr.extend(each)
            sl = spec.get("$slice") if isinstance(spec, dict) else None
            if isinstance(sl, int) and sl < 0:
                arr = arr[sl:]
            doc[field] = arr
        self.store[key] = doc
        return dict(pre) if pre else None   # return_document=False ⇒ pre-image


@pytest.fixture()
def fp(monkeypatch):
    fake = _FakeFpCol()
    monkeypatch.setattr(fc, "_fingerprints_col", lambda: fake)
    fc._fp_indexes_ensured.discard(_FakeFpCol.name)
    return fake


def _check(sha="H1", record_ref=None, item_id="it-1"):
    return asyncio.run(fc.record_and_check_fingerprint(
        tenant_id="t1", app_slug="app", sha256=sha, modality="image",
        task_type="fraud-screening", item_id=item_id, record_ref=record_ref,
    ))


def test_same_record_reeval_is_not_a_duplicate(fp):
    # Re-running the SAME application with the SAME photo must NOT flag.
    first = _check(record_ref="APP-1", item_id="APP-1-photo")
    assert first["duplicate"] is False               # first sighting
    again = _check(record_ref="APP-1", item_id="APP-1-photo")
    assert again["duplicate"] is False               # SAME record → no false alarm
    assert again["prior_refs"] == []


def test_cross_record_reuse_is_a_real_duplicate(fp):
    # The genuine double-dip: same photo, DIFFERENT application → flagged.
    _check(record_ref="APP-1", item_id="APP-1-photo")
    other = _check(record_ref="APP-2", item_id="APP-2-photo")
    assert other["duplicate"] is True
    assert other["prior_refs"] and other["prior_refs"][0]["record_ref"] == "APP-1"


def test_no_record_binding_same_item_id_is_not_a_duplicate(fp):
    # Headless / direct-URL path (no record_ref): a re-run of the SAME item_id
    # (stable per URL) must not flag itself.
    assert _check(record_ref=None, item_id="img-abc")["duplicate"] is False
    assert _check(record_ref=None, item_id="img-abc")["duplicate"] is False


def test_no_record_binding_different_item_id_is_a_duplicate(fp):
    _check(record_ref=None, item_id="img-abc")
    assert _check(record_ref=None, item_id="img-xyz")["duplicate"] is True


# ── A2. record_ref is dataset-QUALIFIED (matching is tenant-wide, so a bare
#        record id from another dataset could collide and silently un-flag reuse)
# ─────────────────────────────────────────────────────────────────────────────
def test_qualify_record_ref_shape_and_fallback():
    assert fc.qualify_record_ref("ops.inspections", "INS-1") == "ops.inspections:INS-1"
    # No dataset ref resolvable (unbound / headless) → bare key, as before.
    assert fc.qualify_record_ref(None, "INS-1") == "INS-1"
    assert fc.qualify_record_ref("ops.inspections", None) is None


def test_same_record_ref_strict_between_qualified_refs():
    # THE COLLISION FIX: two datasets both numbering from 1 are NOT the same record.
    assert fc.same_record_ref("ops.inspections:1001", "ops.inspections:1001") is True
    assert fc.same_record_ref("ops.inspections:1001", "fin.claims:1001") is False


def test_same_record_ref_legacy_bare_matches_its_qualified_form():
    # A ref written before qualification is a bare key — it must still match the
    # SAME record's new qualified ref, else every pre-existing artifact flags itself.
    assert fc.same_record_ref("INS-1", "ops.inspections:INS-1") is True
    assert fc.same_record_ref("ops.inspections:INS-1", "INS-1") is True
    # …but a legacy bare key for a DIFFERENT record still doesn't match.
    assert fc.same_record_ref("INS-2", "ops.inspections:INS-1") is False


def test_colliding_ids_across_datasets_now_flag_as_reuse(fp):
    # Same artifact on record "1001" of TWO different datasets = real cross-case
    # reuse. Pre-namespacing both were record_ref="1001" → read as the same record
    # → silently NOT flagged (the false NEGATIVE this fix closes).
    a = fc.qualify_record_ref("ops.inspections", "1001")
    b = fc.qualify_record_ref("fin.claims", "1001")
    assert _check(record_ref=a, item_id="ops-1001-photo")["duplicate"] is False
    out = _check(record_ref=b, item_id="fin-1001-photo")
    assert out["duplicate"] is True                      # now correctly caught
    assert out["prior_refs"][0]["record_ref"] == a


def test_same_qualified_record_reeval_still_not_a_duplicate(fp):
    # The no-false-alarm guarantee survives qualification.
    q = fc.qualify_record_ref("ops.inspections", "INS-1")
    assert _check(record_ref=q, item_id="INS-1-photo")["duplicate"] is False
    assert _check(record_ref=q, item_id="INS-1-photo")["duplicate"] is False


def test_legacy_bare_ref_then_qualified_reeval_is_not_a_duplicate(fp):
    # MIGRATION: artifact first fingerprinted with a legacy bare ref, then
    # re-screened after the qualification change → must NOT flag itself.
    _check(record_ref="INS-1", item_id="INS-1-photo")          # legacy write
    out = _check(record_ref=fc.qualify_record_ref("ops.inspections", "INS-1"),
                 item_id="INS-1-photo")                        # post-change re-eval
    assert out["duplicate"] is False
    assert out["prior_refs"] == []


# ─────────────────────────────────────────────────────────────────────────────
# B. Role-aware reuse per item type — strip the false alarm, keep the real one
#    Chain: raw artifact_flags → apply_reuse_signal(role) → severity_points score
# ─────────────────────────────────────────────────────────────────────────────
def _score_after_role(flags, *, artifact_role=None, reuse_policy=None):
    """Mimic the runtime: strip by role, then run the deterministic scorer over
    what would reach the T3 gate. Returns (classification, points, counts)."""
    cls = fr.apply_reuse_signal(flags, artifact_role=artifact_role, reuse_policy=reuse_policy)
    points, counts = severity_points({"artifact": flags})
    return cls, points, counts


def test_image_identity_reuse_scores_zero():
    # A headshot reused by the same applicant across cases = verification.
    cls, points, counts = _score_after_role({"duplicate": True}, artifact_role="identity")
    assert cls == "identity"
    assert points == 0
    assert "exact_duplicate" not in counts


def test_image_evidence_reuse_scores_nonzero():
    # An accident/defect photo reused across cases = the real double-dip.
    cls, points, counts = _score_after_role({"duplicate": True}, artifact_role="evidence")
    assert cls == "fraud"
    assert points > 0                               # scored
    assert counts.get("exact_duplicate") == 1


def test_document_identity_reuse_scores_zero():
    # An ID-scan document reused by the same applicant = verification, not fraud.
    cls, points, _ = _score_after_role(
        {"duplicate": True, "phash_near_dups": [{"hamming_bits": 2}]},
        artifact_role="identity")
    assert cls == "identity"
    assert points == 0


def test_document_text_reuse_scores_as_evidence_reuse():
    # A re-exported invoice: byte-DIFFERENT (no `duplicate`), same text → the
    # document near-dup tier is what catches it.
    cls, points, counts = _score_after_role(
        {"text_near_dups": [{"hamming_bits": 1, "refs": [{"record_ref": "fin.claims:C-9"}]}]},
        artifact_role="evidence")
    assert cls == "fraud"
    assert counts.get("doc_text_near_dup") == 1
    assert points == 3


def test_document_text_reuse_on_identity_doc_is_exempt():
    # An ID scan the same applicant legitimately resubmits: the doc-content tier
    # must obey artifact_role exactly like the pixel tiers — no false identification.
    flags = {"text_near_dups": [{"hamming_bits": 0, "refs": [{"record_ref": "kyc.apps:A-2"}]}]}
    cls, points, counts = _score_after_role(flags, artifact_role="identity")
    assert cls == "identity"
    assert "text_near_dups" not in flags            # stripped before the gate
    assert flags.get("identity_match_near") is True
    assert points == 0 and counts == {}


def test_supporting_artifact_reuse_scores_zero():
    # A brochure / generic T&C PDF reused everywhere is meaningless.
    cls, points, _ = _score_after_role({"duplicate": True}, artifact_role="supporting")
    assert cls == "none"
    assert points == 0


def test_evidence_column_trusted_by_explicit_ignore_policy_scores_zero():
    # An evidence column the ontology explicitly trusts (reuse_policy='ignore').
    cls, points, _ = _score_after_role(
        {"duplicate": True}, artifact_role="evidence", reuse_policy="ignore")
    assert cls == "none"
    assert points == 0


def test_identity_reuse_but_tampered_still_scores_no_false_clearance():
    # The inverse guard: stripping the identity REUSE must NOT clear a TAMPERING
    # signal — metadata anomalies are role-independent (a doctored ID is still bad).
    flags = {"duplicate": True, "metadata": {"anomalies": ["modified-after-creation"]}}
    cls, points, counts = _score_after_role(flags, artifact_role="identity")
    assert cls == "identity"
    assert "exact_duplicate" not in counts          # reuse exempted …
    assert counts.get("metadata_anomaly") == 1      # … tampering NOT exempted
    assert points == 1


def test_unannotated_column_defaults_to_flagging_no_silent_weakening():
    # SAFE DEFAULT: a column the ontology says nothing about still catches reuse
    # (the ontology may only RELAX, never silently weaken screening).
    cls, points, counts = _score_after_role({"duplicate": True})   # role=None
    assert cls == "fraud"
    assert counts.get("exact_duplicate") == 1
    assert points > 0


# ─────────────────────────────────────────────────────────────────────────────
# B2. Document text SimHash — the doc identity tier. Its false-alarm risk is
#     LOW-ENTROPY text, so the entropy floor is the safety property to pin.
# ─────────────────────────────────────────────────────────────────────────────
_INVOICE = (
    "Tax Invoice number INV-2026-00417 dated 14 March 2026. Vendor Acme Power "
    "Services Private Limited, GSTIN 27AABCA1234F1Z5. Bill to Northern Grid "
    "Division. Description: replacement of distribution transformer bushing "
    "assembly, including labour and transport. Quantity 3 units at 18,500 each. "
    "Subtotal 55,500. CGST 9 percent 4,995. SGST 9 percent 4,995. Total payable "
    "65,490 rupees only. Payment terms net thirty days."
)


def test_simhash_entropy_floor_refuses_thin_text():
    # A fingerprint over a few tokens collides across unrelated documents, and every
    # collision would surface as a bogus "reused document". No signal > false signal.
    assert fc.simhash64("") is None
    assert fc.simhash64("paid") is None
    assert fc.simhash64("invoice total 65490 rupees") is None      # < 24 tokens
    assert fc.simhash64(_INVOICE) is not None                       # substantive


def test_simhash_is_layout_and_case_insensitive_same_document():
    # A re-export changes whitespace/case/line-breaks, not content → same identity.
    reexported = _INVOICE.upper().replace(" ", "\n  ")
    a, b = fc.simhash64(_INVOICE), fc.simhash64(reexported)
    assert a is not None and a == b


def test_simhash_separates_unrelated_documents():
    other = (
        "Field inspection report reference INS-88 recorded on 2 April 2026 by "
        "inspector R Kumar for feeder line seven near the Ashford substation. "
        "Observed corrosion on the pole mounted isolator and a cracked insulator "
        "disc. Recommended immediate replacement and a follow up thermal scan "
        "within thirty days. No outage reported during the inspection window."
    )
    a, b = fc.simhash64(_INVOICE), fc.simhash64(other)
    assert a and b
    # Unrelated documents must be far outside the near-dup band (≤3 of 64).
    assert fc.hamming_hex(a, b) > fc._TEXT_NEAR_BITS * 3


def test_simhash_word_order_matters():
    # Reordered boilerplate is not the same document (shingles are order-sensitive).
    shuffled = " ".join(reversed(_INVOICE.split()))
    assert fc.hamming_hex(fc.simhash64(_INVOICE), fc.simhash64(shuffled)) > fc._TEXT_NEAR_BITS


def test_document_text_near_dup_is_found_across_records_not_within(fp):
    # The store tier end-to-end: same text on a DIFFERENT record → text_near_dups;
    # the same record re-screened → nothing (no self-flag).
    sh = fc.simhash64(_INVOICE)

    async def _check_doc(sha, record_ref, item_id):
        return await fc.record_and_check_fingerprint(
            tenant_id="t1", app_slug="app", sha256=sha, modality="document",
            task_type="invoice", item_id=item_id, record_ref=record_ref,
            text_simhash=sh)

    # First sighting on claim C-1 (bytes "AAA").
    asyncio.run(_check_doc("AAA", "fin.claims:C-1", "C-1-doc"))
    # Re-screening the SAME record with re-exported bytes → NOT a duplicate.
    same = asyncio.run(_check_doc("BBB", "fin.claims:C-1", "C-1-doc"))
    assert "text_near_dups" not in same
    # The same invoice text on ANOTHER claim, different bytes → caught.
    other = asyncio.run(_check_doc("CCC", "fin.claims:C-2", "C-2-doc"))
    assert other["duplicate"] is False              # bytes differ → SHA sees nothing
    assert other["text_near_dups"]                  # …but the text tier does
    assert other["text_near_dups"][0]["hamming_bits"] == 0


# ─────────────────────────────────────────────────────────────────────────────
# B3. Cross-case identifier linking is ONTOLOGY-driven, not LLM/heuristic-driven.
#     sources.json `fraud_screening.identity_fields` declares the linkable keys.
# ─────────────────────────────────────────────────────────────────────────────
def test_ontology_identity_field_links_even_when_heuristic_misses_it():
    import entity_links as el

    values = {"policy_reference": "POL-88771", "notes": "routine"}
    # Heuristic alone doesn't recognise 'policy_reference' as an identifier …
    assert el.extract_linkable(values) == []
    # … but the SOURCE declares it a cross-record key → it links (generic 'id').
    got = el.extract_linkable(values, None, ["policy_reference"])
    assert len(got) == 1
    assert got[0]["entity_type"] == "id" and got[0]["field"] == "policy_reference"


def test_specific_pinned_type_wins_over_generic_identity_default():
    import entity_links as el

    values = {"chassis": "1HGCM82633A004352"}
    got = el.extract_linkable(values, {"chassis": "vin"}, ["chassis"])
    assert got[0]["entity_type"] == "vin"        # not downgraded to 'id'


def test_identity_fields_only_widen_never_restrict_linking():
    import entity_links as el

    values = {"phone": "9876543210", "policy_reference": "POL-1"}
    # Declaring ONE key must not stop the heuristic from linking the other —
    # the ontology may relax scoring, never weaken screening.
    fields = {g["field"] for g in el.extract_linkable(values, None, ["policy_reference"])}
    assert fields == {"phone", "policy_reference"}


def test_undeclared_low_value_field_still_does_not_link():
    import entity_links as el

    # No ontology, no pin, not identifier-shaped → still nothing (no new noise).
    assert el.extract_linkable({"status": "open", "colour": "red"}) == []


# ─────────────────────────────────────────────────────────────────────────────
# C. Deterministic scorer never manufactures points
# ─────────────────────────────────────────────────────────────────────────────
def test_empty_and_benign_signals_score_zero():
    assert severity_points(None) == (0, {})
    assert severity_points({}) == (0, {})
    assert severity_points([]) == (0, {})
    # A blob of ordinary case fields with no fraud keys.
    pts, counts = severity_points(
        {"claim_id": "C-1", "amount": 5000, "status": "open", "photos": ["a", "b"]})
    assert pts == 0 and counts == {}


def test_adversarial_deep_blob_is_bounded_not_crashing():
    # A pathologically nested payload must not RecursionError/DoS the scorer.
    node: dict = {}
    cur = node
    for _ in range(10000):
        cur["n"] = {}
        cur = cur["n"]
    pts, counts = severity_points(node)
    assert "walk_truncated" in counts    # capped, visible
    assert pts == 0                       # walk_truncated has weight 0 → no phantom score


def test_cross_case_evidence_exact_dup_is_the_only_scored_signal_here():
    # Positive control: a real exact-duplicate on an (un-stripped) evidence blob.
    pts, counts = severity_points({"artifact": {"duplicate": True}})
    assert counts == {"exact_duplicate": 1}
    assert pts == 3


# ─────────────────────────────────────────────────────────────────────────────
# D. Ontology gate — a non-fraud app captures and scores nothing
# ─────────────────────────────────────────────────────────────────────────────
def test_non_fraud_ontology_does_not_activate_screening():
    # applies omitted + no column roles ⇒ OFF (source said nothing about fraud).
    assert fr.screening_active(None, {}) is False
    assert fr.screening_active({"value_fields": ["amt"]}, {}) is False


def test_explicit_opt_out_wins_over_declared_roles():
    assert fr.screening_active({"applies": False},
                               {"x": {"artifact_role": "evidence"}}) is False


class _Spec:
    def __init__(self, tools_v2):
        self.tools_v2 = tools_v2


def test_per_item_fingerprint_gate_is_PER_DATASET_not_app_global():
    # A source that never opted into fraud must not have its artifacts hashed into
    # the store just because a SIBLING dataset in the same app did.
    from tools_v2_dispatch import _dataset_fraud_active

    spec = _Spec([
        # only the claims dataset is screened
        {"kind": "consistency_check", "data_source_id": "ds_claims",
         "url_columns": ["damage_photo_url"]},
    ])
    assert _dataset_fraud_active(spec, "ds_claims") is True     # screened → fingerprint
    assert _dataset_fraud_active(spec, "ds_hr") is False        # silent sibling → NEVER
    # Unbound (headless/direct-URL) → no dataset ontology to consult → opt-in absent.
    assert _dataset_fraud_active(spec, None) is False


def test_per_item_gate_ignores_an_unwired_screen_on_the_same_dataset():
    from tools_v2_dispatch import _dataset_fraud_active

    # A screen with no url_columns captures nothing → not active for its dataset.
    spec = _Spec([{"kind": "consistency_check", "data_source_id": "ds_claims",
                   "url_columns": []}])
    assert _dataset_fraud_active(spec, "ds_claims") is False


def test_fraud_inactive_for_agent_without_a_wired_screen():
    # A screen is "wired" only with BOTH url_columns AND a data_source_id — the same
    # definition the runtime uses (tools_v2_dispatch._dataset_fraud_active). Anything
    # less ⇒ no fraud surface anywhere (no fingerprinting, no calibration button).
    assert fr.fraud_active_for_agent(_Spec([])) is False
    # no columns captured
    assert fr.fraud_active_for_agent(
        _Spec([{"kind": "consistency_check", "data_source_id": "insp",
                "url_columns": []}])) is False
    # UNBOUND screen: columns but no data_source_id → can't resolve artifacts → inactive
    assert fr.fraud_active_for_agent(
        _Spec([{"kind": "consistency_check", "url_columns": ["photo_url"]}])) is False
    # fully wired
    assert fr.fraud_active_for_agent(
        _Spec([{"kind": "consistency_check", "data_source_id": "insp",
                "url_columns": ["photo_url"]}])) is True


# ─────────────────────────────────────────────────────────────────────────────
# E. Below-gate / low risk — no fraud card is put in front of the officer
# ─────────────────────────────────────────────────────────────────────────────
@pytest.fixture()
def _no_screening_store(monkeypatch):
    # The below-gate path best-effort-persists a screening row; give it a no-op
    # sink so the test asserts the GATE behavior, not storage.
    async def _noop_indexes():
        return None

    class _Sink:
        async def insert_one(self, doc):
            return None

    monkeypatch.setattr("fraud_synthesis._ensure_scr_indexes", _noop_indexes)
    monkeypatch.setattr("fraud_synthesis._screenings_col", lambda: _Sink())


def test_below_gate_returns_gated_with_no_fraud_card(_no_screening_store):
    # points below gate_min_points, sampling OFF ⇒ deterministic gated result,
    # NO reasoning pass, and crucially NO item_id (⇒ no dispositionable card).
    out = asyncio.run(run_synthesis(
        settings=None, tenant_id="t", app_slug="app", record_id="R1",
        context="ctx", signals={"note": "nothing suspicious"},
        gate_min_points=2, sample_rate=0.0,
    ))
    assert out["gated"] is True
    assert out.get("points", 0) == 0
    assert "item_id" not in out          # no fraud card surfaced to the officer
    assert "fraud_risk" not in out


def test_below_gate_with_one_weak_signal_still_gated(_no_screening_store):
    # A single weak signal (1 pt) under a gate of 2 ⇒ still no card.
    out = asyncio.run(run_synthesis(
        settings=None, tenant_id="t", app_slug="app", record_id="R2",
        context="ctx", signals={"metadata": {"anomalies": ["x"]}},   # 1 point
        gate_min_points=2, sample_rate=0.0,
    ))
    assert out["gated"] is True
    assert out["points"] == 1
    assert "item_id" not in out


# ─────────────────────────────────────────────────────────────────────────────
# F. Capture matrix — only evidence + identity columns are fingerprinted
# ─────────────────────────────────────────────────────────────────────────────
def test_capture_matrix_by_item_role():
    col_roles = {
        "damage_photo_url": {"artifact_role": "evidence"},   # captured
        "headshot_url": {"artifact_role": "identity"},       # captured (to verify)
        "brochure_url": {"artifact_role": "supporting"},     # NOT captured
    }
    targets = fr._fingerprint_targets(col_roles)
    assert set(targets) == {"damage_photo_url", "headshot_url"}
    assert "brochure_url" not in targets
