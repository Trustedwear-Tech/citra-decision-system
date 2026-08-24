# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

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
