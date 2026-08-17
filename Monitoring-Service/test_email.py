#!/usr/bin/env python3
# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""
Test script to verify AWS SES email configuration.
"""

import sys
import os
from pathlib import Path

# Add the app directory to the Python path
app_dir = Path(__file__).parent / "app"
sys.path.insert(0, str(app_dir))

from app.config import load_config
from app.alert_manager import AlertManager
from app.logging_setup import setup_logging


def test_email_config():
    """Test the email configuration by sending a test email."""
    print("Loading configuration...")
    config = load_config()

    print("Setting up logging...")
    setup_logging(config.logging)

    print("Initializing AlertManager...")
    alert_manager = AlertManager(config.alert)

    print("Sending test email...")
    try:
        alert_manager.send_alert(
            source="test",
            alert_type="test_email",
            subject="Test Email from Monitoring Service",
            body="This is a test email to verify the AWS SES configuration is working correctly.\n\nIf you received this email, the configuration is correct!"
        )
        print("✅ Test email sent successfully!")
        print(f"📧 Sent to: {config.alert.to_email}")
        print(f"📧 From: {config.alert.from_email}")
        print(f"🌍 AWS Region: {config.alert.aws_region}")
    except Exception as e:
        print(f"❌ Failed to send test email: {e}")
        return False

    return True


if __name__ == "__main__":
    success = test_email_config()
    sys.exit(0 if success else 1)