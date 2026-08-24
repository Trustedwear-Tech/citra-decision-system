# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""Example: drive the equipment-inspection fraud screen headlessly.

Run:  DA_BASE_URL=... DA_TOKEN=... python examples/fraud_screen.py
The same calls back a desktop UI, an automation, or a mobile app — only the
rendering differs.
"""
import os

from decision_app import DecisionAppClient

client = DecisionAppClient(os.environ["DA_BASE_URL"], token=os.environ["DA_TOKEN"])
SLUG = "equipment-inspection-fraud-screen"


def main() -> None:
    # 1. Self-describing contract — what to call + governance rules.
    contract = client.get_contract(SLUG)
    print("actions:", contract["run_actions"])

    # 2. Your own list, from the queue panel.
    queue = client.get_panel_data(SLUG, "inspections_queue")
    rows = queue.get("rows") or []
    if not rows:
        print("no inspections")
        return
    inspection_id = str(rows[0]["inspection_id"])

    # 3. Agent screens it (fraud tiers run server-side).
    rec = client.recommend(SLUG, contract["run_actions"][0], {"inspection_id": inspection_id})
    print("verdict:", rec.get("decision"), "| why:", rec.get("reasoning"))

    # 4. Per-item review cards: analysed media, bureau/CIBIL `api` checks, and the
    #    fraud `case`. The contract's `item_review_gate` says which must be
    #    dispositioned before Apply — but a `case` (fraud) card is EVIDENCE ONLY:
    #    it never gates and never rejects; the officer decides.
    gate = contract.get("item_review_gate")
    for f in rec.get("item_findings") or []:
        print(f"item {f['item_id']} [{f['modality']}]:", f.get("recommendation"), f.get("fields"))
        if f["modality"] == "case":
            # Fraud: confirm (accept) or dismiss (reject) the flagged concern.
            # Learning feedback only — it does NOT reject the overall decision.
            client.submit_feedback(SLUG, f["item_id"], {
                "modality": "case",
                "task_type": f["item_type"],   # e.g. "fraud-screening"
                "decision": "reject",          # dismissed as a false positive
                "reason": "duplicate is a legitimate re-inspection",
                "subject": f.get("subject") or inspection_id,
            })
        elif gate == "hard":
            # Non-fraud items must be dispositioned before /approve under a hard gate.
            client.submit_feedback(SLUG, f["item_id"], {
                "modality": f["modality"],
                "task_type": f["item_type"],
                "decision": "accept",
                "subject": f.get("subject") or inspection_id,
            })

    # 5. Officer governs the outcome (approve, or override Fail -> Pass). To reject
    #    the whole decision, call approve(..., decision="reject").
    if rec.get("status") == "pending_approval":
        client.approve(
            SLUG, rec["correlation_id"],
            decision="approve",
            overrides=[{"outcome": "Pass"}],   # allow-list enforced server-side
            note="Reviewed evidence; genuine repair.",
        )

    # 6. Audit feeds the self-learning loop.
    print("audit:", client.get_audit(SLUG, rec["correlation_id"]))

    # Optional: a read-only fraud calibration report. Raises 409 fraud_not_enabled
    # if the app's ontology does not turn fraud detection on.
    try:
        report = client.calibrate_fraud(SLUG)
        print("fraud signals by reject-rate:", report.get("per_signal_hit_rate"))
    except Exception as e:  # noqa: BLE001 - example only
        print("fraud calibration unavailable:", e)

    # 7. A record's photo, streamed via the MCP (never a storage URL).
    photo = client.fetch_media(SLUG, "ds_inspections",
                               key_field="inspection_id", key=inspection_id, col="defect_photo_url")
    print("photo bytes:", len(photo))


if __name__ == "__main__":
    main()
