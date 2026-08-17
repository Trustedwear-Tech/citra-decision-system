# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""
source-mcp-template auth.py — User-JWT verification, visibility PDP, audit.

Called from main.py's /query handler to:

  1. Verify X-User-JWT against Citra's shared HS256 secret (JWT_SECRET).
  2. Enforce per-source visibility (roles_allowed, public_within_org,
     cross_org_ids) using claims embedded in the token:
        { org_id, dept_ids, roles, entity_type, district_ids, ... }
  3. Write an audit record to the `dept_query_audit` collection.

Fail-open vs fail-closed:
  • AUTHZ_ENFORCE=true (prod default on real deployments) → reject on any
    missing/invalid/expired JWT, reject on visibility deny.
  • AUTHZ_ENFORCE=false (dev default) → log and allow, so local demos
    without a running user-service still work.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx
import jwt

logger = logging.getLogger(__name__)

# ── Config ──────────────────────────────────────────────────────────────────
# Citra shared-auth model: a dept-MCP belongs to a *customer* org (e.g.
# bihar-gov) but is deployed on the Citra platform and TRUSTS Citra's auth.
# This MCP verifies X-User-JWT against Citra's shared HS256 secret (JWT_SECRET)
# — the SAME key user-service signs with — so any Citra user is authenticated
# here without this MCP owning its own keypair. The source still belongs to
# its own org; only the auth key is shared.
_JWT_SECRET = os.getenv("JWT_SECRET", "")
# Default FAIL-CLOSED. Dev compose overrides via env to keep local demos easy.
_ENFORCE = os.getenv("AUTHZ_ENFORCE", "true").lower() in ("1", "true", "yes")
_LEEWAY = int(os.getenv("JWT_LEEWAY_SECONDS", "30"))

# Audit is shipped to smart-app-service over HTTP (the MCP holds NO Citra DB
# credentials). Backoff so a down ingest endpoint isn't pounded on every op —
# the in-memory buffer absorbs the gap and flushes on recovery.
_audit_sink_last_failure_at: float = 0.0
_AUDIT_RETRY_BACKOFF_SECONDS: float = float(os.getenv("AUDIT_RETRY_BACKOFF", "30"))
# Fail-fast HTTP timeout for the audit-ingest POST (seconds). Parsed at import —
# a malformed value crashes boot (config error) rather than being swallowed.
_AUDIT_HTTP_TIMEOUT_SECONDS: float = float(os.getenv("AUDIT_HTTP_TIMEOUT_SECONDS", "5"))


class AuthzError(Exception):
    """Raised when AuthZ denies the request (either unauth or visibility)."""

    def __init__(self, reason: str, *, status_code: int = 403):
        super().__init__(reason)
        self.reason = reason
        self.status_code = status_code


def verify_user_jwt(token: Optional[str]) -> Dict[str, Any]:
    """
    Verify an X-User-JWT and return its claims.

    Raises AuthzError when the token is missing, bad, or the verifier isn't
    configured (in enforce mode). In non-enforce (dev) mode returns an empty
    dict when no token is present so callers can fall through.
    """
    if not token:
        if _ENFORCE:
            raise AuthzError("missing X-User-JWT", status_code=401)
        return {}

    # Citra shared-auth path — verify against the shared HS256 secret. This
    # is how a dept-MCP trusts Citra-issued tokens (user-service, the
    # builder pod, smart-app-service all sign with this one key). Signature
    # + expiry are the trust anchor; audience is not enforced here because
    # general Citra tokens are not dept-MCP-audience-scoped.
    if not _JWT_SECRET:
        if _ENFORCE:
            raise AuthzError("JWT_SECRET not configured", status_code=503)
        logger.warning("⚠️ [AUTHZ] JWT_SECRET not set — skipping JWT verification (dev mode)")
        return {}

    try:
        return jwt.decode(
            token,
            _JWT_SECRET,
            algorithms=["HS256"],
            leeway=_LEEWAY,
            # audience deliberately not enforced (see above) — but a Citra token
            # may still carry an `aud` claim, so disable the check explicitly
            # rather than let PyJWT reject it.
            options={"require": ["exp"], "verify_aud": False},
        )
    except jwt.ExpiredSignatureError:
        raise AuthzError("token expired", status_code=401)
    except jwt.PyJWTError as exc:
        raise AuthzError(f"token invalid: {exc}", status_code=401)


