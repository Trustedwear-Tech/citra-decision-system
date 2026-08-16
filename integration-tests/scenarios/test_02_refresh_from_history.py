# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""
Skipped: ``wf_refresh_history`` workflow template is not yet checked into
the citra-workflow repo (verified 2026-05-10 — no YAML/JSON template
named refresh_history or wf_refresh_history found).

Once the template lands, the intended assertions are:
  1. Workflow pulls historical rows from the SAP stub via dept-MCP
  2. Indexes each row into Mongo (claims_history collection)
  3. Embeds + writes to Milvus collection ``itest_*_claims_history``
  4. Watermark advances only on success
  5. Re-run with same watermark is a no-op (idempotency)

Track the implementation gap; do NOT delete this file — it's the
fail-loud reminder that this scenario is owed.
"""
import pytest


pytestmark = pytest.mark.skip(reason=(
    "wf_refresh_history workflow template not implemented yet "
    "(verified 2026-05-10 against citra-workflow; no template found)."
))


def test_refresh_from_history_indexes_mongo_and_milvus():
    pass  # placeholder — see module docstring


def test_refresh_from_history_advances_watermark_only_on_success():
    pass  # placeholder — see module docstring


def test_refresh_from_history_is_idempotent_at_same_watermark():
    pass  # placeholder — see module docstring
