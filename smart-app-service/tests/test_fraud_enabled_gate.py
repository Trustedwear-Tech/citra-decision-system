"""Phase 0 gate: fraud_active_for_agent — the single ontology-derived signal
that decides whether ANY fraud surface (L2 learning, L3 calibration, the fraud
UI) exists for an app. True iff the autowire wired a consistency_check tool with
url_columns AND a data_source_id binding (i.e. sources.json opted a bound
dataset into fraud_screening).

data_source_id is required so this predicate MATCHES the runtime's
``tools_v2_dispatch._dataset_fraud_active`` — an unbound screen can't resolve
artifacts, so the runtime treats it as inactive; if the publish-side predicate
said True for it, the app card would offer fraud surfaces that never fire
(ontology-review finding O6)."""
from fraud_roles import fraud_active_for_agent


class _T:
    def __init__(self, kind, url_columns=None, data_source_id=None):
        self.kind = kind
        self.url_columns = url_columns
        self.data_source_id = data_source_id


class _Agent:
    def __init__(self, tools_v2):
        self.tools_v2 = tools_v2


def test_active_when_screen_wired_and_bound():
    a = _Agent([_T("consistency_check", url_columns=["defect_photo_url"],
                   data_source_id="insp")])
    assert fraud_active_for_agent(a) is True


def test_inactive_when_screen_wired_but_unbound():
    # url_columns without a data_source_id = the runtime can't resolve artifacts;
    # the publish predicate must agree it's INACTIVE (one definition, both layers).
    a = _Agent([_T("consistency_check", url_columns=["defect_photo_url"])])
    assert fraud_active_for_agent(a) is False


def test_inactive_when_consistency_check_unwired():
    # tool present but ontology never gave it url_columns → screening off
    a = _Agent([_T("consistency_check", url_columns=[], data_source_id="insp")])
    assert fraud_active_for_agent(a) is False


def test_inactive_when_no_consistency_check():
    a = _Agent([_T("mcp"), _T("image_analyze")])
    assert fraud_active_for_agent(a) is False


def test_inactive_on_empty_agent():
    assert fraud_active_for_agent(_Agent([])) is False
    assert fraud_active_for_agent(_Agent(None)) is False


def test_active_if_any_screen_wired_and_bound():
    a = _Agent([_T("consistency_check", url_columns=[]),
                _T("consistency_check", url_columns=["photo_url"],
                   data_source_id="insp")])
    assert fraud_active_for_agent(a) is True
