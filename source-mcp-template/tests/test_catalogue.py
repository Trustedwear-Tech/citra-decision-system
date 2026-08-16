"""Unit tests for the catalogue dispatcher (list / describe / sample / run_query / execute_action)."""
from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException


@pytest.fixture
def stub_sources(monkeypatch):
    """Replace the in-memory sources map with a curated test fixture."""
    from router import _sources

    sources = {
        "claims_db": {
            "source_id": "claims_db",
            "dept_id": "insurance",
            "org_id": "bajaj",
            "type": "structured",
            "name": "Claims DB",
            "connection": {"type": "sqlite", "env_prefix": "TEST_CLAIMS"},
            "options": {"tables": ["policies", "claims"]},
            "catalogue": {
                "datasets": [
                    {
                        "id": "claims_db.policies",
                        "name": "policies",
                        "kind": "sql",
                        "description": "Master policy table",
                        "columns": [
                            {"name": "policy_id", "type": "VARCHAR", "is_primary_key": True},
                            {"name": "customer_email", "type": "VARCHAR", "pii": True, "semantic_type": "email"},
                            {"name": "premium", "type": "DECIMAL", "semantic_type": "currency_amount"},
                        ],
                        "read_via": {"kind": "sql", "target": "policies"},
                        "write_actions": [
                            {
                                "id": "create_policy",
                                "verb": "create",
                                "sql_template": "INSERT INTO policies (policy_id, premium) VALUES (:policy_id, :premium)",
                                "input_schema": {"required": ["policy_id"]},
                            }
                        ],
                        "row_count_approx": 1234,
                        "samples_redacted": True,
                    }
                ]
            },
        },
        "policies_kb": {
            "source_id": "policies_kb",
            "dept_id": "insurance",
            "org_id": "bajaj",
            "type": "semantic",
            "name": "Policies KB",
        },
    }
    # The introspection TTL cache is keyed by (source_id, dataset_id) — the
    # SAME ids every test here reuses. Clear it alongside the sources map or
    # one test's (possibly empty) introspection leaks into the next.
    import catalogue as cat
    cat._introspect_cache.clear()
    _sources.clear()
    _sources.update(sources)
    yield _sources
    _sources.clear()
    cat._introspect_cache.clear()


def test_list_datasets_includes_explicit_and_synthesised(stub_sources):
    from catalogue import list_datasets

    resp = list_datasets()
    ids = sorted(d.id for d in resp.datasets)
    # claims_db.policies came from explicit catalogue block;
    # policies_kb falls back to single-dataset synthesis.
    assert "claims_db.policies" in ids
    assert "policies_kb" in ids
    assert resp.total == len(ids)


def test_describe_dataset_returns_full_schema(stub_sources):
    from catalogue import describe_dataset

    schema = describe_dataset("claims_db.policies", source_id="claims_db")
    assert schema.kind.value == "sql"
    assert schema.row_count_approx == 1234
    cols = {c.name: c for c in schema.columns}
    assert cols["customer_email"].pii is True
    assert cols["customer_email"].semantic_type == "email"
    assert any(w.id == "create_policy" for w in schema.write_actions)
    assert schema.read_via.target == "policies"


def test_describe_dataset_404(stub_sources):
    from catalogue import describe_dataset

    with pytest.raises(HTTPException) as ei:
        describe_dataset("nonexistent.x", source_id="claims_db")
    assert ei.value.status_code == 404


def test_describe_dataset_search_without_source(stub_sources):
    from catalogue import describe_dataset

    schema = describe_dataset("claims_db.policies")  # no source_id
    assert schema.id == "claims_db.policies"


