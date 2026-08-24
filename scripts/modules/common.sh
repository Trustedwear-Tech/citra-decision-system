#!/usr/bin/env bash
# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

#
# =============================================================================
# Citra AI — Shared Setup Utilities
# =============================================================================
# Sourced by all setup modules. Do not run directly.
# =============================================================================

# Prevent double-sourcing
[[ -n "$_CITRA_COMMON_LOADED" ]] && return 0
_CITRA_COMMON_LOADED=1

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE="${ENV_FILE:-$REPO_ROOT/.env}"
COMPOSE_DIR="$REPO_ROOT/infrastructure/compose"
STATE_FILE="$REPO_ROOT/.setup-state.json"
MODULES_DIR="$REPO_ROOT/scripts/modules"

# ---------------------------------------------------------------------------
# Colors & output
# ---------------------------------------------------------------------------
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
DIM='\033[2m'
NC='\033[0m'

info()    { echo -e "${CYAN}[INFO]${NC} $1"; }
ok()      { echo -e "${GREEN}[  OK]${NC} $1"; }
warn()    { echo -e "${YELLOW}[WARN]${NC} $1"; }
fail()    { echo -e "${RED}[FAIL]${NC} $1"; }
error()   { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }
step()    { echo -e "\n${BOLD}${CYAN}── Step $1: $2${NC}"; }
header()  { echo -e "\n${BOLD}════════════════════════════════════════${NC}\n  ${BOLD}$1${NC}\n${BOLD}════════════════════════════════════════${NC}\n"; }

# ---------------------------------------------------------------------------
# Docker Compose helpers
# ---------------------------------------------------------------------------
detect_compose_cmd() {
    if docker compose version >/dev/null 2>&1; then
        echo "docker compose"
    elif command -v docker-compose >/dev/null 2>&1; then
        echo "docker-compose"
    else
        error "Docker Compose is not installed."
    fi
}

COMPOSE_CMD="$(detect_compose_cmd 2>/dev/null || true)"

# Run docker compose with base + specific module compose file
# Usage: compose_up <compose-file> [extra-args...]
compose_up() {
    local file="$1"; shift
    $COMPOSE_CMD -f "$COMPOSE_DIR/docker-compose.base.yml" \
                 -f "$COMPOSE_DIR/$file" \
                 --env-file "$ENV_FILE" \
                 up -d --build "$@"
}

compose_down() {
    local file="$1"; shift
    $COMPOSE_CMD -f "$COMPOSE_DIR/docker-compose.base.yml" \
                 -f "$COMPOSE_DIR/$file" \
                 --env-file "$ENV_FILE" \
                 down "$@"
}

compose_ps() {
    local file="$1"; shift
    $COMPOSE_CMD -f "$COMPOSE_DIR/docker-compose.base.yml" \
                 -f "$COMPOSE_DIR/$file" \
                 --env-file "$ENV_FILE" \
                 ps "$@"
}

# ---------------------------------------------------------------------------
# .env helpers
# ---------------------------------------------------------------------------

# Load .env into current shell (doesn't export — just for reading)
load_env() {
    [[ -f "$ENV_FILE" ]] || return 1
    set -o allexport
    # shellcheck disable=SC1090
    source "$ENV_FILE" 2>/dev/null
    set +o allexport
}

# Get a value from .env file
env_get() {
    local key="$1"
    if [[ -f "$ENV_FILE" ]]; then
        grep -E "^${key}=" "$ENV_FILE" 2>/dev/null | tail -1 | cut -d'=' -f2- | sed 's/^["'\''"]//;s/["'\''"]$//'
    fi
}

# Set a key=value in .env (updates existing or appends)
env_set() {
    local key="$1" value="$2"
    [[ -f "$ENV_FILE" ]] || touch "$ENV_FILE"
    if grep -qE "^${key}=" "$ENV_FILE" 2>/dev/null; then
        # Use a delimiter that won't conflict with values
        sed -i "s|^${key}=.*|${key}=${value}|" "$ENV_FILE"
    else
        echo "${key}=${value}" >> "$ENV_FILE"
    fi
}

