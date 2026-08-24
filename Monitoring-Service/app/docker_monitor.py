# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""docker_monitor.py — MONITOR + REPORT ONLY.

This module observes Docker containers and sends forensic alerts.
It does NOT restart, kill, stop, pause, or otherwise modify any container.

Two monitoring modes:
  1. Healthcheck monitor  — alerts when a container becomes unhealthy.
  2. Restart detector     — detects externally-triggered restarts and
                            classifies their likely cause using available
                            evidence (exit code, OOMKilled, timing, labels).
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

import psutil
import docker

from .config import DockerConfig
from .alert_manager import AlertManager

logger = logging.getLogger(__name__)

_ZERO_TS = "0001-01-01T00:00:00Z"  # Docker's null timestamp sentinel


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #

def _parse_dt(ts: str) -> Optional[datetime]:
    """Parse a Docker ISO8601 timestamp. Returns None for null/zero/bad input."""
    if not ts or ts.startswith("0001"):
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _classify_restart_cause(
    prev: Dict[str, Any],
    curr: Dict[str, Any],
    host_boot: datetime,
) -> Tuple[str, str, str]:
    """
    Return (cause_label, confidence, evidence_text).

    Uses the state snapshot from the PREVIOUS poll (``prev``) plus the
    current post-restart state (``curr``) to classify why the container
    restarted.  Confidence levels: HIGH | MEDIUM | LOW.
    """
    # --- OOMKilled (most definitive signal) ---
    if prev.get("oom_killed") or curr.get("oom_killed"):
        which = "prev" if prev.get("oom_killed") else "curr"
        exit_code = prev.get("exit_code") if which == "prev" else curr.get("exit_code")
        return (
            "OOM Kill — container ran out of memory",
            "HIGH",
            f"OOMKilled=true captured in {which} poll, ExitCode={exit_code}",
        )

    # --- ExitCode from previous poll (captured before restart cleared it) ---
    exit_code = prev.get("exit_code")
    if exit_code == 137:
        return (
            "SIGKILL (ExitCode=137) — forcible kill; possible cgroups OOM, manual kill, or daemon restart",
            "MEDIUM",
            "ExitCode=137 captured in previous poll. Confirm with: dmesg | grep -i oom",
        )
    if exit_code == 143:
        return (
            "SIGTERM (ExitCode=143) — graceful shutdown; likely docker compose recreate / manual stop",
            "MEDIUM",
            "ExitCode=143 captured in previous poll. Common during: docker compose up / docker stop",
        )
    if exit_code == 1:
        return (
            "Application crash — process exited with error code 1",
            "MEDIUM",
            "ExitCode=1 captured in previous poll. Check application logs above.",
        )
    if exit_code is not None and exit_code != 0:
        return (
            f"Application exit — ExitCode={exit_code} captured before restart",
            "MEDIUM",
            f"Non-zero ExitCode={exit_code}",
        )

    # --- Host-reboot detection via container StartedAt vs host boot time ---
    started_at = _parse_dt(curr.get("started_at"))
    if started_at is not None:
        if started_at <= host_boot:
            return (
                "Host reboot — container StartedAt is at or before host boot time",
                "HIGH",
                f"StartedAt={curr['started_at']!r} <= host_boot={host_boot.isoformat()}",
            )
        boot_gap_s = (started_at - host_boot).total_seconds()
        if 0 <= boot_gap_s < 120:
            return (
                "Host reboot — container started within 120 s of host boot",
                "MEDIUM",
                f"StartedAt={curr['started_at']!r}, host_boot={host_boot.isoformat()}, gap={boot_gap_s:.0f}s",
            )

    # --- FinishedAt → StartedAt gap as timing evidence ---
    finished_at = _parse_dt(curr.get("finished_at"))
    if started_at is not None and finished_at is not None:
        gap_s = (started_at - finished_at).total_seconds()
        if 0 < gap_s < 5:
            return (
                "Docker restart policy — automatic restart by Docker engine",
                "MEDIUM",
                f"FinishedAt→StartedAt gap={gap_s:.1f}s (< 5 s) indicates automatic restart",
            )
        if gap_s >= 5:
            return (
                f"Restart after {gap_s:.0f}s gap — likely manual restart or compose recreate",
                "LOW",
                f"FinishedAt={curr.get('finished_at')!r}, StartedAt={curr.get('started_at')!r}, gap={gap_s:.0f}s",
            )

    # --- Compose config-hash change → deployment ---
    labels = curr.get("labels") or {}
    prev_labels = prev.get("labels") or {}
    curr_hash = labels.get("com.docker.compose.config-hash", "")
    prev_hash = prev_labels.get("com.docker.compose.config-hash", "")
    if curr_hash and prev_hash and curr_hash != prev_hash:
        return (
            "Compose deployment — config-hash changed between polls",
            "HIGH",
            f"compose config-hash: {prev_hash!r} → {curr_hash!r}",
        )

    # --- Clean exit ---
    if exit_code == 0:
        return (
            "Planned shutdown (ExitCode=0) — compose recreate or manual restart",
            "MEDIUM",
            "Container exited cleanly (exit 0) before being restarted",
        )

    return (
        "Unknown — insufficient evidence",
        "LOW",
        (
            "ExitCode and OOMKilled were not captured before restart. "
            "Run: docker inspect, journalctl -u docker --since='1 hour ago'"
        ),
    )


