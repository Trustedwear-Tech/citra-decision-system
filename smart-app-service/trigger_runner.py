# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""Trigger runner — webhook + schedule + poll.

The runner is **the only place** that fires runs not initiated by an
end-user click. It does three jobs:

1. ``verify_webhook(...)`` — HMAC validation for ``POST
   /apps/{slug}/trigger/{trigger_id}``.
2. ``fire_trigger(...)`` — runs one action with the **app's tenant
   service principal** as the requester. Same path used by webhooks +
   the scheduler tick.
3. ``tick_once(...)`` — single pass over all enabled schedule / poll
   triggers. Production calls this from a periodic asyncio task; tests
   call it directly.

Identity discipline: triggered runs use ``user_id = "system:<slug>"``
and inherit the app's ``tenant_id``. They never inherit an end-user's
JWT; HITL gates and audit still apply.
"""

from __future__ import annotations

import asyncio
import hmac
import hashlib
import json
import logging
import os
import re
import time

import jwt
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException, status

from config import Settings
from env_context import current_env
from models import AgentSpec, AppSpec, RunRequest, RunResponse, Trigger
from runtime import execute_run

logger = logging.getLogger(__name__)

# Poll fan-out bounds. A poll tick processes at most this many NEW rows, with
# bounded parallelism — each row is a full LLM agent run (seconds), so an
# unbounded "fire one per row" loop would block the tick for many minutes and
# spike cost. A backlog drains over successive ticks (the dedup cursor advances
# only for rows actually processed). Concurrency is clamped to the platform
# batch cap (C-02 = 8). Env-overridable.
# Poll fan-out is deliberately SMALL to protect a shared LLM/GPU endpoint:
# each agent run makes several LLM calls, so even a few concurrent rows × many
# published apps can rate-limit a self-hosted inference server. Defaults: at
# most 5 NEW rows per tick, processed ONE-AT-A-TIME (concurrency 1). A backlog
# drains over successive ticks. Override per-deployment via env if the LLM
# endpoint can take more.
_MAX_POLL_ROWS_PER_TICK = max(1, int(os.getenv("TRIGGER_MAX_POLL_ROWS_PER_TICK", "5")))
_POLL_CONCURRENCY = max(1, min(8, int(os.getenv("TRIGGER_POLL_CONCURRENCY", "1"))))
# Per-row retry budget on the unattended scheduler path. A row whose agent run
# FAILS is NOT consumed (left unmarked so the next tick re-polls + retries it)
# until it either succeeds OR exhausts this many attempts — at which point it is
# dead-lettered (a fired_via="dead_letter" run-history record IT can inspect /
# replay) and consumed so it stops retrying. Run-now keeps single-shot semantics
# (attempts not tracked). Was previously 1-and-drop (a transient blip lost the row).
_MAX_ROW_ATTEMPTS = max(1, int(os.getenv("TRIGGER_MAX_ROW_ATTEMPTS", "3")))
# Hard floor on timer cadence (cron handled via croniter; interval/poll here).
# Clamped at runtime so even a spec that slipped a tighter value past
# validation can't fire faster than the operator floor. Matches
# Settings.min_trigger_interval_seconds (default 300s = 5 min).
_MIN_TRIGGER_INTERVAL = max(
    60,
    int(os.getenv("MIN_TRIGGER_INTERVAL_SECONDS",
                  os.getenv("MIN_CRON_INTERVAL_SECONDS", "300"))),
)


# ---------------------------------------------------------------------------
# Secret resolution
# ---------------------------------------------------------------------------


def resolve_secret(secret_ref: Optional[str]) -> Optional[str]:
    """Resolve a ``secret_ref`` from AppSpec into the actual secret value.

    Supported schemes today:
      * ``env:NAME``                — read from environment
      * ``vault://...``             — reserved; not implemented in OSS dev
      * plain string (NOT supported for HMAC; rejected to fail-loud)

    Returns ``None`` if the reference is missing or unresolvable. The
    caller decides whether that's a configuration error.
    """
    if not secret_ref:
        return None
    if secret_ref.startswith("env:"):
        name = secret_ref[4:].strip()
        return os.getenv(name) or None
    if secret_ref.startswith("vault://"):
        # Vault integration is deployment-specific; in OSS we read from
        # an env var named after the path's last segment.
        tail = secret_ref.rsplit("/", 1)[-1]
        return os.getenv(f"VAULT_{tail.upper()}") or None
    # Inline secrets are forbidden — fail-closed so a typo doesn't yield
    # a literal "abc123" being trusted.
    logger.warning(
        "secret_ref '%s' is not in env:/vault:// form — refusing to use",
        secret_ref,
    )
    return None


# ---------------------------------------------------------------------------
# Webhook HMAC verification
# ---------------------------------------------------------------------------


_SIG_PREFIX = re.compile(r"^(sha256=)?", re.IGNORECASE)


def verify_webhook_signature(
    *, secret: str, body: bytes, signature_header: Optional[str]
) -> bool:
    """Constant-time HMAC-SHA256 verification.

    Accepts headers in either ``sha256=<hex>`` or plain ``<hex>`` form.
    Empty / missing header returns ``False``.
    """
    if not signature_header or not secret:
        return False
    candidate = _SIG_PREFIX.sub("", signature_header.strip(), count=1)
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    try:
        return hmac.compare_digest(candidate.lower(), expected.lower())
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------------------
# Trigger lookup
# ---------------------------------------------------------------------------


def find_trigger(app_spec: AppSpec, trigger_id: str) -> Optional[Trigger]:
    for t in app_spec.triggers or []:
        if t.id == trigger_id:
            return t
    return None


# ---------------------------------------------------------------------------
# Fire one trigger
# ---------------------------------------------------------------------------


def _system_principal(app_doc: dict) -> Tuple[str, Optional[str]]:
    """Identity used by triggered runs."""
    slug = app_doc.get("slug") or app_doc.get("app_id") or "unknown"
    return f"system:{slug}", app_doc.get("tenant_id")


def _mint_system_auth(settings: Settings, app_spec: AppSpec, app_doc: dict) -> str:
    """Mint a short-lived, tenant-scoped ``Bearer`` JWT for a triggered run.

    A trigger runs as the app's tenant service principal (see ``Trigger``).
    The agent's dept-MCP read / dry-run calls need a forwardable
    ``X-User-JWT``; without one the MCP rejects them (``user_org=None``) and
    the run captures no ``planned_writes`` — so it returns ``completed`` and
    stages nothing. This token is scoped to the app's OWN tenant/org and is
    used ONLY for the plan-then-stage phase: the real source write still
    commits on the officer's Approve, under the officer's own authority. So
    it is not an autonomous-write grant — it only lets the agent PROPOSE.
    """
    now = int(time.time())
    sub = f"system:{app_spec.slug}"
    tenant = app_doc.get("tenant_id")
    org = (app_doc.get("app_spec") or {}).get("org_id") or tenant
    token = jwt.encode(
        {
            "sub": sub,
            "user_id": sub,
            "email": f"{sub}@trigger.citra.ai",
            "tenant_id": tenant,
            "org_id": org,
            "roles": ["org_admin"],
            "iss": settings.jwt_issuer,
            "iat": now,
            "exp": now + 600,
        },
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )
    return f"Bearer {token}"


async def fire_trigger(
    *,
    settings: Settings,
    app_spec: AppSpec,
    agent_spec: AgentSpec,
    trigger: Trigger,
    inputs: Dict[str, Any],
    correlation_id: Optional[str] = None,
    auth_header: Optional[str] = None,
    plan_only: bool = True,
    force_fire: bool = False,
) -> RunResponse:
    """Run ``trigger.action`` with the supplied inputs.

    ``plan_only`` defaults True: a triggered run is a PRECOMPUTED recommendation
    — the agent reasons and proposes writes (captured as ``planned_writes``) but
    commits NOTHING. The caller stages the result into the officer inbox; a
    human approves to commit. This is the same plan-then-apply contract the
    on-demand ``/run`` path uses, so a trigger is just "run the agent ahead of
    the click." HITL gates still apply.

    ``force_fire`` bypasses the enabled check — used by the manual "Run now"
    path so the BA can test a trigger that is (by default) deactivated. The
    scheduler never sets it (it only fires already-enabled triggers).
    """
    if not trigger.enabled and not force_fire:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"trigger '{trigger.id}' is disabled",
        )
    req = RunRequest(
        action=trigger.action,
        inputs=inputs or {},
        user_id=f"system:{app_spec.slug}",
        correlation_id=correlation_id,
    )
    return await execute_run(
        settings=settings,
        app_spec=app_spec,
        agent_spec=agent_spec,
        request=req,
        auth_header=auth_header,
        plan_only=plan_only,
    )


# ---------------------------------------------------------------------------
# Poll cursor + dedup
# ---------------------------------------------------------------------------


def _resolve_template(template: Dict[str, Any], row: Dict[str, Any]) -> Dict[str, Any]:
    """Apply a template like ``{"claim_id": "$row.claim_id"}`` to a row."""
    out: Dict[str, Any] = {}
    for k, v in template.items():
        if isinstance(v, str) and v.startswith("$row."):
            path = v[5:].split(".")
            cur: Any = row
            for p in path:
                if isinstance(cur, dict):
                    cur = cur.get(p)
                else:
                    cur = None
                    break
            out[k] = cur
        else:
            out[k] = v
    return out


def _row_dedup_key(row: Dict[str, Any], dedup_field: str) -> Optional[str]:
    v = row.get(dedup_field)
    if v is None:
        return None
    return str(v)


# ---------------------------------------------------------------------------
# Scheduler tick
# ---------------------------------------------------------------------------


async def _is_due(trigger: Trigger, last_fired_at: Optional[datetime], now: datetime) -> bool:
    """Decide whether to fire ``trigger`` on this tick.

    Cron is approximated by an interval check using ``every_seconds``
    fallback — full cron is out of scope for v0; the scheduler caller
    is responsible for converting cron → next-fire-time and storing it.
    For ``schedule.interval`` this is straightforward.
    """
    if trigger.type == "schedule.interval":
        every = max(_MIN_TRIGGER_INTERVAL, trigger.every_seconds or _MIN_TRIGGER_INTERVAL)
        if last_fired_at is None:
            return True
        return (now - last_fired_at).total_seconds() >= every
    if trigger.type == "schedule.cron":
        # Real cron evaluation: fire iff a scheduled slot has elapsed since we
        # last fired. This honours the BA's intent ('0 6 * * *' = 6am daily),
        # not "every 60s". A bad/missing cron FAILS LOUD (logs + does NOT fire)
        # rather than silently degrading to a once-a-minute storm.
        cron = (trigger.cron or "").strip()
        if not cron:
            logger.error(
                "trigger %s is schedule.cron but has no cron expression — not firing",
                trigger.id,
            )
            return False
        base = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
        if trigger.tz:
            try:
                from zoneinfo import ZoneInfo
                base = base.astimezone(ZoneInfo(trigger.tz))
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "trigger %s has bad tz %r (%s) — evaluating cron in UTC",
                    trigger.id, trigger.tz, e,
                )
        try:
            from croniter import croniter
            prev_slot = croniter(cron, base).get_prev(datetime)
        except Exception as e:  # noqa: BLE001
            logger.error(
                "trigger %s has unparseable cron %r (%s) — not firing "
                "(fix the cron expression and re-publish)",
                trigger.id, cron, e,
            )
            return False
        if last_fired_at is None:
            return True  # first activation: fire once now, then on-schedule
        lf = last_fired_at if last_fired_at.tzinfo else last_fired_at.replace(tzinfo=timezone.utc)
        return lf < prev_slot
    if trigger.type == "poll":
        every = max(_MIN_TRIGGER_INTERVAL, trigger.every_seconds or _MIN_TRIGGER_INTERVAL)
        if last_fired_at is None:
            return True
        return (now - last_fired_at).total_seconds() >= every
    return False


class PollToolError(Exception):
    """A poll-tool call failed (transport / auth / bad response). Raised so the
    caller records the trigger run as FAILED and surfaces it, instead of a
    swallowed ``[]`` that is indistinguishable from 'no new rows'."""


async def _call_poll_tool(
    *,
    settings: Settings,
    server_and_tool: str,
    args: Dict[str, Any],
    auth_header: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Call discovery-service ``POST /mcp/{server}/call`` for a poll tool.

    Authenticated like every other dept-MCP call: the service API key as the
    ``Authorization`` bearer plus the minted system ``X-User-JWT`` (without it
    the MCP 401s under AUTHZ_ENFORCE=true). Returns the row list on success;
    a genuinely empty result is ``[]``. ANY failure (transport, >=400, non-JSON,
    unrecognized shape) RAISES ``PollToolError`` — a down/misconfigured source
    must be visibly failing, never silently idle (fail-loud)."""
    import httpx

    if "." not in server_and_tool:
        raise PollToolError(f"poll tool '{server_and_tool}' must be 'server.tool'")
    server, tool = server_and_tool.split(".", 1)
    url = f"{settings.discovery_url_for(current_env()).rstrip('/')}/mcp/{server}/call"
    headers = {"Content-Type": "application/json"}
    if settings.mcp_service_api_key:
        headers["Authorization"] = f"Bearer {settings.mcp_service_api_key}"
    if auth_header:
        headers["X-User-JWT"] = auth_header.removeprefix("Bearer ").strip()
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                url,
                json={"tool": tool, "arguments": args},
                headers=headers,
            )
    except httpx.HTTPError as e:
        logger.warning("poll tool %s transport error: %s", server_and_tool, e)
        raise PollToolError(f"transport error: {e}") from e
    if resp.status_code >= 400:
        logger.warning(
            "poll tool %s returned %s: %s",
            server_and_tool, resp.status_code, resp.text[:300],
        )
        raise PollToolError(
            f"{server_and_tool} returned {resp.status_code}: {resp.text[:200]}"
        )
    try:
        body = resp.json()
    except ValueError as e:
        logger.warning("poll tool %s returned non-JSON body", server_and_tool)
        raise PollToolError("non-JSON response body") from e
    if isinstance(body, list):
        return [r for r in body if isinstance(r, dict)]
    if isinstance(body, dict):
        for key in ("rows", "items", "results", "data"):
            v = body.get(key)
            if isinstance(v, list):
                return [r for r in v if isinstance(r, dict)]
    logger.warning("poll tool %s returned unrecognized shape", server_and_tool)
    raise PollToolError("unrecognized response shape (no rows/items/results/data)")


