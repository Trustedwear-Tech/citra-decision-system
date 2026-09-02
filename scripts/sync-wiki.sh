#!/usr/bin/env bash
# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

# Publish wiki/ to the GitHub wiki.
#
#   bash scripts/sync-wiki.sh            # show what would change
#   bash scripts/sync-wiki.sh --push     # actually publish
#
# WHY THE PAGES LIVE IN THIS REPO
#
# A GitHub wiki is a separate git repository that is normally edited in a
# browser, which means wiki content drifts from the code it documents and no
# one reviews a change to it. Keeping the source here means a docs change
# arrives in the same pull request as the code change, and this script is the
# only way it reaches the wiki.
#
# ONE-TIME SETUP
#
# The wiki repository does not exist until the first page is created through
# the web UI -- cloning before that fails with "Repository not found", which
# looks like a permissions problem and is not. Create any page once at
#
#   https://github.com/Trustedwear-Tech/citra-decision-system/wiki
#
# after which this script owns the content.
#
# LINKS
#
# Pages are written for the wiki, not for the repo: images point at
# raw.githubusercontent.com and file links at blob URLs, because the wiki is a
# different repository and relative paths into this one resolve to nothing.
set -uo pipefail

REPO="${REPO:-Trustedwear-Tech/citra-decision-system}"
SRC="$(cd "$(dirname "$0")/.." && pwd)/wiki"
PUSH=0
[ "${1:-}" = "--push" ] && PUSH=1

[ -d "$SRC" ] || { echo "no wiki/ directory at $SRC" >&2; exit 1; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

if ! git clone -q "https://github.com/${REPO}.wiki.git" "$TMP/wiki" 2>/dev/null; then
  cat >&2 <<EOF
Could not clone https://github.com/${REPO}.wiki.git

The wiki repository is not created until its first page is saved in the web
UI. Open

  https://github.com/${REPO}/wiki

create a page with any content, then re-run this. Everything after that is
handled here.
EOF
  exit 1
fi

changed=0
for f in "$SRC"/*.md; do
  n="$(basename "$f")"
  if [ ! -f "$TMP/wiki/$n" ] || ! cmp -s "$f" "$TMP/wiki/$n"; then
    printf '  %-42s %s\n' "$n" "$([ -f "$TMP/wiki/$n" ] && echo updated || echo new)"
    cp "$f" "$TMP/wiki/$n"
    changed=$((changed + 1))
  fi
done

# Pages in the wiki that no longer exist here are REPORTED, never deleted:
# someone may have written one in the browser, and silently removing their work
# is not a sync script's decision to make.
for f in "$TMP"/wiki/*.md; do
  n="$(basename "$f")"
  [ -f "$SRC/$n" ] || printf '  %-42s only in the wiki - left alone\n' "$n"
done

if [ "$changed" = 0 ]; then
  echo "  wiki is up to date"
  exit 0
fi

if [ "$PUSH" = 0 ]; then
  echo
  echo "  $changed page(s) would change. Re-run with --push to publish."
  exit 0
fi

cd "$TMP/wiki"
git add -A
git -c user.name="${GIT_AUTHOR_NAME:-docs sync}" \
    -c user.email="${GIT_AUTHOR_EMAIL:-noreply@citra-ai.com}" \
    commit -q -m "docs: sync wiki from repo wiki/"
git push -q origin HEAD
echo
echo "  published $changed page(s) to https://github.com/${REPO}/wiki"