# --------------------------------------------------------------------------- #
# DockerMonitor                                                                #
# --------------------------------------------------------------------------- #

class DockerMonitor:
    """
    MONITOR + REPORT ONLY.

    Detects container health degradation and external restarts, then sends
    detailed forensic alerts.  This class NEVER restarts, kills, stops,
    pauses, or otherwise mutates any container.
    """

    def __init__(self, config: DockerConfig, alert_manager: AlertManager) -> None:
        self.config = config
        self.alert_manager = alert_manager
        self._stop = threading.Event()

        # Attempt to connect to the docker daemon; degrade gracefully if unavailable
        try:
            self.client = docker.from_env()
            logger.info(
                "DockerMonitor initialized (MONITOR ONLY — no restart). "
                "check_interval=%ss, grace_period=%ss",
                self.config.check_interval_seconds,
                self.config.startup_grace_period_seconds,
            )
        except Exception as exc:
            self.client = None
            logger.error(
                "DockerMonitor: cannot connect to Docker daemon — docker monitoring disabled. "
                "Ensure /var/run/docker.sock is mounted. Error: %s",
                exc,
            )

        # container name → last-seen state snapshot
        self._known: Dict[str, Dict[str, Any]] = {}
        # names for which an "unhealthy" alert has already been sent
        # (reset when the container returns to healthy)
        self._alerted_unhealthy: set = set()
        # True after the first full poll completes (prevents false alarms on startup)
        self._initialized = False

    # ------------------------------------------------------------------ #
    # Main loop                                                            #
    # ------------------------------------------------------------------ #

    def start(self) -> None:
        if self.client is None:
            logger.warning("DockerMonitor: docker daemon unavailable — monitor loop not started")
            return
        logger.info("DockerMonitor started")
        while not self._stop.is_set():
            try:
                self._poll_once()
            except Exception as exc:
                logger.error("Unhandled error in DockerMonitor poll: %s", exc, exc_info=True)
            self._stop.wait(self.config.check_interval_seconds)

    def _poll_once(self) -> None:
        if self.client is None:
            return
        try:
            containers = self.client.containers.list(all=True)
        except Exception as exc:
            logger.error("Failed to list containers: %s", exc)
            return

        host_boot = datetime.fromtimestamp(psutil.boot_time(), tz=timezone.utc)

        seen_names: set = set()
        for container in containers:
            try:
                self._process_container(container, host_boot)
                seen_names.add(container.name)
            except Exception as exc:
                logger.error(
                    "Error processing container %s: %s", container.name, exc, exc_info=True
                )

        if not self._initialized:
            logger.info(
                "DockerMonitor initial state captured for %d container(s). Monitoring active.",
                len(seen_names),
            )
            self._initialized = True

        # Clean up state for containers that no longer exist
        gone = set(self._known.keys()) - seen_names
        for name in gone:
            logger.info("Container no longer visible, removing from state: %s", name)
            self._known.pop(name, None)
            self._alerted_unhealthy.discard(name)

    def _process_container(self, container, host_boot: datetime) -> None:
        container.reload()
        attrs = container.attrs
        state = attrs.get("State", {})
        health = state.get("Health")
        name = container.name
        cid = container.short_id
        image = attrs.get("Config", {}).get("Image", "unknown")
        labels = attrs.get("Config", {}).get("Labels") or {}
        restart_policy = attrs.get("HostConfig", {}).get("RestartPolicy", {})

        curr_snapshot: Dict[str, Any] = {
            "name": name,
            "container_id": cid,
            "restart_count": state.get("RestartCount", 0),
            "status": state.get("Status", ""),
            "exit_code": state.get("ExitCode"),
            "oom_killed": state.get("OOMKilled", False),
            "started_at": state.get("StartedAt", ""),
            "finished_at": state.get("FinishedAt", ""),
            "health_status": (health or {}).get("Status", ""),
            "labels": labels,
        }

        prev_snapshot = self._known.get(name)

        # ---- First time we see this container ---- #
        if prev_snapshot is None or not self._initialized:
            self._known[name] = curr_snapshot
            logger.debug(
                "Registered container: %s (%s), status=%s, restart_count=%d",
                name, cid, curr_snapshot["status"], curr_snapshot["restart_count"],
            )
            return

        # ---- Detect external restart ---- #
        # A *recreation* (deploy: `docker compose up` replaces the container)
        # gets a brand-new container ID and resets RestartCount to 0. That is a
        # routine redeploy, not a crash-restart, and must NOT page. A genuine
        # in-place restart (crash / OOM / `docker restart`) keeps the same
        # container ID, so we only treat a StartedAt change as a restart when the
        # container ID is unchanged. Without this guard every deploy emails a
        # bogus "Restart detected … restart_count 0 → 0" for every container.
        recreated = (
            prev_snapshot.get("container_id") is not None
            and curr_snapshot["container_id"] != prev_snapshot.get("container_id")
        )
        started_changed = (
            curr_snapshot["started_at"]
            and curr_snapshot["started_at"] != prev_snapshot["started_at"]
            and curr_snapshot["started_at"] != _ZERO_TS
        )
        restart_detected = (not recreated) and (
            curr_snapshot["restart_count"] > prev_snapshot["restart_count"]
            or started_changed
        )

        if recreated:
            logger.info(
                "Redeploy detected (not a restart): %s  container_id %s → %s  "
                "restart_count %d → %d — suppressing restart alert",
                name, prev_snapshot.get("container_id"), curr_snapshot["container_id"],
                prev_snapshot["restart_count"], curr_snapshot["restart_count"],
            )
            # A fresh container can legitimately go unhealthy later; allow it.
            self._alerted_unhealthy.discard(name)

        if restart_detected:
            logger.info(
                "Restart detected: %s (%s)  restart_count %d → %d  started_at: %r → %r",
                name, cid,
                prev_snapshot["restart_count"], curr_snapshot["restart_count"],
                prev_snapshot["started_at"], curr_snapshot["started_at"],
            )
            cause, confidence, evidence = _classify_restart_cause(
                prev_snapshot, curr_snapshot, host_boot
            )
            self._send_restart_alert(
                container, attrs, state, health, curr_snapshot, prev_snapshot,
                cause, confidence, evidence,
                host_boot, image, restart_policy, labels,
            )
            # Reset so a new unhealthy state after this restart fires a fresh alert
            self._alerted_unhealthy.discard(name)

        # ---- Detect unhealthy healthcheck ---- #
        if health:
            health_status = health.get("Status", "")
            if health_status == "unhealthy" and name not in self._alerted_unhealthy:
                started_at = _parse_dt(curr_snapshot["started_at"])
                now = datetime.now(timezone.utc)
                uptime_s = (now - started_at).total_seconds() if started_at else None
                if uptime_s is not None and uptime_s < self.config.startup_grace_period_seconds:
                    logger.info(
                        "Container %s is unhealthy but only %.0fs old (grace=%ds), skipping alert",
                        name, uptime_s, self.config.startup_grace_period_seconds,
                    )
                else:
                    logger.warning(
                        "Container unhealthy (Monitoring-Service will NOT restart it): %s (%s)",
                        name, cid,
                    )
                    self._send_unhealthy_alert(
                        container, attrs, state, health, curr_snapshot,
                        host_boot, image, restart_policy, labels,
                    )
                    self._alerted_unhealthy.add(name)

            elif health_status != "unhealthy" and name in self._alerted_unhealthy:
                logger.info("Container %s recovered from unhealthy state", name)
                self._alerted_unhealthy.discard(name)

        # ---- Persist current state ---- #
        self._known[name] = curr_snapshot

    # ------------------------------------------------------------------ #
    # Alert builders                                                       #
    # ------------------------------------------------------------------ #

    def _collect_logs(self, container, tail: int = 100) -> str:
        try:
            return container.logs(tail=tail).decode("utf-8", errors="replace")
        except Exception as exc:
            return f"(unable to retrieve logs: {exc})"

    def _format_healthcheck_log(self, health: Optional[dict]) -> str:
        if not health:
            return "  (no healthcheck configured)"
        logs = health.get("Log") or []
        if not logs:
            return "  (no healthcheck probe history)"
        lines = []
        for entry in logs[-5:]:  # last 5 probes
            ts = entry.get("Start", "")[:19]
            exit_c = entry.get("ExitCode", "?")
            output = (entry.get("Output") or "").strip()[:200]
            lines.append(f"  [{ts}] ExitCode={exit_c}  {output}")
        return "\n".join(lines)

    def _format_host_context(self, host_boot: datetime) -> str:
        now = datetime.now(timezone.utc)
        uptime_s = (now - host_boot).total_seconds()
        h = int(uptime_s // 3600)
        m = int((uptime_s % 3600) // 60)
        mem = psutil.virtual_memory()
        cpu = psutil.cpu_percent(interval=None)
        return (
            f"  Host uptime:   {h}h {m}m  (boot: {host_boot.strftime('%Y-%m-%d %H:%M:%S UTC')})\n"
            f"  CPU usage:     {cpu:.1f}%\n"
            f"  Memory:        {mem.percent:.1f}%  "
            f"({mem.used // 1024 // 1024} MB used / {mem.total // 1024 // 1024} MB total)"
        )

    def _send_restart_alert(
        self,
        container,
        attrs: dict,
        state: dict,
        health: Optional[dict],
        curr: Dict[str, Any],
        prev: Dict[str, Any],
        cause: str,
        confidence: str,
        evidence: str,
        host_boot: datetime,
        image: str,
        restart_policy: dict,
        labels: dict,
    ) -> None:
        name = container.name
        cid = container.short_id
        log_output = self._collect_logs(container, tail=100)
        prev_restart = prev.get("restart_count", 0)
        curr_restart = curr.get("restart_count", 0)
        compose_project = labels.get("com.docker.compose.project", "")
        compose_service = labels.get("com.docker.compose.service", "")

        subject = f"[Docker Alert] Restart detected: {name}  (total restarts={curr_restart})"
        body = (
            "=" * 70 + "\n"
            "RESTART DETECTED — Container restarted externally\n"
            "(Monitoring-Service does NOT restart containers)\n"
            "=" * 70 + "\n\n"

            "[CONTAINER INFO]\n"
            f"  Name:            {name}\n"
            f"  Short ID:        {cid}\n"
            f"  Image:           {image}\n"
            + (f"  Compose project: {compose_project} / service: {compose_service}\n"
               if compose_project else "")
            + f"  Restart policy:  {restart_policy.get('Name', 'unknown')}"
              f"  (max={restart_policy.get('MaximumRetryCount', 0)})\n\n"

            "[RESTART EVENT]\n"
            f"  Restart count:   {prev_restart} → {curr_restart}\n"
            f"  Previous start:  {prev.get('started_at', 'unknown')!r}\n"
            f"  New start:       {curr.get('started_at', 'unknown')!r}\n"
            f"  Finished at:     {curr.get('finished_at', 'unknown')!r}\n\n"

            "[LIKELY CAUSE]\n"
            f"  {cause}\n"
            f"  Confidence:  {confidence}\n\n"

            "[EVIDENCE]\n"
            f"  {evidence}\n\n"

            "[FORENSIC STATE (captured at detection time)]\n"
            f"  Exit code (prev poll):  {prev.get('exit_code')}\n"
            f"  OOMKilled (prev poll):  {prev.get('oom_killed', False)}\n"
            f"  Current status:         {curr.get('status')}\n"
            f"  Current exit code:      {curr.get('exit_code')}\n"
            f"  Current OOMKilled:      {curr.get('oom_killed', False)}\n\n"

            "[HEALTHCHECK PROBE HISTORY (last 5)]\n"
            + self._format_healthcheck_log(health) + "\n\n"

            "[HOST CONTEXT]\n"
            + self._format_host_context(host_boot) + "\n\n"

            "[LAST 100 LOG LINES]\n"
            + log_output + "\n\n"

            "[RECOMMENDED ACTION]\n"
            f"  1. Verify container is stable:   docker ps | grep {name}\n"
            f"  2. Full forensic inspect:         docker inspect {name}\n"
            f"  3. Full log review:               docker logs --tail 500 {name}\n"
            f"  4. Check host OOM events:         dmesg | grep -i oom\n"
            f"  5. Check Docker daemon log:       journalctl -u docker --since='1 hour ago'\n"
            f"  6. Review log lines above for the error that preceded shutdown\n"
        )

        self.alert_manager.send_alert(
            source=name,
            alert_type="container_restarted",
            subject=subject,
            body=body,
        )

    def _send_unhealthy_alert(
        self,
        container,
        attrs: dict,
        state: dict,
        health: Optional[dict],
        curr: Dict[str, Any],
        host_boot: datetime,
        image: str,
        restart_policy: dict,
        labels: dict,
    ) -> None:
        name = container.name
        cid = container.short_id
        log_output = self._collect_logs(container, tail=100)
        restart_count = curr.get("restart_count", 0)
        compose_project = labels.get("com.docker.compose.project", "")
        compose_service = labels.get("com.docker.compose.service", "")

        subject = f"[Docker Alert] Container unhealthy: {name}  (restarts={restart_count})"
        body = (
            "=" * 70 + "\n"
            "UNHEALTHY CONTAINER ALERT\n"
            "Monitoring-Service will NOT restart this container.\n"
            "Manual intervention is required.\n"
            "=" * 70 + "\n\n"

            "[CONTAINER INFO]\n"
            f"  Name:            {name}\n"
            f"  Short ID:        {cid}\n"
            f"  Image:           {image}\n"
            + (f"  Compose project: {compose_project} / service: {compose_service}\n"
               if compose_project else "")
            + f"  Restart policy:  {restart_policy.get('Name', 'unknown')}"
              f"  (max={restart_policy.get('MaximumRetryCount', 0)})\n"
              f"  Total restarts:  {restart_count}\n\n"

            "[HEALTHCHECK STATUS]\n"
            f"  Status:          {(health or {}).get('Status', 'none')}\n"
            f"  Failing streak:  {(health or {}).get('FailingStreak', 0)}\n\n"

            "[HEALTHCHECK PROBE HISTORY (last 5)]\n"
            + self._format_healthcheck_log(health) + "\n\n"

            "[CURRENT STATE]\n"
            f"  Container status:  {curr.get('status')}\n"
            f"  Exit code:         {curr.get('exit_code')}\n"
            f"  OOMKilled:         {curr.get('oom_killed', False)}\n"
            f"  Started at:        {curr.get('started_at')!r}\n"
            f"  Finished at:       {curr.get('finished_at')!r}\n\n"

            "[HOST CONTEXT]\n"
            + self._format_host_context(host_boot) + "\n\n"

            "[LAST 100 LOG LINES]\n"
            + log_output + "\n\n"

            "[RECOMMENDED ACTION]\n"
            f"  1. Review log lines and healthcheck probes above\n"
            f"  2. Full forensic inspect:   docker inspect {name}\n"
            f"  3. If manual restart needed: docker restart {name}\n"
            f"  4. If crash-looping:         docker logs --tail 500 {name}\n"
            f"  5. Check host resources above (CPU / memory)\n"
        )

        self.alert_manager.send_alert(
            source=name,
            alert_type="container_unhealthy",
            subject=subject,
            body=body,
        )

    # ------------------------------------------------------------------ #
    # Lifecycle                                                            #
    # ------------------------------------------------------------------ #

    def stop(self) -> None:
        logger.info("Stopping DockerMonitor")
        self._stop.set()

    def run_in_thread(self) -> threading.Thread:
        t = threading.Thread(target=self.start, daemon=True)
        t.start()
        return t
