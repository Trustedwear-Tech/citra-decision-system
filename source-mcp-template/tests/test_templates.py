# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""Deploy templates (templates/*.sources.json) must always validate.

A template that drifts from the registry models is worse than none — it teaches
integrators an ontology the platform rejects. Every rule the template shows off
(domain enums, payment_proof pinning, claim-context pairs) is exercised simply
by parsing the file.
"""
import glob
import json
import os

import pytest

from registry_models import SourcesFile

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATES = sorted(glob.glob(os.path.join(_HERE, "templates", "*.sources.json")))


def test_templates_exist():
    assert len(TEMPLATES) >= 4, "deploy templates missing (plan §6)"


@pytest.mark.parametrize("path", TEMPLATES, ids=[os.path.basename(p) for p in TEMPLATES])
def test_template_validates_and_is_domain_annotated(path):
    doc = json.loads(open(path, encoding="utf-8").read())
    sf = SourcesFile.model_validate(doc)
    fname = os.path.basename(path)
    for src in sf.sources():
        # Every template source declares its cell — that's what a template IS.
        assert src.domain is not None, f"{fname}: source {src.source_id} has no domain"
        # vertical/sub_vertical/country are open strings since the domain went
        # global; templates still declare a concrete cell, hence no .value.
        cell = f"{src.domain.vertical}-{src.domain.sub_vertical}-{src.domain.country}"
        assert fname.startswith(cell), (
            f"{fname}: filename does not match its domain cell {cell}")
        # Derivations are filled at validation — the template ships complete.
        assert src.domain.currency and src.domain.date_order
    # Deliberately NO fraud_screening assertion: fraud is a FEATURE the
    # ontology opts into, never a requirement — a template (or a future cell)
    # with no fraud annotation at all is a valid deployment starter. What a
    # template must always carry is its domain cell (asserted above); what it
    # declares beyond that is the vertical's choice.
