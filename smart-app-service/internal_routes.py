"""FastAPI router for /smart-app/internal/* — proxy endpoints called by the
builder pod and runtime engine.

These endpoints are NOT public. They are guarded by an HMAC-signed bearer
(see ``internal_bearer.py``) instead of the user JWT middleware. The auth
middleware bypasses them so this router can do its own validation.

v1 endpoints
~~~~~~~~~~~~
- ``POST /smart-app/internal/ocr`` — Qwen3-VL (or compatible) vision proxy.
- ``POST /smart-app/internal/ocr/pages`` — multi-page OCR.
- ``POST /smart-app/internal/mcp`` — dept-MCP tool/query proxy.
- ``POST /smart-app/internal/rag`` — dept-MCP RAG search proxy.

Authorisation
~~~~~~~~~~~~~
Every route requires the HMAC bearer to declare the matching
*tool kind* (``vision_ocr`` | ``mcp`` | ``rag``). MCP and RAG
additionally require the bearer's ``bindings`` claim to
list the specific ``(source_id[, tool_name])`` so a
leaked runtime bearer cannot enumerate other tools the BA never wired.
"""

from __future__ import annotations

import base64
import binascii
import logging
import os
import re
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from openai import APIConnectionError, APIStatusError

from config import Settings, get_settings
from env_context import set_current_env
from internal_bearer import (
    InternalBearerClaims,
    InternalBearerError,
    verify_internal_bearer,
)
from llm_client import get_llm_client
from llm_proxy_budget import BudgetExceeded, record_tokens, reserve_call
from ocr_proxy import OcrError, ocr_image, ocr_pdf_pages, _fetch_image_url
from proxy_clients import (
    ProxyError,
    call_citra_semantic_search,
    call_dept_mcp_query,
    list_discovery_sources,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/smart-app/internal", tags=["internal"])


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------


def _extract_bearer(authorization: Optional[str]) -> Optional[str]:
    if not authorization:
        return None
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1]


def _require_internal_claims(
    request: Request, *, required_tool: Optional[str] = None
) -> InternalBearerClaims:
    settings: Settings = get_settings()
    raw = _extract_bearer(
        request.headers.get("Authorization")
        or request.headers.get("authorization")
    )
    if not raw:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="internal bearer required",
        )
    try:
        claims = verify_internal_bearer(
            signing_key=settings.internal_signing_key,
            bearer=raw,
        )
    except InternalBearerError as e:
        logger.warning("[internal] bearer rejected: %s (%s)", e, e.code)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "invalid_bearer", "code": e.code},
        ) from e

    if required_tool and required_tool not in claims.tools:
        # Cost-gate: even if the bearer is valid, it must declare the
        # specific tool the request is invoking. Builder pods get a
        # broad bearer; runtime bearers are tightly scoped to the
        # tools the published AppSpec declared.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "tool_not_authorised",
                "tool": required_tool,
                "subject": claims.subject,
            },
        )

    # The builder pod calls these proxy routes mid-conversation to discover
    # tools / read the catalogue / probe MCPs. The builder is ALWAYS test, so
    # bind the env here (one chokepoint for every internal route) — proxy_clients
    # + catalogue_client then resolve against the test MCPs. When no test env is
    # configured this resolves to prod (legacy). Runtime bearers are left at the
    # prod default; the in-process runtime path binds its own env per app.
    if claims.kind == "builder" and settings.test_environment_available:
        set_current_env("test")
    return claims


# ---------------------------------------------------------------------------
# OCR endpoint
# ---------------------------------------------------------------------------


