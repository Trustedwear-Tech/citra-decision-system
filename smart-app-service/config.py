# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""Smart App Service — configuration."""

import json
import os
from functools import lru_cache
from typing import Optional

from pydantic import model_validator
from pydantic_settings import BaseSettings

# Load .env into os.environ BEFORE the Settings fields evaluate their
# os.getenv() defaults below. pydantic-settings v2 silently ignores the
# legacy ``class Config: env_file`` (see main.py), so this is the real
# .env-reading step for local dev. override=False → Vault / compose-set
# env wins in prod. No-op when no .env exists (containers inject env).
try:  # pragma: no cover - trivial bootstrap
    from dotenv import load_dotenv

    # Untracked local-secrets overlay FIRST (.env is git-tracked and holds only
    # placeholders; under override=False the first loader to set a var wins, so
    # secrets must load before .env or the placeholder shadows the real key).
    # Real env (Vault / compose) still beats both — load_dotenv never overrides
    # already-set process env.
    load_dotenv(".env.secret")
    load_dotenv()
except ImportError:
    pass


def _require_env(name: str) -> str:
    """Return env var ``name`` or fail loud (RULE #1).

    For a connection string / inter-service URL / secret we refuse to boot
    on a silent fallback default — a missing value must surface at startup,
    not silently route to a wrong host. OPTIONAL, feature-gated config keeps
    using ``os.getenv(name, "")`` and is disabled by its ``*_enabled`` flag
    (a configured no-op, which is distinct from a failure).
    """
    val = os.getenv(name)
    if not val:
        raise RuntimeError(
            f"Required environment variable {name!r} is not set. Set it in "
            f".env (local dev) or Vault (prod); refusing to start on a "
            f"fallback default."
        )
    return val


def _parse_extra_body(raw: str) -> dict:
    """Parse a JSON ``extra_body`` env string into a dict.

    Empty/unset → ``{}``. Non-empty but invalid JSON, or JSON that isn't an
    object, raises — a misconfigured ``reasoning``/``provider`` block is a
    boot-time error we want surfaced loudly, not a silent run with the wrong
    tool-calling behaviour.
    """
    raw = (raw or "").strip()
    if not raw:
        return {}
    parsed = json.loads(raw)  # raises on invalid JSON — fail loud
    if not isinstance(parsed, dict):
        raise ValueError(
            f"LLM_LARGE_EXTRA_BODY must be a JSON object, got {type(parsed).__name__}"
        )
    return parsed


