"""Feedback fold — officer judgement → the clause-memory evidence ledger.

**The single-summary rubric is GONE.** This module used to maintain one
~1000-word blob per (tenant, app, modality, task_type), rewritten in full by an
LLM on every officer correction, inside the officer's approve/reject request.
That is deleted, not deprecated: no `summary`, no `_resummarize`, no
`rubric_to_prompt`, no governed-edit surface, and the
``smartapp_analysis_rubrics`` collection is dropped (see
``purge_legacy_rubrics.py``). See docs/clause-memory-graph-plan.md §1 for why —
in short, it compressed at WRITE time when it should have selected at READ time,
so every correction degraded the ones before it and no rule could ever be blamed
for a bad recommendation.

What remains is the thin, genuinely shared part:

  * ``rubric_tenant_for_app`` — the CANONICAL bucket key, used by both the fold
    side and the read side. If these two ever disagree the loop silently never
    closes, so there is exactly one function that answers it.
  * ``fold_decision_feedback`` — the record-decision entry point.
  * ``append_correction``     — the item entry point (image/document/api/case).

Both simply write to ``smartapp_corrections``; consolidation turns that evidence
into clauses out of band.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

# The record-level decision bucket. Item buckets use their own
# (modality, task_type) pairs, e.g. ("image", "asset-inspection-defect").
RECORD_MODALITY = "record"
RECORD_TASK_TYPE = "decision"

RECORD_RUBRIC_HEADER = (
    "JUDGEMENTS learned from this app's officers (their reject reasons and "
    "corrections to past recommendations) — their experience, covering what "
    "the rules/SOP do not spell out."
)


def rubric_tenant_for_app(app: Any) -> Optional[str]:
    """The CANONICAL tenant key for an app's learned memory: the APP's own org.

    Both the fold side and the read side MUST derive the bucket tenant through
    this one function — an app-anchored key is the only one stable across
    officers, triggers, minted system JWTs and multi-org hubs (a row-tenant or
    JWT-org key lets folds and reads land in different buckets and the loop
    silently never closes). Accepts the Mongo app document (dict with app_spec
    inside) or an AppSpec object. Returns None when the app carries no org
    identity — callers must SKIP loudly, never coerce to ''."""
    if app is None:
        return None
    if isinstance(app, dict):
        spec = app.get("app_spec") or {}
        return (
            spec.get("org_id") or spec.get("tenant_id")
            or app.get("org_id") or app.get("tenant_id") or None
        )
    return getattr(app, "org_id", None) or getattr(app, "tenant_id", None) or None


async def fold_decision_feedback(
    *,
    tenant_id: Optional[str],
    app_slug: str,
    actor: Optional[str] = None,
    correlation_id: Optional[str] = None,
    reason: Optional[str] = None,
    overrides: Optional[List[Dict[str, Any]]] = None,
    recommendation: Optional[str] = None,
    reason_code: Optional[str] = None,
    contested_fields: Optional[List[str]] = None,
    case_facets: Optional[List[str]] = None,
    signature_version: Optional[int] = None,
    case_ref: Optional[Dict[str, Any]] = None,
    officer_role: Optional[str] = None,
    injected_clause_ids: Optional[List[str]] = None,
    cited_clause_ids: Optional[List[str]] = None,
    overruled_clause_ids: Optional[List[str]] = None,
) -> bool:
    """Record ONE record-level officer judgement as clause-memory evidence.

    A clean approve carries no lesson and must not be recorded (no signal, no
    noise). ``tenant_id`` must come from ``rubric_tenant_for_app`` — a missing
    tenant SKIPS loudly, never a synthesized '' bucket no reader queries.

    FULLY non-raising: any failure is logged and never fails the officer's
    decision — the reason remains durable on the DecisionRecord regardless.
    Returns True when recorded."""
    try:
        if not tenant_id:
            log.warning(
                "[FOLD] decision feedback SKIPPED for app %s — no tenant key "
                "resolvable (the lesson stays on the DecisionRecord only)",
                app_slug,
            )
            return False

        corrected_fields: List[str] = []
        for ov in (overrides or []):
            fromto = (ov or {}).get("override")
            if isinstance(fromto, dict):
                corrected_fields.extend(str(f) for f in fromto)

        reason = (reason or "").strip()
        if not reason and not corrected_fields:
            return False  # clean approve — nothing to learn

        from corrections import record_correction

        cid = await record_correction(
            tenant_id=tenant_id, app_slug=app_slug,
            modality=RECORD_MODALITY, task_type=RECORD_TASK_TYPE,
            event=("override" if corrected_fields else "reject"),
            officer=actor, officer_role=officer_role,
            correlation_id=correlation_id, case_ref=case_ref,
            case_facets=case_facets, signature_version=signature_version,
            reason_code=reason_code,
            contested_fields=contested_fields or corrected_fields or None,
            overrides=overrides, reason_text=reason,
            recommendation=recommendation,
            injected_clause_ids=injected_clause_ids,
            cited_clause_ids=cited_clause_ids,
            overruled_clause_ids=overruled_clause_ids,
        )
        return cid is not None
    except Exception:  # noqa: BLE001 — derived learning store; loud, never fatal
        log.exception(
            "[FOLD] decision feedback failed for %s/%s — the reason is still "
            "durable on the DecisionRecord", tenant_id, app_slug,
        )
        return False


async def append_correction(
    *,
    tenant_id: str,
    app_slug: str,
    modality: str,
    task_type: str,
    reason: str,
    actor: Optional[str] = None,
    item_id: Optional[str] = None,
    subject: Optional[str] = None,
    fields: Optional[List[str]] = None,
    reason_code: Optional[str] = None,
    case_facets: Optional[List[str]] = None,
    signature_version: Optional[int] = None,
) -> None:
    """Record ONE item-level officer reject as clause-memory evidence.

    The entry point for all four item modalities (image / document / api /
    case) — ONE wiring point, so they cannot drift apart.

    ``subject`` is a few-word pointer to WHAT the item was ('transformer
    nameplate photo'). For api/case it becomes a scope facet; for
    image/document it rides as metadata only, because the model does not emit
    it until after it has looked (learned_memory.SUBJECT_SCOPED_MODALITIES).
    Fully non-raising."""
    reason = (reason or "").strip()
    if not reason:
        return
    try:
        from corrections import record_correction
        from learned_memory import item_subject_facet

        facets = list(case_facets or []) + item_subject_facet(subject, modality)
        await record_correction(
            tenant_id=tenant_id, app_slug=app_slug,
            modality=modality, task_type=task_type,
            event="reject", officer=actor, correlation_id=item_id,
            case_facets=facets, signature_version=signature_version,
            reason_code=reason_code, contested_fields=fields,
            reason_text=reason, recommendation=subject,
        )
    except Exception:  # noqa: BLE001 — derived learning store; loud, never fatal
        log.exception(
            "[FOLD] item feedback failed for %s/%s/%s — the officer's "
            "disposition is still durable on the item ledger",
            tenant_id, app_slug, modality,
        )
