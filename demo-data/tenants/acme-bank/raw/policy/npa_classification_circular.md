<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: BUSL-1.1

  Licensed under the Business Source License 1.1. Non-production use is granted;
  production use requires a commercial licence until the Change Date, after
  which this file converts to Apache-2.0. See LICENSE at the repository root.
-->

# Asset Classification and Provisioning — Circular AB/RISK/2026-07

**Document type:** Circular · **Owner:** Credit Risk
**Classification:** Internal · **Issued:** 01 July 2026 · **Supersedes:** AB/RISK/2025-11

> Synthetic circular written for the Acme Bank demo tenant. Modelled on Indian
> asset-classification practice; not a reproduction of regulatory text.

---

## 1. Why this circular

To restate, in one place, how a retail account is staged as it ages, when it
becomes non-performing, and what provisioning follows. Field staff are the
first to see an account deteriorate, so the staging must be understood beyond
the risk team.

## 2. Special Mention Accounts (SMA)

An account that is not yet non-performing but shows early stress is staged:

| Stage | Principal or interest overdue for |
|---|---|
| `sma_0` | 1–30 days |
| `sma_1` | 31–60 days |
| `sma_2` | 61–90 days |

SMA staging is **not** a credit judgement — it is arithmetic on the overdue
period, computed daily at day-end. An account cures out of SMA only when the
entire overdue (principal, interest and charges) is cleared.

## 3. Non-performing assets

An account is classified `sub_standard` when it remains overdue for **more than
90 days**. Once classified:

- interest is no longer recognised as income unless realised;
- the account moves to the Recovery desk;
- upgrade to `standard` requires the **entire arrears** to be cleared, not a
  part payment or a fresh promise.

Further ageing (doubtful, loss) is handled centrally by Credit Risk and is
outside the scope of branch and collections staff.

## 4. Borrower-level, not facility-level

Classification is at the **borrower** level. Where a customer holds more than
one facility with the bank, the worst classification applies to all of them.
An officer must not report a customer as `standard` on one loan while another
is `sma_2`.

## 5. Restructuring

- A restructured account is flagged `restructured = true` and remains flagged
  for the life of the loan.
- Restructuring does not by itself upgrade classification; the account holds
  its stage through the specified performance period.
- Restructuring requires Credit Risk approval. Collections may propose, not
  approve.

## 6. Provisioning (indicative)

| Classification | Provision on outstanding |
|---|---|
| `standard` — secured retail | 0.40% |
| `standard` — unsecured retail | 0.75% |
| `sma_0` to `sma_2` | As standard, with watchlist reporting |
| `sub_standard` — secured | 15% |
| `sub_standard` — unsecured | 25% |

Provisioning is computed centrally. Figures here are for understanding what an
ageing account costs the bank, and why bucket movement is chased.

## 7. Reporting cadence

| Report | Frequency | Audience |
|---|---|---|
| Bucket movement | Daily | Collections |
| SMA register | Weekly | Credit Risk, Business Heads |
| NPA position and provisioning | Monthly | ALCO |
| Restructured book | Quarterly | Board Risk Committee |

## 8. What field staff must take from this

1. The difference between DPD 60 and DPD 61 is a stage change, and the
   difference between 90 and 91 is a classification change. **Work an account
   before the boundary, not after.**
2. A part payment that does not clear the full overdue does **not** cure the
   stage, however welcome it is.
3. A customer with one deteriorating facility is a deteriorating customer —
   look across their relationship, not at the one account in front of you.
