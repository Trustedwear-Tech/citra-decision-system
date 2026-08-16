"""Auto-inject a chart panel when the AppSpec contains numeric data but no chart.

Belt-and-braces companion to the `citra-app-spec` skill rule:
> "You MUST inject at least one chart panel when the data is numeric."

The builder agent is supposed to add the chart itself. This module catches
the case where it forgets, so users always get a visualisation when one
is meaningful. Pure logic — no DB, no IO.

Heuristics (intentionally simple):

- If the spec already contains a panel with `type=="chart"`, do nothing.
- Otherwise scan `queue` panels for a column that *looks* numeric by name
  (amount, revenue, count, qty, price, score, duration, latency, ratio,
  rate, total, sum, value, time / date / week / month).
- If a numeric column is found, append a chart panel that shares the
  queue's `data_source`, picks the first time-like column for `x` and the
  first numeric column for `y`. Type = `line` if `x` is time-like, else `bar`.

Anything ambiguous: do nothing. We never mutate the agent spec, and we
never replace existing panels. The result is always strictly a superset
of the original `panels[]`.
"""

from __future__ import annotations

import re
from typing import List, Optional, Tuple

from models import AppSpec, ChartPanel, Panel, QueuePanel


_NUMERIC_HINTS = frozenset({
    "amount", "amt", "revenue", "income", "cost", "price", "qty",
    "quantity", "count", "total", "sum", "score", "duration", "latency",
    "value", "ratio", "rate", "percentage", "percent", "balance",
    "throughput", "volume", "size", "consumers", "units", "kwh", "mw",
    "minutes", "saidi", "saifi", "assessed", "billed", "collected", "paid",
})

_TIME_HINTS = frozenset({
    "time", "date", "timestamp", "ts", "day", "week", "month", "year",
    "quarter", "due", "created", "updated", "at", "on",
})

# Category dimensions that make a good chart x-axis.
_CATEGORY_HINTS = frozenset({
    "category", "channel", "status", "type", "kind", "priority", "division",
    "circle", "region", "zone", "feeder", "dept", "department", "state",
    "class", "group", "segment", "tier", "stage", "reason", "cause", "scope",
})

# Identifier columns — NEVER a measure or a meaningful axis. The bug this
# guards: ``consumer_id`` was treated as numeric because "sum" is a substring
# of "conSUMer", so the injector summed account numbers into a 100-billion bar.
_IDENTIFIER_RE = re.compile(
    r"(^|_)(id|ids|no|nos|num|number|code|ref|key|uuid|guid|pk)$", re.IGNORECASE
)


