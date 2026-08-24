# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""Drive one REAL builder session per surface and grade what it authors.

    SAS=http://127.0.0.1:9100 python -u scripts/drive_all_surfaces.py [embed|app|dashboard|api]

WHY
---
Everything about how the builder decides a surface changed today — AGENTS.md's
routing rules, the citra-embed-spec skill, DetailPanel.actions in the schema,
the static checks, and the surface note now prefixed onto the first turn. All of
that is INPUT to an LLM. The deterministic half (publish → store → deliver) is
covered by tests/test_publish_roundtrip.py and proves none of it: a spec that
publishes perfectly can still be the wrong shape for the surface the BA asked
for.

So this drives the real thing, once per surface, and grades the AUTHORED spec:
does an embed build produce a detail-only card with the trigger on the detail,
does a dashboard still get its chart, does an API build author no pages at all.

Turn mechanics (daemon-thread reader, disk-polled progress) are inherited from
drive_embed_build.py — see its docstring for the four ways the SSE stream lied.
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

SAS = os.getenv("SAS", "http://127.0.0.1:9100")
TURN_DEADLINE = int(os.getenv("TURN_DEADLINE", "900"))
QUIET_AFTER = int(os.getenv("QUIET_AFTER", "90"))
BUILD_DIR = "/workspace/build"
#: Extra content-free "carry on" turns allowed after the scripted ones.
MAX_NUDGES = int(os.getenv("MAX_NUDGES", "3"))

ROOT = Path(__file__).resolve().parents[1]
# Only needed when minting locally; a CITRA_TOKEN run has no local .env to read.
_m = re.search(r"^JWT_SECRET=(.*)$",
               (ROOT / ".env").read_text(encoding="utf-8", errors="ignore")
               if (ROOT / ".env").exists() else "", re.M)
# JWT_SECRET from the environment wins — that is how a run INSIDE a service
# container gets it (the local .env does not exist there). Falls back to the
# repo .env for ordinary local runs.
SECRET = os.getenv("JWT_SECRET", "").strip() or (_m.group(1).strip() if _m else "")

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def out(*a):
    print(*a, flush=True)


#: A pre-minted bearer token. Set this to drive a REMOTE stack (prod) whose
#: JWT_SECRET must not be copied onto a laptop: mint on the box, where the
#: secret already lives, and pass only the short-lived token here.
PREMINTED = os.getenv("CITRA_TOKEN", "").strip()


def token(ba: str) -> str:
    if PREMINTED:
        return PREMINTED
    # Checked HERE, not at import: a CITRA_TOKEN run legitimately has no signing
    # key, and refusing at module scope would break the very mode that exists so
    # the key never reaches a laptop. This is the one point where a signature is
    # actually required and we know we cannot produce one.
    if not SECRET:
        raise SystemExit(
            "No signing key, and no pre-minted token -- refusing to continue.\n"
            "\n"
            "Fix it one of these ways:\n"
            "  CITRA_TOKEN=<token>   drive a REMOTE stack; mint it on the box, where\n"
            "                        the signing key already lives, and never copy the\n"
            "                        key itself onto a laptop\n"
            "  JWT_SECRET=<key>      mint locally from the environment\n"
            f"  {ROOT / '.env'}\n"
            "                        a JWT_SECRET=... line here, for ordinary local runs\n"
            "\n"
            "Continuing would mint a token signed with an EMPTY key. That token is\n"
            "well-formed, so nothing complains until the far end rejects it as a bare\n"
            "401 -- which reads as a permissions problem and sends you auditing the\n"
            "user, the roles and the token expiry, none of which are wrong."
        )
    sa = "svc:work-" + ba.replace("@", "-").replace(".", "-") + "@acme-bank.citra.ai"
    return jwt.encode({
        "sub": ba, "user_id": ba, "email": ba,
        "tenant_id": "acme-bank", "org_id": "acme-bank",
        "roles": ["user", "org_admin", "decision-app-builder"],
        "dept_ids": ["lending", "central_ops"],
        "work_sa_id": sa, "service_account_admin_of": [sa],
        "iat": int(time.time()), "exp": int(time.time()) + 21600, "iss": "Citra-AI",
    }, SECRET, algorithm="HS256")


