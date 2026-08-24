# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""Unit tests for publish-time anchor_read derivation (anchor_derivation.py).

Pure — a fake catalogue fetch stands in for data-discovery. Pins the design:
key_field comes from the action's required id INPUT (works with no primary key);
is_primary_key only corroborates; fail-loud is scoped to unguardable mutates.
"""
import pytest

from anchor_derivation import AnchorDerivationError, derive_anchor_reads


class _Action:
    def __init__(self, name, input_schema, data_bindings=None, anchor_read=None):
        self.name = name
        self.input_schema = input_schema
        self.data_bindings = data_bindings
        self.anchor_read = anchor_read


class _Tool:
    def __init__(self, kind, source_id=None, dataset_id=None, action_id=None):
        self.kind = kind
        self.source_id = source_id
        self.dataset_id = dataset_id
        self.action_id = action_id


class _Agent:
    def __init__(self, actions, tools_v2=None):
        self.actions = actions
        self.tools_v2 = tools_v2 or []


def _schema(*required):
    return {"type": "object", "required": list(required),
            "properties": {r: {"type": "string"} for r in required}}


def _cat(cols, kind="sql", write_actions=None):
    return {"kind": kind, "columns": cols, "write_actions": write_actions or []}


def _fetch(mapping):
    async def f(src, ds):
        return mapping.get((src, ds))
    return f


@pytest.mark.asyncio
async def test_derives_from_pk_via_tools_v2_read():
    action = _Action("route_complaint", _schema("complaint_id"))
    agent = _Agent([action], tools_v2=[
        _Tool("mcp", "field_operations", "field_operations.complaints"),
        _Tool("mcp_action", "field_operations", "field_operations.complaints", "route"),
    ])
    mapping = {("field_operations", "field_operations.complaints"): _cat(
        [{"name": "complaint_id", "is_primary_key": True}, {"name": "status"}],
        write_actions=[{"id": "route", "verb": "update"}])}

    warns = await derive_anchor_reads(agent, fetch_entry=_fetch(mapping), mode="enforce")
    assert warns == []
    ar = action.anchor_read
    assert ar.source_id == "field_operations"
    assert ar.dataset_id == "field_operations.complaints"
    assert ar.key_field == "complaint_id"
    assert ar.kind == "sql"


@pytest.mark.asyncio
async def test_derives_when_table_has_no_primary_key():
    """A logical key column with is_primary_key=False still yields an anchor."""
    action = _Action("route", _schema("complaint_id"))
    agent = _Agent([action], tools_v2=[
        _Tool("mcp", "erp", "erp.complaints"),
        _Tool("mcp_action", "erp", "erp.complaints", "route")])
    mapping = {("erp", "erp.complaints"): _cat(
        [{"name": "complaint_id", "is_primary_key": False}, {"name": "status"}],
        write_actions=[{"id": "route", "verb": "update"}])}

    warns = await derive_anchor_reads(agent, fetch_entry=_fetch(mapping), mode="enforce")
    assert warns == []
    assert action.anchor_read.key_field == "complaint_id"


@pytest.mark.asyncio
async def test_respects_existing_anchor_read():
    sentinel = object()
    action = _Action("route", _schema("complaint_id"), anchor_read=sentinel)
    agent = _Agent([action], tools_v2=[_Tool("mcp", "erp", "erp.c")])
    await derive_anchor_reads(agent, fetch_entry=_fetch({}), mode="enforce")
    assert action.anchor_read is sentinel  # untouched


@pytest.mark.asyncio
async def test_no_required_id_is_noop():
    action = _Action("summarize", {"type": "object", "properties": {}})
    agent = _Agent([action], tools_v2=[_Tool("mcp", "erp", "erp.c")])
    warns = await derive_anchor_reads(agent, fetch_entry=_fetch({}), mode="enforce")
    assert warns == [] and action.anchor_read is None


@pytest.mark.asyncio
async def test_unguardable_mutate_raises_in_enforce():
    """Input id doesn't map to the mutated table's key → cannot guard → fail."""
    action = _Action("update_ticket", _schema("ticket_ref"))
    agent = _Agent([action], tools_v2=[
        _Tool("mcp_action", "erp", "erp.tickets", "update_ticket")])
    mapping = {("erp", "erp.tickets"): _cat(
        [{"name": "row_uuid", "is_primary_key": True}, {"name": "subject"}],
        write_actions=[{"id": "update_ticket", "verb": "update"}])}

    with pytest.raises(AnchorDerivationError) as ei:
        await derive_anchor_reads(agent, fetch_entry=_fetch(mapping), mode="enforce")
    assert "erp.tickets" in str(ei.value)


@pytest.mark.asyncio
async def test_unguardable_mutate_warns_in_warn_mode():
    action = _Action("update_ticket", _schema("ticket_ref"))
    agent = _Agent([action], tools_v2=[
        _Tool("mcp_action", "erp", "erp.tickets", "update_ticket")])
    mapping = {("erp", "erp.tickets"): _cat(
        [{"name": "row_uuid", "is_primary_key": True}],
        write_actions=[{"id": "update_ticket", "verb": "update"}])}

    warns = await derive_anchor_reads(agent, fetch_entry=_fetch(mapping), mode="warn")
    assert len(warns) == 1 and "erp.tickets" in warns[0]
    assert action.anchor_read is None


@pytest.mark.asyncio
async def test_create_write_needs_no_anchor():
    """A create-from-scratch write with an unmatched id does NOT fail."""
    action = _Action("open_case", _schema("new_case_id"))
    agent = _Agent([action], tools_v2=[
        _Tool("mcp_action", "erp", "erp.cases", "open_case")])
    mapping = {("erp", "erp.cases"): _cat(
        [{"name": "case_pk", "is_primary_key": True}],
        write_actions=[{"id": "open_case", "verb": "create"}])}

    warns = await derive_anchor_reads(agent, fetch_entry=_fetch(mapping), mode="enforce")
    assert warns == [] and action.anchor_read is None


@pytest.mark.asyncio
async def test_catalogue_unreachable_never_fails():
    action = _Action("update_ticket", _schema("ticket_ref"))
    agent = _Agent([action], tools_v2=[
        _Tool("mcp_action", "erp", "erp.tickets", "update_ticket")])
    # fetch returns None for everything → cannot judge → no fail, no anchor
    warns = await derive_anchor_reads(agent, fetch_entry=_fetch({}), mode="enforce")
    assert warns == [] and action.anchor_read is None


@pytest.mark.asyncio
async def test_mode_off_is_noop():
    action = _Action("update_ticket", _schema("ticket_ref"))
    agent = _Agent([action], tools_v2=[
        _Tool("mcp_action", "erp", "erp.tickets", "update_ticket")])
    mapping = {("erp", "erp.tickets"): _cat(
        [{"name": "row_uuid", "is_primary_key": True}],
        write_actions=[{"id": "update_ticket", "verb": "update"}])}
    warns = await derive_anchor_reads(agent, fetch_entry=_fetch(mapping), mode="off")
    assert warns == [] and action.anchor_read is None
