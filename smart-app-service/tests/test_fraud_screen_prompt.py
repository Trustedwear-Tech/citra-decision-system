# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""Guaranteed invocation: the auto-wired fraud screen isn't just available — the
runtime injects a mandatory-invocation stanza so the agent actually runs it."""
import models
import runtime


def _spec(url_columns):
    return models.AgentSpec(
        agent_id="a", name="n", system_prompt="base",
        tools_v2=[models.ConsistencyCheckTool(
            name="fraud_screen_insp", data_source_id="insp",
            key_field="equipment_id", url_columns=url_columns)],
    )


def test_stanza_present_and_names_the_tool_when_active():
    blk = runtime._render_fraud_screen_block(_spec(["defect_photo_url"]))
    assert "FRAUD SCREEN" in blk and "MUST call" in blk
    assert "fraud_screen_insp" in blk               # names the exact tool to call
    assert "never auto-reject" in blk.lower()       # preserves the universal-approval invariant
    # Conditional wording — no spurious call when the action isn't about a record.
    assert "no call is needed" in blk.lower()


def test_no_stanza_when_no_active_screen():
    # A consistency_check with no captured columns is not a fraud screen.
    assert runtime._render_fraud_screen_block(_spec([])) == ""
    # No tools at all → no stanza.
    assert runtime._render_fraud_screen_block(
        models.AgentSpec(agent_id="a", name="n", system_prompt="b", tools_v2=[])) == ""
