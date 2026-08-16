# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""
data-discovery-service — PII / semantic-type classifier (re-export shim)
========================================================================
The implementation now lives in ``citra_service_utils.data_classifier`` so the
crawler (building ``data_catalogue``) and the Citra Workflow Engine's Dept Data
Flow ingestion (building ``dept_sources``) share ONE ruleset and never drift.

This module re-exports the shared API so existing imports
(``from classifier import classify_column, classify_value``) keep working.
Add/adjust rules in ``citra_service_utils/data_classifier.py`` only.
"""

from __future__ import annotations

from citra_service_utils.data_classifier import (  # noqa: F401
    classify_column,
    classify_value,
)

__all__ = ["classify_column", "classify_value"]
