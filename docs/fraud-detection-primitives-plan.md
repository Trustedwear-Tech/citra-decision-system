<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: Apache-2.0

  Licensed under the Apache License, Version 2.0 (the "License"); you may not
  use this file except in compliance with the License. You may obtain a copy of
  the License at http://www.apache.org/licenses/LICENSE-2.0
-->

# Fraud Detection as Platform Primitives — Design Plan

**Status:** P1 IMPLEMENTED (local, 2026-07-02) — `smart-app-service/fraud_checks.py`
(normalizers, validators, cross-check, arithmetic, fingerprint+metadata),
`consistency_check` tools_v2 kind (models + dispatch + runtime-reference mirror),
`query` (case context) now REQUIRED on `image_analyze`/`doc_extract`,
`artifact_flags` (SHA-256 dedup + EXIF/PDF anomalies) attached to every
`ItemFinding`, builder proposal rule in citra-agent-spec / citra-tool-catalogue /
citra-safety-rules skills. P2/P3 remain planned. Authored 2026-07-02.
**Owner surface:** smart-app-service (tools_v2 + builder skills) + small shared primitives.
**Doctrine:** platform primitives, not vertical features · overlay correlated to SoR, never a
second source of truth (see `app-owned-data-plane.md`) · plan-then-apply governance — a fraud
flag is evidence on a recommendation, never an auto-reject · match size to problem · fail loud.

---

## 1. Why this is a primitive, not an insurance feature

Every "application-shaped" Decision App (insurance claim, loan origination, expense
reimbursement, machine inspection) has three artifact classes:

- **R** — the structured Record (form/application data, from the SoR via dept-MCP)
- **D** — Documents (PDFs: IDs, invoices, statements, reports)
- **I** — Images (photos of asset / damage / receipts)

Nearly all fraud is an inconsistency **within or between** these classes. The taxonomy is
identical across verticals; only the configured identifiers, rubric criteria, and thresholds
change. The litmus test: the moment platform code says `if vertical == "insurance"`, it is in
the wrong layer — that belongs in the app template's configuration.

## 2. Fraud taxonomy (cross-artifact consistency matrix)