# ---------------------------------------------------------------------------
# Visibility PDP
# ---------------------------------------------------------------------------

def _normalise_list(v: Any) -> List[str]:
    if not v:
        return []
    if isinstance(v, str):
        return [v]
    return [str(x) for x in v]


def source_owner_dept(source: Dict[str, Any]) -> Optional[str]:
    """Owning dept of a dept_sources doc — the ONE accessor both the
    visibility PDP and the audit trail use, so auth and audit can never
    disagree on who owns a source."""
    return source.get("dept_id") or source.get("owner_dept_id")


def is_visible(source: Dict[str, Any], claims: Dict[str, Any]) -> tuple[bool, str]:
    """Pure boolean form of check_visibility — returns (allowed, reason).

    Used both by check_visibility() (the enforcement entry point) and by
    catalogue.list_datasets() to filter what each user sees. Keeps a single
    source of truth for the visibility matrix so dataset enumeration can't
    leak sources the caller can't query.
    """
    visibility = source.get("visibility") or {}

    roles_allowed = set(_normalise_list(visibility.get("roles_allowed"))) or {"user"}
    user_roles_raw = _normalise_list(claims.get("roles")) or ["user"]
    user_roles = set(user_roles_raw)
    user_roles_lower = {r.lower() for r in user_roles_raw}

    # super_admin is a PLATFORM-WIDE role in Citra — it spans every org, not
    # just the caller's own. (org_admin is the org-scoped one.) A platform
    # super_admin therefore sees every source regardless of org / dept /
    # roles_allowed. Checked first, before the gates below.
    if "super_admin" in user_roles_lower:
        return True, "super_admin_platform_wide"

    # Workflow-system identity bypass. The token was minted by citra-auth's
    # mint_workflow_org_token() for a cron / webhook run with no live human
    # caller. The workflow itself was authored by an IT-role user (the
    # workflow service enforces this at create time), so the identity has
    # been pre-authorized at the trigger side. Org/dept gates below still
    # apply — only the role-list intersection is bypassed.
    if claims.get("workflow_system") is True:
        # Fall through to org/dept checks; the role gate doesn't apply.
        pass
    elif not (roles_allowed & user_roles):
        return False, (
            f"role denied: user_roles={sorted(user_roles)} "
            f"not in roles_allowed={sorted(roles_allowed)}"
        )

    user_org = claims.get("org_id")
    user_depts = set(_normalise_list(claims.get("dept_ids")))
    source_dept = source_owner_dept(source)
    # A source inherits the deployment's ORG_ID when it doesn't declare its own
    # (legitimate single-customer config — one org, sources needn't repeat it).
    # But if NEITHER is set we cannot determine the source's org. In enforce
    # mode that's a fail-closed denial: otherwise the `org_unspecified` branch
    # below would grant access on a bare dept-name collision across orgs.
    source_org = source.get("org_id") or os.getenv("ORG_ID")
    if not source_org and _ENFORCE:
        return False, (
            "source org indeterminate: source has no org_id and ORG_ID env is "
            "unset — refusing cross-dept visibility in enforce mode"
        )
    public_within_org = bool(visibility.get("public_within_org", False))
    cross_org_ids = set(_normalise_list(visibility.get("cross_org_ids")))

    # Eval order: dept_member → org_admin_within_org → public_within_org → cross_org.
    # Putting dept_member FIRST means dept-admins/members get the most specific
    # audit reason; the org_admin bypass only fires for users who aren't a member
    # of the source's dept but legitimately have cross-dept reach.
    #
    # dept_member REQUIRES org match when both sides declare an org_id —
    # otherwise a user in org-partner with dept_id=legal would match an
    # org-acme legal source just because the dept names collide. Legitimate
    # cross-org access goes through cross_org_ids below.
    same_org = (user_org and source_org and user_org == source_org)
    org_unspecified = (not source_org) or (not user_org)
    if source_dept and source_dept in user_depts and (same_org or org_unspecified):
        return True, "dept_member"
    if same_org and "org_admin" in user_roles_lower:
        # org_admin sees every dept's data within their own org. dept_admin
        # is deliberately NOT granted this — they only see their own depts.
        return True, "org_admin_within_org"
    if same_org and public_within_org:
        return True, "public_within_org"
    if user_org and user_org in cross_org_ids:
        return True, "cross_org_allowlisted"

    return False, (
        f"org/dept denied: user_org={user_org}, user_depts={sorted(user_depts)}, "
        f"source_org={source_org}, source_dept={source_dept}, "
        f"public_within_org={public_within_org}, "
        f"cross_org_ids={sorted(cross_org_ids)}"
    )


