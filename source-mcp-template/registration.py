"""
Dept MCP Server — Discovery Registration Client
=================================================
Lifecycle with the Citra discovery service:

  - On startup: POST /tools/register for every active source pulled from
    the central ``dept_sources`` Mongo collection (filtered by ORG_ID + DEPT_IDS).
  - On shutdown: DELETE /tools/{tool_id} for every registered source
    (DISABLED by default — see ``deregister_all``).

Registration is ONE-SHOT at startup — there is no keep-alive heartbeat loop.
The discovery-service registry is Mongo-persisted (upsert) and has NO
heartbeat-TTL reaper (``/tools/available`` filters on ``active:True`` and never
reads ``last_heartbeat``), so a periodic PUT /heartbeat did nothing for
availability. Registrations change rarely and only via IT change management, so
a registered record simply stands until the next restart re-POSTs it. (The 60s
``_heartbeat_loop`` — which also self-healed a 404 by re-registering — was
removed 2026-07-01.)

Each source registers as its own tool so the Citra routing LLM can select
individual data sources rather than coarse-grained MCP servers.
"""

import logging
import socket
from typing import Any, Dict, List

import httpx

from config import get_settings

logger = logging.getLogger(__name__)

# tool_id → registration payload. Tracked here regardless of which instance
# actually wrote the POST (a non-leader still records it) so ``deregister_all``
# can clean up on shutdown when explicitly enabled.
_registered: Dict[str, Dict[str, Any]] = {}


def _register_lock_ttl() -> int:
    """TTL for the per-tool registration leader lock (§2/P3). One heartbeat
    window (floor 30s) so leadership re-contends cleanly if the winner dies."""
    return max(int(get_settings().heartbeat_interval_seconds or 60), 30)


def _make_tool_id(source: Dict[str, Any]) -> str:
    """Stable tool_id surviving MCP restarts. Multi-dept deployments include dept_id."""
    cfg = get_settings()
    org = source.get("org_id") or cfg.org_id or "default"
    dept = source.get("dept_id") or (cfg.dept_ids[0] if cfg.dept_ids else "default")
    return f"{org}-{dept}-{source['source_id']}"


def _self_base_url() -> str:
    """Base URL this MCP advertises to discovery.

    ``MCP_PUBLIC_BASE_URL`` overrides the default container-hostname URL
    for hybrid deployments where a consumer reaches the MCP by a
    different address (e.g. a published port) than its Docker hostname.
    """
    cfg = get_settings()
    if cfg.mcp_public_base_url:
        return cfg.mcp_public_base_url.rstrip("/")
    return f"http://{socket.getfqdn()}:{cfg.port}"


def _self_query_endpoint() -> str:
    return f"{_self_base_url()}/query"


def _self_health_endpoint() -> str:
    return f"{_self_base_url()}/health"


def _semantic_collection_name(source: Dict[str, Any]) -> str:
    """The Milvus RAG collection a semantic source's chunks live in, published to
    discovery so the platform reader fetches Milvus directly.

    A source's DECLARED ``rag.milvus_collection`` WINS when present — that's how a
    source opts into the SHARED dept-library collection (``mcp_dept_libraries``,
    scalar-isolated by org/dept/source) instead of a per-dept collection. The read
    side (platform reader) reads exactly this name from discovery, and the write
    side (dept-library ingest) writes exactly this collection, so declaring it is
    the single point of truth. Only when a source declares NO collection do we
    fall back to the derived ``<prefix>_<dept>_<source>`` name."""
    declared = ((source.get("rag") or {}).get("milvus_collection") or "").strip()
    if declared:
        return declared
    cfg = get_settings()
    dept_id = source.get("dept_id") or (cfg.dept_ids[0] if cfg.dept_ids else None)
    return cfg.collection_name_semantic(source["source_id"], dept_id)


