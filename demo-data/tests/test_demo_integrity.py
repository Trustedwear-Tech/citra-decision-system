# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""
Integrity tests — file-by-file sanity checks for the demo-data setup.

These complement test_demo_fixtures.py (which does Pydantic-model
validation) with concrete file-level checks:

  * Every Python file compiles
  * Every *.import.json is valid JSON
  * Every fetched PDF has the %PDF- magic header
  * Cross-fixture: workflow source_ids match dept_source registry
  * The UCI dataset file is present and parseable

These run without any live infrastructure.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import List

import pytest


ROOT = Path(__file__).resolve().parents[1]


# ──────────────────────────────────────────────────────────────────────
# Python script syntax
# ──────────────────────────────────────────────────────────────────────


def _python_files() -> List[Path]:
    return sorted([
        p for p in ROOT.rglob("*.py")
        if "raw/" not in str(p).replace("\\", "/")
        and "__pycache__" not in str(p)
        and ".venv" not in str(p)
    ])


@pytest.mark.parametrize("py", _python_files(), ids=lambda p: str(p.relative_to(ROOT)))
def test_python_file_compiles(py: Path):
    """Every demo-data .py file must parse cleanly."""
    source = py.read_text(encoding="utf-8")
    try:
        ast.parse(source, filename=str(py))
    except SyntaxError as exc:
        pytest.fail(f"{py.relative_to(ROOT)}: {exc}")


# ──────────────────────────────────────────────────────────────────────
# JSON validity
# ──────────────────────────────────────────────────────────────────────


def _json_files() -> List[Path]:
    return sorted([
        p for p in ROOT.rglob("*.json")
        if "raw/" not in str(p).replace("\\", "/")
        and ".pytest_cache" not in str(p)
        and "node_modules" not in str(p)
    ])


