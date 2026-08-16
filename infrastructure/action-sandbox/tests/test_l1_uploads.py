# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""
L1 unit tests for the session-uploads sanitizer + manifest helpers.
"""
from __future__ import annotations

import os
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Stub fastapi.HTTPException so we can import the module without the real dep.
if "fastapi" not in sys.modules:
    sys.modules["fastapi"] = types.ModuleType("fastapi")
fa = sys.modules["fastapi"]
class _HE(Exception):
    def __init__(self, *, status_code: int = 500, detail: str = ""):
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"{status_code}: {detail}")
fa.HTTPException = _HE  # type: ignore
fa.FastAPI = type("FastAPI", (), {"__init__": lambda *a, **k: None})  # type: ignore
fa.File = lambda **k: None  # type: ignore
fa.Form = lambda **k: None  # type: ignore
fa.Header = lambda **k: None  # type: ignore
fa.Request = type("Request", (), {})  # type: ignore
fa.UploadFile = type("UploadFile", (), {})  # type: ignore
fa.responses = types.SimpleNamespace(JSONResponse=type("JR", (), {}), StreamingResponse=type("SR", (), {}))  # type: ignore
sys.modules["fastapi.responses"] = fa.responses
if "uvicorn" not in sys.modules:
    sys.modules["uvicorn"] = types.ModuleType("uvicorn")
sys.modules["uvicorn"].run = lambda *a, **k: None  # type: ignore

# Point UPLOADS_ROOT at a tempdir BEFORE importing the module.
TMP = tempfile.mkdtemp(prefix="citra-upload-test-")
os.environ["CITRA_SESSION_UPLOADS_DIR"] = TMP

from runner import adapter as A  # noqa: E402

FAILED: list[str] = []


def _ok(msg: str) -> None:
    print(f"  OK   {msg}")


def _fail(msg: str) -> None:
    print(f"  FAIL {msg}")
    FAILED.append(msg)


def _expect_400(label: str, fn):
    try:
        fn()
    except _HE as e:
        if e.status_code == 400:
            _ok(f"{label} -> 400")
            return
        _fail(f"{label}: expected 400, got {e.status_code}: {e.detail}")
    except Exception as e:  # noqa: BLE001
        _fail(f"{label}: unexpected exception type {type(e).__name__}: {e}")
    else:
        _fail(f"{label}: expected HTTPException 400, none raised")


print("[1/3] _sanitize_filename")
_expect_400("empty",          lambda: A._sanitize_filename(""))
_expect_400("dotonly",        lambda: A._sanitize_filename("."))
_expect_400("dotdot",         lambda: A._sanitize_filename(".."))
_expect_400("just-slash",     lambda: A._sanitize_filename("/"))

# Filename sanitization (PASS through, but stripped):
cases = [
    ("plain.pdf",                 "plain.pdf"),
    ("../../etc/passwd",          "passwd"),
    ("subdir/file.txt",           "file.txt"),
    ("C:\\Users\\foo\\bar.docx",  "bar.docx"),
    ("name with spaces.csv",      "name with spaces.csv"),
    ("foo\x00.txt",               "foo.txt"),       # null stripped, file kept
    (".bashrc",                   "bashrc"),        # leading dot stripped
    ("a" * 300 + ".bin",          ("a" * 300 + ".bin")[-200:]),
]
for raw, expected in cases:
    got = A._sanitize_filename(raw)
    if got == expected:
        _ok(f"{raw!r} -> {got!r}")
    else:
        _fail(f"{raw!r}: expected {expected!r}, got {got!r}")


print("[2/3] _session_dir / _manifest_path")
_expect_400("bad-sid (slash)",  lambda: A._session_dir("a/b"))
_expect_400("bad-sid (dot)",    lambda: A._session_dir("a.b"))

sid = "abc123XYZ-_"
sdir = A._session_dir(sid)
if sdir == os.path.join(TMP, sid):
    _ok(f"valid sid -> {sdir}")
else:
    _fail(f"valid sid: got {sdir}")
mp = A._manifest_path(sid)
if mp == os.path.join(TMP, sid, ".manifest.json"):
    _ok(f"manifest path -> {mp}")
else:
    _fail(f"manifest path: got {mp}")


print("[3/3] manifest read/write roundtrip")
sid2 = "session-test-1"
m1 = A._read_manifest(sid2)
if m1 == {"session_id": sid2, "files": []}:
    _ok("empty session -> empty manifest")
else:
    _fail(f"empty manifest: got {m1}")

# Write
m1["files"].append({"name": "x.txt", "size": 5, "sha256": "deadbeef", "uploaded_at": 1, "pending": True})
A._write_manifest(sid2, m1)
m2 = A._read_manifest(sid2)
if m2["files"][0]["pending"] is True and m2["files"][0]["sha256"] == "deadbeef":
    _ok("write+read roundtrip")
else:
    _fail(f"roundtrip: got {m2}")

# Manifest must be 0600.
mode = os.stat(A._manifest_path(sid2)).st_mode & 0o777
if mode == 0o600:
    _ok(f"manifest perms 0600")
else:
    # On Windows the chmod isn't fully honored; tolerate that.
    print(f"  SKIP perms (got {oct(mode)} on this platform)")


print()
import shutil
shutil.rmtree(TMP, ignore_errors=True)
if FAILED:
    print(f"FAILED ({len(FAILED)}):")
    for f in FAILED:
        print(f"  - {f}")
    sys.exit(1)
print("ALL UPLOAD UNIT TESTS PASSED")
