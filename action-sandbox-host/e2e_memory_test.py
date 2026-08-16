# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""
L4 E2E — OpenClaw cross-session memory restore (Phase 13).

Verifies the disk-cache-of-Mongo design end-to-end:

  1. Mint a scoped action-sandbox JWT (same way Citra-Service does it).
  2. PUT a memory note via Citra-Service /action-chat/internal/scratch/
     memory/<slug>  ->  upsert in Mongo (action-chat-open-claw-temp).
  3. Spawn a real container via the sandbox-host /spawn.
     entrypoint.sh runs the bulk-restore step on boot.
  4. docker exec -> assert /workspace/memory/<slug>.md exists with the
     expected title + body.
  5. Negative isolation: a *different* user spawns and must NOT see the
     first user's memory note.
  6. Cleanup: stop both sandboxes + delete the memory doc.

Requires:
  - Citra-Service       at 127.0.0.1:8085   (uvicorn task running)
  - action-sandbox-host at 127.0.0.1:7090   (running)
  - docker daemon reachable, image
    SANDBOX_HOST_IMAGE=citra-agent-sandbox-base:ephemeral built locally
  - .env JWT_SECRET, SANDBOX_HOST_SECRET match between the two services.

Does NOT call any LLM. The container is stopped well before /task is
ever sent — we only validate the boot-time memory restore.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx
import jwt

# ---- env load -------------------------------------------------------------
SVC_ENV = Path(r"c:\Github\Citra-AI\Citra-Service\.env")
HOST_ENV = Path(r"c:\Github\Citra-AI\action-sandbox-host\.env.local")
for p in (SVC_ENV, HOST_ENV):
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip())

JWT_SECRET = os.environ["JWT_SECRET"]
JWT_ISSUER = os.environ.get("JWT_ISSUER", "Citra-AI")
SANDBOX_HOST_SECRET = os.environ["SANDBOX_HOST_SECRET"]
SANDBOX_HOST = "http://127.0.0.1:7090"
CITRA_SVC = "http://127.0.0.1:8085"
# host.docker.internal is how the container reaches the host's Citra-Service.
PROXY_BASE_FOR_CONTAINER = "http://host.docker.internal:8085"


def _mint(user_id: str, session_id: str) -> str:
    """Same shape as services/action_scoped_token.mint_scoped_token."""
    now = int(time.time())
    return jwt.encode(
        {
            "sub": user_id,
            "user_id": user_id,
            "email": f"{user_id}@e2e.test",
            "org_id": None,
            "dept_ids": [],
            "roles": ["user"],
            "scope": "action-sandbox",
            "session_id": session_id,
            "aud": os.environ.get(
                "CITRA_ACTION_SCOPED_TOKEN_AUDIENCE", "citra-action-sandbox"
            ),
            "iss": JWT_ISSUER,
            "iat": now,
            "exp": now + 900,
        },
        JWT_SECRET,
        algorithm="HS256",
    )


def _put_memory(token: str, slug: str, *, title: str, body: str,
                tags: list[str]) -> None:
    r = httpx.put(
        f"{CITRA_SVC}/action-chat/internal/scratch/memory/{slug}",
        headers={"Authorization": f"Bearer {token}"},
        json={"body": body, "title": title, "tags": tags},
        timeout=15.0,
    )
    r.raise_for_status()
    print(f"  PUT memory/{slug} -> {r.status_code} {r.json().get('slug')!r}")


def _delete_memory(token: str, slug: str) -> None:
    httpx.delete(
        f"{CITRA_SVC}/action-chat/internal/scratch/memory/{slug}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=15.0,
    )


def _spawn(user_id: str, session_id: str, scoped_token: str) -> dict:
    body = {
        "user_id": user_id,
        "session_id": session_id,
        "env": {
            "CITRA_USER_ID": user_id,
            "CITRA_SESSION_ID": session_id,
            "CITRA_SCOPED_TOKEN": scoped_token,
            "CITRA_CONTROL_SECRET": "e2e-control-" + session_id[:8],
            "CITRA_PROXY_BASE_URL": PROXY_BASE_FOR_CONTAINER,
            # LLM creds: entrypoint.sh requires these to be NON-EMPTY even
            # though we never send /task. Pull from CITRA_ACTION_* (the
            # sandbox-manager env-naming) with a fallback to CITRA_*.
            "CITRA_LLM_BASE_URL": (
                os.environ.get("CITRA_LLM_BASE_URL")
                or os.environ.get("CITRA_ACTION_LLM_BASE_URL")
                or "https://openrouter.ai/api/v1"
            ),
            "CITRA_LLM_API_KEY": (
                os.environ.get("CITRA_LLM_API_KEY")
                or os.environ.get("CITRA_ACTION_LLM_API_KEY")
                or "unused-by-l4-memory-test"
            ),
            "CITRA_ACTION_MODEL": (
                os.environ.get("CITRA_ACTION_MODEL")
                or "deepseek/deepseek-chat-v3.1"
            ),
        },
        "labels": {"citra.test": "l4-memory"},
    }
    r = httpx.post(
        f"{SANDBOX_HOST}/spawn",
        headers={"X-Sandbox-Host-Secret": SANDBOX_HOST_SECRET},
        json=body,
        timeout=120.0,
    )
    if r.status_code != 200:
        raise RuntimeError(f"spawn failed {r.status_code}: {r.text[:400]}")
    return r.json()