def _tokens(name: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", name.lower()) if t}


def _pretty(name: str) -> str:
    if name == "count":
        return "count"
    return re.sub(r"[_\s]+", " ", str(name)).strip().title()


def _is_identifier(name: str) -> bool:
    return bool(_IDENTIFIER_RE.search(name or ""))


def _is_numeric_name(name: str) -> bool:
    # Token match (not substring) AND not an identifier — so "consumer_id" is
    # excluded and "assessed_amount" / "affected_consumers" are measures.
    if _is_identifier(name):
        return False
    return bool(_tokens(name) & _NUMERIC_HINTS)


def _is_time_name(name: str) -> bool:
    if _is_identifier(name):
        return False
    return bool(_tokens(name) & _TIME_HINTS)


def _is_category_name(name: str) -> bool:
    if _is_identifier(name):
        return False
    return bool(_tokens(name) & _CATEGORY_HINTS)


def _slug_id(base: str, existing: set[str]) -> str:
    candidate = re.sub(r"[^a-z0-9_]", "_", base.lower()).strip("_") or "chart"
    if candidate not in existing:
        return candidate
    i = 2
    while f"{candidate}_{i}" in existing:
        i += 1
    return f"{candidate}_{i}"


def _pick_chart_axes(
    queue: QueuePanel,
) -> Optional[Tuple[str, str, str, str, Optional[str]]]:
    """Return (x, y, chart_type, aggregation, time_grain) or None.

    Three shapes, in order of preference:
      - measure over time      → line, SUM, bucketed by day
      - measure by category    → bar,  SUM
      - count by category      → bar,  COUNT(*) (when there's no real measure —
                                 the right chart for an operational queue like
                                 complaints: counts per category/channel)
    Identifier columns are never chosen as a measure or axis.
    """
    cols = [c for c in (queue.columns or [])]
    if not cols:
        return None
    time_col = next((c for c in cols if _is_time_name(c)), None)
    measure = next(
        (c for c in cols if _is_numeric_name(c) and c != time_col), None
    )
    # Category axis: a category-hinted column first, else any plain column
    # that is not an id / time / the measure (avoids long free-text columns
    # only as a last resort).
    category = next((c for c in cols if _is_category_name(c)), None)
    if category is None:
        category = next(
            (
                c for c in cols
                if c not in (measure, time_col)
                and not _is_identifier(c)
                and not _is_time_name(c)
            ),
            None,
        )

    if measure:
        if time_col:
            return time_col, measure, "line", "sum", "day"
        if category:
            return category, measure, "bar", "sum", None
        return None
    # No real measure — count records by a category dimension.
    if category:
        return category, "count", "bar", "count", None
    return None


def _inject_into_panel_list(panels: List[Panel], existing_ids: set[str]) -> Optional[List[Panel]]:
    """If panels has a chart-worthy queue and no chart yet, return a new list
    with the chart inserted. Returns None if no change is warranted.
    """
    if any(getattr(p, "type", None) == "chart" for p in panels):
        return None
    queue: Optional[QueuePanel] = next(
        (p for p in panels if getattr(p, "type", None) == "queue"), None
    )
    if queue is None:
        return None
    axes = _pick_chart_axes(queue)
    if axes is None:
        return None
    x, y, chart_type, aggregation, time_grain = axes
    base = queue.title or queue.id
    title = (
        f"{base} by {_pretty(x)}" if aggregation == "count"
        else f"{_pretty(y)} by {_pretty(x)}"
    )
    chart = ChartPanel(
        id=_slug_id(f"{queue.id}_chart", existing_ids),
        title=title,
        type="chart",
        chart_type=chart_type,  # type: ignore[arg-type]
        data_source=queue.data_source,
        x=x,
        y=y,
        aggregation=aggregation,  # type: ignore[arg-type]
        time_grain=time_grain,  # type: ignore[arg-type]
    )
    new_panels = list(panels)
    insert_at = new_panels.index(queue) + 1
    for i, p in enumerate(new_panels):
        if getattr(p, "type", None) == "dashboard":
            insert_at = i + 1
            break
    new_panels.insert(insert_at, chart)
    return new_panels


def maybe_inject_chart_panel(spec: AppSpec) -> AppSpec:
    """Return a (possibly updated) spec with an auto-injected chart panel.

    Handles both layout modes:
      - Single-page (``spec.panels``): inject into the flat list.
      - Multi-page (``spec.pages``): scan each page, inject into the first
        page that has a chart-worthy queue without a chart already.

    Returns the *same* instance unchanged when no change is warranted.
    Never raises.
    """
    # Already has a chart anywhere? Bail.
    if any(getattr(p, "type", None) == "chart" for p in spec.all_panels):
        return spec

    existing_ids = {p.id for p in spec.all_panels}

    if spec.pages:
        new_pages = []
        injected = False
        for page in spec.pages:
            # NEVER inject into an EMBED page. AppSpec rejects chart/map there
            # (the embed bundle aliases echarts and leaflet away, so the panel
            # cannot render inside a customer's application). This function runs
            # AFTER validation, so injecting here writes a document that the
            # very same model refuses to load — bricking the app on EVERY read,
            # publish included. Observed exactly once and it cost a demo app:
            # the builder authored queue+detail correctly, publish added
            # `<queue>_chart`, and every subsequent read 500'd.
            if getattr(page, "kind", "standard") == "embed":
                new_pages.append(page)
                continue
            if not injected:
                updated = _inject_into_panel_list(page.panels, existing_ids)
                if updated is not None:
                    new_pages.append(page.model_copy(update={"panels": updated}))
                    injected = True
                    continue
            new_pages.append(page)
        if not injected:
            return spec
        return spec.model_copy(update={"pages": new_pages})

    # Single-page mode.
    updated = _inject_into_panel_list(list(spec.panels), existing_ids)
    if updated is None:
        return spec
    return spec.model_copy(update={"panels": updated})
