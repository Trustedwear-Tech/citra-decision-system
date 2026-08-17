# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""Validate AgentSpec.action.data_bindings against the data-discovery catalogue.

Runs at /publish before the AppSpec is persisted. The aim is to catch
LLM-fabricated dataset/column/action references at design time rather
than at the user's first run.

The validator returns two lists:
  errors[]   \u2014 hard rejections; publish must fail
  warnings[] \u2014 soft issues recorded into AppSpec.requirements_unmet[]

Errors:
  E_UNKNOWN_DATASET         dataset_id not in catalogue
  E_UNKNOWN_COLUMN          column not present on dataset
  E_UNKNOWN_ACTION          write_action_id not declared on dataset
  E_REQUIRED_FIELD_MISSING  write_action.input_schema.required[] not
                            satisfiable from caller's input_schema

Warnings:
  W_STALE_CATALOGUE         entry.last_refreshed_at older than threshold
  W_SOURCE_MISMATCH         binding.source_id != catalogue.source_id

The validator is async so it can fan out catalogue lookups in parallel.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from catalogue_client import fetch_catalogue_entry
from config import Settings
from discovery_cache import DiscoveryError
from models import AgentSpec, DataBindingRead, DataBindingWrite

logger = logging.getLogger(__name__)


_STALE_AFTER = timedelta(days=14)


class BindingError(dict):
    """Lightweight typed dict so callers can json-serialise errors as-is."""


def _err(code: str, action: str, **fields: Any) -> Dict[str, Any]:
    return {"code": code, "action": action, **fields}