def _build_registration_payload(source: Dict[str, Any]) -> Dict[str, Any]:
    cfg = get_settings()
    source_id: str = source["source_id"]
    vis: Dict[str, Any] = source.get("visibility") or {}
    # Normalize EXACTLY as router._swap_registry does when it decides what to drop
    # from the query registry — else a source authored `type: "Semantic"` / " semantic "
    # is dropped from /query but advertised with rag_collection OMITTED and a raw
    # source_type, so a consumer comparing `== "semantic"` misroutes it to the
    # dept-MCP /query → 404. Advertise the normalized value.
    source_type: str = str(source.get("type") or "semantic").strip().lower()
    dept_id = source.get("dept_id") or (cfg.dept_ids[0] if cfg.dept_ids else "")

    payload = {
        "tool_id": _make_tool_id(source),
        "name": source.get("name") or source_id,
        "description": _build_description(source),
        # A semantic source is answered by the platform RAG reader, NOT this MCP's
        # /query (which would 404 — it serves no RAG). Advertise an EMPTY
        # query_endpoint so a stale/naive consumer can't route a RAG read to the
        # dead address; consumers branch on source_type=="semantic" to the reader.
        "query_endpoint": "" if source_type == "semantic" else _self_query_endpoint(),
        "health_endpoint": _self_health_endpoint(),
        # §2/P1 — the emitting instance. Discovery can ignore this today; it's
        # the client-side hook for the per-instance member-set liveness model
        # (a tool is "down" only when its LAST member stops heartbeating).
        "instance_id": cfg.resolved_instance_id,
        "source_id": source_id,
        "source_type": source_type,
        "org_ids": [source.get("org_id") or cfg.org_id] if (source.get("org_id") or cfg.org_id) else [],
        "dept_ids": [dept_id] if dept_id else cfg.dept_ids,
        "visibility": {
            "roles_allowed": vis.get("roles_allowed", ["user"]),
            "cross_org_ids": vis.get("cross_org_ids", []),
            "public_within_org": vis.get("public_within_org", False),
        },
        "api_key": cfg.mcp_api_key,
        "tags": source.get("tags", []),
        "data_types": _data_types_for(source_type),
        "taxonomy": source.get("taxonomy") or None,
        "query_timeout_seconds": source.get("query_timeout_seconds"),
        # Capability flag — citra-app-builder reads this from discovery to
        # decide whether to author a "Refresh from History" workflow when a
        # Smart App picks this source. Defaults to False if not set.
        "supports_history": bool(source.get("supports_history", False)),
    }
    # Semantic (RAG) sources: publish the Milvus collection so the platform reader
    # fetches it DIRECTLY. Consumers branch on source_type=="semantic" and never
    # call query_endpoint for these (the MCP serves no RAG — pure disconnect).
    if source_type == "semantic":
        payload["rag_collection"] = _semantic_collection_name(source)
    return payload


def _data_types_for(source_type: str) -> List[str]:
    if source_type == "structured":
        return ["structured"]
    if source_type == "rest_api":
        return ["api"]
    if source_type == "mongodb":
        return ["documents", "structured"]
    return ["documents"]


def _build_description(source: Dict[str, Any]) -> str:
    """Compose the description shown to the routing LLM.

    For rest_api sources we append the operator-supplied
    ``options.invocation_template`` so the LLM understands how to phrase
    upstream calls.

    For sources that declare a ``taxonomy`` block we render the doc_type
    vocabulary (label, synonyms, examples) so the routing LLM knows which
    ``doc_types`` filter values to send on /query.
    """
    base = (source.get("description") or "").strip()
    parts: List[str] = [base] if base else []

    if source.get("type") == "rest_api":
        invocation = (source.get("options") or {}).get("invocation_template")
        if invocation:
            parts.append(f"Invocation hint: {str(invocation).strip()}")

    taxonomy_text = _render_taxonomy(source.get("taxonomy"))
    if taxonomy_text:
        parts.append(taxonomy_text)

    return "\n\n".join(p for p in parts if p).strip()


