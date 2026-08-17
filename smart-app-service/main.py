# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""Smart App Service — FastAPI application.

Endpoints (v0)
--------------
GET    /health
POST   /publish              Called by builder pod to persist app + agent specs
GET    /apps                 List apps (My Apps page)
GET    /apps/{slug}          Fetch full spec (used by runtime)
DELETE /apps/{slug}          Archive

POST   /build                Reserved (phase 6) — opens build session
POST   /apps/{slug}/edit     Reserved (phase 6) — re-spawns builder
POST   /apps/{slug}/run      Reserved (phase 7) — runtime entry
"""

from __future__ import annotations

import logging
import asyncio
import os
import secrets
import time
import uuid
import json
import re
import base64
import hmac
import hashlib
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Literal, Optional, Tuple

# Load .env BEFORE importing config — pydantic v1-style ``class Config:
# env_file = ".env"`` is silently ignored by pydantic-settings v2, so we
# do it explicitly here. Containers / production deployments inject env
# vars directly and ``load_dotenv`` is a no-op when no .env exists.
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Vault bootstrap — populates os.environ from prod/smart-app-service via
# AppRole BEFORE any settings/config import. overwrite=False, so existing
# env (local .env or compose) wins for dev.
if os.getenv("VAULT_ADDR"):
    from citra_service_utils import load_from_vault
    load_from_vault()

import httpx
import jwt
from fastapi import Body, Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from jsonschema.exceptions import ValidationError as JsonSchemaValidationError
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorCollection
import automation_control
from pymongo.errors import OperationFailure
from pydantic import BaseModel, ConfigDict, Field
from pydantic import ValidationError as PydanticValidationError

from auth import (
    JWTAuthMiddleware,
    get_optional_user_id,
    get_org_id,
    get_sa_admin_of,
    get_sa_member_of,
    get_secure_user_id,
    get_tenant_id,
    get_user_dept_ids,
    get_user_roles,
    require_publish_scope,
)
from auto_chart import maybe_inject_chart_panel
from capabilities import get_capabilities
from config import Settings, get_settings
from models import (
    AUDIENCE_RANK,
    AgentSpec,
    AppDetailResponse,
    EmbedSpecResponse,
    EmbedSnippetResponse,
    AppListResponse,
    AppSpec,
    AppStatus,
    AppSummary,
    AudienceLevel,
    ApproveRequest,
    compute_plan_hash,
    AuditRunDetailResponse,
    AuditRunListResponse,
    AutoCommitListResponse,
    ChangeLedgerResponse,
    AuditRunSummary,
    BuildRequest,
    BuildResponse,
    BuildSessionStatus,
    CapabilitiesResponse,
    HealthResponse,
    PanelDataResponse,
    DetailDataResponse,
    PublishOption,
    PublishOptionsResponse,
    PublishRequest,
    PublishResponse,
    ChatResponse,
    DecisionRecord,
    RunRequest,
    RunResponse,
    SelfLearningRequest,
    SelfLearningResponse,
    WorkflowStagingRow,
    RuntimeTokenResponse,
    SetAudienceRequest,
    SetAudienceResponse,
    SuccessResponse,
    Theme,
    TriggerFireResponse,
    format_audience,
    parse_audience,
)
from panel_data import resolve_panel_data, resolve_detail_data, resolve_field_options
from embed_keys import (
    ensure_embed_key,
    env_for_key,
    is_external_surface,
)
from runtime import execute_run, chat_with_agent, ChatProducedNoReply
import llm_rate_limit
from llm_rate_limit import LLMRateLimitError
from sse import run_with_heartbeat, SSE_HEADERS
from sandbox_client import SandboxClient, SandboxHostError
from tools_v2_dispatch import (
    build_openai_tools_from_tools_v2,
    dispatch_tools_v2_call,
)
from trigger_runner import (
    fire_trigger,
    find_trigger,
    resolve_secret,
    run_trigger_once,
    tick_once,
    verify_webhook_signature,
)
from validators import validate_agent_spec, validate_app_spec
from data_binding_validator import (
    validate_data_bindings,
    validate_panel_columns,
    validate_data_source_refs,
)
from publish_validators import (
    reject_allow_writes_in_chat,
    scan_for_secrets,
    validate_dashboard_page_has_narrator,
    validate_chart_axes,
    validate_direct_write_buttons_confirm,
    validate_editable_fields,
    validate_case_signature,
    validate_case_signature_projection,
    validate_factor_set,
    validate_case_signature_confirmed,
    validate_case_signature_stable,
    validate_factor_checks_can_score,
    validate_item_tools_declare_task_type,
    validate_factor_set_mode_stable,
    validate_rubric_finding_matches_declaration,
    validate_icons,
    validate_no_media_columns,
    validate_grounding_contract,
    validate_internal_audience,
    validate_no_admin_actions,
    validate_no_delete_verbs,
    validate_mcp_action_has_input_schema,
    validate_required_lookup_is_bound,
    validate_tool_sources_resolvable,
    validate_update_has_identifier,
)
from catalogue_client import (
    fetch_catalogue_entry,
    fetch_catalogue_list,
    fetch_catalogue_search,
    trim_for_prompt,
)
from relevance_filter import needs_scope_narrowing, rerank_candidates

def _catalogue_rerank_text(entry: Dict[str, Any]) -> str:
    """Rerank text for one catalogue entry — name + description + columns."""
    cols = entry.get("columns") or []
    col_names = ", ".join(
        str(c.get("name", "?")) for c in cols[:20] if isinstance(c, dict)
    )
    return (
        f"{entry.get('name') or entry.get('dataset_id') or ''}\n"
        f"{entry.get('description') or ''}\n"
        f"Columns: {col_names}"
    ).strip()

# Make logging unicode-safe before configuring it. Log messages contain non-ASCII
# (→ arrows, emojis); on Windows the default console encoding is cp1252, which
# raises UnicodeEncodeError in the handler and can DROP the record — including
# error logs. Reconfigure the streams to UTF-8 so every line (and every error) is
# emitted reliably. No-op on Linux (already UTF-8).
import sys as _sys
for _stream in (_sys.stdout, _sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # Python 3.7+
    except Exception:  # noqa: BLE001 — odd/older stream; logging still functions
        pass

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _setup_file_logging() -> None:
    """Write logs to a rotating FILE (in addition to stdout) so the
    Monitoring-Service log-watcher and the observability stack (Loki/promtail)
    pick up WARNING/ERROR lines and alert IT. Container mounts the host log dir
    at /app/logs (see docker-compose); locally falls back to ./logs. A failure
    to open the file degrades to stdout-only with a loud warning — it never
    blocks boot."""
    from logging.handlers import RotatingFileHandler

    log_dir = os.getenv("LOG_DIR", "/app/logs")
    log_file = os.getenv("LOG_FILE", "smart-app-service.log")
    root = logging.getLogger()
    for candidate in (log_dir, "logs"):
        try:
            os.makedirs(candidate, exist_ok=True)
            fh = RotatingFileHandler(
                os.path.join(candidate, log_file),
                maxBytes=5 * 1024 * 1024,
                backupCount=7,
                encoding="utf-8",   # reliably capture unicode log records (→, emojis)
            )
            fh.setLevel(logging.INFO)
            fh.setFormatter(
                logging.Formatter(
                    "%(asctime)s %(levelname)s %(name)s: %(message)s"
                )
            )
            root.addHandler(fh)
            logger.info("file logging -> %s/%s", candidate, log_file)
            return
        except Exception as e:  # noqa: BLE001
            logger.warning("file logging unavailable at %s (%s)", candidate, e)
    logger.warning("file logging disabled — stdout only")


_setup_file_logging()


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

_mongo_client: Optional[AsyncIOMotorClient] = None
# Strong refs to fire-and-forget background tasks (Run-now agent runs) so the
# event loop doesn't GC them mid-run; cleared on completion via done-callback.
_run_now_bg_tasks: set = set()
_apps_col: Optional[AsyncIOMotorCollection] = None
_agents_col: Optional[AsyncIOMotorCollection] = None
_spec_versions_col: Optional[AsyncIOMotorCollection] = None
_prompt_packs_col: Optional[AsyncIOMotorCollection] = None
_skills_col: Optional[AsyncIOMotorCollection] = None
_build_sessions_col: Optional[AsyncIOMotorCollection] = None
_pending_runs_col: Optional[AsyncIOMotorCollection] = None
_workflow_staging_col: Optional[AsyncIOMotorCollection] = None
_trigger_state_col: Optional[AsyncIOMotorCollection] = None
_trigger_runs_col: Optional[AsyncIOMotorCollection] = None
_smart_app_records_col: Optional[AsyncIOMotorCollection] = None
_app_run_audit_col: Optional[AsyncIOMotorCollection] = None
_decision_records_col: Optional[AsyncIOMotorCollection] = None
# Dept-MCP data-plane audit trail. smart-app-service is the sole WRITER
# (POST /api/audit/ingest, called by the customer-side dept-MCPs so they hold
# no Citra DB credentials); citra-workflow reads it for the Operational Data
# Flow UI. Single GLOBAL collection — never test-routed.
_dept_query_audit_col: Optional[AsyncIOMotorCollection] = None
# Runtime halt/pause control (the kill switches). Single GLOBAL collection —
# never test-routed: a halt applies to live operations regardless of env.
_control_col: Optional[AsyncIOMotorCollection] = None

# ── Environment (test ↔ prod) ──────────────────────────────────────────────
# A request runs in one environment. The builder/build path is always "test";
# a run of a published app resolves by store (see resolve_app_environment). The
# value is held in a contextvar (async-safe, per-task) so the Mongo accessors
# and the discovery helpers pick test-vs-prod WITHOUT threading a param through
# every signature. Default "prod" — anything that doesn't explicitly set the
# env keeps prod behaviour (a test app accessed on an unrouted path simply
# isn't found → fail-closed, never prod corruption).
# The contextvar + accessors live in env_context (a dependency-free module) so
# deep modules — proxy_clients, panel_data, capabilities, runtime — can read
# current_env() without a circular import on main.
from env_context import (  # noqa: E402,F401
    current_embed_key,
    current_env,
    set_current_embed_key,
    set_current_env,
)

# Test isolation is by COLLECTION NAME, not a separate db. When the request env
# is "test", every smartapp collection routes to its ``test_``-prefixed sibling
# in the SAME db (smartapp_apps → test_smartapp_apps). A test app's definition
# AND operational data live only there, so the prod queue + audit chain stay
# pristine and an unpromoted app is fail-closed against prod (absent from the
# prod collection → 404 if env isn't set). Promote copies the spec doc from the
# test_ collection to the prod collection. _db is the (single) database handle.
TEST_COLLECTION_PREFIX = "test_"
_db: Optional[Any] = None


def _test_collection_name(prod_name: str) -> str:
    return f"{TEST_COLLECTION_PREFIX}{prod_name}"


def _route_col(prod_col: AsyncIOMotorCollection, collection_name: str) -> AsyncIOMotorCollection:
    """Route to the test_-prefixed collection (same db) when the request env is
    'test'; else the prod collection. Prod is the safe default."""
    if current_env() == "test" and _db is not None:
        return _db[_test_collection_name(collection_name)]
    return prod_col


async def resolve_app_environment(slug: str) -> str:
    """Resolve a published app's environment by STORE, server-side.

    Present in the prod apps collection → "prod"; else present in the test_
    apps collection → "test"; else "prod" (the caller's own 404 then fires —
    fail-closed). Cheap: a single indexed {slug} lookup per collection.
    Uses the RAW handles (not the env-routed accessors) — this is what decides
    the env, so routing here would be circular.

    When no test environment is configured (``_db`` unset, or the test discovery
    planes absent), there is nowhere for a test app to live, so resolution
    collapses to "prod" without a second lookup — keeping the single-environment
    fast path unchanged."""
    if _apps_col is None or _db is None or not get_settings().test_environment_available:
        return "prod"
    if await _apps_col.find_one({"slug": slug}, {"_id": 1}) is not None:
        return "prod"
    test_apps = _db[_test_collection_name(get_settings().apps_collection)]
    if await test_apps.find_one({"slug": slug}, {"_id": 1}) is not None:
        return "test"
    return "prod"


def _resolve_build_env() -> str:
    """The build path (build session, builder pod, publish) is ALWAYS "test"
    when a test environment is configured — every build + BA-test runs against
    the test MCPs and writes to the test_ collections. When no test env is
    configured the platform runs the legacy single-environment behaviour
    ("prod") so existing deployments keep working; this is a configured no-op
    (test disabled), logged once at build time, not a silent fallback."""
    return "test" if get_settings().test_environment_available else "prod"


def _bind_build_env() -> str:
    """Set + return the build-path environment for this request."""
    env = _resolve_build_env()
    set_current_env(env)
    if env != "test":
        logger.info(
            "[env] build running in prod (no test environment configured — set "
            "TEST_DISCOVERY_SERVICE_URL to build/test against test MCPs)"
        )
    return env


async def _embed_key_environment(slug: str) -> Optional[str]:
    """The environment an embed key entitles THIS slug to, or None.

    Store-based resolution is prod-first, and a PROMOTED app lives in both
    stores — so a bank's UAT card, holding an ``emb_test_`` key, would bind
    prod for every call after the spec fetch and read/write PRODUCTION records.
    The key is environment-tagged, so it can re-derive the environment per call.

    NOT a caller-chosen environment. The key must actually exist in that
    environment's store bound to THIS slug, so a page can only reach an
    environment it genuinely holds a key for. A forged or foreign key resolves
    to None and the caller falls back to store resolution.
    """
    key = current_embed_key()
    if not key:
        return None
    env = env_for_key(key)
    if env is None:
        return None
    if env == "test" and (
        _db is None or not get_settings().test_environment_available
    ):
        return None
    prior = current_env()
    try:
        set_current_env(env)
        doc = await get_apps_col().find_one(
            {"embed_key": key, "slug": slug}, {"_id": 1}
        )
    finally:
        set_current_env(prior)
    return env if doc else None


async def _bind_app_env(slug: str) -> str:
    """Resolve + set the environment for an app-scoped request, and return it.

    An embed key on the request wins when it verifies (see
    ``_embed_key_environment``) — that is what keeps a customer's UAT card in
    test after the app has been promoted. Otherwise resolve by store: a test app
    (present only in test_ collections) routes to test; everything else to prod.

    Call at the TOP of an app-scoped handler, before any get_apps_col()/
    get_*_col() access, so the routed accessors + the discovery helpers all see
    the right environment.
    """
    env = await _embed_key_environment(slug)
    if env is None:
        env = await resolve_app_environment(slug)
    set_current_env(env)
    return env


def get_smart_app_records_col() -> AsyncIOMotorCollection:
    if _smart_app_records_col is None:
        raise RuntimeError("Database not initialised")
    return _route_col(_smart_app_records_col, get_settings().smart_app_records_collection)


def get_app_run_audit_col() -> AsyncIOMotorCollection:
    if _app_run_audit_col is None:
        raise RuntimeError("Database not initialised")
    return _route_col(_app_run_audit_col, get_settings().app_run_audit_collection)


def get_decision_records_col() -> AsyncIOMotorCollection:
    """The self-improving loop's DecisionRecord substrate (env-routed).

    Mutable + loop-facing: the read-back poller stamps the outcome here later.
    Distinct from the immutable hash-chained ``smartapp_run_audit`` ledger,
    which stays the authoritative compliance record."""
    if _decision_records_col is None:
        raise RuntimeError("Database not initialised")
    return _route_col(_decision_records_col, get_settings().decision_records_collection)


def get_control_col() -> AsyncIOMotorCollection:
    """Runtime halt/pause control collection. GLOBAL — not env-routed."""
    if _control_col is None:
        raise RuntimeError("Database not initialised")
    return _control_col


def _app_dept_ids(app_doc: Optional[dict]) -> List[str]:
    """ORG-QUALIFIED dept tokens an app belongs to, for dept-scoped halt.

    Returns ``"<org_id>:<dept_id>"`` tokens (not bare dept ids) because dept ids
    are NON-UNIQUE across orgs — a bare ``operations`` would collide between
    tenants and a dept halt would freeze the wrong org. Sources: app_spec
    ``dept_id``/``dept_ids`` + a ``dept:<id>`` audience. Empty when the app has
    no dept association (dept halt then doesn't match — global/org still do)."""
    if not app_doc:
        return []
    spec = app_doc.get("app_spec") or {}
    raw: set = set()
    for key in ("dept_id", "dept_ids"):
        v = spec.get(key) or app_doc.get(key)
        if isinstance(v, str) and v:
            raw.add(v)
        elif isinstance(v, list):
            raw.update(x for x in v if isinstance(x, str) and x)
    aud = spec.get("audience") or ""
    if isinstance(aud, str) and aud.startswith("dept:"):
        raw.add(aud.split(":", 1)[1])
    # Canonical org component = the app's tenant_id (the authoritative field
    # apps are stored/filtered by, and what the halt store keys on). Do NOT
    # prefer spec.org_id — it can diverge from tenant_id and the dept token
    # would then never match a stored dept halt.
    app_org = app_doc.get("tenant_id") or spec.get("org_id") or spec.get("tenant_id") or ""
    return [f"{app_org}:{d}" for d in raw]


async def _automation_halt_reason(
    app_doc: Optional[dict] = None, *, slug: Optional[str] = None, tenant_id: Optional[str] = None
) -> Optional[dict]:
    """The halt record (or None) currently blocking automation for this app."""
    _slug = slug or (app_doc or {}).get("slug")
    _org = tenant_id or (app_doc or {}).get("tenant_id")
    _depts = _app_dept_ids(app_doc)
    return await automation_control.get_halt(
        get_control_col(), org_id=_org, dept_ids=_depts, slug=_slug
    )


async def _enforce_automation_allowed(
    app_doc: Optional[dict] = None,
    *,
    slug: Optional[str] = None,
    tenant_id: Optional[str] = None,
    what: str = "This operation",
) -> None:
    """Raise 503 if a halt/pause covers this app (global/org/dept/app scope).

    Blocks runs, autonomous writes, approvals and trigger intake — reads and
    the audit trail stay available. The kill switch."""
    reason = await _automation_halt_reason(app_doc, slug=slug, tenant_id=tenant_id)
    if reason:
        scope = reason.get("scope_type", "?")
        note = reason.get("reason") or "paused by an administrator"
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"{what} is blocked — automation halted at {scope} scope: {note}",
        )


def get_apps_col() -> AsyncIOMotorCollection:
    if _apps_col is None:
        raise RuntimeError("Database not initialised")
    return _route_col(_apps_col, get_settings().apps_collection)


def get_agents_col() -> AsyncIOMotorCollection:
    if _agents_col is None:
        raise RuntimeError("Database not initialised")
    return _route_col(_agents_col, get_settings().agents_collection)


def get_spec_versions_col() -> AsyncIOMotorCollection:
    """Spec version-history store (env-routed). A test app's snapshots live in
    test_smartapp_spec_versions; prod in smartapp_spec_versions."""
    if _spec_versions_col is None:
        raise RuntimeError("Database not initialised")
    return _route_col(_spec_versions_col, get_settings().spec_versions_collection)


# prompt_packs + skills are platform assets (the builder's library) — identical
# in test and prod, so they are NOT environment-routed (a test build reads them
# from the prod store).
def get_prompt_packs_col() -> AsyncIOMotorCollection:
    if _prompt_packs_col is None:
        raise RuntimeError("Database not initialised")
    return _prompt_packs_col


def get_skills_col() -> AsyncIOMotorCollection:
    if _skills_col is None:
        raise RuntimeError("Database not initialised")
    return _skills_col


def get_pending_runs_col() -> AsyncIOMotorCollection:
    if _pending_runs_col is None:
        raise RuntimeError("Database not initialised")
    return _route_col(_pending_runs_col, get_settings().pending_runs_collection)


def get_workflow_staging_col() -> AsyncIOMotorCollection:
    if _workflow_staging_col is None:
        raise RuntimeError("Database not initialised")
    return _route_col(_workflow_staging_col, get_settings().workflow_staging_collection)


def get_trigger_state_col() -> AsyncIOMotorCollection:
    if _trigger_state_col is None:
        raise RuntimeError("Database not initialised")
    return _route_col(_trigger_state_col, get_settings().trigger_state_collection)


def get_trigger_runs_col() -> AsyncIOMotorCollection:
    if _trigger_runs_col is None:
        raise RuntimeError("Database not initialised")
    return _route_col(_trigger_runs_col, get_settings().trigger_runs_collection)


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Distributed leader election (Redis). The trigger tick, outcome poller, periodic
# grounding rebuild, and sweeps are SINGLETON work — they must run on exactly ONE
# instance or they duplicate (double-fired triggers, double-stamped outcomes, N
# rebuilds). Every instance runs the election loop; only the lock holder executes
# the singleton loops, and leadership FAILS OVER when the holder dies (lock TTL
# expires). The trigger-QUEUE consumer is NOT gated — it's a competing consumer,
# safe on every instance. Fail-closed: on a Redis error we relinquish leadership
# (no instance runs singleton work) rather than risk split-brain.
# ---------------------------------------------------------------------------
_INSTANCE_ID = uuid.uuid4().hex
_is_leader_flag = False


def _is_leader() -> bool:
    return _is_leader_flag


async def _leader_election_loop(stop_event: "asyncio.Event") -> None:
    global _is_leader_flag
    try:
        from citra_queue.queue import _async_redis
    except Exception:  # noqa: BLE001 — without Redis we cannot elect; fail closed
        logger.error("[leader] citra_queue/Redis unavailable — singleton loops will NOT run")
        _is_leader_flag = False
        return
    key = os.getenv("SCHEDULER_LEADER_KEY", "smartapp:scheduler:leader")
    ttl_ms = max(int(os.getenv("SCHEDULER_LEADER_TTL_MS", "30000")), 5000)
    renew_s = max(ttl_ms / 3000.0, 2.0)  # renew at ~1/3 of the TTL
    rc = None
    while not stop_event.is_set():
        try:
            if rc is None:
                rc = await _async_redis()
            if await rc.set(key, _INSTANCE_ID, nx=True, px=ttl_ms):
                _is_leader_flag = True            # acquired
            else:
                cur = await rc.get(key)            # decode_responses=True → str
                if cur == _INSTANCE_ID:
                    await rc.set(key, _INSTANCE_ID, xx=True, px=ttl_ms)  # renew
                    _is_leader_flag = True
                else:
                    _is_leader_flag = False        # someone else holds it
        except Exception:  # noqa: BLE001 — Redis blip: relinquish, reconnect, retry
            logger.warning("[leader] election tick failed; relinquishing leadership")
            _is_leader_flag = False
            rc = None
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=renew_s)
        except asyncio.TimeoutError:
            pass
    # Shutdown: release the lock if we hold it, so a peer takes over immediately.
    try:
        if rc is not None and _is_leader_flag and (await rc.get(key)) == _INSTANCE_ID:
            await rc.delete(key)
    except Exception:  # noqa: BLE001
        pass
    _is_leader_flag = False


def _load_app_spec(app_doc: Dict[str, Any]) -> AppSpec:
    """Validate a STORED app document into an AppSpec, failing legibly.

    Publish validates, so a stored spec should always re-validate — but the rule
    set moves. A spec written before a rule existed, or accepted by a service
    process running older code, stays in the store and then fails on every
    subsequent read. Bare `AppSpec.model_validate(app_doc["app_spec"])` turns
    that into a naked 500 with no body, from ~23 call sites.

    That is not a cosmetic problem. The builder's entire fix-and-retry loop is
    driven by the response body: a 422 naming the broken rule it re-authors and
    re-publishes; an empty 500 it can only guess at. Observed: a `chart` panel
    on an embed page (rejected by AppSpec, persisted anyway by a stale worker)
    500'd the smoke gate, and the builder concluded the `{param.id}` filter
    "needs a host context" and handed the BA a URL for a bricked app.

    RULE #1 — this does not swallow anything. It LOGS the violation and
    propagates it as a 422 that says which app and which rule.
    """
    try:
        return AppSpec.model_validate(app_doc["app_spec"])
    except PydanticValidationError as e:
        slug = app_doc.get("slug") or app_doc.get("app_id") or "?"
        logger.error("stored spec for %r fails validation: %s", slug, e)
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "action": "fix_and_retry",
                "passed": False,
                "issues": [{
                    "severity": "fail",
                    "class": "spec",
                    "msg": f"the stored spec for '{slug}' no longer validates: {e}",
                    "likely_fix": "re-author the spec to satisfy the rule above and "
                                  "re-publish — the stored version cannot render",
                }],
            },
        ) from e


# Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _mongo_client, _db, _apps_col, _agents_col, _spec_versions_col, _prompt_packs_col, _skills_col, _build_sessions_col, _pending_runs_col, _workflow_staging_col, _trigger_state_col, _trigger_runs_col, _smart_app_records_col, _app_run_audit_col, _decision_records_col, _dept_query_audit_col, _control_col

    settings = get_settings()
    _mongo_client = AsyncIOMotorClient(settings.mongo_uri)
    db = _mongo_client[settings.mongo_db]
    # Single db handle; test apps route to test_-prefixed collections within it
    # (see _route_col). No second db.
    _db = db

    _apps_col = db[settings.apps_collection]
    _agents_col = db[settings.agents_collection]
    _spec_versions_col = db[settings.spec_versions_collection]
    _prompt_packs_col = db[settings.prompt_packs_collection]
    _skills_col = db[settings.skills_collection]
    _build_sessions_col = db[settings.build_sessions_collection]
    _pending_runs_col = db[settings.pending_runs_collection]
    _workflow_staging_col = db[settings.workflow_staging_collection]
    _trigger_state_col = db[settings.trigger_state_collection]
    _trigger_runs_col = db[settings.trigger_runs_collection]
    _smart_app_records_col = db[settings.smart_app_records_collection]
    _app_run_audit_col = db[settings.app_run_audit_collection]
    _decision_records_col = db[settings.decision_records_collection]
    # Dept-MCP data-plane audit — same central-Mongo db citra-workflow reads for
    # the Operational Data Flow UI (all services resolve to the same MONGO_DB).
    _dept_query_audit_col = db["dept_query_audit"]
    _control_col = db["smartapp_control"]

    # Atlas (and other managed Mongo deployments) often run with a
    # least-privilege application user that lacks ``createIndex``;
    # indexes are provisioned out-of-band via IaC. Treat permission
    # errors as warnings so the service still boots — the queries that
    # rely on these indexes will simply do collection scans, which is
    # acceptable for dev / staging.
    async def _ensure_index(col, keys, **kwargs):
        try:
            await col.create_index(keys, **kwargs)
        except OperationFailure as e:
            if e.code in (13, 8000):  # Unauthorized / AtlasError
                logger.warning(
                    "skipping create_index on %s (insufficient privileges): %s",
                    col.name, e,
                )
                return
            raise

    await _ensure_index(_apps_col, "slug", unique=True)
    await _ensure_index(_apps_col, [("tenant_id", 1), ("owner", 1)])
    await _ensure_index(_apps_col, "status")
    # Embed keys are looked up on EVERY call from an embedded card (the key is
    # re-verified per request to keep a customer's UAT card in test after the
    # app is promoted), so this is a hot path, not an occasional one. Sparse:
    # only externally-consumed apps carry a key.
    await _ensure_index(_apps_col, "embed_key", sparse=True)
    await _ensure_index(_agents_col, "agent_id", unique=True)
    # smartapp_spec_versions — one row per superseded (app+agent) spec revision.
    # Unique on (slug, version) so a re-published version idempotently overwrites
    # its snapshot; the (slug, version desc) order backs the history list + trim.
    await _ensure_index(
        _spec_versions_col, [("slug", 1), ("version", -1)], unique=True
    )
    await _ensure_index(_build_sessions_col, "session_id", unique=True)
    # Reuse lookup: find the live builder for an exact (owner, app_id,
    # build_kind) target. Reap-on-spawn lookup: an owner's active sessions.
    await _ensure_index(
        _build_sessions_col,
        [("owner", 1), ("app_id", 1), ("build_kind", 1), ("status", 1)],
    )
    await _ensure_index(_build_sessions_col, [("owner", 1), ("status", 1)])
    await _ensure_index(_pending_runs_col, "correlation_id", unique=True)
    await _ensure_index(_pending_runs_col, [("tenant_id", 1), ("status", 1)])

    # smartapp_workflow_staging — per-case rows written by the workflow
    # engine for officer review. Idempotent on
    # (workflow_execution_id, case_natural_key) so workflow retries upsert.
    # Read indexes cover the two hot paths: panel reads filtered by
    # (tenant, status) and reviewer inbox filtered by (dept_id, status).
    # The created_at desc index backs time-window / max_age_days filters.
    await _ensure_index(
        _workflow_staging_col,
        [("workflow_execution_id", 1), ("case_natural_key", 1)],
        unique=True,
    )
    await _ensure_index(
        _workflow_staging_col, [("tenant_id", 1), ("status", 1)]
    )
    await _ensure_index(
        _workflow_staging_col,
        [("assignable_to.dept_id", 1), ("status", 1)],
    )
    await _ensure_index(_workflow_staging_col, [("created_at", -1)])
    # Backs the per-run OFFICER CORRECTIONS prefetch (runtime._prefetch_corrections_block):
    # filter by (slug, status) + sort resolved_at desc — index-covered so it's not
    # a per-decision collection scan.
    await _ensure_index(
        _workflow_staging_col,
        [("slug", 1), ("status", 1), ("resolved_at", -1)],
    )

    await _ensure_index(_trigger_state_col, "key", unique=True)

    # smartapp_trigger_runs — one row per trigger firing (history + failures).
    # Hot reads: per-app/per-trigger history (newest first) and an
    # operator sweep for failing triggers.
    await _ensure_index(
        _trigger_runs_col, [("slug", 1), ("trigger_id", 1), ("created_at", -1)]
    )
    await _ensure_index(_trigger_runs_col, [("created_at", -1)])
    await _ensure_index(_trigger_runs_col, "trigger_run_id", unique=True)

    # smart_app_records — single shared collection for every BA's queue /
    # decision / approval rows. See models.SmartAppRecord and
    # citra-workflow's mongo_writer node. Indexes cover the three hot
    # access paths: panel reads (app_id+kind+status), ownership sweep
    # (org_id+owner_id), and GDPR / admin-delete picker (author_user_id).
    await _ensure_index(
        _smart_app_records_col,
        [("app_id", 1), ("kind", 1), ("status", 1), ("created_at", -1)],
    )
    await _ensure_index(
        _smart_app_records_col,
        [("org_id", 1), ("owner_type", 1), ("owner_id", 1)],
    )
    await _ensure_index(_smart_app_records_col, "author_user_id")
    await _ensure_index(
        _smart_app_records_col,
        [("app_id", 1), ("record_id", 1)],
        unique=True,
    )
    await _ensure_index(_smart_app_records_col, "source_workflow_id")
    await _ensure_index(_smart_app_records_col, "deleted_at")
    # Thread/history overlay reads: all comment/review rows for a SoR record,
    # newest first (DataSource.mode="thread" → write_app_record appends rows
    # anchored by thread_of; panel_data filters by thread_of).
    await _ensure_index(
        _smart_app_records_col,
        [("app_id", 1), ("kind", 1), ("thread_of", 1), ("created_at", -1)],
    )

    # app_run_audit — append-only audit trail, one row per /run. Indexes
    # cover the Audit tab's two access paths: the per-app run list
    # (app_id + created_at desc) and the single-run lookup (correlation_id,
    # NOT unique — an approved re-run appends a second row). audit_id is
    # the unique per-row key.
    await _ensure_index(_app_run_audit_col, "audit_id", unique=True)
    await _ensure_index(
        _app_run_audit_col, [("app_id", 1), ("created_at", -1)]
    )
    await _ensure_index(_app_run_audit_col, "correlation_id")
    await _ensure_index(
        _app_run_audit_col, [("tenant_id", 1), ("created_at", -1)]
    )

    # dept_query_audit — the dept-MCP data-plane trail (written via
    # /api/audit/ingest). Indexes cover the Operational Data Flow UI's read
    # shapes: per-owning-dept, per-user, and newest-first org browse.
    await _ensure_index(_dept_query_audit_col, [("source_dept_id", 1), ("ts", -1)])
    await _ensure_index(_dept_query_audit_col, [("user_id", 1), ("ts", -1)])
    await _ensure_index(_dept_query_audit_col, [("ts", -1)])

    # decision_records — self-improving loop substrate (mutable; outcome stamped
    # later by the poller). Indexes cover the three hot paths: the poller scan
    # (committed + unsettled, oldest first), the write-back pull (agent +
    # outcome.label), and the loop-metrics read (app + time window).
    await _ensure_index(_decision_records_col, "decision_id", unique=True)
    await _ensure_index(
        _decision_records_col,
        [("action_result.committed", 1), ("outcome", 1), ("created_at", 1)],
    )
    await _ensure_index(
        _decision_records_col,
        [("agent_id", 1), ("tenant_id", 1), ("outcome.label", 1), ("created_at", -1)],
    )
    await _ensure_index(
        _decision_records_col,
        [("app_id", 1), ("tenant_id", 1), ("created_at", -1)],
    )
    # Org-wide Success Rate rollup (GET /org/decision-stats): a tenant-only
    # filter + created_at sort — the compounds above lead with app_id/agent_id
    # and cannot serve it index-ordered.
    await _ensure_index(
        _decision_records_col,
        [("tenant_id", 1), ("created_at", -1)],
    )
    # E3 resubmission join (entity_links.rejected_priors): record-key lookup
    # across the tenant's decisions. Multikey over record_keys.key_values —
    # without it the join walks the tenant's entire decision history.
    await _ensure_index(
        _decision_records_col,
        [("tenant_id", 1), ("record_keys.key_values", 1), ("created_at", -1)],
    )

    # Test-environment collections (same db, test_-prefixed). Mirror only the
    # UNIQUE indexes that guard correctness (duplicate slug / agent_id /
    # correlation_id / record / audit row); the secondary read indexes are
    # optional at test scale and left to collection scans. Created only when a
    # test environment is configured, so prod-only deployments add nothing.
    if settings.test_environment_available:
        _t = lambda name: _db[_test_collection_name(name)]  # noqa: E731
        await _ensure_index(_t(settings.apps_collection), "slug", unique=True)
        await _ensure_index(_t(settings.agents_collection), "agent_id", unique=True)
        await _ensure_index(
            _t(settings.spec_versions_collection),
            [("slug", 1), ("version", -1)],
            unique=True,
        )
        await _ensure_index(_t(settings.build_sessions_collection), "session_id", unique=True)
        await _ensure_index(_t(settings.pending_runs_collection), "correlation_id", unique=True)
        await _ensure_index(
            _t(settings.workflow_staging_collection),
            [("workflow_execution_id", 1), ("case_natural_key", 1)],
            unique=True,
        )
        await _ensure_index(_t(settings.trigger_state_collection), "key", unique=True)
        await _ensure_index(
            _t(settings.smart_app_records_collection),
            [("app_id", 1), ("record_id", 1)],
            unique=True,
        )
        await _ensure_index(_t(settings.app_run_audit_collection), "audit_id", unique=True)
        await _ensure_index(_t(settings.decision_records_collection), "decision_id", unique=True)

    # Leader election — runs on EVERY instance; gates the singleton loops below
    # so they execute on exactly one instance (with failover). The trigger-queue
    # consumer is intentionally NOT gated (competing consumer).
    _leader_stop = asyncio.Event()
    leader_task = asyncio.create_task(_leader_election_loop(_leader_stop))
    logger.info("[leader] election started (instance=%s)", _INSTANCE_ID[:8])

    # Background scheduler — opt-in via env var so tests / dev don't fire
    # triggers unintentionally. Production sets SCHEDULER_ENABLED=1.
    scheduler_task = None
    if os.getenv("SCHEDULER_ENABLED", "").lower() in ("1", "true", "yes"):
        interval = int(os.getenv("SCHEDULER_TICK_SECONDS", "30"))

        async def _scheduler_loop():
            while True:
                if not _is_leader():
                    await asyncio.sleep(interval)
                    continue
                try:
                    await tick_once(
                        settings=settings,
                        apps_col=_apps_col,
                        agents_col=_agents_col,
                        trigger_state_col=_trigger_state_col,
                        pending_runs_col=_pending_runs_col,
                        stage_recommendation=_stage_recommendation,
                        record_run=_record_trigger_run,
                    )
                except Exception:  # noqa: BLE001
                    logger.exception("scheduler tick failed")
                # Self-improving loop — Stage 4 outcome read-back. Shares the
                # scheduler cadence; self-gates by each decision's window_days,
                # so most ticks are cheap no-ops. Isolated try so a poll failure
                # never affects trigger ticks.
                try:
                    await poll_decision_outcomes(settings=settings)
                except Exception:  # noqa: BLE001
                    logger.exception("outcome poller tick failed")
                # Stamp the last-tick time so the Automation Control panel can show
                # the learning scheduler is alive. Best-effort (must not break the
                # tick) but LOUD — a persistent failure here means Redis is down,
                # which also breaks grounding refresh state, so surface it.
                try:
                    from grounding_runs import get_grounding_run_store
                    get_grounding_run_store().cache.setex(
                        "scheduler:last_tick", 3600,
                        datetime.now(timezone.utc).isoformat(),
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("scheduler last-tick stamp failed: %s", exc)
                await asyncio.sleep(interval)

        scheduler_task = asyncio.create_task(_scheduler_loop())
        logger.info("scheduler started (every %ss)", interval)

    # (Removed 2026-07-01: the preview-app TTL sweep. Preview apps no longer
    # exist — publishing targets the test environment and promotion goes to prod
    # via /apps/{slug}/promote-to-prod — so there is nothing to sweep.)

    # Build-session idle sweep. A builder pod whose BA closed the tab / crashed
    # without the clean stop call would otherwise hold a capacity slot until the
    # host's 2 h max-age backstop. This reaps pods idle past
    # BUILD_SESSION_IDLE_TIMEOUT_SECONDS (~30 min) so abandoned builds free
    # their slot promptly. Activity is stamped (last_activity_at) on every
    # chat/steer turn — an actively-used pod is never reaped.
    idle_sweep_task = None
    _sweep_settings = get_settings()
    _idle_timeout = max(int(_sweep_settings.build_session_idle_timeout_seconds), 300)
    _idle_sweep_interval = max(min(_idle_timeout // 3, 300), 60)

    async def _idle_sweep_loop():
        from datetime import timedelta
        from sandbox_client import SandboxClient, SandboxHostError
        sb = SandboxClient(_sweep_settings)
        sessions_col = get_build_sessions_col()
        while True:
            await asyncio.sleep(_idle_sweep_interval)
            if not _is_leader():
                continue
            try:
                cutoff = datetime.now(timezone.utc) - timedelta(seconds=_idle_timeout)
                stale = await sessions_col.find({
                    "status": BuildSessionStatus.ACTIVE.value,
                    "last_activity_at": {"$lt": cutoff},
                }).to_list(length=100)
                for s in stale:
                    sid = s.get("session_id")
                    if not sid:
                        continue
                    try:
                        await sb.stop_session(sid, host_base=s.get("sandbox_host_base"))
                    except SandboxHostError as e:
                        # Host unreachable — leave ACTIVE so we retry next sweep
                        # rather than marking it dead while the pod may live.
                        logger.warning("[idle_sweep] stop_session(%s) failed: %s", sid, e)
                        continue
                    await sessions_col.update_one(
                        {"session_id": sid, "status": BuildSessionStatus.ACTIVE.value},
                        {"$set": {
                            "status": BuildSessionStatus.TIMED_OUT.value,
                            "ended_at": datetime.now(timezone.utc),
                            "ended_reason": "idle_timeout",
                        }},
                    )
                    logger.info("[idle_sweep] reaped idle builder session %s", sid)
            except Exception:  # noqa: BLE001
                logger.exception("idle_sweep failed")

    idle_sweep_task = asyncio.create_task(_idle_sweep_loop())
    logger.info(
        "build-session idle sweep started (idle=%ss, every %ss)",
        _idle_timeout, _idle_sweep_interval,
    )

    # Periodic FULL grounding rebuild (self-improving loop backstop). The delta
    # upsert in the scheduler loop carries continuous per-decision learning; this
    # weekly pass re-curates canonicals, pulls new seed history, and folds in
    # direct-human decisions. Prod-gated (only with the scheduler). Default 7 days;
    # waits one interval before the first pass so a restart doesn't stampede.
    grounding_rebuild_task = None
    _gr_days = int(os.getenv("GROUNDING_FULL_REFRESH_DAYS", "7"))
    if _gr_days > 0 and os.getenv("SCHEDULER_ENABLED", "").lower() in ("1", "true", "yes"):
        _gr_interval = _gr_days * 86400

        async def _grounding_rebuild_loop():
            while True:
                await asyncio.sleep(_gr_interval)
                if not _is_leader():
                    continue
                try:
                    n = await _rebuild_all_grounded(settings)
                    logger.info("[grounding] periodic full rebuild enqueued %d app(s)", n)
                except Exception:  # noqa: BLE001
                    logger.exception("periodic grounding rebuild failed")

        grounding_rebuild_task = asyncio.create_task(_grounding_rebuild_loop())
        logger.info("periodic grounding rebuild started (every %d days)", _gr_days)

    # Clause consolidation (docs/clause-memory-graph-plan.md §9.1): folds pending
    # officer corrections into atomic clauses. Deliberately OFF the officer's
    # approve/reject path — the legacy _resummarize ran an LLM rewrite inside
    # that request; this batches instead. Leader-elected so two workers cannot
    # double-count officer support and defeat the promotion gate. ON by default:
    # Phases A–C only WRITE the clause store, nothing injects it until an app
    # sets case_signature.learning.mode, so running it changes no behaviour.
    consolidation_task = None
    _cons_interval = int(os.getenv("CONSOLIDATION_INTERVAL_SECONDS", "900"))
    if _cons_interval > 0:

        async def _consolidation_loop():
            while True:
                await asyncio.sleep(_cons_interval)
                if not _is_leader():
                    continue
                try:
                    from consolidation import run_consolidation_pass

                    await run_consolidation_pass()
                except Exception:  # noqa: BLE001 — never kills the loop
                    logger.exception("clause consolidation pass failed")

        consolidation_task = asyncio.create_task(_consolidation_loop())
        logger.info("clause consolidation started (every %ds)", _cons_interval)

    # Durable trigger-job consumer (Redis Streams via citra_queue): drains
    # webhook/Run-now jobs with crash-recovery (XAUTOCLAIM) + retry + DLQ. ON by
    # default — the routes enqueue, so this must run to process them. Independent
    # of SCHEDULER_ENABLED (poll/cron, which stays prod-gated).
    trigger_consumer_task = None
    _trigger_stop = asyncio.Event()
    if os.getenv("TRIGGER_QUEUE_CONSUMER", "true").lower() in ("1", "true", "yes"):
        import trigger_queue
        trigger_consumer_task = asyncio.create_task(
            trigger_queue.run_trigger_consumer(_trigger_stop, _fire_trigger_job)
        )
        logger.info("smartapp trigger queue consumer started")

    # User-lifecycle consumer (`default` queue). Citra-User-Service enqueues
    # user.deactivated / user.delete_applied there; their only consumer left
    # with Citra-Worker in the 2026-08-08 split, so until this ran those jobs
    # were enqueued and NEVER processed — the admin API reported "inheritance
    # job enqueued" while nothing inherited anything. This service owns
    # smartapp_apps, the one resource kind that still needs inheriting.
    lifecycle_consumer_task = None
    _lifecycle_stop = asyncio.Event()
    if os.getenv("LIFECYCLE_QUEUE_CONSUMER", "true").lower() in ("1", "true", "yes"):
        import lifecycle_queue
        lifecycle_consumer_task = asyncio.create_task(
            lifecycle_queue.run_lifecycle_consumer(_lifecycle_stop, _db)
        )
        logger.info("user-lifecycle queue consumer started")

    logger.info("smart-app-service started on port %s", settings.port)
    try:
        yield
    finally:
        _trigger_stop.set()
        _lifecycle_stop.set()
        _leader_stop.set()
        leader_task.cancel()
        if trigger_consumer_task is not None:
            trigger_consumer_task.cancel()
        if lifecycle_consumer_task is not None:
            lifecycle_consumer_task.cancel()
        if scheduler_task is not None:
            scheduler_task.cancel()
        if idle_sweep_task is not None:
            idle_sweep_task.cancel()
        if grounding_rebuild_task is not None:
            grounding_rebuild_task.cancel()
        if consolidation_task is not None:
            consolidation_task.cancel()
        # Drain in-flight /run + /approve turns before tearing down clients so a
        # rolling deploy doesn't abort an audited write mid-commit. The SSE layer
        # detaches these as background tasks (so a client disconnect doesn't kill
        # the commit) — give them a bounded window to finish here.
        try:
            import sse as _sse
            drain = getattr(_sse, "drain_inflight", None)
            if drain is not None:
                await asyncio.wait_for(drain(), timeout=float(os.getenv("SMARTAPP_SHUTDOWN_DRAIN_SECONDS", "30")))
        except asyncio.TimeoutError:
            logger.warning("smartapp shutdown: in-flight run drain timed out; proceeding")
        except Exception as _exc:  # noqa: BLE001 — best-effort drain
            logger.warning("smartapp shutdown: in-flight drain skipped: %s", _exc)
        # Close the shared pooled httpx client so keep-alive sockets are drained.
        try:
            from http_client import aclose_http_client
            await aclose_http_client()
        except Exception as _exc:  # noqa: BLE001
            logger.warning("smartapp shutdown: http client close skipped: %s", _exc)
        if _mongo_client is not None:
            _mongo_client.close()


# Error tracker (GlitchTip / Sentry) — no-op unless SENTRY_DSN is set
try:
    import sentry_sdk
    from sentry_sdk.integrations.fastapi import FastApiIntegration
    from sentry_sdk.integrations.starlette import StarletteIntegration
    from sentry_sdk.integrations.asyncio import AsyncioIntegration
    from sentry_sdk.integrations.logging import LoggingIntegration
    _sentry_dsn = os.getenv("SENTRY_DSN", "").strip()
    if _sentry_dsn:
        sentry_sdk.init(
            dsn=_sentry_dsn,
            environment=os.getenv("ENVIRONMENT", "prod"),
            release=os.getenv("GIT_SHA", "unknown"),
            traces_sample_rate=float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.05")),
            send_default_pii=False,
            attach_stacktrace=True,
            max_breadcrumbs=50,
            integrations=[
                FastApiIntegration(transaction_style="endpoint"),
                StarletteIntegration(transaction_style="endpoint"),
                AsyncioIntegration(),
                LoggingIntegration(level=logging.INFO, event_level=logging.ERROR),
            ],
        )
        sentry_sdk.set_tag("service", "smart-app-service")
except Exception as _sentry_exc:
    logger.warning("Sentry init skipped: %s", _sentry_exc)


app = FastAPI(
    title="Citra Smart App Service",
    version="0.1.0",
    description="Build, persist, and serve Citra Power AI Apps.",
    lifespan=lifespan,
)


@app.exception_handler(LLMRateLimitError)
async def _llm_rate_limit_handler(request: Request, exc: LLMRateLimitError):
    """Per-user LLM-call rate limit hit → HTTP 429 with Retry-After. Covers
    every path that calls the LLM (run / chat / sub-agent); trigger runs catch
    it earlier and record a failed firing instead."""
    return JSONResponse(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        content={
            "detail": str(exc),
            "code": "llm_rate_limited",
            "limit": exc.limit,
            "window_seconds": exc.window,
            "retry_after": exc.retry_after,
        },
        headers={"Retry-After": str(exc.retry_after)},
    )


# Distributed tracing — propagates X-Request-ID + W3C traceparent on
# outbound calls to sandbox-host, builder pods.
try:
    from citra_service_utils import (
        setup_tracing as _setup_tracing,
        request_id_middleware as _request_id_middleware,
    )
    app.middleware("http")(_request_id_middleware)
    _setup_tracing(app, service_name="smart-app-service")
except ImportError:
    logger.warning("citra-service-utils not installed; distributed tracing disabled")

app.add_middleware(
    CORSMiddleware,
    # Restrict to the runtime + UI origins. Wildcard is rejected because
    # we forward Authorization through the runtime's API proxies; even with
    # allow_credentials=False this is unnecessarily permissive.
    # An empty/whitespace-only CORS_ALLOWED_ORIGINS falls back to the dev
    # default — os.getenv only uses the default when the var is *unset*,
    # so treat an empty string the same as missing.
    allow_origins=[
        o.strip()
        for o in (
            os.getenv("CORS_ALLOWED_ORIGINS", "").strip()
            or "http://localhost:3000,http://localhost:3100,http://localhost:8081"
        ).split(",")
        if o.strip()
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    # X-Device-ID is sent by the Citra-UI authService on every authenticated
    # request; omitting it makes the browser preflight fail with 400.
    allow_headers=["Authorization", "Content-Type", "Accept", "X-Device-ID"],
)

@app.middleware("http")
async def _capture_embed_key(request: Request, call_next):
    """Carry the embed key into request context for _bind_app_env.

    An embedded card resolves its environment from the key prefix on its FIRST
    call (/embed/{key}/spec). Every call after that is addressed by slug — run,
    panel data, detail, approve — and a PROMOTED app exists in both stores, so
    slug resolution returns prod. Without this, a bank's UAT card would read and
    write PRODUCTION records the moment the app was promoted.

    Captured here rather than threaded through ~25 handler signatures: missing
    one endpoint is precisely how the gap arose, and a middleware cannot miss
    one. The header is only a HINT — _bind_app_env verifies the key exists in
    that environment bound to the requested slug before honouring it.
    """
    set_current_embed_key(request.headers.get("x-citra-embed-key"))
    return await call_next(request)


# Authentication. Public endpoints (/health, /publish, /docs in dev) are
# whitelisted inside JWTAuthMiddleware itself. Everything else requires a
# valid Citra user JWT.
app.add_middleware(JWTAuthMiddleware)


# Internal proxy endpoints used by the builder pod and runtime engine.
# These bypass the user-JWT middleware (whitelisted in
# auth.JWTAuthMiddleware.public_patterns) and run their own HMAC-bearer
# validation per request — see internal_routes._require_internal_claims.
from internal_routes import router as _internal_router  # noqa: E402

app.include_router(_internal_router)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _runtime_url(slug: str, settings: Settings) -> str:
    return f"{settings.apps_base_url.rstrip('/')}/{slug}"


def _pod_reachable_url(url: str) -> str:
    """Rewrite a host-loopback URL so it resolves from inside a builder pod.

    smart-app-service runs on the host in local dev, so service URLs in
    its env (e.g. ``DISCOVERY_SERVICE_URL=http://localhost:9000``) point at
    the host's loopback. Injected verbatim into a container, ``localhost``
    is the *container itself* — unreachable. Docker Desktop exposes the
    host as ``host.docker.internal``; rewrite to that. In prod the URLs
    are in-cluster service names (no ``localhost``) so this is a no-op.
    """
    if not url:
        return url
    return url.replace("//localhost", "//host.docker.internal").replace(
        "//127.0.0.1", "//host.docker.internal"
    )


def _summary(
    doc: dict, settings: Settings, *, caller: Optional[dict] = None
) -> AppSummary:
    spec = doc.get("app_spec", {}) or {}
    # 'dashboard' is no longer an artefact kind — coerce a legacy stored value
    # so AppSummary.kind (AppKind) validates, and surface the dashboard signal
    # via has_dashboard_page (any page.kind=='dashboard', or a legacy doc).
    raw_kind = spec.get("kind", "app")
    has_dashboard_page = raw_kind == "dashboard" or any(
        (p or {}).get("kind") == "dashboard" for p in (spec.get("pages") or [])
    )
    has_embed_page = any(
        (p or {}).get("kind") == "embed" for p in (spec.get("pages") or [])
    )
    kind = "app" if raw_kind == "dashboard" else raw_kind
    # Automation mode for the list-card button label: if ANY trigger commits
    # autonomously the card should read "Auto-Process", otherwise "Auto-Recommend"
    # (recommend triggers, or no trigger yet → the default).
    _trigs = spec.get("triggers") or []
    automation_mode = (
        "auto_process"
        if any((t or {}).get("execution_mode") == "auto_process" for t in _trigs)
        else "recommend"
    )
    return AppSummary(
        # Defensive: a published app always has a top-level app_id, but never let
        # a single doc missing it KeyError and 500 the whole list — fall back to
        # the slug (always present) so the list stays resilient.
        app_id=doc.get("app_id") or doc.get("slug") or "",
        slug=doc["slug"],
        title=spec.get("title", doc.get("slug", "")),
        description=spec.get("description"),
        owner_type=spec.get("owner_type") or "service_account",
        owner_id=spec.get("owner_id"),
        tenant_id=doc.get("tenant_id"),
        kind=kind,
        has_dashboard_page=has_dashboard_page,
        has_embed_page=has_embed_page,
        has_embed_key=bool(doc.get("embed_key")),
        automation_mode=automation_mode,
        has_automation=bool(_trigs),
        status=AppStatus(doc.get("status", "draft")),
        version=doc.get("version", 1),
        deployed_at=doc.get("deployed_at"),
        url=_runtime_url(doc["slug"], settings),
        audience=spec.get("audience") or "owner",
        grounded=bool(doc.get("grounded")),
        fraud_enabled=bool(doc.get("fraud_enabled")),
        headless=bool(spec.get("headless")),
        can_edit=(
            _can_edit_app(
                doc,
                caller.get("user_id") or "",
                caller.get("tenant_id"),
                user_org_id=caller.get("org_id"),
                user_dept_ids=caller.get("dept_ids"),
                user_roles=caller.get("roles"),
                sa_admin_of=caller.get("sa_admin_of"),
            )
            if caller is not None
            else True
        ),
    )


def _safe_summaries(
    docs: list, settings: Settings, caller: Optional[dict]
) -> list:
    """Map docs → AppSummary, skipping (and loudly logging) any single doc that
    fails to summarise. One malformed app doc must never 500 the entire list."""
    out = []
    for d in docs:
        try:
            out.append(_summary(d, settings, caller=caller))
        except Exception as exc:  # noqa: BLE001 — isolate one bad doc, keep the list
            logger.error(
                "[list_apps] skipping unsummarisable app doc slug=%s id=%s: %s",
                (d or {}).get("slug"), (d or {}).get("_id"), exc,
            )
    return out


def _user_can_access(app_doc: dict, request: Request) -> bool:
    """Authorize read access to an app — audience-based (fail-closed).

    Every app is owned by a Service Account (``owner_type=service_account``);
    edit access flows through that SA. Read/Run access is controlled by the
    ``audience`` field on the AppSpec.

    Grant access when ANY of:
      * caller is super_admin;
      * caller is a member/admin of the owning SA (owner always sees);
      * the audience matches the caller:
          - 'owner'        → covered by SA membership above
          - 'team:<sa>'    → caller is a member/admin of that SA
          - 'dept:<dept>'  → caller belongs to that dept
          - 'org'          → caller is in the app's tenant
    """
    roles = get_user_roles(request)
    if "super_admin" in roles:
        return True
    user_tenant = get_tenant_id(request)
    spec = app_doc.get("app_spec") or {}

    owner_id = spec.get("owner_id")
    sa_ids = set(get_sa_admin_of(request) + get_sa_member_of(request))
    if owner_id and owner_id in sa_ids:
        return True

    # Scoped admins of the OWNING scope can read too (edit ⇒ read) — mirrors
    # _can_edit_app. Without this, an org_admin/dept_admin who oversees the app
    # (and sees Edit / Auto-Recommend / Audit on the Admin tab) would 404 on every
    # one of those GETs, since they aren't in the consumer audience. super_admin
    # is handled above.
    app_org_id = spec.get("org_id") or app_doc.get("tenant_id")
    caller_org = get_org_id(request) or user_tenant
    if "org_admin" in roles and app_org_id and app_org_id == caller_org:
        return True
    # dept_admin must be scoped to the OWNING ORG too — dept_ids are non-unique
    # human strings reused across tenants, so a dept overlap alone would leak
    # cross-tenant read/run access. Require same-org AND dept overlap.
    if (
        "dept_admin" in roles
        and app_org_id
        and app_org_id == caller_org
        and (set(spec.get("dept_ids") or []) & set(get_user_dept_ids(request)))
    ):
        return True

    audience = spec.get("audience") or "owner"
    try:
        level, target = parse_audience(audience)
    except ValueError:
        return False  # corrupt audience → fail-closed

    if level == "owner":
        return False  # owner-SA membership was already the only path
    if level == "team":
        return bool(target) and target in sa_ids
    if level == "dept":
        # Scope to the app's org too — dept_ids are non-unique human strings
        # reused across tenants, so dept membership ALONE would leak access
        # cross-tenant. Require same-org AND dept membership.
        app_org = spec.get("org_id") or app_doc.get("tenant_id")
        return (
            bool(target)
            and target in set(get_user_dept_ids(request))
            and bool(app_org)
            and app_org == caller_org
        )
    if level == "org":
        tenant_id = app_doc.get("tenant_id") or spec.get("tenant_id")
        return bool(tenant_id) and bool(user_tenant) and tenant_id == user_tenant
    return False


def _can_render_app(
    app_doc: dict,
    request: Request,
    user_id: Optional[str] = None,
    user_tenant: Optional[str] = None,
) -> bool:
    """Access check for read endpoints (spec + panel data) the runtime serves.

    There is NO trusted-runtime god scope. citra-app-runtime forwards the END
    USER's JWT (launched from Citra-UI as ``?_t=``, validated here with the
    shared JWT_SECRET — exactly as Citra-Service validates it). Access is the
    user's OWN audience-based access, so an anonymous caller (no / invalid user
    token, or a bare ``scope=citra-app-runtime`` token with no user identity)
    gets nothing.

    - PUBLISHED app → any caller whose audience grants access (``_user_can_access``).
    - Unpublished (draft / test / archived) → only owner-SA admins / editors,
      so in-flight work is never exposed to audience members.
    """
    if not _user_can_access(app_doc, request):
        return False
    if (app_doc.get("status") or "draft") == AppStatus.PUBLISHED.value:
        return True
    # Unpublished: restrict to editors/owners (audience members can't see drafts).
    return _can_edit_app(
        app_doc,
        get_secure_user_id(request),
        get_tenant_id(request),
        user_org_id=get_org_id(request),
        user_dept_ids=get_user_dept_ids(request),
        user_roles=get_user_roles(request),
        sa_admin_of=get_sa_admin_of(request),
    )


def _can_edit_app(
    app_doc: dict,
    user_id: str,
    user_tenant: Optional[str],
    *,
    user_org_id: Optional[str] = None,
    user_dept_ids: Optional[List[str]] = None,
    user_roles: Optional[List[str]] = None,
    sa_admin_of: Optional[List[str]] = None,
) -> bool:
    """Who may edit / delete this app — owner-SA admin (or override role).

    Authorised when ANY of:
      * caller is super_admin
      * caller is an admin of the owning SA (always — SA-owned by design)
      * caller has org_admin role AND the app's tenant matches theirs
      * caller has dept_admin role AND a dept_ids overlap with the app's
    """
    roles = list(user_roles or [])
    dept_ids = list(user_dept_ids or [])
    sa_admin = list(sa_admin_of or [])

    spec = app_doc.get("app_spec") or {}
    owner_type = spec.get("owner_type") or "service_account"
    owner_id = spec.get("owner_id")
    app_org_id = spec.get("org_id") or spec.get("tenant_id") or app_doc.get("tenant_id")
    # An app's dept is declared in TWO places and this read only ever saw one.
    # `dept_ids` is what the builder authors; `audience = "dept:<id>"` is what
    # publishing sets, and it is what the UI renders as the app's dept badge.
    # acme-bank's lending apps shipped with dept_ids=[] and audience="dept:lending",
    # so the card said "Dept · lending" while the dept_admin OF lending was
    # refused their own app's memory surface — the gate disagreeing with the
    # label above it. `_app_dept_ids` already unions both for halt scoping;
    # authorization must read the same union or the two drift apart again.
    app_dept_ids = list(spec.get("dept_ids") or [])
    _aud = spec.get("audience") or ""
    if isinstance(_aud, str) and _aud.startswith("dept:"):
        _aud_dept = _aud.split(":", 1)[1].strip()
        if _aud_dept and _aud_dept not in app_dept_ids:
            app_dept_ids.append(_aud_dept)

    if "super_admin" in roles:
        return True
    if owner_type == "service_account" and owner_id and owner_id in sa_admin:
        return True
    if "org_admin" in roles and app_org_id and app_org_id == user_org_id:
        return True
    # dept_admin must be scoped to the OWNING ORG — dept_ids are non-unique
    # human strings reused across tenants, so a dept overlap alone would grant
    # cross-tenant edit. Require same-org AND dept overlap.
    if (
        "dept_admin" in roles
        and app_org_id
        and app_org_id == user_org_id
        and app_dept_ids
        and any(d in dept_ids for d in app_dept_ids)
    ):
        return True

    return False


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


@app.get("/health", response_model=HealthResponse)
async def health(settings: Settings = Depends(get_settings)) -> HealthResponse:
    mongo_ok = False
    try:
        if _mongo_client is not None:
            await _mongo_client.admin.command("ping")
            mongo_ok = True
    except Exception as e:  # noqa: BLE001
        logger.warning("Mongo ping failed: %s", e)

    return HealthResponse(
        status="ok" if mongo_ok else "degraded",
        environment=settings.environment,
        mongo_connected=mongo_ok,
    )


class AuditIngestRequest(BaseModel):
    """Batch of dept-MCP data-plane audit records to persist."""
    records: List[Dict[str, Any]] = Field(default_factory=list)


@app.post("/api/audit/ingest")
async def ingest_dept_query_audit(request: Request, body: AuditIngestRequest):
    """Persist a batch of dept-MCP data-plane audit records into
    ``dept_query_audit`` (the Operational Data Flow trail read by citra-workflow).

    The customer-side dept-MCPs POST here instead of writing the central Citra
    Mongo directly — so they hold NO Citra DB credentials. Auth: the MCP mints an
    HS256 token on the shared JWT_SECRET carrying ``svc="mcp_audit"``; the JWT
    middleware has already verified the signature, so here we only require the
    service claim (a plain user token must NOT be able to write audit → 403).

    ``mcp_org_id`` is stamped SERVER-side from the authenticated token so a
    compromised MCP can't forge another org's provenance. Best-effort but LOUD on
    a store failure (5xx) so the MCP keeps its records buffered and retries."""
    claims = getattr(request.state, "user", None) or {}
    if claims.get("svc") != "mcp_audit":
        raise HTTPException(status_code=403, detail="audit ingest requires an MCP service token")
    if _dept_query_audit_col is None:
        raise HTTPException(status_code=503, detail="audit store not initialised")

    records = [r for r in (body.records or []) if isinstance(r, dict)]
    if not records:
        return {"written": 0}
    now = datetime.now(timezone.utc)
    caller_org = claims.get("org_id")
    for r in records:
        r["received_at"] = now
        if caller_org:                       # server-authoritative MCP identity
            r["mcp_org_id"] = caller_org
    res = await _dept_query_audit_col.insert_many(records, ordered=False)
    return {"written": len(res.inserted_ids)}


# ---------------------------------------------------------------------------
# Publish (called by builder pod)
# ---------------------------------------------------------------------------


def _can_access_build_session(doc: dict, request: Request) -> bool:
    """A build session is owned by a Work SA (``doc["owner"]``).

    The caller may use it when they are an admin or member of that SA, or
    a super_admin. There is no user-id ownership — access is SA-membership.
    """
    owning_sa = doc.get("owner")
    if not owning_sa:
        return True  # ownerless system/legacy session — no SA gate
    u = getattr(request.state, "user", None) or {}
    if "super_admin" in (u.get("roles") or []):
        return True
    sa_ids = set(
        (u.get("service_account_admin_of") or [])
        + (u.get("service_account_member_of") or [])
    )
    return owning_sa in sa_ids


def _publisher_own_work_sa(
    publisher: dict, tenant_id: Optional[str]
) -> Optional[str]:
    """The publisher's OWN Work SA id — for a direct human dev-publish that
    carries no build session (the builder path resolves ownership from the
    build session instead; see ``publish_app``).

    Resolution:
      1. ``publisher["work_sa_id"]`` — present on a BA's login JWT.
      2. Else derive it with the SAME deterministic rule as ``/build`` and
         Citra-User-Service (``svc:work-<slug>@<org>.citra.ai`` — see
         ``_work_sa_id``). This is the id the user actually owns; we never
         synthesize a divergent id, which would silently orphan the app
         under an owner no one is admin of.

    Returns ``None`` when there is no real user to derive from (a bare
    ``builder:<session>`` placeholder), so the caller rejects loud with
    ``work_sa_id_missing`` rather than guessing.
    """
    explicit = (publisher.get("work_sa_id") or "").strip()
    if explicit:
        return explicit
    user_id = (publisher.get("user_id") or "").strip()
    if not user_id or user_id.startswith("builder:"):
        return None
    return _work_sa_id(user_id, publisher.get("org_id") or tenant_id)


def _build_published_summary(app_spec: AppSpec, version: int) -> str:
    """A BA-facing description of exactly WHAT was published — pages + panels +
    any AI triggers — relayed verbatim so the BA knows what just went live."""
    title = app_spec.title or app_spec.slug
    lines: List[str] = []
    pages = app_spec.pages or []
    if pages:
        lines.append(f"Published **{title}** — a {len(pages)}-page app (v{version}):")
        for p in pages:
            ptypes = sorted({pn.type for pn in (p.panels or [])})
            tag = " · dashboard" if getattr(p, "kind", "standard") == "dashboard" else ""
            lines.append(f"  • {p.title or p.id}{tag} — {', '.join(ptypes) or 'empty'}")
    else:
        ptypes = sorted({pn.type for pn in (app_spec.panels or [])})
        lines.append(f"Published **{title}** (v{version}) — panels: {', '.join(ptypes) or 'none'}.")
    trigs = app_spec.triggers or []
    if trigs:
        lines.append(
            f"{len(trigs)} AI trigger(s) run the app's agent automatically and stage a "
            "recommendation for the officer to approve. ⚠️ **Published INACTIVE for "
            "safety** — review each, then turn it on in the app's **Auto-Recommend** panel:"
        )
        for t in trigs:
            if t.type == "webhook":
                detail = "webhook (URL in the Auto-Recommend panel)"
            elif t.type == "poll":
                detail = f"poll `{t.tool}` for new rows"
            elif t.type == "schedule.cron":
                detail = f"schedule `{t.cron}`"
            else:
                detail = f"every {t.every_seconds or 60}s"
            lines.append(f"  • {t.id} → runs `{t.action}` — **inactive**; {detail}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Spec version history (snapshot-on-overwrite + rollback)
# ---------------------------------------------------------------------------
# Every publish / spec-edit / promote overwrites the live app + agent docs in
# place. Before that overwrite, we snapshot the OUTGOING (app_spec, agent_spec)
# as one atomic revision so an officer can roll the app back to a known-good
# state. History is trimmed to ``max_spec_versions`` (default 3) per app. The
# live version is never a snapshot — it is snapshotted only when superseded —
# so a rollback is itself reversible (it snapshots the current live version
# first). See GET /apps/{slug}/versions and POST /apps/{slug}/versions/{v}/rollback.

def _version_summary(
    *, version: int, app_spec: Dict[str, Any], agent_version: Optional[int],
    is_current: bool, snapshotted_at: Any = None, snapshotted_by: str = "",
    reason: str = "", status: Optional[str] = None,
) -> Dict[str, Any]:
    """Compact, UI-facing descriptor for one version (current or snapshot).
    Never ships the full spec — the rollback call re-reads that by version."""
    spec = app_spec or {}
    triggers = spec.get("triggers") or []
    return {
        "version": version,
        "is_current": is_current,
        "title": spec.get("title") or spec.get("display_name") or spec.get("slug"),
        "trigger_count": len(triggers),
        "active_trigger_count": sum(
            1 for t in triggers if isinstance(t, dict) and t.get("enabled")
        ),
        "agent_version": agent_version,
        "status": status,
        "snapshotted_at": snapshotted_at,
        "snapshotted_by": snapshotted_by,
        "reason": reason,
    }


async def _snapshot_prior_version(
    *,
    apps_col: AsyncIOMotorCollection,
    agents_col: AsyncIOMotorCollection,
    versions_col: AsyncIOMotorCollection,
    app_doc_before: Optional[Dict[str, Any]],
    actor: str,
    reason: str,
    max_keep: int,
) -> None:
    """Snapshot the OUTGOING (app_spec + agent_spec) revision BEFORE it is
    overwritten, then trim ``slug`` to the ``max_keep`` most-recent snapshots.

    Pass the SAME-environment collection handles the caller is about to write to
    (routed accessors for publish/edit, raw prod handles for promote) so the
    snapshot lands in the matching store. No-op when there is no prior published
    version (first publish) — there is nothing to roll back to yet.

    Fail-loud: a snapshot failure raises, aborting the publish/edit/promote, so
    we never silently overwrite a version we failed to preserve.
    """
    if not app_doc_before:
        return
    prior_version = int(app_doc_before.get("version") or 0)
    if prior_version < 1:
        return
    slug = app_doc_before.get("slug")
    agent_id = app_doc_before.get("agent_id")
    # Read the prior agent doc BEFORE the caller overwrites it. Must be called
    # while the live agent doc still holds the superseded agent_spec.
    agent_doc = (
        await agents_col.find_one({"agent_id": agent_id}) if agent_id else None
    )
    rev = {
        "slug": slug,
        "app_id": app_doc_before.get("app_id"),
        "version": prior_version,
        "agent_id": agent_id,
        "tenant_id": app_doc_before.get("tenant_id"),
        "app_spec": app_doc_before.get("app_spec"),
        "agent_spec": (agent_doc or {}).get("agent_spec"),
        "agent_version": (agent_doc or {}).get("version"),
        "grounded": bool(app_doc_before.get("grounded")),
        "status": app_doc_before.get("status"),
        "snapshotted_at": datetime.now(timezone.utc),
        "snapshotted_by": actor or "system",
        "reason": reason,
    }
    # Idempotent on (slug, version): a re-published version overwrites its snap.
    await versions_col.replace_one(
        {"slug": slug, "version": prior_version}, rev, upsert=True
    )
    # Trim: keep only the ``max_keep`` highest versions for this slug. Sort in
    # Python (N is tiny — ``max_keep`` + 1) so trimming is deterministic and does
    # not depend on server-side cursor ordering. Delete by (slug, version) — the
    # unique key — rather than _id, so the drop set is unambiguous.
    keep = max(1, max_keep)
    all_versions = sorted(
        {
            int(d.get("version") or 0)
            async for d in versions_col.find({"slug": slug}, {"version": 1})
        },
        reverse=True,
    )
    drop_versions = all_versions[keep:]
    if drop_versions:
        await versions_col.delete_many(
            {"slug": slug, "version": {"$in": drop_versions}}
        )
    logger.info(
        "[spec-version] snapshot %s v%d (reason=%s, kept<=%d) by %s",
        slug, prior_version, reason, max_keep, actor or "system",
    )


@app.post("/publish", response_model=PublishResponse)
async def publish_app(
    payload: PublishRequest,
    request: Request,
    settings: Settings = Depends(get_settings),
) -> PublishResponse:
    # Builder pods authenticate with a scoped sandbox token; humans (in dev)
    # can publish with their user JWT. Either way we require *some* token.
    publisher = require_publish_scope(request)
    publisher_tenant = publisher.get("tenant_id") or publisher.get("org_id")
    auth_header = request.headers.get("authorization")

    # The builder always publishes into the TEST environment (when configured):
    # the app + agent land in the test_ collections and the BA tests them there
    # against the test MCPs. Promote later copies the spec to the prod
    # collections. No test env configured → legacy prod publish (see
    # _bind_build_env). Set BEFORE any get_apps_col()/get_agents_col() write.
    _bind_build_env()

    # ── Ownership is smart-app-service's concern, NOT the builder's ──
    # A builder pod must not assert who owns the app. smart-app-service
    # recorded the owning Work SA + tenant on the build session from the
    # AUTHENTICATED BA at /build; resolve them here by session_id (the pod
    # token only proves "I am the builder for this session"). A direct human
    # dev-publish has no build session and falls back to its own identity
    # (see _publisher_own_work_sa). Done AFTER _bind_build_env so the session
    # is read from the same (test_) store /build wrote it to.
    session_owner: Optional[str] = None
    session_tenant: Optional[str] = None
    if publisher.get("scope") == "smart-app-builder":
        _sid = (
            publisher.get("session_id")
            or getattr(payload, "session_id", None)
            or ""
        ).strip()
        _sess = (
            await get_build_sessions_col().find_one({"session_id": _sid})
            if _sid
            else None
        )
        if _sess:
            session_owner = (_sess.get("owner") or "").strip() or None
            session_tenant = (_sess.get("tenant_id") or "").strip() or None

    # A test publish is BA-only: force audience="owner" so an unpromoted app in
    # the test_ store is visible/runnable ONLY to the builder, never to the
    # team/dept/org. The BA picks the real audience at promote-to-prod time.
    # Test writes COMMIT against test_ collections (no dry-run).
    if current_env() == "test" and payload.app_spec is not None:
        payload.app_spec.audience = "owner"

    # SmartApps are always an app (+ agent). There is no workflow path —
    # automation is an app trigger (app_spec.triggers[]). app_spec is required.
    if payload.app_spec is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="PublishRequest must include app_spec",
        )
    publish_warnings: list[dict] = []

    for _t in (payload.app_spec.triggers or []):
        # SAFETY: AI triggers publish DEACTIVATED — a builder-authored Trigger
        # defaults enabled=True, but a freshly published app must not start
        # firing the agent until a human activates it in the Auto-Recommend panel.
        _t.enabled = False

    # Tenant ownership enforcement (defence-in-depth):
    # The publisher must speak for the same tenant the AppSpec claims, OR
    # the AppSpec must inherit from the publisher's tenant when it left
    # tenant_id unset. Cross-tenant publishes were previously possible by
    # crafting an AppSpec with a different tenant_id.
    spec_tenant = payload.app_spec.tenant_id
    if spec_tenant and publisher_tenant and spec_tenant != publisher_tenant:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"app_spec.tenant_id '{spec_tenant}' does not match the "
                f"publisher's tenant '{publisher_tenant}'"
            ),
        )

    # Re-validate via JSON Schema as a belt-and-braces check before persistence.
    # Strip None for fields the server fills in (app_id, version, deployed_at,
    # status) so JSON Schema 'type: string' constraints don't reject defaults.
    # 'dashboard' is no longer a top-level kind — it's a page (page.kind ==
    # 'dashboard') inside a kind='app'. has_dashboard_page gates the few
    # publish steps that differ for the executive surface (skip auto-chart
    # injection, which the curated dashboard page already owns).
    has_dashboard_page = any(
        getattr(p, "kind", "standard") == "dashboard"
        for p in payload.app_spec.pages
    )
    try:
        validate_app_spec(
            payload.app_spec.model_dump(mode="json", exclude_none=True)
        )
        if payload.agent_spec is not None:
            validate_agent_spec(
                payload.agent_spec.model_dump(mode="json", exclude_none=True)
            )
    except (JsonSchemaValidationError, PydanticValidationError) as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Spec validation failed: {e}",
        )

    # Publish-time cost-gate: vision_ocr tool requires OCR to be configured
    # on this deployment. Fails the publish loud rather than silently
    # producing an app whose runtime can't actually OCR anything. Dashboard
    # narrator agents typically don't declare vision_ocr — but if they do
    # (a BA might want OCR over screenshot artefacts), the same gate applies.
    if payload.agent_spec is not None:
        wants_ocr = any(
            t.kind in ("vision_ocr", "image_analyze", "doc_extract")
            for t in (payload.agent_spec.tools_v2 or [])
        )
        if wants_ocr and not settings.ocr_enabled:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "ocr_not_configured",
                    "message": (
                        "AgentSpec declares the vision_ocr tool but this"
                        " smart-app-service deployment has no VISION_BASE_URL"
                        " / VISION_API_KEY / VISION_MODEL configured."
                        " Either configure the vision endpoint or remove"
                        " the vision_ocr tool from the AgentSpec."
                    ),
                },
            )

    # Publish-time cost-gate: neighbor_samples tool requires Milvus +
    # an embedding endpoint to be reachable from smart-app-service. Same
    # fail-loud principle as vision_ocr — never publish an agent whose
    # runtime can't actually query the few-shot collection.
    if payload.agent_spec is not None:
        wants_neighbors = any(
            t.kind == "neighbor_samples"
            for t in (payload.agent_spec.tools_v2 or [])
        )
        if wants_neighbors:
            missing: List[str] = []
            if not settings.milvus_uri:
                missing.append("MILVUS_URI (or ZILLIZ_CLOUD_URI)")
            # Embedding endpoint only needed for the 'neighbors' mode
            # (vector search). 'canonical' mode is filter-only. Be
            # strict: any neighbors-mode tool needs embedding configured.
            wants_neighbors_mode = any(
                (t.kind == "neighbor_samples"
                 and (getattr(t, "mode", "neighbors") or "neighbors") == "neighbors")
                for t in (payload.agent_spec.tools_v2 or [])
            )
            if wants_neighbors_mode and not settings.embedding_base_url:
                missing.append("EMBEDDING_BASE_URL")
            if missing:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail={
                        "code": "neighbor_samples_not_configured",
                        "message": (
                            "AgentSpec declares the neighbor_samples tool"
                            " but this smart-app-service deployment is"
                            f" missing required config: {', '.join(missing)}."
                            " Either configure these env vars or remove"
                            " the neighbor_samples tool from the AgentSpec."
                        ),
                        "missing": missing,
                    },
                )

    # Cross-check every action.data_bindings against the data-discovery
    # catalogue. Hard errors block publish; warnings flow into
    # AppSpec.requirements_unmet so the BA sees them in the builder UI.
    binding_errors: List[Dict[str, Any]] = []
    binding_warnings: List[Dict[str, Any]] = []
    # Always validate action data_bindings: an app may freely mix a
    # dashboard page with action pages. A pure-dashboard app (narrator with
    # read-only tools, no write bindings) makes this a no-op.
    if payload.agent_spec is not None:
        binding_errors, binding_warnings = await validate_data_bindings(
            payload.agent_spec,
            settings=settings,
            auth_header=auth_header,
            tenant_id=publisher_tenant or "",
            data_sources=(payload.app_spec.data_sources if payload.app_spec else None),
            app_spec=payload.app_spec,
        )
        if binding_errors:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "message": "data_bindings reference unknown datasets/columns/actions",
                    "errors": binding_errors,
                },
            )

    # Cross-check dashboard/chart/queue PANEL column references against the
    # catalogue. data_bindings (above) only covers AgentSpec action reads/
    # writes; a KPI metric field, filter predicate, chart axis or queue column
    # can be hallucinated independently (e.g. SUM("previous_arrears_balance")
    # when the real column is "arrears_carried"). Catch it here at publish —
    # fail LOUD — instead of letting it render a blank/broken tile in prod.
    # FIRST: assert every structured data_source.ref resolves to a real
    # catalogue dataset. A malformed ref (e.g. the builder duplicating the
    # source as a path prefix — "field_operations/field_operations.complaints")
    # parses to a bogus source_id that discovery can't resolve, so EVERY panel
    # renders empty at runtime ("UI but no data"). validate_panel_columns
    # defensively *skips* a data_source whose catalogue entry is absent, so it
    # cannot catch this — we must reject the bad ref explicitly here, fail LOUD.
    if payload.app_spec is not None:
        ref_errors = await validate_data_source_refs(
            payload.app_spec,
            settings=settings,
            auth_header=auth_header,
            tenant_id=publisher_tenant or "",
        )
        if ref_errors:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "data_source_ref_unresolved",
                    "message": (
                        "A data_source.ref does not resolve to a catalogue "
                        "dataset. The ref MUST be the dataset_id verbatim "
                        "('<source_id>.<table>') — do not prefix it with the "
                        "source_id and a slash. Fix the ref(s) and re-publish."
                    ),
                    "errors": ref_errors,
                },
            )

    if payload.app_spec is not None:
        panel_col_errors = await validate_panel_columns(
            payload.app_spec,
            settings=settings,
            auth_header=auth_header,
            tenant_id=publisher_tenant or "",
        )
        if panel_col_errors:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "panel_columns_unknown",
                    "message": (
                        "A dashboard/chart/queue panel references columns that do "
                        "not exist on the dataset (likely hallucinated). Use only "
                        "columns present in the catalogue for this dataset."
                    ),
                    "errors": panel_col_errors,
                },
            )

    # Cross-check that every mcp/rag tool in tools_v2 resolves to a real
    # registered source in discovery-service. Catches typo'd source_ids
    # (the most common BA mistake) BEFORE the app reaches a real user
    # who'd see a "tool not found" runtime error. Applies to both app
    # and dashboard kinds — the dashboard narrator's mcp tools must
    # resolve too.
    if payload.agent_spec is not None and auth_header:
        unresolved_sources = await validate_tool_sources_resolvable(
            payload.agent_spec,
            settings=settings,
            auth_header=auth_header,
        )
        if unresolved_sources:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "tool_sources_unresolved",
                    "message": (
                        "AgentSpec declares mcp/rag tools whose source_id "
                        "is not registered in discovery-service. The BA "
                        "likely typed a wrong source name, or the dept-MCP "
                        "is offline. Either fix the source_id or remove "
                        "the tool from the AgentSpec."
                    ),
                    "unresolved": unresolved_sources,
                },
            )

    # ── Layer B publish validators (W-/H-/L-/P-/T-/D-/C-/A-/S- rules) ─────
    # Each validator returns offender dicts; first non-empty result raises
    # HTTP 422 with detail={code: rule_id, message, errors}. Order matters:
    # cheap pure-data rules first, so a BA fixing one failure doesn't have
    # to wait for an expensive check to re-run.
    layer_b_app = payload.app_spec
    layer_b_agent = payload.agent_spec

    def _raise_layer_b(rule_id: str, message: str, errors: List[Dict[str, Any]]) -> None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": rule_id, "message": message, "errors": errors},
        )

    _b = validate_no_delete_verbs(layer_b_app, layer_b_agent)
    if _b:
        _raise_layer_b(
            "W-01",
            "hard-delete verbs are forbidden — use a soft-delete write_action.",
            _b,
        )
    _b = reject_allow_writes_in_chat(layer_b_agent)
    if _b:
        _raise_layer_b(
            "H-04",
            "remove `hitl_policy.allow_writes_in_chat` from the AgentSpec.",
            _b,
        )
    # C-03 — timer-trigger floor: cron / interval / poll must not fire more
    # often than the operator minimum (default 5 min). C-06 — a TIMER trigger
    # (cron/interval) fires the agent with NO per-record input ({}), so its
    # action must be a BATCH action (no required inputs); per-record work
    # belongs on a poll/webhook trigger. Both enforced here so a misconfigured
    # spec is rejected at publish, not discovered as a silent runtime failure.
    _trig_floor = get_settings().min_trigger_interval_seconds
    _agent_actions = {
        getattr(a, "name", None): a
        for a in (getattr(layer_b_agent, "actions", None) or [])
    }
    for _t in (getattr(layer_b_app, "triggers", None) or []):
        _tt = getattr(_t, "type", None)
        if _tt == "schedule.cron" and getattr(_t, "cron", None):
            _validate_cron_expr(_t.cron, min_interval_seconds=_trig_floor)
        if _tt in ("poll", "schedule.interval"):
            _ev = getattr(_t, "every_seconds", None)
            if _ev is not None and _ev < _trig_floor:
                _raise_layer_b(
                    "C-03",
                    f"{_tt} trigger interval must be >= {_trig_floor}s — each fire "
                    f"runs the agent; a tighter cadence rate-limits the LLM. Use a "
                    f"webhook for near-real-time.",
                    [{"trigger_id": getattr(_t, "id", "?"),
                      "every_seconds": _ev, "min": _trig_floor}],
                )
        if _tt in ("schedule.cron", "schedule.interval"):
            _act = _agent_actions.get(getattr(_t, "action", None))
            _req = ((getattr(_act, "input_schema", None) or {}).get("required")
                    if _act else None) or []
            if _req:
                _raise_layer_b(
                    "C-06",
                    f"the {_tt} trigger '{getattr(_t, 'id', '?')}' fires the agent "
                    f"on a timer with NO per-record input, but its action "
                    f"'{getattr(_t, 'action', None)}' requires {_req}. A timer "
                    f"trigger's action must process a batch (no required inputs) — "
                    f"the agent queries the pending set itself. Use a poll or "
                    f"webhook trigger for per-record work (input via input_template).",
                    [{"trigger_id": getattr(_t, "id", "?"),
                      "action": getattr(_t, "action", None), "requires": _req}],
                )
    _b = validate_grounding_contract(layer_b_agent)
    if _b:
        _raise_layer_b(
            "G-01",
            "a grounded agent (neighbor_samples) needs a vetted grounding contract.",
            _b,
        )
    # Resolve the publishing tenant (spec → build session → publisher token).
    effective_tenant = spec_tenant or session_tenant or publisher_tenant

    # NOTE: PII is NOT the builder's / publish path's concern — it is owned by
    # the infrastructure layer (source + runtime). There are no PII gates here.
    # TODO(T-03): catalogue admin_only index not yet wired through publish
    # path. We pass catalogue_index=None, so the validator no-ops defensively
    # and admin_only actions can be exposed from a SmartApp undetected. To
    # close T-03 we need the per-write-action `admin_only` boolean from the
    # data-discovery-service / MCP catalogue model (write-action metadata
    # feed), built into an index {action_id: {admin_only: bool}} for this
    # app's bound MCP and passed here. Tracked production follow-up —
    # requires cross-service catalogue plumbing, not done here.
    _b = validate_no_admin_actions(layer_b_agent, catalogue_index=None)
    if _b:
        _raise_layer_b(
            "T-03",
            "admin_only catalogue actions cannot be exposed from a SmartApp.",
            _b,
        )
    _b = validate_dashboard_page_has_narrator(layer_b_app, layer_b_agent)
    if _b:
        _raise_layer_b(
            "D-02",
            "a dashboard page requires a narrator agent (app_spec.agent_id).",
            _b,
        )
    _b = validate_chart_axes(layer_b_app)
    if _b:
        _raise_layer_b(
            "V-CHART-01",
            "an aggregated chart has x == y — x (category/time) and y (metric) must be different columns.",
            _b,
        )
    _b = validate_icons(layer_b_app)
    if _b:
        _raise_layer_b(
            "I-01",
            "an icon name is outside the closed icon vocabulary — pick from "
            "models.ICON_NAMES (citra-ui-panels 'Icons' lists them).",
            _b,
        )
    _b = validate_case_signature(layer_b_app)
    if _b:
        _raise_layer_b(
            "CS-01",
            "the case_signature does not resolve against the bound datasets — a "
            "facet on a missing column emits __unknown for every case and "
            "silently learns nothing.",
            _b,
        )
    _b = validate_rubric_finding_matches_declaration(layer_b_app)
    if _b:
        _raise_layer_b(
            "FS-05",
            "the rubric this build recorded finding in the policy does not match "
            "what the spec declares — see the finding and the factor_set.",
            _b,
        )
    _b = validate_factor_set(layer_b_app)
    if _b:
        _raise_layer_b(
            "FS-01",
            "a declared factor reads a dataset this app is not bound to — it "
            "would produce no finding on any case, and under mode='composite' "
            "the case still renders a confident grade over a partial rubric.",
            _b,
        )
    _b = validate_factor_checks_can_score(layer_b_app, layer_b_agent)
    if _b:
        _raise_layer_b(
            "FS-06",
            "a declared factor is served by a mode='rule' check, which returns a "
            "verdict and no number — the factor comes back unscored on every "
            "case and drops out of the composite's denominator.",
            _b,
        )
    _b = validate_case_signature_confirmed(layer_b_app)
    if _b:
        _raise_layer_b(
            "CS-04",
            "the facet families decide the scope of every judgement this app "
            "will ever learn. Propose them to the BA IN CHAT, implement whatever "
            "they change, and record the list they accepted in "
            "confirmed_families.",
            _b,
        )
    _b = validate_case_signature_projection(layer_b_app)
    if _b:
        _raise_layer_b(
            "CS-02",
            "a facet reads a column no panel selects — facets are derived from "
            "the panel's projection, so the family resolves to __unknown on every "
            "case and every clause scoped to it is dead.",
            _b,
        )
    _b = validate_editable_fields(layer_b_app, layer_b_agent)
    if _b:
        _raise_layer_b(
            "E-01",
            "editable_fields must reference real input_schema fields, declared "
            "data sources, and — for enum fields — a static options list "
            "mirroring the enum (else the override renders LOCKED). See each "
            "error's reason for the exact fix.",
            _b,
        )
    _b = validate_direct_write_buttons_confirm(layer_b_app, layer_b_agent)
    if _b:
        _raise_layer_b(
            "W-06",
            "a direct-write tool_button (kind='mcp_action') must set `confirm`.",
            _b,
        )
    _b = validate_no_media_columns(layer_b_app)
    if _b:
        _raise_layer_b(
            "F-01",
            "Citra-stored media columns (format:\"file\") are disabled — SoR "
            "media is read via an mcp data_source and streamed through the MCP.",
            _b,
        )
    _b = validate_update_has_identifier(layer_b_agent)
    if _b:
        _raise_layer_b(
            "W-02",
            "update/upsert/delete-soft actions need an identifier in required[].",
            _b,
        )
    _b = validate_mcp_action_has_input_schema(layer_b_agent)
    if _b:
        _raise_layer_b(
            "W-08",
            "every mcp_action must declare a non-empty input_schema with required "
            "fields — copy the source write_action's input_schema verbatim.",
            _b,
        )
    _b = validate_required_lookup_is_bound(layer_b_agent)
    if _b:
        _raise_layer_b(
            "W-09",
            "a required:true mcp lookup must be bound (set dataset_id + "
            "dataset_kind) so the read-before-write gate can anchor it.",
            _b,
        )
    _b = validate_internal_audience(layer_b_app)
    if _b:
        _raise_layer_b(
            "S-01",
            "audience must be one of owner|team:*|dept:*|org (no public).",
            _b,
        )
    # S-03: scan the WHOLE payload, not just one spec — secrets often
    # leak into trigger config, custom_modules, etc.
    _b = scan_for_secrets(payload.model_dump(mode="json", exclude_none=True))
    if _b:
        _raise_layer_b(
            "S-03",
            "credential-shaped values found in the spec; use env:NAME refs.",
            _b,
        )
    apps = get_apps_col()
    agents = get_agents_col()

    now = datetime.now(timezone.utc)

    slug = payload.app_spec.slug

    existing = await apps.find_one({"slug": slug})
    if existing:
        # Cross-tenant slug squat: the publisher cannot overwrite an existing
        # app belonging to a different tenant just because they picked the
        # same slug.
        existing_tenant = existing.get("tenant_id")
        if (
            existing_tenant
            and publisher_tenant
            and existing_tenant != publisher_tenant
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"slug '{slug}' is owned by another tenant",
            )
        # FS-02 — factor_set.mode is permanent for a published app. This is the
        # one factor-set rule that cannot live in Layer B: it needs the STORED
        # spec, which only this handler has in hand.
        _fs_mode = validate_factor_set_mode_stable(
            payload.app_spec, existing.get("app_spec") or {}
        )
        if _fs_mode:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "factor_set_mode_changed",
                    "message": (
                        "factor_set.mode cannot change on a published app — "
                        "every past decision was recorded under the old mode. "
                        "Publish this as a new app instead."
                    ),
                    "errors": _fs_mode,
                },
            )
        # CS-03 — a published case_signature cannot vanish on a rebuild. Same
        # reason FS-02 lives here: it needs the STORED spec. The builder
        # re-authors the spec from scratch on a rebuild, and an omitted optional
        # field is indistinguishable from a deliberate removal — so the one that
        # silently kills clause memory is refused.
        _cs_lost = validate_case_signature_stable(
            payload.app_spec, existing.get("app_spec") or {}
        )
        if _cs_lost:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "case_signature_dropped",
                    "message": (
                        "this app already publishes a case_signature and this "
                        "version declares none — every clause it has learned "
                        "would stop firing. Carry case_signature across from "
                        "SEED_APP_SPEC unchanged."
                    ),
                    "errors": _cs_lost,
                },
            )
        version = (existing.get("version", 0) or 0) + 1
        app_id = existing["app_id"]
    else:
        version = 1
        app_id = f"app_{uuid.uuid4().hex[:12]}"

    agent_id = payload.agent_spec.agent_id if payload.agent_spec else None
    # effective_tenant (spec → build session → publisher token) — resolved once,
    # used by ownership/audience scoping below.
    effective_tenant = effective_tenant or spec_tenant or session_tenant or publisher_tenant

    # ── Ownership validation (SA-only) ──
    # owner_type="user" is rejected. If the spec didn't pick an SA,
    # we resolve a deterministic Personal SA id for the publisher.
    requested_owner_type = getattr(payload.app_spec, "owner_type", None) or "service_account"
    requested_owner_id = getattr(payload.app_spec, "owner_id", None) or ""
    sa_admin_of = publisher.get("service_account_admin_of", []) or []
    sa_member_of = publisher.get("service_account_member_of", []) or []
    publisher_roles = set(publisher.get("roles") or [])
    is_global_admin = bool(publisher_roles & {"org_admin", "super_admin"})

    if requested_owner_type == "user":
        raise HTTPException(
            status_code=400,
            detail={
                "code": "user_owner_not_supported",
                "message": (
                    "owner_type='user' is not supported. SmartApps must be owned "
                    "by a service_account, dept, or org. Use the publisher's "
                    "Personal SA via POST /api/auth/me/personal-sa or pick an SA "
                    "you are admin/member of."
                ),
            },
        )

    if requested_owner_type == "service_account":
        # Owner resolution, in priority order:
        #   1. Build session owner — AUTHORITATIVE for builder publishes. The
        #      pod cannot choose the owner; smart-app-service recorded it from
        #      the authenticated BA at /build. Overrides whatever the pod put
        #      in the spec.
        #   2. Explicit spec owner_id — a direct human publish targeting an SA.
        #   3. The publisher's own Work SA — a direct human publish that left
        #      owner_id unset. Rejects loud if it can't be resolved.
        # SmartApps are durable team-portable resources — they go on the Work
        # SA, never the Personal SA.
        if session_owner:
            requested_owner_id = session_owner
        elif not requested_owner_id:
            requested_owner_id = _publisher_own_work_sa(publisher, effective_tenant)
            if not requested_owner_id:
                logger.warning(
                    "[publish] reject: work_sa_id unresolved for user=%s tenant=%s",
                    publisher.get("user_id"), effective_tenant,
                )
                raise HTTPException(
                    status_code=400,
                    detail={
                        "code": "work_sa_id_missing",
                        "message": (
                            "Cannot publish: your Work Service Account is not provisioned. "
                            "Sign out + sign in to refresh, or contact your admin to run "
                            "'Fix Service Accounts' on your user record."
                        ),
                    },
                )
        # Authorization: a session-owned publish is authorised by construction
        # (the session owner IS the BA who started the authenticated build).
        # Otherwise the publisher must be admin/member of the owning SA, or a
        # global admin.
        is_own_work_sa = bool(session_owner) or (
            requested_owner_id == _publisher_own_work_sa(publisher, effective_tenant)
        )
        member = (requested_owner_id in sa_admin_of) or (requested_owner_id in sa_member_of)
        if not (member or is_own_work_sa or is_global_admin):
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "service_account_membership_required",
                    "message": (
                        f"caller must be admin or member of {requested_owner_id} to "
                        "publish SmartApps under it"
                    ),
                },
            )
    elif requested_owner_type == "dept":
        dept_ids = publisher.get("dept_ids") or []
        if requested_owner_id not in dept_ids and not is_global_admin:
            raise HTTPException(
                status_code=403,
                detail=f"publisher not a member of dept {requested_owner_id}",
            )
    elif requested_owner_type == "org":
        if requested_owner_id != effective_tenant and "super_admin" not in publisher_roles:
            raise HTTPException(
                status_code=403,
                detail="can only publish org-owned apps in your own org",
            )
        if not is_global_admin:
            raise HTTPException(
                status_code=403,
                detail="org_admin or super_admin required for org-owned apps",
            )

    # ── Audience validation ──
    # Audience controls who can SEE/RUN. The publisher must be authorised
    # to publish at the requested audience level. Default is "owner" (the
    # owning SA's members only); anything wider requires the matching role.
    requested_audience = (getattr(payload.app_spec, "audience", None) or "owner")
    try:
        parse_audience(requested_audience)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "invalid_audience", "message": str(exc)},
        )
    if requested_audience != "owner":
        publisher_claims = {
            "user_id": publisher.get("user_id") or "",
            "email": publisher.get("email") or "",
            "org_id": effective_tenant or "",
            "dept_ids": list(publisher.get("dept_ids") or []),
            "roles": list(publisher.get("roles") or []),
            "service_account_admin_of": sa_admin_of,
            "service_account_member_of": sa_member_of,
        }
        aud_level, aud_target = parse_audience(requested_audience)
        ok, reason = _can_publish_at(publisher_claims, aud_level, aud_target, None)
        if not ok:
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "audience_publish_not_allowed",
                    "audience": requested_audience,
                    "reason": reason,
                    "message": (
                        f"publisher is not authorised to publish at audience "
                        f"'{requested_audience}' ({reason})"
                    ),
                },
            )

    app_spec = payload.app_spec.model_copy(
        update={
            "app_id": app_id,
            "version": version,
            "status": AppStatus.PUBLISHED,
            "deployed_at": now,
            "tenant_id": effective_tenant,
            # SA-only ownership — `owner` mirrors the owning Work SA, never
            # a user id. owner_type/owner_id are the authoritative pair.
            "owner": requested_owner_id,
            "owner_type": requested_owner_type,
            "owner_id": requested_owner_id,
            "org_id": getattr(payload.app_spec, "org_id", None) or effective_tenant,
            "dept_ids": getattr(payload.app_spec, "dept_ids", []) or [],
        }
    )
    if not has_dashboard_page:
        # Builder convention: every app with numeric data should have a chart.
        # If the builder agent forgot, add one. Pure no-op when a chart already
        # exists or when no numeric column can be detected. Skipped when the
        # app has a curated dashboard page (it already owns its charts).
        app_spec = maybe_inject_chart_panel(app_spec)

    # Surface data-binding warnings to the BA via requirements_unmet[].
    if binding_warnings:
        warning_strs = [
            f"{w['code']}@{w['action']}:{w.get('dataset_id','')}"
            # Append the human-readable detail when present, so a warning that
            # carries diagnostic text (e.g. fraud_autowire_failed) reaches the BA
            # instead of surfacing as a bare, context-free code.
            + (f" — {w['detail']}" if w.get("detail") else "")
            for w in binding_warnings
        ]
        merged = list(app_spec.requirements_unmet) + warning_strs
        app_spec = app_spec.model_copy(update={"requirements_unmet": merged})

    # Hydrate the dataset ↔ tool directory from discovery-service. The
    # builder authors actions with (source_id, dataset_id) bindings; we
    # resolve each unique source_id once here so the runtime agent has
    # zero-lookup access to "which MCP serves this dataset?". Fail-soft:
    # if discovery is unreachable the directory is empty and the runtime
    # falls back to per-call resolve_source.
    try:
        from dataset_directory import hydrate_dataset_directory
        directory = await hydrate_dataset_directory(
            app_spec,
            agent_spec=payload.agent_spec,
            discovery_url=(settings.discovery_url_for(current_env()) or "").rstrip("/"),
            user_jwt=(auth_header or "").removeprefix("Bearer ").strip(),
            settings=settings,
            auth_header=auth_header,
            tenant_id=effective_tenant or publisher_tenant or "",
        )
        app_spec = app_spec.model_copy(update={"dataset_directory": directory})
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[publish] dataset_directory hydration failed for slug=%s: %s "
            "(persisting with empty directory; runtime will fall back to "
            "resolve_source)",
            slug, exc,
        )

    # Derive Action.anchor_read from the catalogue so the runtime can
    # deterministically pre-load the base record each decision is about
    # (read-before-write Part A). Input-driven + PK-corroborated, so it works on
    # tables with no primary key. In "enforce" mode this FAILS the publish if an
    # action mutates a keyed record it cannot anchor (an unguardable write).
    _anchor_mode = getattr(settings, "anchor_on_publish_mode", "enforce")
    if payload.agent_spec is not None and _anchor_mode != "off":
        from anchor_derivation import AnchorDerivationError, derive_anchor_reads
        from catalogue_client import fetch_catalogue_entry
        from discovery_cache import DiscoveryError as _DiscErr

        _anchor_tenant = effective_tenant or publisher_tenant or ""

        async def _fetch_cat(src: str, ds: str):
            try:
                return await fetch_catalogue_entry(
                    settings=settings, auth_header=auth_header,
                    tenant_id=_anchor_tenant, dataset_id=ds, source_id=src,
                )
            except _DiscErr:
                return None  # catalogue unreachable → cannot judge; never fail on an outage

        try:
            _anchor_warnings = await derive_anchor_reads(
                payload.agent_spec, fetch_entry=_fetch_cat, mode=_anchor_mode,
            )
        except AnchorDerivationError as exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"read-before-write: {exc}",
            )
        if _anchor_warnings:
            merged = list(app_spec.requirements_unmet) + _anchor_warnings
            app_spec = app_spec.model_copy(update={"requirements_unmet": merged})

    from fraud_roles import fraud_active_for_agent as _fraud_active_for_agent
    # Resolve the ROI money definitions BEFORE the doc build (catalogue
    # lookups; empty dict when no dataset declares value_semantics). Never
    # fails the publish — mis-declarations drop loudly inside the resolver.
    _resolved_value_semantics: Dict[str, Any] = {}
    try:
        from value_stamping import resolve_app_value_semantics

        _resolved_value_semantics = await resolve_app_value_semantics(
            app_spec=app_spec, settings=settings, auth_header=auth_header,
            tenant_id=effective_tenant or publisher_tenant or "",
        )
    except Exception:  # noqa: BLE001 — resolution outage must not block publish
        logger.exception("[value] value_semantics resolution failed at publish")

    # Display identity (runtime-ui-modernization-plan.md Track E): default the
    # app theme's company name / logo / primary from the ontology's
    # `organization` block so IT declares identity ONCE and every app inherits
    # it. Builder-authored theme values always win; resolution failure never
    # blocks publish.
    _org_identity: Optional[Dict[str, Any]] = None
    try:
        _org_identity = await _resolve_org_identity(
            app_spec=app_spec, settings=settings, auth_header=auth_header,
            tenant_id=effective_tenant or publisher_tenant or "",
        )
    except Exception:  # noqa: BLE001
        logger.exception("[identity] organization resolution failed at publish")
    if _org_identity:
        _theme = app_spec.theme or Theme()
        _theme_updates: Dict[str, Any] = {}
        if not _theme.company_name:
            _theme_updates["company_name"] = (
                _org_identity.get("short_name") or _org_identity.get("name"))
        if not _theme.logo_url and _org_identity.get("logo_url"):
            _theme_updates["logo_url"] = _org_identity["logo_url"]
        if not _theme.primary and _org_identity.get("brand_color"):
            _theme_updates["primary"] = _org_identity["brand_color"]
        if _theme_updates:
            app_spec = app_spec.model_copy(
                update={"theme": _theme.model_copy(update=_theme_updates)})
            logger.info("[identity] app %r inherits org identity %s",
                        slug, sorted(_theme_updates))

    # An externally-consumed app (embed page or headless) gets a stable,
    # environment-tagged key the customer pastes into their own codebase.
    # PRESERVED across republishes — a changing key would mean re-pasting the
    # snippet on every release. None for ordinary apps, which are opened from
    # Citra and need no key. See embed_keys.py.
    _embed_key = ensure_embed_key(
        app_spec=app_spec, env=current_env(), existing_doc=existing,
    )

    app_doc = {
        "app_id": app_id,
        "slug": slug,
        "tenant_id": app_spec.tenant_id,
        "owner": app_spec.owner_id,
        "agent_id": agent_id,
        "status": AppStatus.PUBLISHED.value,
        "version": version,
        "deployed_at": now,
        "embed_key": _embed_key,
        "app_spec": app_spec.model_dump(mode="json"),
        # Whether this app is grounded on history — drives the UI's manual
        # "Refresh grounding" action. Grounding lives on the AgentSpec, so
        # stamp a doc-level flag the list can read without an agent lookup.
        "grounded": bool(
            payload.agent_spec is not None
            and getattr(payload.agent_spec, "grounding", None)
        ),
        # Whether the ontology enabled fraud screening — drives the card's
        # "Calibrate fraud" action + all fraud surfaces. Computed AFTER the
        # fraud auto-wiring (validate_data_bindings), so it reflects sources.json.
        "fraud_enabled": bool(
            payload.agent_spec is not None
            and _fraud_active_for_agent(payload.agent_spec)
        ),
        # The ROI spine: each dataset's MONEY definition resolved against the
        # catalogue (realization routing + currency + definition_version). The
        # Stage-4 poller reads this to stamp outcome.value; value-stats and
        # builder ROI pages aggregate the stamps. Empty when no dataset
        # declares value_semantics. See docs/money-saved-roi-plan.md.
        "value_semantics": _resolved_value_semantics,
        # The customer's display identity from the ontology (None when no
        # source declares an `organization` block). Presentation only.
        "organization": _org_identity,
    }
    # Snapshot the version we're about to overwrite so it remains rollback-able.
    # Only when a prior version exists (first publish has nothing to snapshot).
    # Runs in the SAME env as the write (routed cols).
    if existing:
        await _snapshot_prior_version(
            apps_col=apps,
            agents_col=agents,
            versions_col=get_spec_versions_col(),
            app_doc_before=existing,
            actor=publisher.get("email") or get_secure_user_id(request) or "",
            reason="publish",
            max_keep=settings.max_spec_versions,
        )
    await apps.replace_one({"slug": slug}, app_doc, upsert=True)

    # Facet families ARE the retrieval key — a clause fires iff
    # `scope_facets ⊆ case_facets`, so a republish that renames or drops a
    # family leaves every clause scoped to the old name unable to match any
    # case, forever, while still reading `active`. Shared with the hand-edit
    # path, which can drop a signature just as easily.
    await _reconcile_case_signature(
        slug=slug, app_doc=app_doc, previous_app_doc=existing,
        actor=(publisher.get("email") if isinstance(publisher, dict) else None)
        or get_secure_user_id(request) or "publish",
        origin="PUBLISH",
    )

    if payload.agent_spec is not None:
        agent_spec = payload.agent_spec.model_copy(update={"version": version})
        # Cross-tenant agent-id squat protection: if an agent doc with
        # this id already exists in a DIFFERENT tenant, reject. Pair this
        # with the tenant-scoped lookup filter on the read path.
        existing_agent = await agents.find_one({"agent_id": agent_id})
        if (
            existing_agent
            and existing_agent.get("tenant_id")
            and effective_tenant
            and existing_agent["tenant_id"] != effective_tenant
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"agent_id '{agent_id}' is owned by another tenant"
                ),
            )
        await agents.replace_one(
            {"agent_id": agent_id},
            {
                "agent_id": agent_id,
                "tenant_id": effective_tenant,
                "version": version,
                "agent_spec": agent_spec.model_dump(mode="json"),
                "updated_at": now,
            },
            upsert=True,
        )

        if payload.prompt_packs:
            await get_prompt_packs_col().insert_many(
                [
                    {**pp, "agent_id": agent_id, "version": version}
                    for pp in payload.prompt_packs
                ]
            )
        if payload.skills:
            await get_skills_col().insert_many(
                [
                    {**sk, "agent_id": agent_id, "version": version}
                    for sk in payload.skills
                ]
            )

    # Clause-memory nudge (docs/clause-memory-graph-plan.md §2): an app whose
    # officer reviews recommendations but that declares NO case_signature still
    # learns — its rules just come out unscoped and its reject UI offers no
    # reason categories, so live corrections land uncoded and can never form a
    # rule (§9.2). A WARNING, not a gate: the signature is opt-in by design,
    # but shipping without one should be a choice the builder saw, not a step
    # it silently skipped. This is the backstop for the builder agent missing
    # the AGENTS.md Phase-3.5 step — the first full sandbox test did exactly
    # that, and nothing surfaced it.
    #
    # The predicate used to be `any(action.approval_required)`, which was dead:
    # review became universal and the builder skills now explicitly FORBID
    # setting that flag (citra-agent-spec/SKILL.md), so it is False on every
    # doctrine-conformant spec and this warning never fired — including on the
    # rebuild that dropped dealer-limit-review's signature. Ask the question the
    # doctrine actually asks instead: does this app make a judgement someone
    # reviews? An app with actions, or with a declared rubric, does. A read-only
    # dashboard has neither, and is the one case that should skip silently.
    _has_review_surface = (
        bool(getattr(payload.agent_spec, "actions", None) or [])
        or getattr(payload.app_spec, "factor_set", None) is not None
    )
    if _has_review_surface and getattr(payload.app_spec, "case_signature", None) is None:
        publish_warnings.append({
            "code": "case_signature_missing",
            "message": (
                "This app's recommendations are reviewed by an officer, but no "
                "`case_signature` is declared — so the judgements it learns "
                "cannot be scoped to case types, and the reject UI offers no "
                "reason categories, which means officer corrections stay "
                "uncoded and never form judgements. Author one per `citra-app-spec` → "
                "`references/case-signature.md` (facets from the bound "
                "dataset's own columns + the reject-reason codes) and "
                "republish. Skip only if this app should not learn."
            ),
        })

    # Few-shot-from-history grounding is populated MANUALLY: publish does NOT
    # auto-build the sample collection. The runtime no-ops safely on an empty
    # collection (the agent runs on its base prompt), and the BA explicitly
    # populates/refreshes it from the UI ("Refresh grounding" →
    # POST /apps/{slug}/grounding/refresh). Surface that as a publish note so
    # the BA knows the step is required before grounding takes effect.
    if payload.agent_spec is not None and getattr(payload.agent_spec, "grounding", None):
        publish_warnings.append({
            "code": "grounding_refresh_required",
            "message": (
                "This app learns from your historical decisions. **Run "
                "'Refresh grounding' BEFORE you test it** — that loads your "
                "past decisions so the app decides the way your team does. "
                "Until you do, it runs on its base prompt and your test won't "
                "reflect the grounded behaviour. (Smart Apps list → this app → "
                "Refresh grounding; you'll see live progress and a done event.)"
            ),
        })

    # Outcome-loop sanity: warn (don't block) when outcome_poll.table is not a
    # table this app actually WRITES to. The read-back loop labels a decision by
    # re-reading the row it wrote — a poll table the app never updates is almost
    # always a cloned/template leftover (e.g. cloning theft-triage carries its
    # `theft_cases`/`recovery_status` poll onto an inspection app). Soft, because
    # the outcome can LEGITIMATELY settle in a different table than the one
    # written (route here, observe resolved/reopened elsewhere) — so the BA
    # confirms rather than being blocked.
    _ag = payload.agent_spec
    _op = getattr(_ag, "outcome_poll", None) if _ag is not None else None
    _op_table = (getattr(_op, "table", None) or "").strip() if _op else ""
    if _op_table:
        _write_tables = {
            (getattr(t, "dataset_id", "") or "").split(".")[-1].strip().lower()
            for t in (getattr(_ag, "tools_v2", None) or [])
            if getattr(t, "kind", None) == "mcp_action"
            and (getattr(t, "dataset_id", "") or "")
        }
        if _write_tables and _op_table.lower() not in _write_tables:
            publish_warnings.append({
                "code": "outcome_poll_table_not_written",
                "message": (
                    f"Outcome tracking is set to read back '{_op_table}', but this "
                    f"app only writes to {sorted(_write_tables)}. Confirm '{_op_table}' "
                    "is really where this decision's outcome settles — if it's a "
                    "leftover from a cloned/template app, point outcome tracking at "
                    "the table the app decides on, or remove it."
                ),
            })

    _published_url = _runtime_url(slug, settings)
    # Deterministic URL handoff. Stamp the published URL onto the build session
    # and flag a pending publish event; the build-chat relay then emits a
    # STRUCTURED `publish` event at the turn's terminal `done`, and the UI
    # renders the "Open app" deploy strip from it. This makes URL delivery
    # independent of the agent narrating "live at <url>" in prose (which failed
    # silently when phrasing drifted) — and persists the URL for later lookup.
    _stamp_sid = (
        publisher.get("session_id")
        or getattr(payload, "session_id", None)
        or ""
    ).strip()
    if _stamp_sid:
        try:
            await get_build_sessions_col().update_one(
                {"session_id": _stamp_sid},
                {"$set": {
                    "published_url": _published_url,
                    "published_slug": slug,
                    "published_version": version,
                    "published_event_pending": True,
                }},
            )
        except Exception as _stamp_err:
            # The publish itself SUCCEEDED (app persisted). The stamp only drives
            # the structured event, so don't fail the publish — but surface it
            # loudly (log + a warning the caller/agent sees), never swallow.
            logger.error(
                "[publish] could not stamp build session %s for the publish event: %s",
                _stamp_sid, _stamp_err,
            )
            publish_warnings.append({
                "code": "publish_event_unrecorded",
                "message": (
                    "Published, but the deterministic URL handoff could not be "
                    "recorded — the Open-app strip may not appear automatically."
                ),
            })

    return PublishResponse(
        app_id=app_id,
        slug=slug,
        url=_published_url,
        version=version,
        warnings=publish_warnings,
        published_summary=_build_published_summary(app_spec, version),
    )


class PromoteToProdRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # Optional audience override applied to the prod copy — one of exactly:
    # owner | org | team:<sa_id> | dept:<dept_id>. When omitted, the test
    # spec's existing audience carries over.
    audience: Optional[str] = None
    # ── Grounding gate (few-shot from history) ──────────────────────────────
    # A grounded app runs degraded (cold-start note, no precedent) until its
    # few-shot memory is refreshed. Promote requires the memory to be fresh:
    #   * refresh_grounding=True  → run a refresh as part of promote (streamed).
    #   * promote_ungrounded=True → explicit override: ship without grounding.
    # With neither set, promoting a grounded app whose memory is never/stale
    # returns 409 grounding_refresh_required so the UI can prompt.
    refresh_grounding: bool = False
    promote_ungrounded: bool = False


class PromoteToProdResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    slug: str
    prod_url: str
    version: int
    agent_promoted: bool
    # Grounding-gate outcome: run_id when a refresh was kicked off as part of
    # promote (poll /grounding/refresh/status), and a status the UI can render:
    # "refreshing" | "fresh" | "skipped" (override) | None (not a grounded app).
    grounding_refresh_run_id: Optional[str] = None
    grounding_status: Optional[str] = None


# Strong refs to in-flight grounding-refresh background jobs (asyncio holds
# only a weak ref to a bare create_task result).
_GROUNDING_JOBS: set = set()

# Human-readable progress weights so the UI can render a bar without guessing.
_GROUNDING_PHASE_PCT = {
    "queued": 2, "pulling": 15, "packaging": 35, "selecting": 50,
    "guarding": 60, "embedding": 80, "swapping": 92, "done": 100, "error": 100,
}


async def _fetch_loop_samples(*, agent_id: str, tenant_id: str, contract) -> List[Dict[str, Any]]:
    """Loop write-back source (Stage 5): this agent's own validated-GOOD
    DecisionRecords, converted to grounding samples. Reads the PROD
    decision_records (grounding always learns from real observed outcomes, never
    the ephemeral test plane). Best-effort — a fetch failure logs and returns []
    so the historical refresh still runs; write-back never blocks grounding."""
    from grounding_refresh import loop_decision_to_sample
    if _decision_records_col is None:
        return []
    try:
        cursor = _decision_records_col.find({
            "agent_id": agent_id,
            "tenant_id": tenant_id,
            "$or": [
                # POSITIVES: human-mediated validated-good only. auto_process (the
                # model repeating its own confident decisions → echo chamber) and
                # human_direct (action payload, no rich context) are excluded —
                # they ground the slow way via the periodic resolved-table pull.
                {"outcome.label": "good", "mode": "human_approved"},
                # NEGATIVES (the "outcome flow"): ANY mode that was rejected or
                # turned out bad. Learning to AVOID is always safe and is exactly
                # the anti-pattern signal a positives-only history table lacks.
                {"outcome.label": "bad"},
            ],
        }).sort("created_at", -1).limit(2000)
        out: List[Dict[str, Any]] = []
        async for rec in cursor:
            s = loop_decision_to_sample(rec, contract)
            if s:
                out.append(s)
        if out:
            logger.info(
                "[grounding] write-back: %d good loop decisions for agent=%s",
                len(out), agent_id,
            )
        return out
    except Exception:  # noqa: BLE001 — write-back is additive; never block refresh
        logger.exception("[grounding] write-back fetch failed (agent=%s)", agent_id)
        return []


async def _run_grounding_job(
    *, settings: Settings, run_doc: dict, contract, user_jwt: str, generation: int,
) -> None:
    """Background worker: run the refresh, streaming phase progress into the
    Redis-backed run record (the UI polls it), then mark complete/failed. The
    curated samples live ONLY in Milvus (vectors the runtime reads) — no Mongo
    copy. Never raises (it's a detached task)."""
    from grounding_refresh import refresh_grounding, GroundingRefreshError
    from grounding_runs import get_grounding_run_store

    store = get_grounding_run_store()
    slug = run_doc["slug"]
    agent_id = run_doc["agent_id"]

    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    async def _on_phase(phase: str, info: dict) -> None:
        run_doc["phase"] = phase
        run_doc["progress"] = _GROUNDING_PHASE_PCT.get(phase, 0)
        run_doc["counts"] = info
        run_doc["updated_at"] = _now()
        store.put(slug, run_doc)

    try:
        # Write-back: fold this agent's validated-good loop decisions into the
        # corpus alongside the seed history (Stage 5 — closes the loop).
        extra = await _fetch_loop_samples(
            agent_id=agent_id, tenant_id=run_doc["tenant_id"], contract=contract,
        )
        res = await refresh_grounding(
            settings=settings, tenant_id=run_doc["tenant_id"], agent_id=agent_id,
            app_slug=slug, contract=contract, user_jwt=user_jwt,
            generation=generation, on_phase=_on_phase, extra_samples=extra,
        )
        run_doc.update({
            "status": "complete", "phase": "done", "progress": 100,
            "collection": res.collection,
            "result": {
                "total_samples": res.total_samples,
                "canonical_samples": res.canonical_samples,
                "neighbor_samples": res.neighbor_samples,
                "decision_classes": res.decision_classes,
                "decision_fill_rate": round(res.guard.decision_fill_rate, 3),
                "live_count_before": res.guard.live_count,
            },
            "completed_at": _now(), "updated_at": _now(),
        })
        store.put(slug, run_doc, done=True)
        # Durable freshness for the card / status endpoint (survives the run-doc
        # TTL). The refresh (Milvus swap) already succeeded, so a persist failure
        # must NOT mask it as a failed job — log LOUD and continue (the card falls
        # back to "never refreshed", which is visibly wrong and prompts a re-run).
        try:
            store.put_last(slug, {
                "last_refreshed_at": run_doc["completed_at"],
                "sample_count": res.total_samples,
                "canonical_samples": res.canonical_samples,
                "neighbor_samples": res.neighbor_samples,
                "last_run_id": run_doc["run_id"],
            })
        except Exception as exc:  # noqa: BLE001 — refresh succeeded; log, don't fail it
            logger.error(
                "[grounding] freshness persist FAILED slug=%s run=%s: %s",
                slug, run_doc["run_id"], exc,
            )
        logger.info("[grounding] job %s complete slug=%s total=%d", run_doc["run_id"], slug, res.total_samples)
    except GroundingRefreshError as e:
        run_doc.update({"status": "failed", "phase": "error", "progress": 100,
                        "error": {"code": e.code, "message": str(e)},
                        "completed_at": _now(), "updated_at": _now()})
        store.put(slug, run_doc, done=True)
        logger.warning("[grounding] job %s FAILED slug=%s code=%s: %s", run_doc["run_id"], slug, e.code, e)
    except Exception as e:
        run_doc.update({"status": "failed", "phase": "error", "progress": 100,
                        "error": {"code": "internal", "message": str(e)},
                        "completed_at": _now(), "updated_at": _now()})
        store.put(slug, run_doc, done=True)
        logger.exception("[grounding] job %s crashed slug=%s: %s", run_doc["run_id"], slug, e)


def _enqueue_grounding_refresh(
    *, settings: Settings, slug: str, agent_id: str, tenant_id: str,
    contract, user_jwt: str, requested_by: Optional[str] = None,
) -> Optional[str]:
    """Build a grounding run doc + spawn the background refresh job. Returns the
    run_id, or None if a refresh is already running for this app (debounce —
    one refresh at a time per app). Shared by the manual endpoint and the
    auto-refresh-on-outcome path (Stage 5, continuous learning)."""
    from grounding_runs import get_grounding_run_store
    store = get_grounding_run_store()
    inflight = store.get(slug)
    if inflight and inflight.get("status") == "running":
        return None
    run_id = f"gr_{uuid.uuid4().hex[:16]}"
    now = datetime.now(timezone.utc).isoformat()
    run_doc = {
        "run_id": run_id, "slug": slug, "agent_id": agent_id, "tenant_id": tenant_id,
        "status": "running", "phase": "queued", "progress": 0,
        "requested_by": requested_by,
        "started_at": now, "updated_at": now, "completed_at": None,
        "error": None, "counts": {}, "result": None,
    }
    store.put(slug, run_doc)
    task = asyncio.create_task(_run_grounding_job(
        settings=settings, run_doc=run_doc, contract=contract,
        user_jwt=user_jwt, generation=int(time.time()),
    ))
    _GROUNDING_JOBS.add(task)
    task.add_done_callback(_GROUNDING_JOBS.discard)
    return run_id


def _grounding_freshness_state(slug: str) -> str:
    """Return ``'never' | 'stale' | 'fresh'`` for a grounded app's few-shot
    memory, from the durable last-refresh record (``grounding:last:<slug>``).

    Grounding is env-unified (one shared Milvus collection keyed by agent_id, a
    single non-env-routed MILVUS_URI, and slug-keyed freshness), so this single
    record reflects a refresh done in EITHER test or prod for this slug.

    Staleness threshold reuses the periodic full-rebuild cadence
    (``GROUNDING_FULL_REFRESH_DAYS``, default 7). A value <= 0 disables the
    stale check, so only a genuine 'never refreshed' gates the promote."""
    from grounding_runs import get_grounding_run_store
    last = get_grounding_run_store().get_last(slug)
    if not last or not last.get("last_refreshed_at"):
        return "never"
    days = int(os.getenv("GROUNDING_FULL_REFRESH_DAYS", "7"))
    if days <= 0:
        return "fresh"
    try:
        ts = datetime.fromisoformat(
            str(last["last_refreshed_at"]).replace("Z", "+00:00")
        )
    except (ValueError, TypeError):
        # An unparseable timestamp is not proof of freshness — prompt a refresh.
        return "stale"
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    age_s = (datetime.now(timezone.utc) - ts).total_seconds()
    return "stale" if age_s > days * 86400 else "fresh"


async def _rebuild_all_grounded(settings: Settings) -> int:
    """Enqueue a FULL grounding rebuild for every grounded, non-archived app — the
    periodic hygiene pass (re-curate canonicals, pull new seed history + fold in
    direct-human decisions). Delta upsert carries the continuous per-decision
    path; this is the weekly backstop. Prod store; per-app failures are isolated.
    Returns the number of rebuilds enqueued."""
    set_current_env("prod")
    if _apps_col is None or _agents_col is None:
        return 0
    from trigger_runner import _mint_system_auth
    from models import GroundingContract
    try:
        apps = await _apps_col.find(
            {"status": {"$ne": AppStatus.ARCHIVED.value}}
        ).to_list(length=1000)
    except Exception:  # noqa: BLE001
        logger.exception("[grounding] periodic rebuild: app scan failed")
        return 0
    n = 0
    for app_doc in apps:
        try:
            agent_id = app_doc.get("agent_id")
            tenant_id = app_doc.get("tenant_id") or ""
            slug = app_doc.get("slug")
            if not (agent_id and slug):
                continue
            adoc = await _agents_col.find_one(
                {"agent_id": agent_id, "tenant_id": tenant_id},
                {"agent_spec.grounding": 1, "agent_spec.outcome_poll": 1},
            )
            aspec = (adoc or {}).get("agent_spec") or {}
            grounding = aspec.get("grounding") or None
            if not grounding:
                continue  # only grounded agents
            # Auto-run gate: the periodic full rebuild runs ONLY for apps where the
            # user enabled auto-learning. Default = manual (skip; manual endpoint
            # still works).
            if not ((aspec.get("outcome_poll") or {}).get("auto_refresh")):
                continue
            contract = GroundingContract.model_validate(grounding)
            try:
                app_spec = _load_app_spec(app_doc)
                header = _mint_system_auth(settings, app_spec, app_doc)
                user_jwt = header.removeprefix("Bearer ").strip() if header else ""
            except Exception:  # noqa: BLE001 — fall back to service-key auth
                user_jwt = ""
            rid = _enqueue_grounding_refresh(
                settings=settings, slug=slug, agent_id=agent_id, tenant_id=tenant_id,
                contract=contract, user_jwt=user_jwt, requested_by="periodic_rebuild",
            )
            if rid:
                n += 1
        except Exception:  # noqa: BLE001 — per-app; never crash the sweep
            logger.exception(
                "[grounding] periodic rebuild failed for %s", app_doc.get("slug")
            )
    return n


async def _resolve_grounded_app(slug: str, request: Request):
    """Shared guard for the grounding endpoints: bind env, load app + agent,
    check edit rights, require a grounding contract. Returns (app_doc,
    agent_id, agent_spec)."""
    requester_id = get_secure_user_id(request)
    if not requester_id:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="authentication required")
    await _bind_app_env(slug)
    app_doc = await get_apps_col().find_one({"slug": slug})
    if not app_doc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"app '{slug}' not found")
    if not _can_edit_app(
        app_doc, requester_id, get_tenant_id(request),
        user_org_id=get_org_id(request),
        user_dept_ids=get_user_dept_ids(request),
        user_roles=get_user_roles(request),
        sa_admin_of=get_sa_admin_of(request),
    ):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="not authorised for this app")
    agent_id = (app_doc.get("app_spec") or {}).get("agent_id")
    agent_doc = await get_agents_col().find_one({"agent_id": agent_id}) if agent_id else None
    if not agent_doc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="agent for this app not found")
    try:
        agent_spec = AgentSpec.model_validate(agent_doc.get("agent_spec") or {})
    except Exception as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"agent_spec invalid: {e}")
    if not agent_spec.grounding:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="this app's agent has no grounding contract — nothing to refresh",
        )
    return app_doc, agent_id, agent_spec


@app.post("/apps/{slug}/grounding/refresh")
async def start_app_grounding_refresh(
    slug: str,
    request: Request,
    settings: Settings = Depends(get_settings),
) -> dict:
    """Start an ASYNC rebuild of the agent's few-shot-from-history samples and
    return a ``run_id`` immediately. The BA polls
    ``GET /apps/{slug}/grounding/refresh/status`` for phase/progress and the
    completion event. Grounding pulls the REAL (prod) domain history and writes
    a dedicated Milvus collection (vectors) + Mongo collection (audit copy);
    the guard leaves live samples intact on a degraded pull. Requires app-edit
    rights; 409 if the app isn't grounded; 409 if a refresh is already running.
    """
    app_doc, agent_id, agent_spec = await _resolve_grounded_app(slug, request)
    from grounding_runs import get_grounding_run_store
    store = get_grounding_run_store()

    # One refresh at a time per app (the swap replaces the agent's rows).
    inflight = store.get(slug)
    if inflight and inflight.get("status") == "running":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={"code": "refresh_in_progress", "run_id": inflight.get("run_id"),
                    "message": "a grounding refresh is already running for this app"},
        )

    auth_header = request.headers.get("authorization") or request.headers.get("Authorization")
    tenant_id = app_doc.get("tenant_id") or get_tenant_id(request) or ""
    run_id = _enqueue_grounding_refresh(
        settings=settings, slug=slug, agent_id=agent_id, tenant_id=tenant_id,
        contract=agent_spec.grounding,
        user_jwt=(auth_header or "").removeprefix("Bearer ").strip(),
        requested_by=get_secure_user_id(request),
    )
    if run_id is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={"code": "refresh_in_progress",
                    "message": "a grounding refresh is already running for this app"},
        )
    return {"run_id": run_id, "status": "running", "slug": slug}


@app.post("/apps/{slug}/items/{item_id}/feedback")
async def submit_item_feedback(
    slug: str,
    item_id: str,
    request: Request,
    settings: Settings = Depends(get_settings),
) -> dict:
    """Per-item human feedback on an ``ItemFinding`` (image / document analysis).

    Every item (record / image / document) is reviewed with exactly one of three
    dispositions — no auto-accept, no other option:
    - ``accept`` → recorded (no rubric change).
    - ``reject`` → REQUIRES a reason; the reason is appended to the
      ``(app, modality, task_type)`` rubric as a learned criterion, so the NEXT
      similar item is judged with it (prompt-criteria learning; see
      docs/multimodal-decision-apps-plan.md). Learning is per **task_type**, not
      per file.
    - ``cancel`` → recorded (no rubric change) — the item is dispositioned
      without accepting or correcting it.
    """
    # Route the rubric write to the SAME environment the run reads from. Without
    # this, a TEST app's reject writes to the prod rubric collection while the
    # tool reads the test one during /run — silently breaking the learning loop.
    await _bind_app_env(slug)
    body = await request.json()
    modality = (body.get("modality") or "image").strip()
    task_type = (body.get("task_type") or "").strip()
    decision = (body.get("decision") or "").strip().lower()
    reason = (body.get("reason") or "").strip()
    # A few-word pointer to WHAT this item was (the finding's subject) — anchors
    # the reject reason to a subject-type so the rubric learns "for nameplate
    # photos, reject ..." rather than a context-free criterion. Trimmed defensively.
    subject = (body.get("subject") or "").strip()[:120] or None
    # Structured reason taxonomy (clause-memory plan §3). WITHOUT this the item
    # correction lands uncoded, and consolidation refuses to author a clause
    # from an uncoded cluster (§9.2) — so image/document/api feedback would
    # accumulate as evidence that can never become a rule. Optional: an app with
    # no case_signature records None, which reads honestly as "not captured".
    reason_code = (body.get("reason_code") or "").strip()[:40] or None
    # Fraud gate: case-level fraud feedback only exists when the ontology enabled
    # screening for this app — no fraud surface (and so no L2 learning) otherwise.
    # The APP tenant is captured here because the verdict stamp below must use
    # the SAME tenant key the screening rows were written under
    # (_screening_tenant is app-tenant-FIRST) — the caller's JWT tenant claim
    # can legitimately differ (dept-scoped claims, cross-tenant admins) and a
    # JWT-keyed stamp would silently miss every row.
    _case_app_tenant: Optional[str] = None
    if modality == "case":
        _fapp = await get_apps_col().find_one(
            {"slug": slug}, {"fraud_enabled": 1, "tenant_id": 1})
        if not (_fapp and _fapp.get("fraud_enabled")):
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail={"code": "fraud_not_enabled",
                        "message": "fraud screening is not enabled for this app"},
            )
        _case_app_tenant = (_fapp.get("tenant_id") or "").strip() or None
    # "case" = fraud-flag feedback (L2 fraud case rubric): the officer confirms
    # or dismisses a fraud screening with a reason; a reject-with-reason folds
    # into the (modality="case", task_type="fraud-screening") rubric exactly
    # like artifact reject-reasons fold into image/document rubrics.
    # "api" = per-check feedback (check_evaluate): the officer accepts/rejects one
    # API/SoR check verdict (CIBIL, Aadhaar) with a reason; a reject folds into the
    # (modality="api", task_type="<check>") rubric exactly like image/doc rubrics.
    if modality not in ("image", "document", "case", "api"):
        raise HTTPException(422, detail={"code": "bad_modality", "message": "modality must be image|document|case|api"})
    if not task_type:
        raise HTTPException(422, detail={"code": "missing_task_type", "message": "task_type required"})
    if decision not in ("accept", "reject", "cancel"):
        raise HTTPException(422, detail={"code": "bad_decision", "message": "decision must be accept|reject|cancel"})

    tenant_id = get_tenant_id(request) or ""
    actor = get_secure_user_id(request)
    rubric_updated = False
    if decision == "reject":
        if not reason:
            raise HTTPException(422, detail={"code": "reason_required", "message": "a reason is required on reject"})
        # Mirror of the UI's REASON_MAX (ItemFindingReview): a criterion should
        # be a sentence or two — an uncapped paste would bloat the rubric
        # summarizer input and the ledger precedent prompts. Fail loud, don't
        # silently truncate someone's carefully written reason.
        if len(reason) > 500:
            raise HTTPException(422, detail={
                "code": "reason_too_long",
                "message": f"reason is {len(reason)} chars — max 500; please shorten to the key criterion",
            })
        from analysis_rubrics import append_correction

        # Validate the code against the app's declared taxonomy — an invented
        # code would silently partition consolidation into a cluster of one that
        # can never reach the promotion gate. Unknown ⇒ reject loudly rather
        # than absorb it, the same discipline __unknown enforces for facets.
        if reason_code:
            _fapp_sig = await get_apps_col().find_one(
                {"slug": slug}, {"_id": 0, "app_spec": 1})
            from case_signature import reason_codes as _declared_codes
            from case_signature import signature_of as _sig_of

            _codes = _declared_codes(_sig_of(_fapp_sig))
            if _codes and reason_code not in _codes:
                raise HTTPException(422, detail={
                    "code": "unknown_reason_code",
                    "message": (f"reason_code {reason_code!r} is not in this "
                                f"app's taxonomy: {', '.join(_codes)}"),
                })

        await append_correction(
            tenant_id=tenant_id, app_slug=slug, modality=modality,
            task_type=task_type, reason=reason, actor=actor, item_id=item_id,
            subject=subject, reason_code=reason_code,
        )
        rubric_updated = True
    # Item ledger Write-2: EVERY disposition is knowledge — accept ("what
    # worked", the positive class a future trained model needs), reject (with
    # the reason), and cancel. The rubric above stays the aggregated SOP layer
    # (rejects only); this stamps the per-item ORIGINAL so precedent retrieval
    # and the tenant's multimodal training set keep both classes. "case"
    # (fraud-flag feedback) has no per-artifact row — rubric-only by design.
    ledger_updated = False
    # image / document / api all have a per-item Write-1 ledger row → stamp the
    # officer's disposition on it (both classes feed precedent retrieval +
    # training). Only "case" (fraud-flag) is rubric-only with no per-item row.
    if modality in ("image", "document", "api"):
        from item_records import record_item_disposition

        ledger_updated = await record_item_disposition(
            tenant_id=tenant_id, slug=slug, item_id=item_id,
            modality=modality, task_type=task_type, decision=decision,
            reason=reason or None, actor=actor, subject=subject,
        )
    # Case-modality verdict stamp (Screening Health panel — see
    # docs/fraud-screening-admin-panel-plan.md §2 gap 1). The rubric write above
    # teaches the JUDGE; this label is what makes the false-alarm RATE
    # computable: accept = the flag was a real issue, reject = false alarm.
    # The case finding's item_id is "<record_id>-fraud" (fraud_synthesis stamp).
    screening_stamped = False
    if modality == "case" and decision in ("accept", "reject"):
        from fraud_synthesis import record_id_from_item_id, stamp_officer_verdict

        _rec_id = record_id_from_item_id(item_id)
        try:
            screening_stamped = await stamp_officer_verdict(
                # APP tenant first — the key screenings are WRITTEN under
                # (see the fraud-gate block above); JWT tenant only as the
                # last-resort fallback for a tenant-less app doc.
                tenant_id=_case_app_tenant or tenant_id,
                app_slug=slug, record_id=_rec_id,
                verdict="confirmed" if decision == "accept" else "dismissed",
                reason=reason or None, actor=actor,
            )
        except Exception as exc:  # noqa: BLE001 — visible, never fails the feedback
            logger.warning("[FRAUD] verdict stamp failed for %s/%s: %s",
                           slug, _rec_id, exc)
    return {
        "ok": True, "decision": decision, "rubric_updated": rubric_updated,
        "item_recorded": ledger_updated, "screening_stamped": screening_stamped,
        "task_type": task_type, "modality": modality, "item_id": item_id,
    }


@app.post("/apps/{slug}/factors/{factor_id}/override")
async def override_factor_score(
    slug: str,
    factor_id: str,
    request: Request,
    settings: Settings = Depends(get_settings),
) -> dict:
    """An officer corrects one factor's score, and says why.

    This is the surface the factor scorecard exists for. An override here is a
    correction that arrives ALREADY SCOPED to a factor — a far better input to
    learned memory than free-text override notes, because factor-scoped
    corrections cluster on what was actually disputed instead of on how someone
    phrased it (docs/factor-scorecard-plan.md).

    Body: ``{correlation_id, score?, band?, reason, reason_code?}``.
    ``reason`` is REQUIRED — an unexplained number is exactly the artefact this
    replaces. At least one of score/band must be present.

    Three things happen, in this order, and the correction is written LAST so a
    failed recompute never teaches the memory something that was rejected:

      1. the stored card is re-scored **in code** from the declared weights, so
         the officer's grade and the ledger's grade cannot diverge;
      2. the row keeps the model's own score in ``original_score`` and the card
         keeps ``grade_before_override`` — an override can never be invisible;
      3. the correction is folded into the ``(app, "api", factor_id)`` bucket,
         the same path a per-check reject already uses.

    NOTE on authority: an override can move the grade, and where approval limits
    key off the grade that means an officer could widen their own signing limit.
    This endpoint deliberately does NOT arbitrate that — an authority matrix is
    the customer's policy, not the engine's. It records everything such a rule
    needs (who, when, why, the model's original, both grades) and logs the
    change.
    """
    await _bind_app_env(slug)
    body = await request.json()
    correlation_id = (body.get("correlation_id") or "").strip()
    reason = (body.get("reason") or "").strip()
    reason_code = (body.get("reason_code") or "").strip()[:40] or None
    raw_score = body.get("score")
    band = (body.get("band") or "").strip() or None

    if not correlation_id:
        raise HTTPException(422, detail={"code": "missing_correlation_id",
                                         "message": "correlation_id is required"})
    if not reason:
        raise HTTPException(422, detail={
            "code": "reason_required",
            "message": "a reason is required: an unexplained score is the "
                       "artefact this scorecard exists to replace",
        })
    if len(reason) > 500:
        raise HTTPException(422, detail={
            "code": "reason_too_long",
            "message": f"reason is {len(reason)} chars — max 500",
        })
    score = None
    if raw_score is not None:
        try:
            score = float(raw_score)
        except (TypeError, ValueError):
            raise HTTPException(422, detail={"code": "bad_score",
                                             "message": f"score {raw_score!r} is not a number"})
    if score is None and band is None:
        raise HTTPException(422, detail={
            "code": "nothing_to_override",
            "message": "supply a score or a band",
        })

    app_doc = await get_apps_col().find_one({"slug": slug})
    if not app_doc:
        raise HTTPException(404, detail={"code": "app_not_found", "message": slug})
    spec_dict = (app_doc.get("app_spec") or {}).get("factor_set")
    if not spec_dict:
        raise HTTPException(409, detail={
            "code": "no_factor_set",
            "message": "this app declares no factor_set, so it has no factor to override",
        })
    from models import FactorSet
    factor_set = FactorSet.model_validate(spec_dict)

    # Composite key "{workflow_execution_id}:{case_natural_key}" — the SAME
    # resolution the approve path uses, and it must stay the same. A trigger
    # stages ONE ROW PER CASE under a single execution id, so keying on the
    # execution id alone returns an arbitrary sibling: an officer correcting one
    # dealer would silently rewrite another's scorecard. A natural key may
    # contain its own colons, so partition on the FIRST only.
    exec_id, _, case_key = correlation_id.partition(":")
    if not exec_id or not case_key:
        raise HTTPException(400, detail={
            "code": "bad_correlation_id",
            "message": ("correlation_id must be "
                        "'{workflow_execution_id}:{case_natural_key}'"),
        })
    row = await get_workflow_staging_col().find_one(
        {"workflow_execution_id": exec_id, "case_natural_key": case_key})
    if not row or row.get("slug") != slug:
        raise HTTPException(404, detail={"code": "case_not_found",
                                         "message": f"no staged case for {correlation_id}"})
    # Tenant defence-in-depth — identical rule to approve. Without it an
    # authenticated officer in one org could re-score another org's case.
    _actor_tenant = get_tenant_id(request)
    if row.get("tenant_id") and _actor_tenant and row["tenant_id"] != _actor_tenant:
        raise HTTPException(403, detail={
            "code": "cross_tenant_override",
            "message": "cross-tenant override forbidden",
        })
    card = row.get("scorecard")
    if not card:
        raise HTTPException(409, detail={
            "code": "no_scorecard",
            "message": "this case carries no scorecard to override",
        })
    # An override after the decision is committed would rewrite the basis of a
    # decision that has already been acted on. Refuse: the record stands, and a
    # changed judgement is a new decision.
    if (row.get("status") or "") not in ("pending_review", "pending_je_review",
                                         "pending_ae_review", "pending_ee_review"):
        raise HTTPException(409, detail={
            "code": "case_not_pending",
            "message": (f"this case is {row.get('status')!r} — a factor cannot be "
                        "re-scored after the decision was made. The record stands."),
        })

    actor = get_secure_user_id(request)
    from factor_scoring import FactorScoringError, apply_factor_override
    try:
        updated = apply_factor_override(
            card, factor_set, factor_id=factor_id, score=score, band=band,
            reason=reason, actor=actor,
        )
    except FactorScoringError as exc:
        raise HTTPException(422, detail={"code": "override_rejected",
                                         "message": str(exc)})

    # Conditional on the revision we READ. Two officers correcting different
    # factors on one case would otherwise both succeed and the second would
    # replace the first — a correction that appeared to save and vanished.
    _seen_rev = (card or {}).get("revision") or 0
    _res = await get_workflow_staging_col().update_one(
        {"_id": row["_id"],
         "$or": [{"scorecard.revision": _seen_rev},
                 # Cards written before `revision` existed carry no field.
                 *([{"scorecard.revision": {"$exists": False}}] if _seen_rev == 0 else [])]},
        {"$set": {"scorecard": updated}},
    )
    if _res.matched_count == 0:
        raise HTTPException(409, detail={
            "code": "scorecard_changed",
            "message": ("someone else corrected this scorecard while you were "
                        "editing. Reload the case and apply your change again — "
                        "saving now would discard their correction."),
        })

    # The correction, scoped to the factor. Written after the recompute so a
    # rejected override never teaches anything. modality="api" because a factor
    # IS a check_evaluate finding — same bucket, same consolidation.
    from analysis_rubrics import append_correction, rubric_tenant_for_app

    _rubric_tenant = rubric_tenant_for_app(app_doc)
    learned = False
    if _rubric_tenant:
        await append_correction(
            tenant_id=_rubric_tenant,
            app_slug=slug, modality="api", task_type=factor_id,
            reason=reason, actor=actor, item_id=f"{correlation_id}:{factor_id}",
            subject=factor_id, reason_code=reason_code,
            case_facets=row.get("case_facets"),
            signature_version=row.get("signature_version"),
        )
        learned = True
    else:
        # SKIP LOUDLY, never coerce to "". The read side derives the bucket from
        # the same function, so a correction filed under an empty key can never
        # be retrieved — the loop would look wired and silently never close.
        # The override itself is already durable on the staging row above; only
        # the LEARNING is skipped.
        logger.error(
            "[SCORECARD] app %s carries no org identity — the factor "
            "correction on %s cannot be filed to a learning bucket and was "
            "SKIPPED. The override is saved; the lesson is not.", slug, factor_id,
        )
    logger.info(
        "[SCORECARD] %s overrode factor %r on %s/%s — grade %s -> %s",
        actor, factor_id, slug, correlation_id,
        updated.get("grade_before_override"), updated.get("grade"),
    )
    return {"ok": True, "factor_id": factor_id, "scorecard": updated,
            "correction_recorded": learned}


@app.post("/apps/{slug}/fraud-calibration")
async def run_fraud_calibration(
    slug: str,
    request: Request,
    settings: Settings = Depends(get_settings),
) -> dict:
    """L3 calibration — IT/owner-TRIGGERED, never a background job (fraud plan
    §9). Joins the app's fraud screenings against DecisionRecords and reports
    each signal type's officer-rejection hit-rate: LOW-rate signals over many
    cases are pruning candidates; HIGH-rate ones justify more weight. The
    report is returned (and logged) — applying weight/rubric changes stays a
    human decision."""
    user_id = get_secure_user_id(request)
    user_tenant = get_tenant_id(request)
    await _bind_app_env(slug)
    app_doc = await get_apps_col().find_one({"slug": slug})
    if not app_doc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="app not found")
    if not _can_edit_app(
        app_doc, user_id, user_tenant,
        user_org_id=get_org_id(request),
        user_dept_ids=get_user_dept_ids(request),
        user_roles=get_user_roles(request),
        sa_admin_of=get_sa_admin_of(request),
    ):
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            detail="not allowed to run calibration for this app")
    # Fraud gate: no fraud surface exists unless the ontology enabled screening.
    if not app_doc.get("fraud_enabled"):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={"code": "fraud_not_enabled",
                    "message": "fraud screening is not enabled for this app "
                               "(no dataset opted into fraud_screening in the ontology)"},
        )
    from fraud_synthesis import run_calibration

    report = await run_calibration(
        tenant_id=app_doc.get("tenant_id") or user_tenant, app_slug=slug,
    )
    logger.info("[FRAUD-L3] calibration for %s: %d screenings, %d matched",
                slug, report["screenings_considered"], report["matched_to_decisions"])
    return report


def _screening_period_start(period: str) -> Optional[datetime]:
    """'week' | 'month' | 'all' → window start (None = all time). Anything else
    is a 422 — a typo'd period must not silently become all-time."""
    p = (period or "month").strip().lower()
    if p == "week":
        return datetime.now(timezone.utc) - timedelta(days=7)
    if p == "month":
        return datetime.now(timezone.utc) - timedelta(days=30)
    if p == "all":
        return None
    raise HTTPException(422, detail={"code": "bad_period",
                                     "message": "period must be week|month|all"})


@app.get("/org/screening-stats")
async def org_screening_stats(
    request: Request,
    period: str = "month",
    settings: Settings = Depends(get_settings),
) -> dict:
    """Org-wide Screening Health rollup — the HomePanel admin card
    (docs/fraud-screening-admin-panel-plan.md §1.1): cases screened / with
    warnings / confirmed / false alarms + per-app rows. Admin-gated; read-only
    aggregation, computed on demand (no cron)."""
    roles = set(get_user_roles(request) or [])
    if not roles & {"org_admin", "super_admin", "dept_admin"}:
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            detail="screening stats are admin-only")
    # Screenings are keyed by the APP tenant. In this single-tenant deployment
    # posture that equals the caller's org, but the JWT may carry the value in
    # either claim (a dept_admin token can lack org_id and fall back to a
    # dept-shaped tenant_id) — so match on BOTH claims rather than silently
    # showing a zeroed card when the wrong one was picked.
    tenants = [t for t in {get_org_id(request), get_tenant_id(request)} if t]
    if not tenants:
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            detail="no org/tenant claim on the token")
    from fraud_synthesis import screening_stats

    stats = await screening_stats(
        tenant_id=tenants[0] if len(tenants) == 1 else tenants,
        since=_screening_period_start(period))
    # Display names for the per-app rows (best-effort; slug is the fallback).
    slugs = [a["app_slug"] for a in stats.get("apps", [])]
    if slugs:
        names = {
            d["slug"]: d.get("name")
            async for d in get_apps_col().find(
                {"slug": {"$in": slugs}}, {"slug": 1, "name": 1})
        }
        for a in stats["apps"]:
            a["name"] = names.get(a["app_slug"]) or a["app_slug"]
    stats["period"] = period
    # Distinct ontology domain triples across this deployment's screens —
    # autowire stamps them from sources.json (Phase 2). Feeds the Screening
    # Health badge ("US · Utility · Metering inspection") and orders the
    # education catalogue by relevance. Empty when no source declares one.
    # agent_spec lives in the AGENTS collection (app docs carry app_spec only),
    # so resolve the caller's LIVE apps first — tenant-scoped like every other
    # query in this endpoint, and published-only so a retired app's stamped
    # domain can't show a stale badge forever.
    _domains: List[Dict[str, str]] = []
    _seen_domains: set = set()
    _live_agent_ids = [
        _a["agent_id"]
        async for _a in get_apps_col().find(
            {"tenant_id": {"$in": tenants},
             "status": AppStatus.PUBLISHED.value,
             "agent_id": {"$nin": [None, ""]}},
            {"agent_id": 1})
    ]
    if _live_agent_ids:
        async for _d in get_agents_col().find(
                {"agent_id": {"$in": _live_agent_ids},
                 "agent_spec.tools_v2": {
                     "$elemMatch": {"kind": "consistency_check",
                                    "domain": {"$ne": None}}}},
                {"agent_spec.tools_v2.kind": 1, "agent_spec.tools_v2.domain": 1}):
            for _t in ((_d.get("agent_spec") or {}).get("tools_v2") or []):
                if not (isinstance(_t, dict) and _t.get("kind") == "consistency_check"):
                    continue
                _dom = _t.get("domain")
                if isinstance(_dom, dict) and _dom:
                    _key = tuple(sorted(_dom.items()))
                    if _key not in _seen_domains:
                        _seen_domains.add(_key)
                        _domains.append(_dom)
    stats["domains"] = _domains
    return stats


@app.get("/apps/{slug}/screening-stats")
async def app_screening_stats(
    slug: str,
    request: Request,
    period: str = "month",
    settings: Settings = Depends(get_settings),
) -> dict:
    """Per-app Screening Health drill-down (plan §1.2): signal-type breakdown
    with plain labels, per-signal officer verdicts, verbatim dismissal reasons,
    and the deterministic §3 false-alarm advisories. Same access gate as
    fraud-calibration; 409 when the ontology never enabled screening."""
    user_id = get_secure_user_id(request)
    user_tenant = get_tenant_id(request)
    await _bind_app_env(slug)
    app_doc = await get_apps_col().find_one({"slug": slug})
    if not app_doc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="app not found")
    if not _can_edit_app(
        app_doc, user_id, user_tenant,
        user_org_id=get_org_id(request),
        user_dept_ids=get_user_dept_ids(request),
        user_roles=get_user_roles(request),
        sa_admin_of=get_sa_admin_of(request),
    ):
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            detail="not allowed to view screening stats for this app")
    from fraud_synthesis import screening_stats

    stats = await screening_stats(
        tenant_id=app_doc.get("tenant_id") or user_tenant, app_slug=slug,
        since=_screening_period_start(period))
    # Unlike calibration (an ACTION), stats are a read-only view — historical
    # screenings stay visible even when screening is currently off (an app
    # published before the fraud_enabled flag, or later opted out, still has
    # real judged history the admin must be able to see). Only when there is
    # ALSO nothing to show is the 409 the more helpful answer.
    fraud_enabled = bool(app_doc.get("fraud_enabled"))
    if not fraud_enabled and not (stats.get("totals") or {}).get("screened"):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={"code": "fraud_not_enabled",
                    "message": "fraud screening is not enabled for this app "
                               "(no dataset opted into fraud_screening in the ontology)"},
        )
    stats["fraud_enabled"] = fraud_enabled
    stats["period"] = period
    return stats


async def _resolve_org_identity(
    *, app_spec: AppSpec, settings: Settings,
    auth_header: Optional[str], tenant_id: Optional[str],
) -> Optional[Dict[str, Any]]:
    """The customer's display identity from the ontology
    (docs/runtime-ui-modernization-plan.md Track E): the first mcp data
    source whose catalogue entry carries an ``organization`` block wins
    (deterministic — a tenant's sources should all declare the same one).
    Returns the raw block ({name, short_name?, logo_url?, brand_color?}) or
    None when no source declares identity."""
    from catalogue_client import fetch_catalogue_entry

    for ds in (app_spec.data_sources or []):
        if getattr(ds, "type", None) != "mcp" or not getattr(ds, "ref", None):
            continue
        try:
            entry = await fetch_catalogue_entry(
                settings=settings, tenant_id=tenant_id, dataset_id=ds.ref,
                auth_header=auth_header,
            )
        except Exception as exc:  # noqa: BLE001 — identity is a default, not a gate
            logger.warning("[identity] catalogue fetch failed for %r: %s",
                           ds.ref, exc)
            continue
        org = (entry or {}).get("organization")
        if isinstance(org, dict) and org.get("name"):
            return org
    return None


#: Minimum disposed decisions PER COHORT before a memory-lift number is
#: published — below this the endpoint returns lift=None with a note, never a
#: fabricated percentage off three data points.
_LIFT_MIN_COHORT = int(os.getenv("MEMORY_LIFT_MIN_COHORT", "10"))


async def _value_stats(
    *, tenants: List[str], since: Optional[datetime],
    slug: Optional[str] = None,
) -> dict:
    """The canonical money aggregation (docs/money-saved-roi-plan.md V4):
    sums of poller-stamped ``outcome.value`` amounts, attribution-filtered by
    the rule STAMPED on each value (the ontology's frozen definition), grouped
    by kind + currency, with per-app breakdown and the definition versions
    echoed — every headline number is traceable to the definition that
    computed it."""
    col = get_decision_records_col()
    match: Dict[str, Any] = {
        "tenant_id": {"$in": tenants},
        "outcome.value.amount": {"$type": "number"},
    }
    if since is not None:
        match["created_at"] = {"$gte": since}
    if slug:
        match["slug"] = slug
    pipeline: List[Dict[str, Any]] = [
        {"$match": match},
        {"$project": {
            "slug": 1, "mode": 1,
            "has_overrides": {"$gt": [{"$size": {"$ifNull": ["$overrides", []]}}, 0]},
            "amount": "$outcome.value.amount",
            "kind": "$outcome.value.kind",
            "currency": "$outcome.value.currency",
            "attribution": "$outcome.value.attribution",
            "defver": "$outcome.value.definition_version",
        }},
        # THE attribution rule, applied per record from its OWN stamp:
        # approved_recommendation / approved_within_window count only officer-
        # approved-as-recommended decisions (the window was already enforced at
        # stamping); any_citra_touched counts every committed decision.
        {"$match": {"$expr": {"$or": [
            {"$eq": ["$attribution", "any_citra_touched"]},
            {"$and": [
                {"$eq": ["$mode", "human_approved"]},
                {"$eq": ["$has_overrides", False]},
            ]},
        ]}}},
        {"$facet": {
            "totals": [{"$group": {
                "_id": {"kind": "$kind", "currency": "$currency"},
                "amount": {"$sum": "$amount"},
                "decisions": {"$sum": 1},
            }}],
            "by_app": [{"$group": {
                "_id": {"slug": "$slug", "kind": "$kind", "currency": "$currency"},
                "amount": {"$sum": "$amount"},
                "decisions": {"$sum": 1},
            }}, {"$sort": {"amount": -1}}],
            "definitions": [{"$group": {
                "_id": None,
                "versions": {"$addToSet": "$defver"},
                "attributions": {"$addToSet": "$attribution"},
            }}],
        }},
    ]
    agg = await col.aggregate(pipeline).to_list(length=1)
    f = agg[0] if agg else {}
    totals = [{"kind": t["_id"].get("kind"), "currency": t["_id"].get("currency"),
               "amount": round(float(t.get("amount") or 0), 2),
               "decisions": t.get("decisions", 0)}
              for t in (f.get("totals") or [])]
    by_app = [{"slug": t["_id"].get("slug"), "kind": t["_id"].get("kind"),
               "currency": t["_id"].get("currency"),
               "amount": round(float(t.get("amount") or 0), 2),
               "decisions": t.get("decisions", 0)}
              for t in (f.get("by_app") or [])]
    defs = (f.get("definitions") or [{}])
    d0 = defs[0] if defs else {}
    # Errors are part of the honest picture: values that could NOT be stamped.
    err_match = dict(match)
    err_match.pop("outcome.value.amount", None)
    err_match["outcome.value_error"] = {"$exists": True}
    value_errors = await col.count_documents(err_match)
    return {
        "totals": totals,
        "by_app": by_app,
        "definition_versions": sorted(v for v in (d0.get("versions") or []) if v),
        "attributions": sorted(a for a in (d0.get("attributions") or []) if a),
        "value_errors": value_errors,
        # Honesty by construction: no invented baselines (plan §9).
        "baseline": None,
        "note": None if totals else (
            "no stamped values yet — declare value_semantics in sources.json, "
            "republish the app, and values accrue as decisions settle "
            "(outcome poller)"),
    }


@app.post("/admin/value-backfill")
async def value_backfill(
    request: Request,
    slug: Optional[str] = None,
    limit: int = 500,
    settings: Settings = Depends(get_settings),
) -> dict:
    """Backfill ``outcome.value`` onto ALREADY-SETTLED decisions
    (docs/money-saved-roi-plan.md V2/V5): decisions labelled before this app's
    ``value_semantics`` existed (or before the ROI spine shipped) get their
    value stamped now, so pilot history isn't lost.

    Semantics:
      * Only records with a settled label and NO value/value_error are touched;
        the historical label is NEVER rewritten — this stamps money, not history.
      * Value computation is the SAME path the poller uses (fresh structured
        read-back + compute_outcome_value), so a backfilled number is exactly
        what the poller would have stamped — same definition_version, same
        fail-loud rules (a failed read stamps value_error, never a silent zero).
      * Admin-gated, capped, idempotent (stamped records fall out of the query).
    """
    roles = set(get_user_roles(request) or [])
    if not roles & {"org_admin", "super_admin", "dept_admin"}:
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            detail="value backfill is admin-only")
    tenants = [t for t in {get_org_id(request), get_tenant_id(request)} if t]
    if not tenants:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="no org/tenant claim")
    limit = max(1, min(int(limit), 2000))
    col = get_decision_records_col()
    agents = get_agents_col()
    q: Dict[str, Any] = {
        "tenant_id": {"$in": tenants},
        "outcome.label": {"$in": ["good", "bad", "neutral"]},
        "outcome.value": {"$exists": False},
        "outcome.value_error": {"$exists": False},
    }
    if slug:
        q["slug"] = slug
    cursor = col.find(q).sort("created_at", 1).limit(limit)

    from value_stamping import pick_value_semantics_for_table

    sys_jwt_cache: Dict[str, Optional[str]] = {}
    vs_cache: Dict[str, Any] = {}
    scanned = stamped = errored = no_definition = unreadable = 0
    async for rec in cursor:
        scanned += 1
        app_id = rec.get("app_id") or ""
        adoc = await agents.find_one(
            {"agent_id": rec.get("agent_id"), "tenant_id": rec.get("tenant_id")},
            {"agent_spec.outcome_poll": 1},
        )
        cfg = (((adoc or {}).get("agent_spec") or {}).get("outcome_poll")) or None
        if not cfg:
            no_definition += 1
            continue
        if app_id not in vs_cache:
            _vs_doc = await get_apps_col().find_one(
                {"app_id": app_id, "tenant_id": rec.get("tenant_id")},
                {"value_semantics": 1},
            ) or await get_apps_col().find_one(
                {"slug": rec.get("slug"), "tenant_id": rec.get("tenant_id")},
                {"value_semantics": 1},
            )
            vs_cache[app_id] = (_vs_doc or {}).get("value_semantics") or None
        value_cfg = pick_value_semantics_for_table(vs_cache.get(app_id), cfg.get("table"))
        if not value_cfg:
            # The app has no money definition — republish after annotating
            # sources.json; nothing to stamp until then.
            no_definition += 1
            continue
        if app_id not in sys_jwt_cache:
            sys_jwt_cache[app_id] = await _mint_poll_system_jwt(settings, rec)
        verdict = await _classify_decision_outcome(
            settings=settings, rec=rec, cfg=cfg,
            user_jwt=sys_jwt_cache.get(app_id), value_cfg=value_cfg,
        )
        if verdict is None:
            # Row no longer readable / no longer settled-looking — LOUD skip;
            # the stored label stays untouched.
            logger.error("[backfill] read-back failed — value NOT stamped "
                         "(decision=%s)", rec.get("decision_id"))
            unreadable += 1
            continue
        update: Dict[str, Any] = {}
        if verdict.get("value") is not None:
            update["outcome.value"] = verdict["value"]
            update["outcome.value"]["basis"] = {
                **(verdict["value"].get("basis") or {}), "backfilled": True}
        if verdict.get("value_error"):
            update["outcome.value_error"] = verdict["value_error"]
            errored += 1
        if not update:
            # Settled but valueless under the definition (e.g. a non-prevented
            # status under prevented_loss) — a genuine non-value; leave as-is.
            continue
        await col.update_one({"decision_id": rec["decision_id"]},
                             {"$set": update})
        if "outcome.value" in update:
            stamped += 1
    return {
        "scanned": scanned, "stamped": stamped, "value_errors": errored,
        "skipped_no_definition": no_definition, "unreadable": unreadable,
        "slug": slug, "limit": limit,
    }


@app.get("/org/value-stats")
async def org_value_stats(
    request: Request,
    period: str = "month",
    settings: Settings = Depends(get_settings),
) -> dict:
    """Org-wide Money impact — the canonical ROI spine aggregation
    (docs/money-saved-roi-plan.md). Every number here is a sum of
    poller-stamped ``outcome.value`` amounts computed by the ontology's FROZEN
    definitions; the definition versions and attribution rules are echoed so
    the headline is defensible. Admin-gated like /org/decision-stats."""
    roles = set(get_user_roles(request) or [])
    if not roles & {"org_admin", "super_admin", "dept_admin"}:
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            detail="value stats are admin-only")
    tenants = [t for t in {get_org_id(request), get_tenant_id(request)} if t]
    if not tenants:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="no org/tenant claim")
    stats = await _value_stats(
        tenants=tenants, since=_screening_period_start(period))
    stats["period"] = period
    return stats


@app.get("/apps/{slug}/value-stats")
async def app_value_stats(
    slug: str,
    request: Request,
    period: str = "month",
    settings: Settings = Depends(get_settings),
) -> dict:
    """Per-app Money impact drill-down. Same gate + tenant discipline as the
    org rollup; scoped to one app's ledger."""
    roles = set(get_user_roles(request) or [])
    if not roles & {"org_admin", "super_admin", "dept_admin"}:
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            detail="value stats are admin-only")
    tenants = [t for t in {get_org_id(request), get_tenant_id(request)} if t]
    if not tenants:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="no org/tenant claim")
    stats = await _value_stats(
        tenants=tenants, since=_screening_period_start(period), slug=slug)
    stats["period"] = period
    stats["slug"] = slug
    return stats


@app.get("/org/decision-stats")
async def org_decision_stats(
    request: Request,
    period: str = "month",
    settings: Settings = Depends(get_settings),
) -> dict:
    """Org-wide Success Rate rollup — the HomePanel admin card
    (docs/adoption-metrics-precedent-citation-plan.md §1): across all Decision
    Apps, how many AI recommendations were accepted / accepted-with-changes /
    rejected, plus the current pending backlog and the memory-lift cohorts.

    Bucket exactness rules (they lie in a demo if fudged):
      * accepted              = human_approved with ZERO overrides
      * accepted_with_changes = human_approved with overrides — its own bucket,
        never folded into accepted or rejected
      * rejected              = human_rejected
      * automated             = auto_process, reported separately and NEVER
        mixed into the human acceptance rate (automation is configured, not earned)
      * human_direct is EXCLUDED (no AI recommendation existed)
      * pending               = current staging backlog (a NOW quantity — not
        period-filtered; the disposition buckets are)
    Admin-gated; read-only aggregation on demand (no cron)."""
    roles = set(get_user_roles(request) or [])
    if not roles & {"org_admin", "super_admin", "dept_admin"}:
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            detail="decision stats are admin-only")
    # Rows are keyed by the APP tenant; match both of the caller's plausible
    # claims (same tenant-key lesson as /org/screening-stats).
    tenants = [t for t in {get_org_id(request), get_tenant_id(request)} if t]
    if not tenants:
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            detail="no org/tenant claim on the token")
    tenant_match: Any = tenants[0] if len(tenants) == 1 else {"$in": tenants}
    since = _screening_period_start(period)

    match: Dict[str, Any] = {"tenant_id": tenant_match}
    if since is not None:
        match["created_at"] = {"$gte": since}
    # One server-side pass: slug × mode × overridden × memory cohort. The
    # cohort key keeps None (pre-stamp rows) distinct from 0 (a real cold
    # run) — coercing old rows to "cold" would poison the lift baseline.
    rows = await get_decision_records_col().aggregate([
        {"$match": match},
        {"$group": {
            "_id": {
                "slug": "$slug",
                "mode": "$mode",
                "overridden": {"$gt": [{"$size": {"$ifNull": ["$overrides", []]}}, 0]},
                "cohort": {"$switch": {
                    "branches": [
                        {"case": {"$eq": [{"$ifNull": ["$retrieval_count", None]}, None]},
                         "then": "unknown"},
                        {"case": {"$gt": ["$retrieval_count", 0]}, "then": "with_memory"},
                    ],
                    "default": "cold",
                }},
            },
            "n": {"$sum": 1},
        }},
    ]).to_list(length=10000)

    def _dbucket() -> Dict[str, int]:
        return {"accepted": 0, "accepted_with_changes": 0, "rejected": 0,
                "automated": 0, "pending": 0}

    totals = _dbucket()
    per_app: Dict[str, Dict[str, int]] = {}
    cohorts = {c: {"accepted": 0, "disposed": 0} for c in ("with_memory", "cold")}
    for r in rows:
        key = r["_id"]
        mode, n = key.get("mode"), r.get("n", 0)
        if mode == "human_approved":
            field = "accepted_with_changes" if key.get("overridden") else "accepted"
        elif mode == "human_rejected":
            field = "rejected"
        elif mode == "auto_process":
            field = "automated"
        else:
            continue  # human_direct (no AI recommendation) and unknown modes
        slug_key = key.get("slug") or "?"
        for b in (totals, per_app.setdefault(slug_key, _dbucket())):
            b[field] += n
        # Memory-lift cohorts: HUMAN dispositions only, known-cohort rows only.
        cohort = key.get("cohort")
        if mode in ("human_approved", "human_rejected") and cohort in cohorts:
            cohorts[cohort]["disposed"] += n
            if field == "accepted":
                cohorts[cohort]["accepted"] += n

    # Current pending backlog from the officer inbox (not period-filtered —
    # pending is a NOW quantity).
    pending_rows = await get_workflow_staging_col().aggregate([
        {"$match": {"tenant_id": tenant_match,
                    "status": {"$regex": "^pending_"}}},
        {"$group": {"_id": "$slug", "n": {"$sum": 1}}},
    ]).to_list(length=2000)
    for p in pending_rows:
        slug_key = p["_id"] or "?"
        per_app.setdefault(slug_key, _dbucket())["pending"] += p.get("n", 0)
        totals["pending"] += p.get("n", 0)

    def _acc_rate(b: Dict[str, int]) -> Optional[float]:
        disposed = b["accepted"] + b["accepted_with_changes"] + b["rejected"]
        return round(b["accepted"] / disposed, 3) if disposed else None

    def _cohort_rate(c: Dict[str, int]) -> Optional[float]:
        return round(c["accepted"] / c["disposed"], 3) if c["disposed"] else None

    # Memory lift: acceptance%(with memory) − acceptance%(cold); published
    # only when BOTH cohorts clear the minimum — never a number off 3 rows.
    lift: Optional[float] = None
    lift_note: Optional[str] = None
    wm, cold = cohorts["with_memory"], cohorts["cold"]
    if wm["disposed"] >= _LIFT_MIN_COHORT and cold["disposed"] >= _LIFT_MIN_COHORT:
        lift = round((_cohort_rate(wm) or 0) - (_cohort_rate(cold) or 0), 3)
    else:
        lift_note = (
            f"insufficient data — needs ≥{_LIFT_MIN_COHORT} disposed decisions in "
            f"both cohorts (with memory: {wm['disposed']}, cold: {cold['disposed']})"
        )

    apps_out = [
        {"app_slug": s, **b, "acceptance_rate": _acc_rate(b)}
        for s, b in sorted(per_app.items())
    ]
    slugs = [a["app_slug"] for a in apps_out]
    if slugs:
        names = {
            d["slug"]: d.get("name")
            async for d in get_apps_col().find(
                {"slug": {"$in": slugs}}, {"slug": 1, "name": 1})
        }
        for a in apps_out:
            a["name"] = names.get(a["app_slug"]) or a["app_slug"]

    return {
        "period": period,
        "since": since.isoformat() if since else None,
        "totals": {**totals, "acceptance_rate": _acc_rate(totals)},
        "apps": apps_out,
        "memory_lift": {
            "with_memory": {**wm, "acceptance_rate": _cohort_rate(wm)},
            "cold": {**cold, "acceptance_rate": _cohort_rate(cold)},
            "lift": lift,
            **({"note": lift_note} if lift_note else {}),
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/apps/{slug}/grounding/refresh/status")
async def get_app_grounding_refresh_status(
    slug: str,
    request: Request,
    run_id: Optional[str] = None,
    settings: Settings = Depends(get_settings),
) -> dict:
    """Poll the progress of a grounding refresh. Returns the latest run for the
    app: status (running/complete/failed), phase, progress (0–100), counts, and
    — on completion — the result or error. (Run state is transient in Redis;
    timestamps are ISO strings.)"""
    await _resolve_grounded_app(slug, request)
    from grounding_runs import get_grounding_run_store
    doc = get_grounding_run_store().get(slug)
    if not doc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="no grounding refresh found for this app")
    if run_id and doc.get("run_id") != run_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="no such grounding run for this app")
    return doc


@app.get("/apps/{slug}/grounding/status")
async def get_app_grounding_status(
    slug: str,
    request: Request,
    settings: Settings = Depends(get_settings),
) -> dict:
    """DURABLE grounding freshness for the app card: has the few-shot memory EVER
    been refreshed, and if so when + how many samples. Unlike
    /grounding/refresh/status (transient job progress that 404s after its TTL),
    this survives so the card can show 'Pending — never refreshed' vs 'Last
    refreshed <date> · N samples' and offer a manual refresh."""
    await _resolve_grounded_app(slug, request)
    from grounding_runs import get_grounding_run_store
    store = get_grounding_run_store()
    last = store.get_last(slug)
    run = store.get(slug)
    return {
        "slug": slug,
        "never_refreshed": last is None,
        "last_refreshed_at": (last or {}).get("last_refreshed_at"),
        "sample_count": (last or {}).get("sample_count"),
        "canonical_samples": (last or {}).get("canonical_samples"),
        "neighbor_samples": (last or {}).get("neighbor_samples"),
        "in_progress": bool(run and run.get("status") == "running"),
    }


@app.post("/apps/{slug}/promote-to-prod", response_model=PromoteToProdResponse)
async def promote_to_prod(
    slug: str,
    request: Request,
    payload: PromoteToProdRequest = PromoteToProdRequest(),
    settings: Settings = Depends(get_settings),
) -> PromoteToProdResponse:
    """Promote a TEST app to prod — copy its spec from the test_ collections to
    the prod collections. The whole develop→ship operation.

    The app was built + tested against the test MCPs (its definition + queue +
    audit live in the test_ collections). Promote copies ``app_spec`` (and the
    ``agent_spec`` it references) into the prod collections; the runtime then
    resolves the slug to prod by store and runs it against the prod MCPs. IT
    owns that the prod schema matches test — there is no re-validation or diff
    here (by design; see docs/multi-environment-test-prod-plan.md).

    Only writes the spec docs. Operational data (queue/audit/records) is NOT
    copied — prod starts clean; the test rows stay in the test_ collections.
    """
    requester_id = get_secure_user_id(request)
    if not requester_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="authentication required",
        )
    if not settings.test_environment_available:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="no test environment configured — nothing to promote from",
        )
    if _db is None or _apps_col is None or _agents_col is None:
        raise RuntimeError("Database not initialised")

    # Read the SOURCE from the test_ collections with RAW handles (promote
    # decides the env, so it must not depend on the routed accessors).
    test_apps = _db[_test_collection_name(settings.apps_collection)]
    test_agents = _db[_test_collection_name(settings.agents_collection)]
    src = await test_apps.find_one({"slug": slug})
    if not src:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no test app '{slug}' to promote (build + test it first)",
        )

    # Authorisation: same gate as editing the app (owner-SA admin or override
    # role). A test app a BA can edit is one they may ship.
    if not _can_edit_app(
        src, requester_id, get_tenant_id(request),
        user_org_id=get_org_id(request),
        user_dept_ids=get_user_dept_ids(request),
        user_roles=get_user_roles(request),
        sa_admin_of=get_sa_admin_of(request),
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="not authorised to promote this app",
        )

    # ── Grounding gate ──────────────────────────────────────────────────────
    # A grounded app learns from historical decisions; if its few-shot memory was
    # never refreshed (or is stale), it would go live in prod running degraded —
    # the runtime injects a cold-start note and reasons WITHOUT precedent. Detect
    # grounding from the test agent spec, then require a fresh refresh (or an
    # explicit override) BEFORE any write. Grounding is env-unified (shared
    # agent_id + Milvus + slug-keyed freshness), so the refresh enqueued after the
    # promote populates exactly the rows the prod app reads.
    grounded = False
    grounding_contract = None
    _promote_agent_id = src.get("agent_id")
    if _promote_agent_id:
        _ga = await test_agents.find_one(
            {"agent_id": _promote_agent_id}, {"agent_spec.grounding": 1}
        )
        _gspec = ((_ga or {}).get("agent_spec") or {}).get("grounding")
        if _gspec:
            grounded = True
            from models import GroundingContract
            try:
                grounding_contract = GroundingContract.model_validate(_gspec)
            except Exception as e:  # a bound-but-invalid contract can't be refreshed
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail={"code": "grounding_invalid",
                            "message": f"grounding contract invalid: {e}"},
                )

    if grounded and not payload.promote_ungrounded:
        _fresh = _grounding_freshness_state(slug)
        if _fresh in ("never", "stale") and not payload.refresh_grounding:
            from grounding_runs import get_grounding_run_store
            _last = get_grounding_run_store().get_last(slug)
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail={
                    "code": "grounding_refresh_required",
                    "message": (
                        "This app learns from your historical decisions. Its "
                        "grounding has "
                        + ("never been refreshed" if _fresh == "never"
                           else "gone stale")
                        + " — refresh it for production, or promote without "
                          "grounding."
                    ),
                    "freshness": _fresh,
                    "last_refreshed_at": (_last or {}).get("last_refreshed_at"),
                    "sample_count": (_last or {}).get("sample_count"),
                },
            )

    # Optional audience override — validate here (same as POST /audience)
    # before it reaches the raw spec write below, so a bad value (e.g. bare
    # "team") fails loud instead of persisting unfetchably.
    clean_audience: Optional[str] = None
    if payload.audience is not None:
        clean_audience = payload.audience.strip()
        try:
            parse_audience(clean_audience)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "invalid_audience", "message": str(exc)},
            )

    # The prod slug drops any _preview suffix the build artefact may carry, so
    # the live app gets the clean slug regardless of how it was published.
    prod_slug = slug[: -len("_preview")] if slug.endswith("_preview") else slug
    src_spec = dict(src.get("app_spec") or {})
    src_spec["slug"] = prod_slug
    if clean_audience is not None:
        src_spec["audience"] = clean_audience
    # Strip build-artefact-only fields so the prod row is a clean published app.
    src_spec["preview_mode"] = False
    src_spec["preview_until"] = None
    src_spec["promoted_to_slug"] = None
    src_spec["promoted_at"] = None
    # AI triggers arrive in prod DEACTIVATED — the officer activates them in the
    # prod Auto-Recommend panel (a promote shouldn't auto-start schedules/webhooks
    # in prod without a prod-side click), even if they were toggled on in test.
    for _t in (src_spec.get("triggers") or []):
        if isinstance(_t, dict):
            _t["enabled"] = False

    now = datetime.now(timezone.utc)
    existing = await _apps_col.find_one({"slug": prod_slug})
    if existing:
        existing_tenant = existing.get("tenant_id")
        src_tenant = src.get("tenant_id")
        if existing_tenant and src_tenant and existing_tenant != src_tenant:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"prod slug '{prod_slug}' is owned by another tenant",
            )
        version = (existing.get("version", 0) or 0) + 1
        app_id = existing["app_id"]
    else:
        version = 1
        app_id = src.get("app_id") or f"app_{uuid.uuid4().hex[:12]}"
    src_spec["app_id"] = app_id

    # The LIVE embed key: minted on first promote, then preserved by every
    # promote after it. `existing` is the PROD row, so a re-promote reuses the
    # key the customer already pasted into their codebase — minting a fresh one
    # each release would force them to re-paste, which is worse than the problem
    # environment-tagged keys solve. `src` carries the TEST key (promote copies
    # the whole document), so it must NOT be inherited: env_for_key sees the
    # emb_test_ prefix and mints for prod instead. See embed_keys.py.
    _prod_embed_key = ensure_embed_key(
        app_spec=src_spec, env="prod", existing_doc=existing,
    )

    # Snapshot the PRIOR prod version before this promote overwrites it (only when
    # one exists — first promote has nothing to snapshot). Uses RAW prod handles —
    # promote always targets prod, never the routed test store — and runs before
    # the agent overwrite below so the live prod agent doc still holds the
    # superseded agent_spec.
    if existing:
        await _snapshot_prior_version(
            apps_col=_apps_col,
            agents_col=_agents_col,
            versions_col=_spec_versions_col,
            app_doc_before=existing,
            actor=requester_id or "",
            reason="promote-to-prod",
            max_keep=settings.max_spec_versions,
        )

    # Copy the referenced agent spec first (so the prod app never points at a
    # missing agent), then the app. Both keyed by their stable id.
    agent_promoted = False
    agent_id = src.get("agent_id")
    if agent_id:
        src_agent = await test_agents.find_one({"agent_id": agent_id})
        if src_agent:
            agent_doc = {
                k: v for k, v in src_agent.items() if k != "_id"
            }
            await _agents_col.replace_one(
                {"agent_id": agent_id}, agent_doc, upsert=True
            )
            agent_promoted = True
        else:
            logger.warning(
                "[promote-to-prod] test app %s references agent %s not found in "
                "test_agents — promoting app shell without agent",
                slug, agent_id,
            )

    prod_doc = {
        "app_id": app_id,
        "slug": prod_slug,
        "tenant_id": src.get("tenant_id"),
        "agent_id": agent_id,
        "version": version,
        "deployed_at": now,
        "status": AppStatus.PUBLISHED.value,
        "embed_key": _prod_embed_key,
        "app_spec": src_spec,
        # Carry the grounding flag so the prod card shows "Refresh grounding".
        "grounded": bool(src.get("grounded")),
        # Carry the fraud flag so the prod card shows "Calibrate fraud".
        "fraud_enabled": bool(src.get("fraud_enabled")),
        # Carry the publish-resolved ROI money definitions + display identity —
        # the PROD outcome poller and value-stats read THIS doc; dropping them
        # here silently disabled value stamping for promoted apps (live-caught
        # on the acme recovery tracker).
        "value_semantics": src.get("value_semantics") or {},
        "organization": src.get("organization"),
    }
    await _apps_col.replace_one({"slug": prod_slug}, prod_doc, upsert=True)

    logger.info(
        "[promote-to-prod] %s → prod:%s (version=%d, agent=%s, requester=%s)",
        slug, prod_slug, version, agent_promoted, requester_id,
    )
    # ── Grounding gate: apply the chosen path now the prod app exists ───────
    # The 409 gate above already blocked a never/stale grounded promote that
    # asked for neither refresh nor override, so here we only ACT on the choice.
    grounding_run_id: Optional[str] = None
    grounding_status: Optional[str] = None
    if grounded:
        if payload.promote_ungrounded:
            grounding_status = "skipped"
            logger.warning(
                "[promote-to-prod] %s promoted WITHOUT grounding — explicit "
                "override by %s (app will run on its base prompt until refreshed)",
                prod_slug, requester_id,
            )
        elif payload.refresh_grounding:
            # Enqueue against the PROD slug + PRESERVED agent_id: the refresh pulls
            # real history and swaps this agent's rows in the shared collection, so
            # the just-promoted prod app is grounded; the UI streams progress via
            # /grounding/refresh/status. None run_id = a refresh is already running.
            _auth = request.headers.get("authorization") or request.headers.get("Authorization")
            grounding_run_id = _enqueue_grounding_refresh(
                settings=settings, slug=prod_slug, agent_id=agent_id,
                tenant_id=src.get("tenant_id") or "",
                contract=grounding_contract,
                user_jwt=(_auth or "").removeprefix("Bearer ").strip(),
                requested_by=requester_id,
            )
            grounding_status = "refreshing" if grounding_run_id else "fresh"
        else:
            # Passed the gate without asking to refresh → memory was already fresh.
            grounding_status = "fresh"

    return PromoteToProdResponse(
        slug=prod_slug,
        prod_url=_runtime_url(prod_slug, settings),
        version=version,
        agent_promoted=agent_promoted,
        grounding_refresh_run_id=grounding_run_id,
        grounding_status=grounding_status,
    )


# ---------------------------------------------------------------------------
# List + fetch + archive
# ---------------------------------------------------------------------------


@app.get("/admin/apps")
async def admin_list_apps(request: Request, limit: int = 200) -> dict:
    """Admin list of every smart-app scoped to caller's authority.

    super_admin → all apps; org_admin → their org; dept_admin → dept overlap.
    """
    roles = set(get_user_roles(request))
    org_id = get_org_id(request)
    dept_ids = set(get_user_dept_ids(request))
    apps = get_apps_col()

    q: dict = {}
    if "super_admin" in roles:
        pass
    elif "org_admin" in roles and org_id:
        q["$or"] = [
            {"app_spec.org_id": org_id},
            {"app_spec.tenant_id": org_id},
            {"tenant_id": org_id},
        ]
    elif "dept_admin" in roles and dept_ids:
        # dept_admin sees apps that either tag this dept in their scope OR
        # are owned by this dept (owner_type='dept', owner_id in dept_ids).
        # Matches the /apps?scope=admin lens for dept_admin.
        dept_list = list(dept_ids)
        q["$or"] = [
            {"app_spec.dept_ids": {"$in": dept_list}},
            {
                "app_spec.owner_type": "dept",
                "app_spec.owner_id": {"$in": dept_list},
            },
        ]
    else:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="admin role required")

    cur = apps.find(q).sort("app_spec.updated_at", -1).limit(int(limit))
    out = []
    async for doc in cur:
        spec = doc.get("app_spec") or {}
        out.append({
            "slug": doc.get("slug"),
            "title": spec.get("title") or doc.get("title"),
            "owner_type": spec.get("owner_type"),
            "owner_id": spec.get("owner_id"),
            "audience": spec.get("audience") or "owner",
            "org_id": spec.get("org_id") or spec.get("tenant_id") or doc.get("tenant_id"),
            "dept_ids": spec.get("dept_ids") or [],
            "kind": spec.get("kind", "app"),
            "status": doc.get("status"),
        })
    return {"count": len(out), "apps": out}


@app.get("/apps", response_model=AppListResponse)
async def list_apps(
    request: Request,
    scope: str = Query(default="all", pattern="^(mine|shared|all|admin|test)$"),
    kind: Optional[str] = Query(
        default=None,
        description="Filter by artefact kind: app | dashboard.",
    ),
    include_archived: bool = Query(default=False),
    limit: int = Query(default=100, ge=1, le=500),
    settings: Settings = Depends(get_settings),
) -> AppListResponse:
    """List apps under one of five lenses (default ``all``).

      * ``all``    — everything visible to the caller via owner-SA OR
                     audience (owner/team/dept/org). API-only lens (the
                     UI's "Everything" tab was removed — it was mine+shared
                     without test, so its count read as wrong).
      * ``test``   — caller's own apps in the TEST store still AWAITING
                     promotion (no prod copy, or test re-published since
                     the last promote).
      * ``mine``   — owned by an SA the caller ADMINS (apps the caller can
                     actually edit/archive). SA-membership-only apps are NOT
                     "mine" — they fall under ``shared``.
      * ``shared`` — visible but NOT owned by an SA the caller admins (apps
                     the caller can run; editable only if a role grants it).
      * ``admin``  — admin-only audit lens. Role-gated:
                       dept_admin → apps in any of caller's depts
                       org_admin / super_admin →
                            every app in the caller's tenant
                     Non-admins get 403.
    """
    user_tenant = get_tenant_id(request)
    user = getattr(request.state, "user", None) or {}
    roles = set(user.get("roles") or [])
    sa_admin_of = list(user.get("service_account_admin_of") or [])
    sa_member_of = list(user.get("service_account_member_of") or [])
    sa_ids_all = list(dict.fromkeys([s for s in (sa_admin_of + sa_member_of) if s]))
    dept_ids = list(user.get("dept_ids") or [])
    claims_for_vis = {
        "service_account_admin_of": sa_admin_of,
        "service_account_member_of": sa_member_of,
        "dept_ids": dept_ids,
        "org_id": user.get("org_id") or user_tenant or "",
        "tenant_id": user_tenant or "",
        "roles": list(roles),
    }

    # Each independent filter goes in `filter_clauses`; we $and them at
    # the end so that multiple $or-shaped predicates (e.g. scope=all's
    # visibility + kind=app's existence-check) don't clobber each other
    # by both wanting the top-level `$or` key.
    q: dict = {}
    filter_clauses: list[dict] = []

    if scope == "test":
        # Test tab: the caller's own apps living in the TEST store (test_
        # collections). Bind env so every accessor below reads test_; require a
        # configured test env (else there are no test apps). Test apps are
        # audience="owner" + owned by the BA's Work SA, so the filter mirrors
        # "mine" — the caller's own SA-admin apps.
        if not settings.test_environment_available:
            return AppListResponse(apps=[], total=0)
        set_current_env("test")
        if not sa_admin_of:
            return AppListResponse(apps=[], total=0)
        filter_clauses.append({
            "app_spec.owner_type": "service_account",
            "app_spec.owner_id": {"$in": sa_admin_of},
        })
    elif scope == "all":
        clauses = _visibility_or_clauses(claims_for_vis)
        if not clauses:
            return AppListResponse(apps=[], total=0)
        filter_clauses.append({"$or": clauses})
    elif scope == "mine":
        # Only apps the caller can edit: owned by an SA they ADMIN (not merely
        # are a member of). Member-only apps are surfaced under "shared".
        if not sa_admin_of:
            return AppListResponse(apps=[], total=0)
        filter_clauses.append({
            "app_spec.owner_type": "service_account",
            "app_spec.owner_id": {"$in": sa_admin_of},
        })
    elif scope == "shared":
        # Visible to the caller but NOT owned by an SA they admin. Kept
        # complementary to "mine" (same SA-admin set) so every visible app
        # falls in exactly one of the two tabs.
        clauses = _visibility_or_clauses(claims_for_vis)
        if not clauses:
            return AppListResponse(apps=[], total=0)
        filter_clauses.append({"$or": clauses})
        if sa_admin_of:
            filter_clauses.append({"app_spec.owner_id": {"$nin": sa_admin_of}})
    else:
        # scope == "admin"
        if not (roles & {"dept_admin", "org_admin", "super_admin"}):
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="scope=admin requires an admin role")
        if not user_tenant:
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="admin scope requires a resolvable tenant")
        q["tenant_id"] = user_tenant
        if roles & {"org_admin", "super_admin"}:
            pass  # tenant_id filter is enough
        else:
            # dept_admin — see apps in any of their depts. An app can touch
            # a dept via either: (a) AppSpec.dept_ids[] (scope tag), or
            # (b) AppSpec.owner_type='dept' + owner_id in user's dept_ids
            # (the app is owned by that dept). We need both clauses.
            if not dept_ids:
                raise HTTPException(status.HTTP_403_FORBIDDEN, detail="dept_admin role requires dept_ids in JWT")
            filter_clauses.append({"$or": [
                {"app_spec.dept_ids": {"$in": dept_ids}},
                {
                    "app_spec.owner_type": "dept",
                    "app_spec.owner_id": {"$in": dept_ids},
                },
            ]})

    if not include_archived:
        q["status"] = {"$ne": AppStatus.ARCHIVED.value}
    if kind:
        if kind not in ("app", "dashboard"):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail="kind must be one of: app, dashboard",
            )
        if kind == "app":
            filter_clauses.append({"$or": [
                {"app_spec.kind": "app"},
                {"app_spec.kind": {"$exists": False}},
            ]})
            # Keep the "app" and "dashboard" tabs mutually exclusive: an app
            # that has a dashboard page surfaces under ?kind=dashboard only.
            filter_clauses.append({"app_spec.pages.kind": {"$ne": "dashboard"}})
        elif kind == "dashboard":
            # 'dashboard' is no longer a stored kind — it's an app with a
            # page whose kind is 'dashboard'. This stays a valid LIST filter
            # (the UI's "Dashboards" tab) by matching the page marker. Match
            # both migrated apps and any un-migrated legacy kind='dashboard'.
            filter_clauses.append({"$or": [
                {"app_spec.pages.kind": "dashboard"},
                {"app_spec.kind": "dashboard"},
            ]})

    if len(filter_clauses) == 1:
        q.update(filter_clauses[0])
    elif filter_clauses:
        q["$and"] = filter_clauses

    cursor = get_apps_col().find(q).sort("deployed_at", -1).limit(limit)
    caller = {
        "user_id": user.get("user_id") or "",
        "tenant_id": user_tenant,
        "org_id": user.get("org_id") or user_tenant or "",
        "dept_ids": dept_ids,
        "roles": list(roles),
        "sa_admin_of": sa_admin_of,
    }
    docs = [doc async for doc in cursor]

    if scope == "test" and docs:
        # The Test tab lists apps AWAITING promotion. Promote copies the spec
        # to prod but leaves the test doc untouched, so without this filter a
        # shipped app sits in Test forever. A test app is "awaiting" when no
        # prod copy exists for its (prod-)slug in the same tenant, or the test
        # copy was re-published after the last promote (test deployed_at is
        # newer than prod's).
        def _prod_slug(s: str) -> str:
            return s[: -len("_preview")] if s.endswith("_preview") else s

        prod_deployed: Dict[tuple, Any] = {}
        prod_slugs = list({_prod_slug(d.get("slug") or "") for d in docs})
        async for p in _apps_col.find(
            {"slug": {"$in": prod_slugs}},
            {"slug": 1, "tenant_id": 1, "deployed_at": 1},
        ):
            prod_deployed[(p.get("slug"), p.get("tenant_id"))] = p.get("deployed_at")

        def _awaiting_promotion(d: dict) -> bool:
            key = (_prod_slug(d.get("slug") or ""), d.get("tenant_id"))
            if key not in prod_deployed:
                return True
            test_at, prod_at = d.get("deployed_at"), prod_deployed[key]
            return bool(test_at and prod_at and test_at > prod_at)

        docs = [d for d in docs if _awaiting_promotion(d)]
        apps = _safe_summaries(docs, settings, caller)
        return AppListResponse(apps=apps, total=len(apps))

    apps = _safe_summaries(docs, settings, caller)
    total = await get_apps_col().count_documents(q)
    return AppListResponse(apps=apps, total=total)


@app.get("/apps/{slug}", response_model=AppDetailResponse)
async def get_app(slug: str, request: Request) -> AppDetailResponse:
    user_id = get_secure_user_id(request)
    user_tenant = get_tenant_id(request)
    # Resolve test↔prod by store before any collection access (see _bind_app_env).
    env = await _bind_app_env(slug)
    apps = get_apps_col()
    agents = get_agents_col()

    app_doc = await apps.find_one({"slug": slug})
    if not app_doc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="app not found")

    # ``citra-app-runtime`` is a trusted server-side fetcher that renders
    # published apps for end users (see _can_render_app).
    if not _can_render_app(app_doc, request, user_id, user_tenant):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="app not found")

    spec_kind = (app_doc.get("app_spec") or {}).get("kind", "app")
    agent_spec_payload = None
    if spec_kind == "app" and app_doc.get("agent_id"):
        agent_doc = await agents.find_one(
            {
                "agent_id": app_doc["agent_id"],
                "tenant_id": app_doc.get("tenant_id"),
            }
        )
        if not agent_doc:
            raise HTTPException(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="agent spec missing for app",
            )
        agent_spec_payload = agent_doc["agent_spec"]

    return AppDetailResponse(
        app_spec=app_doc["app_spec"],
        agent_spec=agent_spec_payload,
        environment="test" if env == "test" else "prod",
        organization=app_doc.get("organization"),
    )



async def _reconcile_case_signature(
    *, slug: str, app_doc: Dict[str, Any],
    previous_app_doc: Optional[Dict[str, Any]],
    actor: str, origin: str,
) -> None:
    """Bring an app's learned clauses back in line with the signature just saved.

    Facet families ARE the retrieval key: a clause fires iff
    ``scope_facets ⊆ case_facets``. So a save that renames or drops a family
    leaves every clause scoped to the old name unable to match any case, ever,
    while still reading ``active`` in the store — knowledge that looks present
    and is not. Observed in prod: a rebuild renamed ``income_proof`` to
    ``income_proof_type`` and the clause scoped to ``income_proof:present`` went
    dark with no signal at all.

    A rename DECLARED in ``FacetSpec.aliases`` migrates the scope in place; a
    family that simply vanished moves its clauses to ``orphaned`` (out of
    LIVE_STATUSES) so the app stops claiming knowledge it cannot apply.

    Shared by /publish and the hand-edit save. It used to live inline in
    publish, which meant a hand-edit could drop a whole signature and orphan
    nothing — the one path CS-03 does not cover was also the one path that did
    no clean-up.

    Non-raising, like every other memory write on a user's critical path
    (fold_decision_feedback, record_correction): reconciliation failing must not
    fail a save. It logs at exception level instead — the one thing it must
    never do is pass quietly.
    """
    try:
        from analysis_rubrics import rubric_tenant_for_app
        import clause_store as _cs

        _sig = (app_doc.get("app_spec") or {}).get("case_signature") or {}
        _facets = _sig.get("facets") or []
        _families = [f.get("family") for f in _facets if f.get("family")]
        _fam_aliases = {
            str(_a): str(f.get("family"))
            for f in _facets
            for _a in (f.get("aliases") or [])
            if f.get("family")
        }
        # Losing the WHOLE signature is the maximal version of the failure this
        # exists to catch, and a `if _families:` guard skipped exactly that
        # case: drop one family and its clauses get orphaned; drop all of them
        # and nothing happened at all. So reconcile whenever there is something
        # to reconcile — families now, or families before. Neither ⇒ genuinely
        # nothing to do, skip the read.
        _prev_sig = ((previous_app_doc or {}).get("app_spec") or {}).get("case_signature") or {}
        _prev_families = [f.get("family") for f in (_prev_sig.get("facets") or [])
                          if f.get("family")]
        if _prev_families and not _families:
            logger.error(
                "[clause-memory] %s DROPPED THE CASE SIGNATURE for %s — it "
                "declared %d facet famil(ies) (%s) and now declares none. Every "
                "case will derive case_facets=[] and NO scoped clause can ever "
                "fire again. Their clauses are being orphaned so the app stops "
                "claiming knowledge it cannot apply. If this was not deliberate, "
                "restore the signature and save again.",
                origin, slug, len(_prev_families), sorted(set(_prev_families)),
            )
        if _families or _prev_families:
            _rec = await _cs.reconcile_scope_families(
                tenant_id=rubric_tenant_for_app(app_doc),
                app_slug=slug,
                families=_families,
                alias_map=_fam_aliases,
                actor=actor or origin.lower(),
            )
            if _rec.get("orphaned"):
                logger.warning(
                    "[%s] %s: %d learned clause(s) ORPHANED — facet famil(ies) "
                    "%s no longer emitted. They can never match a case again. "
                    "Re-add the family, or declare the rename in "
                    "FacetSpec.aliases, to bring them back.",
                    origin, slug, _rec["orphaned"], _rec.get("families_dropped"))
    except Exception:  # noqa: BLE001 — never fail a save on memory upkeep
        logger.exception(
            "[%s] %s: clause scope reconcile FAILED — learned clauses may be "
            "silently unable to match cases. Investigate before relying on this "
            "app's memory.", origin, slug)


@app.put("/apps/{slug}/spec", response_model=AppDetailResponse)
async def save_app_spec(slug: str, payload: dict, request: Request) -> AppDetailResponse:
    """Review-and-edit: persist a hand-edited ``app_spec`` (+ optional
    ``agent_spec``) for an EXISTING app. Validates exactly like publish (Pydantic
    is the sole validator), PRESERVES server identity (slug / tenant_id /
    agent_id / owner / app_id / status — the editor can't change those), and
    bumps ``version``. Owner/admin only; env-routed (test vs prod store).

    Body: ``{"app_spec": {...}, "agent_spec": {...}|null}``.
    """
    user_id = get_secure_user_id(request)
    user_tenant = get_tenant_id(request)
    _spec_env = await _bind_app_env(slug)
    apps = get_apps_col()
    agents = get_agents_col()

    app_doc = await apps.find_one({"slug": slug})
    if not app_doc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="app not found")
    # Hand-editing a LIVE spec is a reasonable admin escape hatch for an app or
    # dashboard: the blast radius is inside Citra and it is version-snapshotted
    # and reversible. For an EXTERNALLY consumed surface it is not — the change
    # reaches a customer's production screen with no test cycle and no chance
    # for them to validate it. Those go through test → promote, full stop.
    if _spec_env == "prod" and is_external_surface(app_doc.get("app_spec")):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=(
                f"'{slug}' is embedded in a customer's application, so its live "
                "spec cannot be edited directly — the change would reach their "
                "officers with no test cycle. Edit the TEST app and promote it "
                "(POST /apps/{slug}/promote-to-prod)."
            ),
        )
    if not _can_edit_app(
        app_doc, user_id, user_tenant,
        user_org_id=get_org_id(request),
        user_dept_ids=get_user_dept_ids(request),
        user_roles=get_user_roles(request),
        sa_admin_of=get_sa_admin_of(request),
    ):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="not authorised to edit this app")

    new_app = payload.get("app_spec")
    if not isinstance(new_app, dict):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="body.app_spec (object) required")
    new_agent = payload.get("agent_spec")

    # Identity is server-owned — overwrite whatever the editor sent so a hand-edit
    # can never re-slug, re-tenant, re-own, or repoint the agent of an app.
    existing = app_doc.get("app_spec") or {}
    new_app["slug"] = slug
    new_app["tenant_id"] = app_doc.get("tenant_id") or existing.get("tenant_id")
    if app_doc.get("agent_id"):
        new_app["agent_id"] = app_doc["agent_id"]
    for k in ("owner_id", "owner_type"):
        if existing.get(k) is not None:
            new_app[k] = existing[k]

    # CS-04 on the HAND-EDIT path: a person is looking at this spec right now
    # and pressing Save, so stamp the confirmation from the authenticated editor
    # rather than making them fill in three fields by hand in a JSON textarea.
    #
    # ONLY when the families actually changed. Stamping on every save meant
    # editing a panel title re-attributed the facet confirmation to whoever made
    # that edit — recording that they reviewed a grouping they never looked at,
    # and quietly overwriting the name of the person who did. If the stored
    # confirmation already describes exactly these families, it stands.
    _sig = new_app.get("case_signature")
    if isinstance(_sig, dict) and (_sig.get("facets") or []):
        _fams = sorted({f.get("family") for f in _sig["facets"]
                        if isinstance(f, dict) and f.get("family")})
        _already = sorted({str(f) for f in (_sig.get("confirmed_families") or []) if f})
        if _fams and _fams != _already:
            _sig["confirmed_by"] = user_id
            _sig["confirmed_at"] = datetime.now(timezone.utc).isoformat()
            _sig["confirmed_families"] = _fams

    try:
        app_spec_obj, _ = validate_app_spec(new_app)
    except Exception as exc:  # noqa: BLE001 — surface the validation error to the editor
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"app_spec invalid: {exc}")

    agent_spec_obj = None
    if isinstance(new_agent, dict):
        # AgentSpec carries ``agent_id`` (preserve identity) but NOT ``tenant_id``
        # (extra-forbidden — tenant lives on the wrapper doc, see replace_one below).
        if app_doc.get("agent_id"):
            new_agent["agent_id"] = app_doc["agent_id"]
        new_agent.pop("tenant_id", None)
        try:
            agent_spec_obj, _ = validate_agent_spec(new_agent)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"agent_spec invalid: {exc}")

    # ── The SAME spec-shape rules /publish enforces ─────────────────────────
    #
    # `validate_app_spec` is Pydantic and nothing else, so until now this route
    # skipped every Layer-B rule. That made the hand-edit the one unguarded way
    # into an app: it could drop a case_signature (the exact failure CS-03
    # exists to refuse, and the only route that can still reach it), flip a
    # factor_set mode past FS-02, point a facet at a column the dataset does not
    # have, or wire a rule-mode check to a scored factor. The route's own
    # docstring claimed it "validates exactly like publish"; now it does.
    #
    # A RATCHET, not a gate: block what this edit INTRODUCES, allow what was
    # already broken. The hand-edit is the admin escape hatch for fixing an app,
    # so refusing it because of a pre-existing violation locks the door on the
    # way to the fix — dealer-limit-review already fails CS-02 on four families
    # whose columns no panel projects, and the way to fix that is an edit.
    #
    # Findings are keyed by (rule, code, location), so re-saving an untouched
    # violation passes and adding one more does not.
    _prev_spec = existing or {}

    def _rule_findings(_app, _agent, _prev) -> Dict[tuple, Dict[str, Any]]:
        out: Dict[tuple, Dict[str, Any]] = {}
        for _rule, _errs in (
            ("CS-01", validate_case_signature(_app)),
            ("CS-02", validate_case_signature_projection(_app)),
            ("CS-03", validate_case_signature_stable(_app, _prev)),
            ("CS-04", validate_case_signature_confirmed(_app)),
            ("FS-01", validate_factor_set(_app)),
            ("FS-02", validate_factor_set_mode_stable(_app, _prev)),
            ("FS-05", validate_rubric_finding_matches_declaration(_app)),
            ("FS-06", validate_factor_checks_can_score(_app, _agent)),
            ("M-01", validate_item_tools_declare_task_type(_agent)),
        ):
            for _e in (_errs or []):
                _d = _e if isinstance(_e, dict) else {"detail": str(_e)}
                # Key on the REASON, not the location. CS-01/CS-02 build their
                # location from the facet INDEX (`facets[2].from_column`), so a
                # swap in the same slot — fix one bad column, introduce a
                # different bad one — produced an identical key and the new
                # violation was waved through as pre-existing. Inserting a facet
                # shifted every later index and mis-attributed the rest. The
                # reason names the family and column, so it identifies the
                # violation itself and survives reordering.
                out[(_rule, _d.get("code"), _d.get("reason") or _d.get("location"))] = {
                    "rule": _rule, **_d}
        return out

    _now_bad = _rule_findings(app_spec_obj, agent_spec_obj, _prev_spec)
    _was_bad: Dict[tuple, Dict[str, Any]] = {}
    if _now_bad and _prev_spec:
        try:
            # Baseline against the spec being replaced. CS-03/FS-02 compare a
            # spec to its PREDECESSOR, so baselining the old spec against itself
            # is exactly right: unchanged ⇒ no finding ⇒ they still fire on a
            # real change.
            #
            # The AGENT baseline must be the STORED agent spec, not the incoming
            # one — FS-06 reads the agent, so baselining it against the new
            # agent made every agent-side violation look pre-existing and the
            # rule never fired. Only the app spec is edited far more often, so
            # this is easy to get wrong and silent when you do.
            _prev_agent_obj = None
            _prev_agent_doc = await agents.find_one(
                {"agent_id": app_doc.get("agent_id"),
                 "tenant_id": app_doc.get("tenant_id")}) if app_doc.get("agent_id") else None
            if (_prev_agent_doc or {}).get("agent_spec"):
                try:
                    _prev_agent_obj, _ = validate_agent_spec(
                        dict(_prev_agent_doc["agent_spec"]))
                except Exception:  # noqa: BLE001 — no agent baseline; app rules still ratchet
                    _prev_agent_obj = None
            _prev_obj, _ = validate_app_spec(dict(_prev_spec))
            _was_bad = _rule_findings(_prev_obj, _prev_agent_obj, _prev_spec)
        except Exception:  # noqa: BLE001 — an unparseable predecessor has no baseline
            logger.warning(
                "[spec-edit] %s: the stored spec does not validate, so every "
                "rule finding on this edit is treated as new", slug)

    _introduced = [v for k, v in _now_bad.items() if k not in _was_bad]
    if _introduced:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "spec_rules_failed",
                "message": ("this edit introduces failures of the same publish "
                            "rules a build must pass. Pre-existing ones are not "
                            "blocking; these are new. Fix and save again."),
                "errors": _introduced,
            },
        )
    if _now_bad:
        logger.warning(
            "[spec-edit] %s saved with %d PRE-EXISTING rule violation(s): %s",
            slug, len(_now_bad), sorted({f"{k[0]}:{k[1]}" for k in _now_bad}))

    # Snapshot the version we're superseding BEFORE mutating app_doc in place,
    # while the live agent doc still holds the old agent_spec.
    await _snapshot_prior_version(
        apps_col=apps,
        agents_col=agents,
        versions_col=get_spec_versions_col(),
        app_doc_before=app_doc,
        actor=user_id or "",
        reason="spec-edit",
        max_keep=get_settings().max_spec_versions,
    )

    now = datetime.now(timezone.utc)
    version = int(app_doc.get("version") or 0) + 1
    app_doc.pop("_id", None)
    app_doc["app_spec"] = app_spec_obj.model_dump(mode="json")
    app_doc["version"] = version
    app_doc["updated_at"] = now
    app_doc["grounded"] = bool(agent_spec_obj is not None and getattr(agent_spec_obj, "grounding", None))
    await apps.replace_one({"slug": slug}, app_doc, upsert=True)

    saved_agent = None
    if agent_spec_obj is not None and app_doc.get("agent_id"):
        await agents.replace_one(
            {"agent_id": app_doc["agent_id"], "tenant_id": app_doc.get("tenant_id")},
            {
                "agent_id": app_doc["agent_id"],
                "tenant_id": app_doc.get("tenant_id"),
                "version": version,
                "agent_spec": agent_spec_obj.model_dump(mode="json"),
                "updated_at": now,
            },
            upsert=True,
        )
        saved_agent = agent_spec_obj.model_dump(mode="json")

    # Same clean-up publish does. A hand-edit can rename or drop a facet family
    # exactly like a rebuild can, and before this it orphaned nothing — so the
    # app kept reporting clauses as live that could never match a case again.
    await _reconcile_case_signature(
        slug=slug, app_doc=app_doc, previous_app_doc={"app_spec": existing},
        actor=user_id or "spec-edit", origin="SPEC-EDIT",
    )

    logger.info("[spec-edit] %s saved by %s (v%d)", slug, user_id, version)
    return AppDetailResponse(app_spec=app_doc["app_spec"], agent_spec=saved_agent)


@app.post("/apps/{slug}/case-signature/confirm")
async def confirm_case_signature(slug: str, payload: dict, request: Request) -> Dict[str, Any]:
    """Record that a human reviewed this app's facet families (CS-04).

    Exists so confirming does not mean hand-editing a JSON blob. The UI shows
    the families in plain language and posts the list back; the SERVER decides
    who confirmed, from the token — a client-supplied identity would make the
    field worthless.

    The posted ``families`` must match what the app currently declares. That is
    not ceremony: it is what stops a stale screen certifying a spec that has
    moved underneath it, and it is the same comparison CS-04 makes at publish.
    """
    user_id = get_secure_user_id(request)
    _env = await _bind_app_env(slug)
    apps = get_apps_col()
    app_doc = await apps.find_one({"slug": slug})
    if not app_doc or not _can_render_app(app_doc, request):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="app not found")
    # Same guard the hand-edit save carries: a customer-embedded prod surface
    # is not edited in place, and this writes to app_spec like any other edit.
    if _env == "prod" and is_external_surface(app_doc.get("app_spec")):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=(f"'{slug}' is embedded in a customer's application, so its "
                    "live spec cannot be edited directly. Confirm on the TEST "
                    "app and promote it."),
        )
    if not _can_edit_app(
        app_doc, user_id, get_tenant_id(request),
        user_org_id=get_org_id(request),
        user_dept_ids=get_user_dept_ids(request),
        user_roles=get_user_roles(request),
        sa_admin_of=get_sa_admin_of(request),
    ):
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            detail="not authorised to edit this app")

    sig = (app_doc.get("app_spec") or {}).get("case_signature") or {}
    declared = sorted({f.get("family") for f in (sig.get("facets") or [])
                       if isinstance(f, dict) and f.get("family")})
    if not declared:
        raise HTTPException(status.HTTP_409_CONFLICT, detail={
            "code": "no_case_signature",
            "message": "this app declares no facet families, so there is "
                       "nothing to confirm",
        })

    posted = payload.get("families")
    if not isinstance(posted, list) or not posted:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail={
            "code": "families_required",
            "message": "body.families (the list you are confirming) is required",
        })
    if sorted({str(f) for f in posted if f}) != declared:
        raise HTTPException(status.HTTP_409_CONFLICT, detail={
            "code": "families_changed",
            "message": ("the app's facet families changed since this screen "
                        "loaded — reload and review the current list before "
                        "confirming."),
            "declared_families": declared,
        })

    # Snapshot + version bump, like every other write to app_spec. A bare
    # $set left the stored spec differing from every snapshot recorded for that
    # version, so two different spec contents shared one version number and the
    # confirmation appeared in no history — rollback would have restored a spec
    # that was never live.
    now = datetime.now(timezone.utc)
    await _snapshot_prior_version(
        apps_col=apps,
        agents_col=get_agents_col(),
        versions_col=get_spec_versions_col(),
        app_doc_before=app_doc,
        actor=user_id or "",
        reason="case-signature-confirm",
        max_keep=get_settings().max_spec_versions,
    )
    version = int(app_doc.get("version") or 0) + 1
    await apps.update_one({"slug": slug}, {"$set": {
        "app_spec.case_signature.confirmed_by": user_id,
        "app_spec.case_signature.confirmed_at": now,
        "app_spec.case_signature.confirmed_families": declared,
        "app_spec.version": version,
        "version": version,
        "updated_at": now,
    }})
    logger.info("[CS-04] %s confirmed %d facet famil(ies) on %s (v%d): %s",
                user_id, len(declared), slug, version, declared)
    return {"ok": True, "confirmed_by": user_id,
            "confirmed_at": now.isoformat(), "confirmed_families": declared}


# ── Spec version history + rollback ─────────────────────────────────────────


@app.get("/apps/{slug}/versions")
async def list_app_versions(slug: str, request: Request) -> Dict[str, Any]:
    """List the app's version history — the live version plus the retained
    snapshots (most recent first). Snapshots are roll-back targets; the live
    version is marked ``is_current`` and cannot be rolled back to itself.
    Edit-grade authz (same gate as editing the app). Env-routed by store."""
    user_id = get_secure_user_id(request)
    user_tenant = get_tenant_id(request)
    await _bind_app_env(slug)
    apps = get_apps_col()
    app_doc = await apps.find_one({"slug": slug})
    if not app_doc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="app not found")
    if not _can_edit_app(
        app_doc, user_id, user_tenant,
        user_org_id=get_org_id(request),
        user_dept_ids=get_user_dept_ids(request),
        user_roles=get_user_roles(request),
        sa_admin_of=get_sa_admin_of(request),
    ):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="not authorised to view this app's versions")

    current_version = int(app_doc.get("version") or 0)
    agent_version = None
    if app_doc.get("agent_id"):
        agent_doc = await get_agents_col().find_one(
            {"agent_id": app_doc["agent_id"]}, {"version": 1}
        )
        agent_version = (agent_doc or {}).get("version")

    versions = [
        _version_summary(
            version=current_version,
            app_spec=app_doc.get("app_spec") or {},
            agent_version=agent_version,
            is_current=True,
            snapshotted_at=app_doc.get("updated_at") or app_doc.get("deployed_at"),
            snapshotted_by="",
            reason="current",
            status=app_doc.get("status"),
        )
    ]
    snaps = [rev async for rev in get_spec_versions_col().find({"slug": slug})]
    snaps.sort(key=lambda r: int(r.get("version") or 0), reverse=True)
    for rev in snaps:
        versions.append(
            _version_summary(
                version=int(rev.get("version") or 0),
                app_spec=rev.get("app_spec") or {},
                agent_version=rev.get("agent_version"),
                is_current=False,
                snapshotted_at=rev.get("snapshotted_at"),
                snapshotted_by=rev.get("snapshotted_by") or "",
                reason=rev.get("reason") or "",
                status=rev.get("status"),
            )
        )
    return {
        "slug": slug,
        "current_version": current_version,
        "max_kept": get_settings().max_spec_versions,
        "versions": versions,
    }


class RollbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: Optional[str] = None


@app.post("/apps/{slug}/versions/{version}/rollback")
async def rollback_app_version(
    slug: str, version: int, request: Request,
    payload: RollbackRequest = RollbackRequest(),
) -> Dict[str, Any]:
    """Roll the app back to a retained snapshot. FORWARD-ONLY: the snapshot is
    re-validated and re-published as a NEW version (current + 1); the version
    counter never rewinds. The current live version is snapshotted first, so a
    rollback is itself reversible. AI triggers land DISABLED — a rollback must
    not silently re-arm autonomous writes (the officer re-activates in the
    Auto-Recommend panel). Edit-grade authz; env-routed by store."""
    user_id = get_secure_user_id(request)
    user_tenant = get_tenant_id(request)
    await _bind_app_env(slug)
    apps = get_apps_col()
    agents = get_agents_col()
    versions_col = get_spec_versions_col()

    app_doc = await apps.find_one({"slug": slug})
    if not app_doc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="app not found")
    if not _can_edit_app(
        app_doc, user_id, user_tenant,
        user_org_id=get_org_id(request),
        user_dept_ids=get_user_dept_ids(request),
        user_roles=get_user_roles(request),
        sa_admin_of=get_sa_admin_of(request),
    ):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="not authorised to roll back this app")

    current_version = int(app_doc.get("version") or 0)
    if version == current_version:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"version {version} is already the live version — nothing to roll back",
        )
    rev = await versions_col.find_one({"slug": slug, "version": version})
    if not rev:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=(
                f"version {version} is not in the retained history (only the last "
                f"{get_settings().max_spec_versions} versions are kept)"
            ),
        )

    # Re-validate the snapshot before restoring it — a spec valid N versions ago
    # may reference a since-deleted data source / tool / collection. Fail loud
    # (422) rather than re-publishing a spec that no longer loads.
    restored_app = dict(rev.get("app_spec") or {})
    # Identity is server-owned — restore against the LIVE app's identity so a
    # rollback can never re-slug / re-tenant / re-own / repoint the agent.
    existing_spec = app_doc.get("app_spec") or {}
    restored_app["slug"] = slug
    restored_app["tenant_id"] = app_doc.get("tenant_id") or existing_spec.get("tenant_id")
    if app_doc.get("agent_id"):
        restored_app["agent_id"] = app_doc["agent_id"]
    for k in ("owner_id", "owner_type"):
        if existing_spec.get(k) is not None:
            restored_app[k] = existing_spec[k]
    # AI triggers come back DISABLED — a restore must not re-start schedules /
    # webhooks without a fresh activation click (mirrors promote-to-prod).
    for _t in (restored_app.get("triggers") or []):
        if isinstance(_t, dict):
            _t["enabled"] = False
    try:
        app_spec_obj, _ = validate_app_spec(restored_app)
    except Exception as exc:  # noqa: BLE001 — surface to the caller
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"snapshot v{version} app_spec no longer valid: {exc}",
        )

    agent_spec_obj = None
    restored_agent = rev.get("agent_spec")
    if isinstance(restored_agent, dict) and app_doc.get("agent_id"):
        restored_agent = dict(restored_agent)
        restored_agent["agent_id"] = app_doc["agent_id"]
        restored_agent.pop("tenant_id", None)
        try:
            agent_spec_obj, _ = validate_agent_spec(restored_agent)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"snapshot v{version} agent_spec no longer valid: {exc}",
            )

    # Snapshot the CURRENT live version first, so this rollback is reversible.
    await _snapshot_prior_version(
        apps_col=apps,
        agents_col=agents,
        versions_col=versions_col,
        app_doc_before=app_doc,
        actor=user_id or "",
        reason=f"pre-rollback-to-v{version}",
        max_keep=get_settings().max_spec_versions,
    )

    now = datetime.now(timezone.utc)
    new_version = current_version + 1
    app_doc.pop("_id", None)
    app_doc["app_spec"] = app_spec_obj.model_dump(mode="json")
    app_doc["version"] = new_version
    app_doc["updated_at"] = now
    app_doc["grounded"] = bool(
        agent_spec_obj is not None and getattr(agent_spec_obj, "grounding", None)
    )
    app_doc.setdefault("lifecycle_audit", [])
    app_doc["lifecycle_audit"].append(
        _lifecycle_audit_entry(
            _claims(request), "rollback",
            from_version=current_version, to_version=version,
            new_version=new_version, reason=payload.reason or "",
        )
    )
    await apps.replace_one({"slug": slug}, app_doc, upsert=True)

    if agent_spec_obj is not None and app_doc.get("agent_id"):
        await agents.replace_one(
            {"agent_id": app_doc["agent_id"], "tenant_id": app_doc.get("tenant_id")},
            {
                "agent_id": app_doc["agent_id"],
                "tenant_id": app_doc.get("tenant_id"),
                "version": new_version,
                "agent_spec": agent_spec_obj.model_dump(mode="json"),
                "updated_at": now,
            },
            upsert=True,
        )

    logger.info(
        "[spec-rollback] %s rolled back to v%d → published as v%d by %s",
        slug, version, new_version, user_id,
    )
    return {
        "ok": True,
        "slug": slug,
        "rolled_back_to": version,
        "new_version": new_version,
        "triggers_disabled": True,
    }


# ── Spec REVIEW / lint ──────────────────────────────────────────────────────
# Advisory analysis for an IT/admin reviewer: correctness + performance smells.
# It is strictly READ-ONLY — it never rewrites or reformats the spec; it returns
# findings (severity + message + JSON path) that sit ALONGSIDE the spec so the
# reviewer locates and fixes the issue by hand via the edit pane.

# Field-name fragments that usually denote a DATA-DERIVED dimension (people,
# crews, locations, growing id spaces). A static option list for one of these
# tends to drift — used only for a gentle INFO nudge, never an error.
_DYNAMIC_FIELD_HINTS = (
    "assign", "crew", "team", "officer", "owner", "_je", "je_",
    "location", "zone", "region", "feeder", "_dt", "dt_", "consumer",
    "meter", "dealer", "vendor", "operator", "staff",
)


def _os_get(osrc: Any, key: str, default: Any = None) -> Any:
    """Read a key from an OptionsSource that may be a dict (form-panel schema)
    or a Pydantic object (FieldSpec.options)."""
    if osrc is None:
        return default
    if isinstance(osrc, dict):
        return osrc.get(key, default)
    return getattr(osrc, key, default)


def _collect_option_fields(app_spec: "AppSpec", agent_spec: Any) -> List[Dict[str, Any]]:
    """Every select/lookup OptionsSource in the spec, with a human JSON path.
    Covers FORM-panel fields (schema_inline / agent.input_schema) and agent
    override fields (editable_fields). The linter + live-cardinality probe both
    consume this."""
    out: List[Dict[str, Any]] = []
    for p in (getattr(app_spec, "all_panels", None) or []):
        pid = getattr(p, "id", "?")
        schema = getattr(p, "schema_inline", None)
        if not schema and getattr(p, "schema_ref", None) == "agent.input_schema" and agent_spec is not None:
            schema = getattr(agent_spec, "input_schema", None)
        for fname, fdef in (((schema or {}).get("properties") or {}).items()):
            osrc = (fdef or {}).get("options_source")
            if osrc:
                out.append({"path": f"panels[{pid}].fields.{fname}", "field": fname, "osrc": osrc})
    if agent_spec is not None:
        carriers = list(getattr(agent_spec, "tools_v2", None) or []) + list(getattr(agent_spec, "actions", None) or [])
        for t in carriers:
            tid = getattr(t, "name", None) or getattr(t, "id", None) or "?"
            for ef in (getattr(t, "editable_fields", None) or []):
                osrc = getattr(ef, "options", None)
                if osrc is not None:
                    out.append({
                        "path": f"actions[{tid}].editable_fields.{getattr(ef, 'name', '?')}",
                        "field": getattr(ef, "name", "?"), "osrc": osrc,
                    })
    return out


def _lint_app_spec(app_spec: "AppSpec", agent_spec: Any) -> List[Dict[str, Any]]:
    """STATIC review of a spec for correctness + performance smells. Pure (no
    I/O) so it is fast + unit-testable; the endpoint layers live cardinality on
    top. Returns findings — never mutates the spec."""
    findings: List[Dict[str, Any]] = []

    def add(severity: str, code: str, path: str, message: str, hint: str) -> None:
        findings.append({"severity": severity, "code": code, "path": path, "message": message, "hint": hint})

    declared = {d.id for d in (getattr(app_spec, "data_sources", None) or [])}

    # 1) Data-source scope — an mcp source with no filters CAN scan the whole
    # table, but only matters where it is actually rendered in bulk. Classify by
    # how the source is consumed so this doesn't false-alarm on lookup tables the
    # agent reads by key (bounded) or sources used only for option combos
    # (bounded by limit). Row-rendering panels → warning; chart aggregation →
    # gentle info; agent/option-only → no finding.
    _ROW_PANELS = {"queue", "calendar", "map", "document_view"}
    panel_use: Dict[str, set] = {}
    for p in (getattr(app_spec, "all_panels", None) or []):
        pds = getattr(p, "data_source", None)
        if pds:
            panel_use.setdefault(pds, set()).add(getattr(p, "type", None))
    for d in (getattr(app_spec, "data_sources", None) or []):
        if getattr(d, "type", None) != "mcp" or (getattr(d, "filters", None) or {}):
            continue
        uses = panel_use.get(d.id, set())
        if uses & _ROW_PANELS:
            add("warning", "unfiltered_source", f"data_sources[{d.id}].filters",
                f"Data source '{d.id}' feeds a list/table panel with no filters — it reads the full table on every load.",
                "Add equality filters to narrow rows (status, region, a date window).")
        elif "chart" in uses:
            add("info", "unfiltered_source_chart", f"data_sources[{d.id}].filters",
                f"Data source '{d.id}' aggregates the full table for a chart — fine if intended, but unbounded as data grows.",
                "Add a time window or scope filter if the chart only needs recent / partitioned rows.")

    # 2) Panels pointing at an undeclared data source (breaks / silently empty).
    for p in (getattr(app_spec, "all_panels", None) or []):
        ds = getattr(p, "data_source", None)
        if ds and ds not in declared:
            add("error", "unknown_data_source", f"panels[{getattr(p, 'id', '?')}].data_source",
                f"Panel '{getattr(p, 'id', '?')}' references data source '{ds}' which is not declared.",
                "Add it to data_sources[] or fix the reference.")

    # 3) Option fields — static drift, oversized static lists, unbounded combos.
    for of in _collect_option_fields(app_spec, agent_spec):
        osrc, path, fname = of["osrc"], of["path"], of["field"]
        kind = _os_get(osrc, "kind", "static")
        if kind == "static":
            vals = _os_get(osrc, "values") or []
            if len(vals) > 25:
                add("warning", "large_static_options", path,
                    f"Field '{fname}' has {len(vals)} hardcoded options — large static lists drift and bloat the spec.",
                    "If these come from data, switch options_source.kind to 'data_source' so they stay live.")
            if any(h in fname.lower() for h in _DYNAMIC_FIELD_HINTS):
                add("info", "static_dynamic_candidate", path,
                    f"Field '{fname}' looks data-derived (people / crews / locations) but uses a static list.",
                    "Consider kind='data_source' so choices stay live — no daily drift, no rebuild.")
        elif kind == "data_source":
            ds = _os_get(osrc, "data_source")
            if ds and ds not in declared:
                add("error", "unknown_data_source", path,
                    f"Field '{fname}' options point at data source '{ds}' which is not declared.",
                    "Add it to data_sources[] or fix the reference.")
            limit = _os_get(osrc, "limit") or 200
            if limit >= 500 and not _os_get(osrc, "search"):
                add("warning", "high_limit_no_typeahead", path,
                    f"Field '{fname}' loads up to {limit} options with no typeahead — a slow, unwieldy combo.",
                    "Enable typeahead (options_source.search) so it queries on demand, or lower the limit.")

    # 4) Grounding cost — re-embedding history on every outcome.
    op = getattr(agent_spec, "outcome_poll", None) if agent_spec is not None else None
    if op is not None and getattr(op, "enabled", False) and getattr(op, "auto_refresh", False):
        add("info", "grounding_auto_refresh", "outcome_poll.auto_refresh",
            "Grounding auto-refreshes on every outcome — each refresh re-embeds history (cost + latency).",
            "If outcomes are frequent, set auto_refresh=false and refresh manually or on a schedule.")

    # 5) Officer-overridable enum fields without explicit options. The runtime
    # derives a static select from the tool's input_schema enum (so the combo
    # still renders), but an explicit list is better authoring: custom labels,
    # deliberate subsets, and no reliance on the fallback. Catches hand-authored
    # specs — the exact prod miss on update_inspection_status.status.
    for t in (getattr(agent_spec, "tools_v2", None) or []) if agent_spec is not None else []:
        if getattr(t, "kind", None) != "mcp_action":
            continue
        tid = getattr(t, "action_id", None) or getattr(t, "id", "?")
        props = ((getattr(t, "input_schema", None) or {}).get("properties") or {})
        for ef in (getattr(t, "editable_fields", None) or []):
            fname = getattr(ef, "name", None)
            if not fname or getattr(ef, "options", None) or getattr(ef, "control", None):
                continue
            enum = (props.get(fname) or {}).get("enum")
            if isinstance(enum, list) and enum:
                add("warning", "enum_field_missing_options",
                    f"actions[{tid}].editable_fields.{fname}",
                    f"Editable field '{fname}' is an enum {enum} in the action's input_schema but "
                    "declares no options — the override combo will be auto-derived from the enum.",
                    "Declare control='select' + a static options list mirroring the enum "
                    "(lets you control labels and offer a deliberate subset).")

    return findings


@app.get("/apps/{slug}/spec/lint")
async def lint_app_spec_endpoint(
    slug: str,
    request: Request,
    live: bool = False,
    settings: Settings = Depends(get_settings),
) -> Dict[str, Any]:
    """Advisory REVIEW of a published spec (correctness + performance) for an
    IT/admin reviewer. READ-ONLY — never modifies the spec. ``live=true`` adds
    REAL distinct-value cardinality for data_source option fields, resolved via
    the same path the runtime uses (resolve_field_options). Same authz as edit.
    """
    user_id = get_secure_user_id(request)
    user_tenant = get_tenant_id(request)
    await _bind_app_env(slug)
    app_doc = await get_apps_col().find_one({"slug": slug})
    if not app_doc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="app not found")
    if not _can_edit_app(
        app_doc, user_id, user_tenant,
        user_org_id=get_org_id(request),
        user_dept_ids=get_user_dept_ids(request),
        user_roles=get_user_roles(request),
        sa_admin_of=get_sa_admin_of(request),
    ):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="not authorised to review this app")

    app_spec = _load_app_spec(app_doc)
    agent_spec = None
    if app_doc.get("agent_id"):
        agent_doc = await get_agents_col().find_one(
            {"agent_id": app_doc["agent_id"], "tenant_id": app_doc.get("tenant_id")}
        )
        if agent_doc:
            agent_spec = AgentSpec.model_validate(agent_doc["agent_spec"])

    findings = _lint_app_spec(app_spec, agent_spec)

    live_error = None
    if live:
        try:
            auth_header = request.headers.get("authorization") or request.headers.get("Authorization")
            by_id = {d.id: d for d in (app_spec.data_sources or [])}
            for of in _collect_option_fields(app_spec, agent_spec):
                osrc = of["osrc"]
                if _os_get(osrc, "kind") != "data_source":
                    continue
                ds = by_id.get(_os_get(osrc, "data_source"))
                vcol = _os_get(osrc, "value_column")
                if ds is None or not vcol:
                    continue
                filt = _os_get(osrc, "filter")
                # An interpolated filter (${record.*}/${param.*}) needs runtime
                # context we don't have here — skip the probe (not a failure).
                if isinstance(filt, dict) and "${" in json.dumps(filt):
                    continue
                resolved = await resolve_field_options(
                    settings=settings, ds=ds, value_column=vcol,
                    label_column=_os_get(osrc, "label_column"),
                    field_filter=filt, limit=1000, auth_header=auth_header, search=None,
                )
                n = len(resolved or [])
                if n >= 50 and not _os_get(osrc, "search"):
                    findings.append({
                        "severity": "warning", "code": "high_cardinality_no_typeahead",
                        "path": of["path"],
                        "message": f"Field '{of['field']}' resolves {n}+ live distinct values with no typeahead — a heavy combo.",
                        "hint": "Enable typeahead (options_source.search) so users search instead of loading all.",
                    })
        except Exception as exc:  # noqa: BLE001 — surface, never swallow (RULE #1)
            logger.warning("[spec-lint] live cardinality probe failed for %s: %s", slug, exc)
            live_error = str(exc)

    counts: Dict[str, int] = {}
    for f in findings:
        counts[f["severity"]] = counts.get(f["severity"], 0) + 1
    return {"slug": slug, "findings": findings, "counts": counts, "live": bool(live), "live_error": live_error}


@app.delete("/apps/{slug}", response_model=SuccessResponse)
async def archive_app(slug: str, request: Request) -> SuccessResponse:
    user_id = get_secure_user_id(request)
    user_tenant = get_tenant_id(request)
    # Resolve test↔prod by store before any collection access (see _bind_app_env).
    # Without this the accessor defaults to the prod collection, so archiving a
    # TEST-store app 404s ("app not found") even though Open/Edit (which DO bind)
    # work — mirror get_app at /apps/{slug}.
    await _bind_app_env(slug)
    apps = get_apps_col()
    app_doc = await apps.find_one({"slug": slug})
    if not app_doc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="app not found")
    if not _can_edit_app(
        app_doc, user_id, user_tenant,
        user_org_id=get_org_id(request),
        user_dept_ids=get_user_dept_ids(request),
        user_roles=get_user_roles(request),
        sa_admin_of=get_sa_admin_of(request),
    ):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="not authorised to delete this app")

    # Tenant-scope the UPDATE so a leaked slug cannot be used to archive
    # an app belonging to another tenant via a same-name collision.
    res = await apps.update_one(
        {"slug": slug, "tenant_id": app_doc.get("tenant_id")},
        {"$set": {"status": AppStatus.ARCHIVED.value}},
    )
    if res.matched_count == 0:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="app not found")
    return SuccessResponse(message=f"archived {slug}")


@app.post("/apps/{slug}/runtime/token", response_model=RuntimeTokenResponse)
async def mint_runtime_token(
    slug: str,
    request: Request,
    settings: Settings = Depends(get_settings),
) -> RuntimeTokenResponse:
    """Mint a short-lived HMAC bearer the runtime engine uses to call the
    smart-app internal proxy on behalf of this published app.

    Trust model:
    - The caller authenticates with their normal user JWT (or a builder
      sandbox JWT) — same auth surface as ``GET /apps/{slug}``.
    - The minted bearer's ``tools`` claim is the **intersection** of
      tools declared in the app's published AgentSpec.tools_v2 with the
      tools this deployment actually has wired up. The proxy then
      enforces per-call that the bearer's claim authorises the requested
      tool, so a runtime can never escalate beyond what the BA published.
    - The minted bearer is short-lived (default 1 hour). Runtime engines
      should refresh on 401, not cache.
    """
    user_id = get_secure_user_id(request)
    user_tenant = get_tenant_id(request)
    # Resolve test↔prod by store before any collection access — the runtime
    # must be able to mint a token for a TEST app (in the test_ collections) to
    # open it in preview, not just prod apps (see _bind_app_env).
    await _bind_app_env(slug)
    apps_col = get_apps_col()
    agents_col = get_agents_col()

    app_doc = await apps_col.find_one({"slug": slug})
    if not app_doc or not _user_can_access(app_doc, request):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="app not found")

    agent_doc = await agents_col.find_one(
        {
            "agent_id": app_doc["agent_id"],
            "tenant_id": app_doc.get("tenant_id"),
        }
    )
    if not agent_doc:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="agent spec missing for app",
        )

    # Derive the tool scope from the published AgentSpec, intersected
    # with what this deployment actually supports. A BA can declare
    # vision_ocr in dev; if prod has OCR_ENABLED=false the runtime token
    # simply won't carry that scope and OCR calls will 403 at the proxy.
    declared_kinds: List[str] = []
    spec = agent_doc.get("agent_spec", {}) or {}
    tools_v2 = spec.get("tools_v2") or []
    for tool in tools_v2:
        kind = tool.get("kind") if isinstance(tool, dict) else None
        if kind and kind not in declared_kinds:
            declared_kinds.append(kind)

    granted_tools: List[str] = []
    for kind in declared_kinds:
        if kind in ("vision_ocr", "image_analyze", "doc_extract") and not settings.ocr_enabled:
            logger.warning(
                "runtime token: dropping declared tool kind %s - vision proxy "
                "not configured (VISION_* unset; no LLM_LARGE_* fallback)",
                kind,
            )
            continue
        if kind == "mcp" and not settings.mcp_enabled:
            logger.warning(
                "runtime token: dropping declared tool kind mcp - MCP proxy disabled"
            )
            continue
        if kind == "rag" and not settings.rag_enabled:
            logger.warning(
                "runtime token: dropping declared tool kind rag - RAG proxy disabled"
            )
            continue
        granted_tools.append(kind)

    # Build per-kind bindings from tools_v2 so the proxy can authorise
    # individual (source_id, tool_name) calls without a DB round trip on
    # the hot path.
    bindings: Dict[str, List[Any]] = {}
    for tool in tools_v2:
        if not isinstance(tool, dict):
            continue
        k = tool.get("kind")
        if k == "mcp" and "mcp" in granted_tools:
            sid = tool.get("source_id")
            tname = tool.get("tool_name")
            if sid:
                bindings.setdefault("mcp", []).append([sid, tname])
        elif k == "rag" and "rag" in granted_tools:
            sid = tool.get("source_id")
            if sid and sid not in bindings.setdefault("rag", []):
                bindings["rag"].append(sid)

    from internal_bearer import mint_internal_bearer

    secret = mint_internal_bearer(
        signing_key=settings.internal_signing_key,
        kind="runtime",
        subject=f"app:{slug}",
        tenant_id=user_tenant,
        tools=granted_tools,
        ttl_seconds=settings.internal_bearer_ttl_seconds,
        bindings=bindings,
    )
    expires_at = int(time.time()) + settings.internal_bearer_ttl_seconds
    proxy_base_url = (
        settings.smart_app_service_callback_url.rstrip("/")
        + "/smart-app/internal"
    )
    return RuntimeTokenResponse(
        secret=secret,
        proxy_base_url=proxy_base_url,
        expires_at=expires_at,
        tools=granted_tools,
    )


# ---------------------------------------------------------------------------
# Embeddable decision UI — spec resolution + export
# See docs/embeddable-decision-ui-plan.md
# ---------------------------------------------------------------------------


def _embed_record_contract(app_spec: AppSpec) -> Optional[Dict[str, str]]:
    """What `recordId` must actually BE, for the developer wiring the card up.

    ``mount({ recordId })`` is the one value the host supplies, and until now
    nothing told the integrator which identifier it wants. A bank's loan screen
    has an application id, a customer id, an account number and a branch code on
    it; pass the wrong one and the card resolves no record and renders empty.
    Nothing in the snippet, the API or the error says which was expected, so the
    integrator's only move is to guess or to ask us.

    The contract is already in the spec — the embed page's detail panel names
    ``id_field``, and its data source names the dataset — so this just makes it
    visible instead of implicit.

    Returns None when it cannot be determined (no embed page, no detail panel,
    or no id_field). Callers omit the field rather than publishing a guess: a
    WRONG contract is worse than an absent one, because the developer would
    trust it.
    """
    page = next((p for p in (app_spec.pages or [])
                 if getattr(p, "kind", None) == "embed"), None)
    if page is None:
        return None
    detail = next((p for p in (page.panels or [])
                   if getattr(p, "type", None) == "detail"), None)
    if detail is None or not getattr(detail, "id_field", None):
        return None
    ds_id = getattr(detail, "data_source", None) or getattr(detail, "linked_to", None)
    dataset = None
    for ds in (app_spec.data_sources or []):
        if ds.id == ds_id:
            dataset = getattr(ds, "ref", None)
            break
    out = {"key_field": detail.id_field}
    if dataset:
        out["dataset"] = dataset
    return out


def _embed_snippet(
    *, embed_key: str, script_url: str,
    contract: Optional[Dict[str, str]] = None,
) -> str:
    """The copy-paste handoff. Kept SERVER-side, not templated in Citra-UI,
    because only this service knows the runtime's public origin, the bundle
    version it serves, and whether the app actually has an embed page."""
    # Name the identifier INLINE, on the line the developer has to edit. A
    # contract documented anywhere else is a contract they will not read.
    if contract:
        where = f" (from {contract['dataset']})" if contract.get("dataset") else ""
        record_line = (
            f"    // recordId must be the {contract['key_field']}"
            f"{where}\n"
            f"    recordId: yourApp.currentRecordId(),   "
            f"// e.g. the row's {contract['key_field']}\n"
        )
    else:
        record_line = "    recordId: yourApp.currentRecordId(),\n"
    return (
        '<div id="citra-decision"></div>\n'
        f'<script src="{script_url}"></script>\n'
        "<script>\n"
        "  const citra = Citra.init({ getToken: () => yourApp.citraToken() });\n"
        "  citra.mount('#citra-decision', {\n"
        f"    embed:    '{embed_key}',\n"
        f"{record_line}"
        "    onDecision: (d) => yourApp.onCitraDecision(d),\n"
        "  });\n"
        "</script>"
    )


@app.get("/embed/{embed_key}/spec", response_model=EmbedSpecResponse)
async def get_embed_spec(embed_key: str, request: Request) -> EmbedSpecResponse:
    """Resolve an embed key to the spec citra.js renders.

    The key's PREFIX carries the environment (`emb_test_` / `emb_live_`), so a
    customer's UAT page and production page can point at different environments
    at the same time — which slug-based resolution cannot express, since promote
    copies test→prod and the slug then always resolves to prod.

    Authorisation is unchanged: the officer's own JWT, checked against the app's
    audience exactly as opening the app in Citra would. The key names WHICH app
    and WHICH environment; it grants nothing by itself.
    """
    env = env_for_key(embed_key)
    if env is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail="not an embed key (expected an emb_test_/emb_live_ prefix)",
        )
    set_current_env(env)
    app_doc = await get_apps_col().find_one({"embed_key": embed_key})
    # 404 for "no such key" AND "not yours" — an embed key is public (it lives in
    # the host page's source), so distinguishing the two would let anyone probe
    # which keys exist.
    if not app_doc or not _can_render_app(app_doc, request):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="embed not found")
    if app_doc.get("status") == AppStatus.ARCHIVED.value:
        raise HTTPException(status.HTTP_410_GONE, detail="embed is archived")

    app_spec = _load_app_spec(app_doc)
    agent_doc = await get_agents_col().find_one(
        {"agent_id": app_doc.get("agent_id"), "tenant_id": app_doc.get("tenant_id")}
    )
    embed_page = next(
        (p for p in (app_spec.pages or []) if getattr(p, "kind", "") == "embed"),
        None,
    )
    if embed_page is None:
        # The key resolves, but this app has no embed page — so there is nothing
        # for citra.js to render. Left alone, `page_id=None` is returned as a
        # 200 and the customer's page shows a blank card with no explanation;
        # they blame their integration rather than the app.
        #
        # This is reachable in practice. An embed key is PRESERVED across
        # republishes (a changing key would mean re-pasting the snippet every
        # release), and `is_external_surface` covers headless too — so
        # republishing the same slug as a headless Decision API keeps the key
        # while removing the page it was minted for. Observed exactly that way.
        #
        # /apps/{slug}/embed/snippet already refuses this case; the surface that
        # actually RENDERS must refuse it too, or the guard only covers the
        # developer who copies the snippet and not the one who already has it.
        logger.warning(
            "[embed] key %r resolves to app %r which has no embed page "
            "(headless=%s) — refusing rather than serving a blank card",
            embed_key, app_doc.get("slug"), getattr(app_spec, "headless", None),
        )
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=(
                f"app '{app_doc.get('slug')}' no longer has an embed page, so "
                "this key cannot render a card. It was most likely republished "
                "as a different surface. Re-publish it with an embed page, or "
                "remove the embed snippet from your page."
            ),
        )
    return EmbedSpecResponse(
        slug=app_doc["slug"],
        app_spec=app_spec,
        agent_spec=(
            AgentSpec.model_validate(agent_doc["agent_spec"]) if agent_doc else None
        ),
        page_id=getattr(embed_page, "id", None),
        environment=env,  # type: ignore[arg-type]
    )


@app.get("/apps/{slug}/embed/snippet", response_model=EmbedSnippetResponse)
async def get_embed_snippet(
    slug: str,
    request: Request,
    settings: Settings = Depends(get_settings),
) -> EmbedSnippetResponse:
    """The Export action behind the My Apps card.

    Returns ~10 lines of text with the embed key and script URL already filled
    in — the developer never looks anything up. Nothing is compiled: `citra.js`
    is one artefact built once in CI for every app, and the per-app part is this
    snippet.
    """
    env = await _bind_app_env(slug)
    app_doc = await get_apps_col().find_one({"slug": slug})
    if not app_doc or not _user_can_access(app_doc, request):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="app not found")

    app_spec = _load_app_spec(app_doc)
    if not is_external_surface(app_spec):
        # Fail loud with the fix. Handing back a snippet for an app that has no
        # embed page would render an empty card in a customer's production
        # screen, and they would blame the integration rather than the missing
        # page.
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=(
                f"app '{slug}' has no embed page, so there is nothing to embed. "
                "Rebuild it with an embed page (the builder's surface question, "
                "or citra-embed-spec) and publish again."
            ),
        )
    embed_key = app_doc.get("embed_key")
    if not embed_key:
        # Published before embed keys existed, or promoted without one.
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=(
                f"app '{slug}' has an embed page but no embed key — republish it "
                "so one is minted."
            ),
        )

    script_url = f"{settings.apps_base_url.rstrip('/')}/v1/citra.js"
    contract = _embed_record_contract(app_spec)
    return EmbedSnippetResponse(
        embed_key=embed_key,
        script_url=script_url,
        environment=env,  # type: ignore[arg-type]
        snippet=_embed_snippet(
            embed_key=embed_key, script_url=script_url, contract=contract,
        ),
        record_contract=contract,
    )


# ---------------------------------------------------------------------------
# Build session helpers
# ---------------------------------------------------------------------------


def _surface_note(
    *, primary_page_kind: Optional[str], build_headless: bool,
) -> Optional[str]:
    """The BA's surface pick, phrased for the builder's first turn.

    Returns None when there is nothing unambiguous to say — and that case is the
    reason this is a function rather than an f-string. ``primary_page_kind``
    arrives as ``"standard"`` for BOTH an explicit "App" pick and the
    "Let's talk it through" option, because ``_builder_env`` defaults an absent
    pick to ``"standard"``. Announcing "the BA picked App" would silently kill
    the one option whose entire purpose is to be asked, so `standard` says
    nothing and AGENTS.md keeps asking there.

    Marked as CONTEXT, not phrased as the BA. The build transcript is an audit
    artefact; writing "I want an embedded card" as though the human typed it
    would put a claim in their mouth that survives even if they switch surface
    two turns later.
    """
    if build_headless:
        return (
            "[Citra context — not typed by the BA: they chose **Decision API "
            "(headless)** in the surface picker. Confirm briefly, do not re-ask "
            "the surface question. Author a headless AppSpec: no panels, no "
            "pages. See AGENTS.md → headless build.]"
        )
    if primary_page_kind == "embed":
        return (
            "[Citra context — not typed by the BA: they chose **Embedded card** "
            "in the surface picker. Confirm briefly, do not re-ask the surface "
            "question. Author the primary page as `kind=\"embed\"` per "
            "`citra-embed-spec`: ONE detail panel carrying the record and the "
            "trigger (`detail.actions[].agent_action`), no queue.]"
        )
    if primary_page_kind == "dashboard":
        return (
            "[Citra context — not typed by the BA: they chose **Dashboard** in "
            "the surface picker. Confirm briefly, do not re-ask the surface "
            "question. Author the primary page as `kind=\"dashboard\"` per "
            "`citra-dashboard-spec` (KPI + chart panels + a narrator agent).]"
        )
    return None


async def _take_surface_note(sessions: AsyncIOMotorCollection, session_id: str) -> Optional[str]:
    """Pop the surface note — at most once per session.

    find_one_and_update so two turns racing (the BA sends again while the first
    is still in flight) cannot both claim it and prefix the note twice.
    """
    doc = await sessions.find_one_and_update(
        {"session_id": session_id, "surface_note": {"$nin": [None, ""]}},
        {"$set": {"surface_note": None}},
    )
    return (doc or {}).get("surface_note")


def get_build_sessions_col() -> AsyncIOMotorCollection:
    if _build_sessions_col is None:
        raise RuntimeError("Database not initialised")
    return _route_col(_build_sessions_col, get_settings().build_sessions_collection)


def _builder_env(
    *,
    settings: Settings,
    session_id: str,
    goal: Optional[str] = None,
    tenant_id: Optional[str],
    owner: Optional[str],
    org_id: Optional[str] = None,
    dept_ids: Optional[list] = None,
    roles: Optional[list] = None,
    seed_app_spec: Optional[dict] = None,
    seed_agent_spec: Optional[dict] = None,
    author_email: Optional[str] = None,
    build_kind: str = "app",
    build_kinds: Optional[list] = None,
    primary_page_kind: Optional[str] = None,
    build_headless: bool = False,
) -> dict:
    """Environment passed to the ephemeral builder pod.

    The pod's AGENTS.md (mounted at /workspace/AGENTS.md) reads these
    to know where to call discovery, where to publish, and which session
    it belongs to.
    """
    import json as _json
    # Fail fast if the large-LLM is not configured. smart-app-service
    # itself relays LLM calls on behalf of the builder pod (the pod
    # never sees the raw OpenRouter / inference-service key); without
    # these, neither the builder skills nor the runtime engine can do
    # any reasoning.
    missing = [
        name for name, val in (
            ("LLM_LARGE_BASE_URL", settings.llm_large_base_url),
            ("LLM_LARGE_API_KEY", settings.llm_large_api_key),
            ("LLM_LARGE_MODEL", settings.llm_large_model),
        ) if not val
    ]
    if missing:
        raise RuntimeError(
            "Smart-App-Service builder LLM is not configured. Set the "
            f"following in smart-app-service .env: {', '.join(missing)}"
        )

    # ----- Internal-proxy bearer minted FIRST so it can double as the
    # ----- "API key" the builder pod's OpenAI SDK presents to our LLM
    # ----- proxy (Authorization: Bearer <hmac>). The same bearer also
    # ----- gates the OCR / MCP / RAG / workflow proxies declared below.
    from internal_bearer import mint_internal_bearer

    builder_tools = ["llm", "validate_form"]
    if settings.ocr_enabled:
        builder_tools.append("vision_ocr")
    if settings.mcp_enabled:
        builder_tools.append("mcp")
    if settings.rag_enabled:
        builder_tools.append("rag")

    builder_internal_bearer = mint_internal_bearer(
        signing_key=settings.internal_signing_key,
        kind="builder",
        subject=f"build:{session_id}",
        tenant_id=tenant_id,
        tools=builder_tools,
        ttl_seconds=settings.internal_bearer_ttl_seconds,
    )

    proxy_base_url = (
        settings.smart_app_service_callback_url.rstrip("/")
        + "/smart-app/internal"
    )
    # OpenAI SDK appends /chat/completions to the base_url, so we hand
    # the pod ``.../internal/llm/v1`` and the path resolves correctly.
    llm_proxy_base_url = proxy_base_url + "/llm/v1"

    env = {
        "BUILD_SESSION_ID": session_id,
        # No BUILD_GOAL here — the builder is a conversational agent that
        # discovers intent from the first chat turn, not a pre-supplied
        # goal string. It is set below only when a goal was actually passed
        # (e.g. the edit flow seeds a synthetic "Edit existing X" goal).
        "BUILD_KIND": build_kind,
        "BUILD_KINDS": ",".join(build_kinds or [build_kind]),
        # Purpose of the primary page. 'dashboard' tells AGENTS.md to author
        # the app's main page as a dashboard page (KPI/chart + hero brief).
        "BUILD_PRIMARY_PAGE_KIND": primary_page_kind or "standard",
        # Headless (decision-API) build: AGENTS.md skips the UI-design phases
        # and authors a headless:true stub AppSpec (no panels/pages).
        "BUILD_HEADLESS": "true" if build_headless else "false",
        # Builder is always "test" when a test env is configured (set on the
        # build-session handler); current_env() resolves test→test discovery,
        # else prod (legacy) — see _resolve_build_env.
        "DISCOVERY_SERVICE_URL": _pod_reachable_url(settings.discovery_url_for(current_env())),
        "SMART_APP_SERVICE_URL": settings.smart_app_service_callback_url,
        # ----- Large LLM injection (proxy, NOT raw OpenRouter creds) -----
        # The pod's OpenAI SDK is pointed at smart-app-service's internal
        # LLM proxy with the HMAC bearer as its "API key". The proxy
        # validates the bearer, enforces the model allowlist + per-session
        # call/token budget, then relays to LLM_LARGE_BASE_URL using this
        # service's own credential. Result: the builder pod can craft any
        # prompt the BA asks for and still cannot exfiltrate the
        # OpenRouter key (it never has it). When on-prem GPU lands, point
        # this service's LLM_LARGE_BASE_URL at inference-service and
        # nothing in the pod changes.
        "LLM_LARGE_BASE_URL": llm_proxy_base_url,
        "LLM_LARGE_API_KEY": builder_internal_bearer,
        # Builder-only model override (settings.builder_llm_model) wins
        # when set — lets the builder run on a top-tier reasoning model
        # without dragging the runtime onto the same expensive tier.
        # Falls back to settings.llm_large_model for back-compat.
        "LLM_LARGE_MODEL": settings.builder_llm_model or settings.llm_large_model,
        "LLM_BASE_URL": llm_proxy_base_url,
        "LLM_API_KEY": builder_internal_bearer,
        "LLM_MODEL": settings.builder_llm_model or settings.llm_large_model,
        "LLM_CONTEXT_WINDOW": str(settings.llm_context_window),
        "LLM_MAX_OUTPUT_TOKENS": str(settings.llm_max_output_tokens),
        # CITRA_JWT is the scoped builder token used by the
        # `citra-app-publish` skill. Issued per build session,
        # scope=smart-app-builder, short-lived. The smart-app-service
        # auth middleware (`require_publish_scope`) validates it.
        "CITRA_JWT": _mint_builder_token(
            settings=settings,
            session_id=session_id,
            owner=owner,
            tenant_id=tenant_id,
            org_id=org_id,
            dept_ids=dept_ids,
            roles=roles,
            author_email=author_email,
        ),
    }

    # ----- Tool catalogue advertised to the pod's `citra-tool-catalogue`
    # ----- skill. SMART_APP_PROXY_BASE_URL + SMART_APP_INTERNAL_SECRET
    # ----- let the pod call OCR / MCP / RAG / workflow proxies (and the
    # ----- LLM proxy above) without ever holding raw provider keys.
    tool_catalogue: list[dict] = []
    if settings.ocr_enabled:
        tool_catalogue.append(
            {
                "name": "vision_ocr",
                "kind": "vision_ocr",
                "description": (
                    "OCR / vision via Qwen3-VL (or compatible). Args:"
                    " {image_url|image_b64, prompt?, content_type?}."
                    " Returns {text, tokens_in, tokens_out, model}."
                    " ALWAYS gate behind a deterministic form-validation"
                    " step so incomplete submissions are rejected before"
                    " incurring vision-token costs."
                ),
                "endpoint": "/ocr",
            }
        )
        tool_catalogue.append(
            {
                "name": "image_analyze",
                "kind": "image_analyze",
                "description": (
                    "STRUCTURED per-image JUDGMENT against a learned rubric — the"
                    " DEFAULT for apps that assess / grade / judge a photo (damage,"
                    " defect, condition); use this, NOT vision_ocr. Returns an"
                    " ItemFinding (your field_schema fields + recommendation +"
                    " confidence) the officer reviews PER image; reject-reasons train"
                    " the task_type's rubric. Declare in AgentSpec.tools_v2 with"
                    " kind=image_analyze, task_type, field_schema; RECORD-BIND with"
                    " data_source_id + url_column + key_field so the agent passes a"
                    " short record_id, never a signed URL (a copied signed URL"
                    " corrupts -> 403). If an SOP/policy governs the judgment, set"
                    " sop_source (the policy/SOP RAG corpus id) + optional sop_query so"
                    " the TOOL fetches + caches the live SOP itself (per app+task_type;"
                    " the agent never carries it) — do NOT seed criteria in Mongo."
                    " Then set AppSpec.item_review_gate."
                ),
            }
        )
        tool_catalogue.append(
            {
                "name": "doc_extract",
                "kind": "doc_extract",
                "description": (
                    "STRUCTURED field extraction from a DOCUMENT (text PDF, scanned"
                    " PDF, or doc image) against a learned rubric — the DEFAULT for"
                    " apps that extract fields from a report / invoice / ID and ACT"
                    " on them; use this, NOT vision_ocr. Returns an ItemFinding"
                    " reviewed PER document. Declare with kind=doc_extract, task_type,"
                    " field_schema, optional model_tier (default large for the text"
                    " reasoning); RECORD-BIND with data_source_id + url_column +"
                    " key_field (agent passes record_id). If an SOP governs extraction,"
                    " set sop_source (policy/SOP RAG corpus id) + optional sop_query so"
                    " the tool fetches + caches the live SOP itself — do NOT seed Mongo."
                    " Set AppSpec.item_review_gate."
                ),
            }
        )
    tool_catalogue.append(
        {
            "name": "validate_form",
            "kind": "validate_form",
            "description": (
                "Deterministic, free-of-charge form-completeness check."
                " Runs locally in the runtime engine — no LLM, no"
                " network. Always declare this tool whenever the AppSpec"
                " has a FormPanel. Have the agent's system prompt"
                " mandate it runs first; if it returns ok=false, return"
                " a rejection to the user immediately."
            ),
            "args_schema": {
                "form_data": "object (the submitted form fields)",
                "schema_id": "string (FormPanel.id or .schema_ref)",
            },
            "returns": "{ok: bool, missing: string[], invalid: string[]}",
        }
    )
    if settings.mcp_enabled:
        tool_catalogue.append(
            {
                "name": "mcp",
                "kind": "mcp",
                "description": (
                    "READ live enterprise data via a registered dept-MCP."
                    " Args: {source_id, tool_name?, query?, args?,"
                    " max_results?}. The BA must have already added an"
                    " entry to AgentSpec.tools_v2 with kind=mcp,"
                    " source_id, and tool_name; the runtime LLM can then"
                    " invoke it. Discovery resolves source_id -> dept-MCP"
                    " endpoint at call time."
                ),
                "endpoint": "/mcp",
            }
        )
        tool_catalogue.append(
            {
                "name": "mcp_action",
                "kind": "mcp_action",
                "description": (
                    "WRITE BACK to a dept-MCP — an UPDATE / INSERT"
                    " registered as a catalogue write action. Use for any"
                    " state-changing action: route, flag, approve, record"
                    " a verdict. Declare in AgentSpec.tools_v2 with"
                    " kind=mcp_action and source_id, dataset_id,"
                    " action_id, input_schema — copy all four VERBATIM"
                    " from the dataset catalogue's write_actions[] (never"
                    " invent them). The agent's system_prompt must tell"
                    " the runtime LLM to call it once a decision is final."
                    " Without this, a review/triage app only recommends —"
                    " the record never changes and the queue never clears."
                ),
                "endpoint": "/execute_action",
            }
        )
    if settings.rag_enabled:
        tool_catalogue.append(
            {
                "name": "rag",
                "kind": "rag",
                "description": (
                    "RAG search across a registered dept-MCP corpus."
                    " Args: {source_id, query, top_k?, filters?}. Returns"
                    " ChunkResult[]. Declare in AgentSpec.tools_v2 with"
                    " kind=rag and source_id; runtime authorises the"
                    " specific source via the bearer's bindings."
                ),
                "endpoint": "/rag",
            }
        )
    # ----- Platform primitives (no tenant data behind them). Advertised so the
    # ----- builder's `tools_v2` declarations satisfy X-01 and the agent knows
    # ----- they are available. Each appears only when the runtime can actually
    # ----- run it — same fail-loud gating as the data-backed kinds above.
    tool_catalogue.append(
        {
            "name": "llm",
            "kind": "llm",
            "description": (
                "Sub-agent — delegate a focused reasoning step (classify,"
                " extract, summarise, draft) to a child agent that has its"
                " OWN narrower tools_v2 (a strict subset of the parent's)."
                " Declare in AgentSpec.tools_v2 with kind=llm. Always"
                " available; no catalogue entry or source_id needed."
            ),
        }
    )
    tool_catalogue.append(
        {
            "name": "consistency_check",
            "kind": "consistency_check",
            "description": (
                "Deterministic (no-LLM, free) fraud cross-checks on a record and"
                " its artifacts: cross-field consistency (claimed vs extracted"
                " values, normalised per locale), format/checksum validators,"
                " optional entity linking (phone/email/account/id shared across"
                " records), and — when RECORD-BOUND — image/PDF SHA-256 exact-"
                "duplicate + perceptual-hash (dHash) reused-artifact detection"
                " across prior records. Declare in AgentSpec.tools_v2 with"
                " kind=consistency_check, field_types, optional link_entities;"
                " for duplicate/reused photo or PDF detection you MUST also"
                " record-bind it: data_source_id (the app data_source holding the"
                " record) + url_columns (the record columns holding artifact URLs,"
                " e.g. ['defect_photo_url']) + key_field (the record key column) —"
                " the tool then resolves each URL via the dept-MCP and hashes the"
                " bytes itself, no vision tool needed. Returns fraud SIGNALS"
                " (exact/near-duplicate artifact, field mismatch,"
                " shared_identifier, identity_cardinality) — evidence, not a"
                " verdict. Always available; pair with fraud_synthesis so flagged"
                " cases escalate to gated reasoning."
            ),
        }
    )
    tool_catalogue.append(
        {
            "name": "check_evaluate",
            "kind": "check_evaluate",
            "description": (
                "Judge ONE API / System-of-Record check result (credit-bureau,"
                " identity/KYC, sanctions, income) into a per-check ItemFinding the"
                " officer reviews and accepts/rejects individually — the structured-"
                "data twin of image_analyze. The agent first READS the check via an"
                " `mcp` tool, then passes that result as `data`; check_evaluate emits"
                " {recommendation, confidence, rationale}. Declare ONE per check with"
                " a DISTINCT task_type (e.g. 'credit-check', 'identity-match') so each"
                " gets its own review card AND its own learned rubric. Two modes:"
                " mode='rule' + rule_expr for a fixed threshold (deterministic, no LLM"
                " — e.g. 'credit_score >= 700', 'status in (\"verified\",\"approved\")';"
                " COMPARISONS/boolean/membership only, no arithmetic); mode='llm'"
                " (default) for judgment checks — set sop_source (policy/SOP RAG corpus"
                " id) + optional sop_query so the tool fetches + caches the live SOP"
                " itself. Optional field_schema (fields to surface on the card),"
                " model_tier. Set AppSpec.item_review_gate so each check is reviewed"
                " before the overall decision commits. Available whenever the app has"
                " an mcp check to judge; pair with a preceding mcp read."
            ),
        }
    )
    tool_catalogue.append(
        {
            "name": "fraud_synthesis",
            "kind": "fraud_synthesis",
            "description": (
                "GATED LLM synthesis over the accumulated fraud signals — runs"
                " ONLY when the deterministic tier crosses a severity-points gate"
                " (cost-controlled; most records never reach it). Declare in"
                " AgentSpec.tools_v2 with kind=fraud_synthesis, model_tier,"
                " gate_min_points, sample_rate. Returns a fraud SCREENING (risk"
                " level, confidence, points breakdown, recommendation) persisted"
                " to smartapp_fraud_screenings for audit + rubric learning. Always"
                " available; declare AFTER a consistency_check / image_analyze that"
                " produces the signals it reasons over."
            ),
        }
    )
    if settings.code_exec_enabled:
        tool_catalogue.append(
            {
                "name": "code_exec",
                "kind": "code_exec",
                "description": (
                    "Deterministic compute / file generation in a sandboxed"
                    " Python runtime (build a CSV/XLSX/PDF, do math the LLM"
                    " shouldn't eyeball-estimate, reshape data). Declare in"
                    " AgentSpec.tools_v2 with kind=code_exec plus a"
                    " `description` prescription and an `allowed_outputs`"
                    " gate (see citra-code-exec)."
                ),
            }
        )
    if settings.milvus_uri:
        tool_catalogue.append(
            {
                "name": "neighbor_samples",
                "kind": "neighbor_samples",
                "description": (
                    "Few-shot grounding — retrieve the tenant's OWN past"
                    " decided cases (collection=Historical_Refresh, rows"
                    " isolated by agent_id) and inject them into the prompt."
                    " canonical mode = always-on baseline; neighbors mode ="
                    " per-case vector match (needs EMBEDDING_BASE_URL). See"
                    " citra-fewshot-from-history."
                ),
            }
        )
    env["TOOL_CATALOGUE"] = _json.dumps(tool_catalogue)
    env["OCR_ENABLED"] = "true" if settings.ocr_enabled else "false"
    env["MCP_ENABLED"] = "true" if settings.mcp_enabled else "false"
    env["RAG_ENABLED"] = "true" if settings.rag_enabled else "false"
    env["SMART_APP_PROXY_BASE_URL"] = proxy_base_url
    env["SMART_APP_INTERNAL_SECRET"] = builder_internal_bearer

    # ----- Adapter (action-sandbox runner) required env -----
    # The pod's runner config (infrastructure/action-sandbox/runner/
    # config.py :: SandboxConfig.from_env) refuses to boot without these
    # six vars. CITRA_AGENT_CONTROL_SECRET is added by the caller right
    # after this function returns. The remaining five we set here so a
    # single _builder_env() call produces a pod-ready env dict.
    env["CITRA_AGENT_USER_ID"] = owner or f"builder:{session_id}"
    env["CITRA_AGENT_SESSION_ID"] = session_id
    # The adapter's scoped_token is only used by adapter-side helpers
    # (artifact upload, milvus proxy). The same JWT we mint above for
    # CITRA_JWT carries scope=smart-app-builder and is signed with the
    # shared JWT_SECRET — re-using it keeps the trust chain identical.
    env["CITRA_AGENT_SCOPED_TOKEN"] = env["CITRA_JWT"]
    if tenant_id:
        env["CITRA_AGENT_ORG_ID"] = tenant_id
    # The runner uses these names (not LLM_LARGE_*) when reading its
    # own config. Same proxy URL + bearer pair as LLM_LARGE_* above —
    # the runner's OpenAI SDK never sees the raw OpenRouter key.
    env["CITRA_AGENT_LLM_BASE_URL"] = llm_proxy_base_url
    env["CITRA_AGENT_LLM_API_KEY"] = builder_internal_bearer
    # The OpenClaw runner reads CITRA_AGENT_MODEL (NOT LLM_MODEL/LLM_LARGE_MODEL)
    # for its agent loop, so the builder-only model override MUST be applied
    # here too. Without it, BUILDER_LLM_MODEL is effectively dead and the
    # builder silently runs on llm_large_model (the runtime/GLM tier) even
    # though LLM_MODEL is pinned to the builder model above. Mirror 2521/2524.
    env["CITRA_AGENT_MODEL"] = settings.builder_llm_model or settings.llm_large_model
    # OpenClaw reads CITRA_AGENT_CONTEXT_WINDOW (NOT LLM_CONTEXT_WINDOW) for the
    # model's declared window — it's the auto-compaction trigger AND a hard
    # pre-flight ceiling ("Context overflow: prompt too large for the model" →
    # build stalls). Without this line it silently fell back to the sandbox
    # entrypoint default (163840, the old V3.1 value), capping builds at ~16% of
    # DeepSeek V4 Pro's real 1M window and halting long builds. Set it explicitly
    # so the window is driven by THIS service's setting, not a stale pod default.
    env["CITRA_AGENT_CONTEXT_WINDOW"] = str(settings.llm_context_window)
    # Optional plumbing — empty values are tolerated (the adapter only
    # complains about the required six). These hook the pod into
    # smart-app-service's internal proxies for artifact upload / email.
    env.setdefault(
        "CITRA_AGENT_PROXY_BASE_URL",
        settings.smart_app_service_callback_url.rstrip("/"),
    )
    env.setdefault(
        "CITRA_AGENT_ARTIFACT_URL",
        settings.smart_app_service_callback_url.rstrip("/")
        + "/smart-app/internal/artifact",
    )
    # citra-mcp-service is the builder's TOOL GATEWAY — its MCP endpoint is merged
    # into the OpenClaw function catalogue and supplies the builder's primary tools:
    # citra_discovery_search/query, citra_spec_validate, citra_visual_review,
    # citra_web_search/fetch, citra_embed/rerank. Run it through _pod_reachable_url()
    # — exactly like DISCOVERY_SERVICE_URL — so a localhost dev URL is rewritten to
    # host.docker.internal; in prod CITRA_MCP_URL must be set to the in-cluster
    # citra-mcp URL the ISOLATED sandbox can resolve (the host.docker.internal
    # default does NOT resolve on Linux/AWS and silently strips the whole tool
    # plane). NOTE: critical tools have direct fallbacks so this isn't a single
    # point of failure — discovery via DISCOVERY_SERVICE_URL (curl), and spec
    # validation via SMART_APP_SERVICE_URL/builder/validate (see TOOLS.md).
    env.setdefault("CITRA_MCP_URL", _pod_reachable_url(settings.citra_mcp_url))

    if goal:
        # Optional. Only the edit flow supplies one today; new builds omit
        # it and the BA's first chat message drives the build.
        env["BUILD_GOAL"] = goal
    if tenant_id:
        env["CITRA_TENANT_ID"] = tenant_id
    if owner:
        env["CITRA_USER_ID"] = owner
    if seed_app_spec is not None:
        env["SEED_APP_SPEC"] = _json.dumps(seed_app_spec)
    if seed_agent_spec is not None:
        env["SEED_AGENT_SPEC"] = _json.dumps(seed_agent_spec)
    return env


def _mint_builder_token(
    *,
    settings: Settings,
    session_id: str,
    owner: Optional[str],
    tenant_id: Optional[str],
    org_id: Optional[str] = None,
    dept_ids: Optional[list] = None,
    roles: Optional[list] = None,
    author_email: Optional[str] = None,
) -> str:
    """Mint a short-lived JWT scoped to the builder pod.

    Same shape as ``services/action_scoped_token.mint_scoped_token`` in
    Citra-Service — signed with the shared ``JWT_SECRET`` so the
    smart-app-service auth middleware accepts it on ``/publish``. The
    ``scope=smart-app-builder`` claim is what ``require_publish_scope``
    looks for.

    ``org_id`` / ``dept_ids`` / ``roles`` are carried through from the
    BA who started the build so the builder pod's discovery calls
    (``citra-mcp-discover`` → ``/tools/available``) get the BA's actual
    org/dept visibility. Without them discovery's ``_tool_visible_to``
    sees an org-less token and returns an empty catalogue.
    """
    now = int(time.time())
    # TTL = builder_scoped_token_ttl_seconds (defaults to
    # build_session_max_seconds = 8 h). Lasts as long as the pod could
    # possibly live so MCP and /publish calls don't 401 mid-build. Same
    # trade-off action-chat made (12 h there) — narrow aud + scope keep
    # blast radius small, no refresh path needed.
    ttl = max(settings.builder_scoped_token_ttl_seconds, 600)
    # The builder is a BUILDER — it does NOT own or assert ownership of the
    # app. /publish resolves the owning Work SA from the build session (which
    # smart-app-service recorded from the authenticated BA at /build), keyed by
    # session_id. So this token carries ONLY what the build itself needs:
    #   • scope + session_id      → authorise /publish for this session
    #   • tenant_id               → tenant scoping / cross-tenant guard
    #   • org_id / dept_ids/roles → the BA's DATA VISIBILITY for discovery
    #                               (citra-mcp-discover → /tools/available)
    # No work_sa_id / service_account_admin_of: ownership is not the pod's.
    payload = {
        "sub": owner or f"builder:{session_id}",
        "user_id": owner or f"builder:{session_id}",
        "scope": "smart-app-builder",
        "session_id": session_id,
        "tenant_id": tenant_id,
        "org_id": org_id,
        "dept_ids": dept_ids or [],
        "roles": roles or [],
        # data-discovery-service (and peers) validate ``iss`` — without
        # it the builder pod's calls to /builder/catalogue 401 and the
        # catalogue silently comes back empty, so the builder has no real
        # dataset / write_action ids to copy and starts inventing them.
        "iss": settings.jwt_issuer,
        # The BA's email — stamped for ownership attribution on the
        # services this token reaches (discovery / catalogue). Falls back to
        # the owning SA id when the originating request carried no email.
        "email": author_email or owner,
        "iat": now,
        "exp": now + ttl,
    }
    return jwt.encode(
        payload, settings.jwt_secret, algorithm=settings.jwt_algorithm
    )


async def _spawn_build_session(
    *,
    settings: Settings,
    goal: Optional[str] = None,
    tenant_id: Optional[str],
    owner: Optional[str],
    org_id: Optional[str] = None,
    dept_ids: Optional[list] = None,
    roles: Optional[list] = None,
    author_email: Optional[str] = None,
    seed_app_spec: Optional[dict] = None,
    seed_agent_spec: Optional[dict] = None,
    app_id: Optional[str] = None,
    build_kind: str = "app",
    build_kinds: Optional[list] = None,
    primary_page_kind: Optional[str] = None,
    build_headless: bool = False,
) -> BuildResponse:
    sessions = get_build_sessions_col()
    client = SandboxClient(settings)

    # ── Reuse: one builder pod per (owner, app_id, build_kind) target ──
    # When the BA reopens the SAME build target (a draft new-build →
    # app_id=None, or an edit of a specific app_id), reattach to the pod
    # that is already running instead of spawning a duplicate. This both
    # caps builders at one-per-target-per-user AND preserves the
    # in-progress build conversation across a UI close/reopen. We only
    # reuse a pod the host confirms is still ALIVE; a stale ACTIVE doc
    # whose pod has gone is marked failed so it won't match again.
    reuse_query = {
        "owner": owner,
        "app_id": app_id,
        "build_kind": build_kind,
        "status": BuildSessionStatus.ACTIVE.value,
    }
    existing = await sessions.find_one(reuse_query, sort=[("started_at", -1)])
    if existing and existing.get("session_id"):
        existing_sid = existing["session_id"]
        try:
            alive = await client.session_alive(
                existing_sid, host_base=existing.get("sandbox_host_base")
            )
        except SandboxHostError as e:
            # Can't confirm liveness because the host is unreachable —
            # fail loud rather than spawning a duplicate (which would also
            # fail) or silently reattaching to a maybe-dead pod.
            logger.error(
                "could not verify existing builder %s liveness: %s",
                existing_sid, e,
            )
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"sandbox host unreachable while checking existing session: {e}",
            )
        if alive and existing.get("adapter_url"):
            logger.info(
                "reusing live builder session %s (owner=%s app_id=%s kind=%s)",
                existing_sid, owner, app_id, build_kind,
            )
            pod_session_token = _mint_builder_token(
                settings=settings,
                session_id=existing_sid,
                owner=owner,
                tenant_id=tenant_id,
                org_id=org_id,
                dept_ids=dept_ids,
                roles=roles,
                author_email=author_email,
            )
            return BuildResponse(
                session_id=existing_sid,
                pod_id=existing.get("pod_id"),
                builder_url=existing.get("adapter_url"),
                pod_session_token=pod_session_token,
                status=BuildSessionStatus.ACTIVE,
                started_at=existing.get("started_at") or datetime.now(timezone.utc),
                reused=True,
            )
        # Pod is gone — retire the stale doc so the next reuse query skips it.
        await sessions.update_one(
            {"session_id": existing_sid},
            {"$set": {
                "status": BuildSessionStatus.FAILED.value,
                "stopped_at": datetime.now(timezone.utc),
                "error": "pod not alive on reattach",
            }},
        )

    # ── Reap-on-spawn: clean up THIS owner's other ACTIVE sessions whose
    #    pods are dead (orphans left by tab-closes that never tore down).
    #    Live pods for OTHER targets are left alone — they are legitimately
    #    one-per-target.
    async for doc in sessions.find(
        {"owner": owner, "status": BuildSessionStatus.ACTIVE.value}
    ):
        sid = doc.get("session_id")
        if not sid:
            continue
        doc_host = doc.get("sandbox_host_base")
        try:
            if await client.session_alive(sid, host_base=doc_host):
                continue  # live other-target builder — leave it running
        except SandboxHostError as e:
            logger.warning("reap-on-spawn liveness check failed for %s: %s", sid, e)
            continue
        try:
            await client.stop_session(sid, host_base=doc_host)  # best-effort; pod likely already gone
        except SandboxHostError as e:
            logger.warning("reap-on-spawn stop failed for %s: %s", sid, e)
        await sessions.update_one(
            {"session_id": sid},
            {"$set": {
                "status": BuildSessionStatus.FAILED.value,
                "stopped_at": datetime.now(timezone.utc),
                "error": "reaped orphan on spawn",
            }},
        )

    session_id = f"bs_{uuid.uuid4().hex[:16]}"
    user_id = owner or "anonymous"

    env = _builder_env(
        settings=settings,
        session_id=session_id,
        goal=goal,
        tenant_id=tenant_id,
        owner=owner,
        org_id=org_id,
        dept_ids=dept_ids,
        roles=roles,
        author_email=author_email,
        seed_app_spec=seed_app_spec,
        seed_agent_spec=seed_agent_spec,
        build_kind=build_kind,
        build_kinds=build_kinds,
        primary_page_kind=primary_page_kind,
        build_headless=build_headless,
    )

    # Per-session control-plane secret. The pod adapter checks the
    # X-Citra-Control header against CITRA_AGENT_CONTROL_SECRET on every
    # /task call. We mint a fresh random value, inject it into the
    # pod env, and persist it on the build_sessions doc so the
    # /build/{id}/chat/stream relay can present it when forwarding the
    # BA's chat turn into the pod.
    control_secret = secrets.token_urlsafe(32)
    env["CITRA_AGENT_CONTROL_SECRET"] = control_secret
    env["CITRA_CONTROL_SECRET"] = control_secret

    started_at = datetime.now(timezone.utc)
    # The surface the BA picked, stated IN THE CONVERSATION rather than left to
    # env. It reaches the pod prefixed onto the first forwarded turn (see
    # _take_surface_note in the chat relay) — env alone is one paragraph in a
    # 400-line AGENTS.md and was being missed, which is how a BA who clicked
    # "Embedded card" still got asked which surface they wanted.
    #
    # Prefixed onto an existing turn, NOT sent as its own: the UI fires the BA's
    # pending first message the moment /build returns, so a separately injected
    # turn would race it and two concurrent turns scramble the conversation.
    surface_note = _surface_note(
        primary_page_kind=primary_page_kind, build_headless=bool(build_headless),
    )
    doc = {
        "session_id": session_id,
        "app_id": app_id,
        "tenant_id": tenant_id,
        "owner": owner,
        "goal": goal,
        "build_kind": build_kind,
        "build_kinds": list(build_kinds or [build_kind]),
        "primary_page_kind": primary_page_kind or "standard",
        "build_headless": bool(build_headless),
        #: Consumed exactly once, by the first chat turn. None when the BA made
        #: no explicit pick (see _surface_note).
        "surface_note": surface_note,
        "status": BuildSessionStatus.ACTIVE.value,
        "started_at": started_at,
        # Bumped on every chat/steer turn; the idle sweep reaps the pod when
        # this falls older than BUILD_SESSION_IDLE_TIMEOUT_SECONDS so an
        # abandoned build frees its slot in ~30 min, not the 2 h max-age.
        "last_activity_at": started_at,
        "transcript": [],
        "q_and_a": [],
        "control_secret": control_secret,
    }

    try:
        spawn_resp = await client.spawn_builder(
            session_id=session_id,
            user_id=user_id,
            env=env,
            labels={
                "citra.build.tenant": tenant_id or "",
                "citra.build.owner": owner or "",
            },
            tier=settings.builder_tier,
        )
    except SandboxHostError as e:
        logger.error("failed to spawn builder pod: %s", e)
        doc["status"] = BuildSessionStatus.FAILED.value
        doc["error"] = str(e)
        await get_build_sessions_col().insert_one(doc)
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"sandbox host could not start builder pod: {e}",
        )

    doc["pod_id"] = spawn_resp.get("container_id")
    doc["pod_name"] = spawn_resp.get("container_name")
    doc["adapter_url"] = spawn_resp.get("adapter_url")
    # Which host the pod landed on — session_alive / stop_session target it
    # directly across a multi-host fleet (None for single-host deployments).
    doc["sandbox_host_base"] = spawn_resp.get("sandbox_host_base")
    await get_build_sessions_col().insert_one(doc)

    # Mint a pod session token the browser will present on the WebSocket
    # handshake. Same secret as JWT_SECRET so the adapter can verify it
    # without a separate signing key.
    pod_session_token = _mint_builder_token(
        settings=settings,
        session_id=session_id,
        owner=owner,
        tenant_id=tenant_id,
        org_id=org_id,
        dept_ids=dept_ids,
        roles=roles,
        author_email=author_email,
    )

    return BuildResponse(
        session_id=session_id,
        pod_id=spawn_resp.get("container_id"),
        builder_url=spawn_resp.get("adapter_url"),
        pod_session_token=pod_session_token,
        status=BuildSessionStatus.ACTIVE,
        started_at=started_at,
    )


# ---------------------------------------------------------------------------
# Reserved endpoints (later phases)
# ---------------------------------------------------------------------------


# Roles allowed to BUILD & publish Decision Apps/APIs. Building is the builder
# persona's job — consumers only see apps/dashboards published to them. The UI
# hides the build card for non-builders, but that is not access control: this
# server-side gate is the authoritative check. Mirrors the frontend
# canBuildApps rule (App.js) and citra-auth Roles.DECISION_APP_BUILDER.
_APP_BUILDER_ROLES = frozenset(
    {"super_admin", "org_admin", "dept_admin", "decision-app-builder"}
)


def _require_app_builder(request: Request) -> None:
    """403 unless the caller may build Decision Apps (admin or builder role)."""
    roles = set(get_user_roles(request) or [])
    if not (roles & _APP_BUILDER_ROLES):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail=(
                "Building Decision Apps requires the decision-app-builder role "
                "or an admin role (org/dept/super)."
            ),
        )


@app.post("/build", response_model=BuildResponse)
async def build_app(
    payload: BuildRequest,
    request: Request,
    settings: Settings = Depends(get_settings),
) -> BuildResponse:
    """Spawn an ephemeral builder pod for a new Power AI App.

    The pod loads /workspace/AGENTS.md and runs the four-phase build
    (Internship → Expertise → Compose → Deploy). When ready, it POSTs
    back to /publish with the validated AppSpec + AgentSpec.
    """
    # Builder-only surface — authoritative gate (the hidden UI card is not it).
    _require_app_builder(request)
    user_id = get_secure_user_id(request)
    user_tenant = get_tenant_id(request)
    # The build path is always test (when configured): the builder pod is
    # spawned with the test discovery URL and the build session lands in the
    # test_ collection. Set BEFORE _spawn_build_session (see _bind_build_env).
    _bind_build_env()
    _u = getattr(request.state, "user", None) or {}
    # The build session — and every app it publishes — is owned by the
    # caller's Work SA, never a raw user id. Resolve it from the JWT;
    # derive deterministically only as a fallback for older tokens.
    owning_sa = (_u.get("work_sa_id") or "").strip() or _work_sa_id(
        user_id, _u.get("org_id") or user_tenant
    )
    # The originating BA's email — stamped onto the builder token for
    # ownership attribution on discovery / catalogue calls. user_id is the
    # email in this system; the explicit ``email`` claim is preferred.
    author_email = (_u.get("email") or "").strip() or user_id
    return await _spawn_build_session(
        settings=settings,
        goal=payload.goal,
        tenant_id=payload.tenant_id or user_tenant,
        owner=owning_sa,
        # Carry the BA's org context into the builder pod's token so
        # citra-mcp-discover's /tools/available calls see the BA's
        # actual MCP visibility (org / dept / role scoped).
        org_id=get_org_id(request),
        dept_ids=get_user_dept_ids(request),
        roles=get_user_roles(request),
        author_email=author_email,
        build_kind=payload.build_kind,
        build_kinds=payload.build_kinds,
        primary_page_kind=payload.primary_page_kind,
        build_headless=payload.build_headless,
    )


@app.get("/build/{session_id}", response_model=dict)
async def get_build_session(session_id: str, request: Request) -> dict:
    user_id = get_secure_user_id(request)
    # Build sessions live in the build store (test_ under a test env) — bind it.
    _bind_build_env()
    doc = await get_build_sessions_col().find_one({"session_id": session_id})
    if not doc:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail="build session not found"
        )
    if not _can_access_build_session(doc, request):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail="not authorized for this build session (owned by a different Work SA)",
        )
    doc.pop("_id", None)
    return doc


@app.post("/build/{session_id}/chat/stream")
async def relay_build_chat(
    session_id: str,
    payload: dict,
    request: Request,
    settings: Settings = Depends(get_settings),
):
    """Forward a BA chat turn into the builder pod and stream events back.

    The browser cannot reach the pod adapter directly:
      - the adapter is on a private host port (sandbox-host network)
      - it gates /task on a server-side ``CITRA_AGENT_CONTROL_SECRET``

    This endpoint is the user-facing relay. We authenticate the BA via
    the standard JWT middleware, look up the build_session doc, then
    POST to ``<adapter_url>/task`` with the per-session control secret
    and stream the SSE response straight through (event frames are
    already SSE-formatted by the adapter, so this is a byte-pipe).
    """
    user_id = get_secure_user_id(request)
    # Build sessions live in the build store (test_ when a test env is
    # configured — POST /build binds it). Bind it here too so this lookup
    # reads the SAME collection it was written to, else it 404s under a test env.
    _bind_build_env()
    sessions = get_build_sessions_col()
    doc = await sessions.find_one({"session_id": session_id})
    if not doc:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail="build session not found"
        )
    if not _can_access_build_session(doc, request):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail="not authorized for this build session (owned by a different Work SA)",
        )

    adapter_url = doc.get("adapter_url")
    control_secret = doc.get("control_secret")
    if not adapter_url or not control_secret:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="build session has no live builder pod (already stopped?)",
        )

    # Keep the idle sweep from reaping a pod that's actively in use.
    await sessions.update_one(
        {"session_id": session_id},
        {"$set": {"last_activity_at": datetime.now(timezone.utc)}},
    )

    message = (payload or {}).get("message")
    if not isinstance(message, str) or not message.strip():
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail="message is required"
        )

    # FIRST turn only: prefix the surface the BA picked in Citra-UI, so the
    # builder is TOLD in the conversation instead of having to notice one
    # paragraph of AGENTS.md. Popped atomically, so a racing second turn cannot
    # prefix it twice.
    #
    # Only `forwarded` carries the note. The transcript log below records the
    # BA's OWN text — the note reaches the pod without becoming part of what we
    # record the human as having said.
    forwarded = message
    _surface = await _take_surface_note(sessions, session_id)
    if _surface:
        forwarded = f"{_surface}\n\n{message}"

    target = f"{adapter_url.rstrip('/')}/task"
    headers = {
        "X-Citra-Control": control_secret,
        "Content-Type": "application/json",
    }

    # Best-effort transcript log on the build_sessions doc so the BA's
    # turn survives even if the SSE response drops mid-stream.
    try:
        await sessions.update_one(
            {"session_id": session_id},
            {
                "$push": {
                    "transcript": {
                        "role": "user",
                        "text": message,
                        "ts": datetime.now(timezone.utc),
                    }
                }
            },
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("build chat transcript log failed: %s", exc)

    body = {"message": forwarded}

    async def proxy_stream():
        timeout = httpx.Timeout(
            connect=10.0,
            read=settings.build_session_idle_timeout_seconds,
            write=10.0,
            pool=10.0,
        )
        # ALWAYS drive the browser to a TERMINAL state. The adapter's chunked
        # SSE can close with an INCOMPLETE final chunk (an abandoned
        # StopAsyncIteration task on the pod when the chat:final branch breaks).
        # httpx then raises RemoteProtocolError mid-read and the final assistant
        # message + the adapter's own `done` are lost. Without a guaranteed
        # terminal here the BA's chat freezes on the last partial delta with
        # "Mid-task" stuck FOREVER — even though the build SUCCEEDED server-side.
        # So: emit `done` on every exit path (clean OR dropped), as a plain
        # `data:` event (the shape the frontend's stream parser renders — a
        # named `event: error` is silently ignored), and on a drop tell the BA
        # the build may have published and to reopen/refresh.
        _DONE = b'data: {"type": "done"}\n\n'
        _buf: List[bytes] = []  # accumulate the assistant's output to persist on exit (drop-proof)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream(
                    "POST", target, json=body, headers=headers
                ) as resp:
                    if resp.status_code >= 400:
                        err_text = (await resp.aread()).decode(
                            "utf-8", errors="replace"
                        )
                        err_evt = json.dumps(
                            {
                                "type": "error",
                                "message": (
                                    f"adapter rejected /task ({resp.status_code}): "
                                    f"{err_text[:300]}"
                                ),
                            }
                        )
                        yield f"data: {err_evt}\n\n".encode()
                        yield _DONE
                        return
                    async for chunk in resp.aiter_raw():
                        if chunk:
                            _buf.append(chunk)
                            yield chunk
            # Upstream ended. If this session published during the turn, emit a
            # STRUCTURED `publish` event (the UI renders the "Open app" deploy
            # strip from it) exactly once — deterministic delivery that does NOT
            # depend on the agent narrating "live at <url>" in prose. The
            # pending flag (set by /publish) makes this fire once and survive a
            # dropped turn (the next clean `done` re-emits if still pending).
            try:
                _pub = await get_build_sessions_col().find_one(
                    {"session_id": session_id},
                    {"published_url": 1, "published_slug": 1,
                     "published_version": 1, "published_event_pending": 1, "_id": 0},
                )
                if _pub and _pub.get("published_event_pending") and _pub.get("published_url"):
                    _pub_evt = json.dumps({
                        "type": "publish",
                        "url": _pub["published_url"],
                        "slug": _pub.get("published_slug") or "",
                        "version": _pub.get("published_version"),
                    })
                    yield f"data: {_pub_evt}\n\n".encode()
                    await get_build_sessions_col().update_one(
                        {"session_id": session_id},
                        {"$set": {"published_event_pending": False}},
                    )
            except Exception as _pub_err:
                logger.warning(
                    "[build-chat relay] publish-event emit failed for %s: %s",
                    session_id, _pub_err,
                )
            # Upstream ended — guarantee the browser sees a terminal `done`
            # even if the adapter's own `done` was in a dropped final chunk
            # (a duplicate `done` is idempotent for the client).
            yield _DONE
        except httpx.HTTPError as exc:
            # Incomplete chunked read / adapter drop mid-stream: the final
            # message may be lost, but the build often SUCCEEDED. Surface a
            # readable note + a terminal `done` so the UI never hangs.
            logger.warning(
                "[build-chat relay] upstream stream dropped for %s: %s",
                session_id, exc,
            )
            note = json.dumps({
                "type": "status",
                "stage": "stream_interrupted",
                "text": (
                    "The builder connection dropped before the final message. "
                    "Your app may already be published — check the Test tab, or "
                    "reopen this build to see the result."
                ),
            })
            yield f"data: {note}\n\n".encode()
            yield _DONE
        finally:
            # Persist the ASSISTANT's streamed output to the session transcript so a
            # dropped/closed stream never loses the builder's reply — the UI (and
            # tests) can poll GET /build/{id} and resume. (User turns already logged
            # above; this closes the gap where only the user side survived a drop.)
            # Swallow BaseException so persistence can NEVER block the response close
            # (mirrors the adapter's aclose guard).
            try:
                _atext = b"".join(_buf).decode("utf-8", "replace")[-200000:]
                if _atext.strip():
                    await sessions.update_one(
                        {"session_id": session_id},
                        {"$push": {"transcript": {
                            "role": "assistant", "raw": _atext,
                            "ts": datetime.now(timezone.utc),
                        }}},
                    )
            except BaseException as _e:  # noqa: BLE001
                logger.warning("[build-chat relay] assistant transcript persist skipped: %s", _e)

    return StreamingResponse(
        proxy_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/build/{session_id}/cancel")
async def cancel_build_turn(
    session_id: str,
    request: Request,
) -> JSONResponse:
    """Abort the in-flight builder turn.

    Forwards to the adapter's ``/task/cancel``, which calls OpenClaw's
    native ``chat.abort`` RPC over the gateway WebSocket. OpenClaw
    terminates the active run server-side and emits ``cancelled`` on
    the SSE stream the BA's chat is consuming.
    """
    user_id = get_secure_user_id(request)
    # Build sessions live in the build store (test_ when a test env is
    # configured — POST /build binds it). Bind it here too so this lookup
    # reads the SAME collection it was written to, else it 404s under a test env.
    _bind_build_env()
    sessions = get_build_sessions_col()
    doc = await sessions.find_one({"session_id": session_id})
    if not doc:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail="build session not found"
        )
    if not _can_access_build_session(doc, request):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail="not authorized for this build session (owned by a different Work SA)",
        )

    # The idle sweep reaps abandoned pods (status → timed_out) but leaves the
    # stale adapter_url/control_secret on the doc. A BA whose tab is still open
    # can click Cancel after that — there is nothing left to cancel. Treat it as
    # already-cancelled rather than POSTing to a dead pod (which resets the
    # connection → ReadError → a misleading 502 that floods GlitchTip).
    if doc.get("status") != BuildSessionStatus.ACTIVE.value:
        return JSONResponse({"cancelled": True, "already_ended": True})

    adapter_url = doc.get("adapter_url")
    control_secret = doc.get("control_secret")
    if not adapter_url or not control_secret:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="build session has no live builder pod",
        )

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.post(
                f"{adapter_url.rstrip('/')}/task/cancel",
                headers={"X-Citra-Control": control_secret},
            )
    except httpx.HTTPError as exc:
        # Pod was ACTIVE per the doc but is unreachable now — it crashed/was
        # torn down between idle sweeps. Nothing is left to cancel, so the
        # cancel intent is satisfied; log loudly (this is a genuine unexpected
        # pod death) but don't 502 — the BA has nothing to act on.
        logger.warning(
            "[build cancel] active pod unreachable for %s — treating as cancelled: %s",
            session_id, exc,
        )
        return JSONResponse({"cancelled": True, "pod_unreachable": True})
    return JSONResponse({"cancelled": r.status_code == 200})


@app.post("/build/{session_id}/steer")
async def steer_build_turn(
    session_id: str,
    payload: dict,
    request: Request,
) -> JSONResponse:
    """Forward a follow-up BA message into an in-flight builder turn.

    Pure pass-through to the adapter's ``/task/steer``, which calls
    OpenClaw's ``chat.send`` RPC. The session is already configured
    with ``messages.queue.mode = steer`` (in openclaw.json), so OpenClaw
    queues the message and injects it between tool boundaries — we do
    not cancel anything, we do not wrap, we do not decide.

    The BA's typed text is appended to the build_session transcript so
    chat reload reflects what was actually said mid-stream.
    """
    user_id = get_secure_user_id(request)
    # Build sessions live in the build store (test_ when a test env is
    # configured — POST /build binds it). Bind it here too so this lookup
    # reads the SAME collection it was written to, else it 404s under a test env.
    _bind_build_env()
    sessions = get_build_sessions_col()
    doc = await sessions.find_one({"session_id": session_id})
    if not doc:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail="build session not found"
        )
    if not _can_access_build_session(doc, request):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail="not authorized for this build session (owned by a different Work SA)",
        )

    message = (payload or {}).get("message")
    if not isinstance(message, str) or not message.strip():
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail="message is required"
        )

    # If the idle sweep already reaped this pod (status → timed_out) the stale
    # adapter_url still lingers on the doc. Steering it would POST to a dead pod
    # → ReadError → a misleading 502 in GlitchTip. The BA's message genuinely
    # cannot land, so fail loud with an actionable 409 (not a server 5xx).
    if doc.get("status") != BuildSessionStatus.ACTIVE.value:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="build session is no longer live; reopen the build to continue",
        )

    # Steering counts as activity — keep the idle sweep off this pod.
    await sessions.update_one(
        {"session_id": session_id},
        {"$set": {"last_activity_at": datetime.now(timezone.utc)}},
    )

    adapter_url = doc.get("adapter_url")
    control_secret = doc.get("control_secret")
    if not adapter_url or not control_secret:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="build session has no live builder pod",
        )

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(
                f"{adapter_url.rstrip('/')}/task/steer",
                headers={
                    "X-Citra-Control": control_secret,
                    "Content-Type": "application/json",
                },
                json={"message": message},
            )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            detail=f"builder pod unreachable: {exc}",
        ) from exc

    if r.status_code != 200:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            detail=f"adapter rejected steer: {r.status_code} {r.text[:300]}",
        )

    # B4 fix: skip persisting OpenClaw inline directives (``/queue``,
    # ``/status``, ``/tools``, etc.). Those are admin commands the
    # gateway consumes before the LLM sees them; persisting them would
    # leak into chat-reload as if the BA "typed" them.
    if not message.lstrip().startswith("/"):
        try:
            await sessions.update_one(
                {"session_id": session_id},
                {
                    "$push": {
                        "transcript": {
                            "role": "user",
                            "text": message,
                            "ts": datetime.now(timezone.utc),
                            "meta": {"kind": "steer"},
                        }
                    }
                },
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("build steer transcript log failed: %s", exc)

    try:
        return JSONResponse(r.json())
    except Exception:  # noqa: BLE001
        return JSONResponse({"queued": True})


@app.delete("/build/{session_id}", response_model=SuccessResponse)
async def stop_build_session(
    session_id: str,
    request: Request,
    settings: Settings = Depends(get_settings),
) -> SuccessResponse:
    user_id = get_secure_user_id(request)
    # Build sessions live in the build store (test_ when a test env is
    # configured — POST /build binds it). Bind it here too so this lookup
    # reads the SAME collection it was written to, else it 404s under a test env.
    _bind_build_env()
    sessions = get_build_sessions_col()
    doc = await sessions.find_one({"session_id": session_id})
    if not doc:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail="build session not found"
        )
    if not _can_access_build_session(doc, request):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail="not authorized for this build session (owned by a different Work SA)",
        )

    client = SandboxClient(settings)
    try:
        stopped = await client.stop_session(
            session_id, host_base=doc.get("sandbox_host_base")
        )
    except SandboxHostError as e:
        logger.warning("sandbox-host stop failed: %s", e)
        stopped = False

    await sessions.update_one(
        {"session_id": session_id},
        {
            "$set": {
                "status": BuildSessionStatus.COMPLETED.value
                if stopped
                else BuildSessionStatus.FAILED.value,
                "stopped_at": datetime.now(timezone.utc),
            }
        },
    )
    return SuccessResponse(
        message=f"stopped {session_id}" if stopped else f"{session_id} not running on host"
    )


@app.post("/apps/{slug}/edit", response_model=BuildResponse)
async def edit_app(
    slug: str,
    request: Request,
    settings: Settings = Depends(get_settings),
) -> BuildResponse:
    """Re-open an existing app in a builder pod, with the current spec seeded."""
    user_id = get_secure_user_id(request)
    user_tenant = get_tenant_id(request)
    # Load the SOURCE app by store (it may be a live prod app the BA wants to
    # iterate on, or an unpromoted test app). The new build session + pod are
    # bound to test below, just before the spawn — so editing a prod app starts
    # a fresh test build that the BA re-promotes.
    await _bind_app_env(slug)
    apps = get_apps_col()
    agents = get_agents_col()

    app_doc = await apps.find_one({"slug": slug})
    if not app_doc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="app not found")
    if not _can_edit_app(
        app_doc, user_id, user_tenant,
        user_org_id=get_org_id(request),
        user_dept_ids=get_user_dept_ids(request),
        user_roles=get_user_roles(request),
        sa_admin_of=get_sa_admin_of(request),
    ):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="not authorised to edit this app")

    app_kind = (app_doc.get("app_spec") or {}).get("kind", "app")
    # Legacy stored dashboards (pre page-kind migration) edit as apps with a
    # dashboard primary page. The AppSpec page-kind coercion only runs when a
    # spec is parsed; the edit handler reads the raw Mongo doc, so normalise
    # the retired kind here too — otherwise the builder pod receives a
    # BUILD_KIND it no longer understands and the narrator agent isn't seeded.
    was_legacy_dashboard = app_kind == "dashboard"
    if was_legacy_dashboard:
        app_kind = "app"

    seed_agent_spec: Optional[dict] = None
    if app_kind == "app":
        agent_doc = await agents.find_one(
            {
                "agent_id": app_doc["agent_id"],
                "tenant_id": app_doc.get("tenant_id"),
            }
        )
        if not agent_doc:
            raise HTTPException(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="agent spec missing for app",
            )
        seed_agent_spec = agent_doc["agent_spec"]

    # Compose effective build_kinds so AGENTS.md re-runs the right phases.
    edit_kinds = [app_kind]

    # Preserve the executive treatment across edits: if the app's primary
    # page is a dashboard page (or it's a legacy dashboard being migrated on
    # the fly), tell the builder so it keeps it.
    _seed_pages = (app_doc.get("app_spec") or {}).get("pages") or []
    edit_primary_page_kind = (
        "dashboard"
        if was_legacy_dashboard
        or any(p.get("kind") == "dashboard" for p in _seed_pages)
        else "standard"
    )
    # The new build always runs in test (when configured): session + pod target
    # the test_ collections / test MCPs, regardless of where the source app
    # lives. Set AFTER loading the source, just before the spawn.
    _bind_build_env()
    return await _spawn_build_session(
        settings=settings,
        goal=f"Edit existing {app_kind}: {app_doc['app_spec'].get('title', slug)}",
        tenant_id=app_doc.get("tenant_id") or user_tenant,
        owner=app_doc["app_spec"].get("owner_id") or user_id,
        seed_app_spec=app_doc["app_spec"],
        seed_agent_spec=seed_agent_spec,
        app_id=app_doc["app_id"],
        build_kind=app_kind,
        build_kinds=edit_kinds,
        primary_page_kind=edit_primary_page_kind,
    )


# ---------------------------------------------------------------------------
# Run audit trail
# ---------------------------------------------------------------------------
#
# Every /run invocation appends one immutable row to the app_run_audit
# collection — the decision, the LLM's reasoning, the evidence that drove
# it, and the agent_spec version in force. Rows are never updated; an
# approved re-run appends a second row under the same correlation_id.


def _audit_content_hash(doc: Dict[str, Any]) -> str:
    """SHA-256 over an audit row's content, for tamper-evidence.

    Excludes self-referential / volatile keys. Any later edit to a
    persisted row changes this hash, so tampering is detectable. The
    ``prev_hash`` field IS included in the hash input — that is what
    turns per-row tamper-evidence into a tenant-scoped chain (deleting
    or reordering rows breaks the chain on the next insert).
    """
    payload = {
        k: v for k, v in doc.items() if k not in ("content_hash", "_id")
    }
    blob = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _timeline_has_data_write(timeline: Any) -> bool:
    if not isinstance(timeline, list):
        return False
    for step in timeline:
        if isinstance(step, dict) and step.get("step") == "data_write":
            if step.get("status") in ("ok", "error", "blocked"):
                return True
    return False


def _build_audit_doc(
    *,
    response: RunResponse,
    slug: str,
    app_doc: Dict[str, Any],
    agent_doc: Dict[str, Any],
    user_id: Optional[str],
    tenant_id: Optional[str],
    action: str,
    inputs: Dict[str, Any],
    started_at: datetime,
) -> Dict[str, Any]:
    """Assemble one ``app_run_audit`` row from a finished run."""
    now = datetime.now(timezone.utc)
    agent_spec = agent_doc.get("agent_spec") or {}
    write_events = list(response.write_events or [])
    had_writes = bool(write_events) or _timeline_has_data_write(response.timeline)
    # An LLM-issued write without a structured decision block is the worst
    # audit shape: a mutation with no recorded reason. Flag it loud so the
    # audit UI can badge the row and an out-of-band review can chase it.
    audit_missing = bool(had_writes and not response.decision)
    return {
        "correlation_id": response.correlation_id,
        "slug": slug,
        "app_id": app_doc.get("app_id"),
        "agent_id": app_doc.get("agent_id"),
        "agent_spec_version": agent_spec.get("version"),
        "tenant_id": tenant_id,
        "requested_by": user_id,
        "action": action,
        "inputs": inputs,
        "status": response.status,
        "decision": response.decision,
        "reasoning": response.reasoning,
        "citations": response.citations,
        "cited_precedents": getattr(response, "cited_precedents", None) or [],
        # The frozen case signature, lifted out of `references` so the decision
        # surface can show the officer WHICH cases a correction of theirs would
        # teach. Read-only there — facets are derived, never officer-chosen.
        "case_facets": list((response.references or {}).get("case_facets") or []),
        # The learned judgements in front of the model for this run. The officer
        # must be able to see what their team already taught — otherwise the
        # memory is invisible, and nobody can tell when the agent ignored a
        # judgement that fired.
        "cited_clauses": list((response.references or {}).get("cited_clauses") or []),
        "references": response.references,
        "write_events": write_events,
        "write_count": len(write_events),
        "audit_missing": audit_missing,
        "outputs": response.outputs,
        "timeline": response.timeline,
        "error": response.error,
        "model": response.model,
        "usage": response.usage,
        "duration_ms": int((now - started_at).total_seconds() * 1000),
        "trace_id": getattr(response, "trace_id", None),
        "surface": "smartapp_runtime",
        "created_at": now,
    }


class AuditPersistError(Exception):
    """Raised when an audit row could not be written for a run that mutated state.

    The ``/run`` endpoint converts this into a 5xx so the caller is not
    told the run succeeded when the immutable record of the write is
    missing — a silent ledger gap on a write is worse than an explicit
    failure response.
    """


async def _fetch_prev_hash(
    *, col: AsyncIOMotorCollection, tenant_id: Optional[str]
) -> Optional[str]:
    """Return the ``content_hash`` of the most recent audit row in this
    tenant's chain (None for a tenant's first row).

    Concurrent inserts under the same tenant can briefly fork — two rows
    landing at the same instant may both reference the same prev_hash.
    For SmartApp's write volume this is acceptable: any fork is detected
    by the chain verifier (two rows with the same prev_hash) and that's
    exactly what a reviewer would want to see. A future tightening can
    move to a per-tenant counter doc with optimistic concurrency.
    """
    query: Dict[str, Any] = {"tenant_id": tenant_id} if tenant_id else {
        "tenant_id": None
    }
    latest = await col.find_one(query, sort=[("created_at", -1)])
    if not latest:
        return None
    h = latest.get("content_hash")
    return h if isinstance(h, str) else None


async def _persist_audit(doc: Dict[str, Any], *, fatal_on_write: bool = True) -> None:
    """Insert one append-only audit row.

    Stamps ``audit_id`` + ``created_at`` (if absent), pulls the previous
    row's ``content_hash`` into ``prev_hash`` (per-tenant chain), and
    computes the row's own ``content_hash`` over the chained payload.

    ``fatal_on_write``: when True (the default) and the row records a
    data write, an insert failure raises ``AuditPersistError`` so the
    caller can return a 5xx instead of pretending the run succeeded. The
    write into the source system already happened by this point — but at
    least the API response signals that the immutable record is missing.
    Set False for cases where audit loss is genuinely tolerable (e.g.
    purely read-only chat turns; we already avoid persisting those).
    """
    row = dict(doc)
    row.setdefault("audit_id", f"aud_{uuid.uuid4().hex[:16]}")
    row.setdefault("created_at", datetime.now(timezone.utc))
    col: Optional[AsyncIOMotorCollection]
    try:
        col = get_app_run_audit_col()
        row["prev_hash"] = await _fetch_prev_hash(
            col=col, tenant_id=row.get("tenant_id")
        )
    except Exception:  # noqa: BLE001
        # Either the audit collection is unavailable (test stub, mis-init)
        # or the prev_hash lookup failed. Either way we record None as the
        # chain link — the new row becomes an explicit break point, not a
        # silent reset.
        logger.exception(
            "audit collection / prev_hash unavailable; chain break recorded"
        )
        col = None
        row["prev_hash"] = None
    row["content_hash"] = _audit_content_hash(row)
    had_writes = bool(row.get("write_events")) or _timeline_has_data_write(
        row.get("timeline")
    )
    if col is None:
        if fatal_on_write and had_writes:
            raise AuditPersistError(
                f"audit collection unavailable for write run "
                f"{row.get('correlation_id')}"
            )
        return
    try:
        await col.insert_one(row)
    except Exception:  # noqa: BLE001
        logger.exception(
            "failed to persist run audit (correlation_id=%s)",
            row.get("correlation_id"),
        )
        if fatal_on_write and had_writes:
            raise AuditPersistError(
                f"audit row missing for write run {row.get('correlation_id')}"
            )


# ---------------------------------------------------------------------------
# Self-improving loop — DecisionRecord substrate (Stage 5 write-back hooks).
# See docs/citra-self-improving-loop-plan.md. These write the MUTABLE, loop-
# facing projection alongside the immutable audit ledger; the read-back poller
# (Stage 4) stamps the outcome later. A failure here is logged LOUD but is NOT
# fatal to the caller: the authoritative record already landed in the immutable
# audit chain / auto_process_decisions, from which this projection is rebuildable
# — a recoverable derived-store miss, not a swallowed failure (RULE #1).
# ---------------------------------------------------------------------------

def _build_decision_record(
    *,
    decision_id: str,
    correlation_id: str,
    mode: str,
    slug: Optional[str],
    app_doc: Dict[str, Any],
    tenant_id: Optional[str],
    context: Dict[str, Any],
    recommendation: Dict[str, Any],
    write_events: List[Dict[str, Any]],
    action_result: Dict[str, Any],
    model: Any = None,
    audit_collection: Optional[str] = None,
    audit_id: Optional[str] = None,
    decision_reason: Optional[str] = None,
    retrieval_count: Optional[int] = None,
    injected_clause_ids: Optional[List[str]] = None,
    cited_clauses: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Assemble one DecisionRecord from a committed decision.

    The officer's override delta (the gold label) and the write targets (what the
    Stage-4 read-back poller will look up) are derived from ``write_events``. Built
    via the ``DecisionRecord`` model so the persisted shape can never drift from
    the published open schema."""
    overrides: List[Dict[str, Any]] = []
    record_keys: List[Dict[str, Any]] = []
    for ev in (write_events or []):
        if not isinstance(ev, dict):
            continue
        ov = ev.get("override")
        if ov:
            overrides.append({
                "dataset_id": ev.get("dataset_id"),
                "action_id": ev.get("action_id"),
                "source_id": ev.get("source_id"),
                "override": ov,
            })
        # The target the read-back poller (Stage 4) will look up. We carry the
        # write payload so the per-app key_field/key_value can be resolved at
        # poll time (the key resolution itself is Step 2, app-specific).
        if ev.get("dataset_id") or ev.get("source_id"):
            _payload = ev.get("args")
            # Queryable key candidates: the payload's scalar values. WHICH one
            # is the record key is app-config known only at poll time, so we
            # stamp them all (capped) — this is what lets the E3 resubmission
            # join (entity_links.rejected_priors) match a prior case by its
            # record key with an indexed query instead of never matching.
            _key_values = []
            if isinstance(_payload, dict):
                _key_values = [
                    str(v).strip() for v in _payload.values()
                    if isinstance(v, (str, int)) and str(v).strip()
                    and len(str(v)) <= 128
                ][:8]
            record_keys.append({
                "source_id": ev.get("source_id"),
                "dataset_id": ev.get("dataset_id"),
                "action_id": ev.get("action_id"),
                "payload": _payload,
                "key_values": _key_values,
            })
    now = datetime.now(timezone.utc)
    rec = DecisionRecord(
        decision_id=decision_id,
        correlation_id=correlation_id,
        app_id=app_doc.get("app_id"),
        agent_id=app_doc.get("agent_id"),
        slug=slug,
        tenant_id=tenant_id,
        mode=mode,
        audit_ref={
            "collection": audit_collection,
            "correlation_id": correlation_id,
            "audit_id": audit_id,
        },
        context=context or {},
        recommendation=recommendation or {},
        overrides=overrides,
        record_keys=record_keys,
        action_result=action_result or {},
        model=model,
        retrieval_count=retrieval_count,
        # Which learned rules were in front of the model for this decision.
        # The memory-lift cohorts key on this, so if it is not carried here
        # EVERY decision reads as "cold" and the card reports a confident
        # zero lift — a wrong number, not a missing one.
        injected_clause_ids=list(injected_clause_ids or []),
        cited_clauses=list(cited_clauses or []),
        decision_reason=decision_reason,
        outcome=None,
        created_at=now,
        updated_at=now,
    )
    return rec.model_dump()


async def _persist_decision_record(doc: Dict[str, Any]) -> None:
    """Upsert one DecisionRecord (keyed by ``decision_id``) into the mutable,
    env-routed loop substrate. Loud-but-non-fatal on failure (derived store)."""
    try:
        col = get_decision_records_col()
    except Exception:  # noqa: BLE001 — substrate unavailable; recoverable from audit
        logger.error(
            "[loop] decision_records collection unavailable; DecisionRecord %s "
            "not written (recoverable from audit ledger)", doc.get("decision_id"),
        )
        return
    try:
        await col.update_one(
            {"decision_id": doc.get("decision_id")},
            {"$set": doc},
            upsert=True,
        )
    except Exception as e:  # noqa: BLE001 — LOG loud; derived store, recoverable
        logger.error(
            "[loop] failed to persist DecisionRecord %s (correlation_id=%s): %s "
            "(recoverable from audit ledger)",
            doc.get("decision_id"), doc.get("correlation_id"), e,
        )


async def _update_decision_record(decision_id: str, fields: Dict[str, Any]) -> None:
    """Patch fields onto an existing DecisionRecord (e.g. action_result at auto-
    process finalize, or the outcome verdict from the Stage-4 poller). Stamps
    ``updated_at``. Loud-but-non-fatal on failure (derived store)."""
    patch = {**fields, "updated_at": datetime.now(timezone.utc)}
    try:
        col = get_decision_records_col()
        await col.update_one({"decision_id": decision_id}, {"$set": patch})
    except Exception as e:  # noqa: BLE001 — LOG loud; derived store, recoverable
        logger.error(
            "[loop] failed to update DecisionRecord %s with %s: %s (recoverable)",
            decision_id, list(fields), e,
        )


# ---------------------------------------------------------------------------
# Self-improving loop — Stage 4: outcome read-back poller. Reads the source
# record back BY KEY via the STRUCTURED /run_query plane (never the NL planner)
# once a decision has settled, and stamps good/bad/unknown per the agent's
# OutcomePollConfig. The verdict is what write-back later learns from, so it
# must be deterministic and auditable.
# ---------------------------------------------------------------------------

def _sql_quote(value: Any) -> str:
    """Single-quote + escape a literal for an inlined SQL equality. The value is
    sourced from our OWN committed write payload (not live user input) and the
    dept-MCP validates SELECT-only server-side — but we still escape quotes."""
    return "'" + str(value).replace("'", "''") + "'"


def _build_readback_query(
    kind: str, table: str, key_field: str, key_value: Any
) -> Optional[Any]:
    """Build the per-kind STRUCTURED read-by-key query for /run_query. Never NL."""
    if kind == "sql":
        return f"SELECT * FROM {table} WHERE {key_field} = {_sql_quote(key_value)}"
    if kind == "odata":
        return {"entity": table, "$filter": f"{key_field} eq {_sql_quote(key_value)}", "$top": 1}
    if kind == "soql":
        return f"SELECT FIELDS(ALL) FROM {table} WHERE {key_field} = {_sql_quote(key_value)} LIMIT 1"
    return None  # rest / semantic not supported for a deterministic verdict


async def _classify_decision_outcome(
    *, settings: Settings, rec: Dict[str, Any], cfg: Dict[str, Any], user_jwt: Optional[str],
    value_cfg: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Read the source record back by key and classify the decision good/bad.

    Returns the outcome dict, or None when still unsettled / unreadable (so the
    record is retried next tick). FAIL-LOUD: unreadable/misconfigured paths LOG at
    error and return None — never a silent 'good' (RULE #1)."""
    from proxy_clients import call_dept_mcp_read, ProxyError

    kind = cfg.get("kind") or "sql"
    table = cfg.get("table")
    key_field = cfg.get("key_field")
    if not (table and key_field):
        logger.error(
            "[loop] outcome_poll missing table/key_field (decision=%s)",
            rec.get("decision_id"),
        )
        return None
    payload_key_field = cfg.get("payload_key_field") or key_field
    status_field = cfg.get("status_field")
    good = set(cfg.get("good_values") or [])
    bad = set(cfg.get("bad_values") or [])
    neutral = set(cfg.get("neutral_values") or [])
    hold_field = cfg.get("hold_field")

    # Locate the write target + key value from the stored record_keys.
    target = None
    for rk in (rec.get("record_keys") or []):
        if isinstance(rk, dict) and (rk.get("payload") or {}).get(payload_key_field) is not None:
            target = rk
            break
    if target is None:
        logger.error(
            "[loop] no record_key carrying %r (decision=%s)",
            payload_key_field, rec.get("decision_id"),
        )
        return None
    payload = target.get("payload") or {}
    key_value = payload.get(payload_key_field)
    written_hold = payload.get(hold_field) if hold_field else None

    query = _build_readback_query(kind, table, key_field, key_value)
    if query is None:
        logger.error(
            "[loop] unsupported outcome_poll kind=%r (decision=%s)",
            kind, rec.get("decision_id"),
        )
        return None
    try:
        resp = await call_dept_mcp_read(
            settings=settings, user_jwt=user_jwt,
            source_id=target.get("source_id"), dataset_id=target.get("dataset_id"),
            kind=kind, query=query, row_limit=1,
        )
    except ProxyError as e:
        logger.error("[loop] read-back failed (decision=%s): %s", rec.get("decision_id"), e)
        return None
    rows = (resp or {}).get("rows") or []
    if not rows:
        logger.warning(
            "[loop] read-back found no row for key=%r (decision=%s)",
            key_value, rec.get("decision_id"),
        )
        return None
    row = rows[0] if isinstance(rows[0], dict) else {}
    cur_status = row.get(status_field) if status_field else None

    label: Optional[str] = None
    reason: Optional[str] = None
    if hold_field and written_hold is not None and row.get(hold_field) != written_hold:
        label = "bad"
        reason = f"{hold_field} changed {written_hold!r}->{row.get(hold_field)!r}"
    elif cur_status in bad:
        label, reason = "bad", f"{status_field}={cur_status!r}"
    elif cur_status in good:
        label, reason = "good", f"{status_field}={cur_status!r}"
    elif cur_status in neutral:
        # Settled but no routing signal — stamp so we stop polling; write-back ignores it.
        label, reason = "neutral", f"{status_field}={cur_status!r} (no routing signal)"
    else:
        return None  # not yet settled (e.g. in_progress) — re-poll next tick
    verdict: Dict[str, Any] = {
        "label": label,
        "signal": "mcp_readback",
        "observed_at": datetime.now(timezone.utc),
        "evidence": {"reason": reason, "status": cur_status, key_field: key_value},
    }
    # ROI spine (docs/money-saved-roi-plan.md): stamp the decision's VALUE per
    # the ontology's frozen definition. value=None with no error simply means
    # this outcome carries no value under the definition; an error is stamped
    # VISIBLY (never a silent zero).
    if value_cfg:
        try:
            from value_stamping import compute_outcome_value

            _val, _verr = await compute_outcome_value(
                settings=settings, rec=rec, row=row, cur_status=cur_status,
                vs=value_cfg, user_jwt=user_jwt,
            )
            if _val is not None:
                verdict["value"] = _val
            if _verr:
                verdict["value_error"] = _verr
                logger.error("[loop] outcome value NOT stamped (decision=%s): %s",
                             rec.get("decision_id"), _verr)
        except Exception as exc:  # noqa: BLE001 — the label stamp must survive
            verdict["value_error"] = f"{type(exc).__name__}: {exc}"
            logger.exception("[loop] value computation crashed (decision=%s)",
                             rec.get("decision_id"))
    return verdict


async def _mint_poll_system_jwt(settings: Settings, rec: Dict[str, Any]) -> Optional[str]:
    """Mint the app's SYSTEM bearer for a read-back (same principal the auto-
    process write path uses), so the read passes the dept-MCP PDP. Returns the
    raw token, or None (then the read-back falls back to service-key auth)."""
    from trigger_runner import _mint_system_auth
    try:
        apps = get_apps_col()
        app_doc = await apps.find_one(
            {"app_id": rec.get("app_id"), "tenant_id": rec.get("tenant_id")}
        ) or await apps.find_one(
            {"slug": rec.get("slug"), "tenant_id": rec.get("tenant_id")}
        )
        if not app_doc:
            logger.warning(
                "[loop] app not found for read-back (decision=%s)", rec.get("decision_id")
            )
            return None
        app_spec = _load_app_spec(app_doc)
        header = _mint_system_auth(settings, app_spec, app_doc)
        return header.removeprefix("Bearer ").strip() if header else None
    except Exception:  # noqa: BLE001 — read-back can still try service-key auth
        logger.exception(
            "[loop] failed to mint system jwt for read-back (decision=%s)",
            rec.get("decision_id"),
        )
        return None


async def poll_decision_outcomes(*, settings: Settings, limit: int = 200) -> int:
    """Stage-4 outcome read-back tick. Selects committed DecisionRecords whose
    outcome is not yet observed and whose window has elapsed, reads each source
    record back by key (structured, deterministic), and stamps good/bad/unknown
    per the agent's OutcomePollConfig. Returns the number stamped this tick. Runs
    against the PROD store (the scheduler is prod-only)."""
    set_current_env("prod")
    try:
        col = get_decision_records_col()
        agents = get_agents_col()
    except RuntimeError:
        return 0
    now = datetime.now(timezone.utc)
    cursor = col.find(
        {"outcome": None, "action_result.committed": True}
    ).sort("created_at", 1).limit(limit)

    sys_jwt_cache: Dict[str, Optional[str]] = {}
    _gcache: Dict[str, Any] = {}
    stamped = 0
    delta_n = 0
    timed_out = 0
    async for rec in cursor:
        try:
            agent_id = rec.get("agent_id")
            if not agent_id:
                continue
            adoc = await agents.find_one(
                {"agent_id": agent_id, "tenant_id": rec.get("tenant_id")},
                {"agent_spec.outcome_poll": 1, "agent_spec.grounding": 1},
            )
            cfg = (((adoc or {}).get("agent_spec") or {}).get("outcome_poll")) or None
            if not cfg or not cfg.get("enabled"):
                continue
            window = float(cfg.get("window_days", 7.0))
            created = rec.get("created_at")
            if isinstance(created, datetime):
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                if now < created + timedelta(days=window):
                    continue  # too early — re-poll later
            app_id = rec.get("app_id") or ""
            if app_id not in sys_jwt_cache:
                sys_jwt_cache[app_id] = await _mint_poll_system_jwt(settings, rec)
            # Per-app resolved money definitions (stamped at publish) — cached
            # per tick like the system JWT.
            _vs_key = f"vs:{app_id}"
            if _vs_key not in _gcache:
                _vs_doc = await get_apps_col().find_one(
                    {"app_id": app_id, "tenant_id": rec.get("tenant_id")},
                    {"value_semantics": 1},
                ) or await get_apps_col().find_one(
                    {"slug": rec.get("slug"), "tenant_id": rec.get("tenant_id")},
                    {"value_semantics": 1},
                )
                _gcache[_vs_key] = (_vs_doc or {}).get("value_semantics") or None
            from value_stamping import pick_value_semantics_for_table
            verdict = await _classify_decision_outcome(
                settings=settings, rec=rec, cfg=cfg, user_jwt=sys_jwt_cache.get(app_id),
                value_cfg=pick_value_semantics_for_table(
                    _gcache.get(_vs_key), cfg.get("table")),
            )
            if verdict is None:
                # Backstop: retire records that never settle — stuck in_progress, or
                # an unreadable / unauthorized source (read-back keeps erroring). Past
                # window*3 (min window+7d) since commit, stamp a TERMINAL
                # unknown_timeout so they stop re-polling and don't starve the
                # oldest-first queue. Write-back ignores this label (neither good nor
                # bad), so it never pollutes memory.
                if isinstance(created, datetime):
                    age_days = (now - created).total_seconds() / 86400.0
                    if age_days > max(window * 3.0, window + 7.0):
                        await _update_decision_record(rec["decision_id"], {"outcome": {
                            "label": "unknown_timeout", "signal": "poll_timeout",
                            "observed_at": datetime.now(timezone.utc),
                            "evidence": {"age_days": round(age_days, 1)},
                        }})
                        timed_out += 1
                continue
            await _update_decision_record(rec["decision_id"], {"outcome": verdict})
            stamped += 1
            rec["outcome"] = verdict  # reflect the stamped outcome for polarity
            # Item ledger: the run's image/doc items inherit the case outcome —
            # an accepted photo on a decision that went BAD is a different
            # training example than one on a decision that held.
            if rec.get("correlation_id"):
                from item_records import stamp_items_outcome

                await stamp_items_outcome(rec["correlation_id"], verdict)
            # Continuous DELTA write-back: embed ONLY this just-settled decision and
            # upsert it into the agent's vector memory (bounded, oldest evicted) —
            # no full re-pull/re-embed. Two cases:
            #   • POSITIVE: good + human_approved (echo-chamber-safe).
            #   • NEGATIVE: ANY mode that turned out bad → ANTI-PATTERN ("avoid").
            # auto_process/human_direct GOOD ground via the periodic table pull.
            _is_pos = verdict.get("label") == "good" and rec.get("mode") == "human_approved"
            _is_neg = verdict.get("label") == "bad"
            # Auto-run gate: only fold into memory continuously when the user
            # enabled auto-learning for THIS app (cfg.auto_refresh, default off =
            # manual). Outcome tracking above still happens regardless.
            if (settings.auto_refresh_on_outcome and cfg.get("auto_refresh")
                    and (_is_pos or _is_neg)):
                grounding = (((adoc or {}).get("agent_spec") or {}).get("grounding")) or None
                if grounding:
                    try:
                        from models import GroundingContract
                        from grounding_refresh import (
                            loop_decision_to_sample, upsert_decision_sample,
                        )
                        contract = _gcache.get(agent_id)
                        if contract is None:
                            contract = GroundingContract.model_validate(grounding)
                            _gcache[agent_id] = contract
                        sample = loop_decision_to_sample(rec, contract)
                        if sample:
                            loop = asyncio.get_running_loop()
                            await loop.run_in_executor(
                                None,
                                lambda s=sample, aid=agent_id: upsert_decision_sample(
                                    settings=settings, agent_id=aid, sample=s,
                                    max_rows=settings.grounding_max_rows_per_agent,
                                ),
                            )
                            delta_n += 1
                    except Exception:  # noqa: BLE001 — write-back is additive
                        logger.exception(
                            "[loop] delta write-back failed for %s", rec.get("decision_id")
                        )
        except Exception:  # noqa: BLE001 — per-record; never crash the tick
            logger.exception("[loop] outcome poll failed for %s", rec.get("decision_id"))
    if stamped or timed_out:
        logger.info(
            "[loop] outcome poller stamped %d decision(s); %d folded into memory "
            "(delta); %d retired (unknown_timeout)",
            stamped, delta_n, timed_out,
        )
    return stamped


@app.post("/apps/{slug}/run", response_model=RunResponse)
async def run_app(
    slug: str,
    payload: RunRequest,
    request: Request,
    settings: Settings = Depends(get_settings),
) -> RunResponse:
    """Invoke an action defined in the app's AgentSpec.

    Phase 7: synchronous LLM call to the configured large-LLM endpoint
    (``LLM_LARGE_BASE_URL`` — OpenRouter for cloud, inference-service
    for on-prem GPU). Future phases will add streaming and the full
    HITL approval flow.
    """
    user_id = get_secure_user_id(request)
    user_tenant = get_tenant_id(request)
    # Resolve test↔prod by store BEFORE any collection access, so the routed
    # accessors + discovery helpers all target the right environment. A test
    # app routes to the test_ collections + test MCPs; a prod app to prod.
    _env = await _bind_app_env(slug)
    apps = get_apps_col()
    agents = get_agents_col()

    app_doc = await apps.find_one({"slug": slug})
    if not app_doc or not _can_render_app(app_doc, request):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="app not found")
    if app_doc.get("status") == AppStatus.ARCHIVED.value:
        raise HTTPException(status.HTTP_410_GONE, detail="app is archived")
    # Kill switch: block runs when a halt/pause covers this app (global/org/
    # dept/app). Reads + audit stay available; only the run/write path stops.
    await _enforce_automation_allowed(app_doc, what="Running this app")

    agent_doc = await agents.find_one(
        {
            "agent_id": app_doc["agent_id"],
            "tenant_id": app_doc.get("tenant_id"),
        }
    )
    if not agent_doc:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="agent spec missing for app",
        )

    app_spec = _load_app_spec(app_doc)
    agent_spec = AgentSpec.model_validate(agent_doc["agent_spec"])

    auth_header = request.headers.get("authorization") or request.headers.get(
        "Authorization"
    )
    started_at = datetime.now(timezone.utc)
    # UNIVERSAL APPROVAL — nothing auto-executes. Every run is plan-then-apply:
    # the agent produces a recommendation (planned_writes captured) and the
    # officer approves / rejects / cancels it in the queue. This holds for both
    # on-demand UI callers and trigger-precomputed runs. There is no
    # severity-based auto-apply path (no financial/legal special case): the
    # human click is the only thing that commits a write.
    plan_only = True
    # The agent run + staging + audit run as one inner coroutine, shared by the
    # blocking path and the SSE keepalive path. An on-demand run is the heaviest
    # agent turn (full tool loop) and most likely to exceed the gateway idle
    # timeout (ALB ~60s / CF ~100s); under text/event-stream we run it via
    # run_with_heartbeat so bytes flow throughout and no gateway returns 504.
    async def _do_run() -> RunResponse:
        # Re-assert env inside the (possibly task-scoped) streaming coro — see
        # the note in chat_app._do_chat.
        set_current_env(_env)
        response = await execute_run(
            settings=settings,
            app_spec=app_spec,
            agent_spec=agent_spec,
            request=payload,
            auth_header=auth_header,
            plan_only=plan_only,
        )
        if response.status == "pending_approval":
            plan_now = datetime.now(timezone.utc)
            plan_ttl_seconds = int(
                os.getenv("SMART_APP_PLAN_TTL_SECONDS", "1800")
            )
            # Stage the recommendation into the SHARED queue
            # (smartapp_workflow_staging) so an on-demand run lands in the SAME
            # officer inbox as a trigger-precomputed one — one queue, one mechanism.
            # Returns the composite ``{run_id}:{case_key}`` so an Approve from this
            # synchronous response routes to the staging-replay path.
            response.correlation_id = await _stage_recommendation(
                response=response,
                slug=slug,
                inputs=payload.inputs,
                tenant_id=user_tenant,
                published_by_user_id=user_id,
                source="queue_action",
                plan_ttl_seconds=plan_ttl_seconds,
            )
        # Append the immutable audit row for this run (every status). If the
        # row records a write, an insert failure surfaces as 5xx — silently
        # losing the immutable record of a state change is worse than telling
        # the caller the run could not be safely acknowledged.
        try:
            await _persist_audit(
                _build_audit_doc(
                    response=response,
                    slug=slug,
                    app_doc=app_doc,
                    agent_doc=agent_doc,
                    user_id=user_id,
                    tenant_id=user_tenant,
                    action=payload.action,
                    inputs=payload.inputs,
                    started_at=started_at,
                ),
            )
        except AuditPersistError as e:
            raise HTTPException(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=(
                    "run completed but the audit row could not be persisted; "
                    "do not consider the write authoritative until a follow-up "
                    f"reconciliation has run (correlation_id={response.correlation_id})"
                ),
            ) from e
        # Item ledger Write-1: persist each image/doc finding (disposition=
        # "proposed") so the officer's later accept/reject stamps a row that
        # already carries the model's verdict + artifact identity. Derived
        # store — loud-but-non-fatal (item_records logs on failure).
        _ifs = getattr(response, "item_findings", None)
        if _ifs:
            from item_records import persist_item_findings

            await persist_item_findings(
                _ifs,
                correlation_id=response.correlation_id,
                slug=slug,
                app_id=app_doc.get("app_id"),
                tenant_id=user_tenant,
                # Freeze the run's signature onto each ledger row so future
                # precedent retrieval can rank by comparability (plan §11).
                case_facets=list((response.references or {}).get("case_facets") or []),
            )
        return response

    if "text/event-stream" in (request.headers.get("accept") or "").lower():
        return StreamingResponse(
            run_with_heartbeat(_do_run, rate_limit_exc=LLMRateLimitError),
            media_type="text/event-stream",
            headers=SSE_HEADERS,
        )
    return await _do_run()


# ---------------------------------------------------------------------------
# UI-driven tool invocation (no LLM)
# ---------------------------------------------------------------------------


class ToolInvokeRequest(BaseModel):
    """Body for ``POST /apps/{slug}/tool/{tool_name}``.

    ``panel_id`` ties the call to a specific panel's ``tool_buttons``
    allowlist — so even with a leaked user JWT, an attacker cannot
    invoke a tool the BA wired only on an admin-only panel from a
    user-facing one.

    OPTIONAL because a HEADLESS app has no panels — the decision-contract,
    the SDKs, and the handler below all say "omit panel_id for headless apps";
    requiring it here made the documented headless direct-decide impossible
    (422 before the handler's headless branch could run). UI apps still fail
    loud when it's missing (see the handler's explicit check).
    """

    model_config = ConfigDict(extra="forbid")

    panel_id: Optional[str] = Field(default=None, min_length=1, max_length=120)
    arguments: Dict[str, Any] = Field(default_factory=dict)


class ToolInvokeResponse(BaseModel):
    correlation_id: str
    tool_name: str
    panel_id: Optional[str] = None   # None for headless direct-decide (no panels)
    result: Dict[str, Any]


@app.post("/apps/{slug}/chat", response_model=ChatResponse)
async def chat_app(
    slug: str,
    payload: dict,
    request: Request,
    settings: Settings = Depends(get_settings),
) -> ChatResponse:
    """Conversational turn with an app's agent — powers the agent_chat panel.

    Body: ``{"messages": [{"role": "user"|"assistant", "content": str}, ...]}``.
    Returns the ChatResponse shape: ``{"reply": str, "blocks": [...],
    "tool_calls": int}``. ``blocks`` carries any inline chart blocks the
    dashboard narrator emitted (spec + inline data); empty for plain text.
    """
    user_id = get_secure_user_id(request)
    user_tenant = get_tenant_id(request)
    # Resolve test↔prod by store before any collection access (see _bind_app_env).
    _env = await _bind_app_env(slug)

    app_doc = await get_apps_col().find_one({"slug": slug})
    # Use the runtime-aware check (same as /apps/{slug} and the panel-data
    # endpoint): citra-app-runtime is a trusted server-side fetcher carrying
    # a scope=citra-app-runtime token and no tenant of its own.
    if not app_doc or not _can_render_app(app_doc, request, user_id, user_tenant):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="app not found")
    if app_doc.get("status") == AppStatus.ARCHIVED.value:
        raise HTTPException(status.HTTP_410_GONE, detail="app is archived")

    agent_doc = await get_agents_col().find_one(
        {"agent_id": app_doc["agent_id"], "tenant_id": app_doc.get("tenant_id")}
    )
    if not agent_doc:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR, detail="agent spec missing for app"
        )

    messages = payload.get("messages")
    if not isinstance(messages, list):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail="body.messages must be a list"
        )

    app_spec = _load_app_spec(app_doc)
    agent_spec = AgentSpec.model_validate(agent_doc["agent_spec"])
    auth_header = request.headers.get("authorization") or request.headers.get(
        "Authorization"
    )
    # Per-user LLM-call rate limit: chat doesn't go through execute_run, so bind
    # the caller here + pre-flight (each _call_llm also counts + enforces).
    llm_rate_limit.set_current_user(get_secure_user_id(request))
    llm_rate_limit.check_current_user()
    started_at = datetime.now(timezone.utc)
    # The agent turn + its audit run as one inner coroutine, shared by both the
    # blocking path and the SSE keepalive path. A chat turn can run a multi-round
    # tool loop that exceeds the gateway idle timeout (ALB ~60s / CF ~100s); when
    # the caller asks for text/event-stream we run this coro under
    # run_with_heartbeat so bytes flow throughout and no gateway returns 504.
    async def _do_chat() -> dict:
        # Re-assert env inside the (possibly task-scoped) streaming coro so the
        # env-routed collection accessors + discovery helpers see the right
        # environment regardless of how the SSE body iterator is scheduled.
        set_current_env(_env)
        try:
            result = await chat_with_agent(
                settings=settings,
                app_spec=app_spec,
                agent_spec=agent_spec,
                messages=messages,
                auth_header=auth_header,
            )
        except HTTPException:
            raise
        except LLMRateLimitError:
            raise  # mapped to 429 — global handler (blocking) / SSE wrapper (stream)
        except ChatProducedNoReply as exc:
            # A turn that produced nothing is a FAILED call, not a 200 carrying
            # the string "(no response)" — that was unreadable to the UI and
            # indistinguishable from success to every API consumer. Diagnosed
            # condition, so warn with the reason rather than dumping a trace.
            logger.warning("chat produced no reply for app %s: %s", slug, exc)
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=str(exc))
        except Exception as exc:  # noqa: BLE001
            logger.exception("chat failed for app %s", slug)
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY, detail=f"chat failed: {exc}"
            )

        # Persist an audit row whenever the chat turn touched (or tried to
        # touch) source-system data. Read-only chat is not audited — the LLM
        # answers don't mutate state, and indexing every Q&A would balloon
        # the ledger. Writes (allowed or blocked) always land here, with the
        # same content_hash + prev_hash chain as /run.
        write_events = (result or {}).get("write_events") or []
        if write_events:
            await _persist_audit(
                {
                    "correlation_id": f"chat_{uuid.uuid4().hex[:12]}",
                    "slug": slug,
                    "app_id": app_doc.get("app_id"),
                    "agent_id": app_doc.get("agent_id"),
                    "agent_spec_version": (agent_doc.get("agent_spec") or {}).get(
                        "version"
                    ),
                    "tenant_id": user_tenant or app_doc.get("tenant_id"),
                    "requested_by": user_id,
                    "action": "_chat",
                    "inputs": {
                        "last_user_message": next(
                            (
                                m.get("content")
                                for m in reversed(messages)
                                if m.get("role") == "user"
                            ),
                            "",
                        )
                    },
                    "status": "completed",
                    "decision": None,
                    "reasoning": (result or {}).get("reply"),
                    "citations": [],
                    "references": {"tool_calls": []},
                    "write_events": write_events,
                    "outputs": {"reply": (result or {}).get("reply")},
                    "timeline": [{"step": "chat_turn", "status": "ok"}] + [
                        {
                            "step": "data_write",
                            "status": ev.get("status"),
                            "dataset_id": ev.get("dataset_id"),
                            "action_id": ev.get("action_id"),
                            "tool": ev.get("tool"),
                        }
                        for ev in write_events
                    ],
                    "error": None,
                    "model": None,
                    "usage": {},
                    "duration_ms": int(
                        (datetime.now(timezone.utc) - started_at).total_seconds() * 1000
                    ),
                    "trace_id": (result or {}).get("trace_id"),
                    "audit_missing": True,  # chat has no decision block by design
                    "surface": "smartapp_chat",
                }
            )
        return result

    if "text/event-stream" in (request.headers.get("accept") or "").lower():
        return StreamingResponse(
            run_with_heartbeat(_do_chat, rate_limit_exc=LLMRateLimitError),
            media_type="text/event-stream",
            headers=SSE_HEADERS,
        )
    return await _do_chat()


@app.post("/apps/{slug}/tool/{tool_name}", response_model=ToolInvokeResponse)
async def invoke_tool(
    slug: str,
    tool_name: str,
    payload: ToolInvokeRequest,
    request: Request,
    settings: Settings = Depends(get_settings),
) -> ToolInvokeResponse:
    """Invoke a ``tools_v2`` tool from a panel button — bypassing the LLM.

    Use cases (the BA already knows the tool + args; LLM reasoning would
    add cost and latency without value):

      * "Refresh" buttons that re-run a fixed RAG / MCP query.
      * "Submit for Approval" buttons that kick a workflow whose inputs
        come straight from the form fields.
      * Idempotent verifications (e.g. "Verify Policy") that take a
        single field as the only argument.

    Auth gates (in order):

      1. User passes the standard JWT middleware + tenant check.
      2. Panel ``panel_id`` exists on the AppSpec.
      3. Panel ``tool_buttons[*].tool_name`` lists ``tool_name`` —
         a leaked-token attacker can't escape the panel's allowlist.
      4. Tool exists in ``agent_spec.tools_v2`` and the deployment can
         actually serve its kind (mcp_enabled, ocr_enabled, ...).

    The request's ``Authorization`` is forwarded to the dispatcher so
    the proxy clients can pass the user JWT to discovery + dept-MCP for
    visibility filtering.
    """
    user_id = get_secure_user_id(request)
    user_tenant = get_tenant_id(request)
    # Resolve test↔prod by store before any collection access — a direct write
    # from a test app must hit the TEST MCP, not prod (see _bind_app_env).
    await _bind_app_env(slug)

    apps = get_apps_col()
    agents = get_agents_col()
    app_doc = await apps.find_one({"slug": slug})
    if not app_doc or not _can_render_app(app_doc, request):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="app not found")
    if app_doc.get("status") == AppStatus.ARCHIVED.value:
        raise HTTPException(status.HTTP_410_GONE, detail="app is archived")
    # Kill switch: direct tool actions (mcp_action commits) stop under a halt.
    await _enforce_automation_allowed(app_doc, what="This action")
    agent_doc = await agents.find_one(
        {
            "agent_id": app_doc["agent_id"],
            "tenant_id": app_doc.get("tenant_id"),
        }
    )
    if not agent_doc:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="agent spec missing for app",
        )

    app_spec = _load_app_spec(app_doc)
    agent_spec = AgentSpec.model_validate(agent_doc["agent_spec"])

    # Resolve panel + verify the button is on this panel's allowlist — UI apps
    # ONLY. A HEADLESS app has no panels; its gate is the agent's tool dispatch
    # table (built below from agent_spec.tools_v2), so an external/custom UI can
    # invoke a declared mcp_action directly — a true "direct assign" with NO LLM —
    # and the human_direct DecisionRecord + audit fire just as in the SmartApp UI.
    button = None
    if not getattr(app_spec, "headless", False):
        if not payload.panel_id:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "error": "panel_id_required",
                    "hint": "panel_id is required for UI apps; only headless apps may omit it",
                },
            )
        panel = next((p for p in app_spec.all_panels if p.id == payload.panel_id), None)
        if panel is None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                detail={"error": "panel_not_found", "panel_id": payload.panel_id},
            )
        button = next(
            (
                b for b in (getattr(panel, "tool_buttons", []) or [])
                if b.tool_name == tool_name
            ),
            None,
        )
        # A form panel may bind a direct-write tool via ``on_submit.tool_name``
        # instead of a tool_button — that is also an allowed direct-write entry
        # point for THIS panel (gated by the panel's own permissions; the form's
        # fields are the args). It carries no per-button roles/confirm.
        _on_submit = getattr(panel, "on_submit", None)
        form_submit_tool = getattr(_on_submit, "tool_name", None) if _on_submit else None
        if button is None and form_submit_tool != tool_name:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                detail={
                    "error": "tool_not_in_panel_allowlist",
                    "panel_id": payload.panel_id,
                    "tool_name": tool_name,
                },
            )

        # Per-button role gate (optional). On top of the panel allowlist + the
        # panel's own permissions, a button may restrict itself to specific roles
        # (e.g. only supervisors may fire "assign" / "close"). Form-submit binds
        # (button is None) gate via the panel permissions only.
        if button is not None and button.roles:
            user_roles = set(get_user_roles(request) or [])
            if not (set(button.roles) & user_roles):
                raise HTTPException(
                    status.HTTP_403_FORBIDDEN,
                    detail={
                        "error": "role_not_permitted_for_button",
                        "tool_name": tool_name,
                        "required_roles": list(button.roles),
                    },
                )

    # Build dispatch table for the AgentSpec — same path the LLM uses.
    _, dispatch_table = build_openai_tools_from_tools_v2(
        agent_spec=agent_spec,
        app_spec=app_spec,
        settings=settings,
    )
    if tool_name not in dispatch_table:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail={
                "error": "tool_not_in_agent_spec_or_kind_disabled",
                "tool_name": tool_name,
            },
        )

    auth_header = request.headers.get("authorization") or request.headers.get(
        "Authorization"
    )
    # Merge button's pre-bound args with caller-supplied args; caller
    # wins per-key so the renderer can substitute ``{param.x}`` templates
    # (tool_button) or pass the form's field values (form on_submit).
    merged_args = {**((button.args if button else {}) or {}), **(payload.arguments or {})}

    correlation_id = f"tool_{uuid.uuid4().hex[:12]}"
    started_at = datetime.now(timezone.utc)
    result = await dispatch_tools_v2_call(
        settings=settings,
        agent_spec=agent_spec,
        app_spec=app_spec,
        dispatch_table=dispatch_table,
        tool_name=tool_name,
        arguments=merged_args,
        auth_header=auth_header,
    )

    # AUDIT — a direct (no-LLM) tool invocation is a real action by a real
    # user and MUST be recorded (rule A-01: who / what / when). This is the
    # "classic write action" path — no agent in the loop — so the only record
    # of it is this row. We audit EVERY direct invocation; an ``mcp_action``
    # (source-system write) additionally carries a write_event and makes an
    # audit-persist failure fatal (never report a committed write whose ledger
    # row is missing). Read-only tools (rag / mcp query / refresh) audit
    # best-effort and never fail the read.
    _tool_kind = next(
        (
            getattr(t, "kind", None)
            for t in (agent_spec.tools_v2 or [])
            if getattr(t, "name", None) == tool_name
        ),
        None,
    )
    _is_write = _tool_kind == "mcp_action"
    # For a WRITE, success requires an explicit positive ack (ok=true, no
    # error) — same strictness as the plan-then-apply replay — so a write that
    # returns {"ok": false} (no "error" key) is recorded as failed, not a
    # silent success. Reads (rag / mcp query) have no "ok" field, so they only
    # fail on an explicit error.
    if _is_write:
        _ok = bool(isinstance(result, dict) and result.get("ok") and not result.get("error"))
    else:
        _ok = not (isinstance(result, dict) and result.get("error"))
    _now = datetime.now(timezone.utc)
    _write_events = (
        [{
            "tool": tool_name,
            "kind": "direct",
            "args": merged_args,
            "result": result,
            "status": "ok" if _ok else "error",
        }]
        if _is_write
        else []
    )
    try:
        await _persist_audit(
            {
                "correlation_id": correlation_id,
                "slug": slug,
                "app_id": app_doc.get("app_id"),
                "agent_id": app_doc.get("agent_id"),
                "agent_spec_version": (agent_doc.get("agent_spec") or {}).get("version"),
                "tenant_id": user_tenant,
                "requested_by": user_id,
                "action": tool_name,
                "inputs": merged_args,
                "status": "completed" if _ok else "failed",
                "decision": None,
                "reasoning": None,
                "citations": [],
                "references": {},
                "write_events": _write_events,
                "write_count": len(_write_events),
                "audit_missing": False,
                "outputs": result if isinstance(result, dict) else {"result": result},
                "timeline": [
                    {"step": "direct_tool", "status": "ok" if _ok else "error", "tool": tool_name}
                ],
                "error": (result.get("error") if isinstance(result, dict) and not _ok else None),
                "model": None,
                "usage": {},
                "duration_ms": int((_now - started_at).total_seconds() * 1000),
                "surface": "smartapp_tool_direct",
                "created_at": _now,
            },
            fatal_on_write=_is_write,
        )
    except AuditPersistError as e:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "tool committed but its audit row could not be persisted; do "
                "not consider the write authoritative until a reconciliation "
                f"has run (correlation_id={correlation_id})"
            ),
        ) from e

    # Self-improving loop: a DIRECT (no-LLM) human write is a real decision —
    # the purest expert signal (the officer chose without a recommendation).
    # Record it so the outcome poller can validate it by SoR read-back and it
    # joins the decision ledger (and can trigger auto-refresh). mode="human_direct",
    # no recommendation. Grounding write-back EXCLUDES human_direct (it carries the
    # action payload, not the rich case context) — the periodic decision-history
    # table pull grounds direct decisions with full context. Non-fatal/derived.
    if _is_write and _ok:
        try:
            _ad = next(
                (t for t in (agent_spec.tools_v2 or [])
                 if getattr(t, "name", None) == tool_name
                 and getattr(t, "kind", None) == "mcp_action"),
                None,
            )
            _dr_wevs = [{
                "tool": tool_name, "kind": "apply", "args": merged_args,
                "result": result, "status": "ok",
                "dataset_id": getattr(_ad, "dataset_id", None),
                "action_id": getattr(_ad, "action_id", None),
                "source_id": getattr(_ad, "source_id", None),
                "override": None,
            }]
            _dr = _build_decision_record(
                decision_id=f"dr_{correlation_id}",
                correlation_id=correlation_id,
                mode="human_direct",
                slug=slug,
                app_doc=app_doc,
                tenant_id=user_tenant or app_doc.get("tenant_id"),
                context=merged_args,
                recommendation={"action": tool_name, "decision": None, "reasoning": None,
                                "planned_writes": [{"payload": merged_args,
                                                    "dataset_id": getattr(_ad, "dataset_id", None),
                                                    "action_id": getattr(_ad, "action_id", None),
                                                    "source_id": getattr(_ad, "source_id", None)}]},
                write_events=_dr_wevs,
                action_result={"committed": True, "applied_count": 1,
                               "status": "completed", "requested_by": user_id},
                model=None,
                audit_collection=get_settings().app_run_audit_collection,
            )
            await _persist_decision_record(_dr)
        except Exception:  # noqa: BLE001 — loop substrate is derived/non-fatal
            logger.exception(
                "[loop] human_direct DecisionRecord failed for %s (non-fatal)",
                correlation_id,
            )

    return ToolInvokeResponse(
        correlation_id=correlation_id,
        tool_name=tool_name,
        panel_id=payload.panel_id,
        result=result,
    )


def _case_ref_of(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """WHICH record a correction was about — {dataset_id, keys}.

    corrections/analysis_rubrics have accepted `case_ref` since the ledger was
    built and nothing ever passed it, so every correction recorded so far says
    what the officer decided and never which application they were looking at.
    A judgement's evidence could show the case's FACETS ("amount_band:lt_500000")
    but not the case, which is the first thing anyone asks of it.

    The POINTER, deliberately, not the record. Returning the row would copy
    another customer's data into a third party's decision context — the facets
    are non-identifying by construction and a loan application is not — and it
    would age badly, since the record has changed since the decision was applied
    to it. An agent that genuinely needs the detail already has read tools and
    a key to use them with.
    """
    keys = row.get("case_natural_key")
    writes = row.get("planned_writes") or []
    ds = None
    for w in writes:
        if isinstance(w, dict) and w.get("dataset_id"):
            ds = w["dataset_id"]
            break
    if not keys and not ds:
        return None
    return {"dataset_id": ds, "keys": keys}


async def _replay_planned_writes_with_overrides(
    *,
    settings: Settings,
    planned_writes: List[Dict[str, Any]],
    overrides: List[Dict[str, Any]],
    correlation_id: str,
    user_jwt: Optional[str],
    request: Request,
    app_spec: AppSpec,
) -> Tuple[bool, int, List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Replay a recommendation's ``planned_writes`` against the dept-MCP.

    Shared by the unified recommendation-queue approve path. Applies any
    officer field-overrides (``overrides[i]`` aligned to ``planned_writes[i]``)
    but ONLY for fields the write declared in ``editable_fields`` and only to
    values inside that field's ``OptionsSource`` allow-list — an officer edit
    cannot fabricate an out-of-list value or touch a non-editable field. An
    overridden payload is re-validated with a ``dry_run`` precheck before
    commit. Each write COMMITS — in the test environment against the test MCP
    (so the BA validates the real effect), and in prod against the prod MCP.

    Returns ``(overall_ok, applied_count, timeline, write_events)``.
    """
    from proxy_clients import call_dept_mcp_execute_action, ProxyError

    auth_header = f"Bearer {user_jwt}" if user_jwt else None
    timeline_apply: List[Dict[str, Any]] = [
        {"step": "approval_gate", "status": "approved"}
    ]
    write_events_apply: List[Dict[str, Any]] = []
    overall_ok = True
    applied_count = 0
    for _i, pw in enumerate(planned_writes):
        sid = pw.get("source_id")
        did = pw.get("dataset_id")
        aid = pw.get("action_id")
        pw_payload = pw.get("payload") or {}
        tool_name = pw.get("tool") or "execute_action"
        _ov = (
            overrides[_i]
            if _i < len(overrides) and isinstance(overrides[_i], dict)
            else {}
        )
        _override_delta: Dict[str, Any] = {}
        if _ov:
            _roles = set(get_user_roles(request) or [])
            _efields = {
                f.get("name"): f
                for f in (pw.get("editable_fields") or [])
                if f.get("editable", True)
            }
            for _fn, _fv in _ov.items():
                fspec = _efields.get(_fn)
                if fspec is None:
                    raise HTTPException(
                        status.HTTP_400_BAD_REQUEST,
                        detail=(
                            f"field '{_fn}' is not officer-editable for "
                            f"action '{aid}'"
                        ),
                    )
                _ebr = set(fspec.get("editable_by_roles") or [])
                if _ebr and not (_ebr & _roles):
                    raise HTTPException(
                        status.HTTP_403_FORBIDDEN,
                        detail=f"your role may not override '{_fn}'",
                    )
                _orig = pw_payload.get(_fn)
                _cv: Any = _fv
                if isinstance(_orig, bool):
                    _cv = str(_fv).strip().lower() in ("true", "1", "yes")
                elif isinstance(_orig, int) and not isinstance(_orig, bool):
                    try:
                        _cv = int(_fv)
                    except (TypeError, ValueError):
                        _cv = _fv
                elif isinstance(_orig, float):
                    try:
                        _cv = float(_fv)
                    except (TypeError, ValueError):
                        _cv = _fv
                if str(_orig) == str(_cv):
                    continue
                _allowed_vals = await _resolve_allowed_option_values(
                    settings=settings,
                    app_spec=app_spec,
                    fspec=fspec,
                    payload=pw_payload,
                    agent_options=pw.get("_options"),
                    auth_header=auth_header,
                )
                if _allowed_vals is not None and str(_cv) not in _allowed_vals:
                    raise HTTPException(
                        status.HTTP_400_BAD_REQUEST,
                        detail=f"override value for '{_fn}' is not an allowed option",
                    )
                _override_delta[_fn] = {"from": _orig, "to": _cv}
                pw_payload = {**pw_payload, _fn: _cv}
        # APP-OWNED OVERLAY planned write: app-local (no dept-MCP source_id) — its
        # dataset_id names a smart_app_records data_source. Commit it via
        # write_app_record (the same env-routed path the direct write uses)
        # instead of the dept-MCP execute path, which would reject it for the
        # missing source_id.
        _ov_ds = next(
            (d for d in (getattr(app_spec, "data_sources", None) or [])
             if getattr(d, "id", None) == did
             and getattr(d, "type", None) == "smart_app_records"),
            None,
        )
        if _ov_ds is not None:
            from data_tools import write_app_record as _war
            _ov_res = await _war(
                ds=_ov_ds, payload=pw_payload, auth_header=auth_header,
                tenant_id=get_tenant_id(request) or "", app_spec=app_spec, plan_only=False,
            )
            _ov_ok = bool(isinstance(_ov_res, dict) and _ov_res.get("ok")
                          and not _ov_res.get("error"))
            if not _ov_ok:
                overall_ok = False
            timeline_apply.append({
                "step": "apply", "tool": tool_name,
                "status": "ok" if _ov_ok else "error", "overlay": True,
            })
            write_events_apply.append({
                "tool": tool_name, "kind": "apply", "args": pw_payload,
                "result": _ov_res, "status": "ok" if _ov_ok else "error",
                "dataset_id": did, "action_id": aid, "source_id": None,
                # carry the override delta (parity with the dept-MCP branch) so
                # the corrections loop sees overlay corrections too.
                "override": _override_delta or None,
            })
            continue
        if not (sid and did and aid):
            overall_ok = False
            timeline_apply.append({
                "step": "apply",
                "status": "error",
                "detail": "planned_write missing source_id/dataset_id/action_id",
                "tool": tool_name,
            })
            write_events_apply.append({
                "tool": tool_name,
                "kind": "apply",
                "args": pw_payload,
                "result": {"error": "incomplete planned_write"},
                "status": "error",
                "dataset_id": did,
                "action_id": aid,
                "source_id": sid,
            })
            continue
        plan_key = pw.get("idempotency_key")
        apply_key = (
            f"{plan_key}:apply:{correlation_id}"
            if plan_key
            else f"apply:{correlation_id}:{aid}"
        )
        if _override_delta:
            try:
                pre = await call_dept_mcp_execute_action(
                    settings=settings, user_jwt=user_jwt,
                    source_id=sid, dataset_id=did, action_id=aid,
                    payload=pw_payload, dry_run=True,
                    idempotency_key=f"{apply_key}:precheck",
                )
                pre_ok = bool(
                    isinstance(pre, dict) and pre.get("ok") and not pre.get("error")
                )
            except ProxyError as e:
                pre, pre_ok = {"error": str(e), "code": e.code}, False
            if not pre_ok:
                overall_ok = False
                timeline_apply.append({
                    "step": "apply", "status": "error",
                    "detail": "override failed re-validation",
                    "dataset_id": did, "action_id": aid, "tool": tool_name,
                })
                write_events_apply.append({
                    "tool": tool_name, "kind": "apply", "args": pw_payload,
                    "result": pre, "status": "error", "dataset_id": did,
                    "action_id": aid, "source_id": sid,
                    "override": _override_delta,
                })
                continue
        try:
            r = await call_dept_mcp_execute_action(
                settings=settings,
                user_jwt=user_jwt,
                source_id=sid,
                dataset_id=did,
                action_id=aid,
                payload=pw_payload,
                # Writes COMMIT on approve — against the test MCP in the test
                # environment (so the BA validates the real effect end-to-end)
                # and against the prod MCP in prod. The human approval click is
                # the commit gate; there is no dry-run replay path.
                dry_run=False,
                idempotency_key=apply_key,
            )
            ok_w = bool(
                isinstance(r, dict) and r.get("ok") and not r.get("error")
            )
        except ProxyError as e:
            r = {"error": str(e), "code": e.code}
            ok_w = False
        applied_count += 1 if ok_w else 0
        if not ok_w:
            overall_ok = False
        # Carry WHY, like the two apply failures above already do. Without it a
        # failed commit surfaced as status=failed with error=null: the officer
        # clicked Apply, the source of record did not change, and nothing on
        # screen said what went wrong. (Seen for real — a rejection_reason
        # longer than the column, which the dry run had accepted.)
        _apply_detail = None
        if not ok_w:
            _apply_detail = str(
                (r or {}).get("error") if isinstance(r, dict) else r
            )[:400] or "write failed"
        timeline_apply.append({
            "step": "apply",
            "status": "ok" if ok_w else "error",
            "dataset_id": did,
            "action_id": aid,
            "tool": tool_name,
            **({"detail": _apply_detail} if _apply_detail else {}),
        })
        write_events_apply.append({
            "tool": tool_name,
            "kind": "apply",
            "args": pw_payload,
            "result": r,
            "status": "ok" if ok_w else "error",
            "dataset_id": did,
            "action_id": aid,
            "source_id": sid,
            "override": _override_delta or None,
        })
    return overall_ok, applied_count, timeline_apply, write_events_apply


async def _approve_workflow_staging(
    *,
    slug: str,
    correlation_id: str,
    payload: ApproveRequest,
    request: Request,
    settings: Settings,
    approver_id: str,
    approver_tenant: Optional[str],
) -> RunResponse:
    """Resolve a recommendation staging row (approve / reject / cancel).

    Backs the unified officer queue: rows here are written EITHER by an app
    trigger (eager precompute) OR by the on-demand ``/run`` path (lazy). The apply path is the shared plan-then-apply replay
    (``_replay_planned_writes_with_overrides``) — iterate ``planned_writes``,
    apply any officer field-overrides under the editable-fields allow-list,
    fire each through ``call_dept_mcp_execute_action``, then stamp the row
    ``applied`` / ``rejected`` / ``cancelled`` / ``stale`` and append an
    audit_trail entry. The self-approval gate does NOT apply (officer-as-
    reviewer is the design — same person who triages can approve).
    """
    # Composite key: "{workflow_execution_id}:{case_natural_key}". A
    # natural_key may contain its own colons (e.g. "CONSUMER:K123"), so
    # we split on the FIRST colon only — workflow_execution_id is opaque
    # but generated without colons by the engine.
    # Kill switch: during a halt/pause, block approve→commit too (a freeze must
    # stop human-approved writes as well). Load the app_doc so DEPT-scoped halts
    # are honored (without it, _app_dept_ids sees no depts and a dept freeze
    # would let approvals through). global / org / app / dept all apply.
    _app_for_halt = await get_apps_col().find_one({"slug": slug})
    await _enforce_automation_allowed(
        _app_for_halt, slug=slug, tenant_id=approver_tenant, what="Approving this decision"
    )
    exec_id, _, case_key = correlation_id.partition(":")
    if not exec_id or not case_key:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=(
                "workflow-staging correlation_id must be "
                "'{workflow_execution_id}:{case_natural_key}'"
            ),
        )

    staging_col = get_workflow_staging_col()
    row = await staging_col.find_one(
        {"workflow_execution_id": exec_id, "case_natural_key": case_key}
    )
    if not row or row.get("slug") != slug:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail="workflow staging row not found"
        )

    # Tenant defence-in-depth — identical rule to queue-action approve.
    if (
        row.get("tenant_id")
        and approver_tenant
        and row["tenant_id"] != approver_tenant
    ):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, detail="cross-tenant approval forbidden"
        )

    current_status = row.get("status")
    # Idempotency: an already-applied row returns the prior result rather
    # than firing the writes a second time. Without this guard a duplicate
    # click / browser retry would push the same writes through the source
    # MCP twice (idempotency_keys help, but we should not even ATTEMPT a
    # re-write — the audit_trail entry from the first apply IS the result).
    if current_status == "applied":
        prior = next(
            (
                e
                for e in (row.get("audit_trail") or [])
                if e.get("decision") == "approved"
            ),
            None,
        )
        return RunResponse(
            correlation_id=correlation_id,
            status="completed",
            outputs={
                "reason": "already applied",
                "applied_by": row.get("applied_by"),
                "resolved_at": (
                    row.get("resolved_at").isoformat()
                    if isinstance(row.get("resolved_at"), datetime)
                    else None
                ),
            },
            timeline=[{"step": "approval_gate", "status": "already_applied"}],
            write_events=(prior or {}).get("write_events") or [],
            planned_writes=row.get("planned_writes") or [],
        )
    if current_status in ("rejected", "expired", "stale"):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"workflow staging row already {current_status}",
        )
    if not (
        isinstance(current_status, str)
        and current_status.startswith("pending_")
    ):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"workflow staging row in unexpected status: {current_status}",
        )

    # Plan TTL — staging rows may carry expires_at. A stale plan is
    # rejected with 410 GONE rather than fired, same posture as the
    # queue-action path.
    expires_at = row.get("expires_at")
    if isinstance(expires_at, datetime):
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        now_utc = datetime.now(timezone.utc)
        if now_utc > expires_at:
            await staging_col.update_one(
                {
                    "workflow_execution_id": exec_id,
                    "case_natural_key": case_key,
                },
                {
                    "$set": {"status": "expired", "resolved_at": now_utc},
                    "$push": {
                        "audit_trail": {
                            "at": now_utc,
                            "actor": approver_id,
                            "decision": "expired",
                            "note": "plan expired before approval",
                        }
                    },
                },
            )
            raise HTTPException(
                status.HTTP_410_GONE,
                detail="staging plan expired — re-triage required",
            )

    apps = get_apps_col()
    app_doc = await apps.find_one(
        {"slug": slug, "tenant_id": row.get("tenant_id")}
    )
    if not app_doc:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="app missing for staging row",
        )

    # ── Server-side per-item review gate (defence-in-depth) ────────────────
    # The Citra web UI blocks Apply until every non-fraud item finding is
    # reviewed, but a HEADLESS integrator calls this endpoint directly — the
    # gate the decision-contract promises must therefore be enforced HERE, not
    # only client-side. Case (fraud) findings are evidence-only and never gate
    # (identical rule to PanelRenderer + the served contract). Soft/none gates
    # skip; reject/cancel commit nothing, so only 'approve' is gated.
    if payload.decision == "approve":
        _gate = (app_doc.get("app_spec") or {}).get("item_review_gate") or "hard"
        if _gate == "hard":
            from item_records import _col as _items_col

            # Key on the FULL composite correlation_id ("<exec_id>:<case_key>") —
            # that is what /run returns and what item_records.persist_item_findings
            # stamps on every ledger row (main.py: persist_item_findings(
            # correlation_id=response.correlation_id)). Querying the pre-":" half
            # (exec_id) matches NOTHING, and a query that matches nothing is
            # indistinguishable from "nothing pending" — so the gate silently never
            # fired and a headless caller committed straight past it (verified in
            # prod 2026-07-16). Fail-loud only works if you query the real key.
            _pending_items = await _items_col().find(
                {
                    "correlation_id": correlation_id,
                    "slug": slug,
                    "disposition": "proposed",
                    "modality": {"$ne": "case"},
                },
                {"item_id": 1, "_id": 0},
            ).to_list(length=50)
            if _pending_items:
                _ids = [str(p.get("item_id")) for p in _pending_items]
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    detail=(
                        f"item_review_gate=hard: {len(_ids)} item finding(s) not yet "
                        f"reviewed ({', '.join(_ids[:5])}{'…' if len(_ids) > 5 else ''}) — "
                        f"disposition each via POST /apps/{slug}/items/{{item_id}}/feedback "
                        "before approving"
                    ),
                )

    # Integrity (display == commit): the officer must approve the SAME proposal
    # that was shown. If the client echoes the hash it displayed and the staged
    # plan has since changed (e.g. a re-run overwrote it), reject as stale rather
    # than silently committing different values. Only enforced when provided.
    if payload.decision == "approve" and payload.expected_plan_hash:
        if compute_plan_hash(row.get("planned_writes") or []) != payload.expected_plan_hash:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail=(
                    "this recommendation changed since you viewed it — refresh "
                    "and re-review before approving"
                ),
            )

    if payload.decision in ("reject", "cancel"):
        # Reject = "this recommendation is wrong"; cancel = "dismiss / not
        # actioning". Both are terminal non-apply states — nothing is written
        # to the source. They differ only in the recorded status/label so the
        # audit can tell a rejection from a withdrawal.
        _terminal_status = "cancelled" if payload.decision == "cancel" else "rejected"
        _label = "cancelled" if payload.decision == "cancel" else "rejected"
        _reason = f"approver {_label}"
        now_utc = datetime.now(timezone.utc)
        await staging_col.update_one(
            {
                "workflow_execution_id": exec_id,
                "case_natural_key": case_key,
            },
            {
                "$set": {
                    "status": _terminal_status,
                    "resolved_at": now_utc,
                    "applied_by": approver_id,
                },
                "$push": {
                    # Uniform shape with the approval audit entry —
                    # consumers (analytics, ledger replay) can iterate
                    # audit_trail and project the same keys regardless
                    # of decision. applied_count + write_events are zero/
                    # empty here (nothing committed); their presence
                    # keeps the schema flat.
                    "audit_trail": {
                        "at": now_utc,
                        "actor": approver_id,
                        "decision": _label,
                        "note": payload.note,
                        # WHY — the causal signal fed back to the model so it
                        # learns from rejections (OFFICER CORRECTIONS prefetch).
                        "decision_reason": payload.decision_reason,
                        "applied_count": 0,
                        "write_events": [],
                    }
                },
            },
        )
        await _persist_audit(
            {
                "correlation_id": correlation_id,
                "slug": slug,
                "app_id": app_doc.get("app_id"),
                "agent_id": None,
                "agent_spec_version": None,
                "tenant_id": row.get("tenant_id"),
                "requested_by": None,
                "approver_id": approver_id,
                "action": "workflow_staging_review",
                "inputs": {
                    "workflow_execution_id": exec_id,
                    "case_natural_key": case_key,
                },
                "status": "failed",
                "decision": _label,
                "reasoning": payload.note or _reason,
                "citations": [],
                "references": {},
                "outputs": {
                    "reason": _reason,
                    "note": payload.note,
                },
                "timeline": [{"step": "approval_gate", "status": _label}],
                "error": _reason,
                "model": None,
                "usage": {},
                "duration_ms": None,
            }
        )
        # Record a REJECTED decision — no write to read back, but the officer's
        # reason is the gold "this recommendation was wrong" signal the decision-
        # learning loop trains on. Cancel = withdrawal (no signal, and no matching
        # DecisionRecord mode), so only rejections are recorded. Non-fatal.
        if payload.decision == "reject":
            try:
                _dr = _build_decision_record(
                    decision_id=f"dr_{correlation_id}",
                    correlation_id=correlation_id,
                    mode="human_rejected",
                    decision_reason=payload.decision_reason,
                    slug=slug,
                    app_doc=app_doc,
                    tenant_id=row.get("tenant_id"),
                    retrieval_count=row.get("retrieval_count"),
                    injected_clause_ids=row.get("injected_clause_ids"),
                    cited_clauses=row.get("cited_clauses"),
                    context=row.get("display_context") or {},
                    recommendation={
                        "action": None,
                        "decision": row.get("llm_recommendation_text"),
                        "reasoning": row.get("llm_reasoning"),
                        "planned_writes": row.get("planned_writes") or [],
                        "cited_precedents": row.get("cited_precedents") or [],
                        "case_facets": row.get("case_facets") or [],
                        "cited_clauses": row.get("cited_clauses") or [],
                        # The factor vector, so drift analysis can eventually
                        # ask which factors actually discriminated. The ledger
                        # already inherits the OUTCOME, so this is the only
                        # missing half of that join.
                        "scorecard": row.get("scorecard"),
                    },
                    write_events=[],
                    action_result={
                        "committed": False,
                        "applied_count": 0,
                        "status": "rejected",
                        "approver_id": approver_id,
                    },
                    model=None,
                    audit_collection=get_settings().app_run_audit_collection,
                )
                await _persist_decision_record(_dr)
                # DECISION rubric: a reject reason is a policy-level lesson —
                # fold it into the app's standing decision criteria (the record
                # analogue of the item rubric). Keyed by the APP's own org via
                # rubric_tenant_for_app so the fold and the run-time read always
                # agree (a row-tenant key can diverge from the reader's key).
                # fold_decision_feedback is fully non-raising. Cancel is
                # excluded by the enclosing reject-only branch.
                from analysis_rubrics import fold_decision_feedback, rubric_tenant_for_app

                await fold_decision_feedback(
                    tenant_id=rubric_tenant_for_app(app_doc),
                    app_slug=slug,
                    actor=approver_id,
                    correlation_id=correlation_id,
                    reason=payload.decision_reason or payload.note,
                    recommendation=row.get("llm_recommendation_text"),
                    # Clause-memory evidence (Phase A). The facets and clause
                    # ids were stamped on the staging row at /run time, so the
                    # correction carries the signature the model actually saw —
                    # never a recomputed one (a later ontology edit must not
                    # rewrite history).
                    reason_code=payload.reason_code,
                    contested_fields=payload.contested_fields,
                    case_ref=_case_ref_of(row),
                    case_facets=row.get("case_facets"),
                    signature_version=row.get("signature_version"),
                    injected_clause_ids=row.get("injected_clause_ids"),
                    cited_clause_ids=[
                        c.get("clause_id")
                        for c in (row.get("cited_clauses") or [])
                        if isinstance(c, dict) and c.get("clause_id")
                    ],
                    overruled_clause_ids=[
                        c.get("clause_id")
                        for c in (row.get("cited_clauses") or [])
                        if isinstance(c, dict) and c.get("clause_id")
                        and c.get("relation") == "overruled"
                    ],
                )
            except Exception:  # noqa: BLE001 — loop substrate is derived/non-fatal
                logger.exception(
                    "[loop] DecisionRecord (rejected) persist failed for %s (non-fatal)",
                    correlation_id,
                )
        return RunResponse(
            correlation_id=correlation_id,
            status="failed",
            outputs={"reason": _reason, "note": payload.note},
            timeline=[{"step": "approval_gate", "status": _label}],
            error=_reason,
        )

    # ── approve: replay planned_writes ─────────────────────────────────
    auth_header = request.headers.get("authorization") or request.headers.get(
        "Authorization"
    )
    user_jwt_apply = (
        (auth_header or "").removeprefix("Bearer ").strip() or None
    )
    if not user_jwt_apply:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail="approver must carry a user JWT for the apply replay",
        )

    planned_writes = row.get("planned_writes") or []
    # Ghost-reference guard: each planned_write must structurally name a
    # source/dataset/action AND the row's case_natural_key must still be
    # populated (workflow engines that null out the natural_key on
    # cleanup leave a husk row pointing at nothing). Without this the
    # replay would dispatch a write against an MCP whose underlying
    # source row no longer exists; the MCP would either silently create
    # a new record or 404 mid-loop. Fail loudly up front instead.
    if not planned_writes:
        raise HTTPException(
            status.HTTP_410_GONE,
            detail=(
                f"staging row {correlation_id} has no planned_writes; "
                "re-triage required"
            ),
        )
    for pw in planned_writes:
        if not (
            pw.get("source_id")
            and pw.get("dataset_id")
            and pw.get("action_id")
        ):
            now_utc = datetime.now(timezone.utc)
            await staging_col.update_one(
                {
                    "workflow_execution_id": exec_id,
                    "case_natural_key": case_key,
                },
                {
                    "$set": {"status": "stale", "resolved_at": now_utc},
                    "$push": {
                        "audit_trail": {
                            "at": now_utc,
                            "actor": approver_id,
                            "decision": "stale",
                            "note": (
                                "planned_write missing "
                                "source_id/dataset_id/action_id"
                            ),
                        }
                    },
                },
            )
            raise HTTPException(
                status.HTTP_410_GONE,
                detail=(
                    f"consumer {case_key} no longer exists or planned_write "
                    "is malformed; re-triage required"
                ),
            )

    started_at = datetime.now(timezone.utc)
    # Shared replay — supports officer field-overrides (editable plan-then-apply)
    # and preview dry-run, identical to the queue-action path, so a recommendation
    # behaves the same whether a workflow precomputed it or the agent produced it
    # on-demand.
    app_spec = _load_app_spec(app_doc)
    (
        overall_ok,
        applied_count,
        timeline_apply,
        write_events_apply,
    ) = await _replay_planned_writes_with_overrides(
        settings=settings,
        planned_writes=planned_writes,
        overrides=payload.overrides or [],
        correlation_id=correlation_id,
        user_jwt=user_jwt_apply,
        request=request,
        app_spec=app_spec,
    )

    # A failed apply must say WHY. status=failed with error=None told the
    # officer their approved decision did not commit and gave them nothing to
    # act on; the reason was sitting in the timeline detail all along.
    _apply_errors = [
        str(t.get("detail")) for t in timeline_apply
        if t.get("status") == "error" and t.get("detail")
    ]
    response = RunResponse(
        correlation_id=correlation_id,
        status="completed" if overall_ok else "failed",
        outputs={
            "workflow_execution_id": exec_id,
            "case_natural_key": case_key,
        },
        timeline=timeline_apply,
        write_events=write_events_apply,
        planned_writes=planned_writes,
        error=None if overall_ok else (
            "; ".join(_apply_errors) or "one or more writes failed"
        ),
    )

    # Audit row — symmetric with queue-action apply path so the audit
    # consumers don't need to branch on row source.
    audit_doc = {
        "correlation_id": correlation_id,
        "slug": slug,
        "app_id": app_doc.get("app_id"),
        "agent_id": None,
        "agent_spec_version": None,
        "tenant_id": row.get("tenant_id"),
        "requested_by": None,
        "approver_id": approver_id,
        "action": "workflow_staging_apply",
        "inputs": {
            "workflow_execution_id": exec_id,
            "case_natural_key": case_key,
        },
        "status": response.status,
        "decision": "approved",
        "reasoning": payload.note or "workflow staging approved",
        "citations": [],
        "references": {},
        "outputs": response.outputs,
        "timeline": timeline_apply,
        "write_events": write_events_apply,
        "write_count": len(write_events_apply),
        "error": None if overall_ok else "one or more writes failed",
        "model": None,
        "usage": {},
        "duration_ms": int(
            (datetime.now(timezone.utc) - started_at).total_seconds() * 1000
        ),
    }
    try:
        await _persist_audit(audit_doc)
    except AuditPersistError as e:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "post-approval apply completed but the audit row could "
                "not be persisted; do not consider the writes authoritative "
                "until a follow-up reconciliation has run "
                f"(correlation_id={correlation_id}, applied={applied_count})"
            ),
        ) from e

    # Self-improving loop (Stage 5): record this human-approved decision so the
    # Stage-4 outcome poller has a row to read back by key. The unified officer
    # queue (workflow_staging) is the PRIMARY approval path, so without this the
    # loop substrate stays empty and outcome tracking has nothing to poll.
    # Mirrors the queue-action apply path; non-fatal (audit row above is
    # authoritative).
    try:
        _dr = _build_decision_record(
            decision_id=f"dr_{correlation_id}",
            correlation_id=correlation_id,
            mode="human_approved",
            decision_reason=payload.decision_reason,
            slug=slug,
            app_doc=app_doc,
            tenant_id=row.get("tenant_id"),
            retrieval_count=row.get("retrieval_count"),
            injected_clause_ids=row.get("injected_clause_ids"),
            cited_clauses=row.get("cited_clauses"),
            context=row.get("display_context") or {},
            recommendation={
                "action": None,
                "decision": row.get("llm_recommendation_text"),
                "reasoning": row.get("llm_reasoning"),
                "planned_writes": planned_writes,
                "cited_precedents": row.get("cited_precedents") or [],
                "case_facets": row.get("case_facets") or [],
                "cited_clauses": row.get("cited_clauses") or [],
                # The factor vector, so drift analysis can eventually ask which
                # factors actually discriminated. The ledger already inherits
                # the OUTCOME, so this is the only missing half of that join.
                "scorecard": row.get("scorecard"),
            },
            write_events=write_events_apply,
            action_result={
                "committed": overall_ok,
                "applied_count": applied_count,
                "status": response.status,
                "approver_id": approver_id,
            },
            model=None,
            audit_collection=get_settings().app_run_audit_collection,
        )
        await _persist_decision_record(_dr)
        # DECISION rubric: an override is the richest correction we get — the
        # officer changed the AI's value AND said why. Fold the delta (+reason)
        # into the app's standing decision criteria. Guards:
        #   * overall_ok — an override whose write FAILED re-validation or
        #     commit is not a validated lesson, and the row stays pending so a
        #     retried Approve would re-fold the identical delta (double-count).
        #     Folding only committed applies also makes retries naturally
        #     idempotent (a committed row cannot be approved again).
        #   * clean approves (no overrides) fold nothing — no signal, no noise.
        # Keyed by the APP's own org (rubric_tenant_for_app) so fold and read
        # always agree. fold_decision_feedback is fully non-raising.
        if _dr.get("overrides") and overall_ok:
            from analysis_rubrics import fold_decision_feedback, rubric_tenant_for_app

            await fold_decision_feedback(
                tenant_id=rubric_tenant_for_app(app_doc),
                app_slug=slug,
                actor=approver_id,
                correlation_id=correlation_id,
                reason=payload.decision_reason or payload.note,
                overrides=_dr.get("overrides"),
                recommendation=row.get("llm_recommendation_text"),
                # Clause-memory evidence (Phase A) — same stamped-at-/run
                # signature as the reject path; contested_fields is left to the
                # fold to derive from the override deltas.
                reason_code=payload.reason_code,
                contested_fields=payload.contested_fields,
                case_ref=_case_ref_of(row),
                case_facets=row.get("case_facets"),
                signature_version=row.get("signature_version"),
                injected_clause_ids=row.get("injected_clause_ids"),
                cited_clause_ids=[
                    c.get("clause_id")
                    for c in (row.get("cited_clauses") or [])
                    if isinstance(c, dict) and c.get("clause_id")
                ],
                overruled_clause_ids=[
                    c.get("clause_id")
                    for c in (row.get("cited_clauses") or [])
                    if isinstance(c, dict) and c.get("clause_id")
                    and c.get("relation") == "overruled"
                ],
            )
    except Exception:  # noqa: BLE001 — loop substrate is derived/non-fatal
        logger.exception(
            "[loop] DecisionRecord build/persist failed for %s (non-fatal)",
            correlation_id,
        )

    now_utc = datetime.now(timezone.utc)
    new_status = "applied" if overall_ok else current_status
    update_doc: Dict[str, Any] = {
        "$push": {
            "audit_trail": {
                "at": now_utc,
                "actor": approver_id,
                "decision": "approved" if overall_ok else "apply_failed",
                "note": payload.note,
                # WHY the officer overrode (the write_events carry the from→to
                # delta; this is the officer's stated reason) — fed back to the
                # model via the OFFICER CORRECTIONS prefetch.
                "decision_reason": payload.decision_reason,
                "applied_count": applied_count,
                "write_events": write_events_apply,
            }
        }
    }
    if overall_ok:
        update_doc["$set"] = {
            "status": new_status,
            "applied_by": approver_id,
            "resolved_at": now_utc,
        }
    await staging_col.update_one(
        {"workflow_execution_id": exec_id, "case_natural_key": case_key},
        update_doc,
    )
    return response


@app.post(
    "/apps/{slug}/run/{correlation_id}/approve",
    response_model=RunResponse,
)
async def approve_run(
    slug: str,
    correlation_id: str,
    payload: ApproveRequest,
    request: Request,
    settings: Settings = Depends(get_settings),
):
    """Content-negotiating wrapper over ``_approve_run_impl``.

    The plan-then-apply replay (and the workflow-staging branch) can run long
    enough to trip the gateway idle timeout (ALB ~60s / CF ~100s) → 504. When
    the caller asks for ``text/event-stream`` we run the whole approval under
    ``run_with_heartbeat`` so bytes flow throughout; otherwise we await it
    directly and FastAPI returns the JSON / real HTTP status as before. Because
    the apply is wrapped whole, its pre-flight 4xx (404/409/403/410) surface as
    terminal ``error`` SSE events (status carried in the event) on the streaming
    path.
    """
    if "text/event-stream" in (request.headers.get("accept") or "").lower():
        return StreamingResponse(
            run_with_heartbeat(
                lambda: _approve_run_impl(
                    slug, correlation_id, payload, request, settings
                ),
                rate_limit_exc=LLMRateLimitError,
            ),
            media_type="text/event-stream",
            headers=SSE_HEADERS,
        )
    return await _approve_run_impl(slug, correlation_id, payload, request, settings)


async def _approve_run_impl(
    slug: str,
    correlation_id: str,
    payload: ApproveRequest,
    request: Request,
    settings: Settings,
) -> RunResponse:
    """Resume a ``pending_approval`` run, or record a rejection.

    Authorization rules (defence-in-depth):
      * Approver must be authenticated AND in the same tenant as the
        original requester.
      * Approver cannot approve their own request (``requested_by`` must
        differ from the approver's user_id) — prevents self-approval of
        threshold-gated actions.
      * The pending run must exist and not already be resolved.

    Approver-role checks against ``hitl_policy.approvers`` are left to a
    role-membership service in a later phase; today we enforce only
    tenant + non-self-approval.
    """
    approver_id = get_secure_user_id(request)
    approver_tenant = get_tenant_id(request)

    # A correction must be worth storing. Disagreeing with the agent — by
    # rejecting its recommendation, or by overriding a field and applying — is
    # the ONLY signal this system learns from, and "wrong" teaches nobody
    # anything: consolidation refuses to author a judgement from vacuous text,
    # so a one-word reason is recorded, never learned from, and quietly wasted.
    #
    # Enforced HERE as well as in the UI on purpose. The client gate is a
    # courtesy — the embed bundle runs on the customer's own page, so any host
    # can post whatever it likes to this endpoint. A rule that only exists in
    # the browser is a suggestion.
    #
    # `cancel` is exempt: it is the absence of a judgement ("not now, wrong
    # queue"), not a correction. Demanding prose there would only manufacture
    # junk from people closing a tab.
    if payload.decision in ("reject", "approve"):
        from corrections import MIN_CORRECTION_WORDS

        _is_correction = payload.decision == "reject" or any(
            isinstance(o, dict) and o for o in (payload.overrides or [])
        )
        if _is_correction:
            # `decision_reason or note` — the SAME expression the fold uses
            # downstream when it records the correction. Checking only
            # decision_reason would reject a client whose prose the ledger
            # would then happily have accepted from `note`.
            _words = len(
                ((payload.decision_reason or payload.note) or "").split())
            if _words < MIN_CORRECTION_WORDS:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail={
                        "code": "correction_reason_too_brief",
                        "message": (
                            f"Write at least {MIN_CORRECTION_WORDS} words saying what "
                            f"the agent got wrong (got {_words}). This is the only "
                            "thing the app can learn from — the category it applies "
                            "to is derived from the case itself."
                        ),
                    },
                )

    # Resolve test↔prod by store before any collection access — approving a
    # test app's recommendation must replay its write against the TEST MCP and
    # read/write the test_ queue, not prod (see _bind_app_env).
    await _bind_app_env(slug)

    # ── workflow_staging branch ────────────────────────────────────────
    # The workflow engine writes per-case rows to ``smartapp_workflow_staging``
    # keyed by ``(workflow_execution_id, case_natural_key)``. The composite
    # key is exposed to the UI as ``{workflow_execution_id}:{case_natural_key}``
    # — the colon is reserved for this collection only (queue-action
    # correlation_ids never contain a colon: they are ``run_*``/``q_*`` ids).
    #
    # When the correlation_id parses as such a composite, we treat it as
    # a workflow-staging approval: planned_writes have the IDENTICAL shape
    # as the queue-action plan-then-apply path, so we reuse the same
    # replay loop verbatim and write back to the staging row instead of
    # the pending_runs row. No second LLM call — apply ≡ plan.
    if ":" in correlation_id:
        return await _approve_workflow_staging(
            slug=slug,
            correlation_id=correlation_id,
            payload=payload,
            request=request,
            settings=settings,
            approver_id=approver_id,
            approver_tenant=approver_tenant,
        )

    # No colon → not a workflow-staging row. The legacy ``pending_runs`` approval
    # path (plan-then-apply replay + threshold-gate LLM re-run) has been REMOVED:
    # nothing in production creates pending_runs rows any more. Both on-demand
    # (``run_app``) and trigger-precomputed (``trigger_runner``) recommendations
    # stage into ``smartapp_workflow_staging`` (composite ``{exec_id}:{case_key}``
    # ids, handled by the branch above). A non-composite correlation_id is
    # therefore an unknown/expired run — fail loud rather than resurrect dead code.
    raise HTTPException(
        status.HTTP_404_NOT_FOUND,
        detail=(
            "no approvable run for this correlation_id; officer-queue rows use "
            "composite ids '{workflow_execution_id}:{case_natural_key}'"
        ),
    )


# ---------------------------------------------------------------------------
# Run audit — read API (backs the Smart App "Audit" tab)
# ---------------------------------------------------------------------------


_AUDIT_SUMMARY_FIELDS = {
    "_id": 0,
    "audit_id": 1,
    "correlation_id": 1,
    "created_at": 1,
    "requested_by": 1,
    "action": 1,
    "status": 1,
    "decision": 1,
    "duration_ms": 1,
    "model": 1,
    "agent_spec_version": 1,
    "audit_missing": 1,
    "write_count": 1,
}


@app.get("/apps/{slug}/runs", response_model=AuditRunListResponse)
async def list_app_runs(
    slug: str,
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    decision: Optional[str] = Query(None),
    action: Optional[str] = Query(None),
    run_status: Optional[str] = Query(None, alias="status"),
    flagged: bool = Query(False),
    settings: Settings = Depends(get_settings),
) -> AuditRunListResponse:
    """List audited runs for an app — newest first.

    Access is gated identically to ``/run``: the caller must be able to
    access the app. Returns lightweight summaries; full evidence is
    fetched per-run via ``/runs/{correlation_id}/audit``.

    ``flagged=true`` restricts to runs that need a reviewer's attention —
    a source-system write with no decision block (``audit_missing``) or a
    failed run. Applied at the DB so the "needs review" view is truthful
    across the whole history, not just the page already fetched; ``total``
    reflects the filtered count.
    """
    user_id = get_secure_user_id(request)
    user_tenant = get_tenant_id(request)
    # Resolve test↔prod by store — a test app's runs live in the test_ audit
    # collection (see _bind_app_env).
    await _bind_app_env(slug)
    app_doc = await get_apps_col().find_one({"slug": slug})
    if not app_doc or not _user_can_access(app_doc, request):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="app not found")

    query: Dict[str, Any] = {"app_id": app_doc["app_id"]}
    if app_doc.get("tenant_id"):
        query["tenant_id"] = app_doc["tenant_id"]
    if decision:
        query["decision"] = decision
    if action:
        query["action"] = action
    if run_status:
        query["status"] = run_status
    if flagged:
        # "Needs review": a write with no decision block, or a failed run.
        query["$or"] = [{"audit_missing": True}, {"status": "failed"}]

    col = get_app_run_audit_col()
    total = await col.count_documents(query)
    rows = (
        await col.find(query, _AUDIT_SUMMARY_FIELDS)
        .sort("created_at", -1)
        .skip(offset)
        .limit(limit)
        .to_list(length=limit)
    )
    return AuditRunListResponse(
        slug=slug,
        total=total,
        limit=limit,
        offset=offset,
        runs=[AuditRunSummary(**r) for r in rows],
    )


@app.get(
    "/apps/{slug}/runs/{correlation_id}/audit",
    response_model=AuditRunDetailResponse,
)
async def get_app_run_audit(
    slug: str,
    correlation_id: str,
    request: Request,
    settings: Settings = Depends(get_settings),
) -> AuditRunDetailResponse:
    """Full audit trail for one run.

    Usually one row; two when the run was approved (the original
    ``pending_approval`` row + the resolved re-run). Newest first.
    """
    user_id = get_secure_user_id(request)
    user_tenant = get_tenant_id(request)
    # Resolve test↔prod by store (see _bind_app_env).
    await _bind_app_env(slug)
    app_doc = await get_apps_col().find_one({"slug": slug})
    if not app_doc or not _user_can_access(app_doc, request):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="app not found")

    query: Dict[str, Any] = {
        "app_id": app_doc["app_id"],
        "correlation_id": correlation_id,
    }
    if app_doc.get("tenant_id"):
        query["tenant_id"] = app_doc["tenant_id"]
    rows = (
        await get_app_run_audit_col()
        .find(query, {"_id": 0})
        .sort("created_at", -1)
        .to_list(length=20)
    )
    if not rows:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail="audit record not found"
        )
    # Attach any auto-process (auto-approve) commits made under this run. These
    # live in ``auto_process_decisions`` (not in ``app_run_audit``), so without
    # this join the proof of what an auto-process run actually wrote — and under
    # which policy rule — is invisible to the reviewer.
    auto_commits: List[Dict[str, Any]] = []
    if _db is not None:
        ap_q: Dict[str, Any] = {"correlation_id": correlation_id}
        if app_doc.get("app_id"):
            ap_q["app_id"] = app_doc["app_id"]
        if app_doc.get("tenant_id"):
            ap_q["tenant_id"] = app_doc["tenant_id"]
        auto_commits = (
            await _route_col(_db["auto_process_decisions"], "auto_process_decisions")
            .find(ap_q, {"_id": 0})
            .sort("created_at", 1)
            .to_list(length=200)
        )
    return AuditRunDetailResponse(
        slug=slug, correlation_id=correlation_id, runs=rows,
        auto_commits=auto_commits,
    )


@app.get("/apps/{slug}/auto-commits", response_model=AutoCommitListResponse)
async def list_app_auto_commits(
    slug: str,
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    committed: Optional[bool] = Query(None),
    settings: Settings = Depends(get_settings),
) -> AutoCommitListResponse:
    """App-wide auto-approve ledger — every auto-process commit attempt, newest
    first, with the policy rule that allowed it, the payload, and the outcome.

    Same access gate + test↔prod resolution as ``/runs``. ``committed=true`` /
    ``false`` narrows to applied vs failed attempts. This is the read surface
    for ``auto_process_decisions`` — the highest-scrutiny (no-human) writes —
    which previously had no UI at all.
    """
    await _bind_app_env(slug)
    app_doc = await get_apps_col().find_one({"slug": slug})
    if not app_doc or not _user_can_access(app_doc, request):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="app not found")
    if _db is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, detail="audit store unavailable"
        )

    query: Dict[str, Any] = {"app_id": app_doc["app_id"]}
    if app_doc.get("tenant_id"):
        query["tenant_id"] = app_doc["tenant_id"]
    if committed is not None:
        query["committed"] = committed

    col = _route_col(_db["auto_process_decisions"], "auto_process_decisions")
    total = await col.count_documents(query)
    rows = (
        await col.find(query, {"_id": 0})
        .sort("created_at", -1)
        .skip(offset)
        .limit(limit)
        .to_list(length=limit)
    )
    return AutoCommitListResponse(
        slug=slug, total=total, limit=limit, offset=offset, rows=rows,
    )


def _ledger_actor_display(*, requested_by, approver_id) -> Tuple[str, str]:
    """(authority_kind, actor_display) for a write-bearing run."""
    if approver_id:
        return "human_approval", approver_id
    if requested_by:
        return "direct_action", requested_by
    return "direct_action", "automated (trigger)"


def _auto_to_change(d: Dict[str, Any]) -> Dict[str, Any]:
    committed = d.get("committed")
    outcome = "applied" if committed is True else ("pending" if committed is None else "failed")
    return {
        "ts": d.get("finalized_at") or d.get("created_at"),
        "actor_type": "ai_auto",
        "actor_display": "AI auto-process",
        "authority": "auto_process_policy",
        "policy_reason": d.get("policy_reason"),
        "action": d.get("action_id") or d.get("action"),
        "target": {
            "source_id": d.get("source_id"), "dataset_id": d.get("dataset_id"),
            "action_id": d.get("action_id"),
        },
        "payload": d.get("payload"),
        "write_events": [],
        "override": None,
        "outcome": outcome,
        "correlation_id": d.get("correlation_id"),
    }


def _run_to_change(d: Dict[str, Any]) -> Dict[str, Any]:
    wes = d.get("write_events") or []
    authority, actor = _ledger_actor_display(
        requested_by=d.get("requested_by"), approver_id=d.get("approver_id"),
    )
    any_ok = any((isinstance(w, dict) and w.get("status") == "ok") for w in wes)
    outcome = "applied" if (d.get("status") == "completed" or any_ok) else "failed"
    first = wes[0] if wes else {}
    return {
        "ts": d.get("created_at"),
        "actor_type": "user",
        "actor_display": actor,
        "authority": authority,
        "policy_reason": None,
        "action": d.get("action"),
        "target": {
            "source_id": first.get("source_id"), "dataset_id": first.get("dataset_id"),
            "action_id": first.get("action_id"),
        },
        "payload": None,
        "write_events": wes,          # each may carry a `delta` (old → new)
        "override": d.get("override"),
        "outcome": outcome,
        "correlation_id": d.get("correlation_id"),
    }


@app.get("/apps/{slug}/changes", response_model=ChangeLedgerResponse)
async def list_app_changes(
    slug: str,
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    actor: Optional[str] = Query(None),    # ai_auto | user
    outcome: Optional[str] = Query(None),  # applied | failed
    settings: Settings = Depends(get_settings),
) -> ChangeLedgerResponse:
    """The Change Ledger — what actually CHANGED and who caused it, newest first.

    Merges (a) auto-process commits (``auto_process_decisions``) and (b) write-
    bearing AI runs (``app_run_audit`` where ``write_count > 0`` — approvals,
    queue actions, overlay edits). **Recommendations that committed nothing are
    excluded** (a suggestion no one acted on changed nothing). Same access gate
    + test↔prod resolution as ``/runs``; fail-loud on a source-read error.
    """
    await _bind_app_env(slug)
    app_doc = await get_apps_col().find_one({"slug": slug})
    if not app_doc or not _user_can_access(app_doc, request):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="app not found")
    if _db is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="audit store unavailable")

    app_id = app_doc["app_id"]
    tenant_id = app_doc.get("tenant_id")
    want_auto = actor in (None, "ai_auto")
    want_user = actor in (None, "user")
    window = offset + limit  # fetch enough from each source to paginate the merge

    changes: List[Dict[str, Any]] = []
    total = 0

    if want_auto:
        aq: Dict[str, Any] = {"app_id": app_id}
        if tenant_id:
            aq["tenant_id"] = tenant_id
        if outcome == "applied":
            aq["committed"] = True
        elif outcome == "failed":
            aq["committed"] = False
        total += await _route_col(_db["auto_process_decisions"], "auto_process_decisions").count_documents(aq)
        adocs = (
            await _route_col(_db["auto_process_decisions"], "auto_process_decisions")
            .find(aq, {"_id": 0}).sort("created_at", -1).limit(window).to_list(length=window)
        )
        changes.extend(_auto_to_change(d) for d in adocs)

    if want_user:
        uq: Dict[str, Any] = {"app_id": app_id, "write_count": {"$gt": 0}}
        if tenant_id:
            uq["tenant_id"] = tenant_id
        if outcome == "applied":
            uq["status"] = "completed"
        elif outcome == "failed":
            uq["status"] = {"$ne": "completed"}
        total += await get_app_run_audit_col().count_documents(uq)
        udocs = (
            await get_app_run_audit_col()
            .find(uq, {"_id": 0}).sort("created_at", -1).limit(window).to_list(length=window)
        )
        changes.extend(_run_to_change(d) for d in udocs)

    # Merge by timestamp (newest first), then page the merged window.
    changes.sort(key=lambda c: c.get("ts") or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    page = changes[offset:offset + limit]
    return ChangeLedgerResponse(
        slug=slug, total=total, limit=limit, offset=offset, changes=page,
    )


# ---------------------------------------------------------------------------
# Recommendation inbox (smartapp_workflow_staging) — per-case rows the app's
# agent stages (on-demand /run or an app trigger), reviewed + approved by the
# officer. Written by _stage_recommendation; read by GET /workflow-staging;
# committed by _approve_workflow_staging (replays planned_writes).
# ---------------------------------------------------------------------------


def _workflow_staging_row_to_dict(row: WorkflowStagingRow) -> Dict[str, Any]:
    """Convert a WorkflowStagingRow into a Mongo-ready dict.

    Stamps ``created_at`` when the caller omitted it so the time-window
    index is meaningful even on lazy callers.
    """
    doc = row.model_dump(mode="python", exclude_none=False)
    if not doc.get("created_at"):
        doc["created_at"] = datetime.now(timezone.utc)
    return doc


def _derive_case_natural_key(inputs: Any) -> Optional[str]:
    """Best-effort stable per-case key for an on-demand recommendation row.

    The staging collection dedupes on ``(workflow_execution_id,
    case_natural_key)``; for an on-demand run we want the key to identify the
    RECORD the officer is acting on, so re-running the same item upserts the
    recommendation rather than piling up duplicate queue rows. Falls back to
    None (caller then uses the run's correlation_id) when no id-like field is
    present in the run inputs.
    """
    if isinstance(inputs, dict):
        for k in (
            "record_id", "id", "case_id", "case_natural_key",
            "claim_id", "row_id", "entity_id", "ticket_id",
        ):
            v = inputs.get(k)
            if v not in (None, ""):
                return str(v)
    return None


async def _record_trigger_run(
    *,
    app_spec,
    app_doc: dict,
    agent_doc: Optional[dict],
    trigger,
    inputs: dict,
    started_at: datetime,
    response: Optional["RunResponse"] = None,
    error: Optional[str] = None,
    fired_via: str = "scheduler",
) -> None:
    """Single sink for every trigger firing (scheduler / poll / manual /
    webhook), success OR failure. Writes one append-only ``trigger_runs`` row
    (the queryable run-history + failure-visibility record), appends an
    ``app_run_audit`` ledger row for runs that actually executed (extending the
    tamper-evident chain to unattended runs), and logs loud (ERROR) on failure
    so the log-watcher + observability stack alert IT. Never raises — a
    recording fault must not kill the scheduler — but every fault is logged."""
    now = datetime.now(timezone.utc)
    slug = getattr(app_spec, "slug", None) or app_doc.get("slug") or "?"
    fired = response is not None and error is None
    run_status = response.status if response is not None else "failed"
    correlation_id = response.correlation_id if response is not None else None
    write_count = len(response.write_events or []) if response is not None else 0

    row = {
        "trigger_run_id": f"trun_{uuid.uuid4().hex[:16]}",
        "slug": slug,
        "app_id": app_doc.get("app_id"),
        "agent_id": app_doc.get("agent_id"),
        "tenant_id": app_doc.get("tenant_id"),
        "trigger_id": getattr(trigger, "id", None),
        "trigger_type": getattr(trigger, "type", None),
        "action": getattr(trigger, "action", None),
        "fired_via": fired_via,
        "requested_by": f"system:{slug}",
        "fired": fired,
        "status": run_status,
        "correlation_id": correlation_id,
        "error": error,
        "decision": response.decision if response is not None else None,
        "write_count": write_count,
        "inputs": inputs,
        "started_at": started_at,
        "finished_at": now,
        "duration_ms": int((now - started_at).total_seconds() * 1000),
        "created_at": now,
    }
    try:
        await get_trigger_runs_col().insert_one(row)
    except Exception:  # noqa: BLE001
        logger.exception("failed to persist trigger_runs row for %s", slug)

    # Extend the immutable run ledger to unattended runs (recommendation event).
    # Triggers are plan_only (no commit), so fatal_on_write=False — a missing
    # audit row here must not crash the scheduler; the trigger_runs row above is
    # the operational record either way.
    if response is not None and agent_doc is not None and run_status in (
        "completed",
        "pending_approval",
    ):
        try:
            await _persist_audit(
                _build_audit_doc(
                    response=response,
                    slug=slug,
                    app_doc=app_doc,
                    agent_doc=agent_doc,
                    user_id=f"system:{slug}",
                    tenant_id=app_doc.get("tenant_id"),
                    action=getattr(trigger, "action", "") or "",
                    inputs=inputs,
                    started_at=started_at,
                ),
                fatal_on_write=False,
            )
        except Exception:  # noqa: BLE001
            logger.exception("failed to persist trigger run audit for %s", slug)

    if not fired:
        # Log loud (ERROR) so the Monitoring-Service log-watcher and the
        # observability stack (Loki/promtail) pick it up and alert IT. The
        # durable trigger_runs row above is the queryable record — nothing is
        # lost. smart-app-service does NOT call Monitoring-Service directly.
        logger.error(
            "[TRIGGER-FAILED] app=%s trigger=%s type=%s action=%s "
            "correlation_id=%s: %s",
            slug,
            getattr(trigger, "id", "?"),
            getattr(trigger, "type", "?"),
            getattr(trigger, "action", "?"),
            correlation_id or "-",
            error or run_status,
        )


def _app_dept_routing(app_spec_dict: dict) -> dict:
    """Derive the officer-inbox routing for a precomputed recommendation from
    the app's dept ownership. Returns ``{"dept_id": <d>}`` when the app is
    dept-scoped (owner_type=dept, or a declared dept_ids), else ``{}``."""
    spec = app_spec_dict or {}
    if spec.get("owner_type") == "dept" and spec.get("owner_id"):
        return {"dept_id": spec["owner_id"]}
    depts = spec.get("dept_ids") or []
    if depts:
        return {"dept_id": depts[0]}
    return {}


async def _record_auto_process_pending(
    *, app_spec, app_doc, trigger, inputs, planned_write, reason, correlation_id,
) -> str:
    """Insert a PENDING auto-process DecisionRecord BEFORE the write commits.

    FAIL-LOUD: raises ``AuditPersistError`` if the record cannot be written, so
    the caller skips the write — a source change can NEVER land without an audit
    row. Returns the ``audit_id`` to finalise with the outcome after the write.
    """
    _ds, _aid = planned_write.get("dataset_id"), planned_write.get("action_id")
    if _db is None:
        # LOG (→ rotating file + stdout + Loki/alerts) AND propagate (RULE #1).
        logger.error(
            "[auto-process] AUDIT STORE UNAVAILABLE — refusing to commit unaudited "
            "(app=%s trigger=%s dataset=%s action=%s)",
            app_spec.slug, trigger.id, _ds, _aid,
        )
        raise AuditPersistError(
            "auto-process audit store unavailable — refusing to commit unaudited"
        )
    audit_id = uuid.uuid4().hex
    try:
        await _route_col(_db["auto_process_decisions"], "auto_process_decisions").insert_one({
            "audit_id": audit_id,
            "correlation_id": correlation_id,
            "app_id": app_doc.get("app_id"), "slug": app_spec.slug,
            "tenant_id": app_doc.get("tenant_id"),
            "trigger_id": trigger.id, "trigger_type": getattr(trigger, "type", None),
            "action": trigger.action, "mode": "auto_process",
            "dataset_id": _ds,
            "action_id": _aid,
            "source_id": planned_write.get("source_id"),
            "payload": planned_write.get("payload"),
            "policy_reason": reason,
            "committed": None,          # pending until the write returns
            "status": "pending",
            "created_at": datetime.now(timezone.utc),
        })
    except Exception as e:  # noqa: BLE001 — LOG loud + propagate; never commit unaudited
        logger.error(
            "[auto-process] PENDING AUDIT WRITE FAILED — refusing to commit unaudited "
            "(app=%s trigger=%s dataset=%s action=%s): %s",
            app_spec.slug, trigger.id, _ds, _aid, e,
        )
        raise AuditPersistError(f"auto-process pending audit write failed: {e}") from e
    # Self-improving loop (Stage 5): mirror this auto-process decision into the
    # DecisionRecord substrate (pending; action_result stamped at finalize).
    # Non-fatal — auto_process_decisions written just above is authoritative.
    try:
        _dr = _build_decision_record(
            decision_id=f"dr_auto_{audit_id}",
            correlation_id=correlation_id,
            mode="auto_process",
            slug=app_spec.slug,
            app_doc=app_doc,
            tenant_id=app_doc.get("tenant_id"),
            context=inputs if isinstance(inputs, dict) else {"inputs": inputs},
            recommendation={
                "action": trigger.action,
                "policy_reason": reason,
                "planned_writes": [planned_write],
            },
            write_events=[{
                "dataset_id": _ds,
                "action_id": _aid,
                "source_id": planned_write.get("source_id"),
                "args": planned_write.get("payload"),
                "override": None,
            }],
            action_result={"committed": None, "status": "pending"},
            audit_collection="auto_process_decisions",
            audit_id=audit_id,
        )
        await _persist_decision_record(_dr)
    except Exception:  # noqa: BLE001 — loop substrate is derived/non-fatal
        logger.exception(
            "[loop] auto-process DecisionRecord build/persist failed "
            "(audit_id=%s, non-fatal)", audit_id,
        )
    return audit_id


async def _finalize_auto_process_decision(
    *, audit_id, app_doc, ok, result,
) -> None:
    """Stamp the write outcome onto the pending DecisionRecord.

    FAIL-LOUD: raises if the outcome cannot be recorded — otherwise the record
    stays ``pending`` (a detectable anomaly), so we surface it rather than
    silently swallow it (RULE #1)."""
    if _db is None:
        logger.error(
            "[auto-process] AUDIT STORE UNAVAILABLE at finalize — outcome unrecorded "
            "(audit_id=%s ok=%s)", audit_id, ok,
        )
        raise AuditPersistError("auto-process audit store unavailable — outcome unrecorded")
    try:
        res = await _route_col(_db["auto_process_decisions"], "auto_process_decisions").update_one(
            {"audit_id": audit_id, "tenant_id": app_doc.get("tenant_id")},
            {"$set": {
                "committed": bool(ok),
                "status": "applied" if ok else "failed",
                "result": result if ok else {
                    "error": (result or {}).get("error") if isinstance(result, dict) else str(result)
                },
                "finalized_at": datetime.now(timezone.utc),
            }},
        )
    except Exception as e:  # noqa: BLE001 — LOG loud + propagate (RULE #1)
        logger.error(
            "[auto-process] FINALIZE AUDIT WRITE FAILED (audit_id=%s ok=%s): %s",
            audit_id, ok, e,
        )
        raise AuditPersistError(f"auto-process finalize audit write failed: {e}") from e
    if res.matched_count == 0:
        logger.error(
            "[auto-process] DecisionRecord %s NOT FOUND at finalize — AUDIT GAP "
            "(the write may have committed without a finalised record)", audit_id,
        )
        raise AuditPersistError(
            f"auto-process DecisionRecord {audit_id} not found at finalize — audit gap"
        )
    # Self-improving loop (Stage 5): stamp the write result onto the mirrored
    # DecisionRecord created at pending. Non-fatal/derived (the authoritative
    # outcome was just written to auto_process_decisions above).
    await _update_decision_record(
        f"dr_auto_{audit_id}",
        {"action_result": {
            "committed": bool(ok),
            "status": "applied" if ok else "failed",
            "result": result if ok else {
                "error": (result or {}).get("error")
                if isinstance(result, dict) else str(result)
            },
        }},
    )


_AUTOPROC_BREAKER_N = 5  # consecutive failed auto-process attempts that TRIP the breaker


async def auto_process_guard_check(*, app_id, slug, trigger_id, policy) -> Tuple[bool, str]:
    """Phase-2 runtime guardrails for auto-process commits, read from the
    ``auto_process_decisions`` audit (env-routed, already written per commit):

      • RATE LIMIT — if >= ``policy.rate_limit_per_hour`` commits already landed
        for this (app, trigger) in the last hour, STOP committing (stage instead).
      • CIRCUIT BREAKER — if the last ``_AUTOPROC_BREAKER_N`` attempts ALL failed,
        TRIP: stop committing until a human resolves the underlying error.

    Returns ``(allow_commit, reason)``. Fail-OPEN only on a missing DB / query
    error (never silently block a healthy commit on infra hiccups), but the
    per-write deterministic gate + severity ceiling still bound everything."""
    try:
        if _db is None:
            return True, "no-db"
        col = _route_col(_db["auto_process_decisions"], "auto_process_decisions")
        now = datetime.now(timezone.utc)
        rl = getattr(policy, "rate_limit_per_hour", None)
        if rl:
            since = now - timedelta(hours=1)
            n = await col.count_documents({
                "app_id": app_id, "trigger_id": trigger_id,
                "committed": True, "created_at": {"$gte": since},
            })
            if n >= rl:
                return False, (
                    f"rate_limit_per_hour ({rl}) reached — {n} auto-commits in the "
                    f"last hour; staging for human review"
                )
        recent = await col.find(
            {"app_id": app_id, "trigger_id": trigger_id},
            sort=[("created_at", -1)],
        ).to_list(_AUTOPROC_BREAKER_N)
        if len(recent) >= _AUTOPROC_BREAKER_N and all(not r.get("committed") for r in recent):
            return False, (
                f"circuit-breaker TRIPPED — last {_AUTOPROC_BREAKER_N} auto-process "
                f"attempts all FAILED; staging until a human resolves the cause"
            )
        return True, "ok"
    except Exception as e:  # noqa: BLE001 — guard read failure must not block a healthy commit
        logger.warning("[auto-process] guard_check read failed (fail-open) for %s/%s: %s",
                        slug, trigger_id, e)
        return True, "guard-check-error"


async def commit_auto_process_writes(
    *, settings, app_spec, app_doc, trigger, inputs, planned_writes, reasons, correlation_id,
    row_key=None,
):
    """Commit the planned_writes that PASSED the deterministic auto-process gate.
    Governed dept-MCP write (or the app-overlay branch), under the app's SYSTEM
    principal, idempotent, AUDITED (a DecisionRecord per write). Called ONLY by
    trigger_runner's auto-process gate — never user-facing. The gate already
    enforced severity ceiling + the deterministic rule + caps; this executes the
    proven write path for the approved subset and fails per-write (never crashes
    the trigger run)."""
    from proxy_clients import call_dept_mcp_execute_action
    from data_tools import write_app_record
    from trigger_runner import _mint_system_auth

    auth_header = _mint_system_auth(settings, app_spec, app_doc)
    user_jwt = auth_header.removeprefix("Bearer ").strip() if auth_header else None
    tenant_id = app_doc.get("tenant_id") or getattr(app_spec, "tenant_id", None) or ""
    # Kill switch (autonomous-write path): if a halt/pause covers this app, do
    # NOT commit. Raising here fails the trigger run loudly (recorded, retried,
    # eventually dead-lettered) rather than writing to a source system during a
    # freeze. This is the most important enforcement point.
    await _enforce_automation_allowed(app_doc, what="Auto-process commit")
    committed = 0
    for _idx, (pw, reason) in enumerate(zip(planned_writes, reasons)):
        sid, did, aid = pw.get("source_id"), pw.get("dataset_id"), pw.get("action_id")
        payload = pw.get("payload") or {}
        _ov = next(
            (d for d in (getattr(app_spec, "data_sources", None) or [])
             if getattr(d, "id", None) == did and getattr(d, "type", None) == "smart_app_records"),
            None,
        )
        # FAIL-LOUD: record the intent BEFORE the write. If the audit row can't
        # be written we raise (out of the trigger run) rather than change a
        # source system with no record of it.
        audit_id = await _record_auto_process_pending(
            app_spec=app_spec, app_doc=app_doc, trigger=trigger, inputs=inputs,
            planned_write=pw, reason=reason, correlation_id=correlation_id,
        )
        ok, result = False, None
        try:
            if _ov is not None:
                result = await write_app_record(
                    ds=_ov, payload=payload, auth_header=auth_header,
                    tenant_id=tenant_id, app_spec=app_spec, plan_only=False,
                )
            elif sid and did and aid:
                result = await call_dept_mcp_execute_action(
                    settings=settings, user_jwt=user_jwt, source_id=sid, dataset_id=did,
                    action_id=aid, payload=payload, dry_run=False,
                    # ROW-stable idempotency: a reprocess of the same source row
                    # (crash mid-tick, retry) reuses this key so the dept-MCP
                    # dedups instead of double-committing. Falls back to the
                    # per-run correlation_id for cron/webhook/run-now (no row_key).
                    idempotency_key=(
                        f"autoproc:{app_spec.slug}:{trigger.id}:{row_key}:{aid}:{_idx}"
                        if row_key else f"autoproc:{correlation_id}:{aid}:{_idx}"
                    ),
                )
            else:
                result = {"error": "planned_write missing target (source_id/dataset_id/action_id)"}
            ok = bool(isinstance(result, dict) and result.get("ok") and not result.get("error"))
        except Exception as e:  # noqa: BLE001 — surface per-write; never crash the trigger run
            result = {"error": str(e)}
            logger.error("[auto-process] COMMIT FAILED app=%s trigger=%s action=%s: %s",
                         app_spec.slug, trigger.id, aid, e)
        if ok:
            committed += 1
            logger.info("[auto-process] COMMITTED app=%s trigger=%s target=%s (rule: %s)",
                        app_spec.slug, trigger.id, aid or (getattr(_ov, "id", "?")), reason)
        # FAIL-LOUD: stamp the outcome onto the pending row (raises on audit gap).
        await _finalize_auto_process_decision(
            audit_id=audit_id, app_doc=app_doc, ok=ok, result=result,
        )
    return committed


async def _flag_sop_drift(slug: str, factor_ids: List[str]) -> None:
    """Mark an app whose rubric was extracted against a policy that has moved.

    Durable, because the officer who sees the warning on one case is not the
    person who can fix it — the owner needs to find this later without having
    replayed that run. Kept OUTSIDE ``app_spec``: the spec is the builder's
    authored artefact and this is an observation about it.

    ``first_seen_at`` is written once (``$setOnInsert`` semantics via
    ``$min``-like guard below) so the record shows how long the app has been
    scoring against a stale rubric, not merely that it still is.

    Non-fatal by design: a decision must not fail because a bookkeeping write
    did. It is logged loudly by the caller either way, and the drift is already
    on the card the officer is looking at.
    """
    if not factor_ids:
        return
    try:
        now = datetime.now(timezone.utc)
        existing = await get_apps_col().find_one(
            {"slug": slug}, {"_id": 1, "factor_set_drift": 1})
        if not existing:
            return
        prior = (existing.get("factor_set_drift") or {})
        await get_apps_col().update_one(
            {"_id": existing["_id"]},
            {"$set": {"factor_set_drift": {
                "factor_ids": sorted(set(factor_ids) | set(prior.get("factor_ids") or [])),
                "first_seen_at": prior.get("first_seen_at") or now,
                "last_seen_at": now,
                "needs_reextraction": True,
            }}},
        )
    except Exception as exc:  # noqa: BLE001 — bookkeeping, never fails a decision
        logger.warning("[SCORECARD] could not flag SOP drift for %s: %s", slug, exc)


async def _stage_recommendation(
    *,
    response: RunResponse,
    slug: str,
    inputs: Any,
    tenant_id: Optional[str],
    published_by_user_id: Optional[str],
    source: str = "queue_action",
    plan_ttl_seconds: Optional[int] = None,
    assignable_to: Optional[dict] = None,
) -> str:
    """Persist a ``pending_approval`` RunResponse as a row in the unified officer
    queue (``smartapp_workflow_staging``, env-routed) and return the composite
    correlation_id ``{run_id}:{case_key}`` that routes a later Approve to the
    staging-replay path (``_approve_workflow_staging``, which replays
    ``planned_writes`` with ``dry_run=false``).

    Idempotent on ``(workflow_execution_id, case_natural_key)`` — re-running the
    same record upserts rather than duplicating. Shared by the on-demand
    ``/run`` path AND the trigger path (webhook / schedule / poll) so a
    PRECOMPUTED recommendation lands in the SAME inbox as an on-demand one, by
    the SAME mechanism. ``get_workflow_staging_col()`` is env-routed, so a test
    app's recommendation lands in ``test_smartapp_workflow_staging`` with no
    extra wiring. ``source``: ``queue_action`` (on-demand) | ``workflow``
    (eager/precomputed by a trigger)."""
    if plan_ttl_seconds is None:
        # On-demand plans get a generous human-review window (30m default): the
        # officer is reviewing NOW, but a careful multi-item review must not 410
        # mid-review and force a re-triage. PRECOMPUTED plans (trigger/workflow)
        # must survive until the officer next logs in — an overnight cron
        # recommendation that 410s by morning defeats the whole point. Safety is
        # preserved by the dry_run re-validate on Approve, NOT by an aggressive
        # expiry, so a longer TTL is safe. Tune via SMART_APP_PLAN_TTL_SECONDS.
        if source == "queue_action":
            plan_ttl_seconds = int(os.getenv("SMART_APP_PLAN_TTL_SECONDS", "1800"))
        else:
            plan_ttl_seconds = int(os.getenv("SMART_APP_TRIGGER_PLAN_TTL_SECONDS", "86400"))
    now = datetime.now(timezone.utc)
    run_id = response.correlation_id
    case_key = _derive_case_natural_key(inputs) or run_id
    # Route precomputed rows to the app's dept so dept officers actually SEE
    # them — list_workflow_staging filters on assignable_to.dept_id ∈ the
    # caller's dept_ids, so an empty assignable_to is invisible to every
    # non-admin officer.
    assignable_to = assignable_to or {}
    dept_ids = [assignable_to["dept_id"]] if assignable_to.get("dept_id") else []
    staging_row = WorkflowStagingRow(
        workflow_execution_id=run_id,
        case_natural_key=case_key,
        tenant_id=tenant_id,
        org_id=tenant_id,
        dept_ids=dept_ids,
        slug=slug,
        llm_recommendation_text=response.decision,
        llm_reasoning=response.reasoning,
        llm_evidence_summary=(response.outputs or {}).get("text"),
        planned_writes=response.planned_writes or [],
        display_context=dict(inputs or {}),
        # Memory-lift carrier (adoption plan §2.1) — rides to the
        # DecisionRecord when the officer disposes this row.
        retrieval_count=(response.references or {}).get("retrieval_count"),
        # Precedent receipts (§4) — rendered on the officer card + carried to
        # the DecisionRecord's recommendation at disposition.
        cited_precedents=list(getattr(response, "cited_precedents", None) or []),
        # The computed scorecard, frozen at /run — the officer's card renders
        # from this rather than re-scoring, and the queue can rank on its grade.
        scorecard=(
            response.scorecard.model_dump()
            if getattr(response, "scorecard", None) is not None else None
        ),
        # Clause memory (clause-memory plan §10.1): what the model SAW, what it
        # SAID it used, and the facets frozen at /run. All three ride to the
        # correction recorded when the officer disposes this row — recomputing
        # facets later would let an ontology edit rewrite history.
        injected_clause_ids=list((response.references or {}).get("injected_clause_ids") or []),
        cited_clauses=list(getattr(response, "cited_clauses", None) or []),
        case_facets=list((response.references or {}).get("case_facets") or []),
        signature_version=(response.references or {}).get("signature_version"),
        status="pending_review",
        assignable_to=assignable_to,
        created_at=now,
        expires_at=now + timedelta(seconds=plan_ttl_seconds),
        source=source,
        published_by_user_id=published_by_user_id,
    )
    # SOP drift (docs/factor-scorecard-plan.md phase 5). Scoring already
    # happened and the card already carries the warning — this makes it findable
    # by the person who can act on it, days later, without replaying the run.
    _card = getattr(response, "scorecard", None)
    if _card is not None and _card.sop_drift_factor_ids:
        logger.warning(
            "[SCORECARD] %s scored against a CHANGED policy for factor(s) %s — "
            "the rubric was confirmed against a different version of the "
            "document and needs re-extraction",
            slug, _card.sop_drift_factor_ids,
        )
        await _flag_sop_drift(slug, list(_card.sop_drift_factor_ids))

    # Dedupe key. For an on-demand action the per-run ``workflow_execution_id``
    # changes on every click, so keying the upsert on it piled up N duplicate
    # pending cards for one record (each independently approvable). Dedupe
    # instead on (slug, case_natural_key) among PENDING rows so a re-run upserts
    # the recommendation; a terminal (applied/rejected) row is history and is
    # left untouched (the filter won't match it, so a fresh pending row is
    # inserted). Trigger/precomputed rows keep the per-execution key.
    if source == "queue_action":
        _dedup_filter = {
            "slug": slug,
            "case_natural_key": case_key,
            "status": "pending_review",
        }
    else:
        _dedup_filter = {
            "workflow_execution_id": run_id,
            "case_natural_key": case_key,
        }
    await get_workflow_staging_col().replace_one(
        _dedup_filter,
        _workflow_staging_row_to_dict(staging_row),
        upsert=True,
    )
    return f"{run_id}:{case_key}"


def _strip_mongo_id(doc: Dict[str, Any]) -> Dict[str, Any]:
    doc.pop("_id", None)
    return doc


@app.get("/workflow-staging", response_model=PanelDataResponse)
async def list_workflow_staging(
    request: Request,
    row_status: Optional[str] = Query(None, alias="status"),
    dept_id: Optional[str] = Query(None),
    role: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    max_age_days: Optional[int] = Query(None, ge=1, le=3650),
) -> PanelDataResponse:
    """Reviewer inbox — list per-case staging rows visible to the caller.

    Visibility comes from the caller's JWT: rows are scoped to the user's
    ``tenant_id`` and intersected with their ``dept_ids``. The optional
    ``status``/``dept_id``/``role`` query params narrow further inside that
    envelope. ``max_age_days`` time-windows by ``created_at``.
    """
    get_secure_user_id(request)
    tenant_id = get_tenant_id(request)
    user_dept_ids = get_user_dept_ids(request)
    user_roles = get_user_roles(request)

    query: Dict[str, Any] = {}
    if tenant_id:
        query["tenant_id"] = tenant_id
    if row_status:
        query["status"] = row_status
    if max_age_days:
        cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
        query["created_at"] = {"$gte": cutoff}

    # Dept visibility: an explicit ?dept_id must be one the caller belongs
    # to (otherwise this would let a JE in dept A peek at dept B). If the
    # caller has dept_ids on their token at all we restrict to that set;
    # tokens with no dept_ids (platform admin) see everything in tenant.
    if dept_id:
        if user_dept_ids and dept_id not in user_dept_ids:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="dept_id filter is outside the caller's dept_ids",
            )
        query["assignable_to.dept_id"] = dept_id
    elif user_dept_ids:
        query["assignable_to.dept_id"] = {"$in": user_dept_ids}

    if role:
        if user_roles and role not in user_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="role filter is outside the caller's roles",
            )
        query["assignable_to.role"] = role

    col = get_workflow_staging_col()
    cursor = col.find(query).sort("created_at", -1).skip(offset).limit(limit + 1)
    raw = [_strip_mongo_id(r) async for r in cursor]
    has_more = len(raw) > limit
    rows = raw[:limit]
    # Backfill ``source`` for rows written before the field was added to
    # the schema. The UI's "AI-recommended" chip predicate reads this
    # field; defaulting it at read time covers historical rows without a
    # Mongo migration. New rows always carry the field via the Pydantic
    # default — this is the safety net.
    for r in rows:
        r.setdefault("source", "trigger")
    return PanelDataResponse(
        panel_id="workflow_staging",
        data_source="workflow_staging",
        columns=[],
        rows=rows,
        total=len(rows) + (1 if has_more else 0),
        truncated=has_more,
        source_kind="workflow_staging",
        note=None,
    )


# ---------------------------------------------------------------------------
# Enterprise distribution — audience-based
# ---------------------------------------------------------------------------
# A SmartApp's distribution is controlled by a single ``audience`` string
# on the AppSpec:
#   'owner'          — only owner_sa_id members (default)
#   'team:<sa_id>'   — members of a specific team SA
#   'dept:<dept_id>' — everyone in a department
#   'org'            — everyone in the tenant
# Edit access is independent of audience and is always controlled by
# owner_sa_id membership. To change audience, the caller must satisfy
# BOTH the current AND target levels (the higher-of rule) — see
# ``_can_change_audience`` and ``_enumerate_publish_options`` defined below.


# ---------------------------------------------------------------------------
# Phase B: Lifecycle transitions on AppSpec
# ---------------------------------------------------------------------------
# Authorization for ownership transitions and archive flows through
# ``_is_lifecycle_admin`` (super_admin OR org_admin in own org OR owning
# SA admin OR dept_admin when dept-owned OR org_admin when org-owned).
# Every lifecycle change appends a ``lifecycle_audit`` entry so the
# resource trail stays reconstructable.


def _claims(request: Request) -> Dict[str, Any]:
    """Smart-app-service equivalent of citra-workflow's _jwt_claims."""
    return {
        "user_id": getattr(request.state, "user_id", ""),
        "email": getattr(request.state, "user_email", "") or "",
        "org_id": getattr(request.state, "org_id", "") or "",
        "dept_ids": list(getattr(request.state, "dept_ids", []) or []),
        "roles": list(getattr(request.state, "roles", []) or []),
        "service_account_admin_of": list(
            (getattr(request.state, "user", {}) or {}).get(
                "service_account_admin_of", []
            ) or []
        ),
        "service_account_member_of": list(
            (getattr(request.state, "user", {}) or {}).get(
                "service_account_member_of", []
            ) or []
        ),
    }


def _work_sa_id(user_id: str, org_id: Optional[str]) -> str:
    """Deterministic Work SA id for a user.

    Format: svc:work-<slug>@<org>.citra.ai

    Work SAs own durable team-portable resources (skills, smart-apps,
    workflows). Materialised by Citra-User-Service at login time. We
    derive the id locally only as a fallback when the JWT claim is
    absent (older tokens / misconfigured users) — the publish flow then
    rejects with a clear error so the user is sent to the admin panel.
    """
    raw = (user_id or "anonymous").lower()
    slug = "".join(c if (c.isalnum() or c == "-") else "-" for c in raw).strip("-")[:60]
    domain = (org_id or "default") + ".citra.ai"
    return f"svc:work-{slug or 'anonymous'}@{domain}"


def _is_lifecycle_admin(claims: Dict[str, Any], app_doc: dict) -> bool:
    """Owner-equivalent control under SA-only ownership: super_admin,
    org_admin (own org), owning SA admin, dept_admin (when dept-owned),
    org_admin (when org-owned).
    """
    roles = set(claims["roles"])
    if "super_admin" in roles:
        return True
    spec = app_doc.get("app_spec") or {}
    # org_id can live on the AppSpec (Layer 3) or the top-level doc
    # (legacy `tenant_id`). Single-tenant deploys: org_id == tenant_id.
    app_org_id = (
        spec.get("org_id")
        or app_doc.get("org_id")
        or app_doc.get("tenant_id")
    )
    if "org_admin" in roles and app_org_id and app_org_id == claims["org_id"]:
        return True
    owner_type = spec.get("owner_type") or "service_account"
    owner_id = spec.get("owner_id") or ""
    if owner_type == "service_account" and owner_id in set(
        claims["service_account_admin_of"]
    ):
        return True
    if owner_type == "dept" and owner_id in set(claims["dept_ids"]) and "dept_admin" in roles:
        return True
    if owner_type == "org" and owner_id == claims["org_id"] and "org_admin" in roles:
        return True
    return False


def _lifecycle_audit_entry(
    claims: Dict[str, Any], action: str, **extra
) -> Dict[str, Any]:
    entry = {
        "action": action,
        "by": claims["email"] or claims["user_id"],
        "at": datetime.now(timezone.utc),
    }
    entry.update(extra)
    return entry


# ---------------------------------------------------------------------------
# Audience: who can SEE and RUN an app
# ---------------------------------------------------------------------------
# Audience is orthogonal to ownership. `owner_sa_id` controls EDIT; the
# `audience` field controls READ/RUN. To change audience to a target value,
# the caller must be authorised to publish at BOTH the current AND target
# levels (the "higher-of" rule). Narrowing back to "owner" still requires
# the current level's role — once IT publishes to org, only org_admin can
# pull it back.


def _can_publish_at(
    claims: Dict[str, Any],
    level: AudienceLevel,
    target_id: Optional[str],
    app_doc: Optional[dict] = None,
) -> tuple[bool, str]:
    """Can the caller publish (or change audience) at this level/target?

    Returns ``(allowed, reason)``. ``reason`` is a short code suitable for
    UI tooltips when ``allowed`` is False.
    """
    roles = set(claims.get("roles") or [])
    if "super_admin" in roles:
        return (True, "")
    sa_admin = set(claims.get("service_account_admin_of") or [])
    sa_member = set(claims.get("service_account_member_of") or [])
    dept_ids = set(claims.get("dept_ids") or [])
    org_id = claims.get("org_id") or ""

    if level == "owner":
        # Owner-SA admin can always set audience to "owner" (narrow to own SA).
        # When an app_doc is provided, also accept membership-of-owner.
        if app_doc is not None:
            spec = app_doc.get("app_spec") or {}
            owner_id = spec.get("owner_id") or ""
            if owner_id and (owner_id in sa_admin or owner_id in sa_member):
                return (True, "")
        if sa_admin:
            return (True, "")
        return (False, "not_an_sa_admin")

    if level == "team":
        if target_id and target_id in sa_admin:
            return (True, "")
        # Publish-to-a-user: a builder/admin may publish an app they own to a
        # target user's Work SA (audience "team:<work_sa>"), even though they do
        # not administer that SA. This only exposes THEIR OWN app to that SA's
        # members — not a cross-tenant data path — so SA-admin membership is not
        # required here. The owner + current-level checks in _can_change_audience
        # still gate who may touch this app at all (and prevent widening others'
        # apps). Mirrors the frontend "Publish to a user" flow.
        if target_id and (roles & _APP_BUILDER_ROLES):
            return (True, "")
        return (False, "not_team_admin")

    if level == "dept":
        if "org_admin" in roles:
            return (True, "")
        if "dept_admin" in roles and target_id and target_id in dept_ids:
            return (True, "")
        return (False, "needs_dept_admin")

    if level == "org":
        if "org_admin" in roles and (not target_id or target_id == org_id):
            return (True, "")
        return (False, "needs_org_admin")

    return (False, "unknown_audience_level")


def _can_change_audience(
    claims: Dict[str, Any], current_audience: str, target_audience: str, app_doc: dict,
) -> tuple[bool, str]:
    """Higher-of(current, target) rule: caller must satisfy BOTH levels."""
    cur_level, cur_target = parse_audience(current_audience)
    tgt_level, tgt_target = parse_audience(target_audience)

    target_ok, target_reason = _can_publish_at(claims, tgt_level, tgt_target, app_doc)
    if not target_ok:
        return (False, f"target_{target_reason}")

    current_ok, current_reason = _can_publish_at(claims, cur_level, cur_target, app_doc)
    if not current_ok:
        return (False, f"current_{current_reason}")

    return (True, "")


def _audience_label(level: AudienceLevel, target_id: Optional[str]) -> str:
    """Human-readable label for an audience value (used in publish-options)."""
    if level == "owner":
        return "Owner only (just my team)"
    if level == "team":
        return f"Team SA · {target_id}"
    if level == "dept":
        return f"Department · {target_id}"
    if level == "org":
        return "Everyone in the organization"
    return target_id or level


def _enumerate_publish_options(
    claims: Dict[str, Any], app_doc: dict,
) -> List[PublishOption]:
    """Return the audience values to offer the caller in the publish picker.

    The list always includes ``owner`` + ``org`` (the two endpoints of the
    hierarchy) plus every team SA the caller admins and every dept they
    belong to. ``allowed`` reflects the higher-of(current, target) rule
    given the app's current audience.
    """
    spec = app_doc.get("app_spec") or {}
    current_aud = spec.get("audience") or "owner"
    owner_id = spec.get("owner_id") or ""
    sa_admin = list(claims.get("service_account_admin_of") or [])
    dept_ids = list(claims.get("dept_ids") or [])
    org_id = claims.get("org_id") or ""

    candidates: list[tuple[AudienceLevel, Optional[str], str]] = []
    candidates.append(("owner", None, "owner"))
    for sa in sa_admin:
        if sa == owner_id:
            continue  # already covered by "owner"
        candidates.append(("team", sa, f"team:{sa}"))
    for d in dept_ids:
        candidates.append(("dept", d, f"dept:{d}"))
    candidates.append(("org", org_id or None, "org"))

    out: List[PublishOption] = []
    seen: set[str] = set()
    for level, tgt, value in candidates:
        if value in seen:
            continue
        seen.add(value)
        allowed, reason = _can_change_audience(claims, current_aud, value, app_doc)
        out.append(
            PublishOption(
                value=value,
                label=_audience_label(level, tgt),
                level=level,
                target_id=tgt,
                allowed=allowed,
                reason=None if allowed else reason,
            )
        )
    return out


def _visibility_or_clauses(claims: Dict[str, Any]) -> List[dict]:
    """OR-clauses for the "apps visible to me via audience or owner-SA" query.

    Visibility:
      1. Caller is a member of the owner SA  (owner_id ∈ my SAs)
      2. audience = "team:<X>" where X is an SA I admin or am a member of
      3. audience = "dept:<X>" where X is a dept I belong to
      4. audience = "org" AND the app's tenant matches mine
    """
    sa_ids = list(
        set(claims.get("service_account_admin_of") or [])
        | set(claims.get("service_account_member_of") or [])
    )
    dept_ids = list(claims.get("dept_ids") or [])
    org_id = claims.get("org_id") or claims.get("tenant_id") or ""

    clauses: List[dict] = []
    if sa_ids:
        clauses.append({
            "app_spec.owner_type": "service_account",
            "app_spec.owner_id": {"$in": sa_ids},
        })
        clauses.append({
            "app_spec.audience": {"$in": [f"team:{s}" for s in sa_ids]},
        })
    if dept_ids:
        clauses.append({
            "app_spec.audience": {"$in": [f"dept:{d}" for d in dept_ids]},
        })
    if org_id:
        clauses.append({"app_spec.audience": "org", "tenant_id": org_id})
    return clauses


class AppTransferRequest(BaseModel):
    new_owner_type: str  # service_account | dept | org
    new_owner_id: str
    reason: Optional[str] = None


@app.post("/apps/{slug}/transfer")
async def transfer_app(slug: str, payload: AppTransferRequest, request: Request):
    """Owner-initiated transfer of an app's ownership. SA / dept / org only.

    Edit access moves with ownership. Audience is preserved across
    transfer — the people who could RUN the app before still can after
    (they keep matching the audience clause).
    """
    claims = _claims(request)
    if not claims["user_id"]:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "auth required")
    if payload.new_owner_type not in {"service_account", "dept", "org"}:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "new_owner_type must be 'service_account', 'dept', or 'org'",
        )
    new_id = (payload.new_owner_id or "").strip()
    if not new_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "new_owner_id required")

    apps = get_apps_col()
    app_doc = await apps.find_one({"slug": slug})
    if not app_doc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "app not found")
    if not _is_lifecycle_admin(claims, app_doc):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "owner or admin required")

    roles = set(claims["roles"])
    is_global_admin = bool(roles & {"org_admin", "super_admin"})

    if not is_global_admin:
        if payload.new_owner_type == "service_account":
            if new_id not in set(claims["service_account_admin_of"]) | set(
                claims["service_account_member_of"]
            ):
                raise HTTPException(
                    status.HTTP_403_FORBIDDEN,
                    f"you are not a member of service account {new_id}",
                )
        elif payload.new_owner_type == "dept":
            if new_id not in set(claims["dept_ids"]):
                raise HTTPException(
                    status.HTTP_403_FORBIDDEN, f"not a member of dept {new_id}"
                )
        elif payload.new_owner_type == "org":
            if new_id != claims["org_id"]:
                raise HTTPException(
                    status.HTTP_403_FORBIDDEN, "can only transfer to your own org"
                )

    spec = dict(app_doc.get("app_spec") or {})
    prev_owner = {
        "owner_type": spec.get("owner_type") or "service_account",
        "owner_id": spec.get("owner_id") or "",
        "changed_at": datetime.now(timezone.utc),
        "changed_by": claims["email"] or claims["user_id"],
    }

    now = datetime.now(timezone.utc)
    await apps.update_one(
        {"slug": slug},
        {
            "$set": {
                "app_spec.owner_type": payload.new_owner_type,
                "app_spec.owner_id": new_id,
                "app_spec.owner_changed_at": now,
                "app_spec.owner_changed_by": claims["email"] or claims["user_id"],
                "updated_at": now,
            },
            "$push": {
                "app_spec.previous_owners": prev_owner,
                "lifecycle_audit": _lifecycle_audit_entry(
                    claims, "transfer",
                    from_owner=prev_owner,
                    to={"owner_type": payload.new_owner_type, "owner_id": new_id},
                    reason=payload.reason or "",
                ),
            },
        },
    )
    return {
        "ok": True,
        "slug": slug,
        "previous_owner": {"owner_type": prev_owner["owner_type"], "owner_id": prev_owner["owner_id"]},
        "new_owner": {"owner_type": payload.new_owner_type, "owner_id": new_id},
        # Audience is preserved through transfer (it controls READ/RUN,
        # independent of OWNERSHIP). Echo it so callers can confirm.
        "audience": spec.get("audience") or "owner",
    }


# ---------------------------------------------------------------------------
# Audience: publish-level distribution control
# ---------------------------------------------------------------------------


@app.get("/apps/{slug}/publish-options", response_model=PublishOptionsResponse)
async def publish_options(slug: str, request: Request) -> PublishOptionsResponse:
    """Return the audience values the caller can pick for this app.

    Drives the publish picker in Citra-UI: allowed options are selectable,
    disallowed options come back with a ``reason`` code (``not_team_admin``,
    ``needs_dept_admin``, ``needs_org_admin``) the UI surfaces as a tooltip.
    """
    claims = _claims(request)
    if not claims["user_id"]:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "auth required")

    # Resolve by store so the audience picker works for a TEST app too (the
    # Promote-to-Prod flow opens this picker for an app in the test_ store).
    await _bind_app_env(slug)
    apps = get_apps_col()
    app_doc = await apps.find_one({"slug": slug})
    if not app_doc or not _user_can_access(app_doc, request):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "app not found")

    spec = app_doc.get("app_spec") or {}
    current = spec.get("audience") or "owner"
    options = _enumerate_publish_options(claims, app_doc)
    return PublishOptionsResponse(slug=slug, current=current, options=options)


def compute_loop_metrics(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Self-improving loop health from DecisionRecords (pure; read-only).

    Headline signals:
      • override_rate (human_approved): fraction the officer CORRECTED — trends
        DOWN as the model's recommendations match the team.
      • good_rate (settled good/bad): trends UP as decisions improve.
      • automation_rate: auto_process share of committed decisions.
    Plus a weekly trend (to SEE the curves) and a per-model breakdown (the
    foundation for the model-swap-invariance comparison)."""
    def _model_key(m: Any) -> str:
        if isinstance(m, dict):
            return str(m.get("id") or m.get("model") or m.get("name") or "unknown")
        return str(m) if m else "unknown"

    def _iso_week(dt: Any) -> str:
        return dt.strftime("%G-W%V") if isinstance(dt, datetime) else "unknown"

    def _rate(a: int, b: int):
        return round(a / b, 4) if b else None

    by_mode: Dict[str, int] = {}
    outcome_counts = {"good": 0, "bad": 0, "neutral": 0, "unknown": 0}
    committed = auto_committed = ha_total = ha_override = 0
    weeks: Dict[str, Dict[str, int]] = {}
    models: Dict[str, Dict[str, int]] = {}
    # Memory-lift cohorts (adoption plan §2.2): acceptance among runs grounded
    # with ≥1 retrieved sample vs cold runs. None retrieval_count = pre-stamp
    # record → excluded (never coerced to cold). "accepted" = approved
    # UNMODIFIED, consistent with /org/decision-stats.
    lift_cohorts = {c: {"accepted": 0, "disposed": 0} for c in ("with_memory", "cold")}
    # Top overridden fields — which fields officers keep correcting; should
    # ROTATE as rubrics absorb corrections.
    override_fields: Dict[str, int] = {}

    for r in records:
        mode = r.get("mode") or "unknown"
        by_mode[mode] = by_mode.get(mode, 0) + 1
        if (r.get("action_result") or {}).get("committed"):
            committed += 1
            if mode == "auto_process":
                auto_committed += 1
        label = ((r.get("outcome") or {}).get("label")) or None
        outcome_counts[label if label in outcome_counts else "unknown"] += 1
        wk = _iso_week(r.get("created_at"))
        w = weeks.setdefault(wk, {"n": 0, "ha": 0, "override": 0, "good": 0, "bad": 0})
        w["n"] += 1
        if mode == "human_approved":
            ha_total += 1
            w["ha"] += 1
            if r.get("overrides"):
                ha_override += 1
                w["override"] += 1
                for ov in r.get("overrides") or []:
                    fmap = (ov or {}).get("override")
                    if isinstance(fmap, dict):
                        for fld in fmap:
                            override_fields[str(fld)] = override_fields.get(str(fld), 0) + 1
        if mode in ("human_approved", "human_rejected"):
            rc = r.get("retrieval_count")
            if rc is not None:
                cohort = "with_memory" if rc > 0 else "cold"
                lift_cohorts[cohort]["disposed"] += 1
                if mode == "human_approved" and not r.get("overrides"):
                    lift_cohorts[cohort]["accepted"] += 1
        if label == "good":
            w["good"] += 1
        elif label == "bad":
            w["bad"] += 1
        mk = _model_key(r.get("model"))
        m = models.setdefault(mk, {"n": 0, "good": 0, "bad": 0})
        m["n"] += 1
        if label == "good":
            m["good"] += 1
        elif label == "bad":
            m["bad"] += 1

    settled = outcome_counts["good"] + outcome_counts["bad"]
    trend = [
        {"week": wk, "n": weeks[wk]["n"],
         "override_rate": _rate(weeks[wk]["override"], weeks[wk]["ha"]),
         "good_rate": _rate(weeks[wk]["good"], weeks[wk]["good"] + weeks[wk]["bad"])}
        for wk in sorted(weeks)
    ]
    by_model = {
        k: {"n": v["n"], "good": v["good"], "bad": v["bad"],
            "good_rate": _rate(v["good"], v["good"] + v["bad"])}
        for k, v in models.items()
    }
    wm, cold = lift_cohorts["with_memory"], lift_cohorts["cold"]
    lift = None
    if wm["disposed"] >= _LIFT_MIN_COHORT and cold["disposed"] >= _LIFT_MIN_COHORT:
        lift = round(
            (wm["accepted"] / wm["disposed"]) - (cold["accepted"] / cold["disposed"]), 4)
    return {
        "total": len(records),
        "by_mode": by_mode,
        "committed": committed,
        "automation_rate": _rate(auto_committed, committed),
        "override_rate": _rate(ha_override, ha_total),
        "good_rate": _rate(outcome_counts["good"], settled),
        "outcome_counts": outcome_counts,
        "trend_weekly": trend,
        "by_model": by_model,
        "top_overridden_fields": sorted(
            ({"field": f, "overrides": n} for f, n in override_fields.items()),
            key=lambda x: -x["overrides"])[:10],
        "memory_lift": {
            "with_memory": {**wm, "acceptance_rate": _rate(wm["accepted"], wm["disposed"])},
            "cold": {**cold, "acceptance_rate": _rate(cold["accepted"], cold["disposed"])},
            "lift": lift,
            **({} if lift is not None else {"note": (
                f"insufficient data — needs ≥{_LIFT_MIN_COHORT} disposed decisions "
                f"in both cohorts (with memory: {wm['disposed']}, cold: {cold['disposed']})"
            )}),
        },
    }


@app.get("/apps/{slug}/decisions/{decision_id}")
async def get_decision_record_detail(
    slug: str, decision_id: str, request: Request,
) -> Dict[str, Any]:
    """One ledger record, read-only — the tap-through behind a precedent chip
    (adoption plan §4.4) and the headless caller's receipt lookup. Scoped to
    the app's own ledger (slug filter) + the app's audience gate; PII posture
    matches the officer surface (context/recommendation are what the officer
    already sees on the result card)."""
    await _bind_app_env(slug)
    app_doc = await get_apps_col().find_one({"slug": slug})
    if not app_doc or not _can_render_app(app_doc, request):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "app not found")
    rec = await get_decision_records_col().find_one(
        {"decision_id": decision_id, "slug": slug}, {"_id": 0})
    if not rec:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "decision not found")
    return rec


@app.get("/apps/{slug}/loop-metrics")
async def get_loop_metrics(
    slug: str, request: Request, days: int = 90,
) -> Dict[str, Any]:
    """Self-improving loop health for this app (read-only), from decision_records:
    override-rate (down = learning), good-rate (up = improving), automation rate,
    a weekly trend, and a per-model breakdown (the model-swap-invariance view)."""
    await _bind_app_env(slug)
    app_doc = await get_apps_col().find_one({"slug": slug})
    if not app_doc or not _can_render_app(app_doc, request):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "app not found")
    since = datetime.now(timezone.utc) - timedelta(days=max(1, int(days)))
    try:
        recs = await get_decision_records_col().find({
            "app_id": app_doc.get("app_id"),
            "tenant_id": app_doc.get("tenant_id"),
            "created_at": {"$gte": since},
        }).sort("created_at", 1).to_list(20000)
    except Exception:  # noqa: BLE001 — metrics are best-effort, never 500 the view
        logger.exception("[loop] loop-metrics fetch failed for %s", slug)
        recs = []
    out = compute_loop_metrics(recs)
    out["slug"] = slug
    out["window_days"] = int(days)
    # Time-to-decision (case staged → officer disposed), from the staging rows'
    # created_at → resolved_at. Median + p90 seconds; best-effort like the rest.
    try:
        _tt_rows = await get_workflow_staging_col().find(
            {"slug": slug, "resolved_at": {"$ne": None},
             "created_at": {"$gte": since}},
            {"created_at": 1, "resolved_at": 1},
        ).sort("created_at", -1).to_list(length=5000)
        _secs = sorted(
            (r["resolved_at"] - r["created_at"]).total_seconds()
            for r in _tt_rows
            if isinstance(r.get("created_at"), datetime)
            and isinstance(r.get("resolved_at"), datetime)
            and r["resolved_at"] >= r["created_at"]
        )
        out["time_to_decision"] = {
            "n": len(_secs),
            "median_seconds": round(_secs[len(_secs) // 2], 1) if _secs else None,
            "p90_seconds": round(_secs[int(len(_secs) * 0.9)], 1) if _secs else None,
        }
    except Exception:  # noqa: BLE001 — metrics are best-effort, never 500 the view
        logger.exception("[loop] time-to-decision fetch failed for %s", slug)
        out["time_to_decision"] = {"n": 0, "median_seconds": None, "p90_seconds": None}
    # Correction absorption (adoption plan §2.3): for each field the officer
    # corrected (decision-rubric corrections carry structured `fields`; older
    # entries are parsed from the "corrected <field>:" prose), did another
    # override on that field occur AFTER the correction + settling window?
    # "taught → stopped recurring" is the feedback-loop-closes proof.
    try:
        from analysis_rubrics import (
            RECORD_MODALITY, RECORD_TASK_TYPE, rubric_tenant_for_app,
        )
        from corrections import corrections_for_bucket

        _absorb: Dict[str, Any] = {"taught": 0, "stopped_recurring": 0, "fields": []}
        # Reads the EVIDENCE ledger directly. The old blob carried the
        # corrections array inline; that collection is gone, and the ledger is
        # the better source anyway — `contested_fields` is structured, so the
        # metric never has to parse "corrected <field>:" out of prose.
        _rows: list = []
        for _t in {app_doc.get("tenant_id"), rubric_tenant_for_app(app_doc)}:
            if _t:
                _rows = await corrections_for_bucket(
                    tenant_id=_t, app_slug=slug,
                    modality=RECORD_MODALITY, task_type=RECORD_TASK_TYPE)
                if _rows:
                    break
        _settle = timedelta(days=7)
        if _rows:
            # latest correction time per field (structured first, prose fallback)
            _latest: Dict[str, datetime] = {}
            for c in _rows:
                at = c.get("at")
                if not isinstance(at, datetime):
                    continue
                flds = c.get("contested_fields") or [
                    m.group(1) for m in
                    re.finditer(r"corrected (\w+):", str(c.get("reason_text") or ""))
                ]
                for f in flds:
                    if f and (f not in _latest or at > _latest[f]):
                        _latest[f] = at
            for f, taught_at in sorted(_latest.items()):
                recurred = any(
                    isinstance(r.get("created_at"), datetime)
                    and r["created_at"] > taught_at + _settle
                    and any(
                        f in ((ov or {}).get("override") or {})
                        for ov in (r.get("overrides") or [])
                    )
                    for r in recs
                )
                _absorb["taught"] += 1
                if not recurred:
                    _absorb["stopped_recurring"] += 1
                _absorb["fields"].append({
                    "field": f,
                    "taught_at": taught_at.isoformat(),
                    "recurred": recurred,
                })
        out["correction_absorption"] = _absorb
    except Exception:  # noqa: BLE001 — metrics are best-effort, never 500 the view
        logger.exception("[loop] correction absorption failed for %s", slug)
        out["correction_absorption"] = {"taught": 0, "stopped_recurring": 0, "fields": []}
    # Memory block — the asset inventory BESIDE the outcome rates: shows the
    # memory machinery firing (rubrics folding, item ledger growing, precedents
    # grounding analyses), not just outcomes improving. Cumulative by design —
    # the memory is an asset, not a window. Each part is best-effort with loud
    # logs (same posture as the records fetch above).
    memory: Dict[str, Any] = {"clauses": {}, "corrections": {}, "items": {},
                              "grounding": None}
    try:
        from analysis_rubrics import rubric_tenant_for_app
        from clause_store import clause_inventory
        from corrections import correction_stats
        from item_records import ledger_stats

        # Both tenant keys: rubric/ledger rows may be keyed by the app org or
        # (legacy image/doc paths) the officer's JWT org — in single-tenant
        # deployments they coincide; $in covers both without double counting.
        _tenants = list({t for t in (
            app_doc.get("tenant_id"), rubric_tenant_for_app(app_doc),
        ) if t})
        memory["clauses"] = await clause_inventory(tenant_ids=_tenants, app_slug=slug)
        memory["corrections"] = await correction_stats(tenant_ids=_tenants, app_slug=slug)
        memory["items"] = await ledger_stats(tenant_ids=_tenants, slug=slug)
    except Exception:  # noqa: BLE001 — metrics are best-effort, never 500 the view
        logger.exception("[loop] memory inventory failed for %s", slug)
    try:
        # Grounding samples: the durable store is Milvus; the run result lives
        # in Redis for 1h after a refresh. Present = fresh info; None = no
        # recent refresh (not "no samples").
        from grounding_runs import get_grounding_run_store

        _grun = get_grounding_run_store().get(slug)
        _gres = (_grun or {}).get("result") or {}
        if _gres.get("total_samples") is not None:
            memory["grounding"] = {
                "total_samples": _gres.get("total_samples"),
                "canonical_samples": _gres.get("canonical_samples"),
                "neighbor_samples": _gres.get("neighbor_samples"),
                "completed_at": _grun.get("completed_at"),
            }
    except Exception:  # noqa: BLE001 — Redis optional here; loud, non-fatal
        logger.exception("[loop] grounding-store read failed for %s", slug)
    out["memory"] = memory
    return out


# ---------------------------------------------------------------------------
# Memory screen API (§ Memory screen — in-product visibility and curation).
# One platform surface: reads are dept-scoped (same gate as rendering the
# app); the ONLY writes are the two curation acts — governed rubric edit
# (version bump + history, never in place) and the ledger exclude-from-
# retrieval flag (curation affects retrieval, never the record).
# ---------------------------------------------------------------------------


def _memory_tenants(app_doc: dict) -> List[str]:
    """Both tenant-key candidates (app-org + legacy JWT-org buckets) — same
    dedupe the loop-metrics memory block uses."""
    from analysis_rubrics import rubric_tenant_for_app

    return list({t for t in (
        app_doc.get("tenant_id"), rubric_tenant_for_app(app_doc),
    ) if t})


def _require_memory_curator(app_doc: dict, request: Request) -> None:
    """The MEMORY-ACCESS gate — both READ and WRITE of the memory surface.

    Authorized = the app's OWNER (the BA/SA that created it, via owner-SA admin)
    OR dept_admin (of the app's dept, same org) OR org_admin (same org) OR
    super_admin — exactly `_can_edit_app`. The learned memory (rubric criteria +
    corrections, the item ledger with officer reasons / artifact refs, the
    export) is a curation/owner surface, NOT an end-user one: a plain audience
    member who can merely open the app must not read or change it. 403 otherwise."""
    if not _can_edit_app(
        app_doc,
        get_secure_user_id(request),
        get_tenant_id(request),
        user_org_id=get_org_id(request),
        user_dept_ids=get_user_dept_ids(request),
        user_roles=get_user_roles(request),
        sa_admin_of=get_sa_admin_of(request),
    ):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "memory curation requires dept_admin / org_admin / super_admin",
        )


class MemoryRubricEdit(BaseModel):
    modality: str
    task_type: str
    summary: str


class MemoryItemExclusion(BaseModel):
    excluded: bool


@app.get("/apps/{slug}/memory/clauses")
async def get_memory_clauses(slug: str, request: Request) -> Dict[str, Any]:
    """"Rules it follows" tab: every learned clause with its scope, the officers
    who back it, how often it fired and how often it was right — plus the
    correction counters behind them.

    Replaces the old /memory/rubrics endpoint. There is no summary blob to show
    any more; a clause list IS the readable form of what the app learned, and
    unlike the blob every line is traceable to the rejects that taught it.
    Owner + dept/org admin only (see _require_memory_curator)."""
    await _bind_app_env(slug)
    app_doc = await get_apps_col().find_one({"slug": slug})
    if not app_doc or not _can_render_app(app_doc, request):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "app not found")
    _require_memory_curator(app_doc, request)
    from clause_store import clause_inventory, list_clauses
    from corrections import correction_stats

    tenants = _memory_tenants(app_doc)
    from case_signature import learning_config, signature_of

    gate = learning_config(signature_of(app_doc))["promotion_min_officers"]
    stats = await correction_stats(tenant_ids=tenants, app_slug=slug)
    out = {
        "slug": slug,
        "clauses": await list_clauses(tenant_ids=tenants, app_slug=slug),
        "inventory": await clause_inventory(tenant_ids=tenants, app_slug=slug),
        "corrections": stats,
        "promotion_min_officers": gate,
    }
    # J6: the gate-reachability notice. Informational, not a cliff — since J2,
    # a small team's judgements are USED as individual judgements, clearly
    # labeled; this just tells the admin why nothing says "team judgement" yet.
    seen = int(stats.get("distinct_officers") or 0)
    if seen < gate:
        out["gate_notice"] = (
            f"This app has seen {seen} officer(s); team judgements need "
            f"{gate} to agree. Its judgements are still used — labeled as "
            "individual officers' judgements — until more officers correct it.")
    return out


@app.get("/apps/{slug}/memory/clauses/{clause_id}/provenance")
async def get_clause_provenance(
    slug: str, clause_id: str, request: Request,
) -> Dict[str, Any]:
    """The rejects that TAUGHT one clause.

    This is the accountability the blob could never offer: an officer asking
    "why does it say that?" gets the actual past cases, verbatim, instead of a
    paragraph nobody can trace."""
    await _bind_app_env(slug)
    app_doc = await get_apps_col().find_one({"slug": slug})
    if not app_doc or not _can_render_app(app_doc, request):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "app not found")
    _require_memory_curator(app_doc, request)
    from clause_store import list_clauses
    from corrections import corrections_by_ids

    tenants = _memory_tenants(app_doc)
    clause = next((c for c in await list_clauses(tenant_ids=tenants, app_slug=slug)
                   if c.get("clause_id") == clause_id), None)
    if clause is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "clause not found")
    return {
        "clause": clause,
        "corrections": await corrections_by_ids(
            correction_ids=list(clause.get("provenance") or [])),
    }


@app.get("/apps/{slug}/memory/clauses/by-officer/{officer}")
async def clauses_taught_by(slug: str, officer: str, request: Request) -> Dict[str, Any]:
    """Every judgement one officer helped teach (J6 — the dismissed-officer
    drill: one filter, one review, one tap each). Curator-gated."""
    await _bind_app_env(slug)
    app_doc = await get_apps_col().find_one({"slug": slug})
    if not app_doc or not _can_render_app(app_doc, request):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "app not found")
    _require_memory_curator(app_doc, request)
    from clause_store import list_clauses

    rows = await list_clauses(tenant_ids=_memory_tenants(app_doc), app_slug=slug)
    return {"officer": officer,
            "clauses": [c for c in rows
                        if officer in (c.get("support_officers") or [])]}


@app.post("/apps/{slug}/memory/clauses/{clause_id}/quarantine")
async def quarantine_clause(
    slug: str, clause_id: str, body: MemoryRubricEdit, request: Request,
) -> Dict[str, Any]:
    """Suspend a judgement (J6): excluded from injection, evidence intact,
    reversible via un-quarantine. The tool for 'that officer was dismissed —
    pull their teachings pending review' — distinct from retire, which is a
    verdict; quarantine is a hold."""
    await _bind_app_env(slug)
    app_doc = await get_apps_col().find_one({"slug": slug})
    if not app_doc or not _can_render_app(app_doc, request):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "app not found")
    _require_memory_curator(app_doc, request)
    from analysis_rubrics import rubric_tenant_for_app
    from clause_store import set_status

    try:
        await set_status(
            tenant_id=rubric_tenant_for_app(app_doc), app_slug=slug,
            clause_id=clause_id, status="quarantined",
            actor=get_secure_user_id(request),
            cause=(body.summary or "quarantined by an admin")[:200])
    except LookupError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from e
    return {"ok": True, "clause_id": clause_id, "status": "quarantined"}


@app.post("/apps/{slug}/memory/clauses/{clause_id}/challenge")
async def challenge_clause_endpoint(
    slug: str, clause_id: str, body: MemoryRubricEdit, request: Request,
) -> Dict[str, Any]:
    """Stop a judgement pending adjudication — the experienced officer's lever.

    Corroboration is a HEADCOUNT: three officers who share a misconception form
    a team judgement, while the one person who knows better contributes one
    dissent in four (0.25, under DISSENT_RATIO) and changes nothing until they
    find a second dissenter. This is the lever they did not have.

    Deliberately NOT a trust tier — nobody's vote is weighted, and seniority is
    never stored. It is a ROLE-held stop: a curator parks ONE clause and a named
    human then has to decide. Everything is attributed: actor and cause go into
    the clause's `history` via set_status, and the `challenge` block records who,
    when, and why in their own words. `body.summary` carries the reason and is
    REQUIRED — an adjudicator is being asked to choose between two officers and
    cannot do that from a flag alone.
    """
    await _bind_app_env(slug)
    app_doc = await get_apps_col().find_one({"slug": slug})
    if not app_doc or not _can_render_app(app_doc, request):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "app not found")
    _require_memory_curator(app_doc, request)
    from analysis_rubrics import rubric_tenant_for_app
    from clause_store import ClauseError, challenge_clause

    try:
        out = await challenge_clause(
            tenant_id=rubric_tenant_for_app(app_doc), app_slug=slug,
            clause_id=clause_id, reason=(body.summary or ""),
            actor=get_secure_user_id(request))
    except LookupError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from e
    except ClauseError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e
    return {"ok": True, **out}


@app.post("/apps/{slug}/memory/clauses/{clause_id}/reinstate")
async def reinstate_clause_endpoint(
    slug: str, clause_id: str, body: MemoryRubricEdit, request: Request,
) -> Dict[str, Any]:
    """Put a judgement the PRECISION MONITOR withdrew back into service.

    Without this, `underperforming` is a one-way door: a parked clause never
    fires again, so its counters freeze and precision can never recover. The
    only exit would be retire — wrong for a judgement that dipped during a bad
    fortnight, or whose blame count is inflated by overrides made for an
    unrelated reason.

    Resets the counters so the judgement gets a real re-measurement window;
    without that the next consolidation pass re-parks it from the same totals.
    The numbers that parked it are preserved on the history entry.

    Only for monitor-parked clauses. A quarantine or a retirement is a person's
    decision and is lifted through its own path."""
    await _bind_app_env(slug)
    app_doc = await get_apps_col().find_one({"slug": slug})
    if not app_doc or not _can_render_app(app_doc, request):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "app not found")
    _require_memory_curator(app_doc, request)
    from analysis_rubrics import rubric_tenant_for_app
    from case_signature import learning_config, signature_of
    from clause_store import ClauseError, reinstate_clause

    try:
        out = await reinstate_clause(
            tenant_id=rubric_tenant_for_app(app_doc), app_slug=slug,
            clause_id=clause_id, actor=get_secure_user_id(request),
            reason=(body.summary or ""),
            promotion_min_officers=learning_config(
                signature_of(app_doc))["promotion_min_officers"])
    except LookupError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from e
    except ClauseError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e
    return {"ok": True, **out}


@app.post("/apps/{slug}/memory/clauses/{clause_id}/challenge/resolve")
async def resolve_clause_challenge(
    slug: str, clause_id: str, body: MemoryRubricEdit, request: Request,
) -> Dict[str, Any]:
    """Adjudicate a challenge. ``body.summary`` = 'uphold' or 'dismiss',
    optionally followed by ': <reason>'.

    uphold  — the challenger is right; the judgement retires. The corrections
              that taught it stay, so a corrected version can re-form.
    dismiss — the judgement stands and returns to service at the tier its
              officer support earns. The challenge REMAINS on the record with
              both names: an objection that was overruled is still history.

    Both outcomes are attributed and neither is silent."""
    await _bind_app_env(slug)
    app_doc = await get_apps_col().find_one({"slug": slug})
    if not app_doc or not _can_render_app(app_doc, request):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "app not found")
    _require_memory_curator(app_doc, request)
    from analysis_rubrics import rubric_tenant_for_app
    from case_signature import learning_config, signature_of
    from clause_store import ClauseError, resolve_challenge

    raw = (body.summary or "").strip()
    action, _, reason = raw.partition(":")
    _promo = learning_config(signature_of(app_doc))["promotion_min_officers"]
    try:
        out = await resolve_challenge(
            tenant_id=rubric_tenant_for_app(app_doc), app_slug=slug,
            clause_id=clause_id, action=action.strip().lower(),
            reason=reason, actor=get_secure_user_id(request),
            promotion_min_officers=_promo)
    except LookupError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from e
    except ClauseError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e
    return {"ok": True, **out}


@app.post("/apps/{slug}/memory/clauses/{clause_id}/sop-resolution")
async def resolve_clause_sop_conflict(
    slug: str, clause_id: str, body: MemoryRubricEdit, request: Request,
) -> Dict[str, Any]:
    """The two-tap resolution for a judgement suspended as sop_conflict (J3).

    body.summary carries the action: 'retire' (the SOP is right) or
    'acknowledge' (the officers are right — the SOP is the stale one; the
    judgement returns to service with the disagreement recorded, which is the
    org's signal that the SOP itself needs updating)."""
    await _bind_app_env(slug)
    app_doc = await get_apps_col().find_one({"slug": slug})
    if not app_doc or not _can_render_app(app_doc, request):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "app not found")
    _require_memory_curator(app_doc, request)
    from analysis_rubrics import rubric_tenant_for_app
    from case_signature import learning_config, signature_of
    from clause_store import ClauseError, resolve_sop_conflict

    action = (body.summary or "").strip().lower()
    try:
        return await resolve_sop_conflict(
            tenant_id=rubric_tenant_for_app(app_doc), app_slug=slug,
            clause_id=clause_id, action=action,
            actor=get_secure_user_id(request),
            promotion_min_officers=learning_config(
                signature_of(app_doc))["promotion_min_officers"])
    except ClauseError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e)) from e
    except LookupError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from e


@app.post("/apps/{slug}/memory/clauses/{clause_id}/retire")
async def retire_clause(
    slug: str, clause_id: str, body: MemoryRubricEdit, request: Request,
) -> Dict[str, Any]:
    """Retire a clause. The ONLY in-product mutation of learned knowledge.

    Retire, never edit: a clause's text is provenanced to the corrections that
    produced it, so rewriting it would leave a rule claiming evidence that does
    not say that. Status change is versioned into `history` — the old
    governed-edit surface is gone with the blob it edited."""
    await _bind_app_env(slug)
    app_doc = await get_apps_col().find_one({"slug": slug})
    if not app_doc or not _can_render_app(app_doc, request):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "app not found")
    _require_memory_curator(app_doc, request)
    from analysis_rubrics import rubric_tenant_for_app
    from clause_store import set_status

    try:
        await set_status(
            tenant_id=rubric_tenant_for_app(app_doc), app_slug=slug,
            clause_id=clause_id, status="retired",
            actor=get_secure_user_id(request),
            cause=(body.summary or "retired by an admin")[:200])
    except LookupError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from e
    return {"ok": True, "clause_id": clause_id, "status": "retired"}


@app.get("/apps/{slug}/memory/items")
async def get_memory_items(
    slug: str,
    request: Request,
    disposition: Optional[str] = None,
    modality: Optional[str] = None,
    limit: int = 50,
    cursor: Optional[str] = None,
) -> Dict[str, Any]:
    """Precedents tab: read-only ledger browse, newest first. Pagination uses an
    OPAQUE ``cursor`` (compound created_at+item_id) so rows sharing a created_at
    are never dropped across a page boundary. Owner + dept/org admin only (the
    ledger carries officer reasons + artifact refs) — not every audience member."""
    await _bind_app_env(slug)
    app_doc = await get_apps_col().find_one({"slug": slug})
    if not app_doc or not _can_render_app(app_doc, request):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "app not found")
    _require_memory_curator(app_doc, request)
    from item_records import encode_ledger_cursor, list_ledger_rows

    limit = max(1, min(int(limit), 200))
    rows = await list_ledger_rows(
        tenant_ids=_memory_tenants(app_doc), slug=slug,
        disposition=disposition, modality=modality,
        limit=limit, cursor=cursor,
    )
    return {
        "slug": slug, "items": rows,
        # cursor only when the page is full — a short page IS the end
        "next_cursor": (encode_ledger_cursor(rows[-1]) if len(rows) == limit else None),
    }


@app.post("/apps/{slug}/memory/items/{item_id}/exclusion")
async def post_memory_item_exclusion(
    slug: str, item_id: str, body: MemoryItemExclusion, request: Request,
) -> Dict[str, Any]:
    """Exclude-from-retrieval flag (or lift it): the row stays in the ledger —
    audit + training set intact — it just never grounds a prompt again.
    Curator-gated."""
    await _bind_app_env(slug)
    app_doc = await get_apps_col().find_one({"slug": slug})
    if not app_doc or not _can_render_app(app_doc, request):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "app not found")
    _require_memory_curator(app_doc, request)
    from item_records import set_precedent_exclusion

    try:
        n = await set_precedent_exclusion(
            tenant_ids=_memory_tenants(app_doc), slug=slug, item_id=item_id,
            excluded=body.excluded, actor=get_secure_user_id(request),
        )
    except Exception as e:  # noqa: BLE001 — a store failure is 503, NOT a 404
        logger.exception("[memory] exclusion write failed for %s/%s", slug, item_id)
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="ledger store unavailable; exclusion not applied") from e
    # matched_count==0 → the item genuinely has no ledger row (404). A re-apply
    # of an already-set flag matches the row (n>0) and is a clean idempotent OK.
    if n == 0:
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            f"no ledger rows for item {item_id!r}")
    return {"item_id": item_id, "excluded": body.excluded, "rows": n}


@app.get("/apps/{slug}/memory/export")
async def get_memory_export(slug: str, request: Request) -> Dict[str, Any]:
    """Manual full memory export for the app (Memory screen — admin action).

    The in-product twin of the §4 bucket workflow: one open-schema JSON
    document with the three memory collections (decision ledger, item ledger,
    rubrics) plus a schema note, so an admin can download and hold the memory
    asset directly. Curator-gated (bulk extraction of the whole asset). The
    downloaded document IS the deliverable — self-describing, no Citra tooling
    needed to read it."""
    await _bind_app_env(slug)
    app_doc = await get_apps_col().find_one({"slug": slug})
    if not app_doc or not _can_render_app(app_doc, request):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "app not found")
    _require_memory_curator(app_doc, request)

    tenants = _memory_tenants(app_doc)
    from clause_store import export_clauses
    from corrections import export_corrections
    from item_records import export_ledger

    # Each collection is fetched independently; a failure is recorded in
    # ``errors`` and flips ``partial`` so the downloaded asset is NEVER a
    # whole-looking partial (RULE #1 — the caller must distinguish failure from
    # a genuinely empty store). All three scope tenant by the SAME $in set
    # (app-org + legacy JWT-org) so the ledgers cover one consistent tenant set.
    errors: Dict[str, str] = {}
    decisions: List[Dict[str, Any]] = []
    try:
        decisions = await get_decision_records_col().find(
            {"app_id": app_doc.get("app_id"), "tenant_id": {"$in": tenants}},
            {"_id": 0},
        ).sort("created_at", -1).to_list(100000)
        if len(decisions) >= 100000:
            errors["decision_records"] = "hit the 100k row cap — partial"
            logger.error("[memory-export] decision_records hit 100k cap for %s", slug)
    except Exception as e:  # noqa: BLE001 — record + surface, never 500 the export
        logger.exception("[memory-export] decision_records fetch failed for %s", slug)
        errors["decision_records"] = f"fetch failed: {e}"

    items: List[Dict[str, Any]] = []
    try:
        items = await export_ledger(tenant_ids=tenants, slug=slug)
    except Exception as e:  # noqa: BLE001
        logger.exception("[memory-export] item ledger fetch failed for %s", slug)
        errors["item_decision_records"] = f"fetch failed: {e}"

    # The learned layer is now TWO stores: the clauses (the rules) and the
    # corrections that produced them (the evidence). Exporting only the rules
    # would hand the customer conclusions with no way to re-derive or audit
    # them — the exact opacity the blob had.
    clauses: List[Dict[str, Any]] = []
    try:
        clauses = await export_clauses(tenant_ids=tenants, app_slug=slug)
    except Exception as e:  # noqa: BLE001
        logger.exception("[memory-export] clause fetch failed for %s", slug)
        errors["smartapp_clauses"] = f"fetch failed: {e}"

    corrections_rows: List[Dict[str, Any]] = []
    try:
        corrections_rows = await export_corrections(tenant_ids=tenants, app_slug=slug)
    except Exception as e:  # noqa: BLE001
        logger.exception("[memory-export] correction fetch failed for %s", slug)
        errors["smartapp_corrections"] = f"fetch failed: {e}"

    return {
        "schema": "citra.memory.export/v1",
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "exported_by": get_secure_user_id(request),
        "app": {
            "slug": slug,
            "app_id": app_doc.get("app_id"),
            "tenant_id": app_doc.get("tenant_id"),
            "title": (app_doc.get("app_spec") or {}).get("title") or slug,
        },
        "counts": {
            "decision_records": len(decisions),
            "item_decision_records": len(items),
            "smartapp_clauses": len(clauses),
            "smartapp_corrections": len(corrections_rows),
        },
        # partial=true means one or more collections failed/truncated — the
        # download is NOT whole. errors names which. Consumers must check this
        # before treating the file as a complete asset.
        "partial": bool(errors),
        "errors": errors,
        "collections": {
            "decision_records": decisions,
            "item_decision_records": items,
            "smartapp_clauses": clauses,
            "smartapp_corrections": corrections_rows,
        },
        "notes": (
            "Open-schema memory export. decision_records = the governed decision "
            "ledger (recommendation, officer action, reasons, overrides, outcome). "
            "item_decision_records = per-image/document ledger (content_sha256 + "
            "media_ref reference files in your own systems of record — bytes are "
            "never held by Citra). smartapp_clauses = the RULES your officers taught "
            "the app, each scoped and linked to its evidence. "
            "smartapp_corrections = that evidence itself — every reject and "
            "override, verbatim, so every rule can be re-derived and audited "
            "rather than taken on trust. This is your asset; the same shape "
            "the continuous bucket export (§4) delivers."
        ),
    }


@app.post("/apps/{slug}/memory/export/run")
async def run_memory_export(
    slug: str, request: Request, mode: str = "incremental",
) -> Dict[str, Any]:
    """Push this app's memory to the customer's bucket as gzipped JSONL — the
    scheduled twin of the manual export (§4). ``mode=incremental`` (default)
    exports rows changed since the last run and advances the watermark;
    ``mode=snapshot`` exports everything to the snapshot prefix. Curator-gated.

    Cron wiring runs this per app in citra-worker; it is also admin-triggerable
    here for on-demand + demo. Fails LOUD if the export bucket isn't configured
    (no silent default) — the customer provisions a write-only bucket."""
    if mode not in ("incremental", "snapshot"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="mode must be 'incremental' or 'snapshot'")
    await _bind_app_env(slug)
    app_doc = await get_apps_col().find_one({"slug": slug})
    if not app_doc or not _can_render_app(app_doc, request):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "app not found")
    _require_memory_curator(app_doc, request)

    settings = get_settings()
    bucket = getattr(settings, "memory_export_bucket", None) or os.getenv("MEMORY_EXPORT_BUCKET")
    if not bucket:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="memory export bucket not configured (set MEMORY_EXPORT_BUCKET "
                   "to the customer's write-only bucket)")
    prefix = (getattr(settings, "memory_export_prefix", None)
              or os.getenv("MEMORY_EXPORT_PREFIX") or f"citra-memory/{slug}")

    from memory_export import _mongo_fetch, _s3_writer, _state_store, run_export

    tenants = _memory_tenants(app_doc)
    fetch = _mongo_fetch(app_doc, tenants)
    get_wm, set_wm = _state_store(app_doc)
    try:
        report = await run_export(
            prefix=prefix, fetch=fetch, writer=_s3_writer(bucket),
            state_get=get_wm, state_set=set_wm,
            now=datetime.now(timezone.utc), snapshot=(mode == "snapshot"),
        )
    except Exception as e:  # noqa: BLE001 — surface the failure loudly, don't 500 opaquely
        logger.exception("[memory-export] run failed for %s", slug)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY,
                            detail=f"memory export failed: {e}") from e
    return {"slug": slug, "bucket": bucket, "prefix": prefix, **report}


@app.get("/usage")
async def get_token_usage(request: Request, days: int = 30) -> Dict[str, Any]:
    """Per-tenant LLM token usage (the billing view). Scoped to the caller's
    org; org_admin / super_admin only. Aggregates the metered token_usage rows
    into totals + by‑model / by‑surface / by‑day for the window."""
    roles = set(get_user_roles(request))
    if not (roles & {"org_admin", "super_admin"}):
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            "org_admin or super_admin required to view usage")
    org_id = get_org_id(request) or get_tenant_id(request)
    if not org_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "no org identity on the caller")
    since = datetime.now(timezone.utc) - timedelta(days=max(1, int(days)))
    from token_metering import usage_summary

    try:
        summary = await usage_summary(tenant_ids=[org_id], since=since)
    except Exception as e:  # noqa: BLE001 — a billing read must surface failure, not 0
        logger.exception("[usage] summary failed for %s", org_id)
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="usage store unavailable") from e
    return {"org_id": org_id, "window_days": int(days), **summary}


@app.get("/apps/{slug}/decision-contract")
async def get_decision_contract(slug: str, request: Request) -> Dict[str, Any]:
    """Self-describing headless decision-API contract for an external/custom UI:
    the request input schema, the response shape, the endpoints to call, and the
    auth + governance rules. Paths are relative to the smart-app-service API base.
    Works for ANY Decision App (headless or UI-backed). View-gated."""
    await _bind_app_env(slug)
    app_doc = await get_apps_col().find_one({"slug": slug})
    if not app_doc or not _can_render_app(app_doc, request):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "app not found")
    agent_doc = await get_agents_col().find_one(
        {"agent_id": app_doc.get("agent_id"), "tenant_id": app_doc.get("tenant_id")},
        {"agent_spec.input_schema": 1, "agent_spec.name": 1,
         "agent_spec.tools_v2": 1, "agent_spec.actions": 1},
    )
    aspec = (agent_doc or {}).get("agent_spec") or {}
    spec = app_doc.get("app_spec") or {}
    _actions = [a for a in (aspec.get("actions") or []) if isinstance(a, dict)]
    # The per-action input schema is the accurate /run `inputs` contract; fall
    # back to the agent-level input_schema, then to an empty object.
    request_schema = (
        aspec.get("input_schema")
        or (_actions[0].get("input_schema") if _actions else None)
        or {"type": "object", "properties": {}}
    )
    # Entry actions for /run (the `action` field the caller sends). The Action
    # model keys the action by ``name``; tolerate a legacy ``id``.
    run_actions = [
        (a.get("name") or a.get("id")) for a in _actions
        if (a.get("name") or a.get("id"))
    ]
    # Governed SoR write actions: which TABLE each writes, the payload SCHEMA, and
    # which fields a human may OVERRIDE. This is the "which tables/schema" answer.
    write_actions = []
    for t in (aspec.get("tools_v2") or []):
        if not (isinstance(t, dict) and t.get("kind") == "mcp_action"):
            continue
        write_actions.append({
            "name": t.get("name"),
            "dataset_id": t.get("dataset_id"),
            "action_id": t.get("action_id"),
            "source_id": t.get("source_id"),
            "input_schema": t.get("input_schema") or {},
            "editable_fields": [
                (f.get("name") if isinstance(f, dict) else f)
                for f in (t.get("editable_fields") or [])
            ],
        })

    def _skeleton(sch: Dict[str, Any]) -> Dict[str, Any]:
        props = (sch or {}).get("properties")
        if not isinstance(props, dict):
            return {}
        return {k: (v.get("type") if isinstance(v, dict) else "value") for k, v in props.items()}

    # What the READ-BEFORE-WRITE guard will enforce before staging a write — so a
    # headless integrator knows the agent itself must review these, not just that
    # the caller sends an id. The anchor record (the id input) plus every
    # bound-and-required media tool must be read, or /run fails loud.
    _entry_action = _actions[0] if _actions else {}
    _entry_schema = (_entry_action.get("input_schema") or {}) if isinstance(_entry_action, dict) else {}
    _entry_props = _entry_schema.get("properties") or {}
    _entry_req_ids = [
        r for r in (_entry_schema.get("required") or [])
        if (_entry_props.get(r) or {}).get("type") in (None, "string")
    ]
    _ar = _entry_action.get("anchor_read") if isinstance(_entry_action, dict) else None
    required_evidence: List[Dict[str, Any]] = []
    if _ar or _entry_req_ids:
        required_evidence.append({
            "kind": "record",
            "key_field": (_ar.get("key_field") if isinstance(_ar, dict)
                          else (_entry_req_ids[0] if _entry_req_ids else None)),
            "prefetched": bool(_ar),
            "note": "the anchor record this id names must be read before any write is staged",
        })
    for t in (aspec.get("tools_v2") or []):
        if not (isinstance(t, dict) and t.get("kind") in ("image_analyze", "doc_extract")):
            continue
        if not t.get("data_source_id") or t.get("required") is False:
            continue  # unbound or opted-out → not enforced
        required_evidence.append({
            "kind": t.get("kind"),
            "tool": t.get("name"),
            "note": "this bound media must be analysed for the record under review before any write is staged",
        })
    # Policy-required data lookups (a bureau / KYC / sanctions check marked
    # required:true on a bound mcp tool). Surfaced so a headless integrator knows
    # the app REQUIRES this lookup to run before a write — the CIBIL-style gate.
    for t in (aspec.get("tools_v2") or []):
        if not (isinstance(t, dict) and t.get("kind") == "mcp"):
            continue
        if t.get("required") is not True or not t.get("dataset_id"):
            continue
        required_evidence.append({
            "kind": "lookup",
            "tool": t.get("name"),
            "dataset_id": t.get("dataset_id"),
            "note": "this policy-required lookup (e.g. a bureau/KYC/sanctions check) "
                    "must be run for the record before any write is staged",
        })

    return {
        "slug": slug,
        "agent": aspec.get("name"),
        "headless": bool(spec.get("headless")),
        # Per-item review gate: "hard" (must review each item finding before Apply),
        # "soft" (warn), or "none". A headless consumer must disposition item_findings
        # via /items/{id}/feedback accordingly before /approve. Case (fraud) items
        # are evidence-only and never gate.
        "item_review_gate": spec.get("item_review_gate") or "hard",
        "run_actions": run_actions,      # values for the /run "action" field
        "write_actions": write_actions,  # tables + payload schema + editable fields
        "endpoints": {
            "recommend": {"method": "POST", "path": f"/apps/{slug}/run"},
            "approve": {"method": "POST", "path": f"/apps/{slug}/run/{{correlation_id}}/approve"},
            "item_feedback": {
                "method": "POST", "path": f"/apps/{slug}/items/{{item_id}}/feedback",
                "note": "disposition one item finding; body {modality, task_type, "
                        "decision: accept|reject, reason?, subject?} — reason is "
                        "REQUIRED on reject (max 500 chars; it trains the rubric). "
                        "With item_review_gate=hard, every non-case finding must be "
                        "dispositioned before /approve (server-enforced, 409).",
            },
            "direct_assign": {
                "method": "POST", "path": f"/apps/{slug}/tool/{{tool_name}}",
                "note": "make a decision WITHOUT the AI (human_direct); body "
                        "{arguments:{...}}; omit panel_id for headless apps",
            },
            "token": {"method": "POST", "path": f"/apps/{slug}/runtime/token"},
        },
        "request_schema": request_schema,
        "required_evidence": required_evidence,
        "response_shape": {
            "correlation_id": "string",
            "status": "completed | pending_approval | failed",
            "decision": "string | null",
            "reasoning": "string | null",
            "planned_writes": "[{dataset_id, action_id, source_id, payload, editable_fields?}]",
            "plan_hash": "string | null — hash of planned_writes as displayed; echo it "
                         "back as approve_request.expected_plan_hash so a plan that "
                         "changed since display is rejected (409) instead of committed",
            "item_findings": "[{item_id, item_type, modality: image|document|api|case, "
                             "subject?, fields, recommendation, confidence, rationale, "
                             "artifact_flags?}] — per-item review cards (media, bureau/KYC "
                             "api checks, fraud case); subject anchors rubric learning; "
                             "artifact_flags carries duplicate/reuse fraud evidence; "
                             "disposition each via the item_feedback endpoint",
            "citations": "[...]",
            "cited_precedents": "[{decision_id, relation: similar|differs, note}] — the PAST CASES the model relied on or deviated from (its receipts); cross-check against references.few_shot_samples",
            "scorecard": (
                "null unless this app declares a factor_set. Then: {mode: "
                "composite|checklist, terminology{panel,row,band,composite}, "
                "gates[{gate_id,label,status: pass|fail|flag,rationale}], gated, "
                "rows[{factor_id,label,scope,score,weight,band,rationale,"
                "citations,clauses_fired,confidence,unscored}], total, max_total, "
                "percent, grade, unscored_factor_ids}. The MODEL scores one "
                "factor at a time; the totals are computed in code from declared "
                "weights, so the same findings always yield the same composite. "
                "mode=checklist carries rows and NO total — judged criteria that "
                "must not be summed. gated=true means a hard policy gate failed "
                "or could not be evaluated, and total/percent/grade are "
                "suppressed: read the gate, not the score. unscored_factor_ids "
                "non-empty means the composite covers less than the full rubric."
            ),
        },
        "approve_request": {
            "decision": "approve | reject | cancel",
            "overrides": "optional [{field: value}] aligned to planned_writes[i]",
            "expected_plan_hash": "optional string — the plan_hash you displayed; "
                                  "mismatch with the staged plan → 409 (display==commit guard)",
            "decision_reason": "optional string — recorded on the decision ledger",
            "note": "optional string",
        },
        "example": {
            "recommend_request": {
                "action": (run_actions[0] if run_actions else "<action>"),
                "inputs": _skeleton(request_schema),
            },
            "approve_request": {"decision": "approve", "overrides": [], "note": ""},
        },
        "auth": (
            "Forward the end-user's `Authorization: Bearer <jwt>` on every call so "
            "per-user authz + audit apply; or mint a scoped runtime token via the "
            "token endpoint."
        ),
        "governance": (
            "Commit ONLY through these endpoints — /run then /approve (AI-recommended: "
            "approve / override / reject / cancel) OR direct_assign (a no-AI human "
            "decision). Both run the governed write path: policy gate, schema-validated "
            "idempotent SoR write, audit, DecisionRecord, outcome loop. The UI must "
            "NEVER write the system of record directly outside these — that is the "
            "governance + self-learning boundary; the UI is presentation only. "
            "Read-before-write: a /run will FAIL rather than stage a decision unless "
            "the agent actually read the evidence in `required_evidence` (the anchor "
            "record and any bound-required image/doc) for the record under review."
        ),
    }


@app.get("/apps/{slug}/self-learning", response_model=SelfLearningResponse)
async def get_self_learning(slug: str, request: Request) -> SelfLearningResponse:
    """Current per-app self-learning state: outcome tracking + auto-run."""
    await _bind_app_env(slug)
    app_doc = await get_apps_col().find_one({"slug": slug})
    if not app_doc or not _can_render_app(app_doc, request):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "app not found")
    agent_doc = await get_agents_col().find_one(
        {"agent_id": app_doc.get("agent_id"), "tenant_id": app_doc.get("tenant_id")},
        {"agent_spec.outcome_poll": 1},
    )
    op = (((agent_doc or {}).get("agent_spec") or {}).get("outcome_poll")) or {}
    return SelfLearningResponse(
        slug=slug, enabled=bool(op.get("enabled")), auto_refresh=bool(op.get("auto_refresh")),
    )


@app.post("/apps/{slug}/self-learning", response_model=SelfLearningResponse)
async def set_self_learning(
    slug: str, payload: SelfLearningRequest, request: Request,
) -> SelfLearningResponse:
    """Turn per-app auto-learning ON/OFF. ON → validated outcomes auto-fold into
    memory (delta) + the periodic rebuild runs. OFF (default) → the app still
    TRACKS outcomes, but memory updates only on a MANUAL refresh. Requires edit
    rights; the app must be grounded + have outcome tracking configured."""
    await _bind_app_env(slug)
    claims = _claims(request)
    if not claims["user_id"]:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "auth required")
    apps = get_apps_col()
    agents = get_agents_col()
    app_doc = await apps.find_one({"slug": slug})
    if not app_doc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "app not found")
    if not _can_edit_app(
        app_doc, claims["user_id"], app_doc.get("tenant_id"),
        user_org_id=claims["org_id"], user_dept_ids=claims["dept_ids"],
        user_roles=claims["roles"], sa_admin_of=claims["service_account_admin_of"],
    ):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "edit rights required")
    agent_id = app_doc.get("agent_id")
    agent_doc = await agents.find_one(
        {"agent_id": agent_id, "tenant_id": app_doc.get("tenant_id")}
    )
    aspec = (agent_doc or {}).get("agent_spec") or {}
    if not aspec.get("grounding") or not aspec.get("outcome_poll"):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {"code": "not_self_learning_capable",
             "message": "app is not grounded / has no outcome tracking configured"},
        )
    auto = bool(payload.auto_refresh)
    op = aspec.get("outcome_poll") or {}
    # Outcome tracking: explicit toggle wins; else keep current (default ON).
    track = payload.enabled if payload.enabled is not None else bool(op.get("enabled", True))
    if not track:
        auto = False          # nothing to auto-learn from if not tracking
    if auto:
        track = True          # auto-run needs tracking on
    sets: Dict[str, Any] = {
        "agent_spec.outcome_poll.enabled": track,
        "agent_spec.outcome_poll.auto_refresh": auto,
        "updated_at": datetime.now(timezone.utc),
    }
    await agents.update_one(
        {"agent_id": agent_id, "tenant_id": app_doc.get("tenant_id")}, {"$set": sets},
    )
    logger.info(
        "[loop] self-learning enabled=%s auto_refresh=%s for %s by %s",
        track, auto, slug, claims["user_id"],
    )
    return SelfLearningResponse(slug=slug, enabled=track, auto_refresh=auto)


@app.post("/apps/{slug}/audience", response_model=SetAudienceResponse)
async def set_audience(
    slug: str, payload: SetAudienceRequest, request: Request,
) -> SetAudienceResponse:
    """Change who can SEE/RUN this app. Edit access is unchanged.

    The caller must satisfy BOTH the current AND target audience levels
    (the higher-of rule):
      * widening owner→dept needs dept_admin
      * narrowing org→dept needs org_admin (the *current* level's role)
      * same-level moves between teams need admin of both teams
    """
    claims = _claims(request)
    if not claims["user_id"]:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "auth required")

    target_audience = (payload.audience or "").strip()
    try:
        parse_audience(target_audience)
    except ValueError as exc:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            {"code": "invalid_audience", "message": str(exc)},
        )

    apps = get_apps_col()
    app_doc = await apps.find_one({"slug": slug})
    if not app_doc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "app not found")
    if not _can_edit_app(
        app_doc, claims["user_id"], app_doc.get("tenant_id"),
        user_org_id=claims["org_id"], user_dept_ids=claims["dept_ids"],
        user_roles=claims["roles"], sa_admin_of=claims["service_account_admin_of"],
    ):
        # Audience change requires edit rights AND audience permission.
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "owner-SA admin or admin role required",
        )

    spec = app_doc.get("app_spec") or {}
    current_audience = spec.get("audience") or "owner"
    if current_audience == target_audience:
        return SetAudienceResponse(
            slug=slug,
            audience=current_audience,
            previous_audience=current_audience,
        )

    ok, reason = _can_change_audience(
        claims, current_audience, target_audience, app_doc,
    )
    if not ok:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            {
                "code": "audience_change_not_allowed",
                "from": current_audience,
                "to": target_audience,
                "reason": reason,
            },
        )

    now = datetime.now(timezone.utc)
    await apps.update_one(
        {"slug": slug},
        {
            "$set": {
                "app_spec.audience": target_audience,
                "updated_at": now,
            },
            "$push": {
                "lifecycle_audit": _lifecycle_audit_entry(
                    claims, "audience_changed",
                    **{"from": current_audience, "to": target_audience,
                       "reason": payload.reason or ""}
                ),
            },
        },
    )
    return SetAudienceResponse(
        slug=slug,
        audience=target_audience,
        previous_audience=current_audience,
    )


class AppArchive(BaseModel):
    reason: Optional[str] = None


@app.post("/apps/{slug}/lifecycle/archive")
async def lifecycle_archive_app(slug: str, payload: AppArchive, request: Request):
    """Soft-archive: status → ARCHIVED. Audience preserved on restore."""
    claims = _claims(request)
    # Bind test↔prod by store first, else a test-store app 404s (see _bind_app_env).
    await _bind_app_env(slug)
    apps = get_apps_col()
    app_doc = await apps.find_one({"slug": slug})
    if not app_doc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "app not found")
    if not _is_lifecycle_admin(claims, app_doc):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "owner or admin required")

    if app_doc.get("status") == AppStatus.ARCHIVED.value:
        return {"ok": True, "slug": slug, "status": "archived", "already_archived": True}

    now = datetime.now(timezone.utc)
    await apps.update_one(
        {"slug": slug},
        {
            "$set": {
                "app_spec.archived_at": now,
                "app_spec.archived_by": claims["email"] or claims["user_id"],
                "status": AppStatus.ARCHIVED.value,
                "updated_at": now,
            },
            "$push": {
                "lifecycle_audit": _lifecycle_audit_entry(
                    claims, "archive", reason=payload.reason or ""
                ),
            },
        },
    )
    return {"ok": True, "slug": slug, "status": "archived"}


@app.post("/apps/{slug}/lifecycle/restore")
async def lifecycle_restore_app(slug: str, request: Request):
    """Restore an archived app. Requires org_admin or super_admin."""
    claims = _claims(request)
    roles = set(claims["roles"])
    if not roles & {"org_admin", "super_admin"}:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "org_admin or super_admin required to restore")

    # Bind test↔prod by store first, else a test-store app 404s (see _bind_app_env).
    await _bind_app_env(slug)
    apps = get_apps_col()
    app_doc = await apps.find_one({"slug": slug})
    if not app_doc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "app not found")
    if "super_admin" not in roles and app_doc.get("org_id") and app_doc.get("org_id") != claims["org_id"]:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "app outside your org")

    if app_doc.get("status") != AppStatus.ARCHIVED.value:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "app is not archived")

    now = datetime.now(timezone.utc)
    await apps.update_one(
        {"slug": slug},
        {
            "$set": {
                "status": AppStatus.PUBLISHED.value,
                "app_spec.restored_at": now,
                "app_spec.restored_by": claims["email"] or claims["user_id"],
                "updated_at": now,
            },
            "$unset": {
                "app_spec.archived_at": "",
                "app_spec.archived_by": "",
            },
            "$push": {
                "lifecycle_audit": _lifecycle_audit_entry(claims, "restore"),
            },
        },
    )
    return {"ok": True, "slug": slug, "status": "published"}


class AppInheritancePolicy(BaseModel):
    inheritance_policy: str  # archive | transfer_to_sa | transfer_to_org | delete_after_grace
    inheritance_target: Optional[str] = None
    inheritance_grace_days: Optional[int] = None


@app.post("/apps/{slug}/inheritance-policy")
async def set_app_inheritance_policy(
    slug: str, payload: AppInheritancePolicy, request: Request
):
    """Configure what happens to this app when the owner-user is deactivated."""
    claims = _claims(request)
    allowed_policies = {
        "archive", "transfer_to_sa", "transfer_to_dept",
        "transfer_to_org", "delete_after_grace",
    }
    if payload.inheritance_policy not in allowed_policies:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"invalid policy (allowed: {sorted(allowed_policies)})",
        )

    apps = get_apps_col()
    app_doc = await apps.find_one({"slug": slug})
    if not app_doc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "app not found")
    if not _is_lifecycle_admin(claims, app_doc):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "owner or admin required")

    target = (payload.inheritance_target or "").strip() or None
    if payload.inheritance_policy in (
        "transfer_to_sa", "transfer_to_dept", "transfer_to_org",
    ) and not target:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"inheritance_target required when policy={payload.inheritance_policy}",
        )
    grace = payload.inheritance_grace_days
    if grace is not None and (grace < 0 or grace > 365):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "grace must be 0..365")

    update: Dict[str, Any] = {
        "app_spec.inheritance_policy": payload.inheritance_policy,
        "app_spec.inheritance_target": target,
        "updated_at": datetime.now(timezone.utc),
    }
    if grace is not None:
        update["app_spec.inheritance_grace_days"] = grace

    await apps.update_one(
        {"slug": slug},
        {
            "$set": update,
            "$push": {
                "lifecycle_audit": _lifecycle_audit_entry(
                    claims, "inheritance_policy_set",
                    policy=payload.inheritance_policy,
                    target=target,
                    grace_days=grace,
                ),
            },
        },
    )
    spec = app_doc.get("app_spec") or {}
    return {
        "ok": True,
        "slug": slug,
        "inheritance_policy": payload.inheritance_policy,
        "inheritance_target": target,
        "inheritance_grace_days": grace if grace is not None else spec.get("inheritance_grace_days", 30),
    }


# ---------------------------------------------------------------------------
# Capabilities — what the builder may promise the BA
# ---------------------------------------------------------------------------


@app.get("/capabilities", response_model=CapabilitiesResponse)
async def capabilities(
    request: Request,
    question: Optional[str] = None,
    settings: Settings = Depends(get_settings),
) -> CapabilitiesResponse:
    """Return the platform feature flags + limits + live tool list.

    Loaded by the ``citra-app-discovery`` skill at the start of an
    interview. Auth required so the tool list is scoped to the caller's
    org/dept/roles (discovery-service applies the scoping).

    Optional ``question`` reranks the RBAC-visible tool list by
    relevance to the BA's free-text goal. The response is always capped
    at 10 tools.
    """
    # Touch user_id to enforce auth (middleware already ran; this raises
    # 401 if the principal is missing).
    get_secure_user_id(request)
    auth_header = request.headers.get("authorization") or request.headers.get(
        "Authorization"
    )
    return await get_capabilities(
        settings,
        auth_header=auth_header,
        question=question,
    )


# ---------------------------------------------------------------------------
# Builder testing \u2014 probe + sample (Layers B and C of the test plan)
# ---------------------------------------------------------------------------


class BuilderProbeRequest(BaseModel):
    """Probe one declared agent tool against its live MCP endpoint.

    Used by the builder's self-test step (Layer B) to confirm \u2014 without
    waiting for a real officer to click \u2014 that every declared tool
    actually responds, the catalogue's schema matches what the agent
    would send, and the auth / network path holds.

    For read tools (``mcp`` / ``rag``) the probe issues a minimal query
    (``limit=1`` / ``top_k=1``) \u2014 fully read-only.
    For write tools (``mcp_action``) the probe issues an /execute_action
    with ``dry_run=true`` and a synthetic payload built from
    ``input_schema`` \u2014 never commits to source. The MCP runs full
    preflight (write-authz + schema validation) and returns a structured
    success/failure that the builder surfaces to the BA.

    **Write-validation (Phase 4):** set ``execute=true`` (mcp_action only) to
    run the write for REAL (``dry_run=false``) so the BA can validate the
    write's actual EFFECT against test data, not just its shape. HARD-GATED to
    the test environment \u2014 the handler refuses ``execute`` unless the request
    resolved to ``test`` (a real write can NEVER run against prod from the
    builder). Cleanup of the committed test rows is by IT reseed/reset.
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["mcp", "rag", "mcp_action"]
    source_id: str = Field(min_length=1)
    dataset_id: Optional[str] = None
    action_id: Optional[str] = None
    # Optional payload override; when absent for mcp_action, the handler
    # synthesises a minimal payload from the catalogued input_schema.
    payload: Optional[Dict[str, Any]] = None
    query: Optional[str] = None
    # Phase 4 write-validation: commit the write for real (dry_run=false).
    # mcp_action only; refused unless the request resolves to the test env.
    execute: bool = False


class BuilderProbeResponse(BaseModel):
    ok: bool
    kind: str
    source_id: str
    dataset_id: Optional[str] = None
    action_id: Optional[str] = None
    detail: Optional[str] = None
    elapsed_ms: Optional[int] = None
    sample_result: Optional[Dict[str, Any]] = None
    # True when the probe actually committed a write (execute=true against the
    # test env); false for every dry-run / read probe.
    committed: bool = False


class BuilderValidateRequest(BaseModel):
    app_spec: Dict[str, Any]
    agent_spec: Optional[Dict[str, Any]] = None


@app.post("/builder/validate")
async def builder_validate(
    payload: BuilderValidateRequest,
    request: Request,
) -> Dict[str, Any]:
    """Validate an AppSpec (+ optional AgentSpec) against BOTH JSON Schema and
    the Pydantic model — the identical two-layer check ``/publish`` runs — but
    WITHOUT persisting anything.

    The build pod cannot ``import models``/``validators`` (neither module is
    shipped into the sandbox image), so this is the builder's only way to catch
    — BEFORE the publish round-trip — both (a) the schema + Pydantic cross-ref
    errors (dangling ``navigate.page``, duplicate ``is_row_click``, sub-agent
    tool-subset), AND (b) the **spec-shape publish rules** that ``/publish``
    runs (W-06 direct-write confirm, H-04
    chat-writes, mcp_action input_schema, update identifier, editable fields,
    dashboard narrator (D-02), grounding contract, admin-only, internal
    audience). A green response here means ``/publish`` will not reject on
    spec-shape grounds — which is what kills the build -> publish -> 422 loop.

    NOT covered here (no catalogue round-trip): the catalogue-resolution rules
    (unknown dataset / column / action). Those stay at ``static_checks.py`` +
    ``/builder/probe`` + ``/publish``, which have the catalogue.
    """
    user_id = get_secure_user_id(request)
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="authentication required",
        )
    try:
        app_model, _ = validate_app_spec(payload.app_spec)
        agent_model = None
        if payload.agent_spec is not None:
            agent_model, _ = validate_agent_spec(payload.agent_spec)
    except (JsonSchemaValidationError, PydanticValidationError) as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Spec validation failed: {e}",
        )

    # Layer B — the same pure-Python spec-shape rules /publish enforces.
    rule_errors: List[Dict[str, Any]] = []

    def _collect(rule_id: str, errs: Any) -> None:
        for e in (errs or []):
            rule_errors.append(
                {"rule": rule_id, **(e if isinstance(e, dict) else {"detail": str(e)})}
            )

    if agent_model is not None:
        _collect("H-04", reject_allow_writes_in_chat(agent_model))
        _collect("T-03", validate_no_admin_actions(agent_model, catalogue_index=None))
        _collect("G-01", validate_grounding_contract(agent_model))
        _collect("update_identifier", validate_update_has_identifier(agent_model))
        _collect("mcp_action_input_schema", validate_mcp_action_has_input_schema(agent_model))
        _collect("W-01", validate_no_delete_verbs(app_model, agent_model))
        _collect("D-02", validate_dashboard_page_has_narrator(app_model, agent_model))
        _collect("editable_fields", validate_editable_fields(app_model, agent_model))
        _collect("W-06", validate_direct_write_buttons_confirm(app_model, agent_model))
        _collect("F-01", validate_no_media_columns(app_model))
        _collect("FS-06", validate_factor_checks_can_score(app_model, agent_model))
        _collect("M-01", validate_item_tools_declare_task_type(agent_model))
        # Pure, spec-only rules that /publish also enforces. They ran ONLY at
        # publish/spec-edit, so the builder could be told passed:true and then
        # be rejected — the one thing this endpoint exists to prevent. The
        # remaining publish-only rules (data bindings, source refs, panel
        # columns, tool sources) are async and resolve against the live
        # catalogue, so they genuinely cannot run on a stateless spec.
        _collect("lookup_bound", validate_required_lookup_is_bound(agent_model))
    _collect("S-01", validate_internal_audience(app_model))
    _collect("V-CHART-01", validate_chart_axes(app_model))
    _collect("I-01", validate_icons(app_model))
    _collect("CS-01", validate_case_signature(app_model))
    _collect("CS-02", validate_case_signature_projection(app_model))
    _collect("CS-04", validate_case_signature_confirmed(app_model))
    _collect("FS-01", validate_factor_set(app_model))
    _collect("FS-05", validate_rubric_finding_matches_declaration(app_model))

    if rule_errors:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "spec_rules_failed",
                "message": "Spec-shape publish rules failed — the same checks /publish runs. Fix and re-validate before publishing.",
                "errors": rule_errors,
            },
        )
    return {"ok": True}


@app.post("/builder/probe", response_model=BuilderProbeResponse)
async def builder_probe(
    payload: BuilderProbeRequest,
    request: Request,
    settings: Settings = Depends(get_settings),
) -> BuilderProbeResponse:
    """Probe a tool the builder wants to declare in the AgentSpec.

    Always read-only or ``dry_run=true``. Reuses the same proxy clients
    the runtime uses, so a green probe is a strong (not airtight)
    signal the tool will actually fire at runtime under the same
    user JWT shape.
    """
    user_id = get_secure_user_id(request)
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="authentication required",
        )
    # A build-time probe runs against the test MCPs (see _bind_build_env).
    _bind_build_env()
    auth_header = request.headers.get("authorization") or request.headers.get(
        "Authorization"
    )
    user_jwt = (auth_header or "").removeprefix("Bearer ").strip() or None

    import time as _time
    started = _time.perf_counter()

    if payload.kind == "mcp_action":
        if not (payload.dataset_id and payload.action_id):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="dataset_id and action_id are required for mcp_action",
            )
        from proxy_clients import call_dept_mcp_execute_action, ProxyError
        synth_payload: Dict[str, Any] = payload.payload or {}
        # Phase 4: execute=true commits the write for real (dry_run=false) so
        # the BA validates the write's EFFECT, not just its shape. A real write
        # may run ONLY against the test environment \u2014 refuse otherwise so the
        # builder can never commit to a prod source. Fail-closed: no test env
        # configured \u2192 current_env() is "prod" \u2192 refused.
        commit = bool(payload.execute)
        if commit and current_env() != "test":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "write-validation (execute=true) runs only in the test "
                    "environment; configure TEST_DISCOVERY_SERVICE_URL. Refusing "
                    "to commit a real write against prod from the builder."
                ),
            )
        try:
            resp = await call_dept_mcp_execute_action(
                settings=settings,
                user_jwt=user_jwt,
                source_id=payload.source_id,
                dataset_id=payload.dataset_id,
                action_id=payload.action_id,
                payload=synth_payload,
                dry_run=not commit,  # dry_run=True (Layer B) unless validating.
            )
            ok = bool(isinstance(resp, dict) and resp.get("ok") and not resp.get("error"))
            detail = None if ok else str(resp.get("error") or resp.get("detail") or resp)[:300]
            sample = resp if isinstance(resp, dict) else None
        except ProxyError as exc:
            ok, detail, sample = False, f"{exc.code}: {exc}", None
        elapsed = int((_time.perf_counter() - started) * 1000)
        return BuilderProbeResponse(
            ok=ok, kind=payload.kind, source_id=payload.source_id,
            dataset_id=payload.dataset_id, action_id=payload.action_id,
            detail=detail, elapsed_ms=elapsed, sample_result=sample,
            committed=bool(commit and ok),
        )

    if payload.kind == "mcp":
        from proxy_clients import call_dept_mcp_query, call_dept_mcp_sample, ProxyError
        try:
            if payload.dataset_id:
                # Probe a READ dataset via the purpose-built sample plane
                # (GET /datasets/{id}/sample) — NO NL query needed, so an
                # NL→SQL MCP can't 422 on a query-less probe (the old path
                # sent {"limit":1} to /query, which requires "query").
                resp = await call_dept_mcp_sample(
                    settings=settings, user_jwt=user_jwt,
                    source_id=payload.source_id, dataset_id=payload.dataset_id, n=1,
                )
            elif payload.query:
                # No specific dataset — probe an explicit NL query.
                resp = await call_dept_mcp_query(
                    settings=settings, user_jwt=user_jwt,
                    source_id=payload.source_id,
                    body={"query": payload.query, "limit": 1},
                )
            else:
                # Fail loud with an actionable message (not a cryptic MCP 422).
                elapsed = int((_time.perf_counter() - started) * 1000)
                return BuilderProbeResponse(
                    ok=False, kind=payload.kind, source_id=payload.source_id,
                    dataset_id=None, action_id=None,
                    detail="mcp probe requires a dataset_id (to sample) or a query (to test)",
                    elapsed_ms=elapsed, sample_result=None,
                )
            ok = isinstance(resp, dict) and not resp.get("error")
            detail = None if ok else str(resp.get("error") or resp.get("detail") or resp)[:300]
            sample = resp if isinstance(resp, dict) else None
        except ProxyError as exc:
            ok, detail, sample = False, f"{exc.code}: {exc}", None
        elapsed = int((_time.perf_counter() - started) * 1000)
        return BuilderProbeResponse(
            ok=ok, kind=payload.kind, source_id=payload.source_id,
            dataset_id=payload.dataset_id, action_id=None,
            detail=detail, elapsed_ms=elapsed, sample_result=sample,
        )

    if payload.kind == "rag":
        # RAG short-circuit: a `rag` binding is a semantic corpus, answered by the
        # Citra-Service platform reader, NEVER the dept-MCP (which serves no RAG) —
        # the same route the runtime uses, so the probe mirrors real firing.
        from proxy_clients import call_citra_semantic_search, ProxyError
        try:
            resp = await call_citra_semantic_search(
                settings=settings,
                user_jwt=user_jwt,
                source_id=payload.source_id,
                query=payload.query or "ping",
                top_k=1,
                org_id=get_org_id(request),
            )
            ok = isinstance(resp, dict) and not resp.get("error")
            detail = None if ok else str(resp.get("error") or resp.get("detail") or resp)[:300]
            sample = resp if isinstance(resp, dict) else None
        except ProxyError as exc:
            ok, detail, sample = False, f"{exc.code}: {exc}", None
        elapsed = int((_time.perf_counter() - started) * 1000)
        return BuilderProbeResponse(
            ok=ok, kind=payload.kind, source_id=payload.source_id,
            dataset_id=payload.dataset_id, action_id=None,
            detail=detail, elapsed_ms=elapsed, sample_result=sample,
        )

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=f"unsupported probe kind {payload.kind!r}",
    )


class BuilderSampleRequest(BaseModel):
    """Pull a small sample of rows from a catalogued dataset.

    Used by Layer C of the testing plan \u2014 augments the builder's
    synthetic self-test cases with real prod-shape inputs so the agent
    is exercised against actual column distributions, encodings, and
    null patterns. The MCP applies its existing visibility + PII
    redaction at the source \u2014 the sample handler does not bypass any
    of those checks.
    """

    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(min_length=1)
    dataset_id: str = Field(min_length=1)
    limit: int = Field(default=3, ge=1, le=20)
    where: Optional[str] = Field(default=None, max_length=400)


class BuilderSampleResponse(BaseModel):
    rows: List[Dict[str, Any]]
    truncated: bool
    detail: Optional[str] = None


@app.post("/builder/sample", response_model=BuilderSampleResponse)
async def builder_sample(
    payload: BuilderSampleRequest,
    request: Request,
    settings: Settings = Depends(get_settings),
) -> BuilderSampleResponse:
    """Fetch a small sample of rows for builder self-test feeding.

    Returns redacted rows \u2014 the dept-MCP enforces PII redaction at the
    source. Hard-capped at 20 rows so a buggy builder cannot use this
    as a data-exfil endpoint. ``where`` is forwarded verbatim \u2014 the
    MCP's SELECT-only AST enforcement is what stops a malicious WHERE
    from doing more than filtering.
    """
    user_id = get_secure_user_id(request)
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="authentication required",
        )
    # A build-time sample reads from the test MCPs (see _bind_build_env).
    _bind_build_env()
    auth_header = request.headers.get("authorization") or request.headers.get(
        "Authorization"
    )
    user_jwt = (auth_header or "").removeprefix("Bearer ").strip() or None

    from proxy_clients import call_dept_mcp_sample, ProxyError
    # Use the dept-MCP's purpose-built sample plane (GET /datasets/{id}/sample) —
    # it needs NO NL query, so an NL→SQL MCP can't 422 on a query-less sample
    # (the old path sent the planner an NL phrasing and got empty/none rows).
    # `where` is not part of the sample surface — sampling returns a
    # representative slice; bind a query tool for a filtered view.
    try:
        resp = await call_dept_mcp_sample(
            settings=settings,
            user_jwt=user_jwt,
            source_id=payload.source_id,
            dataset_id=payload.dataset_id,
            n=payload.limit,
        )
    except ProxyError as exc:
        return BuilderSampleResponse(
            rows=[], truncated=False, detail=f"{exc.code}: {exc}",
        )
    rows = resp.get("rows") if isinstance(resp, dict) else None
    if not isinstance(rows, list):
        return BuilderSampleResponse(
            rows=[], truncated=False,
            detail="MCP did not return a 'rows' list",
        )
    truncated = isinstance(resp, dict) and bool(resp.get("truncated"))
    return BuilderSampleResponse(
        rows=rows[: payload.limit],
        truncated=truncated,
        detail=None,
    )


# ---------------------------------------------------------------------------
# Catalogue \u2014 datasets the BA may bind actions to
# ---------------------------------------------------------------------------


@app.get("/builder/catalogue")
async def builder_catalogue(
    request: Request,
    source_id: Optional[str] = None,
    has_pii: Optional[bool] = None,
    full: bool = False,
    question: Optional[str] = None,
    settings: Settings = Depends(get_settings),
):
    """Return the dataset catalogue for the caller's tenant.

    Consumed by the discovery skill at interview start so the builder LLM
    can only emit AppSpecs that bind to real datasets / write_actions.

    Query params:
      source_id   filter to one dept-mcp source
      has_pii     filter to datasets containing PII (or not)
      full        when true, return the full catalogue rows; when false
                  (default), return the trimmed projection sized for the
                  builder system prompt.
      question     optional free-text BA goal; when provided, reranks the
                  RBAC-visible catalogue by relevance. The response is
                  always capped at 50 entries.
    """
    get_secure_user_id(request)
    auth_header = request.headers.get("authorization") or request.headers.get(
        "Authorization"
    )
    tenant_id = get_tenant_id(request) or ""
    from discovery_cache import DiscoveryError

    # Two-stage retrieval: RECALL (query-driven ANN via /catalogue/search) then
    # PRECISION (cross-encoder rerank). The recall stage scopes to the relevant
    # datasets server-side, so a large org never fetches/ranks the whole
    # catalogue. Only used when there's a real goal to search on; otherwise (or
    # if /catalogue/search is unavailable) fall back to the tenant list.
    # Query-driven recall when there's a real goal; the plain tenant list
    # otherwise. NO silent fallback between them: data-discovery already does
    # the right thing per mode (vectoring disabled → bounded list; enabled +
    # unpopulated index → 503; genuinely empty → []), so any DiscoveryError here
    # is a real failure and must surface — not be masked by quietly switching
    # to the other path (RULE: fail loud).
    q = (question or "").strip()
    recall_used = len(q) >= 8
    try:
        if recall_used:
            entries = await fetch_catalogue_search(
                settings=settings,
                auth_header=auth_header,
                tenant_id=tenant_id,
                question=q,
                source_id=source_id,
                top_k=150,
            )
        else:
            entries = await fetch_catalogue_list(
                settings=settings,
                auth_header=auth_header,
                tenant_id=tenant_id,
                source_id=source_id,
                has_pii=has_pii,
            )
    except DiscoveryError as e:
        raise HTTPException(status_code=e.status, detail=f"catalogue unavailable: {e}")
    total = len(entries)
    # Recall already narrowed by relevance — only flag scope on the list path.
    needs_scope = (not recall_used) and needs_scope_narrowing(entries)
    if needs_scope:
        logger.warning(
            "[BUILDER] large catalogue palette for tenant=%s: %d datasets "
            "(reranked top-50 may miss the right table). Discovery skill should "
            "ask the BA for a target area; query-driven catalogue search is the "
            "scale fix — docs/catalogue-retrieval-plan.md.",
            tenant_id, total,
        )
    ranked = await rerank_candidates(
        settings=settings,
        question=question,
        candidates=entries,
        text_fn=_catalogue_rerank_text,
        top_k=50,
    )
    # Stamp an explicit `ref` on every entry (== dataset_id, already
    # source-qualified) so the builder LLM copies it verbatim into
    # data_source.ref instead of reconstructing it from dataset_id + source_id
    # (which produced the "field_operations/field_operations.complaints" bug).
    for e in ranked:
        if isinstance(e, dict) and e.get("dataset_id"):
            e["ref"] = e["dataset_id"]
    if full:
        return {"entries": ranked, "total": total, "needs_scope": needs_scope}
    return {
        "entries": trim_for_prompt(ranked, max_entries=50),
        "total": total,
        "needs_scope": needs_scope,
    }


# ---------------------------------------------------------------------------
# Gate A — build-time history-quality evaluation
# ---------------------------------------------------------------------------


@app.get("/builder/history-quality")
async def builder_history_quality(
    request: Request,
    dataset_id: str,
    source_id: Optional[str] = None,
    decision_column: Optional[str] = None,
    terminal_states: Optional[str] = None,
    settings: Settings = Depends(get_settings),
):
    """Project the decision-quality signals for ONE dataset so the builder can
    decide whether the history is good enough to ground on (Gate A).

    No new transport: reads the full catalogue entry the crawler already
    produced (column ``distinct_values`` = the decision classes, ``range`` =
    the date span, ``nullable``, plus ``row_count_approx``) and runs the
    deterministic hard gates. The builder LLM applies judgment on top of the
    returned ``signals``/``notes`` and finalizes the ``suggested_contract``.

    Returns ``hard_gate_pass=false`` with reasons when the data is structurally
    unfit — in which case the builder should NOT author the Refresh-from-History
    workflow and should tell the BA why.
    """
    get_secure_user_id(request)
    auth_header = request.headers.get("authorization") or request.headers.get(
        "Authorization"
    )
    tenant_id = get_tenant_id(request) or ""

    entry = await fetch_catalogue_entry(
        settings=settings,
        auth_header=auth_header,
        tenant_id=tenant_id,
        dataset_id=dataset_id,
        source_id=source_id,
    )
    if not entry:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="dataset not found in catalogue")

    dh = dict(entry.get("decision_history") or {})
    notes: List[str] = []
    hard_failures: List[str] = []

    # A dataset need NOT be explicitly flagged `is_decision_record`. The builder
    # may judge — FROM THE REAL SAMPLED ROWS — that a dataset holds completed
    # decisions, and pass the outcome column it OBSERVED (?decision_column=,
    # ?terminal_states=). We then vet that judgment against the crawler's real
    # signals below (the column must EXIST, be low-cardinality with >=2 decided
    # classes, enough rows). NO INVENTION: every gate is checked against actual
    # catalogue data, and if no decision column resolves we FAIL LOUD — it is
    # not a decision dataset and must not be grounded on. The explicit flag,
    # when set, is a confidence hint, not a hard requirement.
    flag_set = bool(dh.get("is_decision_record"))
    if decision_column:
        dh["decision_column"] = decision_column
    if terminal_states:
        dh["terminal_states"] = [s.strip() for s in terminal_states.split(",") if s.strip()]
    if not dh.get("decision_column"):
        return {
            "dataset_id": dataset_id,
            "hard_gate_pass": False,
            "hard_gate_failures": [
                "no decision column identified — this is not a decision dataset "
                "(pass ?decision_column= the completed-outcome column you actually "
                "observed in the sampled rows, or do not ground)"
            ],
            "signals": {},
            "suggested_contract": None,
            "notes": ["Could not identify a completed-decision column — do NOT ground on this dataset, and never invent one."],
        }
    if not flag_set:
        notes.append(
            "dataset is not flagged decision_history.is_decision_record — grounding on the "
            "builder's OBSERVED decision column; vetted below against real catalogue signals, "
            "and the BA must explicitly confirm before this is wired."
        )

    columns = entry.get("columns") or []
    by_name = {c.get("name"): c for c in columns if c.get("name")}
    by_physical = {c.get("physical_name"): c for c in columns if c.get("physical_name")}

    def _col(col_name: Optional[str]) -> Optional[Dict[str, Any]]:
        if not col_name:
            return None
        return by_name.get(col_name) or by_physical.get(col_name)

    decision_col = _col(dh.get("decision_column"))
    timestamp_col = _col(dh.get("timestamp_column"))
    reasoning_col = _col(dh.get("reasoning_column"))

    # Decision classes — distinct_values is the crawler's low-cardinality sample.
    decision_classes = (decision_col or {}).get("distinct_values") or None
    n_classes = len(decision_classes) if decision_classes else None
    decision_nullable = bool((decision_col or {}).get("nullable", True))
    declared_terminal = dh.get("terminal_states") or []

    # Recency / span from the timestamp column range.
    date_range = (timestamp_col or {}).get("range") or {}

    # Rough input-signal width: columns that are not the decision/timestamp/
    # reasoning columns and not primary keys (a decision the model can learn
    # from needs input features beyond the keys).
    structural = {
        (decision_col or {}).get("name"),
        (timestamp_col or {}).get("name"),
        (reasoning_col or {}).get("name"),
    }
    input_cols = [
        c for c in columns
        if c.get("name") not in structural and not c.get("is_primary_key")
    ]
    n_input_cols = len(input_cols)

    row_count = entry.get("row_count_approx")

    signals = {
        "row_count_approx": row_count,
        "decision_column": (decision_col or {}).get("name") or dh.get("decision_column"),
        "decision_classes": decision_classes,
        "n_decision_classes": n_classes,
        "decision_column_nullable": decision_nullable,
        "declared_terminal_states": declared_terminal,
        "timestamp_column": (timestamp_col or {}).get("name") or dh.get("timestamp_column"),
        "date_range": date_range,
        "has_reasoning_column": reasoning_col is not None,
        "n_input_columns": n_input_cols,
    }

    # ── Deterministic hard gates ──
    MIN_ROWS = 50
    if row_count is not None and row_count < MIN_ROWS:
        hard_failures.append(
            f"only ~{row_count} historical rows (< {MIN_ROWS}) — too few to ground on"
        )
    if n_classes is not None and n_classes < 2:
        hard_failures.append(
            f"decision column has {n_classes} distinct value(s) — a one-class corpus teaches no contrast"
        )
    if decision_col is None:
        hard_failures.append(
            f"declared decision_column {dh.get('decision_column')!r} not found in the dataset columns"
        )
    if n_input_cols < 1:
        hard_failures.append(
            "no usable input columns beyond keys/decision/timestamp — nothing for the model to reason from"
        )

    # ── Non-fatal notes for the LLM's judgment layer ──
    if decision_classes is None:
        notes.append(
            "decision-class distribution unknown (column not low-cardinality in the crawl) — "
            "the LLM should weigh class balance cautiously and the contract floor is set conservatively."
        )
    if declared_terminal and decision_classes:
        missing_declared = [t for t in declared_terminal if t not in decision_classes]
        if missing_declared:
            notes.append(
                f"declared terminal_states {missing_declared} not seen in sampled distinct_values — "
                "descriptor may be stale or the column was mis-mapped."
            )
    if decision_nullable:
        notes.append("decision column is nullable — some rows may lack a recorded decision (fill rate unknown from catalogue).")
    if not reasoning_col:
        notes.append("no reasoning column — few-shots will carry input+decision only, no rationale.")

    hard_gate_pass = not hard_failures

    # ── Suggested contract (the builder reviews + finalizes) ──
    suggested_contract: Optional[Dict[str, Any]] = None
    if hard_gate_pass:
        suggested_contract = {
            # Floor on the TOTAL pool the selector forwards (neighbor-search corpus).
            # The selector forwards the whole packaged set, so this is a low sanity
            # floor; shrink_floor guards against collapse of a larger pool.
            "min_samples": 8,
            # Floor on the always-loaded canonical few-shots (selector target is 8,
            # low end of the 5–15 band). This is the always-on grounding.
            "min_canonical": 5,
            "shrink_floor": 0.5,
            # Only COMPLETED-decision rows are grounded: terminal_states are the
            # decided states of the decision column (the operator declares them
            # in decision_history). In-progress states (e.g. 'pending') are
            # excluded from the pull/package. So the guard requires the decided
            # classes — NOT every class the crawler saw (which would include the
            # excluded in-progress ones and fail the guard).
            "terminal_states": declared_terminal or [],
            "required_decision_classes": declared_terminal or decision_classes or [],
            "min_decision_fill_rate": 0.7 if decision_nullable else 0.9,
            "source_profile_baseline": signals,
            "evaluation_verdict": None,  # builder fills the one-paragraph rationale
        }

    # ── Suggested outcome-poll (self-improving loop), DERIVED from the catalogue's
    # decision_history OUTCOME fields — so the app does NOT hand-author outcome
    # columns/values (IT declares them in dept_sources). None when IT hasn't
    # declared a complete outcome signal (grounding can still run; outcomes just
    # won't be auto-observed). auto_refresh stays False: MANUAL until the user
    # enables auto-run in the app. ──
    suggested_outcome_poll: Optional[Dict[str, Any]] = None
    _ocol = dh.get("outcome_field") or dh.get("decision_column")
    _okey = dh.get("key_field")
    _ogood, _obad = dh.get("good_values") or [], dh.get("bad_values") or []
    if hard_gate_pass and _ocol and _okey and (_ogood or _obad):
        _otable = dataset_id.split(".", 1)[1].strip() if "." in dataset_id else dataset_id
        suggested_outcome_poll = {
            "enabled": True,
            "auto_refresh": True,   # auto-learn ON by default; disable per-app in the UI
            "kind": "sql",
            "table": _otable,
            "key_field": _okey,
            "payload_key_field": _okey,
            "status_field": _ocol,
            "good_values": _ogood,
            "bad_values": _obad,
            "neutral_values": dh.get("neutral_values") or [],
            "hold_field": dh.get("outcome_hold_field"),
            "window_days": dh.get("settling_window_days") or 7.0,
        }
    elif hard_gate_pass and _ocol and not _okey:
        notes.append(
            "decision_history declares an outcome column but no key_field — outcome "
            "auto-observation stays OFF until IT adds key_field (the record id used "
            "for read-back) to the catalogue."
        )

    return {
        "dataset_id": dataset_id,
        "hard_gate_pass": hard_gate_pass,
        "hard_gate_failures": hard_failures,
        "signals": signals,
        "suggested_contract": suggested_contract,
        "suggested_outcome_poll": suggested_outcome_poll,
        "notes": notes,
    }


# ---------------------------------------------------------------------------
# Triggers — webhook + admin tick
# ---------------------------------------------------------------------------


@app.post(
    "/apps/{slug}/trigger/{trigger_id}",
    status_code=status.HTTP_202_ACCEPTED,
)
async def webhook_trigger(
    slug: str,
    trigger_id: str,
    request: Request,
    settings: Settings = Depends(get_settings),
) -> Dict[str, Any]:
    """External webhook entry. HMAC-verified, runs as service principal.

    The caller (e.g. an insurer's claims gateway) signs the raw request
    body with the shared HMAC secret resolved from ``trigger.secret_ref``
    and presents it as ``X-Citra-Signature: sha256=<hex>``. There is no
    end-user JWT — triggered runs use ``user_id = "system:<slug>"``.
    """
    body = await request.body()
    # Cap payload size before HMAC to limit DoS surface.
    cap = 1_000_000
    if len(body) > cap:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"webhook payload exceeds {cap} bytes",
        )

    # Resolve test↔prod by store so a test app's webhook fires against the test
    # MCPs and stages into the test_ queue (see _bind_app_env).
    await _bind_app_env(slug)
    apps = get_apps_col()
    agents = get_agents_col()
    app_doc = await apps.find_one({"slug": slug})
    if not app_doc:
        # Don't leak existence to unauthenticated webhook callers.
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="not found")
    if app_doc.get("status") != AppStatus.PUBLISHED.value:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="not found")
    # Kill switch: webhook trigger intake stops under a halt/pause.
    await _enforce_automation_allowed(app_doc, what="Webhook trigger")
    try:
        app_spec = _load_app_spec(app_doc)
    except Exception as e:  # noqa: BLE001
        logger.error("app_spec invalid for %s: %s", slug, e)
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail="bad spec")

    trigger = find_trigger(app_spec, trigger_id)
    if trigger is None or trigger.type != "webhook":
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="trigger not found")
    if not trigger.enabled:
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail="trigger is disabled"
        )

    secret = resolve_secret(trigger.secret_ref)
    if not secret:
        # Misconfigured trigger — refuse rather than accept unsigned input.
        logger.warning(
            "webhook trigger %s/%s has no resolvable secret", slug, trigger_id
        )
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="trigger secret not configured",
        )
    sig = (
        request.headers.get("X-Citra-Signature")
        or request.headers.get("x-citra-signature")
    )
    if not verify_webhook_signature(secret=secret, body=body, signature_header=sig):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, detail="signature mismatch"
        )

    try:
        inputs = json.loads(body) if body else {}
    except json.JSONDecodeError:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail="body must be JSON"
        )
    if not isinstance(inputs, dict):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail="body must be a JSON object"
        )

    agent_doc = await agents.find_one(
        {
            "agent_id": app_doc["agent_id"],
            "tenant_id": app_doc.get("tenant_id"),
        }
    )
    if not agent_doc:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR, detail="agent missing"
        )
    # Durable accept: signature + payload verified above → enqueue → 202. The
    # consumer fires the agent with row_key=job_id (idempotent on redelivery),
    # stages/commits, and records to run history — a process restart no longer
    # loses the event. NOTE: the webhook is now ASYNC — callers treat 202 as
    # "received" and poll run history for the outcome (was a synchronous result).
    import trigger_queue
    job_id = trigger_queue.enqueue_trigger(
        slug=slug, trigger_id=trigger_id, inputs=inputs,
        env=current_env(), source="webhook", tenant_id=app_doc.get("tenant_id"),
    )
    return {"accepted": True, "trigger_id": trigger_id, "job_id": job_id}


@app.post("/scheduler/tick", response_model=dict)
async def scheduler_tick(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> dict:
    """Manual scheduler tick — admin / dev only.

    The production scheduler runs as a background asyncio task; this
    endpoint exists so on-prem operators can force a tick without a
    process restart. Admin-only: a manual tick fans out across EVERY
    tenant's triggers, so it is gated to platform/org/super admins —
    a regular authenticated user must not be able to force-fire the fleet.
    """
    get_secure_user_id(request)
    _roles = set(get_user_roles(request) or [])
    if not (_roles & {"super_admin", "org_admin", "platform_admin"}):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail="scheduler tick requires a platform/org admin role",
        )
    activity = await tick_once(
        settings=settings,
        apps_col=get_apps_col(),
        agents_col=get_agents_col(),
        trigger_state_col=get_trigger_state_col(),
        pending_runs_col=get_pending_runs_col(),
        stage_recommendation=_stage_recommendation,
        record_run=_record_trigger_run,
    )
    return {"fired": sum(1 for a in activity if a.get("fired")), "activity": activity}


@app.get(
    "/apps/{slug}/data/{panel_id}",
    response_model=PanelDataResponse,
)
async def get_panel_data(
    slug: str,
    panel_id: str,
    request: Request,
    settings: Settings = Depends(get_settings),
) -> PanelDataResponse:
    """Resolve the rows backing a queue / chart / dashboard panel.

    v0 only resolves ``type=static`` data sources (rows embedded in the
    AppSpec). MCP / RAG / workflow sources return an empty result with a
    descriptive ``note`` so the UI can show an empty state.
    """
    user_id = get_secure_user_id(request)
    user_tenant = get_tenant_id(request)
    # Resolve test↔prod by store before any collection access — a test app's
    # panels read through the TEST MCP (see _bind_app_env).
    await _bind_app_env(slug)
    apps = get_apps_col()

    app_doc = await apps.find_one({"slug": slug})
    if not app_doc or not _can_render_app(
        app_doc, request, user_id, user_tenant
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="app not found")
    if app_doc.get("status") == AppStatus.ARCHIVED.value:
        raise HTTPException(status.HTTP_410_GONE, detail="app is archived")

    app_spec = _load_app_spec(app_doc)
    auth_header = request.headers.get("authorization") or request.headers.get(
        "Authorization"
    )
    # Row-level visibility for workflow_staging panels — the resolver enforces
    # the caller's tenant + dept scope (the collection has no RLS), matching the
    # /workflow-staging endpoint.
    viewer_scope = {
        "tenant_id": user_tenant,
        "dept_ids": get_user_dept_ids(request),
        "roles": get_user_roles(request),
    }
    # Page params (e.g. a filter_bar's ?district=Patna) flow into the panel's
    # data_source filter predicate. Internal keys (_t auth token, …) are
    # excluded — only declared page params reach the query.
    page_params = {
        k: v for k, v in request.query_params.items() if not k.startswith("_")
    }
    resp = await resolve_panel_data(
        settings=settings,
        app_spec=app_spec,
        panel_id=panel_id,
        auth_header=auth_header,
        viewer_scope=viewer_scope,
        page_params=page_params or None,
    )
    # Fail loud (RULE #1): a real source failure (MCP down / unresolved /
    # access denied) must NOT render as a benign empty state. The resolver
    # surfaces it in ``error``; turn it into a 502 the runtime shows distinctly.
    if resp.error:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=resp.error)
    return resp


# ---------------------------------------------------------------------------
# Builder preview smoke test (Tier 1 — data-contract).
#
# The builder's self-test exercises the AGENT (tool probes, synthetic
# decisions). It NEVER renders the dashboard/app panels against live data, so
# a spec that publishes "successfully" can still show a blank or wrong app
# (unresolved data_source.ref, null KPIs, hallucinated chart columns). This
# endpoint walks EVERY data-bearing panel through the real resolver and
# returns a structured pass/fail report so the builder can fix-and-retry
# BEFORE handing the BA a preview URL. Headless — no browser, no runtime.
# ---------------------------------------------------------------------------

_SMOKE_DATA_PANEL_TYPES = {"dashboard", "chart", "queue", "detail"}
_SMOKE_UNRESOLVED_MARKERS = (
    "could not resolve",
    "not visible",
    "unknown source",
    "no such dataset",
    "unresolved",
)


def _smoke_assess_detail(panel: Any, ddr: Any) -> Dict[str, Any]:
    """Grade a DETAIL panel. A detail panel resolves a single record either
    from the LINKED queue's source (``linked_to``) or directly from its own
    ``data_source`` (embed pages, where the host passes the record id and there
    is no queue to click). Either way it has no LIST to count, so we must NOT
    gate it on "no data_source binding" — that false-fails every valid detail,
    whose binding resolves fine at runtime. The only structural failure is a
    ``linked_to`` that doesn't point at a queue with a usable data_source, which
    ``resolve_detail_data`` reports in its ``note``.
    (The smoke pass uses record_id=None — it proves the binding resolves, not a
    specific record.) Returns the same {id,type,rows,metrics,status,issues} shape."""
    pid = getattr(panel, "id", "?")
    note = (getattr(ddr, "note", None) or "")
    cols = getattr(ddr, "record_columns", None) or []
    issues: List[Dict[str, Any]] = []
    status_ = "ok"
    if "not a queue with a data_source" in note.lower():
        status_ = "fail"
        issues.append({
            "severity": "fail",
            "msg": (f"detail panel '{pid}' linked_to "
                    f"'{getattr(panel, 'linked_to', '?')}' which is not a queue "
                    "with a data_source"),
            "likely_fix": ("point linked_to at a queue/table panel that has a "
                           "data_source — the detail reads its record from there"),
        })
    return {"id": pid, "type": "detail", "rows": (1 if cols else 0),
            "metrics": 0, "status": status_, "issues": issues}


def _smoke_assess_panel(panel: Any, resp: "PanelDataResponse") -> Dict[str, Any]:
    """Grade one resolved panel. Returns {id,type,rows,metrics,status,issues}.
    status ∈ {ok, warn, fail}. A `fail` blocks the preview URL."""
    ptype = getattr(panel, "type", None)
    # Grade against BOTH note and the explicit error channel: a real source
    # failure (MCP down / unresolved) now surfaces in resp.error with note=None,
    # so reading note alone would let a broken-source panel pass the smoke test.
    note = ((resp.note or "") + " " + (getattr(resp, "error", None) or "")).strip()
    note_l = note.lower()
    issues: List[Dict[str, Any]] = []

    # A real source failure surfaces in resp.error (note=None) — hard-fail the
    # smoke gate on ANY error, not just notes matching an "unresolved" marker
    # (an "mcp … returned 401/500/transport" error contains no such marker).
    if getattr(resp, "error", None):
        issues.append({
            "severity": "fail",
            "msg": f"data source failed to resolve: {resp.error}",
            "likely_fix": "the panel's data_source did not resolve or the MCP rejected "
                          "the read — verify data_source.ref + the source is visible to you.",
        })

    unresolved = any(m in note_l for m in _SMOKE_UNRESOLVED_MARKERS)
    if unresolved:
        issues.append({
            "severity": "fail",
            "msg": f"data source did not resolve: {note}",
            "likely_fix": "data_source.ref must be the catalogue dataset_id verbatim "
                          "('<source_id>.<table>'), with no '/' — see the entry's `ref` field.",
        })

    if ptype == "dashboard":
        mets = resp.metrics or []
        if not mets:
            issues.append({"severity": "fail", "msg": "dashboard returned no metrics",
                           "likely_fix": "metrics did not resolve — check data_source.ref + filters"})
        else:
            null_names = [m.get("name") for m in mets if m.get("value") is None]
            if null_names and len(null_names) == len(mets):
                issues.append({"severity": "fail",
                               "msg": f"ALL {len(mets)} KPI metrics resolved null — every tile shows '—'",
                               "likely_fix": "data_source.ref or filter is wrong for this panel"})
            elif null_names:
                issues.append({"severity": "warn",
                               "msg": f"KPI metric(s) resolved null: {null_names}",
                               "likely_fix": "check the field/filter for these metrics"})
    elif ptype == "chart":
        cols = set(resp.columns or [])
        declared = [panel.x]
        declared += [panel.y] if isinstance(panel.y, str) else list(panel.y or [])
        if getattr(panel, "group_by", None):
            declared.append(panel.group_by)
        # Column check is only meaningful once we have columns (derived from rows).
        for c in declared:
            if c and cols and c not in cols:
                issues.append({"severity": "fail",
                               "msg": f"chart references column '{c}' absent from the data "
                                      f"(returned: {sorted(cols)})",
                               "likely_fix": "x / y / group_by must be exact columns on the dataset"})
        if not resp.rows and not unresolved:
            issues.append({"severity": "warn", "msg": "chart returned 0 rows",
                           "likely_fix": "the query/filter may exclude all rows, or the dataset is empty"})
    elif ptype in ("queue", "detail"):
        if not unresolved and not resp.rows:
            issues.append({"severity": "warn", "msg": f"{ptype} returned 0 rows",
                           "likely_fix": "verify the source/filter (may be legitimately empty)"})

    status = "fail" if any(i["severity"] == "fail" for i in issues) else (
        "warn" if issues else "ok")
    # Surface the ACTUAL DATA the API returned so the builder LLM can verify the
    # COMPUTATION against intent (vision only sees pixels; this is the numbers).
    # dashboards → the computed metric name→value pairs; charts/queues → the
    # columns + a small row sample.
    data: Dict[str, Any] = {}
    if ptype == "dashboard":
        data["metric_values"] = [
            {"name": m.get("name"), "value": m.get("value"),
             "delta": (m.get("delta") or {}).get("text") if m.get("delta") else None}
            for m in (resp.metrics or [])
        ]
    else:
        data["columns"] = resp.columns or []
        data["sample"] = (resp.rows or [])[:3]
    return {
        "id": getattr(panel, "id", "?"),
        "type": ptype,
        "rows": resp.total or len(resp.rows or []),
        "metrics": len(resp.metrics or []),
        "status": status,
        "issues": issues,
        "data": data,
    }


# A 1x1 transparent PNG — a real, valid image blob identical in shape to what
# the runtime's form renderer emits for a format:"file" field
# ({filename,content_type,data}). The form-submission smoke uses it so a
# file-field-bound-to-a-string-column mismatch (the classic 422
# "is not of type 'string'") is caught HERE, not at the officer's first submit.
_FILE_PROBE_BLOB = {
    "filename": "smoke-probe.png",
    "content_type": "image/png",
    "data": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR4nGNgYAAAAAMAASsJTYQAAAAASUVORK5CYII=",
}


def _synth_form_inputs(schema: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Build a submit payload from a form's JSON schema using the SAME shapes
    the runtime form renderer emits — a file-blob descriptor for format:"file"
    fields, plausible values otherwise. This is what lets the smoke test
    actually exercise the write the form fires."""
    props = (schema or {}).get("properties") or {}
    out: Dict[str, Any] = {}
    for name, p in props.items():
        if not isinstance(p, dict):
            out[name] = f"PROBE-{name}"
            continue
        fmt, typ = p.get("format"), p.get("type")
        if fmt == "file":
            out[name] = dict(_FILE_PROBE_BLOB)
        elif p.get("enum"):
            out[name] = p["enum"][0]
        elif typ in ("number", "integer"):
            out[name] = p.get("minimum") or 1
        elif typ == "boolean":
            out[name] = False
        elif fmt in ("date", "date-time"):
            out[name] = "2026-01-01"
        elif fmt == "time":
            out[name] = "09:00"
        elif typ == "array":
            items = p.get("items") or {}
            out[name] = [(items.get("enum") or ["PROBE"])[0]]
        else:
            out[name] = f"PROBE-{name}"
    return out


def _resolve_form_schema(panel: Any, agent_spec: Optional["AgentSpec"]) -> Optional[Dict[str, Any]]:
    """A form's fields come from schema_inline, or schema_ref pointing at an
    agent action's input_schema (e.g. 'agent.input_schema' or
    '<action>.input_schema'). Resolve to the JSON schema dict."""
    inline = getattr(panel, "schema_inline", None)
    if inline:
        return inline
    ref = getattr(panel, "schema_ref", None)
    if not ref or not agent_spec:
        return None
    # schema_ref → the on_submit action's input_schema (the common case), else
    # the first action's input_schema.
    on_submit = getattr(panel, "on_submit", None)
    want = getattr(on_submit, "agent_action", None)
    for act in (agent_spec.actions or []):
        if want and getattr(act, "name", None) == want:
            return getattr(act, "input_schema", None)
    acts = agent_spec.actions or []
    return getattr(acts[0], "input_schema", None) if acts else None


def _resolve_form_write(panel: Any, agent_spec: Optional["AgentSpec"],
                        form_schema: Optional[Dict[str, Any]]):
    """Resolve the mcp_action write a form's on_submit fires, as
    (source_id, dataset_id, action_id, input_schema), or None.
    - tool_name → the matching kind='mcp_action' tools_v2 entry.
    - agent_action → the mcp_action whose required inputs are all present in
      the form's fields (the write the form feeds); when the agent has exactly
      one mcp_action, use it."""
    if not agent_spec:
        return None
    mcp_actions = [t for t in (getattr(agent_spec, "tools_v2", None) or [])
                   if getattr(t, "kind", None) == "mcp_action"]
    if not mcp_actions:
        return None

    def _tuple(t):
        return (getattr(t, "source_id", None), getattr(t, "dataset_id", None),
                getattr(t, "action_id", None), getattr(t, "input_schema", None))

    on_submit = getattr(panel, "on_submit", None)
    tname = getattr(on_submit, "tool_name", None)
    if tname:
        t = next((t for t in mcp_actions if getattr(t, "name", None) == tname), None)
        return _tuple(t) if t else None
    if len(mcp_actions) == 1:
        return _tuple(mcp_actions[0])
    form_fields = set(((form_schema or {}).get("properties") or {}).keys())
    best, best_overlap = None, -1
    for t in mcp_actions:
        isc = getattr(t, "input_schema", None) or {}
        if set(isc.get("required") or []) - form_fields:
            continue  # the form can't satisfy this write's required inputs
        overlap = len(set((isc.get("properties") or {}).keys()) & form_fields)
        if overlap > best_overlap:
            best, best_overlap = t, overlap
    return _tuple(best) if best is not None else None


async def _smoke_assess_form(
    *, settings, app_spec, agent_spec, panel, page_id, user_jwt,
) -> Dict[str, Any]:
    """Dry-run a form panel's on_submit WRITE with a synthetic payload (real
    control shapes incl. a file blob), so SUBMIT-time write failures — the 422
    class the read-panel gate never exercises — surface before the preview URL
    is shared. LLM-free: dry-runs the bound mcp_action directly (no agent run),
    so it is deterministic AND works without model credits."""
    base = {"id": getattr(panel, "id", "?"), "type": "form", "page": page_id,
            "rows": 0, "metrics": 0}
    on_submit = getattr(panel, "on_submit", None)
    if not on_submit or not (getattr(on_submit, "agent_action", None)
                             or getattr(on_submit, "tool_name", None)):
        return {**base, "status": "ok", "issues": [],
                "data": {"note": "navigate-only form — no write to exercise"}}
    schema = _resolve_form_schema(panel, agent_spec)
    inputs = _synth_form_inputs(schema)
    issues: List[Dict[str, Any]] = []
    write = _resolve_form_write(panel, agent_spec, schema)
    if not write or not (write[1] and write[2]):
        # Couldn't resolve the write to dry-run. We do NOT statically fail a
        # file field — the platform stores every uploaded blob
        # (data_tools._store_upload_blobs) and writes a ref string into the
        # column (incl. format:"file" inputs), so that submit succeeds. Just
        # flag that we couldn't exercise it.
        issues.append({"severity": "warn",
            "msg": f"form '{base['id']}' on_submit write could not be resolved to dry-run",
            "likely_fix": "ensure on_submit binds to a known agent_action/tool_name whose "
                          "write is catalogued"})
        return {**base, "status": "warn", "issues": issues,
                "data": {"submitted_fields": list(inputs.keys())}}

    source_id, dataset_id, action_id, isc = write
    props = (isc or {}).get("properties") or {}
    file_fields = [n for n, fp in ((schema or {}).get("properties") or {}).items()
                   if isinstance(fp, dict) and fp.get("format") == "file"]
    tested: List[str] = []
    if getattr(on_submit, "tool_name", None):
        # DIRECT write: the form fields ARE the write payload (1:1, no agent
        # mapping) — a real dry-run is faithful and credit-free.
        payload = ({k: v for k, v in inputs.items() if k in props} if props else dict(inputs)) or dict(inputs)
        tested = list(payload.keys())
        from proxy_clients import call_dept_mcp_execute_action, ProxyError
        try:
            resp = await call_dept_mcp_execute_action(
                settings=settings, user_jwt=user_jwt, source_id=source_id,
                dataset_id=dataset_id, action_id=action_id, payload=payload, dry_run=True,
            )
            ok = bool(isinstance(resp, dict) and resp.get("ok") and not resp.get("error"))
            detail = None if ok else str(resp.get("error") or resp.get("detail") or resp)[:300]
        except ProxyError as exc:  # noqa: BLE001
            ok, detail = False, f"{exc.code}: {exc}"[:300]
        except Exception as exc:  # noqa: BLE001
            ok, detail = False, f"{type(exc).__name__}: {exc}"[:300]
        if not ok:
            dl = (detail or "").lower()
            if "is not of type 'string'" in dl or ("filename" in dl and "type" in dl):
                fix = ("a file blob reached the write as an object — the platform S3 fallback "
                       "should have stored it and substituted a ref string. Seeing this means the "
                       "fallback did NOT run (CITRA_SERVICE_URL unset / Citra blob endpoint down) "
                       "— a platform issue to fix, not a missing column.")
            elif "required" in dl:
                fix = "a required write field is missing, or a field name/type mismatches the input_schema."
            else:
                fix = "the form's field names/types must satisfy the write action's input_schema."
            issues.append({"severity": "fail",
                "msg": f"form '{base['id']}' direct submit FAILED (dry-run {dataset_id}.{action_id}): {detail}",
                "likely_fix": fix})
    else:
        # AGENT_ACTION: the agent maps form fields → write inputs (renames,
        # generated keys), so the FAITHFUL test runs the agent in plan_only
        # (the write is dry-run, never committed). This uses the model and
        # surfaces the REAL submit error with correct field mapping — e.g. a
        # file blob landing in a string write column.
        tested = list(inputs.keys())
        try:
            resp = await execute_run(
                settings=settings, app_spec=app_spec, agent_spec=agent_spec,
                request=RunRequest(action=on_submit.agent_action, inputs=inputs,
                                   mode="queue_action"),
                auth_header=(f"Bearer {user_jwt}" if user_jwt else None),
                plan_only=True,
            )
            werr = next(
                (w for w in (resp.write_events or [])
                 if isinstance(w, dict)
                 and ("validation failed" in str(w).lower()
                      or "is not of type" in str(w).lower()
                      or "error" in str(w.get("response") or w.get("result") or {}).lower()
                      or w.get("status") == "error")),
                None,
            )
            terr = next((t for t in (resp.timeline or [])
                         if isinstance(t, dict) and t.get("status") == "error"), None)
            if resp.status == "failed" or werr or terr:
                detail = (resp.error
                          or (str(werr)[:300] if werr else None)
                          or (str(terr.get("detail"))[:300] if terr else None)
                          or "form submit produced a write error")
                dl = detail.lower()
                if "is not of type 'string'" in dl or ("filename" in dl and "type" in dl):
                    fix = ("a file blob reached the write as an object — the platform S3 fallback "
                           "should have stored it and passed a ref string. This 422 means the "
                           "fallback did NOT run (CITRA_SERVICE_URL unset / Citra blob endpoint "
                           "down) — a platform issue, not a missing column.")
                elif "required" in dl:
                    fix = "a required write field is missing, or a field name/type mismatches the input_schema."
                else:
                    fix = "the form's fields must satisfy the write action's input_schema."
                issues.append({"severity": "fail",
                    "msg": f"form '{base['id']}' submit FAILED (agent dry-run): {detail}",
                    "likely_fix": fix})
        except Exception as exc:  # noqa: BLE001 — surface, don't silently skip
            es = str(exc).lower()
            if ("input validation failed" in es or "is not of type" in es
                    or "is a required property" in es
                    or " 422" in es or es.startswith("422")):
                # The agent ran and the SOURCE WRITE was rejected — this is the
                # real submit bug (correct field mapping already applied), not a
                # model outage. Fail loudly with the actionable fix.
                if "is not of type 'string'" in es or ("filename" in es and "type" in es):
                    fix = ("a file blob reached the write as an object — the platform S3 fallback "
                           "should have stored it and passed a ref string. This 422 means the "
                           "fallback did NOT run (CITRA_SERVICE_URL unset / Citra blob endpoint "
                           "down) — a platform issue, not a missing column.")
                else:
                    fix = "the form's field names/types must satisfy the write action's input_schema."
                issues.append({"severity": "fail",
                    "msg": f"form '{base['id']}' submit FAILED (agent dry-run): {str(exc)[:300]}",
                    "likely_fix": fix})
            else:
                issues.append({"severity": "warn",
                    "msg": f"form '{base['id']}' submit could NOT be exercised — agent run error: "
                           f"{type(exc).__name__}: {str(exc)[:200]} (e.g. the model endpoint is "
                           f"unavailable / out of credits).",
                    "likely_fix": "restore model credits / endpoint, then re-run the gate."})
    status = "fail" if any(i["severity"] == "fail" for i in issues) else (
        "warn" if issues else "ok")
    return {**base, "status": status, "issues": issues,
            "data": {"tested_fields": tested or list(inputs.keys()),
                     "write": f"{dataset_id}.{action_id}"}}


# ── Loop-prevention: classify gate issues + cap same-error retries ──────────
# The builder runs gate → fix → gate → fix … An issue it CANNOT fix by editing
# the AppSpec (a missing platform capability, a 5xx, an unimplemented feature
# like a non-file-capable upload column) makes every "fix" a no-op → an infinite
# loop (the 90-turn trap). The cure is not a blind turn cap (that would also
# kill legitimate long builds) — it is telling the builder WHICH failures are
# its to fix, and capping repeats of the SAME failure.
_SAME_ERROR_CAP = 3
_PLATFORM_MARKERS = (
    "returned 5", "transport", "connection", "timeout", "timed out",
    "app not found", "user_org=none", "mcp_rejected", "could not be exercised",
    "model endpoint", "out of credits", "internal server error",
    " 500", " 502", " 503", " 504", "raised:", "resolver error",
)
# NOTE: a file upload is NOT a requirements gap — the platform stores every
# uploaded blob (data_tools._store_upload_blobs), incl. format:"file" inputs,
# and writes a ref string into the column, so that submit succeeds. Those
# markers are deliberately absent here. Only genuinely-unfixable capability
# gaps belong below.
_REQUIREMENTS_MARKERS = (
    "ocr not configured", "ocr_not_configured", "requirements_unmet",
    "exposes no column", "no write action", "ask it to expose",
)


def _classify_issue(msg: str) -> Tuple[str, bool]:
    """Classify a gate issue so the builder knows whether it can fix it.
    Returns (cls, fixable); cls ∈ {spec, requirements, platform}.
    - requirements/platform → fixable=False → the builder must NOT loop: it
      escalates to requirements_unmet + a plain BA message (contact IT).
    - spec → fixable=True → edit the AppSpec and retry (capped at _SAME_ERROR_CAP)."""
    m = (msg or "").lower()
    if any(k in m for k in _REQUIREMENTS_MARKERS):
        return "requirements", False
    if any(k in m for k in _PLATFORM_MARKERS):
        return "platform", False
    return "spec", True


def _issue_fingerprint(panel_id: str, msg: str) -> str:
    """Stable key for the SAME failure across build iterations: panel id + the
    message with volatile bits (uuids, digits) stripped. A genuinely-fixed issue
    stops appearing and its counter resets; a persistent one climbs → escalate."""
    norm = re.sub(r"[0-9a-f]{8,}|\d+", "", (msg or "").lower())
    norm = re.sub(r"\s+", " ", norm).strip()[:80]
    return f"{panel_id}:{norm}"


@app.post("/builder/preview-smoke")
async def builder_preview_smoke(
    slug: str,
    request: Request,
    settings: Settings = Depends(get_settings),
) -> Dict[str, Any]:
    """Render-against-live-data smoke test for a (preview) app. Resolves every
    data-bearing panel AND dry-run-submits every form so submit/upload failures
    surface here, then reports per-panel pass/fail so the builder can
    fix-and-retry before sharing the preview URL. `passed=false` means do NOT
    hand the BA the URL yet."""
    user_id = get_secure_user_id(request)
    user_tenant = get_tenant_id(request)
    # Smoke is a BUILD-path call — the just-published app lives in the test_
    # collections, so bind the BUILD env (always test) directly. Do NOT
    # re-resolve by slug via _bind_app_env: that prefers prod and, on a cross-
    # tenant slug collision (a stale prod app sharing this slug), would bind the
    # wrong app and 404. Matches every other /builder/* endpoint.
    _bind_build_env()
    apps = get_apps_col()
    app_doc = await apps.find_one({"slug": slug})
    if not app_doc or not _can_render_app(app_doc, request, user_id, user_tenant):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="app not found")
    app_spec = _load_app_spec(app_doc)
    auth_header = request.headers.get("authorization") or request.headers.get(
        "Authorization"
    )
    # Load the AgentSpec too — the form-submission smoke drives on_submit
    # (agent_action) through execute_run, and resolves schema_ref forms from
    # the agent's action input_schema.
    agent_spec_obj: Optional[AgentSpec] = None
    if app_doc.get("agent_id"):
        _agent_doc = await get_agents_col().find_one(
            {"agent_id": app_doc["agent_id"], "tenant_id": app_doc.get("tenant_id")}
        )
        if _agent_doc:
            try:
                agent_spec_obj = AgentSpec.model_validate(_agent_doc["agent_spec"])
            except Exception:  # noqa: BLE001 — a bad agent spec is a separate failure
                agent_spec_obj = None

    panels: List[tuple] = []
    if app_spec.panels:
        panels += [(None, p) for p in app_spec.panels]
    for pg in (app_spec.pages or []):
        panels += [(pg.id, p) for p in (pg.panels or [])]

    async def _smoke_one(page_id, p) -> Dict[str, Any]:
        try:
            if getattr(p, "type", None) == "detail":
                # Detail panels have NO direct data_source — they bind via
                # linked_to to the queue's source. Resolve through
                # resolve_detail_data (record_id=None proves the binding) so we
                # don't false-fail them on "no data_source binding".
                ddr = await resolve_detail_data(
                    settings=settings, app_spec=app_spec, panel_id=p.id,
                    record_id=None, auth_header=auth_header,
                    app_id=app_doc.get("app_id"),
                )
                r = _smoke_assess_detail(p, ddr)
            else:
                resp = await resolve_panel_data(
                    settings=settings, app_spec=app_spec,
                    panel_id=p.id, auth_header=auth_header,
                )
                r = _smoke_assess_panel(p, resp)
        except Exception as e:  # noqa: BLE001 — resolver error IS a panel failure
            r = {"id": getattr(p, "id", "?"), "type": getattr(p, "type", None),
                 "rows": 0, "metrics": 0, "status": "fail",
                 "issues": [{"severity": "fail", "msg": f"panel resolve raised: {e}",
                             "likely_fix": "unexpected resolver error — inspect the binding"}]}
        r["page"] = page_id
        return r

    # Independent panels — resolve concurrently so a multi-panel app's preview
    # latency is the slowest panel, not the sum. Per-panel try/except above
    # keeps one failure from sinking the rest (grade-the-rest semantics).
    data_panels = [
        (pg, p) for pg, p in panels
        if getattr(p, "type", None) in _SMOKE_DATA_PANEL_TYPES
    ]
    results: List[Dict[str, Any]] = (
        list(await asyncio.gather(*[_smoke_one(pg, p) for pg, p in data_panels]))
        if data_panels else []
    )

    # Form-submission smoke: dry-run each form's on_submit write with a
    # synthetic payload (real control shapes incl. a file blob). This is the
    # path the read-panel checks never exercise — it catches submit/upload
    # failures (e.g. a format:'file' field bound to a string column → 422)
    # BEFORE the BA clicks submit. Run sequentially: each agent_action form
    # is a (plan-only) agent run, so we don't fan out LLM calls.
    form_panels = [(pg, p) for pg, p in panels if getattr(p, "type", None) == "form"]
    n_forms = 0
    for pg, p in form_panels:
        n_forms += 1
        try:
            results.append(await _smoke_assess_form(
                settings=settings, app_spec=app_spec, agent_spec=agent_spec_obj,
                panel=p, page_id=pg,
                user_jwt=(auth_header or "").removeprefix("Bearer ").strip() or None,
            ))
        except Exception as e:  # noqa: BLE001
            results.append({"id": getattr(p, "id", "?"), "type": "form", "page": pg,
                            "rows": 0, "metrics": 0, "status": "fail",
                            "issues": [{"severity": "fail",
                                        "msg": f"form smoke raised: {e}",
                                        "likely_fix": "unexpected error exercising the form"}]})

    # Loop-prevention enrichment: classify every issue (fixable spec vs
    # unfixable platform/requirements) and count repeats of the SAME failure
    # across build iterations. State lives on the app doc keyed by a
    # digit-stripped fingerprint; a fixed issue's fingerprint disappears and its
    # counter resets, while a persistent one climbs to the cap and escalates.
    prev_attempts = ((app_doc.get("_gate_state") or {}).get("attempts")) or {}
    seen_attempts: Dict[str, int] = {}
    escalations: List[Dict[str, Any]] = []
    for r in results:
        for iss in (r.get("issues") or []):
            cls, fixable = _classify_issue(iss.get("msg", ""))
            fp = _issue_fingerprint(str(r.get("id", "?")), iss.get("msg", ""))
            n = prev_attempts.get(fp, 0) + 1
            seen_attempts[fp] = max(seen_attempts.get(fp, 0), n)
            # A spec issue that won't converge after the cap is, in practice, not
            # fixable from the spec — escalate it like a platform/requirements gap.
            over_cap = fixable and n >= _SAME_ERROR_CAP
            iss["class"] = "spec_exhausted" if over_cap else cls
            iss["fixable"] = fixable and not over_cap
            iss["attempt"] = n
            iss["escalate"] = (not fixable) or over_cap
            if iss["escalate"] and iss.get("severity") == "fail":
                escalations.append({
                    "panel": r.get("id"), "page": r.get("page"),
                    "class": iss["class"], "attempt": n,
                    "msg": iss.get("msg"), "likely_fix": iss.get("likely_fix"),
                })
    # Persist only fingerprints seen THIS run (prunes fixed issues → reset).
    try:
        await apps.update_one({"slug": slug}, {"$set": {"_gate_state.attempts": seen_attempts}})
    except Exception as e:  # noqa: BLE001 — counter is best-effort, never block the gate
        logger.warning("[gate] could not persist attempt counters for %s: %s", slug, e)

    n_fail = sum(1 for r in results if r["status"] == "fail")
    n_warn = sum(1 for r in results if r["status"] == "warn")
    passed = n_fail == 0
    n_data = len(results) - n_forms
    if escalations:
        action = "escalate_to_BA"
        guidance = (
            f"STOP retrying the {len(escalations)} escalated issue(s) below — they are "
            "platform/requirements gaps you CANNOT fix by editing the AppSpec, or the same "
            f"spec error has failed {_SAME_ERROR_CAP}× and will not converge. Do NOT loop. "
            "For each: move it to requirements_unmet, and tell the BA in plain language what "
            "is missing and that IT must address it (e.g. expose a file-capable column / "
            "enable OCR / fix the MCP). Build and ship the REST of the app."
        )
    elif n_fail:
        action = "fix_and_retry"
        guidance = ("Fix the failing spec issue(s) and re-run. Each is fixable by editing the "
                    f"AppSpec; you get max {_SAME_ERROR_CAP} attempts per distinct issue before "
                    "it auto-escalates as unfixable.")
    else:
        action = "ok"
        guidance = "All checks passed."
    return {
        "passed": passed,
        "slug": slug,
        "checked": len(results),
        "failed": n_fail,
        "warnings": n_warn,
        "action": action,
        "guidance": guidance,
        "escalations": escalations,
        "panels": results,
        "summary": (
            f"{n_data} data panel(s) + {n_forms} form(s) checked — "
            f"{n_fail} failed, {n_warn} warning(s)"
            + (f", {len(escalations)} unfixable → escalate to BA." if escalations else ".")
            if results else "no data-bearing panels or forms to check."
        ),
    }



def _interp_options_filter(
    filt: Optional[Dict[str, Any]], context: Dict[str, Any]
) -> Dict[str, Any]:
    """Replace ${record.<col>} / ${param.<name>} tokens in an OptionsSource
    filter with values from the override context (the clicked row / page
    params). Leaves non-template values as-is."""
    record = (context or {}).get("record") or {}
    params = (context or {}).get("params") or {}
    out: Dict[str, Any] = {}
    for col, val in (filt or {}).items():
        if isinstance(val, str):
            m = re.fullmatch(r"\$\{(record|param)\.([a-zA-Z0-9_]+)\}", val.strip())
            if m:
                src = record if m.group(1) == "record" else params
                resolved = src.get(m.group(2))
                if resolved is None:
                    continue  # unresolved scope → drop the predicate, not crash
                out[col] = resolved
                continue
        out[col] = val
    return out


async def _resolve_allowed_option_values(
    *,
    settings: Settings,
    app_spec: AppSpec,
    fspec: Dict[str, Any],
    payload: Dict[str, Any],
    agent_options: Optional[Dict[str, Any]],
    auth_header: Optional[str],
) -> Optional[set]:
    """The allowed string values for an editable field's OptionsSource, or
    None when the field declares no options (no membership constraint — the
    action input_schema is then the only contract). Used to REJECT an override
    value that isn't in the combo, so the dropdown is an enforced allow-list,
    not a decoration.
    """
    opts = (fspec or {}).get("options")
    if not opts:
        return None
    kind = opts.get("kind")
    if kind == "static":
        return {str(o.get("value")) for o in (opts.get("values") or [])}
    if kind == "agent":
        ag = (agent_options or {}).get(fspec.get("name")) or []
        return {str(o.get("value")) for o in ag} if ag else None
    if kind == "data_source":
        ds = next(
            (d for d in (app_spec.data_sources or []) if d.id == opts.get("data_source")),
            None,
        )
        if ds is None or not opts.get("value_column"):
            return None
        resolved = await resolve_field_options(
            settings=settings,
            ds=ds,
            value_column=opts.get("value_column"),
            label_column=opts.get("label_column"),
            field_filter=_interp_options_filter(opts.get("filter"), {"record": payload}),
            limit=opts.get("limit") or 500,
            auth_header=auth_header,
        )
        if resolved is None:
            return None
        return {str(o.get("value")) for o in resolved}
    return None


# ---------------------------------------------------------------------------
# Builder behavior gate — actually RUN each agent action on a real record
# ---------------------------------------------------------------------------
# Panel-render smoke (`/builder/preview-smoke`) proves the app DISPLAYS data.
# It cannot see agent BEHAVIOR — an action that loops, re-fetches a record it
# was already handed, runs for minutes, or fails its write. This gate fires
# each agent action once on a live test record (the exact inputs a queue-button
# click sends: the whole row) and grades the run trace. It is the half of the
# verification loop that would have caught the "Analyze Case re-fetches + loops"
# class. passed=false ⇒ do NOT hand the BA a URL.
_SMOKE_RUN_MAX_TOOL_CALLS = 6   # floor; the effective cap scales with the app's
                                # declared tools (a fraud-screened multimodal app
                                # legitimately calls photo+doc+screen+synthesis+
                                # write in ONE clean pass — 7+ calls is not a
                                # loop when each tool runs once)
_SMOKE_RUN_REPEAT_FLAG = 2      # same tool called >= this many times ⇒ re-querying
# Per-action wall-clock budget. A looping/slow run is EXACTLY what this gate
# exists to catch — but it grades the trace only AFTER execute_run returns, so
# without a cap a looping run hangs the endpoint until the CLIENT's transport
# timeout fires, returning no `runs[]` diagnosis at all (the "smoke didn't return
# a proper result" symptom). Bounding each run server-side guarantees the gate
# always returns a graded result WITH detail. Safe to cancel on timeout: the run
# is plan_only=True (no source commit), so there is nothing to corrupt.
#
# TWO budgets, by the action's resolved model tier: a REASONING model (the
# LARGE tier) legitimately spends >60s thinking before its FIRST tool call —
# grading that as "looping" is a false fail (live-hit 2026-07-20: four smoke
# retries against deepseek-v4-pro before the builder tier-switched). The gate's
# purpose is catching loops, not model think-time, so large-tier actions get
# the bigger budget; fast tiers keep a tight one.
_SMOKE_RUN_PER_ACTION_TIMEOUT_S = int(
    os.getenv("SMOKE_RUN_PER_ACTION_TIMEOUT_S", "90"))
_SMOKE_RUN_REASONING_TIMEOUT_S = int(
    os.getenv("SMOKE_RUN_REASONING_TIMEOUT_S", "240"))


def _smoke_budget_s(settings: "Settings", action: Any, agent_spec: Any) -> int:
    """The per-action smoke budget: reasoning (LARGE-resolved) tiers get the
    long budget, configured fast tiers the tight one. Resolution mirrors the
    runtime's own llm_tier_config (unconfigured medium/small fall back to
    large — and therefore to the long budget, the safe direction)."""
    tier = (getattr(action, "model_tier", None)
            or getattr(agent_spec, "model_tier", None))
    resolved = settings.llm_tier_config(tier)["tier"]
    return (_SMOKE_RUN_REASONING_TIMEOUT_S if resolved == "large"
            else _SMOKE_RUN_PER_ACTION_TIMEOUT_S)


def _find_action_trigger_panel(app_spec: "AppSpec", action_name: str) -> Optional[Any]:
    """The panel whose button fires this agent action — queue OR detail.

    That panel's record is EXACTLY what the runtime sends as the run inputs on
    a click (PanelRenderer fireAction → ``{...row}``), so a row from it is the
    realistic test input.

    Detail panels count since they gained ``actions``: an embed card fires from
    its detail panel and has no queue at all. Scanning only queues here would
    silently skip those actions — `continue` in the caller means the behaviour
    gate reports `checked:0` and passes, which reads as "graded and fine" when
    nothing was graded.
    """
    def _scan(panels: Any) -> Optional[Any]:
        for p in (panels or []):
            if getattr(p, "type", None) not in ("queue", "detail"):
                continue
            for a in (getattr(p, "actions", None) or []):
                if getattr(a, "agent_action", None) == action_name:
                    return p
        return None
    hit = _scan(app_spec.panels)
    if hit is not None:
        return hit
    for pg in (app_spec.pages or []):
        hit = _scan(pg.panels)
        if hit is not None:
            return hit
    return None


def _grade_run_trace(
    action_name: str, run_status: str, timeline: List[Dict[str, Any]],
    duration_s: float, err: Optional[str],
    max_tool_calls: int = _SMOKE_RUN_MAX_TOOL_CALLS,
) -> Dict[str, Any]:
    """Grade one agent run: error / looping / re-fetch / slow."""
    from collections import Counter
    tool_calls = [s for s in (timeline or []) if s.get("step") == "tool_call"]
    cnt = Counter((s.get("tool") or "?") for s in tool_calls)
    issues: List[Dict[str, Any]] = []
    if err or run_status in ("failed", "error"):
        issues.append({"severity": "fail", "msg": f"run failed: {err or run_status}",
                       "likely_fix": "the action errored — inspect the failing tool/step in the trace"})
    repeats = {t: c for t, c in cnt.items() if c >= _SMOKE_RUN_REPEAT_FLAG}
    if repeats:
        issues.append({
            "severity": "fail",
            "msg": f"agent re-queried the same source(s) on one record: {repeats}",
            "likely_fix": "the selected record is ALREADY in the run inputs (the runtime passes the "
                          "queue row in). Tell the agent in its system_prompt to use the provided record "
                          "and read each supplementary source at most ONCE, scoped by its ids — see "
                          "citra-agent-spec 'Write FAST system prompts'.",
        })
    if len(tool_calls) > max_tool_calls:
        issues.append({
            "severity": "fail",
            "msg": f"{len(tool_calls)} tool calls for ONE record (cap {max_tool_calls}) — the run loops / is slow",
            "likely_fix": "fewer reads: use the record from inputs, scope each read by its ids, read once, then decide",
        })
    status = "fail" if any(i["severity"] == "fail" for i in issues) else "ok"
    return {
        "action": action_name, "status": status, "run_status": run_status,
        "tool_calls": len(tool_calls), "by_tool": dict(cnt),
        "duration_s": round(duration_s, 1), "issues": issues,
    }


@app.post("/builder/smoke-run")
async def builder_smoke_run(
    slug: str,
    request: Request,
    settings: Settings = Depends(get_settings),
) -> Dict[str, Any]:
    """Behavior half of the verification loop: fire each agent action once on a
    real test record and grade the run trace (error / loop / re-fetch / slow).
    Run AFTER `/builder/preview-smoke` (which proves panels render) and BEFORE
    sharing the URL. ``passed:false`` means fix the AgentSpec and re-run."""
    user_id = get_secure_user_id(request)
    user_tenant = get_tenant_id(request)
    # BUILD-path call — the just-published app is in the test_ collections.
    _bind_build_env()
    apps = get_apps_col()
    app_doc = await apps.find_one({"slug": slug})
    if not app_doc or not _can_render_app(app_doc, request, user_id, user_tenant):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="app not found")
    app_spec = _load_app_spec(app_doc)
    auth_header = request.headers.get("authorization") or request.headers.get("Authorization")

    agent_doc = None
    if app_doc.get("agent_id"):
        agent_doc = await get_agents_col().find_one(
            {"agent_id": app_doc["agent_id"], "tenant_id": app_doc.get("tenant_id")}
        )
    if not agent_doc:
        return {"passed": True, "slug": slug, "checked": 0, "runs": [],
                "note": "no agent bound — no actions to run"}
    agent_spec = AgentSpec.model_validate(agent_doc["agent_spec"])

    runs: List[Dict[str, Any]] = []
    for action in (agent_spec.actions or []):
        panel = _find_action_trigger_panel(app_spec, action.name)
        if panel is None:
            continue  # not a button action (form on_submit is covered by preview-smoke)
        # Fetch one real record — exactly what a button click sends as inputs.
        try:
            if getattr(panel, "type", None) == "detail":
                # A detail panel resolves ONE record, not a list, and through a
                # different resolver. record_id=None leaves the {param.id}
                # filter unset, which _interp_filters drops — so this reads the
                # first available record, which is what we want for a probe.
                ddr = await resolve_detail_data(
                    settings=settings, app_spec=app_spec, panel_id=panel.id,
                    record_id=None, auth_header=auth_header,
                    app_id=app_doc.get("app_id"),
                )
                rows = [ddr.record] if getattr(ddr, "record", None) else []
            else:
                resp = await resolve_panel_data(
                    settings=settings, app_spec=app_spec,
                    panel_id=panel.id, auth_header=auth_header,
                )
                rows = resp.rows or []
        except Exception as e:  # noqa: BLE001
            runs.append({"action": action.name, "status": "fail", "run_status": "no_data",
                         "tool_calls": 0, "by_tool": {}, "duration_s": 0.0,
                         "issues": [{"severity": "fail",
                                     "msg": f"could not fetch a test record from '{panel.data_source}': {e}",
                                     "likely_fix": "fix the backing queue data_source first (see preview-smoke)"}]})
            continue
        if not rows:
            runs.append({"action": action.name, "status": "warn", "run_status": "empty",
                         "tool_calls": 0, "by_tool": {}, "duration_s": 0.0,
                         "issues": [{"severity": "warn", "msg": "no test rows to run the action on",
                                     "likely_fix": "seed a test row or verify the source — can't prove behavior on empty data"}]})
            continue
        started = datetime.now(timezone.utc)
        _budget_s = _smoke_budget_s(settings, action, agent_spec)
        try:
            response = await asyncio.wait_for(
                execute_run(
                    settings=settings, app_spec=app_spec, agent_spec=agent_spec,
                    request=RunRequest(action=action.name, inputs=dict(rows[0]), mode="queue_action"),
                    auth_header=auth_header, plan_only=True,
                ),
                timeout=_budget_s,
            )
            dur = (datetime.now(timezone.utc) - started).total_seconds()
            # Cap scales with the spec: one clean pass may call each declared
            # tool once (+2 slack for a second artifact/read) — repeats are
            # still flagged separately, so looping stays caught.
            _cap = max(_SMOKE_RUN_MAX_TOOL_CALLS,
                       len(getattr(agent_spec, "tools_v2", None) or []) + 2)
            runs.append(_grade_run_trace(
                action.name, response.status, response.timeline or [], dur, None,
                max_tool_calls=_cap))
        except asyncio.TimeoutError:
            # The run blew the per-action budget — the SLOW/LOOPING defect this
            # gate exists to catch. Grade it as a fail WITH detail instead of
            # letting the endpoint hang until the client gives up (which returned
            # a bare passed:false with no runs[]). plan_only=True ⇒ cancelling the
            # in-flight run commits nothing.
            dur = (datetime.now(timezone.utc) - started).total_seconds()
            runs.append({
                "action": action.name, "status": "fail", "run_status": "timeout",
                "tool_calls": 0, "by_tool": {}, "duration_s": round(dur, 1),
                "issues": [{
                    "severity": "fail",
                    "msg": f"run exceeded {_budget_s}s on ONE record — too slow / likely looping",
                    "likely_fix": "the agent is re-reading sources or looping. The queue row is already passed in "
                                  "as the run inputs — tell the system_prompt to USE that record, scope each "
                                  "supplementary read by its ids, read once, then decide "
                                  "(see citra-agent-spec 'Write FAST system prompts'). If the trace shows ZERO "
                                  "tool calls, the model spent the budget in internal reasoning — this budget is "
                                  "already tier-aware, so treat that as a genuinely stuck model, not a prompt bug.",
                }],
            })
        except Exception as e:  # noqa: BLE001 — a run that raises IS a behavior failure
            dur = (datetime.now(timezone.utc) - started).total_seconds()
            runs.append(_grade_run_trace(action.name, "error", [], dur, str(e)))

    passed = all(r["status"] != "fail" for r in runs)
    return {
        "passed": passed, "slug": slug, "checked": len(runs), "runs": runs,
        "action": "ok" if passed else "fix_and_retry",
        "guidance": ("Every checked action ran cleanly." if passed else
                     "A failed run is fixable in the AgentSpec (usually the system_prompt re-fetches "
                     "the provided record, or reads aren't scoped). Fix and re-run; ≤3 attempts per issue."),
    }


@app.post("/apps/{slug}/field-options")
async def get_field_options(
    slug: str,
    request: Request,
    settings: Settings = Depends(get_settings),
) -> Dict[str, Any]:
    """Resolve the choices for a combo box — for a FORM field or an
    approve-time override field.

    Body: ``{field, context, ...}`` plus EITHER ``action_id`` (override field
    on an mcp_action's editable_fields) OR ``panel_id`` (a form field's
    ``options_source``). The server locates the source in the PUBLISHED spec
    (so the client can never request an arbitrary column) and resolves it:
    static → the literal values; data_source → live DISTINCT column values
    scoped by the (interpolated) filter; agent → empty (those come inline in
    planned_writes._options).
    """
    user_id = get_secure_user_id(request)
    user_tenant = get_tenant_id(request)
    # Resolve test↔prod by store — combo options for a test app resolve their
    # DISTINCT values via the TEST MCP (see _bind_app_env).
    await _bind_app_env(slug)
    body = await request.json()
    field = (body or {}).get("field")
    source_id = (body or {}).get("source_id")
    dataset_id = (body or {}).get("dataset_id")
    action_id = (body or {}).get("action_id")
    panel_id = (body or {}).get("panel_id")
    context = (body or {}).get("context") or {}
    # Typeahead search term (options_source.search fields). Empty/absent → full
    # DISTINCT list (existing behaviour).
    q = (body or {}).get("q")
    if not field or not (action_id or panel_id):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="field + (action_id or panel_id) required",
        )

    app_doc = await get_apps_col().find_one({"slug": slug})
    if not app_doc or not _can_render_app(app_doc, request, user_id, user_tenant):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="app not found")
    app_spec = _load_app_spec(app_doc)

    agent_doc = await get_agents_col().find_one({"agent_id": app_doc.get("agent_id")})
    if not agent_doc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="agent not found")
    agent_spec = AgentSpec.model_validate(agent_doc["agent_spec"])

    # ── FORM-field path: the field's options_source lives on the FormPanel's
    # published schema (schema_inline.properties[field].options_source). The
    # server reads it from the stored spec so the client can't point the combo
    # at an arbitrary column.
    if panel_id:
        panel = next(
            (p for p in app_spec.all_panels if getattr(p, "id", None) == panel_id),
            None,
        )
        # ── filter_bar control path: the options_source lives on the control
        # whose ``param`` matches ``field``. Resolve it exactly like a form
        # field's options_source (static list or live DISTINCT from a source).
        if panel is not None and getattr(panel, "type", None) == "filter_bar":
            ctrl = next(
                (c for c in (getattr(panel, "controls", None) or [])
                 if getattr(c, "param", None) == field),
                None,
            )
            osrc = getattr(ctrl, "options", None) if ctrl is not None else None
            if osrc is None:
                return {"options": []}
            if osrc.kind == "static":
                return {"options": [
                    {"value": o.value, "label": o.label or o.value}
                    for o in (osrc.values or [])
                ]}
            if osrc.kind == "data_source" and osrc.value_column:
                ds = next(
                    (d for d in (app_spec.data_sources or []) if d.id == osrc.data_source),
                    None,
                )
                if ds is None:
                    return {"options": []}
                auth_header = request.headers.get("authorization") or request.headers.get("Authorization")
                resolved = await resolve_field_options(
                    settings=settings,
                    ds=ds,
                    value_column=osrc.value_column,
                    label_column=osrc.label_column,
                    field_filter=_interp_options_filter(osrc.filter, context),
                    limit=osrc.limit,
                    auth_header=auth_header,
                    search=q if osrc.search else None,
                )
                return {"options": resolved or []}
            return {"options": []}
        schema = getattr(panel, "schema_inline", None) if panel is not None else None
        if (not schema) and panel is not None and getattr(panel, "schema_ref", None) == "agent.input_schema":
            schema = getattr(agent_spec, "input_schema", None)
        fdef = ((schema or {}).get("properties") or {}).get(field) or {}
        osrc = fdef.get("options_source") or {}
        kind = osrc.get("kind")
        if kind == "static":
            return {"options": [
                {"value": o.get("value"), "label": o.get("label") or o.get("value")}
                for o in (osrc.get("values") or [])
                if isinstance(o, dict)
            ]}
        if kind == "data_source" and osrc.get("value_column"):
            ds = next(
                (d for d in (app_spec.data_sources or []) if d.id == osrc.get("data_source")),
                None,
            )
            if ds is None:
                return {"options": []}
            auth_header = request.headers.get("authorization") or request.headers.get("Authorization")
            resolved = await resolve_field_options(
                settings=settings,
                ds=ds,
                value_column=osrc.get("value_column"),
                label_column=osrc.get("label_column"),
                field_filter=_interp_options_filter(osrc.get("filter"), context),
                limit=osrc.get("limit"),
                auth_header=auth_header,
                search=q if osrc.get("search") else None,
            )
            # RULE #1: distinguish "no rows" ([]) from "query failed" (None).
            # Masking a failure as an empty allow-list silently breaks the combo
            # (and could wrongly reject a valid override on the approve path).
            if resolved is None:
                raise HTTPException(
                    status.HTTP_502_BAD_GATEWAY,
                    detail=f"could not load options for field '{field}' from "
                           f"data source '{osrc.get('data_source')}'",
                )
            return {"options": resolved}
        return {"options": []}

    # ── Override-field path: locate the mcp_action tool + the field's declared
    # FieldSpec/OptionsSource.
    fspec = None
    for t in (agent_spec.tools_v2 or []):
        if getattr(t, "kind", None) != "mcp_action":
            continue
        if getattr(t, "action_id", None) != action_id:
            continue
        if source_id and getattr(t, "source_id", None) != source_id:
            continue
        if dataset_id and getattr(t, "dataset_id", None) != dataset_id:
            continue
        fspec = next((f for f in (t.editable_fields or []) if f.name == field), None)
        if fspec:
            break
    if fspec is None or fspec.options is None:
        return {"options": []}

    opts = fspec.options
    if opts.kind == "static":
        return {"options": [
            {"value": o.value, "label": o.label} for o in (opts.values or [])
        ]}
    if opts.kind == "data_source":
        ds = next(
            (d for d in (app_spec.data_sources or []) if d.id == opts.data_source),
            None,
        )
        if ds is None or not opts.value_column:
            return {"options": []}
        auth_header = request.headers.get("authorization") or request.headers.get("Authorization")
        resolved = await resolve_field_options(
            settings=settings,
            ds=ds,
            value_column=opts.value_column,
            label_column=opts.label_column,
            field_filter=_interp_options_filter(opts.filter, context),
            limit=opts.limit,
            auth_header=auth_header,
            search=q if getattr(opts, "search", False) else None,
        )
        if resolved is None:
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY,
                detail=f"could not load override options for field '{field}'",
            )
        return {"options": resolved}
    # kind == "agent" → options arrive inline via planned_writes._options
    return {"options": []}


# ---------------------------------------------------------------------------
# AI triggers — fire the app's OWN agent on a schedule / webhook / poll
# (app_spec.triggers[]). The agent runs ahead of the click and stages a
# recommendation into the officer inbox; the officer approves to commit.
# ---------------------------------------------------------------------------


def _validate_cron_expr(expr: Optional[str], *, min_interval_seconds: int) -> None:
    """Raise HTTPException(422) if ``expr`` is an unparseable cron OR fires more
    often than the C-03 floor. Computes the gap between the next two fire slots.
    A blank/None cron is allowed (clearing it). Called at the config boundary so
    a fat-fingered or too-tight cron is rejected with feedback — not silently
    accepted and then never (or too-often) fired."""
    from croniter import croniter  # local import: croniter is an optional dep

    expr = (expr or "").strip()
    if not expr:
        return
    base = datetime(2024, 1, 1, tzinfo=timezone.utc)
    try:
        itr = croniter(expr, base)
        t1 = itr.get_next(datetime)
        t2 = itr.get_next(datetime)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"invalid cron expression '{expr}': {e}",
        )
    gap = (t2 - t1).total_seconds()
    if gap < min_interval_seconds:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"cron '{expr}' fires every {int(gap)}s but the minimum is "
                f"{min_interval_seconds}s (rule C-03) — use a webhook trigger "
                f"for near-real-time."
            ),
        )


# ── Kill switches: halt (global/org/dept) + per-app pause ────────────────────
class HaltRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope_type: str  # "global" | "org" | "dept"
    scope_id: Optional[str] = None  # required for org/dept
    enabled: bool
    reason: Optional[str] = ""


class PauseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: Optional[str] = ""


@app.get("/admin/halt")
async def list_halt_controls(request: Request) -> Dict[str, Any]:
    """Active halts/pauses (global/org/dept/app) — powers the banner + console."""
    roles = set(get_user_roles(request) or [])
    if not (roles & {"super_admin", "org_admin", "dept_admin", "decision-app-builder"}):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="admin role required")
    controls = await automation_control.list_controls(get_control_col())
    # Scope: super sees everything; everyone else sees only global + their own
    # org / dept controls + app pauses on apps in their org. Prevents leaking
    # other tenants' halt reasons/actors on a multi-org hub box.
    if "super_admin" not in roles:
        org = get_tenant_id(request) or get_org_id(request) or ""
        dept_prefix = f"{org}:"
        app_slugs = [c.get("scope_id") for c in controls if c.get("scope_type") == "app"]
        my_app_slugs: set = set()
        if app_slugs and org:
            try:
                docs = await get_apps_col().find(
                    {"slug": {"$in": app_slugs}, "tenant_id": org}, {"slug": 1}
                ).to_list(length=2000)
                my_app_slugs = {d.get("slug") for d in docs}
            except Exception:
                my_app_slugs = set()
        controls = [
            c for c in controls
            if c.get("scope_type") == "global"
            or (c.get("scope_type") == "org" and c.get("scope_id") == org)
            or (c.get("scope_type") == "dept" and str(c.get("scope_id") or "").startswith(dept_prefix))
            or (c.get("scope_type") == "app" and c.get("scope_id") in my_app_slugs)
        ]
    return {"controls": controls}


@app.post("/admin/halt")
async def set_halt_control(payload: HaltRequest, request: Request) -> Dict[str, Any]:
    """Set/clear a halt at global / org / dept scope — the RED BUTTON + dept freeze.

    global & org → org_admin/super_admin · dept → dept_admin of that dept (or
    org_admin/super_admin). Fail-loud on authz."""
    roles = set(get_user_roles(request) or [])
    st = payload.scope_type
    if st not in ("global", "org", "dept"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="scope_type must be global|org|dept")
    # tenant_id is the canonical org identifier apps carry — key halts on it so
    # the stored org/dept scope matches the enforcement side (_app_dept_ids).
    org_id = get_tenant_id(request) or get_org_id(request)
    dept_ids = set(get_user_dept_ids(request) or [])
    is_super = "super_admin" in roles
    is_org = "org_admin" in roles
    if st == "global":
        # Global freezes EVERY org — super_admin only (an org_admin must not be
        # able to halt other tenants on a multi-org hub box).
        if not is_super:
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="global halt requires super_admin")
        scope_id = None
    elif st == "org":
        if not (is_super or is_org):
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="org halt requires org_admin")
        if not is_super and payload.scope_id not in (None, org_id):
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="can only halt your own org")
        scope_id = payload.scope_id or org_id
    else:  # dept — scope_id is ORG-QUALIFIED: "<org_id>:<dept_id>"
        sid = payload.scope_id or ""
        if ":" not in sid:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail="dept scope_id must be '<org_id>:<dept_id>' (dept ids are not unique across orgs)",
            )
        d_org, d_dept = sid.split(":", 1)
        if is_super:
            pass  # any org's dept
        elif is_org:
            if d_org != org_id:
                raise HTTPException(status.HTTP_403_FORBIDDEN, detail="can only halt departments in your org")
        elif "dept_admin" in roles and d_org == org_id and d_dept in dept_ids:
            pass
        else:
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="can only halt a dept you administer")
        scope_id = sid
    actor = get_secure_user_id(request) or "unknown"
    doc = await automation_control.set_control(
        get_control_col(), scope_type=st, scope_id=scope_id,
        enabled=bool(payload.enabled), actor=actor, reason=payload.reason or "",
    )
    logger.warning(
        "[kill-switch] %s scope=%s:%s by=%s reason=%s",
        "HALT" if payload.enabled else "CLEAR", st, scope_id, actor, payload.reason,
    )
    return {"ok": True, "control": doc}


async def _set_app_pause(slug: str, request: Request, paused: bool, reason: str) -> Dict[str, Any]:
    user_id = get_secure_user_id(request)
    user_tenant = get_tenant_id(request)
    await _bind_app_env(slug)
    app_doc = await get_apps_col().find_one({"slug": slug})
    if not app_doc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="app not found")
    if not _can_edit_app(
        app_doc, user_id, user_tenant,
        user_org_id=get_org_id(request), user_dept_ids=get_user_dept_ids(request),
        user_roles=get_user_roles(request), sa_admin_of=get_sa_admin_of(request),
    ):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="not allowed to pause this app")
    actor = user_id or "unknown"
    doc = await automation_control.set_control(
        get_control_col(), scope_type="app", scope_id=slug,
        enabled=paused, actor=actor, reason=reason,
    )
    logger.warning("[kill-switch] app %s %s by=%s", slug, "PAUSED" if paused else "RESUMED", actor)
    return {"ok": True, "paused": paused, "control": doc}


@app.post("/apps/{slug}/pause")
async def pause_app(slug: str, payload: PauseRequest, request: Request) -> Dict[str, Any]:
    """Pause ONE app: stop runs/writes/automation, keep reads + audit. Owner
    (edit-grade) or admin. The everyday per-app kill switch."""
    return await _set_app_pause(slug, request, True, payload.reason or "")


@app.post("/apps/{slug}/resume")
async def resume_app(slug: str, request: Request) -> Dict[str, Any]:
    """Resume a paused app."""
    return await _set_app_pause(slug, request, False, "")


def _require_learning_admin(request: Request) -> set:
    """Admin roles allowed to see/steer the learning batch. Same role set the
    halt console uses — an operator who can stop automation can also stop the
    job that folds officer feedback into learned rules."""
    roles = set(get_user_roles(request) or [])
    if not (roles & {"super_admin", "org_admin", "dept_admin", "decision-app-builder"}):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="admin role required")
    return roles


@app.get("/org/memory-impact")
async def org_memory_impact(request: Request) -> Dict[str, Any]:
    """One-line answer to "is the learned memory worth anything?" (plan §19.2).

    Returns the size of the asset (judgements, the evidence behind them, apps
    covered), the LIFT — acceptance when a learned judgement applied vs when
    none did — and the count of judgements BLOCKED on a human decision. Powers
    the App Memory card subtitle and its attention badge.

    The lift is SUPPRESSED, not fudged, when either cohort is under-powered —
    an unearned number on an admin card is worse than a blank, and this is
    exactly the card someone screenshots.
    """
    roles = set(get_user_roles(request) or [])
    if not (roles & {"super_admin", "org_admin", "dept_admin",
                     "decision-app-builder"}):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="admin role required")

    tenant = get_tenant_id(request) or get_org_id(request)
    out: Dict[str, Any] = {
        "clauses_active": 0, "clauses_candidate": 0, "corrections": 0,
        "apps_with_memory": 0, "lift": None, "lift_note": None,
        # Judgements STUCK until a human decides. None (not 0) until counted:
        # a read failure must never render as "all clear" on the badge.
        "needs_attention": None, "sop_conflict": 0, "dissented": 0,
        "attention_apps": [],
    }
    try:
        import clause_store as cls
        import corrections as cx

        # Reuse the store's own reader rather than an inline aggregation: the
        # counting rules (which statuses are "in use") then live in ONE place
        # and cannot drift from what the clause list shows.
        q = {"tenant_id": tenant} if tenant else {}
        rows = await cls._col().find(
            q, {"_id": 0, "status": 1, "app_slug": 1}).to_list(20_000)
        apps: set = set()
        # Blocked = suspended over an SOP conflict, or disputed by the officers
        # themselves. Both are inert until somebody rules on them, and neither
        # surfaces anywhere except inside the Memory screen — which is exactly
        # why the card has to carry the count. QUARANTINED is deliberately NOT
        # here: an admin already decided that, so it is not waiting on anyone.
        attention_apps: set = set()
        for r in rows:
            st = r.get("status")
            if st == "active":
                out["clauses_active"] += 1
                if r.get("app_slug"):
                    apps.add(r["app_slug"])
            elif st == "candidate":
                out["clauses_candidate"] += 1
            elif st in ("sop_conflict", "dissented"):
                out["sop_conflict" if st == "sop_conflict" else "dissented"] += 1
                if r.get("app_slug"):
                    attention_apps.add(r["app_slug"])
        out["needs_attention"] = out["sop_conflict"] + out["dissented"]
        out["attention_apps"] = sorted(attention_apps)
        out["apps_with_memory"] = len(apps)
        out["corrections"] = len(
            await cx._col().find(q, {"_id": 0, "correction_id": 1}).to_list(100_000))
    except Exception:  # noqa: BLE001 — card enrichment; loud, never 500s
        logger.exception("[memory-impact] asset counts failed")

    # Lift: acceptance WITH a fired clause vs acceptance with none. Same cohort
    # discipline as the Success Rate card — auto_process is excluded entirely
    # (it is configured, not earned), and human_direct has no recommendation to
    # accept or reject.
    try:
        cohorts = {"with_clauses": {"accepted": 0, "disposed": 0},
                   "cold": {"accepted": 0, "disposed": 0}}
        dq: Dict[str, Any] = {"mode": {"$in": ["human_approved", "human_rejected"]}}
        if tenant:
            dq["tenant_id"] = tenant
        async for r in get_decision_records_col().find(
            dq, {"_id": 0, "mode": 1, "overrides": 1, "injected_clause_ids": 1},
        ):
            key = ("with_clauses" if (r.get("injected_clause_ids") or [])
                   else "cold")
            cohorts[key]["disposed"] += 1
            if r.get("mode") == "human_approved" and not (r.get("overrides") or []):
                cohorts[key]["accepted"] += 1

        wc, cold = cohorts["with_clauses"], cohorts["cold"]
        out["cohorts"] = cohorts
        if wc["disposed"] >= _LIFT_MIN_COHORT and cold["disposed"] >= _LIFT_MIN_COHORT:
            out["lift"] = round(
                wc["accepted"] / wc["disposed"] - cold["accepted"] / cold["disposed"], 3)
        else:
            out["lift_note"] = (
                f"not enough decided cases in both cohorts yet "
                f"(with rules: {wc['disposed']}, without: {cold['disposed']}; "
                f"need {_LIFT_MIN_COHORT} each)")
    except Exception:  # noqa: BLE001 — enrichment; loud, never 500s
        logger.exception("[memory-impact] lift cohorts failed")
        out["lift_note"] = "could not compute — see service logs"
    return out


@app.get("/admin/consolidation")
async def admin_consolidation_status(request: Request) -> Dict[str, Any]:
    """Learning batch console (docs/clause-memory-graph-plan.md §9.1, §19).

    Shows the pending-correction queue, which buckets are over threshold, when
    the job last ran and what it did. This is the operator's view of the job
    that REPLACED the per-reject synchronous summarizer — the work moved off
    the officer's request path, so it needs a place to be visible."""
    _require_learning_admin(request)
    from consolidation import consolidation_status

    return await consolidation_status()


@app.post("/admin/consolidation/pause")
async def admin_consolidation_pause(
    payload: PauseRequest, request: Request, paused: bool = True,
) -> Dict[str, Any]:
    """Pause/resume clause consolidation.

    Pausing is SAFE and lossless: corrections keep accumulating with
    ``consumed_by: null`` and fold on the next unpaused pass. Nothing an officer
    does changes — only the derived clause layer stops advancing. Deliberately
    NOT part of the operations kill switches (see consolidation._control_col)."""
    _require_learning_admin(request)
    from consolidation import set_paused

    actor = get_secure_user_id(request) or "unknown"
    doc = await set_paused(paused=bool(paused), actor=actor,
                           reason=payload.reason or "")
    return {"ok": True, "control": doc}


@app.post("/admin/consolidation/run")
async def admin_consolidation_run(request: Request) -> Dict[str, Any]:
    """Run one consolidation pass NOW, bypassing the count/age thresholds.

    Runs inline (not queued) so the operator sees the result. Honours the pause
    flag — 'Run now' on a paused job would make the pause a lie."""
    _require_learning_admin(request)
    from consolidation import run_consolidation_pass

    actor = get_secure_user_id(request) or "unknown"
    logger.info("[CONSOLIDATE] manual pass requested by %s", actor)
    totals = await run_consolidation_pass(force=True)
    return {"ok": True, "result": totals}


@app.get("/admin/automation")
async def admin_automation(request: Request) -> Dict[str, Any]:
    """Fleet control-panel view: every scheduled/triggered app in the admin's
    scope, with each trigger's mode, enabled state, schedule, next_run + last_run
    — so an admin can SEE and control what runs and when. Scope: super_admin →
    all orgs; org_admin → their org; dept_admin → their dept(s)."""
    from datetime import timedelta
    roles = set(get_user_roles(request) or [])
    if not (roles & {"super_admin", "org_admin", "dept_admin"}):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="admin role required")
    is_super = "super_admin" in roles
    is_org = "org_admin" in roles
    org_id = get_tenant_id(request) or get_org_id(request)
    dept_ids = list(get_user_dept_ids(request) or [])

    query: Dict[str, Any] = {"status": {"$ne": AppStatus.ARCHIVED.value}}
    if not is_super:
        query["tenant_id"] = org_id
        if not is_org:  # dept_admin — restrict to apps tagged with their depts
            query["app_spec.dept_ids"] = {"$in": dept_ids}
    docs = await get_apps_col().find(query).sort("deployed_at", -1).to_list(length=1000)

    # Join agents so LEARNING jobs (outcome-poll + grounding refresh) show up
    # alongside execution triggers — those live on agent_spec, not app_spec.triggers.
    _agent_ids = [d.get("agent_id") for d in docs if d.get("agent_id")]
    agent_by_id: Dict[str, dict] = {}
    if _agent_ids:
        try:
            for ag in await get_agents_col().find(
                {"agent_id": {"$in": _agent_ids}}
            ).to_list(length=2000):
                agent_by_id[ag.get("agent_id")] = ag.get("agent_spec") or {}
        except Exception as exc:  # noqa: BLE001 — degrade the panel, but LOUD (else learning rows silently vanish)
            logger.error("[admin_automation] agent join FAILED — learning jobs hidden: %s", exc)
            agent_by_id = {}
    try:
        from grounding_runs import get_grounding_run_store
        _gstore = get_grounding_run_store()
    except Exception as exc:  # noqa: BLE001 — Redis unavailable; grounding freshness omitted, logged
        logger.error("[admin_automation] grounding store unavailable — freshness omitted: %s", exc)
        _gstore = None

    try:
        controls = await automation_control.list_controls(get_control_col())
    except Exception:
        controls = []
    paused_slugs = {c.get("scope_id") for c in controls if c.get("scope_type") == "app"}

    runs_col = get_trigger_runs_col()
    now = datetime.now(timezone.utc)

    def _human(t: dict) -> str:
        ty = t.get("type")
        if ty == "schedule.cron":
            return f"cron · {t.get('cron')}"
        if ty == "schedule.interval":
            return f"every {t.get('every_seconds') or 0}s"
        if ty == "poll":
            return f"poll {t.get('tool')} · every {t.get('every_seconds') or 0}s"
        if ty == "webhook":
            return "on webhook (event-driven)"
        return ty or "?"

    def _next_run(t: dict) -> Optional[str]:
        ty = t.get("type")
        try:
            if ty == "schedule.cron" and t.get("cron"):
                from croniter import croniter
                return croniter(t["cron"], now).get_next(datetime).isoformat()
            if ty in ("schedule.interval", "poll") and t.get("every_seconds"):
                return (now + timedelta(seconds=int(t["every_seconds"]))).isoformat()
        except Exception:
            return None
        return None

    out: List[dict] = []
    for d in docs:
        spec = d.get("app_spec") or {}
        slug = d.get("slug")
        headless = bool(spec.get("headless"))
        has_dash = any((p or {}).get("kind") == "dashboard" for p in (spec.get("pages") or []))
        kind = "api" if headless else ("dashboard" if has_dash else "app")
        trigs: List[dict] = []
        for t in (spec.get("triggers") or []):
            tid = t.get("id")
            last = None
            try:
                lr = await runs_col.find({"slug": slug, "trigger_id": tid}).sort("created_at", -1).to_list(length=1)
                if lr:
                    last = {"at": lr[0].get("created_at"), "status": lr[0].get("status"), "fired_via": lr[0].get("fired_via")}
            except Exception:
                last = None
            trigs.append({
                "id": tid, "type": t.get("type"), "action": t.get("action"),
                "execution_mode": t.get("execution_mode", "recommend"),
                "autonomous": t.get("execution_mode") == "auto_process",
                "enabled": bool(t.get("enabled")),
                "cron": t.get("cron"), "every_seconds": t.get("every_seconds"), "tz": t.get("tz"),
                "tool": t.get("tool"),
                "schedule_human": _human(t), "next_run": _next_run(t), "last_run": last,
            })
        # LEARNING jobs (from agent_spec) — outcome poll + grounding refresh.
        aspec = agent_by_id.get(d.get("agent_id")) or {}
        op = aspec.get("outcome_poll") or {}
        learning: List[dict] = []
        if op.get("enabled"):
            _wd = op.get("window_days", 7.0)
            learning.append({
                "type": "outcome_poll", "enabled": True,
                "auto_learn": bool(op.get("auto_refresh")),
                "window_days": _wd, "table": op.get("table"),
                "schedule_human": f"outcome poll · settles after {_wd}d",
            })
        if aspec.get("grounding"):
            _last = None
            if _gstore is not None:
                try:
                    _last = _gstore.get_last(slug)
                except Exception as exc:  # noqa: BLE001 — per-app freshness read; degrade + log
                    logger.warning("[admin_automation] grounding freshness read failed slug=%s: %s", slug, exc)
                    _last = None
            learning.append({
                "type": "grounding_refresh",
                "auto_learn": bool(op.get("auto_refresh")),
                "never_refreshed": _last is None,
                "last_refreshed_at": (_last or {}).get("last_refreshed_at"),
                "sample_count": (_last or {}).get("sample_count"),
                "schedule_human": (
                    "auto (weekly + on outcome)" if op.get("auto_refresh") else "manual only"
                ),
            })
        out.append({
            "slug": slug, "title": d.get("title") or spec.get("title") or slug,
            "kind": kind, "status": d.get("status"), "paused": slug in paused_slugs,
            "org_id": d.get("tenant_id"), "dept_ids": spec.get("dept_ids") or [],
            "headless": headless, "version": d.get("version"),
            "triggers": trigs, "learning": learning,
        })

    # Recent-runs rollup: per-app stats + an incidents feed (failures /
    # dead-letters). One bulk query over the scoped slugs.
    slugs = [a["slug"] for a in out]
    title_by_slug = {a["slug"]: a["title"] for a in out}
    incidents: List[dict] = []
    try:
        recent = await runs_col.find({"slug": {"$in": slugs}}).sort("created_at", -1).to_list(length=500)
    except Exception:
        recent = []
    stats: Dict[str, dict] = {}
    for r in recent:
        s = r.get("slug")
        st = r.get("status")
        agg = stats.setdefault(s, {"runs": 0, "failures": 0})
        agg["runs"] += 1
        if st in ("failed", "dead_letter"):
            agg["failures"] += 1
            if len(incidents) < 30:
                err = r.get("error")
                incidents.append({
                    "slug": s, "title": title_by_slug.get(s, s),
                    "trigger_id": r.get("trigger_id"), "status": st,
                    "at": r.get("created_at"), "fired_via": r.get("fired_via"),
                    "error": err.get("message") if isinstance(err, dict) else err,
                })
    for a in out:
        a["stats"] = stats.get(a["slug"], {"runs": 0, "failures": 0})

    # Fleet-level system jobs — the global background loops (not per-app), so the
    # admin can SEE the learning schedule that drives every app's outcome poll +
    # weekly grounding rebuild.
    _sched_on = os.getenv("SCHEDULER_ENABLED", "").lower() in ("1", "true", "yes")
    _last_tick = None
    try:
        if _gstore is not None:
            _last_tick = _gstore.cache.get("scheduler:last_tick") or None
    except Exception as exc:  # noqa: BLE001 — informational stamp; degrade + log
        logger.warning("[admin_automation] scheduler last-tick read failed: %s", exc)
        _last_tick = None
    system_jobs = [
        {
            "type": "outcome_poll_scheduler", "enabled": _sched_on,
            "schedule_human": f"every {os.getenv('SCHEDULER_TICK_SECONDS', '30')}s (leader-elected)",
            "last_tick_at": _last_tick,
            "note": "reads settled decisions back from source systems and labels good/bad/neutral",
        },
        {
            "type": "grounding_rebuild", "enabled": _sched_on,
            "schedule_human": f"every {os.getenv('GROUNDING_FULL_REFRESH_DAYS', '7')}d (auto-learn apps only)",
            "note": "weekly few-shot memory rebuild for apps with auto-learn on",
        },
    ]
    return {"apps": out, "incidents": incidents, "system_jobs": system_jobs}


@app.get("/automation-status")
async def automation_status(request: Request) -> Dict[str, Any]:
    """PUBLIC (any authenticated user): is automation halted for the caller's
    context? Drives the non-admin halt banner. Checks global / org / dept
    (org-qualified) — not app-scope."""
    org_id = get_tenant_id(request) or get_org_id(request)
    dept_tokens = [f"{org_id}:{d}" for d in (get_user_dept_ids(request) or [])]
    reason = await automation_control.get_halt(
        get_control_col(), org_id=org_id, dept_ids=dept_tokens, slug=None
    )
    if reason:
        return {
            "halted": True, "scope": reason.get("scope_type"),
            "reason": reason.get("reason"), "actor": reason.get("actor"),
            "since": reason.get("updated_at"),
        }
    return {"halted": False}


def _ai_trigger_view(t: "Trigger", slug: str, settings: Settings) -> Dict[str, Any]:
    is_webhook = t.type == "webhook"
    base = (settings.smart_app_service_callback_url or "").rstrip("/")
    return {
        "id": t.id,
        "type": t.type,
        "action": t.action,
        "enabled": t.enabled,
        "cron": t.cron,
        "every_seconds": t.every_seconds,
        "tz": t.tz,
        "tool": t.tool,
        "dedup_key": t.dedup_key,
        # Webhook callers POST here (HMAC-signed with the secret behind secret_ref).
        "webhook_url": (f"{base}/apps/{slug}/trigger/{t.id}" if is_webhook and base else None),
        "webhook_path": (f"/apps/{slug}/trigger/{t.id}" if is_webhook else None),
        "secret_ref": t.secret_ref if is_webhook else None,
        "has_secret": bool(resolve_secret(t.secret_ref)) if is_webhook else None,
        # Truth for the UI: whether this trigger COMMITS autonomously and its
        # policy. Without these the panel can't tell recommend from auto-process
        # and (wrongly) tells the admin "nothing is committed automatically".
        "execution_mode": getattr(t, "execution_mode", "recommend"),
        "autonomous": getattr(t, "execution_mode", "recommend") == "auto_process",
        "auto_process_policy": (
            t.auto_process_policy.model_dump()
            if getattr(t, "auto_process_policy", None) is not None
            and hasattr(t.auto_process_policy, "model_dump")
            else getattr(t, "auto_process_policy", None)
        ),
    }


@app.get("/apps/{slug}/ai-triggers")
async def get_ai_triggers(
    slug: str, request: Request, settings: Settings = Depends(get_settings),
) -> Dict[str, Any]:
    """List the app's AI triggers (app_spec.triggers) — schedule / webhook / poll
    that fire the app's agent to precompute a recommendation."""
    user_id = get_secure_user_id(request)
    user_tenant = get_tenant_id(request)
    await _bind_app_env(slug)  # resolve test↔prod by store (see _bind_app_env)
    app_doc = await get_apps_col().find_one({"slug": slug})
    if not app_doc or not _can_render_app(app_doc, request, user_id, user_tenant):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="app not found")
    app_spec = _load_app_spec(app_doc)
    return {
        "triggers": [_ai_trigger_view(t, slug, settings) for t in (app_spec.triggers or [])]
    }


class AiTriggerUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: Optional[bool] = None
    cron: Optional[str] = None
    every_seconds: Optional[int] = Field(default=None, ge=30, le=86_400)


@app.patch("/apps/{slug}/ai-triggers/{trigger_id}")
async def update_ai_trigger(
    slug: str,
    trigger_id: str,
    body: AiTriggerUpdate,
    request: Request,
    settings: Settings = Depends(get_settings),
) -> Dict[str, Any]:
    """Enable/disable or retune one AI trigger. The officer activates a trigger
    here (they publish DEACTIVATED). Edit-grade — same authz as editing the app.
    Updates the matching ``app_spec.triggers[]`` entry in place."""
    user_id = get_secure_user_id(request)
    user_tenant = get_tenant_id(request)
    await _bind_app_env(slug)  # resolve test↔prod by store (see _bind_app_env)
    app_doc = await get_apps_col().find_one({"slug": slug})
    if not app_doc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="app not found")
    if not _can_edit_app(
        app_doc, user_id, user_tenant,
        user_org_id=get_org_id(request),
        user_dept_ids=get_user_dept_ids(request),
        user_roles=get_user_roles(request),
        sa_admin_of=get_sa_admin_of(request),
    ):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="not allowed to edit this app")

    app_spec = _load_app_spec(app_doc)
    triggers = list(app_spec.triggers or [])
    idx = next((i for i, t in enumerate(triggers) if t.id == trigger_id), None)
    if idx is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="trigger not found on this app")

    # GUARDRAIL (Phase 2): enabling an AUTO-PROCESS trigger turns on autonomous
    # writes to the source system — strictly higher-stakes than editing the app.
    # Gate it: in PROD it requires an elevated (org-admin/admin) role; TEST stays
    # edit-grade so a BA can validate the app before promotion. Fail-loud (403),
    # never a silent allow. (Disabling is always allowed — that's a safety action.)
    if body.enabled is True and getattr(triggers[idx], "execution_mode", "recommend") == "auto_process":
        _env = current_env()
        _roles = set(get_user_roles(request) or [])
        if _env != "test" and not (_roles & {"org_admin", "admin", "superadmin"}):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                detail=(
                    "Enabling an auto-process trigger in production requires an "
                    "org_admin role — it authorizes the agent to commit to the "
                    "source system without human approval. Validate the app in the "
                    "test environment first, then have an admin enable it."
                ),
            )
        # MANDATORY hourly ceiling to arm autonomous writes. Without a cap the
        # only bound on unattended commits is per-run × poll throughput (see the
        # prod-risk report). Fail loud — no "unlimited" autonomous writes.
        _pol = getattr(triggers[idx], "auto_process_policy", None)
        _ceiling = getattr(_pol, "rate_limit_per_hour", None) if _pol else None
        if not (isinstance(_ceiling, int) and _ceiling > 0):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Enabling autonomous writes requires an hourly commit ceiling "
                    "(auto_process_policy.rate_limit_per_hour) — set a positive cap "
                    "before arming this trigger."
                ),
            )
        logger.info(
            "[auto-process] trigger ENABLE app=%s trigger=%s env=%s ceiling=%s by user=%s roles=%s",
            slug, trigger_id, _env, _ceiling, user_id, sorted(_roles),
        )

    upd: Dict[str, Any] = {}
    if body.enabled is not None:
        upd["enabled"] = body.enabled
    if body.cron is not None:
        # Reject an unparseable / too-tight cron HERE with a 422 the BA sees,
        # instead of accepting it and having the trigger silently never fire.
        _validate_cron_expr(
            body.cron, min_interval_seconds=settings.min_trigger_interval_seconds
        )
        upd["cron"] = body.cron or None
    if body.every_seconds is not None:
        # poll / interval fire the agent on a timer — enforce the 5-min floor so
        # a BA can't set a cadence that rate-limits the LLM or piles up runs.
        _t_type = getattr(triggers[idx], "type", None)
        _floor = settings.min_trigger_interval_seconds
        if _t_type in ("poll", "schedule.interval") and body.every_seconds < _floor:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"{_t_type} interval must be >= {_floor}s — each fire runs the "
                    f"agent (several LLM calls), so a tighter cadence rate-limits "
                    f"the LLM and lets runs pile up. Use a webhook for near-real-time."
                ),
            )
        upd["every_seconds"] = body.every_seconds
    if not upd:
        return {"ok": True, "trigger": _ai_trigger_view(triggers[idx], slug, settings)}

    new_t = triggers[idx].model_copy(update=upd)
    triggers[idx] = new_t
    new_spec = app_spec.model_copy(update={"triggers": triggers})
    await get_apps_col().update_one(
        {"slug": slug},
        {"$set": {"app_spec": new_spec.model_dump(mode="json")}},
    )
    return {"ok": True, "trigger": _ai_trigger_view(new_t, slug, settings)}


class AiTriggerRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # Sample case for a webhook/schedule trigger's one-off test run. Ignored for
    # poll (which pulls the next real row).
    inputs: Optional[Dict[str, Any]] = None


async def _fire_trigger_job(job) -> Dict[str, Any]:
    """citra_queue handler for a queued webhook / Run-now trigger job. Fires ONE
    run with ``row_key=job.id`` (idempotent on replay). Raises JobPermanentFailure
    for unrecoverable input (deleted app/trigger) so the job dead-letters instead
    of retrying forever; any other exception is transient (citra_queue retries)."""
    import citra_queue as _cq
    p = job.payload or {}
    slug = p.get("slug")
    trigger_id = p.get("trigger_id")
    set_current_env(p.get("env") or current_env())
    app_doc = await get_apps_col().find_one({"slug": slug})
    if not app_doc:
        raise _cq.JobPermanentFailure(f"app not found: {slug}")
    app_spec = _load_app_spec(app_doc)
    trigger = find_trigger(app_spec, trigger_id)
    if trigger is None:
        raise _cq.JobPermanentFailure(f"trigger not found: {slug}/{trigger_id}")
    agent_doc = await get_agents_col().find_one(
        {"agent_id": app_doc.get("agent_id"), "tenant_id": app_doc.get("tenant_id")}
    )
    if not agent_doc:
        raise _cq.JobPermanentFailure(f"agent spec missing: {slug}")
    agent_spec = AgentSpec.model_validate(agent_doc["agent_spec"])
    key = f"{slug}:{trigger_id}"
    state = await get_trigger_state_col().find_one({"key": key}) or {}
    activity, processed = await run_trigger_once(
        settings=get_settings(), app_spec=app_spec, agent_spec=agent_spec, trigger=trigger,
        state=state, inputs=p.get("inputs"), pending_runs_col=get_pending_runs_col(),
        app_doc=app_doc, stage_recommendation=_stage_recommendation,
        record_run=_record_trigger_run, agent_doc=agent_doc, row_key=job.id,
    )
    # Advance the poll dedup cursor so the next Run-now picks up the FOLLOWING case.
    if trigger.type == "poll" and processed:
        seen = set(state.get("seen_keys") or [])
        seen.update(processed)
        if len(seen) > 1000:
            seen = set(list(seen)[-1000:])
        await get_trigger_state_col().update_one(
            {"key": key}, {"$set": {"key": key, "seen_keys": list(seen)}}, upsert=True)
    return {"fired": True, "processed": processed, "activity_count": len(activity or [])}


@app.post("/apps/{slug}/ai-triggers/{trigger_id}/run")
async def run_ai_trigger_now(
    slug: str,
    trigger_id: str,
    request: Request,
    body: AiTriggerRunRequest = AiTriggerRunRequest(),
    settings: Settings = Depends(get_settings),
) -> Dict[str, Any]:
    """Manually fire ONE run of an AI trigger (the "Run now" button).

    Always processes exactly one case — poll pulls the next single NEW row;
    webhook/schedule run once with the supplied sample ``inputs`` (or empty).
    Works even when the trigger is deactivated (this is how the BA tests it
    before activating). Edit-grade. In the test env this is the only way a
    trigger fires (the scheduler is prod-only), and it is one-at-a-time by
    design — click again for the next case."""
    user_id = get_secure_user_id(request)
    user_tenant = get_tenant_id(request)
    await _bind_app_env(slug)  # resolve test↔prod by store (see _bind_app_env)
    app_doc = await get_apps_col().find_one({"slug": slug})
    if not app_doc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="app not found")
    if not _can_edit_app(
        app_doc, user_id, user_tenant,
        user_org_id=get_org_id(request),
        user_dept_ids=get_user_dept_ids(request),
        user_roles=get_user_roles(request),
        sa_admin_of=get_sa_admin_of(request),
    ):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="not allowed to run this app's triggers")

    app_spec = _load_app_spec(app_doc)
    trigger = find_trigger(app_spec, trigger_id)
    if trigger is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="trigger not found on this app")
    agent_doc = await get_agents_col().find_one(
        {"agent_id": app_doc.get("agent_id"), "tenant_id": app_doc.get("tenant_id")}
    )
    if not agent_doc:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail="agent spec missing for app")
    agent_spec = AgentSpec.model_validate(agent_doc["agent_spec"])

    # Durable enqueue via citra_queue (Redis Streams): the consumer fires the run
    # with row_key=job_id, so a process restart or redelivery re-runs it safely
    # (idempotent auto-process commit) instead of losing it — replaces the old
    # in-memory asyncio.create_task. The UI polls GET .../runs for the outcome.
    import trigger_queue
    job_id = trigger_queue.enqueue_trigger(
        slug=slug, trigger_id=trigger_id, inputs=body.inputs,
        env=current_env(), source="run_now", tenant_id=app_doc.get("tenant_id"),
    )
    return {
        "started": True,
        "trigger_id": trigger_id,
        "job_id": job_id,
        "message": (
            "Run queued — the agent is processing one case. The result appears "
            "in Run history in a few seconds."
        ),
    }


@app.get("/apps/{slug}/ai-triggers/{trigger_id}/runs")
async def get_ai_trigger_runs(
    slug: str,
    trigger_id: str,
    request: Request,
    limit: int = 50,
    settings: Settings = Depends(get_settings),
) -> Dict[str, Any]:
    """Run history for ONE trigger — last N firings (newest first) with status,
    fired/failed, error, correlation_id, decision summary and timing. Read-grade:
    any caller who can open the app can see its trigger history (same gate as the
    app). This is the system-of-record for "did the cron actually fire, and did
    it fail?" — every firing (scheduler / webhook / poll / manual) is recorded."""
    user_id = get_secure_user_id(request)
    user_tenant = get_tenant_id(request)
    await _bind_app_env(slug)  # resolve test↔prod by store (see _bind_app_env)
    app_doc = await get_apps_col().find_one({"slug": slug})
    if not app_doc or not _can_render_app(app_doc, request, user_id, user_tenant):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="app not found")
    limit = max(1, min(int(limit or 50), 200))
    cursor = (
        get_trigger_runs_col()
        .find({"slug": slug, "trigger_id": trigger_id})
        .sort("created_at", -1)
        .limit(limit)
    )
    runs: List[Dict[str, Any]] = []
    async for r in cursor:
        r.pop("_id", None)
        runs.append(r)
    return {"trigger_id": trigger_id, "runs": runs, "count": len(runs)}


# ── /apps/{slug}/document/{ds_id} ─────────────────────────────────────────
# Runtime proxy: handing the browser a short-lived signed URL pointing
# at the original artifact behind a chunk. The chain:
#
#   browser PanelRenderer "Open" click
#     → POST /api/document/{slug}/{ds_id} (runtime Next.js route)
#       → POST /apps/{slug}/document/{ds_id} (this endpoint)
#         → resolve the source via discovery (re-uses the cache from /data/...)
#         → POST {query_endpoint replaced with /document_url} on the dept-MCP
#           with Authorization: service api_key + X-User-JWT: forwarded user JWT
#       ← {url, expires_at, content_type, doc_path, source_id}
#
# Re-uses every gate /data/{panel_id} already has: user_can_render_app for
# audience visibility, dept-MCP's check_visibility for source-level scope,
# user JWT identity in audit. The signed URL itself enforces TTL.

class _DocumentUrlBody(BaseModel):
    doc_path: str = Field(..., min_length=1, max_length=500)


@app.post("/apps/{slug}/document/{ds_id}")
async def get_document_url(
    slug: str,
    ds_id: str,
    body: _DocumentUrlBody,
    request: Request,
    settings: Settings = Depends(get_settings),
) -> dict:
    """Sign a URL to the source artifact behind a chunk's doc_path.

    Re-uses the same auth chain as /data: the caller's JWT propagates to
    the dept-MCP as ``X-User-JWT``, the MCP re-checks per-source visibility
    before signing, and the URL TTL is enforced by S3 / the bucket.
    """
    user_id = get_secure_user_id(request)
    user_tenant = get_tenant_id(request)
    # Resolve test↔prod by store — a test app's document URLs sign through the
    # TEST MCP (see _bind_app_env).
    await _bind_app_env(slug)
    apps = get_apps_col()

    app_doc = await apps.find_one({"slug": slug})
    if not app_doc or not _can_render_app(app_doc, request, user_id, user_tenant):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="app not found")
    if app_doc.get("status") == AppStatus.ARCHIVED.value:
        raise HTTPException(status.HTTP_410_GONE, detail="app is archived")

    app_spec = _load_app_spec(app_doc)
    ds = next((d for d in (app_spec.data_sources or []) if d.id == ds_id), None)
    if ds is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"data_source '{ds_id}' is not declared on this app",
        )
    if ds.type not in ("mcp", "rag"):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"data_source '{ds_id}' is type='{ds.type}'; only mcp/rag "
                   "sources have backing artifacts",
        )

    # Source-id: rag refs are bare; mcp refs are `<source>.<tool>`.
    source_id = (ds.ref or "").split(".", 1)[0].strip()
    if not source_id:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"data_source '{ds_id}' has an empty ref",
        )

    # Resolve via the existing discovery cache so we share the same
    # cached entry the /data flow built up, and reuse its X-User-JWT
    # forwarding semantics for the discovery call itself.
    auth_header = request.headers.get("authorization") or request.headers.get("Authorization")
    user_jwt = (auth_header or "").removeprefix("Bearer ").strip() or None
    if not user_jwt:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "user JWT required")

    from discovery_cache import DiscoveryError, resolve_source
    try:
        resolved = await resolve_source(
            discovery_url=settings.discovery_url_for(current_env()),
            user_jwt=user_jwt,
            source_id=source_id,
            cache_ttl_seconds=settings.discovery_cache_ttl_seconds,
        )
    except DiscoveryError as e:
        # Same error surface as /data — short message lets the runtime
        # decide whether to silently hide the Open button or alert.
        raise HTTPException(status_code=e.status, detail=f"discovery: {e}")

    # The MCP exposes /document_url at the SAME host as /query — just a
    # different path. Compose the URL the same way panel_data builds the
    # /run_query URL from /query.
    qe = resolved.query_endpoint or ""
    if qe.endswith("/query"):
        doc_url_endpoint = qe[: -len("/query")] + "/document_url"
    else:
        doc_url_endpoint = qe.rstrip("/") + "/document_url"

    api_key = resolved.api_key or settings.mcp_service_api_key
    headers = {"Content-Type": "application/json", "X-User-JWT": user_jwt}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    import httpx
    payload = {"source_id": source_id, "doc_path": body.doc_path}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            mcp_resp = await client.post(doc_url_endpoint, json=payload, headers=headers)
    except httpx.HTTPError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=f"mcp transport: {exc}")

    if mcp_resp.status_code >= 400:
        # Forward the MCP's error code + message so the runtime can show
        # something specific (403 = "you don't have access", 404 = "doc
        # not in bucket", 503 = "object storage not configured").
        try:
            detail = mcp_resp.json().get("detail") or mcp_resp.text[:200]
        except ValueError:
            detail = mcp_resp.text[:200]
        raise HTTPException(status_code=mcp_resp.status_code, detail=f"mcp: {detail}")

    return mcp_resp.json()


@app.get("/apps/{slug}/media/{ds_id}")
async def stream_media(
    slug: str,
    ds_id: str,
    request: Request,
    key: str = Query(..., description="Record key value (the SoR record id)."),
    col: str = Query(..., description="The media column on the record to stream."),
    key_field: str = Query("id", description="Record key column name."),
    settings: Settings = Depends(get_settings),
):
    """STREAM a SoR-record media column (photo / PDF) through the dept-MCP.

    The browser hits this same-origin endpoint with an OPAQUE reference — the
    record key + column — never a storage URL. We resolve the app data_source
    → dept-MCP and proxy the MCP's ``/media`` byte stream straight through; the
    MCP owns source-storage creds + per-source visibility and fetches the bytes
    itself. The browser never touches S3 / the source system. Same fail-closed
    auth chain as ``/document``.
    """
    user_id = get_secure_user_id(request)
    user_tenant = get_tenant_id(request)
    await _bind_app_env(slug)
    app_doc = await get_apps_col().find_one({"slug": slug})
    if not app_doc or not _can_render_app(app_doc, request, user_id, user_tenant):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="app not found")
    if app_doc.get("status") == AppStatus.ARCHIVED.value:
        raise HTTPException(status.HTTP_410_GONE, detail="app is archived")

    app_spec = _load_app_spec(app_doc)
    ds = next((d for d in (app_spec.data_sources or []) if d.id == ds_id), None)
    if ds is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            detail=f"data_source '{ds_id}' is not declared on this app")
    if ds.type not in ("mcp", "rag"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            detail=f"data_source '{ds_id}' is type='{ds.type}'; only "
                                   "mcp/rag sources have backing media")
    # ds.ref for an mcp media source is the FULL dataset id (e.g.
    # field_operations.equipment_inspections); source_id is its first segment.
    dataset_id = (ds.ref or "").strip()
    source_id = dataset_id.split(".", 1)[0].strip()
    if not source_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            detail=f"data_source '{ds_id}' has an empty ref")

    auth_header = request.headers.get("authorization") or request.headers.get("Authorization")
    user_jwt = (auth_header or "").removeprefix("Bearer ").strip() or None
    if not user_jwt:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "user JWT required")

    from discovery_cache import DiscoveryError, resolve_source
    try:
        resolved = await resolve_source(
            discovery_url=settings.discovery_url_for(current_env()),
            user_jwt=user_jwt, source_id=source_id,
            cache_ttl_seconds=settings.discovery_cache_ttl_seconds,
        )
    except DiscoveryError as e:
        raise HTTPException(status_code=e.status, detail=f"discovery: {e}")

    qe = resolved.query_endpoint or ""
    media_endpoint = (qe[:-len("/query")] if qe.endswith("/query") else qe.rstrip("/")) + "/media"
    api_key = resolved.api_key or settings.mcp_service_api_key
    headers = {"Content-Type": "application/json", "X-User-JWT": user_jwt}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    payload = {
        "source_id": source_id, "dataset_id": dataset_id,
        "key_field": key_field, "key_value": key, "column": col,
    }

    import httpx
    client = httpx.AsyncClient(timeout=httpx.Timeout(60.0, read=None))
    try:
        mcp_req = client.build_request("POST", media_endpoint, json=payload, headers=headers)
        mcp_resp = await client.send(mcp_req, stream=True)
    except httpx.HTTPError as exc:
        await client.aclose()
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=f"mcp transport: {exc}")
    # Error BEFORE we start streaming so we can forward a real status code.
    if mcp_resp.status_code >= 400:
        body = await mcp_resp.aread()
        await mcp_resp.aclose(); await client.aclose()
        try:
            detail = json.loads(body).get("detail") or body[:200].decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            detail = body[:200].decode("utf-8", "replace")
        raise HTTPException(status_code=mcp_resp.status_code, detail=f"mcp: {detail}")

    async def _pipe():
        try:
            async for chunk in mcp_resp.aiter_bytes():
                yield chunk
        finally:
            await mcp_resp.aclose()
            await client.aclose()

    passthrough = {
        k: v for k, v in mcp_resp.headers.items()
        if k.lower() in ("content-disposition", "cache-control", "content-length")
    }
    return StreamingResponse(
        _pipe(),
        media_type=mcp_resp.headers.get("content-type") or "application/octet-stream",
        headers=passthrough,
    )


@app.get(
    "/apps/{slug}/detail/{panel_id}",
    response_model=DetailDataResponse,
)
async def get_detail_data(
    slug: str,
    panel_id: str,
    request: Request,
    id: Optional[str] = Query(
        None, description="Record id — matched against the linked queue."
    ),
    settings: Settings = Depends(get_settings),
) -> DetailDataResponse:
    """Resolve everything a ``detail`` panel renders, in one round trip.

    The clicked record is matched from the panel's linked queue by the
    ``id`` query param; ``documents`` / ``agent_timeline`` / ``approval``
    sections are filled from their data source and the audit collections.
    Gated identically to ``/data`` — the trusted runtime token may read
    published apps; everyone else goes through the owner / SA / tenant
    check.
    """
    user_id = get_secure_user_id(request)
    user_tenant = get_tenant_id(request)
    # Resolve test↔prod by store before any collection access, exactly like
    # the sibling /data, /field-options and /document endpoints. Without this
    # the detail panel of a TEST app 404s "app not found" (the default store
    # is prod), which manifests as a broken detail page in the builder's
    # smoke/vision gate — see _bind_app_env.
    await _bind_app_env(slug)
    apps = get_apps_col()

    app_doc = await apps.find_one({"slug": slug})
    if not app_doc or not _can_render_app(
        app_doc, request, user_id, user_tenant
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="app not found")
    if app_doc.get("status") == AppStatus.ARCHIVED.value:
        raise HTTPException(status.HTTP_410_GONE, detail="app is archived")

    app_spec = _load_app_spec(app_doc)
    auth_header = request.headers.get("authorization") or request.headers.get(
        "Authorization"
    )
    return await resolve_detail_data(
        settings=settings,
        app_spec=app_spec,
        panel_id=panel_id,
        record_id=id,
        auth_header=auth_header,
        app_id=app_doc.get("app_id"),
    )


# ---------------------------------------------------------------------------
# Notification centre — pending approvals (your roles) + SLA breaches.
# ---------------------------------------------------------------------------
@app.get("/apps/{slug}/notifications/{panel_id}")
async def get_notifications(
    slug: str,
    panel_id: str,
    request: Request,
    settings: Settings = Depends(get_settings),
) -> Dict[str, Any]:
    """Resolve a ``notifications`` panel: pending approvals the caller's roles
    can act on + overdue (SLA-breached) records. Gated like ``/detail``."""
    user_id = get_secure_user_id(request)
    user_tenant = get_tenant_id(request)
    await _bind_app_env(slug)
    app_doc = await get_apps_col().find_one({"slug": slug})
    if not app_doc or not _can_render_app(app_doc, request, user_id, user_tenant):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="app not found")
    if app_doc.get("status") == AppStatus.ARCHIVED.value:
        raise HTTPException(status.HTTP_410_GONE, detail="app is archived")
    app_spec = _load_app_spec(app_doc)
    auth_header = request.headers.get("authorization") or request.headers.get(
        "Authorization"
    )
    from panel_data import resolve_notifications

    return await resolve_notifications(
        settings=settings,
        app_spec=app_spec,
        panel_id=panel_id,
        slug=slug,
        user_dept_ids=get_user_dept_ids(request),
        user_tenant=user_tenant,
        auth_header=auth_header,
    )


# ---------------------------------------------------------------------------
# Single-record read for edit-mode form prefill (load current values).
# ---------------------------------------------------------------------------
@app.get("/apps/{slug}/record")
async def get_one_record(
    slug: str,
    request: Request,
    source: str = Query(..., description="data_source id to read the record from"),
    id: str = Query(..., description="record key value (matched against `key`)"),
    key: Optional[str] = Query(
        None, description="key column the id matches (e.g. case_id)"
    ),
    settings: Settings = Depends(get_settings),
) -> Dict[str, Any]:
    """Return one record's current field values — used by an edit-mode form to
    prefill. Gated identically to ``/detail``."""
    user_id = get_secure_user_id(request)
    user_tenant = get_tenant_id(request)
    await _bind_app_env(slug)
    app_doc = await get_apps_col().find_one({"slug": slug})
    if not app_doc or not _can_render_app(app_doc, request, user_id, user_tenant):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="app not found")
    if app_doc.get("status") == AppStatus.ARCHIVED.value:
        raise HTTPException(status.HTTP_410_GONE, detail="app is archived")
    app_spec = _load_app_spec(app_doc)
    auth_header = request.headers.get("authorization") or request.headers.get(
        "Authorization"
    )
    from panel_data import resolve_one_record

    record = await resolve_one_record(
        settings=settings,
        app_spec=app_spec,
        source_id=source,
        record_id=id,
        key_field=key,
        auth_header=auth_header,
    )
    return {"record": record}


# ---------------------------------------------------------------------------
# Human notes / comments on a record (the ``comments`` detail section).
# App-local OVERLAY only (kind="comment", thread_of=record) — NEVER the SoR.
# ---------------------------------------------------------------------------
@app.get("/apps/{slug}/records/{record_id}/comments")
async def list_record_comments(
    slug: str,
    record_id: str,
    request: Request,
    settings: Settings = Depends(get_settings),
) -> Dict[str, Any]:
    """Read the human-note thread for one record. Gated identically to
    ``/detail``; used by the ``comments`` section and the post-add refresh."""
    user_id = get_secure_user_id(request)
    user_tenant = get_tenant_id(request)
    await _bind_app_env(slug)
    app_doc = await get_apps_col().find_one({"slug": slug})
    if not app_doc or not _can_render_app(app_doc, request, user_id, user_tenant):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="app not found")
    from panel_data import _fetch_record_comments

    # Same app_id fallback the POST uses, so reads and writes agree even for
    # stub apps that lack app_id.
    comments = await _fetch_record_comments(
        app_doc.get("app_id") or slug, record_id
    )
    return {"record_id": record_id, "comments": comments}


@app.post("/apps/{slug}/records/{record_id}/comments")
async def add_record_comment(
    slug: str,
    record_id: str,
    request: Request,
    payload: Dict[str, Any] = Body(...),
    settings: Settings = Depends(get_settings),
) -> Dict[str, Any]:
    """Append a human note/comment to a record. App-local overlay write
    (``kind='comment'``, ``thread_of=record_id``) — it NEVER touches the
    system-of-record. The author is stamped from the (already
    signature-verified upstream) forwarded JWT, read-only."""
    user_id = get_secure_user_id(request)
    user_tenant = get_tenant_id(request)
    text = str((payload or {}).get("text") or "").strip()
    if not text:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail="comment 'text' is required"
        )
    if len(text) > 5000:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail="comment too long (max 5000 chars)"
        )
    await _bind_app_env(slug)
    app_doc = await get_apps_col().find_one({"slug": slug})
    if not app_doc or not _can_render_app(app_doc, request, user_id, user_tenant):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="app not found")
    if app_doc.get("status") == AppStatus.ARCHIVED.value:
        raise HTTPException(status.HTTP_410_GONE, detail="app is archived")

    # Author + dept come from the VERIFIED claims (signature checked upstream by
    # the auth middleware), not an unverified re-decode.
    app_id = str(app_doc.get("app_id") or slug)
    author = user_id
    dept_ids: List[str] = list(get_user_dept_ids(request) or [])

    import uuid as _uuid
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    row_id = f"comment-{_uuid.uuid4().hex[:20]}"
    await get_smart_app_records_col().insert_one(
        {
            "record_id": row_id,
            "thread_of": str(record_id),
            "app_id": app_id,
            "org_id": user_tenant,
            "dept_ids": dept_ids,
            "owner_type": None,
            "owner_id": None,
            "author_user_id": author,
            "source": "user",
            "kind": "comment",
            "status": None,
            "data": {"text": text},
            "deleted_at": None,
            "created_at": now,
            "updated_at": now,
        }
    )
    logger.info(
        "[record-comment] app=%s thread_of=%s by=%s", app_id, record_id, author
    )
    return {
        "ok": True,
        "id": row_id,
        "text": text,
        "author": author,
        "created_at": now.isoformat(),
    }


if __name__ == "__main__":
    import uvicorn

    _settings = get_settings()
    uvicorn.run(
        "main:app",
        host=os.getenv("HOST", "0.0.0.0"),
        port=_settings.port,
        reload=os.getenv("UVICORN_RELOAD", "").lower() in ("1", "true", "yes"),
    )
