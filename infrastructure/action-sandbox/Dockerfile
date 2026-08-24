# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

# syntax=docker/dockerfile:1.6
#
# Citra Agent Sandbox (base image)
# ================================
# Per-user, long-running execution container for any agent workflow.
# Two known consumers today:
#   - action-chat-service (deep multi-step chat)
#   - citra-app-builder   (smart-app-service build pods)
# Built and published as `citra-agent-sandbox-base`. citra-app-builder
# layers on top of this tag.
#
# Base: official OpenClaw prebuilt image (Node + /app/openclaw.mjs + the
#       `openclaw` CLI at /usr/local/bin/openclaw).
#       The base image's WORKDIR is /app and contains JS artifacts â€” we
#       DO NOT touch /app. All Citra-specific files live under /srv/citra.
#
# On top we add:
#   - Python 3 + FastAPI adapter (runner/)
#   - tini (pid 1, reaps openclaw subprocesses)
#   - gettext-base (envsubst, for config template rendering)
#   - non-root user `citra` (uid 1500) so we can cleanly separate from the
#     base image's `node` user (uid 1000)
#   - persona seed files (workspace-seed/) copied into the per-user
#     /workspace volume on first boot
#   - a baked-in Chart.js -> PNG renderer at /srv/citra/chartjs (used by
#     citra_toolkit.report.make_chartjs_png for the presentation/report
#     skills; Chart.js can't run in WeasyPrint/python-pptx)
#   - install-blocker shim (shims/no-install.sh) symlinked over
#     apt/apt-get/pip/pip3/npm so install attempts fail with exit 127 and
#     a one-line message instead of a confusing PermissionError on the
#     read-only rootfs. OpenClaw's persona (SKILL_ENVIRONMENT.md) explains
#     the policy in detail.
#
# OpenClaw's own nested-sandbox feature is DISABLED
# (see openclaw.config.template.json -> agents.defaults.sandbox.mode=off);
# THIS container IS the sandbox. The outer action-sandbox-host service
# applies (and MUST apply â€” verify in that repo):
#   - read_only=True rootfs
#   - tmpfs:  /tmp        size=128m,mode=1777,nosuid,nodev
#             /home/citra size=16m,mode=0700,uid=1500,gid=1500
#             /run        size=4m
#   - cap_drop=[ALL]
#   - security_opt=[no-new-privileges:true]
#   - user='1500:1500'
#   - networks: citra-action-egress (internal:true) + citra-action-approved-egress (internal:false — host-gateway route for instance mgmt)
#   - per-user named volume mounted at /workspace WITH a driver_opts size cap
#
# All OpenClaw state lives on the per-user volume via OPENCLAW_HOME=
# /workspace/.openclaw-home (set in entrypoint.sh).
#
# Runtime flow:
#   tini -> entrypoint.sh
#           â”œâ”€ export OPENCLAW_HOME=/workspace/.openclaw-home
#           â”œâ”€ render ${OPENCLAW_HOME}/.openclaw/openclaw.json from template+env
#           â”œâ”€ seed /workspace/.openclaw/agent/ from /srv/citra/workspace-seed/
#           â”‚   (only if empty â€” persona edits persist across restarts)
#           â”œâ”€ `openclaw config validate` (fail-fast on schema errors)
#           â”œâ”€ `openclaw gateway run` in background (loopback, :18789)
#           â”œâ”€ wait for /healthz
#           â””â”€ exec python3 -m runner.adapter (foreground, :8090)
#
# We strip OpenClaw's bundled cloud-provider extensions at build time so
# the gateway doesn't walk them at boot and doesn't trigger their
# `stageRuntimeDependencies: true` npm installs on first task. The old
# warm-tree workaround is gone with them.

