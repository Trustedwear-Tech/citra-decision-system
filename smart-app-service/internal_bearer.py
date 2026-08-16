"""Internal-bearer minting and verification for /smart-app/internal/* routes.

The builder pod and runtime engines need to call the OCR proxy (and, in
future, the MCP / RAG / workflow proxies) without ever holding the raw
provider API keys. We mint short-lived HMAC-signed bearers scoped to a
*subject* (build session id or app slug) and a *kind* (``builder`` |
``runtime``).

Format::

    base64url(payload).base64url(hmac_sha256(signing_key, payload))

where ``payload`` is JSON::

    {"k": "builder|runtime", "s": "<subject>", "t": <tenant_or_null>,
     "tools": ["vision_ocr", ...], "iat": <unix>, "exp": <unix>}

Verification re-signs the payload and constant-time compares.

Why not JWT? We already use JWT for user/builder auth. Reusing the same
secret + library here works, but the internal router would have to
distinguish "user JWT" from "internal bearer" by shape. A separate
sign/verify helper with a different payload shape makes the trust
boundary obvious in code review.
"""

from __future__ import annotations

import base64
import hmac
import json
import time
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any, Dict, List, Literal, Optional


InternalBearerKind = Literal["builder", "runtime"]

# Shape of the ``bindings`` claim: a per-tool-kind allowlist embedded in
# the bearer at mint time so the proxy can authorise individual
# (source_id, tool_name) / workflow_id calls without a database round
# trip on the hot path.
#
# Conventions::
#
#     {
#       "mcp":      [["source_id", "tool_name"], ...]    # ordered pairs
#       "rag":      ["source_id", ...]                   # source ids
#     }
#
# ``vision_ocr`` and ``validate_form`` need no bindings (the kind alone
# fully describes what the caller is allowed to do).


