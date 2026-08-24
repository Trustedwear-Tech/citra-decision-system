<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: Apache-2.0

  Licensed under the Apache License, Version 2.0 (the "License"); you may not
  use this file except in compliance with the License. You may obtain a copy of
  the License at http://www.apache.org/licenses/LICENSE-2.0
-->

# Clause Memory & Case Signature — Implementation Plan

> Status: PLAN (2026-07-26) · Owner: rohit@trustedweartech.com
> Replaces the single-blob learned rubric with an **atomic, scoped, provenanced
> clause store** routed by a **case signature** (closed-vocabulary facets).
> Companions: [citra-self-improving-loop-plan.md](citra-self-improving-loop-plan.md) (the loop this
> upgrades), [adoption-metrics-precedent-citation-plan.md](adoption-metrics-precedent-citation-plan.md)
> (citation infrastructure this reuses), [multimodal-decision-apps-plan.md](multimodal-decision-apps-plan.md)
> (§4.4, where the current rubric was specified), [vertical-country-ontology-plan.md](vertical-country-ontology-plan.md)
> (the ontology this extends).

---

## 0. What ALREADY exists (scouted 2026-07-26 — build on it, don't duplicate)

| Piece | Reality |
|---|---|
| Learned-rubric store | `analysis_rubrics.py` — bucket `(tenant_id, app_slug, modality, task_type)`, one `summary` blob capped at `MAX_RUBRIC_WORDS=1000`. |
| Raw feedback | **INTACT.** `corrections[]` is append-only and *never rewritten* (`append_correction`, :437). This is the whole migration story — no evidence has been lost, only the derived view degrades. |
| Full officer reason | **INTACT.** `REASON_MAX_CHARS=500` truncates only the *rubric copy*; the untruncated text stays on the DecisionRecord, joinable via `correlation_id`. |
| Fold sites | `fold_decision_feedback` (:91) called from `main.py:8175` (reject) and `main.py:8387` (approve-with-overrides). One correction line per decision event. |
| Injection sites | `_prefetch_decision_rubric` (runtime.py:661) for the record decision; `tools_v2_dispatch.py:1928 / :2144 / :2419` for item analysis. All go through `rubric_to_prompt`. |
| Citation contract | **EXISTS.** The audit block (runtime.py:105) already makes the model emit `cited_precedents[{decision_id, relation, note}]`, parsed by `_extract_audit_block` (:3557) and persisted (models.py:3765). Clause citation is the same mechanism, one more key. |
| Entity graph | **EXISTS.** `entity_links.py` — closed-vocabulary identifier → case overlay, write-through, with ring / synthetic-identity / double-dip signals. Half the graph is already built. |
| Ontology | `sources.json` → source → `domain{vertical, sub_vertical, country}` → `datasets[]` → `columns[]` (enum values already live in column `description`), plus `fraud_semantics` / `write_actions`. |
| Background job pattern | `_grounding_rebuild_loop` (main.py:915) — the precedent for the consolidation worker. |
| Neighbours | `fetch_item_precedents` (item_records.py:265) ranks by `disposition_at` **recency**, not similarity. `_query_neighbor_samples` (tools_v2_dispatch.py:2965) is vector + scalar filters on the shared `Historical_Refresh` collection. |

**So the real build is: one ontology block + one new collection pair + one batch worker + a retrieval function + a citation key.** The evidence, the citation plumbing, the entity graph and the job pattern all exist.

---

## 1. Diagnosis (why the blob dilutes)

Five failure modes, in order of damage:

1. **Unconditional generalization.** `_resummarize`'s system prompt says *"generalise specifics into clear imperative rules"* — it must, because nothing tells it which case comes next. Context-bound lessons flatten into context-free imperatives. That is dilution, and it is structural, not a prompt bug.
2. **No credit assignment.** The rubric enters the prompt as one undifferentiated block, so a reject is feedback against *all of it*. A bad rule is never blamed, keeps misfiring, generates more corrections, dilutes further. Positive feedback loop.
3. **Generation loss.** N corrections → N full-text LLM rewrites of the same passage; each is a lossy re-encode of a lossy encode. Also O(N) LLM calls **on the officer's synchronous request path** (30s timeout inside approve/reject).
4. **One conditioning axis, and it is free text.** `subject` (120 chars) is the only in-bucket discriminator.
5. **Dissent is destroyed.** The summarizer silently resolves disagreement — effectively last-write-wins. A 60/40 split and a unanimous rule render identically.

**The reframe:** compression happens at *write* time when it should be selection at *read* time. Write-time compression is irreversible; read-time selection is lossless. The 1000-word budget stops being a storage ceiling and becomes a per-case injection budget.

---

## 2. `case_signature` — the builder-authored ontology block

### 2.1 Where it lives, and why

**In `app_spec`, not `sources.json`.** Two reasons:

* Two apps bound to the same dataset care about different facets — the *selection* is an app-level decision, not a source-level fact.
* `sources.json` changes require an MCP image rebuild before the file lands (`extra=forbid`; see [project memory: MCP-image-before-sources.json order]). Putting the signature in `app_spec` keeps facet authoring inside the normal publish cycle with zero MCP coupling.

The facets *reference* dataset columns by name; the publish validator resolves them against the bound dataset's ontology and fails loud on a missing column.

### 2.2 Schema

Added to `schemas/app_spec.schema.json`:

