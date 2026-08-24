# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

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