def check_visibility(source: Dict[str, Any], claims: Dict[str, Any]) -> None:
    """
    Enforce the `visibility` stanza on a source against the caller's claims.

    visibility:
      roles_allowed: [user, dept_admin, ...]   # MANDATORY — defaults to ["user"] if omitted
      public_within_org: true | false
      cross_org_ids: [<org_id>, ...]

    Access is granted when EITHER:
      0. The caller is super_admin — a PLATFORM-WIDE role that spans every
         org (it bypasses the gates below), OR
      ALL of:
      1. The user has at least one role in roles_allowed (ALWAYS enforced —
         a source with no roles_allowed declared is treated as ["user"], i.e.
         the narrowest default, matching discovery-service's registration
         default in registration.py). The role gate is never optional.
      2. ONE of:
         a) user's dept_ids intersect this source's dept_id, OR
         b) user is org_admin within the source's org (cross-dept read), OR
         c) source is public_within_org and user is in the same org, OR
         d) user's org_id is in cross_org_ids allowlist.

    dept_admin gets ONLY (a) — they can't read other depts' data even
    within their own org. org_admin gets (a)+(b) — scoped to its own org.
    super_admin is the platform-wide role: it sees every org's sources
    cross-org (rule 0), the same reach it has everywhere else in Citra.

    In non-enforce mode (AUTHZ_ENFORCE=false, dev only) we log and allow.
    """
    allowed, reason = is_visible(source, claims)
    if not allowed:
        _deny(f"{reason} (source={source.get('source_id')})")
    logger.debug(
        f"✅ [AUTHZ] allow source={source.get('source_id')} "
        f"user={claims.get('user_id')} reason={reason}"
    )


DEFAULT_WRITE_ROLES = ("dept_admin", "org_admin", "super_admin")


def check_write_permission(
    claims: Dict[str, Any],
    *,
    roles_allowed_write: Optional[List[str]] = None,
    action_id: str = "",
) -> None:
    """Write-specific authorization, enforced ON TOP OF check_visibility.

    check_visibility answers "can this caller SEE the source?"; this answers
    "can they WRITE to it?". The two are deliberately separate — a dept user
    may read quality results yet only a dept_admin may release a batch.

    ``roles_allowed_write`` is the action's own allow-list; when empty the
    platform default applies — writes need dept_admin or above, so a plain
    ``user`` cannot write unless an action explicitly lists "user".

    Honours AUTHZ_ENFORCE: in dev (enforce=false) a denial is logged and
    allowed, exactly like check_visibility. Raises AuthzError(403) otherwise.

    This role-set check is the interim model. The planned direction — for
    both the dept MCP and SmartApp Service — is explicit per-source read
    and write capabilities rather than coarse role-name intersection. See
    docs/access-control.md.
    """
    # Normalise roles to lowercase before matching — the read gate
    # (is_visible) lowercases roles first, so a token carrying e.g.
    # "Super_Admin" / "Org_Admin" must be treated consistently here. Without
    # this, a Super_Admin token could pass reads but fail writes.
    roles_raw = claims.get("roles") or ["user"]
    roles = [str(r).lower() for r in roles_raw]
    if "super_admin" in roles:
        return
    allowed_raw = list(roles_allowed_write) if roles_allowed_write else list(DEFAULT_WRITE_ROLES)
    allowed = [str(r).lower() for r in allowed_raw]
    if any(r in allowed for r in roles):
        logger.debug(
            f"✅ [AUTHZ] write allow action={action_id} user={claims.get('user_id')}"
        )
        return
    _deny(
        f"write action {action_id!r} denied — requires one of {allowed_raw}, "
        f"caller has {roles_raw}"
    )


def _deny(reason: str) -> None:
    if _ENFORCE:
        raise AuthzError(reason, status_code=403)
    logger.warning(f"⚠️ [AUTHZ] {reason} — allowed in dev mode (AUTHZ_ENFORCE=false)")


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------

