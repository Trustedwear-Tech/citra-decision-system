# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""End-to-end Action Chat smoke test.

Requires:
  - sandbox-host running at 127.0.0.1:7090
  - Citra-Service running at 127.0.0.1:8085
  - .env with JWT_SECRET
"""
import os, sys, json, time, httpx, jwt
from pathlib import Path

# Load Citra-Service .env to get JWT_SECRET
env_path = Path(r"c:\Github\Citra-AI\Citra-Service\.env")
for line in env_path.read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    k, _, v = line.partition("=")
    os.environ.setdefault(k.strip(), v.strip())

JWT_SECRET = os.environ["JWT_SECRET"]

now = int(time.time())
token = jwt.encode(
    {
        "user_id": "e2e-test-user",
        "email": "e2e@citra.test",
        "iat": now,
        "exp": now + 3600,
    },
    JWT_SECRET,
    algorithm="HS256",
)
print("JWT minted. user_id=e2e-test-user")

base = "http://127.0.0.1:8085"
H = {"Authorization": f"Bearer {token}"}

print("\n== POST /action-chat/query/stream ==")
payload = {
    "message": "Use report.make_pdf to generate a one-page PDF report titled 'E2E HELLO' with a single section saying 'It works.' Then stop.",
    "max_steps": 4,
}

artifact_url = None
sid = None
with httpx.stream("POST", f"{base}/action-chat/query/stream",
                  headers={**H, "Content-Type": "application/json"},
                  json=payload, timeout=900.0) as r:
    print("HTTP", r.status_code)
    if r.status_code != 200:
        print(r.read().decode("utf-8", errors="replace")[:800])
        sys.exit(1)
    cur_event = None
    for raw in r.iter_lines():
        if not raw:
            continue
        line = raw if isinstance(raw, str) else raw.decode("utf-8", errors="replace")
        if line.startswith("event:"):
            cur_event = line[6:].strip()
            continue
        if line.startswith("data:"):
            try:
                evt = json.loads(line[5:].strip())
            except Exception:
                print("[non-json]", line[:160]); continue
            t = evt.get("type") or cur_event
            if t == "session":
                sid = evt.get("session_id")
                print(f"  session_id={sid}")
            elif t == "step":
                print(f"  step #{evt.get('index')} tool={evt.get('tool')}")
            elif t == "artifact":
                print(f"  ARTIFACT kind={evt.get('kind')} title={evt.get('title')!r}")
                if evt.get("url"):
                    artifact_url = evt["url"]
                    print(f"  url={artifact_url[:120]}...")
            elif t == "message":
                txt = (evt.get("text") or "")[:200]
                print(f"  message: {txt}")
            elif t == "error":
                print(f"  ERROR: {evt}")
            elif t in ("done", "complete"):
                print(f"  DONE: {evt}")
            else:
                print(f"  [{t}] {str(evt)[:160]}")

print("\n== verify artifact url ==")
if artifact_url:
    h = httpx.head(artifact_url, timeout=15.0, follow_redirects=True)
    print("HEAD", h.status_code, "content-type=", h.headers.get("content-type"),
          "content-length=", h.headers.get("content-length"))
else:
    print("NO ARTIFACT URL RECEIVED")

print("\n== verify lease in redis ==")
import redis
r = redis.Redis(
    host=os.environ["REDIS_HOST"],
    port=int(os.environ["REDIS_PORT"]),
    username=os.environ.get("REDIS_USERNAME") or None,
    password=os.environ.get("REDIS_PASSWORD") or None,
    ssl=os.environ.get("REDIS_SSL", "false").lower() == "true",
    db=int(os.environ.get("REDIS_DB", "0")),
)
key = "action:lease:e2e-test-user"
val = r.get(key)
print(key, "->", val[:200] if val else None)

print("\n== teardown sandbox ==")
if sid:
    h2 = httpx.post(f"{base}/action-chat/task/{sid}/cancel", headers=H, timeout=30)
    print("cancel:", h2.status_code, h2.text[:160])
print("DONE")
