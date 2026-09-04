# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""Load a folder of SOPs into a department's library, for your own org.

The acme-bank demo ships a policy corpus. An install pointed at your OWN
database gets the data and nothing else, which leaves the product half-built:
an agent can read your records and propose something, but it has no rules to
decide against, nothing to cite, and nothing you can hold it to. "Approve this
loan" and "approve this loan under §4.2 of the credit policy, which says X" are
different products, and the second one is this one.

What belongs here is whatever your team would hand a new joiner and expect them
to follow -- credit policy, claim-settlement SOPs, KYC procedure, the circular
that redefined NPA classification last quarter. Formats: pdf, docx, md, txt.

Runs INSIDE citra-service (it imports that service's ingest path, embedding
client and Milvus wiring):

    docker compose exec -T citra-service \\
      python /app/scripts/quickstart/ingest_sops.py \\
        --org my-org --dept ops --dir /app/my-sops --name "Ops Policy"

Idempotent: the library id is derived from (org, dept, name), so re-running
replaces that library's documents rather than stacking duplicates.

The same corpus can also be uploaded from the UI -- Home -> SOP Library -> New
library -> Upload SOPs -- which is the better route for one-off additions. This
script exists for the first load, where clicking through twelve files is worse
than naming the folder once.

Both routes leave the SAME state behind: the same ``sop_library_<dept>`` source
id, the same shared Milvus collection, and a discovery registration -- so a
library seeded here and one uploaded from the UI are one library, not two.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import List

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# Same namespace the demo ingest uses, so ids are stable and comparable.
_NS = uuid.UUID("6f9619ff-8b86-d011-b42d-00cf4fc964ff")

SUFFIXES = {".pdf", ".docx", ".doc", ".md", ".txt", ".rtf"}

# Category per document, matched on the filename. It is stamped on every chunk
# and is what a panel's or a rag tool's `doc_types` filter matches on -- leave
# it wrong and the corpus is retrievable but every FILTERED view renders "no
# documents found", which looks like an ingest failure and is not one.
DOC_TYPE_RULES = [
    ("circular", "circular"), ("notification", "circular"),
    ("charter", "charter"), ("manual", "manual"),
    ("sop", "sop"), ("procedure", "sop"), ("process", "sop"),
    ("policy", "policy"), ("guideline", "policy"), ("code", "policy"),
]


def _doc_type_for(path: Path) -> str:
    stem = path.stem.lower()
    for keyword, doc_type in DOC_TYPE_RULES:
        if keyword in stem:
            return doc_type
    return "policy"


def _extract_text(path: Path) -> str:
    """Extract with the SAME helper the UI upload uses, so a document ingested
    here and one uploaded through the panel chunk identically."""
    from text_extractors import extract_text_by_file_type
    text, _meta = extract_text_by_file_type(
        path.read_bytes(), path.name, path.suffix.lower().lstrip("."))
    return (text or "").strip()