def req(tok, path, body=None, method="GET"):
    r = urllib.request.Request(
        f"{SAS}{path}",
        data=json.dumps(body).encode() if body is not None else None,
        method=method)
    r.add_header("Authorization", f"Bearer {tok}")
    r.add_header("Content-Type", "application/json")
    return r


def pod_name(session: str) -> str:
    # No docker CLI when this runs INSIDE a container (driving a remote stack
    # from the box). Disk-polling is then unavailable; the stream + per-turn
    # deadline still drive the conversation, and the published app is verified
    # from the store afterwards instead.
    try:
        r = subprocess.run(
            ["docker", "ps", "--filter", f"name=builder-{session[:12]}",
             "--format", "{{.Names}}"], capture_output=True, text=True)
    except (FileNotFoundError, OSError):
        return ""
    n = (r.stdout or "").strip().splitlines()
    return n[0] if n else ""


def build_files(pod: str) -> dict:
    if not pod:
        return {}
    try:
        r = subprocess.run(
            ["docker", "exec", pod, "sh", "-c",
             f"ls -l {BUILD_DIR} 2>/dev/null | awk '{{print $9\" \"$5}}'"],
            capture_output=True, text=True,
            env={**os.environ, "MSYS_NO_PATHCONV": "1"})
    except (FileNotFoundError, OSError):
        return {}
    files = {}
    for line in (r.stdout or "").splitlines():
        p = line.split()
        if len(p) == 2 and p[0]:
            try:
                files[p[0]] = int(p[1])
            except ValueError:
                pass
    return files