# Check if .env exists, create from template if not
ensure_env() {
    if [[ ! -f "$ENV_FILE" ]]; then
        info "Creating .env from template..."
        cp "$REPO_ROOT/.env.example" "$ENV_FILE"
        # Generate random JWT secret
        local jwt
        jwt=$(openssl rand -hex 32 2>/dev/null || head -c 64 /dev/urandom | xxd -p | head -c 64)
        env_set "JWT_SECRET" "$jwt"
        # Generate random MongoDB password
        local mongo_pass
        mongo_pass=$(openssl rand -hex 16 2>/dev/null || head -c 32 /dev/urandom | xxd -p | head -c 32)
        sed -i "s/MONGODB_PASSWORD:-citradev/MONGODB_PASSWORD:-$mongo_pass/" "$ENV_FILE"
        ok ".env created with random secrets"
    fi
}

# ---------------------------------------------------------------------------
# Auto-detect: is a service already configured with a non-default URL?
# Returns 0 (true) if configured externally, 1 if default/local
# ---------------------------------------------------------------------------
is_configured() {
    local key="$1"
    shift
    local defaults=("$@")  # List of default values that mean "local/not configured"
    local current
    current="$(env_get "$key")"

    [[ -z "$current" ]] && return 1  # Not set at all

    for default in "${defaults[@]}"; do
        [[ "$current" == "$default" ]] && return 1
    done

    return 0  # Has a non-default value = externally configured
}

# ---------------------------------------------------------------------------
# Interactive: prompt user about an already-configured or unconfigured service
# Usage: prompt_service_config "MongoDB" "MONGODB_CONN_STRING" "mongodb://root:citradev@mongodb:27017/..." "default1" "default2"
# Returns: "local" | "cloud" | "keep"
# Sets global PROMPT_VALUE to the user-provided URL/key if cloud chosen
# ---------------------------------------------------------------------------
PROMPT_VALUE=""

prompt_service_config() {
    local name="$1"
    local env_key="$2"
    local default_val="$3"
    shift 3
    local defaults=("$@")
    local current
    current="$(env_get "$env_key")"

    if is_configured "$env_key" "${defaults[@]}"; then
        # Already configured with a non-default value
        echo ""
        info "${name} is already configured:"
        echo -e "  ${DIM}${env_key}=${current}${NC}"
        echo ""
        echo "  1) Keep current configuration"
        echo "  2) Change to a different URL/key"
        echo "  3) Switch to local installation"
        echo ""
        local choice
        read -rp "  Choose (1-3) [1]: " choice
        case "$choice" in
            2)
                read -rp "  Enter new ${name} URL/connection string: " PROMPT_VALUE
                echo "cloud"
                return
                ;;
            3)
                PROMPT_VALUE=""
                echo "local"
                return
                ;;
            *)
                PROMPT_VALUE="$current"
                echo "keep"
                return
                ;;
        esac
    else
        # Not configured or using default
        echo ""
        info "Configure ${name}:"
        echo "  1) Install locally (Docker)"
        echo "  2) Use external/cloud URL"
        echo ""
        local choice
        read -rp "  Choose (1-2) [1]: " choice
        case "$choice" in
            2)
                read -rp "  Enter ${name} URL/connection string: " PROMPT_VALUE
                echo "cloud"
                return
                ;;
            *)
                PROMPT_VALUE=""
                echo "local"
                return
                ;;
        esac
    fi
}

# ---------------------------------------------------------------------------
# Health check helpers
# ---------------------------------------------------------------------------

# Wait for an HTTP health endpoint
# Usage: wait_for_health <name> <url> [timeout_seconds=120]
wait_for_health() {
    local name="$1" url="$2" timeout="${3:-120}"
    local elapsed=0
    info "Waiting for ${name} to be healthy..."
    while [[ $elapsed -lt $timeout ]]; do
        if curl -sf "$url" >/dev/null 2>&1; then
            ok "${name} is healthy"
            return 0
        fi
        sleep 3
        elapsed=$((elapsed + 3))
    done
    fail "${name} did not become healthy within ${timeout}s"
    return 1
}

