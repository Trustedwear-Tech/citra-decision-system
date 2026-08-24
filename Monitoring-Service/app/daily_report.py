# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psutil
import docker
import boto3
from botocore.exceptions import ClientError

from .config import load_config
from .logging_setup import setup_logging

IST = timezone(timedelta(hours=5, minutes=30))


def _fmt_percent(value: float) -> str:
    return f"{value:.1f}%"


def collect_system_summary() -> str:
    boot_time = datetime.fromtimestamp(psutil.boot_time(), IST)
    now = datetime.now(IST)
    uptime = now - boot_time

    cpu = psutil.cpu_percent(interval=1)
    mem = psutil.virtual_memory().percent
    disk_root = psutil.disk_usage("/")

    lines = [
        "🖥 System Summary",
        f"- Current time (IST): {now.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- Uptime: {uptime.days}d {uptime.seconds // 3600}h",
        f"- CPU Usage: {_fmt_percent(cpu)}",
        f"- Memory Usage: {_fmt_percent(mem)}",
        f"- Disk Usage (/): {_fmt_percent(disk_root.percent)} "
        f"(used {disk_root.used // (1024**3)} GB / {disk_root.total // (1024**3)} GB)",
        "",
    ]
    return "\n".join(lines)


def collect_docker_summary() -> str:
    client = docker.from_env()
    containers = client.containers.list(all=True)

    lines = ["🐳 Docker Containers"]
    if not containers:
        lines.append("- No containers found.")
        lines.append("")
        return "\n".join(lines)

    for c in containers:
        name = c.name
        cid = c.short_id
        status = c.status
        health = c.attrs.get("State", {}).get("Health", {}).get("Status", "n/a")
        lines.append(f"- {name} ({cid}): status={status}, health={health}")
    lines.append("")
    return "\n".join(lines)


def collect_monitoring_log_tail(log_dir: str, max_lines: int = 50) -> str:
    path = Path(log_dir) / "monitoring-service.log"
    lines = ["📜 Recent Monitoring-Service Activity"]

    if not path.is_file():
        lines.append(f"- Log file not found: {path}")
        lines.append("")
        return "\n".join(lines)

    try:
        content = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        tail = content[-max_lines:]
        if not tail:
            lines.append("- Log is empty.")
        else:
            lines.append(f"- Last {len(tail)} lines from {path}:")
            lines.extend(f"  {ln}" for ln in tail)
    except Exception as exc:
        lines.append(f"- Failed to read log file: {exc}")
    lines.append("")
    return "\n".join(lines)


def build_report_body(log_dir: str) -> str:
    parts = [
        "Daily Health Report - Monitoring-Service",
        "=======================================",
        "",
        collect_system_summary(),
        collect_docker_summary(),
        collect_monitoring_log_tail(log_dir),
        "",
        "End of report.",
    ]
    return "\n".join(parts)


def send_daily_report() -> None:
    config = load_config()
    setup_logging(config.logging)

    logger = logging.getLogger("DailyReport")

    alert_cfg = config.alert

    if not alert_cfg.aws_access_key_id or not alert_cfg.aws_secret_access_key:
        logger.error("AWS credentials are missing. Cannot send daily health report.")
        return

    subject = "Daily Health Report - Monitoring-Service"
    body = build_report_body(config.logging.log_dir)

    logger.info("Preparing to send daily health report email to %s", alert_cfg.to_email)

    try:
        ses_client = boto3.client(
            'ses',
            aws_access_key_id=alert_cfg.aws_access_key_id,
            aws_secret_access_key=alert_cfg.aws_secret_access_key,
            region_name=alert_cfg.aws_region
        )
        
        response = ses_client.send_email(
            Source=alert_cfg.from_email,
            Destination={
                'ToAddresses': alert_cfg.to_email
            },
            Message={
                'Subject': {
                    'Data': subject
                },
                'Body': {
                    'Text': {
                        'Data': body
                    }
                }
            }
        )

        logger.info(
            "Daily health report email sent via AWS SES. message_id=%s",
            response['MessageId'],
        )
    except ClientError as exc:
        logger.error("Failed to send daily health report email via AWS SES: %s", exc)
    except Exception as exc:
        logger.error("Failed to send daily health report email: %s", exc)


def main() -> None:
    send_daily_report()


if __name__ == "__main__":
    main()