# ---- OpenClaw base image: preprocessing + rollback contract --------------
# We deliberately track the moving :slim tag â€” every build picks up
# whatever upstream shipped last. That's acceptable because we only
# depend on a small, known set of things from the base image (listed
# below). If an OpenClaw release breaks any of them, the symptom is a
# build failure or a gateway boot failure; in either case, ROLL BACK to
# the previous known-good upstream version by overriding the build arg:
#
#   docker build \
#     --build-arg OPENCLAW_IMAGE=ghcr.io/openclaw/openclaw:<prev-version>-slim \
#     infrastructure/action-sandbox
#
# (Or revert this Dockerfile to a prior commit and rebuild.) Past
# known-good versions can be found in `docker history` on a sandbox VM
# or via the warm-tree manifest in git history (e.g. 2026.4.23).
#
# What we touch on top of the base image â€” keep this list in sync with
# the RUN steps below:
#   1. /app/dist/extensions/<name>/  â€” we `rm -rf` a deny-list of
#      cloud-provider extensions we never load (see "Strip unused
#      bundled extensions" below). If upstream renames the directory
#      the strip silently no-ops; dead weight survives but nothing
#      breaks. We also `rm -rf /app/skills` â€” upstream's stock skill
#      bundle, which Citra never loads (see "Strip OpenClaw's bundled
#      stock skill library" below).
#   2. /usr/local/bin/{apt,apt-get,pip,pip3,npm}  â€” we symlink the
#      no-install shim over these (see "Install-blocker shim" below).
#      A new package manager appearing upstream (yarn, pnpm, uv) needs
#      adding here.
#   3. The `openclaw` CLI at /usr/local/bin/openclaw, with subcommands
#      `config validate` and `gateway run` â€” called from entrypoint.sh.
#   4. The gateway WebSocket protocol (chat.send / chat.abort /
#      sessions.messages.subscribe / chat.history) â€” consumed by
#      runner/gateway_client.py. Protocol changes surface as runtime
#      errors mid-task, not at build time.
#   5. The config schema at https://openclaw.ai/schemas/config.json â€”
#      keys we set live in openclaw.config.template.json. Schema renames
#      fail loudly at the `openclaw config validate` preflight in
#      entrypoint.sh.
#   6. The base image's `node` user (uid 1000) â€” we sidestep it with a
#      separate `citra` user (uid 1500).
#
# When you bump OpenClaw, smoke-test by spawning ONE deep-research
# session before letting CI ship the image to prod. The build catches
# (1)/(2)/(3)/(5); (4) and any runtime regression need an actual turn.
# Pinned to 2026.5.27 (bumped 2026-06-14). 2026.4.23 blocked gateway control-plane
# startup on a hardcoded model-pricing internet fetch that the no-egress lockdown
# denies (see memory project_sandbox_no_public_egress) — 2026.4.23 has no config to
# disable it. 2026.5.27 supports disabling the pricing fetch (set in
# openclaw.config.template.json) AND carries the ~2026.5.22 SSE fix (no bodyless-200,
# /readyz sidecar gating preserved), i.e. PAST the 2026.5.7 SSE-truncation regression.
# Digest-PINNED so the mutable :2026.5.27-slim tag can never silently drift the
# OpenClaw version under us. When bumping, update BOTH the tag and the @sha256 digest,
# and smoke-test ONE deep-research turn (SSE projection) before CI ships to prod.
ARG OPENCLAW_IMAGE=ghcr.io/openclaw/openclaw:2026.5.27-slim@sha256:51eb7d3b8fc4524c348f9201fbb81d4b7c776e7ae8593d70da47d51a5f092792
FROM ${OPENCLAW_IMAGE} AS runtime

# The openclaw base image drops to USER node at the bottom. Go back to root
# to install packages and lay down our files, then switch to citra.
USER root