```jsonc
"case_signature": {
  "type": "object",
  "required": ["version", "facets", "reason_codes"],
  "additionalProperties": false,
  "properties": {

    "version": { "type": "integer", "minimum": 1 },

    // ── Facet families ────────────────────────────────────────────────────
    // Each family emits AT MOST ONE token per case, of the form "family:value".
    // Token grammar (enforced): ^[a-z][a-z0-9_]{0,31}:[a-z0-9_.<>+-]{1,40}$
    "facets": {
      "type": "array", "minItems": 1, "maxItems": 24,
      "items": {
        "type": "object",
        "required": ["family", "kind"],
        "additionalProperties": false,
        "properties": {
          "family":     { "type": "string", "pattern": "^[a-z][a-z0-9_]{0,31}$" },
          "kind":       { "enum": ["enum", "band", "presence", "age_band", "signal"] },
          "dataset_id": { "type": "string" },   // defaults to the app's primary dataset

          // kind=enum — promote an existing closed-vocabulary column.
          // `values` MUST be declared: an unseen value at runtime is ontology
          // drift and is reported, never silently absorbed (see §4.3).
          "from_column": { "type": "string" },
          "values":      { "type": "array", "items": { "type": "string" }, "maxItems": 40 },
          "value_map":   { "type": "object", "additionalProperties": { "type": "string" } },

          // kind=band — numeric/currency column → ordered bands.
          // edges [1000, 25000, 100000] ⇒ lt_1000 | 1000_25000 | 25000_100000 | gte_100000
          "edges": { "type": "array", "items": { "type": "number" }, "minItems": 1, "maxItems": 6 },

          // kind=presence — nullable column → "<family>:present" | "<family>:absent"
          // (from_column carries the field checked)

          // kind=age_band — day-difference between two date columns, then banded.
          "from_columns": { "type": "array", "items": { "type": "string" },
                            "minItems": 2, "maxItems": 2 },

          // kind=signal — a runtime-produced signal id from the CLOSED platform
          // set (exif_claim_conflict, duplicate_artifact, entity_ring,
          // synthetic_identity, double_dip, amount_outlier, missing_required_field).
          // Emits "<family>:fired" | "<family>:clear"; omitted when the signal
          // did not run for this app (never guessed).
          "signal_id": { "type": "string" }
        }
      }
    },

    // ── Reason taxonomy (why an officer rejects) ──────────────────────────
    // Closed per app. The platform ships a starter set; the builder edits it.
    // `other` is permitted but consolidation NEVER forms a clause from an
    // other-only cluster — instead it surfaces "N corrections coded other" so
    // the builder extends the taxonomy (§9.6).
    "reason_codes": {
      "type": "array", "minItems": 3, "maxItems": 20,
      "items": {
        "type": "object",
        "required": ["code", "label"],
        "additionalProperties": false,
        "properties": {
          "code":  { "type": "string", "pattern": "^[a-z][a-z0-9_]{0,39}$" },
          "label": { "type": "string", "maxLength": 60 },
          "hint":  { "type": "string", "maxLength": 160 }
        }
      }
    },

    // ── Learning controls ─────────────────────────────────────────────────
    "learning": {
      "type": "object",
      "additionalProperties": false,
      "properties": {
        "promotion_min_officers": { "type": "integer", "minimum": 1, "default": 3 },
        "clause_budget_words":    { "type": "integer", "minimum": 100, "default": 1000 },
        "mode": { "enum": ["summary", "clauses", "both"], "default": "summary" }
      }
    }
  }
}
```

`domain.vertical` / `domain.sub_vertical` / `domain.country` are emitted **automatically** as facets — never authored, always present.

### 2.3 Worked example — motor claim approval

```jsonc
"case_signature": {
  "version": 1,
  "facets": [
    { "family": "loss_type",    "kind": "enum",     "from_column": "loss_type",
      "values": ["collision","theft","fire","flood","windshield","vandalism"] },
    { "family": "policy_class", "kind": "enum",     "from_column": "policy_class",
      "values": ["personal","commercial_fleet"] },
    { "family": "amount_band",  "kind": "band",     "from_column": "claim_amount",
      "edges": [1000, 25000, 100000] },
    { "family": "police_report","kind": "presence", "from_column": "police_report_no" },
    { "family": "photos",       "kind": "presence", "from_column": "photos" },
    { "family": "policy_age",   "kind": "age_band",
      "from_columns": ["policy_start_date","loss_date"], "edges": [30, 180] },
    { "family": "exif",         "kind": "signal",   "signal_id": "exif_claim_conflict" }
  ],
  "reason_codes": [
    { "code": "evidence_insufficient", "label": "Evidence insufficient" },
    { "code": "exclusion_applies",     "label": "Policy exclusion applies" },
    { "code": "policy_not_in_force",   "label": "Policy not in force" },
    { "code": "amount_incorrect",      "label": "Amount incorrect" },
    { "code": "fraud_false_positive",  "label": "Fraud flag was wrong" },
    { "code": "fraud_missed",          "label": "Fraud indicator missed" },
    { "code": "authority_limit",       "label": "Above my authority" },
    { "code": "data_stale_or_wrong",   "label": "Source data stale or wrong" },
    { "code": "other",                 "label": "Something else" }
  ],
  "learning": { "promotion_min_officers": 3, "mode": "summary" }
}
```

~35 lines, and every enum was copied from a column description that already existed. **The builder is not designing a taxonomy — they are ticking which columns are decision-relevant.**

### 2.4 Publish validation (`publish_validators.py`)

New `validate_case_signature(app_spec, bound_datasets)` — all failures are **errors**, never warnings:

| Check | Failure |
|---|---|
| `from_column` / `from_columns` resolve against the bound dataset | `case_signature_unknown_column` |
| `kind=band` / `age_band` column is numeric / timestamp | `case_signature_type_mismatch` |
| `kind=signal` `signal_id` is in the platform closed set | `case_signature_unknown_signal` |
| Family names unique; token grammar satisfiable | `case_signature_duplicate_family` |
| `edges` strictly increasing | `case_signature_bad_bands` |
| ≥3 reason codes; codes unique; `other` not the only code | `case_signature_thin_taxonomy` |
| Estimated cell count `Π|family|` ≤ 20 000 | `case_signature_cardinality` (warn at 5 000) |

---

## 3. `smartapp_corrections` — the evidence ledger

Promoted out of the embedded `corrections[]` array into its own env-routed collection (same `_col()` lazy-main + `_test_collection_name` pattern as `analysis_rubrics` / `entity_links`).

```jsonc
{
  "correction_id":  "corr-9004",          // ULID; stable, quotable
  "tenant_id":      "acme-insure",        // via rubric_tenant_for_app — ONE key, as today
  "app_slug":       "motor-claim-approval",
  "modality":       "record",             // reuse the existing axes verbatim
  "task_type":      "decision",

  "correlation_id": "run_01J...",         // → DecisionRecord: FULL untruncated reason
  "case_ref":       { "dataset_id": "claims.motor_claims", "keys": {"claim_id": "CLM-4471"} },

  // The signature AT DECISION TIME, frozen. Never recomputed — a later
  // ontology edit must not silently rewrite history.
  "case_facets":    ["loss_type:theft","amount_band:25000_100000","policy_age:lt_30",
                     "police_report:absent","photos:present","policy_class:personal",
                     "country:us","vertical:insurance"],
  "signature_version": 1,

  "officer":        "maria@acme-insure.com",
  "officer_role":   "claims_adjuster",
  "event":          "reject",             // reject | override
  "recommendation": "Approve $38,000",    // what the AI proposed

  "reason_code":     "evidence_insufficient",   // from case_signature.reason_codes
  "reason_inferred": false,                     // true ⇒ backfilled by classifier (§12)
  "contested_fields":["police_report_no"],
  "overrides":      [{"field":"decision","from":"approve","to":"refer_siu"}],
  "reason_text":    "Theft over $25k needs a police report number on file. The reinsurer bounces these.",

  // Credit assignment (§10)
  "injected_clause_ids": ["C-003","C-011"],   // what the model SAW
  "cited_clause_ids":    ["C-011"],           // what the model SAID it used

  "consumed_by":    null,                 // clause_id once folded; null ⇒ pending
  "at":             ISODate("2026-07-26T09:14:00Z")
}
```

