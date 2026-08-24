#!/bin/sh
# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

# Citra Action Sandbox entrypoint.
#
# 1. Require the three Citra env vars.
# 2. Export OPENCLAW_HOME on the /home/citra tmpfs (state is intentionally
#    ephemeral: containers are single-conversation; cross-conversation
#    continuity comes from CITRA_AGENT_SEED_HISTORY, not disk).
# 3. Generate a per-container gateway token.
# 4. Render ${OPENCLAW_HOME}/.openclaw/openclaw.json from template (envsubst).
# 5. Seed ${OPENCLAW_HOME}/.openclaw/agent/ from /srv/citra/workspace-seed/
#    every cold start (the dir is tmpfs, so it's empty).
# 6. `openclaw config validate` preflight (fail fast on schema errors).
# 7. Start the OpenClaw gateway in background (mode=local, bind=loopback,
#    sandbox=off). No daemon install â€” tini is pid 1.
# 8. Poll /healthz until ready.
# 9. Exec the Citra Control Adapter on :8090 in the foreground.
# 9. Exec the Citra Control Adapter on :8090 in the foreground.
set -eu

# ---- 1. Required env ------------------------------------------------------
: "${CITRA_AGENT_LLM_BASE_URL:?CITRA_AGENT_LLM_BASE_URL is required}"
: "${CITRA_AGENT_LLM_API_KEY:?CITRA_AGENT_LLM_API_KEY is required}"
: "${CITRA_AGENT_MODEL:?CITRA_AGENT_MODEL is required}"

# ---- 2. State location + sandbox awareness env ---------------------------
# OPENCLAW_HOME redirects ALL of OpenClaw's internal path resolution
# (~/.openclaw -> $OPENCLAW_HOME/.openclaw, including sessions.json,
# extensions/, credentials/, hooks/transforms/, last-known-good config,
# .clobbered.* recovery files). It lives on the /home/citra tmpfs:
# rootfs is read-only, and the container is single-conversation so
# there is no need (or desire) to persist state.
OPENCLAW_HOME="${OPENCLAW_HOME:-/home/citra/.openclaw-home}"
export OPENCLAW_HOME

OPENCLAW_CFG_DIR="${OPENCLAW_HOME}/.openclaw"
mkdir -p "$OPENCLAW_CFG_DIR"
# OpenClaw creates its own subdirs (extensions/, logs/, hooks/, credentials/,
# sessions.json) on demand â€” don't pre-create them, that's its job.

# ---- 3. (auth.mode=none â€” no per-container token needed) ----------------
# The gateway is bound to 127.0.0.1:18789 inside this isolated container.
# Per https://docs.openclaw.ai/gateway/configuration-reference auth-none
# is the documented pattern for trusted loopback ingress, which removes
# the WebSocket device-pairing requirement that token-mode imposes.

# ---- 4. Render openclaw.json ---------------------------------------------
# Substitute only the vars we control. Other $-sequences in the template
# (e.g. ${OPENCLAW_HOME} for logging.file) are intentionally substituted
# too so the logging path follows the volume.
: "${CITRA_AGENT_CONTEXT_WINDOW:=163840}"
: "${CITRA_AGENT_MAX_OUTPUT_TOKENS:=50000}"
# Default CITRA_MCP_URL â€” overridable from sandbox-host spawn env. Points
# at the standalone citra-mcp-service which exposes web_search, web_fetch,
# (later: vault, files, ocr, image, ...) as native OpenClaw tools. Both
# action-chat-service and smart-app-service inject the same URL so all
# sandboxes share one tool surface.
: "${CITRA_MCP_URL:=http://host.docker.internal:9090/mcp}"
export CITRA_AGENT_CONTEXT_WINDOW CITRA_AGENT_MAX_OUTPUT_TOKENS CITRA_MCP_URL
# CITRA_AGENT_PROXY_BASE_URL is the in-cluster URL OpenClaw's native
# memorySearch hits for embeddings (via the OpenAI-compatible
# /v1/embeddings alias on action-chat-service). It comes from
# sandbox_manager.py spawn env; we forward it to the template so the
# memorySearch.remote.baseUrl interpolation lands. Required â€” if unset
# the rendered baseUrl is "/v1" and OpenClaw will silently fail to
# embed any memory note. Better to refuse to start.
: "${CITRA_AGENT_PROXY_BASE_URL:?CITRA_AGENT_PROXY_BASE_URL is required (sandbox spawn env)}"
export CITRA_AGENT_PROXY_BASE_URL
ENVSUBST_VARS='${CITRA_AGENT_LLM_BASE_URL} ${CITRA_AGENT_LLM_API_KEY} ${CITRA_AGENT_MODEL} ${OPENCLAW_HOME} ${CITRA_AGENT_CONTEXT_WINDOW} ${CITRA_AGENT_MAX_OUTPUT_TOKENS} ${CITRA_MCP_URL} ${CITRA_AGENT_SCOPED_TOKEN} ${CITRA_AGENT_PROXY_BASE_URL}'
envsubst "$ENVSUBST_VARS" \
  < /srv/citra/openclaw.config.template.json \
  > "$OPENCLAW_CFG_DIR/openclaw.json"
