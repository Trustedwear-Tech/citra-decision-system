<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: Apache-2.0

  Licensed under the Apache License, Version 2.0 (the "License"); you may not
  use this file except in compliance with the License. You may obtain a copy of
  the License at http://www.apache.org/licenses/LICENSE-2.0
-->

# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| 1.x     | Yes       |

## Reporting a Vulnerability

**Do NOT report security vulnerabilities through public GitHub issues.**

Instead, please report them via email to: **security@citra-ai.com**

Please include:

- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Suggested fix (if any)

## Response Timeline

- **Acknowledgment**: Within 48 hours
- **Initial Assessment**: Within 5 business days
- **Fix & Disclosure**: Coordinated with reporter, typically within 30 days

## Scope

The following are in scope:

- All services in this repository (citra-service, user-service, etc.)
- Authentication and authorization flaws
- Data exposure or leakage
- Injection vulnerabilities (SQL, NoSQL, command injection)
- Cryptographic weaknesses
- Infrastructure misconfigurations in provided Docker/K8s manifests

## Out of Scope

- Vulnerabilities in third-party dependencies (report upstream)
- Social engineering
- Denial of service (unless through a code vulnerability)
- Issues in self-hosted infrastructure not using our provided configurations

## Recognition

We credit security researchers in our release notes (with permission).
