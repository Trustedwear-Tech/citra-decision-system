# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""Internet search and URL fetch via the Citra-Service proxy.

The sandbox has no direct internet egress. ``web.search()`` calls Serper
(with DuckDuckGo fallback) server-side; ``web.fetch()`` performs a
SSRF-guarded GET. PDF responses come back as raw bytes for ``docs.extract_pdf``.

Every successful call is auto-recorded into the active research audit
trail (see ``citra_toolkit.research``), so the agent doesn't have to.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass, field
from typing import Any

from ._proxy import proxy_url
from .client import http_client


@dataclass
class Hit:
    title: str
    url: str
    snippet: str
    source: str  # "serper" | "duckduckgo"

    def as_dict(self) -> dict[str, Any]:
        return {"title": self.title, "url": self.url, "snippet": self.snippet, "source": self.source}


@dataclass
class Page:
    url: str
    status: int
    content_type: str
    text: str  # extracted clean text by default (HTML stripped, PDF parsed)
    body: bytes  # empty for text content; populated for PDF / octet-stream
    bytes_len: int
    bucket_url: str | None = None  # populated when cache_to_bucket=True
    raw_html: str = ""  # original HTML when extract=True replaced .text
    tables: list[list[list[str]]] = field(default_factory=list)  # PDF tables
    extracted: bool = False  # True when .text came from docs.extract_*

    @property
    def is_pdf(self) -> bool:
        return self.content_type == "application/pdf"

    @property
    def is_html(self) -> bool:
        return self.content_type.startswith("text/html")


def search(query: str, *, top_k: int = 10, freshness: str | None = None,
           timeout: float = 30.0) -> list[Hit]:
    """Run a web search. Returns a list of ``Hit`` objects."""
    body: dict[str, Any] = {"query": query, "top_k": int(top_k)}
    if freshness:
        body["freshness"] = freshness
    with http_client(timeout=timeout) as c:
        r = c.post(proxy_url("web-search"), json=body)
        r.raise_for_status()
        payload = r.json() or {}
    hits = [
        Hit(
            title=str(item.get("title") or ""),
            url=str(item.get("url") or ""),
            snippet=str(item.get("snippet") or ""),
            source=str(item.get("source") or payload.get("provider") or ""),
        )
        for item in (payload.get("results") or [])
    ]
    try:
        from . import research as _research  # circular-safe
        _research.auto_step("web.search", {"query": query, "top_k": top_k},
                            summary=f"{len(hits)} results", citations=[h.url for h in hits if h.url])
    except Exception:  # noqa: BLE001
        pass
    return hits


def fetch(url: str, *, max_bytes: int = 2_000_000, timeout: float = 30.0,
          cache_to_bucket: bool = False, extract: bool = True,
          ocr_pdf: bool = False) -> Page:
    """Server-side fetch with SSRF guard. Returns a ``Page`` object.

    By default (``extract=True``) the response is run through the best
    available extractor for its content-type and ``page.text`` holds
    clean prose:

    * ``text/html`` → ``docs.extract_html`` (trafilatura → readability →
      BeautifulSoup cascade). The original markup is preserved on
      ``page.raw_html``.
    * ``application/pdf`` → ``docs.extract_pdf`` (pdfminer.six + pypdf
      + pdfplumber). Tables land on ``page.tables``; pass
      ``ocr_pdf=True`` to fall back to Qwen3-VL on scanned pages.
    * ``text/*`` → returned as-is (already plain text).

    Set ``extract=False`` to keep raw bytes / raw HTML on ``page.text``
    and ``page.body`` (rare — mostly for DOM walking or when you
    explicitly want to chunk markup).

    If ``cache_to_bucket`` is true, the raw response body is also
    uploaded to the intermediate bucket lane (24-hour expiry) and the
    presigned URL is set on ``Page.bucket_url`` — handy for adding
    re-runnable evidence to the audit trail.
    """
    body = {"url": url, "max_bytes": int(max_bytes)}
    with http_client(timeout=timeout) as c:
        r = c.post(proxy_url("web-fetch"), json=body)
        r.raise_for_status()
        data = r.json() or {}
    body_bytes = b""
    if data.get("body_b64"):
        try:
            body_bytes = base64.b64decode(data["body_b64"])
        except Exception:  # noqa: BLE001
            body_bytes = b""
    page = Page(
        url=str(data.get("url") or url),
        status=int(data.get("status") or 0),
        content_type=str(data.get("content_type") or ""),
        text=str(data.get("text") or ""),
        body=body_bytes,
        bytes_len=int(data.get("bytes") or 0),
    )

    if extract:
        try:
            from . import docs as _docs  # circular-safe
            if page.is_pdf and body_bytes:
                doc = _docs.extract_pdf(body_bytes, ocr=ocr_pdf)
                page.text = doc.text or ""
                page.tables = doc.tables or []
                page.extracted = True
            elif page.is_html and page.text:
                page.raw_html = page.text
                clean = _docs.extract_html(page.text).text or ""
                if clean:
                    page.text = clean
                    page.extracted = True
            elif page.content_type.startswith("application/") and body_bytes:
                # Office/zip-magic etc. — let docs.extract sniff and route.
                doc = _docs.extract(body_bytes, hint_mime=page.content_type)
                if doc.text:
                    page.text = doc.text
                    page.tables = doc.tables or []
                    page.extracted = True
        except Exception:  # noqa: BLE001 — extraction is best-effort
            pass

    if cache_to_bucket and (body_bytes or page.text):
        try:
            payload_bytes = body_bytes if body_bytes else page.text.encode("utf-8", "replace")
            content_type = page.content_type or ("text/plain" if page.text else "application/octet-stream")
            # Derive a filename from the URL path if possible.
            from urllib.parse import urlparse
            path = urlparse(page.url).path or "/"
            filename = path.rsplit("/", 1)[-1] or "page"
            with http_client(timeout=timeout) as c:
                r2 = c.post(proxy_url("intermediate"), json={
                    "filename": filename,
                    "content_type": content_type,
                    "content_b64": base64.b64encode(payload_bytes).decode("ascii"),
                })
                if r2.status_code < 400:
                    page.bucket_url = (r2.json() or {}).get("presigned_url")
        except Exception:  # noqa: BLE001 — caching is best-effort
            pass

    try:
        from . import research as _research
        _research.auto_step(
            "web.fetch",
            {"url": url, "max_bytes": max_bytes, "cached": bool(page.bucket_url),
             "extracted": page.extracted},
            summary=f"{page.status} {page.content_type} {page.bytes_len}B"
                    + (f" → {len(page.text)} chars clean" if page.extracted else ""),
            citations=[page.url],
        )
    except Exception:  # noqa: BLE001
        pass
    return page