def _render_taxonomy(taxonomy: Any) -> str:
    """Render a source's taxonomy block as a compact prompt hint.

    Expected shape (from dept_sources Mongo doc)::

        taxonomy:
          doc_types:
            - id: policy
              label: HR Policies
              synonyms: [rule, guideline, code of conduct]
              examples: ["Leave Policy v3", "Anti-Harassment Policy"]
            - id: sop
              ...
          classification_levels: [public, internal, confidential]
    """
    if not isinstance(taxonomy, dict):
        return ""
    doc_types = taxonomy.get("doc_types") or []
    if not isinstance(doc_types, list) or not doc_types:
        return ""

    lines: List[str] = [
        "Filter on `doc_types` when the user's intent maps to one of these "
        "document categories (omit otherwise to search all):"
    ]
    for entry in doc_types:
        if not isinstance(entry, dict):
            continue
        dt_id = str(entry.get("id") or "").strip()
        if not dt_id:
            continue
        label = str(entry.get("label") or "").strip()
        synonyms = entry.get("synonyms") or []
        examples = entry.get("examples") or []
        bits = [f"- {dt_id}"]
        if label:
            bits.append(f"({label})")
        if isinstance(synonyms, list) and synonyms:
            bits.append(f"— synonyms: {', '.join(str(s) for s in synonyms[:6])}")
        if isinstance(examples, list) and examples:
            bits.append(f"— e.g. {'; '.join(str(e) for e in examples[:3])}")
        lines.append(" ".join(bits))

    cls_levels = taxonomy.get("classification_levels") or []
    if isinstance(cls_levels, list) and cls_levels:
        lines.append(
            f"Classification levels available: {', '.join(str(c) for c in cls_levels)}."
        )
    return "\n".join(lines)


async def _post_register(
    client: httpx.AsyncClient, tool_id: str, payload: Dict[str, Any]
) -> bool:
    """POST /tools/register for one tool. Returns True on success."""
    cfg = get_settings()
    try:
        resp = await client.post(
            f"{cfg.discovery_url}/tools/register",
            json=payload,
            headers={"X-API-Key": cfg.mcp_api_key},
        )
        resp.raise_for_status()
        logger.info(f"✅ [REGISTRATION] Registered tool: {tool_id} ({payload.get('name')})")
        return True
    except Exception as exc:
        logger.error(f"❌ [REGISTRATION] Failed to register {tool_id}: {exc}")
        return False