async def validate_data_bindings(
    agent_spec: AgentSpec,
    *,
    settings: Settings,
    auth_header: Optional[str],
    tenant_id: str,
    data_sources: Optional[List[Any]] = None,
    app_spec: Optional[Any] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Validate every action's data_bindings. Returns (errors, warnings).

    ``data_sources`` is the AppSpec's data_source list (id → dataset ref). It is
    required for the fraud auto-wiring below — the consistency_check tools live on
    the AgentSpec but the data_sources that resolve their dataset live on the
    AppSpec, so the caller must pass them through.

    ``app_spec`` is optional and used only to auto-wire theme locale/currency
    from the same catalogue this pass already fetched (see locale_ontology)."""

    errors: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []

    # Collect every unique dataset reference once and parallelise the lookups.
    refs: set[tuple[str, str]] = set()
    for action in agent_spec.actions:
        bindings = action.data_bindings
        if not bindings:
            continue
        for r in bindings.reads:
            refs.add((r.source_id, r.dataset_id))
        for w in bindings.writes:
            refs.add((w.source_id, w.dataset_id))

    # Also resolve every mcp data_source the app declares, even if no action
    # binds to it. The fraud auto-wiring below reads each screened dataset's
    # ontology from the catalogue, and a fraud app may carry a consistency_check
    # tool with NO per-action data_bindings — without this, its dataset would be
    # absent from `catalogue` and the screen would never get wired. ``ref`` for an
    # mcp source is the full catalogue dataset_id ("<source_id>.<table>").
    for ds in (data_sources or []):
        # Dict- OR model-shaped (autowire reads them the same dual way; keep the
        # two in lockstep so a dict data_source isn't silently skipped here and
        # then left unresolved there).
        ds_type = ds.get("type") if isinstance(ds, dict) else getattr(ds, "type", None)
        ref = ds.get("ref") if isinstance(ds, dict) else getattr(ds, "ref", None)
        if ds_type == "mcp" and ref:
            refs.add((ref.split(".", 1)[0], ref))

    # And datasets bound DIRECTLY on tools_v2 mcp tools (McpTool.dataset_id) —
    # a builder may pin a keyed-read dataset on the tool without an AppSpec
    # data_source or an action binding. required_lookup_autowire resolves each
    # mandatory_when_used dataset from THIS map and silently skips absent
    # entries, so a tool-only dataset previously escaped the mandatory-lookup
    # gate entirely.
    for t in (getattr(agent_spec, "tools_v2", None) or []):
        t_kind = t.get("kind") if isinstance(t, dict) else getattr(t, "kind", None)
        t_ds = t.get("dataset_id") if isinstance(t, dict) else getattr(t, "dataset_id", None)
        if t_kind == "mcp" and t_ds and "." in t_ds:
            refs.add((t_ds.split(".", 1)[0], t_ds))

    if not refs:
        return errors, warnings

    async def _fetch(source_id: str, dataset_id: str):
        entry = await fetch_catalogue_entry(
            settings=settings,
            auth_header=auth_header,
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            source_id=source_id,
        )
        return (source_id, dataset_id), entry

    try:
        results = await asyncio.gather(*(_fetch(s, d) for (s, d) in refs))
    except DiscoveryError as exc:
        # The catalogue is unavailable (outage), not "datasets missing". We
        # can't validate bindings without it. Mirror the deliberate
        # infra-blip-doesn't-block-publish policy in publish_validators, but
        # loudly: log at error level and skip rather than emit false
        # "dataset not found" errors that would block the publish. Genuine
        # 404s still come back as None below and are validated normally.
        logger.error(
            "catalogue unavailable during data-binding validation — skipping "
            "(publish not blocked on infra outage): %s",
            exc,
        )
        return errors, warnings
    catalogue: Dict[tuple[str, str], Optional[Dict[str, Any]]] = dict(results)

    now = datetime.now(timezone.utc)

    for action in agent_spec.actions:
        bindings = action.data_bindings
        if not bindings:
            continue

        for r in bindings.reads:
            entry = catalogue.get((r.source_id, r.dataset_id))
            _check_read(action.name, r, entry, errors, warnings, now)

        for w in bindings.writes:
            entry = catalogue.get((w.source_id, w.dataset_id))
            _check_write(action, w, entry, errors, warnings)

    # Ontology-driven fraud auto-wiring: stamp each consistency_check tool's
    # url_column_roles from the resolved catalogue (artifact_role/reuse_policy),
    # so reuse detection knows an identity photo (reuse expected) from evidence
    # (reuse = double-dip) WITHOUT the builder LLM authoring it. Mutates
    # agent_spec.tools_v2 in place (persisted on save). We don't BLOCK the publish
    # on an auto-wire failure, but we must not swallow it either — the auto-CREATE
    # branch is the only thing that installs a screen on an opted-in dataset, so a
    # silent failure = a security primitive missing with no one told. Surface it as
    # a warning the builder sees (flows into AppSpec.requirements_unmet).
    try:
        from fraud_roles import autowire_fraud_roles
        _n = autowire_fraud_roles(agent_spec, catalogue, data_sources=data_sources)
        if _n:
            logger.info(
                "[fraud-autowire] stamped url_column_roles on %d consistency_check tool(s)", _n)
    except Exception as exc:  # noqa: BLE001 — surfaced (below), not silently swallowed
        logger.exception("[fraud-autowire] failed")
        warnings.append(_err(
            "fraud_autowire_failed", "*",
            detail=(f"Fraud screening could not be auto-wired ({type(exc).__name__}: {exc}). "
                    "Datasets that opted into fraud_screening may ship WITHOUT a screen — "
                    "review before publishing."),
        ))

    # Ontology-driven locale: lift domain.currency/country off the SAME catalogue
    # onto AppSpec.theme, so a currency-formatted column renders in the currency
    # the data is actually denominated in. Without it the runtime falls back to
    # en-US/USD and an Indian loan book renders in dollars beside an agent
    # reasoning in rupees. Never overrides an authored currency; stamps nothing
    # when the ontology is silent or sources disagree (see locale_ontology).
    try:
        from locale_ontology import autowire_theme_locale
        _cur = autowire_theme_locale(app_spec, catalogue)
        if _cur:
            logger.info("[locale-autowire] stamped theme.currency=%s", _cur)
    except Exception as exc:  # noqa: BLE001 — surfaced, not swallowed
        logger.exception("[locale-autowire] failed")
        warnings.append(_err(
            "locale_autowire_failed", "*",
            detail=(f"Screen currency could not be resolved from the dataset ontology "
                    f"({type(exc).__name__}: {exc}). Monetary columns may render in the "
                    "runtime default currency (USD) — set theme.currency explicitly."),
        ))

    # Ontology-driven required-lookup default: a dataset marked
    # ``mandatory_when_used`` in the catalogue makes its mcp read tool a
    # policy-required check (default ``required=true``), enforced by the
    # read-before-write gate. Same posture as fraud auto-wiring: mutate in place,
    # never BLOCK on a wiring failure, but surface it (never swallow) — a silent
    # failure = a mandated bureau/KYC check quietly not required.
    try:
        from required_lookup_autowire import autowire_required_lookups
        _m = autowire_required_lookups(agent_spec, catalogue)
        if _m:
            logger.info(
                "[required-autowire] defaulted required=true on %d mcp lookup(s)", _m)
    except Exception as exc:  # noqa: BLE001 — surfaced, not silently swallowed
        logger.exception("[required-autowire] failed")
        warnings.append(_err(
            "required_autowire_failed", "*",
            detail=(f"Mandatory-lookup auto-wiring failed ({type(exc).__name__}: {exc}). "
                    "Datasets marked mandatory_when_used may ship WITHOUT the required "
                    "flag — review before publishing."),
        ))

    return errors, warnings


def _check_read(
    action_name: str,
    r: DataBindingRead,
    entry: Optional[Dict[str, Any]],
    errors: List[Dict[str, Any]],
    warnings: List[Dict[str, Any]],
    now: datetime,
) -> None:
    if entry is None:
        errors.append(
            _err("E_UNKNOWN_DATASET", action_name, dataset_id=r.dataset_id, source_id=r.source_id)
        )
        return

    cat_cols = {c.get("name"): c for c in (entry.get("columns") or [])}
    for col in r.columns:
        if col not in cat_cols:
            errors.append(
                _err(
                    "E_UNKNOWN_COLUMN",
                    action_name,
                    dataset_id=r.dataset_id,
                    column=col,
                )
            )

    if entry.get("source_id") and entry["source_id"] != r.source_id:
        warnings.append(
            _err(
                "W_SOURCE_MISMATCH",
                action_name,
                dataset_id=r.dataset_id,
                expected=r.source_id,
                actual=entry["source_id"],
            )
        )

    # Staleness
    last = entry.get("last_refreshed_at")
    if isinstance(last, str):
        try:
            last_dt = datetime.fromisoformat(last.replace("Z", "+00:00"))
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=timezone.utc)
            if now - last_dt > _STALE_AFTER:
                warnings.append(
                    _err(
                        "W_STALE_CATALOGUE",
                        action_name,
                        dataset_id=r.dataset_id,
                        last_refreshed_at=last,
                    )
                )
        except ValueError:
            pass


def _check_write(
    action: Any,
    w: DataBindingWrite,
    entry: Optional[Dict[str, Any]],
    errors: List[Dict[str, Any]],
    warnings: List[Dict[str, Any]],
) -> None:
    if entry is None:
        errors.append(
            _err(
                "E_UNKNOWN_DATASET",
                action.name,
                dataset_id=w.dataset_id,
                source_id=w.source_id,
            )
        )
        return

    actions_by_id: Dict[str, Dict[str, Any]] = {
        wa.get("id"): wa for wa in (entry.get("write_actions") or []) if wa.get("id")
    }
    target = actions_by_id.get(w.action_id)
    if target is None:
        errors.append(
            _err(
                "E_UNKNOWN_ACTION",
                action.name,
                dataset_id=w.dataset_id,
                action_id=w.action_id,
                available=list(actions_by_id),
            )
        )
        return

    # Required-field reachability: the smart-app's caller-side input_schema
    # must contain every field the dataset's write_action insists on. We're
    # generous here \u2014 a missing input_schema means we skip the check rather
    # than block publish, since the BA may inject defaults at runtime.
    required = (target.get("input_schema") or {}).get("required") or []
    caller_props = (action.input_schema or {}).get("properties") or {}
    if required and caller_props:
        missing = [f for f in required if f not in caller_props]
        if missing:
            errors.append(
                _err(
                    "E_REQUIRED_FIELD_MISSING",
                    action.name,
                    dataset_id=w.dataset_id,
                    action_id=w.action_id,
                    required=missing,
                )
            )


# ---------------------------------------------------------------------------
# AppSpec PANEL column validation (dashboards / charts / queues)
# ---------------------------------------------------------------------------
# The data_bindings validator above covers AgentSpec action reads/writes. But
# a dashboard/chart/queue PANEL also references columns directly (a KPI metric
# field, a filter predicate, a chart x/y, a queue column). An LLM can fabricate
# one of these — e.g. SUM("previous_arrears_balance") when the real column is
# "arrears_carried" — and nothing caught it until the tile rendered blank in
# prod. Validate them at publish too, so a hallucinated column fails LOUD here.


def _pget(obj: Any, key: str, default: Any = None) -> Any:
    """Attr-or-dict getter — panels may be pydantic models or plain dicts."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _panel_column_refs(panel: Any, default_ds: Optional[str]) -> Dict[str, set]:
    """Return ``{data_source_id: {column, ...}}`` referenced by one panel.

    Only column-NAME positions are collected (filter/query KEYS, metric
    fields, chart x/y, queue columns) — never filter VALUES (which may be
    relative-date tokens like ``today``), so there's no false-positive risk."""
    out: Dict[str, set] = {}

    def add(ds_id: Optional[str], col: Any) -> None:
        if not ds_id or not isinstance(col, str) or not col.strip():
            return
        out.setdefault(ds_id, set()).add(col)

    ptype = (_pget(panel, "type") or "").lower()

    if ptype in ("dashboard", "kpi"):
        for m in (_pget(panel, "metrics") or []):
            mds = _pget(m, "data_source") or default_ds
            add(mds, _pget(m, "field"))
            for k in (_pget(m, "filter") or {}):
                add(mds, k)
            cmp = _pget(m, "compare")
            if cmp:
                add(mds, _pget(cmp, "date_field"))
            tr = _pget(m, "trend")
            if tr:
                add(mds, _pget(tr, "date_field"))
        return out

    if ptype == "chart":
        x = _pget(panel, "x")
        add(default_ds, x)
        # A COUNT chart's y is the synthetic row-count aggregate (COUNT(*),
        # conventionally y="count"), NOT a real column — don't validate it, or
        # every count chart fails as a "hallucinated column". Other aggregations
        # (sum/avg/…) operate on a real y column, so those are still checked.
        agg = (_pget(panel, "aggregation") or "").lower()
        if agg != "count":
            y = _pget(panel, "y")
            for yy in (y if isinstance(y, list) else [y]):
                if isinstance(yy, str) and yy.strip().lower() == "count":
                    continue  # 'count' is always the row-count keyword, never a column
                add(default_ds, yy)
        add(default_ds, _pget(panel, "group_by"))
        for k in (_pget(panel, "query") or {}):
            add(default_ds, k)
        return out

    if ptype in ("queue", "table"):
        for c in (_pget(panel, "columns") or []):
            # columns may be strings or {key/name: ...} dicts
            add(default_ds, c if isinstance(c, str) else (_pget(c, "key") or _pget(c, "name")))
        ds_sort = _pget(panel, "default_sort")
        if ds_sort:
            add(default_ds, _pget(ds_sort, "column"))
        for c in (_pget(panel, "searchable_columns") or []):
            add(default_ds, c)
        for f in (_pget(panel, "filters") or []):
            # queue filters are column names (strings)
            add(default_ds, f if isinstance(f, str) else _pget(f, "column"))
    return out


async def validate_data_source_refs(
    app_spec: Any,
    *,
    settings: Settings,
    auth_header: Optional[str],
    tenant_id: str,
) -> List[Dict[str, Any]]:
    """Assert every structured (mcp) data_source `ref` resolves to a REAL
    catalogue dataset for this tenant. Fail LOUD on a ref that cannot resolve.

    This is distinct from `validate_panel_columns`, which defensively *skips*
    a data_source whose catalogue entry is absent (doc/RAG sources, infra
    outages) to avoid false positives on columns. That skip is exactly what
    lets a *malformed* ref slip through to runtime as a blank dashboard.

    The canonical ref is the catalogue `dataset_id` verbatim — ``<source>.<table>``
    (e.g. ``field_operations.complaints``). Two failure modes are caught:
      • a ``/`` in the ref — a builder tic that duplicates the source as a path
        prefix (``field_operations/field_operations.complaints``). The source_id
        parser splits on the first ``.`` and gets a bogus ``a/a`` source that
        discovery can't resolve → every panel renders empty.
      • a ref whose dataset is genuinely not in the catalogue (hallucinated
        table / wrong source).

    Infra outages (DiscoveryError) are NOT treated as failures — we only reject
    a ref the catalogue can *authoritatively* say it does not have.
    """
    errors: List[Dict[str, Any]] = []
    seen: set = set()
    for d in (_pget(app_spec, "data_sources") or []):
        if (_pget(d, "type") or "") != "mcp":
            continue
        ref = (_pget(d, "ref") or "").strip()
        ds_id = _pget(d, "id") or "?"
        # A bare ref with no "." is a doc/RAG-style source, not a structured
        # dataset — column/dataset resolution doesn't apply.
        if "." not in ref:
            continue
        if ref in seen:
            continue
        seen.add(ref)
        if "/" in ref:
            errors.append(
                _err(
                    "E_MALFORMED_DATASET_REF",
                    f"data_source={ds_id}",
                    ref=ref,
                    expected=ref.rsplit("/", 1)[-1],
                    hint=(
                        "data_source.ref must be the catalogue dataset_id "
                        "verbatim ('<source_id>.<table>') — never prefix it "
                        "with the source_id and a slash."
                    ),
                )
            )
            continue
        src = ref.split(".", 1)[0].strip()
        try:
            entry = await fetch_catalogue_entry(
                settings=settings, auth_header=auth_header,
                tenant_id=tenant_id, dataset_id=ref, source_id=src,
            )
        except DiscoveryError:
            continue  # infra outage — don't block on a transient catalogue gap
        if not entry:
            errors.append(
                _err(
                    "E_UNKNOWN_DATASET",
                    f"data_source={ds_id}",
                    ref=ref,
                    source_id=src,
                    hint=(
                        "ref does not match any catalogue dataset for this "
                        "tenant. Use a dataset_id returned by /builder/catalogue."
                    ),
                )
            )
    return errors


async def validate_panel_columns(
    app_spec: Any,
    *,
    settings: Settings,
    auth_header: Optional[str],
    tenant_id: str,
) -> List[Dict[str, Any]]:
    """Validate every panel column reference against the catalogue schema.

    Returns a list of error dicts (empty = OK). Defensive: when the catalogue
    is unavailable, or a data_source maps to a dataset not present in the
    catalogue (e.g. a doc/RAG source with no columns), that panel is skipped
    rather than failing the publish on infra/coverage gaps — we only reject a
    column that is *demonstrably absent* from a dataset the catalogue knows."""
    # Map data_source id -> (source_id, dataset_id). ref is "src.dataset" for
    # structured mcp sources; a bare "src" (no dot) is a doc/RAG source we skip.
    ds_map: Dict[str, tuple[str, str]] = {}
    for d in (_pget(app_spec, "data_sources") or []):
        if (_pget(d, "type") or "") != "mcp":
            continue
        ref = (_pget(d, "ref") or "").strip()
        if "." not in ref:
            continue
        src = ref.split(".", 1)[0].strip()
        did = _pget(d, "id")
        if did:
            # The catalogue keys datasets by the FULL source-qualified ref
            # (e.g. "billing.bills"), not the bare table — look it up by ref.
            ds_map[did] = (src, ref)

    # Collect referenced columns per data_source across all panels.
    refs: Dict[str, set] = {}
    for page in (_pget(app_spec, "pages") or []):
        for panel in (_pget(page, "panels") or []):
            for ds_id, cols in _panel_column_refs(panel, _pget(panel, "data_source")).items():
                if ds_id in ds_map:
                    refs.setdefault(ds_id, set()).update(cols)

    if not refs:
        return []

    async def _fetch(ds_id: str):
        src, dataset = ds_map[ds_id]
        try:
            entry = await fetch_catalogue_entry(
                settings=settings, auth_header=auth_header,
                tenant_id=tenant_id, dataset_id=dataset, source_id=src,
            )
        except DiscoveryError:
            entry = None  # infra outage — skip (don't block publish)
        return ds_id, entry

    fetched = dict(await asyncio.gather(*(_fetch(d) for d in refs)))

    errors: List[Dict[str, Any]] = []
    for ds_id, cols in refs.items():
        entry = fetched.get(ds_id)
        if not entry:
            continue  # dataset not catalogued (or outage) → skip, no false +ve
        cat_cols = {c.get("name") for c in (entry.get("columns") or []) if c.get("name")}
        if not cat_cols:
            continue
        src, dataset = ds_map[ds_id]
        for col in sorted(cols):
            if col not in cat_cols:
                errors.append(
                    _err(
                        "E_UNKNOWN_PANEL_COLUMN",
                        f"panel:data_source={ds_id}",
                        dataset_id=dataset,
                        source_id=src,
                        column=col,
                    )
                )
    return errors
