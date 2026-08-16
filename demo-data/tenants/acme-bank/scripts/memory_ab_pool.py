# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""Pool the A/B batches into one result.

Pooling is only legitimate when the batches ran under IDENTICAL conditions, so
this refuses to combine files whose cases overlap — the whole point of the second
batch was that it is 12 DIFFERENT applications, not the first eight again.

Why pool at all: a single batch of 8 moved from p=0.0039 to p=0.0625 across two
runs of the same setup. That spread is LLM nondeterminism, and it is the reason
n=8 is too small to quote. n=20 is where the answer stops moving.

    python memory_ab_pool.py
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Dict, List

SCRIPT_DIR = Path(__file__).resolve().parent
BATCHES = ["memory_ab_dsa_results_batch1.json",
           "memory_ab_dsa_results_off8.json",
           "memory_ab_dsa_results_rerun.json"]
CLAUSE = "C-002"


def sign_test(b: int, c: int) -> float:
    n = b + c
    if n == 0:
        return 1.0
    return sum(math.comb(n, k) for k in range(b, n + 1)) / (2 ** n)


def main() -> int:
    rows: List[Dict[str, Any]] = []
    seen: Dict[str, str] = {}
    for name in BATCHES:
        p = SCRIPT_DIR / name
        if not p.exists():
            print(f"missing {name} — run the batch first")
            return 1
        batch = json.loads(p.read_text(encoding="utf-8"))
        # Drop failed calls BEFORE the duplicate check. Three cases in batch 2
        # came back 401 (one JWT, minted once, expired mid-batch) and were
        # re-run; the void originals must not collide with their replacements,
        # and a failed call is not evidence either way.
        voided = [r for r in batch
                  if r["with_memory"].get("http") != 200
                  or r["without_memory"].get("http") != 200]
        if voided:
            print(f"  {name}: dropping {len(voided)} void run(s) "
                  f"({[v['application_id'] for v in voided]})")
        batch = [r for r in batch if r not in voided]
        for r in batch:
            aid = r["application_id"]
            if aid in seen:
                print(f"REFUSING TO POOL — {aid} appears in both {seen[aid]} and "
                      f"{name}. Overlapping cases would count one application "
                      f"twice and inflate the result.")
                return 2
            seen[aid] = name
        rows += batch
        print(f"{name}: {len(batch)} case(s)")

    t = [r for r in rows if r["arm"] == "treatment"]
    c = [r for r in rows if r["arm"] == "control"]
    b = sum(1 for r in t if r["with_memory"]["used_judgement"]
            and not r["without_memory"]["used_judgement"])
    a = sum(1 for r in t if not r["with_memory"]["used_judgement"]
            and r["without_memory"]["used_judgement"])
    both = sum(1 for r in t if r["with_memory"]["used_judgement"]
               and r["without_memory"]["used_judgement"])
    neither = sum(1 for r in t if not r["with_memory"]["used_judgement"]
                  and not r["without_memory"]["used_judgement"])
    fired = sum(1 for r in t if CLAUSE in (r["with_memory"]["injected"] or []))
    cited = sum(1 for r in t if CLAUSE in (r["with_memory"]["cited"] or []))
    declined = sum(1 for r in t if r["with_memory"]["hard_decline"])
    leaked = sum(1 for r in c if CLAUSE in (r["with_memory"]["injected"] or []))
    ctrl_changed = sum(1 for r in c
                       if r["with_memory"]["used_judgement"]
                       != r["without_memory"]["used_judgement"])
    p = sign_test(b, a)

    print("\n" + "=" * 66)
    print(f"POOLED — {len(t)} DSA treatment case(s), {len(c)} control case(s)")
    print(f"  {CLAUSE} injected                     : {fired}/{len(t)}")
    print(f"  {CLAUSE} cited by name                : {cited}/{len(t)}")
    print(f"  declined on policy anyway             : {declined}/{len(t)}")
    print(f"  used the judgement WITH memory only   : {b}")
    print(f"  used it WITHOUT memory only           : {a}")
    print(f"  used in both / neither                : {both} / {neither}")
    print(f"  sign test over {b + a} discordant pair(s)      : p = {p:.4f}")
    if c:
        print(f"  control: wrongly injected {leaked}/{len(c)}, "
              f"changed {ctrl_changed}/{len(c)}")
    print("=" * 66)

    if p <= 0.01:
        verdict = f"DEMONSTRATED: memory changed the reasoning on {b} of {len(t)} " \
                  f"distinct cases (p={p:.4f})"
    elif p <= 0.05:
        verdict = f"demonstrated at the 5% level: {b}/{len(t)} (p={p:.4f}) — " \
                  f"solid, not overwhelming"
    else:
        verdict = f"NOT demonstrated: {b} for, {a} against, p={p:.4f}"
    print(f"\n  {verdict}")
    print("  scope of the claim: THIS lesson on THIS app. The three lessons that "
          "restate the SOP changed nothing.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
