<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: BUSL-1.1

  Licensed under the Business Source License 1.1. Non-production use is granted;
  production use requires a commercial licence until the Change Date, after
  which this file converts to Apache-2.0. See LICENSE at the repository root.
-->

# citra-dashboard-spec — Narrator agent: the 5 canonical patterns + AgentSpec shape

Read this when authoring the dashboard's narrator agent (Step 3). Every
dashboard ships with some subset of these — pick the ones the BA's goal
touches, but **at minimum implement #1 (Brief me)**. The narrator surfaces as
the runtime's automatic hero-brief copilot (top band, briefs on open, expands
to chat) — you author the agent, the runtime renders the band.

### Pattern 1 — "Brief me" / executive narrative *(always present)*

The agent reads all bound datasets, finds the 2–3 biggest changes vs. the prior period, and writes a 3-sentence narrative. Triggered the moment the dashboard loads (and on every refresh).

System prompt extract:
> "When asked to *brief* the dashboard, query each bound dataset for the current period vs the prior period (same length, immediately preceding). Identify the top 2–3 movers — biggest absolute change, biggest % change, biggest anomaly vs trend. Write three sentences: (1) one-line summary of the period, (2) the 2–3 movers with numbers, (3) one suggested action or follow-up question. Cite the dataset for every number (e.g. `[orders_2024]`)."

> **NUMBER DISCIPLINE — bake this into every narrator prompt.** The narrator **computes each figure itself**: for any count / total / sum, run ONE aggregate query (COUNT / SUM) with the right filter and quote that number — never count a capped row *sample*, and never answer a data question from memory. Put the **canonical metric definitions** in the prompt as a short, sharp list so the narrator's numbers match the KPI tiles *by construction* — e.g. `Active outages = outages.status='active'` (count the **records table**, NOT a related table like `feeders`); `Open theft = theft_cases.recovery_status IN ('pending','under_recovery')`; honour date framing (`today` = `CAST(... AS DATE)=CURRENT_DATE`). It must **never estimate, round-guess, or compute a derived number it didn't directly query** — especially period-over-period deltas (`up from N`, `X→Y`, `+P%`) and per-category splits. If the exact windowed/grouped count wasn't queried, **don't state it** — describe what you DID query instead. A brief with no fabricated figure is correct; a plausible-but-wrong figure is a defect.
>
> *(History: the runtime could once **inject** the tile aggregates as an "authoritative figures" block. That injection is now **OFF by default** (`DASHBOARD_GROUND_TRUTH_INJECT`) — it overrode the question's own framing, e.g. forcing a whole-table "open" total onto a "today" question, and steered the narrator off its own definitions. Author the narrator to be **self-sufficient** via aggregate queries + canonical definitions; do NOT write prompts that assume injected figures exist.)*

> **The brief is TEXT — do NOT emit a ` ```chart ` block in the auto-brief.** Inline charts are Pattern 5 (the *interactive* copilot answer), not the opening brief. A chart block in the brief that the narrator can't form perfectly renders as a wall of raw JSON. Keep Pattern 1 to prose + the authoritative numbers.

### Pattern 2 — "Why did X drop / spike?" Q&A *(always present)*

When the BA clicks a chart point or asks "why is region East down 22% in March?", the agent joins context across all bound datasets and explains.

System prompt extract:
> "When asked WHY a metric moved: identify the time window + dimension cut, query the relevant datasets at finer grain, cross-reference other bound datasets for the cause, and conclude with (a) the most likely driver, (b) confidence low/medium/high, (c) what data would raise confidence."

### Pattern 3 — Anomaly auto-tagging *(when datasets have time columns)*

On dashboard load, the agent scans the most-recent points of each time-series chart and flags outliers. Outliers render as 🚨 markers on the chart with a one-line hover explanation.

System prompt extract:
> "When asked to *flag anomalies*: for each time-series chart, fetch the last 12 grain-units, compute mean+stddev of the prior 11, flag any latest point > 2.5σ from the mean, drill once for the contributing dimension, and return `{panel_id, x_value, severity, one_line_reason}` per anomaly."

### Pattern 4 — NL filter / smart drill-down *(when BA asks "show me only X")*

The BA types in the chat panel: "show me only contracts at risk in last 30 days" → the agent translates to filters that apply across all panels.

System prompt extract:
> "When the user describes a SUBSET ('show me only X', 'just the Z ones'), translate to `{filter_set: {dataset_id: {column: value}, ...}}`, confirm in plain language before the runtime applies it, and reject filters that reference non-existent columns — ask for clarification instead."

### Pattern 5 — "Show me a chart" *(inline charts in the copilot)*

When the BA asks to *see / compare / trend* something in the copilot, the narrator fetches the rows via its `mcp` tools and returns a concise text answer **plus** a chart, by appending a fenced ` ```chart ` block to its reply:

```chart
{"chart_type":"bar","title":"Recovery by circle","x":"circle","y":"recovered_amount","data":[{"circle":"Begusarai","recovered_amount":4200000}, ...]}
```

The runtime parses these fenced ` ```chart ` blocks out of the reply, renders them as ECharts inline in the copilot, and shows the clean prose beside them. The narrator puts the fetched rows in the block's `"data"` and picks `chart_type` only — **never colours or sizes** (same spec/render separation as the dashboard panels; the runtime's executive theme owns the look).

> **Valid-or-omitted.** The block must be ONE well-formed JSON object with `chart_type` + `x` + `y` present and a closed ` ``` ` fence. The runtime now **drops** (and logs) any block that fails to parse or is missing those keys — it is no longer shown as raw JSON. So a malformed block = no chart at all, not a JSON dump. Keep it simple: prefer a **single-series** chart (`y` a string, not an array) with a small `data` array of rows you actually fetched; a complex multi-series comparison is the most common thing the LLM mis-forms. Only emit a chart when one genuinely helps, and only in the interactive answer (Pattern 5) — never the brief.

System prompt extract:
> "When the user asks to SEE, COMPARE, or TREND something, fetch the rows with your mcp tools, write a one-or-two-sentence answer, and append a fenced ```chart block whose JSON has `chart_type` (bar|line|area|pie), `title`, `x`, `y`, and a `data` array of the fetched rows. Pick chart_type and fields only — never emit colours, sizes, or styling. The runtime renders the block as a chart inline; keep the prose clean and short."

### AgentSpec shape

```jsonc
{
  "spec_version": "v0",
  "agent_id": "<slug>-narrator",
  "name": "<Dashboard title> Narrator",
  "description": "Reads the dashboard's data sources and answers brief / why / anomaly / NL-filter / show-me-a-chart questions.",
  "model_tier": "large",
  "system_prompt": "You are the narrator for the <Dashboard title> dashboard. ...\n\n<patterns 1-5 above, instantiated for this dashboard>",
  "tools_v2": [
    // One mcp tool per bound catalogue dataset:
    {
      "kind": "mcp",
      "name": "query_orders",
      "source_id": "<source_id>",
      "tool_name": "<dataset query tool>"
    },
    // ... one per dataset
    // Optional: a code_exec tool for stat computations (mean/stddev/percentiles)
    {
      "kind": "code_exec",
      "name": "compute_stats",
      "timeout_seconds": 30,
      "allowed_outputs": ["json"]
    }
  ]
}
```
