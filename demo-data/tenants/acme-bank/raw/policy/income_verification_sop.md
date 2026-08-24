<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: Apache-2.0

  Licensed under the Apache License, Version 2.0 (the "License"); you may not
  use this file except in compliance with the License. You may obtain a copy of
  the License at http://www.apache.org/licenses/LICENSE-2.0
-->

# Income Verification SOP — Acme Bank & Insurance Ltd

**Document type:** SOP · **Owner:** Credit Operations
**Classification:** Internal · **Version:** 3.1 · **Effective:** 01 April 2026

> Synthetic SOP written for the Acme Bank demo tenant.

---

## 1. Purpose

To standardise how declared income is **evidenced** on a retail credit
application, so that two officers examining the same file collect and check
the same things.

This SOP covers the **collection, validity and arithmetic** of income
documents. Judgement about whether a particular applicant's overall financial
picture hangs together remains with the credit officer.

## 2. Which document set applies

| Applicant type | Mandatory set |
|---|---|
| Salaried | Latest 3 payslips + 6 months' salary-credit bank statement + Form 16 (latest year) |
| Self-employed professional | ITR with computation (2 assessment years) + 12 months' bank statement |
| Business / proprietor | ITR with computation (2 AY) + audited financials where turnover exceeds the audit threshold + 12 months' current-account statement |
| Pensioner | Pension order + 6 months' pension-credit statement |

An application must not proceed to sanction with an incomplete set. Record the
gap as `document_missing`.

## 3. Payslip checks (salaried)

1. Payslip carries employer name, employee name, month, and a breakup of
   earnings and deductions.
2. The three payslips are consecutive and cover the three months immediately
   preceding application.
3. **Net income** = gross earnings − statutory deductions. Variable pay
   (incentive, overtime, bonus) is averaged over 12 months and taken at 50%.
4. Net income on the payslip matches the salary credit in the bank statement
   for the same month within ±5%. A larger gap must be explained in writing by
   the employer.

## 4. Bank statement checks

1. Statement is for an account in the applicant's name, continuous, and either
   bank-generated or e-statement with a verifiable reference.
2. Salary credits appear on a regular date with a consistent narration.
3. Note and record: average monthly balance, number of inward cheque or NACH
   returns, and any EMI debits not disclosed on the application. **Undisclosed
   EMIs must be added to the FOIR computation.**
4. Statements with obvious editing artefacts are rejected and the file is
   marked for re-submission.

## 5. ITR checks (self-employed, professional, business)

1. The return is for the correct assessment year and is the **filed** copy,
   bearing an acknowledgement number and filing date.
2. The acknowledgement number is well-formed and the filing date precedes the
   application date.
3. The computation sheet accompanies the return.
4. Income considered for eligibility is **net profit after tax**, plus
   depreciation and other non-cash charges added back, averaged over the two
   assessment years on record.
5. Where the two years differ by more than 25%, take the lower year.
6. Business applicants: GST returns for the last 4 quarters are collected where
   registration exists, and turnover is noted.

## 6. Form 16 checks (salaried)

1. Form 16 corresponds to the latest completed financial year.
2. Part A bears a TRACES watermark and a certificate number.
3. The employer name matches the payslips and the employment proof.

## 7. Recording the outcome

Record on the application:

- `income_proof_type` — the document type relied on,
- the **net monthly income** taken for eligibility, and how it was computed,
- `foir_percent`, computed on that figure,
- any deduction, add-back or averaging applied, in one line.

Where the applicant's declared figure differs from the figure computed from the
**document set applicable under §2** — payslips and salary credits for a
salaried applicant, the ITR route for a self-employed one — the computed figure
is used for eligibility, and the difference is noted in the remarks.

## 8. Escalation

Escalate to the Credit Manager where:

- an employer cannot be verified against the empanelled-employer list,
- the bank statement shows more than two NACH returns in 6 months,
- documents appear altered, or
- the applicant declines to provide a document in the mandatory set.

## 9. What this SOP does not cover

Valuation of property, bureau interpretation, and the final credit call. Those
are governed by the **Retail Credit Policy**.

---

*Prepared by Credit Operations. Suggestions for amendment go to the Head of
Credit Operations; officers who identify a recurring gap in this SOP are asked
to raise it rather than work around it.*
