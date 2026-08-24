# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""
Seed the Acme Bank policy library into the SHARED dept-library collection.

Adapted from the acme-power ingester, deliberately by copy: the chunking, the
embedding provider and the shared-collection resolution stay byte-identical to
the path that is known to work, so this tenant cannot silently drift from it.

Dept SOP/policy libraries all share ONE Milvus collection
(`<prefix>_dept_libraries`), isolated by the scalar-indexed `org_id` + `dept` +
`source_id` fields. This script seeds through Citra-Service's own ingestion
primitive (`dept_library_store.ingest_dept_document`) rather than writing a
bespoke collection, which guarantees:

  * the exact shared schema + scalar isolation fields the platform reader queries,
  * UNIFORM chunking (the same splitter the folder panel uses),
  * the SAME embedding provider the reader embeds queries with — a mismatch here
    is silent and simply degrades retrieval.

Identifiers (MUST match SPEC.md §1/§6 and build_mcp_sources.py):
    source_id  = acme_bank_policy_library
    dept       = central_ops
    org_id     = acme-bank
    collection = <resolved by dept_library_store.shared_dept_collection()>

The corpus is the RULES layer: the SOP is supreme, and anything the app learns
from officers sits underneath it. One deliberate property — no document tells an
officer to reconcile a salaried applicant's tax-filed income against their
declared income. That gap is what LAN-NEEDLE-001 exercises, and it is what the
app can only learn from officer corrections.

Idempotent: a STABLE folder_id + per-doc document_ids (uuid5 of doc_path) let a
re-run hard-delete this source's prior rows before re-inserting — no dupes.

Runs under Citra-Service's venv (needs its deps + .env for Milvus + embeddings):
    C:/Github/Citra-AI/Citra-Service/myenv/Scripts/python.exe ingest_docs.py
Originals are still uploaded to S3 (the runtime "Open" button hands back a
signed URL); that is unchanged and independent of the vector store.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List, Tuple

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ── Wire in Citra-Service (single source of truth for the shared store) ───────
RAW_ROOT = Path(__file__).resolve().parents[1] / "raw"
REPO_ROOT = Path(__file__).resolve().parents[4]          # …/Citra-AI
CITRA_SERVICE_DIR = REPO_ROOT / "Citra-Service"
if not CITRA_SERVICE_DIR.is_dir():
    log.error("Citra-Service not found at %s — cannot reach the shared dept store.",
              CITRA_SERVICE_DIR)
    sys.exit(2)
sys.path.insert(0, str(CITRA_SERVICE_DIR))
try:
    from dotenv import load_dotenv
    load_dotenv(CITRA_SERVICE_DIR / ".env")               # Milvus URI + embedding creds
except Exception:  # noqa: BLE001 — dotenv optional if env already exported
    pass

# Identifiers — MUST match SPEC.md exactly.
ORG_ID = "acme-bank"
INDUSTRY = "bfsi"
SOURCE_ID = "acme_bank_policy_library"
DEPT = "central_ops"

# S3 / object-storage settings for the raw source artifacts (the "Open" button).
S3_BUCKET = os.getenv("BUCKET_NAME", "demo-source-citra")
S3_REGION = os.getenv("BUCKET_REGION", "ap-south-1")
S3_PREFIX = os.getenv("BUCKET_KEY_PREFIX", "acme-bank")

TEXT_EXTENSIONS = {".md", ".txt"}
PDF_EXTENSION = ".pdf"

# Filename keyword -> doc_type. First match wins; falls back to "policy". These
# feed the `doc_type` taxonomy field on every chunk (filterable in the shared
# collection).
DOC_TYPE_RULES: List[Tuple[str, str]] = [
    ("sop", "sop"),
    ("circular", "circular"),
    ("code", "code"),
    ("guidelines", "guideline"),
    ("guideline", "guideline"),
    ("policy", "policy"),
]

# Stable folder id for this source so a re-run can hard-delete + re-insert (uuid5
# is deterministic — NO Date/random, so reseeds are reproducible).
_NS = uuid.UUID("00000000-0000-0000-0000-0000ac3e0001")   # arbitrary fixed namespace
FOLDER_ID = str(uuid.uuid5(_NS, f"{ORG_ID}/{DEPT}/{SOURCE_ID}"))
# Audit-only stamp on the seeded folder/file records. Never an ownership
# key -- dept libraries are authorized by (org_id, dept_id), not by creator.
SEED_USER_ID = "seed:ingest_docs"


def _doc_type_for(path: Path) -> str:
    stem = path.stem.lower()
    for keyword, doc_type in DOC_TYPE_RULES:
        if keyword in stem:
            return doc_type
    return "policy"


def _document_id(doc_path: str) -> str:
    return str(uuid.uuid5(_NS, f"{SOURCE_ID}:{doc_path}"))