async def tick_once(
    *,
    settings: Settings,
    apps_col,
    agents_col,
    trigger_state_col,
    pending_runs_col,
    now: Optional[datetime] = None,
    stage_recommendation=None,
    record_run=None,
) -> List[Dict[str, Any]]:
    """One pass over all triggers across all published apps.

    Returns a list of activity records (for tests / logs):
    ``[{"slug","trigger_id","fired":bool,"reason":str,"correlation_id":...}]``.
    """
    now = now or datetime.now(timezone.utc)
    activity: List[Dict[str, Any]] = []

    # Only published apps with triggers.
    cursor = apps_col.find({"status": "published"})
    async for app_doc in cursor:
        spec_raw = app_doc.get("app_spec") or {}
        if not spec_raw.get("triggers"):
            continue
        try:
            app_spec = AppSpec.model_validate(spec_raw)
        except Exception as e:  # noqa: BLE001
            logger.warning("app_spec invalid for %s: %s", app_doc.get("slug"), e)
            continue
        agent_doc = await agents_col.find_one({"agent_id": app_doc.get("agent_id")})
        if not agent_doc:
            continue
        try:
            agent_spec = AgentSpec.model_validate(agent_doc.get("agent_spec") or {})
        except Exception as e:  # noqa: BLE001
            logger.warning("agent_spec invalid for %s: %s", app_doc.get("slug"), e)
            continue

        for trigger in app_spec.triggers:
            if not trigger.enabled:
                continue
            if trigger.type not in ("schedule.cron", "schedule.interval", "poll"):
                continue
            key = f"{app_spec.slug}:{trigger.id}"
            state = await trigger_state_col.find_one({"key": key}) or {}
            last_fired_at = state.get("last_fired_at")
            if not await _is_due(trigger, last_fired_at, now):
                continue

            if trigger.type in ("schedule.cron", "schedule.interval"):
                rec = await _fire_and_persist(
                    settings=settings,
                    app_spec=app_spec,
                    agent_spec=agent_spec,
                    trigger=trigger,
                    inputs={},
                    pending_runs_col=pending_runs_col,
                    app_doc=app_doc,
                    stage_recommendation=stage_recommendation,
                    record_run=record_run,
                    agent_doc=agent_doc,
                    fired_via="scheduler",
                )
                rec["slug"] = app_spec.slug
                rec["trigger_id"] = trigger.id
                activity.append(rec)
                await trigger_state_col.update_one(
                    {"key": key},
                    {"$set": {"key": key, "last_fired_at": now}},
                    upsert=True,
                )
                continue

            # poll — bounded: at most _MAX_POLL_ROWS_PER_TICK new rows this tick
            # (1 in test), processed with bounded concurrency. A backlog drains
            # over successive ticks; the cursor advances only for rows that ran.
            try:
                rows = await _call_poll_tool(
                    settings=settings,
                    server_and_tool=trigger.tool or "",
                    args=_resolve_poll_args(trigger.args, state.get("last_cursor")),
                    auth_header=_mint_system_auth(settings, app_spec, app_doc),
                )
            except PollToolError as e:
                # The source is down / misconfigured. Record the failure (so it
                # is visible in run history + alerts IT) and advance the cursor
                # so we retry next interval rather than hammering.
                logger.warning(
                    "poll trigger %s/%s failed: %s", app_spec.slug, trigger.id, e
                )
                if record_run is not None:
                    try:
                        await record_run(
                            app_spec=app_spec,
                            app_doc=app_doc,
                            agent_doc=agent_doc,
                            trigger=trigger,
                            inputs={},
                            started_at=now,
                            error=f"poll failed: {e}",
                            fired_via="poll",
                        )
                    except Exception:  # noqa: BLE001
                        logger.exception("record_run sink failed for poll %s", app_spec.slug)
                activity.append(
                    {"slug": app_spec.slug, "trigger_id": trigger.id, "fired": False, "reason": str(e)}
                )
                await trigger_state_col.update_one(
                    {"key": key},
                    {"$set": {"key": key, "last_fired_at": now}},
                    upsert=True,
                )
                continue
            seen: set = set(state.get("seen_keys") or [])
            # Per-row retry counters (persisted): a row whose agent run fails
            # accumulates here and is retried on the next tick; the entry is
            # cleared on success or when it is dead-lettered (max attempts).
            row_attempts: Dict[str, int] = dict(state.get("row_attempts") or {})
            poll_activity, processed_keys = await _process_new_poll_rows(
                settings=settings,
                app_spec=app_spec,
                agent_spec=agent_spec,
                trigger=trigger,
                rows=rows,
                seen=seen,
                max_rows=_MAX_POLL_ROWS_PER_TICK,
                concurrency=_POLL_CONCURRENCY,
                pending_runs_col=pending_runs_col,
                app_doc=app_doc,
                stage_recommendation=stage_recommendation,
                record_run=record_run,
                agent_doc=agent_doc,
                fired_via="poll",
                attempts=row_attempts,
                max_attempts=_MAX_ROW_ATTEMPTS,
            )
            activity.extend(poll_activity)
            for k in processed_keys:
                seen.add(k)
            # Limit unbounded growth of seen-set; keep last 1000.
            if len(seen) > 1000:
                seen = set(list(seen)[-1000:])
            await trigger_state_col.update_one(
                {"key": key},
                {
                    "$set": {
                        "key": key,
                        "last_fired_at": now,
                        "seen_keys": list(seen),
                        "row_attempts": row_attempts,
                    }
                },
                upsert=True,
            )
    return activity