# ---- Strip unused bundled extensions -------------------------------------
# OpenClaw's prebuilt image ships every cloud-provider plugin so out-of-the-
# box users can BYO Bedrock / Azure / Vertex models. Citra has its own
# inference proxy (the `citra` provider in openclaw.config.template.json),
# so none of these are reachable from inside the sandbox anyway â€” they're
# dead code on disk that still gets walked at gateway boot and that used
# to drag `stageRuntimeDependencies: true` npm installs into every cold
# spawn (the reason the now-removed warm-tree tar existed).
#
# Keep this list conservative: only remove extensions we are CERTAIN
# Citra never loads. Anything else (memory, code-execution, core tools)
# stays. If the gateway logs an unknown-extension error on boot after
# adding to this list, revert that entry.
RUN set -eu; \
    EXT_DIR=/app/dist/extensions; \
    if [ -d "$EXT_DIR" ]; then \
        for ext in \
            amazon-bedrock \
            amazon-bedrock-mantle \
            anthropic-vertex \
            azure-speech \
            msteams \
            browser \
            microsoft \
            acpx \
        ; do \
            if [ -d "$EXT_DIR/$ext" ]; then \
                echo "[strip] removing $EXT_DIR/$ext"; \
                rm -rf "$EXT_DIR/$ext"; \
            fi; \
        done; \
    fi

# ---- Strip OpenClaw's bundled stock skill library ------------------------
# Upstream ships ~50 general personal-assistant skills at /app/skills
# (slack, spotify-player, apple-notes, discord, 1password, trello, â€¦).
# Citra agents load their skills exclusively from the per-user workspace
# (/workspace/.openclaw/workspace/skills/, seeded by seed-persona.sh from
# /srv/citra/workspace-seed/skills/) â€” /app/skills is NEVER on the active
# skill path. Left in place it's dead weight + attack surface, and a
# `find /app` by the agent surfaces 50 irrelevant SKILL.md files that
# pollute its context. Remove it. If a future OpenClaw release relocates
# the bundle the rm silently no-ops; nothing breaks.
RUN rm -rf /app/skills

# ---- Raise the LIVE chat-display reply truncation cap --------------------
# OpenClaw caps the live assistant reply at 8000 chars. The final message is
# broadcast via projectChatDisplayMessage(msg) with NO maxChars arg, which
# falls back to the literal `8e3` default in
# /app/dist/chat-display-projection-*.js. There are 3 such defaults:
#   DEFAULT_CHAT_HISTORY_TEXT_MAX_CHARS = 8e3
#   truncateChatHistoryText(text, maxChars = opts?.maxChars ?? 8e3)
#   projectChatDisplayMessages(messages, options) → ... options?.maxChars ?? 8e3
# The gateway.webchat.chatHistoryMaxChars config ONLY overrides the
# history-read + inject paths — NOT this live broadcast — so long Deep
# Analytics replies were cut mid-answer with "...(truncated)...". There is no
# config knob for the live path, so we raise the literal cap in the vendored
# bundle to 2e6. FAIL THE BUILD LOUDLY if the pattern moved (OpenClaw bump)
# so we re-patch instead of silently shipping the 8000 cap again.
RUN set -eu; \
    f=$(ls /app/dist/chat-display-projection-*.js 2>/dev/null | head -1); \
    [ -n "$f" ] || { echo "CAP-PATCH ERROR: chat-display-projection JS not found — OpenClaw layout changed, re-patch"; exit 1; }; \
    before=$(grep -oE '\?\?[[:space:]]*8e3|MAX_CHARS[[:space:]]*=[[:space:]]*8e3' "$f" | wc -l); \
    [ "$before" -ge 3 ] || { echo "CAP-PATCH ERROR: expected >=3 '8e3' cap sites in $f, found $before — re-patch for this OpenClaw version"; exit 1; }; \
    sed -i -E 's/(\?\?[[:space:]]*)8e3/\12e6/g; s/(MAX_CHARS[[:space:]]*=[[:space:]]*)8e3/\12e6/g' "$f"; \
    after=$(grep -oE '\?\?[[:space:]]*8e3|MAX_CHARS[[:space:]]*=[[:space:]]*8e3' "$f" | wc -l); \
    [ "$after" -eq 0 ] || { echo "CAP-PATCH ERROR: $after '8e3' cap site(s) remain in $f after patch"; exit 1; }; \
    echo "[cap-patch] raised live chat-display cap 8e3->2e6 at $before site(s) in $f"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_BREAK_SYSTEM_PACKAGES=1 \
    CITRA_SANDBOX_VERSION=0.3.0 \
    CITRA_SRV_DIR=/srv/citra \
    HOME=/home/citra \
    PATH=/usr/local/bin:/usr/bin:/bin \
    PYTHONPATH=/srv/citra/runner \
    \
    # OpenClaw gateway kill switches (private undocumented env-var
    # surface â€” discovered by grepping the bundled JS for
    # process.env.OPENCLAW_*). The gateway's "starting channels and
    # sidecars" phase otherwise blocks ~72 seconds on the bonjour
    # mDNS service-advertisement probe â€” pointless inside a sandboxed
    # container that has no multicast network. Setting this skips
    # the probe entirely and cuts cold start from ~80s to ~5s.
    # The SKIP_CRON flag is a bonus â€” we never enable cron jobs and
    # the empty store-init still costs a few ms.
    OPENCLAW_DISABLE_BONJOUR=1 \
    OPENCLAW_SKIP_CRON=1 \
    OPENCLAW_SKIP_CHANNELS=1 \
    OPENCLAW_TEST_FAST=1