async def register_all(sources: List[Dict[str, Any]]) -> None:
    """Register every source with discovery. Called once at startup.

    Multi-instance behaviour (§2):
      • P2 — warns when ``MCP_PUBLIC_BASE_URL`` is unset, because each pod then
        advertises its own per-pod endpoint and the shared record's
        ``query_endpoint`` flaps to addresses consumers can't route to.
      • P3 — when ``register_leader_lock`` is on, instances contend for a short
        Redis lock per tool_id and only the winner POSTs the registration; the
        rest skip the write but still HEARTBEAT liveness. Fail-open: no Redis ⇒
        every instance registers (legacy single-node behaviour).
    """
    import plan_cache

    cfg = get_settings()
    if not cfg.discovery_url:
        logger.warning("⚠️ [REGISTRATION] DISCOVERY_URL not set — skipping registration")
        return
    if not cfg.mcp_api_key:
        logger.warning("⚠️ [REGISTRATION] MCP_API_KEY not set — skipping registration")
        return

    # P2 — per-pod endpoint flapping risk under multiple replicas.
    if not cfg.mcp_public_base_url:
        logger.warning(
            "⚠️ [REGISTRATION] MCP_PUBLIC_BASE_URL unset — advertising the per-pod "
            "endpoint %s. Fine for a single instance; under multiple replicas the "
            "discovery query_endpoint FLAPS (last-writer-wins) to per-pod addresses "
            "consumers can't route to. Set MCP_PUBLIC_BASE_URL to the stable "
            "gateway/ALB URL, identical on every pod.", _self_query_endpoint(),
        )

    use_lock = bool(cfg.register_leader_lock)
    ttl = _register_lock_ttl()

    wrote_any = False
    async with httpx.AsyncClient(timeout=15.0) as client:
        for source in sources:
            tool_id = _make_tool_id(source)
            payload = _build_registration_payload(source)
            # Track REGARDLESS of who writes the registration so deregister_all
            # can clean up on shutdown when explicitly enabled.
            _registered[tool_id] = payload
            if use_lock and not plan_cache.try_acquire_lock(f"register:{tool_id}", ttl):
                logger.info(
                    "⏭️ [REGISTRATION] %s — another instance holds the registration "
                    "lock; this instance skips the POST (the leader's upsert stands)",
                    tool_id,
                )
                continue
            await _post_register(client, tool_id, payload)
            wrote_any = True

        # Staleness reconcile: deactivate THIS registrant's tools that are no
        # longer in SOURCES_FILE (a renamed/removed source otherwise lingers
        # active forever — no heartbeat reaper, deregister-on-shutdown off — and
        # keeps misleading the builder). Ownership = shared registration api_key.
        # Guarded on a non-empty source list so a broken/empty SOURCES_FILE can
        # never mass-deactivate a healthy registry, and on wrote_any so a
        # leader-lock LOSER stays write-silent (the leader reconciles with the
        # identical list). Fail-soft: reconcile is hygiene, not a boot
        # dependency — but LOG loud on failure.
        if sources and wrote_any:
            try:
                resp = await client.post(
                    f"{cfg.discovery_url}/tools/reconcile",
                    json={
                        "api_key": cfg.mcp_api_key,
                        "active_tool_ids": list(_registered.keys()),
                        # The ownership scope. The api_key alone is NOT ownership:
                        # smart-app-service holds one fleet-wide
                        # MCP_SERVICE_API_KEY, so every dept MCP presents the
                        # same key and an unscoped sweep retires other
                        # departments' sources. We may retire only what we serve.
                        "org_id": cfg.org_id,
                        "dept_ids": list(cfg.dept_ids or []),
                    },
                )
                resp.raise_for_status()
                gone = (resp.json() or {}).get("deactivated") or []
                if gone:
                    logger.warning(
                        "🧹 [REGISTRATION] Reconcile deactivated %d stale tool(s) "
                        "no longer in SOURCES_FILE: %s", len(gone), gone,
                    )
            except Exception as exc:  # noqa: BLE001 — hygiene step, never block boot
                logger.warning(
                    "⚠️ [REGISTRATION] Stale-tool reconcile failed (registry may "
                    "still advertise removed sources until it succeeds): %s", exc,
                )


async def deregister_all() -> None:
    """Deregister on shutdown — DISABLED by default (§2/P1).

    ``tool_id`` is SHARED across instances, so a DELETE on one pod's graceful
    stop removes the source for ALL consumers, and ``register_all`` only runs at
    startup — the source can stay gone until a full restart. Default behaviour
    leaves the record in place: the registry is Mongo-persisted and there is no
    heartbeat-TTL reaper, so a registration simply stands until the next restart
    re-POSTs it (or an explicit DELETE removes it). Set DEREGISTER_ON_SHUTDOWN=true
    only for a deliberately single-instance deployment.
    """
    cfg = get_settings()
    if not cfg.discovery_url or not _registered:
        return
    if not cfg.deregister_on_shutdown:
        logger.info(
            "🫥 [REGISTRATION] deregister-on-shutdown disabled (multi-instance safe) "
            "— leaving %d tool(s) registered; the persisted record stands until the "
            "next restart re-registers it", len(_registered),
        )
        return
    async with httpx.AsyncClient(timeout=10.0) as client:
        for tool_id in list(_registered.keys()):
            try:
                resp = await client.request(
                    "DELETE",
                    f"{cfg.discovery_url}/tools/{tool_id}",
                    json={"api_key": cfg.mcp_api_key},
                )
                resp.raise_for_status()
                logger.info(f"🗑️ [REGISTRATION] Deregistered: {tool_id}")
            except Exception as exc:
                logger.warning(f"⚠️ [REGISTRATION] Deregister failed for {tool_id}: {exc}")


# NOTE: the 60s heartbeat loop (start_heartbeat / stop_heartbeat /
# _heartbeat_loop) was removed 2026-07-01. The discovery registry is
# Mongo-persisted with no heartbeat-TTL reaper, so the keep-alive did nothing
# for availability; its only side-effect was re-registering on a 404, an edge
# case (records aren't reaped and deregister is disabled). Registration is now
# purely startup-driven — a restart re-POSTs every source.
