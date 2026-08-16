<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: BUSL-1.1

  Licensed under the Business Source License 1.1. Non-production use is granted;
  production use requires a commercial licence until the Change Date, after
  which this file converts to Apache-2.0. See LICENSE at the repository root.
-->

# Collections and Recovery SOP — Acme Bank & Insurance Ltd

**Document type:** SOP · **Owner:** Collections, Retail Assets
**Classification:** Internal · **Version:** 6.1 · **Effective:** 01 April 2026

> Synthetic SOP written for the Acme Bank demo tenant.

---

## 1. Buckets

Accounts are worked by days past due (DPD):

| Bucket | DPD | Owner |
|---|---|---|
| `0` | Current | Branch / service |
| `1-30` | 1–30 | Tele-calling |
| `31-60` | 31–60 | Field collections |
| `61-90` | 61–90 | Field collections + Collections Manager review |
| `90+` | 91+ | Recovery / legal |

Bucket is recalculated daily. An account that cures moves back a bucket only
after the overdue is fully cleared, not on part payment.

## 2. Contact strategy by bucket

| Bucket | Channel sequence | Frequency |
|---|---|---|
| `1-30` | SMS → WhatsApp → call | Up to 3 contacts/week |
| `31-60` | Call → field visit | Up to 4 contacts/week, at most 1 visit |
| `61-90` | Field visit → Collections Manager call | Up to 5 contacts/week, at most 2 visits |
| `90+` | Legal notice → settlement discussion | As directed by Recovery |

**Call window is 08:00–19:00.** No contact on a customer's declared day of
religious observance where recorded. Conduct rules in the **Fair Practices
Code** apply to every contact and to every agency acting for the bank.

## 3. Recording the outcome

Every attempt is logged with an outcome:

| Outcome | Meaning |
|---|---|
| `ptp` | Customer promised to pay — record date and amount |
| `paid` | Payment received or confirmed |
| `no_contact` | Not reachable |
| `dispute` | Customer disputes the amount or the account |
| `refused` | Contact made, payment refused |
| `wrong_number` | Contact details incorrect — route for data correction |

An attempt that is not logged did not happen. Bucket movement without logged
activity is a supervisory exception.

## 4. Promise to pay (PTP)

1. A PTP must have a **date and an amount**. "Will pay soon" is not a PTP;
   log it as `no_contact` or `refused` as appropriate.
2. A PTP date more than 15 days out requires Collections Manager approval.
3. Follow up on the PTP date itself, not before.
4. When a PTP is honoured, mark `ptp_kept = true`.
5. **When a PTP is broken, mark `ptp_kept = false` and escalate the account one
   contact tier.** A broken promise is a materially different situation from a
   first contact and must not be worked as though it were new.
6. Two broken PTPs on the same account go to the Collections Manager before any
   further promise is accepted.

## 5. Disputes

On a `dispute` outcome:

- stop collection activity on the disputed amount immediately;
- record what is disputed and route to the servicing team within 1 working day;
- resume only after the dispute is resolved and the customer is informed.

Collecting against a live dispute is a Code breach.

## 6. Hardship

Where a customer demonstrates genuine hardship — job loss, hospitalisation,
business disruption — the officer may propose:

- a short payment holiday (maximum 3 months, Collections Manager approval),
- part-payment with a written schedule, or
- restructuring, referred to Credit Risk.

Hardship cases are worked with reduced contact frequency. Record the hardship
and the arrangement; do not simply pause and let the account drift.

## 7. Field visits

- Visit only addresses on record. Trace activity for untraceable customers is
  `skip_trace` and is handled by the tracing desk, not by field agents.
- Carry and show authorisation. Never discuss the debt with anyone other than
  the customer or a co-borrower.
- Never collect cash. Payments are made only through bank channels; the officer
  provides the payment link or challan.
- Log the visit the same day, including a nil outcome.

## 8. Legal and settlement

- Legal notice is issued only from the `90+` bucket, after Recovery review.
- Settlement (waiver of part of the dues) requires: written customer request,
  Recovery Head approval within delegated authority, and a settlement letter
  stating the bureau reporting consequence.
- The customer is told, in writing, that a settled account is reported as
  settled and affects future credit.

## 9. Prioritisation

Where the day's worklist exceeds capacity, work in this order:

1. Accounts with a PTP falling due today.
2. Accounts about to roll into a worse bucket (DPD 28–30, 58–60, 88–90).
3. Highest overdue amount within the worst bucket.
4. Accounts with no contact attempt in the last 7 days.

Accounts flagged `dispute` or `legal` are excluded from routine worklists.