**`consumed_by` replaces the positional `summarized_count` watermark.** Same lossless guarantee — a failed consolidation leaves `consumed_by: null` and the correction folds next pass — but robust to reordering and re-runnable per document.

**Reason cap.** `corrections.CORRECTION_REASON_MAX_CHARS = 2000` — the ledger keeps the officer's full text, safe because it never enters a run prompt and only feeds consolidation.

> **Implemented as a SECOND constant, not a raise of the existing one.** `analysis_rubrics.REASON_MAX_CHARS` stays 500: while any app is still in `mode='summary'`, that text *is* the summarizer's input and *does* shape a bounded prompt. Two consumers, two budgets — collapsing them would have quietly tripled the legacy summarizer's input on every app that has not migrated.

Indexes:
* `{tenant_id:1, app_slug:1, modality:1, task_type:1, consumed_by:1, at:1}` — the consolidation scan
* `{tenant_id:1, app_slug:1, correlation_id:1}` — decision drill-down
* `{correction_id:1}` unique

---

## 4. Facet derivation at run time

### 4.1 Where

New `case_signature.py`, called from `runtime.py` immediately before `_prefetch_decision_rubric`, and from the item-analysis dispatch paths. Output is threaded into the run context and stamped on the staging row.

```python
def derive_facets(record: dict, sig: CaseSignature, *, signals: dict) -> FacetSet
```

### 4.2 Deterministic by construction

No LLM. Every facet family is a pure function of a column value, a band table, a null check, a date difference, or a signal that already ran. This is the property that makes the graph a routing table rather than a guess — and it means facet derivation costs no tokens and cannot fail open.

### 4.3 Ontology drift — fail loud, never absorb

An `enum` column value not in `values` emits `family:__unknown` and logs at WARNING with the offending value. `__unknown` is a **legal token that no clause may ever be scoped to** (rejected by the clause writer), so it can never match — but it *is* queryable, so the Memory screen can show *"loss_type:\_\_unknown on 23% of cases in the last 30 days"*. Drift becomes a visible metric instead of a silent mis-route.

A `presence` family on a missing column, or a `band` family on a non-numeric value, raises — that is a publish-validated invariant being violated at run time and must not be swallowed.

---

## 5. `smartapp_clauses` — the clause store

```jsonc
{
  "clause_id":   "C-017",                 // stable per (tenant, app); quoted in prompts
  "tenant_id":   "acme-insure",
  "app_slug":    "motor-claim-approval",
  "modality":    "record",
  "task_type":   "decision",

  "text": "For theft losses above $25,000, do not recommend approval unless a police report number is on file.",
  "text_words": 19,                       // <= CLAUSE_MAX_WORDS (40)

  // ── Scope: a CONJUNCTION. The clause fires iff scope_facets ⊆ case_facets.
  //    [] = global (fires on every case of this bucket).
  "scope_facets": ["loss_type:theft", "amount_band:25000_100000"],
  "scope_size":   2,                      // denormalised |scope_facets| — specificity sort key
  "signature_version": 1,

  "reason_code":      "evidence_insufficient",
  "contested_fields": ["police_report_no"],

  // ── Provenance: every clause is re-derivable from real officer events.
  "provenance":        ["corr-8812","corr-9004","corr-9130"],
  "support_officers":  ["maria@acme-insure.com","dan@…","priya@…"],   // DISTINCT, capped 50
  "support_count":     3,
  "dissent_officers":  [],
  "dissent_count":     0,

  // ── Performance (aggregated by the consolidation job — NEVER written on the
  //    hot path; derived from injected_clause_ids on decision rows, §10).
  "fired_count":  41,
  "blamed_count": 2,
  "precision":    0.951,                  // null until fired_count >= MIN_FIRED_FOR_PRECISION (10)

  "status":  "active",                    // candidate | active | dissented | superseded | retired
  "version": 3,
  "history": [ { "version": 2, "text": "…", "scope_facets": ["loss_type:theft"],
                 "changed_by": "consolidation", "cause": "refined_by:C-034",
                 "at": ISODate("…") } ],

  // ── Graph edges (out-edges; §7)
  "refines":        [],                   // this clause narrows those
  "refined_by":     ["C-034"],
  "contradicts":    [],
  "superseded_by":  null,
  "merged_from":    ["C-009"],

  "embedding_id": "clause_C-017",         // Milvus PK for the hybrid-recall leg
  "authored_by":  "consolidation",        // consolidation | builder
  "created_at":   ISODate("…"),
  "updated_at":   ISODate("…"),
  "last_confirmed_at": ISODate("…")       // last time a correction reinforced it
}
```

`CLAUSE_MAX_WORDS = 40` (env `CLAUSE_MAX_WORDS`). One rule, one sentence.

Indexes:
* `{tenant_id:1, app_slug:1, modality:1, task_type:1, status:1, scope_facets:1}` — **multikey; the hot retrieval index**
* `{tenant_id:1, app_slug:1, clause_id:1}` unique
* `{tenant_id:1, app_slug:1, status:1, updated_at:-1}` — Memory screen
* `{tenant_id:1, app_slug:1, reason_code:1}` — consolidation matching

---

## 6. The subset query (how routing actually executes)

The retrieval predicate is *set containment*: keep clauses whose scope is a **subset** of the case's facets. Mongo does this in one query:

```python
case_facets = ["loss_type:theft", "amount_band:25000_100000", "policy_class:commercial_fleet", ...]

{
  "tenant_id": tid, "app_slug": slug, "modality": m, "task_type": tt,
  "status": {"$in": ["active", "dissented"]},
  "$or": [
    {"scope_facets": {"$size": 0}},                 # global clauses
    {"scope_facets": {"$in": case_facets}},         # index-selective prefilter (multikey)
  ],
  "$expr": {"$setIsSubset": ["$scope_facets", case_facets]},   # exact containment
}
```

The `$in` clause drives the multikey index (any clause sharing ≥1 facet); `$setIsSubset` is applied as a residual filter on that small candidate set. **No graph database is required for this**, which is the whole point of §7.

---

## 7. The graph — nodes, edges, and where each lives

| Edge | Stored as | Purpose |
|---|---|---|
| `Clause —SCOPED_TO→ Facet` | `clauses.scope_facets[]` (multikey) | **Routing.** The query in §6. |
| `Correction —EVIDENCES→ Clause` | `clauses.provenance[]` ↔ `corrections.consumed_by` | Every clause re-derivable, splittable, auditable. |
| `Correction —BLAMES→ Clause` | `corrections.cited_clause_ids[]` | **Credit assignment** (§10). The highest-value edge. |
| `Clause —REFINES / CONTRADICTS / SUPERSEDES→ Clause` | `clauses.refines[] / contradicts[] / superseded_by` | Additive correction. Contradiction is *stored*, never auto-resolved. |
| `Officer —SUPPORTS / DISSENTS→ Clause` | `clauses.support_officers[] / dissent_officers[]` | Promotion gate; dissent surfacing. |
| `Case —HAS_FACET→ Facet` | `corrections.case_facets[]`, item-ledger rows | Neighbour ranking (§11). |
| `Entity —APPEARS_IN→ Case` | **`smartapp_entity_links` (exists)** | Ring / double-dip; neighbour boost. |

