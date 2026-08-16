"""Memory export — the scheduled twin of the manual Memory-screen export (§4).

Pushes the customer's memory asset (the three golden collections) to a bucket
THEY own, as gzipped JSONL, so "you own your memory" is physical, not a slide.
The manual export (main.py get_memory_export) is on-demand + whole; this is the
repeatable, incremental push.

Design:
  * Scope = decision_records + item_decision_records + smartapp_clauses
    + smartapp_corrections (the learned RULES and the EVIDENCE behind
    them — rules alone would be conclusions the customer cannot audit).
    Ops collections and media bytes are never exported (media lives in the SoR;
    the ledger holds only content_sha256 + media_ref).
  * WATERMARK INCREMENTAL, not append-on-insert: ledger rows MUTATE after insert
    (the outcome poller stamps decision_records; item dispositions are Write-2;
    rubrics fold corrections) and every mutation advances ``updated_at``. So each
    run exports rows with ``updated_at`` strictly after the last run's high-water
    mark, and the documented consumer merge rule is "latest wins by
    decision_id / (correlation_id,item_id) / rubric key".
  * SNAPSHOT mode exports everything to a ``snapshot/`` prefix so a consumer can
    reconcile from scratch and a missed incremental self-heals (fail-loud
    posture applied to the export).

I/O is INJECTED (``fetch`` / ``writer`` / ``state``) so the orchestration is
unit-testable with fakes; the real Mongo/S3 impls are thin wrappers at the
bottom. The S3 write + Mongo reads cannot be unit-verified without live infra;
they are exercised by the trigger endpoint against a running stack.
"""
from __future__ import annotations

import gzip
import json
import logging
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("memory_export")

# collection name → the export "kind" segment used in the S3 key + the merge key
# the consumer dedupes on (documented, not enforced here).
EXPORT_COLLECTIONS: Dict[str, str] = {
    "decision_records": "decision_id",
    "item_decision_records": "item_id",
    "smartapp_clauses": "clause_id",
    "smartapp_corrections": "correction_id",
}

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Pure helpers (unit-tested)
# ---------------------------------------------------------------------------


def _json_default(o: Any) -> Any:
    if isinstance(o, datetime):
        return o.isoformat()
    return str(o)


def serialize_jsonl_gz(docs: List[Dict[str, Any]]) -> bytes:
    """One JSON object per line, gzipped. Datetimes → ISO. Deterministic key
    order so a re-export of the same rows is byte-stable."""
    lines = [
        json.dumps(d, default=_json_default, sort_keys=True, ensure_ascii=False)
        for d in docs
    ]
    raw = ("\n".join(lines) + ("\n" if lines else "")).encode("utf-8")
    # mtime=0 → the gzip header is stable across runs (only the payload varies).
    return gzip.compress(raw, mtime=0)


def batch_key(prefix: str, collection: str, dt: datetime, seq: int = 0) -> str:
    """`<prefix>/<collection>/YYYY/MM/DD/batch-NNN.jsonl.gz` — incremental page."""
    p = prefix.strip("/")
    return (f"{p}/{collection}/{dt:%Y/%m/%d}/batch-{seq:03d}.jsonl.gz"
            if p else f"{collection}/{dt:%Y/%m/%d}/batch-{seq:03d}.jsonl.gz")


def snapshot_key(prefix: str, collection: str, dt: datetime) -> str:
    """`<prefix>/snapshot/<collection>/YYYY/MM/DD/full.jsonl.gz` — full reconcile."""
    p = prefix.strip("/")
    tail = f"snapshot/{collection}/{dt:%Y/%m/%d}/full.jsonl.gz"
    return f"{p}/{tail}" if p else tail


def next_watermark(docs: List[Dict[str, Any]], prev: Optional[datetime]) -> Optional[datetime]:
    """Highest ``updated_at`` across the exported docs, else the previous mark.
    Advancing on the MAX (not 'now') means a row written mid-run is caught next
    run, never skipped."""
    best = prev
    for d in docs:
        # `at` covers the append-only corrections ledger, which has no
        # updated_at — without it every run would re-export the whole ledger.
        ts = d.get("updated_at") or d.get("created_at") or d.get("at")
        if isinstance(ts, datetime) and (best is None or ts > best):
            best = ts
    return best


# ---------------------------------------------------------------------------
# Orchestrator (unit-tested with fake fetch/writer/state)
# ---------------------------------------------------------------------------


async def run_export(
    *,
    prefix: str,
    fetch: Callable[[str, Optional[datetime]], Any],
    writer: Callable[[str, bytes], None],
    state_get: Callable[[str], Any],
    state_set: Callable[[str, datetime], Any],
    now: datetime,
    snapshot: bool = False,
) -> Dict[str, Any]:
    """Export each memory collection once. Incremental: from each collection's
    watermark, write the changed rows, advance the mark. Snapshot: from epoch,
    write to the snapshot prefix, DO NOT touch the incremental watermark.

    ``fetch`` / ``state_get`` / ``state_set`` are awaited (real impls are motor
    coroutines); ``writer`` is sync (boto3). Each collection is independent and
    loud-but-non-fatal — one collection's failure never aborts the others."""
    report: Dict[str, Any] = {"mode": "snapshot" if snapshot else "incremental",
                              "collections": {}}
    for coll in EXPORT_COLLECTIONS:
        try:
            since = None if snapshot else await state_get(coll)
            docs = await fetch(coll, since)
            key = (snapshot_key(prefix, coll, now) if snapshot
                   else batch_key(prefix, coll, now))
            if docs or snapshot:
                writer(key, serialize_jsonl_gz(docs))
            advanced = None
            if not snapshot and docs:
                mark = next_watermark(docs, since)
                if mark is not None:
                    await state_set(coll, mark)
                    advanced = mark
            report["collections"][coll] = {
                "exported": len(docs),
                "key": key if (docs or snapshot) else None,
                "watermark": advanced.isoformat() if advanced else (
                    since.isoformat() if since else None),
            }
            logger.info("[memory-export] %s %s: %d row(s) → %s",
                        report["mode"], coll, len(docs),
                        key if (docs or snapshot) else "(nothing new)")
        except Exception:  # noqa: BLE001 — one collection's failure is isolated + loud
            logger.exception("[memory-export] %s FAILED for collection %s",
                             report["mode"], coll)
            report["collections"][coll] = {"error": True}
    return report


