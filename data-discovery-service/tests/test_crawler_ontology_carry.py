# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""What describe emits must reach the catalogue — or be dropped deliberately.

data-discovery-service/models.py declares no model_config, so pydantic's default
extra='ignore' silently discards any describe key with no matching field. That
mechanism already bit once (see the note at models.py:93-95). These pin the
fields it bit again.

Deliberate drops are NOT tested here: the write_actions execution details
(method / endpoint / sql_template / key_fields / idempotency_key_field /
requires_csrf) are the MCP's business and nothing downstream consumes them.
roles_allowed_write is different — it is AUTHZ the builder must see.
"""
from models import CatalogueEntry, CatalogueWriteAction


def test_write_action_carries_roles_allowed_write():
    """The MCP really enforces this allow-list (auth.py:250-290). Dropping it
    let the builder publish an app whose users are structurally barred from the
    action — surfacing only at runtime as an unexplained 403."""
    w = CatalogueWriteAction(
        id="approve_claim", verb="update",
        roles_allowed_write=["dept_admin", "org_admin"],
    )
    assert w.roles_allowed_write == ["dept_admin", "org_admin"]


def test_write_action_roles_default_is_empty_meaning_mcp_default():
    """Empty = "use the MCP's DEFAULT_WRITE_ROLES (dept_admin+)", NOT everyone."""
    w = CatalogueWriteAction(id="a", verb="update")
    assert w.roles_allowed_write == []


def test_entry_separates_crawl_time_from_source_data_freshness():
    """Two facts, two fields. The crawler overwrote the identically-named
    last_refreshed_at with utcnow(), so the source's declared data freshness was
    unrecoverable and no consumer could tell "this data is six months stale"
    from "we crawled it five minutes ago"."""
    e = CatalogueEntry(
        tenant_id="t", source_id="s", dataset_id="s.d", name="d", physical_name="d", kind="sql",
        source_last_refreshed_at="2026-01-15T00:00:00Z",
    )
    assert e.source_last_refreshed_at == "2026-01-15T00:00:00Z"
    # our crawl stamp is still present and independent
    assert e.last_refreshed_at is not None
    assert str(e.last_refreshed_at) != e.source_last_refreshed_at


def test_entry_carries_samples_redacted():
    """Without it a consumer reasoning over sample rows can't tell PII-masked
    values from raw ones."""
    e = CatalogueEntry(
        tenant_id="t", source_id="s", dataset_id="s.d", name="d", physical_name="d", kind="sql",
        samples_redacted=True,
    )
    assert e.samples_redacted is True


def test_samples_redacted_defaults_unknown_not_false():
    """None = the MCP didn't say. False would assert "these are raw values",
    which we don't know."""
    e = CatalogueEntry(
        tenant_id="t", source_id="s", dataset_id="s.d", name="d", physical_name="d", kind="sql",
    )
    assert e.samples_redacted is None
    assert e.source_last_refreshed_at is None