def _stop(session_id: str) -> None:
    httpx.delete(
        f"{SANDBOX_HOST}/session/{session_id}",
        headers={"X-Sandbox-Host-Secret": SANDBOX_HOST_SECRET},
        timeout=30.0,
    )


def _wait_for_memory_dir(container: str, *, timeout: float = 60.0) -> list[str]:
    """Poll `docker exec ls /workspace/memory` until entrypoint finishes
    the bulk-restore (or timeout). Returns the file list."""
    deadline = time.time() + timeout
    last_err = ""
    while time.time() < deadline:
        try:
            out = subprocess.run(
                ["docker", "exec", container, "ls", "-1", "/workspace/memory"],
                capture_output=True, text=True, timeout=10,
            )
            if out.returncode == 0:
                files = [f for f in out.stdout.strip().splitlines() if f]
                if files:
                    return files
                # Dir exists but empty — entrypoint may still be restoring.
                last_err = "empty"
            else:
                last_err = (out.stderr or out.stdout).strip()[:200]
        except subprocess.TimeoutExpired:
            last_err = "docker exec timeout"
        time.sleep(1.5)
    raise TimeoutError(
        f"memory dir never populated for {container}: last={last_err!r}"
    )


def _read_in_container(container: str, path: str) -> str:
    out = subprocess.run(
        ["docker", "exec", container, "cat", path],
        capture_output=True, text=True, timeout=10,
    )
    if out.returncode != 0:
        raise RuntimeError(f"cat {path} failed: {out.stderr.strip()}")
    return out.stdout


# =============================================================== test ====
def main() -> int:
    user_a = f"e2e-mem-a-{uuid.uuid4().hex[:8]}"
    user_b = f"e2e-mem-b-{uuid.uuid4().hex[:8]}"
    sess_a = f"sess-a-{uuid.uuid4().hex[:12]}"
    sess_b = f"sess-b-{uuid.uuid4().hex[:12]}"
    slug = "decision-pricing-2026"
    title = "E2E Pricing Decision"
    body = "We chose Postgres over DuckDB for the dashboard mirror on 2026-05-01."
    tags = ["decision", "pricing"]

    tok_a = _mint(user_a, sess_a)
    tok_b = _mint(user_b, sess_b)

    failures: list[str] = []
    spawned: list[str] = []  # session_ids to stop
    container_a = container_b = ""

    try:
        # 1. Pre-seed memory for user A.
        print("== seed memory for user A ==")
        _put_memory(tok_a, slug, title=title, body=body, tags=tags)

        # 2. Spawn container for user A.
        print(f"\n== spawn user A ({user_a}, {sess_a}) ==")
        info_a = _spawn(user_a, sess_a, tok_a)
        spawned.append(sess_a)
        container_a = info_a["container_name"]
        print(f"  container={container_a} adapter={info_a['adapter_url']}")

        # 3. Wait for bulk-restore to populate /workspace/memory.
        print("\n== wait for bulk-restore (user A) ==")
        files_a = _wait_for_memory_dir(container_a, timeout=90.0)
        print(f"  files: {files_a}")
        expected = f"{slug}.md"
        if expected not in files_a:
            failures.append(
                f"user A: expected {expected!r} in /workspace/memory, "
                f"got {files_a!r}"
            )

        # 4. Validate file content.
        if expected in files_a:
            content = _read_in_container(container_a, f"/workspace/memory/{expected}")
            print(f"  file content ({len(content)}b):")
            for line in content.splitlines()[:6]:
                print(f"    | {line}")
            if title not in content:
                failures.append(f"user A: title {title!r} missing in {expected}")
            if body not in content:
                failures.append(f"user A: body missing in {expected}")
            if "tags:" not in content.lower():
                failures.append(f"user A: tags comment missing in {expected}")

        # 5. Spawn a SECOND container for user B and prove isolation.
        print(f"\n== spawn user B ({user_b}, {sess_b}) ==")
        info_b = _spawn(user_b, sess_b, tok_b)
        spawned.append(sess_b)
        container_b = info_b["container_name"]
        print(f"  container={container_b}")

        # User B has no notes; bulk-restore should print "no prior notes"
        # and leave the dir empty. Allow the dir to be either absent of
        # the slug or fully empty.
        print("\n== verify user B has no leak ==")
        try:
            files_b = _wait_for_memory_dir(container_b, timeout=20.0)
        except TimeoutError:
            files_b = []  # empty dir is the expected outcome
        print(f"  user B files: {files_b}")
        if expected in files_b:
            failures.append(
                f"ISOLATION BREACH: user B sees user A's note {expected!r}"
            )

    except Exception as e:
        failures.append(f"unexpected exception: {e!r}")
        import traceback
        traceback.print_exc()
    finally:
        print("\n== cleanup ==")
        for sid in spawned:
            try:
                _stop(sid)
                print(f"  stopped {sid}")
            except Exception as e:
                print(f"  stop {sid} failed: {e}")
        try:
            _delete_memory(tok_a, slug)
            print(f"  deleted memory {slug}")
        except Exception as e:
            print(f"  delete memory failed: {e}")

    print("\n== result ==")
    if failures:
        print("FAIL")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("PASS — memory restored across container boot, isolation enforced")
    return 0


if __name__ == "__main__":
    sys.exit(main())
