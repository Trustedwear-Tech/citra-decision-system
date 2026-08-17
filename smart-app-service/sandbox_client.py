# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""HTTP client for action-sandbox-host.

smart-app-service uses this to spawn ephemeral builder pods (profile
`app-builder`) that turn a BA goal into AppSpec + AgentSpec JSON.

The wire contract matches action-sandbox-host/models.py — keep in sync.

Stability:
  Wrapped in a per-process circuit breaker so a hung sandbox host fails
  fast (after 3 consecutive transport-level failures) instead of
  starving the gunicorn worker. The breaker recovers automatically:
  one trial call is permitted after 30 s, and one success → CLOSED.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Literal, Optional

import httpx
from citra_service_utils import CircuitBreaker, CircuitBreakerOpen

from config import Settings

logger = logging.getLogger(__name__)

# Pod profiles that action-sandbox-host knows how to spawn. Keep in sync
# with action-sandbox-host/models.py — typos here would silently fail
# at the host with a confusing 400.
SandboxProfile = Literal["app-builder"]
_BUILDER_PROFILE: SandboxProfile = "app-builder"


class SandboxHostError(RuntimeError):
    """Raised when the sandbox host returns a non-2xx response."""


class SandboxHostUnavailable(SandboxHostError):
    """Raised when the breaker is OPEN — sandbox host has failed
    repeatedly and we're failing fast for the recovery window."""


# One breaker PER HOST — keyed by control URL. With a multi-host fleet a
# single bad host failing 3× must NOT fast-fail spawns onto the other,
# healthy hosts, so the breaker can't be a process-wide singleton.
# failure_threshold=3 + recovery=30s means a sustained outage on one host
# trips after ~3 attempts and recovers within 30 s of that host coming back.
_BREAKERS: Dict[str, CircuitBreaker] = {}


def _breaker_for(host_base: str) -> CircuitBreaker:
    b = _BREAKERS.get(host_base)
    if b is None:
        b = CircuitBreaker(
            name=f"action-sandbox-host[{host_base}]",
            failure_threshold=3,
            recovery_timeout=30.0,
            expected_exception=(httpx.HTTPError, SandboxHostError),
        )
        _BREAKERS[host_base] = b
    return b


