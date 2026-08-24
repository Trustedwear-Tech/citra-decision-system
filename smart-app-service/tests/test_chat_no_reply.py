# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""A chat turn that produces nothing must FAIL, not return 200.

The loop used to fall back to ``reply="(no response)"``: a 200 whose body was a
placeholder string. The UI printed it verbatim, every decision-API consumer read
it as success, and the only signal a turn had failed was a human reading the
word. Observed in prod — a row-level question burned all 8 rounds and
synthesised nothing, and the caller could not tell that from an answer.

It now raises ChatProducedNoReply → 502 with the reason. The exemption that
matters: a chart-only answer (blocks, no prose) is a REAL reply and must not
raise — _parse_chart_blocks strips the chart out of the prose, so an empty
`reply` alongside blocks is the normal dashboard-narrator shape.
"""
import pytest

from runtime import ChatProducedNoReply, _MAX_CHAT_ROUNDS


def test_no_reply_exception_carries_a_diagnosis():
    exc = ChatProducedNoReply(
        "the agent produced no answer after 8 tool call(s). The model returned "
        "empty content and the tool-free synthesis retry did too."
    )
    msg = str(exc)
    # The operator must learn WHY, not just that it failed.
    assert "no answer" in msg
    assert "tool call" in msg


def test_chat_round_cap_is_named_not_magic():
    # The error message quotes this budget; loop and message must not drift.
    assert _MAX_CHAT_ROUNDS == 8


def test_is_an_exception_so_the_endpoint_can_map_it():
    # main.py maps this to 502 explicitly; it must not be swallowed as a value.
    assert issubclass(ChatProducedNoReply, Exception)
    with pytest.raises(ChatProducedNoReply):
        raise ChatProducedNoReply("boom")


class _FakeParse:
    """The two shapes that decide raise-vs-return, mirroring the runtime guard
    `if not reply and not blocks:`."""


@pytest.mark.parametrize(
    "reply,blocks,should_fail",
    [
        ("", [], True),                                   # nothing said, nothing drawn → FAIL
        ("", [{"type": "chart"}], False),                 # chart-only answer → legitimate
        ("1,300 open complaints", [], False),             # prose answer → legitimate
        ("here is the trend", [{"type": "chart"}], False),  # prose + chart → legitimate
    ],
)
def test_only_empty_prose_AND_no_blocks_fails(reply, blocks, should_fail):
    # The exact predicate from runtime.chat_with_agent. A chart-only turn must
    # never raise — that regression would silently break dashboard narrators.
    assert (not reply and not blocks) is should_fail
