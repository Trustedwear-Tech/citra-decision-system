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
# Citra AI Platform — Environment Validator & Setup Helper
# =============================================================================
#
# This script does NOT install anything. It validates your .env configuration,
# checks connectivity to configured services, and generates initial .env files.
#
# Usage:
#   ./scripts/setup.sh                  # Show configuration status
#   ./scripts/setup.sh --check          # Validate connectivity to all endpoints
#   ./scripts/setup.sh --generate-env   # Generate .env from template with random secrets
#   ./scripts/setup.sh --help           # Show this help
#
# For deployment instructions, see:
#   docs/deployment-guide.md            # Docker Compose / EC2 deployment
#   docs/components/*.md                # Individual component guides
#
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="$ROOT_DIR/.env"

# ---------------------------------------------------------------------------
# Colors
# ---------------------------------------------------------------------------
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
DIM='\033[2m'
NC='\033[0m'

ok()   { echo -e "  ${GREEN}✓${NC} $1"; }
warn() { echo -e "  ${YELLOW}!${NC} $1"; }
fail() { echo -e "  ${RED}✗${NC} $1"; }
info() { echo -e "  ${DIM}$1${NC}"; }

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
env_get() {
    local key="$1"
    if [[ -f "$ENV_FILE" ]]; then
        grep -E "^${key}=" "$ENV_FILE" 2>/dev/null | head -1 | cut -d'=' -f2- | sed 's/^["'\''"]//;s/["'\''"]$//'
    fi
}

is_set() {
    local val
    val="$(env_get "$1")"
    [[ -n "$val" && "$val" != "change-me"* && "$val" != "your-"* ]]
}

