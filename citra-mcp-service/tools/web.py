# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""Web search + web fetch tools — first two members of the citra MCP
tool surface.

Both are stateless w.r.t. user context (Serper queries don't depend on
the user; the URL fetch is SSRF-guarded). We still pass the caller into
the executor so we can log per-user audit and so future tools that DO
need user scope have a uniform signature.
"""
from __future__ import annotations

import base64
import ipaddress
import logging
import re
import socket
import time
from typing import Any
from urllib.parse import urlparse

import httpx

from auth import CallerContext
from config import get_config
from .registry import ToolSpec, register_tool

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# citra_web_search
# ---------------------------------------------------------------------------
_WEB_SEARCH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": "Search query.",
        },
        "top_k": {
            "type": "integer",
            "description": "Max number of results (1-25, default 10).",
            "minimum": 1,
            "maximum": 25,
        },
        "freshness": {
            "type": "string",
            "description": "Restrict to past day/week/month/year.",
            "enum": ["d", "w", "m", "y"],
        },
    },
    "required": ["query"],
    "additionalProperties": False,
}


async def _exec_web_search(args: dict[str, Any], caller: CallerContext) -> dict[str, Any]:
    cfg = get_config()
    if not cfg.serper_api_key:
        raise RuntimeError("SERPER_API_KEY is not configured on citra-mcp-service")

    query = (args.get("query") or "").strip()
    if not query:
        raise ValueError("query is required")
    top_k = max(1, min(int(args.get("top_k") or 10), 25))
    freshness = (args.get("freshness") or "").strip() or None

    payload: dict[str, Any] = {"q": query, "num": top_k}
    if freshness in {"d", "w", "m", "y"}:
        payload["tbs"] = f"qdr:{freshness}"

    headers = {"X-API-KEY": cfg.serper_api_key, "Content-Type": "application/json"}
    started = time.time()
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.post(cfg.serper_api_url, headers=headers, json=payload)
        r.raise_for_status()
        data = r.json() or {}

    results: list[dict[str, Any]] = []
    for hit in (data.get("organic") or [])[:top_k]:
        results.append({
            "title": (hit.get("title") or "").strip(),
            "url": (hit.get("link") or "").strip(),
            "snippet": (hit.get("snippet") or "").strip(),
            "source": "serper",
        })
    elapsed_ms = int((time.time() - started) * 1000)
    logger.info(
        "[tool=citra_web_search] user=%s q_len=%d top_k=%d hits=%d elapsed_ms=%d",
        caller.user_id, len(query), top_k, len(results), elapsed_ms,
    )
    return {
        "query": query,
        "provider": "serper",
        "results": results,
        "elapsed_ms": elapsed_ms,
    }


register_tool(ToolSpec(
    name="citra_web_search",
    description=(
        "Search the public web via Serper. Returns up to top_k hits, each "
        "with title / url / snippet / source. Use when the caller needs "
        "current events, external sources, or wants to know what the web "
        "says about a topic. Last-resort source — prefer internal data "
        "(vault, dept-MCP, etc.) when the answer is in-corpus."
    ),
    schema=_WEB_SEARCH_SCHEMA,
    executor=_exec_web_search,
))


# ---------------------------------------------------------------------------
# citra_web_fetch
# ---------------------------------------------------------------------------
_WEB_FETCH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "url": {
            "type": "string",
            "description": "Absolute http(s) URL to fetch.",
        },
        "max_bytes": {
            "type": "integer",
            "description": "Body cap in bytes (default 2,000,000; hard max 16,777,216).",
            "minimum": 1024,
        },
    },
    "required": ["url"],
    "additionalProperties": False,
}


_WEB_FETCH_ALLOWED_CT = (
    "text/", "application/json", "application/xml", "application/xhtml+xml",
    "application/pdf", "application/rss+xml", "application/atom+xml",
)

# Hard cap on the page TEXT returned to the caller. `max_bytes` bounds
# the *download*; this bounds what lands in the agent's context. Without
# it a single fetch returned up to 2 MB of raw HTML — a few of those
# overflow the model's context window mid-research and the turn dies
# with no answer. ~16k chars ≈ 4k tokens: a research turn can fetch
# several pages and still synthesize.
_WEB_FETCH_TEXT_CAP = 16000


def _html_to_text(raw: str) -> str:
    """Dependency-free HTML -> readable text. citra-mcp-service has no
    extraction library, so this drops script/style/head, strips tags,
    unescapes the common entities, and collapses whitespace — enough to
    keep a fetched page small and legible instead of raw markup."""
    t = re.sub(r"(?is)<(script|style|head|noscript|svg|template)\b.*?</\1>", " ", raw)
    t = re.sub(r"(?s)<!--.*?-->", " ", t)
    t = re.sub(r"(?s)<[^>]+>", " ", t)
    for a, b in (("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
                 ("&quot;", '"'), ("&#39;", "'"), ("&nbsp;", " ")):
        t = t.replace(a, b)
    t = re.sub(r"[ \t\r\f]+", " ", t)
    t = re.sub(r"\n[ \t]*\n[ \t]*(\n[ \t]*)*", "\n\n", t)
    return t.strip()


def _is_public_ip(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            return False
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_multicast or ip.is_reserved or ip.is_unspecified):
            return False
    return True


async def _exec_web_fetch(args: dict[str, Any], caller: CallerContext) -> dict[str, Any]:
    cfg = get_config()
    url = (args.get("url") or "").strip()
    if not url:
        raise ValueError("url is required")
    max_bytes = int(args.get("max_bytes") or cfg.web_fetch_max_bytes_default)
    max_bytes = max(1024, min(max_bytes, cfg.web_fetch_max_bytes_hard))

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("only http/https URLs are allowed")
    host = parsed.hostname or ""
    if not host or not _is_public_ip(host):
        raise ValueError("host resolves to a non-public address (SSRF blocked)")

    fetch_headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36 "
            "CitraMCP/1.0"
        ),
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "application/pdf;q=0.9,*/*;q=0.8"
        ),
        "Accept-Language": "en-US,en;q=0.9",
        # Deliberately omits brotli — httpx can't decode it without the
        # optional `brotli` extra, which would silently leak compressed
        # bytes to callers (the bug we fixed in action-chat-service).
        "Accept-Encoding": "gzip, deflate",
    }

    started = time.time()
    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True, max_redirects=5) as client:
        async with client.stream("GET", url, headers=fetch_headers) as r:
            content_type = (r.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
            if content_type and not any(content_type.startswith(p) for p in _WEB_FETCH_ALLOWED_CT):
                raise ValueError(f"unsupported content-type: {content_type}")
            final_host = (urlparse(str(r.url)).hostname or "")
            if final_host and not _is_public_ip(final_host):
                raise ValueError("redirected to non-public host (SSRF blocked)")
            buf = bytearray()
            async for chunk in r.aiter_bytes():
                buf.extend(chunk)
                if len(buf) >= max_bytes:
                    buf = buf[:max_bytes]
                    break
            status = r.status_code
            final_url = str(r.url)

    is_pdf = content_type == "application/pdf"
    payload: dict[str, Any] = {
        "url": final_url,
        "status": status,
        "content_type": content_type,
        "bytes": len(buf),
    }
    if is_pdf or content_type.startswith("application/octet-stream"):
        payload["body_b64"] = base64.b64encode(bytes(buf)).decode("ascii")
        payload["text"] = ""
        payload["truncated"] = False
    else:
        decoded = bytes(buf).decode("utf-8", errors="replace")
        # HTML/XML: strip to readable text so a fetch can't dump raw
        # markup into the agent's context.
        looks_html = (
            "html" in content_type
            or "xml" in content_type
            or decoded.lstrip()[:1] == "<"
        )
        if decoded and looks_html:
            decoded = _html_to_text(decoded)
        # Hard text cap — the real overflow guard (see _WEB_FETCH_TEXT_CAP).
        if len(decoded) > _WEB_FETCH_TEXT_CAP:
            payload["text"] = (
                decoded[:_WEB_FETCH_TEXT_CAP]
                + f"\n\n[truncated — {len(decoded)} chars extracted; first "
                  f"{_WEB_FETCH_TEXT_CAP} returned. Fetch a more specific "
                  f"URL, or search again with a narrower query, if you "
                  f"need the rest.]"
            )
            payload["truncated"] = True
        else:
            payload["text"] = decoded
            payload["truncated"] = False

    elapsed_ms = int((time.time() - started) * 1000)
    payload["elapsed_ms"] = elapsed_ms
    logger.info(
        "[tool=citra_web_fetch] user=%s url=%s status=%s bytes=%d ms=%d",
        caller.user_id, url, status, len(buf), elapsed_ms,
    )
    return payload


register_tool(ToolSpec(
    name="citra_web_fetch",
    description=(
        "Server-side HTTP GET with SSRF guard. Returns decoded text "
        "(or base64 body for PDF/binary). Decodes gzip/deflate "
        "transparently. Use to read pages returned by citra_web_search "
        "or user-supplied URLs. Private/loopback hosts are rejected."
    ),
    schema=_WEB_FETCH_SCHEMA,
    executor=_exec_web_fetch,
))
