# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""OpenClaw gateway WebSocket client.

We are a channel into OpenClaw, just like the Telegram, WhatsApp, Slack,
and WebChat channels. OpenClaw owns the chat system: queueing, steering,
threading, abort, history, sub-agent dispatch. We forward messages and
relay events. Nothing more.

Protocol reference (pulled from OpenClaw source main, May 2026):

    Connect handshake (PROTOCOL_VERSION=4):

        gateway -> client : {type:"event", event:"connect.challenge",
                             payload:{nonce, ts}}
        client  -> gateway: {type:"req", id, method:"connect", params:{
                             minProtocol:3, maxProtocol:4,
                             client:{id:"webchat", version, platform,
                                     mode:"webchat"},
                             role:"operator",
                             scopes:["operator.read","operator.write"],
                             auth:{token}}}
        gateway -> client : {type:"res", id, ok:true, payload:{type:
                             "hello-ok", protocol, server, features,
                             snapshot, auth, policy}}

    RPC frame: {type:"req", id, method, params}
                -> {type:"res", id, ok, payload?, error?}

    Event frame: {type:"event", event, payload, seq?, stateVersion?}

    Methods we use:
        chat.send                       (send a user message)
            params: {sessionKey, message, idempotencyKey, ...}
            -> {runId, messageSeq}
        chat.abort                      (abort an active turn)
            params: {sessionKey, runId?}
        chat.history                    (read transcript)
            params: {sessionKey, limit?, maxChars?}
        sessions.messages.subscribe     (subscribe to transcript events)
            params: {key}
        sessions.messages.unsubscribe   (unsubscribe)
            params: {key}

    Events we relay (filtered by sessionKey at the broadcaster, and again
    on our side for safety):
        session.message  payload: {sessionKey, message, messageId?,
                                   messageSeq?, ...sessionSnapshot}
        session.tool     payload: {runId, sessionKey, stream:"tool",
                                   ts, data:{phase, name, toolCallId,
                                              args, ...}, ...}
        sessions.changed payload: {sessionKey, phase, ts, ...}

References (sparse-checked openclaw repo):
  - src/gateway/protocol/schema/frames.ts            (RPC envelopes)
  - src/gateway/protocol/schema/logs-chat.ts         (chat.* params)
  - src/gateway/protocol/schema/sessions.ts          (sessions.* params)
  - src/gateway/protocol/version.ts                  (PROTOCOL_VERSION=4)
  - src/gateway/protocol/client-info.ts              (client IDs / modes)
  - src/gateway/operator-scopes.ts                   (operator.read/write)
  - src/gateway/server-broadcast.ts                  (event scopes)
  - src/gateway/server-session-events.ts             (session.message shape)
  - src/gateway/test-helpers.server.ts               (connectReq, rpcReq)
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import time as _time
import uuid
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable

import websockets
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from websockets.exceptions import ConnectionClosed
from websockets.legacy.client import WebSocketClientProtocol

logger = logging.getLogger("action-sandbox.gateway")


# ---- device identity ------------------------------------------------------
# OpenClaw's WebSocket gateway requires every connect frame to carry a
# device-pairing block: an Ed25519 public key + a signature over the
# v3 auth payload (deviceId|clientId|clientMode|role|scopes|signedAt|
# token|nonce|platform|deviceFamily). Even under auth.mode="none" the
# gateway insists on a device identity for any scoped operation.
#
# We generate a key on first start, persist to disk so the deviceId is
# stable across restarts, and rebuild the signature for each connect
# (the nonce changes per challenge).
def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def _normalize_metadata(value: str | None) -> str:
    """Match TS normalizeDeviceMetadataForAuth: trim + ASCII-lowercase."""
    if not isinstance(value, str):
        return ""
    trimmed = value.strip()
    if not trimmed:
        return ""
    out = []
    for ch in trimmed:
        c = ord(ch)
        if 65 <= c <= 90:  # ASCII A-Z
            out.append(chr(c + 32))
        else:
            out.append(ch)
    return "".join(out)