### Do we need a graph database? **No — and not soon.**

* The hot query is set containment on an indexed array — Mongo answers it directly (§6).
* Traversals are ≤3 hops and always start from a known clause or facet.
* Volume is thousands of clauses and tens of thousands of corrections per app.
* Adding Neo4j means a new datastore, a new Vault AppRole, a new backup and deploy story, for a query we already serve.

**Revisit only if variable-length path queries become a product feature** (ring-of-rings, N-hop collusion chains over `entity_links`). That is a fraud-graph decision, not a memory-architecture one, and should be taken on its own merits.

---

## 8. Retrieval & injection

New `select_clauses()` in `clause_store.py`, called from `_prefetch_decision_rubric` (which becomes a dispatcher on `learning.mode`).

```
1. facets  = derive_facets(record, sig, signals)             # §4, deterministic
2. cands   = subset_query(facets)                            # §6
           ∪ milvus_topk(case_narrative, k=20, filter=app)   # hybrid recall for mis-scoped clauses
3. drop status ∈ {retired, superseded}
4. partition off status="dissented" → at most ONE disagreement line (§8.2)
5. score = w_spec·scope_size
         + w_sup ·log1p(support_count)
         + w_prec·(precision ?? PRECISION_PRIOR)
         + w_rec ·exp(-age_days / RECENCY_HALFLIFE)
   sort by (scope_size DESC, score DESC)         # ← specificity backoff, structurally
6. dedupe on (reason_code, contested_fields) keeping the most specific survivor
7. fill to learning.clause_budget_words; emit each with its clause_id
8. return (block, injected_clause_ids)
```

**Step 5's `scope_size DESC` primary sort is the answer to facet sparsity.** It is n-gram backoff: a thin `(theft ∧ photo ∧ us ∧ >25k)` cell falls through to `(theft ∧ photo)` and then `(theft)` with no special-casing and no cold-start cliff. An empty store yields an empty block — exactly today's empty-summary behaviour, no new code path.

### 8.1 Prompt block

```
DECISION CRITERIA learned from this app's officers (clause set v12) — apply the
ones that fit this case and cite the ids you relied on:
- [C-034] For theft on commercial fleet policies, require a fleet incident
  report rather than a police report. (agreed by 4 officers)
- [C-023] Do not approve theft where the vehicle was left unattended with keys
  in the ignition — exclusion 4(b). (agreed by 3 officers)
⚠ Officers disagree on whether theft on a policy under 30 days old always
  requires SIU referral (4 vs 3). Surface both readings; do not assert.
```

### 8.2 Dissent rendering

A clause with `dissent_count / (support_count + dissent_count) >= DISSENT_RATIO` (default 0.34) is **never injected as a rule**. It renders as one ⚠ line naming the disagreement, and raises a builder adjudication flag on the Memory screen. Silent averaging is exactly the failure we are removing.

### 8.3 Audit-block extension

`runtime.py:105` gains one key alongside `cited_precedents` — additive, so agents that omit it still parse:

```jsonc
"cited_clauses": [ {"clause_id": "C-034", "relation": "applied" | "overruled",
                    "note": "<one line: why it fit, or why it did not>"} ]
```

Parsed in `_extract_audit_block` (:3557), carried on `AgentResponse` (models.py:3765 sibling), persisted on the staging/decision row next to `cited_precedents`. **This is the same wire the precedent-citation work already laid** — one more key, one more extractor branch.

---

## 9. Consolidation — corrections → clauses

### 9.1 Where it runs

A background loop in smart-app-service modelled on `_grounding_rebuild_loop` (main.py:915), leader-elected (WEB_CONCURRENCY=2). **Off the officer's request path entirely** — this is the single biggest latency and cost win in the plan.

Trigger per `(tenant, app, modality, task_type)`: `pending >= CONSOLIDATE_MIN_PENDING` (5) **or** oldest pending age `> CONSOLIDATE_MAX_AGE` (6h).

### 9.2 Algorithm

1. `pending = corrections where consumed_by is null`, oldest first.
2. **Hard-partition by `reason_code`.** Never cluster across codes — "evidence insufficient" and "exclusion applies" are different lessons even when the words rhyme.
3. Within a partition, cluster by `cosine(embedding) >= CLUSTER_COSINE` (0.82) **and** `jaccard(case_facets) >= CLUSTER_JACCARD` (0.4).
4. For each cluster, try to **match an existing clause**: same `reason_code`, `cosine(text) >= MATCH_COSINE` (0.86), and scope-compatible.
   * **Match → reinforce.** Append to `provenance`, add the officer to `support_officers`, bump `last_confirmed_at`. **The text is not rewritten.** This is the anti-dilution invariant.
   * **No match → propose a candidate** (§9.3).
5. Set `consumed_by` on every folded correction.

### 9.3 Scope inference — intersection, then lift

A new clause's scope is the **intersection** of its cluster's `case_facets` (what the cases genuinely share), then filtered by a lift test against the app's base rate:

```
keep facet f  iff  1.0 / P(f | app's last 500 cases) >= LIFT_MIN     # 1.3
```

Because `P(f | cluster) = 1.0` by construction, this drops facets that are near-universal anyway. `country:us` at 95% base rate → lift 1.05 → **dropped**. `loss_type:theft` at 12% → lift 8.3 → **kept**. Cheap, and it stops every clause being pointlessly scoped to the deployment's constants.

### 9.4 Clause text authoring

The LLM's job is narrow: phrase what the officers already said. The prompt supplies the verbatim corrections and constrains hard:

* ≤ `CLAUSE_MAX_WORDS` (40), one imperative sentence.
* Every assertion must be supported by the quoted corrections — **no facts introduced**.
* No scope words in the text (scope lives in `scope_facets`; duplicating it in prose re-creates the drift we are removing).
* Output the text only.

The result lands as `status: "candidate"`, never active.

### 9.5 Promotion gate

`candidate → active` requires `support_count >= learning.promotion_min_officers` **distinct officers** (default 3), or an explicit builder approve on the Memory screen. This is what stops one prolific officer writing the app's policy. Backfill-inferred corrections (`reason_inferred: true`) count toward clustering but **not** toward this gate.

### 9.6 Contradiction, merge, supersede

* **Contradiction** — two active clauses with overlapping scope, the same `contested_fields`, and opposite polarity → set mutual `contradicts` edges, flip both to `status: "dissented"`, raise a builder flag. **Never auto-resolved.**
* **Merge** — same `reason_code`, `cosine >= MERGE_COSINE` (0.93), and one scope is a subset of the other → merge into the *more general* clause, recording `merged_from`. Reversible by construction.
* **Supersede** — builder-only, from the Memory screen. How a new SOP retires a clause cleanly instead of by slow erosion.
* **`other`-coded clusters** — never form a clause. Surfaced as *"14 corrections coded `other` — consider adding a reason code"*, so the taxonomy improves from use.

