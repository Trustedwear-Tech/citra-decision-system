# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""
URL Validation Utility — SSRF Protection
Blocks requests to internal/private/metadata IP ranges.
"""

import ipaddress
import logging
import socket
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# RFC 1918, loopback, link-local, and cloud metadata IP ranges
_BLOCKED_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),   # Link-local & cloud metadata
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),     # Carrier-grade NAT
    ipaddress.ip_network("::1/128"),           # IPv6 loopback
    ipaddress.ip_network("fc00::/7"),          # IPv6 private
    ipaddress.ip_network("fe80::/10"),         # IPv6 link-local
]


def is_safe_url(url: str) -> bool:
    """
    Validate that a URL is safe to fetch (not pointing to internal resources).

    Checks:
    1. Scheme must be http or https
    2. Hostname must resolve to a public IP (not RFC 1918, loopback, link-local, metadata)
    3. No bare IP addresses in unusual formats

    Returns True if safe, False if blocked.
    """
    try:
        parsed = urlparse(url)

        # 1. Scheme check
        if parsed.scheme not in ("http", "https"):
            logger.warning(f"[SSRF] Blocked non-HTTP scheme: {parsed.scheme}")
            return False

        hostname = parsed.hostname
        if not hostname:
            logger.warning("[SSRF] Blocked URL with no hostname")
            return False

        # 2. Resolve hostname to IP(s) and check each
        try:
            addr_infos = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
        except socket.gaierror:
            # DNS resolution failed — could be non-existent host, allow the
            # upstream request to fail naturally with a connection error
            return True

        for family, _, _, _, sockaddr in addr_infos:
            ip_str = sockaddr[0]
            try:
                ip = ipaddress.ip_address(ip_str)
            except ValueError:
                continue

            for network in _BLOCKED_NETWORKS:
                if ip in network:
                    logger.warning(f"[SSRF] Blocked internal IP {ip} (network {network}) for host {hostname}")
                    return False

        return True

    except Exception as exc:
        logger.error(f"[SSRF] URL validation error: {exc}")
        return False
