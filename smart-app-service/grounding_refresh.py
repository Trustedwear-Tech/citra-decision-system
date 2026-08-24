# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""Few-shot-from-history grounding refresh — server-side, no workflow engine.

The Smart App "few-shot from history" feature grounds an agent in the tenant's
own past decisions: at runtime the ``neighbor_samples`` tool loads the nearest
historical records (the inputs a team saw + the decision they made) into the
prompt. This module is the **writer** that (re)builds the Milvus collection the
tool reads (``samples_<agent_id>``).

It used to be a Citra workflow (``wf_refresh_history``). With the workflow
engine removed, the same pipeline lives here as a deterministic — no LLM —
smart-app-service operation, driven by an ``AgentSpec.grounding`` contract and
invoked at /publish and on demand (``POST /apps/{slug}/grounding/refresh``).

Pipeline (mirrors the old workflow nodes):

    pull → package(PII scrub + dedupe) → select(canonical few-shots)
         → GUARD (Gate B) → atomic swap into samples_<agent_id>

The GUARD runs BEFORE the destructive swap; the new corpus is built in a fresh
physical collection and the read alias is only re-pointed once everything
passed, so a degraded pull can never replace good live samples.

Fail-loud: every I/O failure raises ``GroundingRefreshError`` with a ``code``;
callers surface it. Nothing is swallowed and no partial corpus is published.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from config import Settings
from models import GroundingContract

logger = logging.getLogger(__name__)


class GroundingRefreshError(Exception):
    def __init__(self, msg: str, code: str = "grounding_refresh_failed"):
        super().__init__(msg)
        self.code = code


@dataclass
class GuardResult:
    ok: bool
    failures: List[str] = field(default_factory=list)
    # Stats computed during evaluation (surfaced to the caller / audit).
    total_packaged: int = 0
    canonical_count: int = 0
    live_count: int = 0
    decision_classes: List[str] = field(default_factory=list)
    decision_fill_rate: float = 0.0


@dataclass
class RefreshResult:
    collection: str
    physical_collection: str
    total_samples: int
    canonical_samples: int
    neighbor_samples: int
    decision_classes: List[str]
    guard: GuardResult
    # The curated samples (Mongo source-of-truth / audit copy). The Milvus
    # collection holds their embedded vectors; this is the readable record.
    samples: List[Dict[str, Any]] = field(default_factory=list)


# ── PII scrub ───────────────────────────────────────────────────────────────
# Few-shots go verbatim into the LLM prompt; scrub obvious direct identifiers
# from free-text values. This is a coarse safety net, NOT a DLP engine — the
# field selection (input_fields) is the primary control.
_PII_PATTERNS = [
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "<email>"),
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "<ssn>"),  # US SSN (3-2-4)
    (re.compile(r"\b\d{2}-\d{7}\b"), "<ein>"),  # US EIN (2-7)
    (re.compile(r"\b(?:\+?\d[\d\s-]{8,}\d)\b"), "<phone>"),
    (re.compile(r"\b\d{12,19}\b"), "<acct>"),  # long account / card-like runs
    (re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b"), "<pan>"),  # India PAN
    (re.compile(r"\b\d{4}\s?\d{4}\s?\d{4}\b"), "<aadhaar>"),  # India Aadhaar
]
_PII_FIELD_HINTS = ("email", "phone", "mobile", "aadhaar", "pan", "ssn", "account_no", "card")


def _scrub_value(key: str, value: Any) -> Any:
    if isinstance(value, str):
        kl = key.lower()
        if any(h in kl for h in _PII_FIELD_HINTS):
            return "<redacted>"
        out = value
        for pat, repl in _PII_PATTERNS:
            out = pat.sub(repl, out)
        return out
    return value


