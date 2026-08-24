// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

/**
 * Example: drive the equipment-inspection fraud screen from a custom UI.
 *
 * Run: set DA_BASE_URL + DA_TOKEN, then `tsx examples/fraud-screen.ts`.
 * The same flow works from a React Native screen or an Electron window — only
 * the rendering differs; the calls are identical.
 */
import { DecisionAppClient } from "../src/index";

const client = new DecisionAppClient({
  baseUrl: process.env.DA_BASE_URL!, // e.g. https://apps.citra-ai.com/api
  token: process.env.DA_TOKEN!, // the end-user's JWT
});

const SLUG = "equipment-inspection-fraud-screen";

async function main() {
  // 1. Read the self-describing contract — what to call, with what body.
  const contract = await client.getContract(SLUG);
  console.log("actions:", contract.run_actions);
  console.log("governance:", contract.governance);

  // 2. Render your own list from the queue panel's rows.
  const queue = await client.getPanelData(SLUG, "inspections_queue");
  const first = queue.rows?.[0];
  if (!first) return console.log("no inspections");
  const inspectionId = String(first.inspection_id);

  // 3. Ask the agent to screen it (fraud tiers run server-side inside the agent).
  const rec = await client.recommend(SLUG, {
    action: contract.run_actions[0] ?? "screen_inspection",
    inputs: { inspection_id: inspectionId },
  });
  console.log("verdict:", rec.decision, "| why:", rec.reasoning);
  console.log("staged writes:", rec.planned_writes);

  // 4. Per-item review cards: analysed media, bureau/CIBIL `api` checks, and the
  //    fraud `case`. Render each; the officer dispositions it. The contract's
  //    `item_review_gate` says which MUST be reviewed before Apply — but a
  //    `case` (fraud) card is EVIDENCE ONLY: it never gates and never rejects.
  for (const f of rec.item_findings ?? []) {
    console.log(`item ${f.item_id} [${f.modality}]:`, f.recommendation, f.fields);
    if (f.modality === "case") {
      // Fraud: confirm (accept) or dismiss (reject) the flagged concern. This is
      // learning feedback only — it does NOT reject the overall decision.
      await client.submitFeedback(SLUG, f.item_id, {
        modality: "case",
        task_type: f.item_type, // e.g. "fraud-screening"
        decision: "reject", // officer dismissed the flag as a false positive
        reason: "duplicate is a legitimate re-inspection",
        subject: f.subject ?? inspectionId,
      });
    } else if (contract.item_review_gate === "hard") {
      // Non-fraud items must be dispositioned before /approve under a hard gate.
      await client.submitFeedback(SLUG, f.item_id, {
        modality: f.modality,
        task_type: f.item_type,
        decision: "accept",
        subject: f.subject ?? inspectionId,
      });
    }
  }

  // 5. Officer governs the outcome. Approve as-is, OR override an editable field
  //    (e.g. flip the AI's Fail → Pass) before the governed write. To reject the
  //    whole decision, call approve(..., decision: "reject").
  if (rec.status === "pending_approval") {
    await client.approve(SLUG, rec.correlation_id, {
      decision: "approve",
      overrides: [{ outcome: "Pass" }], // aligned to planned_writes[0]; allow-list enforced
      note: "Reviewed evidence; genuine repair.",
    });
  }

  // 6. Audit feeds the self-learning loop.
  const audit = await client.getAudit(SLUG, rec.correlation_id);
  console.log("audit:", audit);

  // Optional: a read-only fraud calibration report (how often each signal
  // coincided with an officer reject). 409 fraud_not_enabled if fraud is off.
  try {
    const report = await client.calibrateFraud(SLUG);
    console.log("fraud signals by reject-rate:", report.per_signal_hit_rate);
  } catch (e) {
    console.log("fraud calibration unavailable:", (e as Error).message);
  }

  // 7. A record's photo/PDF, streamed through the MCP (never a storage URL).
  const photo = await client.fetchMedia(SLUG, "ds_inspections", {
    keyField: "inspection_id",
    key: inspectionId,
    col: "defect_photo_url",
  });
  console.log("photo bytes:", photo.size);
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
