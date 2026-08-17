<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: BUSL-1.1

  Licensed under the Business Source License 1.1. Non-production use is granted;
  production use requires a commercial licence until the Change Date, after
  which this file converts to Apache-2.0. See LICENSE at the repository root.
-->

# Deploy templates — one starter `sources.json` per (vertical, country)

Each file is a **complete, valid** sources registry for one target market cell
(see `docs/vertical-country-ontology-plan.md` §6 in the main repo). The sales /
onboarding motion: pick the cell, copy the template, replace the placeholders,
and the deployment demos in the prospect's own vocabulary with the vertical's
killer fraud checks pre-annotated.

| Template | domain | Pre-annotated checks |
|---|---|---|
| `banking-loan_recovery-IN` | banking / loan_recovery / IN | payment_proof vs the payments ledger, ALL FOUR artifact roles, date rule (stale receipt), decision_history, explicit opt-out, **bureau score as a rest_api dataset** (`mandatory_when_used`) |
| `insurance-claims-IN` / `-US` | insurance / claims / {IN,US} | EXIF-vs-claim (incident date + GPS), verify_against (invoice vs surveyor estimate), date rule (claim-after-policy-start), all roles, decision_history, explicit opt-out, **industry fraud registry as a rest_api dataset** |
| `utility-power_recovery-IN` | utility / power_recovery / IN | payment_proof vs billing, identity-role ID proof ("new tenant"), premise/consumer entity keys, explicit opt-out |
| `field_service-equipment_inspection-US` | field_service / equipment_inspection / US | full EXIF claim context (1 km pack radius), verify_against (serial vs asset master), date rule (inspection-before-work-order), supporting role |

## The grammar the templates teach

Every dataset in a template plays one of FOUR parts — learn the contrast, it is
the whole fraud ontology:

| Part | How it's declared | Template example |
|---|---|---|
| **Screened dataset** | `fraud_screening.applies: true` (or artifact roles) | disputes, claims, inspections |
| **Verification target** | NO `fraud_screening` block at all — silence. Named by another dataset's `payment_proof.ledger_dataset` / `verify_against.target_dataset` | payments ledger, surveyor estimates, asset master |
| **Explicit opt-out** | `fraud_screening: {"applies": false}` — hard off; autowire clears any hand-wired screen | branch/tariff/garages masters |
| **Agent-read lookup (API-as-dataset)** | `type: rest_api` + `kind: rest` + `input_schema` (+ `mandatory_when_used` for required checks) | bureau credit score, fraud registry |

And every artifact column declares what its document IS:

| `artifact_role` | Reuse across cases means | Extra behavior |
|---|---|---|
| `evidence` | fraud (double-dip / recycled proof) | fingerprinted + EXIF-vs-claim |
| `identity` | VERIFICATION (same person, expected) | never a duplicate flag — the false-alarm killer |
| `payment_proof` | fraud (one receipt can't clear two cases) | PINS the E4 ledger check to this document only |
| `supporting` | meaningless | never fingerprinted at all |

## APIs, Salesforce, SAP — everything is a dataset

The bureau/registry sources show the pattern: an external API is exposed as a
**parameterised dataset** (`input_schema` declares the required params, the
`read_via.extra` request/response mapping does the HTTP), the MCP holds the
corporate credentials and the audit trail, and the app's agent reads it like
any table. `mandatory_when_used: true` makes the platform ENFORCE the lookup
before any write is staged.

**Swapping the backend needs no new template.** Salesforce / SAP / OData
sources are the same dataset abstraction — change the dataset `kind`
(`soql` / `sap_rfc` / `odata`) and the `connection` block (where all URL/auth
complexity lives, via `env_prefix` — never inline); every fraud/domain
annotation is kind-agnostic and stays exactly as written.

**One rule to remember:** a `rest` dataset can be an agent-read lookup and a
registry-evidence source, but it can NOT be a `payment_proof` /
`verify_against` target — the structured read-by-key plane covers
sql / odata / soql / mongodb. Point ledgers and masters at structured datasets.

**Media columns need no URL setup here.** `column_kind: image_url|document_url`
+ a role is the whole declaration; the VALUES in your rows are keys/URLs your
MCP resolves server-side at screening time (`/resolve_media`) — the browser
and the templates never carry storage details.

## How to use

1. Copy the template to your tenant's `mcp/sources.json`.
2. Replace every `REPLACE_*` value (`dept_id`, `org_id`) and the
   `connection.env_prefix` credentials (see `docs/sources-file.md` §4 — never
   inline credentials).
3. Rename tables/columns to your real schema. The fraud annotations
   (`artifact_role`, `fraud_screening`, `identity_fields`) are the templates'
   showcase — keep the ones whose checks you want; **fraud screening is a
   feature the ontology opts into, never a requirement** (delete the blocks
   and the deployment simply runs without screening). Keep `domain` — that's
   what makes the deployment vertical- and locale-aware.
4. Validate: `python validate_sources.py path/to/sources.json` — every rule in
   the template (payment_proof pinning, domain enums, claim-context pairs) is
   enforced at publish, so a typo dies here, not silently in production.
5. Boot the MCP with `SOURCES_FILE` pointing at the file; crawl; build apps.

The `domain` block is what makes the deployment vertical-aware: locale-correct
ID validators and date parsing (`country`), pack-default tolerances and
missing-annotation advisories (`sub_vertical`), and the admin Screening Health
badge. Behavior packs are defaults only — every explicit value in the file wins.

Templates are validated in CI (`tests/test_templates.py`): every file here must
parse against `registry_models.SourcesFile` at all times.
