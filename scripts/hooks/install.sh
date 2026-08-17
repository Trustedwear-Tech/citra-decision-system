#!/usr/bin/env bash
# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

# Install the pre-push sync check into both repos.
# .git/hooks is not tracked, so this has to be run once per clone.
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)/pre-push"
for repo in "C:/Github/Citra-AI" "C:/Github/citra-decision-system"; do
  if [ -d "$repo/.git" ]; then
    cp "$SRC" "$repo/.git/hooks/pre-push"
    chmod +x "$repo/.git/hooks/pre-push"
    echo "  installed -> $repo/.git/hooks/pre-push"
  else
    echo "  skipped (not a git repo) -> $repo"
  fi
done
