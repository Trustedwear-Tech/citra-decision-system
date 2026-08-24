# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""Ontology-review O1: describe_dataset must carry the DECLARED semantic layer
(column_kind / mime_hint / pii / description / enums / fraud ontology) for EVERY
dataset kind — introspection only ever yields the physical layer, and the
2026-07 seam audit found declared column_kind/mime_hint dropped on every path
(the documented "declare it, the platform does the rest" media contract
silently no-oped unless the column NAME matched the crawler's heuristics).
"""
from __future__ import annotations

import catalogue
import router


_DECLARED = {
    "id": "db.evidence",
    "source_id": "db",
    "kind": "mongodb",   # non-relational: bypasses _live_introspect_full's overlay
    "columns": [
        {
            "name": "meter_seal_evidence",       # name matches NO crawler heuristic
            "physical_name": "meter_seal_evidence",
            "type": "string",
            "column_kind": "image_url",
            "mime_hint": "image/jpeg",
            "pii": True,
            "sensitivity": "confidential",
            "description": "Photo of the meter seal",
            "distinct_values": None,
            "artifact_role": "evidence",
            "reuse_policy": "suspicious",
        }
    ],
    "read_via": {"kind": "mongodb", "target": "evidence"},
}

_SOURCE = {"source_id": "db", "org_id": "acme", "type": "structured",
           "datasets": [_DECLARED]}


def _describe(monkeypatch, introspected_columns):
    """Run describe_dataset with introspection stubbed to return bare physical
    columns (the reachable-backend case that used to DROP declared semantics)."""
    # describe_dataset resolves the source via router.get_source (router owns
    # the store); catalogue._sources is only the no-source_id fallback path.
    monkeypatch.setattr(router, "_sources", {"db": _SOURCE})
    monkeypatch.setattr(
        catalogue, "_cached_introspect",
        lambda source, ds, kind: {
            "columns": introspected_columns, "relationships": [], "row_count": 1,
        },
    )
    return catalogue.describe_dataset("db.evidence", source_id="db")


def test_declared_semantics_survive_live_introspection(monkeypatch):
    # Introspection returns ONLY the physical layer — exactly what a live Mongo
    # sample yields. Every declared semantic field must be overlaid, fill-only.
    schema = _describe(monkeypatch, [
        {"name": "meter_seal_evidence", "physical_name": "meter_seal_evidence",
         "type": "string", "nullable": True},
    ])
    col = schema.columns[0]
    assert col.column_kind == "image_url"          # the media contract
    assert col.mime_hint == "image/jpeg"
    assert col.pii is True
    assert col.sensitivity == "confidential"
    assert col.description == "Photo of the meter seal"
    assert col.artifact_role == "evidence"         # fraud ontology
    assert col.reuse_policy == "suspicious"


def test_overlay_is_fill_only_never_overrides_physical(monkeypatch):
    # If introspection DID produce a value, the declared one must not clobber it.
    schema = _describe(monkeypatch, [
        {"name": "meter_seal_evidence", "physical_name": "meter_seal_evidence",
         "type": "string", "nullable": True, "description": "introspected wins"},
    ])
    assert schema.columns[0].description == "introspected wins"
    assert schema.columns[0].column_kind == "image_url"   # gap still filled


def test_declared_fallback_path_carries_media_contract(monkeypatch):
    # Introspection empty → declared columns[] are the schema; _to_column_spec
    # itself must map column_kind/mime_hint (it previously dropped them even here).
    schema = _describe(monkeypatch, [])
    col = schema.columns[0]
    assert col.column_kind == "image_url"
    assert col.mime_hint == "image/jpeg"
