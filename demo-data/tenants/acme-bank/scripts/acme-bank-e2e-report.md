<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: BUSL-1.1

  Licensed under the Business Source License 1.1. Non-production use is granted;
  production use requires a commercial licence until the Change Date, after
  which this file converts to Apache-2.0. See LICENSE at the repository root.
-->

# Acme Bank — middleware / data-plane E2E report

Run: 2026-08-08 16:35:39

| Step | Core | Result | Name | Detail |
|---|---|---|---|---|
| 1 | yes | PASS | MCP /health — 4 structured sources, registered, right org | sources=['insurance_claims', 'loan_origination', 'loan_servicing', 'sales_crm'] org=acme-bank registered=True |
| 2 | yes | PASS | MCP /datasets — all 16 SQL datasets exposed | 16 datasets exposed; all 16 expected present |
| 3 | yes | PASS | LAN-NEEDLE-001 — clean file, uncorroborated income | clean on every SOP check, and filed 620,000 vs declared 2,220,000 = 28% corroborated |
| 4 | yes | PASS | LON-NEEDLE-002 — DPD 61 with a broken promise-to-pay | dpd=61 bucket=61-90 npa=sma_2, 1 broken PTP |
| 5 | yes | PASS | CLM-NEEDLE-003 — estimate byte-identical to one other claim | identical estimate on ['CLM-2026-000001', 'CLM-NEEDLE-003'] — duplicate screen has a real target |
| 6 | yes | PASS | record_credit_decision — dry run does not mutate | dry_run ok and the row is untouched |
| 7 | yes | PASS | catalogue — 16 datasets, tenant-clean, searchable | 17 datasets, no cross-tenant rows, search top hit correct |
| 8 | yes | PASS | SOP corpus — 12 documents ingested and org-isolated | 12 chunks / 12 docs in mcp_dept_libraries; income SOP present (policy/income_verification_sop.md) |
| 9 | yes | PASS | apps — decision apps carry case_signature, dashboard does not | acme-bank-collections-priority: 4 facets / 7 codes<br>acme-bank-claim-triage: 5 facets / 7 codes<br>acme-bank-sales: no signature (correct — dashboard only) |
| 9.5 | yes | PASS | audience — every officer sees their own app | collections: ['loan-credit-decision', 'acme-bank-collections-priority']<br>claims: ['loan-credit-decision', 'acme-bank-claim-triage']<br>lending: ['loan-credit-decision', 'loan-application-triage']<br>sales_distribution: ['loan-credit-decision', 'acme-bank-sales'] |
| 9.6 | no | PASS | documents — real files, exactly one deliberate duplicate | filed documents: 1013<br>byte-identical groups: 1<br>the intended pair: ['DOC-00000002', 'DOC-NEEDLE-001']<br>/media streamed 1,609 bytes (application/pdf) |
| 9.7 | yes | PASS | needles — every demo case is reachable in its panel | LON-NEEDLE-002: 500 rows, truncated=True<br>CLM-NEEDLE-003: 465 rows, truncated=False<br>CLM-NEEDLE-004: 465 rows, truncated=False<br>LED-NEEDLE-005: 500 rows, truncated=True |
| 9.8 | yes | PASS | reject — the correction lands, coded, in the ledger | correction=corr-bcb0ad02405b49e1 reason_code=income_not_corroborated facets=['amount_band:1000000_2500000', 'foir_band:lt_30', 'income_proof:present', 'product:personal', 'sourcing_channel:branch'] |
| 9.9 | no | INFO | memory — seeded evidence consolidated into team judgements | loan-application-triage: scope ['amount_band:1000000_2500000', 'foir_band:lt_30', 'income_proof:present', 'product:personal', 'sourcing_channel:branch'] != ['income_proof:present'] — the judgement does not apply to the cases it was taught on<br>loan-application-triage: status=candidate officers=1 — expected a live judgement with 3 distinct officers (promotion_min_officers)<br>acme-bank-collections-priority: [active] scope=['risk_flag:dispute'] officers=3 "Stop collection activity and route the case to servicing whe…"<br>acme-bank-claim-triage: [active] scope=['intimation_delay:gte_30'] officers=3 "For intimation past the 30-day window, perform exclusion rev…"<br>loan-application-triage: [candidate] scope=['amount_band:1000000_2500000', 'foir_band:lt_30', 'income_proof:present', 'product:personal', 'sourcing_channel:branch'] officers=1 "Refer for verification when the filed return does not corrob…" |
| 9.95 | no | PASS | memory — team standing requires 3 DISTINCT officers | acme-bank-collections-priority/C-001: status=active distinct_officers=3 (threshold 3)<br>loan-application-triage/C-003: status=candidate distinct_officers=1 (threshold 3)<br>loan-application-triage/C-001: status=dissented distinct_officers=3 (threshold 3)<br>loan-application-triage/C-002: status=dissented distinct_officers=3 (threshold 3) |
| 10 | no | PASS | discovery-service reachable | discovery up: {"status": "ok", "service": "discovery-service", "tool_count": 10} |
| 11 | no | PASS | memory — corrections and judgements for this tenant | corrections=6 judgements=2 (seeded) |

**Core steps: ALL PASS**
