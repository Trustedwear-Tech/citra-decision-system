# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

import logging
import re
import smtplib
import time
from email.mime.text import MIMEText
from typing import Dict, List, Tuple, Optional

import psutil
import requests

from .config import AlertConfig

logger = logging.getLogger(__name__)

# Lazy-import boto3 only when SES transport is used
_boto3 = None


def _get_boto3():
    global _boto3
    if _boto3 is None:
        import boto3 as _b3
        _boto3 = _b3
    return _boto3


class AlertManager:
    def __init__(self, config: AlertConfig) -> None:
        self.config = config
        self.transport = config.transport.lower()
        # key: (source, alert_type) or (source, alert_type, fingerprint) -> timestamp
        self._last_sent: Dict[Tuple[str, ...], float] = {}

        if self.transport == "ses":
            if not self.config.aws_access_key_id or not self.config.aws_secret_access_key:
                logger.warning("AWS credentials are not set. SES alerts will NOT be sent.")
        elif self.transport == "smtp":
            if not self.config.smtp_host:
                logger.warning("SMTP_HOST not set. SMTP alerts will NOT be sent.")
        elif self.transport == "webhook":
            if not self.config.webhook_url:
                logger.warning("ALERT_WEBHOOK_URL not set. Webhook alerts will NOT be sent.")
        elif self.transport == "log":
            logger.info("Alert transport set to 'log' — alerts will be logged only (no email/webhook).")
        else:
            logger.warning("Unknown ALERT_TRANSPORT '%s'. Falling back to 'log'.", self.transport)
            self.transport = "log"

        # Validate email addresses (for ses/smtp)
        if self.transport in ("ses", "smtp"):
            all_emails = [e for e in [self.config.from_email] + self.config.to_email if e]
            invalid_emails = [e for e in all_emails if not self._is_valid_email(e)]
            if invalid_emails:
                logger.error("Invalid email addresses found: %s. Email alerts may fail.", invalid_emails)

        logger.info(
            "AlertManager initialized. transport=%s from_email=%s to_emails=%s cooldown=%s",
            self.transport,
            self.config.from_email,
            self.config.to_email,
            self.config.cooldown_seconds,
        )

    def _is_valid_email(self, email: str) -> bool:
        """Basic email validation using regex."""
        pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        return re.match(pattern, email) is not None

    def _should_send(
        self,
        source: str,
        alert_type: str,
        fingerprint: Optional[str] = None,
    ) -> bool:
        """
        Decide whether to send an alert based on cooldown.

        - For normal alerts (no fingerprint): cooldown per (source, alert_type)
        - For log alerts with fingerprint: cooldown per (source, alert_type, fingerprint)
        """
        now = time.time()

        if fingerprint is None:
            key: Tuple[str, ...] = (source, alert_type)
        else:
            key = (source, alert_type, fingerprint)

        last = self._last_sent.get(key)
        if last is None or now - last >= self.config.cooldown_seconds:
            self._last_sent[key] = now
            return True

        logger.info(
            "Alert suppressed by cooldown. source=%s alert_type=%s fingerprint=%s seconds_since_last=%.1f",
            source,
            alert_type,
            fingerprint,
            now - last if last is not None else -1,
        )
        return False

    def send_alert(
        self,
        source: str,
        alert_type: str,
        subject: str,
        body: str,
        fingerprint: Optional[str] = None,
        to: Optional[List[str]] = None,
    ) -> None:
        if not self._should_send(source, alert_type, fingerprint=fingerprint):
            return

        # Add CPU & Memory stats only for NON-system alerts
        if alert_type not in ("cpu_high", "mem_high"):
            cpu = psutil.cpu_percent()
            mem = psutil.virtual_memory().percent
            body += (
                f"\n\nSystem Status:\n"
                f"CPU Usage: {cpu}%\n"
                f"Memory Usage: {mem}%"
            )

        if self.transport == "ses":
            self._send_ses(source, alert_type, subject, body, to=to)
        elif self.transport == "smtp":
            self._send_smtp(source, alert_type, subject, body, to=to)
        elif self.transport == "webhook":
            self._send_webhook(source, alert_type, subject, body)
        else:
            # log transport — just log the alert
            logger.warning(
                "ALERT [%s/%s]: %s\n%s", source, alert_type, subject, body
            )

    def _send_ses(
        self, source: str, alert_type: str, subject: str, body: str,
        to: Optional[List[str]] = None,
    ) -> None:
        if not self.config.aws_access_key_id or not self.config.aws_secret_access_key:
            logger.error(
                "Cannot send alert email (AWS credentials missing). "
                "source=%s alert_type=%s subject=%s",
                source, alert_type, subject,
            )
            return

        recipients = to or self.config.to_email
        try:
            boto3 = _get_boto3()
            ses_client = boto3.client(
                'ses',
                aws_access_key_id=self.config.aws_access_key_id,
                aws_secret_access_key=self.config.aws_secret_access_key,
                region_name=self.config.aws_region
            )

            response = ses_client.send_email(
                Source=self.config.from_email,
                Destination={'ToAddresses': recipients},
                Message={
                    'Subject': {'Data': subject},
                    'Body': {'Text': {'Data': body}},
                },
            )

            logger.info(
                "Alert email sent via AWS SES. source=%s alert_type=%s subject=%s message_id=%s",
                source, alert_type, subject, response['MessageId'],
            )
        except Exception as exc:
            logger.error(
                "Failed to send alert via SES. source=%s alert_type=%s subject=%s error=%s",
                source, alert_type, subject, exc,
            )

    def _send_smtp(
        self, source: str, alert_type: str, subject: str, body: str,
        to: Optional[List[str]] = None,
    ) -> None:
        recipients = to or self.config.to_email
        try:
            msg = MIMEText(body)
            msg['Subject'] = subject
            msg['From'] = self.config.from_email
            msg['To'] = ', '.join(recipients)

            if self.config.smtp_use_tls:
                server = smtplib.SMTP(self.config.smtp_host, self.config.smtp_port)
                server.starttls()
            else:
                server = smtplib.SMTP(self.config.smtp_host, self.config.smtp_port)

            if self.config.smtp_username:
                server.login(self.config.smtp_username, self.config.smtp_password)

            server.sendmail(self.config.from_email, recipients, msg.as_string())
            server.quit()

            logger.info(
                "Alert email sent via SMTP. source=%s alert_type=%s subject=%s",
                source, alert_type, subject,
            )
        except Exception as exc:
            logger.error(
                "Failed to send alert via SMTP. source=%s alert_type=%s subject=%s error=%s",
                source, alert_type, subject, exc,
            )

    def _send_webhook(self, source: str, alert_type: str, subject: str, body: str) -> None:
        if not self.config.webhook_url:
            logger.error(
                "Cannot send webhook alert (ALERT_WEBHOOK_URL missing). "
                "source=%s alert_type=%s subject=%s",
                source, alert_type, subject,
            )
            return

        try:
            payload = {
                "source": source,
                "alert_type": alert_type,
                "subject": subject,
                "body": body,
            }
            resp = requests.post(
                self.config.webhook_url,
                json=payload,
                timeout=10,
            )
            resp.raise_for_status()
            logger.info(
                "Alert sent via webhook. source=%s alert_type=%s subject=%s status=%s",
                source, alert_type, subject, resp.status_code,
            )
        except Exception as exc:
            logger.error(
                "Failed to send alert via webhook. source=%s alert_type=%s subject=%s error=%s",
                source, alert_type, subject, exc,
            )
