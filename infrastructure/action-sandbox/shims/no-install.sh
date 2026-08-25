#!/bin/sh
# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

# Citra Action Sandbox — install blocker shim.
#
# Symlinked over apt, apt-get, pip, pip3, npm in the Dockerfile.
# Exits 127 (command-not-found) with a one-line explanation so the agent
# stops trying instead of burning turns on PermissionError tracebacks from
# a read-only rootfs + offline network. OpenClaw's persona
# (~/.openclaw/agent/SKILL_ENVIRONMENT.md) explains the policy in detail.
echo "[CITRA-SANDBOX] install blocked: $(basename "$0") $*. Sandbox is offline + read-only. See ~/.openclaw/agent/SKILL_ENVIRONMENT.md." >&2
exit 127
