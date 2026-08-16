<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: BUSL-1.1

  Licensed under the Business Source License 1.1. Non-production use is granted;
  production use requires a commercial licence until the Change Date, after
  which this file converts to Apache-2.0. See LICENSE at the repository root.
-->

# Data Protection and Customer Consent — Acme Bank & Insurance Ltd

**Document type:** Policy · **Owner:** Data Protection Officer, Compliance
**Classification:** Internal · **Version:** 1.6 · **Effective:** 01 April 2026

> Synthetic policy written for the Acme Bank demo tenant. Modelled on Indian
> data-protection practice; not a reproduction of statutory text.

---

## 1. Scope

All personal data of customers, applicants, claimants and leads, in every
system and in every physical file, including data held by service providers
acting for us.

## 2. Principles

1. **Purpose limitation** — collect for a stated purpose, use for that purpose.
2. **Minimisation** — collect the least that serves the purpose.
3. **Accuracy** — correct on request, promptly.
4. **Storage limitation** — retain per §6, then dispose.
5. **Security** — protect in transit and at rest.
6. **Accountability** — every access is attributable to a person.

## 3. Consent

- Consent is taken at collection, in clear language, and is specific to the
  purpose. Bundled consent for unrelated purposes is not valid consent.
- Consent for marketing is **separate** from consent for servicing, and may be
  withdrawn without affecting the product.
- Withdrawal is honoured within 7 working days across every channel.
- Consent records are retained for as long as the data is held.

## 4. Masking standard

The following are stored **masked**, and unmasked values are never written to
any application database, log, report or export:

| Field | Stored as |
|---|---|
| PAN | First three characters, then `XX`, then the numeric block and check letter (`ABCXX1234F`) |
| Aadhaar | Last four digits only |
| Mobile | Last four digits only (`XXXXXX1234`) |
| Bank account | Last four digits only |

Masking is applied **at the point of capture**, not at the point of display. A
system that stores the full value and masks it on screen does not comply with
this policy: a misconfigured screen, an export or a log line then exposes it.

Where a full value is unavoidable for a regulatory filing, it is retrieved from
the source system of record, used for the filing, and not persisted.

## 5. Access

- Access is role-based and least-privilege.
- Every read of a customer record in a servicing system is logged with the
  identity of the reader.
- Bulk export requires the Data Protection Officer's approval and is logged
  with the purpose.
- Screenshots containing customer data are not shared on messaging platforms.

## 6. Retention

| Data | Retained |
|---|---|
| KYC records | Regulatory minimum after relationship closure |
| Loan and claim files | 8 years after closure or settlement |
| Lead data (not converted) | 12 months from last contact |
| Call recordings | 12 months |
| Marketing consent records | Life of the consent, plus 3 years |

Disposal is by secure deletion, recorded in the disposal register.

## 7. Third parties

Every processor acting for us — collections agencies, surveyors, verification
vendors, cloud providers — is under a written agreement covering purpose,
security, sub-processing, breach notification and deletion on termination.
A processor may not use our customer data for any purpose of its own.

## 8. Breach

Any suspected exposure — a misdirected email, a lost device, an unredacted
document, an unauthorised access — is reported to the Data Protection Officer
**the same day**. Do not investigate alone, and do not attempt to retrieve or
delete evidence.

Assessment, notification to affected individuals and regulatory reporting are
handled by the DPO.

## 9. Customer rights

Customers may ask what data we hold, ask for correction, withdraw consent, and
complain about our handling of their data. Requests are answered within 30 days
and are routed through the **Grievance Redressal Policy**.

## 10. Demonstration and test data

Data used for demonstrations, training or testing is synthetic, or is
irreversibly masked. Production customer data is never used to demonstrate a
product.
