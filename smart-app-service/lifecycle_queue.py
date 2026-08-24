# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""User-lifecycle consumer — resource inheritance when a user is deactivated.

WHY THIS EXISTS HERE
--------------------
Citra-User-Service enqueues ``user.deactivated`` and ``user.delete_applied``
jobs onto the ``default`` queue (see its ``src/services/workerQueue.js``). Their
only consumer used to be Citra-Worker, which left with the workflow automation
system on 2026-08-08. From then until now those jobs were enqueued and NEVER
RUN: the admin API answered "user deactivated; resource inheritance job
enqueued" while nothing ran it, so a departing user's apps were never reassigned
and a GDPR deletion request was never applied. Silent success is the worst
failure mode we have, so this closes it.

WHY IT IS SO MUCH SMALLER THAN THE HANDLER IT REPLACES
------------------------------------------------------
Citra-Worker's ``inheritance_handlers.py`` was 847 lines because it walked six
resource kinds. Five of them no longer exist in this product:

    Workflows          -> the workflow engine left the Decision System
    skills             -> skill-service retired
    presentations      -> left with the presentation product
    printables         -> ditto
    composer_reports   -> ditto
    smartapp_apps      -> STILL HERE. This service owns it.

So the honest port is not "move 847 lines"; it is "handle the one collection
that survives, in the service that already owns it". Porting the rest would
have meant carrying inheritance logic for collections nothing writes.

POLICY
------
Each app records an ``inheritance_policy`` at creation time:

    archive             -> lifecycle_stage="archived"   (default; safe)
    transfer_to_sa      -> owner becomes inheritance_target (a service account)
    transfer_to_org     -> owner becomes the org
    delete_after_grace  -> archive now, schedule deletion in N days

Every transition appends a ``lifecycle_audit`` entry so the trail stays
reconstructable and an admin can reverse an individual case when an inheritance
target turns out to be wrong.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

import citra_queue as cq

logger = logging.getLogger(__name__)

QUEUE = cq.DEFAULT_QUEUE          # "default" — where user-service enqueues
HANDLERS = {"user.deactivated", "user.delete_applied"}

APPS_COLLECTION = "smartapp_apps"
HANDOFF_COLLECTION = "HandoffReports"

DEFAULT_POLICY = "archive"
DEFAULT_GRACE_DAYS = 30


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _apply_policy(db, app: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, Any]:
    """Apply one app's inheritance policy. Returns a report row."""
    policy = (app.get("inheritance_policy") or DEFAULT_POLICY).strip()
    target = app.get("inheritance_target") or ""
    org_id = payload.get("org_id") or app.get("org_id") or ""
    slug = app.get("slug") or str(app.get("_id"))

    update: Dict[str, Any] = {}
    note = ""

    if policy == "transfer_to_sa" and target:
        update = {"owner_type": "service_account", "owner_id": target}
        note = f"transferred to service account {target}"
    elif policy == "transfer_to_org" and org_id:
        update = {"owner_type": "org", "owner_id": org_id}
        note = f"transferred to org {org_id}"
    elif policy == "delete_after_grace":
        grace = int(app.get("inheritance_grace_days") or DEFAULT_GRACE_DAYS)
        update = {
            "lifecycle_stage": "archived",
            "scheduled_deletion_at": (_now() + timedelta(days=grace)).isoformat(),
        }
        note = f"archived; deletion scheduled in {grace} day(s)"
    else:
        # Unknown policy, or transfer with no usable target: fall back to the
        # SAFE option and say so in the report. Never drop the resource on the
        # floor, and never guess a new owner.
        if policy in ("transfer_to_sa", "transfer_to_org"):
            note = f"policy {policy!r} had no usable target — archived instead"
        elif policy != DEFAULT_POLICY:
            note = f"unknown policy {policy!r} — archived instead"
        else:
            note = "archived"
        policy = DEFAULT_POLICY
        update = {"lifecycle_stage": "archived"}

    audit = {
        "action": "user_deactivated_inheritance",
        "at": _now().isoformat(),
        "actor": payload.get("deactivated_by") or "system",
        "previous_owner": app.get("owner_id"),
        "policy": policy,
        "note": note,
    }
    await db[APPS_COLLECTION].update_one(
        {"_id": app["_id"]},
        {"$set": update, "$push": {"lifecycle_audit": audit}},
    )
    return {"slug": slug, "policy": policy, "note": note}


async def handle_user_deactivated(payload: Dict[str, Any], db) -> Dict[str, Any]:
    """Walk every app owned by the departing user and apply its policy."""
    user_id = (payload.get("user_id") or payload.get("email") or "").strip()
    if not user_id:
        raise cq.JobPermanentFailure("user.deactivated payload has no user_id/email")

    cursor = db[APPS_COLLECTION].find({"owner_id": user_id})
    rows: List[Dict[str, Any]] = []
    async for app in cursor:
        rows.append(await _apply_policy(db, app, payload))

    report = {
        "_id": str(uuid.uuid4()),
        "kind": "user_deactivated",
        "user_id": user_id,
        "org_id": payload.get("org_id"),
        "at": _now().isoformat(),
        "resources": rows,
    }
    await db[HANDOFF_COLLECTION].insert_one(report)
    logger.info("user.deactivated %s — %d app(s) inherited", user_id, len(rows))
    return {"inherited": len(rows)}