def _mint_audit_token() -> str:
    """Mint a short-lived HS256 service token (shared JWT_SECRET) authenticating
    THIS MCP to smart-app-service's audit-ingest endpoint. Carries svc="mcp_audit"
    (the only capability it grants) + the MCP's org so the server can stamp
    authoritative provenance. No end-user identity — this proves the caller is a
    trusted dept-MCP, not who ran the query (that's in the record itself)."""
    from config import get_settings
    cfg = get_settings()
    now = int(time.time())
    return jwt.encode(
        {
            "svc": "mcp_audit",
            "sub": "mcp-audit",
            "org_id": cfg.org_id or None,
            "iat": now,
            "exp": now + 300,
        },
        _JWT_SECRET,
        algorithm="HS256",
    )


async def _post_audit_records(batch: List[Dict[str, Any]]) -> bool:
    """POST a batch of audit records to smart-app-service /api/audit/ingest.
    Returns True on success, False on any failure (caller re-queues). Honours a
    short failure backoff so a down endpoint isn't hit on every op."""
    global _audit_sink_last_failure_at
    from config import get_settings
    cfg = get_settings()
    base = (cfg.smart_app_service_url or "").rstrip("/")
    if not base:
        logger.warning("⚠️ [AUDIT] SMART_APP_SERVICE_URL not set — cannot ship audit")
        return False
    if not _JWT_SECRET:
        logger.warning("⚠️ [AUDIT] JWT_SECRET not set — cannot mint audit token")
        return False
    now = time.time()
    if (now - _audit_sink_last_failure_at) < _AUDIT_RETRY_BACKOFF_SECONDS:
        return False  # within backoff window — keep buffering
    try:
        # default=str so a stray non-JSON value (e.g. a datetime) degrades to a
        # string instead of failing the whole batch — audit must never break.
        payload = json.dumps({"records": batch}, default=str)
        async with httpx.AsyncClient(timeout=_AUDIT_HTTP_TIMEOUT_SECONDS) as client:
            resp = await client.post(
                f"{base}/api/audit/ingest",
                content=payload,
                headers={
                    "Authorization": f"Bearer {_mint_audit_token()}",
                    "Content-Type": "application/json",
                },
            )
        resp.raise_for_status()
        return True
    except Exception as exc:  # noqa: BLE001 — audit must never fail a query
        logger.warning(f"⚠️ [AUDIT] ingest POST failed, buffering: {exc}")
        _audit_sink_last_failure_at = now
        return False


