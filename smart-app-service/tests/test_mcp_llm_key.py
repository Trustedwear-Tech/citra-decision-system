# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""Per-tenant MCP LLM key (Wave 3, slice C).

The customer-side MCP authenticates to the platform LLM proxy with this scoped
key; the proxy holds the real provider key. The key must round-trip through
verify_internal_bearer, carry ONLY the `llm` tool, and be tenant-attributed +
per-tenant-budgeted (subject = mcp:<tenant>).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from internal_bearer import (  # noqa: E402
    InternalBearerError,
    MCP_LLM_KEY_DEFAULT_TTL_SECONDS,
    mint_mcp_llm_key,
    verify_internal_bearer,
)

_KEY = "test-internal-signing-key"


def test_mints_a_verifiable_llm_scoped_tenant_key():
    tok = mint_mcp_llm_key("acme-power", signing_key=_KEY)
    claims = verify_internal_bearer(signing_key=_KEY, bearer=tok)
    # scoped to ONLY the llm tool — the proxy's required_tool="llm" gate passes,
    # and nothing else (no mcp/rag/vision) is authorised by this key.
    assert claims.tools == ["llm"]
    # tenant-attributed (metering) + per-tenant subject (budget)
    assert claims.tenant_id == "acme-power"
    assert claims.subject == "mcp:acme-power"


def test_authorises_llm_but_not_other_tools():
    claims = verify_internal_bearer(
        signing_key=_KEY, bearer=mint_mcp_llm_key("t1", signing_key=_KEY))
    assert "llm" in claims.tools
    assert not claims.allows_rag("any-source")           # not an rag key
    assert not claims.allows_mcp("any-source", "tool")   # not an mcp-tool key


def test_default_ttl_is_bounded():
    import time
    tok = mint_mcp_llm_key("t1", signing_key=_KEY)
    claims = verify_internal_bearer(signing_key=_KEY, bearer=tok)
    remaining = claims.exp - int(time.time())
    assert 0 < remaining <= MCP_LLM_KEY_DEFAULT_TTL_SECONDS + 5


def test_wrong_signing_key_is_rejected():
    tok = mint_mcp_llm_key("t1", signing_key=_KEY)
    with pytest.raises(InternalBearerError):
        verify_internal_bearer(signing_key="a-different-key", bearer=tok)


def test_requires_tenant_id():
    with pytest.raises(InternalBearerError):
        mint_mcp_llm_key("", signing_key=_KEY)