| # | Class | Fraud types | Insurance example | Finance/lending example |
|---|-------|-------------|-------------------|-------------------------|
| R↔R | Cross-record patterns | Duplicate applications; identity reuse; velocity; ring patterns (shared phone/bank/address/agent/repair-shop); synthetic identity; resubmission-after-rejection; split cases under approval thresholds | Same phone on 4 rejected claims; claim 3 days after policy start | Mule accounts; circular payments; same collateral pledged twice; invoice financed at 2 lenders |
| R↔D | Record vs document | Name/DOB/amount/date on form ≠ extracted doc values; doc belongs to another person/asset; inflated invoice vs claim; lookalike doc passed as mandatory doc | Repair invoice total ≠ claimed amount; RC book name ≠ claimant | Payslip income ≠ declared income; statement account ≠ applicant |
| D (intra) | Document forgery | Digital tampering (fonts, copy-move, overwrite); template fraud; metadata anomalies (PDF modified after signature date, suspicious producer); failed format/checksum validation (PAN, IFSC, VIN, policy no.); arithmetic errors | Photoshopped garage estimate | Fabricated bank statement (rows don't reconcile) |
| I (intra) | Image fraud | Reused/near-duplicate photos across cases; internet/stock images; EXIF anomalies (capture time before incident, GPS ≠ claimed location, camera model flips mid-set); AI-generated images; splicing | Same crash photo on two claims | Same shop-front photo for two "different" vendors |
| I↔R/D | Image vs claim | Asset in photo ≠ registered asset (plate/VIN/serial); damage inconsistent with narrative; severity inflated; visible location/weather/time ≠ claim | Photo shows Swift, record says Honda City | Collateral photo ≠ valuation address |
| Behavioral | Process gaming | Timing patterns; round-number amounts; officer/agent clustering on high-payout approvals | Adjuster approves 3× dept average | Officer approves own referrals |

Machine inspection maps onto the same matrix for free (reused "pass" photos, pencil-whipped
inspections, serial-in-photo ≠ asset record) — confirming these are horizontal primitives.

## 3. Reuse map (~70% of the stack already exists)

| Capability | Existing system — reuse as-is |
|---|---|
| Image/doc analysis | `image_analyze` / `doc_extract` (tools_v2; Qwen3-VL vision + large-tier reasoner) |
| Learned "how to judge" criteria | Rubric store `smartapp_analysis_rubrics` per (tenant, app, modality, task_type) |
| Similar past cases + outcomes | Grounding vector store (Milvus) + DecisionRecords + Stage-4/5 outcome loop |
| Within-source cross-record queries | dept-MCP read path (deterministic filtered reads; NL→SQL not needed per lookup) |
| Tabular anomaly checks | duckdb-query-service |
| Batch screening | Workflow engine + triggers (recommend-only) |
| Human disposition | Plan-then-apply officer queue + per-item review gate (`ItemFinding`) |
| Evidence trail | Hash-chained `smartapp_run_audit` + `auto_process_decisions` |
| Identifier-column detection | `classify_column` semantic classifier — auto-suggests entity-link keys |

## 4. New primitives (deliberately small)

### 4.1 Artifact fingerprint service — CPU-only, ~zero cost
On artifact ingestion: SHA-256 (exact dup), pHash/dHash (near-dup image), EXIF extraction
(time/GPS/camera), PDF metadata extraction (producer, mod-date vs signature date). Stored on
the artifact record in Mongo. Results cached forever by content hash.

### 4.2 Image embedding index
One small CLIP/SigLIP-class embedding per image → one Milvus collection for the deployment
(env-prefixed `test_`/prod like other stores). Enables "this photo ≈ photo from case #8213".
Embeddings cost pennies; runs on the existing embedding path.

### 4.3 Cross-check engine — deterministic, zero LLM
Normalize + compare Record fields ↔ `doc_extract`ed fields ↔ vision-extracted identifiers →
structured `mismatches[]` with severity. Pure Python over JSON the pipeline already produces.
Includes format/checksum validators (PAN, IFSC, VIN, policy-number patterns) and arithmetic
checks (invoice line items vs totals).

### 4.4 Entity-link index — the overlay (NOT a graph DB)
See §5. A Mongo collection of normalized identifier → record/case pointers. Ring detection is
a 1–2 hop query; Neo4j is overkill until proven otherwise (match size to problem).

### 4.5 Fraud-signal aggregator tool
Composes 4.1–4.4 + rubric + grounding into one structured signal report
(`signals[], severity, evidence_refs`) for the agent → recommendation → officer queue.

## 4b. Globalization — locale packs (2026-07-02; USA is the primary market)

The engine (cross-check, fingerprints, image index, entity overlay, rubrics,
synthesis, calibration) is geography-free. Only the VALIDATOR/NORMALIZER layer
is locale-specific, packaged as **locale packs** selected per deployment via
``FRAUD_LOCALE`` (default **us**; Indian demo tenants set ``in``):

| | ``us`` pack | ``in`` pack | common |
|---|---|---|---|
| ID validators | SSN (SSA rules), EIN (IRS prefixes), ABA routing (3-7-1 checksum), ZIP/ZIP+4 | PAN, IFSC, GSTIN, Aadhaar (Verhoeff) | VIN (ISO 3779 / NA check digit), email |
| Phone rule | NANP (area+exchange 2-9) | 10-digit starting 6-9 |
| Slash dates | MM/DD first (03/04 = Mar 4) | DD/MM first (03/04 = 3 Apr) |
| Amounts | $ / USD / € / £ / ₹ all stripped (grouping-agnostic) | same |

Cross-locale ID types stay validatable when a field NAME names them explicitly
(name-driven ⇒ no false positives); phone rules are locale-exclusive. Entity
linking adds ssn/ein/routing as linkable types (zip is deliberately NOT — too
coarse to be an identifier). Adding a region (uk/eu/sg…) = adding a pack dict,
not a refactor.

## 5. Entity-link index design (single-tenant, MCP-fed, external entities)

**Deployment model:** SINGLE-TENANT — one dedicated deployment per org. One `entity_links`
collection, env-routed (`test_entity_links` / `entity_links`). `tenant_id` stays stamped in
rows per the standing "multi-tenant code, single-tenant deploy" posture.

### 5.1 Two lookup problems — only one needs an index

**Tier A — within-source lookups: NO index; ask the SoR through the MCP.**
"Other claims with this phone", velocity counts, duplicate policy numbers — the source system
already holds this. The fraud tool issues a deterministic parameterized read through the
existing dept-MCP path (filtered read, not NL→SQL — no LLM cost per lookup). We never copy
what the SoR can answer itself.

**Tier B — the overlay index: only what the SoR CANNOT answer.** Three cases:

1. **Artifact-extracted entities.** A bank account on an invoice, a garage name on a
   letterhead, an IMEI visible in a photo — surfaced by `doc_extract`/vision at analysis time;
   these exist as columns NOWHERE in the SoR. If not captured at screening they are lost.
   The overlay is the memory of what analysis saw *inside* the artifacts.
2. **Cross-source links.** Claimant phone (claims system) = payee phone (vendor master) =
   applicant phone (loan system). No single SQL query spans source systems; the overlay is
   the join surface.
3. **External parties.** Garages, repair shops, third-party beneficiary accounts, agents —
   not customers, not SoR rows. An entity is just `(entity_type, normalized_value)` with a
   **role tag** (claimant / payee / repairer / issuer / witness). External entities are
   first-class; rings almost always pivot on them.

### 5.2 Bounded by construction ("entities are vast" containment)

- **Closed vocabulary:** only builder-selected identifier types per app (proposed at build
  time, auto-suggested via `classify_column`): phone, PAN, bank account+IFSC, VIN/serial,
  email, vendor/garage name, address-hash. Never "all entities".
- **Pointers, not payload:** a row is
  `{tenant_id, entity_type, normalized_value, role, source_ref{source_id,dataset_id,record_id},
  case_ref, extracted_from: record|doc|image, first_seen, last_seen}` — ~200 bytes.
  The SoR record remains the single source of truth; we store the *link* only.
- **Write-through population:** rows upserted when a case is screened (the cross-check engine
  already extracted+normalized the identifiers). No crawler, no sync pipeline, no cron.
  Optional one-time IT-triggered backfill workflow per app for history (manual trigger,
  consistent with the no-background-jobs stance).
- **v1 matching = exact-on-normalized only** for high-precision types (phone→digits,
  account, PAN, VIN, IFSC, email). Names/addresses stored as normalized hashes, used only as
  corroborating signals. No fuzzy matching in v1 — no false-positive explosion; every hit is
  explainable to the officer with evidence refs.
- **Retention** aligned to the app's case retention; index rows carry case_ref so purges cascade.

### 5.3 Signals it produces (and what it does NOT cover)

| Fraud type | Entity index? | Signal shape |
|---|---|---|
| Rings (shared phone/account/agent/garage) | ✅ core | `shared_count ≥ N` distinct cases, role-tagged |
| Synthetic identity | ✅ | cardinality anomaly: 1 phone → 5 names; 1 account → 4 PANs |
| Double-dip / duplicate financing | ✅ | same VIN/invoice-no/account in ≥2 cases (cross-source) |
| External-party concentration | ✅ | same garage on N high-value claims; same beneficiary across vendors |
| Resubmission after rejection | ✅ + DecisionRecords join | identifiers match prior case with outcome=rejected |
| Velocity within one system | Tier A (MCP read, no index) | count over time window |
| Doc forgery / metadata / arithmetic | ❌ → §4.3 validators | — |
| Reused / near-dup images | ❌ → §4.1/4.2 image index | — |
| Image ↔ record mismatch | ❌ → §7 context contract | — |
| Staged damage / severity inflation | ❌ → rubric learning | — |

## 6. Token/GPU economics — tiered funnel (the sustainable design)

Spend tokens only where cheap tiers raised suspicion.

| Tier | Checks | Cost | Coverage |
|---|---|---|---|
| **T0 deterministic** | exact-hash dup, EXIF/GPS/date, PDF metadata, format/checksum validators, arithmetic, field cross-check, entity-link lookups, velocity | ~free (CPU) | 100% of cases |
| **T1 embeddings** | image near-dup search, text similarity to past cases | pennies | 100% |
| **T2 vision LLM** | Qwen3-VL per image, once per content-hash, cached forever — already paid as the app's normal analysis; fraud context rides the SAME call | existing cost | 100% |
| **T3 reasoning synthesis** | GLM/DeepSeek cross-examines ONLY flagged cases (T0–T2 signals over threshold) + ~5% random audit sample | the only new LLM cost, on ~5–15% of cases | gated |

Cache all analysis by `(artifact_hash, context_hash)` — resubmissions and re-runs are free.
T0/T1 catch the highest-yield fraud (duplicate photos, EXIF lies, field mismatches, rings)
with zero LLM spend.

## 7. Agent context contract (correctness linchpin)

Contextless analysis cannot catch I↔R fraud ("describe this image" vs "verify this image").

- **Tool schema:** fraud-verify tools take a REQUIRED structured `context` argument —
  `{claimed_asset, claimed_incident, claimed_date, claimed_location, claimed_amount,
  expected_identifiers[]}`. Schema-required so the agent cannot call them contextless
  (fail loud, not degrade silently).
- **AgentSpec prompt section** (builder-authored from the template): before any artifact
  analysis, assemble case context from the record and pass it. Vision prompts become targeted
  verification: "verify: does this image show plate KA-01-XX-1234; is damage consistent with a
  rear-end collision on 2026-06-14".
- Tools inject case context + the active rubric block (existing) + T0/T1 findings, and return
  structured `mismatches[]` with severity + evidence, flowing into the recommendation timeline
  and the per-item review gate the officer already uses.
- **Learning loop:** officer overrides feed the rubric (existing correction path); confirmed
  outcomes feed grounding; a periodic calibration job scores which signals actually predicted
  confirmed fraud and prunes dead criteria.

## 7b. Rubric learning for fraud — three layers, ONE mechanism

No new learning system is built. The existing rubric store
(`smartapp_analysis_rubrics`, keyed by tenant/app/modality/task_type) IS the
learning mechanism; fraud adds one new bucket and one statistical loop:

| Layer | Bucket / store | Learns | Trains on | Injected into |
|---|---|---|---|---|
| **L1 Artifact rubric** (exists) | `(modality=image\|document, task_type)` | how to judge ONE image/doc (incl. artifact-level fraud cues: "rust at dent edge = pre-existing") | officer per-item reject reasons (existing path) | T2 vision/doc prompt (existing) |
| **L2 Fraud case rubric** (P2) | `(modality="case", task_type="fraud-screening")` — same collection, same summarizer, same versioning | how to WEIGH signals into a case-level fraud assessment ("no-EXIF on WhatsApp images is normal alone"; "garage X estimates run ~20% high") | officer confirming/dismissing a FRAUD FLAG with a reason (new correction path, mirrors the existing one) | T3 synthesis prompt |
| **L3 Calibration** (P3) | statistical job over DecisionRecords outcome read-back | which signals/criteria actually PREDICTED confirmed fraud | ground-truth outcomes (confirmed fraud vs paid clean) | tunes the T3 gate threshold; prunes dead criteria from L1/L2 |

L1 judges the artifact, L2 judges the case, L3 judges the judges. Every rubric
version, signal, flag, and officer click lands in the hash-chained audit ledger
(`rubric_version` already stamps each ItemFinding), so an investigator can
replay exactly why a case was flagged and under which learned criteria.

## 8. Builder proposal rule

**Trigger heuristic** (in the builder discovery skill, feeding `design_dossier`): propose
fraud screening when the app has ALL THREE — (a) a disposition decision
(approve/reject/pay/settle), (b) monetary or asset-value fields, (c) identity + supporting
artifacts (doc/image attachments). Keyword reinforcement: claim, loan, application,
disbursement, settlement, reimbursement, KYC.

- **Match** → builder PROPOSES (never silently adds) a "Fraud & Consistency Screening"
  section: which T0–T3 signals, which identifier types to entity-link (auto-suggested), seed
  rubric criteria for the vertical, T3 escalation threshold, HITL disposition. The BA
  accepts/edits — configuration, not code.
- **No match** (routing, FAQ, status tracker, simple triage) → not proposed. Simple apps stay
  simple.
- **Governance unchanged:** screening is read-only analysis → staged recommendation. It never
  auto-rejects; the officer click remains the only commit (universal-approval invariant). A
  fraud flag is evidence attached to the recommendation.

## 9. Phasing

ALL THREE PHASES APPROVED (2026-07-02) — "even a few caught frauds pay for the
infra". Decisions taken: `entity_links.normalized_value` stored PLAINTEXT
(dedicated single-tenant; investigator UX wins). P2 build order:

| Step | Scope | New cost | Status |
|---|---|---|---|
| **P1** | Cross-check engine; exact-hash dedup; EXIF/PDF metadata; format validators; builder proposal rule; agent context contract | ~zero | ✅ SHIPPED (local) |
| **P2a** | Entity-link overlay (plaintext, `smartapp_entity_links`, write-through from `consistency_check` w/ required `record_id`; ring + double-dip + synthetic-identity signals) + velocity via dataset-bound mcp `filters` reads (skill guidance) | ~zero | ✅ SHIPPED (local) |
| **P2b** | dHash near-dup (LSH-banded, on fingerprint docs) + VL image index (nvidia/llama-nemotron-embed-vl-1b-v2 via the ALREADY-PAID OpenRouter /embeddings — decided 2026-07-02 after live benchmarking: nemotron separates copies from unrelated cleanly (crop 0.98 / unrelated 0.58-0.68 / similar-scene 0.845, thresholds 0.92/0.80 unchanged), google/gemini-embedding-2 does NOT separate for image-to-image near-dup (unrelated 0.86-0.97 — retrieval-aligned, not perceptual), and jina-clip-v2 was dropped to avoid a second vendor. Input = data-URI string; 2048-dim stored on fingerprint / 512-dim indexed in Milvus `smartapp_fraud_image_index`, embed-once-per-hash. Air-gapped deployments point IMAGE_EMBED_URL at self-hosted open-weights — per-deployment indexes never share a vector space) | embeddings (pennies) | ✅ SHIPPED (local; Milvus path untested-live) |
| **P2c** | T3 gated synthesis (`fraud_synthesis` tool — server-side severity gate, deterministic points, ~5% audit sample, screenings persisted to `smartapp_fraud_screenings`) + L2 fraud case rubric (`modality="case"`/`task_type="fraud-screening"` bucket; officer flag feedback via the existing item-feedback endpoint) | bounded T3; gate provisional until L3 | ✅ SHIPPED (local) |
| **P3** | L3 calibration: `POST /apps/{slug}/fraud-calibration` (IT/owner-triggered, no cron) — per-signal officer-rejection hit-rate from screenings × DecisionRecords; sampled audits via `sample_rate`. AI-image detector + external registries remain deferred (customer-funded). | ~zero (report only) | ✅ calibration + sampling SHIPPED (local); detector/registries deferred |

**Review hardening (2026-07-02, second review pass — 18 verified findings fixed):**
requirements.txt gained Pillow+pypdf (deploy-breaker); `artifact_flags` now reaches
the officer's per-item payload structurally AND is serialized FIRST so the runtime's
tool-result slice never truncates it; `consistency_check`/`fraud_synthesis` are
chat-blocked (no-writes-from-chat); fraud stores key on the APP tenant via one
shared `_screening_tenant` helper (stable across officer/trigger runs; tenant-None
skips cross-case checks VISIBLY, never a null namespace); Milvus similar-image
search filters by tenant (cross-tenant evidence leak); prior-refs dedup is
record-based (no self-duplicate on re-runs); identical-but-unparseable values are
never a mismatch; the truncation auto-retry respects the context window; agent-node
large cap is 32K (history-safe); the proxy floor is reasoning-model-gated +
env-tunable; emails keep hyphens in entity linking; severity walk is iterative with
a 5K-node budget; signal weights are env-tunable (`FRAUD_SIGNAL_WEIGHTS`);
index-ensure is per routed collection; calibration is index-ordered + projected;
CPU parsing is off-loop; entity lookups run concurrently with projections.
KNOWN ACCEPTED GAPS: the condensed runtime-reference copies of
tools_v2_dispatch/runtime remain long-pre-diverged (builder authority = SKILL.md +
models reference, both synced); agent-relayed `signals` fidelity is mitigated (not
eliminated) by structural officer evidence — server-side accumulation is a
follow-up.

**External registry architecture (decided 2026-07-02):** registries the corporate
already subscribes to (ClaimSearch / NICB / bureau / IIB) integrate **via the
dept-MCP**, never as direct calls from smart-app-service — IT declares each as a
`rest_api` LIVE-PASSTHROUGH dataset in the MCP's `SOURCES_FILE` (`sources.json` —
the central `dept_sources` registry is RETIRED; sources flow SOURCES_FILE → the
discovery registry on boot) (creds stay MCP-side; per-call
`X-User-JWT` gives FCRA-grade attribution in `dept_query_audit`); the agent queries
the resulting `mcp` read tool and nests raw matches under
`external_registry_matches` in the `fraud_synthesis` signals (scored 4 pts/match —
scorer is registry-ready today). Direct-call exception: pure INFRASTRUCTURE
services with Citra-owned creds and no corporate data semantics (e.g. the Jina
embedding API).

