# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""Learned-memory reader — clauses, everywhere, no alternative.

docs/clause-memory-graph-plan.md. Four prompt sites read learned memory:

  * the record DECISION rubric      (runtime)
  * image analysis                  (tools_v2_dispatch)
  * document extraction             (tools_v2_dispatch)
  * per-check / fraud-case review   (tools_v2_dispatch)

All four read CLAUSES. The single-summary blob is gone — not deprecated, not
behind a flag, deleted (see the purge in this commit). There is no mode switch
because a switch is a promise that two systems stay in step, and the whole
reason the blob failed was that nobody could see it degrading.

An app without a ``case_signature`` still learns. Its clauses simply carry no
facet scope — they are global within their ``(modality, task_type)`` bucket,
which is already a meaningful scope — and its officers pick from no reason
taxonomy. Declaring a signature buys SCOPING and CODING, not membership.

Facet sourcing differs by site, and this is the one asymmetry worth knowing:

  * **record** — facets come from the case's own columns.
  * **image / document** — the ITEM's subject ("transformer nameplate photo")
    is emitted by the model AFTER it looks, so it cannot scope retrieval. These
    sites inherit the RECORD's facets instead, which the runtime already
    computed for the same case. So an image clause can be scoped to
    "theft claims" but never to "nameplate photos".
  * **api / case** — the tool call names the check up front, so the subject IS
    knowable and DOES scope.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence, Tuple

from case_signature import learning_config, normalize_value, signature_of

log = logging.getLogger(__name__)

#: Facet family carrying an item's subject-type.
ITEM_SUBJECT_FAMILY = "item_subject"

#: Modalities whose subject is known BEFORE the prompt is built.
#:
#: Scoping a clause to ``item_subject:x`` for image/document would mint clauses
#: that can never satisfy the subset test at read time — silent dead knowledge,
#: indistinguishable from "this app has not learned anything yet". The subject
#: still rides on the correction as metadata; it is just not a scope.
SUBJECT_SCOPED_MODALITIES = frozenset({"api", "case"})


def item_subject_facet(subject: Optional[str], modality: Optional[str] = None) -> List[str]:
    """``['item_subject:credit_bureau_check']`` — or [] when unusable.

    Empty for a missing subject (an unscoped clause is honest; one scoped to a
    guessed subject silently mis-fires) and empty for modalities whose subject
    is not knowable at read time."""
    s = (subject or "").strip()
    if not s:
        return []
    if modality is not None and modality not in SUBJECT_SCOPED_MODALITIES:
        return []
    return [f"{ITEM_SUBJECT_FAMILY}:{normalize_value(s)}"]


async def learned_block(
    *,
    app_spec: Any,
    tenant_id: Optional[str],
    app_slug: Optional[str],
    modality: str,
    task_type: str,
    case_facets: Optional[Sequence[str]] = None,
    header: Optional[str] = None,
) -> Tuple[str, List[str], Optional[str]]:
    """Learned memory for one bucket: ``(block, clause_ids, version)``.

    ``version`` is the traceability tag stamped on the resulting finding —
    ``clauses/C-003,C-034``. An auditor must be able to match a disputed finding
    to the exact criteria that were active; bounded to 8 ids but every one is
    resolvable.

    Enrichment path: any failure logs loudly and returns an empty block. A
    degraded learning store must never take an analysis or a decision down.
    """
    if not tenant_id or not app_slug:
        log.warning(
            "[learned-memory] %s/%s: no tenant/app key — learned memory skipped "
            "(folds for this bucket are skipped too, so read and write agree)",
            app_slug, task_type)
        return "", [], None

    try:
        from clause_store import select_clauses

        cfg = learning_config(signature_of(app_spec))
        block, clause_ids = await select_clauses(
            tenant_id=tenant_id, app_slug=app_slug,
            modality=modality, task_type=task_type,
            case_facets=list(case_facets or []),
            budget_words=cfg["clause_budget_words"],
            header=header)
    except Exception as exc:  # noqa: BLE001 — enrichment; loud, never blocks
        log.warning("[learned-memory] clause read failed for %s/%s/%s: %s",
                    app_slug, modality, task_type, exc)
        return "", [], None

    version = ("clauses/" + ",".join(clause_ids[:8])) if clause_ids else "clauses/none"
    return block, clause_ids, version