# ── Object-store upload (unchanged — originals back the "Open" button) ────────
def _content_type_for(path: Path) -> str:
    return {".md": "text/markdown; charset=utf-8",
            ".txt": "text/plain; charset=utf-8",
            ".pdf": "application/pdf"}.get(path.suffix.lower(), "application/octet-stream")


def _s3_key_for(doc_path: str) -> str:
    return f"{S3_PREFIX.strip('/')}/{SOURCE_ID}/{doc_path.lstrip('/')}"


def _upload_to_bucket(docs: List[Path]) -> int:
    access = os.getenv("BUCKET_ACCESS_KEY")
    secret = os.getenv("BUCKET_SECRET_KEY")
    if not (access and secret):
        log.warning("S3 upload skipped: BUCKET_ACCESS_KEY / BUCKET_SECRET_KEY not set. "
                    "RAG works; the runtime 'Open' button will 404 until re-run with keys.")
        return 0
    try:
        import boto3
        from botocore.client import Config as _BotoConfig
    except ImportError:
        log.warning("boto3 not installed; S3 upload skipped.")
        return 0
    endpoint_url = (os.getenv("BUCKET_ENDPOINT_URL") or "").strip() or None
    # A custom endpoint means an S3-compatible service (MinIO), which serves
    # path style. Virtual-hosted style would dial "<bucket>.<host>:<port>" and
    # fail to resolve. AWS keeps virtual.
    addressing_style = "path" if endpoint_url else "virtual"
    client_kwargs: Dict[str, Any] = dict(
        aws_access_key_id=access, aws_secret_access_key=secret, region_name=S3_REGION,
        config=_BotoConfig(signature_version="s3v4",
                           s3={"addressing_style": addressing_style}))
    if endpoint_url:
        client_kwargs["endpoint_url"] = endpoint_url
    elif S3_REGION and S3_REGION != "us-east-1":
        client_kwargs["endpoint_url"] = f"https://s3.{S3_REGION}.amazonaws.com"
    import boto3 as _b
    s3 = _b.client("s3", **client_kwargs)
    uploaded = 0
    for doc in docs:
        doc_path = doc.relative_to(RAW_ROOT).as_posix()
        key = _s3_key_for(doc_path)
        try:
            # SSE is an AWS-S3-ism: MinIO rejects it with NotImplemented
            # ("KMS not configured") unless a KMS backend is wired up. Send it
            # only against real S3 — same conditional as addressing_style above.
            extra = {} if endpoint_url else {"ServerSideEncryption": "AES256"}
            s3.put_object(Bucket=S3_BUCKET, Key=key, Body=doc.read_bytes(),
                          ContentType=_content_type_for(doc), **extra)
            log.info("  ↑ s3://%s/%s", S3_BUCKET, key)
            uploaded += 1
        except Exception as exc:  # noqa: BLE001
            log.error("  ✗ upload failed for %s: %s", doc_path, exc)
    log.info("Uploaded %d/%d documents to s3://%s/%s/%s/",
             uploaded, len(docs), S3_BUCKET, S3_PREFIX, SOURCE_ID)
    return uploaded


def _extract_text(path: Path) -> str:
    """Extract text with the SAME helper the dept-library upload uses (uniform
    section markers). Fail loud per-doc; a bad file must not silently vanish."""
    from text_extractors import extract_text_by_file_type
    data = path.read_bytes()
    text, _meta = extract_text_by_file_type(data, path.name, path.suffix.lower().lstrip("."))
    return (text or "").strip()


async def _run(docs: List[Path], *, dry_run: bool) -> int:
    from dept_library_store import (
        ensure_shared_dept_collection, ingest_dept_document, delete_dept_docs,
        shared_dept_collection,
    )
    collection = shared_dept_collection()
    log.info("Target SHARED collection: %s  (source_id=%s dept=%s org=%s folder=%s)",
             collection, SOURCE_ID, DEPT, ORG_ID, FOLDER_ID)

    if dry_run:
        for d in docs:
            dp = d.relative_to(RAW_ROOT).as_posix()
            log.info("  [dry] %-52s  doc_type=%s  document_id=%s",
                     dp, _doc_type_for(d), _document_id(dp))
        log.info("Dry run — shared collection untouched.")
        return 0

    ensure_shared_dept_collection()
    # Idempotent: hard-delete THIS source's prior rows (by our stable folder_id)
    # before re-inserting — delete-not-archive, matching the folder panel.
    removed = delete_dept_docs(folder_id=FOLDER_ID)
    log.info("Purged %d prior vector(s) for folder=%s", removed, FOLDER_ID)

    total_chunks = 0
    for doc in docs:
        doc_path = doc.relative_to(RAW_ROOT).as_posix()
        doc_type = _doc_type_for(doc)
        text = _extract_text(doc)
        if not text:
            log.warning("  ⚠ no text extracted from %s — skipped", doc_path)
            continue
        n = await ingest_dept_document(
            org_id=ORG_ID, dept_id=DEPT, source_id=SOURCE_ID, folder_id=FOLDER_ID,
            document_id=_document_id(doc_path), doc_path=doc_path,
            filename=doc.name, text=text, doc_type=doc_type,
        )
        log.info("  %-52s -> %d chunk(s)  doc_type=%s", doc_path, n, doc_type)
        total_chunks += n
    log.info("Ingestion complete — %d chunks across %d docs into %s.",
             total_chunks, len(docs), collection)

    # The vectors are retrievable now, but nothing in the UI knows this
    # corpus exists until it is registered as a library.
    _register_dept_library(docs)
    return 0



