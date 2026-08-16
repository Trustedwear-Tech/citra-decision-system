# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""
Fake policies RAG source.

Holds 50 short policy documents in memory; ``search_policies`` does a
brain-dead substring match (good enough for integration tests — the
real RAG service is exercised separately in the unit tests). The chunk
shape mirrors what source-mcp-template/rag/semantic_engine.py returns.
"""
from __future__ import annotations

from typing import Any, Dict, List


_POLICIES: List[Dict[str, Any]] = []


_POLICY_CONTENT = {
    "policy_4.2.1": (
        "Auto-approve threshold for motor claims: claims under $500 USD "
        "are auto-approved if the policy is active, claimant is verified, "
        "and no fraud signal has been raised. Adjusters should not "
        "manually triage claims under this threshold."
    ),
    "policy_4.3.2": (
        "Single-event windshield/glass replacement claims under $1,000 "
        "are auto-approved subject to policy 4.2.1's verification checks."
    ),
    "policy_5.1": (
        "Standard collision claims between $500 and $10,000 are routed "
        "to a junior adjuster for review within 48 hours."
    ),
    "policy_7.1": (
        "High-value claims (over $10,000 USD) MUST be reviewed by a "
        "senior reviewer. Automatic approval is not permitted regardless "
        "of policy status. Required artifacts: photos, police report (if "
        "applicable), claimant statement."
    ),
    "policy_7.4": (
        "Multi-party collision claims require evidence from all involved "
        "parties before any decision is rendered. SLA: 5 business days."
    ),
    "policy_9.3": (
        "Fraud signals: more than 5 claims from the same claimant within "
        "30 days, claim amount > 200% of vehicle's standard repair cost, "
        "claimant evading callbacks. ANY ONE triggers a fraud review and "
        "auto-rejection of the current claim until cleared."
    ),
    "policy_9.5": (
        "Policy lapse on claim incident date results in claim rejection "
        "with reason code POLICY_LAPSED. Customer is notified by email "
        "within 24 hours."
    ),
}


def reset_policies() -> None:
    global _POLICIES
    _POLICIES = []
    for pid, body in _POLICY_CONTENT.items():
        _POLICIES.append({
            "id": pid,
            "text": body,
            "doc_type": "policy",
            "classification": "internal",
            "source_id": "policies_rag",
        })


reset_policies()


def search_policies(query: str, *, top_k: int = 5) -> List[Dict[str, Any]]:
    """Brain-dead substring match — good enough for integration tests."""
    if not query:
        return _POLICIES[:top_k]
    q = query.lower()
    hits = []
    for p in _POLICIES:
        if q in p["text"].lower() or q in p["id"].lower():
            hits.append(dict(p, score=1.0))
        elif any(word in p["text"].lower() for word in q.split()):
            hits.append(dict(p, score=0.5))
    hits.sort(key=lambda h: h["score"], reverse=True)
    return hits[:top_k]
