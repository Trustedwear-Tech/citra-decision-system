# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""Smoke test: spawn ephemeral sandbox via host API, hit adapter /health & /status, kill."""
import os, sys, time, json, secrets, httpx

HOST = "http://127.0.0.1:7090"
SECRET = "test-sandbox-host-secret-e2e-2025"
H = {"X-Sandbox-Host-Secret": SECRET}

session_id = "smoke-" + secrets.token_hex(6)
control_secret = secrets.token_hex(16)

payload = {
    "user_id": "smoke-user",
    "session_id": session_id,
    "env": {
        "CITRA_USER_ID": "smoke-user",
        "CITRA_SESSION_ID": session_id,
        "CITRA_SCOPED_TOKEN": "smoke",
        "CITRA_CONTROL_SECRET": control_secret,
        # Inference is unused for smoke; provide harmless values.
        "CITRA_LLM_BASE_URL": "http://127.0.0.1:9",
        "CITRA_LLM_API_KEY": "smoke",
        "CITRA_ACTION_MODEL": "gpt-4o-mini",
    },
}

print("== capacity ==")
r = httpx.get(f"{HOST}/capacity", headers=H, timeout=5)
print(r.status_code, r.text)

print("== spawn ==")
r = httpx.post(f"{HOST}/spawn", headers=H, json=payload, timeout=120)
print(r.status_code)
print(r.text[:600])
r.raise_for_status()
sp = r.json()
adapter = sp["adapter_url"]
print("adapter:", adapter)

print("== adapter /health ==")
r = httpx.get(f"{adapter}/health", timeout=5)
print(r.status_code, r.text)

print("== adapter /status ==")
r = httpx.get(f"{adapter}/status",
              headers={"X-Citra-Control": control_secret}, timeout=5)
print(r.status_code, r.text)

print("== verify tmpfs (docker exec) ==")
import subprocess
out = subprocess.check_output(
    ["docker", "exec", sp["container_id"], "sh", "-c",
     "mount | grep -E 'tmpfs.*workspace|tmpfs.*home/citra|tmpfs.*session-uploads'"],
    text=True,
)
print(out)

print("== teardown ==")
r = httpx.delete(f"{HOST}/session/{session_id}", headers=H, timeout=30)
print(r.status_code, r.text)
print("OK")