@router.post("/ocr")
async def internal_ocr(request: Request, body: dict = Body(...)) -> dict:
    """OCR proxy.

    Body::
        {
          "image_b64"?: "<base64-encoded image bytes>",
          "image_url"?: "https://...",   # alternative to image_b64
          "content_type"?: "image/png",  # only used with image_b64
          "prompt"?: "<custom prompt>"
        }

    Exactly one of ``image_b64`` or ``image_url`` must be set.

    Returns::
        {"text": "<extracted text>", "tokens_in": N, "tokens_out": N,
         "model": "<vision-model-name>"}
    """
    claims = _require_internal_claims(request, required_tool="vision_ocr")
    settings = get_settings()

    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="body must be a JSON object")

    image_b64 = body.get("image_b64")
    image_url = body.get("image_url")
    prompt = body.get("prompt") or None
    content_type = (body.get("content_type") or "image/png").strip().lower()

    if bool(image_b64) == bool(image_url):
        raise HTTPException(
            status_code=400,
            detail="exactly one of image_b64 or image_url is required",
        )

    if image_b64:
        if not isinstance(image_b64, str):
            raise HTTPException(status_code=400, detail="image_b64 must be a string")
        if len(image_b64) > 16 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="image_b64 too large")
        try:
            raw = base64.b64decode(image_b64, validate=True)
        except (binascii.Error, ValueError) as e:
            raise HTTPException(status_code=400, detail=f"invalid base64: {e}") from e
        mime = content_type
    else:
        try:
            raw, mime, _filename = await _fetch_image_url(image_url)
        except OcrError as e:
            raise HTTPException(status_code=e.status, detail={"code": e.code, "message": str(e)}) from e

    try:
        if mime == "application/pdf":
            # v1 does not rasterise PDFs server-side; the caller must
            # send each page as a separate image. Reject with a clear
            # error so the runtime knows to pre-rasterise.
            raise HTTPException(
                status_code=415,
                detail=(
                    "application/pdf must be pre-rasterised by the caller;"
                    " send each page via /ocr individually or use the"
                    " batch shape with `pages: [<b64>, ...]`"
                ),
            )
        result = await ocr_image(
            settings=settings,
            image_bytes=raw,
            mime_type=mime,
            prompt=prompt,
        )
    except OcrError as e:
        logger.warning("[internal/ocr] failed for subject=%s: %s (%s)", claims.subject, e, e.code)
        raise HTTPException(
            status_code=e.status,
            detail={"code": e.code, "message": str(e)},
        ) from e

    return {
        "text": result.text,
        "tokens_in": result.tokens_in,
        "tokens_out": result.tokens_out,
        "model": result.model,
    }


@router.post("/ocr/pages")
async def internal_ocr_pages(request: Request, body: dict = Body(...)) -> dict:
    """Multi-page OCR proxy.

    Body::
        {
          "pages": ["<base64 page 1>", "<base64 page 2>", ...],
          "prompt"?: "<custom prompt>"
        }

    Uses one chat-completion call with all pages as image content. Costs
    more tokens than single-page; intended for documents where context
    across pages matters (e.g. multi-page forms, tabular reports).
    """
    claims = _require_internal_claims(request, required_tool="vision_ocr")
    settings = get_settings()

    pages_b64 = body.get("pages")
    prompt = body.get("prompt") or None
    if not isinstance(pages_b64, list) or not pages_b64:
        raise HTTPException(status_code=400, detail="pages must be a non-empty list")
    if len(pages_b64) > 20:
        raise HTTPException(status_code=413, detail="too many pages (max 20 per call)")

    pages: list[bytes] = []
    for i, p in enumerate(pages_b64):
        if not isinstance(p, str):
            raise HTTPException(status_code=400, detail=f"pages[{i}] must be a base64 string")
        try:
            pages.append(base64.b64decode(p, validate=True))
        except (binascii.Error, ValueError) as e:
            raise HTTPException(status_code=400, detail=f"pages[{i}] invalid base64: {e}") from e

    try:
        result = await ocr_pdf_pages(
            settings=settings,
            pages=pages,
            prompt=prompt,
        )
    except OcrError as e:
        logger.warning("[internal/ocr/pages] failed for subject=%s: %s (%s)", claims.subject, e, e.code)
        raise HTTPException(
            status_code=e.status,
            detail={"code": e.code, "message": str(e)},
        ) from e

    return {
        "text": result.text,
        "tokens_in": result.tokens_in,
        "tokens_out": result.tokens_out,
        "model": result.model,
        "page_count": len(pages),
    }


# ---------------------------------------------------------------------------
# LLM proxy (builder pod)
# ---------------------------------------------------------------------------
#
# OpenAI-compatible relay so the builder pod can call this service with a
# stock OpenAI SDK pointed at ``LLM_LARGE_BASE_URL = .../smart-app/internal/llm/v1``
# and ``LLM_LARGE_API_KEY = <HMAC bearer>``. The pod never holds a raw
# OpenRouter / inference-service credential — this service forwards using
# its own.


_LLM_AUDIT = logging.getLogger("smart_app_service.llm_proxy")

# Models for which prompt-cache injection is safe. Anthropic models support
# ``cache_control`` on content parts; OpenRouter passes the directive
# through (and injects the anthropic-beta header itself). Non-Anthropic
# models ignore the marker harmlessly, but we gate on the substring so a
# future provider that errors on the extra key isn't surprised.
_CACHE_ELIGIBLE_MODEL_SUBSTRINGS = ("anthropic/", "claude")