class DeviceIdentity:
    """Ed25519 keypair + derived deviceId for the gateway connect."""

    def __init__(self, private_key: ed25519.Ed25519PrivateKey) -> None:
        self.private_key = private_key
        self.public_key = private_key.public_key()
        raw_pub = self.public_key.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        self.public_key_raw = raw_pub
        # deviceId per src/infra/device-identity.ts deriveDeviceIdFromPublicKey:
        # sha256(raw 32-byte Ed25519 public key), hex-encoded.
        self.device_id = hashlib.sha256(raw_pub).hexdigest()
        # publicKey field per buildDeviceAuthPayloadV3 callers:
        # base64url(raw 32-byte public key), no padding.
        self.public_key_b64url = _b64url(raw_pub)

    @classmethod
    def load_or_create(cls, path: str | Path) -> "DeviceIdentity":
        p = Path(path)
        if p.is_file():
            try:
                with p.open("rb") as f:
                    raw = f.read()
                key = ed25519.Ed25519PrivateKey.from_private_bytes(raw)
                return cls(key)
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "device identity at %s unreadable, regenerating: %s", p, e,
                )
        p.parent.mkdir(parents=True, exist_ok=True)
        key = ed25519.Ed25519PrivateKey.generate()
        raw = key.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )
        with p.open("wb") as f:
            f.write(raw)
        try:
            os.chmod(p, 0o600)
        except OSError:
            pass
        return cls(key)

    def sign_v3_payload(
        self,
        *,
        client_id: str,
        client_mode: str,
        role: str,
        scopes: list[str],
        nonce: str,
        token: str = "",
        platform: str = "linux",
        device_family: str = "",
    ) -> tuple[str, int]:
        """Return (signature_b64url, signedAtMs).

        Payload format mirrors src/gateway/device-auth.ts
        buildDeviceAuthPayloadV3 verbatim.
        """
        signed_at_ms = int(_time.time() * 1000)
        payload = "|".join(
            [
                "v3",
                self.device_id,
                client_id,
                client_mode,
                role,
                ",".join(scopes),
                str(signed_at_ms),
                token,
                nonce,
                _normalize_metadata(platform),
                _normalize_metadata(device_family),
            ]
        )
        sig = self.private_key.sign(payload.encode("utf-8"))
        return _b64url(sig), signed_at_ms


# ---- protocol constants (kept aligned with OpenClaw source) ---------------
PROTOCOL_VERSION = 4
MIN_CLIENT_PROTOCOL_VERSION = 3
CLIENT_ID_WEBCHAT = "webchat"
CLIENT_MODE_WEBCHAT = "webchat"
SCOPE_READ = "operator.read"
SCOPE_WRITE = "operator.write"
ROLE_OPERATOR = "operator"

# Bounded session-key length per ChatSendSessionKeyString.
CHAT_SEND_SESSION_KEY_MAX_LENGTH = 512


class GatewayError(RuntimeError):
    """Raised when the gateway returns ok=false or transport fails."""

    def __init__(self, code: str, message: str, details: Any = None) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.details = details


class GatewayNotConnectedError(RuntimeError):
    """Raised when an RPC is attempted before connect() succeeds."""


EventCallback = Callable[[str, dict[str, Any]], Awaitable[None] | None]


class _Subscription:
    """One open subscription to a stream of events.

    Filters events by ``sessionKey`` at the application layer (the gateway
    also scopes by subscriber set, but a single client may hold multiple
    subscriptions and receive events from all of them; the dispatch loop
    needs to fan-out by key)."""

    def __init__(self, session_key: str, queue: asyncio.Queue) -> None:
        self.session_key = session_key
        self.queue = queue


