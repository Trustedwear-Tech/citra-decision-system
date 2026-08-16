#!/bin/bash
# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

# entrypoint.sh — runs as root, fixes docker socket GID, then drops to appuser
set -e

DOCKER_SOCK=/var/run/docker.sock

if [ -S "$DOCKER_SOCK" ]; then
    SOCKET_GID=$(stat -c '%g' "$DOCKER_SOCK")
    if [ "$SOCKET_GID" = "0" ]; then
        # Socket owned by root group (Docker Desktop / some Linux installs)
        # Add appuser to root group for socket access
        usermod -aG root appuser 2>/dev/null || true
    else
        # Socket owned by a named group (Linux with docker group, e.g. GID 999)
        # Create or update 'docker' group to match host GID, add appuser
        groupmod -g "$SOCKET_GID" docker 2>/dev/null || \
            groupadd -g "$SOCKET_GID" docker 2>/dev/null || true
        usermod -aG docker appuser 2>/dev/null || true
    fi
fi

exec gosu appuser "$@"
