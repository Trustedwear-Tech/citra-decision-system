# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""Citra Decision API — Python SDK.

    from decision_app import DecisionAppClient

    client = DecisionAppClient("https://apps.citra-ai.com/api", token=user_jwt)
    contract = client.get_contract("equipment-inspection-fraud-screen")
    rec = client.recommend(contract["slug"], contract["run_actions"][0],
                           {"inspection_id": "INS-2026-0013"})
    client.approve(contract["slug"], rec["correlation_id"],
                   overrides=[{"outcome": "Pass"}], note="genuine repair")
"""
from .client import DecisionAppClient, DecisionApiError

__all__ = ["DecisionAppClient", "DecisionApiError"]
__version__ = "0.1.0"