**Never: rewrite the whole set. Never: delete.**

---

## 10. Credit assignment (the blame edge)

1. **At `/run`:** `select_clauses()` returns `injected_clause_ids`; stamp it on the staging row. *(No write to `smartapp_clauses` on the hot path — `fired_count` is aggregated later from these stamps.)*
2. **At approve/reject:** the correction records both `injected_clause_ids` and `cited_clause_ids` (from the audit block).
3. **Blame rule:** on reject/override, blame `cited ∩ injected` — the clauses the model *said* it used.
   * If `cited` is empty, **blame nothing.** Punishing the whole set is precisely the bug being fixed.
   * Track `uncited_reject_rate` as a health metric: a high value means the model is not citing and the loop is degraded — a real alarm, not a silent decay.
4. **Aggregation:** the consolidation job recomputes `fired_count`, `blamed_count`, `precision` from the stamped rows. Zero extra hot-path writes.
5. **Low precision triggers refinement, not deletion.** When `precision < PRECISION_FLOOR` (0.7) with `fired_count >= 10`, the job clusters the blaming corrections and proposes a *narrowed* clause plus a sibling for the excluded cases — the `C-017 / C-034` pattern.

---

## 11. Neighbour re-ranking (rows, not rules)

Both neighbour paths currently retrieve by recency or by text similarity alone, neither of which knows what makes a case *comparable*.

* **`fetch_item_precedents` (item_records.py:265)** — stamp `case_facets` on the item-ledger row at decision time, then rank the `accepted` / `rejected` tiers by `jaccard(case_facets, current_facets)` with `disposition_at` as tiebreak. Keep the class balance and the `retrieval_excluded` curation gate exactly as they are. The `exact` (content-hash reuse) tier is untouched.
* **`_query_neighbor_samples` (tools_v2_dispatch.py:2965)** — add `facets` as an ARRAY scalar field on the shared `Historical_Refresh` collection and pre-filter the vector search with `ARRAY_CONTAINS_ANY(facets, [...])` in `_build_neighbor_filter`. ⚠ **Verify the Zilliz cluster's Milvus version supports ARRAY fields + `ARRAY_CONTAINS_ANY` before committing to this**; the fallback is a denormalised `loss_type`-style scalar per high-value family, which is uglier but works everywhere.
* **Entity boost** — cases sharing an identifier via `entity_links` get a rank bonus. The overlay already exists; this is a lookup, not new plumbing.

---

## 12. Migration & backfill

`corrections[]` was never rewritten, so nothing has to be reconstructed from a lossy summary.

1. **Promote** every embedded `corrections[]` entry into `smartapp_corrections` (`correction_id` minted, `consumed_by: null`).
2. **Recover the full reason** by joining `correlation_id` → DecisionRecord, replacing the 500-char truncation with the original text.
3. **Backfill facets** by re-reading the record from the SoR and running `derive_facets`. Unavailable record ⇒ `case_facets: []` (global scope) — **honest, not guessed**.
4. **Backfill reason codes** with a one-off classifier over `reason_text`, flagged `reason_inferred: true`. Inferred corrections cluster but do not count toward the promotion gate (§9.5).
5. **Freeze the legacy blob.** `smartapp_analysis_rubrics` stays readable; `rubric_to_prompt` still renders it when `learning.mode == "summary"`. Nothing is deleted.
6. **Cut over per app** via `learning.mode`: `summary` (today) → `both` (clauses generated, both blocks measured, only summary injected) → `clauses`. Default stays `summary` until §13 passes for that app.

---

## 13. Evaluation (gate — not optional)

Without this we cannot tell whether any of it helped.

**Harness:** hold out the last M=100 dispositioned cases per app. Exclude *their* corrections from the clause store. Replay each case in `summary` mode and `clauses` mode.

| Metric | Definition |
|---|---|
| `agreement@1` | recommendation matches the officer's actual disposition |
| `field_override_rate` | fraction of fields the officer changed |
| `prompt_words` | injected learned-memory words (expect ≤ today) |
| `clause_precision_p50/p10` | distribution across fired clauses |
| `uncited_reject_rate` | §10.3 health signal |

**Ship gate, per app:** `clauses` mode ships only when `agreement@1 >= summary` mode on the holdout **and** `p10(clause_precision) >= 0.6`. A per-app gate, not a global flag day.

---

## 14. Build sequence

Ordered so that **nothing changes officer-visible behaviour until Phase D**, while the data the later phases need is being collected from Phase A.

| Phase | Content | Behaviour change |
|---|---|---|
| **A. Evidence** | `smartapp_corrections` collection; reason-code + contested-field capture in the reject/override UI; `REASON_MAX_CHARS` → 2000; stamp `injected_clause_ids` (empty for now). Old summary path untouched. | None |
| **B. Signature** | `case_signature` schema + `validate_case_signature` + `case_signature.py` derivation; facets stamped on corrections and item-ledger rows; `__unknown` drift metric. | None |
| **C. Clauses** | `smartapp_clauses`; consolidation worker; backfill (§12). Clauses generated but **not injected**. | None |
| **D. Retrieval** | `select_clauses()`; `_prefetch_decision_rubric` dispatches on `learning.mode`; `cited_clauses` audit key; eval harness; per-app cutover. | **Yes — gated** |
| **E. Neighbours** | Facet-ranked precedents + Milvus facet filter + entity boost. | Yes |
| **F. Memory UI** | Clause list, provenance drill-down (clause → the 3 rejects that taught it), dissent adjudication, builder edit/retire/supersede, drift metrics. | Yes |
| **G. Memory impact & export** | Clause-level attribution on the memory-lift cohorts; "how memory helped" per app; HomePanel admin card; clauses + corrections in the S3 export. **§19 — deferred until A–D ship.** | Yes |

Phases A–C are pure instrumentation. If D's eval fails for an app, that app simply stays on `summary` with no rollback needed.

---

## 15. Risks

| Risk | Mitigation |
|---|---|
| **Ontology drift** silently mis-routes clauses | `__unknown` facet + WARNING + Memory-screen drift metric; publish validator on every republish. Same shape as the I-01 icon gate. |
| **LLM invents policy** in clause text | Text must be supported by quoted corrections; `status: candidate` until ≥3 distinct officers; builder-visible and editable; provenance clickable. Consistent with the standing no-LLM-authored-writes posture. |
| **Facet sparsity** — no clause has support in a narrow cell | Specificity backoff (§8 step 5) is structural, not a fallback. Cardinality validator warns at 5 000 cells. |
| **Officer weighting drifts into trust tiers** | Explicit non-goal (§16). Distinct-officer counting only; no per-officer weight, ever. |
| **Ranker becomes the new bottleneck** | Once the store is lossless, selection quality determines output quality — hence `clause_precision` in the eval, not just end-to-end agreement. |
| **Milvus ARRAY support** | Verify version before Phase E; documented fallback (§11). |
| **Consolidation worker doubles-runs** | Leader election, as `_grounding_rebuild_loop`; `consumed_by` makes folding idempotent per correction. |
| **Reason taxonomy is wrong at authoring time** | `other` is allowed, never forms clauses, and is surfaced as a prompt to extend the taxonomy (§9.6). |