# ── Stage: package ────────────────────────────────────────────────────────
def package_rows(
    rows: List[Dict[str, Any]], contract: GroundingContract
) -> List[Dict[str, Any]]:
    """Map raw historical rows → sample envelopes, PII-scrubbed and deduped.

    Pure. Each sample: {source_id, input{}, output{}, decision, severity,
    reasoning, _input_text}. Rows missing a decision OR the id field are
    dropped (a sample with no recorded outcome teaches nothing).
    """
    samples: List[Dict[str, Any]] = []
    seen_ids: set = set()
    for r in rows:
        if not isinstance(r, dict):
            continue
        sid = r.get(contract.source_id_field)
        decision = r.get(contract.decision_field)
        if sid is None or decision is None or str(decision).strip() == "":
            continue
        # Only COMPLETED decisions become few-shots. When the contract names the
        # decided/terminal states, drop in-progress rows (e.g. 'pending') so the
        # agent grounds on decisions actually made, not the current status.
        if contract.terminal_states and str(decision) not in contract.terminal_states:
            continue
        sid = str(sid)
        if sid in seen_ids:
            continue
        seen_ids.add(sid)

        inp = {f: _scrub_value(f, r.get(f)) for f in contract.input_fields if f in r}
        out_fields = contract.output_fields or [contract.decision_field]
        outp = {f: _scrub_value(f, r.get(f)) for f in out_fields if f in r}
        reasoning = None
        if contract.reasoning_field and r.get(contract.reasoning_field) is not None:
            reasoning = _scrub_value(contract.reasoning_field, r.get(contract.reasoning_field))
        severity = None
        if contract.severity_field and r.get(contract.severity_field) is not None:
            severity = str(r.get(contract.severity_field))

        samples.append({
            "source_id": sid,
            "input": inp,
            "output": outp,
            "decision": str(decision),
            "severity": severity,
            "reasoning": str(reasoning) if reasoning is not None else None,
            # Deterministic text used for embedding + dedupe of identical inputs.
            "_input_text": json.dumps(inp, sort_keys=True, separators=(",", ":"), default=str),
        })
    return samples


# ── Stage: write-back (loop decisions → samples) ────────────────────────────
def loop_decision_to_sample(
    rec: Dict[str, Any], contract: GroundingContract
) -> Optional[Dict[str, Any]]:
    """Convert a settled DecisionRecord (the loop's OWN observed decision) into a
    grounding sample envelope, matching ``package_rows`` output.

    Pass only GOOD-outcome decisions here — they become positive few-shots so the
    agent grounds on its own validated successes, INCLUDING the officer's
    corrected value when a recommendation was overridden (the committed payload
    reflects the correction). Returns None when the record lacks a usable
    input/output. Pure — no I/O.
    """
    context = rec.get("context") or {}
    # The committed payload = the validated-good OUTPUT (post-override). Prefer
    # the actual write args captured in record_keys; else the planned payload.
    committed: Dict[str, Any] = {}
    for rk in (rec.get("record_keys") or []):
        if isinstance(rk, dict) and isinstance(rk.get("payload"), dict):
            committed.update(rk["payload"])
    if not committed:
        pws = ((rec.get("recommendation") or {}).get("planned_writes")) or []
        first = pws[0] if pws else {}
        if isinstance(first, dict) and isinstance(first.get("payload"), dict):
            committed = dict(first["payload"])
    if not context and not committed:
        return None

    inp = {f: _scrub_value(f, context.get(f)) for f in contract.input_fields if f in context}
    # If input_fields don't line up with the loop context keys, fall back to the
    # whole scrubbed context so we still teach something, not an empty input.
    if not inp and context:
        inp = {k: _scrub_value(k, v) for k, v in context.items()}

    out_fields = contract.output_fields or list(committed.keys())
    outp = {f: _scrub_value(f, committed.get(f)) for f in out_fields if f in committed}

    rec_obj = rec.get("recommendation") or {}
    # Decision class: prefer the contract's decision field from the committed
    # output, else the recommendation's decision/action label.
    decision = (
        committed.get(contract.decision_field)
        if committed.get(contract.decision_field) is not None
        else rec_obj.get("decision") or rec_obj.get("action")
    )
    if decision is None or str(decision).strip() == "":
        return None

    # OVERRIDE-AS-CORRECTION SIGNAL: when the officer changed the AI's
    # recommendation, encode the correction into the few-shot's reasoning, so a
    # retrieved sample tells the model "here, your kind of recommendation was
    # corrected from X to Y" — the contrastive signal a static history table
    # (which stores ONLY the final committed value) cannot provide. The reader
    # already surfaces reasoning_trace, so this needs no schema/reader change.
    corrections: List[str] = []
    for ov in (rec.get("overrides") or []):
        for fld, fromto in ((ov or {}).get("override") or {}).items():
            if isinstance(fromto, dict):
                corrections.append(f"{fld}: {fromto.get('from')!r} -> {fromto.get('to')!r}")
    _reasoning = rec_obj.get("reasoning")
    _reasoning = str(_reasoning) if _reasoning is not None else None
    if corrections:
        _note = (
            "Officer CORRECTED the AI recommendation - " + "; ".join(corrections)
            + ". Prefer the corrected decision for similar cases."
        )
        _reasoning = (_note + " " + _reasoning) if _reasoning else _note

    # POLARITY (the "outcome flow"): a rejected / bad-outcome decision becomes an
    # ANTI-PATTERN few-shot, so the model is shown "do NOT do this" — the signal a
    # positives-only history table cannot give. Marked unmistakably in BOTH the
    # decision label ("AVOID: ...") and the reasoning, so the LLM reading the
    # retrieved sample treats it as something to avoid, not to follow. Negatives
    # are pulled for ALL modes (learning to avoid is always safe); positives only
    # for human_approved (echo-chamber-safe — see _fetch_loop_samples).
    is_negative = ((rec.get("outcome") or {}).get("label") == "bad")
    decision_str = str(decision)
    if is_negative:
        decision_str = "AVOID: " + decision_str
        _anti = ("ANTI-PATTERN: this decision was rejected or turned out badly - "
                 "do NOT repeat it for similar inputs.")
        _reasoning = (_anti + " " + _reasoning) if _reasoning else _anti

    severity = (
        str(committed.get(contract.severity_field))
        if contract.severity_field and committed.get(contract.severity_field) is not None
        else None
    )
    return {
        "source_id": str(rec.get("decision_id")),
        "input": inp,
        "output": outp,
        "decision": decision_str,
        "severity": severity,
        "reasoning": _reasoning,
        "_input_text": json.dumps(inp, sort_keys=True, separators=(",", ":"), default=str),
        "from_loop": True,        # provenance; ignored by the Milvus writer
        "from_override": bool(corrections),
        "polarity": "bad" if is_negative else "good",
    }


