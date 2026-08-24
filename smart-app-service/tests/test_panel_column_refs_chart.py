# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""Chart panels: the synthetic COUNT aggregate ('count') is not a column.

Regression for panel_columns_unknown false-positives — a bar chart with
y='count' + aggregation='count' was flagged as referencing a hallucinated
column, blocking republish of otherwise-valid apps.
"""
from data_binding_validator import _panel_column_refs


def _refs(panel):
    return _panel_column_refs(panel, panel.get("data_source"))


def test_count_chart_does_not_reference_count_column():
    panel = {"type": "chart", "chart_type": "bar", "data_source": "ds",
             "x": "channel", "y": "count", "aggregation": "count"}
    refs = _refs(panel).get("ds", set())
    assert "channel" in refs          # x is a real grouping column
    assert "count" not in refs        # the count aggregate is NOT a column


def test_count_literal_skipped_even_without_aggregation():
    panel = {"type": "chart", "data_source": "ds", "x": "status", "y": "count"}
    assert "count" not in _refs(panel).get("ds", set())


def test_sum_chart_still_validates_its_y_column():
    panel = {"type": "chart", "data_source": "ds",
             "x": "month", "y": "amount", "aggregation": "sum"}
    refs = _refs(panel).get("ds", set())
    assert "amount" in refs and "month" in refs   # real columns still checked