def _resolve_poll_args(args: Dict[str, Any], last_cursor: Any) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for k, v in (args or {}).items():
        if isinstance(v, str) and v == "$last_cursor":
            out[k] = last_cursor
        else:
            out[k] = v
    return out


async def _fire_and_persist(
    *,
    settings: Settings,
    app_spec: AppSpec,
    agent_spec: AgentSpec,
    trigger: Trigger,
    inputs: Dict[str, Any],
    pending_runs_col,
    app_doc: dict,
    stage_recommendation=None,
    record_run=None,
    agent_doc: Optional[dict] = None,
    fired_via: str = "scheduler",
    force_fire: bool = False,
    row_key: Optional[str] = None,
) -> Dict[str, Any]:
    # row_key: stable natural key of the source row (poll path). Threaded into
    # the auto-process commit so its idempotency key is ROW-stable — a reprocess
    # of the same row (crash mid-tick, retry) dedups at the dept-MCP instead of
    # double-committing. None for cron/webhook/run-now → falls back to per-run.
    started_at = datetime.now(timezone.utc)

    async def _record(response=None, error=None):
        if record_run is None:
            return
        try:
            await record_run(
                app_spec=app_spec,
                app_doc=app_doc,
                agent_doc=agent_doc,
                trigger=trigger,
                inputs=inputs,
                started_at=started_at,
                response=response,
                error=error,
                fired_via=fired_via,
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "record_run sink failed for %s/%s", app_spec.slug, trigger.id
            )

    try:
        resp = await fire_trigger(
            settings=settings,
            app_spec=app_spec,
            agent_spec=agent_spec,
            trigger=trigger,
            inputs=inputs,
            force_fire=force_fire,
            # System credential so the agent's dept-MCP dry-run authorises —
            # without it the plan captures no writes and nothing stages.
            auth_header=_mint_system_auth(settings, app_spec, app_doc),
        )
    except HTTPException as e:
        logger.warning(
            "trigger %s/%s rejected: %s",
            app_spec.slug, trigger.id, e.detail,
        )
        await _record(error=f"rejected: {e.detail}")
        return {"fired": False, "reason": str(e.detail)}
    except Exception as e:  # noqa: BLE001
        logger.exception("trigger %s/%s failed", app_spec.slug, trigger.id)
        await _record(error=str(e))
        return {"fired": False, "reason": str(e)}

    if resp.status == "pending_approval":
        _ap_policy = getattr(trigger, "auto_process_policy", None)
        if getattr(trigger, "execution_mode", "recommend") == "auto_process" and _ap_policy is not None:
            # AUTO-PROCESS: policy-gate each proposed write — COMMIT the rule-passers
            # autonomously (via the governed dept-MCP path), STAGE the rest for a
            # human (fall back to auto-recommend). The policy is deterministic and
            # FAILS CLOSED (see auto_process.gate_planned_write).
            import auto_process as _ap
            from main import (  # lazy: avoid import cycle
                commit_auto_process_writes as _commit_auto,
                auto_process_guard_check as _ap_guard,
            )
            _commit_pws, _commit_reasons, _stage_pws = [], [], []
            _max_auto = _ap_policy.max_auto_per_run
            # PHASE-2 GUARDRAILS (rate-limit + circuit-breaker), read from the
            # auto_process_decisions audit. If tripped, commit NOTHING this run —
            # every write falls back to the human queue with the guard reason.
            _guard_ok, _guard_reason = await _ap_guard(
                app_id=app_doc.get("app_id"), slug=app_spec.slug,
                trigger_id=trigger.id, policy=_ap_policy,
            )
            if not _guard_ok:
                logger.warning(
                    "[auto-process] GUARD blocked auto-commit app=%s trigger=%s: %s",
                    app_spec.slug, trigger.id, _guard_reason,
                )
            for _pw in (resp.planned_writes or []):
                _decision, _reason = _ap.gate_planned_write(
                    _ap_policy, _pw, _ap.build_ctx(_pw, row=inputs))
                if _guard_ok and _decision == "commit" and len(_commit_pws) < _max_auto:
                    _commit_pws.append(_pw)
                    _commit_reasons.append(_reason)
                else:
                    if not _guard_ok and _decision == "commit":
                        _reason = _guard_reason
                    elif _decision == "commit":
                        _reason = f"max_auto_per_run ({_max_auto}) reached this run"
                    _stage_pws.append({**_pw, "_auto_process_declined": _reason})
            _committed = 0
            if _commit_pws:
                _committed = await _commit_auto(
                    settings=settings, app_spec=app_spec, app_doc=app_doc,
                    trigger=trigger, inputs=inputs, planned_writes=_commit_pws,
                    reasons=_commit_reasons, correlation_id=resp.correlation_id,
                    row_key=row_key,
                )
            if _stage_pws and stage_recommendation is not None:
                _spec = app_doc.get("app_spec") or {}
                _routing = (
                    {"dept_id": _spec["owner_id"]}
                    if _spec.get("owner_type") == "dept" and _spec.get("owner_id")
                    else {"dept_id": _spec["dept_ids"][0]} if _spec.get("dept_ids") else {}
                )
                resp.correlation_id = await stage_recommendation(
                    response=resp.model_copy(update={"planned_writes": _stage_pws}),
                    slug=app_spec.slug, inputs=inputs, tenant_id=app_doc.get("tenant_id"),
                    published_by_user_id=f"system:{app_spec.slug}", source="trigger",
                    assignable_to=_routing,
                )
            logger.info(
                "[auto-process] %s/%s: committed=%d staged=%d",
                app_spec.slug, trigger.id, _committed, len(_stage_pws),
            )
            # Reflect the outcome in the run status: if nothing was left for a
            # human (all writes auto-committed), the run is DONE, not pending.
            if not _stage_pws:
                resp.status = "completed"
        # Stage the precomputed recommendation into the unified officer inbox
        # (smartapp_workflow_staging) so it sits beside on-demand ones — the
        # caller injects ``stage_recommendation`` (main._stage_recommendation),
        # which is env-routed. A stager is REQUIRED — the legacy pending_runs
        # fallback was removed with the pending_runs approval path.
        elif stage_recommendation is not None:
            # Route the precomputed recommendation to the app's dept so dept
            # officers see it (empty routing is invisible to non-admins).
            _spec = app_doc.get("app_spec") or {}
            _routing = {}
            if _spec.get("owner_type") == "dept" and _spec.get("owner_id"):
                _routing = {"dept_id": _spec["owner_id"]}
            elif _spec.get("dept_ids"):
                _routing = {"dept_id": _spec["dept_ids"][0]}
            resp.correlation_id = await stage_recommendation(
                response=resp,
                slug=app_spec.slug,
                inputs=inputs,
                tenant_id=app_doc.get("tenant_id"),
                published_by_user_id=f"system:{app_spec.slug}",
                source="trigger",
                assignable_to=_routing,
            )
        else:
            # No stager injected. The legacy ``pending_runs`` approval path has
            # been removed (see ``main._approve_run_impl``), so a row staged here
            # could never be approved. Fail loud rather than insert an
            # un-approvable husk row — a trigger MUST be given ``stage_recommendation``
            # so its recommendation lands in the unified officer queue.
            raise RuntimeError(
                "trigger_runner.run_trigger_once requires stage_recommendation; "
                "the legacy pending_runs fallback has been removed"
            )
    # Item ledger Write-1 (trigger path): auto-recommended runs analyze the
    # same images/docs an on-demand run would — their findings must land in the
    # per-item ledger too, keyed by the FINAL (staged) correlation_id. Derived
    # store — loud-but-non-fatal (item_records logs on failure).
    _ifs = getattr(resp, "item_findings", None)
    if _ifs and resp.correlation_id:
        from item_records import persist_item_findings

        await persist_item_findings(
            _ifs,
            correlation_id=resp.correlation_id,
            slug=app_spec.slug,
            app_id=app_doc.get("app_id"),
            tenant_id=app_doc.get("tenant_id"),
            case_facets=list((resp.references or {}).get("case_facets") or []),
        )
    await _record(response=resp)
    return {
        "fired": True,
        "status": resp.status,
        "correlation_id": resp.correlation_id,
    }


