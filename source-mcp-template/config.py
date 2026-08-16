"""
Dept MCP Server — Configuration
================================
Query-plane only. Ingestion is owned by the Citra Workflow Engine
("Dept Data Flow" pipelines). Source definitions live in the central
``dept_sources`` Mongo collection inside Citra-Service.

All values injected via environment variables or a .env file.
Dept IT fills in the env file; they never touch Python code.
"""

import os
from functools import lru_cache
from typing import List, Optional

from pydantic import model_validator
from pydantic_settings import BaseSettings

# Load .env into os.environ for local/bare runs. pydantic-settings v2 ignores
# the legacy ``class Config: env_file``; under docker-compose env arrives via
# the compose ``environment:`` block instead. override=False so injected env
# wins. No-op when no .env exists.
try:  # pragma: no cover - trivial bootstrap
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass


def _require_env(name: str) -> str:
    """Return env var ``name`` or fail loud (RULE #1).

    A connection string gets NO localhost fallback — a missing value must
    surface at startup rather than silently target a non-existent local DB.
    """
    val = os.getenv(name)
    if not val:
        raise RuntimeError(
            f"Required environment variable {name!r} is not set. Dept IT must "
            f"set it in the MCP's .env; refusing to start on a fallback."
        )
    return val


class Settings(BaseSettings):
    # ── Identity ────────────────────────────────────────────────────────────
    org_id: str = ""                   # Organisation this dept belongs to
    # DEPT_IDS: comma-separated list, e.g. "agri,agri-procurement" — loaded via @property
    mcp_api_key: str = ""             # Shared secret used to register/heartbeat with discovery

    # ── Discovery service ───────────────────────────────────────────────────
    discovery_url: str = ""            # e.g. http://discovery-service:9000
    # NOTE: the keep-alive heartbeat loop was removed (registration is one-shot
    # at startup). This value NO LONGER schedules a heartbeat — it only sizes the
    # per-tool registration leader-lock TTL (_register_lock_ttl in registration.py).
    heartbeat_interval_seconds: int = 60
    # Base URL this MCP advertises to discovery as its query/health endpoint.
    # Defaults (empty) to socket.getfqdn():PORT — correct when every
    # consumer shares the MCP's Docker network. Set this when consumers
    # reach the MCP by a different address than its container hostname
    # (e.g. a host-run smart-app-service hitting a published port:
    # MCP_PUBLIC_BASE_URL=http://host.docker.internal:8501).
    #
    # MULTI-INSTANCE (§2/P2): when N pods of the SAME MCP run, leaving this
    # empty makes every pod advertise its OWN per-pod FQDN — discovery's single
    # query_endpoint then FLAPS (last-writer-wins) to addresses consumers can't
    # route to. Set this to the STABLE gateway/ALB/Traefik service URL, identical
    # on every pod, so discovery stores one routable endpoint. registration.py
    # WARNs loudly at startup when this is unset (per-pod fallback in use).
    mcp_public_base_url: str = ""

    # Stable-per-process instance id — distinguishes this pod from sibling
    # replicas of the same MCP (§2/P1 liveness, §2/P3 leader election). Defaults
    # to the container hostname (compose/k8s set HOSTNAME); resolved lazily so a
    # missing HOSTNAME falls back to the FQDN.
    instance_id: str = ""

    # §2/P1 — deregister-on-shutdown. tool_id is SHARED across instances, so a
    # DELETE on one pod's graceful stop removes the source for ALL consumers
    # (and register_all only runs at startup). Default OFF: rely on discovery's
    # heartbeat TTL to reap a source only when its LAST member stops heartbeating.
    # Set true ONLY for a known single-instance deployment that wants an instant
    # clean-up on shutdown.
    deregister_on_shutdown: bool = False

    # §2/P3 — leader-elected registration. When several instances (or uvicorn
    # workers) of the same MCP start together, every one POSTs the same
    # registration payload — redundant writes + races. With this on, instances
    # contend for a short Redis lock per tool_id and only the winner writes the
    # registration; the rest skip the POST but still heartbeat liveness.
    # Fail-open: if Redis is absent the lock is a no-op and every instance
    # registers (legacy behaviour), so this never blocks a single-node demo.
    register_leader_lock: bool = True

    # ── Milvus (READ ONLY — collections are written by Workflow Engine) ─────
    milvus_uri: str = "http://milvus:19530"
    milvus_token: str = ""
    milvus_vector_dim: int = 768       # Must match the embedding model output dimension
    # Per-deployment namespace for Milvus collections. Must match the
    # ``collection_prefix`` used by VectorSinkNode / StructuredSchemaSinkNode
    # workflow nodes for this dept (default 'mcp').
    milvus_collection_prefix: str = "mcp"

    # ── Audit ingest (smart-app-service) ───────────────────────────────────
    # The MCP holds NO Citra database credentials. Data-plane audit records are
    # POSTed to smart-app-service's /api/audit/ingest (authenticated with an
    # HS256 service token on the shared JWT_SECRET); smart-app-service is the
    # sole writer of dept_query_audit. Unset ⇒ audit is buffered + logged but not
    # shipped (a dev/offline deployment with no platform reachable).
    smart_app_service_url: str = os.getenv("SMART_APP_SERVICE_URL", "")

    # ── Local source registry (SOURCES_FILE) ───────────────────────────────
    # Path to a JSON file holding the source documents (a list, or
    # {"sources": [...]}). This is the ONLY source registry the MCP reads: it
    # loads its sources from THIS FILE and publishes them to the discovery
    # registry on boot, so the MCP runs entirely inside the customer's estate
    # with NO Citra Mongo dependency. REQUIRED (unless SOURCES_JSON is set) —
    # validated below (the legacy central-Mongo `dept_sources` load mode was
    # removed 2026-07-10). Filtered by org_id + dept_ids + is_active.
    sources_file: str = ""

    # Inline alternative to ``sources_file`` for file-free hosts. When set, this
    # holds the source registry payload DIRECTLY as a JSON string (a list, or
    # {"sources": [...]}) — exactly what SOURCES_FILE would contain. Lets a pure
    # env-var runtime (Cloud Run, Container Apps, Fargate, or a k8s ConfigMap
    # env) ship the non-secret registry without mounting or baking in a file.
    # ``sources_file`` takes precedence when BOTH are set. Loaded by
    # router.load_sources_from_file().
    sources_json: str = ""

    # Schema validation of the source registry (registry_models.RegistrySource).
    # TRUE (default): a source that fails validation REFUSES THE BOOT, with every
    # problem in the report — not the first. A registry is config: it is written
    # deliberately, reviewed before deploy, and validate_sources.py exists so this
    # never reaches a running MCP. Serving a half-understood registry is how a
    # typo'd `artifact_roles` silently disabled fraud screening for months.
    #
    # FALSE: log each invalid source loudly and SKIP it, serving the rest. The
    # documented escape hatch for an emergency — a live MCP that must come back
    # up now, with one bad source, rather than not at all. It is not a quiet
    # fallback: the source is absent and the log says exactly why.
    sources_strict: bool = (
        os.getenv("SOURCES_STRICT", "true").strip().lower() in ("1", "true", "yes")
    )

    # Background poll interval (seconds) for the in-memory source registry.
    # DEFAULT 0 = load ONCE at startup (no polling). ``dept_sources`` schema is
    # IT-owned and changes only through change management, so the deliberate act
    # after an edit is to restart the MCP — matching the data-discovery catalogue
    # crawl and the discovery registration, which are also startup-only. Set > 0
    # only if you specifically want hand-authored dept_sources edits to hot-reload
    # on a cadence without a restart (atomic swap). (Was 300s, tied to the now-
    # removed Workflow Engine CatalogueSink; changed 2026-07-01.)
    sources_refresh_seconds: int = 0

    # Max tables/datasets handed to the NL→query planner per request. When a
    # source (or the whole MCP) has MORE tables than this, the relevant ones
    # are picked by ranking — cosine embedding + optional reranker — against
    # the question, so a 100-table source doesn't flood the prompt. This is the
    # dataset-selection cap; it is SEPARATE from the row limit (max_results).
    nl_query_max_datasets: int = 8

    # ── Embedding service (OpenAI-compatible API; needed for /query) ────────
    # Cloud default: baai/bge-m3 via OpenRouter. On-prem: point at the
    # self-hosted inference-service serving the same model. Override via
    # EMBEDDING_* env vars.
    embedding_base_url: str = "https://openrouter.ai/api/v1"
    embedding_model: str = "baai/bge-m3"
    embedding_api_key: str = "not-needed"
    embedding_timeout: float = 30.0
    # Output dimensionality requested from the embeddings endpoint. bge-m3
    # served over an OpenAI-compatible API supports a `dimensions` param to
    # truncate + re-normalise its native 1024-d vector; this MUST match the
    # dim the Milvus collection was ingested at. The whole
    # platform standardises on 768, so that is the default. Set to 0 ONLY for a
    # self-hosted model that ignores the param and emits its own native dim
    # (then set ``milvus_vector_dim`` to that native dim to match).
    embedding_dimension: int = 768

    # ── LLM service (for structured SQL generation at query time) ───────────
    # Cloud demo default: OpenRouter. On-prem: point at the self-hosted
    # inference-service (vLLM). Override via LLM_* env vars.
    llm_base_url: str = "https://openrouter.ai/api/v1"
    llm_model: str = "deepseek/deepseek-chat"
    llm_api_key: str = "not-needed"
    llm_timeout: float = 60.0
    # Provider-specific ``extra_body`` merged into every planner LLM call.
    # For GLM-class reasoning models on OpenRouter set
    # ``LLM_EXTRA_BODY={"reasoning":{"exclude":true}}`` so the model returns
    # SQL in ``content`` instead of leaking it into a reasoning field (which
    # leaves ``content`` null and crashed the planner). Default ``{}`` keeps
    # non-OpenRouter / vLLM backends unaffected. Parsed from JSON env by
    # pydantic-settings.
    llm_extra_body: dict = {}
    # Output-token cap for the NL→SQL planner call. Must cover a reasoning
    # model's hidden reasoning tokens (GLM/DeepSeek spend ~3-5K) PLUS the SQL
    # JSON. max_tokens is an UPPER bound (the model stops when done) and billing
    # is on tokens ACTUALLY generated, so a generous cap is cost-neutral and
    # only PREVENTS truncation. 512 → null content; 2048 still starved GLM under
    # load; 16000 leaves ample headroom for reasoning + the JSON answer.
    llm_planner_max_tokens: int = 16000
    # NL→SQL self-correction attempts. On a SQL execution error the planner is
    # re-prompted with the failing SQL + DB error to fix it, up to this many
    # times. 1 = single-shot (legacy behaviour).
    llm_planner_max_retries: int = 3

    # ── Reranker ─────────────────────────────────────────────────────────────
    reranker_url: str = ""             # e.g. http://reranker-service:7302
    reranker_top_k: int = 5

    # ── Semantic search tuning ───────────────────────────────────────────────
    # fetch_limit raised 20->40 so a batched query has headroom to rank+return
    # the top ~20 DISTINCT chunks. min_score stays 0.25 (sensible global
    # precision floor): the "only 1 chunk returned" bug was the dedup KEY
    # collapsing chunks (source/chunk_index were None) — NOT the threshold; the
    # policy chunks all score 0.35-0.59, well above 0.25. Real fix is in
    # rag/semantic_engine.py (dedup by the unique chunk id).
    semantic_fetch_limit: int = 40
    semantic_min_score: float = 0.25
    # Retrieval planner is a best-effort optimisation — it makes an LLM call
    # per query. Keep this well under the consumer's per-call timeout
    # (Citra-Service budgets 15s for semantic) so a slow/hung planner LLM
    # fails fast and the query falls back to the default flow.
    planner_timeout_seconds: float = 6.0
    # Set false to skip the planner LLM call entirely (default single-query
    # flow). Worth disabling when the configured LLM is slow/unreliable —
    # the planner only ever optimises retrieval, never gates correctness.
    planner_enabled: bool = True

    # ── Structured search tuning ─────────────────────────────────────────────
    structured_table_match_limit: int = 3
    structured_sample_rows_limit: int = 10
    structured_result_row_limit: int = 50

    # ── AuthZ (X-User-JWT verification) ───────────────────────────────────
    # Citra shared-auth model: set jwt_secret to Citra's shared HS256 secret
    # — the SAME JWT_SECRET user-service / smart-app-service sign with — and
    # this MCP verifies every Citra user token against it. The source still
    # belongs to its own customer org; only the auth key is shared with the
    # Citra platform.
    jwt_secret: str = ""               # Citra shared HS256 secret (== user-service JWT_SECRET)
    jwt_leeway_seconds: int = 30
    authz_enforce: bool = True         # FAIL-CLOSED by default

    # ── /query handler durability (timeout + concurrency cap) ───────────────
    # Hard ceiling on a single /query's plan_and_execute chain. A slow LLM /
    # DB round-trip is wrapped in asyncio.wait_for(timeout=this) and a timeout
    # returns HTTP 504 — fail loud rather than hang the caller indefinitely.
    query_timeout_seconds: int = 120
    # Max number of /query plan_and_execute calls running concurrently. Gated
    # behind a module-level asyncio.Semaphore so a backlog of slow LLM/DB
    # chains can't pin unbounded concurrent requests (event-loop starvation /
    # connection-pool exhaustion). Excess requests wait for a permit, bounded
    # by query_timeout_seconds.
    query_max_concurrency: int = 8

    # ── Connection pools (§1 — thread-pool ↔ DB-pool sizing) ─────────────────
    # SQLAlchemy QueuePool per (source, process). The old hard-coded 2+4=6
    # meant the 7th concurrent query on a source queued (pool_timeout). Size
    # these to the per-instance concurrency you actually expect (≈
    # query_max_concurrency), and keep the to_thread executor (below) at least
    # this big so a checked-out connection always has a worker thread to run on.
    db_pool_size: int = 5
    db_max_overflow: int = 10
    db_pool_timeout: int = 30          # seconds a query waits for a free connection
    db_pool_recycle: int = 1800        # recycle conns older than this (stale-conn guard)

    # Default asyncio executor size. Every blocking DB/connector read is
    # offloaded via asyncio.to_thread, which shares ONE default ThreadPool
    # (~min(32, cpu+4) ⇒ ~6 on a 2-vCPU box). Under load that starves the
    # offload path before the DB pool is even busy. >0 installs a right-sized
    # executor at startup (see main.py); 0 keeps the interpreter default.
    thread_pool_max_workers: int = 32

    # ── Logging ──────────────────────────────────────────────────────────────
    # Explicit level wins; empty ⇒ WARNING in prod (per-query INFO is noisy at
    # scale), INFO elsewhere. See main.py.
    log_level: str = ""

    # ── Server ───────────────────────────────────────────────────────────────
    port: int = 8090
    environment: str = "dev"

    class Config:
        env_file = ".env"
        extra = "ignore"

    @model_validator(mode="after")
    def _require_a_source_registry(self):
        """Fail loud (RULE #1) if there is nowhere to load sources from:
        ``SOURCES_FILE`` (a path) OR ``SOURCES_JSON`` (inline payload) must be
        set. (The legacy central-Mongo ``dept_sources`` load mode was removed
        2026-07-10 — the MCP is file-defined.) Deferred to instantiation
        (get_settings) rather than import-time so tooling can import config
        without the env."""
        if not self.sources_file and not self.sources_json:
            raise ValueError(
                "No source registry configured: set SOURCES_FILE to the local "
                "JSON registry file, or SOURCES_JSON to the inline JSON payload "
                "(file-free hosts). Refusing to start without a source of truth.")
        return self

    @property
    def dept_ids(self) -> List[str]:
        raw = os.getenv("DEPT_IDS", "")
        return [d.strip() for d in raw.split(",") if d.strip()]

    @property
    def resolved_instance_id(self) -> str:
        """Stable id for THIS process/pod. Explicit ``INSTANCE_ID`` wins, then
        the container hostname (compose/k8s inject HOSTNAME), then the FQDN."""
        import socket
        return (self.instance_id or os.getenv("HOSTNAME") or socket.getfqdn() or "instance").strip()

    @property
    def resolved_log_level(self) -> str:
        """Effective log level: explicit LOG_LEVEL wins; else WARNING in prod,
        INFO otherwise (per-query INFO logging is too chatty under load)."""
        if self.log_level:
            return self.log_level.upper()
        return "WARNING" if str(self.environment).lower() in ("prod", "production") else "INFO"

    def _dept_seg(self, dept_id: Optional[str]) -> str:
        return _safe(dept_id) if dept_id else (_safe(self.dept_ids[0]) if self.dept_ids else "default")

    def collection_name_semantic(self, source_id: str, dept_id: Optional[str] = None) -> str:
        """Milvus collection produced by VectorSinkNode: <prefix>_<dept>_<source>."""
        return f"{_safe(self.milvus_collection_prefix or 'mcp')}_{self._dept_seg(dept_id)}_{_safe(source_id)}"

    def collection_name_structured(self, source_id: str, dept_id: Optional[str] = None) -> str:
        """Milvus collection produced by StructuredSchemaSinkNode: <prefix>_<dept>_<source>_str."""
        return f"{_safe(self.milvus_collection_prefix or 'mcp')}_{self._dept_seg(dept_id)}_{_safe(source_id)}_str"


def _safe(name: str) -> str:
    """Sanitize identifier for Milvus collection name segments."""
    import re
    return re.sub(r"[^a-zA-Z0-9]", "_", name)[:40].lower()


@lru_cache
def get_settings() -> Settings:
    return Settings()
