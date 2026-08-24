# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""Author `case_signature` for the acme-power Decision Apps.

Clause memory works without a signature — clauses just come out unscoped. This
adds the two things a signature actually buys:

  * FACETS   — so a clause learned about high-value theft cases does not fire
               on a windshield-grade complaint;
  * REASON CODES — so officer rejects can CLUSTER. Consolidation refuses to
               author a clause from an uncoded cluster, so without a taxonomy
               an app accumulates evidence forever and never forms a rule.

Every facet below references a column that exists in
`demo-data/tenants/acme-power/mcp/sources.json`. Enum `values` are copied from
those columns' declared vocabularies — this is the point made in the plan: the
builder is not designing a taxonomy, they are marking which existing columns
are decision-relevant.

Reason codes are authored per app from what its officers plausibly dispute, not
from a generic list. A taxonomy nobody's rejections fit is worse than none: it
pushes everything into `other`, which never forms clauses.

Usage:
    python migrate_acme_case_signatures.py --dry-run
    python migrate_acme_case_signatures.py --apply
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
from typing import Any, Dict

log = logging.getLogger("migrate_acme_case_signatures")

# ---------------------------------------------------------------------------
# Signatures
# ---------------------------------------------------------------------------

COMPLAINT_ROUTING: Dict[str, Any] = {
    "version": 1,
    "facets": [
        # field_operations.complaints — every enum below is the column's own.
        {"family": "category", "kind": "enum", "from_column": "category",
         "values": ["no_power", "billing_issue", "meter_fault",
                    "connection_request", "theft_report", "voltage_low",
                    "transformer_issue", "other"]},
        {"family": "priority", "kind": "enum", "from_column": "priority",
         "values": ["low", "medium", "high", "urgent"]},
        {"family": "channel", "kind": "enum", "from_column": "channel",
         "values": ["care_line", "app", "portal", "walk_in", "text_sms"]},
        {"family": "status", "kind": "enum", "from_column": "status",
         "values": ["new", "routed", "in_progress", "resolved", "escalated"]},
        # Days from registration to SLA breach — a routing decision on a
        # complaint that is already 2 hours from breach is a different decision.
        {"family": "sla_window", "kind": "age_band",
         "from_columns": ["registered_at", "sla_due_at"], "edges": [1, 3]},
        {"family": "assigned", "kind": "presence", "from_column": "assigned_to"},
    ],
    "reason_codes": [
        {"code": "wrong_department", "label": "Routed to the wrong team",
         "hint": "The category was read correctly but the owning team is wrong."},
        {"code": "wrong_priority", "label": "Priority is wrong"},
        {"code": "miscategorised", "label": "Category misread from the text"},
        {"code": "sla_unrealistic", "label": "SLA target is not achievable"},
        {"code": "duplicate_complaint", "label": "Duplicate of an open complaint"},
        {"code": "needs_field_visit", "label": "Cannot be resolved remotely"},
        {"code": "data_stale_or_wrong", "label": "Source data stale or wrong"},
        {"code": "other", "label": "Something else"},
    ],
    "learning": {"promotion_min_officers": 3, "clause_budget_words": 1000},
}

INSPECTION_TRIAGE: Dict[str, Any] = {
    "version": 1,
    "facets": [
        # field_operations.equipment_inspections
        {"family": "triage_status", "kind": "enum", "from_column": "status",
         "values": ["pass", "repair", "fail"]},
        {"family": "defect_photo", "kind": "presence",
         "from_column": "defect_photo_url"},
        {"family": "inspection_report", "kind": "presence",
         "from_column": "inspection_report_url"},
        # These are the facets the IMAGE and DOCUMENT tools inherit — an image
        # clause cannot scope on its own subject (the model names that only
        # after looking), so the case context is its only routable scope.
        {"family": "exif_conflict", "kind": "signal",
         "signal_id": "exif_capture_before_claim"},
        {"family": "gps_conflict", "kind": "signal",
         "signal_id": "exif_gps_far_from_claim"},
    ],
    "reason_codes": [
        {"code": "severity_wrong", "label": "Severity called wrong",
         "hint": "e.g. flagged fail when it is contained and repairable."},
        {"code": "wrong_component", "label": "Wrong component identified"},
        {"code": "image_unreadable", "label": "Photo too poor to judge"},
        {"code": "evidence_insufficient", "label": "Not enough evidence to decide"},
        {"code": "report_contradicts_photo", "label": "Report and photo disagree"},
        {"code": "normal_wear", "label": "Normal wear, not a defect"},
        {"code": "data_stale_or_wrong", "label": "Source data stale or wrong"},
        {"code": "other", "label": "Something else"},
    ],
    "learning": {"promotion_min_officers": 3, "clause_budget_words": 1000},
}

