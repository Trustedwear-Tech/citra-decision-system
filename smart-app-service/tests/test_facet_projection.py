"""CS-02 + the wiring-gap alarm — a facet column that no panel selects.

Observed on acme-bank (2026-08-06, live prod): the `sourcing_channel` facet
column existed on the bound dataset, so CS-01 passed, but the review panel's
`columns` list omitted it. Every case derived `sourcing_channel:__unknown`, so
clause C-002 (scope `sourcing_channel:dsa`) — the judgement the customer
evidence pack is built on — could never be retrieved. It fired 19/19 when the
harness called the API with the full record and 0/1 through the Decision App.
The only signal was a drift WARNING indistinguishable from routine ontology
drift.

Two defences, pinned here: fail the publish, and if one ever slips through,
make the runtime say WIRING GAP rather than "undeclared value".
"""
from __future__ import annotations

import logging
from types import SimpleNamespace

from case_signature import derive_facets
from publish_validators import validate_case_signature_projection

CHANNEL = {"family": "sourcing_channel", "kind": "enum",
           "from_column": "sourcing_channel",
           "values": ["dsa", "branch", "digital", "bancassurance"]}


def _app(panel_columns, facets=(CHANNEL,)):
    panel = SimpleNamespace(id="app_queue", columns=list(panel_columns)
                            if panel_columns is not None else None,
                            data_source_id="loans")
    return SimpleNamespace(
        pages=[SimpleNamespace(panels=[panel])],
        case_signature=SimpleNamespace(facets=[SimpleNamespace(**f) for f in facets]),
    )


def _cs02(errs):
    return [e for e in errs if e["rule_id"] == "CS-02"]


# --- publish gate ----------------------------------------------------------

def test_facet_column_missing_from_the_projection_blocks_publish():
    """The exact acme-bank shape."""
    app = _app(["application_id", "product", "amount_requested", "status"])
    errs = _cs02(validate_case_signature_projection(app))
    assert len(errs) == 1
    assert "sourcing_channel" in errs[0]["reason"]
    assert errs[0]["code"] == "case_signature_column_not_projected"


def test_projected_column_passes():
    app = _app(["application_id", "sourcing_channel", "status"])
    assert _cs02(validate_case_signature_projection(app)) == []


def test_physical_name_split_is_tolerated():
    """Facets may be authored against a dotted logical name."""
    facet = dict(CHANNEL, from_column="loans.sourcing_channel")
    app = _app(["sourcing_channel"], facets=(facet,))
    assert _cs02(validate_case_signature_projection(app)) == []


def test_a_panel_selecting_the_whole_row_makes_the_check_inconclusive():
    """No `columns` on a row-reading panel = selects everything. We do not fail
    on a guess."""
    app = _app(None)
    assert _cs02(validate_case_signature_projection(app)) == []


def test_signal_facets_are_exempt():
    """Derived from screening signals, not from a column."""
    app = _app(["application_id"],
               facets=({"family": "exif", "kind": "signal", "signal_id": "s1"},))
    assert _cs02(validate_case_signature_projection(app)) == []


def test_no_case_signature_is_not_an_error():
    app = SimpleNamespace(pages=[], case_signature=None)
    assert validate_case_signature_projection(app) == []


def test_age_band_checks_both_columns():
    facet = {"family": "age", "kind": "age_band",
             "from_columns": ["applied_at", "decided_at"]}
    app = _app(["applied_at"], facets=(facet,))
    errs = _cs02(validate_case_signature_projection(app))
    assert len(errs) == 1 and "decided_at" in errs[0]["reason"]


# --- runtime alarm ---------------------------------------------------------

def test_absent_column_is_reported_as_a_wiring_gap_not_drift(caplog):
    """An absent column and an undeclared value both yield __unknown, but they
    need different fixes, so they must not share one message."""
    sig = {"facets": [CHANNEL]}
    with caplog.at_level(logging.ERROR):
        facets, unknown = derive_facets({"application_id": "L-1"}, sig)
    assert "sourcing_channel:__unknown" in facets
    assert unknown == ["sourcing_channel"]
    assert "WIRING GAP" in caplog.text
    assert "ABSENT" in caplog.text


def test_present_but_undeclared_value_is_still_reported_as_drift(caplog):
    sig = {"facets": [CHANNEL]}
    with caplog.at_level(logging.WARNING):
        facets, unknown = derive_facets({"sourcing_channel": "telesales"}, sig)
    assert "sourcing_channel:__unknown" in facets
    assert unknown == ["sourcing_channel"]
    assert "ontology drift" in caplog.text
    assert "WIRING GAP" not in caplog.text


def test_present_column_with_declared_value_resolves():
    facets, unknown = derive_facets({"sourcing_channel": "dsa"},
                                    {"facets": [CHANNEL]})
    assert "sourcing_channel:dsa" in facets
    assert unknown == []


def test_column_present_but_null_is_drift_not_a_wiring_gap(caplog):
    """One case with no channel recorded is a data gap; the column IS wired."""
    with caplog.at_level(logging.ERROR):
        facets, _ = derive_facets({"sourcing_channel": None},
                                  {"facets": [CHANNEL]})
    assert "sourcing_channel:__unknown" in facets
    assert "WIRING GAP" not in caplog.text