---

## 16. Non-goals

* **No graph database.** §7. Revisit only for variable-length path queries over `entity_links`.
* **No auto-resolution of dissent.** Contradictions are stored and escalated to a human.
* **No per-officer trust or calibration weights.** Distinct-officer counts only.
* **No graduated autonomy.** Clause precision informs *retrieval ranking* and nothing else — it never widens what the system may do unattended.
* **No change to the SOP path.** The authored standard still comes live from the RAG `sop_source` at call time; `seed_rubric` stays deprecated. Clauses remain purely the *learned* layer on top.

---

## 17. Open questions

1. **Item-level vs record-level rollout order.** The record decision path (`_prefetch_decision_rubric`) has richer facets; the item paths (`tools_v2_dispatch`) have three call sites and more traffic. Proposal: record first — better signal, one call site.
2. **Cross-app clause sharing.** Two apps in the same department relearn the same lesson independently. Worth a `scope: "dept"` clause tier later; explicitly out of scope for v1.
3. **Clause count ceiling.** No cap proposed — retrieval is budgeted, storage is cheap. Revisit if a single app exceeds ~5 000 active clauses.
4. **Who adjudicates dissent** — builder (`decision-app-builder`) or a claims supervisor role? Affects the Memory-screen gating in Phase F.

---

## 18. Data volumes & prompt budget

The design stores strictly more than today and injects strictly less. Both halves matter, so both are quantified here.

### 18.1 What the batch does to text — three operations, one of which writes

| Operation | Trigger | LLM call | Touches clause text | Share (mature app) |
|---|---|---|---|---|
| **REINFORCE** | Correction matches an existing clause (§9.4) | **No** | **No** — `$push` provenance, `$addToSet` officer, bump `last_confirmed_at` | ~85–90% |
| **CREATE** | Cluster matches nothing | Yes — **once, ever** | Writes the sentence, then never again | ~10% |
| **MERGE** | Two clauses near-duplicate (cosine ≥ `MERGE_COSINE`) | Optional | Keeps the more general text; records `merged_from` | rare |

**It consolidates and merges. It does not summarize.**

> **The invariant:** a clause's text is written once, at birth, from ~3 corrections, and is never rewritten by later feedback. The 400th correction reinforcing C-017 changes two fields — `provenance` gains an id, `support_count` goes 3→4. The 19 words are byte-identical to the day it was born.

This is the whole anti-dilution argument. Today, correction #400 triggers an LLM rewrite of all 1000 words — the 400th lossy re-encode of an already-lossy encode (§1.3). That operation ceases to exist.

### 18.2 LLM cost

Assume 600 corrections/year for a busy app, yielding ~68 distinct lessons.

| | Today | With clauses |
|---|---|---|
| Calls/year | **600** (one per correction) | **~68** (one per *new lesson*) |
| Input per call | current 1000-word rubric + the new correction | ~3 corrections (~6 KB) |
| Output per call | rewrite of all 1000 words | **one ≤40-word sentence** |
| Runs where | **inside the officer's approve/reject request** (30 s timeout, `_resummarize`) | batch worker, off the hot path |

≈10× fewer calls, each far smaller, none blocking an officer.

### 18.3 Run-prompt budget

One line per fired clause — id, text, support:

```
- [C-034] For theft on commercial fleet policies, require a fleet incident
  report rather than a police report. (agreed by 4 officers)
```

≈45 words. For the CLM-6120 walkthrough: of ~72 active clauses, 5 survived the subset query and dedupe.

| | Today | With clauses |
|---|---|---|
| Words injected | **1000, every case, always** | **~225** (5 × 45) |
| Relevance | windshield / flood / collision guidance blurred into shared imperatives | every line's scope matched *this* case |
| What the cap means | a **compression** ceiling — knowledge is crushed to fit | a **selection** ceiling — rarely reached |

`clause_budget_words` still defaults to 1000, but it now bounds *how many relevant clauses fit*, not *how much knowledge may exist*. An app can hold 5 000 clauses and still inject 250 words.

### 18.4 What never reaches a run prompt

Officer reason text · officer names · `provenance` ids · past cases' facets · dissented clause bodies (one ⚠ line only) · any correction, ever.

Corrections feed **only** the consolidation job. Each is read ~3 times in its life, then serves as audit and drill-down.

### 18.5 Storage growth

600 corrections/year × ~2 KB ≈ **1.2 MB per app per year**. Ten apps over ten years is ~120 MB. Not a design constraint.

Clause count grows **sub-linearly** with corrections: early on most corrections CREATE, later almost all REINFORCE. 600 corrections → ~72 clauses, and year two adds fewer than year one. That saturation curve is itself the product story — the app stops needing new rules and starts confirming the ones it has (`support_count` climbing while clause count flattens). §19 renders it.

---

## 19. Memory impact & admin visibility — Phase G (deferred)

> **Deferred until A–D ship.** Recorded here so the instrumentation in A–D is designed to feed it, not retrofitted.

### 19.1 What already exists (scouted 2026-07-26)

| Piece | Reality |
|---|---|
| **Memory-lift cohorts** | **BUILT.** `main.py:3583` / `:9931` split disposed decisions into `with_memory` (`retrieval_count > 0`) vs `cold`, and publish `memory_lift = acceptance%(with) − acceptance%(cold)` (:3671, :9977), suppressed below `MIN_COHORT` (:3278). |
| **App Memory admin card** | **BUILT.** `HomePanel.js:898–907` — FeatureCard → `onOpenMemory`, `tourId="admin-memory-card"`. |
| **Memory screen** | **BUILT.** `MemoryScreen.js` — three tabs: `rubrics` ("What it learned"), `precedents`, `stats`; governed rubric edit; manual export. |
| **Memory export** | **BUILT.** `memory_export.py` — `EXPORT_COLLECTIONS` = `decision_records` / `item_decision_records` / `smartapp_analysis_rubrics` → gzipped JSONL to S3, watermarked. |

So Phase G is **extension, not greenfield**: the cohort machinery, the card and the export pipeline all exist and simply do not know clauses exist yet.

### 19.2 Build

