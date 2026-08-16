# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""
Configuration for Playwright Render Service
"""
import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    # Server settings
    PORT = int(os.getenv("PORT", 3001))
    HOST = os.getenv("HOST", "0.0.0.0")
    
    # Browser pool settings
    MIN_BROWSERS = int(os.getenv("MIN_BROWSERS", 1))
    MAX_BROWSERS = int(os.getenv("MAX_BROWSERS", 3))
    BROWSER_IDLE_TIMEOUT = int(os.getenv("BROWSER_IDLE_TIMEOUT", 300))  # seconds
    
    # Default output format
    DEFAULT_OUTPUT_FORMAT = os.getenv("DEFAULT_OUTPUT_FORMAT", "html")  # "html" or "pdf"
    
    # Request timeout (increased for slow/protected sites)
    REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", 45))  # seconds
    
    # Default wait strategy: "load", "domcontentloaded", or "networkidle"
    # "domcontentloaded" is faster and works better with sites that have continuous network activity
    DEFAULT_WAIT_FOR = os.getenv("DEFAULT_WAIT_FOR", "domcontentloaded")
    
    # Stealth mode
    STEALTH_MODE = os.getenv("STEALTH_MODE", "true").lower() == "true"
    
    # CORS
    ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "*").split(",")


config = Config()