def _register_dept_library(docs: List[Path]) -> int:
    """Make the ingested corpus visible in the SOP Library panel.

    The vectors alone are invisible to the UI: the panel lists Mongo `folders`
    and, inside a library, Mongo `files`. Without these the demo shows "No
    department libraries yet" while the apps cite these very documents.

    Idempotent, on the same stable FOLDER_ID the vectors carry, so a re-run
    updates in place rather than creating a second library.
    """
    from datetime import datetime as _dt

    from citra_auth.constants import OwnerType
    from CRUD_utils import get_mongo_client, MONGODB_DATABASE
    from dept_library import FOLDER_KIND, _store_dept_original
    from dept_library_store import shared_dept_collection

    db = get_mongo_client()[MONGODB_DATABASE]
    now = _dt.now()

    db["folders"].update_one(
        {"_id": FOLDER_ID},
        {"$set": {
            "owner_type": OwnerType.DEPT,
            "folder_kind": FOLDER_KIND,
            "org_id": ORG_ID,
            "dept_id": DEPT,
            "milvus_collection": shared_dept_collection(),
            # Must match what the chunks are stamped with, or the panel and the
            # retriever disagree about which corpus this library is.
            "source_id": SOURCE_ID,
            # Org-wide: the demo has one admin, and every department's apps
            # cite this corpus. Curation stays admin-only regardless.
            "public_within_org": True,
            "name": "Policy Library",
            "description": ("Acme Bank credit, collections and claims policy — "
                            "the SOPs the Decision Apps cite."),
            "color": "#0f766e",
            "created_by": SEED_USER_ID,
            "updated_at": now,
            "deleted": False,
        }, "$setOnInsert": {"created_at": now}},
        upsert=True,
    )

    # Replace this library's file records outright rather than merging: a
    # renamed or removed source document would otherwise linger in the panel
    # forever, listed but no longer backed by any vector.
    db["files"].delete_many({"folder_id": FOLDER_ID, "owner_type": "dept"})

    written = 0
    for doc in docs:
        doc_path = doc.relative_to(RAW_ROOT).as_posix()
        try:
            _store_dept_original(
                content=doc.read_bytes(),
                content_type=_content_type_for(doc),
                org_id=ORG_ID, dept_id=DEPT, folder_id=FOLDER_ID,
                document_id=_document_id(doc_path),
                filename=doc.name,
                file_ext=doc.suffix.lstrip("."),
                user_id=SEED_USER_ID,
                doc_type=_doc_type_for(doc),
            )
            written += 1
        except Exception as exc:  # noqa: BLE001
            # Loud, and it does not abort: the vectors are already in, so the
            # apps still cite correctly. Only this document's row in the panel
            # is missing, and a re-run fixes it.
            log.error("  x could not register '%s' in the library panel: %s",
                      doc_path, exc)

    log.info("Registered library '%s' (folder=%s) with %d/%d document record(s)",
             "Policy Library", FOLDER_ID, written, len(docs))
    return written

def main() -> int:
    ap = argparse.ArgumentParser(
        description="Seed acme-bank policy docs into the shared dept-library collection.")
    ap.add_argument("--limit", type=int, default=0, help="Limit docs (debug); 0 = all.")
    ap.add_argument("--dry-run", action="store_true", help="Plan only — no Milvus/S3 writes.")
    ap.add_argument("--upload-only", action="store_true",
                    help="Upload originals to S3 and exit (no embedding/Milvus).")
    ap.add_argument("--skip-upload", action="store_true",
                    help="Skip the S3 original upload (index only).")
    args = ap.parse_args()

    docs = sorted(p for p in RAW_ROOT.rglob("*")
                  if p.is_file() and p.suffix.lower() in (TEXT_EXTENSIONS | {PDF_EXTENSION}))
    if args.limit:
        docs = docs[:args.limit]
    log.info("Found %d documents under %s", len(docs), RAW_ROOT)
    if not docs:
        log.warning("No documents to ingest — nothing to do.")
        return 0

    if not args.dry_run and not args.skip_upload:
        _upload_to_bucket(docs)
    if args.upload_only:
        log.info("Upload-only mode complete. Shared collection left untouched.")
        return 0

    return asyncio.run(_run(docs, dry_run=args.dry_run))


if __name__ == "__main__":
    sys.exit(main())