1. **Export** — add to `EXPORT_COLLECTIONS`:
   ```python
   "smartapp_clauses":     "clause_id",
   "smartapp_corrections": "correction_id",
   ```
   Both carry `updated_at` / `at`, so the existing watermark logic applies unchanged. This makes the learned memory genuinely portable — the customer-onboarding commitment, now covering the layer that actually holds the knowledge.

2. **Clause-level attribution** — the cohort split is currently binary on `retrieval_count`. Add a third cohort keyed on `injected_clause_ids` (§10.1):
   * `with_clauses` — ≥1 clause fired
   * `with_memory` — precedents only, no clause fired
   * `cold` — neither

   Same suppression floor. This answers *"did the learned rules help, separately from the retrieved cases?"* — a question the current binary split cannot.

3. **Per-clause impact** — from the counters the consolidation job already aggregates (§10.4), with no new collection:

   | Metric | Meaning |
   |---|---|
   | `fired_count` | how often this rule was relevant |
   | `precision` | how often the officer agreed when it fired |
   | `support_count` / `dissent_count` | how many officers back it |
   | `last_confirmed_at` | is it still live knowledge or a fossil |

4. **MemoryScreen — a 4th tab, "Rules it follows"** — clause list sorted by `fired_count`, each row: text · scope facets as chips · N officers · precision · last confirmed. Tapping drills to the provenance corrections (*"the 3 rejects that taught this"*). Plus the Phase F affordances: dissent adjudication, edit, retire, supersede.

5. **HomePanel card subtitle** — the existing App Memory card gains the one-line impact sentence, e.g. *"47 rules learned from 612 officer corrections · +14% acceptance when they apply."* The lift number must obey the same cohort floor — **suppressed, never fudged**, per the Success-Rate bucket doctrine.

6. **Drift panel** — `family:__unknown` rates per facet family (§4.3), so ontology drift is a number an admin sees rather than a silent mis-route.

### 19.3 Doctrine constraints

* Every number is **suppressed when its cohort is under-powered** — an unearned lift number in a demo is worse than a blank.
* `auto_process` decisions stay out of the human-acceptance cohorts, exactly as the Success-Rate card already does.
* Nothing here grants autonomy. Clause precision informs *ranking* and *display* only (§16).

---

## 20. Implementation status & design deltas (2026-07-27)

**Built and tested locally — LOCAL ONLY, nothing committed, nothing deployed.**

| Phase | Status | Modules |
|---|---|---|
| A — evidence ledger | **DONE** | `corrections.py`, `analysis_rubrics.fold_decision_feedback`, `ApproveRequest.reason_code/contested_fields`, 2 fold sites in `main.py` |
| B — case signature | **DONE** | `case_signature.py`, `models.CaseSignature/FacetSpec/ReasonCodeSpec/LearningControls`, `publish_validators.validate_case_signature` (CS-01), schemas regenerated |
| C — clause store + consolidation | **DONE** | `clause_store.py`, `consolidation.py`, `_consolidation_loop` in the lifespan |
| D — retrieval + injection + blame | **DONE** | `runtime._prefetch_decision_clauses`, `cited_clauses` audit key, `injected_clause_ids`/`case_facets` on the staging row, `consolidation.aggregate_clause_performance` |
| D2 — item paths (image/doc/api/case) | **DONE** | `learned_memory.py`, 3 read sites in `tools_v2_dispatch.py`, item fold via `append_correction` |
| D3 — batch admin control | **DONE** | `/admin/consolidation` + `/pause` + `/run`, `LearningBatchScreen.js`, HomePanel card |
| E — neighbour re-ranking | not started | |
| F — Memory UI (clause list, provenance drill-down) | not started | |
| G — memory impact & export (§19) | partially — control surface only | |

Tests: **131 new**, all passing (`test_corrections_ledger`, `test_case_signature`, `test_clause_store`, `test_learned_memory_dispatch`). Full suite: 18 failures, **identical to the pre-existing baseline** (live Redis / discovery-service / MCP dependencies, plus a stale jsonschema-vs-pydantic expectation in `test_panel_data`) — verified by restoring the original schema and re-running.

### Deltas from the plan as written

Three things the plan did not anticipate, each found by a failing test:

**1. Clauses need a lexical fingerprint (`match_tokens`).** The plan matched new corrections against `clause.text`. But that text is an LLM *paraphrase* — an officer writing *"theft claim police report number was not on file"* scores ~0.11 Jaccard against *"Do not approve without the required evidence on file."* Every correction would have failed to match and authored a near-duplicate clause, fragmenting one lesson into many. Clauses now carry `match_tokens` — the content-word fingerprint of the officer language that taught them — and matching runs against that. The fingerprint widens on reinforce; **the text still never changes.** This would have hit embeddings too (embedding-of-paraphrase vs embedding-of-complaint), so it is not an artefact of choosing lexical similarity.

**2. `__unknown` must bypass token normalization.** `normalize_value` strips leading underscores, so the drift token collapsed to `family:unknown` — indistinguishable from a real column value of "unknown", i.e. silently absorbable. That is precisely what the reserved token exists to prevent. `unknown_token()` now builds it directly, and `clause_store.normalize_scope` refuses any scope containing one.

**3. Item subjects are only scopeable for `api`/`case`.** For `image`/`document` the subject (*"transformer nameplate photo"*) is emitted by the **model after looking** — it does not exist when the prompt is assembled. Scoping a clause to `item_subject:x` there would mint clauses that can never satisfy the subset test: silent dead knowledge, indistinguishable from "this app hasn't learned yet". `SUBJECT_SCOPED_MODALITIES` gates it; for image/doc the subject is recorded as correction metadata but never as a scope.

### Additions beyond the plan

* **`learned_memory.py`** — one dispatcher for the mode switch, shared by all four prompt sites. Without it an app could half-migrate: record decisions learning from clauses while photo findings still learn from a diluting blob.
* **Traceability tag preserved.** Item findings stamp `rubric_version`; under clauses that becomes `clauses/C-003,C-034`. Losing the tag would have silently broken an audit guarantee the blob path already made.
* **`consolidation` control is NOT in `smartapp_control`.** The kill switches halt business operations and surface in the halt banner; pausing the learning batch stops nothing an officer does. Separate collection (`smartapp_learning_control`), separate endpoints, fail-open on read.
* **`aggregate_clause_performance` returns the uncited-reject rate** and warns above 50% — a degraded citation habit makes precision unmeasurable, so it is an alarm rather than silent decay.

### Compliance audit (2026-07-27) — four unclosed loops found and fixed

A pass over "is every piece actually *wired*, not just written" found four places where code existed but nothing called it. Each would have failed silently — the system would look like it was learning and simply never improve.

**1. Item corrections had no `reason_code`.** The endpoint parsed `reason` and `subject` but never forwarded a code, so every image/document/api correction landed uncoded — and consolidation refuses to author a clause from an uncoded cluster (§9.2). Item feedback would have accumulated forever as evidence that could never become a rule. The endpoint now accepts `reason_code` and **validates it against the app's declared taxonomy** (an invented code partitions consolidation into a cluster of one that can never reach the promotion gate).

