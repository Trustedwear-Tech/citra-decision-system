# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""Drive a builder session and watch what it writes to disk.

Grading is `check_embed_build.py`'s job. This script's only job is to hold the
conversation without hanging — which took four attempts, because the SSE stream
has failed in a different way each time.

    SAS=http://127.0.0.1:9177 python -u scripts/drive_embed_build.py [session_id]

WHY THE STREAM IS NOT THE SOURCE OF TRUTH
-----------------------------------------
Each fix exposed the next fault:

  1. block-buffered stdout — a live conversation looked like a dead process
  2. wrapped in `timeout`, which SIGTERMs Python: no flush even on exit
  3. `say()` blocked forever; the stream does not close when a turn ends
  4. the per-turn deadline was checked INSIDE the frame loop, so a stream that
     delivers nothing at all never iterates and the deadline never fires —
     43 minutes of silence with a 900s deadline set

So the stream is now read on a DAEMON THREAD (a hung read can never block the
process) and the real signal is the POD'S OWN FILESYSTEM. The builder writes
discovery.json, ui_design.md, agent_spec.json, app_spec.json into
/workspace/build as it goes; that is durable, ordered, and cannot lie about
progress the way a socket can.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import jwt

SAS = os.getenv("SAS", "http://127.0.0.1:9177")
TURN_DEADLINE = int(os.getenv("TURN_DEADLINE", "900"))
QUIET_AFTER = int(os.getenv("QUIET_AFTER", "90"))
BUILD_DIR = "/workspace/build"

ROOT = Path(__file__).resolve().parents[1]
env = (ROOT / ".env").read_text(encoding="utf-8", errors="ignore")
SECRET = re.search(r"^JWT_SECRET=(.*)$", env, re.M).group(1).strip()

BA = os.getenv("BA_EMAIL", "ba@acme-bank-demo.citra.ai")
# A build session is owned by the BA's WORK Service Account and every later
# turn is checked against it — without this claim the chat 403s.
WORK_SA = os.getenv("WORK_SA", "svc:work-"
                    + BA.replace("@", "-").replace(".", "-")
                    + "@acme-bank.citra.ai")
TOKEN = jwt.encode(
    {"sub": BA, "user_id": BA, "email": BA,
     "tenant_id": "acme-bank", "org_id": "acme-bank",
     "roles": ["user", "org_admin", "decision-app-builder"],
     "dept_ids": ["lending", "central_ops"],
     "work_sa_id": WORK_SA, "service_account_admin_of": [WORK_SA],
     "iat": int(time.time()), "exp": int(time.time()) + 21600, "iss": "Citra-AI"},
    SECRET, algorithm="HS256")


# Windows consoles default to cp1252, which cannot encode the box-drawing
# characters below — a driver must never die of its own progress output.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def out(*a):
    print(*a, flush=True)


def _req(path, body=None, method="GET"):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(f"{SAS}{path}", data=data, method=method)
    r.add_header("Authorization", f"Bearer {TOKEN}")
    r.add_header("Content-Type", "application/json")
    return r


# ── the pod's filesystem: the honest progress signal ────────────────────────

def pod_name(session: str) -> str:
    # The container name TRUNCATES the session id (citra-builder-bs_412541e07),
    # so match only the prefix that survives.
    r = subprocess.run(
        ["docker", "ps", "--filter", f"name=builder-{session[:12]}",
         "--format", "{{.Names}}"],
        capture_output=True, text=True)
    name = (r.stdout or "").strip().splitlines()
    return name[0] if name else ""


def build_files(pod: str) -> dict[str, int]:
    """{filename: size} under /workspace/build — empty if the pod is gone."""
    if not pod:
        return {}
    r = subprocess.run(
        ["docker", "exec", pod, "sh", "-c",
         f"ls -l {BUILD_DIR} 2>/dev/null | awk '{{print $9\" \"$5}}'"],
        capture_output=True, text=True,
        env={**os.environ, "MSYS_NO_PATHCONV": "1"})
    files = {}
    for line in (r.stdout or "").splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0]:
            try:
                files[parts[0]] = int(parts[1])
            except ValueError:
                pass
    return files


# ── one turn ────────────────────────────────────────────────────────────────

def say(session: str, text: str, pod: str) -> None:
    """Send a turn; return when it is done, quiet, or out of time.

    The read runs on a daemon thread so a wedged socket cannot hold the process
    (fault 3+4). The MAIN thread owns the clock, so the deadline always fires.
    """
    state = {"chunks": [], "last": time.time(), "done": False, "shown": 0}

    def reader():
        try:
            resp = urllib.request.urlopen(
                _req(f"/build/{session}/chat/stream", {"message": text}, "POST"),
                timeout=QUIET_AFTER)
            for raw in resp:
                line = raw.decode(errors="replace").strip()
                if not line.startswith("data:"):
                    continue
                try:
                    evt = json.loads(line[5:].strip())
                except Exception:
                    continue
                if evt.get("isHeartbeat"):
                    continue          # keep-alives are not progress
                state["last"] = time.time()
                piece = (evt.get("deltaText") or evt.get("text")
                         or evt.get("content") or "")
                kind = evt.get("kind") or evt.get("type") or evt.get("event") or ""
                if isinstance(piece, str) and piece and kind in (
                        "chat", "message", "assistant", "text", "session.message"):
                    state["chunks"].append(piece)
                if evt.get("state") in ("complete", "done", "final") or kind in (
                        "done", "complete", "turn.complete"):
                    state["done"] = True
                    return
        except Exception as e:  # noqa: BLE001 — the thread must never take the process with it
            state["err"] = f"{type(e).__name__}: {e}"
        finally:
            state["done"] = True

    t = threading.Thread(target=reader, daemon=True)
    t.start()

    started = time.time()
    before = build_files(pod)
    while True:
        time.sleep(3)
        joined = "".join(state["chunks"])
        if len(joined) > state["shown"]:                  # stream progress
            out("    " + joined[state["shown"]:].strip()[:1200])
            state["shown"] = len(joined)
        if state["done"] and not t.is_alive():
            break
        quiet = time.time() - state["last"]
        if quiet > QUIET_AFTER:
            now = build_files(pod)
            if now != before:                             # disk progress
                out(f"    [stream quiet {int(quiet)}s but files changed: "
                    f"{sorted(set(now) - set(before)) or 'sizes'} — waiting]")
                before, state["last"] = now, time.time()
                continue
            out(f"    [quiet {int(quiet)}s and no new files — next turn]")
            break
        if time.time() - started > TURN_DEADLINE:          # MAIN-thread clock
            out(f"    [deadline {TURN_DEADLINE}s — next turn]")
            break
    if state.get("err"):
        out(f"    [reader: {state['err']}]")
    out(f"    ~build dir: {sorted(build_files(pod)) or '(empty)'}")


TURNS = [
    "We want our credit officers to decide loan applications without leaving "
    "our own loan origination screen.",
    "Yes — the embedded card, rendered inside our own screen. Our page already "
    "shows one application at a time and knows its application_id. The officer "
    "clicks a Review button to get the recommendation — option (a).",
    "Use the loan_origination source: loan_applications, keyed by "
    "application_id. Show product, amount_requested, foir_percent, "
    "sourcing_channel, income_proof_type and status.",
    "Recommend approve or decline against our credit policy (FOIR capped at "
    "50% for unsecured products) and propose writing the decision back with "
    "record_credit_decision. Nothing commits without the officer approving.",
    "Reject reasons: foir_above_cap; income_not_corroborated; "
    "dsa_sourced_needs_verification; data_stale_or_wrong.",
    "That all looks right — please author the specs and publish it now.",
]


def main() -> int:
    if len(sys.argv) > 1:
        session = sys.argv[1]
    else:
        with urllib.request.urlopen(_req("/build", {
                "goal": "A credit officer decision card for loan applications, "
                        "embedded in our own loan origination screen.",
                "build_kinds": ["app"], "primary_page_kind": "embed"},
                "POST"), timeout=300) as r:
            session = json.loads(r.read())["session_id"]
    pod = pod_name(session)
    out(f"\nsession {session}\npod     {pod or '(not found — is it up?)'}\n")

    # Turns 1..N-1 are discovery; if a session already got through them (the
    # stalled run did), resuming at the authoring turn saves ~40 minutes.
    start = int(os.getenv("TURN_START", "1"))
    for i, t in enumerate(TURNS, 1):
        if i < start:
            continue
        out(f"── turn {i}/{len(TURNS)} " + "─" * 44)
        out(f"  BA: {t[:150]}")
        say(session, t, pod)
        out("")

    out("waiting for app_spec.json to appear …")
    deadline = time.time() + 1200
    while time.time() < deadline:
        if "app_spec.json" in build_files(pod):
            out("  app_spec.json written")
            break
        time.sleep(15)
    else:
        out("  never appeared — the builder did not reach authoring")

    out("\ngrade it:\n  MONGO_URI=mongodb://localhost:27077 MONGO_DB=citra_e2e "
        "python scripts/check_embed_build.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