def _looks_anthropic(model: str) -> bool:
    m = (model or "").lower()
    return any(s in m for s in _CACHE_ELIGIBLE_MODEL_SUBSTRINGS)


# GLM-class models on OpenRouter (z-ai/glm-*) must be pinned to the z-ai
# provider with NO fallback — otherwise OpenRouter routes them to whatever
# provider it likes (e.g. StreamLake), with different behaviour, pricing, and
# no reasoning directive. Gated on the substring so the pinning applies only
# to GLM upstreams.
_GLM_MODEL_SUBSTRINGS = ("z-ai/", "glm")


def _looks_glm(model: str) -> bool:
    m = (model or "").lower()
    return any(s in m for s in _GLM_MODEL_SUBSTRINGS)


def _to_cached_content(content: Any) -> Any:
    """Wrap a message's ``content`` so the LAST text block carries an
    ephemeral ``cache_control`` marker.

    Accepts either a plain string (the common OpenClaw shape) or an
    already-structured content-part list. Returns a content-part list with
    the breakpoint on the final text part. A non-text / unrecognised shape
    is returned unchanged (we never want to corrupt a payload to chase a
    cache hit).
    """
    if isinstance(content, str):
        if not content:
            return content
        return [{
            "type": "text",
            "text": content,
            "cache_control": {"type": "ephemeral"},
        }]
    if isinstance(content, list) and content:
        # Mark the last text part; leave earlier parts (and any image /
        # tool parts) untouched.
        out = [dict(p) if isinstance(p, dict) else p for p in content]
        for i in range(len(out) - 1, -1, -1):
            p = out[i]
            if isinstance(p, dict) and p.get("type") == "text":
                p["cache_control"] = {"type": "ephemeral"}
                return out
        return out
    return content


def _extract_cache_tokens(usage: Any, which: str) -> Optional[int]:
    """Pull cache read/write token counts out of a usage object regardless
    of provider shape.

    Anthropic-via-OpenRouter returns ``cache_read_input_tokens`` /
    ``cache_creation_input_tokens`` (top-level on usage). The OpenAI shape
    nests cached reads under ``prompt_tokens_details.cached_tokens`` (no
    write counter). Returns None when neither is present.
    """
    if usage is None:
        return None
    if which == "read":
        v = getattr(usage, "cache_read_input_tokens", None)
        if v is not None:
            return int(v)
        details = getattr(usage, "prompt_tokens_details", None)
        cached = getattr(details, "cached_tokens", None) if details else None
        return int(cached) if cached is not None else None
    if which == "write":
        v = getattr(usage, "cache_creation_input_tokens", None)
        if v is not None:
            return int(v)
        # OpenRouter nests the write counter under prompt_tokens_details.
        details = getattr(usage, "prompt_tokens_details", None)
        w = getattr(details, "cache_write_tokens", None) if details else None
        return int(w) if w is not None else None
    return None


def _inject_prompt_cache(messages: Any) -> int:
    """Add ``cache_control`` breakpoints to maximise prompt-cache reuse.

    Anthropic allows up to 4 breakpoints. We place them on:
      1. the system message (the big, stable AGENTS.md + skill prefix), and
      2. the last message BEFORE the newest user turn (caches the growing
         conversation transcript across a multi-turn tool loop).

    Mutates ``messages`` in place. Returns the number of breakpoints set so
    the caller can log it. Safe to call repeatedly / on any shape — it
    no-ops on anything it doesn't recognise.
    """
    if not isinstance(messages, list) or not messages:
        return 0
    marked = 0

    # Breakpoint 1: the first system message.
    for msg in messages:
        if isinstance(msg, dict) and msg.get("role") == "system":
            msg["content"] = _to_cached_content(msg.get("content"))
            marked += 1
            break

    # Breakpoint 2: the message just before the final user turn — i.e. the
    # tail of the prior conversation. Only worth it when there's history
    # beyond [system, user].
    if len(messages) >= 3:
        # Walk back from the end past the trailing user turn(s) to the last
        # assistant/tool message and cache up to there.
        idx = len(messages) - 1
        # skip the latest user message(s) — they vary every turn
        while idx >= 0 and isinstance(messages[idx], dict) and messages[idx].get("role") == "user":
            idx -= 1
        if idx >= 1:  # don't double-mark the system message at index 0
            tgt = messages[idx]
            if isinstance(tgt, dict) and tgt.get("role") in ("assistant", "tool"):
                tgt["content"] = _to_cached_content(tgt.get("content"))
                marked += 1
    return marked


