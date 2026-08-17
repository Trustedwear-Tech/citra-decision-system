# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""Case signature — deterministic facet derivation (Phase B of
docs/clause-memory-graph-plan.md).

A case's *signature* is a small set of closed-vocabulary tokens describing what
KIND of case it is: ``loss_type:theft``, ``amount_band:25000_100000``,
``police_report:absent``. Clauses are SCOPED to facet sets and fire only when
their scope is a subset of the case's facets (plan §6), so the signature is the
routing key for the whole clause-memory design.

Two properties this module exists to guarantee:

  * **Deterministic.** Every facet is a pure function of a column value, a band
    table, a null check, a date difference, or a signal that already ran. No
    LLM, no inference, no tokens spent. That is what makes the clause graph a
    routing table rather than a guess.
  * **Loud on drift.** An enum value the ontology did not declare becomes
    ``family:__unknown`` — a legal, queryable token that NO clause may ever be
    scoped to, so it can never mis-route, but it IS countable. Ontology drift
    becomes a number an admin sees (plan §4.3, §19.2) instead of a silent
    mis-match.

The signature is computed once per run and FROZEN onto the staging row, so the
correction recorded later carries the signature the model actually saw — a
subsequent ontology edit must never rewrite what past cases looked like.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

from fraud_checks import (
    SIGNAL_CAMERA_FLIP,
    SIGNAL_CAPTURE_BEFORE,
    SIGNAL_DATE_RULE,
    SIGNAL_GPS_FAR,
    SIGNAL_PAY_AMOUNT,
    SIGNAL_PAY_DATE,
    SIGNAL_PAY_NOT_FOUND,
    SIGNAL_PAY_PARTY,
    SIGNAL_PHOTOSET_TIMING,
    SIGNAL_STATEMENT_BREAK,
    SIGNAL_VERIFY_MISMATCH,
    SIGNAL_VERIFY_NOT_FOUND,
)

log = logging.getLogger(__name__)

#: The CLOSED set a ``kind="signal"`` facet may reference. Imported from the
#: emitting modules rather than re-typed, so a renamed signal breaks the import
#: instead of silently never firing. entity_links' three are string literals at
#: its emission sites (entity_links.py:236/:250/:365) and have no constants to
#: import; if that changes, import them the same way.
ENTITY_SIGNALS = ("shared_identifier", "identity_cardinality",
                  "resubmitted_after_rejection")

PLATFORM_SIGNAL_IDS: frozenset = frozenset({
    SIGNAL_CAPTURE_BEFORE, SIGNAL_GPS_FAR, SIGNAL_CAMERA_FLIP,
    SIGNAL_PAY_NOT_FOUND, SIGNAL_PAY_AMOUNT, SIGNAL_PAY_DATE, SIGNAL_PAY_PARTY,
    SIGNAL_PHOTOSET_TIMING, SIGNAL_VERIFY_NOT_FOUND, SIGNAL_VERIFY_MISMATCH,
    SIGNAL_DATE_RULE, SIGNAL_STATEMENT_BREAK,
    *ENTITY_SIGNALS,
})

FACET_KINDS = ("enum", "band", "presence", "age_band", "signal")

#: Reserved value marking a case whose column carried an UNDECLARED enum value.
#: Legal to emit, illegal to scope a clause to (enforced in clause_store).
UNKNOWN = "__unknown"

_FAMILY_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_VALUE_SAFE_RE = re.compile(r"[^a-z0-9_.<>+-]+")
_VALUE_MAX = 40


# NOTE: there was a ``CaseSignatureError`` here, raised when a facet's value
# could not be used. It is gone rather than kept-and-unused, because a live
# exception name invites `except CaseSignatureError` around derive_facets that
# can never fire, and implies a fatal path that no longer exists.
#
# Derivation is ALL-OR-NOTHING — the caller catches broadly — so raising for one
# facet returned ``case_facets: []`` for the whole case and killed clause
# retrieval outright. Every per-facet problem now degrades to ``__unknown``:
# logged, reported in the returned ``unknown`` list, and unable to match any
# clause, which is the safe direction. Nothing in derivation is fatal.