class InternalBearerError(Exception):
    """Raised on malformed / expired / invalid-signature bearers."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class InternalBearerClaims:
    kind: InternalBearerKind
    subject: str
    tenant_id: Optional[str]
    tools: List[str]
    iat: int
    exp: int
    bindings: Dict[str, List[Any]] = field(default_factory=dict)

    def allows_mcp(self, source_id: str, tool_name: Optional[str]) -> bool:
        """True iff this bearer authorises a specific MCP tool call."""
        if "mcp" not in self.tools:
            return False
        entries = self.bindings.get("mcp") or []
        for ent in entries:
            if isinstance(ent, (list, tuple)) and len(ent) >= 1:
                ent_src = ent[0]
                ent_tool = ent[1] if len(ent) > 1 else None
            elif isinstance(ent, str):
                ent_src, ent_tool = ent, None
            else:
                continue
            if ent_src != source_id:
                continue
            # tool_name is optional in the spec; an entry with no tool
            # name allows any tool from that source.
            if ent_tool is None or tool_name is None or ent_tool == tool_name:
                return True
        return False

    def allows_rag(self, source_id: str) -> bool:
        if "rag" not in self.tools:
            return False
        return source_id in (self.bindings.get("rag") or [])


def _b64url_encode(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def mint_internal_bearer(
    *,
    signing_key: str,
    kind: InternalBearerKind,
    subject: str,
    tenant_id: Optional[str],
    tools: List[str],
    ttl_seconds: int,
    bindings: Optional[Dict[str, List[Any]]] = None,
) -> str:
    """Produce a signed bearer string for /smart-app/internal/* calls."""

    if not signing_key:
        raise InternalBearerError(
            "no_signing_key",
            "internal_signing_key is empty; refusing to mint",
        )
    now = int(time.time())
    payload = {
        "k": kind,
        "s": subject,
        "t": tenant_id,
        "tools": list(tools or []),
        "iat": now,
        "exp": now + max(ttl_seconds, 60),
        "b": dict(bindings or {}),
    }
    payload_b = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    sig = hmac.new(signing_key.encode("utf-8"), payload_b, sha256).digest()
    return f"{_b64url_encode(payload_b)}.{_b64url_encode(sig)}"


# Default TTL for a provisioned per-tenant MCP LLM key (30 days). Long enough to
# avoid frequent re-issuance, short enough to bound a leak; rotate by re-minting.
MCP_LLM_KEY_DEFAULT_TTL_SECONDS = 30 * 24 * 3600


def mint_mcp_llm_key(
    tenant_id: str,
    *,
    signing_key: str,
    ttl_seconds: int = MCP_LLM_KEY_DEFAULT_TTL_SECONDS,
) -> str:
    """Mint the per-tenant LLM key a CUSTOMER-SIDE dept-MCP uses as its
    ``LLM_API_KEY`` against the platform LLM proxy (``/smart-app/internal/llm/v1``).

    The MCP's NL→SQL planner must call an LLM, but the real upstream provider key
    must NOT live in the customer estate. So the MCP points ``LLM_BASE_URL`` at
    the platform proxy and authenticates with THIS scoped key; the proxy holds
    the real key server-side (from Vault) and forwards. The key is a ``runtime``
    internal bearer scoped to ONLY the ``llm`` tool, with:
      • ``subject = "mcp:<tenant>"`` — the proxy's per-subject token budget then
        caps LLM spend PER TENANT;
      • ``tenant_id`` — so every call is metered to the tenant (billing).
    Rotation: re-mint and push the new key to the MCP config; ``exp`` bounds a
    leak. (A zero-touch refresh endpoint is a follow-on hardening.)
    """
    if not tenant_id:
        raise InternalBearerError("no_tenant", "tenant_id is required for an MCP LLM key")
    return mint_internal_bearer(
        signing_key=signing_key,
        kind="runtime",
        subject=f"mcp:{tenant_id}",
        tenant_id=tenant_id,
        tools=["llm"],
        ttl_seconds=ttl_seconds,
    )


def verify_internal_bearer(
    *,
    signing_key: str,
    bearer: str,
) -> InternalBearerClaims:
    """Validate signature + expiry and return claims."""

    if not signing_key:
        raise InternalBearerError(
            "no_signing_key",
            "internal_signing_key is empty; cannot verify",
        )
    if not bearer or "." not in bearer:
        raise InternalBearerError("malformed", "bearer is malformed")
    try:
        payload_b64, sig_b64 = bearer.split(".", 1)
        payload_b = _b64url_decode(payload_b64)
        sig = _b64url_decode(sig_b64)
    except Exception as e:  # noqa: BLE001
        raise InternalBearerError("malformed", f"bearer decode failed: {e}") from e

    expected = hmac.new(signing_key.encode("utf-8"), payload_b, sha256).digest()
    if not hmac.compare_digest(expected, sig):
        raise InternalBearerError("bad_signature", "signature mismatch")

    try:
        payload = json.loads(payload_b.decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        raise InternalBearerError("malformed", f"payload not json: {e}") from e

    kind = payload.get("k")
    if kind not in ("builder", "runtime"):
        raise InternalBearerError("bad_kind", f"unknown bearer kind: {kind}")
    subject = payload.get("s")
    if not subject:
        raise InternalBearerError("malformed", "missing subject")
    exp = int(payload.get("exp") or 0)
    if exp < int(time.time()):
        raise InternalBearerError("expired", "bearer has expired")

    raw_bindings = payload.get("b") or {}
    if not isinstance(raw_bindings, dict):
        raise InternalBearerError("malformed", "bindings claim must be an object")

    return InternalBearerClaims(
        kind=kind,
        subject=subject,
        tenant_id=payload.get("t"),
        tools=list(payload.get("tools") or []),
        iat=int(payload.get("iat") or 0),
        exp=exp,
        bindings={str(k): list(v or []) for k, v in raw_bindings.items()},
    )
