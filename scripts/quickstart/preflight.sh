# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

# Check the host can actually run this BEFORE anything is written or asked.
#
# Sourced, not executed -- `. scripts/quickstart/preflight.sh; preflight`.
#
# The README has always listed the prerequisites; nothing ever checked them. A
# host without Docker got as far as writing .env and then:
#
#     scripts/quickstart/setup.sh: line 77: docker: command not found
#
# which is a true statement and useless advice. Worse in the wizard, which asks
# for an org name, an admin password and an API key first, so the interview was
# thrown away. These checks run before the first question and before the first
# byte of .env.
#
# We check and instruct. We do NOT install: Docker needs root, differs on every
# platform, and a script that silently apt-gets a daemon onto someone's machine
# has exceeded what "run the setup script" grants it.

preflight() {
  local fail=0

  # 1. The binary.
  if ! command -v docker >/dev/null 2>&1; then
    echo "  [X] docker is not on PATH." >&2
    echo "      Install Docker Desktop (macOS/Windows) or Docker Engine (Linux):" >&2
    echo "        https://docs.docker.com/get-docker/" >&2
    echo "      On Windows, run this from Git Bash or WSL after Docker Desktop starts." >&2
    fail=1
  else
    # 2. The daemon. Installed-but-not-running is the single most common
    #    state, and its failure surfaces much later as a connection refused
    #    from somewhere inside compose.
    if ! docker info >/dev/null 2>&1; then
      echo "  [X] docker is installed but the daemon is not reachable." >&2
      echo "      Start Docker Desktop, or: sudo systemctl start docker" >&2
      echo "      \`docker version\` should print a Server section; if it does not, it is not running." >&2
      fail=1
    fi

    # 3. Compose v2. v1 was the `docker-compose` hyphenated binary and does
    #    not understand the `include:` key that docker-compose.quickstart.yml
    #    is built on, so a v1 host fails with a confusing schema error rather
    #    than a missing-feature one.
    if ! docker compose version >/dev/null 2>&1; then
      echo "  [X] 'docker compose' (v2) is unavailable." >&2
      if command -v docker-compose >/dev/null 2>&1; then
        echo "      You have the old v1 'docker-compose'. This stack uses the v2 'include:'" >&2
        echo "      key, which v1 cannot parse. Upgrade to Compose v2." >&2
      fi
      echo "        https://docs.docker.com/compose/install/" >&2
      fail=1
    fi
  fi

  # 4. The vendored shared packages. Every service Dockerfile copies from
  #    citra-common/. It used to be a git submodule, so a plain clone and both
  #    source downloads left it EMPTY and every build died on a path that is
  #    plainly present in a developer's checkout. It is vendored now, but an
  #    older clone on this machine may still have the empty submodule dir.
  if [ ! -f citra-common/citra-auth/pyproject.toml ]; then
    echo "  [X] citra-common/ is empty or incomplete." >&2
    echo "      This clone predates citra-common being vendored. Update it:" >&2
    echo "        git submodule deinit -f citra-common 2>/dev/null || true" >&2
    echo "        git pull" >&2
    fail=1
  fi

  # Advisory only -- a small machine still installs, it just swaps. Not worth
  # blocking on, and definitely not worth guessing wrong about and blocking on.
  local kb=""
  if [ -r /proc/meminfo ]; then
    kb=$(awk '/MemTotal/{print $2}' /proc/meminfo 2>/dev/null || true)
  fi
  if [ -n "$kb" ] && [ "$kb" -lt 15000000 ] 2>/dev/null; then
    echo "  [!] $((kb / 1024 / 1024)) GB RAM detected; 16 GB is the tested floor (Milvus plus the fleet)." >&2
    echo "      The install will proceed and may be slow or get OOM-killed." >&2
  fi

  if [ "$fail" -ne 0 ]; then
    echo "" >&2
    echo "  Prerequisites are not met -- stopping before anything is written." >&2
    echo "  See the Requirements table in README.md." >&2
    return 1
  fi
  return 0
}