# ---------------------------------------------------------------------------
# Token helpers
# ---------------------------------------------------------------------------


def normalize_value(v: Any) -> str:
    """Canonicalise a raw column value into the token's value half."""
    s = str(v if v is not None else "").strip().lower()
    s = s.replace(" ", "_")
    s = _VALUE_SAFE_RE.sub("_", s).strip("_")
    return s[:_VALUE_MAX] or UNKNOWN


def token(family: str, value: Any) -> str:
    return f"{family}:{normalize_value(value)}"


def unknown_token(family: str) -> str:
    """The drift token, built WITHOUT normalization.

    ``normalize_value`` strips leading underscores, so routing UNKNOWN through
    ``token()`` would yield ``family:unknown`` — indistinguishable from a real
    column value of "unknown" and therefore silently absorbable, which is the
    exact failure the reserved token exists to prevent."""
    return f"{family}:{UNKNOWN}"


def _num(v: Any) -> Optional[float]:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).strip().replace(",", ""))
    except (TypeError, ValueError):
        return None


def _fmt_edge(e: float) -> str:
    return str(int(e)) if float(e).is_integer() else str(e).replace(".", "p")


def band_token(family: str, value: float, edges: Sequence[float]) -> str:
    """Map a number onto its ordered band. edges=[1000,25000] ⇒
    lt_1000 | 1000_25000 | gte_25000."""
    ed = sorted(float(e) for e in edges)
    if value < ed[0]:
        return f"{family}:lt_{_fmt_edge(ed[0])}"
    for lo, hi in zip(ed, ed[1:]):
        if lo <= value < hi:
            return f"{family}:{_fmt_edge(lo)}_{_fmt_edge(hi)}"
    return f"{family}:gte_{_fmt_edge(ed[-1])}"


def _as_date(v: Any) -> Optional[datetime]:
    if isinstance(v, datetime):
        return v
    if isinstance(v, date):
        return datetime(v.year, v.month, v.day)
    s = str(v or "").strip()
    if not s:
        return None
    s = s.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%Y/%m/%d"):
            try:
                return datetime.strptime(s[:10], fmt)
            except ValueError:
                continue
    return None


def _get(record: Dict[str, Any], col: str) -> Any:
    """Column lookup tolerant of the physical/logical name split — a dataset's
    ``physical_name`` may differ from the ``name`` the builder authored against
    (sources.json carries both)."""
    if col in record:
        return record[col]
    tail = col.rsplit(".", 1)[-1]
    return record.get(tail)


def _has(record: Dict[str, Any], col: str) -> bool:
    """Is the column PRESENT on the record at all (any value, including NULL)?

    Distinct from ``_get(...) is None`` on purpose. A column that is present and
    null is a gap in one case's data; a column that is absent from every record
    the runtime is handed is a WIRING gap — the panel projection feeding review
    never selected it — and it disables every clause scoped to that family for
    every case. Those two need different alarms, so they need different tests."""
    return col in record or col.rsplit(".", 1)[-1] in record


# ---------------------------------------------------------------------------
# Derivation
# ---------------------------------------------------------------------------


def _signal_fired(signals: Any, signal_id: str) -> bool:
    """True when ``signal_id`` appears in the run's produced signals.

    Accepts the two shapes the platform emits: a list of dicts each carrying a
    ``signal`` key (fraud_checks / entity_links), or a flat list/set of ids."""
    if not signals:
        return False
    for s in signals:
        if isinstance(s, dict):
            if s.get("signal") == signal_id:
                return True
        elif str(s) == signal_id:
            return True
    return False


