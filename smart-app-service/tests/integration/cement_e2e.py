# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

r"""End-to-end UI-middleware tester for the SmartApp builder + runtime.

Run this from a venv that has `httpx`, `pyjwt`, `python-dotenv`. The harness:

1. Mints a JWT from Citra-Service/.env's ``JWT_SECRET`` (HS256).
2. POSTs ``/build`` to smart-app-service (port 9100) with a fixed cement BA goal.
3. Subscribes to ``/build/{session_id}/chat/stream`` and logs every SSE event
   (message / thinking / tool_call / tool_result / plan / error).
4. Pattern-matches the agent's questions and fires scripted BA replies through
   the same chat/stream endpoint.
5. Periodically ``docker exec``'s into the builder pod and snapshots
   ``/workspace/build/*`` files so we have ground truth at each phase boundary.
6. Captures the ``publish`` event → slug → URL.
7. Walks the runtime (port 3100): renders each page, fetches panel data, fires
   an Approve action against a NEEDS_APPROVAL row.
8. Writes a structured Markdown report at
   ``smart-app-service/tests/integration/cement-e2e-report.md``.

Not a pytest test (would be too slow + token-hungry). Run it manually:

    cd c:/Github/Citra-AI/smart-app-service
    .\venv\Scripts\Activate.ps1
    python tests/integration/cement_e2e.py

Pass ``--dry-run`` to skip the actual /build call and just print what would
be sent.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

# Windows console defaults to cp1252 when stdout is redirected; emoji
# narrations crash with UnicodeEncodeError. Force UTF-8 with replace so
# the test always runs to completion regardless of console codepage.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import httpx
import jwt
from dotenv import dotenv_values


REPO_ROOT = Path(__file__).resolve().parents[3]
CITRA_SERVICE_ENV = REPO_ROOT / "Citra-Service" / ".env"
REPORT_PATH = Path(__file__).parent / "cement-e2e-report.md"

SMART_APP_URL = "http://localhost:9100"
RUNTIME_URL = "http://localhost:3100"

# Fully scripted BA replies. Keyed by a substring of the agent's question.
# The first matching pattern wins. Add more entries here as the test surface grows.
SCRIPTED_REPLIES: list[tuple[str, str]] = [
    # Phase 1 — "no MCPs registered" guidance (lets the test progress past
    # an empty discovery to validate Phases 2-4 with a placeholder source)
    ("no registered dept-mcps",
     "Understood — surface plant_ops_kiln_runs in requirements_unmet and proceed with a placeholder data source. Build the app skeleton so we can wire the real MCP later."),
    ("no data connections",
     "Understood — surface plant_ops_kiln_runs in requirements_unmet and proceed with a placeholder data source. Build the app skeleton so we can wire the real MCP later."),
    ("not registered",
     "Understood — surface plant_ops_kiln_runs in requirements_unmet and proceed with a placeholder data source. Build the app skeleton so we can wire the real MCP later."),
    ("placeholder",
     "Yes, proceed with a placeholder source. The MCP will be registered separately."),
    # Phase 1 clarifying questions
    ("3 clarifying questions",
     "Goal is to triage daily kiln operations. Users: plant manager, plant engineer. Need to spot low-TPD days and approve corrective actions."),
    # Phase 2 — sample input/decision table
    ("sample input",
     "Yes, those decisions look right. Approve."),
    ("decision table",
     "Looks good, ship it."),
    # Phase 3 — canonical Q11 script (10 questions)
    ("multi-page",                 "Multi-page, please."),
    ("which fits how your users",  "Multi-page."),
    ("landing page",               "Inbox of kiln runs as the landing page."),
    ("submission form would be",   "Separate page."),
    ("queue page",                 "Inbox shares the page with a TPD chart."),
    ("clicking a row",             "Separate page for the detail view."),
    ("chart of",                   "Inline next to the queue on the inbox page."),
    ("chat panel",                 "Global floating chat across every page."),
    ("workflow",                   "No workflow this time."),
    ("nav chrome",                 "Sidebar nav."),
    ("after submitting",           "Land on the new run's detail page."),
    ("After submitting",           "Land on the new run's detail page."),
    # Q11 approval. Match ONLY the distinctive option phrasing the agent
    # proposes in the Q11 question ("queue-and-resolve") — never the bare
    # word "approve"/"sign-off", which appears in almost every message
    # because the whole app is about approvals. A bare match fired this
    # reply on turns 2-5 when the agent was asking unrelated questions.
    ("queue-and-resolve",          "Queue-and-resolve in the app — no email notification, no SLA."),
    # Lock-in confirmation
    ("locked in",                  "Yes, locked in. Ship it."),
    ("Locked in",                  "Yes, locked in. Ship it."),
    ("looks good",                 "Yes, looks good — ship it."),
    ("ship it",                    "Yes — ship it."),
]

BA_GOAL = (
    "Build me a cement plant operations triage app. Show me every day's kiln "
    "runs from plant_ops_kiln_runs, flag days where TPD dropped below 1000 "
    "for my review, and let me approve corrective-action plans the plant "
    "engineer submits. Above 5000-tonne corrective actions need my sign-off."
)


@dataclass
class Event:
    """One parsed event from the SSE stream."""
    ts: float
    kind: str  # message | thinking | tool_call | tool_result | plan | error | other
    raw: dict[str, Any]

    def text(self) -> str:
        if self.kind == "message":
            return str(self.raw.get("text") or self.raw.get("content") or "")
        if self.kind == "thinking":
            return str(self.raw.get("text") or "")
        if self.kind == "tool_call":
            return f"{self.raw.get('name', '?')}({json.dumps(self.raw.get('args') or {})[:120]})"
        if self.kind == "tool_result":
            r = self.raw.get("result") or self.raw.get("output") or ""
            return str(r)[:200]
        if self.kind == "plan":
            items = self.raw.get("items") or []
            return f"{len(items)} todos: " + ", ".join(
                f"[{i.get('status','?')[:1]}]{i.get('content','')[:30]}" for i in items[:5]
            )
        if self.kind == "error":
            return str(self.raw.get("message") or self.raw.get("error") or "")
        return json.dumps(self.raw)[:200]


@dataclass
class Session:
    events: list[Event] = field(default_factory=list)
    session_id: Optional[str] = None
    pod_container_id: Optional[str] = None
    publish_slug: Optional[str] = None
    publish_url: Optional[str] = None
    workspace_snapshots: dict[str, dict[str, str]] = field(default_factory=dict)  # phase_label → {file: contents}
    runtime_findings: dict[str, Any] = field(default_factory=dict)

    def add(self, kind: str, raw: dict[str, Any]) -> None:
        ev = Event(ts=time.time(), kind=kind, raw=raw)
        self.events.append(ev)
        ts_str = datetime.now().strftime("%H:%M:%S")
        if kind == "thinking":
            print(f"[{ts_str}] 💭 {ev.text()[:140]}")
        elif kind == "message":
            print(f"[{ts_str}] 💬 {ev.text()[:200]}")
        elif kind == "tool_call":
            print(f"[{ts_str}] 🛠️  CALL  {ev.text()}")
        elif kind == "tool_result":
            print(f"[{ts_str}] 🛠️  RESULT {ev.text()[:120]}")
        elif kind == "plan":
            print(f"[{ts_str}] 📋 PLAN {ev.text()}")
        elif kind == "error":
            print(f"[{ts_str}] ❌ ERROR {ev.text()}")
        else:
            print(f"[{ts_str}] ? {kind}: {ev.text()[:80]}")


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def mint_jwt() -> str:
    """Mint a BA-style user JWT for the harness.

    Carries ``work_sa_id`` in the SAME deterministic form Citra-User-Service
    and smart-app-service's ``_work_sa_id`` use (``svc:work-<slug>@<org>``).
    The builder build session records this as the owner, /publish stamps the
    app onto it, and GET /apps (``scope=mine``) then surfaces it because the
    token's ``service_account_admin_of`` contains the same id.
    """
    env = dotenv_values(CITRA_SERVICE_ENV)
    secret = env.get("JWT_SECRET")
    if not secret:
        raise RuntimeError(f"JWT_SECRET not in {CITRA_SERVICE_ENV}")
    issuer = env.get("JWT_ISSUER", "Citra-AI")
    # Identify as the acme-cement org admin. org_id MUST be "acme-cement"
    # (the value the cement dept-MCP registers its tools under in
    # discovery) and roles MUST include "org_admin" — discovery's
    # _tool_visible_to grants an org_admin every tool whose org_ids
    # contains their org. With the old "acme-cement-demo" / "admin"
    # values discovery returned an empty catalogue.
    user_id = "demo.org.admin@acme-cement.citra.ai"
    tenant_id = "acme-cement"
    # Deterministic Work SA id — mirrors smart-app-service/_work_sa_id and
    # Citra-User-Service workSAService (svc:work-<slug>@<org>.citra.ai).
    _slug = "".join(
        c if (c.isalnum() or c == "-") else "-" for c in user_id.lower()
    ).strip("-")[:60]
    work_sa_id = f"svc:work-{_slug or 'anonymous'}@{tenant_id}.citra.ai"
    payload = {
        "user_id": user_id,
        "email":   user_id,
        "name":    "Anita Rao (Org Admin)",
        "org_id":  "acme-cement",
        "tenant_id": tenant_id,
        "dept_ids": ["plant_ops", "quality", "sales_dispatch"],
        "roles":   ["org_admin"],
        # Mirror what Citra-User-Service would stamp on a real login JWT:
        "work_sa_id": work_sa_id,
        "service_account_admin_of": [work_sa_id],
        "iss":     issuer,
        "iat":     datetime.now(timezone.utc),
        "exp":     datetime.now(timezone.utc) + timedelta(hours=2),
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def auth_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "X-Team-ID": "acme-cement-demo",
    }


# ---------------------------------------------------------------------------
# BA replier — chooses a scripted answer for an agent question
# ---------------------------------------------------------------------------

IDLE_MARKERS = ("no_reply", "no reply", "nothing to continue", "no pending task",
                "fresh session", "what would you like to work on")

# Phase-progression markers — when the agent emits any of these, send a
# permissive "continue" so it advances to the next phase. These are
# present in the agent's narration even when no '?' is asked.
PROGRESS_MARKERS = (
    "phase 1 complete", "phase 2 complete", "phase 3 complete",
    "phase 1 discovery complete", "phase 1 internship complete",
    "moving on to phase", "moving to phase",
    "ready to start phase", "ready for phase",
    "agent design complete", "agent spec complete",
    "ui design complete", "design is frozen", "design frozen",
    "app spec complete", "appspec complete",
    "publishing your app", "publish complete",
)


def pick_reply(agent_text: str) -> Optional[str]:
    """Return a scripted BA reply if the agent's message looks like a question
    OR signals phase progression.

    Returns None to STOP the loop when:
      - The agent went idle (NO_REPLY / "what would you like to work on?").
      - There's no recognised pattern AND no '?' AND no progress marker.
    """
    lower = agent_text.lower()
    if any(m in lower for m in IDLE_MARKERS):
        # Agent is idle — bail.
        return None
    # Try every scripted pattern, with or without '?' (some agent prompts
    # end with imperatives like "Confirm if this is right.")
    for pattern, reply in SCRIPTED_REPLIES:
        if pattern.lower() in lower:
            return reply
    # Phase-progression — if the agent narrated "Phase X complete",
    # nudge it to proceed to the next phase.
    if any(m in lower for m in PROGRESS_MARKERS):
        return "Sounds good — please continue with the next phase."
    # Must contain a '?' to be a question we can default-answer.
    if "?" not in agent_text:
        return None
    # Fall-through: question we don't have a script for. Permissive default.
    return "Yes, that works for me. Please continue."


# ---------------------------------------------------------------------------
# SSE parser
# ---------------------------------------------------------------------------

async def consume_sse(
    session: Session,
    response: httpx.Response,
    on_message_text: callable,
) -> None:
    """Iterate the SSE response body, classify each event, and notify on
    each agent text message so the harness can decide to reply.
    """
    current_event = "message"  # default per SSE spec
    async for line in response.aiter_lines():
        if not line:
            current_event = "message"
            continue
        if line.startswith(":"):
            continue  # comment / keepalive
        if line.startswith("event:"):
            current_event = line[6:].strip()
            continue
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        try:
            payload = json.loads(data)
        except json.JSONDecodeError:
            payload = {"text": data}
        # Classify by the 'type' field the adapter projects
        ptype = (payload.get("type") or current_event or "").lower()
        if ptype in ("thinking",):
            session.add("thinking", payload)
        elif ptype in ("tool_call",):
            session.add("tool_call", payload)
        elif ptype in ("tool_result",):
            session.add("tool_result", payload)
        elif ptype in ("plan",):
            session.add("plan", payload)
        elif ptype in ("error",):
            session.add("error", payload)
        elif ptype in ("session.complete", "done", "complete"):
            session.add("done", payload)
            return
        else:
            session.add("message", payload)
            text = str(payload.get("text") or payload.get("content") or "")
            if text:
                await on_message_text(text)


# ---------------------------------------------------------------------------
# Build session orchestration
# ---------------------------------------------------------------------------

async def start_build(client: httpx.AsyncClient, token: str) -> dict[str, Any]:
    print(f"\n=== POST /build with cement goal ===")
    r = await client.post(
        f"{SMART_APP_URL}/build",
        headers=auth_headers(token),
        json={"goal": BA_GOAL, "build_kinds": ["app"]},
        timeout=30.0,
    )
    r.raise_for_status()
    body = r.json()
    print(f"session_id = {body.get('session_id')}")
    print(f"pod_id     = {body.get('pod_id')}")
    return body


def send_ba_message(client: httpx.AsyncClient, token: str, session_id: str, message: str):
    """Return an async context manager for a streaming POST to /chat/stream.

    Use as ``async with send_ba_message(...) as resp:`` — httpx's
    ``client.stream()`` is itself a context manager (not a coroutine), so
    we do NOT ``await`` it before the ``async with``.
    """
    return client.stream(
        "POST",
        f"{SMART_APP_URL}/build/{session_id}/chat/stream",
        headers={**auth_headers(token), "Accept": "text/event-stream"},
        json={"message": message},
        timeout=httpx.Timeout(connect=10.0, read=600.0, write=10.0, pool=10.0),
    )


def snapshot_workspace(session: Session, label: str) -> None:
    """docker exec into the builder pod and read /workspace/build/*.

    Best-effort — if the pod is gone or files missing, just log and continue.
    """
    if not session.pod_container_id:
        # Find the most-recent container started from citra-app-builder
        try:
            out = subprocess.run(
                ["docker", "ps", "--filter", "ancestor=citra-app-builder:latest",
                 "--format", "{{.ID}}", "--latest"],
                capture_output=True, text=True, timeout=10,
            )
            cid = out.stdout.strip()
            if cid:
                session.pod_container_id = cid
        except Exception as e:
            print(f"[snapshot] docker ps failed: {e}")
            return
    cid = session.pod_container_id
    if not cid:
        return
    files = ["domain.md", "discovery.json", "agent_spec.json", "ui_design.md",
            "app_spec.json", "tests.json", "test-results.json"]
    snap = {}
    for f in files:
        try:
            out = subprocess.run(
                ["docker", "exec", cid, "cat", f"/workspace/build/{f}"],
                capture_output=True, text=True, timeout=10,
            )
            if out.returncode == 0 and out.stdout:
                snap[f] = out.stdout
        except Exception:
            pass
    if snap:
        session.workspace_snapshots[label] = snap
        print(f"[snapshot:{label}] captured {len(snap)} files: {list(snap.keys())}")


# ---------------------------------------------------------------------------
# Runtime click-through
# ---------------------------------------------------------------------------

async def walk_runtime(client: httpx.AsyncClient, token: str, slug: str, findings: dict[str, Any]) -> None:
    print(f"\n=== Runtime walk for slug={slug} ===")
    # 1. Landing page
    r = await client.get(f"{RUNTIME_URL}/{slug}", headers=auth_headers(token), timeout=15.0)
    findings["landing"] = {"status": r.status_code, "len": len(r.text),
                           "has_app_title": "app-title" in r.text}
    print(f"GET /{slug}  HTTP {r.status_code}  ({len(r.text)} bytes)")
    # 2. Fetch the AppSpec from the smart-app-service so we know the pages + panel ids
    r = await client.get(f"{SMART_APP_URL}/apps/{slug}", headers=auth_headers(token), timeout=10.0)
    if r.status_code != 200:
        findings["spec_fetch"] = {"status": r.status_code, "error": r.text[:200]}
        return
    detail = r.json()
    app_spec = detail.get("app_spec") or {}
    pages = app_spec.get("pages") or []
    panels = app_spec.get("panels") or []
    findings["app_spec_summary"] = {
        "kind": app_spec.get("kind"),
        "pages_count": len(pages),
        "panels_count_top_level": len(panels),
        "data_sources": [d.get("id") for d in (app_spec.get("data_sources") or [])],
        "navigation": app_spec.get("navigation"),
    }
    print(f"AppSpec: kind={app_spec.get('kind')}, "
          f"{len(pages)} page(s) + {len(panels)} top-level panel(s)")
    # 3. Walk each page route
    routes = []
    if pages:
        for p in pages:
            routes.append(p.get("id"))
    else:
        routes = ["(main)"]
    findings["routes_tested"] = []
    for route in routes:
        url = f"{RUNTIME_URL}/{slug}" if route == "(main)" else f"{RUNTIME_URL}/{slug}/{route}"
        r = await client.get(url, headers=auth_headers(token), timeout=15.0)
        findings["routes_tested"].append({"route": route, "status": r.status_code, "bytes": len(r.text)})
        print(f"GET {url}  HTTP {r.status_code}")
    # 4. Walk every panel's data endpoint
    all_panels = panels.copy()
    for p in pages:
        all_panels.extend(p.get("panels") or [])
    findings["panel_data"] = []
    for panel in all_panels:
        pid = panel.get("id")
        if not pid:
            continue
        if panel.get("type") not in ("queue", "dashboard", "chart"):
            continue
        r = await client.get(
            f"{RUNTIME_URL}/api/data/{slug}/{pid}",
            headers=auth_headers(token), timeout=15.0,
        )
        findings["panel_data"].append({
            "panel_id": pid,
            "panel_type": panel.get("type"),
            "status": r.status_code,
            "rows": (r.json().get("rows") if r.status_code == 200 else None) and len(r.json()["rows"]) or 0,
        })
        print(f"GET /api/data/{slug}/{pid}  HTTP {r.status_code}")


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def write_report(session: Session) -> None:
    REPORT_PATH.write_text(_render_report(session), encoding="utf-8")
    print(f"\n=== Report written to {REPORT_PATH} ===")


def _render_report(s: Session) -> str:
    by_kind: dict[str, int] = {}
    for ev in s.events:
        by_kind[ev.kind] = by_kind.get(ev.kind, 0) + 1
    lines = [
        "# Cement E2E test report",
        f"_Generated {datetime.now().isoformat()}_",
        "",
        f"- session_id: `{s.session_id}`",
        f"- publish slug: `{s.publish_slug}`",
        f"- publish url: {s.publish_url}",
        f"- total events: {len(s.events)}",
        "",
        "## Events by kind",
        "",
        "| kind | count |",
        "| --- | --- |",
    ]
    for k, c in sorted(by_kind.items(), key=lambda kv: -kv[1]):
        lines.append(f"| {k} | {c} |")
    lines.extend(["", "## Workspace snapshots (per phase)", ""])
    for label, snap in s.workspace_snapshots.items():
        lines.append(f"### {label}")
        for fname, body in snap.items():
            lines.append(f"#### `{fname}` ({len(body)} bytes)")
            lines.append("```")
            lines.append(body[:2000])
            lines.append("```")
        lines.append("")
    lines.extend(["## Runtime findings", "", "```json",
                  json.dumps(s.runtime_findings, indent=2)[:5000], "```", ""])
    lines.extend(["## Last 80 thinking emissions", ""])
    for ev in [e for e in s.events if e.kind == "thinking"][-80:]:
        lines.append(f"- {ev.text()[:200]}")
    lines.extend(["", "## Last 50 tool calls", ""])
    for ev in [e for e in s.events if e.kind == "tool_call"][-50:]:
        lines.append(f"- `{ev.text()[:200]}`")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main(args: argparse.Namespace) -> None:
    session = Session()
    token = mint_jwt()
    print(f"JWT minted (length={len(token)})")
    if args.dry_run:
        print("DRY RUN — exiting before /build call")
        return

    async with httpx.AsyncClient() as client:
        # Smoke-test the JWT first
        r = await client.get(f"{SMART_APP_URL}/apps", headers=auth_headers(token), timeout=10.0)
        print(f"smoke-test GET /apps  HTTP {r.status_code}")
        if r.status_code not in (200, 304):
            print(f"  body: {r.text[:300]}")

        build_resp = await start_build(client, token)
        session.session_id = build_resp.get("session_id")
        if not session.session_id:
            print("ERROR: no session_id returned")
            print(json.dumps(build_resp, indent=2))
            return

        # Give the pod a moment to spawn before snapshotting / streaming
        await asyncio.sleep(3)
        snapshot_workspace(session, "after_spawn")

        # Drive the agent — send the BA goal verbatim as the first chat
        # message. The pod's BUILD_GOAL env var is only used to seed files in
        # /workspace; the agent's first turn requires the user to actually
        # state the goal in chat (it doesn't auto-read BUILD_GOAL).

        next_message = BA_GOAL
        turn_no = 0
        max_turns = 30  # safety cap
        last_msg_text = ""
        # Match an URL on its own OR wrapped in markdown bold (**…**) so we
        # capture both `live at https://…` and `live at **https://…**`.
        url_pattern = re.compile(
            r"live at\s+\*{0,2}(?P<url>https?://[^\s)*]+)\*{0,2}",
            re.I,
        )
        # Slug = last `/[a-z0-9-]+` segment in the URL.
        slug_pattern = re.compile(r"/(?P<slug>[a-z0-9-]+)/?$", re.I)

        async def on_message_text(text: str) -> None:
            nonlocal last_msg_text
            last_msg_text = text
            m = url_pattern.search(text)
            if m and not session.publish_url:
                session.publish_url = m.group("url").rstrip("./*")
                slug_match = slug_pattern.search(session.publish_url)
                if slug_match:
                    session.publish_slug = slug_match.group("slug")
                    print(f"!!! publish detected: slug={session.publish_slug} url={session.publish_url}")

        while turn_no < max_turns:
            turn_no += 1
            print(f"\n--- BA turn {turn_no}: '{next_message[:80]}' ---")
            async with send_ba_message(client, token, session.session_id, next_message) as resp:
                if resp.status_code != 200:
                    body = await resp.aread()
                    print(f"ERROR: /chat/stream returned {resp.status_code}: {body[:200]}")
                    break
                last_msg_text = ""
                await consume_sse(session, resp, on_message_text)

            # Snapshot after each turn so we can inspect what the agent wrote
            snapshot_workspace(session, f"after_turn_{turn_no}")

            # Did the agent end with a question we need to answer?
            if session.publish_slug:
                print("publish detected — leaving the build loop")
                break
            reply = pick_reply(last_msg_text)
            if not reply:
                # No actionable reply — bail rather than spin.
                print(f"no scripted reply for last agent message; ending loop after {turn_no} turns")
                print(f"  last agent msg: {last_msg_text[:200]!r}")
                break
            next_message = reply

        # Walk the runtime if we have a slug
        if session.publish_slug:
            await asyncio.sleep(2)
            await walk_runtime(client, token, session.publish_slug, session.runtime_findings)

    write_report(session)


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="Print the JWT + skip the /build call")
    return ap.parse_args()


if __name__ == "__main__":
    asyncio.run(main(parse_args()))