# Upstream 429 (provider rate-limit) back-off. When the upstream LLM provider
# (e.g. OpenRouter) throttles, we do NOT switch models and we do NOT dead-end
# the build — we WAIT and retry, transparently, so the caller's single request
# blocks through the throttle window and gets a real answer. Respect the
# provider's Retry-After when present; otherwise exponential back-off w/ jitter.
_LLM_429_MAX_RETRIES = int(os.getenv("SMART_APP_LLM_429_MAX_RETRIES", "5"))
_LLM_429_BASE_DELAY = float(os.getenv("SMART_APP_LLM_429_BASE_DELAY", "5"))
_LLM_429_CAP = float(os.getenv("SMART_APP_LLM_429_CAP", "60"))


async def _create_with_429_backoff(client: Any, upstream_kwargs: dict, *, model: str) -> Any:
    """Call upstream chat-completions, backing off + retrying ONLY on a 429
    (provider rate limit). A throttle becomes a WAIT, not a failure — no model
    switch. Re-raises on any non-429 error, or on a 429 after max retries."""
    import asyncio as _asyncio
    import random as _random

    attempt = 0
    while True:
        try:
            return await client.chat.completions.create(**upstream_kwargs)
        except APIStatusError as e:
            if getattr(e, "status_code", None) != 429 or attempt >= _LLM_429_MAX_RETRIES:
                raise
            # Prefer the provider's Retry-After; else exponential back-off (cap).
            retry_after = 0.0
            try:
                resp = getattr(e, "response", None)
                hdr = resp.headers.get("retry-after") if resp is not None else None
                retry_after = float(hdr) if hdr else 0.0
            except Exception:  # noqa: BLE001 — header parse is best-effort
                retry_after = 0.0
            delay = retry_after if retry_after > 0 else min(
                _LLM_429_BASE_DELAY * (2 ** attempt), _LLM_429_CAP
            )
            delay += _random.uniform(0, min(2.0, _LLM_429_BASE_DELAY))  # jitter
            _LLM_AUDIT.warning(
                "[llm_proxy] upstream 429 (provider rate limit) model=%s — "
                "backing off %.1fs then retrying (attempt %d/%d, no model switch)",
                model, delay, attempt + 1, _LLM_429_MAX_RETRIES,
            )
            await _asyncio.sleep(delay)
            attempt += 1


