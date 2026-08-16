<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: BUSL-1.1

  Licensed under the Business Source License 1.1. Non-production use is granted;
  production use requires a commercial licence until the Change Date, after
  which this file converts to Apache-2.0. See LICENSE at the repository root.
-->

# Vertical + Country Ontology — Domain-Driven Fraud Screening Plan

> Status: ALL PHASES 0–6 BUILT + REVIEWED + 17 CONFIRMED FINDINGS FIXED (2026-07-20, local — not committed/deployed).
> Review fixes landed: schemas regenerated (drift test green) · domains badge reads the AGENTS collection, tenant-scoped, published-only · E3 matches the new `record_keys.key_values` stamp (writer + index added; overridden approvals excluded; negation-aware denial matching) · E7 tz-naive normalization · `_read_row_by_key` surfaces in-band MCP errors (an errored lookup can never read as "not found") · doc values come from `extracted` ONLY (pinning hole closed) · unparseable ledger/target values now BLOCK "verified" with a note · statement rows with missing/garbled txn columns skip the chain link visibly (never false-flag) · ring-key warn survives the cardinality branch · composed description clamped (create+reconcile) + runtime-reference mirror cap aligned at 1600 · vertical-pack tolerances now reach verify_against too · E1 migrated onto `_read_row_by_key` and E6 reuses E1's row (one record read per screening) · `payment_doc_attached` names the right config key per check · `_refresh_payment_sentence` deleted · `bare_record_key` owned by fraud_checks.
> Accepted debt (documented, not fixed): payment_proof/verify_against twin merge (mitigated by pack threading + shared gate; full preset-merge is Phase 3.1 if wanted) · E4/verify target reads still sequential · E7's app_slug grain is last-writer-wins on shared datasets · deploy order: MCP image with new registry models MUST roll before the annotated sources.json reaches prod (extra=forbid).
> Other open follow-ups: per-template demo seed data (§6), image_analyze emitting `serial_no`, per-inspector E7 grain. · Owner: rohit@trustedweartech.com
> Phase 0 (E4 doc-disambiguation F1/F2/F3/F6) + Phase 1 (`domain` block end-to-end,
> ontology-driven locale) are implemented and tested. §2 note: the block lives on
> the **source** (each dataset inherits; complete-block dataset override), not the
> file top level — the bare-array file form has no top level. acme-power annotated
> `utility / power_recovery + metering_inspection / US` (it is the US-flavored demo).
> Companion docs: `fraud-detection-coverage-matrix.md` (what we detect),
> `fraud-detection-primitives-plan.md` (how), `wedge-strengthening-plan.md` (why).

## 0. The strategic question first: does fixating on a vertical + country help?

**Yes — for deployment and targeting. No — for the codebase.** The two must not be
confused:

- **GTM/deploy**: a named `(vertical, sub_vertical, country)` triple turns onboarding
  into "pick your template": a starter `sources.json` with the right annotations, the
  right validators pre-armed, demo seeds in the right currency/ID formats, and a sales
  page that says "Loan-recovery fraud screening for Indian NBFCs" instead of "a
  configurable platform". Deployments are **single-tenant** (one org per deployment),
  so one deployment ≡ one country and usually one vertical — the grain fits perfectly.
- **Code**: ONE platform. The ontology *selects* from built-in packs; it never forks
  behavior. A vertical pack is **data** (defaults, weights, advisory text, expected
  annotations), not a code path. Unknown vertical ⇒ generic behavior, never an error
  at runtime — but a typo'd vertical ⇒ **schema rejection at publish** (fail loud).

## 1. Current state (what already exists — build on it, don't duplicate)

