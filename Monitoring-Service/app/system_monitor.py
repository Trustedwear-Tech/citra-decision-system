# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

import logging
import threading
import time

import psutil

from .config import ResourceConfig
from .alert_manager import AlertManager

logger = logging.getLogger(__name__)


class SystemMonitor:
    def __init__(self, config: ResourceConfig, alert_manager: AlertManager) -> None:
        self.config = config
        self.alert_manager = alert_manager
        self._stop = threading.Event()
        self._high_cpu_start = None
        self._high_mem_start = None

        logger.info(
            "SystemMonitor initialized. CPU threshold=%s%% duration=%ss, MEM threshold=%s%% duration=%ss, interval=%ss",
            self.config.cpu_threshold_percent,
            self.config.cpu_duration_seconds,
            self.config.mem_threshold_percent,
            self.config.mem_duration_seconds,
            self.config.check_interval_seconds,
        )

    def start(self) -> None:
        logger.info("SystemMonitor started")
        # Prime psutil's CPU percent measurement to avoid meaningless first value
        psutil.cpu_percent(interval=1)
        while not self._stop.is_set():
            cpu = psutil.cpu_percent(interval=None)
            mem = psutil.virtual_memory().percent

            cpu_above = cpu >= self.config.cpu_threshold_percent
            mem_above = mem >= self.config.mem_threshold_percent

            now = time.time()

            # Handle CPU monitoring
            if cpu_above:
                if self._high_cpu_start is None:
                    self._high_cpu_start = now
                    logger.info(
                        "High CPU usage detected. CPU=%.1f threshold=%s%%",
                        cpu,
                        self.config.cpu_threshold_percent,
                    )
                elif now - self._high_cpu_start >= self.config.cpu_duration_seconds:
                    logger.warning(
                        "High CPU usage persisted for %.1fs. CPU=%.1f",
                        now - self._high_cpu_start,
                        cpu,
                    )
                    subject = "[System Alert] High CPU usage"
                    body = (
                        f"CPU usage: {cpu:.1f}%\n"
                        f"Threshold: {self.config.cpu_threshold_percent}% for "
                        f"{self.config.cpu_duration_seconds} seconds or more."
                    )
                    self.alert_manager.send_alert(
                        source="system",
                        alert_type="cpu_high",
                        subject=subject,
                        body=body,
                    )
                    self._high_cpu_start = now
            else:
                if self._high_cpu_start is not None:
                    logger.info("CPU usage back to normal. CPU=%.1f", cpu)
                self._high_cpu_start = None

            # Handle MEM monitoring
            if mem_above:
                if self._high_mem_start is None:
                    self._high_mem_start = now
                    logger.info(
                        "High Memory usage detected. MEM=%.1f threshold=%s%%",
                        mem,
                        self.config.mem_threshold_percent,
                    )
                elif now - self._high_mem_start >= self.config.mem_duration_seconds:
                    logger.warning(
                        "High Memory usage persisted for %.1fs. MEM=%.1f",
                        now - self._high_mem_start,
                        mem,
                    )
                    subject = "[System Alert] High Memory usage"
                    body = (
                        f"Memory usage: {mem:.1f}%\n"
                        f"Threshold: {self.config.mem_threshold_percent}% for "
                        f"{self.config.mem_duration_seconds} seconds or more."
                    )
                    self.alert_manager.send_alert(
                        source="system",
                        alert_type="mem_high",
                        subject=subject,
                        body=body,
                    )
                    self._high_mem_start = now
            else:
                if self._high_mem_start is not None:
                    logger.info("Memory usage back to normal. MEM=%.1f", mem)
                self._high_mem_start = None

            self._stop.wait(self.config.check_interval_seconds)

    def stop(self) -> None:
        logger.info("Stopping SystemMonitor")
        self._stop.set()

    def run_in_thread(self) -> threading.Thread:
        t = threading.Thread(target=self.start, daemon=True)
        t.start()
        return t
