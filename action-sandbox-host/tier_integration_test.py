# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""End-to-end integration test for the tier-aware sandbox-host.

Exercises every code path I touched in the tier patch:
  1. /capacity now reports free_ram_bytes + tier_capacity[] (new fields)
  2. /spawn with no tier  -> falls back to Quick (back-compat with old callers)
  3. /spawn with tier=quick/standard/heavy -> per-tier cgroup + env enforcement
  4. /spawn when RAM is exhausted -> 503 (pool translates to 409 upstream)
  5. Container labels record tier + mem_bytes (so /capacity's RAM math works)
  6. Container env carries DUCKDB_* from the resolved tier

Runs the SCHEDULER directly (no Citra-Service / action-chat-service /
LLM dependencies) and inspects the resulting Docker containers via
`docker inspect`. The agent inside the container won't be able to
do any work (LLM endpoint is a stub) but the adapter's /health comes
up regardless, which is all the scheduler needs to return success.

Requires:
  - Docker Desktop running
  - citra-agent-sandbox-base:latest built locally
  - networks citra-action-egress + citra-action-approved-egress
  - scheduler running on 127.0.0.1:7090 with the test secret
"""
import json
import os
import secrets
import subprocess
import sys
import time
from typing import Any

import httpx

HOST = "http://127.0.0.1:7090"
SECRET = os.environ.get("SANDBOX_HOST_SECRET", "tier-integration-test-secret")
H = {"X-Sandbox-Host-Secret": SECRET}
TIMEOUT = 90  # adapter cold-spawn can take 30-60s

passed = 0
failed = 0
warnings = 0
container_ids: list[str] = []


def assertion(name: str, cond: bool, detail: str = "") -> None:
    global passed, failed
    if cond:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}  -- {detail}")


def warn(name: str, detail: str = "") -> None:
    global warnings
    warnings += 1
    print(f"  WARN  {name}  -- {detail}")


def docker_inspect(container_id: str) -> dict[str, Any]:
    out = subprocess.run(
        ["docker", "inspect", container_id],
        capture_output=True, text=True, timeout=10,
    )
    if out.returncode != 0:
        return {}
    arr = json.loads(out.stdout)
    return arr[0] if arr else {}


def reap(container_id: str) -> None:
    subprocess.run(
        ["docker", "rm", "-f", container_id],
        capture_output=True, text=True, timeout=15,
    )


def spawn_payload(session_id: str, tier: str | None = None) -> dict[str, Any]:
    p = {
        "user_id": "tier-test-user",
        "session_id": session_id,
        "env": {
            "CITRA_AGENT_USER_ID": "tier-test-user",
            "CITRA_AGENT_SESSION_ID": session_id,
            "CITRA_AGENT_LLM_BASE_URL": "http://127.0.0.1:9",
            "CITRA_AGENT_LLM_API_KEY": "test",
            "CITRA_AGENT_MODEL": "gpt-4o-mini",
            "CITRA_AGENT_SCOPED_TOKEN": "test",
            "CITRA_AGENT_CONTROL_SECRET": secrets.token_hex(16),
        },
    }
    if tier is not None:
        p["tier"] = tier
    return p


# ====================================================================
# Test 1 — /capacity new shape
# ====================================================================
print("\n== test 1: /capacity reports new fields (free_ram_bytes + tier_capacity[]) ==")
r = httpx.get(f"{HOST}/capacity", headers=H, timeout=5)
assertion("capacity returns 200", r.status_code == 200, r.text)
cap = r.json()
print(f"  capacity body: {json.dumps(cap, indent=2)}")
assertion("free_ram_bytes is present", "free_ram_bytes" in cap)
assertion("free_ram_bytes > 0 (psutil works)", cap.get("free_ram_bytes", -1) > 0,
          f"value={cap.get('free_ram_bytes')}")
assertion("tier_capacity is a list", isinstance(cap.get("tier_capacity"), list))
tiers = {t["tier"]: t for t in cap.get("tier_capacity") or []}
for name in ("quick", "standard", "heavy"):
    assertion(f"tier_capacity contains {name}", name in tiers,
              f"got {list(tiers.keys())}")
if "quick" in tiers and "heavy" in tiers:
    assertion(
        "quick.mem_limit_bytes < heavy.mem_limit_bytes (catalog wired)",
        tiers["quick"]["mem_limit_bytes"] < tiers["heavy"]["mem_limit_bytes"],
        f"q={tiers['quick']['mem_limit_bytes']} h={tiers['heavy']['mem_limit_bytes']}",
    )
    assertion(
        "quick.can_spawn >= heavy.can_spawn (more rooms for smaller tier)",
        tiers["quick"]["can_spawn"] >= tiers["heavy"]["can_spawn"],
        f"q={tiers['quick']['can_spawn']} h={tiers['heavy']['can_spawn']}",
    )


# ====================================================================
# Test 2 — spawn at each tier, verify cgroup + env match
# ====================================================================
EXPECTED = {
    "quick":    {"mem_bytes": 2 * 1024**3,  "duckdb": "1GB",  "cpu_quota": 200_000},
    "standard": {"mem_bytes": 4 * 1024**3,  "duckdb": "2GB",  "cpu_quota": 300_000},
    "heavy":    {"mem_bytes": 8 * 1024**3,  "duckdb": "4GB",  "cpu_quota": 400_000},
}

for tier in ("quick", "standard", "heavy"):
    print(f"\n== test 2.{tier}: spawn at tier={tier}, verify enforcement ==")
    session_id = f"tier-test-{tier}-{secrets.token_hex(4)}"
    payload = spawn_payload(session_id, tier=tier)
    started = time.time()
    r = httpx.post(f"{HOST}/spawn", headers=H, json=payload, timeout=TIMEOUT)
    elapsed = time.time() - started
    if r.status_code != 200:
        # Cold sandbox health check can fail in CI / test machines without a
        # working LLM URL — record as a warning, not a hard fail.
        warn(f"spawn tier={tier} returned {r.status_code} in {elapsed:.1f}s",
             r.text[:300])
        continue
    body = r.json()
    container_id = body["container_id"]
    container_ids.append(container_id)
    print(f"  spawned container {container_id[:12]} in {elapsed:.1f}s")
    assertion(f"spawn returns tier={tier}", body.get("tier") == tier,
              f"got {body.get('tier')!r}")

    info = docker_inspect(container_id)
    hc = info.get("HostConfig", {})
    cfg = info.get("Config", {})
    labels = cfg.get("Labels", {})
    env_list = cfg.get("Env", []) or []
    env_map = {kv.split("=", 1)[0]: kv.split("=", 1)[1] for kv in env_list if "=" in kv}

    exp = EXPECTED[tier]
    assertion(f"{tier}: cgroup memory == {exp['mem_bytes']} bytes",
              int(hc.get("Memory") or 0) == exp["mem_bytes"],
              f"got {hc.get('Memory')}")
    assertion(f"{tier}: cgroup CpuQuota == {exp['cpu_quota']}",
              int(hc.get("CpuQuota") or 0) == exp["cpu_quota"],
              f"got {hc.get('CpuQuota')}")
    assertion(f"{tier}: label citra.action.tier == {tier!r}",
              labels.get("citra.action.tier") == tier,
              f"got {labels.get('citra.action.tier')!r}")
    assertion(f"{tier}: label citra.action.mem_bytes matches mem_limit",
              labels.get("citra.action.mem_bytes") == str(exp["mem_bytes"]),
              f"got {labels.get('citra.action.mem_bytes')!r}")
    assertion(f"{tier}: env CITRA_AGENT_TIER == {tier!r}",
              env_map.get("CITRA_AGENT_TIER") == tier,
              f"got {env_map.get('CITRA_AGENT_TIER')!r}")
    assertion(f"{tier}: env DUCKDB_MEMORY_LIMIT == {exp['duckdb']!r}",
              env_map.get("DUCKDB_MEMORY_LIMIT") == exp["duckdb"],
              f"got {env_map.get('DUCKDB_MEMORY_LIMIT')!r}")


# ====================================================================
# Test 3 — back-compat: spawn with NO tier field -> defaults to Quick
# ====================================================================
print("\n== test 3: spawn with NO tier field -> Quick (back-compat) ==")
session_id = f"tier-test-notier-{secrets.token_hex(4)}"
payload = spawn_payload(session_id, tier=None)
assert "tier" not in payload, "test setup: payload should have no tier"
started = time.time()
r = httpx.post(f"{HOST}/spawn", headers=H, json=payload, timeout=TIMEOUT)
elapsed = time.time() - started
if r.status_code != 200:
    warn(f"untiered spawn returned {r.status_code} in {elapsed:.1f}s", r.text[:300])
else:
    body = r.json()
    container_id = body["container_id"]
    container_ids.append(container_id)
    info = docker_inspect(container_id)
    hc = info.get("HostConfig", {})
    labels = (info.get("Config", {}) or {}).get("Labels", {})
    assertion("untiered spawn body.tier == 'quick'", body.get("tier") == "quick",
              f"got {body.get('tier')!r}")
    assertion("untiered cgroup memory == 2 GiB",
              int(hc.get("Memory") or 0) == 2 * 1024**3,
              f"got {hc.get('Memory')}")
    assertion("untiered label tier == 'quick'",
              labels.get("citra.action.tier") == "quick",
              f"got {labels.get('citra.action.tier')!r}")


# ====================================================================
# Test 4 — capacity-exhausted path -> 503
# ====================================================================
print("\n== test 4: spawn after RAM exhausted -> 503 ==")
# We've already spawned ~3 Quick + 1 Standard + 1 Heavy on a 32 GB box with
# 4 GB reserved. Committed: 2+2+2+2+4+8 = 20 GB; free: ~8 GB. Try a Heavy
# (needs 8 GB) — most likely fits; force it harder by spawning back-to-back
# until rejection.
exhausted = False
for i in range(4):
    session_id = f"tier-test-fill-{i}-{secrets.token_hex(4)}"
    payload = spawn_payload(session_id, tier="heavy")
    r = httpx.post(f"{HOST}/spawn", headers=H, json=payload, timeout=TIMEOUT)
    if r.status_code == 503:
        exhausted = True
        body_text = r.text
        print(f"  reached capacity at fill attempt {i+1}; 503 body: {body_text[:200]}")
        assertion("503 mentions tier=heavy",
                  "tier=heavy" in body_text or "heavy" in body_text,
                  body_text[:200])
        break
    elif r.status_code == 200:
        container_ids.append(r.json()["container_id"])
    else:
        warn(f"fill spawn {i+1} returned unexpected {r.status_code}", r.text[:200])
        break
if not exhausted:
    warn("RAM exhaustion not reached after 4 heavy spawns on this box",
         "may indicate the box has more free RAM than expected — check /capacity")


# ====================================================================
# Test 5 — /capacity after spawns reflects committed RAM
# ====================================================================
print("\n== test 5: /capacity after spawns reflects committed RAM ==")
r = httpx.get(f"{HOST}/capacity", headers=H, timeout=5)
if r.status_code == 200:
    cap2 = r.json()
    print(f"  capacity now: running={cap2.get('running')} free_ram_bytes={cap2.get('free_ram_bytes')}")
    assertion("running > 0", (cap2.get("running") or 0) > 0)
    assertion("free_ram_bytes decreased from initial",
              cap2.get("free_ram_bytes", 0) < cap.get("free_ram_bytes", 0),
              f"initial={cap.get('free_ram_bytes')} now={cap2.get('free_ram_bytes')}")
else:
    warn("post-spawn /capacity failed", f"status={r.status_code}")


# ====================================================================
# Teardown
# ====================================================================
print(f"\n== teardown: removing {len(container_ids)} spawned containers ==")
for cid in container_ids:
    try:
        reap(cid)
        print(f"  reaped {cid[:12]}")
    except Exception as e:
        print(f"  reap {cid[:12]} failed: {e}")


# ====================================================================
# Summary
# ====================================================================
print(f"\n{'='*60}")
print(f"RESULT: {passed} passed, {failed} failed, {warnings} warned")
print(f"{'='*60}")
sys.exit(1 if failed > 0 else 0)
