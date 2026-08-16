#!/bin/sh
# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

# Idempotent re-seed of the persona files, skills, and memory cache.
#
# Invoked at boot by entrypoint.sh AND before every turn by the
# control adapter. Re-running is cheap (small files, all on local
# tmpfs) and self-healing: if the agent deleted or modified any
# persona file in the previous turn, it's restored before the next
# turn's system prompt is built.
#
# This is the backstop. The primary defense is the persona's
# self-preservation rule in SOUL.md / AGENTS.md.
#
# Required env vars:
#   OPENCLAW_HOME              — agent home dir (set by entrypoint)
#   CITRA_AGENT_USER_ID         — for USER.md envsubst
#   CITRA_AGENT_USER_EMAIL
#   CITRA_AGENT_ORG_ID
#   CITRA_AGENT_DEPT_IDS
#   CITRA_AGENT_ROLES
#   CITRA_AGENT_ACTIVE_FOLDER_ID
#   CITRA_AGENT_PERSONAL_DATA_ENABLED
#   CITRA_AGENT_SESSION_ID
set -eu

WORKSPACE_DIR=/workspace/.openclaw/workspace
AGENT_DIR=/workspace/.openclaw/agent
SKILLS_DIR="$WORKSPACE_DIR/skills"
mkdir -p "$WORKSPACE_DIR" "$AGENT_DIR" "$SKILLS_DIR"

for f in AGENTS.md SOUL.md TOOLS.md IDENTITY.md MEMORY.md; do
  if [ -f "/srv/citra/workspace-seed/$f" ]; then
    cp -f "/srv/citra/workspace-seed/$f" "$WORKSPACE_DIR/$f"
    cp -f "/srv/citra/workspace-seed/$f" "$AGENT_DIR/$f"
  fi
done

if [ -f /srv/citra/workspace-seed/USER.md ]; then
  envsubst '${CITRA_AGENT_USER_ID} ${CITRA_AGENT_USER_EMAIL} ${CITRA_AGENT_ORG_ID} ${CITRA_AGENT_DEPT_IDS} ${CITRA_AGENT_ROLES} ${CITRA_AGENT_ACTIVE_FOLDER_ID} ${CITRA_AGENT_PERSONAL_DATA_ENABLED} ${CITRA_AGENT_SESSION_ID}' \
    < /srv/citra/workspace-seed/USER.md \
    > "$WORKSPACE_DIR/USER.md"
  cp -f "$WORKSPACE_DIR/USER.md" "$AGENT_DIR/USER.md"
fi

for src in /srv/citra/workspace-seed/SKILL_*.md; do
  [ -e "$src" ] || continue
  base=$(basename "$src" .md)
  name=$(echo "$base" | sed 's/^SKILL_//' | tr '[:upper:]' '[:lower:]')
  mkdir -p "$SKILLS_DIR/$name"
  cp -f "$src" "$SKILLS_DIR/$name/SKILL.md"
done

# Pre-structured skill directories. The smart-app-builder consumer ships
# its skills as /srv/citra/workspace-seed/skills/<name>/SKILL.md — whole
# directories copied in by its Dockerfile — instead of flat SKILL_*.md
# files. Copy any such tree through verbatim so the agent actually sees
# them in <workspace>/skills/. The action-chat consumer has no skills/
# dir in its seed (it uses flat SKILL_*.md handled above), so the -d
# guard simply skips this for it. Without this step the builder pod
# runs with ZERO skills loaded.
if [ -d /srv/citra/workspace-seed/skills ]; then
  cp -rf /srv/citra/workspace-seed/skills/. "$SKILLS_DIR/"
fi

# Builder validation assets. The smart-app-builder consumer ships JSON
# Schemas (schemas/) and the Layer-A static-check harness
# (builder-workspace/static_checks.py) into the seed via its Dockerfile.
# Seed both into the workspace at the exact paths the builder skills
# reference, so spec validation and the static cross-spec checks actually
# run instead of silently falling back to manual. The action-chat consumer
# ships neither dir, so the -d guards make this a clean no-op there.
for d in schemas builder-workspace; do
  if [ -d "/srv/citra/workspace-seed/$d" ]; then
    mkdir -p "$WORKSPACE_DIR/$d"
    cp -rf "/srv/citra/workspace-seed/$d/." "$WORKSPACE_DIR/$d/"
  fi
done

if [ -n "${OPENCLAW_HOME:-}" ]; then
  mkdir -p "${OPENCLAW_HOME}/.openclaw/agent" "${OPENCLAW_HOME}/.openclaw/workspace/skills"
  cp -rf "$AGENT_DIR/." "${OPENCLAW_HOME}/.openclaw/agent/" 2>/dev/null || true
  cp -rf "$SKILLS_DIR/." "${OPENCLAW_HOME}/.openclaw/workspace/skills/" 2>/dev/null || true
fi

# ---- Specialist agents ----------------------------------------------------
# Multi-agent architecture: master ("main") delegates Tier 2/3 work to
# specialists via OpenClaw's sessions_spawn tool. Per the docs, sub-agent
# context only injects AGENTS.md + TOOLS.md (no SOUL/IDENTITY/USER) — so
# each specialist's workspace dir needs only those two files. Their
# AGENTS.md is small (~1.5 KB) and domain-specific; TOOLS.md is the same
# shared catalogue main uses.
#
# The roster is NOT hardcoded here — this base seeder is neutral. It seeds
# whatever specialist personas the CONSUMER ships under
# /srv/citra/workspace-seed/agents/<name>/AGENTS.md (the action-chat consumer
# ships 6; the smart-app-builder consumer ships none, so this loop is a
# no-op there). The matching delegation WIRING lives in that consumer's
# openclaw.config.overlay.json — see entrypoint.sh step 4a.
for src in /srv/citra/workspace-seed/agents/*/AGENTS.md; do
  [ -f "$src" ] || continue
  agent=$(basename "$(dirname "$src")")
  dst_dir="/workspace/.openclaw/agents/$agent"
  mkdir -p "$dst_dir"
  cp -f "$src" "$dst_dir/AGENTS.md"
  cp -f /srv/citra/workspace-seed/TOOLS.md "$dst_dir/TOOLS.md"
done
