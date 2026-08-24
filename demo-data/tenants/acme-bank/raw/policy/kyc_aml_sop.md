<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: Apache-2.0

  Licensed under the Apache License, Version 2.0 (the "License"); you may not
  use this file except in compliance with the License. You may obtain a copy of
  the License at http://www.apache.org/licenses/LICENSE-2.0
-->

# KYC and AML SOP — Acme Bank & Insurance Ltd

**Document type:** SOP · **Owner:** Compliance, Central Operations
**Classification:** Internal · **Version:** 5.0 · **Effective:** 01 April 2026

> Synthetic SOP written for the Acme Bank demo tenant. Modelled on Indian
> KYC/AML practice; not a reproduction of regulatory text.

---

## 1. When KYC is performed

- At onboarding, before any account, loan or policy is issued.
- On **re-KYC** triggers (§5).
- Whenever the customer's risk category changes.

No disbursement, claim settlement or policy issuance may proceed while
`kyc_status` is `pending`.

## 2. Officially valid documents

One document each for identity and address:

| Purpose | Accepted |
|---|---|
| Identity | Aadhaar (masked), passport, driving licence, voter ID |
| Address | Aadhaar (masked), passport, utility bill (≤2 months), registered rent agreement |
| Tax | PAN — mandatory for all credit and for insurance above the reporting threshold |

**Aadhaar handling.** Only the last four digits are recorded. The full number is
never keyed into any system, printed, or attached to a file. A document image
containing a full Aadhaar must be redacted before upload; an unredacted image
is a reportable incident, not a formatting error.

**PAN handling.** Stored masked (`ABCXX1234F`). PAN is verified for structural
validity and against the name on record.

## 3. Customer risk categorisation

| Category | Indicators | Review cycle |
|---|---|---|
| Low | Salaried, verified employer, stable address, domestic transactions | 8 years |
| Medium | Self-employed, cash-intensive business, address change in 12 months | 4 years |
| High | PEP or PEP-related, adverse media, non-face-to-face onboarding without video KYC, high-value cash | 2 years |

Category is recorded at onboarding and revisited at every review.

## 4. Screening

1. **Sanctions and watchlist** screening at onboarding and on every name or
   address change.
2. **PEP screening** — a positive or possible match goes to Compliance before
   the relationship proceeds. An officer must not clear a PEP match alone.
3. **Adverse media** — for medium and high risk, and for any credit above
   ₹50 lakh.
4. A screening hit is recorded with its disposition and the officer's reason,
   whether cleared or escalated.

## 5. Re-KYC triggers

- Periodic cycle elapsed for the risk category.
- Change of address, name, or constitution of a business entity.
- Dormant account being reactivated.
- Transaction pattern materially inconsistent with the declared profile.
- Any document on file having expired.

Set `kyc_status = re_kyc_due` on trigger. The customer has 30 days from
intimation before servicing restrictions apply.

## 6. Suspicious activity

Escalate to the Principal Officer, Compliance, on the same working day where:

- the customer is reluctant to provide, or provides inconsistent, identity
  information;
- funds flow is inconsistent with the stated occupation or business;
- a third party appears to be operating the relationship;
- structuring is apparent — transactions arranged to sit just under a
  reporting threshold; or
- documents from different sources disagree about a material fact.

**Do not tip off the customer.** Escalation is confidential; the officer
records the escalation reference only.

## 7. Record keeping

KYC records are retained for the regulatory minimum after relationship
closure. Retention, access and masking standards are set out in the **Data
Protection and Customer Consent Policy**.

## 8. Accountability

The officer who accepts a KYC pack is accountable for its completeness. Where a
pack is accepted with a deficiency because of business urgency, the deficiency,
the approver and the cure date must all be recorded.