## 10. Open questions

1. Backfill depth: how much history to seed the entity index per app (IT decides at proposal
   time; default = none, organic growth only)?
2. PII posture for `entity_links.normalized_value`: plaintext (investigator UX, matches
   single-tenant posture) vs salted hash for extra-sensitive types — per-type choice at
   proposal time.
3. AI-generated-image detection (P3): model choice + cost; defer until a customer asks.
4. Officer-facing "why flagged" view: reuse the per-item review gate rendering or a dedicated
   evidence panel in the app template.

---

## Addendum P4 — Ontology-driven artifact roles (context for the reuse signal)

**Problem.** The reuse detectors (SHA-256 / dHash / embeddings) answer only "seen
before?". Whether that is fraud depends on **what the artifact is**, which the
detector cannot know:

| Scenario | Artifact | "Seen before" means | Verdict |
|---|---|---|---|
| Student re-applies for a job after 6 months | headshot = **identity** | same person | ✅ legitimate — reuse *expected* |
| Insurance / inspection claim | accident-or-defect photo = **evidence** | recycled proof | 🚩 double-dip fraud |

Same bit, opposite meaning. Pre-P4 the reuse hit was counted uniformly
(`_dup_hits`), so an identity headshot would be flagged like recycled evidence.

**Fix (shipped, prototype).** The **source ontology declares the role**, it rides
the catalogue, and the builder auto-wires it onto the screening tool:

- **`sources.json`** — per artifact column: `artifact_role` (`identity` |
  `evidence` | `supporting`) and optional `reuse_policy` (`expected` |
  `suspicious` | `ignore`). Per dataset: a `fraud_screening` block with a
  tristate `applies` (`true` = screen · `false` = hard opt-out · omitted =
  screen iff a column declares a role) plus optional advisory hints
  `value_fields` / `identity_fields`. (There is deliberately **no** `triggers`
  key — the agent only ever *recommends*; the officer decides, so a screen has
  no approve/reject verbs to trigger.)
- **Catalogue carry** — `data-discovery` crawler copies both onto
  `CatalogueColumn.artifact_role/reuse_policy` and `CatalogueEntry.fraud_screening`.
  (The MCP describe layer overlays `artifact_role`/`reuse_policy` back onto
  live-introspected relational columns, so declared roles survive for reachable
  SQL sources — not just file/RFC backends.)
- **Builder auto-wiring** — `fraud_roles.autowire_fraud_roles(agent_spec, catalogue,
  data_sources)` (data_sources come from the *AppSpec*), called from
  `data_binding_validator.validate_data_bindings`, is **the whole trigger**: for a
  dataset where screening is active it **CREATES** a `consistency_check` screen if
  none exists (key_field = the dataset primary key), sets `url_columns` to the
  evidence/identity artifacts and stamps their roles. On an **explicit** opt-out
  (`applies=false`) it CLEARS the screen; on mere **silence** (no ontology at all)
  it leaves a hand-authored screen untouched. Where the ontology speaks it is
  authority (its role overrides a stale one on the same column), but a column a
  human explicitly added is preserved. The **LLM never decides fraud scope**.
