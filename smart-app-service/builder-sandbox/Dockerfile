# syntax=docker/dockerfile:1.6
#
# Citra Smart-App Builder Sandbox
# ===============================
# Per-session ephemeral build pod consumed by smart-app-service to turn
# a BA's plain-language goal into a published Citra Smart App
# (AgentSpec + AppSpec + optional WorkflowSpec).
#
# Layers the smart-app builder persona (SOUL, IDENTITY, USER, MEMORY)
# from ./workspace-seed/ on top of the neutral citra-agent-sandbox-base.
# Pulls AGENTS.md, TOOLS.md, schemas, and the builder skills from
# the smart-app-service repo so maintainership stays with the service team.
#
# Build (context = smart-app-service/, so the COPYs from builder-workspace/,
# schemas/ and skills/ resolve):
#
#   docker build \
#     -f smart-app-service/builder-sandbox/Dockerfile \
#     -t citra-app-builder:latest \
#     smart-app-service
#
# action-sandbox-host spawns this image when handling smart-app build
# sessions (config key: SANDBOX_HOST_BUILDER_IMAGE — defaults to this tag).
#
# Symmetry note: action-chat-service/sandbox/ is the sibling
# consumer image. Both have the same shape (Dockerfile + workspace-seed/
# + README.md). Keep them aligned — if you add a base-image step here,
# consider whether the symmetric step makes sense in action-chat-service/sandbox/.

ARG BASE_IMAGE=citra-agent-sandbox-base:latest
FROM ${BASE_IMAGE}

USER root

# ---- 1. Seed the smart-app-builder persona ------------------------------
# These four files come from builder-sandbox/workspace-seed/
# (build context root is smart-app-service/, hence the prefix).
COPY builder-sandbox/workspace-seed/SOUL.md      /srv/citra/workspace-seed/SOUL.md
COPY builder-sandbox/workspace-seed/IDENTITY.md  /srv/citra/workspace-seed/IDENTITY.md
COPY builder-sandbox/workspace-seed/USER.md      /srv/citra/workspace-seed/USER.md
COPY builder-sandbox/workspace-seed/MEMORY.md    /srv/citra/workspace-seed/MEMORY.md

# ---- 2. Orchestration + schemas owned by smart-app-service --------------
# Maintainership of these lives with the service team; they are siblings
# of builder-sandbox/ inside smart-app-service/, the single source of truth.
COPY builder-workspace/AGENTS.md /srv/citra/workspace-seed/AGENTS.md
COPY builder-workspace/TOOLS.md  /srv/citra/workspace-seed/TOOLS.md
COPY schemas/                    /srv/citra/workspace-seed/schemas/
# The Layer-A static cross-spec check harness the citra-self-test skill runs.
# Without this COPY it never ships and the static checks silently no-op.
# seed-persona.sh (base image) copies builder-workspace/ into the pod
# workspace at boot, alongside schemas/.
COPY builder-workspace/static_checks.py /srv/citra/workspace-seed/builder-workspace/static_checks.py

# Sub-agent personas. seed-persona.sh (base image) seeds each
# /srv/citra/workspace-seed/agents/<id>/AGENTS.md into
# /workspace/.openclaw/agents/<id>/ at boot. The builder ships ONE sub-agent —
# `runtime-verifier` — which the overlay (step 4) adds to main.allowAgents.
# Phase 3.6 delegates spec↔runtime verification to it so the heavy runtime
# snapshot is read in its disposable context, never the main builder's prompt.
COPY builder-sandbox/workspace-seed/agents/ /srv/citra/workspace-seed/agents/

# ---- 3. The builder skills ----------------------------------------------
# Each smart-app-service skill is a directory containing SKILL.md (and
# sometimes helper files). The whole skills/ tree is copied into
# /srv/citra/workspace-seed/skills/ so seed-persona.sh from the base image
# places each at /workspace/.openclaw/workspace/skills/<name>/ at every
# container start.
#
# SCALABILITY: this is a single wildcard COPY of the entire skills/ tree —
# adding a new skill under smart-app-service/skills/citra-*/ ships
# automatically, no Dockerfile edit required. (Previously each skill needed
# its own COPY line, which silently dropped any skill a maintainer forgot —
# e.g. citra-safety-rules. See `project_app_builder_dockerfile_drift.md`.)
COPY skills/ /srv/citra/workspace-seed/skills/

# ---- 4. Config overlay -------------------------------------------------
# Disable OpenClaw's native memorySearch for the builder. It's a stateless,
# per-session pod with no need for semantic memory, and its
# CITRA_AGENT_PROXY_BASE_URL points at smart-app-service (which serves
# /llm/v1 chat completions, NOT the /v1/embeddings alias that
# action-chat-service has) — so every memory sync 404'd in the runner log.
# The base entrypoint deep-merges this overlay onto the rendered config.
COPY builder-sandbox/openclaw.config.overlay.json /srv/citra/openclaw.config.overlay.json

# Permissions: uid 1500 is the citra user inside the sandbox base image.
RUN chown -R 1500:1500 /srv/citra/workspace-seed /srv/citra/openclaw.config.overlay.json && \
    chmod -R a+rX /srv/citra/workspace-seed && \
    chmod a+r /srv/citra/openclaw.config.overlay.json

USER 1500

# Entrypoint + CMD inherit from the base.
