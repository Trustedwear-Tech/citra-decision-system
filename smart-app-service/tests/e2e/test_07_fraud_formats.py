# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""Fraud identifier-format validators — pure unit tests (no stack).

Imports fraud_checks directly. Every validator must REJECT malformed input
(deterministic); valid-format cases are asserted for the checksum-free / standard
formats and left as documented gaps for the checksum ones (GSTIN, Aadhaar) where
a valid sample requires a real check digit.
"""
from __future__ import annotations

import os
import sys

import pytest

# fraud_checks lives in the service root (one dir up from tests/e2e/).
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

fc = pytest.importorskip("fraud_checks", reason="fraud_checks not importable in this env")


def _ok(fn, v) -> bool:
    res = fn(v)
    return res[0] if isinstance(res, tuple) else bool(res)


# ── Invalid input MUST be rejected (deterministic across all validators) ─────
_INVALID = [
    ("validate_pan", "NOTAPAN"),
    ("validate_ifsc", "12345"),
    ("validate_gstin", "BADGSTIN"),
    ("validate_vin", "SHORTVIN"),
    ("validate_aadhaar", "11"),
    ("validate_email", "not-an-email"),
    ("validate_phone_in", "12345"),
    ("validate_phone_us", "555"),
    ("validate_ssn", "000-00-0000"),
    ("validate_ein", "1"),
    ("validate_routing", "12345"),
    ("validate_zip", "ABCDE"),
]


@pytest.mark.parametrize("fnname,bad", _INVALID, ids=[x[0] for x in _INVALID])
def test_rejects_invalid(fnname, bad):
    fn = getattr(fc, fnname, None)
    if fn is None:
        pytest.skip(f"{fnname} not present")
    assert _ok(fn, bad) is False, f"{fnname} accepted invalid input {bad!r}"


# ── Valid, checksum-free / standard formats SHOULD pass ─────────────────────
_VALID = [
    ("validate_pan", "ABCPE1234F"),   # 4th char 'P' = valid holder-type
    ("validate_ifsc", "SBIN0001234"),
    ("validate_vin", "1HGBH41JXMN109186"),
    ("validate_email", "officer@acme-power.co.in"),
    ("validate_phone_in", "9876543210"),
    ("validate_phone_us", "4155551234"),
    ("validate_ssn", "123-45-6789"),
    ("validate_ein", "12-3456789"),
    ("validate_routing", "021000021"),   # standard ABA checksum-valid
    ("validate_zip", "94105"),
]


@pytest.mark.parametrize("fnname,good", _VALID, ids=[x[0] for x in _VALID])
def test_accepts_valid(fnname, good):
    fn = getattr(fc, fnname, None)
    if fn is None:
        pytest.skip(f"{fnname} not present")
    assert _ok(fn, good) is True, f"{fnname} rejected valid input {good!r}"