def derive_facets(
    record: Optional[Dict[str, Any]],
    signature: Optional[Dict[str, Any]],
    *,
    signals: Any = None,
    signals_ran: bool = False,
    domain: Optional[Dict[str, Any]] = None,
) -> Tuple[List[str], List[str]]:
    """Compute ``(facets, unknown_families)`` for one case.

    ``signals_ran`` distinguishes "the screening ran and this signal did not
    fire" (⇒ ``family:clear``) from "screening never ran for this app" (⇒ the
    facet is OMITTED). Guessing ``clear`` in the second case would let a clause
    scoped to ``exif:clear`` fire on a case nobody checked.

    ``domain`` contributes vertical / sub_vertical / country automatically —
    never authored, always present when the ontology supplies them.

    A facet whose value cannot be derived — an absent column, a null, an
    unparseable date, a non-numeric band column, an undeclared enum value —
    yields ``family:__unknown`` and is reported in ``unknown_families``. It is
    never silently dropped and never fatal: no clause may be scoped to
    ``__unknown``, so an undecidable family stops matching rather than matching
    the wrong thing, and the other families still route normally. The volume of
    the log distinguishes a routine data gap from a spec error.
    """
    facets: List[str] = []
    unknown: List[str] = []
    rec = record or {}

    for key in ("vertical", "sub_vertical", "country"):
        val = (domain or {}).get(key)
        if val:
            facets.append(token(key, val))

    for spec in ((signature or {}).get("facets") or []):
        family = str(spec.get("family") or "").strip()
        kind = str(spec.get("kind") or "").strip()
        if not _FAMILY_RE.match(family) or kind not in FACET_KINDS:
            log.warning(
                "[SIGNATURE] skipping malformed facet spec family=%r kind=%r",
                family, kind,
            )
            continue

        if kind == "signal":
            sid = str(spec.get("signal_id") or "")
            if not signals_ran:
                continue  # never guessed — see docstring
            facets.append(token(family, "fired" if _signal_fired(signals, sid) else "clear"))
            continue

        if kind == "age_band":
            cols = spec.get("from_columns") or []
            if len(cols) != 2:
                log.warning("[SIGNATURE] %s: age_band needs 2 columns", family)
                continue
            start, end = _as_date(_get(rec, cols[0])), _as_date(_get(rec, cols[1]))
            if start is None or end is None:
                # A missing/unparseable date is not drift in the ontology, it is
                # a gap in the record. Visible, never silently omitted.
                facets.append(unknown_token(family))
                unknown.append(family)
                continue
            facets.append(band_token(family, (end - start).days, spec.get("edges") or [0]))
            continue

        col = str(spec.get("from_column") or "")
        raw = _get(rec, col)

        if kind == "presence":
            present = raw is not None and str(raw).strip() != "" and raw != []
            facets.append(token(family, "present" if present else "absent"))
            continue

        if kind == "band":
            n = _num(raw)
            if n is None:
                # This USED to raise, and the raise was the wrong trade — the
                # same one the scorecard made. One unusable value in ONE band
                # family took out derivation for the WHOLE case: the caller
                # catches broadly, so the run landed with `case_facets: []`,
                # no clause retrieval, and nothing learned — reported only as a
                # routine "clause-memory load failed" warning.
                #
                # It also could not be prevented at publish, which is what the
                # old message told people to do. A nullable numeric column is
                # perfectly legitimate: a personal loan has no LTV, so
                # `ltv_percent` is NULL on exactly the cases where the concept
                # does not apply. The spec is right and the data is right.
                #
                # `__unknown` is the honest answer and the mechanism already
                # exists: the family is reported in `unknown`, and NO clause can
                # ever be scoped to `__unknown` (see the module docstring), so
                # the failure direction is safe — that family stops matching
                # rather than matching the wrong thing. This is also exactly
                # what the age_band branch above already does for an unparseable
                # date, and what the enum branch does for an absent column.
                #
                # Two causes, two volumes. A null is a gap in the record and
                # routine. A non-null value that is not a number means the spec
                # banded a column that does not hold numbers — that IS a spec
                # error, so it is logged at ERROR and named as such, but it
                # still does not take the case down.
                if raw is None or (isinstance(raw, str) and not raw.strip()):
                    log.warning(
                        "[SIGNATURE] facet '%s': column '%s' is NULL on this "
                        "case — deriving '%s'. No clause scoped to this family "
                        "can fire for this case, which is the safe direction. "
                        "If the column is nullable by design, that is expected.",
                        family, col, unknown_token(family),
                    )
                else:
                    log.error(
                        "[SIGNATURE] SPEC ERROR — facet '%s' is a BAND over "
                        "column '%s', but the column holds %r, which is not a "
                        "number. Every case will derive '%s' and NO clause "
                        "scoped to this family can ever fire. Either the facet "
                        "should be kind='enum', or it is reading the wrong "
                        "column.",
                        family, col, raw, unknown_token(family),
                    )
                facets.append(unknown_token(family))
                unknown.append(family)
                continue
            facets.append(band_token(family, n, spec.get("edges") or [0]))
            continue

        # kind == "enum"
        #
        # A column the record does not carry AT ALL is not ontology drift, and
        # calling it drift hid a live defect on acme-bank: the review panel's
        # projection omitted `sourcing_channel`, every case derived
        # `sourcing_channel:__unknown`, and the DSA judgement scoped to
        # `sourcing_channel:dsa` could never be retrieved — 19/19 over the API,
        # 0/1 through the app, reported only as a routine drift warning.
        # Drift is a data question ("declare this value"); an absent column is a
        # wiring question ("select it in the panel"). Different fix, different
        # alarm — and this one takes out every clause in the family at once.
        if not _has(rec, col):
            log.error(
                "[SIGNATURE] WIRING GAP — facet '%s' reads column '%s', which is "
                "ABSENT from the record handed to derivation (not null — absent). "
                "Every case will derive '%s' and NO clause scoped to this family "
                "can ever fire. Add '%s' to the projection of the panel that feeds "
                "review.",
                family, col, unknown_token(family), col,
            )
            facets.append(unknown_token(family))
            unknown.append(family)
            continue

        val = normalize_value(raw)
        mapped = (spec.get("value_map") or {}).get(val, val)
        declared = {normalize_value(v) for v in (spec.get("values") or [])}
        if declared and mapped not in declared:
            log.warning(
                "[SIGNATURE] ontology drift — facet '%s' saw undeclared value %r "
                "(column '%s'); declared: %s",
                family, raw, col, sorted(declared)[:12],
            )
            facets.append(unknown_token(family))
            unknown.append(family)
            continue
        facets.append(f"{family}:{mapped}")

    # Deduped + sorted so the token set is canonical: two runs of the same case
    # must produce byte-identical facets or the subset query is unstable.
    return sorted(set(facets)), sorted(set(unknown))


