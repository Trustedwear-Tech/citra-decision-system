# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""L2 — Auto-process BUILDER matrix. Drive ONE focused prompt per scenario, then
inspect the GENERATED app_spec (triggers/policy) OR the builder's clarifying
question. LLM + builder-pod dependent. See docs/auto-process-test-plan.md.
Usage: python scripts/autoproc_builder_matrix.py <B1|B2|B3|B4|B5|B6|B7|B8>"""
import os, sys, time, json, re, asyncio, jwt, httpx
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))
SMART = "http://localhost:9100"
SEC = os.getenv("JWT_SECRET"); ISS = os.getenv("JWT_ISSUER", "Citra-AI")
WSA = "svc:work-gm-it-bsphcl-power-demo-citra-ai@bsphcl.citra.ai"
PSA = "svc:personal-gm-it-bsphcl-power-demo-citra-ai@bsphcl.citra.ai"

SCEN = {
 "B0": ("Build an app to help handle field_operations cases — the AI should help decide what to do with each one. One page.",
        "menu"),   # MODE NOT stated → builder must PRESENT the 3-mode menu, not silently default/build
 "B1": ("review each inspection in field_operations and RECOMMEND approve/reject for the next stage. Keep it one page. Publish to test.",
        "recommend"),
 "B2": ("Build an app over field_operations: when a case amount is UNDER 10000, AUTO-APPROVE it; otherwise send it for review. One page. Publish to test.",
        "auto_threshold"),
 "B3": ("Build an app that AUTOMATICALLY ROUTES each incoming request to one of customer_service, it, support, or technical. One page. Publish to test.",
        "auto_set"),
 "B4": ("Build an app that AUTO-PROCESSES all the field_operations cases. (give no criteria)",
        "ask"),
 "B6": ("Screen field_operations cases using their notes and fields, and AUTO-PROCESS the clear-cut ones. One page.",
        "ask"),
 "B7": ("Let me screen a batch of field_operations cases ON A BUTTON CLICK. One page. Publish to test.",
        "on_demand"),
 "B8": ("Build an app that AUTO-APPROVES any case, ANY amount, no limit. One page. Publish to test.",
        "ask_or_cap"),
}

def tok():
    now = int(time.time())
    return jwt.encode({"user_id": "gm-it@bsphcl-power-demo.citra.ai", "email": "gm-it@bsphcl-power-demo.citra.ai",
        "org_id": "bsphcl", "dept_ids": ["central_pmu"], "roles": ["user", "org_admin"], "work_sa_id": WSA,
        "service_account_admin_of": [PSA, WSA], "service_account_member_of": [],
        "iat": now, "exp": now + 14400, "iss": ISS, "sub": "gm-it@bsphcl-power-demo.citra.ai"}, SEC, algorithm="HS256")

SID_GOAL = sys.argv[1] if len(sys.argv) > 1 else "B2"
GOAL, KIND = SCEN[SID_GOAL]
# For BUILD scenarios we push it to finish with sensible defaults (we can't answer
# arbitrary grounding questions in an automated test). ASK/MENU scenarios stop on
# turn 1 — the clarifying question IS the result, so CONT is never reached for them.
CONT = "Use sensible defaults for any unspecified detail (pick the obvious dataset/field/action), do NOT ask further questions, and PUBLISH to test now (http://localhost:3100/<slug>)."

async def turn(c, H, sid, msg):
    slug, texts = None, []
    try:
        async with c.stream("POST", f"{SMART}/build/{sid}/chat/stream", headers={**H, "Accept": "text/event-stream"}, json={"message": msg}) as r:
            if r.status_code != 200:
                return None, [f"HTTP {r.status_code}"]
            async for line in r.aiter_lines():
                if not line.startswith("data:"): continue
                raw = line[5:].strip()
                if not raw: continue
                try: ev = json.loads(raw)
                except Exception: continue
                if (ev.get("type") or "") == "done": break
                t = ev.get("text") or ev.get("content") or ""
                if t:
                    texts.append(t)
                    m = re.search(r"3100/([a-z0-9][a-z0-9-]*)", t)
                    if m: slug = m.group(1)
    except Exception as e:  # SSE drop on a resource-tight box — keep the partial capture
        texts.append(f"[stream-error: {type(e).__name__}]")
    return slug, texts

async def run():
    H = {"Authorization": f"Bearer {tok()}"}
    async with httpx.AsyncClient(timeout=150) as c:   # cold pod spawn can exceed 30s
        r = await c.post(f"{SMART}/build", headers=H, json={"goal": GOAL, "build_kind": "app"})
        if r.status_code >= 400: print(f"[{SID_GOAL}] POST /build {r.status_code}: {r.text[:200]}"); return
        sid = r.json()["session_id"]
    print(f"[{SID_GOAL}] kind={KIND} session={sid}", flush=True)
    NEUTRAL = "I'd like to build this. Go ahead and ask me whatever you need to decide HOW the AI should handle each case — I'll answer."
    def clar(text):
        t = text.lower()
        # mode words the builder may use (loosened: includes auto/automatic/on-demand)
        mode_words = ["on-demand", "on demand", "auto-recommend", "auto recommend",
                      "auto-process", "auto process", "automatically", "automatic", "auto-"]
        mh = sum(1 for m in mode_words if m in t)
        # a CHOICE framing (so a plain recommend build that merely says "recommend"
        # doesn't false-positive — we want the builder OFFERING a choice + asking)
        choice = any(p in t for p in ["would you like", "do you want", "you can either",
                                      "prefer", "which mode", "pick one", "choose", "option",
                                      "or should", "how should the ai handle", "or do you want"])
        explicit = any(p in t for p in ["how should the ai handle", "pick one", "three options",
                                        "three ways", "three modes"])
        on_demand = ("on-demand" in t or "on demand" in t)
        menu = explicit or (mh >= 1 and choice) or (on_demand and mh >= 2)
        ask = any(p in t for p in ["what amount", "what limit", "what threshold", "value cap", "no limit",
                                   "auto-process all", "what criteria", "which criteria", "bounding criteria",
                                   "a limit it should", "what's the limit", "what bound", "under what"])
        return menu, ask
    is_clarify = KIND in ("ask", "ask_or_cap", "menu")
    slug, all_text = None, []
    async with httpx.AsyncClient(timeout=httpx.Timeout(connect=10, read=2700, write=10, pool=10)) as c:
        maxturns = 3 if is_clarify else 5
        for i in range(maxturns):
            msg = GOAL if i == 0 else (NEUTRAL if is_clarify else CONT)
            slug, texts = await turn(c, H, sid, msg)
            all_text += texts
            mnow, anow = clar("\n".join(all_text))
            print(f"[{SID_GOAL}] turn{i+1} slug={slug} menu={mnow} ask={anow} tail={' | '.join(t[:60] for t in texts[-2:])}", flush=True)
            if slug: break
            if is_clarify and (mnow or anow): break   # captured the clarifying question — stop, don't push to build
    # Drop-proof: also read the PERSISTED transcript — assistant output now survives
    # SSE drops (relay fix), so the clarifying message is captured even if the live
    # stream dropped mid-turn.
    try:
        async with httpx.AsyncClient(timeout=30) as c:
            sr = await c.get(f"{SMART}/build/{sid}", headers=H)
            if sr.status_code == 200:
                for e in (sr.json().get("transcript") or []):
                    if e.get("role") == "assistant":
                        all_text.append(e.get("raw") or e.get("text") or "")
        print(f"[{SID_GOAL}] transcript merged → {len(all_text)} segments", flush=True)
    except Exception as _e:  # noqa: BLE001
        print(f"[{SID_GOAL}] transcript fetch skipped: {_e}", flush=True)
    chat = "\n".join(all_text).lower()
    spec = None
    if slug:
        async with httpx.AsyncClient(timeout=30) as c:
            rr = await c.get(f"{SMART}/apps/{slug}", headers=H)
            if rr.status_code == 200: spec = (rr.json() or {}).get("app_spec") or {}
    trigs = (spec or {}).get("triggers") or []
    def pol(t): return t.get("auto_process_policy") or {}
    ap_trigs = [t for t in trigs if t.get("execution_mode") == "auto_process"]
    # CLARIFYING signals (same tightened detector as the turn loop)
    presents_menu, asks_bound = clar(chat)
    clarified = presents_menu or asks_bound
    print(f"[{SID_GOAL}] triggers={[(t.get('execution_mode'), bool(t.get('auto_process_policy'))) for t in trigs]} "
          f"| menu={presents_menu} asks_bound={asks_bound}", flush=True)
    if ap_trigs: print(f"[{SID_GOAL}] policy={json.dumps([pol(t).get('auto_commit_when') for t in ap_trigs], default=str)[:240]}", flush=True)
    # verdict per scenario kind
    if KIND == "recommend":
        ok = bool(slug) and not ap_trigs
    elif KIND == "auto_threshold":
        ok = bool(ap_trigs) and ("amount" in json.dumps(ap_trigs, default=str).lower())
    elif KIND == "auto_set":
        ok = bool(ap_trigs) and ('"in"' in json.dumps(ap_trigs, default=str) or "'in'" in json.dumps(ap_trigs, default=str))
    elif KIND == "on_demand":
        ok = not ap_trigs  # no auto trigger
    elif KIND == "menu":
        # mode NOT stated → must PRESENT the menu (clarify), NOT silently build/auto-process
        ok = presents_menu and not ap_trigs
    elif KIND in ("ask", "ask_or_cap"):
        # auto-process intent but no bound → must clarify (ask bound / present menu), NOT silently auto-process-all
        ok = clarified and not ap_trigs
    else:
        ok = False
    print(f"[{SID_GOAL}] clarified={clarified}  VERDICT: {'PASS' if ok else 'CHECK'}", flush=True)

asyncio.run(run())