| Layer | Exists today | Gap |
|---|---|---|
| Locale validators | `_VALIDATORS_BY_LOCALE` packs in `fraud_checks.py`: **in** = PAN/IFSC/GSTIN/Aadhaar/phone-IN · **us** = SSN/EIN/ABA-routing/ZIP/phone-US · common = VIN/email. Cross-locale IDs stay name-driven (a field literally named `pan_no` validates as PAN anywhere). | Selected by deployment env `FRAUD_LOCALE`, invisible to the ontology, no per-dataset grain, no catalogue/UI surface. |
| Date order | `normalize_date` is locale-ordered (03/04 = Apr 3 US, 3 Mar IN). | Same env dependency. |
| Amounts | `normalize_amount` strips all grouping — `₹1,23,456` (lakh grouping) and `$123,456` both parse. Verify with a test; no change expected. | — |
| Fraud checks | E1 EXIF↔claim + E4 payment-proof, both ontology-driven (`fraud_screening` block). | No vertical awareness; E4 doc-disambiguation gap (§4 F1–F3). |
| Roles | `artifact_role: evidence|identity` on columns. | No `payment_proof` role. |
| Vertical | **Nothing.** | Everything in §3. |

## 2. Ontology design — the `domain` block

Exactly the ordering requested: **vertical first, sub-vertical second, country third.**

```jsonc
// sources.json — TOP LEVEL (source grain; every dataset inherits)
{
  "domain": {
    "vertical": "banking",          // 1st — closed enum (registry, §3)
    "sub_vertical": "loan_recovery",// 2nd — closed enum PER vertical
    "country": "IN",                // 3rd — ISO-3166 alpha-2; enum {IN, US} for v1
    "region": "BR",                 // optional — state/province code, free-form
    "currency": "INR",              // optional — DERIVED from country when omitted
    "date_order": "DMY",            // optional — DERIVED from country when omitted
    "notes": "DISCOM arrears recovery"  // optional free-text, shown in catalogue
  },
  "sources": [ ... ]
}
```

```jsonc
// Optional PER-DATASET override (rare: one MCP serving two lines of business)
{ "name": "insurance_claims", "domain": { "vertical": "insurance", "sub_vertical": "claims" }, ... }
```

**Rules (all enforced, all fail-loud):**
1. `extra = "forbid"` — a typo'd key rejects at publish, like every fraud block.
2. `vertical` and `country` are **closed enums** in the JSON schema. Extending =
   adding to the registry + schema regen (one PR), not loosening validation.
3. `sub_vertical` must belong to its `vertical` (validator on the model — the pairs
   table in §3 is the source of truth).
4. Derivation, never silent defaulting: omit `currency`/`date_order` ⇒ derived from
   `country` and **logged once at crawl**; an explicit value that contradicts the
   country derivation is allowed but WARN-logged (deliberate override, visible).
5. **Precedence for locale-sensitive behavior**: dataset `domain` → source `domain`
   → `FRAUD_LOCALE` env (kept as legacy fallback) → `us`. When ontology and env
   disagree, ontology wins and the disagreement is logged — the env var becomes a
   bootstrap default for un-annotated deployments, nothing more.
6. `domain` is **advisory-only for behavior selection** — it can arm defaults and
   relax/tune, but a dataset still opts into screening via `fraud_screening`
   (existing rule: ontology can only relax, absence = no check).

## 3. The registry — verticals, sub-verticals, and what each drives

### 3.1 Registry (v1 — extensible by design, one entry per target market)

| vertical | sub_verticals | Primary checks armed by the pack |
|---|---|---|
| `insurance` | `claims`, `underwriting` | E1 photo-vs-claim (capture date vs incident, GPS vs site) · artifact reuse · invoice arithmetic · entity rings (corroboration) · E6 claim-vs-policy-start |
| `banking` | `loan_origination`, `loan_recovery` | origination: checksum IDs · synthetic identity · collateral reuse · E5 statement reconciliation · recycled docs. recovery: **E4 payment-proof** · doctored receipts · E3 resubmission · receipt reuse across accounts |
| `utility` | `power_recovery`, `metering_inspection` | E4 vs billing ledger · E1 GPS-vs-premise · "new tenant" identifier clearing (entity) · reused inspection photos |
| `field_service` | `equipment_inspection` | E1 full pack · wrong-asset/nameplate semantics · defect-photo reuse · E7 pencil-whipping (corroboration) · E6 date rules |

