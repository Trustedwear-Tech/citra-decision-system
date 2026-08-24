# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""
L1 static checks for the Citra Action Sandbox image build artifacts.

Run with:
    python tests/test_l1_static.py

Exits non-zero on the first failure. No third-party deps; uses only stdlib
so it can run from a vanilla Python without the adapter venv.
"""
from __future__ import annotations

import json
import os
import py_compile
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FAILED: list[str] = []


def _ok(msg: str) -> None:
    print(f"  OK   {msg}")


def _fail(msg: str) -> None:
    print(f"  FAIL {msg}")
    FAILED.append(msg)


# ---------------------------------------------------------------------------
# 1. Python syntax of every adapter module.
# ---------------------------------------------------------------------------
print("[1/4] python compile")
for py in (ROOT / "runner").rglob("*.py"):
    try:
        py_compile.compile(str(py), doraise=True)
        _ok(str(py.relative_to(ROOT)))
    except py_compile.PyCompileError as e:
        _fail(f"{py.relative_to(ROOT)}: {e}")


# ---------------------------------------------------------------------------
# 2. Render openclaw.config.template.json with stub vars and json.loads it.
# ---------------------------------------------------------------------------
print("[2/4] config template render + json parse")
tmpl = (ROOT / "openclaw.config.template.json").read_text()

# Keep in sync with the ${...} vars in openclaw.config.template.json and the
# envsubst allowlist in entrypoint.sh (these are the post-rename CITRA_AGENT_*
# names; numeric ones like CONTEXT_WINDOW/MAX_OUTPUT_TOKENS must stub to bare
# numbers so the rendered JSON parses).
stub = {
    "CITRA_AGENT_LLM_BASE_URL": "https://stub.example/v1",
    "CITRA_AGENT_LLM_API_KEY": "sk-stub",
    "CITRA_AGENT_MODEL": "gpt-stub",
    "CITRA_AGENT_CONTEXT_WINDOW": "163840",
    "CITRA_AGENT_MAX_OUTPUT_TOKENS": "50000",
    "CITRA_MCP_URL": "https://stub.example/mcp",
    "CITRA_AGENT_SCOPED_TOKEN": "tok-stub",
    "CITRA_AGENT_PROXY_BASE_URL": "https://stub.example",
    "OPENCLAW_HOME": "/home/citra/.openclaw-home",
}
# envsubst only substitutes ${VAR} (and $VAR), no defaults / ${VAR:-x}.
rendered = re.sub(
    r"\$\{([A-Z_][A-Z0-9_]*)\}",
    lambda m: stub.get(m.group(1), m.group(0)),
    tmpl,
)

try:
    cfg = json.loads(rendered)
    _ok("json.loads ok")
except json.JSONDecodeError as e:
    _fail(f"json parse failed: {e}\n--- rendered ---\n{rendered}")
    cfg = {}

# Must-have keys (regression guard for the recent revert).
def _walk(d: dict, *keys: str):
    cur = d
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return None
        cur = cur[k]
    return cur

checks = [
    (("logging", "file"), f"{stub['OPENCLAW_HOME']}/.openclaw/logs/openclaw.log"),
    (("logging", "maxFileBytes"), 52428800),
    (("update", "checkOnStart"), False),
    (("update", "auto", "enabled"), False),
    (("plugins", "enabled"), True),
    (("gateway", "controlUi", "enabled"), False),
    (("agents", "defaults", "sandbox", "mode"), "off"),
    (("tools", "deny"), [
        "browser", "canvas", "nodes", "message",
        "web_search", "web_fetch", "fetch", "http",
        "cron", "process",
    ]),
]
for path, expected in checks:
    got = _walk(cfg, *path)
    if got == expected:
        _ok(f"{'.'.join(path)} == {expected!r}")
    else:
        _fail(f"{'.'.join(path)}: expected {expected!r}, got {got!r}")

# Regression guard: control UI must NOT have allowedOrigins (it should be
# fully disabled, not just origin-restricted).
if _walk(cfg, "gateway", "controlUi", "allowedOrigins") is not None:
    _fail("gateway.controlUi.allowedOrigins present (UI should be fully off)")
else:
    _ok("gateway.controlUi.allowedOrigins absent (good)")

# Regression guard: the BASE template must stay NEUTRAL — only the `main`
# agent, with an empty subagents.allowAgents. The sub-agent roster
# (researcher/analyst/reporter/excel-generator/chartist/synthesizer) lives
# ONLY in the action-chat consumer's openclaw.config.overlay.json, merged at
# boot. If a future edit re-wires sub-agents into the shared base, the
# smart-app-builder consumer would again advertise a delegation tool naming
# personas it never ships — this guard catches that.
agent_list = _walk(cfg, "agents", "list") or []
agent_ids = [a.get("id") for a in agent_list if isinstance(a, dict)]
if agent_ids == ["main"]:
    _ok("agents.list == ['main'] (neutral base)")
else:
    _fail(f"agents.list should be ['main'] in the base template, got {agent_ids!r}")
main_entry = next((a for a in agent_list if isinstance(a, dict) and a.get("id") == "main"), {})
allow = ((main_entry.get("subagents") or {}).get("allowAgents"))
if allow == []:
    _ok("main.subagents.allowAgents == [] (neutral base)")
else:
    _fail(f"main.subagents.allowAgents should be [] in the base template, got {allow!r}")


# ---------------------------------------------------------------------------
# 3. Parser unit slice for OpenClawRunner._parse_line.
# ---------------------------------------------------------------------------
print("[3/4] OpenClawRunner._parse_line")
sys.path.insert(0, str(ROOT))
# Stub out fastapi/uvicorn/httpx so the import doesn't require the venv.
import types
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
fa.FastAPI = _Dummy  # type: ignore
fa.Header = lambda **k: None  # type: ignore
fa.File = lambda **k: None  # type: ignore
fa.Form = lambda **k: None  # type: ignore
fa.UploadFile = _Dummy  # type: ignore
fa.HTTPException = type("HTTPException", (Exception,), {"__init__": lambda self, **kw: None})  # type: ignore
fa.Request = _Dummy  # type: ignore
fa.responses = types.SimpleNamespace(JSONResponse=_Dummy, StreamingResponse=_Dummy)  # type: ignore
sys.modules["fastapi.responses"] = fa.responses
sys.modules["uvicorn"].run = lambda *a, **k: None  # type: ignore

from runner.adapter import OpenClawRunner  # noqa: E402

cases: list[tuple[str, dict]] = [
    ('{"type":"message","text":"hi"}', {"type": "message", "text": "hi"}),
    ("TOOL_CALL: shell echo hi", {"type": "tool_call", "name": "shell", "args_raw": "echo hi"}),
    ("TOOL_RESULT: shell ok",     {"type": "tool_result", "name": "shell", "ok": True}),
    ("TOOL_RESULT: shell error",  {"type": "tool_result", "name": "shell", "ok": False}),
    ("plain text chunk",          {"type": "message", "text": "plain text chunk\n"}),
]
for line, expected in cases:
    got = OpenClawRunner._parse_line(line)
    if got == expected:
        _ok(f"{line!r}")
    else:
        _fail(f"{line!r}: expected {expected!r}, got {got!r}")


# ---------------------------------------------------------------------------
# 4. SKILL_ENVIRONMENT.md must mention OPENCLAW_HOME persistence and shim.
# ---------------------------------------------------------------------------
print("[4/4] SKILL_ENVIRONMENT.md persona content")
skill = (ROOT / "workspace-seed" / "SKILL_ENVIRONMENT.md").read_text(encoding="utf-8")
for needle in [
    "OPENCLAW_HOME",
    "/home/citra/.openclaw-home",
    "Workspace is ephemeral",
    "citra_toolkit.publish",
    "[CITRA-SANDBOX] install blocked",
    "127",
]:
    if needle in skill:
        _ok(f"contains {needle!r}")
    else:
        _fail(f"missing {needle!r} in SKILL_ENVIRONMENT.md")

# Regression: the old persistent-volume language must be gone.
for anti in [
    "per-user Docker volume",
    "survive across\nsessions",
    "/workspace/.openclaw-home",
    "/workspace/reports/<session_id>",
]:
    if anti in skill:
        _fail(f"stale persistent-volume text still present: {anti!r}")
    else:
        _ok(f"absent (good): {anti!r}")

# Entrypoint default must point at /home/citra now.
entry = (ROOT / "entrypoint.sh").read_text(encoding="utf-8")
if "OPENCLAW_HOME=\"${OPENCLAW_HOME:-/home/citra/.openclaw-home}\"" in entry:
    _ok("entrypoint OPENCLAW_HOME default == /home/citra/.openclaw-home")
else:
    _fail("entrypoint OPENCLAW_HOME default not /home/citra/.openclaw-home")
if "/workspace/.openclaw-home" in entry:
    _fail("entrypoint still references /workspace/.openclaw-home")
else:
    _ok("entrypoint no longer references /workspace/.openclaw-home")

# Dockerfile must NOT declare /workspace as a Docker volume any more.
df = (ROOT / "Dockerfile").read_text(encoding="utf-8")
if 'VOLUME ["/workspace"]' in df:
    _fail("Dockerfile still declares VOLUME [\"/workspace\"] (sandbox should be ephemeral)")
else:
    _ok("Dockerfile has no /workspace VOLUME (ephemeral)")


print()
if FAILED:
    print(f"FAILED ({len(FAILED)}):")
    for f in FAILED:
        print(f"  - {f}")
    sys.exit(1)
print("ALL L1 CHECKS PASSED")