def test_live_introspection_preserves_declared_semantics(stub_sources, monkeypatch):
    """When the SQL backend IS reachable, introspection owns physical truth
    (type/nullable/PK/FK) but the declared semantic layer the DB can't carry
    (description, distinct_values, sensitivity) must be OVERLAID — else the
    catalogue (and the SmartApp builder) would lose every hand-authored column
    description + enum. Regression guard for the bsphcl builder gap."""
    import catalogue as cat

    # Declared block carries the human/semantic layer (enums + descriptions).
    stub_sources["claims_db"]["catalogue"]["datasets"][0]["columns"] = [
        {"name": "policy_id", "type": "VARCHAR", "is_primary_key": True,
         "description": "Policy identifier"},
        {"name": "status", "type": "VARCHAR", "description": "Lifecycle state",
         "distinct_values": ["active", "lapsed", "cancelled"]},
    ]

    class _FakeConnector:
        # Physical truth from the live DB — NO descriptions/enums (Postgres
        # carries none unless COMMENT ON), and a DIFFERENT type to prove
        # physical truth wins for type while declared wins for semantics.
        def extract_schema(self, conn, table):
            return {"row_count": 7, "columns": [
                {"name": "policy_id", "type": "TEXT", "nullable": False},
                {"name": "status", "type": "TEXT", "nullable": True},
            ]}
        def extract_primary_keys(self, conn, table):
            return ["policy_id"]
        def extract_foreign_keys(self, conn, table):
            return []

    monkeypatch.setattr(cat, "_connector_for_kind", lambda kind: _FakeConnector())

    schema = cat.describe_dataset("claims_db.policies", source_id="claims_db")
    cols = {c.name: c for c in schema.columns}
    # physical truth from introspection
    assert cols["policy_id"].type == "TEXT"
    assert cols["policy_id"].is_primary_key is True
    # declared semantics overlaid (would be lost without the merge)
    assert cols["status"].description == "Lifecycle state"
    assert cols["status"].distinct_values == ["active", "lapsed", "cancelled"]
    assert cols["policy_id"].description == "Policy identifier"


def test_run_query_rejects_non_string_for_sql(stub_sources):
    from catalogue import run_query
    from models import DatasetKind, RunQueryRequest

    req = RunQueryRequest(
        source_id="claims_db", kind=DatasetKind.sql, query={"not": "a string"}
    )
    with pytest.raises(HTTPException) as ei:
        asyncio.run(run_query(req))
    assert ei.value.status_code == 400


def test_run_query_unknown_source_404(stub_sources):
    from catalogue import run_query
    from models import DatasetKind, RunQueryRequest

    req = RunQueryRequest(source_id="nope", kind=DatasetKind.sql, query="SELECT 1")
    with pytest.raises(HTTPException) as ei:
        asyncio.run(run_query(req))
    assert ei.value.status_code == 404


def test_run_query_extension_kinds_are_dispatched_not_501(stub_sources):
    """odata / soql / rest / mongodb are IMPLEMENTED connectors — run_query
    DISPATCHES them (surfacing any connector error on RunQueryResponse.error)
    rather than raising the old 501 'extension point' stub."""
    from catalogue import run_query
    from models import DatasetKind, RunQueryRequest, RunQueryResponse

    for kind in (DatasetKind.odata, DatasetKind.soql, DatasetKind.rest, DatasetKind.mongodb):
        req = RunQueryRequest(source_id="claims_db", kind=kind, query="x")
        try:
            resp = asyncio.run(run_query(req))
        except HTTPException as ei:
            # A different status (e.g. 400 bad query / 404 missing dataset) is
            # acceptable; the point is it is NO LONGER a 501 extension stub.
            assert ei.status_code != 501, f"kind={kind} still raises 501"
            continue
        assert isinstance(resp, RunQueryResponse)


def test_execute_action_unknown_action_404(stub_sources):
    from catalogue import execute_action
    from models import ExecuteActionRequest

    req = ExecuteActionRequest(
        source_id="claims_db",
        dataset_id="claims_db.policies",
        action_id="does_not_exist",
        payload={},
    )
    with pytest.raises(HTTPException) as ei:
        asyncio.run(execute_action(req))
    assert ei.value.status_code == 404


def test_execute_action_missing_required_field_422(stub_sources):
    from catalogue import execute_action
    from models import ExecuteActionRequest

    req = ExecuteActionRequest(
        source_id="claims_db",
        dataset_id="claims_db.policies",
        action_id="create_policy",
        payload={"premium": 1500},  # missing required policy_id
    )
    with pytest.raises(HTTPException) as ei:
        asyncio.run(execute_action(req))
    assert ei.value.status_code == 422


def test_execute_action_dry_run(stub_sources):
    from catalogue import execute_action
    from models import ExecuteActionRequest

    req = ExecuteActionRequest(
        source_id="claims_db",
        dataset_id="claims_db.policies",
        action_id="create_policy",
        payload={"policy_id": "P1", "premium": 1500},
        dry_run=True,
    )
    resp = asyncio.run(execute_action(req))
    assert resp.ok is True
    assert resp.result["dry_run"] is True
    assert resp.result["payload"]["policy_id"] == "P1"