**2. `record_dissent` had zero call sites.** No clause could ever reach `dissented`, so the disagreement-suppression path in retrieval (§8.2) was dead code — and the summarizer's silent-winner behaviour would have survived in a new form. Dissent now has a defined source the plan never specified: the model reports `relation: "overruled"` on a clause that fired, the officer still rejects/overrides, and that pair is dissent. Stored as `overruled_clause_ids` on the correction, aggregated per **distinct officer** in the batch.

**3. `apply_performance` / `aggregate_clause_performance` were never called.** `precision` stayed `None` forever, so the ranker ran permanently on its prior and the §10.5 refinement trigger could never fire. Both are now invoked from the consolidation pass.

**4. The batch only ever swept PROD.** The collection accessors route off `env_context`'s contextvar, whose default is `"prod"`. A background loop has no request to set it, so **test-env apps would pile up corrections that were never folded** — their clause memory permanently empty while looking merely "not learned yet". `run_consolidation_pass` now sweeps every provisioned environment (`test` only when `test_environment_available`), restores the contextvar in a `finally`, and the admin status reports per-environment rows plus a `queue_partial` flag when one environment fails to answer.

### Dev verification (2026-07-27) — run against real Mongo + a real LLM

Booted `smart-app-service` locally against the shared dev Atlas cluster. Not a stub run: the medium model authored the clause.

* 3 seeded corrections from **distinct officers** → **one** clause, 15 words: *"Do not accept a theft claim over $25,000 without a police report number on file."*
* Scope came out as the facet **intersection** `[amount_band:25000_100000, loss_type:theft]` — §9.3 working as designed.
* `status: active` on the 3-officer gate; `provenance` linked all three; `match_tokens` fingerprint captured.
* A **4th** correction from a different officer → `created 0, reinforced 1` — text **byte-identical**, `version` still 1, support 3→4, still exactly one clause. **The write-once invariant holds against a real model.**
* Pause / resume / run-now verified live; pause correctly refused the run in **both** environments.

**One real bug found, which is what a dev run is for.** `_run_pass_one_env` summed a hardcoded tuple into a `totals` dict missing `performance_updated`, so a bucket that consolidated *successfully* was reported as an error and the rest of that environment's buckets were skipped. The multi-env merge above it had the same fixed-key shape and would have silently dropped new counters. Both now roll up dynamically (386c12a9).

**Volume reality check.** The entire dev environment holds **5 legacy corrections across 3 buckets** (`acme-power-inspection-triage` image 2 / document 1, `acme-power-complaint-auto-routing` record 2). The eval gate needs 20 disputed cases minimum, so **no app can be cut over on dev data**. Prod volume is unmeasured — worth checking before investing further in the cutover path.

### Clustering-gate redesign (2026-07-27) — overlap coefficient, not Jaccard

Reviewing the 12,960-cell warning on `acme-power-complaint-auto-routing` exposed that the warning's own rationale ("3 officers must hit the same cell") was wrong — and that the real mechanism had two genuine bugs:

1. **Jaccard-over-union punished richer signatures.** Two corrections about the SAME lesson sharing 3 core facets but differing on 3 incidental ones (channel, status) scored 3/9 = 0.33 < 0.4 and never clustered — declaring more context made an app *slower* to learn. Replaced with the **overlap coefficient** (`|A∩B| / min(|A|,|B|)`, threshold 0.5), which only asks "of the facets you could share, how many do you?" and is immune to each side's extras.
2. **Facetless history could never corroborate live evidence.** `jaccard([], [x]) = 0.0`, so every backfilled correction was silently unable to combine with post-migration corrections toward the promotion gate. Either side empty now passes the facet gate — absence of evidence is not disagreement; reason_code + text similarity still gate.

Cardinality remains a publish guardrail but reframed: high cell counts mean the builder is declaring *context* rather than *decision factors* (drift surface, signature bloat) — not that learning stalls. Skill §5 rewritten around the question "would an officer's correction ever turn on this family?". The routing app's signature was trimmed accordingly (dropped `channel` + `status`, 12,960 → 360 cells, version 2 so history stays attributed to v1).

### Full-fidelity builder test (2026-07-27) — PASS on round 3

Real sandbox build (fresh pod, rebuilt image, scripted BA turns, real publish): the builder **authored a valid `case_signature` unprompted** for a complaint-triage app —

* families `category · priority · division · sla_window` — all decision-relevant; notably it *followed* skill §5 and skipped `channel`/`status` this round
* reason codes include a domain-specific **`theft_misrouted`**, derived from the BA's own stated concern — the taxonomy is tailored, not templated
* validated from the **published** app through the real gates: CS-01 clean, 8,370 cells (above the 5,000 soft warning, under the cap — acceptable post-overlap-coefficient)

It took three rounds, and each failure taught something durable: **round 1** — build succeeded but no signature: a SKILL.md directive alone does not survive the real agent loop; the step must live in the AGENTS.md phase checklist the agent executes (plus the `case_signature_missing` publish warning as backstop). **Round 2** — `/build` reattached to round 1's still-live pod and tested nothing: rebuilding the image is not enough, the old pod must die first. Harness: `tests/integration/acme_power_builder_e2e.py`.

### Phase-2 plan: Rules vs Judgements (2026-07-27)

A logical (non-technical) system review plus owner doctrine produced
[sop-rules-officer-judgement-plan.md](sop-rules-officer-judgement-plan.md): **SOP is king** (learned content
never overrides it, conflicts are surfaced with a two-tap resolution that can
also flag a stale SOP); the learned layer is renamed **judgement** — officer
experience filling the unknowns SOP cannot enumerate; and **a single officer's
judgement is used immediately, labeled as such** — corroboration upgrades its
standing instead of gatekeeping its existence (supersedes this plan's §8
candidate-exclusion). Plus fixes for the review's findings: junk-reason and
pattern-not-person authoring gates, comparability-ranked corrections window,
gate-reachability visibility, officer-taught quarantine, reason-code aliasing.

### Still not wired (honest gaps)

* **No app sets `case_signature` yet.** Every app is on `mode='summary'`, so the clause path is inert against real data. Authoring one for `acme-power` is the next concrete step — but see the volume note above: without feedback history it will have nothing to learn from.
* **Not deployed to prod.** Held deliberately: deploying adds `cited_clauses` to every agent's audit-block prompt and starts the batch spending tokens, for a feature no app can yet use. Ships when an app is actually switchable.
* **§11 (neighbour re-ranking)** and **§19.2 items 1–6 (export, cohorts, MemoryScreen clause tab, HomePanel impact line, drift panel)** are untouched.
* `clause_inventory`, `correction_stats`, `corrections_by_ids`, `is_stale` are implemented and tested but have no callers — they are the Phase F/G read surfaces, expected to be unwired until those phases.