class SandboxClient:
    def __init__(self, settings: Settings, timeout_seconds: float = 90.0):
        self._settings = settings
        self._timeout = timeout_seconds

    # ---- host fleet ---------------------------------------------------
    def _hosts(self) -> list[str]:
        """Configured host control URLs, normalised (no trailing slash).

        ``sandbox_host_url`` may be a comma-separated list; a single value
        yields a one-element list (legacy behaviour).
        """
        raw = self._settings.sandbox_host_url or ""
        hosts = [h.strip().rstrip("/") for h in raw.split(",") if h.strip()]
        if not hosts:
            raise SandboxHostError("SANDBOX_HOST_URL is not configured.")
        return hosts

    def _headers(self) -> Dict[str, str]:
        if not self._settings.sandbox_host_secret:
            raise SandboxHostError(
                "SANDBOX_HOST_SECRET is not configured; cannot reach sandbox-host."
            )
        return {
            "X-Sandbox-Host-Secret": self._settings.sandbox_host_secret,
            "Content-Type": "application/json",
        }

    async def _builder_headroom(self, host_base: str) -> int:
        """Best-effort: how many more builder pods this host can take.

        Reads GET /capacity and returns the app-builder tier's can_spawn
        (falling back to `remaining` slots). Any error → -1 so the ranker
        sorts this host last but STILL tries it for failover.
        """
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(
                    f"{host_base}/capacity", headers=self._headers()
                )
            if resp.status_code >= 400:
                return -1
            body = resp.json()
            free = body.get("free_ram_bytes", -1)
            # Prefer free RAM as the rank key when known; else slot count.
            if isinstance(free, (int, float)) and free >= 0:
                return int(free)
            return int(body.get("remaining", -1))
        except (httpx.HTTPError, ValueError):
            return -1

    async def _ranked_hosts(self) -> list[str]:
        """Hosts ordered best-first for a spawn. Single host → no probe."""
        hosts = self._hosts()
        if len(hosts) == 1:
            return hosts
        scored = [(await self._builder_headroom(h), h) for h in hosts]
        # Highest headroom first; unknown (-1) sinks to the bottom but is
        # still present so failover can reach it.
        scored.sort(key=lambda t: t[0], reverse=True)
        return [h for _, h in scored]

    async def spawn_builder(
        self,
        *,
        session_id: str,
        user_id: str,
        env: Dict[str, str],
        labels: Optional[Dict[str, str]] = None,
        tier: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Spawn an `app-builder` pod and return the sandbox-host response.

        The returned dict contains: session_id, container_id, container_name,
        host_port, adapter_url, public_host — PLUS ``sandbox_host_base``: the
        control URL the pod actually landed on. Callers MUST persist that so
        ``session_alive`` / ``stop_session`` target the right host later.

        Across a multi-host fleet we capacity-rank the hosts and try them in
        order, failing over past any that are full (503) or unreachable. Each
        host has its own circuit breaker, so one dead host can't short-circuit
        spawns onto the healthy ones. ``SandboxHostUnavailable`` is raised only
        when EVERY host is breaker-open or failing.
        """
        payload = {
            "user_id": user_id,
            "session_id": session_id,
            "env": env,
            "labels": labels or {},
            "profile": _BUILDER_PROFILE,
            "tier": (tier or "quick"),
        }

        async def _spawn_on(host_base: str) -> Dict[str, Any]:
            url = f"{host_base}/spawn"
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                try:
                    resp = await client.post(url, json=payload, headers=self._headers())
                except httpx.HTTPError as e:
                    raise SandboxHostError(f"sandbox-host unreachable: {e}") from e
            if resp.status_code >= 400:
                raise SandboxHostError(
                    f"sandbox-host /spawn failed ({resp.status_code}): {resp.text}"
                )
            body = resp.json()
            body["sandbox_host_base"] = host_base
            return body

        hosts = await self._ranked_hosts()
        last_exc: Exception | None = None
        for host_base in hosts:
            try:
                return await _breaker_for(host_base).call(
                    lambda hb=host_base: _spawn_on(hb)
                )
            except CircuitBreakerOpen as exc:
                logger.warning("sandbox spawn short-circuited on %s: %s", host_base, exc)
                last_exc = SandboxHostUnavailable(str(exc))
                continue
            except SandboxHostError as exc:
                logger.warning("sandbox spawn failed on %s, trying next host: %s",
                               host_base, exc)
                last_exc = exc
                continue
        # Every host refused or was unreachable.
        if isinstance(last_exc, SandboxHostUnavailable):
            raise last_exc
        raise SandboxHostError(
            f"all {len(hosts)} sandbox host(s) failed to spawn builder: {last_exc}"
        )

    async def session_alive(
        self, session_id: str, *, host_base: Optional[str] = None
    ) -> bool:
        """True iff the host reports a still-running pod for this session.

        Used by the builder-session reuse path to decide whether to
        reattach to an existing pod or spawn a fresh one. A host that
        answers ``alive=false`` (or 404) means the pod is gone — return
        False. A host that is unreachable / errors is NOT the same as
        "pod dead": we raise ``SandboxHostError`` so the caller fails loud
        rather than silently spawning a duplicate.

        ``host_base`` targets the specific host the session landed on (from
        the persisted ``sandbox_host_base``). Legacy docs without it fall
        back to probing every configured host — alive on ANY is alive.
        """
        targets = [host_base.rstrip("/")] if host_base else self._hosts()

        async def _alive_on(base: str) -> bool:
            url = f"{base}/session/{session_id}"
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                try:
                    resp = await client.get(url, headers=self._headers())
                except httpx.HTTPError as e:
                    raise SandboxHostError(f"sandbox-host unreachable: {e}") from e
            if resp.status_code == 404:
                return False
            if resp.status_code >= 400:
                raise SandboxHostError(
                    f"sandbox-host session status failed ({resp.status_code}): {resp.text}"
                )
            return bool(resp.json().get("alive", False))

        last_exc: Exception | None = None
        for base in targets:
            try:
                if await _breaker_for(base).call(lambda b=base: _alive_on(b)):
                    return True
                # Definitive alive=false on this host; keep checking others
                # only in the fan-out (no host_base) case.
            except CircuitBreakerOpen as exc:
                last_exc = SandboxHostUnavailable(str(exc))
                continue
            except SandboxHostError as exc:
                last_exc = exc
                continue
        # No host reported alive=true. If we also failed to REACH a host
        # (targeted or fan-out), liveness is indeterminate — fail loud so
        # the caller doesn't silently spawn a duplicate.
        if last_exc is not None:
            raise last_exc
        return False

    async def stop_session(
        self, session_id: str, *, host_base: Optional[str] = None
    ) -> bool:
        """Stop a running builder pod. Returns True if a host found it.

        Breaker-wrapped per host so a hung host doesn't block teardown
        beyond its fast-fail window. ``host_base`` targets the session's
        host; without it we issue the DELETE to every configured host
        (only one owns the pod; the rest 404 harmlessly).

        Returns True if a host stopped the pod, False if a host
        definitively reported it gone (404). Raises ``SandboxHostError``
        (preserving the original contract) when NO host could be reached
        and nothing was stopped — so callers (e.g. the idle sweep) leave
        the session ACTIVE and retry rather than marking a possibly-live
        pod dead.
        """
        targets = [host_base.rstrip("/")] if host_base else self._hosts()

        async def _stop_on(base: str) -> bool:
            url = f"{base}/session/{session_id}"
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                try:
                    resp = await client.delete(url, headers=self._headers())
                except httpx.HTTPError as e:
                    raise SandboxHostError(f"sandbox-host unreachable: {e}") from e
            if resp.status_code == 404:
                return False
            if resp.status_code >= 400:
                raise SandboxHostError(
                    f"sandbox-host stop failed ({resp.status_code}): {resp.text}"
                )
            return bool(resp.json().get("ok", False))

        stopped = False
        last_exc: Exception | None = None
        for base in targets:
            try:
                if await _breaker_for(base).call(lambda b=base: _stop_on(b)):
                    stopped = True
            except CircuitBreakerOpen as exc:
                logger.warning("sandbox stop short-circuited on %s: %s", base, exc)
                last_exc = SandboxHostUnavailable(str(exc))
                continue
            except SandboxHostError as exc:
                logger.warning("sandbox stop failed on %s: %s", base, exc)
                last_exc = exc
                continue
        if stopped:
            return True
        # Nothing was stopped. If that's because a host was unreachable
        # (not a definitive 404), fail loud so the caller retries.
        if last_exc is not None:
            raise last_exc
        return False