async def _llm_proxy_handler(
    request: Request, *, body: dict, claims: InternalBearerClaims
) -> Any:
    import json as _json

    settings = get_settings()

    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="body must be a JSON object")

    requested_model = body.get("model")
    if not isinstance(requested_model, str) or not requested_model.strip():
        raise HTTPException(status_code=400, detail="model is required")

    # Fail-closed model allowlist. Even if both the explicit allowlist and
    # LLM_LARGE_MODEL are unset, we refuse — never let the builder pod
    # reach an arbitrary model on our credential.
    allowed = settings.llm_proxy_allowed_models
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "LLM proxy not configured: set LLM_LARGE_MODEL or"
                " SMART_APP_LLM_PROXY_ALLOWED_MODELS"
            ),
        )
    if requested_model not in allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "model_not_allowed",
                "model": requested_model,
                "allowed": sorted(allowed),
            },
        )

    try:
        call_no = reserve_call(
            claims.subject,
            max_calls=settings.llm_proxy_max_calls_per_session,
            max_tokens=settings.llm_proxy_max_tokens_per_session,
        )
    except BudgetExceeded as e:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "error": "session_budget_exceeded",
                "kind": e.kind,
                "used": e.used,
                "limit": e.limit,
            },
        ) from e

    upstream_model = requested_model
    stream = bool(body.get("stream", False))
    client = get_llm_client(settings)

    # Build the upstream kwargs by passing through every OpenAI-known
    # parameter the caller supplied. We do not whitelist individual fields
    # so new SDK features (response_format, parallel_tool_calls, ...) work
    # without changes here.
    upstream_kwargs = {k: v for k, v in body.items() if k != "model"}
    upstream_kwargs["model"] = upstream_model

    # Reasoning-headroom floor: REASONING models (GLM/DeepSeek/QwQ) spend
    # hidden reasoning tokens against max_tokens, so a low (or absent) per-call
    # cap truncates mid-think and returns empty/partial content to the builder.
    # The floor applies ONLY to reasoning-class models — non-reasoning upstreams
    # (e.g. Anthropic models with an 8K output ceiling) would REJECT an
    # unconditional 32K. Env-tunable; the caller may still request MORE.
    _floor = int(os.getenv("SMART_APP_LLM_PROXY_MIN_TOKENS", "32000"))
    _is_reasoning = bool(re.search(
        r"deepseek|glm|qwq|-r1\b|reasoner|thinking", upstream_model, re.IGNORECASE
    ))
    _req_mt = upstream_kwargs.get("max_tokens")
    if _is_reasoning and (not isinstance(_req_mt, int) or _req_mt < _floor):
        upstream_kwargs["max_tokens"] = _floor

    # OpenRouter routing extensions (provider pinning, model fallback list,
    # transforms, etc.) are NOT params on the typed OpenAI SDK method —
    # passing them as kwargs raises TypeError. The SDK merges arbitrary
    # JSON body fields via ``extra_body``, so we move any OpenRouter-
    # specific key there. This lets the caller pin a caching-capable
    # provider (e.g. {"provider": {"order": ["z-ai"]}}) or constrain
    # routing without us hard-coding the field list into the SDK call.
    _extra_body: Dict[str, Any] = {}
    # provider/models/transforms/route/preset are OpenRouter ROUTING extensions;
    # reasoning/thinking are provider REASONING controls (OpenRouter "reasoning",
    # DeepSeek/Anthropic-style "thinking"). None are typed params on the OpenAI
    # SDK's create(), so leaving them as kwargs raises TypeError (the builder's
    # DeepSeek turns carry "thinking" and 500'd mid-build). The SDK forwards
    # arbitrary JSON body fields via extra_body, so move them there.
    for _k in ("provider", "models", "transforms", "route", "preset", "reasoning", "thinking"):
        if _k in upstream_kwargs:
            _extra_body[_k] = upstream_kwargs.pop(_k)
    # Pin the z-ai provider (+ its reasoning directive) for GLM-class upstreams,
    # pulled from the configured LLM_LARGE_EXTRA_BODY. Without this the builder
    # pod's GLM calls carry no provider routing and OpenRouter sends them to an
    # arbitrary provider (e.g. StreamLake); the config pins
    # provider.order=["z-ai"], allow_fallbacks=false so it's z-ai ONLY (a hard
    # fail if z-ai is down, never a silent fallback), and adds the reasoning
    # directive that stops GLM leaking chain-of-thought into tool calls. Any
    # key the caller set explicitly still wins (merged last).
    if _looks_glm(upstream_model) and settings.llm_large_extra_body:
        _merged = dict(settings.llm_large_extra_body)
        # This proxy serves the BUILDER pod, whose UI SHOWS the model's
        # reasoning while it builds (the BA watches it think). So EMIT the
        # reasoning (exclude=false) here, overriding the runtime default. GLM
        # returns reasoning in a SEPARATE field, not in content, so tool calls
        # stay clean. The runtime narrator keeps exclude=true via its own
        # direct LLM call (officers must not see chain-of-thought).
        if isinstance(_merged.get("reasoning"), dict):
            _merged["reasoning"] = {**_merged["reasoning"], "exclude": False}
        _merged.update(_extra_body)  # a provider/reasoning the caller set wins
        _extra_body = _merged
    if _extra_body:
        upstream_kwargs["extra_body"] = _extra_body

    # Prompt-cache injection. The builder pod re-sends a large, stable
    # system prefix (AGENTS.md + the active SKILL.md + schemas) on every
    # tool-loop turn. Marking it with ``cache_control: ephemeral`` lets
    # OpenRouter → Anthropic serve those tokens from cache at ~0.1x cost on
    # repeat turns. The OpenAI SDK carries the content-part markers verbatim;
    # OpenRouter injects the anthropic-beta header itself. Gated on an
    # Anthropic model + an env flag (default ON) so a non-Anthropic upstream
    # never sees the extra keys.
    cache_breakpoints = 0
    cache_enabled = (
        settings.llm_proxy_prompt_cache_enabled
        and _looks_anthropic(upstream_model)
    )
    if cache_enabled:
        try:
            cache_breakpoints = _inject_prompt_cache(
                upstream_kwargs.get("messages")
            )
        except Exception:  # noqa: BLE001 — never fail a build on a cache hint
            _LLM_AUDIT.warning(
                "[llm_proxy] prompt-cache injection failed; relaying uncached",
                exc_info=True,
            )
            cache_breakpoints = 0

    if stream:
        # Force usage reporting in the final chunk so token accounting
        # actually fires. OpenRouter and the OpenAI API both honour this;
        # without it most providers omit usage from streaming responses
        # and we'd silently undercount against the per-session budget.
        opts = upstream_kwargs.get("stream_options")
        if not isinstance(opts, dict):
            opts = {}
        opts.setdefault("include_usage", True)
        upstream_kwargs["stream_options"] = opts

    if not stream:
        try:
            resp = await _create_with_429_backoff(client, upstream_kwargs, model=upstream_model)
        except APIConnectionError as e:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"upstream LLM unreachable: {e}",
            ) from e
        except APIStatusError as e:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"upstream returned {e.status_code}: {str(e)[:500]}",
            ) from e

        usage = getattr(resp, "usage", None)
        total_tokens = int(getattr(usage, "total_tokens", 0) or 0)
        try:
            tokens_used = record_tokens(
                claims.subject,
                total_tokens,
                max_tokens=settings.llm_proxy_max_tokens_per_session,
            )
        except BudgetExceeded as e:
            # Over-cap on the response — we still return *this* call's result
            # (already billed upstream) but log it. The NEXT call is now refused
            # pre-forward by reserve_call's token gate (429), so no further
            # OpenRouter spend — not just when the call counter catches up.
            tokens_used = e.used
            _LLM_AUDIT.warning(
                "[llm_proxy] subject=%s OVER token budget after call: %d/%d "
                "— next call will be refused before forwarding (no further spend)",
                claims.subject, e.used, e.limit,
            )

        # Cache-token fields come back under usage.prompt_tokens_details
        # (OpenAI shape) or as top-level cache_*_input_tokens (Anthropic via
        # OpenRouter). Surface both so we can measure the hit rate and prove
        # the savings. None when the upstream didn't cache.
        _cache_read = _extract_cache_tokens(usage, "read")
        _cache_write = _extract_cache_tokens(usage, "write")
        _LLM_AUDIT.info(
            "[llm_proxy] subject=%s tenant=%s call=%d model=%s "
            "prompt_tokens=%s completion_tokens=%s total_tokens=%s "
            "cache_read=%s cache_write=%s cache_breakpoints=%d "
            "session_total=%d",
            claims.subject,
            claims.tenant_id or "-",
            call_no,
            upstream_model,
            getattr(usage, "prompt_tokens", None),
            getattr(usage, "completion_tokens", None),
            total_tokens,
            _cache_read,
            _cache_write,
            cache_breakpoints,
            tokens_used,
        )
        return resp.model_dump(exclude_none=True)

    # ---- streaming path ----
    async def _sse() -> Any:
        chunk_total_tokens = 0
        try:
            stream_iter = await _create_with_429_backoff(client, upstream_kwargs, model=upstream_model)
        except APIConnectionError as e:
            err = {"error": "upstream_unreachable", "detail": str(e)}
            yield f"data: {_json.dumps(err)}\n\n"
            yield "data: [DONE]\n\n"
            return
        except APIStatusError as e:
            err = {
                "error": "upstream_status",
                "status": e.status_code,
                "detail": str(e)[:500],
            }
            yield f"data: {_json.dumps(err)}\n\n"
            yield "data: [DONE]\n\n"
            return

        try:
            async for chunk in stream_iter:
                payload = chunk.model_dump(exclude_none=True)
                usage = payload.get("usage") or {}
                if isinstance(usage, dict) and usage.get("total_tokens"):
                    # Take the maximum — providers may emit usage in the
                    # final chunk only, or send incremental updates.
                    chunk_total_tokens = max(
                        chunk_total_tokens, int(usage["total_tokens"])
                    )
                yield f"data: {_json.dumps(payload, separators=(',', ':'))}\n\n"
        finally:
            yield "data: [DONE]\n\n"
            try:
                tokens_used = record_tokens(
                    claims.subject,
                    chunk_total_tokens,
                    max_tokens=settings.llm_proxy_max_tokens_per_session,
                )
            except BudgetExceeded as e:
                tokens_used = e.used
            _LLM_AUDIT.info(
                "[llm_proxy/stream] subject=%s tenant=%s call=%d model=%s "
                "stream_total_tokens=%d session_total=%d",
                claims.subject,
                claims.tenant_id or "-",
                call_no,
                upstream_model,
                chunk_total_tokens,
                tokens_used,
            )

    return StreamingResponse(_sse(), media_type="text/event-stream")


