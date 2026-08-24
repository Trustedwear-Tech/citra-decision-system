# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""
Pytest config for demo-data fixture validation.

Loads smart-app-service's `models.py` as a uniquely-named module via
importlib and exposes it as `sas_models`.
"""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load_as(module_name: str, path: Path):
    """Load a Python file as a uniquely-named module so a service's
    `models.py` can't collide with another module of the same name."""
    spec = importlib.util.spec_from_file_location(module_name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


# Need to point sys.path at the service folder so the model's own
# internal imports resolve.
sys.path.insert(0, str(ROOT / "smart-app-service"))
sas_models = _load_as("sas_models", ROOT / "smart-app-service" / "models.py")
