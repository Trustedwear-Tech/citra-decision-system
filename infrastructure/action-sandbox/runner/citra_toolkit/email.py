# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""Email a report to the user via the adapter → Citra-Service bridge.

Recipient is ALWAYS the authenticated user — there is no way to send to an
arbitrary address from inside the sandbox. This is intentional: long-running
tasks should be able to ping the user when done, but the sandbox must not
become a generic outbound-mail relay.

Usage::

    from citra_toolkit import report, publish, email
    pdf = report.make_pdf(html, title="q1-summary")
    publish.publish(pdf, kind="pdf", title="Q1 Summary")  # also show in chat
    email.send(
        subject="Your Q1 Summary is ready",
        intro="The report you asked for is attached as a download link.",
        attachments=[pdf],
    )

Mechanism:
- Writes one ``{"type": "email", ...}`` line into the outbox.
- The adapter uploads each referenced file to Citra-Service (same path as
  ``publish``), receives presigned URLs, builds an HTML email body listing
  each file with its download link, and posts to Citra-Service's
  ``/action-chat/internal/email`` endpoint.
- The UI receives an ``email_sent`` SSE event so the user knows it went out.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Iterable

from .publish import _outbox_path

_MAX_ATTACHMENTS = 10
_MAX_SUBJECT = 180
_MAX_BODY = 8000


def send(
    *,
    subject: str,
    intro: str = "",
    attachments: Iterable[str | os.PathLike[str]] = (),
) -> dict[str, object]:
    """Queue an email to the authenticated user with download links.

    Args:
      subject: Email subject line. Trimmed to 180 chars.
      intro:   Optional plain-text intro paragraph above the link list.
      attachments: Iterable of file paths under ``/workspace`` that the
        adapter should upload and link from the email body.

    Returns the outbox entry that was written.
    """
    if not isinstance(subject, str) or not subject.strip():
        raise ValueError("email.send(): subject is required")
    intro = (intro or "").strip()
    if len(intro) > _MAX_BODY:
        raise ValueError(f"email.send(): intro exceeds {_MAX_BODY} chars")

    workspace = Path(os.getenv("CITRA_WORKSPACE_DIR", "/workspace")).resolve()
    rel_paths: list[str] = []
    for raw in attachments:
        p = Path(raw)
        if not p.is_file():
            raise FileNotFoundError(f"email.send(): not a file: {p}")
        try:
            rel = p.resolve().relative_to(workspace)
        except ValueError as e:
            raise ValueError(f"email.send(): file must live under {workspace}: {p}") from e
        rel_paths.append(str(rel))
    if len(rel_paths) > _MAX_ATTACHMENTS:
        raise ValueError(
            f"email.send(): too many attachments ({len(rel_paths)} > {_MAX_ATTACHMENTS})"
        )

    entry = {
        "type": "email",
        "subject": subject.strip()[:_MAX_SUBJECT],
        "intro": intro,
        "attachments": rel_paths,
        "ts": time.time(),
    }
    out: Path = _outbox_path()
    with out.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry
