# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""Internal code-exec route — exposes services.code_executor.execute_code
to smart-app-service runtime for ``tools_v2[].kind="code_exec"`` dispatch.

Why this lives in Citra-Service (not in a new microservice):
  • The Docker sandbox pool, S3 bucket creds, and the warm-container
    image are all already wired here. Standing up a separate
    ``code-exec-service`` would duplicate the entire Docker-host
    plumbing without adding security isolation — the sandbox itself is
    the security boundary, not the surrounding HTTP service.
  • Smart-app-service is stateless and deliberately knows nothing about
    Docker. It proxies the user JWT through to this endpoint exactly
    like it does for citra-workflow's ``/api/workflows/*`` (separate
    service post Phase J split, reached via WORKFLOW_SERVICE_URL) and
    dept-MCP ``/query``.

Auth: standard user JWT middleware (the smart-app-service runtime
forwards the BA-or-end-user's session bearer in the Authorization
header). Outputs are uploaded to S3 under
``quick-chat/smartapp/{slug}/{user_id}/{exec_id}/output/`` so they're
attributed to the real user and clearly separated from quick-chat
session outputs in audit logs.
"""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from citra_auth import get_secure_user_id
from services.code_executor import execute_code

logger = logging.getLogger(__name__)

router = APIRouter()


# Slug must be filesystem/URL-safe so it can land in an S3 key without
# escaping. Matches the smart-app-service slug pattern.
_SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,118}[a-z0-9]$")


class CodeExecInputFile(BaseModel):
    """One input file to mount into the sandbox.

    The smart-app caller already has the file in S3 (uploaded via the
    standard form-attachment flow). We stream it into ``/workspace/input/``
    inside the container — the LLM-authored script reads it from there.
    """

    filename: str = Field(min_length=1, max_length=255)
    s3_key: str = Field(min_length=1, max_length=1024)


class CodeExecRunRequest(BaseModel):
    """Request body for ``POST /internal/code-exec/run``."""

    script: str = Field(
        min_length=1,
        max_length=200_000,
        description="Python source. Reads /workspace/input/, writes /workspace/output/.",
    )
    output_filename: str = Field(
        min_length=1,
        max_length=255,
        description="Expected output filename. Used for content-type guessing.",
    )
    input_files: List[CodeExecInputFile] = Field(
        default_factory=list,
        description="Files to download from S3 into /workspace/input/.",
    )
    app_slug: Optional[str] = Field(
        default=None,
        max_length=120,
        description="Smart-app slug — used purely for S3 path attribution.",
    )

    @field_validator("app_slug")
    @classmethod
    def _validate_slug(cls, v: Optional[str]) -> Optional[str]:
        if v is None or v == "":
            return None
        if not _SLUG_PATTERN.match(v):
            raise ValueError("app_slug must be lowercase alphanumeric/hyphens")
        return v


class CodeExecOutputFile(BaseModel):
    filename: str
    download_url: str
    size: int
    content_type: str


class CodeExecRunResponse(BaseModel):
    success: bool
    stdout: str
    stderr: str
    output_files: List[CodeExecOutputFile] = Field(default_factory=list)


@router.post(
    "/internal/code-exec/run",
    response_model=CodeExecRunResponse,
    tags=["Internal Code Exec"],
)
async def internal_code_exec_run(
    request: Request, body: CodeExecRunRequest
) -> Dict[str, Any]:
    """Run a Python script in the sandbox pool, return stdout + uploaded
    output files (S3 presigned URLs, 30-min expiry).

    Failures inside the sandbox (script errors, validation fails, exec
    timeout) surface as ``{success:false, stderr:...}`` — they are NOT
    HTTP errors, because the runtime LLM needs to see the stderr so it
    can author a corrected script on the next turn.
    """
    user_id = get_secure_user_id(request)
    slug = body.app_slug or "ad-hoc"
    exec_id = uuid.uuid4().hex[:8]
    session_id = f"smartapp/{slug}/{user_id}/{exec_id}"

    files_payload: List[Dict[str, str]] = [
        {"filename": f.filename, "s3_key": f.s3_key} for f in body.input_files
    ]

    logger.info(
        "[CODE_EXEC_RUN] user=%s slug=%s files=%d output=%s",
        user_id,
        slug,
        len(files_payload),
        body.output_filename,
    )

    try:
        result = await execute_code(
            script=body.script,
            session_id=session_id,
            files=files_payload,
            output_filename=body.output_filename,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("[CODE_EXEC_RUN] sandbox failure")
        raise HTTPException(
            status_code=500,
            detail={"error": "code_exec_failed", "message": str(exc)},
        )

    return {
        "success": bool(result.get("success")),
        "stdout": result.get("stdout") or "",
        "stderr": result.get("stderr") or "",
        "output_files": result.get("output_files") or [],
    }
