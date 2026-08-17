#!/usr/bin/env bash
# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

# Install the repo's git hooks. .git/hooks is not tracked, so this has to be
# run once per clone.
#
# Installs pre-commit, which blocks private material being committed here.
# It REPLACES the old pre-push sync check: there is no second tree to be out of
# sync with any more, so that hook now only ever produced noise.
set -euo pipefail

HOOKS="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HOOKS/../.." && pwd)"

[ -d "$REPO/.git" ] || { echo "not a git repo: $REPO" >&2; exit 1; }

cp "$HOOKS/pre-commit" "$REPO/.git/hooks/pre-commit"
chmod +x "$REPO/.git/hooks/pre-commit"
echo "  installed -> $REPO/.git/hooks/pre-commit"

# The pre-push sync check belonged to the two-tree era. Remove it if a previous
# install left one behind, otherwise it keeps blocking pushes over a comparison
# that no longer means anything.
if [ -f "$REPO/.git/hooks/pre-push" ] && grep -q "sync_public.py" "$REPO/.git/hooks/pre-push" 2>/dev/null; then
  rm -f "$REPO/.git/hooks/pre-push"
  echo "  removed   -> $REPO/.git/hooks/pre-push  (obsolete two-tree sync check)"
fi

echo
echo "  pre-commit blocks: anything under private/, the Firebase hosting files,"
echo "  infrastructure/aws/, and a staged marketing IntroScreen.js."
echo "  Deliberate override:  SKIP_PRIVATE_CHECK=1 git commit"
