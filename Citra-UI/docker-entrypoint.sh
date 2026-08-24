#!/bin/sh
# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

# =============================================================================
# Citra AI — Runtime configuration injection for Docker deployments
# =============================================================================
# Injects a <script> tag into index.html that sets window.__CITRA_ENV__
# with values from Docker environment variables. The app's config.js reads
# window.__CITRA_ENV__ as override for build-time EXPO_PUBLIC_* values.
#
# Usage:
#   docker run -p 80:80 \
#     -e CITRA_API_URL=https://api.example.com \
#     -e CITRA_USER_SERVICE_URL=https://api.example.com/user-service \
#     ghcr.io/citra-ai/citra-ui:latest
#
# For developers running locally (npx expo start) — this script is not used.
# =============================================================================

set -e

# Find the index.html that is ACTUALLY served. Two images serve this app and
# they put it in different places: Dockerfile.web builds an nginx image
# (/usr/share/nginx/html), while Dockerfile.dev — the one the quickstart builds
# — serves ./dist with `serve`. This was hardcoded to the nginx path, so under
# the quickstart the injection wrote to a file nobody reads and the bundle fell
# back to its build-time default of https://api.citra-ai.com. A self-hoster got
# a UI pointed at the vendor's production API and could not sign in.
INDEX=""
for candidate in /usr/share/nginx/html/index.html                  /app/Citra-UI/dist/index.html                  ./dist/index.html                  /app/dist/index.html; do
  if [ -f "$candidate" ]; then INDEX="$candidate"; break; fi
done
if [ -z "$INDEX" ]; then
  echo "FATAL: no index.html found to inject runtime config into." >&2
  echo "       Looked in the nginx and dist locations. Without it the bundle" >&2
  echo "       uses its build-time API URLs, which are NOT this deployment's." >&2
  exit 1
fi
echo "-> injecting runtime config into $INDEX"

# Build the runtime config JSON from environment variables
# Only include variables that are actually set
CONFIG="{"
FIRST=true

add_config() {
  KEY="$1"
  VALUE="$2"
  if [ -n "$VALUE" ]; then
    if [ "$FIRST" = true ]; then
      FIRST=false
    else
      CONFIG="${CONFIG},"
    fi
    # Escape double quotes and backslashes in value
    ESCAPED_VALUE=$(printf '%s' "$VALUE" | sed 's/\\/\\\\/g; s/"/\\"/g')
    CONFIG="${CONFIG}\"${KEY}\":\"${ESCAPED_VALUE}\""
  fi
}

# Core URLs (most enterprise customers only need these)
add_config "CITRA_API_URL"          "$CITRA_API_URL"
add_config "USER_SERVICE_URL"       "$CITRA_USER_SERVICE_URL"
add_config "AUTH_BASE_URL"          "$CITRA_AUTH_BASE_URL"

# Google OAuth (optional — only if customer uses Google sign-in)
add_config "GOOGLE_WEB_CLIENT_ID"   "$CITRA_GOOGLE_CLIENT_ID"

CONFIG="${CONFIG}}"

if [ "$CONFIG" = "{}" ]; then
  echo "⚠️  No CITRA_* environment variables set — using build-time defaults"
else
  echo "✅ Injecting runtime config into index.html"
  echo "   Config: $CONFIG"

  # Inject <script> tag right after <head>
  sed -i "s|<head>|<head><script>window.__CITRA_ENV__=${CONFIG};</script>|" "$INDEX"
fi

exec "$@"
