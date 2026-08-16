# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

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


def test_workflows_cover_every_dept_source():
    """One ingestion workflow per dept_source. No orphan workflows, no missing ones."""
    sources = json.loads(
        (ROOT / "mcp-config" / "cement_dept_sources.import.json").read_text("utf-8")
    )
    workflows = json.loads(
        (ROOT / "workflows" / "cement_ingestion_workflows.import.json").read_text("utf-8")
    )
    expected = {f"demo_ingest_{s['source_id']}" for s in sources}
    actual = {w["name"] for w in workflows}
    assert actual == expected, f"workflow ↔ source mismatch: {expected ^ actual}"


def test_every_workflow_writes_to_demo_tagged_milvus():
    """Demo workflows MUST tag Milvus chunks with `tag=demo` for safe cleanup."""
    workflows = json.loads(
        (ROOT / "workflows" / "cement_ingestion_workflows.import.json").read_text("utf-8")
    )
    for w in workflows:
        sinks = [n for n in w["nodes"] if n["type"] == "milvus_writer"]
        assert sinks, f"{w['name']} has no milvus_writer node"
        for s in sinks:
            tags = s.get("config", {}).get("tags", {})
            assert tags.get("tag") == "demo", (
                f"{w['name']} → {s['id']} writes Milvus without tag=demo — "
                "could be impossible to clean up later"
            )
            assert tags.get("industry") == "manufacturing"


def test_every_workflow_uses_demo_s3_bucket():
    workflows = json.loads(
        (ROOT / "workflows" / "cement_ingestion_workflows.import.json").read_text("utf-8")
    )
    for w in workflows:
        listers = [n for n in w["nodes"] if n["type"] == "s3_list"]
        assert listers, f"{w['name']} has no s3_list node"
        for n in listers:
            assert n["config"]["bucket"] == "demo-source-citra", (
                f"{w['name']} → {n['id']} reads from "
                f"{n['config'].get('bucket')!r}; expected demo-source-citra"
            )
            assert n["config"]["prefix"].startswith("manufacturing/cement/"), (
                f"{w['name']} → {n['id']} prefix should start with "
                f"manufacturing/cement/, got {n['config'].get('prefix')!r}"
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
