# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""citra_visual_review — the builder's visual-QA worker, exposed at MCP level.

OpenClaw works at the MCP level, so the SmartApp builder agent calls this tool
naturally (no HTTP plumbing of its own). It composes two existing services:

  1. playwright-render-service  GET /screenshot   — headless Chromium PNG of a
     rendered app page (the browser runs THERE, never in the agent sandbox or
     in this service).
  2. the vision model (GLM-4.6V) via the user-data backend's /internal/ocr
     route with a custom dashboard-QA prompt — it reasons about what a human
     reviewer would see and returns structured issues.

Returns {passed, overall_ok, issues:[{severity, area, description, likely_fix}]}
so the main agent can fix-and-re-render before sharing a preview URL.
"""
from __future__ import annotations

import base64
import json
import logging
import time
from typing import Any, Optional
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

import httpx
import jwt

from auth import CallerContext
from config import get_config
from ._forward import call_internal
from .registry import ToolSpec, register_tool

logger = logging.getLogger(__name__)

# Identity claims copied verbatim from the caller into the fresh render token.
_RENDER_TOKEN_CLAIMS = (
    "user_id", "email", "name", "org_id", "tenant_id",
    "dept_ids", "roles", "work_sa_id", "service_account_admin_of",
)


def _mint_render_token(caller: CallerContext, ttl_seconds: int = 900) -> str:
    """Mint a FRESH short-lived runtime token from the caller's own identity.

    The headless render loads the app via the runtime's ``?_t=`` handoff, which
    needs a valid user-identity JWT. Relying on whatever token the agent pastes
    into the URL is fragile — across a multi-turn build it ages out ("token
    expired"). Instead we re-sign the caller's identity claims (same JWT_SECRET
    + issuer the caller was validated with) with a fresh ~15-min expiry, so the
    render always gets a live token regardless of build age.
    """
    cfg = get_config()
    claims = caller.raw_claims or {}
    now = int(time.time())
    out: dict[str, Any] = {
        k: claims.get(k) for k in _RENDER_TOKEN_CLAIMS if claims.get(k) is not None
    }
    out.setdefault("user_id", caller.user_id)
    out["iss"] = claims.get("iss") or cfg.jwt_issuer
    out["iat"] = now
    out["exp"] = now + ttl_seconds
    return jwt.encode(out, cfg.jwt_secret, algorithm="HS256")


def _with_fresh_token(url: str, caller: CallerContext) -> str:
    """Strip any stale ``_t`` from the URL and append a fresh render token."""
    parts = urlsplit(url)
    q = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != "_t"]
    q.append(("_t", _mint_render_token(caller)))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(q), parts.fragment))


def _rewrite_to_internal(url: str) -> str:
    """Public app host → internal runtime URL, when configured.

    The public host sits behind Cloudflare; from the render container that
    resolves IPv6-only and the screenshot dies as "DNS/unreachable" even though
    the app itself is healthy (live-hit 2026-07-20: every builder render gate
    on the box). With APP_PUBLIC_HOST + APP_RUNTIME_INTERNAL_URL set, the
    review URL keeps its path/query/token but loads over the shared docker
    network instead. Unconfigured ⇒ unchanged (dev setups reach the app
    directly)."""
    cfg = get_config()
    if not (cfg.app_public_host and cfg.app_runtime_internal_url):
        return url
    parts = urlsplit(url)
    if parts.hostname != cfg.app_public_host.strip().lower():
        return url
    base = urlsplit(cfg.app_runtime_internal_url)
    logger.info("[visual_review] rewriting %s -> %s for the render",
                parts.netloc, base.netloc)
    return urlunsplit((base.scheme or "http", base.netloc,
                       parts.path, parts.query, parts.fragment))

_QA_PROMPT = (
    "You are a meticulous QA reviewer for an OPERATIONS DASHBOARD an officer "
    "relies on. You are shown a screenshot of ONE rendered page. Judge ONLY "
    "what is visible. Report problems a user would notice:\n"
    "- charts that are blank/empty or show no series/bars/lines\n"
    "- KPI tiles showing '—', 'NaN', blank, or placeholder values\n"
    "- overlapping or unreadable legends, axis labels, or text\n"
    "- raw/ugly axis labels (e.g. ISO timestamps like 2026-03-02T00:00:00)\n"
    "- visible error text ('No data', 'could not resolve', stack traces)\n"
    "- broken / cut-off / overflowing layout\n"
    "Do NOT invent problems. If the page looks correct and populated, say so.\n"
    "Respond with STRICT JSON only, no prose:\n"
    '{"overall_ok": true|false, "issues": [{"severity": "fail"|"warn", '
    '"area": "<tile/chart/region>", "description": "<what is wrong>", '
    '"likely_fix": "<concrete fix>"}]}'
)

_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "url": {
            "type": "string",
            "description": "Full URL of the rendered app page to review, including any "
                           "?_t=<token> handoff the runtime needs.",
        },
        "context": {
            "type": "string",
            "description": "Optional: what the page is supposed to show (helps the reviewer).",
        },
        "width": {"type": "integer", "description": "Viewport width (default 1440)."},
        "height": {"type": "integer", "description": "Viewport height (default 1600)."},
        "full_page": {"type": "boolean", "description": "Capture the full scrollable page (default true)."},
    },
    "required": ["url"],
    "additionalProperties": False,
}


def _extract_json_object(text: str) -> Optional[dict[str, Any]]:
    """First balanced {...} JSON object out of a model response (may be fenced)."""
    if not text:
        return None
    s = text.strip()
    if s.startswith("```"):
        s = s.split("```", 2)[1] if s.count("```") >= 2 else s
        s = s[4:] if s[:4].lower() == "json" else s
    start = s.find("{")
    if start < 0:
        return None
    depth = 0
    for i in range(start, len(s)):
        if s[i] == "{":
            depth += 1
        elif s[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(s[start : i + 1])
                except (ValueError, TypeError):
                    return None
    return None


async def _exec(args: dict[str, Any], caller: CallerContext) -> dict[str, Any]:
    url = (args.get("url") or "").strip()
    if not url:
        raise ValueError("url is required")
    cfg = get_config()
    if not cfg.render_service_url:
        raise ValueError("RENDER_SERVICE_URL is not configured for this MCP")

    # Always attach a FRESH token — never trust the (possibly stale) ?_t= the
    # agent pasted into the URL. This is what fixes the "token expired" render.
    url = _with_fresh_token(url, caller)
    # Public host → internal runtime (when configured) so the headless browser
    # loads the app over the docker network instead of Cloudflare.
    url = _rewrite_to_internal(url)

    # 1) Screenshot via playwright-render-service (browser lives there).
    params: dict[str, Any] = {
        "url": url,
        "width": int(args.get("width") or 1440),
        "height": int(args.get("height") or 1600),
        "full_page": "true" if args.get("full_page", True) else "false",
        "settle_ms": 3500,  # let echarts/canvas paint
    }
    shot_url = cfg.render_service_url.rstrip("/") + "/screenshot"
    try:
        async with httpx.AsyncClient(timeout=90.0) as client:
            r = await client.get(shot_url, params=params)
            r.raise_for_status()
            png = r.content
    except httpx.HTTPError as e:
        raise RuntimeError(f"render-service screenshot failed: {e}") from e
    if not png:
        raise RuntimeError("render-service returned an empty screenshot")

    # 2) Vision critique via the shared vision proxy (custom QA prompt).
    ctx = (args.get("context") or "").strip()
    prompt = _QA_PROMPT + (f"\n\nPage context: {ctx}" if ctx else "")
    body = {
        "image_b64": base64.b64encode(png).decode("ascii"),
        "content_type": "image/png",
        "prompt": prompt,
    }
    res = await call_internal(
        "actionchat/internal/ocr", body, caller, timeout_seconds=180.0
    )
    text = (res or {}).get("text") or ""
    parsed = _extract_json_object(text) or {}
    issues = parsed.get("issues") if isinstance(parsed.get("issues"), list) else []
    has_fail = any((i or {}).get("severity") == "fail" for i in issues)
    overall_ok = parsed.get("overall_ok")
    if overall_ok is None:
        overall_ok = not has_fail
    # Only a `fail`-severity issue BLOCKS the preview URL. `warn`s are advisory
    # (surfaced to the agent/BA) — the model's holistic `overall_ok` is kept
    # separately but does not gate, so a cosmetic nit doesn't stall the build.
    return {
        "passed": not has_fail,
        "overall_ok": bool(overall_ok),
        "issues": issues,
        "model": (res or {}).get("model"),
        "raw": text if not parsed else None,  # only when JSON parse failed
    }


register_tool(ToolSpec(
    name="citra_visual_review",
    description=(
        "Visual-QA worker: renders an app page in a headless browser and has the "
        "vision model critique the screenshot for issues a user would see — blank/"
        "empty charts, overlapping or unreadable labels, '—'/missing KPIs, error "
        "text, broken layout. Returns {passed, overall_ok, issues:[{severity, area, "
        "description, likely_fix}]}. Run it on each preview page AFTER the data "
        "checks pass and BEFORE sharing the preview URL; treat a 'fail' as blocking."
    ),
    schema=_SCHEMA,
    executor=_exec,
    # The SmartApp builder is the primary caller; action-chat sandboxes may use it too.
    allowed_scopes=("smart-app-builder", "action-sandbox"),
))