async def log_audit(
    *,
    source_id: str,
    query: str,
    claims: Dict[str, Any],
    result_count: int,
    allowed: bool,
    reason: str = "",
    op: str = "query",
    source: Optional[Dict[str, Any]] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    """Best-effort audit log. Never raises — audit must not block queries.

    Every data-plane operation on this MCP writes one record:
      • op="query"          — RAG / NL query via /query
      • op="run_query"      — catalogue read via /run_query
      • op="execute_action" — catalogue WRITE via /execute_action

    ``source`` is the dept_sources registry doc for the target source; from
    it we denormalise WHO OWNS the data (source_dept_id / source_name) onto
    the record so audit review never needs a join back to dept_sources.
    Pass it wherever the route has the doc in scope (every route does).

    ``extra`` is shallow-merged into the record so each op can attach its
    own fields (e.g. action_id / verb / dry_run / payload_fields for a
    write) without changing this signature. Writes MUST be audited — the
    /execute_action route always calls this, on allow, deny, and failure.

    If Mongo is down, the record is buffered in a bounded in-memory deque
    and flushed opportunistically by `_flush_audit_buffer` once Mongo
    recovers. The buffer caps at AUDIT_BUFFER_MAX to prevent OOM under a
    sustained outage; overflow increments an audit-drop counter but does
    not fail the query.
    """
    # Owning dept of the TARGET source — source_owner_dept is the same
    # accessor the visibility PDP uses (auth and audit must never disagree
    # on ownership). None when the source doc is absent or carries no dept;
    # that's recorded as-is, not defaulted.
    source_dept_id = None
    source_name = None
    if source:
        source_dept_id = source_owner_dept(source)
        source_name = source.get("name")
    if op == "execute_action" and source_dept_id is None:
        logger.warning(
            f"⚠️ [AUDIT] write op on source={source_id!r} has no resolvable "
            f"dept (dept_id/owner_dept_id missing on source doc)"
        )

    # Deployment identity — which MCP produced this record. Matters when
    # several dept-MCPs share the central citra-ai Mongo, and as a cross-check
    # against a misconfigured source doc. cfg is reused below for the Mongo
    # db name; ORG_ID-unset is normalised to None (config defaults it to "")
    # so audit queries get one missing-value representation.
    try:
        from config import get_settings
        cfg = get_settings()
        mcp_org_id, mcp_dept_ids = cfg.org_id or None, cfg.dept_ids
    except Exception as exc:  # pragma: no cover — settings load is boot-fatal elsewhere
        logger.warning(f"⚠️ [AUDIT] could not resolve MCP identity: {exc}")
        cfg, mcp_org_id, mcp_dept_ids = None, None, []

    user_dept_ids = claims.get("dept_ids") or []
    record = {
        # Float epoch — JSON-safe. The writer (smart-app-service) stamps its own
        # BSON-date `received_at` server-side, which owns any opt-in TTL now, so
        # the MCP no longer carries a `ts_date` datetime (it isn't JSON-safe).
        "ts": time.time(),
        "op": op,
        "source_id": source_id,
        "query": (query or "")[:500],
        # ── Who: the acting user ────────────────────────────────────────
        "user_id": claims.get("user_id") or claims.get("sub"),
        "user_email": claims.get("email"),
        "user_name": claims.get("name"),
        "org_id": claims.get("org_id"),
        "user_dept_ids": user_dept_ids,
        # DEPRECATED alias of user_dept_ids — kept so pre-existing audit
        # queries keep working; new consumers read the unambiguous pair
        # user_dept_ids vs source_dept_id.
        "dept_ids": user_dept_ids,
        "roles": claims.get("roles") or [],
        # ── Where: the data that was touched + the MCP that did it ─────
        "source_dept_id": source_dept_id,
        "source_name": source_name,
        "mcp_org_id": mcp_org_id,
        "mcp_dept_ids": mcp_dept_ids,
        "result_count": result_count,
        "allowed": allowed,
        "reason": reason,
        # Impersonation markers — carried on the user's HS256 session token
        # when an admin uses the impersonation flow. `act` = the admin doing the
        # acting (RFC 8693). impersonation_id correlates back to the
        # impersonation_audit row in Citra-User-Service.
        "act": claims.get("act"),
        "impersonation_id": claims.get("impersonation_id"),
    }
    # op-specific fields (action_id, verb, dry_run, payload_fields, ok, error …)
    if extra:
        record.update({k: v for k, v in extra.items() if k not in record})
    # Buffer this record, then opportunistically ship the whole buffer to
    # smart-app-service over HTTP. Everything flows through the buffer + batch
    # POST, so a transient ingest outage never blocks the query — records stay
    # buffered and flush on recovery. Loud, never raises (audit is best-effort).
    _buffer_audit(record)
    try:
        await _flush_audit_buffer()
    except Exception as exc:  # noqa: BLE001 — audit must never fail a query
        logger.warning(f"⚠️ [AUDIT] flush failed, records buffered: {exc}")


# ---------------------------------------------------------------------------
# Audit buffer — in-memory safety net for Mongo outages.
# ---------------------------------------------------------------------------
from collections import deque as _deque

_AUDIT_BUFFER_MAX = int(os.getenv("AUDIT_BUFFER_MAX", "10000"))
_audit_buffer: "_deque[Dict[str, Any]]" = _deque(maxlen=_AUDIT_BUFFER_MAX)
_audit_dropped_total = 0


def _buffer_audit(record: Dict[str, Any]) -> None:
    global _audit_dropped_total
    if len(_audit_buffer) >= _AUDIT_BUFFER_MAX:
        # deque with maxlen will evict the oldest; count it.
        _audit_dropped_total += 1
    _audit_buffer.append(record)


async def _flush_audit_buffer() -> bool:
    """Ship buffered audit records to smart-app-service /api/audit/ingest.
    No-op if the buffer is empty. Returns False when the POST failed (endpoint
    down / within backoff) so the records are re-queued; True otherwise."""
    if not _audit_buffer:
        return True
    batch: list = []
    # Snapshot up to 500 at a time — cap write-amplification on recovery.
    for _ in range(min(500, len(_audit_buffer))):
        try:
            batch.append(_audit_buffer.popleft())
        except IndexError:
            break
    if not batch:
        return True
    if await _post_audit_records(batch):
        logger.info(f"[AUDIT] Shipped {len(batch)} records to smart-app-service "
                    f"(remaining={len(_audit_buffer)}, dropped={_audit_dropped_total})")
        return True
    # Ingest unavailable — put them back on the left so ordering holds.
    for rec in reversed(batch):
        _audit_buffer.appendleft(rec)
    return False
