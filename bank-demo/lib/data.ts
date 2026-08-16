// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

/**
 * Acme Bank's own book of business. Static on purpose — this is a stand-in for
 * the bank's core systems, not a database.
 *
 * The LENDING records are deliberately the real ones from
 * `loan_origination.loan_applications` in the acme-bank demo tenant, because
 * the Citra card reads that source live: the id this screen shows has to
 * resolve on the Citra side or the card renders an empty state. Collections and
 * Claims are illustrative only — they exist so the demo looks like a bank
 * rather than a single-screen harness.
 */

export type Application = {
  id: string;
  applicant: string;
  product: string;
  amount: number;
  foir: number;
  channel: string;
  proof: string;
  branch: string;
  received: string;
  status: string;
};

/** Ids that MUST exist in loan_origination.loan_applications. */
export const APPLICATIONS: Application[] = [
  {
    id: "LAN-2026-000005", applicant: "R. Iyer", product: "Home",
    amount: 4422000, foir: 67.21, channel: "DSA", proof: "Payslip",
    branch: "BR-0023 · Mumbai", received: "14 Jun 2024", status: "Under review",
  },
  {
    id: "LAN-2026-000003", applicant: "S. Nair", product: "Personal",
    amount: 850000, foir: 41.8, channel: "Branch", proof: "ITR",
    branch: "BR-0011 · Pune", received: "12 Jun 2024", status: "Under review",
  },
  {
    id: "LAN-2026-000001", applicant: "K. Desai", product: "Business",
    amount: 2750000, foir: 55.4, channel: "Digital", proof: "Bank statement",
    branch: "BR-0045 · Ahmedabad", received: "09 Jun 2024", status: "Under review",
  },
];

export type Collection = {
  id: string;
  borrower: string;
  bucket: string;
  outstanding: number;
  emisMissed: number;
  lastContact: string;
  promiseToPay: string | null;
};

export const COLLECTIONS: Collection[] = [
  { id: "COL-88213", borrower: "M. Fernandes", bucket: "31–60 DPD", outstanding: 184500, emisMissed: 2, lastContact: "02 Jul 2024", promiseToPay: "18 Jul 2024" },
  { id: "COL-88240", borrower: "A. Bhattacharya", bucket: "61–90 DPD", outstanding: 421000, emisMissed: 3, lastContact: "28 Jun 2024", promiseToPay: null },
  { id: "COL-88301", borrower: "P. Reddy", bucket: "1–30 DPD", outstanding: 62300, emisMissed: 1, lastContact: "05 Jul 2024", promiseToPay: "12 Jul 2024" },
];

export type Claim = {
  id: string;
  policy: string;
  insured: string;
  vehicle: string;
  incident: string;
  estimate: number;
  garage: string;
  status: string;
};

export const CLAIMS: Claim[] = [
  { id: "CLM-2026-4471", policy: "MOT-9930-22", insured: "V. Menon", vehicle: "Hyundai Creta 2021", incident: "Rear-end collision, Bandra", estimate: 138400, garage: "AutoWorks Andheri", status: "Surveyor assigned" },
  { id: "CLM-2026-4488", policy: "MOT-7741-19", insured: "D. Kulkarni", vehicle: "Maruti Baleno 2019", incident: "Hail damage, Nashik", estimate: 46700, garage: "Sai Motors", status: "Awaiting estimate" },
  { id: "CLM-2026-4502", policy: "MOT-5512-23", insured: "T. Joseph", vehicle: "Tata Nexon EV 2023", incident: "Kerb strike, Kochi", estimate: 91250, garage: "EV Care Kochi", status: "Under review" },
];

export const inr = (n: number) =>
  "₹" + n.toLocaleString("en-IN", { maximumFractionDigits: 0 });