THEFT_TRIAGE: Dict[str, Any] = {
    "version": 1,
    "facets": [
        # field_operations.theft_cases + inspections
        {"family": "recovery_status", "kind": "enum",
         "from_column": "recovery_status",
         "values": ["pending", "under_recovery", "recovered", "written_off",
                    "disputed"]},
        {"family": "amount_band", "kind": "band", "from_column": "assessed_amount",
         "edges": [1000, 25000, 100000]},
        {"family": "criminal_referral", "kind": "presence",
         "from_column": "fir_reference"},
        {"family": "finding", "kind": "enum", "from_column": "finding",
         "values": ["normal", "tampering_visible", "bypass_connection",
                    "meter_reversed", "unauthorised_load"]},
        {"family": "evidence_photo", "kind": "presence",
         "from_column": "evidence_photo_url"},
        {"family": "entity_ring", "kind": "signal", "signal_id": "shared_identifier"},
        {"family": "repeat_offender", "kind": "signal",
         "signal_id": "resubmitted_after_rejection"},
    ],
    "reason_codes": [
        {"code": "evidence_insufficient", "label": "Not enough evidence to confirm"},
        {"code": "amount_incorrect", "label": "Assessed amount is wrong"},
        {"code": "referral_required", "label": "Needs a criminal referral first"},
        {"code": "authority_limit", "label": "Above my authority"},
        {"code": "fraud_false_positive", "label": "Fraud flag was wrong"},
        {"code": "fraud_missed", "label": "Fraud indicator was missed"},
        {"code": "data_stale_or_wrong", "label": "Source data stale or wrong"},
        {"code": "other", "label": "Something else"},
    ],
    "learning": {"promotion_min_officers": 3, "clause_budget_words": 1000},
}

RECOVERY_TRACKER: Dict[str, Any] = {
    "version": 1,
    "facets": [
        {"family": "recovery_status", "kind": "enum",
         "from_column": "recovery_status",
         "values": ["pending", "under_recovery", "recovered", "written_off",
                    "disputed"]},
        {"family": "amount_band", "kind": "band", "from_column": "assessed_amount",
         "edges": [1000, 25000, 100000]},
        {"family": "criminal_referral", "kind": "presence",
         "from_column": "fir_reference"},
        {"family": "case_age", "kind": "age_band",
         "from_columns": ["detection_date", "closed_date"], "edges": [30, 180]},
    ],
    "reason_codes": [
        {"code": "writeoff_premature", "label": "Too early to write off"},
        {"code": "amount_incorrect", "label": "Assessed amount is wrong"},
        {"code": "authority_limit", "label": "Above my authority"},
        {"code": "escalation_needed", "label": "Needs escalation, not a reminder"},
        {"code": "disputed_by_consumer", "label": "Consumer dispute is open"},
        {"code": "data_stale_or_wrong", "label": "Source data stale or wrong"},
        {"code": "other", "label": "Something else"},
    ],
    "learning": {"promotion_min_officers": 3, "clause_budget_words": 1000},
}

#: Apps deliberately NOT given a signature:
#:   * acme-power-cmd-daily-briefing — a briefing generator, not a per-case
#:     decision. There is no "case" to give a signature.
#:   * acme-power-dt-failure-response — its dataset bindings are not settled in
#:     dev; guessing columns would publish facets that emit __unknown forever,
#:     which is worse than none (the app still learns, just unscoped).
SIGNATURES: Dict[str, Dict[str, Any]] = {
    "acme-power-complaint-auto-routing": COMPLAINT_ROUTING,
    "acme-power-inspection-triage": INSPECTION_TRIAGE,
    "acme-power-theft-triage": THEFT_TRIAGE,
    "acme-power-recovery-tracker": RECOVERY_TRACKER,
}


async def migrate(*, apply: bool) -> Dict[str, Any]:
    import main
    from models import CaseSignature

    db = main._db
    stats = {"checked": 0, "validated": 0, "written": 0, "skipped": [],
             "invalid": []}

    for slug, sig in SIGNATURES.items():
        stats["checked"] += 1
        doc = await db["smartapp_apps"].find_one({"slug": slug}, {"_id": 0, "slug": 1})
        if not doc:
            stats["skipped"].append(f"{slug}: not found in this environment")
            continue
        # Validate through the real model — a signature that cannot round-trip
        # would 422 the app's NEXT publish, turning a memory upgrade into an
        # outage for whoever republishes it.
        try:
            CaseSignature.model_validate(sig)
            stats["validated"] += 1
        except Exception as e:  # noqa: BLE001
            stats["invalid"].append(f"{slug}: {e}")
            continue
        if apply:
            await db["smartapp_apps"].update_one(
                {"slug": slug}, {"$set": {"app_spec.case_signature": sig}})
            stats["written"] += 1
            log.info("[MIGRATE] %s ← case_signature v%s (%d facets, %d codes)",
                     slug, sig["version"], len(sig["facets"]),
                     len(sig["reason_codes"]))
    return stats


async def _main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="write (default: dry run)")
    ap.add_argument("--env", choices=["prod", "test"], default="prod")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    import main
    from env_context import set_current_env

    set_current_env(args.env)
    if main._db is None:
        from motor.motor_asyncio import AsyncIOMotorClient

        client = AsyncIOMotorClient(os.environ["MONGO_URI"])
        main._db = client[os.environ.get("MONGO_DB", "dev")]

    stats = await migrate(apply=args.apply)
    print(json.dumps(stats, indent=2))
    if not args.apply:
        print("\nDRY RUN — pass --apply to write")
    return 1 if stats["invalid"] else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
