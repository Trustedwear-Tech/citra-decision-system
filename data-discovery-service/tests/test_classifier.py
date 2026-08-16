# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""Unit tests for the PII / semantic-type classifier."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from classifier import classify_column, classify_value


def test_classify_value_email():
    assert classify_value("rohit@example.com") == ("email", True)


def test_classify_value_pan():
    assert classify_value("ABCDE1234F") == ("pan", True)


def test_classify_value_aadhaar_with_spaces():
    assert classify_value("1234 5678 9012") == ("aadhaar", True)


def test_classify_value_aadhaar_with_dashes():
    assert classify_value("1234-5678-9012") == ("aadhaar", True)


def test_classify_value_ifsc():
    assert classify_value("HDFC0001234") == ("ifsc", False)


def test_classify_value_url_not_pii():
    assert classify_value("https://example.com/x") == ("url", False)


def test_classify_value_unknown():
    assert classify_value("just some text") is None


def test_column_voting_prefers_consistent_pattern():
    samples = ["a@b.com", "c@d.com", "e@f.com", "not-an-email", None]
    sem, pii = classify_column("contact", samples)
    assert sem == "email"
    assert pii is True


def test_column_voting_rejects_below_threshold():
    samples = ["a@b.com", "junk", "junk", "junk"]
    sem, pii = classify_column("misc", samples, min_pattern_hits=3)
    # Falls back to name hint — "misc" matches nothing → unknown
    assert sem is None
    assert pii is False


def test_name_hint_aadhaar():
    sem, pii = classify_column("aadhaar_number", [])
    assert sem == "aadhaar"
    assert pii is True


def test_name_hint_pan():
    sem, pii = classify_column("pan_no", [])
    assert sem == "pan"
    assert pii is True


def test_name_hint_email():
    sem, pii = classify_column("CUSTOMER_EMAIL", [])
    assert sem == "email"
    assert pii is True


def test_name_hint_amount_not_pii():
    sem, pii = classify_column("policy_premium_amount", [])
    assert sem == "currency_amount"
    assert pii is False


def test_value_overrides_name_hint():
    """If sample values strongly match a different pattern, values win."""
    # name says 'address' (would be pii) but values are clearly emails
    sem, pii = classify_column("address", ["a@b.com", "c@d.com", "e@f.com"])
    assert sem == "email"
    assert pii is True