async def handle_user_delete_applied(payload: Dict[str, Any], db) -> Dict[str, Any]:
    """Hard-delete the user's apps after an approved deletion request.

    INTERACTION WITH INHERITANCE — subtle, and easy to "fix" wrongly later.
    This matches on ``owner_id`` at the time it runs. If ``user.deactivated``
    already transferred an app to a service account or to the org, that app no
    longer matches and is CORRECTLY left alone: it belongs to the organisation
    now, and a personal-data erasure request must not destroy org property.

    So a deactivate-then-delete sequence deletes FEWER apps than the
    deactivation touched, and that difference is the transferred ones. A test
    asserting the two counts are equal is asserting the wrong thing.
    """
    user_id = (payload.get("user_id") or payload.get("email") or "").strip()
    if not user_id:
        raise cq.JobPermanentFailure("user.delete_applied payload has no user_id/email")

    res = await db[APPS_COLLECTION].delete_many({"owner_id": user_id})
    await db[HANDOFF_COLLECTION].insert_one({
        "_id": str(uuid.uuid4()),
        "kind": "user_delete_applied",
        "user_id": user_id,
        "org_id": payload.get("org_id"),
        "at": _now().isoformat(),
        "deleted": res.deleted_count,
    })
    logger.info("user.delete_applied %s — %d app(s) deleted", user_id, res.deleted_count)
    return {"deleted": res.deleted_count}


async def _process(job, db) -> None:
    fn = {
        "user.deactivated": handle_user_deactivated,
        "user.delete_applied": handle_user_delete_applied,
    }[job.handler]
    try:
        result = await fn(job.payload or {}, db)
        await cq.mark_done(job, result)
    except cq.JobPermanentFailure as e:
        logger.error("lifecycle job %s permanently failed: %s", job.id, e)
        await cq.mark_failed(job, str(e), permanent=True)
    except Exception as e:  # noqa: BLE001
        # Transient (Mongo blip, etc.): let the queue redeliver rather than
        # acking a job that did not run.
        logger.error("lifecycle job %s failed: %s", job.id, e, exc_info=True)
        await cq.mark_failed(job, str(e), permanent=False)


async def run_lifecycle_consumer(
    stop_event: asyncio.Event,
    db,
    *,
    reclaim_interval_seconds: int = 300,
) -> None:
    """Drain + reclaim on the ``default`` queue, for lifecycle handlers only.

    NOTE ON THE FOREIGN-HANDLER RULE: the sibling trigger consumer dead-letters
    any handler it does not own, because it is the sole consumer of the
    ``smartapp`` queue. This consumer must NOT do that. ``default`` is a shared
    queue and other producers may enqueue handlers we know nothing about;
    dead-lettering those would destroy another service's work. We leave
    unrecognised jobs unacked so their real consumer can claim them.
    """
    consumer = cq.default_consumer_name()
    logger.info("🟢 lifecycle consumer active (queue=%s, consumer=%s)", QUEUE, consumer)

    async def _drain() -> None:
        while not stop_event.is_set():
            try:
                job = await cq.consume_one([QUEUE], block_seconds=5, consumer=consumer)
                if job is None:
                    continue
                if job.handler not in HANDLERS:
                    logger.debug("lifecycle consumer ignoring handler %r (job %s)",
                                 job.handler, job.id)
                    continue
                await _process(job, db)
            except Exception as e:  # noqa: BLE001
                logger.error("lifecycle drain iteration failed: %s", e, exc_info=True)
                await asyncio.sleep(1.0)

    async def _reclaim() -> None:
        # claim_stale RETURNS the reclaimed jobs — they still need processing.
        # Treating it as fire-and-forget would silently drop every job abandoned
        # by a crashed worker, which is the exact failure this consumer exists
        # to end.
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=reclaim_interval_seconds)
                break  # stopped
            except asyncio.TimeoutError:
                pass
            try:
                for job in await cq.claim_stale([QUEUE], consumer=consumer):
                    if stop_event.is_set():
                        break
                    if job.handler not in HANDLERS:
                        continue
                    logger.warning("♻️  re-processing reclaimed lifecycle job %s (%s)",
                                   job.id, job.handler)
                    await _process(job, db)
            except Exception as e:  # noqa: BLE001 — log loud; the loop continues
                logger.error("lifecycle reclaim failed: %s", e, exc_info=True)

    try:
        await asyncio.gather(_drain(), _reclaim())
    except Exception:  # noqa: BLE001 — the consumer must never die silently
        logger.error("lifecycle consumer exited abnormally", exc_info=True)
        raise