### 3.2 What `country` drives (deterministic, cheap — the efficiency win)

| Behavior | IN | US |
|---|---|---|
| ID validator pack | PAN, Aadhaar, GSTIN, IFSC, phone-IN | SSN, EIN, ABA routing, ZIP, phone-US |
| Date order | DMY | MDY |
| Currency label in evidence text | ₹ / INR (lakh-crore aware) | $ / USD |
| Advisory examples in Screening Health | UTR references, IFSC formats | ACH trace numbers, routing formats |
| Demo seeds / starter templates | Indian names, UTRs, IFSC codes | US names, ACH refs, routing numbers |

Mechanism: autowire stamps a resolved `locale` (from the dataset's effective
`domain.country`) onto every `consistency_check` / `fraud_synthesis` config it
creates or reconciles; the dispatch passes it through to `validate_formats`,
`normalize_date`, `exif_vs_claim`, `payment_proof_check` — all of which already
accept `locale=`. **The env var stops being the decider; the ontology becomes it.**

### 3.3 What `vertical`/`sub_vertical` drives

1. **Default tolerances + gate weights** per sub-vertical (data, not code):
   e.g. `gps_radius_km` default 10 generally but 1 km for `metering_inspection`
   (premise-bound), `date_window_days` 3 for loan_recovery but 7 for insurance
   claims. Ontology per-field values always override pack defaults.
2. **Missing-annotation advisories at autowire (WARN, never error):** a
   `loan_recovery` dataset with document artifacts but no `payment_proof` block
   logs "loan_recovery dataset 'x.y' has evidence documents but no payment_proof
   declared — the killer check for this vertical is off. See sources-file.md §10.2".
   This is the learning-curve-visible wedge applied to onboarding: the system tells
   the integrator what a good ontology for their vertical looks like.
3. **Screening Health education catalogue ordering/filtering:** the "What we screen
   for" page leads with the groups relevant to the deployment's vertical (payment-proof
   group first for recovery verticals, photo-metadata first for inspections), and the
   header shows the domain badge: *"India · Banking · Loan recovery"*. Everything
   stays visible — filtering is ordering, not hiding.
4. **Builder skill hints:** the app-builder skill receives the domain triple in the
   catalogue context so generated apps name the right checks in recommendations
   ("payment VERIFIED against the ledger" phrasing for recovery apps).
5. **Deploy templates (§6):** one starter `sources.json` per (vertical, country).

## 4. Fixes & enhancements folded in (from the E4 doc-matching analysis)

These ship FIRST (Phase 0) — they close a live false-alarm risk in E4.

- **F1 — `artifact_role: "payment_proof"` + doc binding.** New role value on
  columns (schema enum extended). Autowire stamps the receipt column onto the
  payment check; the dispatch accepts a payment reference **only** from that
  document's extraction. A land-purchase bill (role `evidence`) attached to the
  same record can never feed the ledger match — the two-bills case becomes
  structurally impossible, not prompt-dependent.
