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

  # 3b. Compose must be new enough to OVERRIDE an included service, not just
  #     new enough to exist. docker-compose.quickstart.yml `include`s the infra
  #     and dev files and then re-declares 13 of their services to add
  #     `env_file: [.env]`. Newer Compose merges that; older Compose refuses:
  #
  #       services.minio conflicts with imported resource
  #
  #     Checking a version NUMBER would mean guessing which release changed it.
  #     Ask Compose to parse the real file instead -- the capability is the
  #     thing that matters, and this is the same lesson as testing for the venv
  #     MODULE rather than for the python binary.
  if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    _cfg_err="$(docker compose -f docker-compose.quickstart.yml config -q 2>&1 || true)"
    case "$_cfg_err" in
      *"conflicts with imported resource"*)
        echo "  [X] Your Docker Compose is too old for this stack's compose file." >&2
        echo "      It reports: ${_cfg_err}" >&2
        echo "      Upgrade Compose v2 (the version get.docker.com installs works):" >&2
        echo "        https://docs.docker.com/compose/install/" >&2
        fail=1
        ;;
    esac
  fi

  # 4. curl. wait_for() in start.sh polls the health endpoints with it, so
  #    without it the install hangs its full 5-minute timeout and then reports
  #    the SERVICE as unhealthy, which sends you debugging the wrong thing.
  if ! command -v curl >/dev/null 2>&1; then
    echo "  [X] curl is not on PATH (the installer polls service health with it)." >&2
    echo "      Debian/Ubuntu: sudo apt install curl        macOS: brew install curl" >&2
    fail=1
  fi

  # 5. Python, and specifically a python that can build a venv and pip-install.
  #    seed-demo.sh creates a venv and installs into it. Debian and Ubuntu ship
  #    python3 WITHOUT ensurepip, so `python3 -m venv` fails on a box where
  #    python3 is plainly installed:
  #
  #      The virtual environment was not created successfully because ensurepip
  #      is not available. ... you need to install the python3-venv package
  #
  #    The README said "python3 on PATH", which is true and not sufficient.
  #    Checking the interpreter alone would reproduce exactly that mistake, so
  #    the module is checked, not the binary.
  local py
  py="$(command -v python3 || command -v python || true)"
  if [ -z "$py" ]; then
    echo "  [X] python3 is not on PATH (the setup and seed scripts are Python)." >&2
    echo "      Debian/Ubuntu: sudo apt install python3 python3-venv python3-pip" >&2
    echo "      macOS: brew install python       Windows: https://python.org/downloads" >&2
    fail=1
  else
    if ! "$py" -c "import ensurepip" >/dev/null 2>&1; then
      echo "  [X] $py cannot create virtual environments (no ensurepip)." >&2
      echo "      The demo seed builds a venv and installs into it." >&2
      echo "      Debian/Ubuntu: sudo apt install python3-venv" >&2
      fail=1
    fi
    if ! "$py" -m pip --version >/dev/null 2>&1; then
      echo "  [!] $py has no pip module. The seed venv usually provides its own," >&2
      echo "      but if seeding fails: sudo apt install python3-pip" >&2
    fi
  fi

  # 6. The vendored shared packages. Every service Dockerfile copies from
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