def test_exec_sql_action_pads_omitted_declared_fields(monkeypatch):
    """A partial payload must bind every schema-declared field — omitted ones
    as NULL — so SQLAlchemy doesn't raise InvalidRequestError on a ``:param``
    with no value. Regression for the dispatch-time update_outage_status 500.
    """
    import catalogue
    from models import ExecuteActionRequest, WriteAction

    captured = {}

    class _FakeConn:
        def execute(self, stmt, params):
            captured["params"] = params
            return type("R", (), {"rowcount": 1})()

    class _FakeEngine:
        def begin(self):
            engine = self

            class _Ctx:
                def __enter__(self_inner):
                    return _FakeConn()

                def __exit__(self_inner, *a):
                    return False

            return _Ctx()

    from connectors import sql_connector
    monkeypatch.setattr(sql_connector, "_get_engine", lambda conn: _FakeEngine())

    action = WriteAction(
        id="update_outage_status",
        verb="update",
        sql_template=(
            "UPDATE outages SET status=:status, "
            "end_time=COALESCE(:end_time, end_time), "
            "saidi_minutes=COALESCE(:saidi_minutes, saidi_minutes) "
            "WHERE outage_id=:outage_id"
        ),
        input_schema={
            "required": ["outage_id", "status"],
            "properties": {
                "outage_id": {"type": "string"},
                "status": {"type": "string"},
                "end_time": {"type": "string"},
                "saidi_minutes": {"type": "number"},
            },
        },
    )
    req = ExecuteActionRequest(
        source_id="outage_management",
        dataset_id="outage_management.outages",
        action_id="update_outage_status",
        payload={"outage_id": "OUT-2", "status": "active"},  # dispatch: no end_time/saidi
    )

    result = catalogue._exec_sql_action({"connection": {}}, action, req, {})

    assert result["rowcount"] == 1
    # Omitted-but-declared fields bound to NULL; supplied fields kept their value.
    assert captured["params"] == {
        "outage_id": "OUT-2",
        "status": "active",
        "end_time": None,
        "saidi_minutes": None,
    }


def _capture_sql_params(monkeypatch):
    """Stub the SQL engine and return the dict the executor binds into."""
    captured = {}

    class _FakeConn:
        def execute(self, stmt, params):
            captured["params"] = params
            return type("R", (), {"rowcount": 1})()

    class _FakeEngine:
        def begin(self):
            class _Ctx:
                def __enter__(self_inner):
                    return _FakeConn()

                def __exit__(self_inner, *a):
                    return False

            return _Ctx()

    from connectors import sql_connector
    monkeypatch.setattr(sql_connector, "_get_engine", lambda conn: _FakeEngine())
    return captured


def _decision_action():
    from models import WriteAction

    return WriteAction(
        id="record_credit_decision",
        verb="update",
        sql_template="UPDATE loan_applications SET status=:status, "
                     "decided_by=:decided_by, decided_at=:decided_at, "
                     "note=:note WHERE application_id=:application_id",
        input_schema={
            "required": ["application_id", "status"],
            "properties": {
                "application_id": {"type": "string"},
                "status": {"type": "string"},
                "note": {"type": "string"},
                "decided_by": {"type": "string", "x-citra-fill": "actor"},
                "decided_at": {"type": "string", "format": "date-time"},
            },
        },
    )


def test_exec_sql_action_empty_string_is_null_for_typed_fields(monkeypatch):
    """An empty string for a field that cannot hold one is an omission.

    Regression for a live embed Apply: the agent proposed the write without
    filling decided_at, "" reached a timestamp column and Postgres rejected
    the whole UPDATE. A bare `type: string` keeps "" — clearing text is a
    legitimate edit.
    """
    import catalogue
    from models import ExecuteActionRequest

    captured = _capture_sql_params(monkeypatch)
    req = ExecuteActionRequest(
        source_id="core_banking",
        dataset_id="core_banking.loan_applications",
        action_id="record_credit_decision",
        payload={"application_id": "LN-1", "status": "rejected",
                 "decided_at": "", "note": ""},
    )

    catalogue._exec_sql_action(
        {"connection": {}}, _decision_action(), req,
        {"email": "credit.officer@acme-bank.citra.ai"},
    )

    # date-time cannot hold "" -> NULL, so COALESCE preserves/defaults it.
    assert captured["params"]["decided_at"] is None
    # A plain string can -> the officer's clearing edit survives.
    assert captured["params"]["note"] == ""


def test_exec_sql_action_stamps_actor_from_verified_claims(monkeypatch):
    """x-citra-fill: actor binds the verified caller, overriding the payload.

    The agent proposes a write long before an officer approves it, so it
    cannot know who commits it. Whoever does is stamped here, at the point of
    write — and a payload asserting someone else is overridden, not honoured.
    """
    import catalogue
    from models import ExecuteActionRequest

    captured = _capture_sql_params(monkeypatch)
    req = ExecuteActionRequest(
        source_id="core_banking",
        dataset_id="core_banking.loan_applications",
        action_id="record_credit_decision",
        payload={"application_id": "LN-1", "status": "rejected",
                 "decided_by": "someone.else@example.com"},
    )

    catalogue._exec_sql_action(
        {"connection": {}}, _decision_action(), req,
        {"email": "credit.officer@acme-bank.citra.ai", "user_id": "u1"},
    )

    assert captured["params"]["decided_by"] == "credit.officer@acme-bank.citra.ai"


