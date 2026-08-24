# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""
citra-service-utils — PII / semantic-type column classifier
============================================================
Lightweight, dependency-free classifier for tagging a data column with a
coarse semantic type and a PII flag. Shared so every service that profiles a
data source — the data-discovery-service crawler (building ``data_catalogue``)
and the Citra Workflow Engine's Dept Data Flow ingestion (building
``dept_sources``) — applies the SAME rules and never drifts.

It runs over:
  • the column NAME (case-insensitive substring match)
  • the column SAMPLE VALUES (regex match against canonical patterns)

Heavy NER / Presidio integration is intentionally NOT a hard dependency —
most enterprise installs run on-prem with limited extra packages. When
Presidio is available, callers can wire it in by overriding ``classify_value``.

Returned semantic_type values (extend freely):
  email, phone, aadhaar, pan, gstin, ifsc, credit_card, ip_address,
  pincode, dob, person_name, address, account_number, claim_id,
  customer_id, policy_id, currency_amount, percentage, url,
  ssn, ein, aba_routing, zip   (US types — additive; India types retained)
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Optional, Tuple

# ---------------------------------------------------------------------------
# Regex catalogue. Keep these tight — false positives on PII flags are
# more dangerous than false negatives at the catalogue layer (a flagged
# column is still queryable, it just gets redacted in samples).
# ---------------------------------------------------------------------------

_PATTERNS: list[tuple[str, re.Pattern, bool]] = [
    # (semantic_type, regex, is_pii)
    ("email",          re.compile(r"^[\w.+-]+@[\w-]+\.[\w.-]+$", re.I),                 True),
    # --- US PII (additive — see docs/fraud-detection-primitives-plan.md §4b) ---
    # SSN 3-2-4 (dashed form only; bare 9-digit runs collide with too much).
    ("ssn",            re.compile(r"^\d{3}-\d{2}-\d{4}$"),                              True),
    # EIN 2-7 (dashed form only; distinct dash position from SSN).
    ("ein",            re.compile(r"^\d{2}-\d{7}$"),                                    True),
    # US ZIP+4 (5-4). Placed before phone: the loose phone regex would otherwise
    # swallow the dashed 10-char form. Bare 5-digit ZIP is handled lower down.
    ("zip",            re.compile(r"^\d{5}-\d{4}$"),                                    False),
    # --- India PII (retained) ---
    ("aadhaar",        re.compile(r"^\d{4}[\s-]?\d{4}[\s-]?\d{4}$"),                    True),
    ("pan",            re.compile(r"^[A-Z]{5}\d{4}[A-Z]$"),                             True),
    ("gstin",          re.compile(r"^\d{2}[A-Z]{5}\d{4}[A-Z][A-Z0-9]Z[A-Z0-9]$"),       True),
    ("ifsc",           re.compile(r"^[A-Z]{4}0[A-Z0-9]{6}$"),                           False),
    ("credit_card",    re.compile(r"^\d{13,19}$"),                                      True),
    ("phone",          re.compile(r"^\+?\d[\d\s-]{8,14}\d$"),                           True),
    ("ip_address",     re.compile(r"^\d{1,3}(\.\d{1,3}){3}$"),                          False),
    # US ABA routing — 9 solid digits. Placed after credit_card (13-19) so it
    # doesn't shadow longer runs; before pincode/zip which are shorter.
    ("aba_routing",    re.compile(r"^\d{9}$"),                                          False),
    # India PIN (6-digit) retained; bare US ZIP is 5-digit (ZIP+4 handled above).
    ("pincode",        re.compile(r"^\d{6}$"),                                          False),
    ("zip",            re.compile(r"^\d{5}$"),                                          False),
    ("url",            re.compile(r"^https?://", re.I),                                 False),
]

# ---------------------------------------------------------------------------
# Column-name heuristics. Triggered when sample values are missing or
# ambiguous. Keys are lowercased substrings; matched left-to-right.
# ---------------------------------------------------------------------------

_NAME_HINTS: list[tuple[str, str, bool]] = [
    # (substring,            semantic_type,  is_pii)
    ("aadhaar",              "aadhaar",      True),
    ("aadhar",               "aadhaar",      True),
    ("pan_no",               "pan",          True),
    ("pan_number",           "pan",          True),
    ("gstin",                "gstin",        True),
    ("ifsc",                 "ifsc",         False),
    # US identifiers (name-driven ⇒ no false positives on cross-locale types)
    ("ssn",                  "ssn",          True),
    ("social_security",      "ssn",          True),
    ("ein",                  "ein",          True),
    ("employer_id",          "ein",          True),
    ("routing",              "aba_routing",  False),
    ("aba",                  "aba_routing",  False),
    ("zip",                  "zip",          False),
    ("zipcode",              "zip",          False),
    ("postal_code",          "zip",          False),
    ("email",                "email",        True),
    ("mobile",               "phone",        True),
    ("phone",                "phone",        True),
    ("contact_no",           "phone",        True),
    ("dob",                  "dob",          True),
    ("date_of_birth",        "dob",          True),
    ("first_name",           "person_name",  True),
    ("last_name",            "person_name",  True),
    ("full_name",            "person_name",  True),
    ("customer_name",        "person_name",  True),
    ("insured_name",         "person_name",  True),
    ("address",              "address",      True),
    ("account_no",           "account_number", True),
    ("account_number",       "account_number", True),
    ("acct_no",              "account_number", True),
    ("claim_id",             "claim_id",     False),
    ("claim_no",             "claim_id",     False),
    ("policy_id",            "policy_id",    False),
    ("policy_no",            "policy_id",    False),
    ("customer_id",          "customer_id",  False),
    ("cust_id",              "customer_id",  False),
    ("amount",               "currency_amount", False),
    ("price",                "currency_amount", False),
    ("premium",              "currency_amount", False),
    ("balance",              "currency_amount", False),
]


def classify_value(value: str) -> Optional[Tuple[str, bool]]:
    """Classify a single sample value. Returns (semantic_type, is_pii) or None."""
    if not value or not isinstance(value, str):
        return None
    s = value.strip()
    if not s:
        return None
    for sem, pat, is_pii in _PATTERNS:
        if pat.match(s):
            return sem, is_pii
    return None


def classify_column(
    name: str,
    sample_values: Iterable[Any] = (),
    min_pattern_hits: int = 3,
) -> Tuple[Optional[str], bool]:
    """Return (semantic_type, is_pii) for a column.

    Strategy:
      1. Pattern-match across sample values; if at least ``min_pattern_hits``
         non-null samples agree on the same semantic_type, accept it.
      2. Otherwise fall back to column-name substring hints.
      3. Otherwise return (None, False).
    """
    # Step 1 — value voting
    counts: dict[str, int] = {}
    pii_seen = False
    for v in sample_values:
        if v is None:
            continue
        c = classify_value(str(v))
        if c is None:
            continue
        sem, is_pii = c
        counts[sem] = counts.get(sem, 0) + 1
        pii_seen = pii_seen or is_pii
    if counts:
        winner, hits = max(counts.items(), key=lambda kv: kv[1])
        if hits >= min_pattern_hits:
            # PII flag from the matching pattern
            for sem, _, is_pii in _PATTERNS:
                if sem == winner:
                    return winner, bool(is_pii)
            return winner, pii_seen

    # Step 2 — name hint fallback
    lname = (name or "").strip().lower()
    for sub, sem, is_pii in _NAME_HINTS:
        if sub in lname:
            return sem, is_pii

    return None, False