def signature_of(app_spec: Any) -> Optional[Dict[str, Any]]:
    """The app's ``case_signature`` as a plain dict, or None.

    Accepts the Mongo app document (dict with app_spec inside), a bare app_spec
    dict, or the Pydantic AppSpec — the same tolerance rubric_tenant_for_app
    has, and for the same reason (callers hold whichever shape they were given).
    """
    if app_spec is None:
        return None
    obj: Any = app_spec
    if isinstance(obj, dict) and "app_spec" in obj:
        obj = obj.get("app_spec") or {}
    if isinstance(obj, dict):
        sig = obj.get("case_signature")
    else:
        sig = getattr(obj, "case_signature", None)
    if sig is None:
        return None
    if hasattr(sig, "model_dump"):
        return sig.model_dump(exclude_none=False)
    return sig if isinstance(sig, dict) else None


def learning_config(signature: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Learning controls with defaults applied. Defaults are conservative:
    mode='summary' means an app that has not opted in keeps the legacy blob."""
    learn = ((signature or {}).get("learning") or {}) if signature else {}
    return {
        "promotion_min_officers": int(learn.get("promotion_min_officers") or 3),
        "clause_budget_words": int(learn.get("clause_budget_words") or 1000),
        "mode": str(learn.get("mode") or "summary"),
    }


def reason_codes(signature: Optional[Dict[str, Any]]) -> List[str]:
    return [
        str(rc.get("code"))
        for rc in ((signature or {}).get("reason_codes") or [])
        if isinstance(rc, dict) and rc.get("code")
    ]
