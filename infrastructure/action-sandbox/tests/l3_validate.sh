#!/bin/sh
# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

# Render with the EXACT varlist entrypoint.sh uses, then validate.
set -e
export CITRA_LLM_BASE_URL=https://stub.example/v1
export CITRA_LLM_API_KEY=sk-stub
export CITRA_ACTION_MODEL=stub-model
export OPENCLAW_GATEWAY_TOKEN=tok-stub
export OPENCLAW_HOME=/workspace/.openclaw-home

mkdir -p "$OPENCLAW_HOME/.openclaw"

envsubst '${CITRA_LLM_BASE_URL} ${CITRA_LLM_API_KEY} ${CITRA_ACTION_MODEL} ${OPENCLAW_GATEWAY_TOKEN} ${OPENCLAW_HOME}' \
  < /srv/citra/openclaw.config.template.json \
  > "$OPENCLAW_HOME/.openclaw/openclaw.json"

echo "==== first 3 lines ===="
head -3 "$OPENCLAW_HOME/.openclaw/openclaw.json"
echo "==== top-level keys ===="
python3 -c "import json; print(list(json.load(open('$OPENCLAW_HOME/.openclaw/openclaw.json')).keys()))"
echo "==== validate ===="
openclaw config validate --json