mask() {
    local val="$1"
    if [[ ${#val} -le 8 ]]; then
        echo "****"
    else
        echo "${val:0:4}...${val: -4}"
    fi
}

check_tcp() {
    local host="$1" port="$2" timeout="${3:-3}"
    if command -v nc &>/dev/null; then
        nc -z -w "$timeout" "$host" "$port" 2>/dev/null
    elif command -v timeout &>/dev/null; then
        timeout "$timeout" bash -c "echo >/dev/tcp/$host/$port" 2>/dev/null
    elif command -v curl &>/dev/null; then
        curl -sf --connect-timeout "$timeout" "http://${host}:${port}" &>/dev/null || \
        curl -sf --connect-timeout "$timeout" "telnet://${host}:${port}" &>/dev/null
    else
        return 1
    fi
}

check_http() {
    local url="$1" timeout="${2:-5}"
    if command -v curl &>/dev/null; then
        curl -sf --connect-timeout "$timeout" --max-time "$((timeout + 2))" "$url" &>/dev/null
    elif command -v wget &>/dev/null; then
        wget -q --timeout="$timeout" -O /dev/null "$url" 2>/dev/null
    else
        return 1
    fi
}

parse_host_port() {
    local url="$1"
    local hostport="${url#*://}"
    hostport="${hostport%%/*}"
    hostport="${hostport##*@}"
    if [[ "$hostport" == *:* ]]; then
        echo "${hostport%%:*}" "${hostport##*:}"
    else
        echo "$hostport" "80"
    fi
}

# ---------------------------------------------------------------------------
# --generate-env
# ---------------------------------------------------------------------------
generate_env() {
    if [[ -f "$ENV_FILE" ]]; then
        echo ""
        warn ".env already exists at $ENV_FILE"
        read -rp "  Overwrite? (y/N): " confirm
        if [[ "$confirm" != [yY] ]]; then
            info "Aborted."
            exit 0
        fi
    fi

    local template="$ROOT_DIR/.env.example"
    if [[ ! -f "$template" ]]; then
        fail "Template not found: .env.example"
        exit 1
    fi

    cp "$template" "$ENV_FILE"

    local jwt_secret
    jwt_secret="$(openssl rand -hex 32 2>/dev/null || head -c 64 /dev/urandom | od -An -tx1 | tr -d ' \n')"
    sed -i "s|^JWT_SECRET=.*|JWT_SECRET=$jwt_secret|" "$ENV_FILE"

    local mcp_secret
    mcp_secret="$(openssl rand -hex 16 2>/dev/null || head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')"
    sed -i "s|^MCP_OAUTH_CLIENT_SECRET=.*|MCP_OAUTH_CLIENT_SECRET=$mcp_secret|" "$ENV_FILE"

    echo ""
    ok ".env generated at $ENV_FILE"
    ok "Random secrets generated for JWT_SECRET, MCP_OAUTH_CLIENT_SECRET"
    echo ""
    info "Next steps:"
    info "  1. Edit .env — configure MongoDB, Milvus, Redis, Object Storage"
    info "  2. Set your LLM provider (OpenRouter or any OpenAI-compatible API)"
    info "  3. Run: ./scripts/setup.sh --check    to validate connectivity"
    info ""
    info "See docs/deployment-guide.md and docs/components/ for instructions."
}

# ---------------------------------------------------------------------------
# --check (connectivity validation)
# ---------------------------------------------------------------------------
check_connectivity() {
    echo ""
    echo -e "  ${BOLD}Connectivity Check${NC}"
    echo -e "  ${DIM}Testing configured endpoints...${NC}"
    echo ""

    local pass=0
    local fail_count=0
    local skip=0

    # --- MongoDB ---
    echo -e "  ${BOLD}MongoDB${NC}"
    local mongo_conn
    mongo_conn="$(env_get MONGODB_CONN_STRING)"
    if [[ -z "$mongo_conn" ]]; then
        warn "MONGODB_CONN_STRING not set"
        skip=$((skip + 1))
    elif [[ "$mongo_conn" == *"mongodb+srv://"* ]]; then
        if command -v mongosh &>/dev/null; then
            if mongosh "$mongo_conn" --eval "db.adminCommand('ping')" --quiet 2>/dev/null; then
                ok "MongoDB Atlas: connected"
                pass=$((pass + 1))
            else
                fail "MongoDB Atlas: connection failed"
                fail_count=$((fail_count + 1))
            fi
        else
            info "MongoDB Atlas configured (install mongosh to verify): $(mask "$mongo_conn")"
            skip=$((skip + 1))
        fi
    else
        read -r host port <<< "$(parse_host_port "$mongo_conn")"
        if check_tcp "$host" "$port"; then
            ok "MongoDB: $host:$port reachable"
            pass=$((pass + 1))
        else
            fail "MongoDB: $host:$port unreachable"
            fail_count=$((fail_count + 1))
        fi
    fi

    # --- Redis ---
    echo -e "  ${BOLD}Redis${NC}"
    local redis_host redis_port
    redis_host="$(env_get REDIS_HOST)"
    redis_port="$(env_get REDIS_PORT)"
    redis_port="${redis_port:-6379}"
    if [[ -z "$redis_host" ]]; then
        warn "REDIS_HOST not set"
        skip=$((skip + 1))
    else
        if check_tcp "$redis_host" "$redis_port"; then
            ok "Redis: $redis_host:$redis_port reachable"
            pass=$((pass + 1))
        else
            fail "Redis: $redis_host:$redis_port unreachable"
            fail_count=$((fail_count + 1))
        fi
    fi

    # --- Milvus ---
    echo -e "  ${BOLD}Milvus${NC}"
    local milvus_uri
    milvus_uri="$(env_get MILVUS_URI)"
    if [[ -z "$milvus_uri" ]]; then
        warn "MILVUS_URI not set"
        skip=$((skip + 1))
    else
        read -r host port <<< "$(parse_host_port "$milvus_uri")"
        if check_tcp "$host" "$port"; then
            ok "Milvus: $host:$port reachable"
            pass=$((pass + 1))
        else
            fail "Milvus: $host:$port unreachable"
            fail_count=$((fail_count + 1))
        fi
    fi

    # --- Object Storage ---
    echo -e "  ${BOLD}Object Storage${NC}"
    local s3_endpoint
    s3_endpoint="$(env_get AWS_S3_ENDPOINT_URL)"
    if [[ -z "$s3_endpoint" ]]; then
        warn "AWS_S3_ENDPOINT_URL not set (using AWS S3 default?)"
        skip=$((skip + 1))
    else
        if check_http "${s3_endpoint}/minio/health/live" 3 || check_http "$s3_endpoint" 3; then
            ok "Object Storage: $s3_endpoint reachable"
            pass=$((pass + 1))
        else
            read -r host port <<< "$(parse_host_port "$s3_endpoint")"
            if check_tcp "$host" "$port"; then
                ok "Object Storage: $host:$port reachable (TCP)"
                pass=$((pass + 1))
            else
                fail "Object Storage: $s3_endpoint unreachable"
                fail_count=$((fail_count + 1))
            fi
        fi
    fi

    # --- Chat Completion LLM ---
    echo -e "  ${BOLD}Chat Completion LLM${NC}"
    local llm_base_url llm_api_key llm_model
    llm_base_url="$(env_get LLM_BASE_URL)"
    llm_api_key="$(env_get LLM_API_KEY)"
    llm_model="$(env_get LLM_MODEL)"
    if [[ -n "$llm_base_url" ]]; then
        if check_http "$llm_base_url/models" 5; then
            ok "LLM: $llm_base_url reachable (model: ${llm_model:-not set})"
            pass=$((pass + 1))
        else
            read -r host port <<< "$(parse_host_port "$llm_base_url")"
            if check_tcp "$host" "$port"; then
                ok "LLM: $host:$port reachable (TCP, model: ${llm_model:-not set})"
                pass=$((pass + 1))
            else
                fail "LLM: $llm_base_url unreachable"
                fail_count=$((fail_count + 1))
            fi
        fi
    elif [[ -n "$llm_api_key" ]]; then
        ok "LLM API key: configured (model: ${llm_model:-not set})"
        pass=$((pass + 1))
    else
        warn "LLM_BASE_URL not set"
        skip=$((skip + 1))
    fi

    # --- Internal Services ---
    echo -e "  ${BOLD}Internal Services${NC}"
    local svc_checks=(
        "USER_SERVICE_URL:User Service:7004"
        "RERANKER_URL:Reranker:7302"
        "DUCKDB_SERVICE_URL:DuckDB Query:7301"
        "PLAYWRIGHT_RENDER_URL:Playwright Render:3001"
    )
    for entry in "${svc_checks[@]}"; do
        local var_name="${entry%%:*}"
        local remainder="${entry#*:}"
        local label="${remainder%%:*}"
        local default_port="${remainder##*:}"
        local url
        url="$(env_get "$var_name")"
        if [[ -n "$url" ]]; then
            read -r host port <<< "$(parse_host_port "$url")"
            if check_tcp "$host" "$port" 2; then
                ok "$label: $host:$port reachable"
                pass=$((pass + 1))
            else
                warn "$label: $host:$port not reachable (may not be started yet)"
            fi
        else
            info "$label: not configured (will use default localhost:$default_port)"
        fi
    done

    # --- Summary ---
    echo ""
    echo "  ────────────────────────────────────"
    echo -e "  ${GREEN}Passed${NC}: $pass  ${YELLOW}Skipped${NC}: $skip  ${RED}Failed${NC}: $fail_count"
    echo "  ────────────────────────────────────"
    echo ""

    if [[ $fail_count -gt 0 ]]; then
        info "Fix failed connections, then re-run: ./scripts/setup.sh --check"
        exit 1
    fi
}

# ---------------------------------------------------------------------------
# Default: show configuration status
# ---------------------------------------------------------------------------
show_status() {
    echo ""
    echo -e "${BOLD}${CYAN}"
    cat << 'BANNER'
   _____ _ _                       _    ___
  / ____(_) |                     / \  |_ _|
 | |    _| |_ _ __ __ _         / _ \  | |
 | |   | | __| '__/ _` |       / ___ \ | |
 | |___| | |_| | | (_| |      / /   \ \|___|
  \_____|_|\__|_|  \__,_|     /_/     \_\
BANNER
    echo -e "${NC}"
    echo -e "  ${BOLD}Environment Configuration Status${NC}"
    echo ""

    if [[ ! -f "$ENV_FILE" ]]; then
        fail ".env file not found"
        echo ""
        info "Generate one:  ./scripts/setup.sh --generate-env"
        info "Or copy:       cp .env.example .env"
        echo ""
        exit 1
    fi

    echo -e "  ${BOLD}Core Infrastructure${NC}"
    echo ""

    # MongoDB
    local mongo_conn
    mongo_conn="$(env_get MONGODB_CONN_STRING)"
    if [[ -n "$mongo_conn" ]]; then
        if [[ "$mongo_conn" == *"mongodb+srv://"* ]]; then
            ok "MongoDB:         Atlas ($(mask "$mongo_conn"))"
        elif [[ "$mongo_conn" == *"localhost"* || "$mongo_conn" == *"127.0.0.1"* ]]; then
            ok "MongoDB:         localhost"
        else
            ok "MongoDB:         $(mask "$mongo_conn")"
        fi
    else
        fail "MongoDB:         not configured"
    fi

    # Milvus
    local milvus_uri
    milvus_uri="$(env_get MILVUS_URI)"
    if [[ -n "$milvus_uri" ]]; then
        if [[ "$milvus_uri" == *"zillizcloud"* ]]; then
            ok "Milvus:          Zilliz Cloud"
        else
            ok "Milvus:          $milvus_uri"
        fi
    else
        fail "Milvus:          not configured"
    fi

    # Object Storage
    local s3_endpoint
    s3_endpoint="$(env_get BUCKET_ENDPOINT_URL)"
    if [[ -n "$s3_endpoint" ]]; then
        if [[ "$s3_endpoint" == *"amazonaws.com"* ]]; then
            ok "Object Storage:  AWS S3"
        elif [[ "$s3_endpoint" == *"minio"* || "$s3_endpoint" == *":9000"* ]]; then
            ok "Object Storage:  MinIO ($s3_endpoint)"
        else
            ok "Object Storage:  $s3_endpoint"
        fi
    elif [[ -n "$(env_get BUCKET_NAME)" ]]; then
        ok "Object Storage:  AWS S3 (no endpoint URL)"
    else
        fail "Object Storage:  not configured"
    fi

    # Redis
    local redis_host
    redis_host="$(env_get REDIS_HOST)"
    if [[ -n "$redis_host" ]]; then
        ok "Redis:           $redis_host:$(env_get REDIS_PORT)"
    else
        warn "Redis:           not configured (optional but recommended)"
    fi

    echo ""
    echo -e "  ${BOLD}AI / LLM Configuration${NC}"
    echo ""

    local llm_provider
    local llm_base_url llm_model
    llm_base_url="$(env_get LLM_BASE_URL)"
    llm_model="$(env_get LLM_MODEL)"
    if [[ -n "$llm_base_url" ]]; then
        ok "Chat Completion: ${llm_model:-not set} @ $llm_base_url"
    elif [[ -n "$(env_get LLM_API_KEY)" ]]; then
        ok "Chat Completion: ${llm_model:-not set} (API key configured)"
    else
        fail "Chat Completion: not configured (set LLM_BASE_URL + LLM_API_KEY + LLM_MODEL)"
    fi

    local vision_url
    vision_url="$(env_get VISION_BASE_URL)"
    if [[ -n "$vision_url" ]]; then
        ok "Vision/OCR:      $vision_url"
    else
        warn "Vision/OCR:      not configured (optional)"
    fi

    local imagegen_url
    imagegen_url="$(env_get IMAGE_GEN_BASE_URL)"
    if [[ -n "$imagegen_url" ]]; then
        ok "Image Gen:       $imagegen_url"
    else
        warn "Image Gen:       not configured (optional)"
    fi

    local embed_url
    embed_url="$(env_get EMBEDDING_BASE_URL)"
    if [[ -n "$embed_url" ]]; then
        ok "Embeddings:      $(env_get EMBEDDING_MODEL) @ $embed_url"
    else
        warn "Embeddings:      not configured"
    fi

    echo ""
    echo -e "  ${BOLD}Application Services${NC}"
    echo ""

    local jwt
    jwt="$(env_get JWT_SECRET)"
    if [[ -n "$jwt" && "$jwt" != "change-me"* ]]; then
        ok "JWT Secret:      configured"
    else
        fail "JWT Secret:      not set (run --generate-env or set manually)"
    fi

    local env_var label
    for pair in \
        "CITRA_SERVICE_PORT:Citra-Service (Core API)" \
        "USER_SERVICE_PORT:User-Service (Auth)" \
        "DUCKDB_SERVICE_PORT:DuckDB Query" \
        "RERANKER_PORT:Reranker" \
        "PLAYWRIGHT_PORT:Playwright Render"
    do
        env_var="${pair%%:*}"
        label="${pair##*:}"
        local port_val
        port_val="$(env_get "$env_var")"
        if [[ -n "$port_val" ]]; then
            ok "$label: port $port_val"
        else
            info "$label: using default"
        fi
    done

    echo ""
    echo -e "  ${BOLD}Optional Features${NC}"
    echo ""

    local search_base_url
    search_base_url="$(env_get SEARCH_BASE_URL)"
    if [[ -n "$search_base_url" ]]; then
        ok "Internet Search: $(env_get SEARCH_MODEL) @ $search_base_url"
    elif [[ -n "$(env_get SEARCH_API_KEY)" ]]; then
        ok "Internet Search: API key configured"
    else
        info "Internet Search: not configured"
    fi

    if is_set "SERPER_API_KEY"; then
        ok "Web Search API:  Serper configured"
    else
        info "Web Search API:  not configured (Reader search disabled)"
    fi

    local vault_addr
    vault_addr="$(env_get VAULT_ADDR)"
    if [[ -n "$vault_addr" ]]; then
        ok "HashiCorp Vault: $vault_addr"
    else
        info "HashiCorp Vault: not configured (using .env file)"
    fi

    local alert_transport
    alert_transport="$(env_get ALERT_TRANSPORT)"
    if [[ -n "$alert_transport" && "$alert_transport" != "log" ]]; then
        ok "Alerts:          $alert_transport"
    else
        info "Alerts:          log only (set ALERT_TRANSPORT for email/webhook)"
    fi

    echo ""
    echo "  ────────────────────────────────────────────────────────────"
    echo -e "  ${BOLD}Commands${NC}"
    echo "    Validate connectivity:   ./scripts/setup.sh --check"
    echo "    Regenerate .env:         ./scripts/setup.sh --generate-env"
    echo ""
    echo -e "  ${BOLD}Documentation${NC}"
    echo "    Deployment guide:        docs/deployment-guide.md"
    echo "    Component guides:        docs/components/"
    echo "  ────────────────────────────────────────────────────────────"
    echo ""
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
case "${1:-}" in
    --generate-env)
        generate_env
        ;;
    --check)
        if [[ ! -f "$ENV_FILE" ]]; then
            fail ".env not found. Run: ./scripts/setup.sh --generate-env"
            exit 1
        fi
        check_connectivity
        ;;
    --help|-h)
        head -24 "$0" | grep -E "^#" | sed 's/^# \?//'
        ;;
    "")
        show_status
        ;;
    *)
        echo "Unknown option: $1"
        echo "Usage: ./scripts/setup.sh [--check | --generate-env | --help]"
        exit 1
        ;;
esac