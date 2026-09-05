# runtime-reference Ã¢â‚¬â€ VENDORED SNAPSHOT (do not edit by hand)
Generated: 2026-09-06 00:46:42
Source repo: C:\Github\Citra-AI

renderer/  <- citra-app-runtime/src/        (how the spec RENDERS Ã¢â‚¬â€ PanelRenderer.tsx, types/spec.ts, lib/pages.ts, lib/chartToEcharts.ts, app/.../page.tsx, app/api/*/route.ts)
executor/  <- smart-app-service/*.py         (how the spec is QUERIED + CALLED)
  - panel_data.py        data_source -> query (dashboard KPI/ratio, chart GROUP BY, queue rows, NL vs SQL)
  - tools_v2_dispatch.py agent tool dispatch (what params each tool kind exposes to the LLM; mcp read = NL query+args, mcp_action = input_schema)
  - runtime.py           agent run loop (inputs injection into the prompt, _MAX_TOOL_ITERATIONS, tier->model)
  - data_tools.py        app-owned overlay writes (smart_app_records merge/thread + delta)
  - models.py            canonical AppSpec / AgentSpec Pydantic models (extra='forbid' Ã¢â‚¬â€ the real field contract)
  - auto_process.py      auto-commit policy evaluation (the AutoProcessPolicy condition engine)
  - factor_scoring.py    the scorecard aggregator â€” proof the MODEL never does the arithmetic (weights applied here, gate short-circuit, band + grade assignment)
validators/ <- smart-app-service/*.py        (how the spec is CHECKED at publish Ã¢â‚¬â€ the REAL rules, not prose)
  - validators.py            the two-layer (JSON-Schema + Pydantic) entry; what's server-stamped vs builder-authored
  - publish_validators.py    every publish gate (W-/H-/T-/S-/D- rules, update_identifier, editable_fields, G-01, Ã¢â‚¬Â¦)
  - data_binding_validator.py panel/data-source binding + chart-column checks

Refresh: re-run vendor-runtime-reference.ps1 before rebuilding citra-app-builder:latest.



