def test_exec_sql_action_refuses_actor_stamp_without_identity(monkeypatch):
    """No identity in the token + an audited actor field = refuse the write.

    Committing an unattributed decision is worse than not committing one.
    """
    import catalogue
    from models import ExecuteActionRequest

    captured = _capture_sql_params(monkeypatch)
    req = ExecuteActionRequest(
        source_id="core_banking",
        dataset_id="core_banking.loan_applications",
        action_id="record_credit_decision",
        payload={"application_id": "LN-1", "status": "rejected"},
    )

    with pytest.raises(HTTPException) as ei:
        catalogue._exec_sql_action({"connection": {}}, _decision_action(), req, {})
    assert ei.value.status_code == 403
    assert "decided_by" in str(ei.value.detail)
    assert "params" not in captured  # nothing reached the database


def test_exec_sql_action_error_does_not_leak_sql_to_the_caller(monkeypatch):
    """A driver error reaches the officer as a diagnosis, not as a statement.

    SQLAlchemy's str() appends [SQL: ...] and [parameters: ...]; psycopg2 adds
    a LINE fragment. Forwarding that verbatim disclosed the schema to a bank
    officer's browser. The full error still goes to the log.
    """
    import catalogue
    from models import ExecuteActionRequest
    from sqlalchemy import exc as sa_exc

    boom = sa_exc.DataError(
        statement="UPDATE loan_applications SET decided_at=%(decided_at)s ...",
        params={"decided_at": "", "application_id": "LN-1"},
        orig=Exception(
            'invalid input syntax for type timestamp: ""\n'
            "LINE 1: ...SET status='rejected', decided_by='', decided_at=''..."
        ),
    )

    class _FakeEngine:
        def begin(self):
            class _Ctx:
                def __enter__(self_inner):
                    raise boom

                def __exit__(self_inner, *a):
                    return False

            return _Ctx()

    from connectors import sql_connector
    monkeypatch.setattr(sql_connector, "_get_engine", lambda conn: _FakeEngine())

    req = ExecuteActionRequest(
        source_id="core_banking",
        dataset_id="core_banking.loan_applications",
        action_id="record_credit_decision",
        payload={"application_id": "LN-1", "status": "rejected"},
    )
    with pytest.raises(HTTPException) as ei:
        catalogue._exec_sql_action(
            {"connection": {}}, _decision_action(), req,
            {"email": "credit.officer@acme-bank.citra.ai"},
        )

    detail = str(ei.value.detail)
    assert ei.value.status_code == 400          # bad value = the request's fault
    assert "invalid input syntax for type timestamp" in detail  # still diagnostic
    for leaked in ("UPDATE", "LINE 1", "loan_applications", "[SQL", "parameters"):
        assert leaked not in detail, f"{leaked!r} leaked to the caller"


def test_exec_sql_action_unreachable_database_is_503(monkeypatch):
    """A connection failure is not the officer's fault, and its message can
    carry host/credential hints — so it is replaced, not trimmed."""
    import catalogue
    from models import ExecuteActionRequest
    from sqlalchemy import exc as sa_exc

    boom = sa_exc.OperationalError(
        statement="UPDATE loan_applications ...",
        params={},
        orig=Exception(
            'could not connect to server: "10.0.3.14", port 5432, user "citra_rw"'
        ),
    )

    class _FakeEngine:
        def begin(self):
            class _Ctx:
                def __enter__(self_inner):
                    raise boom

                def __exit__(self_inner, *a):
                    return False

            return _Ctx()

    from connectors import sql_connector
    monkeypatch.setattr(sql_connector, "_get_engine", lambda conn: _FakeEngine())

    req = ExecuteActionRequest(
        source_id="core_banking",
        dataset_id="core_banking.loan_applications",
        action_id="record_credit_decision",
        payload={"application_id": "LN-1", "status": "rejected"},
    )
    with pytest.raises(HTTPException) as ei:
        catalogue._exec_sql_action(
            {"connection": {}}, _decision_action(), req,
            {"email": "credit.officer@acme-bank.citra.ai"},
        )

    detail = str(ei.value.detail)
    assert ei.value.status_code == 503
    assert "not reachable" in detail
    for leaked in ("10.0.3.14", "5432", "citra_rw"):
        assert leaked not in detail, f"{leaked!r} leaked to the caller"
