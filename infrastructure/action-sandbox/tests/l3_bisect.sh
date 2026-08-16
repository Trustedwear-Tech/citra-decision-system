#!/bin/sh
# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

# Bisect openclaw.config.template.json to find the unrecognized key.
set +e
export CITRA_LLM_BASE_URL=https://stub.example/v1
export CITRA_LLM_API_KEY=sk-stub
export CITRA_ACTION_MODEL=stub-model
export OPENCLAW_GATEWAY_TOKEN=tok-stub
export OPENCLAW_HOME=/workspace/.openclaw-home
mkdir -p $OPENCLAW_HOME/.openclaw

CFG=$OPENCLAW_HOME/.openclaw/openclaw.json

write() {
  cat > "$CFG"
  echo "==== test: $1 ===="
  openclaw config validate --json 2>&1
  echo
}

# 1. Bare minimum
write "minimum-empty" <<EOF
{}
EOF

write "schema-only" <<EOF
{"\$schema": "https://openclaw.ai/schemas/config.json"}
EOF

write "gateway-only" <<EOF
{
  "gateway": {
    "mode": "local",
    "bind": "loopback",
    "port": 18789,
    "auth": {"mode":"token","token":"tok"}
  }
}
EOF

write "with-models" <<EOF
{
  "gateway": {"mode":"local","bind":"loopback","port":18789,"auth":{"mode":"token","token":"tok"}},
  "models": {"mode":"merge","providers":{"citra":{"baseUrl":"x","apiKey":"y","api":"openai-completions","authHeader":true,"models":[{"id":"m","name":"M","reasoning":false,"input":["text"],"contextWindow":1000,"maxTokens":100}]}}}
}
EOF

write "with-channels-empty" <<EOF
{
  "gateway": {"mode":"local","bind":"loopback","port":18789,"auth":{"mode":"token","token":"tok"}},
  "channels": {}
}
EOF

write "with-tools" <<EOF
{
  "gateway": {"mode":"local","bind":"loopback","port":18789,"auth":{"mode":"token","token":"tok"}},
  "tools": {"profile":"coding","deny":["browser"]}
}
EOF

write "with-update" <<EOF
{
  "gateway": {"mode":"local","bind":"loopback","port":18789,"auth":{"mode":"token","token":"tok"}},
  "update": {"checkOnStart": false}
}
EOF

write "with-logging" <<EOF
{
  "gateway": {"mode":"local","bind":"loopback","port":18789,"auth":{"mode":"token","token":"tok"}},
  "logging": {"level":"info","consoleStyle":"compact","file":"/tmp/o.log","maxFileBytes": 52428800}
}
EOF

write "with-agents" <<EOF
{
  "gateway": {"mode":"local","bind":"loopback","port":18789,"auth":{"mode":"token","token":"tok"}},
  "agents": {"defaults":{"model":"x/y","workspace":"/workspace/.openclaw/workspace","skipBootstrap":true,"timeoutSeconds":1800,"heartbeat":{"every":"0m"},"sandbox":{"mode":"off"}},"list":[{"id":"main","default":true,"name":"Main","agentDir":"/workspace/.openclaw/agent"}]}
}
EOF

# 2. Render the actual template and validate
envsubst < /srv/citra/openclaw.config.template.json > "$CFG"
echo "==== test: ACTUAL TEMPLATE ===="
openclaw config validate --json 2>&1
echo
