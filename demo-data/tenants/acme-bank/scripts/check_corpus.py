# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""
Guard the acme-bank SOP corpus.

The flagship demo (LAN-NEEDLE-001) rests on a GAP: no document may instruct an
officer to reconcile a salaried applicant's TAX-FILED income against their
DECLARED income. The Income Verification SOP checks that Form 16 is *genuine*
and computes eligibility from payslips — it never compares the filed figure to
the declared one.

If someone "improves" the corpus by adding that instruction, the app can learn
the lesson from the SOP and the demonstration of learned judgement collapses —
silently, and only visible during a demo. This script fails loudly instead.

Also checks that each Decision App has SOPs it can ground on, and that the
needle cases have supporting policy text to cite.

Usage:
    python check_corpus.py          # exit 1 on any failure
"""
from __future__ import annotations

import pathlib
import re
import sys

DOCS = pathlib.Path(__file__).resolve().parent.parent / "raw" / "policy"

# Sentences that would CLOSE the gap.
GAP_PATTERNS = [
    r"(itr|form\s*16|tax filing|filed return)[^.]{0,120}"
    r"(corroborat|reconcil|cross[- ]?verif|compare)[^.]{0,120}declared",
    r"declared[^.]{0,120}(corroborat|reconcil|cross[- ]?verif|compare)[^.]{0,120}"
    r"(itr|form\s*16|tax filing|filed return)",
    r"(itr|form\s*16)[^.]{0,80}(does not|doesn't|fails to)[^.]{0,80}"
    r"(support|match|corroborat)",
]

# Terms each Decision App needs to find something to cite.
APP_TERMS = {
    "loan triage": ["foir", "bureau", "ltv", "income proof"],
    "collections": ["ptp", "bucket", "dpd", "call window", "hardship"],
    "claim triage": ["intimation", "surveyor", "waiting period", "repudiat"],
    "sales dashboard": ["suitability", "free-look", "licence"],
}

# Policy text the needle cases rely on being citable.
NEEDLE_ANCHORS = {
    "CLM-NEEDLE-004 (late intimation)": "intimation_window_days",
    "CLM-NEEDLE-003 (duplicate document)": "byte-for-byte identical",
    "LON-NEEDLE-002 (broken PTP)": "ptp_kept",
}


def main() -> int:
    files = sorted(DOCS.glob("*.md"))
    if not files:
        print(f"FAIL: no documents under {DOCS}")
        return 1

    fails: list[str] = []
    corpus = {f.name: f.read_text(encoding="utf-8").lower() for f in files}
    flat = {n: " ".join(t.split()) for n, t in corpus.items()}

    print(f"=== corpus: {len(files)} documents, "
          f"{sum(len(t.split()) for t in corpus.values())} words ===")

    print("\n=== the deliberate gap (must find NOTHING) ===")
    leaked = False
    for name, text in flat.items():
        for pat in GAP_PATTERNS:
            for m in re.finditer(pat, text):
                print(f"  [LEAK] {name}: ...{m.group(0)[:160]}...")
                fails.append(f"gap closed in {name}")
                leaked = True
    if not leaked:
        print("  [ok ] nothing reconciles filed income against declared income")

    print("\n=== each Decision App has something to ground on ===")
    for app, terms in APP_TERMS.items():
        hits = {t: sum(1 for c in corpus.values() if t in c) for t in terms}
        missing = [t for t, n in hits.items() if n == 0]
        print(f"  [{'ok ' if not missing else 'FAIL'}] {app:<16} "
              + ", ".join(f"{t}x{n}" for t, n in hits.items()))
        if missing:
            fails.append(f"{app} missing {missing}")

    print("\n=== needle cases are citable ===")
    for label, anchor in NEEDLE_ANCHORS.items():
        ok = any(anchor.lower() in c for c in corpus.values())
        print(f"  [{'ok ' if ok else 'FAIL'}] {label}")
        if not ok:
            fails.append(label)

    print("\nFAILURES:", sorted(set(fails)) or "none")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
