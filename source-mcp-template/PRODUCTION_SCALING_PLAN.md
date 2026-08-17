<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: BUSL-1.1

  Licensed under the Business Source License 1.1. Non-production use is granted;
  production use requires a commercial licence until the Change Date, after
  which this file converts to Apache-2.0. See LICENSE at the repository root.
-->

# MCP production scaling plan

`source-mcp-template` is the shared high-performance data plane — many apps call
it, and we run **many instances of the same MCP horizontally**. This is the master
plan for running it (and the whole ~21-service Citra platform) at scale. Parts:
  1. High-concurrency hardening (per-instance + shared deps)
  2. Multi-instance discovery / registration / catalogue
  3. Platform-wide horizontal scaling — all ~21 services behind Traefik
  4. Connector perf parity → see `PERF_PARITY_TODO.md`

---

## 1. High-concurrency hardening

**Already in place (good):** blocking DB/connector reads offloaded via
`asyncio.to_thread` (event loop never freezes); engine/client pooling; plan +
count caches (24h, plan sliding); `/query` concurrency semaphore + timeout;
Postgres statement timeout; fail-loud on connector errors.

**Blockers / levers (none block the demo; they gate "very high perf under load"):**
- **Single uvicorn worker per container** (`CMD uvicorn main:app`, no `--workers`)
  → one event loop + GIL = one CPU core; all `to_thread` work shares the default
  pool (~`min(32, cpu+4)` threads → ~6 on a 2-vCPU box). Fix: `--workers N` /
  `WEB_CONCURRENCY` sized to cores; optionally raise the default executor.
- **Thread-pool ↔ DB-pool mismatch**: pool is `pool_size=2, max_overflow=4` = 6
  conns/source/worker; the 7th concurrent query queues (`pool_timeout` ~30s). Size
  the pool to expected concurrency.
- **Postgres connection storm**: `replicas × sources × 6` can exceed PG
  `max_connections`. Put **PgBouncer (transaction pooling)** in front.
- **Per-cold-query upstreams** (LLM / Milvus / embedding / reranker) have their own
  rate/concurrency limits; the box semaphore doesn't add upstream capacity. Add an
  **embedding cache + dataset-selection cache**, **per-upstream circuit
  breakers/bulkheads**, and size the `/query` semaphore per instance.
- **Redis on the hot path** (every query hits the cache tier): the cache tier is
  fail-open (a stall adds a ~1s socket-timeout, then degrades to no-cache) so it
  needs no HA — but see §3/G3 for the tiering rule (coordination state must NOT sit
  on the evict-prone cache tier).
- **Cache stampede**: cold-start / TTL-expiry → many identical misses hit LLM+DB at
  once. Add a **single-flight lock** on cold keys.
- **Per-replica introspection cache** (in-process, 300s) → each replica
  re-introspects; mass expiry / scale-out = a DB herd. **Move introspection to
  Redis** (shared) to dedupe. (Also ties to §2/P3.)
- **Verbose per-query INFO logging** → WARNING in prod or async logging.
- **Verify the audit write** (`dept_query_audit`) is async/buffered, not a sync
  Mongo write per request.

**Top 3:** (1) workers + right-sized thread/DB pools, (2) PgBouncer,
(3) single-flight + embedding/selection cache.

---

## 2. Multi-instance discovery / registration / catalogue

When N instances of the SAME MCP run, three things must be handled — registration
to discovery, schema publication, and catalogue creation in
data-discovery-service. See `registration.py`.

**Baseline (good):** `tool_id = {org}-{dept}-{source_id}` (`_make_tool_id`) is
**stable across instances** — it has no host/pod component — so discovery's
register upsert converges N instances onto **one logical record per source**. That
prevents N duplicate tools. But three hazards remain:

