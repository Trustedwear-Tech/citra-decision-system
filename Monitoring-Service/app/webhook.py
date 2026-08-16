# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""
Alertmanager webhook receiver.

Runs a minimal FastAPI app inside the Monitoring-Service that accepts
Alertmanager's native JSON payload at POST /webhook/alert and fans each
incoming alert out through the existing AlertManager transport (SES, SMTP,
webhook, or log).

Mount the server from main.py via `run_webhook_server(alert_manager)` — it
spins up a uvicorn in a daemon thread so the existing monitoring loops keep
running.

Payload reference (Alertmanager v2 webhook):
    {
      "version": "4",
      "status": "firing" | "resolved",
      "alerts": [
        {
          "status": "firing" | "resolved",
          "labels": {"alertname": "...", "severity": "...", "dept": "...",
                     "component": "...", ...},
          "annotations": {"summary": "...", "description": "..."},
          "fingerprint": "abcdef...",
          "startsAt": "...", "endsAt": "..."
        }
      ]
    }
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any, Dict, List, Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Request

from .alert_manager import AlertManager

logger = logging.getLogger(__name__)


def _format_alert(alert: Dict[str, Any]) -> Dict[str, str]:
    """Flatten one Alertmanager alert into the fields AlertManager.send_alert wants."""
    labels = alert.get("labels", {}) or {}
    annotations = alert.get("annotations", {}) or {}

    alertname = labels.get("alertname", "UnknownAlert")
    severity = labels.get("severity", "info")
    component = labels.get("component", "platform")
    dept = labels.get("dept") or labels.get("org") or "central"
    status = alert.get("status", "firing")

    summary = annotations.get("summary") or alertname
    description = annotations.get("description") or ""

    subject = f"[Citra][{severity.upper()}][{status}] {summary}"
    body_lines = [
        f"Alert:       {alertname}",
        f"Status:      {status}",
        f"Severity:    {severity}",
        f"Component:   {component}",
        f"Dept/Org:    {dept}",
        f"Summary:     {summary}",
        "",
        description,
        "",
        "Labels:",
    ]
    for k, v in sorted(labels.items()):
        body_lines.append(f"  {k}: {v}")

    return {
        "source": f"{component}:{dept}",
        "alert_type": alertname.lower(),
        "subject": subject,
        "body": "\n".join(body_lines),
        "fingerprint": alert.get("fingerprint") or f"{alertname}:{dept}:{component}",
    }


def _format_glitchtip(payload: Dict[str, Any]) -> Dict[str, str]:
    """Flatten a GlitchTip "general webhook" payload into AlertManager fields.

    GlitchTip posts a Slack-style body when an issue alert fires:
        {
          "alias": "GlitchTip",
          "text": "GlitchTip Alert",
          "attachments": [
            {"title": "...", "title_link": "https://.../issues/123",
             "text": "...", "color": "#e52b50", "fields": [...]}
          ],
          "sections": [...]            # google-chat variant
        }
    The exact shape drifts between GlitchTip versions, so every field is read
    defensively and we fall back to whatever text we can find.
    """
    attachments = [a for a in (payload.get("attachments") or []) if isinstance(a, dict)]
    first = attachments[0] if attachments else {}

    top_text = (payload.get("text") or "").strip()
    title = (first.get("title") or top_text or "GlitchTip issue").strip()
    link = (first.get("title_link") or "").strip()

    body_lines: List[str] = []
    if top_text and top_text.lower() != title.lower():
        body_lines += [top_text, ""]

    for att in attachments:
        t = (att.get("title") or "").strip()
        if t:
            body_lines.append(t)
        att_link = (att.get("title_link") or "").strip()
        if att_link:
            body_lines.append(f"  {att_link}")
        at = (att.get("text") or "").strip()
        if at:
            body_lines.append(f"  {at}")
        for f in att.get("fields") or []:
            if isinstance(f, dict) and f.get("title"):
                body_lines.append(f"  {f.get('title')}: {f.get('value', '')}")
        body_lines.append("")

    body = "\n".join(body_lines).strip() or title

    # Stable per-issue fingerprint so AlertManager's cooldown collapses repeats
    # of the same issue. The issue URL is unique; fall back to the title.
    fingerprint = link or f"glitchtip:{title}"

    return {
        "source": "glitchtip",
        "alert_type": "exception",
        "subject": f"[Citra][GlitchTip] {title}",
        "body": body,
        "fingerprint": fingerprint,
    }


