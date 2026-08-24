# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

import os
from dataclasses import dataclass, field
from typing import List

from dotenv import load_dotenv

# Load .env into environment
load_dotenv()


def _split_env_list(var_name: str, default: List[str]) -> List[str]:
    raw = os.getenv(var_name)
    if not raw:
        return default
    return [item.strip() for item in raw.split(",") if item.strip()]


@dataclass
class LogConfig:
    directories: List[str] = field(default_factory=lambda: _split_env_list(
        "LOG_DIRECTORIES",
        [
            "/logs/citra-service",
            "/logs/citra-user-service",
            "/logs/playwright-render-service",
        ],
    ))
    error_keywords: List[str] = field(default_factory=lambda: _split_env_list(
        "LOG_ERROR_KEYWORDS",
        [
            "ERROR",
            "Error",
            "CRITICAL",
            "Critical",
            "Exception",
            "Traceback",
            "not authorized",
            "Unauthorized",
            "Could not check database",
            "permission denied",
            "failed to",
        ],
    ))
    error_context_lines_before: int = int(os.getenv("LOG_ERROR_CONTEXT_LINES_BEFORE", 50))
    error_context_lines_after: int = int(os.getenv("LOG_ERROR_CONTEXT_LINES_AFTER", 50))
    error_wait_seconds: int = int(os.getenv("LOG_ERROR_WAIT_SECONDS", 5))
    batch_window_seconds: int = int(os.getenv("LOG_BATCH_WINDOW_SECONDS", 30))
    fingerprint_cooldown_seconds: int = int(os.getenv("LOG_FINGERPRINT_COOLDOWN_SECONDS", 300))
    poll_interval_seconds: int = int(os.getenv("LOG_POLL_INTERVAL_SECONDS", 10))


@dataclass
class ResourceConfig:
    cpu_threshold_percent: float = float(os.getenv("CPU_THRESHOLD_PERCENT", 85))
    cpu_duration_seconds: int = int(os.getenv("CPU_DURATION_SECONDS", 60))
    mem_threshold_percent: float = float(os.getenv("MEM_THRESHOLD_PERCENT", 85))
    mem_duration_seconds: int = int(os.getenv("MEM_DURATION_SECONDS", 60))
    check_interval_seconds: int = int(os.getenv("RESOURCE_CHECK_INTERVAL_SECONDS", 5))


@dataclass
class DockerConfig:
    check_interval_seconds: int = int(os.getenv("DOCKER_CHECK_INTERVAL_SECONDS", 15))
    startup_grace_period_seconds: int = int(os.getenv("DOCKER_STARTUP_GRACE_PERIOD_SECONDS", 300))


@dataclass
class AlertConfig:
    # Transport: ses | smtp | webhook | log
    transport: str = os.getenv("ALERT_TRANSPORT", "ses")

    # AWS SES settings (transport=ses)
    aws_access_key_id: str = os.getenv("AWS_ACCESS_KEY_ID", "")
    aws_secret_access_key: str = os.getenv("AWS_SECRET_ACCESS_KEY", "")
    aws_region: str = os.getenv("AWS_REGION", "ap-south-1")

    # SMTP settings (transport=smtp)
    smtp_host: str = os.getenv("SMTP_HOST", "localhost")
    smtp_port: int = int(os.getenv("SMTP_PORT", "587"))
    smtp_username: str = os.getenv("SMTP_USERNAME", "")
    smtp_password: str = os.getenv("SMTP_PASSWORD", "")
    smtp_use_tls: bool = os.getenv("SMTP_USE_TLS", "true").lower() == "true"

    # Webhook settings (transport=webhook)
    webhook_url: str = os.getenv("ALERT_WEBHOOK_URL", "")

    # Email settings (used by ses and smtp)
    from_email: str = os.getenv("EMAIL_DEFAULT_SENDER", "")
    to_email: List[str] = field(default_factory=lambda: _split_env_list(
        "EMAIL_DEFAULT_TO",
        [],
    ))
    cooldown_seconds: int = int(os.getenv("ALERT_COOLDOWN_SECONDS", 600))


@dataclass
class LoggingConfig:
    log_dir: str = os.getenv("MONITOR_LOG_DIR", "/monitoring-service/logs")
    log_level: str = os.getenv("MONITOR_LOG_LEVEL", "INFO")

@dataclass
class Config:
    log: LogConfig = field(default_factory=LogConfig)
    resources: ResourceConfig = field(default_factory=ResourceConfig)
    docker: DockerConfig = field(default_factory=DockerConfig)
    alert: AlertConfig = field(default_factory=AlertConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)



def load_config() -> Config:
    return Config()