async def _run(*, org: str, dept: str, docs: List[Path], name: str,
               source_id: str, public: bool, dry_run: bool) -> int:
    from citra_auth.constants import OwnerType
    from CRUD_utils import get_mongo_client, MONGODB_DATABASE
    from dept_library import (
        FOLDER_KIND, _store_dept_original, build_dept_library_registration,
        dept_library_source_id,
    )
    from dept_library_store import (
        ensure_shared_dept_collection, ingest_dept_document, delete_dept_docs,
        shared_dept_collection,
    )

    # Default to the id the UI derives (`sop_library_<dept>`), not a separate
    # `<org>_<dept>_policy_library`. Two ids for one department's SOPs means two
    # sources sharing one Milvus collection but isolated from each other by
    # source_id -- so a later upload from Home -> SOP Library would create a
    # second library that cannot see the documents seeded here.
    source_id = (source_id or "").strip() or dept_library_source_id(dept)

    folder_id = str(uuid.uuid5(_NS, f"{org}/{dept}/{name}"))
    collection = shared_dept_collection()
    log.info("Library '%s'  org=%s dept=%s folder=%s source=%s -> %s",
             name, org, dept, folder_id, source_id, collection)

    if dry_run:
        for d in docs:
            log.info("  [dry] %-50s doc_type=%s", d.name, _doc_type_for(d))
        log.info("Dry run - nothing written.")
        return 0

    ensure_shared_dept_collection()

    # Advertise the library in the DISCOVERY registry, exactly as the UI does
    # (dept_library.create_dept_library). Without this the corpus is ingested and
    # UNREADABLE: /semantic/search resolves a source's scope from discovery and
    # returns 403 "unknown or retired semantic source" for anything it does not
    # know -- so the builder never catalogues it and no agent can cite it. This
    # script used to stop at Mongo + Milvus, while the wizard told the operator
    # their apps would now decide by their SOPs.
    #
    # Registered BEFORE ingesting, and fail loud: a half-seeded library nobody
    # can read is worse than none, and re-running is idempotent.
    from services.enterprise_mcp_client import register_source, service_api_key
    try:
        await register_source(
            build_dept_library_registration(
                org_id=org, dept_id=dept, name=name,
                description=f"SOPs and policy for {dept}.",
                api_key=service_api_key(), public_within_org=public,
                source_id=source_id),
            service_api_key(),
        )
    except Exception as exc:  # noqa: BLE001 -- reported, never ingested behind
        log.error("Discovery registration FAILED for org=%s dept=%s source=%s: %s",
                  org, dept, source_id, exc)
        log.error("Nothing was ingested. Check DISCOVERY_SERVICE_URL and "
                  "SERVICE_API_KEY in citra-service, then re-run.")
        return 1
    log.info("Registered '%s' with discovery as a semantic source", source_id)

    # Replace this library's contents rather than merging, so a document
    # removed from the folder does not linger in the index forever.
    removed = delete_dept_docs(folder_id=folder_id)
    log.info("Purged %d prior vector(s) for this library", removed)

    db = get_mongo_client()[MONGODB_DATABASE]
    now = datetime.now()
    db["folders"].update_one(
        {"_id": folder_id},
        {"$set": {
            "owner_type": OwnerType.DEPT, "folder_kind": FOLDER_KIND,
            "org_id": org, "dept_id": dept,
            "milvus_collection": collection, "source_id": source_id,
            "public_within_org": public,
            "name": name,
            "description": f"SOPs and policy for {dept}.",
            "color": "#0f766e", "created_by": "seed:ingest_sops",
            # This library owns its discovery registration, so deleting it from
            # the UI deregisters the source -- dept_library deregisters only what
            # it registered, never an externally-advertised one.
            "discovery_registered": True,
            "updated_at": now, "deleted": False,
        }, "$setOnInsert": {"created_at": now}},
        upsert=True,
    )
    db["files"].delete_many({"folder_id": folder_id, "owner_type": "dept"})

    chunks = failed = 0
    for doc in docs:
        doc_type = _doc_type_for(doc)
        document_id = str(uuid.uuid5(_NS, f"{source_id}:{doc.name}"))
        try:
            text = _extract_text(doc)
        except Exception as exc:  # noqa: BLE001
            log.error("  x %-50s could not be read: %s", doc.name, exc)
            failed += 1
            continue
        if not text:
            # Not fatal, but never silent: a scanned PDF with no text layer
            # ingests as nothing and would otherwise look successful.
            log.error("  x %-50s no text extracted (scanned image? needs OCR)", doc.name)
            failed += 1
            continue
        n = await ingest_dept_document(
            org_id=org, dept_id=dept, source_id=source_id, folder_id=folder_id,
            document_id=document_id, doc_path=doc.name, filename=doc.name,
            text=text, doc_type=doc_type,
        )
        _store_dept_original(
            content=doc.read_bytes(), content_type=None, org_id=org, dept_id=dept,
            folder_id=folder_id, document_id=document_id, filename=doc.name,
            file_ext=doc.suffix.lstrip("."), user_id="seed:ingest_sops",
            doc_type=doc_type,
        )
        log.info("  %-50s -> %d chunk(s)  doc_type=%s", doc.name, n, doc_type)
        chunks += n

    log.info("Done - %d chunk(s) from %d document(s); %d could not be read.",
             chunks, len(docs) - failed, failed)
    if failed and not chunks:
        log.error("Nothing was ingested. Your apps will have no rules to cite.")
        return 1
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Ingest a folder of SOPs into a department's library.")
    ap.add_argument("--org", required=True, help="Organisation id (e.g. my-org).")
    ap.add_argument("--dept", required=True, help="Department id (e.g. ops).")
    ap.add_argument("--dir", required=True, help="Folder of SOP documents.")
    ap.add_argument("--name", default="Policy Library", help="Library name shown in the UI.")
    ap.add_argument("--source-id", default="",
                    help="Defaults to sop_library_<dept> -- the id the UI derives.")
    ap.add_argument("--private", action="store_true",
                    help="Restrict to the department (default: readable org-wide).")
    ap.add_argument("--dry-run", action="store_true", help="List what would be ingested.")
    a = ap.parse_args()

    root = Path(a.dir)
    if not root.is_dir():
        log.error("Not a directory: %s", root)
        return 2
    docs = sorted(p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in SUFFIXES)
    if not docs:
        log.error("No documents in %s (looked for: %s)",
                  root, ", ".join(sorted(SUFFIXES)))
        return 2
    log.info("Found %d document(s) under %s", len(docs), root)

    # Resolved inside _run -- the derivation lives in citra-service's
    # dept_library, which is only importable in that container.
    return asyncio.run(_run(org=a.org, dept=a.dept, docs=docs, name=a.name,
                            source_id=(a.source_id or "").strip(),
                            public=not a.private, dry_run=a.dry_run))


if __name__ == "__main__":
    sys.exit(main())
