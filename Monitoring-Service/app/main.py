# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

import logging
import os
import signal
import sys
import time

# Vault bootstrap MUST run BEFORE `.config import load_config` (config reads
# env at module load). Inline urllib loader (own-folder build context).
if os.getenv("VAULT_ADDR"):
    from .vault_bootstrap import load_from_vault
    load_from_vault()

from .config import load_config
from .logging_setup import setup_logging
from .alert_manager import AlertManager
from .log_monitor import LogMonitor
from .system_monitor import SystemMonitor
from .docker_monitor import DockerMonitor
from .webhook import run_webhook_server
from .observability import init_sentry


def main() -> None:
    config = load_config()
    setup_logging(config.logging)

    # Error tracker (GlitchTip / Sentry) — no-op unless SENTRY_DSN is set
    init_sentry("monitoring-service")

    logger = logging.getLogger("MonitoringService")
    logger.info("Monitoring-Service starting up")

    alert_manager = AlertManager(config.alert)

    log_monitor = LogMonitor(config.log, alert_manager)
    system_monitor = SystemMonitor(config.resources, alert_manager)
    docker_monitor = DockerMonitor(config.docker, alert_manager)

    log_monitor.run_in_thread()
    system_monitor.run_in_thread()
    docker_monitor.run_in_thread()

    # Alertmanager webhook receiver — fans incoming alerts out through the
    # AlertManager transport (SES/SMTP/webhook/log). Daemon thread.
    run_webhook_server(alert_manager)

    logger.info("Monitoring-Service started. Press Ctrl+C to exit.")

    def handle_sigterm(signum, frame):
        logger.info("Received termination signal (%s). Shutting down...", signum)
        log_monitor.stop()
        system_monitor.stop()
        docker_monitor.stop()
        sys.exit(0)

    signal.signal(signal.SIGTERM, handle_sigterm)
    signal.signal(signal.SIGINT, handle_sigterm)

    while True:
        time.sleep(60)


if __name__ == "__main__":
    main()
