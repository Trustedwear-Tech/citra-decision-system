<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: Apache-2.0

  Licensed under the Apache License, Version 2.0 (the "License"); you may not
  use this file except in compliance with the License. You may obtain a copy of
  the License at http://www.apache.org/licenses/LICENSE-2.0
-->

# Health Claim Settlement SOP — Acme Bank & Insurance Ltd

**Document type:** SOP · **Owner:** Claims, Health
**Classification:** Internal · **Version:** 3.8 · **Effective:** 01 April 2026

> Synthetic SOP written for the Acme Bank demo tenant.

---

## 1. Scope

Hospitalisation claims under health policies issued by Acme Bank & Insurance,
whether cashless at a network hospital or by reimbursement.

## 2. Intimation

| Situation | Intimate within |
|---|---|
| Planned hospitalisation | 48 hours before admission |
| Emergency admission | 24 hours of admission |
| Reimbursement claim | The policy's `intimation_window_days` from discharge |

Late intimation follows the same graded treatment as motor (see §2.1 of the
Motor Claim Settlement SOP): condonation by the officer up to 7 days, Claims
Manager approval to 30, and repudiation beyond that unless the reason is
compelling and documented.

## 3. Cashless pathway

1. Hospital submits the pre-authorisation request with the treating doctor's
   plan and estimate.
2. Decision on pre-authorisation within **4 hours** of a complete request.
3. Enhancement requests are decided within 4 hours.
4. Final bill reviewed against the pre-authorised amount at discharge;
   discharge approval within **4 hours** of the final bill.

## 4. Reimbursement documents

- Duly signed claim form
- Discharge summary bearing the hospital's stamp and the treating doctor's
  signature
- Final bill with an itemised breakup, and payment receipts
- Investigation reports supporting the diagnosis
- Pharmacy invoices with the corresponding prescriptions
- Implant sticker and invoice, where applicable
- FIR or MLC where the admission arises from an accident or assault

## 5. Waiting periods

| Type | Period |
|---|---|
| Initial waiting (all illnesses except accident) | 30 days |
| Specified ailments (hernia, cataract, joint replacement, etc.) | 24 months |
| Pre-existing disease | As stated on the policy schedule, typically 36 months |
| Maternity | 24 months, where the benefit is opted |

Waiting periods run from the **first inception** of continuous cover, not from
the latest renewal, where the policy has been renewed without a break.

## 6. Common exclusions

- Treatment not requiring hospitalisation, or admission solely for evaluation
- Cosmetic treatment unless reconstructive after an accident
- Dental treatment unless arising from an accident
- Self-inflicted injury, and treatment for substance abuse
- Experimental or unproven treatment
- Non-medical consumables listed in the policy's non-payable schedule

## 7. Assessment

1. Confirm the policy was in force and the premium paid.
2. Confirm the admission was medically necessary and of appropriate duration —
   an admission materially longer than the norm for the procedure is queried
   with the hospital, not silently deducted.
3. Apply room-rent capping proportionately where the room category exceeds the
   entitlement.
4. Apply co-payment and sub-limits per the schedule.
5. Deduct non-payable consumables.
6. Check for the same ailment claimed within the last 12 months.

## 8. Turnaround

| Stage | TAT |
|---|---|
| Pre-authorisation decision | 4 hours |
| Query raised on a reimbursement claim | 7 working days of receipt |
| Decision after complete documents | 15 working days |
| Payment after approval | 5 working days |

Queries are raised **once, consolidated**. Serial queries on the same claim are
a service failure and are reported to the Claims Manager.

## 9. Repudiation

State the clause, the facts and the escalation route. Where repudiation rests
on a pre-existing disease, the medical basis for concluding the condition
pre-dated cover must be on file — a diagnosis close in time to inception is not,
by itself, that basis.
