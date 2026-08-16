# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""
Error Report API — receives UI crash reports and emails them to support.

Flow:
1. UI ErrorBoundary catches an unhandled exception (web only)
2. POSTs error details to POST /api/report-error
3. This endpoint forwards an email via User-Service /send-contact-email
"""

import os
import re
import logging
import httpx
from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from typing import Optional

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Error Report"])

_USER_SERVICE_URL = os.getenv("USER_SERVICE_URL", "http://localhost:7004")
_SUPPORT_EMAIL = "support@citra-ai.com"

# Regex to strip absolute file paths from stack traces (e.g. /home/user/app/... or C:\Users\...)
_PATH_RE = re.compile(r'(?:[A-Za-z]:\\|/)(?:[\w.\-]+[/\\]){2,}', re.MULTILINE)

def _sanitize_stack(text: str, max_len: int) -> str:
    """Truncate and strip absolute file paths from stack/component traces."""
    if not text:
        return text
    sanitized = _PATH_RE.sub('<path>/', text)
    if len(sanitized) > max_len:
        sanitized = sanitized[:max_len] + "\n...(truncated)"
    return sanitized


class ErrorReportRequest(BaseModel):
    error: str = Field(..., max_length=1000, description="Error message")
    stack: Optional[str] = Field(default=None, max_length=5000, description="Stack trace")
    url: Optional[str] = Field(default=None, max_length=500, description="Page URL where error occurred")
    userAgent: Optional[str] = Field(default=None, max_length=500, description="Browser user-agent")
    userEmail: Optional[str] = Field(default=None, max_length=200, description="Logged-in user email")
    timestamp: Optional[str] = Field(default=None, max_length=50, description="ISO timestamp")
    componentStack: Optional[str] = Field(default=None, max_length=3000, description="React component stack")


@router.post("/api/report-error")
async def report_error(request: Request, body: ErrorReportRequest):
    """
    Receive a UI error report and forward it as an email to support.
    No auth required — the ErrorBoundary may fire before/without login.
    """
    try:
        user_label = body.userEmail or "anonymous"
        subject = f"UI Error Report — {body.error[:80]}"

        # Build plain-text body (sanitize paths, truncate)
        safe_stack = _sanitize_stack(body.stack, 2000)
        safe_component = _sanitize_stack(body.componentStack, 1500)

        lines = [
            f"Error: {body.error}",
            f"User: {user_label}",
            f"URL: {body.url or 'N/A'}",
            f"Timestamp: {body.timestamp or 'N/A'}",
            f"User-Agent: {body.userAgent or 'N/A'}",
            "",
            "--- Stack Trace ---",
            safe_stack or "(none)",
        ]
        if safe_component:
            lines += ["", "--- Component Stack ---", safe_component]

        message_text = "\n".join(lines)

        # Forward to User-Service /send-contact-email
        payload = {
            "name": "Citra UI Error Reporter",
            "email": body.userEmail or "noreply@citra-ai.com",
            "mobile": "+0000000000",
            "subject": subject,
            "message": message_text,
        }

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{_USER_SERVICE_URL}/api/send-contact-email",
                json=payload,
            )

        if resp.status_code == 200:
            logger.info(f"📧 [ERROR_REPORT] Sent error report for {user_label}")
        else:
            logger.warning(f"⚠️ [ERROR_REPORT] User-Service returned {resp.status_code}: {resp.text[:200]}")

        return {"success": True, "message": "Error report received"}

    except Exception as exc:
        logger.error(f"❌ [ERROR_REPORT] Failed to process error report: {exc}")
        # Don't fail the client — this is fire-and-forget from the UI
        return {"success": False, "message": "Failed to send error report"}
