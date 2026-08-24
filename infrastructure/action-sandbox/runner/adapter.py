# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""Citra Control Adapter — thin WebChat channel into OpenClaw.

We are a channel adapter, exactly like the Telegram, WhatsApp, Slack and
WebChat adapters that ship with OpenClaw. Our job:

1. Authenticate the inbound HTTP request from action-chat-service.
2. Forward the user's message verbatim to OpenClaw via the gateway's
   native ``chat.send`` RPC over WebSocket.
3. Subscribe to the gateway's ``session.message`` / ``session.tool`` /
   ``sessions.changed`` events for the session and stream them back as
   SSE to action-chat-service (which forwards to the UI).
4. Forward ``/task/steer`` as another ``chat.send`` — OpenClaw's
   ``messages.queue.mode = "steer"`` config (set in openclaw.json) makes
   it inject between tool boundaries with no cancellation. WE DO NOT
   DECIDE.
5. Forward ``/task/cancel`` as ``chat.abort``.

This replaces the previous HTTP ``/v1/responses`` + raw-stream JSONL +
outbox-tail design. That design used the OpenAI-compatibility shim and
required us to act as a controller (cancel on steer, synthesise events,
filter logs). The native gateway protocol exposes the full chat surface
OpenClaw uses for every other channel, with steering and queue
behaviour controlled by config — not by us.

Outbound event mapping (gateway event -> Citra SSE event):
    session.message  -> message  (chat-display projected message body;
                                  text content, attachments, role)
    session.tool     -> tool_call | tool_result   (per data.phase)
    sessions.changed -> status (transient lifecycle markers; suppressed
                                if redundant with message stream)

Cancellation / errors / completion:
    The adapter relies on OpenClaw to emit a final ``session.message``
    with role=assistant and the closed transcript when a turn ends.
    Explicit ``chat.abort`` results in OpenClaw broadcasting a
    sessions.changed event and the run terminating server-side. We
    surface ``done`` when the user disconnects or the subscription
    iterator ends.

References (sparse-checked openclaw repo, May 2026):
  - src/gateway/protocol/schema/logs-chat.ts  (chat.send / chat.abort)
  - src/gateway/protocol/schema/sessions.ts   (sessions.* schemas)
  - src/gateway/server-session-events.ts      (session.message payload)
  - src/gateway/server-broadcast.ts           (event scopes)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import subprocess
import time
import uuid
from typing import Any, AsyncIterator

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
import uvicorn

from .config import SandboxConfig
from .gateway_client import GatewayError, OpenClawGatewayClient
from .outbox import OutboxWatcher

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
)
logger = logging.getLogger("action-sandbox")


# ---- session key derivation ----------------------------------------------
# OpenClaw's chat.send uses ``sessionKey`` to route the message. The same
# key reused across calls lands in the same session (same memory, same
# in-flight run, eligible for steering). We derive a stable key from the
# user's session_id + the agent we're targeting + the channel name so
# the gateway's per-channel queue config (messages.queue.byChannel.webchat
# = "steer") applies. Bounded to 512 chars per ChatSendSessionKeyString.
#
# Multi-tab support: when ``channel`` differs across calls, OpenClaw sees
# them as distinct conversation threads — independent chat history,
# independent run state, independent steer queue — even though they
# share THIS sandbox container (Python kernel, DuckDB session, scratch
# workspace, MEMORY.md). This is the model that lets a user open the
# same Action Chat in 5 browser tabs without 5 containers spawning:
# each tab passes its own channel_id, the adapter routes per-tab, and
# the OpenClaw gateway keeps the conversations cleanly separated.
def _session_key_for(session_id: str, agent: str = "main", channel: str = "webchat") -> str:
    safe_sid = re.sub(r"[^A-Za-z0-9._\-]", "_", session_id)[:200]
    safe_channel = re.sub(r"[^A-Za-z0-9._\-]", "_", channel or "webchat")[:64] or "webchat"
    return f"agent:{agent}:{safe_channel}:{safe_sid}"


# OpenClaw's queue config keys per-channel rules on the FIRST segment of
# the channel string (everything before the first dot or dash). Our
# per-tab channel ids look like ``webchat-<32hex>`` so the existing
# messages.queue.byChannel.webchat="steer" config applies to all tabs.
# When the caller doesn't supply a channel_id we keep the legacy
# "webchat" string verbatim so /task calls from older Citra-Service
# replicas keep working unchanged.
def _channel_for_request(channel_id: str | None) -> str:
    if not channel_id or not str(channel_id).strip():
        return "webchat"
    cid = str(channel_id).strip()
    # Always namespaced under "webchat" so OpenClaw's per-channel queue
    # config in openclaw.json keeps applying.
    return f"webchat-{cid}"