def say(tok, session: str, text: str, pod: str) -> None:
    """One turn. Reader on a daemon thread; the MAIN thread owns the clock."""
    st = {"chunks": [], "last": time.time(), "done": False, "shown": 0}

    def reader():
        try:
            resp = urllib.request.urlopen(
                req(tok, f"/build/{session}/chat/stream", {"message": text}, "POST"),
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
                    continue
                st["last"] = time.time()
                piece = (evt.get("deltaText") or evt.get("text")
                         or evt.get("content") or "")
                kind = evt.get("kind") or evt.get("type") or evt.get("event") or ""
                if isinstance(piece, str) and piece and kind in (
                        "chat", "message", "assistant", "text", "session.message"):
                    st["chunks"].append(piece)
                if evt.get("state") in ("complete", "done", "final") or kind in (
                        "done", "complete", "turn.complete"):
                    st["done"] = True
                    return
        except Exception as e:  # noqa: BLE001 — must never take the process with it
            st["err"] = f"{type(e).__name__}: {e}"
        finally:
            st["done"] = True

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    started = time.time()
    before = build_files(pod)
    while True:
        time.sleep(3)
        joined = "".join(st["chunks"])
        if len(joined) > st["shown"]:
            out("    " + joined[st["shown"]:].strip()[:900])
            st["shown"] = len(joined)
        if st["done"] and not t.is_alive():
            break
        if time.time() - st["last"] > QUIET_AFTER:
            now = build_files(pod)
            if now != before:
                out(f"    [quiet but files changed: {sorted(set(now) - set(before)) or 'sizes'}]")
                before, st["last"] = now, time.time()
                continue
            out("    [quiet and no new files — next turn]")
            break
        if time.time() - started > TURN_DEADLINE:
            out(f"    [deadline {TURN_DEADLINE}s — next turn]")
            break
    if st.get("err"):
        out(f"    [reader: {st['err']}]")


# ── the four surfaces ───────────────────────────────────────────────────────
# Each opener is deliberately NEUTRAL about the surface: it describes the
# business problem only. The surface must come from the BA's picker choice
# (build_kinds / primary_page_kind / build_headless → the prefixed surface
# note), which is exactly what is under test. An opener that said "build me an
# embedded card" would test nothing.

SURFACES = {
    "embed": {
        "build": {"build_kinds": ["app"], "primary_page_kind": "embed"},
        "turns": [
            "Our credit officers decide loan applications. Use the "
            "loan_origination source: loan_applications, keyed by application_id.",
            "Recommend approve or decline against our credit policy (FOIR capped "
            "at 50% for unsecured) and propose writing the decision back with "
            "record_credit_decision. Reject reasons: foir_above_cap; "
            "income_not_corroborated; dsa_sourced_needs_verification.",
            "That's right — author the specs and publish it now.",
        ],
    },
    "app": {
        "build": {"build_kinds": ["app"], "primary_page_kind": "standard"},
        "turns": [
            "Our credit officers work a queue of loan applications. Use the "
            "loan_origination source: loan_applications, keyed by application_id.",
            "They open one, see the details, and get a recommendation to approve "
            "or decline against the FOIR cap. Reject reasons: foir_above_cap; "
            "income_not_corroborated.",
            "That's right — author the specs and publish it now.",
        ],
    },
    "dashboard": {
        "build": {"build_kinds": ["app"], "primary_page_kind": "dashboard"},
        "turns": [
            "Our lending head wants to see how the loan book is doing. Use the "
            "loan_origination source: loan_applications.",
            "Show volumes and amounts over time, and a breakdown by product and "
            "sourcing channel.",
            "That's right — author the specs and publish it now.",
        ],
    },
    "api": {
        "build": {"build_kinds": ["app"], "build_headless": True},
        "turns": [
            "We want a credit decision on a loan application. Use the "
            "loan_origination source: loan_applications, keyed by application_id.",
            "Approve or decline against the FOIR cap (50% unsecured), with the "
            "reasoning. Our own front-end will call it.",
            "That's right — author the specs and publish it now.",
        ],
    },
}


def drive(name: str) -> str:
    cfg = SURFACES[name]
    ba = f"ba-{name}-{int(time.time()) % 100000}@acme-bank-demo.citra.ai"
    tok = token(ba)
    body = dict(cfg["build"])
    with urllib.request.urlopen(req(tok, "/build", body, "POST"), timeout=300) as r:
        session = json.loads(r.read())["session_id"]
    pod = pod_name(session)
    out(f"\n{'='*70}\n{name.upper()}  session={session}  pod={pod or '(none)'}\n{'='*70}")
    for i, t in enumerate(cfg["turns"], 1):
        out(f"\n-- turn {i}/{len(cfg['turns'])} --\n  BA: {t[:130]}")
        say(tok, session, t, pod)

    # KEEP GOING until the specs exist. A fixed turn count is wrong because the
    # paths are not the same length: embed/dashboard/api author in 3 turns, but
    # the App path runs a UI-design phase and asks more. The first run of this
    # script ended the app session on a genuine question ("what's the FOIR
    # cap?") with no turn left to answer it, and the empty build dir read
    # exactly like a broken app path — a harness artefact I nearly reported as
    # a product fault.
    #
    # NUDGE is deliberately CONTENT-FREE: it answers nothing and adds no
    # requirement, so it cannot smuggle in the design under test. It only says
    # "your call, carry on" — enough to unblock a builder waiting on a
    # preference it has already offered a default for.
    NUDGE = ("Use your best judgement on anything still open, and go ahead — "
             "author the specs and publish.")
    # DONE means published, not "the spec files appeared". Waiting only for
    # app_spec.json + agent_spec.json stops the conversation the moment the
    # builder finishes AUTHORING — before it validates, runs static_checks.py,
    # publishes and smoke-tests. A run cut off there produces no
    # static_check_results.json, so any check meant to influence the build never
    # even executes, and the harness reports a clean finish. Cost me one full
    # run to notice.
    _DONE_MARKERS = ("static_check_results.json", "smoke_result.json",
                     "publish_payload.json")
    for extra in range(MAX_NUDGES):
        files = build_files(pod)
        if any(m in files for m in _DONE_MARKERS):
            break
        out(f"\n-- nudge {extra + 1}/{MAX_NUDGES} (specs not written yet) --")
        say(tok, session, NUDGE, pod)
    else:
        out("  !! never reached publish after nudges — read this transcript")
    out(f"\n  build dir: {sorted(build_files(pod)) or '(empty)'}")
    return session


def main() -> int:
    which = sys.argv[1:] or list(SURFACES)
    for name in which:
        if name not in SURFACES:
            out(f"unknown surface {name!r}; known: {list(SURFACES)}")
            return 2
    sessions = {}
    for name in which:
        try:
            sessions[name] = drive(name)
        except Exception as e:  # noqa: BLE001 — one surface failing must not stop the rest
            out(f"\n!! {name} FAILED to drive: {type(e).__name__}: {e}")
            sessions[name] = None
    out("\n\n=== sessions ===")
    for k, v in sessions.items():
        out(f"  {k:10} {v or 'FAILED'}")
    out("\ngrade with:  python scripts/grade_all_surfaces.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
