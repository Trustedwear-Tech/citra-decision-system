<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: Apache-2.0

  Licensed under the Apache License, Version 2.0 (the "License"); you may not
  use this file except in compliance with the License. You may obtain a copy of
  the License at http://www.apache.org/licenses/LICENSE-2.0
-->

# Decision memory — experiment protocol

**Question.** When officers overrule the system and say why, does the system
learn it, does the next recommendation change, and is the change worth money?

**Status.** Pre-registered protocol. Nothing below has been run. Every threshold
is fixed here, before the first run, so the result cannot be read to fit.

---

## 0. Why the previous experiment is not evidence

The runs in `demo-data/tenants/acme-bank/scripts/memory_ab_dsa_results*.json`
cannot be quoted. Read from the files, not the note:

| Problem | Evidence |
|---|---|
| Runs that never happened were recorded as results | 15 rows are `http=401`, `secs≈2.0`, `decision=None` — the login token expired mid-run and the refusal was written down as "no decision". The note's own file is 10 real cases, not 12. |
| No noise floor | The same case was never run twice with memory OFF. The control arm shows a verdict moving (`Approve → Under Review`) on a case where the clause was not cited — so verdicts wander on their own, by an unknown amount. |
| The measure was attribution, not behaviour | Scored "did the model cite C-002", falling back to keywords. Whether the **recommendation** changed was recorded but never the headline. |
| Wrong population | 14 of 19 treatment cases were hard declines (bureau, FOIR, income). On a file already dead on a rule the verdict cannot move. The branch where the money is — files that pass every gate — was not sampled. |
| Clauses were seeded by hand | `seed_memory.py --apply` wrote the clause directly. The learning pipeline (correction → cluster → clause → active) was never exercised. It tested injection, not learning. |
| Four result files that disagree | `results.json` says no verdict moved; `off8.json` shows five moving `Under Review → Reject`. Neither is the experiment. |
| Model routing was not pinned | `LLM_MODEL=deepseek/deepseek-v4-pro:nitro` — `:nitro` routes to whichever provider is fastest at that moment. Two runs may not have hit the same weights. |

---

## 1. Hypotheses, in the order they must be tested

Each one is a gate for the next. If H0 fails, stop and fix the system; nothing
after it is interpretable.

| | Hypothesis | Pass threshold (fixed now) |
|---|---|---|
| **H0** | The system is stable enough to measure: the same case, memory OFF, run three times, gives the same verdict. | Verdict agreement **≥ 90 %** across the noise-floor pool. Below that, stop. |
| **H1** | Officers' corrections, entered through the real product, become an active learned judgement — no hand-seeding. | Clause reaches `active` from ≥ 3 officers' corrections; its text says what they said; scope is exactly `sourcing_channel:dsa`. |
| **H2** | With the judgement active, the recommendation on matching cases changes — the verdict or the required action — beyond the noise floor. | Shift rate on treatment minus noise floor **≥ 30 points**; sign test on discordant pairs **p < 0.01**. |
| **H3** | It stays silent where it does not apply. | Injection on control cases **= 0**; verdict shift on control **≤ noise floor**. |
| **H4** | The changed recommendations are the right ones, and the money is real. | Of planted bad files, **≥ 80 %** diverted to verification; of genuine files, **≤ 20 %** diverted. Exposure caught reported in ₹. |
| **H5** | It can be switched off. | Retire the clause → treatment cases return to baseline verdicts (agreement with OFF arm ≥ noise floor). |

---

## 2. What the demo data can and cannot prove

`seed_postgres.py` assigns `sourcing_channel` at random (`_pick(CHANNELS,
CHANNEL_W)`) and assigns delinquency by an independent draw. **There is no
relationship between channel and outcome in the seeded book.** So on the demo
data as it stands, H4 is unmeasurable: there is no fact of the matter about
which DSA files *should* have been held.

Two honest options; this protocol takes the first and labels it everywhere:

- **Plant a labelled signal.** Add a hidden `coached` flag to a fixed fraction
  of DSA-sourced applications that pass every gate; give those files an
  `employer_name` that does not exist in `branches`/`agents`/known employers, and
  a later delinquency. The flag is never visible to the model. Every H4 figure is
  then reported as *"on a planted signal"* — it proves the **mechanism** catches
  what it was taught to catch, and nothing about any real book.
- **Run on a real book.** The only proof of money. Same protocol, no planting,
  outcomes from the customer's own ledger, 6–18 months after decision.

