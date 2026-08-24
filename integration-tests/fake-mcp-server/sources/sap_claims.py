# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""
Fake SAP motor-claims source.

Generates 5,000 deterministic motor-claim rows on first import. The same
random seed is used every run so test assertions are stable.

Decision distribution (matches the integration-test plan):
  approve   72% — 80% under $500, 20% under $50k
  escalate  13% — all > $10k, mostly $10-50k
  reject    15% — fraud / policy lapsed / claimant mismatch

Every row carries seeded PII in ``claimant_name`` and ``adjuster_notes``
so the SamplePackagerNode's PII scrubber has something to fail loudly on
if it regresses.
"""
from __future__ import annotations

import json
import random
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


_SEED = 20260510
_DATA_PATH = Path(__file__).parent.parent / "data" / "motor_claims.json"

_VEHICLES = ["Honda Civic", "Honda Accord", "Toyota Camry", "Ford F-150",
             "Chevrolet Silverado", "Tesla Model 3", "Hyundai Elantra",
             "Volkswagen Jetta", "BMW 3 Series", "Audi A4"]

_REASON_CODES = {
    "approve": ["STD_THRESHOLD", "UNDER_500", "WINDSHIELD_STD", "BUMPER_STD"],
    "escalate": ["HIGH_VALUE", "COLLISION_REVIEW", "MULTI_PARTY", "AMBIGUOUS_FAULT"],
    "reject": ["FRAUD_SIGNAL", "POLICY_LAPSED", "CLAIMANT_MISMATCH", "OUT_OF_SCOPE"],
}

_POLICIES = ["policy_4.2.1", "policy_4.3.2", "policy_5.1", "policy_7.1",
             "policy_7.4", "policy_9.3", "policy_9.5"]

# PII strings deliberately seeded for the scrubber to catch. These look
# real but are entirely synthetic.
_PII_TEMPLATES = [
    "Contact: {name} {surname} <{email}>, phone +1-{phone1}-{phone2}-{phone3}",
    "Notes from adjuster {name}: spoke to claimant at {phone1}-{phone2}-{phone3}",
    "Reached out via {email} on {date}; claimant requested callback",
    "Cleared by underwriter (PAN: {pan}) — no further action",
]
_FIRST_NAMES = ["Alex", "Priya", "Marcus", "Yuki", "Ravi", "Sara", "Chen", "Maya"]
_SURNAMES = ["Patel", "Singh", "Garcia", "Tanaka", "Lopez", "Murphy", "Wong", "Kim"]


_CLAIMS: List[Dict[str, Any]] = []


def _make_pan() -> str:
    """5 letters + 4 digits + 1 letter — synthetic PAN."""
    rng = random.Random(_SEED)
    letters = "ABCDEFGHIJKLMNPQRSTUVWXYZ"
    return (
        "".join(rng.choice(letters) for _ in range(5))
        + "".join(rng.choice("0123456789") for _ in range(4))
        + rng.choice(letters)
    )


def _seed_pii_string(rng: random.Random) -> str:
    name = rng.choice(_FIRST_NAMES)
    surname = rng.choice(_SURNAMES)
    template = rng.choice(_PII_TEMPLATES)
    return template.format(
        name=name,
        surname=surname,
        email=f"{name.lower()}.{surname.lower()}@example.com",
        phone1=rng.randint(200, 999),
        phone2=rng.randint(100, 999),
        phone3=rng.randint(1000, 9999),
        pan=_make_pan(),
        date=(datetime(2024, 1, 1) + timedelta(days=rng.randint(0, 364))).date().isoformat(),
    )


def _generate_claims() -> List[Dict[str, Any]]:
    """Deterministic 5k-row claim set."""
    rng = random.Random(_SEED)
    claims: List[Dict[str, Any]] = []
    target = 5000

    # Budget per decision class.
    decisions = (
        ["approve"] * int(target * 0.72)
        + ["escalate"] * int(target * 0.13)
        + ["reject"] * int(target * 0.15)
    )
    rng.shuffle(decisions)

    base_date = datetime(2024, 1, 1)
    for i, decision in enumerate(decisions):
        # Amount distribution by decision.
        if decision == "approve":
            amt = rng.choices(
                [rng.randint(50, 499), rng.randint(500, 4999), rng.randint(5000, 50000)],
                weights=[0.80, 0.15, 0.05], k=1,
            )[0]
        elif decision == "escalate":
            amt = rng.choices(
                [rng.randint(10_000, 50_000), rng.randint(50_000, 200_000)],
                weights=[0.85, 0.15], k=1,
            )[0]
        else:  # reject
            amt = rng.choices(
                [rng.randint(100, 1_000), rng.randint(1_000, 30_000)],
                weights=[0.45, 0.55], k=1,
            )[0]

        incident_date = base_date + timedelta(days=rng.randint(0, 364))
        reason = rng.choice(_REASON_CODES[decision])
        cited_policy = rng.choice(_POLICIES)

        claimant_name = f"{rng.choice(_FIRST_NAMES)} {rng.choice(_SURNAMES)}"
        adjuster_notes = _seed_pii_string(rng)

        claims.append({
            "claim_id": f"CL-{i:06d}",
            "claim_amount": amt,
            "vehicle": rng.choice(_VEHICLES),
            "claimant_name": claimant_name,
            "incident_date": incident_date.isoformat(),
            "evidence_urls": [
                f"https://acme.example/evidence/{i:06d}_a.jpg",
                f"https://acme.example/evidence/{i:06d}_b.jpg",
            ],
            "status": "closed",
            "decision": decision,
            "amount_paid": amt if decision == "approve" else 0,
            "reason_code": reason,
            "cited_policy": cited_policy,
            "adjuster_notes": adjuster_notes,
        })

    # Cache to disk so other tests can inspect the canonical seed.
    try:
        _DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
        _DATA_PATH.write_text(json.dumps(claims[:50], indent=2))  # sample only
    except OSError:
        pass

    return claims


def reset_sap_claims() -> None:
    global _CLAIMS
    _CLAIMS = _generate_claims()


# Seed on import so tests that don't reset still see data.
reset_sap_claims()


def seed_count() -> int:
    return len(_CLAIMS)


def list_motor_claims(
    *,
    filters: Dict[str, Any],
    max_results: int,
    historical: bool,
    offset: int = 0,
) -> Tuple[List[Dict[str, Any]], int, Optional[int]]:
    """Filter + paginate + return claims.

    Supports filters: status, since, decision, max_amount, min_amount,
    claim_id, date_range (string "YYYY-MM-DD..YYYY-MM-DD").
    """
    rows = _CLAIMS
    if filters:
        rows = [r for r in rows if _row_matches(r, filters)]

    total = len(rows)
    page = rows[offset : offset + max_results]
    next_offset: Optional[int] = (offset + len(page)) if (offset + len(page)) < total else None
    return page, total, next_offset


def _row_matches(row: Dict[str, Any], filters: Dict[str, Any]) -> bool:
    if "status" in filters and row.get("status") != filters["status"]:
        return False
    if "decision" in filters and row.get("decision") != filters["decision"]:
        return False
    if "claim_id" in filters and row.get("claim_id") != filters["claim_id"]:
        return False
    if "max_amount" in filters and row.get("claim_amount", 0) > filters["max_amount"]:
        return False
    if "min_amount" in filters and row.get("claim_amount", 0) < filters["min_amount"]:
        return False
    if "since" in filters:
        try:
            since = filters["since"]
            if row.get("incident_date", "") < since:
                return False
        except (TypeError, ValueError):
            pass
    if "date_range" in filters:
        rng = filters["date_range"]
        if isinstance(rng, str) and ".." in rng:
            lo, hi = rng.split("..", 1)
            d = row.get("incident_date", "")
            if d < lo or d > hi:
                return False
    return True
