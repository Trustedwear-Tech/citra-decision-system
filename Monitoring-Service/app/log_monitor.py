# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

import logging
import os
import threading
import time
import hashlib
import re
from typing import Dict, List, Optional

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileSystemEvent

from .config import LogConfig
from .alert_manager import AlertManager

logger = logging.getLogger(__name__)


class LogEventHandler(FileSystemEventHandler):
    def __init__(self, log_config: LogConfig, alert_manager: AlertManager) -> None:
        super().__init__()
        self.log_config = log_config
        self.alert_manager = alert_manager

        # Track last read position for each log file
        self._positions: Dict[str, int] = {}
        # Lock that protects _positions only
        self._lock = threading.Lock()

        # --- Batching ---
        # Collected error lines per file, waiting for the debounce window to expire
        self._batch_buffers: Dict[str, List[str]] = {}
        # Active debounce timer per file
        self._batch_timers: Dict[str, threading.Timer] = {}
        # Lock that protects batch state
        self._batch_lock = threading.Lock()

        # --- Fingerprint cooldown ---
        # fingerprint -> timestamp of last email sent
        self._recent_fingerprints: Dict[str, float] = {}
        # Lock that protects fingerprint state
        self._fp_lock = threading.Lock()

        # Configuration shortcuts
        self.batch_window_seconds: int = log_config.batch_window_seconds
        self.fingerprint_cooldown_seconds: int = log_config.fingerprint_cooldown_seconds
        self.max_batch_lines: int = getattr(log_config, "max_batch_lines", 500)
        self.poll_interval_seconds: int = log_config.poll_interval_seconds

        # --- Polling ---
        self._poll_stop_event = threading.Event()
        self._poll_thread: Optional[threading.Thread] = None

    def on_modified(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        try:
            self._process_file(event.src_path)
        except Exception:
            logger.exception("Unhandled error in _process_file (on_modified)")

    def on_created(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        try:
            self._process_file(event.src_path)
        except Exception:
            logger.exception("Unhandled error in _process_file (on_created)")

    # ------------------------------------------------------------------ #
    # Polling loop (safety-net path)                                       #
    # ------------------------------------------------------------------ #

    def start_polling(self) -> None:
        """Start the background polling thread."""
        self._poll_stop_event.clear()
        self._poll_thread = threading.Thread(
            target=self._polling_loop,
            name="log-poll",
            daemon=True,
        )
        self._poll_thread.start()
        logger.info(
            "LogEventHandler polling loop started (interval=%ds)", self.poll_interval_seconds
        )

    def stop_polling(self) -> None:
        """Signal the polling thread to stop and wait for it to exit."""
        self._poll_stop_event.set()
        if self._poll_thread is not None:
            self._poll_thread.join(timeout=self.poll_interval_seconds + 5)
            self._poll_thread = None
        logger.info("LogEventHandler polling loop stopped")

    def _polling_loop(self) -> None:
        """Periodically call _poll_once until stop is requested."""
        while not self._poll_stop_event.wait(timeout=self.poll_interval_seconds):
            try:
                self._poll_once()
            except Exception:
                logger.exception("Unhandled error in _poll_once")

    def _poll_once(self) -> None:
        """
        Check every tracked log file for new bytes written since the last
        read position.  Uses the same _positions dict as the watchdog path —
        whichever path runs first consumes the bytes; the other sees nothing
        new, so there are no duplicate detections.
        """
        with self._lock:
            paths = list(self._positions.keys())

        for path in paths:
            if not path.endswith(".log"):
                continue
            if os.path.basename(path) == "monitoring-service.log":
                continue

            try:
                current_size = os.path.getsize(path)
            except OSError:
                continue

            with self._lock:
                last_pos = self._positions.get(path, 0)

            if current_size > last_pos:
                logger.debug(
                    "Poller detected growth in %s (%d → %d bytes)", path, last_pos, current_size
                )
                try:
                    self._process_file(path)
                except Exception:
                    logger.exception("Unhandled error in _process_file (poll)")

    def _process_file(self, path: str) -> None:
        if not path.endswith(".log"):
            return

        if os.path.basename(path) == "monitoring-service.log":
            return

        try:
            current_size = os.path.getsize(path)
            with self._lock:
                last_pos = self._positions.get(path, 0)

            # Handle truncation / log rotation
            if last_pos > current_size:
                logger.info(
                    "Log file truncated or rotated: %s (old_pos=%d, new_size=%d)",
                    path, last_pos, current_size
                )
                last_pos = 0

            with open(path, "rb") as f:
                f.seek(last_pos)
                new_data = f.read()
                with self._lock:
                    self._positions[path] = f.tell()

        except FileNotFoundError:
            logger.warning("Log file not found: %s", path)
            return
        except Exception as exc:
            logger.error("Error reading log file %s: %s", path, exc)
            return

        if not new_data:
            return

        lines = new_data.splitlines(keepends=True)
        i = 0

        while i < len(lines):
            line = lines[i].rstrip(b"\r\n").decode("utf-8", errors="ignore")

            if not self._is_error_line(line):
                i += 1
                continue

            # Start collecting block
            block_lines = [line]
            i += 1

            # Capture continuation lines (indented or no timestamp prefix)
            while i < len(lines):
                next_line = lines[i].rstrip(b"\r\n").decode("utf-8", errors="ignore")

                # Stop if next line looks like a new log entry
                if re.match(r'^\d{4}-\d{2}-\d{2}', next_line):
                    break

                block_lines.append(next_line)
                i += 1

            full_block = "\n".join(block_lines)

            logger.warning("Error block detected in %s: %s", path, block_lines[0].strip())

            with self._batch_lock:
                buf = self._batch_buffers.setdefault(path, [])
                buf.append(full_block)

                # Avoid unlimited memory growth during noisy logs
                if len(buf) > self.max_batch_lines:
                    buf.pop(0)

                # Cancel existing timer and start a fresh debounce window
                existing_timer = self._batch_timers.get(path)
                if existing_timer is not None:
                    existing_timer.cancel()

                timer = threading.Timer(
                    self.batch_window_seconds,
                    self._flush_batch_alert,
                    args=(path,),
                )
                self._batch_timers[path] = timer
                timer.start()

    def _flush_batch_alert(self, path: str) -> None:
        """
        Called when the debounce timer for *path* fires.
        Collects all buffered error lines, builds a single consolidated email,
        applies fingerprint-based cooldown, and sends via AlertManager.
        """
        with self._batch_lock:
            lines = self._batch_buffers.pop(path, [])
            self._batch_timers.pop(path, None)

        if not lines:
            return

        try:
            # Build batch fingerprint from all normalized lines
            service_folder = os.path.basename(os.path.dirname(path))
            normalized_lines = sorted(set(self._normalize_line(ln) for ln in lines))
            batch_key = "\n".join(f"{service_folder}:{n}" for n in normalized_lines)
            fingerprint = hashlib.sha1(batch_key.encode(), usedforsecurity=False).hexdigest()  # deduplication fingerprint, not security

            # Apply fingerprint cooldown
            now = time.time()
            with self._fp_lock:
                last_sent = self._recent_fingerprints.get(fingerprint)
                if last_sent is not None and now - last_sent < self.fingerprint_cooldown_seconds:
                    logger.info(
                        "Batch alert suppressed by fingerprint cooldown for %s "
                        "(fingerprint=%s, seconds_since_last=%.1f)",
                        path, fingerprint, now - last_sent,
                    )
                    return
                self._recent_fingerprints[fingerprint] = now

                # Prune stale fingerprints (older than 2× cooldown)
                cutoff = now - self.fingerprint_cooldown_seconds * 2
                for fp in [k for k, v in self._recent_fingerprints.items() if v < cutoff]:
                    del self._recent_fingerprints[fp]

            # Determine highest severity in the batch
            has_critical = any(" - critical - " in ln.lower() for ln in lines)
            has_error = any(" - error - " in ln.lower() for ln in lines)
            if has_critical or has_error:
                emoji, alert_level = "❌", "Error"
            else:
                emoji, alert_level = "⚠️", "Warning"

            filename = os.path.basename(path)
            # Show unique normalized lines count (otherwise duplicates inflate email)
            count = len(normalized_lines)
            subject = (
                f"{emoji} [Log Alert] Batch {alert_level.lower()}s detected "
                f"in {filename} ({count} issue{'s' if count != 1 else ''})"
            )

            context_block = self._extract_context_window(
                path,
                lines,   # pass full error blocks
                window=50,
            )

            body = (
                f"File: {path}\n"
                f"Context window: 50 lines before first error "
                f"and 50 lines after last error\n\n"
                f"{context_block}"
            )

            logger.info(
                "Sending batch alert for %s: %d line(s), fingerprint=%s",
                path, count, fingerprint,
            )

            self.alert_manager.send_alert(
                source=path,
                alert_type="log_error",
                subject=subject,
                body=body,
                fingerprint=fingerprint,
            )

        except Exception as exc:
            logger.error("Error flushing batch alert for %s: %s", path, exc)

    def _is_error_line(self, line: str) -> bool:
        """
        Decide if a log line should be treated as an error that triggers alerting.
        Only triggers on lines with specific log level formats: ' - warning - ', ' - error - ', or ' - critical - '.
        """

        # Normalize for easier matching
        lower = line.lower()

        stripped = lower.strip()

        # Ignore common continuation lines (prevents 2 emails for 1 error)
        if stripped.startswith("balance:"):
            return False
        if stripped.startswith("cost:"):
            return False
        if stripped.startswith("traceback"):
            return False
        if stripped.startswith("{") or stripped.startswith("["):
            return False
        if lower.startswith(" ") or lower.startswith("\t"):
            return False

        # Check for specific log level formats to avoid false positives
        if " - warning - " in lower or " - error - " in lower or " - critical - " in lower:
            return True

        return False

    def _normalize_line(self, line: str) -> str:
        """
        Normalize a log line for stable fingerprinting by removing all
        volatile tokens (timestamps, IDs, numbers, emails, IPs).
        """
        # 1. Remove timestamp + log-level prefix
        #    Handles: YYYY-MM-DD HH:MM:SS,mmm - LEVEL -
        #             YYYY-MM-DDTHH:MM:SS.mmmZ - LEVEL -
        line = re.sub(
            r'^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d{1,6})?(?:Z)?\s*-\s*[A-Z]+\s*-\s*',
            '',
            line,
        )

        # 1.1 Remove retry/attempt noise (Gemini/Claude/OpenAI style)
        line = re.sub(r'\(attempt\s*\d+\s*/\s*\d+\)', '(attempt <N>/<N>)', line, flags=re.IGNORECASE)
        line = re.sub(r'retrying in\s*[\d.]+\s*s', 'retrying in <SEC>s', line, flags=re.IGNORECASE)

        # 1.2 Remove request-id style prefix in square brackets
        line = re.sub(r'^\[[0-9a-fA-F-]{32,36}\]\s*', '', line)

        # 2. Remove bracketed request IDs / UUIDs  e.g. [8dc3981f-d3d5-44bb-81a4-08b24e6b7cda]
        line = re.sub(r'\[[0-9a-fA-F-]{32,36}\]', '', line)

        # 3. Replace IP:PORT patterns before general number removal
        line = re.sub(r'\b\d{1,3}(?:\.\d{1,3}){3}:\d+\b', '<IP_PORT>', line)

        # 4. Replace emails
        line = re.sub(
            r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}',
            '<EMAIL>',
            line,
        )

        # 5. Replace currency/balance/cost numbers only (avoid collapsing 401 vs 503 etc.)
        line = re.sub(r'₹\s*[-+]?\d+(?:\.\d+)?', '₹<AMOUNT>', line)
        line = re.sub(r'balance:\s*[-+]?\d+(?:\.\d+)?', 'balance:<AMOUNT>', line, flags=re.IGNORECASE)
        line = re.sub(r'cost:\s*[-+]?\d+(?:\.\d+)?', 'cost:<AMOUNT>', line, flags=re.IGNORECASE)

        # 6. Normalise whitespace and lowercase
        line = re.sub(r'\s+', ' ', line).strip().lower()

        return line

    def close(self) -> None:
        """Cancel any pending batch timers and clear buffers/fingerprints.
        Safe to call multiple times."""
        with self._batch_lock:
            # cancel any running timers
            for t in list(self._batch_timers.values()):
                try:
                    t.cancel()
                except Exception as exc:  # noqa: BLE001
                    logger.debug("Timer cancel failed (non-critical): %s", exc)
            self._batch_timers.clear()
            self._batch_buffers.clear()
        with self._fp_lock:
            self._recent_fingerprints.clear()

    def _extract_context_window(
        self,
        path: str,
        error_lines: List[str],
        window: int = 50,
    ) -> str:
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                all_lines = f.readlines()

            # Identify first and last error occurrence
            first_idx = None
            last_idx = None

            for i, line in enumerate(all_lines):
                for err in error_lines:
                    header = err.split("\n")[0]
                    if header in line:
                        if first_idx is None:
                            first_idx = i
                        last_idx = i

            if first_idx is None:
                return "".join(error_lines)

            start = max(0, first_idx - window)
            end = min(len(all_lines), last_idx + window + 1)

            context_slice = all_lines[start:end]

            # Mark error lines with 👉
            marked_context = []
            for line in context_slice:
                if any(err.split("\n")[0] in line for err in error_lines):
                    marked_context.append("👉 " + line)
                else:
                    marked_context.append(line)

            return "".join(marked_context)

        except Exception:
            logger.exception("Failed to extract context window")
            return "".join(error_lines)


class LogMonitor:
    def __init__(self, log_config: LogConfig, alert_manager: AlertManager) -> None:
        self.log_config = log_config
        self.alert_manager = alert_manager
        self.observer = Observer()

    def start(self) -> None:
        handler = LogEventHandler(self.log_config, self.alert_manager)
        # keep reference so we can close timers on shutdown
        self.handler = handler

        # Watch only unique parent roots
        watch_roots = set()

        for directory in self.log_config.directories:
            parent = os.path.abspath(os.path.join(directory, ".."))
            watch_roots.add(parent)

        # Initialize positions for already existing log files
        for root_dir in watch_roots:
            if os.path.isdir(root_dir):
                for root, _, files in os.walk(root_dir):
                    for name in files:
                        if not name.endswith(".log"):
                            continue
                        path = os.path.join(root, name)
                        try:
                            size = os.path.getsize(path)
                            with handler._lock:
                                handler._positions[path] = size
                            logger.info("Initialized log file position at EOF: %s (%d bytes)", path, size)
                        except OSError as exc:
                            logger.warning("Unable to stat log file %s: %s", path, exc)

                logger.info("Watching root log directory recursively: %s", root_dir)
                self.observer.schedule(handler, root_dir, recursive=True)
            else:
                logger.warning("Root log directory does not exist yet, still scheduling: %s", root_dir)
                # Still schedule it (important!)
                self.observer.schedule(handler, root_dir, recursive=True)

        self.observer.start()
        # Start the polling safety-net AFTER the observer so positions are initialised
        handler.start_polling()
        logger.info("LogMonitor started (watchdog + polling hybrid)")

    def stop(self) -> None:
        logger.info("Stopping LogMonitor")
        handler = getattr(self, "handler", None)
        if handler is not None:
            try:
                handler.stop_polling()
            except Exception:
                logger.exception("Error stopping polling loop")
            try:
                handler.close()
            except Exception:
                logger.exception("Error closing LogEventHandler")
        self.observer.stop()
        self.observer.join()

    def run_in_thread(self) -> threading.Thread:
        t = threading.Thread(target=self.start, daemon=True)
        t.start()
        return t