- **Near-guaranteed invocation** — `runtime._render_fraud_screen_block` injects a
  stanza naming the auto-wired screen tool(s) so the screen is RUN when a decision
  concerns a screened record. This is prompt-level guidance, not a hard server-side
  guarantee (a model that skips the call still yields an un-screened recommendation).
- **Runtime, role-aware** — `tools_v2_dispatch` runs each duplicate through
  `fraud_roles.apply_reuse_signal`: an `identity` match → *verification* (its raw
  fingerprint markers, across SHA / dHash / CLIP tiers, are stripped so the T3 gate
  does not score them); `evidence` reuse → fraud signal, every finding carrying the
  WHY. This module decides only WHETHER a hit counts; **scoring is the T3 gate's
  job** (`fraud_synthesis.severity_points`, count-based + `FRAUD_SIGNAL_WEIGHTS`
  env-tunable) — so no per-artifact points number is published here (it could never
  match the gate). Only suspicious reuse counts toward `n_issues`; identity matches
  are surfaced separately (`identity_matches`).

**Doctrine preserved.** Deterministic + zero-LLM; explainable-only (each verdict
carries the reason); evidence on a recommendation, never an auto-reject. **Safe
default:** an un-annotated column is treated as `evidence`/`suspicious` — so the
ontology can only ever *relax* screening (mark a column identity/supporting),
never silently weaken it. Existing apps keep flagging every duplicate until their
source declares roles and they re-publish.