- **P1 (HIGH) — Deregister-on-shutdown deletes the SHARED record.**
  `deregister_all` sends `DELETE /tools/{tool_id}` on ANY pod shutdown. With a
  shared `tool_id`, one pod's graceful stop (scale-down or rolling deploy)
  **removes the source for ALL consumers**; the heartbeat loop logs failures but
  does NOT re-register, and `register_all` runs only at startup — so the source can
  stay gone until a full restart. Rolling deploys can also race (old pod's delete
  lands after new pod's register).
  **Fix:** make liveness per-instance — discovery tracks a member SET per `tool_id`
  ({instance_id → endpoint, last_heartbeat}); a pod's shutdown removes only its own
  membership; the tool is removed only when the **last** member's heartbeat
  expires. (Or: don't hard-delete on shutdown; rely on discovery heartbeat TTL.)

- **P2 (HIGH) — The advertised endpoint must be the shared gateway URL, not the
  pod.** `_self_query_endpoint()` uses `MCP_PUBLIC_BASE_URL` or, if unset, the
  per-pod `socket.getfqdn()`. N pods writing per-pod FQDNs → the registered
  endpoint **flaps** (last-writer-wins) to addresses consumers can't route to.
  **Fix:** set `MCP_PUBLIC_BASE_URL` to a STABLE ALB/Traefik service URL, identical
  across all pods; discovery stores that service endpoint and the gateway fans out
  to healthy instances. (Or discovery stores per-instance endpoints and
  load-balances itself.)

- **P3 (MED) — Redundant schema introspection + write by every instance.** Every
  pod introspects the source DB and writes its registration/schema on startup →
  N× DB introspection load (a thundering herd; ties to §1's per-replica
  introspection cache) and write races.
  **Fix:** publish schema **once** — a leader-elected pod or an out-of-band
  registration/ingestion job — keyed by a **schema fingerprint** so an unchanged
  schema is a no-op; serving pods only heartbeat liveness.

- **P4 (MED) — Catalogue dedup + re-embed churn in data-discovery-service.** It
  builds `data_catalogue` (a vector index) from discovery. It must **upsert by
  logical source** (`tool_id`/`source_id`) so N registrations/heartbeats → ONE
  catalogue entry, and **re-embed only when the schema fingerprint changes** — else
  N× embedding cost, vector-index pollution, and constant churn.

**Target architecture (separates identity, liveness, schema):**
- **Logical-source registration** — idempotent, one record per `tool_id`; owns
  schema, metadata, visibility, and the GATEWAY endpoint.
- **Per-instance liveness** — pods register/heartbeat into a member set; a tool is
  "down" only when its member set is empty.
- **Schema** — published once by an owner/leader (or ingestion job), fingerprinted;
  unchanged ⇒ no write, no re-embed.
- **Consumers** resolve `tool_id` → gateway URL; the gateway/discovery
  load-balances across live instances.
- **data-discovery** upserts catalogue by `tool_id`; re-embeds on fingerprint
  change only.

---

## 3. Platform-wide horizontal scaling (all ~21 services behind Traefik)

Everything ultimately scales by **replicas behind Traefik + shared state in Redis /
Mongo / Postgres**. Reviewed 2026-06-22 — the platform is largely built for this;
the hard distributed patterns are already in place. Categorize each service:

**Already scale-ready (just add replicas + LB; mind DB pools):**
- Stateless APIs over a shared store: `discovery-service` (stateless over shared
  Mongo `tools` coll + `last_heartbeat` index), `data-discovery-service`,
  `citra-mcp-service`, `reranker-service`, `duckdb-query-service`,
  `Citra-User-Service`/`citra-auth`, `citra-app-runtime` (SSR), `playwright-render-
  service` (per-instance browser pool, but requests are stateless → no affinity).
- **`citra-workflow` scheduler** — already does **Redis leader election** + cron-fire
  lock (at-most-once per window) + overlap lock; the API scales, the cron runs once.
- **`smart-app-service` LLM rate limit** — already **Redis-backed** (atomic INCR Lua,
  shared across instances, fail-loud) → the per-user cap aggregates correctly.
- **`action-chat-service` sandbox affinity** — already **Redis "Pattern C" lease**
  (session → {sandbox_host, adapter_url, container_id}); stateless instances look up
  the lease and route to the host running the user's sandbox.
- `Citra-Service` already runs sharded (×8); `citra-service-utils` ships a shared
  circuit-breaker.

**Genuine remaining gaps:**
- G1 was `collaboration-server`'s lack of horizontal-scale support; moot as of
  2026-08-09 — the service was removed entirely (unused, never wired into any
  live Citra-UI feature).
- **G2 (HIGH) — discovery registration model (= §2 P1/P2).** `discovery-service`
  itself scales (shared Mongo), but the data model is one record per `tool_id` with a
  single `query_endpoint` + `last_heartbeat`: any one MCP instance's shutdown DELETE
  removes the shared source, and the endpoint flaps to a per-pod URL unless
  `MCP_PUBLIC_BASE_URL` = the gateway. Fix per §2 (per-instance member set + gateway
  endpoint), on BOTH the client (`registration.py`) and the discovery schema.
- **G3 (HIGH) — Redis tiering: coordination state is on the wrong tier.** There are
  TWO Redis today (verified from launch cmds):
    - **Cache tier** `citra-redis` — `--maxmemory 512mb --maxmemory-policy
      allkeys-lru`, **no persistence**. Reached via `citra_cache` → `REDIS_HOST`.
    - **Queue tier** `citra-queue-redis` — `--appendonly yes --appendfsync everysec`
      (durable). Reached via `citra_queue` → `QUEUE_REDIS_HOST` (falls back to
      `REDIS_HOST` if unset — make sure it's set in prod, else jobs land on the
      evict-prone cache!).
  The split is GOOD and **relaxes the earlier "all caches need HA" worry** — the
  cache tier is fail-open, evict-OK, **needs no HA** (a flush = cold queries). BUT
  it exposes a real bug: **coordination/state keys ride the LRU cache tier.**
  Confirmed: the **scheduler leader-election + cron-fire locks** (`citra-workflow/
  scheduler.py` via `citra_cache`) and the **per-user rate-limit counters**
  (`smart-app llm_rate_limit` via `citra_cache`) are on `citra-redis` — where
  `allkeys-lru` can **evict a lock/lease/counter** under memory pressure (not just
  cache entries), and a restart wipes them (no persistence). Evicting the
  leader/cron lock can resurrect **duplicate scheduled runs** (the very thing leader
  election prevents — and the cron-fire safety-net lock is on the same evict tier);
  evicting a **session lease** orphans a user's sandbox. **CONFIRMED — `action-chat
  session_lease` uses `cache_manager` (→ same `citra_cache`/`REDIS_HOST` cache
  tier)**; a not-yet-expired lease can be LRU-evicted (or wiped on restart) →
  the live sandbox becomes unroutable, gets re-spawned, and the old one is orphaned.
  **Systemic root cause:** every coordination concern funnels through the single
  `citra_cache` / `cache_manager` (→ `REDIS_HOST` = the LRU cache tier). The fix is
  one wiring change: give locks/leases/counters a durable, `noeviction` keyspace.
  **Fix → treat Redis as THREE logical tiers:**
    1. **Cache** (LRU, no persistence, no HA) — plan/count/embedding/schema caches.
    2. **Queue** (AOF, `noeviction`, HA) — worker jobs.
    3. **Coordination/state** (`noeviction` + persistence + HA) — leader & cron-fire
       locks, session leases, rate-limit counters, idempotency keys. **Move these
       off the `allkeys-lru` cache tier** onto a durable/noeviction store (a separate
       DB/keyspace on the queue Redis, or a dedicated small coordination Redis).
       This is a CORRECTNESS fix, not just HA.
- **G4 (MED) — shared DB bottlenecks at fan-out.** PG connection storm from
  N-replicas × pools → **PgBouncer** (transaction pooling); size Mongo pools; mind
  Milvus + Vault HA.
- **G5 (MED) — per-service workers + Traefik config.** uvicorn `--workers`/
  `WEB_CONCURRENCY` per service (see §1); right-size DB pools; Traefik: sticky
  sessions where needed (collab doc rooms; SSE if not fully Redis-routed), health
  checks, and **disable response buffering on SSE** routes.
- **G6 (MED) — sandbox HOST fleet placement.** Pattern-C routes a session to its
  EXISTING sandbox host, but scaling the `action-sandbox-host` fleet needs
  **placement** (which host spawns a NEW sandbox) + capacity tracking — the lease
  solves routing, not scheduling.
- **G7 (LOW / verify) — SSE progress relay.** Confirm progress streams
  (`action-chat progress_bus`, `Citra-Service sandbox_progress_relay`) are
  Redis-routed or covered by Pattern-C affinity, so an SSE consumer on instance A
  sees progress from work running on instance B.

**Release-blockers before scaling past one instance:** G2 (discovery dedup),
G3 (Redis HA). The rest are throughput/efficiency hardening.

---

## 4. Connector perf parity
See `PERF_PARITY_TODO.md` — extend the SQL path's caching + pooling to
SAP/Salesforce/REST/BigQuery/data-broker connectors when they go live.
