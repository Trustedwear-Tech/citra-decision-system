"""Tests for tools_v2_dispatch — the runtime-side dispatcher that turns
``AgentSpec.tools_v2`` entries into OpenAI function-call tools and
routes invocations to the right backend (in-process, no HTTP self-call).
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import Settings  # noqa: E402
from models import (  # noqa: E402
    AgentSpec,
    AppSpec,
    Action,
    FormPanel,
    McpTool,
    RagTool,
    ValidateFormTool,
    VisionOcrTool,
)
from tools_v2_dispatch import (  # noqa: E402
    build_openai_tools_from_tools_v2,
    dispatch_tools_v2_call,
)


def _settings(**over):
    base = dict(
        sandbox_host_secret="x",
        llm_large_base_url="http://llm.test/v1",
        llm_large_api_key="test-key",
        llm_large_model="test/model",
        mcp_service_api_key="svc-mcp-key",
        discovery_service_url="http://discovery.test",
    )
    base.update(over)
    return Settings(**base)


def _form_panel() -> FormPanel:
    return FormPanel(
        id="claim_form",
        type="form",
        title="Claim",
        on_submit={"agent_action": "noop"},
        schema_inline={
            "type": "object",
            "required": ["policy_number", "amount"],
            "properties": {
                "policy_number": {"type": "string"},
                "amount": {"type": "number"},
                "fraud": {"type": "boolean"},
            },
        },
    )


def _app_spec_with_form() -> AppSpec:
    return AppSpec(
        slug="demo",
        title="Demo",
        tenant_id="t1",
        agent_id="agent_demo",
        panels=[_form_panel()],
    )


def _agent_with_tools(tools_v2: list) -> AgentSpec:
    return AgentSpec(
        agent_id="agent_demo",
        name="demo",
        system_prompt=(
            "You are a claims agent. Always run validate_form first."
        ),
        actions=[Action(name="noop", description="noop")],
        tools_v2=tools_v2,
    )


#: Vision OCR is an OPTIONAL capability: the tool is only registered when
#: VISION_BASE_URL / VISION_API_KEY / VISION_MODEL are configured. These three
#: tests assert on that tool, so without a vision endpoint they were failing
#: rather than skipping — which meant a contributor cloning the public repo with
#: no vision key saw three red tests and no explanation.
def _ocr_configured() -> bool:
    from config import Settings

    return bool(getattr(Settings(), "ocr_enabled", False))


_needs_ocr = pytest.mark.skipif(
    not _ocr_configured(),
    reason="vision OCR not configured (VISION_BASE_URL / VISION_API_KEY / "
           "VISION_MODEL) — the ocr_doc tool is not registered without it",
)


@_needs_ocr
def test_build_openai_tools_validate_form_and_vision_ocr():
    agent = _agent_with_tools(
        [
            ValidateFormTool(name="check_form", schema_ref="claim_form"),
            VisionOcrTool(name="ocr_doc"),
        ]
    )
    tools, dispatch = build_openai_tools_from_tools_v2(
        agent_spec=agent, app_spec=_app_spec_with_form(), settings=_settings()
    )
    names = [t["function"]["name"] for t in tools]
    assert names == ["check_form", "ocr_doc"]
    assert "form_data" in tools[0]["function"]["parameters"]["properties"]
    assert "image_url" in tools[1]["function"]["parameters"]["properties"]
    assert dispatch["check_form"]["kind"] == "validate_form"
    assert dispatch["ocr_doc"]["kind"] == "vision_ocr"


def test_build_openai_tools_drops_kinds_when_proxy_disabled():
    agent = _agent_with_tools(
        [
            McpTool(name="lookup", source_id="s1", tool_name="t1"),
        ]
    )
    # No MCP service key -> the MCP proxy is disabled by design.
    s = Settings(
        sandbox_host_secret="x",
        llm_large_base_url="http://llm.test/v1",
        llm_large_api_key="test-key",
        llm_large_model="test/model",
        mcp_service_api_key="",
    )
    tools, dispatch = build_openai_tools_from_tools_v2(
        agent_spec=agent, app_spec=None, settings=s
    )
    assert tools == []
    assert dispatch == {}


@pytest.mark.asyncio
async def test_dispatch_validate_form_local_only():
    agent = _agent_with_tools(
        [ValidateFormTool(name="check_form", schema_ref="claim_form")]
    )
    app = _app_spec_with_form()
    _, dispatch = build_openai_tools_from_tools_v2(
        agent_spec=agent, app_spec=app, settings=_settings()
    )
    # Missing required + wrong type
    result = await dispatch_tools_v2_call(
        settings=_settings(),
        agent_spec=agent,
        app_spec=app,
        dispatch_table=dispatch,
        tool_name="check_form",
        arguments={"form_data": {"amount": "not-a-number", "fraud": "yes"}},
        auth_header=None,
    )
    assert result["ok"] is False
    assert "policy_number" in result["missing"]
    assert "fraud" in result["invalid"]

    # Happy path
    ok_result = await dispatch_tools_v2_call(
        settings=_settings(),
        agent_spec=agent,
        app_spec=app,
        dispatch_table=dispatch,
        tool_name="check_form",
        arguments={"form_data": {"policy_number": "P1", "amount": 100}},
        auth_header=None,
    )
    assert ok_result == {"ok": True, "missing": [], "invalid": []}


@pytest.mark.asyncio
async def test_dispatch_unknown_tool():
    result = await dispatch_tools_v2_call(
        settings=_settings(),
        agent_spec=_agent_with_tools([]),
        app_spec=None,
        dispatch_table={},
        tool_name="ghost",
        arguments={},
        auth_header=None,
    )
    assert result["code"] == "unknown_tool"


@pytest.mark.asyncio
async def test_dispatch_mcp_forwards_via_proxy_client():
    agent = _agent_with_tools(
        [McpTool(name="lookup", source_id="s1", tool_name="t1")]
    )
    s = _settings()
    _, dispatch = build_openai_tools_from_tools_v2(
        agent_spec=agent, app_spec=None, settings=s
    )
    fake = AsyncMock(return_value={"results": [{"text": "ok"}]})
    with patch("tools_v2_dispatch.call_dept_mcp_query", fake):
        result = await dispatch_tools_v2_call(
            settings=s,
            agent_spec=agent,
            app_spec=None,
            dispatch_table=dispatch,
            tool_name="lookup",
            arguments={"query": "find policy 42", "args": {"customer_id": "c1"}},
            auth_header="Bearer user-jwt-abc",
        )
    assert result == {"results": [{"text": "ok"}]}
    fake.assert_awaited_once()
    kwargs = fake.await_args.kwargs
    assert kwargs["user_jwt"] == "user-jwt-abc"
    assert kwargs["source_id"] == "s1"
    assert kwargs["body"]["tool_name"] == "t1"
    assert kwargs["body"]["query"] == "find policy 42"
    # The dept-MCP derives tenant/customer from the forwarded user_jwt, NOT from a
    # client-supplied body field. The forward body is field-whitelisted
    # (tool_name/query/max_results/filters) precisely so a model- or client-claimed
    # `customer_id` can't ride along and spoof another tenant. Assert it's dropped.
    assert "customer_id" not in kwargs["body"]


@pytest.mark.asyncio
async def test_dispatch_semantic_dataset_routes_to_citra_not_mcp():
    """RAG short-circuit: a kind=semantic dataset is answered by Citra-Service,
    NEVER the dept-MCP. Deterministic dispatch on dataset_kind."""
    agent = _agent_with_tools([
        McpTool(name="policy_search", source_id="policy_lib", tool_name="q",
                dataset_id="policy_lib", dataset_kind="semantic"),
    ])
    s = _settings(citra_service_url="http://citra.test")
    _, dispatch = build_openai_tools_from_tools_v2(
        agent_spec=agent, app_spec=None, settings=s
    )
    citra = AsyncMock(return_value={"source_id": "policy_lib", "count": 1,
                                    "chunks": [{"text": "penalty clause"}]})
    mcp = AsyncMock(return_value={"results": []})
    with patch("tools_v2_dispatch.call_citra_semantic_search", citra), \
         patch("tools_v2_dispatch.call_dept_mcp_query", mcp):
        result = await dispatch_tools_v2_call(
            settings=s, agent_spec=agent, app_spec=None, dispatch_table=dispatch,
            tool_name="policy_search",
            arguments={"query": "penalty for meter tampering", "max_results": 3},
            auth_header="Bearer user-jwt-abc",
        )
    assert result["count"] == 1
    citra.assert_awaited_once()
    mcp.assert_not_awaited()                    # the MCP is NEVER hit for semantic
    kw = citra.await_args.kwargs
    assert kw["source_id"] == "policy_lib"
    assert kw["query"] == "penalty for meter tampering"
    assert kw["user_jwt"] == "user-jwt-abc"
    assert kw["top_k"] == 3


@pytest.mark.asyncio
async def test_dispatch_structured_dataset_still_uses_mcp():
    """A structured (sql) dataset must keep going to the dept-MCP — the
    short-circuit routes ONLY semantic, nothing else."""
    agent = _agent_with_tools([
        McpTool(name="billing_lookup", source_id="billing", tool_name="q",
                dataset_id="billing.invoices", dataset_kind="sql"),
    ])
    s = _settings(citra_service_url="http://citra.test")
    _, dispatch = build_openai_tools_from_tools_v2(
        agent_spec=agent, app_spec=None, settings=s
    )
    citra = AsyncMock(return_value={"count": 0, "chunks": []})
    mcp = AsyncMock(return_value={"results": [{"text": "ok"}]})
    with patch("tools_v2_dispatch.call_citra_semantic_search", citra), \
         patch("tools_v2_dispatch.call_dept_mcp_query", mcp):
        result = await dispatch_tools_v2_call(
            settings=s, agent_spec=agent, app_spec=None, dispatch_table=dispatch,
            tool_name="billing_lookup",
            arguments={"query": "unpaid invoices"},   # NL path, no filters
            auth_header="Bearer u",
        )
    assert result == {"results": [{"text": "ok"}]}
    mcp.assert_awaited_once()
    citra.assert_not_awaited()


@pytest.mark.asyncio
async def test_dispatch_rag_requires_query():
    agent = _agent_with_tools([RagTool(name="search", source_id="kb1", top_k=5)])
    s = _settings()
    _, dispatch = build_openai_tools_from_tools_v2(
        agent_spec=agent, app_spec=None, settings=s
    )
    result = await dispatch_tools_v2_call(
        settings=s,
        agent_spec=agent,
        app_spec=None,
        dispatch_table=dispatch,
        tool_name="search",
        arguments={},
        auth_header="Bearer u",
    )
    assert result["code"] == "bad_args"


@pytest.mark.asyncio
async def test_dispatch_rag_tool_routes_to_citra_not_mcp():
    """RAG short-circuit: a `rag` tool is an unstructured-corpus (semantic) query,
    answered by Citra-Service, NEVER the dept-MCP. top_k defaults from the spec;
    filters pass through."""
    agent = _agent_with_tools([RagTool(name="search", source_id="kb1", top_k=5)])
    s = _settings(citra_service_url="http://citra.test")
    _, dispatch = build_openai_tools_from_tools_v2(
        agent_spec=agent, app_spec=None, settings=s
    )
    citra = AsyncMock(return_value={"source_id": "kb1", "count": 1,
                                    "chunks": [{"text": "policy text"}]})
    mcp = AsyncMock(return_value={"results": []})
    with patch("tools_v2_dispatch.call_citra_semantic_search", citra), \
         patch("tools_v2_dispatch.call_dept_mcp_query", mcp):
        result = await dispatch_tools_v2_call(
            settings=s,
            agent_spec=agent,
            app_spec=None,
            dispatch_table=dispatch,
            tool_name="search",
            arguments={"query": "policies", "filters": {"region": "us"}},
            auth_header="Bearer u",
        )
    assert result["count"] == 1
    citra.assert_awaited_once()
    mcp.assert_not_awaited()                    # the MCP is NEVER hit for rag
    kw = citra.await_args.kwargs
    assert kw["source_id"] == "kb1"
    assert kw["query"] == "policies"
    assert kw["top_k"] == 5                     # default from the spec
    assert kw["filters"] == {"region": "us"}


@pytest.mark.asyncio
async def test_dispatch_semantic_dataset_forwards_doc_path():
    """A doc_path arg ⇒ whole-document fetch: forwarded to Citra-Service so it
    returns ALL sections of one document, not top-k passages."""
    agent = _agent_with_tools([
        McpTool(name="policy_search", source_id="policy_lib", tool_name="q",
                dataset_id="policy_lib", dataset_kind="semantic"),
    ])
    s = _settings(citra_service_url="http://citra.test")
    tools, dispatch = build_openai_tools_from_tools_v2(agent_spec=agent, app_spec=None, settings=s)
    # schema: a semantic dataset tool exposes doc_path
    props = tools[0]["function"]["parameters"]["properties"]
    assert "doc_path" in props
    citra = AsyncMock(return_value={"source_id": "policy_lib", "count": 6, "chunks": []})
    with patch("tools_v2_dispatch.call_citra_semantic_search", citra), \
         patch("tools_v2_dispatch.call_dept_mcp_query", AsyncMock()):
        await dispatch_tools_v2_call(
            settings=s, agent_spec=agent, app_spec=None, dispatch_table=dispatch,
            tool_name="policy_search",
            arguments={"query": "whole SOP", "doc_path": "policy/dt_failure_response_sop.md"},
            auth_header="Bearer u")
    assert citra.await_args.kwargs["doc_path"] == "policy/dt_failure_response_sop.md"


@pytest.mark.asyncio
async def test_dispatch_rag_tool_forwards_doc_path():
    agent = _agent_with_tools([RagTool(name="search", source_id="kb1", top_k=5)])
    s = _settings(citra_service_url="http://citra.test")
    tools, dispatch = build_openai_tools_from_tools_v2(agent_spec=agent, app_spec=None, settings=s)
    assert "doc_path" in tools[0]["function"]["parameters"]["properties"]
    citra = AsyncMock(return_value={"source_id": "kb1", "count": 6, "chunks": []})
    with patch("tools_v2_dispatch.call_citra_semantic_search", citra), \
         patch("tools_v2_dispatch.call_dept_mcp_query", AsyncMock()):
        await dispatch_tools_v2_call(
            settings=s, agent_spec=agent, app_spec=None, dispatch_table=dispatch,
            tool_name="search",
            arguments={"query": "x", "doc_path": "policy/theft_inspection_sop.md"},
            auth_header="Bearer u")
    assert citra.await_args.kwargs["doc_path"] == "policy/theft_inspection_sop.md"


@pytest.mark.asyncio
async def test_fetch_sop_cached_uses_platform_reader_not_mcp():
    """The standing-SOP loader is an SOP corpus = semantic source → answered by
    the Citra-Service platform reader, NEVER the dept-MCP. A sop_doc_path fetches
    the WHOLE SOP document; org_id is passed so a service token can be minted."""
    import tools_v2_dispatch as tv
    citra = AsyncMock(return_value={"source_id": "sop_lib", "count": 2,
                                    "chunks": [{"text": "Section 1"}, {"text": "Section 2"}]})
    mcp = AsyncMock()
    cache = MagicMock()
    cache.get.return_value = None
    with patch("proxy_clients.call_citra_semantic_search", citra), \
         patch.object(tv, "call_dept_mcp_query", mcp), \
         patch.object(tv, "_sop_cache", lambda: cache):
        text = await tv._fetch_sop_cached(
            settings=_settings(citra_service_url="http://citra.test"),
            user_jwt="Bearer u", sop_source="sop_lib", sop_query=None,
            tenant_id="acme", app_slug="claims", modality="image",
            task_type="damage-photo",
            sop_doc_path="policy/dt_failure_response_sop.md")
    assert "Section 1" in text and "Section 2" in text
    citra.assert_awaited_once()
    mcp.assert_not_awaited()                    # NEVER the dept-MCP for an SOP corpus
    kw = citra.await_args.kwargs
    assert kw["source_id"] == "sop_lib"
    assert kw["org_id"] == "acme"
    assert kw["doc_path"] == "policy/dt_failure_response_sop.md"


@pytest.mark.asyncio
async def test_dispatch_rag_doc_path_only_no_query_allowed():
    """A doc_path-only rag call (no query) is a whole-document read — must route
    to Citra-Service, not be rejected as bad_args."""
    agent = _agent_with_tools([RagTool(name="search", source_id="kb1", top_k=5)])
    s = _settings(citra_service_url="http://citra.test")
    _, dispatch = build_openai_tools_from_tools_v2(agent_spec=agent, app_spec=None, settings=s)
    citra = AsyncMock(return_value={"source_id": "kb1", "count": 6, "chunks": []})
    with patch("tools_v2_dispatch.call_citra_semantic_search", citra):
        res = await dispatch_tools_v2_call(
            settings=s, agent_spec=agent, app_spec=None, dispatch_table=dispatch,
            tool_name="search", arguments={"doc_path": "policy/x.md"},  # NO query
            auth_header="Bearer u")
    assert res["count"] == 6
    citra.assert_awaited_once()
    kw = citra.await_args.kwargs
    assert kw["doc_path"] == "policy/x.md" and kw["query"] == ""


@pytest.mark.asyncio
async def test_dispatch_semantic_dataset_doc_path_only_no_query_allowed():
    agent = _agent_with_tools([
        McpTool(name="policy_search", source_id="policy_lib", tool_name="q",
                dataset_id="policy_lib", dataset_kind="semantic"),
    ])
    s = _settings(citra_service_url="http://citra.test")
    _, dispatch = build_openai_tools_from_tools_v2(agent_spec=agent, app_spec=None, settings=s)
    citra = AsyncMock(return_value={"source_id": "policy_lib", "count": 6, "chunks": []})
    with patch("tools_v2_dispatch.call_citra_semantic_search", citra), \
         patch("tools_v2_dispatch.call_dept_mcp_query", AsyncMock()):
        res = await dispatch_tools_v2_call(
            settings=s, agent_spec=agent, app_spec=None, dispatch_table=dispatch,
            tool_name="policy_search", arguments={"doc_path": "policy/x.md"},  # NO query
            auth_header="Bearer u")
    assert res["count"] == 6
    assert citra.await_args.kwargs["doc_path"] == "policy/x.md"


def test_structured_dataset_tool_has_no_doc_path():
    """doc_path is semantic-only — a structured (sql) dataset tool must NOT
    expose it (there are no documents to fetch)."""
    agent = _agent_with_tools([
        McpTool(name="billing_lookup", source_id="billing", tool_name="q",
                dataset_id="billing.invoices", dataset_kind="sql"),
    ])
    s = _settings(citra_service_url="http://citra.test")
    tools, _ = build_openai_tools_from_tools_v2(agent_spec=agent, app_spec=None, settings=s)
    assert "doc_path" not in tools[0]["function"]["parameters"]["properties"]


@pytest.mark.asyncio
@_needs_ocr
async def test_dispatch_vision_ocr_with_b64():
    import base64

    agent = _agent_with_tools([VisionOcrTool(name="ocr_doc")])
    s = _settings()
    _, dispatch = build_openai_tools_from_tools_v2(
        agent_spec=agent, app_spec=None, settings=s
    )

    class _FakeResult:
        text = "extracted"
        tokens_in = 10
        tokens_out = 5
        model = "gpt-4o-mini"

    fake = AsyncMock(return_value=_FakeResult())
    with patch("tools_v2_dispatch.ocr_image", fake):
        result = await dispatch_tools_v2_call(
            settings=s,
            agent_spec=agent,
            app_spec=None,
            dispatch_table=dispatch,
            tool_name="ocr_doc",
            arguments={
                "image_b64": base64.b64encode(b"\x89PNGfake").decode(),
                "content_type": "image/png",
            },
            auth_header="Bearer u",
        )
    assert result["text"] == "extracted"
    assert result["tokens_in"] == 10
    assert fake.await_count == 1


@pytest.mark.asyncio
@_needs_ocr
async def test_dispatch_vision_ocr_rejects_bad_b64():
    agent = _agent_with_tools([VisionOcrTool(name="ocr_doc")])
    s = _settings()
    _, dispatch = build_openai_tools_from_tools_v2(
        agent_spec=agent, app_spec=None, settings=s
    )
    result = await dispatch_tools_v2_call(
        settings=s,
        agent_spec=agent,
        app_spec=None,
        dispatch_table=dispatch,
        tool_name="ocr_doc",
        arguments={"image_b64": "!!!not-base64!!!"},
        auth_header="Bearer u",
    )
    assert result["code"] == "bad_base64"


# ---------------------------------------------------------------------------
# kind=llm sub-call
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_openai_tools_includes_llm_subcall():
    """The llm kind must now appear in the OpenAI tool list (was
    previously skipped)."""
    from models import LlmSubcallTool

    agent = _agent_with_tools(
        [
            ValidateFormTool(name="vf", schema_ref="claim_form"),
            LlmSubcallTool(
                name="summarise",
                system_prompt="You summarise insurance claim notes.",
                model_tier="tier_b",
            ),
        ]
    )
    tools, dispatch = build_openai_tools_from_tools_v2(
        agent_spec=agent, app_spec=_app_spec_with_form(), settings=_settings()
    )
    names = [t["function"]["name"] for t in tools]
    assert "summarise" in names
    assert "summarise" in dispatch
    summarise = next(t for t in tools if t["function"]["name"] == "summarise")
    assert summarise["function"]["parameters"]["required"] == ["prompt"]


@pytest.mark.asyncio
async def test_dispatch_llm_calls_inference_with_bound_system_prompt():
    """Dispatch must pass the tools_v2 entry's system_prompt as the
    sub-call system message and forward the user-provided prompt."""
    from models import LlmSubcallTool

    agent = _agent_with_tools(
        [
            LlmSubcallTool(
                name="summarise",
                system_prompt="You summarise insurance claim notes.",
                model_tier="tier_b",
            ),
        ]
    )
    s = _settings()
    _, dispatch = build_openai_tools_from_tools_v2(
        agent_spec=agent, app_spec=None, settings=s
    )

    fake = AsyncMock(return_value={"role": "assistant", "content": "TL;DR: total loss."})
    with patch("runtime._call_llm", new=fake):
        result = await dispatch_tools_v2_call(
            settings=s,
            agent_spec=agent,
            app_spec=None,
            dispatch_table=dispatch,
            tool_name="summarise",
            arguments={"prompt": "Summarise this claim."},
            auth_header="Bearer u",
        )

    assert result["content"] == "TL;DR: total loss."
    assert result["model_tier"] == "tier_b"
    # _call_llm must have been called once with the bound system
    # prompt as the first message.
    assert fake.await_count == 1
    call_kwargs = fake.await_args.kwargs
    msgs = call_kwargs["messages"]
    assert msgs[0]["role"] == "system"
    assert "summarise insurance claim notes" in msgs[0]["content"]
    assert msgs[1] == {"role": "user", "content": "Summarise this claim."}


@pytest.mark.asyncio
async def test_dispatch_llm_rejects_empty_prompt():
    from models import LlmSubcallTool

    agent = _agent_with_tools(
        [
            LlmSubcallTool(
                name="summarise",
                system_prompt="anything",
            ),
        ]
    )
    s = _settings()
    _, dispatch = build_openai_tools_from_tools_v2(
        agent_spec=agent, app_spec=None, settings=s
    )
    result = await dispatch_tools_v2_call(
        settings=s,
        agent_spec=agent,
        app_spec=None,
        dispatch_table=dispatch,
        tool_name="summarise",
        arguments={"prompt": "   "},
        auth_header=None,
    )
    assert result["code"] == "bad_args"


# ---------------------------------------------------------------------------
# kind=code_exec sandboxed Python
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_openai_tools_includes_code_exec():
    from models import CodeExecTool

    agent = _agent_with_tools(
        [
            CodeExecTool(
                name="draft_report",
                description="Draft a 2-page PDF claim report.",
            ),
        ]
    )
    tools, dispatch = build_openai_tools_from_tools_v2(
        agent_spec=agent, app_spec=None, settings=_settings()
    )
    names = [t["function"]["name"] for t in tools]
    assert "draft_report" in names
    assert "draft_report" in dispatch
    entry = next(t for t in tools if t["function"]["name"] == "draft_report")
    params = entry["function"]["parameters"]
    assert set(params["required"]) == {"script", "output_filename"}
    assert "input_files" in params["properties"]


@pytest.mark.asyncio
async def test_build_openai_tools_drops_code_exec_when_disabled():
    from models import CodeExecTool

    agent = _agent_with_tools(
        [CodeExecTool(name="draft_report")]
    )
    # Wipe both env vars by zeroing the URL.
    s = _settings(code_exec_service_url="")
    tools, dispatch = build_openai_tools_from_tools_v2(
        agent_spec=agent, app_spec=None, settings=s
    )
    assert tools == []
    assert dispatch == {}


@pytest.mark.asyncio
async def test_dispatch_code_exec_happy_path():
    from models import CodeExecTool

    agent = _agent_with_tools(
        [CodeExecTool(name="draft_report")]
    )
    s = _settings()
    _, dispatch = build_openai_tools_from_tools_v2(
        agent_spec=agent, app_spec=None, settings=s
    )

    upstream = {
        "success": True,
        "stdout": "ok",
        "stderr": "",
        "output_files": [
            {
                "filename": "report.pdf",
                "download_url": "https://s3/x/report.pdf?sig=z",
                "size": 1234,
                "content_type": "application/pdf",
            }
        ],
    }
    fake = AsyncMock(return_value=upstream)
    with patch("tools_v2_dispatch.run_code_exec", new=fake):
        result = await dispatch_tools_v2_call(
            settings=s,
            agent_spec=agent,
            app_spec=_app_spec_with_form(),
            dispatch_table=dispatch,
            tool_name="draft_report",
            arguments={
                "script": "open('/workspace/output/report.pdf','wb').write(b'%PDF-1.4')",
                "output_filename": "report.pdf",
                "input_files": [
                    {"filename": "claim.json", "s3_key": "tenants/t1/claim.json"}
                ],
            },
            auth_header="Bearer u-jwt",
        )

    assert result == upstream
    assert fake.await_count == 1
    kwargs = fake.await_args.kwargs
    assert kwargs["user_jwt"] == "u-jwt"
    assert kwargs["output_filename"] == "report.pdf"
    assert kwargs["app_slug"] == "demo"
    assert kwargs["input_files"] == [
        {"filename": "claim.json", "s3_key": "tenants/t1/claim.json"}
    ]


@pytest.mark.asyncio
async def test_dispatch_code_exec_rejects_empty_script():
    from models import CodeExecTool

    agent = _agent_with_tools([CodeExecTool(name="draft_report")])
    s = _settings()
    _, dispatch = build_openai_tools_from_tools_v2(
        agent_spec=agent, app_spec=None, settings=s
    )
    result = await dispatch_tools_v2_call(
        settings=s,
        agent_spec=agent,
        app_spec=None,
        dispatch_table=dispatch,
        tool_name="draft_report",
        arguments={"script": "   ", "output_filename": "x.pdf"},
        auth_header="Bearer u",
    )
    assert result["code"] == "bad_args"


# ── Ontology gate: fraud reuse work only for a dataset that opted into screening ─
import asyncio  # noqa: E402
from unittest.mock import AsyncMock as _AsyncMock, MagicMock as _MagicMock, patch as _patch  # noqa: E402
from models import ConsistencyCheckTool as _CC  # noqa: E402
from tools_v2_dispatch import _dataset_fraud_active  # noqa: E402
import item_records as _ir  # noqa: E402


def _spec(tools):
    return AgentSpec(agent_id="a", name="n", system_prompt="s", tools_v2=tools)


def test_dataset_fraud_active_true_only_for_a_bound_screen_on_THAT_dataset():
    bound = _CC(name="fs", data_source_id="ds", key_field="k", url_columns=["u"])
    unbound = _CC(name="fs2")                       # no data_source_id / url_columns
    assert _dataset_fraud_active(_spec([bound]), "ds") is True
    assert _dataset_fraud_active(_spec([unbound]), "ds") is False  # not wired
    assert _dataset_fraud_active(_spec([]), "ds") is False         # no screen at all
    # PER-DATASET: a screened sibling must not switch fraud on for another dataset.
    assert _dataset_fraud_active(_spec([bound]), "other_ds") is False
    # Unbound caller (no data_source_id) has no ontology to consult → off.
    assert _dataset_fraud_active(_spec([bound]), None) is False


def _mock_col():
    col = _MagicMock()
    cur = _MagicMock()
    cur.sort.return_value = cur
    cur.to_list = _AsyncMock(return_value=[])
    col.find.return_value = cur
    return col


def test_fetch_item_precedents_skips_exact_query_when_not_fraud():
    # include_exact=False must NOT issue the exact-reuse lookup — a non-fraud app
    # pays for the two neighbor queries only, never the reuse query.
    col = _mock_col()
    with _patch.object(_ir, "_col", return_value=col), \
         _patch.object(_ir, "_ensure_indexes", _AsyncMock()):
        async def _run(include_exact):
            col.find.reset_mock()
            out = await _ir.fetch_item_precedents(
                tenant_id="t", slug="s", modality="image", task_type="x",
                content_sha256="abc123", include_exact=include_exact)
            return col.find.call_count, out
        n_with, out_with = asyncio.run(_run(True))
        n_without, out_without = asyncio.run(_run(False))
    assert n_with == 3            # exact + accepted + rejected
    assert n_without == 2         # accepted + rejected only — exact skipped
    assert out_without["exact"] == []


# ── check_evaluate: per-API-check verdict → reviewable ItemFinding(modality=api) ─
from models import CheckEvaluateTool as _CE  # noqa: E402


def _run_check(tool, args):
    agent = _agent_with_tools([tool])
    s = _settings()
    _openai, table = build_openai_tools_from_tools_v2(agent_spec=agent, app_spec=None, settings=s)
    return asyncio.run(dispatch_tools_v2_call(
        settings=s, agent_spec=agent, app_spec=None, dispatch_table=table,
        tool_name=tool.name, arguments=args, auth_header=None)), table


def test_check_evaluate_rule_mode_emits_api_finding():
    tool = _CE(name="cibil_check", task_type="cibil-check", mode="rule",
               rule_expr="cibil_score >= 700")
    ok, table = _run_check(tool, {"data": {"cibil_score": 780}, "query": "loan eligibility"})
    assert "cibil_check" in table                       # schema wired
    assert not ok.get("error")
    assert ok["modality"] == "api" and ok["item_type"] == "cibil-check"
    assert ok["recommendation"] == "pass"
    bad, _ = _run_check(tool, {"data": {"cibil_score": 650}, "query": "loan eligibility"})
    assert bad["recommendation"] == "flag"              # below threshold → flag


def test_check_evaluate_rule_error_flags_never_silent_pass():
    tool = _CE(name="c", task_type="x", mode="rule", rule_expr="boom(")  # malformed
    res, _ = _run_check(tool, {"data": {"a": 1}, "query": "q"})
    assert res["recommendation"] == "flag"              # fail-loud, never auto-pass
    assert res["confidence"] == 0.0                      # errored rule ≠ certain verdict


def test_check_evaluate_rule_rejects_resource_exhaustion():
    # Any construct that could OOM the worker if eval'd must be rejected by the AST
    # whitelist BEFORE evaluation, and flag at confidence 0.0 (never crash/pass).
    # Covers Pow big-int AND sequence-repetition (Mult + big constant — needs no Pow).
    for expr in ("9**9**9**9 > a", '"A"*999999999 == a', "[0]*999999999 == a",
                 "a << 999999999 > 0"):
        tool = _CE(name="c", task_type="x", mode="rule", rule_expr=expr)
        res, _ = _run_check(tool, {"data": {"a": 1}, "query": "q"})
        assert res["recommendation"] == "flag", expr
        assert res["confidence"] == 0.0, expr


def test_check_evaluate_rule_rejects_calls_and_attributes():
    # No Call / Attribute / Subscript / dunder — blocks sandbox-escape attempts.
    for expr in ("__import__('os')", "a.__class__", "open('x')", "a[0] == 1"):
        tool = _CE(name="c", task_type="x", mode="rule", rule_expr=expr)
        res, _ = _run_check(tool, {"data": {"a": 1}, "query": "q"})
        assert res["recommendation"] == "flag", expr


def test_check_evaluate_rule_allows_membership_and_boolean():
    # Legitimate threshold/membership/boolean rules must still pass (not over-blocked).
    tool = _CE(name="c", task_type="x", mode="rule",
               rule_expr='status in ("approved", "verified") and score >= 700')
    ok, _ = _run_check(tool, {"data": {"status": "approved", "score": 780}, "query": "q"})
    assert ok["recommendation"] == "pass" and ok["confidence"] == 1.0
    bad, _ = _run_check(tool, {"data": {"status": "pending", "score": 780}, "query": "q"})
    assert bad["recommendation"] == "flag" and bad["confidence"] == 1.0  # genuine determination


def test_check_evaluate_requires_data():
    tool = _CE(name="c", task_type="x", mode="rule", rule_expr="a == 1")
    res, _ = _run_check(tool, {"query": "no data"})
    assert res.get("code") == "bad_args"


def test_check_evaluate_schema_exposes_data_and_query():
    tool = _CE(name="c", task_type="x")
    agent = _agent_with_tools([tool])
    openai_tools, _ = build_openai_tools_from_tools_v2(
        agent_spec=agent, app_spec=None, settings=_settings())
    fn = next(t["function"] for t in openai_tools if t["function"]["name"] == "c")
    props = fn["parameters"]["properties"]
    assert "data" in props and "query" in props
    assert set(fn["parameters"]["required"]) == {"data", "query"}


def test_check_evaluate_llm_mode_parses_verdict():
    # llm mode with no tenant/app_spec + no sop_source → only _call_llm is needed
    # (rubric/precedent/SOP blocks are gated on tenant+app_slug / sop_source).
    tool = _CE(name="credit_check", task_type="credit-check", mode="llm")
    agent = _agent_with_tools([tool]); s = _settings()
    _openai, table = build_openai_tools_from_tools_v2(agent_spec=agent, app_spec=None, settings=s)
    canned = {"content": '{"subject":"credit-bureau check","recommendation":"pass",'
                         '"confidence":0.9,"rationale":"score 780 clears policy"}'}
    import runtime as _rt
    with patch.object(_rt, "_call_llm", AsyncMock(return_value=canned)):
        res = asyncio.run(dispatch_tools_v2_call(
            settings=s, agent_spec=agent, app_spec=None, dispatch_table=table,
            tool_name="credit_check",
            arguments={"data": {"credit_score": 780}, "query": "loan eligibility"},
            auth_header=None))
    assert not res.get("error")
    assert res["modality"] == "api" and res["item_type"] == "credit-check"
    assert res["recommendation"] == "pass"
    assert res["confidence"] == 0.9
    assert res["subject"] == "credit-bureau check"


def test_check_evaluate_llm_unparseable_degrades_not_crashes():
    tool = _CE(name="c", task_type="x", mode="llm")
    agent = _agent_with_tools([tool]); s = _settings()
    _openai, table = build_openai_tools_from_tools_v2(agent_spec=agent, app_spec=None, settings=s)
    import runtime as _rt
    with patch.object(_rt, "_call_llm", AsyncMock(return_value={"content": "not json at all"})):
        res = asyncio.run(dispatch_tools_v2_call(
            settings=s, agent_spec=agent, app_spec=None, dispatch_table=table,
            tool_name="c", arguments={"data": {"a": 1}, "query": "q"}, auth_header=None))
    assert not res.get("error")
    assert res["modality"] == "api"
    assert res["recommendation"] is None            # graceful: no crash on bad JSON
    assert "not json" in res["rationale"]            # raw text preserved for the officer


def test_lookup_judgement_tool_builds_without_crashing():
    """Regression: this branch appended to `out`, which does not exist in
    build_openai_tools_from_tools_v2 — so ANY spec declaring a lookup_judgement
    tool killed the agent with a NameError at 0 tool calls, before it did any
    work. It survived because the generated agent_spec schema had drifted and
    never advertised the tool, so no builder could author one; regenerating the
    schema made it reachable and it failed on the first real build."""
    from models import LookupJudgementTool

    tool = LookupJudgementTool(name="lookup_judgement")
    agent = _agent_with_tools([tool])
    openai_tools, table = build_openai_tools_from_tools_v2(
        agent_spec=agent, app_spec=None, settings=_settings())
    names = [t["function"]["name"] for t in openai_tools]
    assert "lookup_judgement" in names
    assert "lookup_judgement" in table


# ── Page limits: two different numbers, for two different costs ──────────────


def test_the_two_pdf_caps_are_independent():
    """100 pages of TEXT is free local parsing. 100 pages of VISION is one
    request carrying 100 images — ocr_pdf_pages batches every page into a single
    call. Sizing them the same would be an accidental cost bomb."""
    import tools_v2_dispatch as d

    assert d.PDF_TEXT_MAX_PAGES == 100
    assert d.PDF_VISION_MAX_PAGES < d.PDF_TEXT_MAX_PAGES
    assert d.PDF_VISION_MAX_PAGES == 20


def test_pdf_text_reports_how_much_it_read():
    """Returns (text, pages_read, pages_total) so a document longer than the cap
    can be REPORTED. A silently half-read policy looks exactly like a fully-read
    one on the officer's card."""
    import inspect

    import tools_v2_dispatch as d

    sig = inspect.signature(d._pdf_text)
    assert "max_pages" in sig.parameters
    # unreadable bytes → the empty triple, never a bare string
    assert d._pdf_text(b"not a pdf") == ("", 0, 0)


def test_text_doc_mimes_cover_what_the_pipeline_curates_into():
    import tools_v2_dispatch as d

    for m in ("text/plain", "text/markdown", "text/csv"):
        assert m in d._TEXT_DOC_MIMES