**Known limitation — role-awareness is confined to `consistency_check`.** The
identity exemption lives in `apply_reuse_signal`, which runs only on the
record-bound `consistency_check` screen. `image_analyze` and `doc_extract` still
emit raw `artifact_flags` (`duplicate` / `phash_near_dups` / `image_index`) with
**no** `artifact_role`, and `fraud_synthesis.severity_points` scores those raw
keys wherever they appear in the signals blob. So if an agent runs
`image_analyze`/`doc_extract` on an *identity* artifact and folds the result into
`fraud_synthesis`, a legitimately reused headshot can still score as a duplicate —
the exact false-positive the ontology exists to kill, on a path the exemption
never reaches. Closing it properly needs a **role-aware gate** (or role context
threaded onto those tools), not a per-call strip. Tracked as a follow-up.

**Also not yet done:** surface `identity_matches` as a positive "verified" chip in
the officer view; L3 calibration weights for the `reused_artifact_exact/near`
signal keys; carry the fraud ontology through non-relational MCP describe paths'
full semantic overlay (fraud fields now carry for all kinds; descriptions/enums
still overlay for relational only).

**Known limitation (E1, same class as the role-aware gate above) — the
EXIF↔claim comparator runs only in `consistency_check`.** A screened dataset
with NO primary key gets no auto-created screen (`_primary_key` returns None →
skip), yet `image_analyze`/`doc_extract` still fingerprint its evidence photos
with the same EXIF metadata — those paths never get the capture-before-claim /
GPS-vs-site comparison (which needs the record's claimed values, read by key —
impossible without a key). ACCEPTED for now: the record-bound screen is the
comparator's natural home, and a keyless dataset can't do the server-side
claim read anyway. Closing it properly rides the same follow-up as the
role-aware gate: thread record context into the shared artifact
post-processing for record-bound per-image tools.