chmod 600 "$OPENCLAW_CFG_DIR/openclaw.json"

# ---- 4a. Optional per-consumer config overlay ---------------------------
# The base template is NEUTRAL: it declares only the `main` agent with an
# empty subagents.allowAgents. The sub-agent ROSTER is NOT wired there, so
# an image that ships no overlay boots with main and nothing to delegate to.
#
# A consumer that DOES want sub-agents ships
# /srv/citra/openclaw.config.overlay.json. citra-app-builder does: its
# overlay adds the `runtime-verifier` sub-agent and patches
# main.subagents.allowAgents to reach it (see
# smart-app-service/builder-sandbox/openclaw.config.overlay.json).
#
# We envsubst it with the SAME var set, then deep-merge it onto the rendered
# openclaw.json. Merge rules (see merge below):
#   - dicts merge recursively (overlay keys win on conflict)
#   - lists of objects that all carry an "id" (e.g. agents.list) merge BY id:
#     same id -> deep-merge, new id -> append. This lets the overlay both add
#     sub-agent entries AND patch main.subagents.allowAgents.
#   - any other value: overlay replaces base.
# Keeping the roster in the consumer overlay (not the shared base) is what
# stops an image from advertising a delegation tool naming personas it does
# not ship.
OVERLAY=/srv/citra/openclaw.config.overlay.json
if [ -f "$OVERLAY" ]; then
  echo "[entrypoint] merging config overlay $OVERLAY"
  envsubst "$ENVSUBST_VARS" < "$OVERLAY" > /tmp/openclaw.overlay.json
  python3 - "$OPENCLAW_CFG_DIR/openclaw.json" /tmp/openclaw.overlay.json <<'PY'
import json, sys

base_path, overlay_path = sys.argv[1], sys.argv[2]
with open(base_path, encoding="utf-8") as f:
    base = json.load(f)
with open(overlay_path, encoding="utf-8") as f:
    overlay = json.load(f)


def _all_keyed(seq):
    return bool(seq) and all(isinstance(x, dict) and "id" in x for x in seq)


def merge(b, o):
    if isinstance(b, dict) and isinstance(o, dict):
        out = dict(b)
        for k, v in o.items():
            out[k] = merge(b[k], v) if k in b else v
        return out
    # lists of {"id": ...} objects merge by id, preserving base order then
    # appending overlay-only entries.
    if isinstance(b, list) and isinstance(o, list) and _all_keyed(b) and _all_keyed(o):
        by_id = {x["id"]: x for x in b}
        order = [x["id"] for x in b]
        for x in o:
            if x["id"] in by_id:
                by_id[x["id"]] = merge(by_id[x["id"]], x)
            else:
                by_id[x["id"]] = x
                order.append(x["id"])
        return [by_id[i] for i in order]
    # scalars / mismatched shapes: overlay wins.
    return o


merged = merge(base, overlay)
with open(base_path, "w", encoding="utf-8") as f:
    json.dump(merged, f, indent=2)
PY
  chmod 600 "$OPENCLAW_CFG_DIR/openclaw.json"
  rm -f /tmp/openclaw.overlay.json
fi

# ---- 5. Seed persona + skill files ---------------------------------------
# /workspace/ is tmpfs and rebuilt every cold start; always seed.
#
# IMPORTANT: OpenClaw's "Project Context" loader (the one that injects
# AGENTS.md / SOUL.md / IDENTITY.md / USER.md / TOOLS.md into the system
# prompt) reads from `agents.defaults.workspace`, NOT `agents.list[].agentDir`.
# Our config sets workspace=/workspace/.openclaw/workspace, so persona
# files MUST live there to actually reach the LLM. (We also seed agentDir
# for legacy lookups, but that path alone is invisible to the model.)
#
# Skills go in <workspace>/skills/<name>/SKILL.md per OpenClaw convention.
WORKSPACE_DIR=/workspace/.openclaw/workspace
AGENT_DIR=/workspace/.openclaw/agent
SKILLS_DIR="$WORKSPACE_DIR/skills"
mkdir -p "$WORKSPACE_DIR" "$AGENT_DIR" "$SKILLS_DIR"

