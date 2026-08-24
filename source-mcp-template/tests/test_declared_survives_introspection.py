# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""Declared sources.json intent must survive a REACHABLE backend.

The 2026-07 ontology fix rescued the semantic layer (descriptions, enums,
artifact_role/reuse_policy, column_kind/mime_hint, pii) onto the live-introspection
path, but stopped at the boundary of "physical truth": the declared column `name`,
`is_primary_key`, `is_foreign_key` and `foreign_ref` were still overwritten by
whatever introspection returned. That is backwards — those are exactly the fields
introspection CANNOT supply for a view, a Mongo collection, a parquet/DuckDB
dataset, or a purely logical FK, which is why the doc tells authors to declare
them. They survived only when the backend was unreachable.

Also covers the loader gaps: supports_history was absent from _flatten's
passthrough (so registration could never publish true), and unknown keys are now
reported instead of silently ignored.
"""
import logging

import catalogue
from catalogue import _live_introspect_full
from router import _flatten, _validate_selected


# ── declared keys + name survive live introspection ────────────────────────

def _ds_with_declared():
    return {
        "id": "src.orders_view",
        "physical_name": "orders_view",
        "columns": [
            {
                "physical_name": "order_id",
                "name": "Order Number",          # BA's display name
                "is_primary_key": True,          # a VIEW has no DB-level PK
                "description": "the order key",
                "artifact_role": "identity",
            },
            {
                "physical_name": "customer_id",
                "is_foreign_key": True,          # logical FK, no constraint
                "foreign_ref": "src.customers.id",
            },
        ],
    }


class _ReachableBackend:
    """A REACHABLE backend that returns bare name+type and NO keys — a view, a
    parquet dataset, or Postgres without COMMENT ON / constraints. This is the
    case that used to destroy declared intent: the merge consulted only these
    introspected columns, so declared values survived ONLY when extract_schema
    raised (i.e. only when the backend was DOWN)."""

    def __init__(self, cols):
        self._cols = cols

    def extract_schema(self, conn, table):
        return {"columns": self._cols, "row_count": 1}

    def extract_primary_keys(self, conn, table):
        return []          # no DB-level PK — the author declared it instead

    def extract_foreign_keys(self, conn, table):
        return []          # no DB-level FK — the author declared it instead


def _introspect(monkeypatch, ds, raw_cols):
    """Drive the REAL _live_introspect_full, faking only the connector."""
    monkeypatch.setattr(catalogue, "_connector_for_kind",
                        lambda kind: _ReachableBackend(raw_cols))
    source = {"connection": {"table": "orders_view"}}

    class _Kind:
        value = "sql"
    return _live_introspect_full(source, ds, _Kind())["columns"]


def test_declared_primary_key_survives_when_introspection_finds_none(monkeypatch):
    cols = _introspect(monkeypatch, _ds_with_declared(), [
        {"name": "order_id", "type": "text"},
        {"name": "customer_id", "type": "text"},
    ])
    by = {c["physical_name"]: c for c in cols}
    assert by["order_id"]["is_primary_key"] is True, "declared PK must survive a reachable backend"
    assert by["customer_id"]["is_foreign_key"] is True
    assert by["customer_id"]["foreign_ref"] == "src.customers.id"


def test_declared_column_name_survives_so_display_name_can_reach_the_catalogue(monkeypatch):
    cols = _introspect(monkeypatch, _ds_with_declared(), [{"name": "order_id", "type": "text"}])
    # The crawler derives display_name = name if name != physical_name else None,
    # so forcing name==physical here made display_name permanently null.
    assert cols[0]["name"] == "Order Number"
    assert cols[0]["physical_name"] == "order_id"


def test_semantic_overlay_still_applies(monkeypatch):
    cols = _introspect(monkeypatch, _ds_with_declared(), [{"name": "order_id", "type": "text"}])
    assert cols[0]["artifact_role"] == "identity"
    assert cols[0]["description"] == "the order key"


def test_undeclared_column_still_defaults_name_to_physical(monkeypatch):
    cols = _introspect(monkeypatch, {"id": "s.t", "physical_name": "t", "columns": []},
                       [{"name": "amount", "type": "numeric"}])
    assert cols[0]["name"] == "amount"
    assert cols[0]["is_primary_key"] is False


def test_introspected_key_still_wins_when_nothing_declared(monkeypatch):
    cols = _introspect(monkeypatch, {"id": "s.t", "physical_name": "t", "columns": []},
                       [{"name": "id", "type": "text", "is_primary_key": True}])
    assert cols[0]["is_primary_key"] is True


def test_declared_column_matching_nothing_is_reported(monkeypatch, caplog):
    """Overlay keys on an EXACT physical-name match, so a typo — or Postgres
    folding an unquoted identifier to lower case — silently discarded the whole
    declared column (artifact_role, column_kind, description and all)."""
    ds = {
        "id": "s.t", "physical_name": "t",
        "columns": [{"physical_name": "Consumer_ID", "artifact_role": "identity"}],
    }
    with caplog.at_level(logging.WARNING):
        _introspect(monkeypatch, ds, [{"name": "consumer_id", "type": "text"}])
    assert "Consumer_ID" in caplog.text
    assert "NO introspected column" in caplog.text


# ── loader: supports_history + unknown-key reporting ───────────────────────

def test_flatten_carries_supports_history():
    """It was absent from the passthrough list, so registration.py's
    source.get("supports_history", False) was unconditionally False — the flag
    was undeclarable from sources.json no matter what the author wrote."""
    flat = _flatten({"source_id": "s", "type": "structured", "supports_history": True})
    assert flat["supports_history"] is True


def test_flatten_defaults_supports_history_absent():
    flat = _flatten({"source_id": "s", "type": "structured"})
    assert "supports_history" not in flat  # absent → registration's False default


def test_validate_selected_reports_every_problem_not_just_the_first():
    """The loader's per-source check. It replaced a hand-written allow-list of
    known keys — that list worked, but it was a SECOND definition of the schema
    and would drift from registry_models.py the first time someone added a field.
    One definition now."""
    problems = _validate_selected({
        "source_id": "s", "type": "structured", "dept_id": "d", "org_id": "o",
        "name": "n", "description": "x", "connection": {"env_prefix": "P"},
        "artifact_rolez": "identity",                     # top-level typo
        "datasets": [{
            "id": "s.t",
            "columns": [{"name": "photo", "artifact_roles": "evidence"}],  # column typo
        }],
    })
    blob = " ".join(problems)
    assert "artifact_rolez" in blob
    assert "artifact_roles" in blob


def test_validate_selected_passes_a_real_shaped_source():
    assert _validate_selected({
        "source_id": "s", "type": "structured", "dept_id": "d", "org_id": "o",
        "name": "n", "description": "d",
        "connection": {"env_prefix": "P"},
        "is_demo": True, "created_at": "2026-01-01",     # real registry metadata
        "datasets": [{"id": "s.t", "columns": [
            {"name": "c", "artifact_role": "evidence", "column_kind": "image_url"},
        ]}],
    }) == []
