"""Engine dispatch — odata / soql / rest.

These :data:`models.DatasetKind` values are wired through the shared dispatcher.
The odata, soql and rest connectors are now IMPLEMENTED (no longer placeholders),
so against an UNCONFIGURED source they return a clean, non-None ``error`` on the
:class:`ExecutionResult` (the connector fails on missing connection/auth) rather
than the old ``"not_implemented"`` sentinel — and crucially never RAISE past the
``query_engine`` wrapper. This surfaced-error contract is what Phase B (smart-app
runtime, enterprise_search) relies on to show a clean failure upstream.
"""
from __future__ import annotations

import asyncio

import pytest


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


@pytest.mark.parametrize("kind", ["odata", "soql", "rest"])
def test_engine_dispatch_surfaces_error_never_raises(kind):
    from query_engine import execute, ExecutionResult

    result = _run(
        execute(
            kind=kind,
            source={"source_id": "s1", "connection": {}},
            query={"entity": "X"} if kind != "soql" else "SELECT Id FROM Account",
            row_limit=10,
            read_via={},
        )
    )
    assert isinstance(result, ExecutionResult)
    assert result.rows == []
    # Implemented connectors surface a real error against an unconfigured source;
    # the contract is "non-None error, no exception escapes", not a fixed string.
    assert result.error, f"expected a surfaced error for kind={kind!r}, got None"
    assert result.sql_used is None


def test_engine_still_raises_for_unknown_kind():
    from query_engine import execute

    with pytest.raises(ValueError):
        _run(
            execute(
                kind="quantum",
                source={"source_id": "s1"},
                query="x",
                row_limit=10,
            )
        )