H0–H3 and H5 need no ground truth and are measured on the demo data as-is.

---

## 3. Design

### Arms

| Arm | What | Size |
|---|---|---|
| **Noise floor** | Cases run 3× with memory OFF. Nothing else differs. | 15 cases × 3 runs = 45 |
| **Treatment** | DSA-sourced applications that **pass every hard gate** (bureau ≥ policy floor, FOIR ≤ limit, income ≥ floor, LTV within limit) and are undecided (`status ∈ {new, under_review}`). Each run twice: memory OFF, then ON. | 40 cases × 2 = 80 |
| **Control** | Non-DSA applications, same gate filter, undecided. Each run twice. | 40 cases × 2 = 80 |
| **Reversibility** | 15 treatment cases re-run after the clause is retired. | 15 |
| **Total** | | **~220 runs** |

The gate filter is the fix for the previous population error. It is a SQL
query against `loan_applications` joined to `customers`, written down in the
harness, and the selected case ids are frozen to a file before any run.

### Teaching, through the product

Three seeded officer personas (`users.json` has fourteen) each reject **three**
different DSA cases through the real correction path — the same API the UI
calls — with a reason in their own words. Nine corrections, three officers,
wording varied naturally (not copy-pasted). This is what the clustering job
sees in production:

- similarity: Jaccard ≥ 0.34 over content tokens, facet overlap ≥ 0.5
- cluster size ≥ 2; **3 distinct officers → active**
- job runs every 900 s when ≥ 5 corrections are pending

Then **wait for the job**, and verify `smartapp_clauses` shows the clause
`active` with `scope_facets == ["sourcing_channel:dsa"]` and a `lesson` a person
would recognise as what the officers wrote. If the clause does not form, that
is an H1 failure and is reported as one — it is not patched with
`seed_memory.py`.

### Order of runs

Interleave, never batch by arm: OFF and ON runs of the same case are adjacent,
and treatment/control cases alternate. Time-of-day and provider drift then fall
equally on both sides.

---

## 4. Metrics

Every metric names the field it is read from. "Verdict" is **structured**:
the `status` value in the planned write (`write_events[].payload.status`, the
`record_loan_decision` action), falling back to the normalised first word of
`decision` only when no write was planned — and the fallback is counted.

### Mechanism (H1)

| Metric | Read from | Pass |
|---|---|---|
| Corrections needed before the clause activates | `smartapp_corrections` count at first `active` | report |
| Distinct officers on the clause | `smartapp_clauses.officer_ids` | = 3 |
| Time from last correction to `active` | clause `activated_at` − last correction `created_at` | ≤ 2 job periods (30 min) |
| Scope facets | `smartapp_clauses.scope_facets` | exactly `sourcing_channel:dsa` |
| Text fidelity | human read of `lesson` vs the nine reasons; token overlap reported | a person says "yes, that's what we said" |

### Behaviour (H2, H3)

| Metric | Read from | Pass |
|---|---|---|
| **Noise floor** — verdict agreement, same case, OFF×3 | verdict | ≥ 90 % |
| Injection rate, treatment | `references.injected_clause_ids` contains the clause | ≥ 95 % |
| Injection rate, control | same | 0 |
| Citation rate, treatment | `cited_clauses[].clause_id` | reported (attribution, not behaviour) |
| **Verdict shift**, treatment (ON ≠ OFF) | verdict pair | shift − noise ≥ 30 pts, p < 0.01 |
| **Action shift**, treatment — the recommendation now requires employer verification | structured first: a planned write with `status ∈ {under_review, verify_employment}` or a required-action field; keyword fallback counted separately | reported alongside verdict shift |
| Verdict shift, control | verdict pair | ≤ noise floor |
| Stability with memory ON — treatment cases run 2× ON | verdict | ≥ noise floor agreement |
| Direction of shift | verdict pair | reported as a transition table: Approve→Hold, Approve→Reject, Hold→Reject, … |

### Impact (H4 — on the planted signal)

| Metric | Definition | Pass |
|---|---|---|
| Catch rate | planted-coached files whose ON verdict is not `approved` ÷ planted-coached files | ≥ 80 % |
| Friction rate | genuine DSA files whose ON verdict moved away from `approved` ÷ genuine DSA files that were `approved` OFF | ≤ 20 % |
| Exposure caught | Σ `amount_requested` of planted-coached files not approved ON | ₹, reported |
| Exposure approved that later defaults | Σ `amount_requested` of planted-coached files approved ON | ₹, reported — this is the miss |
| Cost of friction | count of genuine files sent to verification × officer minutes per verification (assume 20 min, stated) | reported |
| Officer workload delta | verification steps required ON − OFF, per 100 cases | reported |