@router.post("/llm/v1/chat/completions")
async def internal_llm_chat_completions(
    request: Request, body: dict = Body(...)
) -> Any:
    """OpenAI-compatible chat-completions relay for the builder pod.

    The pod's OpenAI SDK is configured with ``base_url = .../smart-app/
    internal/llm/v1`` and ``api_key = <HMAC bearer>``. Both streaming
    (``stream: true``) and non-streaming requests are supported. The
    bearer must declare ``llm`` in its tools claim, and the requested
    ``model`` must be in the configured allowlist (default: only
    ``LLM_LARGE_MODEL``). Per-session call/token budgets bound runaway
    prompt-injection loops.
    """
    claims = _require_internal_claims(request, required_tool="llm")
    return await _llm_proxy_handler(request, body=body, claims=claims)


# ---------------------------------------------------------------------------
# Helpers shared by MCP / RAG routes
# ---------------------------------------------------------------------------


def _extract_user_jwt(request: Request) -> str:
    """Pulls ``X-User-JWT`` (downstream-identity header) off the request.

    The HMAC bearer authorises *which tool* may be called; the user JWT
    identifies *who* is calling it (for org/dept visibility filtering on
    discovery).
    """
    jwt = (
        request.headers.get("X-User-JWT")
        or request.headers.get("x-user-jwt")
        or ""
    ).strip()
    if not jwt:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "missing_user_jwt",
                "message": "X-User-JWT header required for MCP/RAG proxy calls",
            },
        )
    return jwt


