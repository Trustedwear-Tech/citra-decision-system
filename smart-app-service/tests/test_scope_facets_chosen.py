# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0
"""The officer chooses which facets a lesson is about.

Observed on acme-bank: an officer held a branch-sourced personal loan with
"check that the employer was verified before accepting the payslip". With one
correction the judgement's scope was every facet of that file, so it never
fired on the dealer-sourced home loan the lesson was equally about. The run
card's Learning-scope chips are now toggles; the chosen subset is stored on
the correction and is what consolidation intersects.
"""
from __future__ import annotations

from consolidation import infer_scope

CASE = ["product:personal", "sourcing_channel:branch", "amount_band:500000_1000000",
        "foir_band:lt_30", "income_proof:present"]


def test_one_correction_without_a_choice_scopes_to_the_whole_case():
    assert infer_scope([{"case_facets": CASE}]) == sorted(CASE)


def test_one_correction_with_a_choice_scopes_to_the_choice():
    c = {"case_facets": CASE, "scope_facets": ["income_proof:present"]}
    assert infer_scope([c]) == ["income_proof:present"]


def test_choices_intersect_across_corrections():
    a = {"case_facets": CASE, "scope_facets": ["income_proof:present", "product:personal"]}
    b = {"case_facets": ["product:home", "sourcing_channel:dsa", "income_proof:present"],
         "scope_facets": ["income_proof:present", "sourcing_channel:dsa"]}
    assert infer_scope([a, b]) == ["income_proof:present"]


def test_a_correction_without_a_choice_still_contributes_its_whole_case():
    a = {"case_facets": CASE, "scope_facets": ["income_proof:present"]}
    b = {"case_facets": ["product:home", "sourcing_channel:dsa", "income_proof:present"]}
    assert infer_scope([a, b]) == ["income_proof:present"]


def test_drift_tokens_never_enter_a_chosen_scope():
    c = {"case_facets": CASE + ["surveyor:__unknown"],
         "scope_facets": ["income_proof:present", "surveyor:__unknown"]}
    assert infer_scope([c]) == ["income_proof:present"]
