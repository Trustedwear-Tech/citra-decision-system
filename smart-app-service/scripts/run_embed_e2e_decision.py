"""Drive a REAL recommendation through the embed path and read it back.

Everything here is live: the LLM, the dept-MCP, Postgres, the staging queue.
Run it before the browser step so the card has a pending recommendation to show.

    SAS=http://localhost:3100 python scripts/run_embed_e2e_decision.py
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import jwt

BASE = os.getenv("SAS", "http://localhost:3100")   # via the RUNTIME proxy
SLUG = "embed-e2e-loan"
LIVE_KEY = "emb_live_e2e0000000000002"
RECORD = os.getenv("RECORD_ID", "LAN-2026-000001")

env = (Path(__file__).resolve().parents[1] / ".env").read_text(
    encoding="utf-8", errors="ignore")
SECRET = re.search(r"^JWT_SECRET=(.*)$", env, re.M).group(1).strip()
TOKEN = jwt.encode(
    {"sub": "officer@acme-bank.com", "user_id": "officer@acme-bank.com",
     "email": "officer@acme-bank.com", "tenant_id": "acme-bank",
     # dept_admin, not plain 'user': the dept-MCP refuses record_credit_decision
     # to a bare user (403 "requires dept_admin/org_admin/super_admin"). A real
     # credit officer who can commit a decision holds that role — worth knowing
     # when an embed integration returns writes it cannot perform.
     "org_id": "acme-bank", "roles": ["user", "dept_admin"],
     "dept_ids": ["lending"],
     "iat": int(time.time()), "exp": int(time.time()) + 3600, "iss": "Citra-AI"},
    SECRET, algorithm="HS256")


def call(path, body=None, method="GET"):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{BASE}{path}", data=data, method=method)
    req.add_header("Authorization", f"Bearer {TOKEN}")
    req.add_header("Content-Type", "application/json")
    # The header that keeps an embed bound to its own environment.
    req.add_header("X-Citra-Embed-Key", LIVE_KEY)
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            raw = r.read().decode(errors="replace")
            ctype = r.headers.get("content-type", "")
            if "event-stream" in ctype:
                # The run route streams so a long agent turn never hits a
                # gateway idle timeout; the final frame carries the result.
                for line in raw.splitlines():
                    if line.startswith("data: "):
                        evt = json.loads(line[6:])
                        if evt.get("type") == "done":
                            return r.status, evt.get("result")
                return r.status, {"_raw": raw[:400]}
            return r.status, (json.loads(raw) if raw.strip().startswith(("{", "[")) else raw)
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        try:
            return e.code, json.loads(body)
        except Exception:
            return e.code, body


print("\n── 1. run the agent (real LLM) ─────────────────────────────")
s, run = call(f"/api/run/{SLUG}",
              {"action": "review_application",
               "inputs": {"application_id": RECORD},
               "mode": "queue_action"}, "POST")
if s != 200 or not isinstance(run, dict):
    print(f"  FAILED HTTP {s}: {str(run)[:400]}")
    sys.exit(1)

cid = run.get("correlation_id")
steps = [t.get("step") for t in (run.get("timeline") or [])]
writes = run.get("planned_writes") or []
print(f"  status          : {run.get('status')}")
print(f"  correlation_id  : {cid}")
print(f"  decision        : {run.get('decision')}")
print(f"  reasoning       : {str(run.get('reasoning'))[:150]}")
print(f"  timeline        : {steps}")
print(f"  planned_writes  : {len(writes)}")
for w in writes[:2]:
    print(f"     → {w.get('action_id')} on {w.get('dataset_id')}: "
          f"{json.dumps(w.get('payload'))[:160]}")

if run.get("status") != "pending_approval":
    print("\n  NOTE: not staged for approval — nothing for the officer to act on.")
    print("        (a run with no planned_writes completes outright)")
    sys.exit(1)

print("\n── 2. the officer REJECTS, with a reason code ──────────────")
# The reason code is the signal the learning layer runs on. Rejecting is the
# interesting path: approving teaches nothing the agent did not already say.
s, appr = call(
    f"/api/apps/{SLUG}/approve/{cid}",
    {"decision": "reject",
     "reason_code": "dsa_sourced_needs_verification",
     "decision_reason": "dsa_sourced_needs_verification",
     "note": "Digital-sourced but income proof is a payslip only — verify with "
             "bank statements before declining on FOIR alone."},
    "POST")
print(f"  HTTP {s}")
print(f"  status : {appr.get('status') if isinstance(appr, dict) else str(appr)[:200]}")
if isinstance(appr, dict) and appr.get("error"):
    print(f"  error  : {appr.get('error')}")

print("\n── 3. did the correction land? ─────────────────────────────")
print("  (checked directly in Mongo by the caller — see probe output)")
print(json.dumps({"correlation_id": cid,
                  "approve_http": s,
                  "approve_status": appr.get("status") if isinstance(appr, dict) else None},
                 indent=1))
