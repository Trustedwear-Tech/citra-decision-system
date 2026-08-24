# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""The top-N exemption to the no-partial-result invariant.

``answer_rows`` normally requires a count probe <= CAP, so a row sample can never
leave the MCP. That refused a whole class of legitimate questions: "the most
recent complaint" is completely answered by ORDER BY created_at DESC LIMIT 1, but
the guard stripped the LIMIT, counted the base set (2001 > 50) and refused —
while telling the agent to "narrow the filter", which it cannot do without
already knowing a value. Prod symptom: 8 rounds of rephrasing, ~146s, and a
"(no response)" reply.

The exemption is deliberately narrow: BOTH an explicit ORDER BY and a LIMIT n
<= CAP. Ordering is what makes those n rows the complete answer rather than an
arbitrary slice, so an unordered LIMIT — the abuse the guard was written for —
stays refused.
"""
from agentic_sql_planner import CAP, _deterministic_top_n


def test_ordered_limit_one_is_top_n():
    n = _deterministic_top_n(
        'SELECT "complaint_id" FROM complaints ORDER BY "created_at" DESC LIMIT 1', "sql"
    )
    assert n == 1


def test_ordered_limit_n_is_top_n():
    n = _deterministic_top_n(
        'SELECT * FROM complaints ORDER BY "created_at" DESC LIMIT 5', "sql"
    )
    assert n == 5


def test_limit_without_order_by_is_a_sample_not_top_n():
    # THE case the LIMIT-stripping guard exists for: any 5 rows of a 2001-row
    # table. Not deterministic, not an answer — must stay refused.
    assert _deterministic_top_n("SELECT * FROM complaints LIMIT 5", "sql") is None


def test_order_by_without_limit_is_unbounded():
    assert _deterministic_top_n(
        'SELECT * FROM complaints ORDER BY "created_at" DESC', "sql"
    ) is None


def test_limit_above_cap_is_not_exempt():
    # Ordered, but past the inline cap — falls back to the normal count gate.
    assert _deterministic_top_n(
        f'SELECT * FROM complaints ORDER BY "created_at" DESC LIMIT {CAP + 1}', "sql"
    ) is None


def test_limit_exactly_cap_is_exempt():
    assert _deterministic_top_n(
        f'SELECT * FROM complaints ORDER BY "created_at" DESC LIMIT {CAP}', "sql"
    ) == CAP


def test_bare_select_is_not_top_n():
    assert _deterministic_top_n("SELECT * FROM complaints", "sql") is None


def test_unparseable_sql_fails_safe():
    # Fail-safe: an unparseable query keeps the old no-partial-result behaviour
    # rather than being waved through as a top-N.
    assert _deterministic_top_n("SELECT ((( FROM WHERE", "sql") is None


def test_duckdb_dialect_top_n():
    n = _deterministic_top_n(
        'SELECT * FROM readings ORDER BY "ts" DESC LIMIT 3', "duckdb"
    )
    assert n == 3


def test_zero_limit_is_not_a_top_n():
    assert _deterministic_top_n(
        'SELECT * FROM complaints ORDER BY "created_at" DESC LIMIT 0', "sql"
    ) is None