def _proxy_error_to_http(e: ProxyError, *, tag: str, subject: str) -> HTTPException:
    logger.warning("[internal/%s] %s (subject=%s, code=%s)", tag, e, subject, e.code)
    return HTTPException(
        status_code=e.status,
        detail={"code": e.code, "message": str(e)},
    )


# ---------------------------------------------------------------------------
# MCP proxy
# ---------------------------------------------------------------------------


@router.post("/mcp")
async def internal_mcp(request: Request, body: dict = Body(...)) -> dict:
    """MCP tool / query proxy.

    Body::

        {
          "source_id": "insurance-policies",
          "tool_name"?: "lookup_policy",   # currently advisory \u2014 forwarded
          "query"?: "<natural-language query>",
          "args"?: {<extra fields forwarded to dept-MCP /query>},
          "max_results"?: 10
        }

    Authorisation: bearer must declare ``mcp`` AND have a binding for
    ``(source_id, tool_name)`` (or for ``source_id`` with no tool_name).
    """
    claims = _require_internal_claims(request, required_tool="mcp")
    settings = get_settings()

    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="body must be a JSON object")
    source_id = body.get("source_id")
    tool_name = body.get("tool_name")
    if not isinstance(source_id, str) or not source_id:
        raise HTTPException(status_code=400, detail="source_id is required")
    if tool_name is not None and not isinstance(tool_name, str):
        raise HTTPException(status_code=400, detail="tool_name must be a string")

    if not claims.allows_mcp(source_id, tool_name):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "tool_binding_not_authorised",
                "kind": "mcp",
                "source_id": source_id,
                "tool_name": tool_name,
                "subject": claims.subject,
            },
        )

    user_jwt = _extract_user_jwt(request)

    forward_body: Dict[str, Any] = {}
    if "query" in body:
        forward_body["query"] = body["query"]
    if "max_results" in body:
        forward_body["max_results"] = body["max_results"]
    if isinstance(body.get("args"), dict):
        forward_body.update(body["args"])
    if tool_name:
        forward_body.setdefault("tool_name", tool_name)

    try:
        result = await call_dept_mcp_query(
            settings=settings,
            user_jwt=user_jwt,
            source_id=source_id,
            body=forward_body,
        )
    except ProxyError as e:
        raise _proxy_error_to_http(e, tag="mcp", subject=claims.subject) from e

    return result


# ---------------------------------------------------------------------------
# RAG proxy
# ---------------------------------------------------------------------------