echo "[entrypoint] seeding persona files into $WORKSPACE_DIR (Project Context)"
# Seed logic lives in seed-persona.sh so the adapter can re-run it
# before every turn â€” restoring any persona/skill files the agent
# may have deleted in the previous turn (defense-in-depth backstop
# for the SOUL.md self-preservation rule).
sh /srv/citra/seed-persona.sh

# Ephemeral scratch under /workspace.
mkdir -p /workspace/tmp /workspace/reports

# ---- 5a. DuckDB spill directory + tuning defaults ------------------------
# Action-sandbox-host injects DUCKDB_* env vars derived from the container's
# resource caps; we set conservative fallbacks here so the analyst loop
# still works in dev / docker-compose deployments that don't go through
# the host scheduler.
#
# memory_limit: â‰ˆ 50% of container RAM (host scheduler computes this).
# threads: â‰ˆ vCPU quota (host scheduler computes this).
# temp_directory: a real /workspace path (NOT /tmp, which is 64M) so
#   DuckDB can spill big hash tables / sorts when the working set exceeds
#   memory_limit. Required for any SAP-scale aggregation.
# max_temp_directory_size: cap spill so a runaway query can't exhaust
#   the /workspace tmpfs and crash other agent flows holding bytes there.
: "${DUCKDB_MEMORY_LIMIT:=4GB}"
: "${DUCKDB_THREADS:=4}"
: "${DUCKDB_TEMP_DIRECTORY:=/workspace/.duckdb-temp}"
: "${DUCKDB_MAX_TEMP_DIRECTORY_SIZE:=3GB}"
export DUCKDB_MEMORY_LIMIT DUCKDB_THREADS DUCKDB_TEMP_DIRECTORY DUCKDB_MAX_TEMP_DIRECTORY_SIZE
mkdir -p "$DUCKDB_TEMP_DIRECTORY"
echo "[entrypoint] DuckDB: memory_limit=$DUCKDB_MEMORY_LIMIT threads=$DUCKDB_THREADS temp_directory=$DUCKDB_TEMP_DIRECTORY max_temp=$DUCKDB_MAX_TEMP_DIRECTORY_SIZE"

# ---- 5b. Bulk-restore durable memory from Citra-Service ------------------
# OpenClaw's native ``memory_search`` / ``memory_get`` tools read markdown
# notes from /workspace/memory/. The container's filesystem is tmpfs and
# rebuilt every cold start, so prior memory would be invisible without
# this restore step.
#
# Source of truth lives in Mongo (``deep_research_memory`` in the isolated
# action-chat-open-claw-temp DB). We pull every memory note for this user
# and materialise each as ``<slug>.md`` so the agent can grep its own
# prior insights at zero cost. Write-back happens transactionally via the
# ``memory`` toolkit (citra_toolkit.scratch.memory.put) â€” direct disk
# writes are NOT persisted across containers.
#
# Failures here are non-fatal: a fresh user simply has an empty memory
# dir, and the agent operates without prior context.
MEMORY_DIR=/workspace/memory
mkdir -p "$MEMORY_DIR"

if [ -n "${CITRA_AGENT_PROXY_BASE_URL:-}" ] && [ -n "${CITRA_AGENT_SCOPED_TOKEN:-}" ]; then
  echo "[entrypoint] bulk-restoring durable memory into $MEMORY_DIR"
  python3 - <<'PY' || echo "[entrypoint] memory bulk-restore failed (non-fatal); continuing"
import json
import os
import re
import sys
import urllib.error
import urllib.request

base = (os.environ.get("CITRA_AGENT_PROXY_BASE_URL") or "").rstrip("/")
token = os.environ.get("CITRA_AGENT_SCOPED_TOKEN") or ""
if not base or not token:
    sys.exit(0)

url = f"{base}/scratch/memory?limit=500"
req = urllib.request.Request(url, headers={
    "Authorization": f"Bearer {token}",
    "Accept": "application/json",
})
try:
    with urllib.request.urlopen(req, timeout=15) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
    print(f"[memory-restore] fetch failed: {e}", file=sys.stderr)
    sys.exit(0)
