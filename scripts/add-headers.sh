#!/usr/bin/env bash
# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

#
# Thin wrapper. The implementation is scripts/license_headers.py.
#
# This file used to BE the implementation, and it had a defect worth recording
# so it is not reintroduced: it enumerated files with `find` over the working
# tree rather than `git ls-files`. In this repository that is 6,079 files
# against 979 tracked ones -- the difference being .venv-seed/Lib/site-packages,
# which it would have rewritten to claim Trustedwear copyright over certifi,
# boto3, pydantic and every other dependency. It also stamped "PROPRIETARY, all
# rights reserved, NOT an open-source grant" into a tree licensed BUSL-1.1.
#
# The wrapper survives only so that `ci.yml` keeps working unchanged.
#
#   ./scripts/add-headers.sh            # stamp what is missing
#   ./scripts/add-headers.sh --check    # report only; exit 1 if any (CI)

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

PY="${PYTHON:-}"
if [[ -z "$PY" ]]; then
    for candidate in python3 python; do
        if command -v "$candidate" >/dev/null 2>&1; then
            PY="$candidate"
            break
        fi
    done
fi

if [[ -z "$PY" ]]; then
    echo "  [FAIL] no python interpreter found; set PYTHON=/path/to/python" >&2
    exit 1
fi

exec "$PY" "$REPO_ROOT/scripts/license_headers.py" "$@"
