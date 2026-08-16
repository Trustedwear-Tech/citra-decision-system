"""The declared mime_hint is the content_type fallback.

/resolve_media derives content_type from the media ref's file EXTENSION. A ref
with no recognised extension — a bare object key, a signed URL, an opaque id —
yielded content_type=None, which is precisely the case mime_hint exists for, and
the hint was never consulted: declaring mime_hint: "application/pdf" did nothing.

Extension still wins where present: it describes the object actually stored,
while the hint records what the author expects it to be.
"""
import catalogue
from catalogue import declared_mime_hint


def _registry(monkeypatch, columns):
    ds = {"id": "src.docs", "physical_name": "docs", "kind": "sql", "columns": columns}
    monkeypatch.setattr(catalogue, "_find_dataset", lambda s, d: ({"source_id": "src"}, ds))


def test_hint_returned_for_declared_column(monkeypatch):
    _registry(monkeypatch, [{"name": "scan_ref", "mime_hint": "application/pdf"}])
    assert declared_mime_hint("src", "src.docs", "scan_ref") == "application/pdf"


def test_hint_matches_on_physical_name_too(monkeypatch):
    _registry(monkeypatch, [
        {"name": "Scan", "physical_name": "scan_ref", "mime_hint": "image/png"},
    ])
    assert declared_mime_hint("src", "src.docs", "scan_ref") == "image/png"
    assert declared_mime_hint("src", "src.docs", "Scan") == "image/png"


def test_none_when_column_declares_no_hint(monkeypatch):
    _registry(monkeypatch, [{"name": "scan_ref"}])
    assert declared_mime_hint("src", "src.docs", "scan_ref") is None


def test_none_for_unknown_column(monkeypatch):
    _registry(monkeypatch, [{"name": "other", "mime_hint": "application/pdf"}])
    assert declared_mime_hint("src", "src.docs", "scan_ref") is None


def test_unknown_dataset_returns_none_not_raise(monkeypatch):
    """The caller's own lookup reports a bad source/dataset; this helper must not
    turn a content-type nicety into a 500."""
    def _boom(s, d):
        raise KeyError("no such dataset")
    monkeypatch.setattr(catalogue, "_find_dataset", _boom)
    assert declared_mime_hint("src", "nope", "c") is None