except Exception as e:  # noqa: BLE001
    print(f"[memory-restore] parse failed: {e}", file=sys.stderr)
    sys.exit(0)

items = payload.get("items") or []
if not items:
    print("[memory-restore] no prior notes for this user")
    sys.exit(0)

out_dir = "/workspace/memory"
os.makedirs(out_dir, exist_ok=True)
slug_re = re.compile(r"[^a-zA-Z0-9._-]")
written = 0
for d in items:
    slug = (d.get("slug") or "").strip()
    if not slug:
        continue
    safe = slug_re.sub("_", slug)[:96]
    if not safe:
        continue
    title = d.get("title") or slug
    body = d.get("body") or ""
    tags = d.get("tags") or []
    parts = [f"# {title}", "", body.rstrip()]
    if tags:
        parts.extend(["", f"<!-- tags: {', '.join(map(str, tags))} -->"])
    try:
        path = os.path.join(out_dir, f"{safe}.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(parts) + "\n")
        written += 1
    except OSError as e:
        print(f"[memory-restore] could not write {safe}.md: {e}", file=sys.stderr)

print(f"[memory-restore] restored {written}/{len(items)} memory notes")
PY
else
  echo "[entrypoint] CITRA_AGENT_PROXY_BASE_URL or CITRA_AGENT_SCOPED_TOKEN unset; skipping memory bulk-restore"
fi

# ---- 6. Config preflight --------------------------------------------------
# Surface schema errors loudly instead of silently failing the health loop.
if ! openclaw config validate 2>/tmp/openclaw-validate.log; then
  echo "[entrypoint] openclaw config validate FAILED. Config file:" >&2
  cat "$OPENCLAW_CFG_DIR/openclaw.json" >&2
  echo "[entrypoint] validate output:" >&2
  cat /tmp/openclaw-validate.log >&2
  exit 1
fi


# ---- 7. Start OpenClaw gateway (background) ------------------------------
# `gateway run` is the foreground variant. `gateway start` is the systemd/
# launchd/schtasks service manager form, which we don't use inside a
# minimal container (no systemd).
#
# We do NOT pass --raw-stream anymore. The control adapter now speaks
# OpenClaw's native WebSocket gateway protocol (chat.send / chat.abort /
# sessions.messages.subscribe) so server-side tool activity surfaces
# through session.message + session.tool events directly, with no JSONL
# tail required.
echo "[entrypoint] starting openclaw gateway"
openclaw gateway run >/tmp/openclaw.log 2>&1 &
OPENCLAW_PID=$!

# ---- 8. Wait for gateway readiness ---------------------------------------
# The gateway is a WebSocket server on 127.0.0.1:18789 (bind=loopback in
# config). Plain HTTP GETs to /healthz are NOT exposed; instead we detect
# readiness by:
#   (a) "[gateway] ready" appearing in the log, AND
#   (b) a TCP connect to 127.0.0.1:18789 succeeding.
echo "[entrypoint] waiting for openclaw gateway to bind :18789"
READY_PROBE='import socket,sys
s=socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.settimeout(1.0)
try:
    s.connect(("127.0.0.1", 18789))
    sys.exit(0)
except Exception:
    sys.exit(1)
finally:
    s.close()
'
i=0
while [ $i -lt 90 ]; do
  if python3 -c "$READY_PROBE" 2>/dev/null \
     && grep -q '\[gateway\] ready' /tmp/openclaw.log 2>/dev/null; then
    echo "[entrypoint] openclaw gateway ready"
    break
  fi
  if ! kill -0 "$OPENCLAW_PID" 2>/dev/null; then
    echo "[entrypoint] openclaw gateway died early; tail of log:" >&2
    tail -n 200 /tmp/openclaw.log >&2 || true
    exit 1
  fi
  i=$((i+1))
  sleep 1
done

if [ $i -ge 90 ]; then
  echo "[entrypoint] timed out waiting for openclaw gateway" >&2
  tail -n 200 /tmp/openclaw.log >&2 || true
  exit 1
fi


# ---- 9. Citra Control Adapter (foreground) -------------------------------
echo "[entrypoint] starting citra control adapter on :${CITRA_AGENT_SANDBOX_PORT:-8090}"
cd /srv/citra
exec python3 -m runner.adapter
