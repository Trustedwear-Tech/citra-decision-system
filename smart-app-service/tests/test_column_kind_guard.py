"""artifact_role on a NON-media column must not be auto-wired as media.

A fingerprint target is auto-wired into the screen's ``url_columns``, so the
runtime resolves it via /resolve_media. A column declaring an artifact_role AND
an explicit non-media ``column_kind`` is a contradiction: the runtime would try
to presign a plain text value and fail mid-run. Catch it at publish, where the
author can act, and say which sources.json line is wrong.

The asymmetry that matters: an ABSENT column_kind is not a contradiction, it is
simply undeclared — only a handful of live columns declare one, so treating
absent as "plain" would silently disable fraud screening across the fleet.
"""
import logging

from fraud_roles import _fingerprint_targets, roles_from_catalogue_columns


def test_evidence_on_plain_column_is_excluded_and_reported(caplog):
    roles = {"notes": {"artifact_role": "evidence", "column_kind": "plain"}}
    with caplog.at_level(logging.WARNING):
        targets = _fingerprint_targets(roles)
    assert targets == {}, "a plain column must never be wired as media"
    assert "notes" in caplog.text
    assert "column_kind" in caplog.text


def test_absent_column_kind_is_still_fingerprinted():
    """Undeclared ≠ plain. Most live columns declare no column_kind; treating
    absent as non-media would silently disable screening everywhere."""
    roles = {"photo_url": {"artifact_role": "evidence"}}
    assert "photo_url" in _fingerprint_targets(roles)


def test_media_kinds_are_fingerprinted():
    for kind in ("url", "image_url", "document_url", "file"):
        roles = {"c": {"artifact_role": "evidence", "column_kind": kind}}
        assert "c" in _fingerprint_targets(roles), f"{kind} is media"


def test_identity_role_gets_the_same_guard(caplog):
    roles = {"pan": {"artifact_role": "identity", "column_kind": "plain"}}
    with caplog.at_level(logging.WARNING):
        assert _fingerprint_targets(roles) == {}


def test_supporting_role_never_fingerprinted_regardless_of_kind():
    roles = {"c": {"artifact_role": "supporting", "column_kind": "image_url"}}
    assert _fingerprint_targets(roles) == {}


def test_roles_extractor_carries_column_kind():
    cols = [{"name": "photo", "artifact_role": "evidence", "column_kind": "image_url"}]
    out = roles_from_catalogue_columns(cols)
    assert out["photo"]["column_kind"] == "image_url"


def test_roles_extractor_omits_absent_column_kind():
    cols = [{"name": "photo", "artifact_role": "evidence"}]
    out = roles_from_catalogue_columns(cols)
    assert "column_kind" not in out["photo"]


def test_column_kind_alone_does_not_create_a_role_entry():
    """column_kind is not a fraud opt-in; only artifact_role/reuse_policy are."""
    cols = [{"name": "photo", "column_kind": "image_url"}]
    assert roles_from_catalogue_columns(cols) == {}
