<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: Apache-2.0

  Licensed under the Apache License, Version 2.0 (the "License"); you may not
  use this file except in compliance with the License. You may obtain a copy of
  the License at http://www.apache.org/licenses/LICENSE-2.0
-->

# Rules vs Judgements — SOP Supremacy & the Judgement Hierarchy

> Status: IMPLEMENTED in dev (2026-07-27) - J1-J7 built, 24 new tests + full suite green, live-verified · Owner: rohit@trustedweartech.com
> Phase 2 of [clause-memory-graph-plan.md](clause-memory-graph-plan.md). That plan built the
> machinery (evidence ledger → consolidation → scoped clauses → retrieval →
> citation). This plan fixes what a **logical system review** found wrong with
> how that machinery treats *authority* — merged with the owner's doctrine
> below, which resolves several findings more cleanly than the original design.

---

## 0. The doctrine (owner input, 2026-07-27 — this section is normative)

**SOP is king.** The organization's authored standard (the live SOP corpus)
outranks everything the system ever learns. No learned content may override,
soften, or compete with it. When they conflict, SOP wins — and the conflict is
*surfaced*, never silently resolved.

**What officers teach is not rules — it is JUDGEMENT.** The canonical example:
a loan application shows healthy declared earnings, but an experienced officer
notices the tax filing doesn't corroborate them at specific identifiers. No SOP
clause enumerates that tell. That is *experience* — pattern recognition earned
over years — and it is precisely the unknown the SOP cannot cover. The system's
learned layer exists to capture and reuse it.

**A single officer's judgement matters.** It cannot be discarded while waiting
for two more officers who may never exist (a one-officer branch office is a
normal deployment). A lone judgement is used **immediately**, presented for
exactly what it is — *one officer's judgement, not yet corroborated* —
and corroboration **upgrades its standing** instead of gatekeeping its
existence.

This yields a three-tier authority hierarchy, told to the model in plain terms:

| Tier | What it is | Source | Presented to the AI as |
|---|---|---|---|
| **1. Rules** | The SOP — the org's standard | Authored; live-fetched from the SOP corpus | "RULES — your organization's standard. Supreme. Always follow. Nothing below overrides these." |
| **2. Team judgement** | A lesson several officers agree on | ≥ `promotion_min_officers` distinct officers | "TEAM JUDGEMENT — agreed by N officers; apply where it fits, cite it" |
| **3. Individual judgement** | One or two officers' experience, awaiting corroboration | < gate | "AN OFFICER'S JUDGEMENT — noted by [role], not yet corroborated by the team; weigh it, verify from the record, cite it if used" |

Design consequence: the old model — *hide candidates until promoted* — is
replaced by *inject with honest attribution*. "One opinion must not become
policy" is solved by **labeling, not suppression**. This supersedes
clause-memory-plan §8's candidate-exclusion and partially supersedes the
recency-window rationale (a lone judgement no longer needs to "fade": it
persists, scoped and attributed, until corroborated, contradicted, or retired).

> Terminology mapping (code keeps its names; every human/AI surface changes):
> `clause` → **judgement** · `candidate` → **individual judgement** ·
> `active` → **team judgement** · SOP block → **rules**. The Memory screen tab
> becomes **"Judgements it has learned"**; the sales material already uses
> "rule" for the learned layer and must be revised to reserve "rules" for SOP.

---

## 1. Findings register (logical review, 2026-07-27)

| # | Finding (real-life vignette) | Severity | Resolved by |
|---|---|---|---|
| F1 | **Learned content can codify what the law no longer requires; nothing arbitrates vs SOP.** Regulator drops the FIR-in-24h requirement; three old-guard officers still enforce it; the model receives contradictory SOP and learned blocks with undefined precedence. | Critical | Phase J1 (supremacy prompt) + J3 (conflict detection) |
| F2 | **"As discussed" × 3 = junk judgement.** Officers write "ok" / "see file"; vacuous texts cluster on mutual similarity; a content-free judgement is authored and injected forever. | High | Phase J4 |
| F3 | **Silent gate failures on small/shared-login teams.** One-officer branch: gate of 3 unreachable, nothing says so. Shared kiosk login: five humans = one "officer". One human on three accounts = instant "consensus". | High | Doctrine §0 (single judgements now used) + J6 (visibility) |
| F4 | **The recent-corrections window is app-global.** A monsoon flood of 400 corrections evicts the lone theft lesson within hours — the rare case type loses its advisor exactly during the surge. Inconsistent with the comparability-ranked precedent channel (§11 of parent plan). | High | Phase J5 (also largely superseded by J2: lone judgements persist as clauses) |
| F5 | **A person's name can become a generalized judgement; PII flows into prompts.** "Mr. Sharma is a repeat fraudster" × 3 → a judgement naming an individual, injected into every theft case. Officer reasons may carry phone/ID numbers into storage, exports and provenance UI. | High (compliance) | Phase J4 |
| F6 | **No officer-scoped quarantine.** An officer dismissed for collusion leaves judgements they helped teach; the provenance to find them exists, the operation doesn't. | Medium | Phase J6 |
| F7 | **Renaming a reason code splits one lesson across generations.** `wrong_department` → `misrouted`: clustering hard-partitions by code; four agreeing officers sit in two sub-gate clusters forever. | Medium | Phase J7 |
| F8 | **Rubber-stamping reads as success.** Month-end fatigue: officers approve unread; dashboards show rising acceptance; the system cannot tell trust from fatigue. | Medium (human) | J6 (velocity stat, visibility only) |
| F9 | **Automation bias inflates precision.** Officers anchored by the AI's cited judgement agree more; precision partly measures influence, not correctness. Structural mitigation already present (support only ever comes from *dis*agreement). | Accepted, documented | Docs + sales honesty |
| F10 | **Goodhart on dashboards.** If acceptance % becomes a manager KPI, officers game the very feedback learning depends on. | Accepted, org-level | Docs; guidance in admin UI copy |
| F11 | Coincidental scope narrowing → sibling clutter; band-edge gaming (structuring at $24,900); budget eviction of low-rank judgements. | Low / self-healing | Documented; sibling-merge sweep noted as future work |

---

## 2. Phase J1 — the authority hierarchy in the prompt (F1 runtime half)

The single highest-leverage change: the model must *know* the hierarchy.

1. **Rename the blocks.** The SOP block header becomes
   `RULES — your organization's standard operating procedure`. The clause block
   header becomes `JUDGEMENTS — learned from your officers' experience`.
2. **State supremacy explicitly**, once, above both:
   > "RULES are supreme. JUDGEMENTS are officers' experience — they fill gaps
   > the rules do not cover and sharpen how you apply them. A judgement can
   > never override a rule. If a judgement appears to conflict with a rule,
   > FOLLOW THE RULE and say so in your reasoning (`sop_conflict` note) — that
   > report is how the team finds stale judgements or stale SOPs."
3. **Tier attribution inside the judgement block** (see §3 for selection):
   - team judgement: `- [C-017] <text> (team judgement — N officers)`
   - individual: `- [C-031] <text> (one officer's judgement — not yet corroborated; verify against the record before relying on it)`
4. **Audit block**: `cited_clauses[].relation` gains `"overrode_by_rule"` so the
   model can report "a judgement pointed one way, the RULE pointed the other, I
   followed the rule." This is F1's *runtime* backstop and doubles as a data
   feed for J3 (a judgement repeatedly overridden-by-rule is flagged without
   any extra machinery).

**Touchpoints:** `clause_store.render_block` (headers/attribution),
`runtime.py` audit-block contract + `_extract_audit_block`, the SOP-fetch
prompt assembly in the item tools (same supremacy line).
**Test:** render tests for both tiers; extractor test for the new relation.

## 3. Phase J2 — individual judgements are injected, labeled, and bounded

Retrieval changes (supersedes parent-plan §8's candidate exclusion):

1. `candidates_for_facets` statuses become `("active", "candidate", "dissented")`.
2. **Ranking:** team judgements rank above individual regardless of score
   (tier is the primary key after specificity-within-tier). Dissent notices last.
3. **Shadowing:** the existing dedupe-by-(reason_code, contested_fields) keeps
   the highest tier — a team judgement on a lesson silently *shadows* an
   individual one on the same lesson (the individual's evidence has, by then,
   usually been absorbed anyway).
4. **Bound the tail:** at most `MAX_INDIVIDUAL_JUDGEMENTS` (default 3) per case,
   drawn from the budget *after* team judgements — a case must never drown in
   twelve uncorroborated opinions.
5. **Blame/dissent apply from day one:** an individual judgement that misleads
   accrues blame on its first outing; `record_dissent` and precision work
   identically across tiers. Corroboration (reinforce) upgrades it to team; a
   single sustained dissenter now *matters more* (1 support vs 1 dissent ⇒
   dissent ratio 0.5 ⇒ suppressed to a disagreement notice) — which is correct:
   two officers disagreeing 1-1 IS an open question, not anyone's judgement.
6. **One-officer apps** (F3-C): nothing special-cased — the app simply runs on
   individual judgements indefinitely, correctly labeled. J6 makes that state
   visible to the admin rather than mysterious.

**Touchpoints:** `clause_store.py` (statuses, rank_and_budget tiering,
render attribution), `learned_memory.py` unchanged (contract already returns
ids), tests for tiering/shadowing/bounds.

## 4. Phase J3 — judgement-vs-SOP conflict detection (F1 authoring half)

At consolidation time, when a judgement is created **or reinforced**, and the
app has a configured `sop_source`:

1. Retrieve top-k SOP passages for the judgement text (the existing rag-probe
   path the item tools already use — no new infrastructure).
2. One bounded LLM check: *"Does this judgement CONTRADICT any passage — not
   merely add to it? Judgements that fill gaps are expected and fine."*
   Output: `none | contradicts(passage_ref, one-line why)`.
3. On contradiction: set `sop_conflict: {passage_ref, note, at}` on the clause,
   move it to status `dissented`-equivalent (**new status `sop_conflict`**,
   excluded from injection as a judgement; rendered — like dissent — as at most
   one line: *"a learned judgement conflicts with the SOP here and is under
   review"*), and raise a supervisor flag on the Memory screen.
4. **Both resolutions are one tap** on the review surface: *retire the
   judgement* (SOP is right) or *acknowledge — SOP update needed* (the officers
   are right and the SOP is stale; the judgement returns to service with an
   `sop_ack` marker and the disagreement recorded). The second path is the
   owner's point: officer judgement is how the org *discovers* its SOP gaps.
5. Runtime drift between checks is covered by J1's supremacy instruction and
   the `overrode_by_rule` citations: a judgement collecting those is re-checked
   on the next consolidation pass without waiting for reinforcement.

**Honesty constraint:** the checker can miss conflicts (RAG recall, LLM
judgement). J1's runtime supremacy is therefore the primary defense; J3 is the
early-warning system. Never described to customers as a guarantee.

**Touchpoints:** `consolidation.py` (check on create/reinforce), clause schema
(+`sop_conflict`), `clause_store` retrieval/rendering, Memory screen flag + the
two-tap resolution, tests with a stubbed retriever.

## 5. Phase J4 — evidence quality gates (F2, F5)

At consolidation, before authoring:

1. **Substance gate (F2):** a cluster may author only if its combined reason
   texts carry ≥ `MIN_CONTENT_TOKENS` (default 6) distinct content tokens
   (stopword-stripped — machinery exists in `content_tokens`). Refused clusters
   are marked `insufficient_reason` (NOT consumed — if an officer later writes
   a real reason for the same lesson, the vacuous ones ride along as
   corroboration they still are). Surfaced as a Memory-screen counter:
   *"N corrections too brief to learn from — ask officers for one concrete
   sentence"* — a coaching signal, not a silent bin.
2. **Pattern-not-person gate (F5):** the authoring prompt gains a hard
   constraint: *"Describe the PATTERN, never the person. No personal names,
   phone numbers, account or ID numbers — person-specific concerns are handled
   by entity screening, not judgements."* Post-check: reject authored text
   matching obvious identifier shapes (phone/ID regexes already exist in
   `fraud_checks` normalizers); on rejection, retry once with the violation
   quoted, then leave pending. A person-shaped lesson belongs to
   `entity_links`, which already exists for exactly that.
3. **Reason-text PII posture (decision recorded, not built):** stored reasons
   remain verbatim (single-tenant posture, audit value) but are **never**
   injected into prompts (already true) and the provenance UI remains
   curator-gated (already true). A masking pass at export is noted as a
   customer-onboarding option, not built now.

**Touchpoints:** `consolidation.py` (two gates), `corrections.py`
(insufficient_reason marker), Memory screen counter, tests incl. the
"as discussed ×3" and "Mr. Sharma ×3" vignettes verbatim.

## 6. Phase J5 — comparability-ranked corrections window (F4)

`_prefetch_corrections_block` currently takes the 8 most *recent* corrections
app-wide. Change: over-fetch (40, as now), rank by facet overlap with the
current case (staging rows already carry frozen `case_facets`; overlap
coefficient, same metric as clustering), recency as tiebreak; empty-facet rows
keep recency order (honest degradation, same rule as §11 of the parent plan).

Post-J2 this channel narrows to what it is good at: **uncoded and not-yet-
clustered** corrections — the newest, rawest signal. Coded-and-clustered
lessons now persist as judgements instead of depending on this window, which
resolves the monsoon-surge vignette twice over.

**Touchpoints:** `runtime.py` prefetch (thread `_case_facets`, rank), tests.

## 7. Phase J6 — visibility & org operations (F3, F6, F8)

1. **Gate-reachability notice (F3):** consolidation status + App Memory card
   gain `distinct_officers_seen` per app. When it is below
   `promotion_min_officers`: *"This app has seen N officer(s). Its judgements
   stay 'individual' until more officers correct it — they are still used,
   clearly labeled."* Informational, since J2 removed the cliff. Shared-login
   caveat goes in the builder skill (one login shared by many = the gate
   overcounts agreement in one direction and undercounts in the other; fix is
   account hygiene, not software).
2. **Taught-by view + quarantine (F6):** Memory screen filter "taught by
   officer X" (query on `support_officers`/`provenance` — data already there);
   bulk **suspend** action → new status `quarantined` (excluded from injection,
   evidence intact, reversible), with actor + cause in `history`. The
   dismissed-officer drill: one filter, one review, one tap per judgement.
3. **Review-velocity stat (F8):** median seconds-from-render-to-decision per
   app/officer-cohort on the loop-metrics endpoint, shown on the Success Rate
   drill-down with one honest caption: *"very fast approvals may mean trust —
   or unread approvals; this number cannot tell which, a supervisor can."*
   Visibility only. **Never** an enforcement input, never a per-officer public
   ranking (Goodhart, F10 — and the no-trust-tiers doctrine).

## 8. Phase J7 — reason-code aliasing (F7)

`ReasonCodeSpec` gains `aliases: []` (validated unique across the taxonomy,
CS-01). Consolidation normalizes correction codes through the alias map before
partitioning, so `wrong_department` evidence and `misrouted` evidence are one
lesson. The builder skill's rename instruction becomes: *"never delete a code —
rename by moving the old code into `aliases`."* Publish validator warns when a
previously-published code disappears without an alias (needs the prior spec —
available at publish via the stored app).

---

## 9. What is deliberately NOT changed

* **No per-officer trust weights.** Tiers reflect *count of corroborating
  officers*, never who they are. (Standing doctrine; F9/F10 pressure-tested it
  and it held.)
* **No autonomy widening.** Judgements of any tier shape recommendations only;
  approval surfaces and kill switches are untouched.
* **Agreement still never counts as support.** Only corrections carry evidence;
  an approved recommendation teaches nothing (F9's structural mitigation stays).
* **Accepted risks documented, not "fixed":** automation bias in precision
  (F9), dashboard Goodhart (F10), band-edge structuring and sibling clutter
  (F11) — each noted in the plan and, where customer-facing, in sales honesty
  guidance. A sibling-merge sweep is future work.

## 10. Sequencing & acceptance

| Order | Phase | Acceptance |
|---|---|---|
| 1 | J1 prompt hierarchy | render + extractor tests; a judgement-vs-rule conflict case shows `overrode_by_rule` in a live dev run |
| 2 | J2 individual judgements | tier/shadow/bound tests; one-officer-app dev scenario: single correction → labeled judgement injected on the next matching case |
| 3 | J4 quality gates | "as discussed ×3" authors nothing + counter increments; "Mr. Sharma ×3" authors a pattern or nothing — never a name |
| 4 | J5 corrections ranking | surge vignette test: theft lesson survives 40 flood corrections |
| 5 | J3 SOP conflict | stubbed-retriever tests + one live dev run against the acme SOP library; two-tap resolution works |
| 6 | J6 visibility/quarantine | gate notice renders; taught-by filter + quarantine round-trip |
| 7 | J7 aliasing | split-lesson test: 2+2 across an alias reaches the gate |

UI copy sweep (tab rename to "Judgements", sales page revision reserving
"rules" for SOP) rides with J2. All phases dev-tested before any prod motion,
per standing practice.
