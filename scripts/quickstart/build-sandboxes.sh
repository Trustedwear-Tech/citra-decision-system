#!/usr/bin/env bash
# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

#
# Build the per-user SANDBOX / BUILDER images that `docker compose up` does NOT
# build — they are spawned at runtime by action-sandbox-host (action chat + the
# smart-app builder) and by Citra-Service (quick-chat code execution), so they
# must exist locally before those features are used:
#
#   citra-agent-sandbox-base   ← FROM ghcr.io/openclaw/openclaw  (shared base)
#     └─ citra-app-builder           (smart-app builder pods)
#   quick-chat-sandbox          ← FROM python:3.11-slim  (code execution)
#
# Also creates the two egress networks the host attaches sandboxes to
# (citra-action-egress is INTERNAL = no public egress; approved-egress is NOT
# internal — making it internal breaks all spawns). Idempotent; builds are
# layer-cached, so re-runs are cheap.
#   ./scripts/build-sandboxes.sh            # build from source (default)
#   ./scripts/build-sandboxes.sh --pull     # pull prebuilt from GHCR + retag to local names
#
# --pull fetches the published images (CITRA_REGISTRY / CITRA_VERSION, same defaults
# as docker-compose.release.yml) and retags them to the local tags the host spawns
# by — no local build.
#
set -uo pipefail
# ../.. — this script lives in scripts/quickstart/, so one ".." lands in
# scripts/ and every build context below resolves against the wrong directory:
#   ERROR: failed to build: unable to prepare context:
#          path "infrastructure/action-sandbox" not found
# Both paths exist; the cd was just short. start.sh invokes this by absolute
# path, so the caller's cwd never masked it. Every other quickstart script
# already uses ../.. — this one was the outlier.
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT"

MODE="build"; [ "${1:-}" = "--pull" ] && MODE="pull"
REGISTRY="${CITRA_REGISTRY:-ghcr.io/trustedwear-tech}"
VERSION="${CITRA_VERSION:-latest}"
FAILED=0
note(){ echo "→ $*"; }
build(){ # build <tag> <dockerfile> <context>
  local tag="$1" df="$2" ctx="$3"
  note "building $tag"
  if docker build -t "$tag" -f "$df" "$ctx"; then echo "   ✓ $tag"; else echo "   ✗ $tag FAILED"; FAILED=1; fi
}

# ── Egress networks (the host attaches spawned sandboxes to these) ───────────
note "ensuring sandbox egress networks"
docker network inspect citra-action-egress          >/dev/null 2>&1 || docker network create --internal citra-action-egress >/dev/null && echo "   ✓ citra-action-egress (internal)"
docker network inspect citra-action-approved-egress >/dev/null 2>&1 || docker network create            citra-action-approved-egress >/dev/null && echo "   ✓ citra-action-approved-egress"

# ── --pull: fetch prebuilt from GHCR + retag to the local names ──────────────
if [ "$MODE" = "pull" ]; then
  note "pulling prebuilt sandbox images from ${REGISTRY} (tag ${VERSION})"
  for pair in "citra-app-builder" "quick-chat-sandbox"; do
    src="${REGISTRY}/${pair}:${VERSION}"; dst="${pair}:latest"
    if docker pull "$src" && docker tag "$src" "$dst"; then echo "   ✓ $dst  (from $src)"; else echo "   ✗ $pair FAILED"; FAILED=1; fi
  done
  echo; [ "$FAILED" = 0 ] && echo "✅ prebuilt sandbox images ready (retagged to local names)" || echo "❌ some pulls failed — is the release published + the package public?"
  exit $FAILED
fi

# ── 1. Shared base (pulls the OpenClaw base image on first build) ────────────
build citra-agent-sandbox-base:latest infrastructure/action-sandbox/Dockerfile infrastructure/action-sandbox

# ── 2. Consumers of the base ─────────────────────────────────────────────────
if [ "$FAILED" = 0 ]; then
  build citra-app-builder:latest         smart-app-service/builder-sandbox/Dockerfile    smart-app-service
else
  echo "   ! skipping base consumers — base image build failed"
fi

# ── 3. Quick-chat code-execution sandbox (independent) ───────────────────────
build quick-chat-sandbox:latest Citra-Service/Dockerfile.quick-chat-sandbox Citra-Service

echo
if [ "$FAILED" = 0 ]; then
  echo "✅ sandbox images ready:"
  docker images --format '   {{.Repository}}:{{.Tag}}  ({{.Size}})' | grep -E "citra-agent-sandbox-base|citra-app-builder|quick-chat-sandbox" | sort -u
else
  echo "❌ one or more sandbox images failed to build - app-builder / code-execution"
  echo "   will not work until fixed. See the build output above."
fi
exit $FAILED