# Python 3 + tini + envsubst (gettext-base). ca-certificates is already in
# the base image; re-declaring makes layers deterministic.
# WeasyPrint runtime libs (pango, cairo, gdk-pixbuf, ffi) are required for
# the report.make_pdf() helper in citra_toolkit.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        ca-certificates \
        tini \
        gettext-base \
        python3 \
        python3-pip \
        python3-venv \
        libpango-1.0-0 \
        libpangoft2-1.0-0 \
        libcairo2 \
        libgdk-pixbuf-2.0-0 \
        libffi8 \
        shared-mime-info \
        fonts-dejavu-core \
        libmagic1 \
 && rm -rf /var/lib/apt/lists/* \
 && ln -sf /usr/bin/python3 /usr/local/bin/python

# Non-root user for the adapter + openclaw CLI subprocesses.
# uid 1500 matches the USER configured host-side in sandbox_manager.
# The base image ships without `useradd`, so we edit /etc/passwd directly.
RUN echo 'citra:x:1500:1500::/home/citra:/bin/sh' >> /etc/passwd \
 && echo 'citra:x:1500:' >> /etc/group \
 && mkdir -p /home/citra/.openclaw /workspace /srv/citra /session-uploads \
 && chown -R 1500:1500 /home/citra /workspace /srv/citra /session-uploads \
 && chmod 0700 /session-uploads

WORKDIR /srv/citra

# Python deps for the adapter only. OpenClaw is Node, already in the base.
COPY requirements.txt /srv/citra/requirements.txt
RUN pip3 install --no-cache-dir -r /srv/citra/requirements.txt

# Runtime assets â€” neutral base only. Persona files (SOUL / IDENTITY /
# AGENTS / USER / MEMORY / TOOLS / SKILL_*.md) are NOT shipped in this
# image. Each consumer layers its own persona into /srv/citra/workspace-seed/:
#   - action-chat workload â†’ action-chat-service/sandbox/Dockerfile
#                              builds citra-action-chat-sandbox:latest
#   - smart-app builder    â†’ smart-app-service/builder-sandbox/Dockerfile
#                              builds citra-app-builder:latest
# /srv/citra/workspace-seed/ is created empty here so seed-persona.sh's
# `cp -r /srv/citra/workspace-seed/* ...` is a no-op when this image is run
# directly (which should never happen in production â€” only via a consumer).
#
# The neutrality extends to the AGENT ROSTER: openclaw.config.template.json
# declares only `main` with an empty subagents.allowAgents. A consumer that
# wants research sub-agents ships BOTH their personas
# (workspace-seed/agents/<name>/) AND a delegation overlay
# (/srv/citra/openclaw.config.overlay.json, deep-merged onto the rendered
# config by entrypoint.sh step 4a). action-chat does; the builder does not,
# so the builder's main agent advertises no delegation tool.
COPY runner                          /srv/citra/runner
COPY openclaw.config.template.json   /srv/citra/openclaw.config.template.json
COPY entrypoint.sh                   /srv/citra/entrypoint.sh
COPY seed-persona.sh                 /srv/citra/seed-persona.sh
COPY shims/no-install.sh             /usr/local/bin/citra-no-install
RUN mkdir -p /srv/citra/workspace-seed

# Make citra_toolkit importable without PYTHONPATH so OpenClaw's
# code_execution sandbox (which clears env vars) can still `import citra_toolkit`.
RUN echo "/srv/citra/runner" > /usr/lib/python3/dist-packages/citra_runner.pth

# ---- Chart.js renderer (baked in) ----------------------------------------
# The presentation / reporting skills render web-grade charts with
# Chart.js. Chart.js is JavaScript and cannot run inside WeasyPrint (PDF)
# or python-pptx (PPTX), so charts are pre-rendered to PNG by
# chartjs/render.js (stdin: Chart.js config JSON -> stdout: PNG);
# citra_toolkit.report.make_chartjs_png() shells out to it.
#
# node-canvas needs cairo/pango (already installed above for WeasyPrint)
# plus pangocairo/jpeg/gif/rsvg at runtime. We install the build
# toolchain, compile the modules (a prebuilt binary is used when the
# base image's Node version matches, source build otherwise), then purge
# the toolchain in this SAME layer so it never reaches the shipped image.
# This RUN must precede the install-blocker shim below â€” that shim
# symlinks /usr/local/bin/npm to a no-op.
#
# matplotlib stays in the image (citra_toolkit.report.make_chart_png) for
# in-process analytical plots; Chart.js is the deliverable-polish layer.
COPY chartjs /srv/citra/chartjs
RUN set -eux; \
    apt-get update; \
    RUNTIME_LIBS="libpangocairo-1.0-0 libjpeg62-turbo libgif7 librsvg2-2"; \
    BUILD_PKGS="build-essential pkg-config libcairo2-dev libpango1.0-dev libjpeg-dev libgif-dev librsvg2-dev"; \
    apt-get install -y --no-install-recommends $RUNTIME_LIBS $BUILD_PKGS; \
    npm install --prefix /srv/citra/chartjs --omit=dev --no-audit --no-fund; \
    apt-get purge -y $BUILD_PKGS; \
    apt-get autoremove -y; \
    apt-get clean; \
    rm -rf /var/lib/apt/lists/* /root/.npm /tmp/*

# ---- Install-blocker shim ------------------------------------------------
# Symlink the package managers actually present in the base image to the
# shim. The shim exits 127 with a one-line stderr message so the agent
# stops retrying. Symlinks in /usr/local/bin shadow /usr/bin via PATH;
# the read-only rootfs would already block writes, but the shim turns an
# opaque PermissionError into an unambiguous "don't bother".
RUN chmod +x /usr/local/bin/citra-no-install \
 && for cmd in apt apt-get pip pip3 npm; do \
        ln -sf /usr/local/bin/citra-no-install "/usr/local/bin/$cmd"; \
    done

RUN chmod +x /srv/citra/entrypoint.sh /srv/citra/seed-persona.sh \
 && chown -R 1500:1500 /srv/citra

USER citra
WORKDIR /srv/citra

# /workspace is intentionally NOT a Docker VOLUME.
# Operators mount it as a per-container tmpfs (size capped). All state
# that previously persisted (OpenClaw home, reports) is either ephemeral
# by design or uploaded to Citra-Service object storage before the
# container is reaped. See docker-compose.action.yml header for the
# host contract.

EXPOSE 8090

ENTRYPOINT ["/usr/bin/tini", "--", "/srv/citra/entrypoint.sh"]