# ── Stage: select ─────────────────────────────────────────────────────────
def select_samples(
    samples: List[Dict[str, Any]], contract: GroundingContract
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Choose the canonical few-shot set; return (canonical, neighbor_pool).

    Pure + deterministic (no RNG — selection is index/round-robin based so
    re-runs are stable). Diversity strategy: round-robin across decision
    classes so every class is represented, honouring ``per_decision_min``,
    up to ``target_count``. The FULL set is the neighbor pool (canonical rows
    are flagged, not removed — the runtime excludes canonicals in neighbors
    mode via the filter).
    """
    by_class: Dict[str, List[Dict[str, Any]]] = {}
    for s in samples:
        by_class.setdefault(s["decision"], []).append(s)

    chosen: List[Dict[str, Any]] = []
    chosen_ids: set = set()

    # 1) guarantee per_decision_min from each class
    for cls, items in by_class.items():
        for s in items[: contract.per_decision_min]:
            if s["source_id"] not in chosen_ids and len(chosen) < contract.target_count:
                chosen.append(s)
                chosen_ids.add(s["source_id"])

    # 2) round-robin fill to target_count
    cursors = {cls: contract.per_decision_min for cls in by_class}
    progressed = True
    while len(chosen) < contract.target_count and progressed:
        progressed = False
        for cls, items in by_class.items():
            i = cursors.get(cls, 0)
            if i < len(items):
                s = items[i]
                cursors[cls] = i + 1
                progressed = True
                if s["source_id"] not in chosen_ids:
                    chosen.append(s)
                    chosen_ids.add(s["source_id"])
                    if len(chosen) >= contract.target_count:
                        break

    canonical_ids = {s["source_id"] for s in chosen}
    for s in samples:
        s["is_canonical"] = s["source_id"] in canonical_ids
    return chosen, samples


# ── Stage: guard (Gate B) ───────────────────────────────────────────────────
def evaluate_guard(
    packaged: List[Dict[str, Any]],
    canonical: List[Dict[str, Any]],
    rows_pulled: int,
    live_count: int,
    contract: GroundingContract,
    decided_count: Optional[int] = None,
) -> GuardResult:
    """Decide whether the refreshed corpus is safe to swap in (pure).

    Rejects (leaving live data intact) when the new set is empty, below the
    sample/canonical floors, missing a required decision class, has too low a
    decision fill rate, or shrinks the live corpus past ``shrink_floor``.
    """
    failures: List[str] = []
    total = len(packaged)
    classes = sorted({s["decision"] for s in packaged})
    # Fill rate answers "what fraction of the rows we PULLED carried a usable
    # decision" — a data-quality signal. It must therefore be measured on the
    # packaged set, BEFORE the fixed-size pool cap
    # (``grounding_max_rows_per_agent``), which is an unrelated cost bound.
    # Measuring the capped pool instead made the cap itself trip the guard:
    # any source with more decided rows than the cap failed, e.g. 300/5000 =
    # 0.06 against a 0.3 floor even though every pulled row was decided.
    # ``decided_count`` carries the pre-cap count; falling back to ``total``
    # keeps older callers behaving as before.
    decided = total if decided_count is None else decided_count
    fill_rate = (decided / rows_pulled) if rows_pulled else 0.0

    if total < contract.min_samples:
        failures.append(
            f"packaged {total} samples < min_samples {contract.min_samples}"
        )
    if len(canonical) < contract.min_canonical:
        failures.append(
            f"canonical {len(canonical)} < min_canonical {contract.min_canonical}"
        )
    missing = [c for c in contract.required_decision_classes if c not in classes]
    if missing:
        failures.append(f"missing required decision classes: {missing}")
    if rows_pulled and fill_rate < contract.min_decision_fill_rate:
        failures.append(
            f"decision fill rate {fill_rate:.2f} < min {contract.min_decision_fill_rate}"
        )
    if live_count > 0 and total < int(live_count * contract.shrink_floor):
        failures.append(
            f"new corpus {total} shrinks live {live_count} past shrink_floor "
            f"{contract.shrink_floor} (floor={int(live_count * contract.shrink_floor)})"
        )

    return GuardResult(
        ok=not failures,
        failures=failures,
        total_packaged=total,
        canonical_count=len(canonical),
        live_count=live_count,
        decision_classes=classes,
        decision_fill_rate=fill_rate,
    )


# ONE shared Milvus collection holds EVERY agent's grounding samples — rows are
# isolated by an ``agent_id`` scalar field, not by per-agent collections. This
# keeps grounding to a single collection regardless of how many agents are
# grounded (respects constrained Milvus deployments, e.g. Zilliz free tier's
# 5-collection cap). The NeighborSamplesTool.collection is this name for all
# agents; the runtime reader adds an ``agent_id == ...`` filter.
GROUNDING_COLLECTION = "Historical_Refresh"


def collection_name_for(agent_id: str) -> str:
    """The shared grounding collection the runtime reads (same for all agents;
    rows are isolated by the ``agent_id`` field)."""
    return GROUNDING_COLLECTION


# ── I/O: historical pull (dept-MCP /run_query) ──────────────────────────────
async def _pull_history(
    *, settings: Settings, user_jwt: str, contract: GroundingContract
) -> List[Dict[str, Any]]:
    """Pull closed/decided rows for the contract's dataset via dept-MCP.

    Reuses the same structured-read path the panel data layer uses
    (resolve_source → POST …/run_query → coerce rows).
    """
    import httpx
    from discovery_cache import DiscoveryError, resolve_source
    from http_client import get_http_client
    from panel_data import _coerce_rows, _run_query_url, _SQL_QUERY_KINDS
    from proxy_clients import _is_service_token

    try:
        # Grounding always pulls the REAL (prod/current) domain history — never
        # the ephemeral test plane — so the agent is grounded on actual past
        # decisions even while the BA is still testing the app.
        resolved = await resolve_source(
            discovery_url=settings.discovery_url_for("current"),
            user_jwt=user_jwt,
            source_id=contract.source_id,
            cache_ttl_seconds=settings.discovery_cache_ttl_seconds,
        )
    except DiscoveryError as e:
        raise GroundingRefreshError(f"source resolve failed: {e}", code="source_unresolved") from e
    if not resolved.query_endpoint:
        raise GroundingRefreshError("source has no query endpoint", code="source_unresolved")

    kind = (contract.source_kind or "sql").lower()
    api_key = resolved.api_key or settings.mcp_service_api_key
    if not api_key:
        raise GroundingRefreshError("no api_key for source", code="mcp_no_api_key")
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    if user_jwt and not _is_service_token(user_jwt):
        headers["X-User-JWT"] = user_jwt
    timeout = resolved.query_timeout_seconds or settings.mcp_call_timeout_seconds or 60
    url = _run_query_url(resolved.query_endpoint)

    # The dept-MCP /run_query caps row_limit at 500, so a larger pull is
    # paginated. (For SQL kinds we build LIMIT/OFFSET pages; an explicit
    # contract.query or a non-SQL source is a single capped request.)
    MCP_MAX = 500

    async def _post_page(query_val: Any, page_limit: int) -> List[Dict[str, Any]]:
        body = {
            "source_id": contract.source_id,
            "dataset_id": contract.dataset_id,
            "kind": kind,
            "query": query_val,
            "row_limit": page_limit,
        }
        try:
            client = get_http_client()
            resp = await client.post(url, json=body, headers=headers, timeout=timeout)
        except httpx.HTTPError as e:
            raise GroundingRefreshError(f"dept-MCP unreachable: {e}", code="mcp_unreachable") from e
        if resp.status_code >= 400:
            raise GroundingRefreshError(
                f"dept-MCP returned {resp.status_code}: {resp.text[:300]}", code="mcp_rejected"
            )
        try:
            payload = resp.json()
        except ValueError as e:
            raise GroundingRefreshError("dept-MCP returned non-JSON", code="mcp_bad_json") from e
        if isinstance(payload, dict) and payload.get("error"):
            raise GroundingRefreshError(f"dept-MCP error: {payload['error']}", code="mcp_error")
        return _coerce_rows(payload)

    all_rows: List[Dict[str, Any]] = []
    if kind in _SQL_QUERY_KINDS and not contract.query:
        from panel_data import _where_clause  # SELECT predicate builder
        table = (
            contract.dataset_id.split(".", 1)[1].strip()
            if "." in contract.dataset_id else contract.dataset_id
        )
        # Pull only DECIDED rows: merge the terminal-state filter into the
        # predicate (decision_field IN terminal_states) so in-progress rows
        # (e.g. 'pending') never reach the sample set.
        pred = dict(contract.filters or {})
        if contract.terminal_states:
            # $in → SQL "IN (...)" (a bare list would render as "= '<list>'").
            pred[contract.decision_field] = {"$in": list(contract.terminal_states)}
        where = _where_clause(pred)
        offset = 0
        while len(all_rows) < contract.max_results:
            page = min(MCP_MAX, contract.max_results - len(all_rows))
            sql = f"SELECT * FROM {table}{where} LIMIT {int(page)} OFFSET {int(offset)}"
            rows = await _post_page(sql, page)
            all_rows.extend(rows)
            if len(rows) < page:  # last page
                break
            offset += page
    else:
        query_val = contract.query or (contract.filters or {})
        all_rows = await _post_page(query_val, min(contract.max_results, MCP_MAX))

    return all_rows[: contract.max_results]


# ── I/O: embeddings ─────────────────────────────────────────────────────────
def _embed_texts(texts: List[str], settings: Settings) -> List[List[float]]:
    """Batch-embed via the OpenAI-compatible endpoint (same contract the
    neighbor_samples reader uses). Sync — called inside a thread executor."""
    try:
        from openai import OpenAI  # type: ignore
    except ImportError as exc:
        raise GroundingRefreshError(f"openai client not installed: {exc}", code="embed_unavailable") from exc
    base_url = settings.embedding_base_url or None
    api_key = settings.embedding_api_key or "EMPTY"
    model = settings.embedding_model or "baai/bge-m3"
    dim = settings.embedding_dimension
    client = OpenAI(base_url=base_url, api_key=api_key)
    out: List[List[float]] = []
    BATCH = 128
    for i in range(0, len(texts), BATCH):
        chunk = texts[i : i + BATCH]
        # Pass dimensions so vectors match the Citra-Service space (default 768).
        resp = client.embeddings.create(model=model, input=chunk, dimensions=dim)
        if not resp.data or len(resp.data) != len(chunk):
            raise GroundingRefreshError("embedding returned wrong count", code="embed_failed")
        out.extend(list(d.embedding) for d in resp.data)
    return out


# ── I/O: Milvus atomic-swap writer ──────────────────────────────────────────
_VARCHAR_LONG = 8192
_VARCHAR_SHORT = 512


def _milvus_client(settings: Settings):
    try:
        from pymilvus import MilvusClient  # type: ignore
    except ImportError as exc:
        raise GroundingRefreshError(f"pymilvus not installed: {exc}", code="milvus_unavailable") from exc
    if not settings.milvus_uri:
        raise GroundingRefreshError("MILVUS_URI not configured", code="milvus_unconfigured")
    kwargs: Dict[str, Any] = {"uri": settings.milvus_uri, "timeout": 30}
    if settings.milvus_token:
        kwargs["token"] = settings.milvus_token
    return MilvusClient(**kwargs)


def _live_count(client, agent_id: str) -> int:
    """How many samples this agent currently has in the shared collection.

    Returns 0 ONLY when the collection genuinely doesn't exist yet (a legitimate
    "no live corpus"). A Milvus query FAILURE is NOT 0 — it raises, because a
    swallowed error here would silently report 0 live samples and disable the
    shrink-floor guard (``evaluate_guard`` only protects when ``live_count>0``),
    letting a degraded refresh replace a good corpus with a tiny one."""
    try:
        if not client.has_collection(GROUNDING_COLLECTION):
            return 0
    except Exception as e:  # noqa: BLE001
        raise GroundingRefreshError(
            f"milvus has_collection failed during live-count: {e}",
            code="live_count_failed",
        ) from e
    safe = (agent_id or "").replace('"', '\\"')
    try:
        rows = client.query(
            collection_name=GROUNDING_COLLECTION,
            filter=f'agent_id == "{safe}"',
            output_fields=["agent_id"],
            limit=16384,
        )
    except Exception as e:  # noqa: BLE001
        raise GroundingRefreshError(
            f"milvus live-count query failed: {e}", code="live_count_failed"
        ) from e
    return len(rows or [])


def _truncate(s: Any, n: int) -> str:
    text = "" if s is None else (s if isinstance(s, str) else json.dumps(s, default=str))
    return text[:n]


def _ensure_grounding_collection(client, dim: int) -> None:
    """Create the shared ``Historical_Refresh`` collection if absent. One
    collection for ALL agents; rows carry an ``agent_id`` field for isolation."""
    from pymilvus import DataType  # type: ignore

    if client.has_collection(GROUNDING_COLLECTION):
        return
    schema = client.create_schema(auto_id=True, enable_dynamic_field=False)
    schema.add_field("id", DataType.INT64, is_primary=True)
    schema.add_field("dense_vector", DataType.FLOAT_VECTOR, dim=dim)
    schema.add_field("agent_id", DataType.VARCHAR, max_length=_VARCHAR_SHORT)
    schema.add_field("source_id", DataType.VARCHAR, max_length=_VARCHAR_SHORT)
    schema.add_field("decision", DataType.VARCHAR, max_length=_VARCHAR_SHORT)
    schema.add_field("severity", DataType.VARCHAR, max_length=_VARCHAR_SHORT)
    schema.add_field("is_canonical", DataType.BOOL)
    schema.add_field("input_json", DataType.VARCHAR, max_length=_VARCHAR_LONG)
    schema.add_field("output_json", DataType.VARCHAR, max_length=_VARCHAR_LONG)
    schema.add_field("reasoning_trace", DataType.VARCHAR, max_length=_VARCHAR_LONG)
    index_params = client.prepare_index_params()
    index_params.add_index(field_name="dense_vector", index_type="AUTOINDEX", metric_type="COSINE")
    client.create_collection(collection_name=GROUNDING_COLLECTION, schema=schema, index_params=index_params)
    logger.info("[grounding] created shared collection %s (dim=%d)", GROUNDING_COLLECTION, dim)


def _write_collection(
    *,
    settings: Settings,
    agent_id: str,
    samples: List[Dict[str, Any]],
    vectors: List[List[float]],
    dim: int,
) -> str:
    """Replace THIS agent's rows in the shared ``Historical_Refresh`` collection
    (delete-by-agent_id, then insert). One collection for all agents; other
    agents' rows are untouched. The guard already validated the new set, and
    delete+insert is flushed together so the empty window is sub-second.

    Returns the collection name. Sync — call inside a thread executor.
    """
    client = _milvus_client(settings)
    _ensure_grounding_collection(client, dim)
    client.load_collection(collection_name=GROUNDING_COLLECTION)

    safe = (agent_id or "").replace('"', '\\"')
    # Remove this agent's previous corpus (no-op on first refresh).
    try:
        client.delete(collection_name=GROUNDING_COLLECTION, filter=f'agent_id == "{safe}"')
    except Exception as exc:
        logger.warning("[grounding] delete prior rows for %s failed: %s", agent_id, exc)

    data = []
    for s, vec in zip(samples, vectors):
        data.append({
            "dense_vector": vec,
            "agent_id": _truncate(agent_id, _VARCHAR_SHORT),
            "source_id": _truncate(s.get("source_id"), _VARCHAR_SHORT),
            "decision": _truncate(s.get("decision"), _VARCHAR_SHORT),
            "severity": _truncate(s.get("severity") or "", _VARCHAR_SHORT),
            "is_canonical": bool(s.get("is_canonical")),
            "input_json": _truncate(s.get("input"), _VARCHAR_LONG),
            "output_json": _truncate(s.get("output"), _VARCHAR_LONG),
            "reasoning_trace": _truncate(s.get("reasoning") or "", _VARCHAR_LONG),
        })
    if data:
        client.insert(collection_name=GROUNDING_COLLECTION, data=data)
    client.flush(collection_name=GROUNDING_COLLECTION)
    return GROUNDING_COLLECTION


def upsert_decision_sample(
    *, settings: Settings, agent_id: str, sample: Dict[str, Any], max_rows: int = 300,
) -> Dict[str, Any]:
    """DELTA write-back: embed ONE validated decision and upsert it into the
    agent's rows — no full re-pull/re-embed (cheap, continuous).

    - Embeds only this sample (1 embedding call).
    - Dedups: deletes any prior row for the same decision (``source_id``).
    - Bounds size: keeps all ``is_canonical`` rows + at most ``max_rows``
      NON-canonical rows per agent, evicting the OLDEST non-canonical first
      (lowest auto-id). The full ``refresh_grounding`` rebuild remains the
      periodic backstop (re-pull seed, re-curate canonicals, guard).

    Sync — call inside a thread executor. Returns {inserted, evicted, kept}."""
    text = sample.get("_input_text") or json.dumps(
        sample.get("input") or {}, sort_keys=True, separators=(",", ":"), default=str
    )
    vectors = _embed_texts([text], settings)
    if not vectors:
        raise GroundingRefreshError("delta embed produced no vector", code="embed_failed")
    dim = len(vectors[0])
    client = _milvus_client(settings)
    _ensure_grounding_collection(client, dim)
    client.load_collection(collection_name=GROUNDING_COLLECTION)

    safe_agent = (agent_id or "").replace('"', '\\"')
    src = str(sample.get("source_id") or "").replace('"', '\\"')
    # Dedup: drop any prior row for this exact decision (re-stamp safe).
    if src:
        try:
            client.delete(
                collection_name=GROUNDING_COLLECTION,
                filter=f'agent_id == "{safe_agent}" and source_id == "{src}"',
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[grounding] delta dedup delete failed (%s): %s", agent_id, exc)

    client.insert(collection_name=GROUNDING_COLLECTION, data=[{
        "dense_vector": vectors[0],
        "agent_id": _truncate(agent_id, _VARCHAR_SHORT),
        "source_id": _truncate(sample.get("source_id"), _VARCHAR_SHORT),
        "decision": _truncate(sample.get("decision"), _VARCHAR_SHORT),
        "severity": _truncate(sample.get("severity") or "", _VARCHAR_SHORT),
        "is_canonical": False,
        "input_json": _truncate(sample.get("input"), _VARCHAR_LONG),
        "output_json": _truncate(sample.get("output"), _VARCHAR_LONG),
        "reasoning_trace": _truncate(sample.get("reasoning") or "", _VARCHAR_LONG),
    }])
    client.flush(collection_name=GROUNDING_COLLECTION)  # make insert visible to the cap query

    # Fixed-size cap: evict oldest non-canonical beyond max_rows.
    evicted = 0
    kept = None
    try:
        rows = client.query(
            collection_name=GROUNDING_COLLECTION,
            filter=f'agent_id == "{safe_agent}" and is_canonical == false',
            output_fields=["id"], limit=16384,
        )
        ids = sorted(r["id"] for r in (rows or []) if "id" in r)
        kept = len(ids)
        if len(ids) > max_rows:
            drop = ids[: len(ids) - max_rows]  # oldest auto-ids first
            client.delete(collection_name=GROUNDING_COLLECTION, filter=f"id in {drop}")
            client.flush(collection_name=GROUNDING_COLLECTION)
            evicted = len(drop)
            kept = max_rows
    except Exception as exc:  # noqa: BLE001
        logger.warning("[grounding] delta cap eviction failed (%s): %s", agent_id, exc)
    logger.info(
        "[grounding] delta upsert agent=%s (+1, evicted=%d, kept=%s)",
        agent_id, evicted, kept,
    )
    return {"inserted": 1, "evicted": evicted, "kept": kept}


# ── Orchestrator ─────────────────────────────────────────────────────────
async def refresh_grounding(
    *,
    settings: Settings,
    tenant_id: str,
    agent_id: str,
    app_slug: str,
    contract: GroundingContract,
    user_jwt: str,
    generation: int,
    on_phase=None,
    extra_samples: Optional[List[Dict[str, Any]]] = None,
) -> RefreshResult:
    """Pull → package → (+ loop write-back) → select → GUARD → atomic-swap. Raises
    on any failure (including a guard rejection), leaving live grounding untouched.

    ``extra_samples`` — loop-derived sample envelopes (validated-good
    DecisionRecords, via ``loop_decision_to_sample``) merged into the historical
    set so the agent grounds on its OWN observed successes. Only grows the pool,
    so it never trips the shrink-floor guard.

    ``on_phase(phase: str, info: dict)`` — optional async callback invoked at
    each stage boundary so a caller can stream progress to the UI. Phases:
    ``pulling, packaging, selecting, guarding, embedding, swapping, done``.
    """
    import asyncio

    async def _emit(phase: str, info: dict) -> None:
        if on_phase is not None:
            try:
                await on_phase(phase, info)
            except Exception as exc:  # progress reporting must never break the run
                logger.warning("[grounding] on_phase(%s) failed: %s", phase, exc)

    logger.info(
        "[grounding] refresh start agent=%s slug=%s source=%s dataset=%s",
        agent_id, app_slug, contract.source_id, contract.dataset_id,
    )
    await _emit("pulling", {})
    rows = await _pull_history(settings=settings, user_jwt=user_jwt, contract=contract)
    rows_pulled = len(rows)

    await _emit("packaging", {"rows_pulled": rows_pulled})
    packaged = package_rows(rows, contract)

    # Write-back: merge loop-observed validated-good decisions (deduped by
    # source_id) so the agent grounds on its own successes, not just the seed
    # history. Loop samples bypass the terminal-states drop (they are already
    # settled, validated decisions).
    if extra_samples:
        seen = {s["source_id"] for s in packaged}
        added = 0
        for s in extra_samples:
            if s and s.get("source_id") and s["source_id"] not in seen:
                packaged.append(s)
                seen.add(s["source_id"])
                added += 1
        logger.info(
            "[grounding] write-back merged %d loop-decision samples (agent=%s)",
            added, agent_id,
        )

    await _emit("selecting", {"rows_pulled": rows_pulled, "packaged": len(packaged)})
    canonical, full_pool = select_samples(packaged, contract)

    # Fixed-size memory: keep canonicals + at most max_rows non-canonical (favour
    # validated loop decisions over seed rows). Bounds corpus size AND embedding
    # cost, matching the delta path's per-agent cap.
    _max_rows = settings.grounding_max_rows_per_agent
    # Count of decided samples BEFORE the cap — the guard's fill-rate signal.
    _decided_before_cap = len(full_pool)
    _canon = [s for s in full_pool if s.get("is_canonical")]
    _noncanon = [s for s in full_pool if not s.get("is_canonical")]
    if len(_noncanon) > _max_rows:
        _loops = [s for s in _noncanon if s.get("from_loop")]
        _seeds = [s for s in _noncanon if not s.get("from_loop")]
        full_pool = _canon + (_loops + _seeds)[:_max_rows]
        logger.info(
            "[grounding] capped non-canonical pool to %d (agent=%s)", _max_rows, agent_id
        )

    # Gate B — evaluate BEFORE the write (delete+insert of this agent's rows).
    loop = asyncio.get_running_loop()
    client = _milvus_client(settings)
    live = await loop.run_in_executor(None, _live_count, client, agent_id)
    await _emit("guarding", {"packaged": len(full_pool), "canonical": len(canonical), "live": live})
    guard = evaluate_guard(full_pool, canonical, rows_pulled, live, contract,
                           decided_count=_decided_before_cap)
    if not guard.ok:
        logger.warning(
            "[grounding] GUARD REJECTED agent=%s: %s (live=%d kept)",
            agent_id, "; ".join(guard.failures), live,
        )
        raise GroundingRefreshError(
            "grounding refresh rejected by guard: " + "; ".join(guard.failures),
            code="guard_rejected",
        )

    # Embed, then replace this agent's rows in the shared collection.
    await _emit("embedding", {"to_embed": len(full_pool)})
    texts = [s["_input_text"] for s in full_pool]
    vectors = await loop.run_in_executor(None, _embed_texts, texts, settings)
    if not vectors:
        raise GroundingRefreshError("no embeddings produced", code="embed_failed")
    dim = len(vectors[0])

    await _emit("swapping", {"total": len(full_pool), "canonical": guard.canonical_count})
    collection = await loop.run_in_executor(
        None,
        lambda: _write_collection(
            settings=settings, agent_id=agent_id,
            samples=full_pool, vectors=vectors, dim=dim,
        ),
    )
    logger.info(
        "[grounding] refresh DONE agent=%s collection=%s total=%d canonical=%d classes=%s",
        agent_id, collection, guard.total_packaged, guard.canonical_count, guard.decision_classes,
    )
    await _emit("done", {"total": guard.total_packaged, "canonical": guard.canonical_count})
    # Strip the internal embedding-text helper before handing samples back for
    # the Mongo audit store (keep the readable input/output/decision/etc).
    clean = [{k: v for k, v in s.items() if k != "_input_text"} for s in full_pool]
    return RefreshResult(
        collection=collection,
        physical_collection=collection,
        total_samples=guard.total_packaged,
        canonical_samples=guard.canonical_count,
        neighbor_samples=guard.total_packaged - guard.canonical_count,
        decision_classes=guard.decision_classes,
        guard=guard,
        samples=clean,
    )