# ---- gateway-event → Citra-SSE projection ---------------------------------
def _project_session_message(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Map ``session.message`` payload -> 0..N Citra UI events.

    The gateway projects message bodies through ``projectChatDisplayMessage``
    so we receive the redacted display form (truncated, secrets removed).
    Shape is the standard chat message: ``{role, content[], ...}`` where
    ``content`` is an array of blocks (text / tool_use / tool_result /
    image / etc).
    """
    msg = payload.get("message")
    if not isinstance(msg, dict):
        return []
    role = msg.get("role") or ""
    content = msg.get("content")
    out: list[dict[str, Any]] = []

    # Some channels deliver the assistant response as a flat string body.
    if isinstance(content, str):
        if role == "assistant" and content.strip():
            out.append({"type": "message", "text": content})
        return out

    if not isinstance(content, list):
        return out

    for block in content:
        if not isinstance(block, dict):
            # Non-dict block — not model reasoning. The reasoning tab is
            # for the model's chain-of-thought only, so drop it rather
            # than dumping raw JSON there.
            continue
        btype = block.get("type") or ""
        if btype in ("text", "output_text"):
            text = block.get("text") or ""
            if isinstance(text, str) and text and role == "assistant":
                out.append({"type": "message", "text": text})
            # Non-assistant text (user echo / system prompt) is NOT
            # LLM output — intentionally not forwarded.
        elif btype in ("tool_use", "tool_call"):
            tname = (block.get("name") or "").strip()
            targs = block.get("input") if "input" in block else block.get("args")
            out.append({
                "type": "tool_call",
                "name": tname,
                "args": targs,
                "call_id": block.get("id") or block.get("tool_use_id") or "",
            })
            # Native TodoWrite recognised by OpenClaw — also emit a
            # typed plan event so the UI can render the task list in
            # its dedicated panel.
            if tname in ("TodoWrite", "todo_write", "TodoList", "todos"):
                todos = targs.get("todos") if isinstance(targs, dict) else None
                if isinstance(todos, list):
                    out.append({
                        "type": "plan",
                        "items": [
                            {
                                "content": (t or {}).get("content") or "",
                                "activeForm": (t or {}).get("activeForm") or "",
                                "status": (t or {}).get("status") or "pending",
                            }
                            for t in todos
                            if isinstance(t, dict)
                        ],
                    })
            if tname == "canvas":
                a = targs if isinstance(targs, dict) else {}
                out.append({
                    "type": "canvas_event",
                    "action": a.get("action") or "",
                    "surface_id": a.get("surfaceId") or a.get("surface_id"),
                    "args": a,
                })
        elif btype in ("tool_result", "tool_use_result"):
            tcontent = block.get("content")
            output: Any
            if isinstance(tcontent, list):
                # Block list — extract any text bodies.
                parts: list[str] = []
                for sub in tcontent:
                    if isinstance(sub, dict):
                        t = sub.get("text")
                        if isinstance(t, str):
                            parts.append(t)
                output = "\n".join(parts) if parts else tcontent
            else:
                output = tcontent
            out.append({
                "type": "tool_result",
                "name": block.get("name") or "",
                "ok": not bool(block.get("is_error")),
                "call_id": block.get("tool_use_id") or block.get("id") or "",
                "output": output,
            })
        elif btype == "thinking":
            # Reasoning surfaced verbatim — let the UI render or hide it.
            text = block.get("text") or block.get("thinking") or ""
            if isinstance(text, str) and text:
                out.append({"type": "thinking", "text": text})
        # Unknown block types (image, audio, video, code, future schema
        # additions) are intentionally not forwarded — dumping their raw
        # JSON into the reasoning tab is noise, not the model's reasoning.
        # tool_use / tool_result blocks above already surface tool activity
        # to the UI's inline tool-step view.
    return out


def _project_session_tool(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Map ``session.tool`` payload -> tool_call / tool_result events.

    Special tools — ``TodoWrite`` and ``canvas`` — get an additional
    higher-level event so the UI can render them in dedicated panels
    instead of as opaque tool entries:

      - ``TodoWrite`` (Claude-Code-style task tracker recognised
        natively by OpenClaw, see src/agents/anthropic-transport-stream.ts
        CLAUDE_CODE_TOOLS) -> ``{"type":"plan","items":[...]}``
      - ``canvas`` (the bundled A2UI tool, see extensions/canvas/) ->
        ``{"type":"canvas_event","action":...,"surfaceId":...}``
    """
    data = payload.get("data")
    if not isinstance(data, dict):
        return []
    phase = (data.get("phase") or "").lower()
    name = (data.get("name") or "").strip()
    call_id = (
        data.get("toolCallId")
        or data.get("tool_use_id")
        or data.get("id")
        or ""
    )
    args = data.get("args") or data.get("input") or {}
    if phase in ("start", "begin", "started"):
        events: list[dict[str, Any]] = [{
            "type": "tool_call",
            "name": name,
            "args": args,
            "call_id": call_id,
        }]
        # Native TodoWrite — surface as a structured plan event the UI
        # can render in a side panel. Args shape matches the Claude
        # Code TodoWrite tool: {todos: [{content, status, activeForm}]}.
        if name in ("TodoWrite", "todo_write", "TodoList", "todos"):
            todos = args.get("todos") if isinstance(args, dict) else None
            if isinstance(todos, list):
                events.append({
                    "type": "plan",
                    "items": [
                        {
                            "content": (t or {}).get("content") or "",
                            "activeForm": (t or {}).get("activeForm") or "",
                            "status": (t or {}).get("status") or "pending",
                        }
                        for t in todos
                        if isinstance(t, dict)
                    ],
                })
        # Native canvas / A2UI — surface as a typed event so the UI
        # can show a "Canvas surface updated" indicator without
        # knowing the per-action arg shape.
        if name == "canvas":
            action = (args.get("action") if isinstance(args, dict) else None) or ""
            events.append({
                "type": "canvas_event",
                "action": action,
                "surface_id": (args.get("surfaceId") or args.get("surface_id"))
                              if isinstance(args, dict) else None,
                "args": args if isinstance(args, dict) else None,
            })
        return events
    if phase in ("end", "done", "finish", "completed", "error"):
        return [{
            "type": "tool_result",
            "name": name,
            "ok": phase != "error" and not data.get("error"),
            "call_id": call_id,
            "output": data.get("output") or data.get("result"),
        }]
    # Unknown phase is a gateway-lifecycle marker, not LLM output —
    # intentionally not forwarded.
    return []


def _build_app(cfg: SandboxConfig) -> FastAPI:
    app = FastAPI(title="Citra Action Sandbox", version="0.4.0")

    # The gateway client is created at app construction but actually
    # connects in the lifespan startup hook. Every request awaits
    # ``client.connect()`` (idempotent if already up).
    gateway = OpenClawGatewayClient()
    activity: dict[str, float] = {
        "last_task_started": 0.0,
        "last_task_finished": 0.0,
    }
    # Track in-flight runs by sessionKey so /task/cancel can target the
    # right runId. Updated when chat.send returns and cleared when a
    # final/aborted/error event is observed.
    active_runs: dict[str, str] = {}
    # Per-sessionKey count of open /task SSE streams. Used by /status to
    # report whether anything is in flight.
    open_streams: dict[str, int] = {}

    @app.on_event("startup")
    async def _startup() -> None:
        # R2 fix: refuse to start if the gateway is configured for
        # remote ingress (bind != "loopback") AND auth.mode == "none".
        # That combo would expose unauthenticated full-scope WebSocket
        # access to anything that can reach the bind interface. We
        # picked auth-none because we're loopback-only inside an
        # isolated container; if someone ever flips bind without
        # restoring auth, the adapter loudly refuses to start instead
        # of silently exposing OpenClaw to the network.
        try:
            cfg_path = os.path.expandvars(
                os.getenv(
                    "OPENCLAW_CONFIG_PATH",
                    "${OPENCLAW_HOME}/.openclaw/openclaw.json",
                )
            )
            with open(cfg_path, "r", encoding="utf-8") as f:
                claw_cfg = json.load(f)
            gw = (claw_cfg or {}).get("gateway") or {}
            bind = (gw.get("bind") or "loopback").lower()
            mode = ((gw.get("auth") or {}).get("mode") or "token").lower()
            if mode == "none" and bind not in ("loopback", "127.0.0.1", "localhost"):
                raise RuntimeError(
                    f"unsafe gateway config: auth.mode=none with bind={bind!r}. "
                    "Either bind to loopback, or switch auth.mode to token/password/"
                    "trusted-proxy. Refusing to start."
                )
        except FileNotFoundError:
            logger.warning("openclaw.json not found at startup; skipping safety check")
        except RuntimeError:
            raise
        except Exception as e:  # noqa: BLE001
            logger.warning("openclaw config safety check failed (non-fatal): %s", e)

        # Kick off the gateway connect in the background. We do NOT
        # `await` it here — on cold spawns the OpenClaw gateway
        # accepts the WS upgrade but takes 30-60s to wire up its
        # connect.challenge handler, and awaiting that here was
        # blocking uvicorn's "application startup complete" signal
        # (and therefore /health) for the same window. action-sandbox-host
        # then thought we were unhealthy for almost a minute on every
        # cold spawn. Move it to a background task: /health goes live
        # immediately, the connect completes whenever the gateway is
        # actually ready, and the first /task either uses the
        # already-warm connection or triggers an on-demand connect
        # itself (gateway.connect() is idempotent).
        async def _connect_in_background() -> None:
            try:
                await gateway.connect()
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "background gateway connect failed: %s "
                    "(first /task will retry on-demand)", e,
                )

        asyncio.create_task(_connect_in_background())

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        await gateway.close()

    def _authorize(secret: str | None) -> None:
        if not secret or secret != cfg.control_plane_secret:
            raise HTTPException(status_code=401, detail="invalid control-plane secret")

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "worker": "openclaw-webchat-channel",
            "user_id": cfg.user_id,
            "session_id": cfg.session_id,
            "version": app.version,
            "gateway_connected": gateway.is_connected,
        }

    @app.get("/status")
    async def status(
        x_citra_control: str | None = Header(default=None, alias="X-Citra-Control"),
    ) -> dict[str, Any]:
        """Authoritative busy/idle signal for the Citra-Service reaper.

        We're "busy" if any /task SSE stream is open OR if the gateway
        reports an active run for our session. The reaper polls this
        before killing a container that looks idle on its wall clock.
        """
        _authorize(x_citra_control)
        any_open = any(v > 0 for v in open_streams.values())
        any_run = bool(active_runs)
        return {
            "busy": any_open or any_run,
            "open_streams": open_streams,
            "active_runs": dict(active_runs),
            "last_task_started": activity["last_task_started"],
            "last_task_finished": activity["last_task_finished"],
            "now": time.time(),
            "session_id": cfg.session_id,
            "gateway_connected": gateway.is_connected,
        }

    async def _ensure_gateway() -> None:
        if not gateway.is_connected:
            await gateway.connect()

    @app.post("/task")
    async def submit_task(
        request: Request,
        x_citra_control: str | None = Header(default=None, alias="X-Citra-Control"),
    ) -> StreamingResponse:
        """Forward the user's first message and stream OpenClaw events.

        OpenClaw owns the agent loop. We send via ``chat.send``, subscribe
        to ``session.message`` / ``session.tool`` / ``sessions.changed``
        events, project them to the Citra UI envelope, and write them as
        SSE frames. No agent-loop logic lives here.
        """
        _authorize(x_citra_control)
        body = await request.json()
        message = (body or {}).get("message") or ""
        if not isinstance(message, str) or not message.strip():
            raise HTTPException(status_code=400, detail="message is required")

        channel_id = (body or {}).get("channel_id") or ""
        session_key = _session_key_for(
            cfg.session_id, channel=_channel_for_request(channel_id),
        )
        # Defense-in-depth: re-seed persona files BEFORE the turn. The
        # primary guard is the self-preservation rule in SOUL.md; this
        # is the backstop in case the prior turn deleted/modified files.
        try:
            await asyncio.to_thread(
                subprocess.run,
                ["sh", "/srv/citra/seed-persona.sh"],
                check=False, capture_output=True, timeout=10,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("persona re-seed failed (non-fatal): %s", e)

        await _ensure_gateway()

        async def stream() -> AsyncIterator[bytes]:
            activity["last_task_started"] = time.time()
            open_streams[session_key] = open_streams.get(session_key, 0) + 1
            # Outbox tail: surfaces artifacts the agent publishes via
            # citra_toolkit.publish() (PDFs, Excel files, charts) and
            # html_blocks. These are agent-generated user-facing
            # deliverables, not log noise — always forwarded.
            outbox = OutboxWatcher(cfg)
            # B2 fix: gateway.subscribe is now `async def` and registers
            # the queue + sends sessions.messages.subscribe BEFORE
            # returning. Awaiting it resolves only after we are fully
            # subscribed, so the chat.send below cannot race a model
            # event past us.
            try:
                sub_iter = (await gateway.subscribe(session_key)).__aiter__()
            except GatewayError as e:
                err = {"type": "error", "message": f"subscribe failed: {e.code}: {e.message}"}
                yield f"data: {json.dumps(err)}\n\n".encode()
                yield b"data: {\"type\":\"done\"}\n\n"
                open_streams[session_key] = max(0, open_streams.get(session_key, 0) - 1)
                activity["last_task_finished"] = time.time()
                return

            # chat.send is fire-and-forget from our side — OpenClaw acks
            # it with a runId we can use for targeted abort. We don't
            # await the run; events flow through the subscription.
            #
            # BUSY detection (prod stall, 2026-07-22): when a message lands
            # while the agent is mid-run, OpenClaw QUEUES it and this stream
            # may see NO projected events until the in-flight run finishes —
            # minutes of silence after the "Working…" breadcrumb (keepalive
            # comments are invisible to the user). Capture the busy state
            # BEFORE our own chat_send overwrites active_runs, so we can say
            # so out loud instead of letting the chat look frozen.
            was_busy = session_key in active_runs
            idem = uuid.uuid4().hex
            try:
                send_payload = await gateway.chat_send(
                    session_key=session_key,
                    message=message,
                    idempotency_key=idem,
                )
                run_id = (
                    send_payload.get("runId")
                    if isinstance(send_payload, dict)
                    else None
                )
                if run_id:
                    active_runs[session_key] = run_id
            except GatewayError as e:
                err = {"type": "error", "message": f"chat.send failed: {e.code}: {e.message}"}
                yield f"data: {json.dumps(err)}\n\n".encode()
                yield b"data: {\"type\":\"done\"}\n\n"
                open_streams[session_key] = max(0, open_streams.get(session_key, 0) - 1)
                activity["last_task_finished"] = time.time()
                return

            # Initial breadcrumb so the UI replaces "Spinning up…" with
            # something reactive even before the first model delta lands.
            yield f"data: {json.dumps({'type':'status','stage':'started','text':'Working…'})}\n\n".encode()
            if was_busy:
                # Say the quiet part: the message is queued behind an
                # in-flight step, and the wait may be long. Without this the
                # BA (or a driving script) stares at a silent stream and
                # reads it as a hang.
                yield f"data: {json.dumps({'type':'status','stage':'queued','text':'The builder is still finishing the previous step — your message is queued and will be answered as soon as it wraps up.'})}\n\n".encode()

            # Stream loop: pull events from the gateway subscription,
            # project to Citra envelope, write as SSE. Heartbeat every
            # 20s keeps any HTTP intermediary from closing on idle.
            #
            # Terminal detection: OpenClaw releases have shifted the
            # terminal signal between chat:final, sessions.changed
            # phase=ended, and (currently) emitting nothing distinct
            # — the run just stops producing events after the
            # assistant's final session.message. We defensively track
            # `assistant_finished` (set when a session.message arrives
            # with stopReason=="stop") and, if no other terminal
            # signal arrives within ONE keepalive window after that,
            # treat the turn as done. The chat/sessions.changed
            # branches remain so that future OpenClaw versions that
            # DO emit explicit terminal events still close the turn
            # immediately (they take precedence over the fallback).
            #
            # The instrumentation log below records event_name +
            # top-level keys for every received event. Remove once
            # the terminal-signal contract is stable across versions.
            assistant_finished_at: float | None = None
            terminal_grace_seconds = 2.0
            # Did this turn actually deliver an assistant reply to the BA? If
            # the gateway subscription closes (StopAsyncIteration) before any
            # reply was projected, the run died silently upstream — most often
            # the session exceeded the model context window after a long build.
            # We MUST surface that as a loud error, not a bare "done" that the
            # UI shows as success (fail-loud; see the StopAsyncIteration branch).
            delivered_reply = False

            def _is_assistant_final(name: str, p: dict[str, Any]) -> bool:
                if name != "session.message":
                    return False
                msg = p.get("message") if isinstance(p, dict) else None
                if not isinstance(msg, dict):
                    return False
                if (msg.get("role") or "") != "assistant":
                    return False
                stop_reason = (msg.get("stopReason") or msg.get("stop_reason") or "")
                return str(stop_reason).lower() in ("stop", "end_turn", "completed")

            # Earlier we factored the terminal drain+done sequence into a
            # nested async generator `_emit_done()` and iterated it from
            # each terminal branch with `async for chunk in _emit_done():
            # yield chunk`. That looked clean but interacted badly with
            # Starlette's StreamingResponse — the chat:final branch
            # would log, the inner generator would run, but Starlette
            # never managed to emit its trailing `more_body=False`
            # body event, so uvicorn surfaced "ASGI callable returned
            # without completing response" and the client side saw
            # "incomplete chunked read". Inlining the yields at each
            # terminal site fixes it; the duplication is a fair cost
            # for not fighting the async-generator-of-async-generator
            # interaction.

            # Silent-stretch watchdog: SSE comment keepalives keep the SOCKET
            # alive but are invisible to the user. If NO projected event has
            # been emitted for a while (queued message behind a long in-flight
            # run, or a very long model call), emit a VISIBLE status with the
            # elapsed time every ~45s so the chat never looks frozen.
            _last_visible = time.time()
            _silent_notice_every = 45.0

            next_task: asyncio.Task | None = asyncio.create_task(sub_iter.__anext__())
            # The agent's LLM call can pause the gateway event stream for
            # 30-60+ seconds while DeepSeek generates a long reply. Docker
            # Desktop's port forwarder on Windows kills idle TCP forwards
            # after ~30s of silence on a chunked response, so we MUST
            # send a chunk to the wire more often than that to keep the
            # forwarder from dropping the connection mid-turn. Cap the
            # wait_timeout at 5s so a keepalive comment is yielded at
            # least every 5s even when OpenClaw isn't emitting events.
            base_wait_timeout = 5.0
            try:
                while next_task is not None:
                    # Tighten the wait to the terminal-grace window
                    # once we've seen the assistant's final message,
                    # so the fallback fires within a couple seconds
                    # of the agent finishing rather than 5s later.
                    wait_timeout = (
                        terminal_grace_seconds
                        if assistant_finished_at is not None
                        else base_wait_timeout
                    )
                    done, _ = await asyncio.wait(
                        {next_task},
                        timeout=wait_timeout,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if not done:
                        # Two reasons to be in this branch:
                        # (1) No events for `wait_timeout` seconds.
                        #     If we've already seen the assistant's
                        #     final message, this IS the terminal —
                        #     OpenClaw simply isn't going to emit a
                        #     chat:final. Drain outbox, send done,
                        #     close cleanly.
                        # (2) Client disconnected. Still yield done
                        #     so the chunked stream terminates with
                        #     a proper end marker — prevents the
                        #     "incomplete chunked read" the upstream
                        #     httpx client otherwise surfaces.
                        if assistant_finished_at is not None:
                            logger.info(
                                "[task-stream] terminal-by-fallback "
                                "sessionKey=%s grace=%.1fs",
                                session_key, terminal_grace_seconds,
                            )
                            async for outbox_evt in outbox.drain():
                                yield f"data: {json.dumps(outbox_evt)}\n\n".encode()
                            yield b"data: {\"type\":\"done\"}\n\n"
                            active_runs.pop(session_key, None)
                            break
                        if await request.is_disconnected():
                            logger.info(
                                "[task-stream] client disconnected "
                                "sessionKey=%s — emitting done before close",
                                session_key,
                            )
                            yield b"data: {\"type\":\"done\"}\n\n"
                            break
                        if time.time() - _last_visible >= _silent_notice_every:
                            _mins = int((time.time() - activity["last_task_started"]) // 60)
                            _busy_txt = (
                                "Still working — the builder is finishing the "
                                "previous step before answering this message"
                                if was_busy else
                                "Still working on this step"
                            )
                            yield (
                                f"data: {json.dumps({'type':'status','stage':'working','text': _busy_txt + (f' ({_mins} min elapsed)…' if _mins else '…')})}\n\n"
                            ).encode()
                            _last_visible = time.time()
                        else:
                            yield b": keepalive\n\n"
                        continue
                    try:
                        event_name, payload = next_task.result()
                        _last_visible = time.time()
                    except StopAsyncIteration:
                        # Subscription closed — gateway is done with us.
                        next_task = None
                        if not delivered_reply and assistant_finished_at is None:
                            # The run closed WITHOUT delivering any assistant
                            # reply and without a normal terminal — it died
                            # silently upstream (most commonly the session
                            # exceeded the model context window after a long
                            # multi-turn build). Fail LOUD with an actionable
                            # message instead of a bare "done" the UI would
                            # render as a successful, empty turn.
                            logger.warning(
                                "[task-stream] subscription closed with NO reply "
                                "delivered sessionKey=%s — surfacing empty-run "
                                "error (likely context-window limit)",
                                session_key,
                            )
                            err = {
                                "type": "error",
                                "message": (
                                    "The builder produced no response for this "
                                    "turn. The build session may have reached its "
                                    "context limit — start a new build session to "
                                    "continue."
                                ),
                            }
                            yield f"data: {json.dumps(err)}\n\n".encode()
                        else:
                            logger.info(
                                "[task-stream] subscription StopAsyncIteration "
                                "sessionKey=%s — emitting done",
                                session_key,
                            )
                        # Always close the chunk stream cleanly with done.
                        yield b"data: {\"type\":\"done\"}\n\n"
                        break
                    next_task = asyncio.create_task(sub_iter.__anext__())

                    # Instrumentation: log every event so we can map
                    # the OpenClaw protocol surface. Top-level keys
                    # are usually small (state/phase/stream/message)
                    # — capture them at INFO so they show in
                    # docker logs. Drop once the terminal-signal
                    # contract is stable.
                    try:
                        _top_keys = (
                            list(payload.keys())[:8] if isinstance(payload, dict) else None
                        )
                    except Exception:  # noqa: BLE001
                        _top_keys = None
                    logger.info(
                        "[task-stream] event=%s keys=%s state=%s phase=%s stream=%s",
                        event_name, _top_keys,
                        payload.get("state") if isinstance(payload, dict) else None,
                        payload.get("phase") if isinstance(payload, dict) else None,
                        payload.get("stream") if isinstance(payload, dict) else None,
                    )

                    if event_name == "session.message":
                        projected = _project_session_message(payload)
                        if projected:
                            delivered_reply = True
                        for ui_evt in projected:
                            yield f"data: {json.dumps(ui_evt)}\n\n".encode()
                        # Drain artifact outbox between gateway events
                        # so reports surface mid-turn, not only at end.
                        async for outbox_evt in outbox.drain():
                            yield f"data: {json.dumps(outbox_evt)}\n\n".encode()
                        if _is_assistant_final(event_name, payload):
                            # Arm the terminal-grace timer. The next
                            # loop iteration will close the turn via
                            # the fallback path if no chat:final or
                            # sessions.changed:ended arrives within
                            # terminal_grace_seconds.
                            assistant_finished_at = time.time()
                            logger.info(
                                "[task-stream] assistant final detected via "
                                "session.message stopReason — arming "
                                "%.1fs terminal-grace sessionKey=%s",
                                terminal_grace_seconds, session_key,
                            )
                    elif event_name == "session.tool":
                        for ui_evt in _project_session_tool(payload):
                            yield f"data: {json.dumps(ui_evt)}\n\n".encode()
                        async for outbox_evt in outbox.drain():
                            yield f"data: {json.dumps(outbox_evt)}\n\n".encode()
                    elif event_name == "chat":
                        # Native ChatEventSchema: lifecycle of the run.
                        # state in {"delta","final","aborted","error"}.
                        # Terminal states close the turn. Deltas and
                        # any unknown state forward to the reasoning
                        # tab so the user sees the model's streaming
                        # output in real time even before chat:final
                        # lands. The UI dedupes against the canonical
                        # session.message text when it eventually
                        # arrives.
                        state = (payload.get("state") or "").lower()
                        if state == "final":
                            logger.info(
                                "[task-stream] chat:final received "
                                "sessionKey=%s — draining outbox + emitting done",
                                session_key,
                            )
                            async for outbox_evt in outbox.drain():
                                yield f"data: {json.dumps(outbox_evt)}\n\n".encode()
                            yield b"data: {\"type\":\"done\"}\n\n"
                            logger.info(
                                "[task-stream] chat:final done emitted, breaking loop "
                                "sessionKey=%s", session_key,
                            )
                            active_runs.pop(session_key, None)
                            break
                        if state == "aborted":
                            yield b"data: {\"type\":\"cancelled\"}\n\n"
                            active_runs.pop(session_key, None)
                            break
                        if state == "error":
                            err_msg = payload.get("errorMessage") or "agent error"
                            yield f"data: {json.dumps({'type':'error','message':err_msg})}\n\n".encode()
                            active_runs.pop(session_key, None)
                            break
                        # state == "delta" or unknown: partial assistant
                        # text streaming. We INTENTIONALLY drop these —
                        # the canonical assembled text arrives in the
                        # session.message text block. Forwarding deltas
                        # would spam the reasoning tab with
                        # "Hi" / "Hi R" / "Hi Roh" / ... incremental
                        # accumulations of the same message.
                    elif event_name == "agent":
                        # Native AgentEventSchema: stream of agent-loop
                        # activity. OpenClaw emits multiple `stream`
                        # variants — we forward everything that has
                        # user-visible content so long-running turns
                        # don't appear silent in the UI:
                        #
                        #   stream=tool          → tool_call / tool_result
                        #                          (mirrors session.tool but
                        #                           arrives earlier in the
                        #                           pipeline; UI dedupes)
                        #   stream=thinking      → THINKING event (reasoning
                        #                          tab — model's hidden
                        #                          chain-of-thought deltas;
                        #                          the ONLY thing the
                        #                          reasoning tab shows)
                        #   stream=assistant     → ignored (partial assistant
                        #                          text deltas; canonical
                        #                          text comes via
                        #                          session.message)
                        #   stream=command_output→ ignored (bash/python
                        #                          stdout — tool output, not
                        #                          reasoning; tool_call /
                        #                          tool_result already show
                        #                          what ran)
                        #   stream=item          → ignored (structured
                        #                          model-output items: tool
                        #                          drafts, plan updates —
                        #                          redundant with
                        #                          session.message)
                        #   stream=lifecycle     → ignored (turn boundary,
                        #                          handled by chat:final
                        #                          + session.message)
                        stream_kind = (payload.get("stream") or "").lower()
                        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
                        if stream_kind in ("tool", "item"):
                            # stream=tool — explicit tool lifecycle.
                            # stream=item — structured model-output items.
                            # MCP tool calls (`citra_*`) surface here as
                            # `item`s, NOT as session.tool / session.message
                            # blocks, so dropping `item` (as we used to)
                            # meant the reasoning tab never showed retrieval
                            # activity OR tool failures — the user just saw
                            # silence. Route both through the tool projector:
                            # it extracts tool_call / tool_result when the
                            # payload has a tool shape (name + phase) and
                            # returns [] for non-tool items (plan drafts,
                            # etc.), so this is safe. tool_call / tool_result
                            # carry a `call_id`; the UI dedupes on it, so a
                            # tool echoed by both `tool` and `item` collapses
                            # to one card.
                            for ui_evt in _project_session_tool({"data": data}):
                                yield f"data: {json.dumps(ui_evt)}\n\n".encode()
                        elif stream_kind in ("lifecycle", "assistant", "command_output"):
                            # lifecycle = turn boundary (no content).
                            # assistant = partial assistant text deltas;
                            # canonical text arrives in session.message
                            # — forwarding deltas would spam reasoning
                            # tab with incremental copies of the chat
                            # message.
                            # command_output = bash/python stdout the
                            # agent is observing — that is tool *output*,
                            # not the model's reasoning. The tool_call /
                            # tool_result events already tell the user
                            # which tools ran; streaming raw stdout (web-
                            # fetch HTML, tracebacks, script echoes) only
                            # floods the reasoning tab.
                            # All three intentionally dropped here.
                            pass
                        elif stream_kind == "thinking":
                            # thinking = real chain-of-thought tokens —
                            # the only thing the reasoning tab should show.
                            txt = (
                                data.get("text")
                                or data.get("delta")
                                or data.get("thinking")
                                or data.get("output")
                                or data.get("stdout")
                                or data.get("stderr")
                                or data.get("content")
                                or ""
                            )
                            if isinstance(txt, str) and txt:
                                evt = {"type": "thinking", "stream": stream_kind, "text": txt}
                                yield f"data: {json.dumps(evt)}\n\n".encode()
                        # Other unknown stream kinds: intentionally
                        # NOT forwarded. If a useful new stream type
                        # appears, add it explicitly above. The
                        # `[task-stream] event=agent stream=X` INFO
                        # log a few lines above records the name so
                        # we can decide whether to surface it.
                    elif event_name == "sessions.changed":
                        # Some flows still emit sessions.changed phase=ended;
                        # treat as a fallback done signal if we somehow
                        # missed the chat:final.
                        phase = (payload.get("phase") or "").lower()
                        if phase in ("ended", "completed", "done"):
                            async for outbox_evt in outbox.drain():
                                yield f"data: {json.dumps(outbox_evt)}\n\n".encode()
                            yield b"data: {\"type\":\"done\"}\n\n"
                            active_runs.pop(session_key, None)
                            break
                        # Non-terminal phases (started, processing, …)
                        # are session-state changes, not LLM output —
                        # intentionally not forwarded.
                    # Unknown event names: intentionally NOT forwarded.
                    # The `[task-stream] event=...` INFO log above
                    # records them so we can decide whether to add
                    # explicit handling. Catch-all forwarding spammed
                    # the reasoning tab with infrastructure noise.
            except asyncio.CancelledError:
                yield b"data: {\"type\":\"cancelled\"}\n\n"
                raise
            except Exception as _stream_exc:  # noqa: BLE001
                # Anything escaping the event-dispatch loop would otherwise
                # propagate out of the async generator and leave Starlette
                # closing the TCP socket WITHOUT writing the chunked
                # terminator. The client side (action-chat-service's
                # httpx.aiter_raw) then surfaces "peer closed connection
                # without sending complete message body" all the way to the
                # UI. Convert to a typed SSE error + clean done so the UI
                # gets a renderable error event and the chunk stream ends
                # gracefully. The traceback is logged so the underlying
                # cause (json.dumps, outbox.drain, projection, etc.) is
                # debuggable from docker logs on the next reproduction.
                logger.exception(
                    "/task stream crashed sessionKey=%s: %s",
                    session_key, _stream_exc,
                )
                try:
                    err = {
                        "type": "error",
                        "message": f"agent stream failed: {type(_stream_exc).__name__}",
                    }
                    yield f"data: {json.dumps(err)}\n\n".encode()
                    yield b"data: {\"type\":\"done\"}\n\n"
                except Exception:  # noqa: BLE001
                    # Connection already torn — nothing to do; the logged
                    # traceback above is the diagnostic record.
                    pass
                active_runs.pop(session_key, None)
            finally:
                if next_task is not None:
                    if not next_task.done():
                        next_task.cancel()
                    else:
                        # next_task already finished — commonly with
                        # StopAsyncIteration, when the gateway subscription
                        # ended between our last await and the terminal
                        # branch. Retrieve its outcome so asyncio does not
                        # log a spurious ERROR ("Task exception was never
                        # retrieved") after every completed turn.
                        try:
                            next_task.exception()
                        except (asyncio.CancelledError, asyncio.InvalidStateError):
                            pass
                # Close the underlying subscription generator (its own
                # finally unsubscribes via a gateway RPC).
                #
                # This MUST NOT be able to delay or break HTTP response
                # completion. Previously this was `await sub_iter.aclose()`
                # guarded by `except Exception` — but the unsubscribe RPC
                # can be slow or get cancelled, and a `CancelledError`
                # (a BaseException, NOT an Exception) then escaped this
                # finally, escaped the generator, and stopped Starlette
                # from emitting the terminal body chunk. uvicorn logged
                # "ASGI callable returned without completing response"
                # and the browser EventSource never saw a clean close —
                # the UI spinner hung forever even though the agent had
                # finished. Bound the RPC with wait_for and swallow
                # *BaseException* so nothing can escape: the generator
                # always returns cleanly and Starlette always completes
                # the response. A missed unsubscribe is harmless — the
                # sandbox is single-conversation and reaped on idle.
                try:
                    await asyncio.wait_for(
                        sub_iter.aclose(),  # type: ignore[attr-defined]
                        timeout=3.0,
                    )
                except BaseException:  # noqa: BLE001  (incl. CancelledError/TimeoutError)
                    pass
                open_streams[session_key] = max(0, open_streams.get(session_key, 0) - 1)
                activity["last_task_finished"] = time.time()

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/task/steer")
    async def steer_task(
        request: Request,
        x_citra_control: str | None = Header(default=None, alias="X-Citra-Control"),
    ) -> JSONResponse:
        """Forward a follow-up message via OpenClaw's native chat.send.

        OpenClaw's ``messages.queue.mode = "steer"`` config (in
        openclaw.json under messages.queue.byChannel.webchat) decides
        what happens: queue the message, inject between tool calls,
        skip remaining tool calls, etc. We do not cancel anything.
        """
        _authorize(x_citra_control)
        body = await request.json()
        message = (body or {}).get("message") or ""
        if not isinstance(message, str) or not message.strip():
            raise HTTPException(status_code=400, detail="message is required")
        channel_id = (body or {}).get("channel_id") or ""

        await _ensure_gateway()
        session_key = _session_key_for(
            cfg.session_id, channel=_channel_for_request(channel_id),
        )
        # A steer is only meaningful while a /task SSE stream is open for
        # this session — that stream is what carries the steered turn's
        # output back to the UI. With NO open stream the steer would be
        # sent into OpenClaw's `steer` queue with no live turn to inject
        # into and no stream to surface anything: the message vanishes and
        # the user sees "stuck forever" (the orphaned-steer bug). Don't
        # fire a doomed steer — tell the caller so it re-sends the message
        # as a fresh /task turn (which opens its own stream).
        if open_streams.get(session_key, 0) <= 0:
            logger.info(
                "[task-steer] no open /task stream for sessionKey=%s — "
                "returning no_active_run; caller should open a fresh /task",
                session_key,
            )
            return JSONResponse({"queued": False, "no_active_run": True})
        idem = uuid.uuid4().hex
        try:
            payload = await gateway.chat_send(
                session_key=session_key,
                message=message,
                idempotency_key=idem,
            )
        except GatewayError as e:
            raise HTTPException(
                status_code=502,
                detail=f"openclaw chat.send rejected steer: {e.code}: {e.message}",
            ) from e
        return JSONResponse({
            "queued": True,
            "runId": payload.get("runId") if isinstance(payload, dict) else None,
        })

    @app.post("/task/cancel")
    async def cancel_task(
        request: Request,
        x_citra_control: str | None = Header(default=None, alias="X-Citra-Control"),
    ) -> JSONResponse:
        """Forward an abort to OpenClaw via chat.abort.

        OpenClaw stops the active run, broadcasts a sessions.changed
        event with phase=aborted, and any open /task stream emits the
        cancelled SSE event before closing.

        Cancel is per-channel: tab A's cancel does not interrupt tab B's
        running turn. Body shape: ``{channel_id?: str}``. Missing /
        empty channel_id targets the legacy "webchat" channel for
        back-compat with older Citra-Service replicas.
        """
        _authorize(x_citra_control)
        # Body is optional — accept empty / non-JSON to stay back-compat
        # with the prior signature (no body).
        channel_id = ""
        try:
            if (request.headers.get("content-type") or "").startswith("application/json"):
                body = await request.json()
                if isinstance(body, dict):
                    channel_id = str(body.get("channel_id") or "")
        except Exception:  # noqa: BLE001 — empty body is allowed
            channel_id = ""
        await _ensure_gateway()
        session_key = _session_key_for(
            cfg.session_id, channel=_channel_for_request(channel_id),
        )
        try:
            await gateway.chat_abort(
                session_key=session_key,
                run_id=active_runs.get(session_key),
            )
        except GatewayError as e:
            raise HTTPException(
                status_code=502,
                detail=f"openclaw chat.abort rejected: {e.code}: {e.message}",
            ) from e
        return JSONResponse({"cancelled": True})

    return app


def main() -> None:
    cfg = SandboxConfig.from_env()
    if not cfg.llm_api_key:
        logger.warning(
            "CITRA_AGENT_LLM_API_KEY is empty — openclaw will fail to reach the LLM. "
            "Fix this in Citra-Service .env."
        )
    app = _build_app(cfg)
    uvicorn.run(app, host=cfg.bind_host, port=cfg.bind_port, log_level="info")


if __name__ == "__main__":
    main()