class OpenClawGatewayClient:
    """Long-lived WebSocket connection to a local OpenClaw gateway.

    Lifecycle:
        client = OpenClawGatewayClient(token="...")
        await client.connect()
        await client.chat_send(session_key, "hi", idempotency_key=...)
        async for evt_name, payload in client.subscribe(session_key):
            ...

    Reconnect: ``connect()`` retries with exponential backoff up to
    ``connect_timeout_seconds``. Once connected the reader loop exits on
    transport error; callers can call ``connect()`` again to resume. The
    adapter wraps this with a watchdog that ensures the connection is up
    before each request.
    """

    def __init__(
        self,
        *,
        url: str | None = None,
        token: str | None = None,
        client_id: str = CLIENT_ID_WEBCHAT,
        client_mode: str = CLIENT_MODE_WEBCHAT,
        client_version: str = "0.3.0-citra",
        client_platform: str | None = None,
        scopes: tuple[str, ...] = (SCOPE_READ, SCOPE_WRITE),
        device_identity_path: str | None = None,
        connect_timeout_seconds: float = 180.0,
        rpc_timeout_seconds: float = 120.0,
        max_message_bytes: int = 32 * 1024 * 1024,
    ) -> None:
        self._url = url or os.getenv(
            "OPENCLAW_GATEWAY_WS_URL", "ws://127.0.0.1:18789",
        )
        # Token is optional when the gateway is configured with
        # ``auth.mode: "none"`` (the documented pattern for loopback-only
        # ingress, which is our shape). When a token is supplied we send
        # it in the connect.auth.token field; the gateway accepts but
        # doesn't require it under auth-none.
        self._token = token or os.getenv("OPENCLAW_GATEWAY_TOKEN", "")
        self._client_id = client_id
        self._client_mode = client_mode
        self._client_version = client_version
        self._client_platform = client_platform or os.uname().sysname.lower()
        self._scopes = list(scopes)
        self._connect_timeout = connect_timeout_seconds
        self._rpc_timeout = rpc_timeout_seconds
        self._max_message_bytes = max_message_bytes
        # Device identity is mandatory: the gateway rejects connect with
        # NOT_PAIRED unless the client signs a v3 auth payload with an
        # Ed25519 key. Default location lives on /home/citra (tmpfs in
        # production but persists for the lifetime of the container —
        # OpenClaw treats first-contact pairing as auto-approve under
        # auth.mode=none, so we just need a stable key).
        identity_path = device_identity_path or os.getenv(
            "OPENCLAW_ADAPTER_DEVICE_IDENTITY_PATH",
            "/home/citra/.openclaw-home/.openclaw-adapter/device.key",
        )
        self._identity = DeviceIdentity.load_or_create(identity_path)

        self._ws: WebSocketClientProtocol | None = None
        self._reader_task: asyncio.Task | None = None
        self._connected_event = asyncio.Event()
        # Pending RPCs by id -> Future expecting the response payload.
        self._pending: dict[str, asyncio.Future] = {}
        # Subscriptions keyed by sessionKey -> set of queues that want events.
        self._subscriptions: dict[str, set[asyncio.Queue]] = {}
        # Hello payload returned at connect time (server version, features).
        self.hello: dict[str, Any] | None = None
        # Lock so two callers can't race a reconnect.
        self._connect_lock = asyncio.Lock()

    # ---- lifecycle ---------------------------------------------------
    async def connect(self) -> None:
        """Open the WebSocket and complete the connect handshake.

        Idempotent: if already connected does nothing. If a previous
        connection died (reader task done) tears down state and reopens.
        """
        async with self._connect_lock:
            if self._ws is not None and not self._ws.closed:
                return
            # Reap any leftover reader task from a previous connection.
            if self._reader_task is not None and not self._reader_task.done():
                self._reader_task.cancel()
                try:
                    await self._reader_task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
            self._reader_task = None
            self._reset_state_after_disconnect()
            loop = asyncio.get_running_loop()
            deadline = loop.time() + self._connect_timeout
            backoff = 0.5
            last_err: Exception | None = None
            while loop.time() < deadline:
                try:
                    await self._open_and_handshake()
                    return
                except (OSError, ConnectionClosed, GatewayError) as e:
                    last_err = e
                    logger.info(
                        "gateway connect attempt failed: %s — retrying in %.1fs",
                        e, backoff,
                    )
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 1.7, 5.0)
            raise RuntimeError(
                f"could not connect to OpenClaw gateway at {self._url}: {last_err}"
            )

    async def _open_and_handshake(self) -> None:
        # The gateway enforces an Origin allow-list (carried over from
        # browser-WebSocket security). For loopback control-plane
        # clients it accepts an Origin matching the gateway URL itself.
        # Without this header the upgrade is rejected with
        # ``INVALID_REQUEST: origin not allowed``.
        origin = self._url.replace("ws://", "http://").replace("wss://", "https://")
        # Strip any path the user passed.
        if "/" in origin.split("://", 1)[1]:
            origin = origin.split("://", 1)[0] + "://" + origin.split("://", 1)[1].split("/", 1)[0]
        # Ping is DISABLED. The OpenClaw gateway emits its own ``tick``
        # event at policy.tickIntervalMs (default 15s) which keeps the
        # connection alive end-to-end. The first agent turn can pause
        # the gateway for 30-60s while extension plugins lazy-install
        # runtime deps (browser, acpx, microsoft) — during that pause
        # client-side pings get no pong and the WS hard-closes with
        # code 1006. Letting the server-side tick drive the keepalive
        # avoids the false-positive disconnect.
        # open_timeout shrunk from default 10s -> 2s for the same reason
        # as the challenge recv timeout below: during cold spawn, the
        # OpenClaw gateway accepts the TCP/WS upgrade but its handshake
        # handler isn't always wired yet, so each connect attempt was
        # burning 10s waiting for an upgrade that wasn't going to happen.
        # With both timeouts at 2s, the outer connect() retry loop
        # cycles in ~2-3s instead of ~15s and converges within ~10s.
        ws = await websockets.connect(
            self._url,
            max_size=self._max_message_bytes,
            ping_interval=None,
            open_timeout=2,
            close_timeout=5,
            extra_headers={"Origin": origin},
        )
        self._ws = ws

        # The gateway pushes ``connect.challenge`` as its first frame.
        # The nonce in the payload MUST be folded into the device
        # signature (per buildDeviceAuthPayloadV3) — without it the
        # gateway rejects with NOT_PAIRED / bad signature.
        #
        # We use a SHORT challenge timeout (2s) so the outer connect()
        # retry loop cycles quickly during the boot window. Right after
        # `[gateway] ready` is logged, OpenClaw's TCP socket accepts
        # WS upgrades but its connect-handshake handler isn't always
        # wired yet — the challenge frame can be delayed by 30-60s on
        # cold spawns. With a 10s timeout, each retry burned 10s, so
        # the adapter sometimes sat at "gateway connect attempt failed"
        # for over a minute before getting through. 2s × ~5 retries
        # = under 15s in practice, and once the handshake handler IS
        # live the challenge arrives in well under 100ms.
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
        except asyncio.TimeoutError as e:
            await ws.close()
            self._ws = None
            raise GatewayError(
                "no_challenge",
                "gateway did not send connect.challenge within 2s",
            ) from e
        try:
            challenge = json.loads(raw)
        except json.JSONDecodeError as e:
            await ws.close()
            self._ws = None
            raise GatewayError("bad_challenge", f"non-JSON challenge: {raw!r}") from e
        if not (
            isinstance(challenge, dict)
            and challenge.get("type") == "event"
            and challenge.get("event") == "connect.challenge"
        ):
            await ws.close()
            self._ws = None
            raise GatewayError(
                "bad_challenge",
                f"expected connect.challenge event, got {challenge!r}",
            )
        nonce = ((challenge.get("payload") or {}).get("nonce") or "")
        if not nonce:
            await ws.close()
            self._ws = None
            raise GatewayError("bad_challenge", "challenge missing nonce")

        # Sign the v3 device-auth payload binding (deviceId, clientId,
        # clientMode, role, scopes, signedAt, token, nonce, platform,
        # deviceFamily) with our Ed25519 key.
        signature_b64url, signed_at_ms = self._identity.sign_v3_payload(
            client_id=self._client_id,
            client_mode=self._client_mode,
            role=ROLE_OPERATOR,
            scopes=self._scopes,
            nonce=nonce,
            token=self._token or "",
            platform=self._client_platform,
            device_family="",
        )

        # Send connect request and await hello-ok in-line (no reader yet).
        connect_id = uuid.uuid4().hex
        connect_req = {
            "type": "req",
            "id": connect_id,
            "method": "connect",
            "params": {
                "minProtocol": MIN_CLIENT_PROTOCOL_VERSION,
                "maxProtocol": PROTOCOL_VERSION,
                "client": {
                    "id": self._client_id,
                    "version": self._client_version,
                    "platform": self._client_platform,
                    "mode": self._client_mode,
                },
                "caps": [],
                "role": ROLE_OPERATOR,
                "scopes": self._scopes,
                "device": {
                    "id": self._identity.device_id,
                    "publicKey": self._identity.public_key_b64url,
                    "signature": signature_b64url,
                    "signedAt": signed_at_ms,
                    "nonce": nonce,
                },
                **({"auth": {"token": self._token}} if self._token else {}),
            },
        }
        await ws.send(json.dumps(connect_req))

        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=10.0)
        except asyncio.TimeoutError as e:
            await ws.close()
            self._ws = None
            raise GatewayError(
                "no_hello",
                "gateway did not respond to connect request within 10s",
            ) from e
        try:
            res = json.loads(raw)
        except json.JSONDecodeError as e:
            await ws.close()
            self._ws = None
            raise GatewayError("bad_hello", f"non-JSON connect response: {raw!r}") from e
        if not (
            isinstance(res, dict)
            and res.get("type") == "res"
            and res.get("id") == connect_id
        ):
            await ws.close()
            self._ws = None
            raise GatewayError(
                "bad_hello", f"expected connect res, got {res!r}",
            )
        if not res.get("ok"):
            err = res.get("error") or {}
            await ws.close()
            self._ws = None
            raise GatewayError(
                err.get("code") or "connect_rejected",
                err.get("message") or "gateway rejected connect",
                err.get("details"),
            )
        self.hello = res.get("payload") or {}
        granted = (self.hello.get("auth") or {}).get("scopes") or []
        logger.info(
            "🟢 openclaw gateway connected protocol=%s connId=%s role=%s scopes=%s",
            self.hello.get("protocol"),
            (self.hello.get("server") or {}).get("connId"),
            (self.hello.get("auth") or {}).get("role"),
            granted,
        )

        # Spawn the reader loop now that the handshake is done.
        self._reader_task = asyncio.create_task(self._reader_loop())
        self._connected_event.set()

    async def close(self) -> None:
        """Close the connection cleanly. Pending RPCs are failed."""
        ws, reader = self._ws, self._reader_task
        # Drop in-process state first so subscribers / pending RPCs see
        # the disconnect promptly even if the WS close hangs.
        self._reset_state_after_disconnect()
        if reader is not None and not reader.done():
            reader.cancel()
            try:
                await reader
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        if ws is not None and not ws.closed:
            try:
                await ws.close()
            except Exception:  # noqa: BLE001
                pass

    def _reset_state_after_disconnect(self) -> None:
        """Synchronous state cleanup: fail pending RPCs, push end-of-
        stream sentinels to subscribers, clear maps. SAFE to call from
        inside the reader task's finally block — does NOT await on
        self._reader_task (B1 fix: that path would have the reader
        awaiting itself, which deadlocks).
        """
        self._connected_event.clear()
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(
                    GatewayError("disconnected", "gateway connection closed")
                )
        self._pending.clear()
        # B7 fix: snapshot the subscription map BEFORE iterating so a
        # subscriber's finally block (calling discard + pop) cannot
        # mutate the dict mid-iteration. B3 fix: try/except QueueFull
        # so a backed-up subscriber doesn't block the rest from learning
        # the connection died.
        snapshot = [(k, list(qs)) for k, qs in self._subscriptions.items()]
        for _key, qs in snapshot:
            for q in qs:
                try:
                    q.put_nowait(None)
                except asyncio.QueueFull:
                    # Subscriber is wedged. Best-effort: drop one item to
                    # make room for the sentinel so the iterator can exit.
                    try:
                        q.get_nowait()
                        q.put_nowait(None)
                    except (asyncio.QueueEmpty, asyncio.QueueFull):
                        pass
        self._subscriptions.clear()
        self._ws = None
        self.hello = None
        # NOTE: _reader_task is NOT cleared here. The reader's own
        # `finally` calls this function; clearing the ref while the
        # reader is still inside this function would race with close().
        # The reader sets itself to None via close(), or it's dropped
        # by GC after the task ends.

    # ---- reader loop -------------------------------------------------
    async def _reader_loop(self) -> None:
        ws = self._ws
        if ws is None:
            return
        try:
            async for raw in ws:
                try:
                    frame = json.loads(raw) if isinstance(raw, str) else json.loads(
                        raw.decode("utf-8", errors="replace")
                    )
                except (json.JSONDecodeError, AttributeError):
                    logger.debug("ignoring non-JSON frame from gateway: %r", raw[:200])
                    continue
                await self._dispatch_frame(frame)
        except ConnectionClosed as e:
            logger.warning("gateway WebSocket closed: code=%s reason=%s", e.code, e.reason)
        except asyncio.CancelledError:
            # Explicit cancel from close(); propagate so the awaiter sees it.
            raise
        except Exception:  # noqa: BLE001
            logger.exception("gateway reader loop crashed")
        finally:
            # B1 fix: synchronous cleanup only — must NOT await on the
            # reader task here (we ARE the reader task). Pending RPC
            # futures are failed and subscribers are sent the
            # end-of-stream sentinel; callers that wait on the reader
            # do so via close().
            self._reset_state_after_disconnect()

    async def _dispatch_frame(self, frame: dict[str, Any]) -> None:
        if not isinstance(frame, dict):
            return
        ftype = frame.get("type")
        if ftype == "res":
            fid = frame.get("id")
            fut = self._pending.pop(fid, None) if fid else None
            if fut is None or fut.done():
                return
            if frame.get("ok"):
                fut.set_result(frame.get("payload"))
            else:
                err = frame.get("error") or {}
                fut.set_exception(
                    GatewayError(
                        err.get("code") or "rpc_error",
                        err.get("message") or "gateway returned error",
                        err.get("details"),
                    )
                )
            return
        if ftype == "event":
            event_name = frame.get("event") or ""
            payload = frame.get("payload") or {}
            # session.message / session.tool / sessions.changed all carry
            # sessionKey on the payload — fan out to subscribers of that
            # key. Other events are dropped (we don't subscribe to them).
            session_key = (
                payload.get("sessionKey")
                if isinstance(payload, dict)
                else None
            )
            if not session_key:
                return
            queues = self._subscriptions.get(session_key)
            if not queues:
                return
            for q in list(queues):
                try:
                    q.put_nowait((event_name, payload))
                except asyncio.QueueFull:
                    logger.warning(
                        "subscriber queue full for sessionKey=%s; dropping %s event",
                        session_key, event_name,
                    )
            return
        # Unknown frame type — ignore, but log at debug.
        logger.debug("unknown frame type from gateway: %r", frame)

    # ---- RPC ---------------------------------------------------------
    async def call(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> Any:
        """Send an RPC and await its response payload."""
        if self._ws is None or self._ws.closed:
            raise GatewayNotConnectedError(
                "gateway connection is not open; call connect() first"
            )
        rpc_id = uuid.uuid4().hex
        req = {"type": "req", "id": rpc_id, "method": method, "params": params or {}}
        # B6 fix: get_running_loop is the modern accessor; the deprecated
        # get_event_loop fallback creates a NEW loop in some asyncio
        # configurations on 3.12+ which would orphan the future from the
        # task awaiting it.
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[rpc_id] = fut
        try:
            await self._ws.send(json.dumps(req))
        except Exception:
            self._pending.pop(rpc_id, None)
            raise
        try:
            return await asyncio.wait_for(
                fut, timeout=timeout if timeout is not None else self._rpc_timeout,
            )
        except asyncio.TimeoutError as e:
            self._pending.pop(rpc_id, None)
            raise GatewayError(
                "rpc_timeout",
                f"no response to {method} within {self._rpc_timeout:.0f}s",
            ) from e

    # ---- chat.* wrappers --------------------------------------------
    async def chat_send(
        self,
        *,
        session_key: str,
        message: str,
        idempotency_key: str,
        thinking: str | None = None,
        attachments: list[Any] | None = None,
        timeout_ms: int | None = None,
    ) -> dict[str, Any]:
        """Send a user message via OpenClaw's native ``chat.send``.

        OpenClaw decides everything from here — whether to start a fresh
        run, queue against an active run, or steer mid-flight (controlled
        by the configured ``messages.queue.mode``). We don't decide.

        ``originating*`` fields are intentionally NOT sent — those
        require operator.admin scope and are for external-channel relays
        (Telegram bot forwarding a user account's message). We're a
        first-party backend channel.
        """
        if not session_key or len(session_key) > CHAT_SEND_SESSION_KEY_MAX_LENGTH:
            raise ValueError(
                f"session_key must be 1..{CHAT_SEND_SESSION_KEY_MAX_LENGTH} chars"
            )
        if not idempotency_key:
            raise ValueError("idempotency_key is required by chat.send")
        params: dict[str, Any] = {
            "sessionKey": session_key,
            "message": message,
            "idempotencyKey": idempotency_key,
        }
        if thinking is not None:
            params["thinking"] = thinking
        if attachments is not None:
            params["attachments"] = attachments
        if timeout_ms is not None:
            params["timeoutMs"] = timeout_ms
        payload = await self.call("chat.send", params)
        return payload if isinstance(payload, dict) else {}

    async def chat_abort(
        self, *, session_key: str, run_id: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"sessionKey": session_key}
        if run_id:
            params["runId"] = run_id
        payload = await self.call("chat.abort", params)
        return payload if isinstance(payload, dict) else {}

    async def chat_history(
        self,
        *,
        session_key: str,
        limit: int | None = None,
        max_chars: int | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"sessionKey": session_key}
        if limit is not None:
            params["limit"] = limit
        if max_chars is not None:
            params["maxChars"] = max_chars
        payload = await self.call("chat.history", params)
        return payload if isinstance(payload, dict) else {}

    # ---- subscriptions ----------------------------------------------
    async def subscribe(
        self, session_key: str, *, queue_size: int = 1024,
    ) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        """Subscribe to ``session.message`` / ``session.tool`` /
        ``sessions.changed`` events for ``session_key``.

        Returns an async iterator yielding ``(event_name, payload)``
        tuples until the caller breaks out of the loop OR the WebSocket
        closes (in which case the iterator ends cleanly).

        B2 fix: registration (queue + RPC) happens BEFORE we return the
        iterator. Callers can rely on "this awaitable resolved → I'm
        subscribed" — without this, ``subscribe(...)`` returned an
        un-iterated async-generator and registration would only happen
        on the first ``__anext__``, racing any chat.send the caller
        issued in between.
        """
        q: asyncio.Queue = asyncio.Queue(maxsize=queue_size)
        self._subscriptions.setdefault(session_key, set()).add(q)
        try:
            await self.call(
                "sessions.messages.subscribe", {"key": session_key},
            )
        except GatewayError:
            self._subscriptions.get(session_key, set()).discard(q)
            raise

        async def _iter() -> AsyncIterator[tuple[str, dict[str, Any]]]:
            try:
                while True:
                    item = await q.get()
                    if item is None:
                        return  # connection closed
                    yield item
            finally:
                queues = self._subscriptions.get(session_key)
                if queues is not None:
                    queues.discard(q)
                    if not queues:
                        self._subscriptions.pop(session_key, None)
                        # Best-effort unsubscribe — if the connection is
                        # already dead this just logs.
                        try:
                            await self.call(
                                "sessions.messages.unsubscribe",
                                {"key": session_key},
                                timeout=5.0,
                            )
                        except Exception:  # noqa: BLE001
                            pass

        return _iter()

    @property
    def is_connected(self) -> bool:
        return self._ws is not None and not self._ws.closed