# Wait for a TCP port to be reachable
# Usage: wait_for_tcp <name> <host> <port> [timeout_seconds=60]
wait_for_tcp() {
    local name="$1" host="$2" port="$3" timeout="${4:-60}"
    local elapsed=0
    info "Waiting for ${name} on ${host}:${port}..."
    while [[ $elapsed -lt $timeout ]]; do
        if (echo >/dev/tcp/"$host"/"$port") 2>/dev/null; then
            ok "${name} is reachable on port ${port}"
            return 0
        fi
        sleep 2
        elapsed=$((elapsed + 2))
    done
    fail "${name} not reachable on ${host}:${port} within ${timeout}s"
    return 1
}

# ---------------------------------------------------------------------------
# Setup state tracking (.setup-state.json)
# ---------------------------------------------------------------------------

# Mark a module as completed
state_set_completed() {
    local module="$1"
    local timestamp
    timestamp="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

    if [[ ! -f "$STATE_FILE" ]]; then
        echo '{}' > "$STATE_FILE"
    fi

    # Simple JSON manipulation with sed (no jq dependency)
    if grep -q "\"$module\"" "$STATE_FILE" 2>/dev/null; then
        sed -i "s|\"$module\":\"[^\"]*\"|\"$module\":\"$timestamp\"|" "$STATE_FILE"
    else
        # Add entry before the closing brace
        sed -i "s|}$|,\"$module\":\"$timestamp\"}|" "$STATE_FILE"
        # Fix leading comma if first entry
        sed -i 's/{,/{/' "$STATE_FILE"
    fi
}

# Check if a module was already completed
state_is_completed() {
    local module="$1"
    [[ -f "$STATE_FILE" ]] && grep -q "\"$module\"" "$STATE_FILE" 2>/dev/null
}

# Reset state for a specific module
state_reset() {
    local module="$1"
    [[ -f "$STATE_FILE" ]] || return 0
    sed -i "s|,\"$module\":\"[^\"]*\"||g; s|\"$module\":\"[^\"]*\",||g; s|\"$module\":\"[^\"]*\"||g" "$STATE_FILE"
}

# Reset all state
state_reset_all() {
    echo '{}' > "$STATE_FILE"
}

# ---------------------------------------------------------------------------
# Module runner — handles skip/resume/force logic
# Usage: run_module <module_id> <module_name> [--force]
# ---------------------------------------------------------------------------
run_module() {
    local module_id="$1"
    local module_name="$2"
    local force="${3:-}"

    local script="$MODULES_DIR/${module_id}.sh"

    if [[ ! -f "$script" ]]; then
        fail "Module script not found: $script"
        return 1
    fi

    # Check if already completed (unless --force)
    if [[ "$force" != "--force" ]] && state_is_completed "$module_id"; then
        echo -e "  ${DIM}[SKIP] ${module_name} (already completed — use --force to re-run)${NC}"
        return 0
    fi

    header "$module_name"

    # Source and run the module
    # shellcheck disable=SC1090
    source "$script"

    local exit_code=$?
    if [[ $exit_code -eq 0 ]]; then
        state_set_completed "$module_id"
        ok "${module_name} — completed"
    else
        fail "${module_name} — failed (exit code: $exit_code)"
    fi
    return $exit_code
}

# ---------------------------------------------------------------------------
# GPU detection
# ---------------------------------------------------------------------------
detect_gpu() {
    if command -v nvidia-smi >/dev/null 2>&1; then
        GPU_COUNT=$(nvidia-smi --list-gpus 2>/dev/null | wc -l || echo "0")
        GPU_AVAILABLE=true
    else
        GPU_COUNT=0
        GPU_AVAILABLE=false
    fi
}

# Check connectivity to a URL (HTTP 2xx/3xx = success)
check_connectivity() {
    local url="$1"
    curl -sf --max-time 5 "$url" >/dev/null 2>&1
}

# Quick test: can we reach a MongoDB connection string?
check_mongodb_connectivity() {
    local conn="$1"
    # Extract host:port from connection string
    local host_port
    host_port=$(echo "$conn" | sed -E 's|^mongodb(\+srv)?://[^@]*@||; s|/.*||; s|,.*||')
    local host="${host_port%%:*}"
    local port="${host_port##*:}"
    [[ "$port" == "$host" ]] && port=27017
    (echo >/dev/tcp/"$host"/"$port") 2>/dev/null
}
