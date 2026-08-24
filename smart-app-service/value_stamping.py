# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""Value stamping — the ROI spine (docs/money-saved-roi-plan.md V2).

The ontology's ``value_semantics`` block defines what a decision's money is;
this module (a) RESOLVES that block against the catalogue at publish (routing
+ validation + definition_version) and (b) COMPUTES ``outcome.value`` for the
Stage-4 poller when a decision settles.

Doctrine:
  * The definition is frozen in sources.json — every stamped value carries
    ``definition_version`` (hash of the block that computed it), so a number
    can always be traced to its definition and a mid-pilot change is VISIBLE.
  * prevented_loss uses the exposure FROZEN AT DECISION TIME (the staging
    display_context snapshot riding DecisionRecord.context) — today's balance
    is not what was at stake.
  * Fail-loud: a mis-declared realization drops at publish with a warning; a
    realization read that errors stamps ``value_error`` — a zero always means
    "genuinely nothing realized", never "the read failed".
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

#: Realization must be readable by the deterministic structured plane.
_STRUCTURED_KINDS = {"sql", "odata", "soql", "mongodb"}
#: Cap on realization rows read per case (visible in basis when hit).
_REALIZATION_ROW_CAP = 500


def definition_version(vs: Dict[str, Any]) -> str:
    """Stable 12-hex hash of the value definition — the freeze receipt."""
    canon = json.dumps(vs, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()[:12]


async def resolve_app_value_semantics(
    *,
    app_spec: Any,
    settings: Any,
    auth_header: Optional[str],
    tenant_id: Optional[str],
) -> Dict[str, Any]:
    """Resolve every mcp data source's ``value_semantics`` for this app.

    Returns {dataset_ref: resolved_block}. Resolution = the authored block +
    realization ROUTING (source_id/kind from the catalogue) + currency (from
    the domain triple) + definition_version. A realization dataset the
    catalogue doesn't know, a non-structured realization kind, or a missing
    realization column drops the BLOCK loudly — a half-wired money definition
    that silently stamps zeros is precisely what this module must prevent.
    """
    from catalogue_client import fetch_catalogue_entry

    out: Dict[str, Any] = {}
    for ds in (getattr(app_spec, "data_sources", None) or []):
        ds_type = ds.get("type") if isinstance(ds, dict) else getattr(ds, "type", None)
        ref = ds.get("ref") if isinstance(ds, dict) else getattr(ds, "ref", None)
        if ds_type != "mcp" or not ref or ref in out:
            continue
        try:
            entry = await fetch_catalogue_entry(
                settings=settings, tenant_id=tenant_id, dataset_id=ref,
                auth_header=auth_header,
            )
        except Exception as exc:  # noqa: BLE001 — publish continues; block skipped loudly
            logger.warning("[value] catalogue fetch failed for %r: %s", ref, exc)
            continue
        vs = (entry or {}).get("value_semantics")
        if not vs:
            continue
        resolved = dict(vs)
        resolved["dataset"] = ref
        dom = (entry or {}).get("domain") or {}
        if dom.get("currency"):
            resolved["currency"] = dom["currency"]
        real = vs.get("realization")
        if real:
            r_ref = real.get("dataset")
            try:
                r_entry = await fetch_catalogue_entry(
                    settings=settings, tenant_id=tenant_id, dataset_id=r_ref,
                    auth_header=auth_header,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "[value] realization dataset %r unreachable — value_semantics "
                    "for %r DROPPED (fix sources.json / crawl): %s", r_ref, ref, exc)
                continue
            if not r_entry:
                logger.warning(
                    "[value] realization dataset %r not in catalogue — "
                    "value_semantics for %r DROPPED", r_ref, ref)
                continue
            r_kind = r_entry.get("kind")
            if r_kind not in _STRUCTURED_KINDS:
                logger.warning(
                    "[value] realization dataset %r has kind %r (not structured) "
                    "— value_semantics for %r DROPPED; point realization at a "
                    "sql/odata/soql/mongodb dataset", r_ref, r_kind, ref)
                continue
            r_cols = {
                (c.get("name") or c.get("physical_name"))
                for c in (r_entry.get("columns") or []) if isinstance(c, dict)
            }
            missing = [f for f in (real.get("match_field"),
                                   real.get("amount_field"),
                                   real.get("date_field"))
                       if f and r_cols and f not in r_cols]
            if missing:
                logger.warning(
                    "[value] realization column(s) %s do not exist in %r — "
                    "value_semantics for %r DROPPED; fix sources.json",
                    missing, r_ref, ref)
                continue
            resolved["realization"] = {
                **real,
                "source_id": str(r_ref).split(".", 1)[0],
                "kind": r_kind,
            }
        resolved["definition_version"] = definition_version(vs)
        out[ref] = resolved
    return out


def pick_value_semantics_for_table(
    vs_map: Optional[Dict[str, Any]], table: Optional[str]
) -> Optional[Dict[str, Any]]:
    """The block for the outcome-poll's dataset: match by table suffix; a
    single-block map applies regardless (one decision dataset per app is the
    overwhelmingly common shape)."""
    if not vs_map:
        return None
    if table:
        for ref, block in vs_map.items():
            if ref == table or ref.endswith("." + table):
                return block
    if len(vs_map) == 1:
        return next(iter(vs_map.values()))
    return None


def _build_multi_query(kind: str, table: str, match_field: str, value: Any) -> Optional[Any]:
    """Structured multi-row read for realization rows. Never the NL planner."""
    from main import _sql_quote  # shared quoting — one definition

    if kind == "sql":
        return (f"SELECT * FROM {table} WHERE {match_field} = "
                f"{_sql_quote(value)}")
    if kind == "odata":
        return {"entity": table, "$filter": f"{match_field} eq {_sql_quote(value)}",
                "$top": _REALIZATION_ROW_CAP}
    if kind == "soql":
        return (f"SELECT FIELDS(ALL) FROM {table} WHERE {match_field} = "
                f"{_sql_quote(value)} LIMIT {_REALIZATION_ROW_CAP}")
    if kind == "mongodb":
        return {match_field: value}
    return None


def _parse_dt(v: Any, locale: Optional[str] = None) -> Optional[datetime]:
    from fraud_checks import normalize_date

    iso = normalize_date(v, locale)
    if not iso:
        return None
    return datetime.strptime(iso, "%Y-%m-%d").replace(tzinfo=timezone.utc)


async def compute_outcome_value(
    *,
    settings: Any,
    rec: Dict[str, Any],
    row: Dict[str, Any],
    cur_status: Any,
    vs: Dict[str, Any],
    user_jwt: Optional[str],
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """The stamped ``outcome.value`` for one settled decision, or an error.

    Returns (value, error) — value is None with error=None when the decision
    simply carries no value under this definition (e.g. an approved claim
    under a prevented_loss definition); that is a NON-value, not a failure.
    """
    from fraud_checks import normalize_amount

    kind = vs.get("value_kind")
    base = {
        "kind": kind,
        "currency": vs.get("currency"),
        "attribution": vs.get("attribution"),
        "definition_version": vs.get("definition_version"),
        "computed_at": datetime.now(timezone.utc),
    }

    if kind == "prevented_loss":
        prevented = [str(p).lower() for p in (vs.get("prevented_when") or [])]
        if str(cur_status).lower() not in prevented:
            return None, None  # outcome carries no prevention value — not an error
        exp_field = vs.get("exposure_field")
        ctx = rec.get("context") or {}
        raw = ctx.get(exp_field)
        basis = "decision_context (exposure frozen at decision time)"
        if raw in (None, ""):
            raw = row.get(exp_field)
            basis = "readback_row (decision-time snapshot unavailable)"
        amount = normalize_amount(raw)
        if amount is None:
            return None, (
                f"prevented_loss exposure {exp_field!r}={raw!r} missing/unparseable "
                f"— value NOT stamped (never a silent zero)")
        return {**base, "amount": float(amount),
                "basis": {"source": basis, "status": cur_status}}, None

    real = vs.get("realization")
    if not real:
        return None, (
            f"value_kind={kind!r} has no resolved realization — value NOT stamped")
    from proxy_clients import call_dept_mcp_read, ProxyError

    match_field = real.get("match_field")
    match_value = (rec.get("context") or {}).get(match_field, row.get(match_field))
    if match_value in (None, ""):
        return None, (
            f"realization match value ({match_field!r}) absent on the case — "
            f"value NOT stamped")
    query = _build_multi_query(real.get("kind") or "sql",
                               str(real["dataset"]).split(".", 1)[-1],
                               match_field, match_value)
    if query is None:
        return None, f"realization kind {real.get('kind')!r} unsupported"
    try:
        resp = await call_dept_mcp_read(
            settings=settings, user_jwt=user_jwt,
            source_id=real.get("source_id"), dataset_id=real.get("dataset"),
            kind=real.get("kind"), query=query, row_limit=_REALIZATION_ROW_CAP,
        )
    except ProxyError as exc:
        return None, f"realization read failed: {exc}"
    if (resp or {}).get("error"):
        return None, f"realization read failed: {resp['error']}"
    rows = (resp or {}).get("rows") or []

    decided = rec.get("created_at")
    if isinstance(decided, datetime) and decided.tzinfo is None:
        decided = decided.replace(tzinfo=timezone.utc)
    window_end = (decided + timedelta(days=int(real.get("window_days") or 90))
                  if isinstance(decided, datetime) else None)
    date_field = real.get("date_field")
    amount_field = real.get("amount_field")

    total = 0.0
    counted = 0
    unparseable = 0
    for r in rows:
        if not isinstance(r, dict):
            continue
        if date_field and isinstance(decided, datetime):
            dt = _parse_dt(r.get(date_field))
            if dt is None or dt < decided or (window_end and dt > window_end):
                continue
        amt = normalize_amount(r.get(amount_field))
        if amt is None:
            if r.get(amount_field) not in (None, ""):
                unparseable += 1
            continue
        total += float(amt)
        counted += 1
    if unparseable and counted == 0:
        return None, (
            f"realization amounts all unparseable ({unparseable} row(s), "
            f"column {amount_field!r}) — value NOT stamped")
    basis: Dict[str, Any] = {
        "realization_dataset": real.get("dataset"),
        "rows_counted": counted,
        "window_days": real.get("window_days"),
        "match": {match_field: match_value},
    }
    if unparseable:
        basis["rows_unparseable"] = unparseable
    if len(rows) >= _REALIZATION_ROW_CAP:
        basis["truncated_at"] = _REALIZATION_ROW_CAP
    return {**base, "amount": round(total, 2), "basis": basis}, None
