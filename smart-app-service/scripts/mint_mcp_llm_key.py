# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""Mint a per-tenant LLM key for a customer-side dept-MCP (Wave 3, slice C).

Provisioning step: the customer-side MCP points ``LLM_BASE_URL`` at the platform
LLM proxy (``https://<platform>/smart-app/internal/llm/v1``) and uses the key
this prints as ``LLM_API_KEY``. The real upstream provider key stays server-side
in the proxy (from Vault) — the customer estate holds only this scoped,
tenant-attributed key. The proxy meters every call to the tenant and caps LLM
spend per tenant (per-subject budget), so billing + runaway-loop protection are
enforced at one point.

Usage:
  SMART_APP_INTERNAL_SIGNING_KEY=<key> \
    python scripts/mint_mcp_llm_key.py <tenant_id> [ttl_days]

NOTE: the MCP's planner model must be in the proxy's LLM_PROXY_ALLOWED_MODELS
allowlist, or the relay rejects it.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from internal_bearer import mint_mcp_llm_key, MCP_LLM_KEY_DEFAULT_TTL_SECONDS


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: mint_mcp_llm_key.py <tenant_id> [ttl_days]", file=sys.stderr)
        sys.exit(2)
    tenant_id = sys.argv[1].strip()
    ttl_seconds = (
        int(sys.argv[2]) * 86400 if len(sys.argv) > 2 else MCP_LLM_KEY_DEFAULT_TTL_SECONDS
    )
    signing_key = os.getenv("SMART_APP_INTERNAL_SIGNING_KEY", "")
    if not signing_key:
        print("SMART_APP_INTERNAL_SIGNING_KEY is not set", file=sys.stderr)
        sys.exit(2)

    token = mint_mcp_llm_key(tenant_id, signing_key=signing_key, ttl_seconds=ttl_seconds)
    # Print ONLY the token on stdout so it pipes cleanly into config/secret tooling.
    print(token)
    print(
        f"# per-tenant MCP LLM key for tenant={tenant_id}, ttl={ttl_seconds // 86400}d\n"
        f"# set on the MCP:  LLM_BASE_URL=https://<platform>/smart-app/internal/llm/v1  "
        f"LLM_API_KEY=<the line above>",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
