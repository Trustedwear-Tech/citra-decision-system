# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""The generated schema file and the validator CLI.

Two artifacts, one source of truth (registry_models.py):
  * schema/sources.schema.json — the EDITOR contract. Generated. An author adds
    "$schema" to their file and VS Code validates as they type. Any language can
    read it.
  * validate_sources.py — the AUTHORITATIVE gate. Runs the pydantic models, so it
    also enforces the cross-field rules JSON Schema cannot express.

The drift gate below is the load-bearing one: a generated file that silently goes
stale is worse than no file, because people trust it.
"""
import json
import pathlib
import subprocess
import sys

import pytest

HERE = pathlib.Path(__file__).resolve().parent.parent
SCHEMA = HERE / "schema" / "sources.schema.json"
REAL = sorted((HERE.parent / "demo-data" / "tenants").glob("*/mcp/sources.json"))


# ── the generated schema ────────────────────────────────────────────────────

def test_schema_file_exists():
    assert SCHEMA.exists(), "run: python gen_sources_schema.py"


def test_schema_file_is_not_stale():
    """THE drift gate. The schema is generated from registry_models.py; if someone
    edits the model and doesn't regenerate, editors would validate against a lie.
    Hand-maintaining two definitions is the exact failure this work removes."""
    proc = subprocess.run(
        [sys.executable, "gen_sources_schema.py", "--check"],
        cwd=HERE, capture_output=True, text=True,
    )
    assert proc.returncode == 0, (
        f"schema/sources.schema.json is stale — regenerate it:\n"
        f"    python gen_sources_schema.py\n{proc.stderr}"
    )


def test_schema_is_a_valid_draft_2020_12_document():
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator.check_schema(schema)


def test_schema_carries_the_enums_that_catch_typos():
    """`evidance` was only ever a log line nobody read. In the schema it's an
    editor squiggle before the file is ever saved."""
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    defs = schema["$defs"]
    assert defs["ArtifactRole"]["enum"] == [
        "identity", "evidence", "supporting", "payment_proof"]
    # The domain triple went GLOBAL (2026-08-10): vertical/sub_vertical/country
    # are open strings, so they are no longer schema enums. Typo safety moved
    # to validation — an unknown vertical is accepted with a loud advisory, an
    # unknown country is rejected against the full ISO-3166 list. See
    # test_domain_is_global_but_still_typo_safe in test_registry_models.py.
    assert "Vertical" not in defs and "SubVertical" not in defs
    assert "Country" not in defs
    assert defs["ReusePolicy"]["enum"] == ["expected", "suspicious", "ignore"]
    assert "image_url" in defs["ColumnKind"]["enum"]
    # The live types the old 4-value enum would have rejected.
    for t in ("bigquery", "sap_rfc", "duckdb"):
        assert t in defs["SourceType"]["enum"]


def test_schema_strictness_matches_the_models():
    """forbid where a typo is SILENT (ontology), allow where it fails loudly
    (backend wiring)."""
    defs = json.loads(SCHEMA.read_text(encoding="utf-8"))["$defs"]
    assert defs["RegistryColumn"]["additionalProperties"] is False
    assert defs["RegistrySource"]["additionalProperties"] is False
    assert defs["RegistryDataset"]["additionalProperties"] is False
    assert defs["Connection"]["additionalProperties"] is True


def test_schema_requires_type():
    """Omitting `type` silently downgraded a structured source to semantic and
    dropped it from /query."""
    defs = json.loads(SCHEMA.read_text(encoding="utf-8"))["$defs"]
    assert "type" in defs["RegistrySource"]["required"]


@pytest.mark.skipif(not REAL, reason="demo-data tenants not present")
@pytest.mark.parametrize("path", REAL, ids=[p.parent.parent.name for p in REAL])
def test_schema_file_validates_every_real_tenant_file(path):
    """The promise of shipping a schema file: point any tool at it and a real
    file passes. If this fails the SCHEMA is wrong, not the file."""
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    doc = json.loads(path.read_text(encoding="utf-8"))
    errors = list(jsonschema.Draft202012Validator(schema).iter_errors(doc))
    assert not errors, "; ".join(
        f"{'/'.join(str(x) for x in e.path)}: {e.message}" for e in errors[:3]
    )


# ── the validator CLI ───────────────────────────────────────────────────────

def _run(path, *args):
    return subprocess.run(
        [sys.executable, "validate_sources.py", str(path), *args],
        cwd=HERE, capture_output=True, text=True,
    )


@pytest.mark.skipif(not REAL, reason="demo-data tenants not present")
def test_cli_accepts_the_real_files():
    proc = _run(str(HERE.parent / "demo-data" / "tenants" / "*" / "mcp" / "sources.json"))
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_cli_catches_the_silent_traps(tmp_path):
    """Every one of these was silent before: a missing `type` downgraded the
    source; a typo'd key did nothing; a bad enum was a log line; a string
    primary_key marked no key at all."""
    f = tmp_path / "sources.json"
    f.write_text(json.dumps({"sources": [
        {   # no `type`
            "source_id": "a", "dept_id": "d", "org_id": "o",
            "name": "n", "description": "x",
            "datasets": [{"id": "a.t", "columns": [
                {"name": "photo", "artifact_roles": "evidence"},   # typo'd key
                {"name": "notes", "artifact_role": "evidance"},    # bad enum
            ]}],
        },
        {   # duplicate id + string primary_key
            "source_id": "a", "type": "mongodb", "dept_id": "d", "org_id": "o",
            "name": "n", "description": "x",
            "connection": {"env_prefix": "M", "primary_key": "batch_id"},
        },
    ]}), encoding="utf-8")

    proc = _run(f)
    assert proc.returncode == 1
    out = proc.stdout
    assert "type" in out                      # required
    assert "artifact_roles" in out            # unknown key
    assert "evidance" in out or "identity" in out   # enum
    assert "duplicate source_id" in out       # file-level
    assert "primary_key" in out               # list-only


def test_cli_reports_duplicates_even_when_a_sibling_is_invalid(tmp_path):
    """File-level checks are computed from the RAW doc on purpose. Keying them off
    a fully-parsed file meant one bad source hid every duplicate in the file."""
    f = tmp_path / "sources.json"
    f.write_text(json.dumps({"sources": [
        {"source_id": "dup", "dept_id": "d", "org_id": "o", "name": "n",
         "description": "x"},                       # invalid: no type
        {"source_id": "dup", "type": "structured", "dept_id": "d", "org_id": "o",
         "name": "n", "description": "x", "connection": {"env_prefix": "P"}},
    ]}), encoding="utf-8")
    proc = _run(f)
    assert proc.returncode == 1
    assert "duplicate source_id" in proc.stdout


def test_cli_rejects_malformed_json(tmp_path):
    f = tmp_path / "sources.json"
    f.write_text("{not json", encoding="utf-8")
    proc = _run(f)
    assert proc.returncode == 1
    assert "invalid JSON" in proc.stdout


def test_cli_rejects_a_non_list_root(tmp_path):
    f = tmp_path / "sources.json"
    f.write_text('{"sources": {"a": 1}}', encoding="utf-8")
    proc = _run(f)
    assert proc.returncode == 1


def test_cli_json_mode_is_machine_readable(tmp_path):
    f = tmp_path / "sources.json"
    f.write_text(json.dumps([{"source_id": "a", "dept_id": "d", "org_id": "o",
                              "name": "n", "description": "x"}]), encoding="utf-8")
    proc = _run(f, "--json")
    assert proc.returncode == 1
    payload = json.loads(proc.stdout)
    assert payload["valid"] is False
    problems = next(iter(payload["files"].values()))
    assert any("type" in p["where"] for p in problems)


def test_cli_exit_2_on_a_missing_file():
    proc = _run("does-not-exist.json")
    assert proc.returncode == 2