def build_webhook_app(alert_manager: AlertManager) -> FastAPI:
    app = FastAPI(
        title="Citra Monitoring — Alertmanager Webhook",
        version="1.0.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.get("/health")
    async def health() -> Dict[str, str]:
        return {"status": "ok", "transport": alert_manager.transport}

    @app.post("/webhook/alert")
    async def webhook_alert(request: Request) -> Dict[str, Any]:
        try:
            payload = await request.json()
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"invalid json: {exc}")

        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="payload must be a JSON object")

        alerts: List[Dict[str, Any]] = payload.get("alerts") or []
        if not alerts:
            logger.info("webhook/alert received empty alert batch")
            return {"accepted": 0}

        dispatched = 0
        for alert in alerts:
            try:
                fmt = _format_alert(alert)
                # AlertManager.send_alert already enforces cooldown via
                # fingerprint — safe against the same alert repeating.
                alert_manager.send_alert(
                    source=fmt["source"],
                    alert_type=fmt["alert_type"],
                    subject=fmt["subject"],
                    body=fmt["body"],
                    fingerprint=fmt["fingerprint"],
                )
                dispatched += 1
            except Exception as exc:
                logger.error("webhook/alert failed to dispatch one alert: %s", exc, exc_info=True)

        return {"accepted": len(alerts), "dispatched": dispatched}

    @app.post("/webhook/glitchtip")
    async def webhook_glitchtip(request: Request) -> Dict[str, Any]:
        """Receive GlitchTip's general-webhook payload and relay it through the
        existing SES transport. Wired as a GlitchTip project Alert → Webhook
        recipient (http://monitoring-service:8400/webhook/glitchtip) so error
        notifications reuse the same email path as Alertmanager alerts."""
        try:
            payload = await request.json()
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"invalid json: {exc}")

        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="payload must be a JSON object")

        # GlitchTip alerts go to their own recipient list (independent of the
        # infra-alert recipients), configurable via GLITCHTIP_ALERT_TO.
        gt_to = [
            e.strip()
            for e in os.getenv(
                "GLITCHTIP_ALERT_TO",
                "rohit@trustedweartech.com,deeepakumar@gmail.com",
            ).split(",")
            if e.strip()
        ]

        try:
            fmt = _format_glitchtip(payload)
            # send_alert enforces per-fingerprint cooldown — safe against the
            # same issue re-firing.
            alert_manager.send_alert(
                source=fmt["source"],
                alert_type=fmt["alert_type"],
                subject=fmt["subject"],
                body=fmt["body"],
                fingerprint=fmt["fingerprint"],
                to=gt_to or None,
            )
        except Exception as exc:
            logger.error("webhook/glitchtip failed to dispatch: %s", exc, exc_info=True)
            raise HTTPException(status_code=500, detail="dispatch failed")

        return {"accepted": 1, "dispatched": 1}

    # Prometheus /metrics endpoint
    try:
        from prometheus_fastapi_instrumentator import Instrumentator
        Instrumentator().instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)
    except ImportError:
        logger.warning("prometheus-fastapi-instrumentator not installed; /metrics disabled")

    return app


def run_webhook_server(alert_manager: AlertManager) -> Optional[threading.Thread]:
    """Start the webhook FastAPI in a daemon thread. No-op if disabled."""
    if os.getenv("MONITORING_WEBHOOK_ENABLED", "true").lower() not in ("1", "true", "yes"):
        logger.info("Monitoring webhook disabled via MONITORING_WEBHOOK_ENABLED")
        return None

    host = os.getenv("MONITORING_WEBHOOK_HOST", "0.0.0.0")  # nosec B104
    port = int(os.getenv("MONITORING_WEBHOOK_PORT", "8400"))

    app = build_webhook_app(alert_manager)
    config = uvicorn.Config(app, host=host, port=port, log_level="info", access_log=False)
    server = uvicorn.Server(config)

    thread = threading.Thread(target=server.run, name="monitoring-webhook", daemon=True)
    thread.start()
    logger.info("Alertmanager webhook listening on %s:%d/webhook/alert", host, port)
    return thread