@router.post("/rag")
async def internal_rag(request: Request, body: dict = Body(...)) -> dict:
    """RAG search proxy.

    Body::

        {
          "source_id": "insurance-policies",
          "query": "<query text>",
          "top_k"?: 8,
          "filters"?: {<dept-MCP-specific filter object>}
        }

    Authorisation: bearer must declare ``rag`` AND list ``source_id``
    in its ``rag`` bindings.
    """
    claims = _require_internal_claims(request, required_tool="rag")
    settings = get_settings()

    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="body must be a JSON object")
    source_id = body.get("source_id")
    query = body.get("query")
    top_k = body.get("top_k")
    filters = body.get("filters")

    if not isinstance(source_id, str) or not source_id:
        raise HTTPException(status_code=400, detail="source_id is required")
    if not isinstance(query, str) or not query.strip():
        raise HTTPException(status_code=400, detail="query is required")
    if top_k is not None and (not isinstance(top_k, int) or top_k < 1 or top_k > 50):
        raise HTTPException(status_code=400, detail="top_k must be an int in [1, 50]")
    if filters is not None and not isinstance(filters, dict):
        raise HTTPException(status_code=400, detail="filters must be an object")

    if not claims.allows_rag(source_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "tool_binding_not_authorised",
                "kind": "rag",
                "source_id": source_id,
                "subject": claims.subject,
            },
        )

    user_jwt = _extract_user_jwt(request)

    # RAG short-circuit: a RAG source is a semantic corpus, answered by the
    # Citra-Service platform reader, NEVER the dept-MCP (pure disconnect — the
    # MCP does no RAG). Citra-Service verifies the end-user JWT and enforces
    # dept scope server-side.
    try:
        result = await call_citra_semantic_search(
            settings=settings,
            user_jwt=user_jwt,
            source_id=source_id,
            query=query,
            top_k=min(top_k or 8, 100),
            filters=filters if isinstance(filters, dict) and filters else None,
            # No forwarded end-user JWT (automated runtime call) ⇒ mint a trusted
            # service token from the caller's org.
            org_id=claims.tenant_id,
            on_behalf_of=claims.subject,
        )
    except ProxyError as e:
        raise _proxy_error_to_http(e, tag="rag", subject=claims.subject) from e

    return result


# ---------------------------------------------------------------------------
# Catalogue (builder-only)
# ---------------------------------------------------------------------------


@router.get("/catalogue")
async def internal_catalogue(request: Request) -> dict:
    """Enumerate pre-built assets the BA can hook into a smart-app:
    MCP sources and RAG corpora.

    Why builder-only: enumeration is a build-time concern. Runtime
    bearers are tightly scoped to specific bindings and have no need
    to discover new tools. Restricting this to ``kind=builder``
    bearers means a leaked runtime token can't enumerate the tenant.

    Each list is best-effort — a missing/disabled upstream returns an
    empty list rather than failing the whole call, so the builder skill
    can still propose the lists that *did* resolve.
    """
    claims = _require_internal_claims(request)
    if claims.kind != "builder":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "catalogue_builder_only",
                "subject": claims.subject,
            },
        )
    settings = get_settings()
    user_jwt = _extract_user_jwt(request)

    sources: list[Dict[str, Any]] = []
    sources_error: Optional[str] = None

    if settings.mcp_enabled:
        try:
            sources = await list_discovery_sources(
                settings=settings, user_jwt=user_jwt
            )
        except ProxyError as e:
            logger.warning("[internal/catalogue] sources failed: %s", e)
            sources_error = e.code

    # Split discovery sources into MCP-style vs RAG-style. ``semantic``
    # source_type is the convention for vector-search corpora; everything
    # else is treated as an MCP action source.
    mcp_sources = [s for s in sources if (s.get("source_type") or "") != "semantic"]
    rag_sources = [s for s in sources if (s.get("source_type") or "") == "semantic"]

    return {
        "mcp_sources": mcp_sources,
        "rag_sources": rag_sources,
        "errors": {
            "sources": sources_error,
        },
    }


# ---------------------------------------------------------------------------
# Discovery cache invalidation
# ---------------------------------------------------------------------------


@router.post("/discovery/invalidate")
async def internal_discovery_invalidate(
    request: Request, body: dict = Body(...)
) -> dict:
    """Drop cached resolutions for a ``source_id``.

    Called by discovery-service / dept-MCP when a source is removed or
    its endpoint is rotated, so subsequent runtime tool calls re-resolve
    the new coordinates instead of serving stale cache for up to
    ``cache_ttl_seconds`` (default 5 minutes).

    Auth: any valid internal HMAC bearer. We don't tool-gate this
    because it's a write-only cache mutation; the worst a leaked bearer
    can do is force re-resolution traffic.
    """
    _require_internal_claims(request)
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="body must be a JSON object")
    source_id = body.get("source_id")
    if not isinstance(source_id, str) or not source_id.strip():
        raise HTTPException(
            status_code=400, detail="source_id must be a non-empty string"
        )

    from discovery_cache import invalidate_source

    dropped = invalidate_source(source_id.strip())
    logger.info(
        "[internal/discovery/invalidate] source_id=%s dropped=%d",
        source_id, dropped,
    )
    return {"source_id": source_id, "entries_dropped": dropped}


