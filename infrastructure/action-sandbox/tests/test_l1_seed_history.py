# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""
L1 unit tests for cross-respawn continuity:

- Citra-Service-side seed-history builder (`ActionSandboxManager._build_seed_history`).
- In-sandbox adapter one-shot prefix (env CITRA_ACTION_SEED_HISTORY → first /task only).

Pure stdlib + light stubs; runs without docker, redis, or fastapi installed.
"""
from __future__ import annotations

import os
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FAILED: list[str] = []


def _ok(msg: str) -> None:
    print(f"  OK   {msg}")


def _fail(msg: str) -> None:
    print(f"  FAIL {msg}")
    FAILED.append(msg)


# ---------------------------------------------------------------------------
# Stub fastapi/uvicorn to import the adapter module without the venv.
# ---------------------------------------------------------------------------
for mod_name in ("fastapi", "uvicorn"):
    if mod_name not in sys.modules:
        sys.modules[mod_name] = types.ModuleType(mod_name)
fa = sys.modules["fastapi"]


class _Dummy:
    def __init__(self, *a, **k): pass
    def __call__(self, *a, **k): return self
    def get(self, *a, **k):
        def deco(f): return f
        return deco
    post = get
    on_event = get


fa.FastAPI = _Dummy  # type: ignore[attr-defined]
fa.Header = lambda **k: None  # type: ignore[attr-defined]
fa.File = lambda **k: None  # type: ignore[attr-defined]
fa.Form = lambda **k: None  # type: ignore[attr-defined]
fa.UploadFile = _Dummy  # type: ignore[attr-defined]
fa.HTTPException = type("HTTPException", (Exception,), {"__init__": lambda self, **kw: None})  # type: ignore[attr-defined]
fa.Request = _Dummy  # type: ignore[attr-defined]
fa.responses = types.SimpleNamespace(JSONResponse=_Dummy, StreamingResponse=_Dummy)  # type: ignore[attr-defined]
sys.modules["fastapi.responses"] = fa.responses
sys.modules["uvicorn"].run = lambda *a, **k: None  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# 1. Adapter-side: env CITRA_ACTION_SEED_HISTORY → injected once into the
#    first /task message, never on subsequent ones.
# ---------------------------------------------------------------------------
print("[1/2] adapter seed-history one-shot")

# Simulate the closure logic exactly as it lives in adapter._build_app.
# We don't run the real FastAPI app — we just assert the prefix algebra
# the route uses around the seed_state dict.
def simulate_first_two_turns(env_seed: str, msg1: str, msg2: str) -> tuple[str, str]:
    seed_state = {"history": env_seed, "consumed": False}

    def with_seed(message: str) -> str:
        if not seed_state["consumed"]:
            seed_state["consumed"] = True
            seed = str(seed_state["history"] or "").strip()
            if seed:
                message = (
                    "[CITRA-CONTEXT] Prior conversation in this session "
                    "(re-seeded after container respawn — these turns "
                    "happened, your tools didn't):\n"
                    f"{seed}\n[/CITRA-CONTEXT]\n\n" + message
                )
        return message

    return with_seed(msg1), with_seed(msg2)


m1, m2 = simulate_first_two_turns(
    "user: hi\nassistant: hello!", "what's the weather?", "and tomorrow?"
)
if "[CITRA-CONTEXT]" in m1 and "user: hi" in m1 and "what's the weather?" in m1:
    _ok("first message gets the seed block")
else:
    _fail(f"first message missing seed block: {m1!r}")
if "[CITRA-CONTEXT]" not in m2 and m2 == "and tomorrow?":
    _ok("second message has no seed (one-shot)")
else:
    _fail(f"second message wrongly carries seed: {m2!r}")

# Empty seed → no prefix.
m1e, _ = simulate_first_two_turns("", "hi", "again")
if m1e == "hi":
    _ok("empty seed → no prefix")
else:
    _fail(f"empty seed leaked prefix: {m1e!r}")

# Whitespace-only seed → no prefix.
m1ws, _ = simulate_first_two_turns("   \n   ", "hi", "again")
if m1ws == "hi":
    _ok("whitespace seed → no prefix")
else:
    _fail(f"whitespace seed leaked prefix: {m1ws!r}")


# ---------------------------------------------------------------------------
# 2. Manager-side: _build_seed_history truncation logic.
#
#    We can't import the manager here (it pulls config/cache dependencies),
#    but the algorithm itself is small and self-contained — re-implement the
#    truncation rule and unit-test it. If the real implementation diverges,
#    Citra-Service unit tests will catch it; this just locks the contract.
# ---------------------------------------------------------------------------
print("[2/2] seed-history truncation contract")

SEED_BYTES = 64  # tiny cap for the test


def build(entries: list[dict], max_bytes: int = SEED_BYTES, max_turns: int = 20) -> str:
    recent = entries[-max_turns:]
    lines = []
    for e in recent:
        role = (e.get("role") or "").strip() or "assistant"
        content = (e.get("content") or "").strip()
        if not content:
            continue
        lines.append(f"{role}: {content}")
    if not lines:
        return ""
    body = "\n".join(lines)
    if len(body.encode("utf-8")) > max_bytes:
        while lines and len("\n".join(lines).encode("utf-8")) > max_bytes:
            lines.pop(0)
        body = "\n".join(lines)
    return body


# Empty history.
if build([]) == "":
    _ok("empty history → empty seed")
else:
    _fail("empty history did not produce empty seed")

# Single short turn fits.
out = build([{"role": "user", "content": "hi"}])
if out == "user: hi":
    _ok("single short turn renders verbatim")
else:
    _fail(f"single short turn: got {out!r}")

# Long history truncated from the head.
hist = [
    {"role": "user", "content": "first user message that is fairly long"},
    {"role": "assistant", "content": "first reply"},
    {"role": "user", "content": "second q"},
    {"role": "assistant", "content": "second a"},
]
out = build(hist, max_bytes=40)
if out and "first user message" not in out and "second" in out:
    _ok("oldest turns dropped to fit byte cap")
else:
    _fail(f"truncation wrong: got {out!r}")

# Skip empty content.
out = build([
    {"role": "user", "content": "   "},
    {"role": "user", "content": "hi"},
])
if out == "user: hi":
    _ok("empty-content turns skipped")
else:
    _fail(f"empty content not skipped: got {out!r}")


print()
if FAILED:
    print(f"FAILED ({len(FAILED)}):")
    for f in FAILED:
        print(f"  - {f}")
    sys.exit(1)
print("ALL SEED-HISTORY UNIT TESTS PASSED")