# ---------------------------------------------------------------------------
# Real I/O wiring (thin; exercised against a live stack, not unit tests)
# ---------------------------------------------------------------------------

_STATE_COLLECTION = "memory_export_state"


def _mongo_fetch(app_doc: Dict[str, Any], tenant_ids: List[str]):
    """Build the real per-collection incremental fetch (updated_at > since,
    newest-mutated first). Drops Mongo _id."""
    import item_records as ir
    import main

    slug = app_doc.get("slug")

    async def _fetch(collection: str, since: Optional[datetime]) -> List[Dict[str, Any]]:
        q: Dict[str, Any] = {}
        if collection == "decision_records":
            col = main.get_decision_records_col()
            # Same $in tenant scope as items/rubrics (app-org + legacy JWT-org),
            # so the pushed ledgers cover one consistent tenant set — not a
            # decision ledger scoped narrower than its item/rubric counterparts.
            q = {"app_id": app_doc.get("app_id"), "tenant_id": {"$in": tenant_ids}}
        elif collection == "item_decision_records":
            col = ir._col()
            q = {"tenant_id": {"$in": tenant_ids}, "slug": slug}
        elif collection == "smartapp_clauses":
            import clause_store as cls

            col = cls._col()
            q = {"tenant_id": {"$in": tenant_ids}, "app_slug": slug}
        elif collection == "smartapp_corrections":
            import corrections as cx

            col = cx._col()
            q = {"tenant_id": {"$in": tenant_ids}, "app_slug": slug}
        else:
            return []
        # Corrections are append-only and carry `at`; everything else is
        # mutable and carries `updated_at`. Watermarking the wrong field would
        # re-export the whole ledger every run, or skip it entirely.
        mark = "at" if collection == "smartapp_corrections" else "updated_at"
        if since is not None:
            q[mark] = {"$gt": since}
        return await col.find(q, {"_id": 0}).sort(mark, 1).to_list(100000)

    return _fetch


def _state_store(app_doc: Dict[str, Any]):
    """Watermark store on the platform Mongo, keyed by (tenant, slug, collection)."""
    import main

    db = getattr(main, "_db", None)
    if db is None:
        raise RuntimeError("Database not initialised")
    col = db[_STATE_COLLECTION]
    tenant = app_doc.get("tenant_id")
    slug = app_doc.get("slug")

    async def _get(collection: str) -> Optional[datetime]:
        row = await col.find_one({"tenant_id": tenant, "slug": slug, "collection": collection})
        return (row or {}).get("watermark")

    async def _set(collection: str, mark: datetime) -> None:
        await col.update_one(
            {"tenant_id": tenant, "slug": slug, "collection": collection},
            {"$set": {"watermark": mark, "updated_at": datetime.now(timezone.utc)}},
            upsert=True,
        )

    return _get, _set


def _s3_writer(bucket: str):
    """boto3 put_object writer to the (customer-owned) export bucket.

    Credentials, in order:
      1. An EXPLICIT memory-export write-only key — ``MEMORY_EXPORT_ACCESS_KEY`` /
         ``MEMORY_EXPORT_SECRET_KEY`` (+ optional ``MEMORY_EXPORT_ENDPOINT_URL`` /
         ``MEMORY_EXPORT_REGION`` / ``AWS_REGION``). This keeps the export
         identity SEPARATE from the platform's own S3 identity (the customer
         provisions a scoped write-only key for their bucket).
      2. else the standard boto3 chain (instance role / ``AWS_*`` env).
    I/O — not unit-tested here."""
    import os
    import boto3

    access = os.getenv("MEMORY_EXPORT_ACCESS_KEY")
    secret = os.getenv("MEMORY_EXPORT_SECRET_KEY")
    region = os.getenv("MEMORY_EXPORT_REGION") or os.getenv("AWS_REGION")
    endpoint = (os.getenv("MEMORY_EXPORT_ENDPOINT_URL") or "").strip() or None

    kwargs: Dict[str, Any] = {}
    if access and secret:
        kwargs["aws_access_key_id"] = access
        kwargs["aws_secret_access_key"] = secret
    if region:
        kwargs["region_name"] = region
    if endpoint:
        kwargs["endpoint_url"] = endpoint
    client = boto3.client("s3", **kwargs)

    def _write(key: str, data: bytes) -> None:
        client.put_object(Bucket=bucket, Key=key, Body=data,
                          ContentType="application/gzip")

    return _write
