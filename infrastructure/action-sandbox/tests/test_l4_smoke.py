# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""
L4 live container smoke test.

Spawns one Citra Action Sandbox container and exercises:
  1. /health     -> 200 + {"status":"ok","worker":"openclaw"}
  2. /task       -> SSE stream emits at least one `message` then `done`
  3. install-blocker -> from inside the container, `pip install requests`
                       exits 127 with the loud line on stderr
  4. persistence -> /workspace/.openclaw-home/.openclaw/openclaw.json
                    exists after the run

Reads CITRA_ACTION_LLM_BASE_URL/API_KEY + CITRA_ACTION_MODEL from
Citra-Service/.env (already wired for OpenRouter in this repo).

Run with:
    python tests/test_l4_smoke.py
"""
from __future__ import annotations

import json
import os
import re
import secrets
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CITRA_ENV = ROOT.parent.parent / "Citra-Service" / ".env"
IMAGE = "citra-agent-sandbox-base:test"
CONTAINER = "citra-agent-sandbox-base-smoke"
HOST_PORT = 18099
CONTROL_SECRET = secrets.token_urlsafe(16)


def _load_env() -> dict[str, str]:
    out: dict[str, str] = {}
    if not CITRA_ENV.exists():
        sys.exit(f"FATAL: {CITRA_ENV} not found")
    for ln in CITRA_ENV.read_text(encoding="utf-8", errors="replace").splitlines():
        s = ln.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, _, v = s.partition("=")
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def _run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def _docker_logs(tail: int = 80) -> str:
    r = _run(["docker", "logs", "--tail", str(tail), CONTAINER])
    return (r.stdout or "") + (r.stderr or "")


def _cleanup() -> None:
    _run(["docker", "rm", "-f", CONTAINER])
    _run(["docker", "volume", "rm", "citra-sandbox-smoke-vol"])


def main() -> int:
    env = _load_env()
    llm_url = env.get("CITRA_ACTION_LLM_BASE_URL")
    llm_key = env.get("CITRA_ACTION_LLM_API_KEY")
    llm_mod = env.get("CITRA_ACTION_MODEL")
    if not (llm_url and llm_key and llm_mod):
        sys.exit("FATAL: CITRA_ACTION_LLM_* missing in Citra-Service/.env")

    _cleanup()  # idempotent
    _run(["docker", "volume", "create", "citra-sandbox-smoke-vol"])

    print("[1/4] starting container")
    spawn = _run([
        "docker", "run", "-d",
        "--name", CONTAINER,
        "-p", f"{HOST_PORT}:8090",
        "-v", "citra-sandbox-smoke-vol:/workspace",
        "-e", f"CITRA_USER_ID=user-smoke",
        "-e", f"CITRA_SESSION_ID=sess-smoke",
        "-e", f"CITRA_SCOPED_TOKEN=stub-token",
        "-e", f"CITRA_CONTROL_SECRET={CONTROL_SECRET}",
        "-e", f"CITRA_LLM_BASE_URL={llm_url}",
        "-e", f"CITRA_LLM_API_KEY={llm_key}",
        "-e", f"CITRA_ACTION_MODEL={llm_mod}",
        IMAGE,
    ])
    if spawn.returncode != 0:
        sys.exit(f"docker run failed: {spawn.stderr}")
    print(f"  container={spawn.stdout.strip()[:12]}")

    print("[2/4] waiting for /health")
    health_url = f"http://127.0.0.1:{HOST_PORT}/health"
    health_ok = False
    deadline = time.time() + 90
    last_err = ""
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(health_url, timeout=2) as r:
                body = json.loads(r.read())
                if body.get("status") == "ok" and body.get("worker") == "openclaw":
                    print(f"  /health -> {body}")
                    health_ok = True
                    break
        except Exception as e:  # noqa: BLE001
            last_err = repr(e)
        # Fast-fail if container died.
        st = _run(["docker", "inspect", "-f", "{{.State.Running}}", CONTAINER])
        if st.stdout.strip() != "true":
            print("--- container died, logs ---")
            print(_docker_logs(200))
            _cleanup()
            return 1
        time.sleep(2)
    if not health_ok:
        print(f"  /health never came up. last_err={last_err}")
        print("--- logs ---")
        print(_docker_logs(200))
        _cleanup()
        return 1

    print("[3/4] POST /task SSE")
    task_url = f"http://127.0.0.1:{HOST_PORT}/task"
    body = json.dumps({"message": "Reply with exactly the word HELLO and nothing else.", "max_steps": 2}).encode()
    req = urllib.request.Request(
        task_url, data=body, method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Citra-Control": CONTROL_SECRET,
            "Accept": "text/event-stream",
        },
    )
    saw = {"message": False, "done": False, "error": False, "any": 0}
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            for raw in r:
                ln = raw.decode(errors="replace").rstrip("\n")
                if not ln.startswith("data: "):
                    continue
                try:
                    evt = json.loads(ln[6:])
                except json.JSONDecodeError:
                    continue
                saw["any"] += 1
                t = evt.get("type")
                if t in saw:
                    saw[t] = True
                if saw["any"] <= 12:
                    snippet = json.dumps(evt)[:160]
                    print(f"  evt: {snippet}")
                if t == "done":
                    break
    except Exception as e:  # noqa: BLE001
        print(f"  /task stream error: {e!r}")
        print("--- logs ---")
        print(_docker_logs(120))

    print(f"  saw: message={saw['message']} done={saw['done']} error={saw['error']} total={saw['any']}")

    print("[4/4] in-container probes")
    # Install blocker check.
    pip_check = _run(["docker", "exec", CONTAINER, "sh", "-c",
                      "pip install requests; echo EXIT=$?"])
    pip_out = (pip_check.stdout or "") + (pip_check.stderr or "")
    pip_loud = "[CITRA-SANDBOX] install blocked" in pip_out and "EXIT=127" in pip_out
    print(f"  pip-shim loud+127: {pip_loud}")
    if not pip_loud:
        print(f"    ---\n{pip_out}\n    ---")

    # Persistence check.
    persist = _run(["docker", "exec", CONTAINER, "sh", "-c",
                    "ls -la /workspace/.openclaw-home/.openclaw/openclaw.json /workspace/.openclaw/agent/ 2>&1"])
    persist_out = persist.stdout + persist.stderr
    persist_ok = "openclaw.json" in persist_out and "SKILL_ENVIRONMENT.md" in persist_out
    print(f"  persistence: openclaw.json + persona seeded: {persist_ok}")
    if not persist_ok:
        print(f"    ---\n{persist_out}\n    ---")

    # Symlink check.
    syml = _run(["docker", "exec", CONTAINER, "sh", "-c",
                 "for c in apt apt-get pip pip3 npm; do "
                 "readlink -f $(command -v $c) 2>/dev/null | "
                 "xargs -I{} echo $c -> {} ; done"])
    print(f"  symlinks:\n{syml.stdout}")

    overall_ok = health_ok and saw["done"] and pip_loud and persist_ok
    print()
    if overall_ok:
        print("L4 SMOKE: PASS")
    else:
        print("L4 SMOKE: FAIL")
        print("--- last 60 log lines ---")
        print(_docker_logs(60))

    _cleanup()
    return 0 if overall_ok else 1


if __name__ == "__main__":
    sys.exit(main())