class Settings(BaseSettings):
    # MongoDB. We share the per-env database (``dev`` / ``test`` / ``prod``)
    # with the rest of the stack; smart-app collections are namespaced
    # with a ``smartapp_`` prefix so they don't collide with Citra-Service
    # or action-chat-service collections in the same database.
    # Required — set MONGO_URI in .env (local dev) or Vault (prod). No
    # localhost fallback: a missing store connection must fail loud, not
    # silently point at a non-existent local Mongo.
    mongo_uri: str = _require_env("MONGO_URI")
    mongo_db: str = os.getenv("MONGO_DB", "dev")
    # Test environment: a SmartApp is BUILT + tested against the TEST MCPs, then
    # PROMOTED (spec copied) to prod. Isolation is by COLLECTION NAME, not a
    # separate db — a test app's definition (apps/agents) AND operational data
    # (queue/audit/pending_runs/trigger_state/records) live in ``test_``-prefixed
    # collections in this SAME db (see ``_route_col`` / ``TEST_COLLECTION_PREFIX``
    # in main.py). So the prod queue + audit hash-chain stay pristine and an
    # unpromoted app is fail-closed against prod (absent from the prod
    # collection). No test-db config is needed — only the test discovery URLs
    # below, which point reads/writes at the test MCPs.
    # Collections — all prefixed ``smartapp_`` to live safely alongside
    # other services' collections in the shared per-env database.
    apps_collection: str = os.getenv("APPS_COLLECTION", "smartapp_apps")
    agents_collection: str = os.getenv("AGENTS_COLLECTION", "smartapp_agents")
    # Spec version history — one row per SUPERSEDED (app_spec + agent_spec)
    # revision, snapshotted just before publish/edit/promote overwrites the live
    # docs. Backs the UI "Version history" panel and POST /versions/{v}/rollback.
    # Trimmed per app to ``max_spec_versions`` most-recent snapshots. Env-routed
    # like apps/agents (test_ prefix). See ``_snapshot_prior_version`` in main.py.
    spec_versions_collection: str = os.getenv(
        "SPEC_VERSIONS_COLLECTION", "smartapp_spec_versions"
    )
    # How many prior (app_spec + agent_spec) snapshots to keep per app. The live
    # version is always separate; this caps the rollback history. Default 3.
    max_spec_versions: int = int(os.getenv("MAX_SPEC_VERSIONS", "3"))
    prompt_packs_collection: str = os.getenv(
        "PROMPT_PACKS_COLLECTION", "smartapp_prompt_packs"
    )
    skills_collection: str = os.getenv("SKILLS_COLLECTION", "smartapp_skills")
    build_sessions_collection: str = os.getenv(
        "BUILD_SESSIONS_COLLECTION", "smartapp_build_sessions"
    )
    pending_runs_collection: str = os.getenv(
        "PENDING_RUNS_COLLECTION", "smartapp_pending_runs"
    )
    trigger_state_collection: str = os.getenv(
        "TRIGGER_STATE_COLLECTION", "smartapp_trigger_state"
    )
    # Append-only history of every TRIGGER firing (scheduler / webhook / poll /
    # manual Run-now), including failures and rejects. Distinct from
    # ``trigger_state`` (which is just the dedup cursor): one row per firing so
    # the Triggers panel can show run history and an operator can see a trigger
    # that has been silently failing. See ``_record_trigger_run`` in main.py.
    trigger_runs_collection: str = os.getenv(
        "TRIGGER_RUNS_COLLECTION", "smartapp_trigger_runs"
    )
    # Minimum interval for ALL timer-based triggers (cron / interval / poll):
    # they MUST NOT fire more often than this. Each fire runs the agent (several
    # LLM calls), so a tighter cadence rate-limits a shared LLM/GPU endpoint and
    # lets runs pile up before the previous batch finishes. Default 5 minutes;
    # use a webhook for near-real-time. Enforced at the config boundary
    # (PATCH /ai-triggers) AND at publish AND clamped at runtime — an operator
    # ceiling a BA cannot lower.
    min_trigger_interval_seconds: int = int(
        os.getenv(
            "MIN_TRIGGER_INTERVAL_SECONDS",
            os.getenv("MIN_CRON_INTERVAL_SECONDS", "300"),
        )
    )
    # Single shared collection that backs every SmartApp's queue / decision
    # / approval / audit panel. Rows are written by the workflow
    # ``mongo_writer`` node and read by the panel data resolver. Rows carry
    # ``app_id`` / ``org_id`` / ``owner_*`` / ``author_user_id`` so the same
    # collection serves every BA across every org without per-BA naming.
    smart_app_records_collection: str = os.getenv(
        "SMART_APP_RECORDS_COLLECTION", "smart_app_records"
    )

    # The recommendation inbox — per-case rows staged when the app's agent
    # proposes source-system writes that an officer must approve (on-demand via
    # /apps/{slug}/run, or precomputed by an app trigger). No AI agent writes to
    # source systems unattended; the UI's Approve step calls dept-MCP
    # /execute_action and only then mutates the source. Collection name is
    # historical (``smartapp_workflow_staging``).
    workflow_staging_collection: str = os.getenv(
        "WORKFLOW_STAGING_COLLECTION", "smartapp_workflow_staging"
    )

    # Few-shot-from-history grounding refresh is async; its job state
    # (status / phase / progress / result) is TRANSIENT and lives in the
    # platform's shared Redis via ``citra_cache`` (see grounding_runs.py).
    # citra_cache reads the standard REDIS_* env vars itself and fails loud
    # when REDIS_HOST is unset — no Settings fields needed here. The durable
    # artefacts are the Milvus collection (vectors the runtime reads) and the
    # source dataset; there is no Mongo copy of samples (redundant with Milvus).

    # Append-only audit trail — one row per ``/run`` invocation, written for
    # EVERY status (completed / failed / pending_approval). Each row records
    # the decision, the LLM's reasoning, the evidence that drove it (matched
    # few-shot samples + tool-call result digests + citations) and the
    # ``agent_spec`` version in force. Rows are never updated: a re-run (e.g.
    # after approval) appends a fresh row under the same ``correlation_id``.
    # Read by the Smart App "Audit" view. See models.AppRunAudit and the
    # docs/smart-app-architecture.md §7 plan.
    app_run_audit_collection: str = os.getenv(
        "APP_RUN_AUDIT_COLLECTION", "smartapp_run_audit"
    )
    # (Removed 2026-07-01: app_run_audit_preview_collection — the preview-app
    # audit sink went away with the preview mechanism; all runs now audit to the
    # single prod ledger above.)
    # Self-improving loop substrate (docs/citra-self-improving-loop-plan.md).
    # One MUTABLE, loop-facing DecisionRecord per committed decision (human-
    # approved AND auto-process), carrying the context, the recommendation, the
    # officer's override delta, the write result, and — stamped later by the
    # read-back outcome poller — the outcome verdict. DERIVED + reconstructable
    # from the immutable ``smartapp_run_audit`` chain + ``auto_process_decisions``;
    # the hash-chained audit ledger stays the authoritative compliance record.
    decision_records_collection: str = os.getenv(
        "DECISION_RECORDS_COLLECTION", "decision_records"
    )
    # Multimodal twin of decision_records: one MUTABLE row per analyzed ITEM
    # (image / document) carrying the model's verdict (fields, recommendation,
    # rationale, confidence, rubric_version), the artifact identifiers
    # (media_ref + content_sha256), the officer's per-item disposition —
    # accept AND reject AND cancel, with the reject reason — and, stamped later
    # by the same read-back poller via correlation_id, the parent decision's
    # outcome. This is the per-item knowledge ledger the aggregated rubric
    # summarizes; the rubric remains the SOP layer, this keeps the originals
    # (retrievable precedents + the customer's future multimodal training set).
    item_decision_records_collection: str = os.getenv(
        "ITEM_DECISION_RECORDS_COLLECTION", "item_decision_records"
    )
    # Self-improving loop: when True, a good outcome triggers a cheap DELTA
    # write-back — embed only that ONE validated decision and upsert it into the
    # agent's vector memory (no full re-pull/re-embed). Makes learning continuous
    # without the cost of rebuilding the whole corpus per outcome. The full
    # refresh_grounding rebuild remains the periodic/manual backstop.
    auto_refresh_on_outcome: bool = (
        os.getenv("AUTO_REFRESH_ON_OUTCOME", "true").lower() in ("1", "true", "yes")
    )
    # Fixed-size memory: max NON-canonical rows kept per agent in the vector
    # corpus. The delta path evicts the oldest beyond this; the full refresh
    # trims to it too. Canonical (curated) rows are always kept on top of this.
    grounding_max_rows_per_agent: int = int(
        os.getenv("GROUNDING_MAX_ROWS_PER_AGENT", "300")
    )
    # (Removed 2026-07-01: preview_app_ttl_days — the preview-app TTL sweep went
    # away with the preview mechanism.)

    # Auth. There is no string default — unset / placeholder values are
    # rejected by auth.py at startup so a misconfigured deployment fails
    # loud rather than silently running on a guessable secret.
    jwt_secret: str = os.getenv("JWT_SECRET", "")
    jwt_algorithm: str = os.getenv("JWT_ALGORITHM", "HS256")
    # Issuer stamped on minted tokens. data-discovery-service (and other
    # platform services) validate the ``iss`` claim — a token minted
    # without it is 401'd, which silently empties the builder catalogue.
    jwt_issuer: str = os.getenv("JWT_ISSUER", "Citra-AI")

    # Upstream services
    # action-sandbox-host listens on :7090 and runs with `network_mode:
    # host` (see action-sandbox-host/docker-compose.yml). On a single VM
    # other containers reach it via host.docker.internal (Docker Desktop
    # / Win+Mac) or the docker0 bridge IP (Linux: typically 172.17.0.1).
    # In Kubernetes it resolves through a Service DNS name as usual.
    #
    # May be a COMMA-SEPARATED list of host control URLs
    # (e.g. "http://host-a:7090,http://host-b:7090"). SandboxClient
    # capacity-ranks them and fails over on spawn, so a single full /
    # unreachable host no longer dead-ends a build. A single value (the
    # common case) behaves exactly as before.
    sandbox_host_url: str = _require_env("SANDBOX_HOST_URL")
    sandbox_host_secret: str = os.getenv("SANDBOX_HOST_SECRET", "")
    # Resource tier for builder pods. Builders historically ran at the
    # host's default ("quick" = 2 GB / 2 cores). Bump to "standard" (4 GB)
    # if spec synthesis OOMs, without a code change. Sent verbatim in the
    # /spawn payload; the host falls back to its default on an unknown tier.
    builder_tier: str = os.getenv("SMARTAPP_BUILDER_TIER", "quick")
    # CURRENT-environment discovery — the discovery plane of wherever this
    # deployment runs (dev / local / prod, per ENVIRONMENT). A run resolves its
    # source_id → MCP here. There are exactly TWO discovery planes: this
    # "current" one and the "test" one below.
    discovery_service_url: str = _require_env("DISCOVERY_SERVICE_URL")
    # TEST-environment discovery — the plane the BUILDER builds + tests against
    # (test MCPs / test data so writes can be executed and validated safely).
    # Unset → the test environment is unavailable (``discovery_url_for("test")``
    # fails loud rather than silently resolving test sources against the current
    # plane). Locally it may point at the SAME url as DISCOVERY_SERVICE_URL when
    # one MCP serves both planes.
    test_discovery_service_url: str = os.getenv("TEST_DISCOVERY_SERVICE_URL", "")
    citra_service_url: str = _require_env("CITRA_SERVICE_URL")
    # ----- Large LLM (forwarded to the builder pod) -----
    # Mirrors the action-chat-service pattern. The builder pod's
    # `citra-app-spec` / `citra-agent-spec` / `citra-dashboard-spec`
    # skills make heavy reasoning calls — they need the large tier
    # (GLM-5.1 / similar). Names match
    # action-chat-service exactly so the same Citra-Service .env block
    # works for both. Falls back to the legacy LLM_BASE_URL / LLM_API_KEY
    # / LLM_MODEL names when LLM_LARGE_* is unset.
    llm_large_base_url: str = (
        os.getenv("LLM_LARGE_BASE_URL")
        or os.getenv("LLM_BASE_URL", "")
    )
    llm_large_api_key: str = (
        os.getenv("LLM_LARGE_API_KEY")
        or os.getenv("LLM_API_KEY", "")
    )
    llm_large_model: str = (
        os.getenv("LLM_LARGE_MODEL")
        or os.getenv("LLM_MODEL", "")
    )
    # Per-surface override for the SmartApp Builder pod. When set, the
    # builder pod's LLM_LARGE_MODEL + LLM_MODEL env are pinned to this
    # value instead of inheriting from llm_large_model. The runtime
    # (agent inference, action loops, chat) keeps using llm_large_model
    # — split because the builder needs a top-tier reasoning model
    # (claude-opus / gpt-5-pro) while the runtime is fine on a faster
    # cheaper tier. Empty string = inherit, same as the historical
    # behaviour. Don't forget to add the model to
    # SMART_APP_LLM_PROXY_ALLOWED_MODELS — the internal proxy enforces
    # an allowlist before relaying to the upstream provider.
    builder_llm_model: str = os.getenv("BUILDER_LLM_MODEL", "")
    # Context window the builder pod's agent should assume. Fed to OpenClaw as
    # the model's contextWindow — it decides when to auto-compact AND acts as a
    # hard pre-flight ceiling (a prompt estimated above it raises "Context
    # overflow: prompt too large for the model" and the build STALLS). It MUST
    # track the configured model's true window, so set LLM_CONTEXT_WINDOW in env
    # to match: DeepSeek V4 Pro (current builder model) is 1,000,000 tokens — see
    # .env (960000, just under 1M for output headroom). The code default below is
    # a conservative fallback only (a model-agnostic value safe for any provider);
    # the real value always comes from env / Vault.
    llm_context_window: int = int(
        os.getenv("LLM_CONTEXT_WINDOW", "163840")
    )
    llm_max_output_tokens: int = int(
        os.getenv("LLM_MAX_OUTPUT_TOKENS", "32768")
    )
    # Per-LLM-call output cap for the chat / run agent loop (``_call_llm``).
    # Must leave room for a reasoning model's chain-of-thought PLUS the tool
    # call / final answer — on OpenRouter reasoning tokens count against this.
    # 1024 (the original hard-coded value) starved GLM mid-reason and produced
    # empty "(no response)" turns. 4096 was the next floor but still too tight:
    # on a large-context triage (50k+ prompt tokens) DeepSeek-v4-pro spent the
    # whole 4096 on the FINAL synthesis turn's reasoning and emitted 0 chars —
    # a silent no-op (prod run 9d3409a1…, 2026-06-22). 20000 leaves the final
    # turn room to reason AND answer.
    # 2026-06-24: bumped 20000 → 40000. On a large dashboard hero-brief the
    # reasoning model spent nearly the whole 20000 budget reasoning and the
    # visible brief was cut off mid-sentence (BSPHCL Command Dashboard). Double
    # the budget so the FINAL answer always has room to complete.
    llm_agent_max_tokens: int = int(
        os.getenv("LLM_AGENT_MAX_TOKENS", "40000")
    )
    # Force a tool call (tool_choice="required") on the first iteration of a
    # write-capable action run, so the agent can't return a prose-only
    # recommendation that records nothing. OFF by default: not every provider
    # supports tool_choice="required" — z-ai/glm-5.1 on OpenRouter 404s when it
    # is sent. Enable ONLY with a tool-call-reliable action-tier model
    # (Claude / GPT-class).
    force_action_tool_call: bool = (
        os.getenv("FORCE_ACTION_TOOL_CALL", "false").lower() in ("1", "true", "yes")
    )
    # Read-before-write evidence guard. When on (default), a /run may not STAGE a
    # write unless the agent actually read the anchor record (the caller-supplied
    # id) and every bound-and-required media tool (image_analyze / doc_extract)
    # for that record — else the run fails loud (evidence_guard.EvidenceNotReviewed)
    # rather than staging an ungrounded recommendation. Documented kill switch;
    # set ENFORCE_READ_BEFORE_WRITE=false only to disable during a rollout.
    enforce_read_before_write: bool = (
        os.getenv("ENFORCE_READ_BEFORE_WRITE", "true").lower() in ("1", "true", "yes")
    )
    # Plan-mode (plan_only=True, recommend→approve) rollout mode for the
    # read-before-write guard. "enforce" (default): a staged write with unread
    # evidence FAILS the run (nothing offered to the officer). "log": dark-launch
    # — the violation is logged (timeline `evidence_gate: would_block`) but the
    # write is still staged, so impact can be observed before enforcing. "off":
    # no plan-mode check. Gated by ENFORCE_READ_BEFORE_WRITE (master).
    read_before_write_plan_mode: str = (
        os.getenv("READ_BEFORE_WRITE_PLAN_MODE", "enforce").lower()
    )
    # Auto-process (plan_only=False, unattended trigger/workflow) rollout mode for
    # the read-before-write guard. That path commits writes LIVE inside the tool
    # loop, so enforcement must happen BEFORE each write dispatches. Dark-launch
    # default is "log": violations are logged (timeline + logger) but the write
    # still commits, so we can observe impact before blocking. "enforce": the
    # write is refused and the model is told to read first (self-correct). "off":
    # no auto-process check at all. Gated by ENFORCE_READ_BEFORE_WRITE (master).
    read_before_write_autoprocess_mode: str = (
        os.getenv("READ_BEFORE_WRITE_AUTOPROCESS_MODE", "log").lower()
    )
    # Required data-LOOKUP gate rollout mode (a SEPARATE dimension from the
    # record/media gate above). When an agent tool is marked ``required: true``
    # (a bound mcp read the policy mandates — e.g. a bureau / KYC / sanctions
    # check), a write may not be staged unless that lookup actually RAN for the
    # case under review. "enforce" (default): the run fails loud (RULE #1).
    # "log": the violation is recorded (timeline `evidence_gate: would_block`)
    # but the write still stages — an observation-only rollout mode. "off": no
    # required-lookup check. Gated by the same master ENFORCE_READ_BEFORE_WRITE
    # switch, but rolled out independently so it can't regress apps already
    # relying on the record/media gate.
    #
    # Was "log" — a dark-launch that shipped and was never flipped, so
    # `mandatory_when_used` (docs/sources-file.md §11: the gate "refuses to
    # stage a write"… the platform "both guides the agent to it and ENFORCES
    # it") was advisory in every default deployment: mark a bureau/KYC check
    # mandatory, skip it, and the write staged anyway with only a log line. A
    # compliance control that does not control is worse than none — the author
    # believes it binds. Enforcing by default makes the doc true; set
    # REQUIRED_READS_MODE=log to re-observe during a rollout.
    required_reads_mode: str = (
        os.getenv("REQUIRED_READS_MODE", "enforce").lower()
    )
    # Publish-time derivation of Action.anchor_read from the catalogue.
    # "enforce" (default): derive the anchor, and FAIL the publish if an action
    # mutates a keyed record it can't anchor (unguardable write). "warn": derive
    # + surface the reason in requirements_unmet instead of failing. "off": skip
    # derivation entirely (anchor_read stays whatever was authored).
    anchor_on_publish_mode: str = (
        os.getenv("ANCHOR_ON_PUBLISH_MODE", "enforce").lower()
    )
    # Provider-specific ``extra_body`` merged into every chat-completion
    # call (OpenRouter ``reasoning`` / ``provider`` routing, etc.). For
    # GLM-class hybrid-reasoning models this block is load-bearing: without
    # a ``reasoning`` directive the model leaks its chain-of-thought (incl.
    # native ``<tool_call>`` markup) into ``content`` instead of returning
    # structured ``tool_calls``, which the dispatch loop then can't execute.
    # Parsed once at import; invalid JSON fails loud rather than silently
    # running unconfigured.
    llm_large_extra_body: dict = _parse_extra_body(
        os.getenv("LLM_LARGE_EXTRA_BODY", "")
    )

    # ── Medium / small tiers (builder-chosen per decision) ──────────────────
    # The BUILDER decides a decision's complexity and sets ``model_tier`` on
    # the Action/AgentSpec; the runtime resolves it to a model HERE, from env.
    # Base/key fall back to the LARGE endpoint when unset (common: same
    # provider, different model). A tier whose MODEL is unset resolves to the
    # LARGE model — we never silently run a SMALLER model than the deployment
    # configured (a small model misreading a decision is the dangerous case),
    # so "unconfigured tier" fails SAFE (up to large), logged at call time.
    llm_medium_base_url: str = os.getenv("LLM_MEDIUM_BASE_URL", "")
    llm_medium_api_key: str = os.getenv("LLM_MEDIUM_API_KEY", "")
    llm_medium_model: str = os.getenv("LLM_MEDIUM_MODEL", "")
    llm_medium_extra_body: dict = _parse_extra_body(os.getenv("LLM_MEDIUM_EXTRA_BODY", ""))
    llm_small_base_url: str = os.getenv("LLM_SMALL_BASE_URL", "")
    llm_small_api_key: str = os.getenv("LLM_SMALL_API_KEY", "")
    llm_small_model: str = os.getenv("LLM_SMALL_MODEL", "")
    llm_small_extra_body: dict = _parse_extra_body(os.getenv("LLM_SMALL_EXTRA_BODY", ""))

    def llm_tier_config(self, tier: Optional[str]) -> dict:
        """Resolve a builder-chosen ``model_tier`` to a concrete LLM endpoint.

        Returns ``{tier, model, base_url, api_key, extra_body, fell_back}``.
        Canonical tiers: ``large`` | ``medium`` | ``small``. Legacy
        ``tier_a/b/c`` all map to LARGE (they always ran on the large model).
        A tier whose model env is unset falls back to LARGE (safe direction).
        """
        t = (tier or "large").strip().lower()
        if t in ("medium",) and self.llm_medium_model:
            return {
                "tier": "medium", "model": self.llm_medium_model,
                "base_url": self.llm_medium_base_url or self.llm_large_base_url,
                "api_key": self.llm_medium_api_key or self.llm_large_api_key,
                "extra_body": self.llm_medium_extra_body or self.llm_large_extra_body,
                "fell_back": False,
            }
        if t in ("small",) and self.llm_small_model:
            return {
                "tier": "small", "model": self.llm_small_model,
                "base_url": self.llm_small_base_url or self.llm_large_base_url,
                "api_key": self.llm_small_api_key or self.llm_large_api_key,
                "extra_body": self.llm_small_extra_body or self.llm_large_extra_body,
                "fell_back": False,
            }
        # large, legacy tier_a/b/c, or an unconfigured medium/small → LARGE.
        return {
            "tier": "large", "model": self.llm_large_model,
            "base_url": self.llm_large_base_url, "api_key": self.llm_large_api_key,
            "extra_body": self.llm_large_extra_body,
            "fell_back": t in ("medium", "small"),
        }

    # The data catalogue (read-only schema/metadata). NOT environment-split:
    # test and prod share it (schemas are identical; IT owns parity), so the
    # builder always reads this prod catalogue. Only the MCP plane
    # (discovery_service_url) is test↔prod.
    data_discovery_service_url: str = _require_env("DATA_DISCOVERY_SERVICE_URL")
    reranker_service_url: str = _require_env("RERANKER_SERVICE_URL")

    # Build sessions
    # IDLE timeout — sandbox-host kills a builder pod after this many
    # seconds of no activity. Default 30 min covers a coffee break.
    build_session_idle_timeout_seconds: int = int(
        os.getenv("BUILD_SESSION_IDLE_TIMEOUT_SECONDS", "1800")
    )
    # MAX lifetime — hard ceiling on how long a builder pod can stay
    # alive, regardless of activity. Default 8 h covers a long design
    # session (BA back-and-forth, multi-phase builds).
    build_session_max_seconds: int = int(
        os.getenv("BUILD_SESSION_MAX_SECONDS", "28800")
    )
    # Scoped JWT TTL minted into the builder pod. Defaults to
    # build_session_max_seconds — the JWT lives as long as the pod
    # could possibly live. Mirrors action-chat-service's design
    # (ACTIONCHAT_SCOPED_TOKEN_TTL defaults to ACTIONCHAT_MAX_SESSION)
    # so internal-route calls stay reachable for the full session;
    # a shorter TTL caused mid-session 403s on action-chat before it
    # was extended.
    builder_scoped_token_ttl_seconds: int = int(
        os.getenv("BUILDER_SCOPED_TOKEN_TTL")
        or os.getenv("BUILD_SESSION_MAX_SECONDS", "28800")
    )

    # App
    port: int = int(os.getenv("PORT", "9100"))
    environment: str = os.getenv("ENVIRONMENT", "dev")
    # NOTE: the public apps host is apps.citra-ai.com (hyphen, .com) — it is the
    # domain Cloudflare/ALB/Traefik actually route to citra-app-runtime
    # (infrastructure/traefik/live/app-box/apps-runtime.yml). "apps.citra.ai"
    # (no hyphen) is NOT a registered domain and NXDOMAINs — do not use it.
    apps_base_url: str = os.getenv("APPS_BASE_URL", "https://apps.citra-ai.com")
    # URL the builder pod uses to reach this service for /publish.
    # In dev with Docker Desktop this is http://host.docker.internal:9100.
    # In prod it's the in-cluster service name.
    smart_app_service_callback_url: str = _require_env(
        "SMART_APP_SERVICE_CALLBACK_URL"
    )
    # The builder pod's tool gateway — citra-mcp-service's MCP endpoint, merged
    # into the OpenClaw function catalogue (discovery_search, spec_validate,
    # visual_review, web_search/fetch, embed/rerank). MUST be set to a value the
    # ISOLATED builder-sandbox network can resolve (mirror DISCOVERY_SERVICE_URL).
    # Dev (Docker Desktop): host.docker.internal. Prod: the in-cluster
    # citra-mcp-service URL — set CITRA_MCP_URL env; the host.docker.internal
    # default does NOT resolve on Linux/AWS and silently strips the whole tool
    # plane (discovery/validate/review), forcing the builder into the
    # build→publish→422 loop. See docs + builder-workspace/TOOLS.md.
    citra_mcp_url: str = os.getenv(
        "CITRA_MCP_URL", "http://host.docker.internal:9090/mcp"
    )

    # ----- Vision / OCR (proxied by smart-app-service) -----
    # The builder pod and runtime engines never see VISION_API_KEY directly.
    # They call POST /smart-app/internal/ocr with a short-lived HMAC
    # bearer; this service forwards to the configured vision endpoint.
    # NO fallback to LLM_LARGE_* (RULE #1): the large model is text-only —
    # silently reusing its endpoint/key would route vision calls to a model
    # that cannot see images. Either VISION_BASE_URL + VISION_API_KEY are
    # both set (OCR on) or both empty (OCR off: vision tools are dropped
    # with warnings and calls 503 ocr_not_configured). A partial set is a
    # misconfiguration and refuses to boot — see _no_partial_vision_config.
    vision_base_url: str = os.getenv("VISION_BASE_URL", "")
    vision_api_key: str = os.getenv("VISION_API_KEY", "")
    # The deployment's vision model (OpenRouter id). We use GLM-4.6V.
    vision_model: str = os.getenv("VISION_MODEL", "z-ai/glm-4.6v:nitro")

    # ----- MCP / RAG / Workflow proxy targets -----
    # Service-to-service shared secret for calling registered dept MCP
    # ``/query`` endpoints. Discovery returns a per-tool ``api_key`` but
    # falling back to this is useful in dev where dept MCPs all share
    # one key. Empty disables the MCP/RAG proxies (they 503).
    mcp_service_api_key: str = (
        os.getenv("MCP_SERVICE_API_KEY")
        or os.getenv("SERVICE_API_KEY", "")
    )
    # Per-tool HTTP timeout for dept-MCP /query (semantic/structured/mongodb).
    # Hard cap; the proxy never waits longer than this.
    mcp_call_timeout_seconds: float = float(
        os.getenv("MCP_CALL_TIMEOUT_SECONDS", "120")
    )
    # Code-exec proxy points at Citra-Service ``/internal/code-exec/run``.
    # Re-uses ``citra_service_url`` by default — the sandbox pool lives
    # there because that's where the Docker host plumbing already exists.
    code_exec_service_url: str = (
        os.getenv("CODE_EXEC_SERVICE_URL")
        or _require_env("CITRA_SERVICE_URL")
    )
    code_exec_call_timeout_seconds: float = float(
        # Tracks the upstream sandbox's hard cap (default 60s) plus a
        # small buffer for download + S3 upload.
        os.getenv("CODE_EXEC_CALL_TIMEOUT_SECONDS", "120")
    )
    # In-process discovery cache TTL for resolving (source_id) -> (query_endpoint, api_key).
    discovery_cache_ttl_seconds: int = int(
        os.getenv("DISCOVERY_CACHE_TTL_SECONDS", "300")
    )

    # ----- Milvus (NeighborSamplesTool runtime) -----
    # Required when AgentSpec.tools_v2[] contains a ``neighbor_samples``
    # tool — Smart App "few-shot from history" pattern. Empty values
    # disable the tool gracefully (the runtime pre-injector logs and
    # falls back to system_prompt-only). Same env contract as the rest
    # of the platform; ZILLIZ_CLOUD_* are the legacy fallback names.
    milvus_uri: str = (
        os.getenv("MILVUS_URI")
        or os.getenv("ZILLIZ_CLOUD_URI", "")
    )
    milvus_token: str = (
        os.getenv("MILVUS_TOKEN")
        or os.getenv("ZILLIZ_CLOUD_TOKEN", "")
    )

    # ----- Fraud image index (P2b — docs/fraud-detection-primitives-plan.md) -----
    # Multimodal image embeddings for near-duplicate / similar-image detection.
    # DEFAULT: nvidia/llama-nemotron-embed-vl-1b-v2 via the ALREADY-PAID
    # OpenRouter account (decided 2026-07-02 after LIVE benchmarking: it
    # separates copies from unrelated images cleanly — crop 0.98 / unrelated
    # 0.58-0.68 — while google/gemini-embedding-2 does NOT separate at all for
    # image↔image near-dup, and jina-clip-v2 would add a second vendor). Key
    # falls back to the platform's OpenRouter key so no extra secret is needed.
    # Input = data-URI string; `dimensions` accepted; 512-dim truncation keeps
    # the separation (live-verified). Per-deployment override: an air-gapped deployment points
    # IMAGE_EMBED_URL at a self-hosted open-weights server (jina-clip-v2 /
    # SigLIP) — each single-tenant deployment owns its OWN index, so vector
    # spaces never need to match across deployments. We persist the FULL
    # requested dim on the fingerprint doc; Milvus indexes a Matryoshka-
    # truncated slice — a future index-dim change is a local re-index with
    # zero new API calls. Empty api key disables the layer gracefully
    # (pHash + SHA-256 still run).
    # Locale pack for the fraud validators + date-order (us | in). Selects
    # which ID validators run (SSN/EIN/ABA-routing/ZIP vs PAN/IFSC/GSTIN/
    # Aadhaar), the phone rule, and slash-date disambiguation (03/04 = Mar 4 in
    # the US, 3 Apr in India). Deployment-level (single-tenant); default 'us'
    # (primary market). Indian demo tenants set FRAUD_LOCALE=in.
    fraud_locale: str = os.getenv("FRAUD_LOCALE", "us")
    image_embed_url: str = os.getenv("IMAGE_EMBED_URL", "https://openrouter.ai/api/v1/embeddings")
    image_embed_api_key: str = (
        os.getenv("IMAGE_EMBED_API_KEY")
        or os.getenv("LLM_LARGE_API_KEY")
        or os.getenv("LLM_API_KEY", "")
    )
    image_embed_model: str = os.getenv("IMAGE_EMBED_MODEL", "nvidia/llama-nemotron-embed-vl-1b-v2:free")
    # Per-call budget for the embedding request. The default model is a FREE
    # OpenRouter tier that throttles under load — when it times out, the
    # similar-image fraud tier is OFF for that artifact, which must be LOUD
    # (log.error + visible image_index_error), never a silent thinner screen.
    image_embed_timeout_s: float = float(os.getenv("IMAGE_EMBED_TIMEOUT_S", "45"))
    image_embed_store_dim: int = int(os.getenv("IMAGE_EMBED_STORE_DIM", "2048"))
    image_embed_index_dim: int = int(os.getenv("IMAGE_EMBED_INDEX_DIM", "512"))
    fraud_image_collection: str = os.getenv("FRAUD_IMAGE_COLLECTION", "smartapp_fraud_image_index")
    # Cosine score at/above which a hit is flagged as a near-duplicate (strong
    # evidence) vs merely "similar" (informational, >= similar threshold).
    fraud_image_dup_threshold: float = float(os.getenv("FRAUD_IMAGE_DUP_THRESHOLD", "0.92"))
    fraud_image_similar_threshold: float = float(os.getenv("FRAUD_IMAGE_SIMILAR_THRESHOLD", "0.80"))

    # ----- Embedding endpoint (NeighborSamplesTool 'neighbors' mode) -----
    # SAME env contract as Citra-Service (utils.py): EMBEDDING_BASE_URL /
    # EMBEDDING_API_KEY / EMBEDDING_MODEL / EMBEDDING_DIMENSION. smart-app-service
    # is a "normal service" so it embeds DIRECTLY against this endpoint (the
    # builder/OpenClaw pod uses the citra embed MCP tool instead). Legacy
    # EMBEDDING_API_BASE is kept only as a fallback. On a private
    # deployment set EMBEDDING_BASE_URL to the inference-service.
    embedding_base_url: str = (
        os.getenv("EMBEDDING_BASE_URL")
        or os.getenv("EMBEDDING_API_BASE", "")  # legacy smart-app name
    )
    embedding_api_key: str = os.getenv("EMBEDDING_API_KEY", "")
    embedding_model: str = os.getenv("EMBEDDING_MODEL", "baai/bge-m3")
    # MUST match the Citra-Service embedding space (default 768) so grounding
    # vectors are comparable — bge-m3 honours the `dimensions` arg, returning
    # a re-normalised 768-d vector instead of its native 1024.
    embedding_dimension: int = int(os.getenv("EMBEDDING_DIMENSION", "768"))

    # ----- Environment-aware discovery resolution (test ↔ current) -----
    # Two discovery planes only: "test" (the builder's TEST_DISCOVERY_SERVICE_URL)
    # and "current" (DISCOVERY_SERVICE_URL — wherever this deployment runs:
    # dev / local / prod per ENVIRONMENT). A run resolves source_id → MCP against
    # one of them. ONLY discovery is env-split; the data catalogue
    # (data_discovery_service_url) is read-only schema/metadata, shared — NOT
    # split. ``environment="test"`` selects the test plane; ANY other value
    # ("prod" / "dev" / "local" / "current" — whatever current_env() yields for
    # the run) selects the current plane. Default "current".
    def discovery_url_for(self, environment: str = "current") -> str:
        if environment == "test":
            if not self.test_discovery_service_url:
                raise RuntimeError(
                    "test environment requested but TEST_DISCOVERY_SERVICE_URL "
                    "is not set — refusing to resolve test sources against the "
                    "current (non-test) discovery plane"
                )
            return self.test_discovery_service_url
        # current environment's discovery (dev / local / prod)
        return self.discovery_service_url

    @property
    def test_environment_available(self) -> bool:
        """True when the test MCP plane is configured (``test_discovery_service_url``).
        That is the ONLY test-specific config: the catalogue is shared (prod) and
        the test collections live in the same db. When False, a build that
        resolves to "test" hits ``discovery_url_for`` which fails loud rather than
        silently resolving against prod sources."""
        return bool(self.test_discovery_service_url)

    @property
    def mcp_enabled(self) -> bool:
        return bool(self.discovery_service_url and self.mcp_service_api_key)

    @property
    def rag_enabled(self) -> bool:
        # RAG re-uses the dept-MCP /query endpoint so the gating is identical.
        return self.mcp_enabled

    @property
    def code_exec_enabled(self) -> bool:
        return bool(self.code_exec_service_url)

    # ----- LLM proxy (builder pod) -----
    # The builder pod calls POST /smart-app/internal/llm/v1/chat/completions
    # with an HMAC bearer; this service relays to LLM_LARGE_BASE_URL using
    # its own credential. The pod never sees the raw OpenRouter key. Caps
    # below bound the blast radius of a compromised / misbehaving builder
    # session (e.g. prompt-injected loop). Per-process, in-memory counters
    # — fine for the single-replica builder topology; promote to Redis if
    # smart-app-service is horizontally scaled.
    llm_proxy_max_calls_per_session: int = int(
        os.getenv("SMART_APP_LLM_PROXY_MAX_CALLS_PER_SESSION", "1000")
    )
    llm_proxy_max_tokens_per_session: int = int(
        os.getenv("SMART_APP_LLM_PROXY_MAX_TOKENS_PER_SESSION", "10000000")
    )
    # Comma-separated; empty string allows only LLM_LARGE_MODEL.
    llm_proxy_allowed_models_raw: str = os.getenv(
        "SMART_APP_LLM_PROXY_ALLOWED_MODELS", ""
    )
    # Prompt-cache injection on the builder LLM proxy. When True (default),
    # the proxy stamps ``cache_control: ephemeral`` on the stable system
    # prefix + conversation tail for Anthropic models so OpenRouter →
    # Anthropic serves repeat-turn prefix tokens from cache at ~0.1x cost.
    # No effect on non-Anthropic models. Set false to disable (e.g. while
    # debugging a provider that rejects the extra content-part keys).
    llm_proxy_prompt_cache_enabled: bool = (
        os.getenv("SMART_APP_LLM_PROXY_PROMPT_CACHE", "true").lower()
        in ("1", "true", "yes")
    )

    @property
    def llm_proxy_allowed_models(self) -> set:
        models = {
            m.strip()
            for m in self.llm_proxy_allowed_models_raw.split(",")
            if m.strip()
        }
        if self.llm_large_model:
            models.add(self.llm_large_model)
        # Builder-only override is implicitly allowed — the BA shouldn't
        # have to remember to add it to the env-driven allowlist when
        # they set BUILDER_LLM_MODEL.
        if self.builder_llm_model:
            models.add(self.builder_llm_model)
        return models

    # ----- Internal-route HMAC signing (builder pod + runtime engine) -----
    # Used to mint short-lived bearer tokens that gate /smart-app/internal/*.
    # REQUIRED and dedicated — there is intentionally NO fallback to
    # JWT_SECRET. Keeping this separate (a) isolates internal-route signing
    # from end-user auth so a rotation/leak of one doesn't affect the other,
    # and (b) removes the "which key is actually signing?" ambiguity that a
    # silent fallback creates. Set it in .env (dev) and Vault (prod). Generate
    # one with:  python -c "import secrets; print(secrets.token_hex(64))"
    # When unset, internal bearer mint/verify fails fast with a clear error
    # (see internal_bearer.py) instead of silently signing with JWT_SECRET.
    internal_signing_key: str = os.getenv("SMART_APP_INTERNAL_SIGNING_KEY", "")
    # Bearer TTL. Builder pods get one per build session; runtime engines
    # mint a fresh one on cold start.
    # IMPORTANT: this bearer is the builder pod's LLM_API_KEY — minted ONCE at
    # spawn and baked into the pod env, never refreshed. It MUST outlast the
    # whole build session (BUILD_SESSION_MAX_SECONDS, default 8h). The old 1h
    # default silently expired mid-build → every LLM call 401'd → the build
    # hung with the UI stuck on "thinking" (no fail-loud surface). Default is
    # now 24h so an interactive build can never outlive its own LLM auth.
    # (Mirrors the earlier fix that extended builder_scoped_token_ttl_seconds.)
    internal_bearer_ttl_seconds: int = int(
        os.getenv("INTERNAL_BEARER_TTL_SECONDS", "86400")
    )

    @property
    def ocr_enabled(self) -> bool:
        """True iff the OCR proxy has enough config to make a call."""
        return bool(
            self.vision_base_url
            and self.vision_api_key
            and self.vision_model
        )

    @model_validator(mode="after")
    def _no_partial_vision_config(self) -> "Settings":
        """Refuse to boot on half-configured vision (RULE #1).

        Fully unset = OCR off (a configured no-op, legitimate). Fully set =
        OCR on. Anything in between means someone set the endpoint without
        the key (or vice versa, or blanked VISION_MODEL) — surface it at
        startup instead of 503ing every vision call at runtime.
        """
        has_url = bool(self.vision_base_url)
        has_key = bool(self.vision_api_key)
        if has_url != has_key or (has_url and has_key and not self.vision_model):
            raise RuntimeError(
                "Vision/OCR config is PARTIAL: VISION_BASE_URL, VISION_API_KEY "
                "and VISION_MODEL must either all be set (OCR on) or "
                "VISION_BASE_URL/VISION_API_KEY both empty (OCR off). "
                f"Got base_url={'set' if has_url else 'EMPTY'}, "
                f"api_key={'set' if has_key else 'EMPTY'}, "
                f"model={self.vision_model or 'EMPTY'!r}. There is NO fallback "
                "to LLM_LARGE_*; refusing to start."
            )
        return self

    class Config:
        env_file = ".env"
        extra = "ignore"


@lru_cache
def get_settings() -> Settings:
    return Settings()
