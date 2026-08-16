# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""Upload the generated claim documents to the tenant bucket and point the
records at them.

Runs only after `generate_claim_documents.py` verification passes — it is
imported by that script's `--upload` path, never used to ship an unverified
corpus.

Two database changes, both deliberate:

  * documents that now HAVE bytes get the real object key and the REAL
    sha256 of those bytes, so the `content_sha256` column and the object agree.
    They disagreed before: the seeder wrote a synthetic uuid5 hex, and the
    duplicate story would have contradicted itself the moment anyone hashed the
    file;
  * every other document row has its `file_url` CLEARED. Those rows sit on
    settled claims nobody opens, and a link that looks openable but is not is
    worse in a demo than an honest absence.

Credentials come from `mcp/.env` (gitignored) — BUCKET_NAME, BUCKET_REGION,
BUCKET_ACCESS_KEY, BUCKET_SECRET_KEY, BUCKET_KEY_PREFIX. Nothing is read from
the shell or written back.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Dict, List

import psycopg2

SCRIPT_DIR = Path(__file__).resolve().parent
TENANT_DIR = SCRIPT_DIR.parent
ENV = TENANT_DIR / "mcp" / ".env"

PG = dict(host="localhost", port=15444, dbname="acme_bank",
          user="acme_bank", password="acme_bank_demo_pw")


def _env() -> Dict[str, str]:
    out: Dict[str, str] = {}
    for line in ENV.read_text(encoding="utf-8").splitlines():
        if line.startswith("BUCKET_") and "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    missing = [k for k in ("BUCKET_NAME", "BUCKET_REGION", "BUCKET_ACCESS_KEY",
                           "BUCKET_SECRET_KEY") if not out.get(k)]
    if missing:
        raise SystemExit(f"mcp/.env is missing {missing}")
    return out


def object_key(prefix: str, claim_id: str, document_id: str) -> str:
    """Deterministic and unique per document — a claim can carry two documents
    of the same type, so the key is built on document_id, not doc_type."""
    p = (prefix or "").strip("/")
    return f"{p + '/' if p else ''}claims/{claim_id}/{document_id}.pdf"


def upload(rows: List[Dict[str, Any]], docs: Dict[str, bytes]) -> int:
    import boto3

    env = _env()
    bucket, prefix = env["BUCKET_NAME"], env.get("BUCKET_KEY_PREFIX", "")
    s3 = boto3.client(
        "s3", region_name=env["BUCKET_REGION"],
        aws_access_key_id=env["BUCKET_ACCESS_KEY"],
        aws_secret_access_key=env["BUCKET_SECRET_KEY"],
    )
    meta = {r["document_id"]: r for r in rows}
    print(f"\nuploading {len(docs)} object(s) to s3://{bucket}/{prefix}")

    filed: List[tuple] = []
    for n, (did, raw) in enumerate(sorted(docs.items()), start=1):
        key = object_key(prefix, meta[did]["claim_id"], did)
        s3.put_object(Bucket=bucket, Key=key, Body=raw,
                      ContentType="application/pdf")
        filed.append((f"s3://{bucket}/{key}", hashlib.sha256(raw).hexdigest(), did))
        if n % 200 == 0:
            print(f"  {n}/{len(docs)}")
    print(f"  {len(docs)}/{len(docs)} done")

    conn = psycopg2.connect(**PG)
    cur = conn.cursor()
    # Clear first, then set: any row we did not file must not look openable.
    cur.execute("update claim_documents set file_url = null")
    cleared = cur.rowcount
    cur.executemany(
        "update claim_documents set file_url = %s, content_sha256 = %s "
        "where document_id = %s", filed)
    conn.commit()
    cur.execute("select count(*) from claim_documents where file_url is not null")
    filed_n = cur.fetchone()[0]
    cur.execute("""select count(*) from (
                     select content_sha256 from claim_documents
                     where file_url is not null
                     group by content_sha256 having count(*) > 1) x""")
    dup_groups = cur.fetchone()[0]
    conn.close()
    print(f"\ncleared file_url on {cleared} row(s); {filed_n} now point at a real "
          f"object")
    print(f"byte-identical groups among filed documents: {dup_groups} "
          f"(1 = the intended reused estimate)")
    return 0