async def _process_new_poll_rows(
    *,
    settings: Settings,
    app_spec: AppSpec,
    agent_spec: AgentSpec,
    trigger: Trigger,
    rows: List[Dict[str, Any]],
    seen: set,
    max_rows: int,
    concurrency: int,
    pending_runs_col,
    app_doc: dict,
    stage_recommendation=None,
    record_run=None,
    agent_doc: Optional[dict] = None,
    fired_via: str = "scheduler",
    force_fire: bool = False,
    attempts: Optional[Dict[str, int]] = None,
    max_attempts: int = 1,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Dedup ``rows`` against ``seen``, take up to ``max_rows`` NEW rows, and run
    the agent for each with bounded ``concurrency`` (each run stages one
    recommendation). Returns ``(activity_records, processed_keys)``.

    Two hard bounds protect the system: (a) at most ``max_rows`` processed per
    call so a large backlog drains over successive ticks rather than blocking;
    (b) **in the TEST environment, ALWAYS exactly one row** — the BA steps
    through cases one at a time via Run-now (test never batches). Only
    ``processed_keys`` are returned, so the caller advances the dedup cursor for
    exactly the rows that ran (capped-out rows are picked up next time)."""
    key_field = trigger.dedup_key
    fresh: List[Tuple[str, Dict[str, Any]]] = []
    cap = 1 if current_env() == "test" else max(1, max_rows)
    for r in rows:
        if not key_field:
            continue
        k = _row_dedup_key(r, key_field)
        if not k or k in seen:
            continue
        fresh.append((k, r))
        if len(fresh) >= cap:
            break
    if not fresh:
        return [], []

    sem = asyncio.Semaphore(max(1, concurrency))

    async def _one(k: str, r: Dict[str, Any]) -> Tuple[str, Dict[str, Any], Dict[str, Any]]:
        async with sem:
            inputs = _resolve_template(trigger.input_template, r)
            rec = await _fire_and_persist(
                settings=settings,
                app_spec=app_spec,
                agent_spec=agent_spec,
                trigger=trigger,
                inputs=inputs,
                pending_runs_col=pending_runs_col,
                app_doc=app_doc,
                stage_recommendation=stage_recommendation,
                record_run=record_run,
                agent_doc=agent_doc,
                fired_via=fired_via,
                force_fire=force_fire,
                row_key=k,
            )
            rec["slug"] = app_spec.slug
            rec["trigger_id"] = trigger.id
            rec["row_key"] = k
            return k, rec, r

    results = await asyncio.gather(*[_one(k, r) for k, r in fresh])
    activity = [rec for _, rec, _ in results]

    # Legacy / Run-now (attempts not tracked): single-shot — consume every
    # attempted row (the BA manually steps through cases; no auto-retry).
    if attempts is None:
        return activity, [k for k, _, _ in results]

    # Scheduler path: SUCCESS-ONLY consume + bounded retry-then-dead-letter.
    # A failed row is left UNMARKED (not in processed_keys), so the next tick
    # re-polls and retries it — until it succeeds or exhausts ``max_attempts``,
    # at which point it is dead-lettered (a fired_via="dead_letter" run-history
    # record for IT to inspect/replay) and consumed so it stops retrying.
    consumed: List[str] = []
    for k, rec, r in results:
        if bool(rec.get("fired")):
            attempts.pop(k, None)
            consumed.append(k)
            continue
        n = attempts.get(k, 0) + 1
        if n >= max_attempts:
            attempts.pop(k, None)
            consumed.append(k)  # give up — consume so we stop retrying forever
            if record_run is not None:
                try:
                    await record_run(
                        app_spec=app_spec, app_doc=app_doc, agent_doc=agent_doc,
                        trigger=trigger,
                        inputs=_resolve_template(trigger.input_template, r),
                        started_at=datetime.now(timezone.utc),
                        error=f"DEAD_LETTER (gave up after {n} attempts): {rec.get('reason')}",
                        fired_via="dead_letter",
                    )
                except Exception:  # noqa: BLE001
                    logger.exception("dead_letter record failed %s/%s", app_spec.slug, trigger.id)
        else:
            attempts[k] = n  # leave unconsumed → retried on the next tick
    return activity, consumed


async def run_trigger_once(
    *,
    settings: Settings,
    app_spec: AppSpec,
    agent_spec: AgentSpec,
    trigger: Trigger,
    state: dict,
    inputs: Optional[Dict[str, Any]],
    pending_runs_col,
    app_doc: dict,
    stage_recommendation=None,
    record_run=None,
    agent_doc: Optional[dict] = None,
    row_key: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Manually fire ONE run of ``trigger`` (the UI "Run now" button). Always
    exactly one case, and ``force_fire=True`` so it works on a (default-)
    deactivated trigger — this is how the BA tests a trigger before activating.

    - ``poll`` → pull the next single NEW row and run the agent on it.
    - ``webhook`` / ``schedule`` → run once with ``inputs`` (a sample case) or {}.

    Returns ``(activity_records, processed_keys)`` — ``processed_keys`` lets the
    caller advance the poll dedup cursor."""
    if trigger.type == "poll":
        try:
            rows = await _call_poll_tool(
                settings=settings,
                server_and_tool=trigger.tool or "",
                args=_resolve_poll_args(trigger.args, state.get("last_cursor")),
                auth_header=_mint_system_auth(settings, app_spec, app_doc),
            )
        except PollToolError as e:
            # Manual test of a broken poll source: record + surface a failed
            # activity record (the BA sees why nothing came back).
            logger.warning("manual poll %s/%s failed: %s", app_spec.slug, trigger.id, e)
            if record_run is not None:
                try:
                    await record_run(
                        app_spec=app_spec,
                        app_doc=app_doc,
                        agent_doc=agent_doc,
                        trigger=trigger,
                        inputs={},
                        started_at=datetime.now(timezone.utc),
                        error=f"poll failed: {e}",
                        fired_via="manual",
                    )
                except Exception:  # noqa: BLE001
                    logger.exception("record_run sink failed for manual poll %s", app_spec.slug)
            return (
                [{"slug": app_spec.slug, "trigger_id": trigger.id, "fired": False, "reason": str(e)}],
                [],
            )
        return await _process_new_poll_rows(
            settings=settings,
            app_spec=app_spec,
            agent_spec=agent_spec,
            trigger=trigger,
            rows=rows,
            seen=set(state.get("seen_keys") or []),
            max_rows=1,
            concurrency=1,
            pending_runs_col=pending_runs_col,
            app_doc=app_doc,
            stage_recommendation=stage_recommendation,
            record_run=record_run,
            agent_doc=agent_doc,
            fired_via="manual",
            force_fire=True,
        )
    rec = await _fire_and_persist(
        settings=settings,
        app_spec=app_spec,
        agent_spec=agent_spec,
        trigger=trigger,
        inputs=inputs or {},
        pending_runs_col=pending_runs_col,
        app_doc=app_doc,
        stage_recommendation=stage_recommendation,
        record_run=record_run,
        agent_doc=agent_doc,
        fired_via="manual",
        force_fire=True,
        row_key=row_key,
    )
    rec["slug"] = app_spec.slug
    rec["trigger_id"] = trigger.id
    return [rec], []