- **F2 — ledger description in the tool description.** Autowire holds the full
  catalogue entry; stamp the ledger dataset's name + catalogue `description` into
  the screen's tool description: *"payment references verify against
  `billing.payments` ('settled customer payments, UTR-keyed') — supply the
  reference from the payment-proof document only."* The recommending LLM sees what
  the ledger is FOR (the user's "LLM must see the dataset description" point).
- **F3 — explicit skip note.** No payment_proof-role document on the record ⇒
  check skipped with a visible note in the output, never run against whatever
  document happens to be attached.
- **F4 — generalize E4 into a `verify_against` primitive (Phase 3).** The E4 shape
  (extract claimed value from a role-tagged document → server-side read-by-key
  against a declared dataset → match/mismatch/not-found) reappears across
  verticals: purchase bill → land registry, agent's "cash collected" → ledger,
  serial-in-photo → asset master, repair bill → surveyor estimate. One generic
  ontology block (`verify_against: [{doc_role, target_dataset, match_field, ...}]`)
  covers them with zero new detection machinery. E4's `payment_proof` block remains
  as-is (it is the first, named instance); `verify_against` is the extensible form.
- **F5 — coverage-matrix domain tables. ✅ DONE 2026-07-20** — the five per-vertical
  fraud tables (loan sanction / loan recovery / insurance / utility recovery /
  equipment inspection, with ✅/◐/E#/⬜/✗ status) now live in
  `fraud-detection-coverage-matrix.md` §1.5, cross-referenced to the §3.1 packs here.
- **F6 — test pin for Indian amount grouping.** Add an explicit test that
  `₹1,23,456.00` ≡ `123456` in `normalize_amount` (believed to work — pin it).

## 5. Schema & model changes (the mesh, end to end)

| # | File | Change |
|---|---|---|
| 1 | `source-mcp-template/registry_models.py` | `Domain` model (extra=forbid; vertical/sub_vertical/country enums; sub-vertical∈vertical validator; currency/date_order derivation helpers) + `domain` at file top level and optional per-dataset. `artifact_role` enum gains `payment_proof` (F1). |
| 2 | `source-mcp-template/models.py` | Mirror `Domain` in the describe layer. |
| 3 | `source-mcp-template/schema/sources.schema.json` | Regenerate (`gen_sources_schema.py`) — enums land in the schema, typos die at publish. |
| 4 | `data-discovery-service/models.py` | `CatalogueDomain` carry-through; crawl logs the derived currency/date_order once. |
| 5 | `smart-app-service/fraud_roles.py` | Resolve effective domain per dataset (dataset→source→env precedence); stamp `locale` onto screens + synthesis configs; pack-default tolerances; missing-annotation advisories (§3.3.2); F1 doc-column binding; F2 description stamping. |
| 6 | `smart-app-service/models.py` | `locale` + `doc_column` on `ConsistencyCheckTool`/`PaymentProofCheck` (+ runtime-reference mirror). |
| 7 | `smart-app-service/tools_v2_dispatch.py` | Thread stamped `locale` into every check call (replaces env resolution when present); F1 acceptance guard; F3 skip note. |
| 8 | `smart-app-service/fraud_synthesis.py` | Pack-aware default weights (data table keyed by sub_vertical; existing `_POINTS_DEFAULTS` = the generic pack). |
| 9 | `Citra-UI/screens/ScreeningHealthScreen.js` | Domain badge in header; catalogue group ordering by vertical (data-driven from a served field on `/org/screening-stats`). |
| 10 | `source-mcp-template/docs/sources-file.md` | New §: the `domain` block — full field table, derivation rules, per-vertical annotation guides ("what a good loan_recovery ontology declares"). |
| 11 | `demo-data/` | acme-power gains `domain: utility/metering_inspection/IN` (first live annotation); template starters per §6. |

## 6. Deploy templates — the targeting artifact

One starter `sources.json` per target cell, shipped in the template repo under
`source-mcp-template/templates/`:

| Template | domain | Pre-annotated |
|---|---|---|
| `banking-loan_recovery-IN` | banking/loan_recovery/IN | payment_proof vs payments ledger, receipt `artifact_role`, entity keys (phone/account), UTR-flavored examples |
| `insurance-claims-IN` · `insurance-claims-US` | insurance/claims/{IN,US} | claim_context (incident date + GPS), invoice arithmetic fields, VIN/policy entity keys |
| `utility-power_recovery-IN` | utility/power_recovery/IN | payment_proof vs billing, premise GPS claim_context, consumer-id entity keys |
| `field_service-equipment_inspection-*` | field_service/equipment_inspection | full E1 claim_context, asset entity keys |

Each template is a **complete, valid, commented** sources file where the integrator
replaces connection details and column names — the "quick build" wedge applied to
fraud onboarding. Sales motion: pick the cell, deploy the template, demo in the
prospect's own vocabulary on day one.

## 7. Phases & effort

| Phase | Scope | Effort |
|---|---|---|
| **0** ✅ BUILT | F1 + F2 + F3 + F6 (E4 doc-disambiguation — closes a live false-alarm risk). Registry additionally REQUIRES the pairing at publish (`_payment_proof_needs_a_tagged_document`); autowire drops an unpinned config loudly; F2 sentence refreshes idempotently on reconcile. | done |
| **1** ✅ BUILT | `Domain` ontology block end-to-end: registry `Domain` (closed enums, pairing validator, currency/date_order derivation fill) + describe/discovery mirrors + `_flatten` carry + catalogue `_effective_domain` normalization + schema regen + autowire `locale` stamp (set/flip/CLEAR) + dispatch threading into cross_check/validate_formats/exif/payment + acme-power annotation (US) + sources-file.md §2.1 | done |
| **2** ✅ BUILT | Vertical packs: `VERTICAL_PACK_DEFAULTS` (metering/equipment inspection → 1 km GPS; insurance claims → 7-day payment window; explicit > pack > platform, enforced by making tolerances Optional end-to-end), missing-annotation advisories at create AND reconcile, domain triple stamped on screens → `/org/screening-stats.domains` → Screening Health badge + vertical-aware catalogue ordering (ordering only, nothing hidden) | done |
| **3** ✅ BUILT | F4 `verify_against`: registry `VerifyAgainst`/`VerifyCompare` (unique slug, pinned to a role-tagged doc_column — publish-rejected otherwise) + autowire routing resolution (target_source_id/kind/description, per-comparison fail-loud drops) + generic comparator (amount/date/id/text, tolerance/window, not-found fact-grade, VERIFIED positive) + dispatch loop (doc-attached gate, server-side read-by-key, per-check results) + gate weights (not-found 3 / mismatch 2) + advisories + description sentence + education-catalogue entry | done |
| **4** ✅ BUILT | Deploy templates under `source-mcp-template/templates/`: banking-loan_recovery-IN, insurance-claims-{IN,US}, utility-power_recovery-IN, field_service-equipment_inspection-US + README + CI validation test (every template must always parse; filename must match its domain cell). Demo SEED DATA per template still pending. | done (seeds pending) |
| **5** ✅ BUILT | New deterministic checks (2026-07-20): **E6** `date_rules` ontology block (registry `DateRule` + column/uniqueness validators + autowire + server-side record read by key + `date_rule_violation` weight 2) · **E3** `entity_links.rejected_priors` (auto-runs after entity link; denial-text match on prior committed decisions; human_rejected excluded; `resubmitted_after_rejection` weight 2, quotes the prior decision) · **E5** `statement_reconciliation` (`statement_rows` arg on the screen; ≥2-break threshold; `statement_chain_break` weight 3). Advisories + Screening Health education entries + sources-file.md §10.4 done. | done |
| **6** ✅ BUILT | Vertical long-tail (2026-07-20): **E2** role-aware gate closure verified + regression-pinned (apply_reuse_signal strips raw reuse keys on all three paths; identity dup scores 0 at the gate) · **E7** photoset-timing cluster (EXIF capture instants persisted on the fingerprint store + (tenant,app,capture_time) index; ≥2 other records within ±15 min → `photoset_timing_cluster`, weight 1, corroboration_only, excluded from issue counts) · **serial-OCR** = verify_against annotation, now shipped in the field_service template (`serial_vs_asset_master`) · **ring keys** — declared-generic identity_fields (address/employer/witness) link as ring keys but score WARN, never mismatch (`_link_severity` + `declared_generic` marker). | done |

Order rationale: Phase 0 protects what's already built; Phase 1 makes country
ontology-driven (the env var demotes to fallback); Phase 2 is where "fixate to
deploy faster" becomes visible to admins and integrators; Phases 3–4 scale the
catalogue across verticals without new detection code; Phases 5–6 add the remaining
detection checks themselves.

### 7.1 Complete phase map — every open item from coverage-matrix §1.5

Every fraud type from the five domain tables that is not already ✅ built, mapped to
the phase that delivers it. (✗ rows — consumption-drop anomaly scoring, external
death/disability registries — are excluded by doctrine or deferred until a customer
subscription exists, and appear in no phase.)

| Open item (vertical) | Coverage-matrix status | Delivered by | How |
|---|---|---|---|
| E4 two-bills disambiguation (all E4 users) | risk on ✅ | **Phase 0** | F1 `payment_proof` role + F2 ledger description + F3 skip note |
| Indian amount-grouping pin (IN deployments) | untested assumption | **Phase 0** | F6 test |
| Locale-correct checksums/dates per deployment (all) | env-driven today | **Phase 1** | `domain.country` → stamped locale |
| Vertical default tolerances + missing-annotation advisories (all) | — | **Phase 2** | §3.3 packs |
| Doctored purchase/valuation bill (loan origination) | ◐ | **Phase 3** | `verify_against` → registry dataset |
| Field-agent "collected cash", no ledger entry (loan recovery) | ◐ | **Phase 3** | `verify_against` flipped: record claims paid → ledger not-found |
| Repair bill vs surveyor estimate (insurance) | ⬜ small | **Phase 3** | `verify_against` doc→doc/dataset amount match |
| Ownership shuffle to evade dues (utility) | ◐ | **Phase 3** | `verify_against` party history on premise + entity keys |
| Inflated income, cross-doc (loan origination) | ⬜ cross-doc | **Phase 3** | `verify_against` payslip income ↔ statement salary credits |
| Stale/misdated documents (loan origination) | E6 | **Phase 5** | declarative date rules: statement period vs application date, payslip age |
| Claim days after policy start (insurance) | E6 | **Phase 5** | E6 date rule |
| Inspection dated before work order (equipment) | E6 | **Phase 5** | E6 date rule |
| Dispute resubmitted after rejection (loan recovery) | E3 | **Phase 5** | entity link → prior DecisionRecord outcome = rejected |
| Duplicate refund/adjustment requests (utility) | E3 | **Phase 5** | same E3 join |
| Fabricated bank statement (loan origination) | E5 | **Phase 5** | running-balance chain breaks, OCR-tolerant, multiple breaks only |
| Serial number in photo ≠ record's asset (equipment) | ⬜ small | **Phase 6** | OCR value from photo → `verify_against` asset master |
| Straw-borrower rings (loan origination) | ◐ | **Phase 6** | address/employer/reference-phone as entity keys — corroboration-only |
| Staged-accident clusters (insurance) | ◐ | **Phase 6** | witness/tow/garage entity keys — corroboration-only |
| Pencil-whipping photoset timing (equipment) | E7 | **Phase 6** | ≥N distinct sites within minutes — corroboration-only |
| Role-aware gate closure (all — kills a false-alarm class) | E2 | **Phase 6** | thread `artifact_role` onto raw artifact_flags |

## 8. Explicit non-goals (doctrine)

- No behavior forks per vertical — packs are defaults and advisories only.
- No anomaly/behavioral scoring per vertical (consumption drops, velocity models) —
  the false-alarm factory stays excluded regardless of what a vertical "expects".
- No auto-enabling checks the ontology didn't opt into — `domain` tunes and advises;
  `fraud_screening` remains the only on-switch.
- No open-string enums "for flexibility" — extending the registry is a one-PR schema
  change; silent unknown values are how typos become silent no-ops.
