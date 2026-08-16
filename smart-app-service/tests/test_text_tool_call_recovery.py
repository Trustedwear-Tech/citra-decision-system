"""Tests for the GLM text-format tool-call recovery in ``_call_llm``.

GLM-class hybrid-reasoning models occasionally emit a tool call as plain text
markup in ``content`` instead of the structured ``tool_calls`` field, e.g.::

    <tool_call>outage_query<arg_key>query</arg_key><arg_value>...</arg_value></tool_call>

The dispatch loop reads only ``tool_calls``, so without recovery the markup is
rendered to the user as the "answer" and no tool runs. ``_parse_text_tool_calls``
turns such blocks back into the structured shape; ``Settings.llm_large_extra_body``
carries the ``reasoning`` directive that should prevent the leak in the first
place.
"""
import json

import config
import runtime


def test_recovers_exact_screenshot_payload():
    """The literal payload that failed in the DT Failure Response copilot."""
    payload = (
        "<tool_call>outage_query"
        "<arg_key>query</arg_key>"
        "<arg_value>count of outages where start_time >= 2025-01-01</arg_value>"
        "<arg_key>max_results</arg_key><arg_value>50</arg_value>"
        "</tool_call>"
    )
    calls = runtime._parse_text_tool_calls(payload)
    assert len(calls) == 1
    fn = calls[0]["function"]
    assert fn["name"] == "outage_query"
    args = json.loads(fn["arguments"])
    assert args["query"] == "count of outages where start_time >= 2025-01-01"
    # numeric scalar coerced from "50" -> 50, free text left as string
    assert args["max_results"] == 50


def test_multiple_blocks_recovered():
    payload = (
        "<tool_call>a<arg_key>x</arg_key><arg_value>1</arg_value></tool_call>"
        " then "
        "<tool_call>b<arg_key>y</arg_key><arg_value>two</arg_value></tool_call>"
    )
    calls = runtime._parse_text_tool_calls(payload)
    assert [c["function"]["name"] for c in calls] == ["a", "b"]
    assert json.loads(calls[1]["function"]["arguments"]) == {"y": "two"}


def test_plain_text_yields_no_calls():
    assert runtime._parse_text_tool_calls("Running SAIDI is 927.6 min/consumer.") == []
    assert runtime._parse_text_tool_calls("") == []


def test_tool_call_with_no_args():
    calls = runtime._parse_text_tool_calls("<tool_call>list_outages</tool_call>")
    assert len(calls) == 1
    assert calls[0]["function"]["name"] == "list_outages"
    assert json.loads(calls[0]["function"]["arguments"]) == {}


def test_extra_body_parser_empty_is_dict():
    assert config._parse_extra_body("") == {}
    assert config._parse_extra_body("   ") == {}


def test_extra_body_parser_valid_object():
    raw = '{"reasoning":{"enabled":true,"effort":"medium","exclude":true}}'
    assert config._parse_extra_body(raw) == {
        "reasoning": {"enabled": True, "effort": "medium", "exclude": True}
    }


def test_extra_body_parser_fails_loud_on_bad_json():
    import pytest

    with pytest.raises(json.JSONDecodeError):
        config._parse_extra_body("not json")


def test_extra_body_parser_rejects_non_object():
    import pytest

    with pytest.raises(ValueError):
        config._parse_extra_body("[1, 2, 3]")