### Running cost

| Metric | Read from |
|---|---|
| Model spend per run, ON vs OFF | `usage.cost` (or tokens × price) |
| Latency per run, ON vs OFF | `duration_ms` |

### Reversibility (H5)

| Metric | Pass |
|---|---|
| After retiring the clause, verdict agreement with the OFF arm on 15 treatment cases | ≥ noise floor |
| Clause no longer in `injected_clause_ids` | 100 % |

---

## 5. Statistics

- **Noise floor first.** Report it as a number. Every "shift" below is stated
  net of it.
- **Paired comparison.** Each case is its own control (OFF vs ON). Count
  discordant pairs; **exact sign test** (McNemar for the 2×2). Report the count,
  not only p.
- **Effect size with interval.** Shift rate with a 95 % Wilson interval. A p-value
  alone is not the result.
- **No exclusions after the fact.** Hard declines in the treatment pool are
  reported in a separate row, not dropped. A case that fails to run is a harness
  failure, reported by id, never a data point.
- **Pre-registration.** The thresholds in §1 are the thresholds. If a result
  misses, the report says it missed.

---

## 6. Harness — required fixes before the first run

`memory_ab_dsa.py` is the starting point. It does not run as-is:

1. **Mint a token per case**, or one whose expiry exceeds the run. The old run
   lost 15 cases to a token that expired at hour one.
2. **A non-200 aborts the run** and names the case. Never write a row for a
   response that was refused.
3. **Structured verdict** from `write_events[].payload.status`; `decision` text
   only as a labelled fallback.
4. **Record `usage`, `duration_ms`, `model`** on every row — the cost metrics
   and the provider actually used.
5. **Pin the model.** Drop `:nitro`; set a provider in `LLM_BASE_URL` routing or
   use the non-routed id. Record `response.model` and fail if it changes
   mid-run.
6. **Assert clause state before every run** (`active` / `retired` as the arm
   requires) by reading `smartapp_clauses`, and write the observed state into the
   row.
7. **One output**: append-only JSONL, one row per run, schema fixed in the
   file's first line; plus the frozen case-id lists and the commit SHA.
8. **Interleave** arms and OFF/ON as in §3.

---

## 7. Procedure

1. **Clean slate.** No containers, volumes, or images from a previous run.
   Fresh clone at a recorded commit.
2. **Install through the wizard** — the demo tenant path. Record: commit SHA,
   `LLM_MODEL`, `EMBEDDING_MODEL`, the clustering thresholds in force.
3. **Plant the H4 signal** in the seed and re-seed (one generator change, behind
   a flag, off by default; the flag value is recorded).
4. **Freeze case pools** by running the gate query; write the id lists.
5. **Confirm memory is empty**: `smartapp_clauses` has no clause for the app.
6. **Noise floor**: 15 cases × 3 OFF runs. Compute H0. **Stop if it fails.**
7. **OFF arm**: all treatment and control cases once, interleaved.
8. **Teach**: nine corrections from three personas through the correction API.
   Wait for the clustering job. Verify the clause (H1). Screenshot the Memory
   screen — this is the demo footage.
9. **ON arm**: all treatment and control cases once, interleaved with a second
   ON run of 15 treatment cases for stability.
10. **Score H2, H3, H4.** Write the transition table.
11. **Retire the clause** in the Memory screen. Re-run 15 treatment cases (H5).
12. **Report**: one markdown results file beside this protocol, the JSONL, the
    frozen pools, the SHA. The old `memory_ab_dsa_results*.json` files are
    deleted in the same commit.

---

## 8. Budget

| | Estimate | Basis |
|---|---|---|
| Runs | ~220 | §3 |
| Wall time | 1.5–2.5 h with 4 in flight | ~1–2 min per run, measured on the first five and revised |
| Model spend | measured on the first five runs, then extrapolated | `usage.cost` |
| Officer time (teaching) | 30–45 min | nine corrections, three personas |

---

## 9. What the recording shows afterwards

Only what happened in this run, on this screen, in this order: the OFF
recommendation on a treatment case; the three officers' corrections going in;
the Memory screen showing the clause form and go active; the same case ON with
the changed recommendation and the officers' names beside it; the clause
retired and the recommendation revert. No figure appears in the video that is
not in the results file of this run.