@pytest.mark.parametrize("jf", _json_files(), ids=lambda p: str(p.relative_to(ROOT)))
def test_json_file_is_valid(jf: Path):
    """Every JSON file in demo-data must parse."""
    try:
        json.loads(jf.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        pytest.fail(f"{jf.relative_to(ROOT)}: {exc}")


# ──────────────────────────────────────────────────────────────────────
# Fetched PDF integrity
# ──────────────────────────────────────────────────────────────────────


def _fetched_pdfs() -> List[Path]:
    raw = ROOT / "raw" / "cement"
    return sorted(raw.rglob("*.pdf")) if raw.exists() else []


@pytest.mark.parametrize("pdf", _fetched_pdfs(), ids=lambda p: p.name)
def test_pdf_has_real_magic_header(pdf: Path):
    """Reject HTML-disguised-as-PDF responses that some CDNs return as 200 OK."""
    head = pdf.read_bytes()[:8].lstrip()
    assert head.startswith(b"%PDF-"), (
        f"{pdf.relative_to(ROOT)} does not start with %PDF- "
        f"(got {head[:8]!r}) — likely an HTML error page."
    )


def test_min_pdf_count_present():
    """Catch silent regressions where fetch script drops sources."""
    pdfs = _fetched_pdfs()
    if not pdfs:
        pytest.skip("raw/cement/ not populated (run fetch_cement_public_sources.py first)")
    assert len(pdfs) >= 15, f"expected ≥15 PDFs, got {len(pdfs)}"


def test_uci_dataset_present_and_parseable():
    """Quality data seeder depends on this XLS."""
    uci = ROOT / "raw" / "cement" / "quality" / "datasets" / "uci_concrete_compressive_strength.xls"
    if not uci.exists():
        pytest.skip("UCI XLS not fetched yet")

    import pandas as pd
    df = pd.read_excel(uci, engine="xlrd")
    assert len(df) > 1000, f"UCI dataset has {len(df)} rows; expected ~1030"
    assert len(df.columns) >= 8, "UCI dataset should have 8 inputs + 1 output column"


# Note: the legacy bcrypt-hash verification test was removed when the demo
# moved off the mongoimport path. Demo personas are now created via
# POST /api/admin/users (placeholder users — no passwordHash) and tested
# via impersonation. See demo-data/scripts/seed_demo_users.py.


# ──────────────────────────────────────────────────────────────────────
# Cross-fixture consistency (the things the validator doesn't catch)
# ──────────────────────────────────────────────────────────────────────


# Three workflow tests lived here and are gone. They asserted on the CEMENT
# demo tenant -- its dept_sources import, its ingestion workflows, its S3
# bucket -- and that tenant was removed. The files they read
# (mcp-config/, workflows/) are not in this repository, so they did not skip:
# they raised FileNotFoundError on every run. A test that cannot reach its
# fixture is not guarding anything; it is noise that trains people to ignore a
# red suite. acme-bank is the only tenant that ships.

def test_media_columns_have_an_item_tool():
    """A dataset with media columns, bound by an app that declares no media tool.

    This is the failure the claims app shipped with, and it is silent in every
    direction. `claim_documents.file_url` carried `column_kind: document_url`,
    the app bound the dataset, and no `doc_extract` was declared -- so the agent
    read document METADATA (hashes, counts, types) and reasoned confidently about
    documents it never opened, while the officer got no per-document card. The
    app published clean, ran clean and cited documents nobody had read.

    It cannot be a publish validator today: `column_kind` lives in the catalogue,
    and the catalogue index is not plumbed into the publish path (main.py passes
    `catalogue_index=None` -- the open T-03 TODO). A validator added there would
    no-op and look like a guard. Here both halves are on disk, so the check is
    real.

    Deliberately not asserted: that fraud screening is declared. Document review
    and fraud screening are separate concerns -- `doc_extract` reviews every
    document, `consistency_check`/`fraud_synthesis` screen for reuse and are
    hand-authored against a real corpus later.
    """
    import json as _json

    src = ROOT / "tenants" / "acme-bank" / "mcp" / "sources.json"
    if not src.exists():
        pytest.skip("acme-bank sources.json not present")
    raw = _json.loads(src.read_text(encoding="utf-8"))
    sources = raw["sources"] if isinstance(raw, dict) else raw

    MEDIA = {"document_url", "image_url", "file"}
    #: dataset ref -> the media columns it declares
    media_by_ref = {}
    for s in sources:
        for ds in s.get("datasets") or []:
            cols = [c["name"] for c in (ds.get("columns") or [])
                    if str(c.get("column_kind") or "") in MEDIA]
            if cols:
                media_by_ref[str(ds.get("id") or "")] = cols

    if not media_by_ref:
        pytest.skip("no media columns declared in the ontology")

    ITEM_KINDS = {"doc_extract", "image_analyze"}
    failures = []
    for app in sorted((ROOT / "tenants" / "acme-bank" / "apps").glob("*.json")):
        spec = _json.loads(app.read_text(encoding="utf-8"))
        app_spec = spec.get("app_spec") or {}
        agent = spec.get("agent_spec") or {}
        bound = {ds.get("ref"): ds.get("id")
                 for ds in (app_spec.get("data_sources") or [])}
        hit = {ref: cols for ref, cols in media_by_ref.items() if ref in bound}
        if not hit:
            continue
        kinds = {t.get("kind") for t in (agent.get("tools_v2") or [])}
        if not (kinds & ITEM_KINDS):
            failures.append(
                f"{app.name} binds {sorted(hit)} whose media columns are "
                f"{sorted(c for cs in hit.values() for c in cs)}, "
                f"but declares no {sorted(ITEM_KINDS)} tool"
            )

    assert not failures, (
        "an app binds a dataset with media columns and cannot read them:\n  "
        + "\n  ".join(failures)
        + "\nThe agent will reason over document METADATA and cite files it "
          "never opened, and the officer gets no per-item review. Declare a "
          "doc_extract / image_analyze tool bound to the media column."
    )


def test_every_app_has_at_least_one_starter_prompt_if_chat():
    """If an app has an agent_chat panel, it must seed at least one starter."""
    for path in sorted((ROOT / "apps").glob("*.json")):
        raw = json.loads(path.read_text("utf-8"))
        for panel in raw["app_spec"]["panels"]:
            if panel["type"] == "agent_chat":
                starters = panel.get("starter_prompts", [])
                assert len(starters) >= 1, (
                    f"{path.name}: agent_chat panel {panel['id']} has no starter_prompts — "
                    "demos always start cold and need a primer"
                )


# ──────────────────────────────────────────────────────────────────────
# S3 + Mongo connectivity (skip if not configured)
# ──────────────────────────────────────────────────────────────────────


def test_s3_bucket_reachable_if_creds_present():
    """If BUCKET_ACCESS_KEY is set in env, the bucket must list cleanly."""
    import os
    ak = os.getenv("BUCKET_ACCESS_KEY") or os.getenv("AWS_ACCESS_KEY_ID")
    sk = os.getenv("BUCKET_SECRET_KEY") or os.getenv("AWS_SECRET_ACCESS_KEY")
    if not (ak and sk):
        pytest.skip("S3 creds not in env — skip")
    try:
        import boto3  # type: ignore
    except ImportError:
        pytest.skip("boto3 not installed — skip")
    s3 = boto3.client(
        "s3",
        region_name=os.getenv("BUCKET_REGION", "ap-south-1"),
        aws_access_key_id=ak,
        aws_secret_access_key=sk,
    )
    bucket = os.getenv("BUCKET_NAME", "demo-source-citra")
    resp = s3.list_objects_v2(Bucket=bucket, Prefix="manufacturing/cement/", MaxKeys=1)
    assert resp.get("ResponseMetadata", {}).get("HTTPStatusCode") == 200


def test_mongo_demo_db_has_expected_collections_if_reachable():
    """Verify seed_cement_mongo.py landed the 6 collections.

    Skipped if the demo Mongo isn't reachable (firewall, offline run, etc.)."""
    import os
    conn = os.getenv("MONGO_DEMO_CONN")
    if not conn:
        pytest.skip("MONGO_DEMO_CONN not set — skip (uses scripts default at runtime, "
                    "but tests must not embed credentials)")

    try:
        from pymongo import MongoClient  # type: ignore
        client = MongoClient(conn, serverSelectionTimeoutMS=10_000)
        client.admin.command("ping")
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"demo Mongo unreachable: {exc}")

    db = client["demo-source-manufacturing"]
    counts = {
        c: db[c].count_documents({"tenant_id": "acme-cement-demo"})
        for c in [
            "plant_ops_kiln_runs",
            "quality_test_results",
            "dispatch_orders",
            "central_sap_master",
            "central_salesforce_accounts",
            "central_historian_kpi_daily",
        ]
    }
    assert all(v > 0 for v in counts.values()), (
        f"some demo collections are empty: {counts}"
    )
    # Spot check: dispatches must reference real customers + real batches.
    sample = db["dispatch_orders"].find_one({"tenant_id": "acme-cement-demo"})
    if sample:
        cust = db["central_salesforce_accounts"].find_one(
            {"account_id": sample["customer_id"], "tenant_id": "acme-cement-demo"}
        )
        assert cust, f"FK broken: dispatch {sample['dispatch_id']} → unknown customer"

        batch = db["quality_test_results"].find_one(
            {"batch_id": sample["batch_id"], "tenant_id": "acme-cement-demo"}
        )
        assert batch, f"FK broken: dispatch {sample['dispatch_id']} → unknown batch"
