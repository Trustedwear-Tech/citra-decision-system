#!/bin/sh
# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

# L3 in-image probes. Mounted into the container and executed.
set +e
echo '--- symlinks ---'
for c in apt apt-get pip pip3 npm; do
  path=$(command -v $c 2>/dev/null)
  if [ -n "$path" ]; then
    printf '%-8s -> %s\n' "$c" "$(readlink -f "$path")"
  else
    printf '%-8s -> NOT FOUND\n' "$c"
  fi
done

echo '--- pip install requests (expect EXIT=127 + loud line) ---'
pip install requests
echo "EXIT=$?"

echo '--- envsubst render of openclaw.config.template.json ---'
export CITRA_LLM_BASE_URL=https://stub.example/v1
export CITRA_LLM_API_KEY=sk-stub
export CITRA_ACTION_MODEL=stub-model
export OPENCLAW_GATEWAY_TOKEN=tok-stub
export OPENCLAW_HOME=/workspace/.openclaw-home
envsubst < /srv/citra/openclaw.config.template.json > /tmp/rendered.json
echo "render exit=$?"
python3 -c "import json; d=json.load(open('/tmp/rendered.json')); print('json ok'); print('logging.file =', d['logging']['file']); print('logging.maxFileBytes =', d['logging']['maxFileBytes']); print('sandbox.mode =', d['agents']['defaults']['sandbox']['mode']); print('tools.deny =', d['tools']['deny']); print('plugins =', d.get('plugins')); print('update =', d['update'])"

echo '--- openclaw config validate (with rendered file at expected path) ---'
mkdir -p /workspace/.openclaw-home/.openclaw
cp /tmp/rendered.json /workspace/.openclaw-home/.openclaw/openclaw.json
openclaw config validate
echo "validate exit=$?"

echo '--- shim file present + exec ---'
ls -la /usr/local/bin/citra-no-install /usr/local/bin/pip /usr/local/bin/apt
