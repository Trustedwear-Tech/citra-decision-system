# Vendors a read-only snapshot of the LIVE runtime that executes a published
# AppSpec/AgentSpec into this skill's runtime-reference/ directory, so the
# SmartApp builder pod can read the actual code that renders + executes its
# spec (instead of guessing). Ships to the pod via the existing
# `COPY skills/` in builder-sandbox/Dockerfile + seed-persona.sh.
#
# RUN THIS BEFORE building citra-app-builder:latest whenever the runtime
# (citra-app-runtime renderer OR smart-app-service executor) changes — the
# snapshot is frozen at vendor time (decision: vendor + rebuild on deploy).
#
#   pwsh smart-app-service/skills/citra-system/vendor-runtime-reference.ps1
#
$ErrorActionPreference = "Stop"
$repo   = (Resolve-Path "$PSScriptRoot\..\..\..").Path          # repo root
$dest   = Join-Path $PSScriptRoot "runtime-reference"
$rend   = Join-Path $dest "renderer"
$exec   = Join-Path $dest "executor"
$valid  = Join-Path $dest "validators"

# Fresh — never leave a stale half-snapshot.
if (Test-Path $dest) { Remove-Item -Recurse -Force $dest }
New-Item -ItemType Directory -Force -Path $rend, $exec, $valid | Out-Null

# --- RENDERER: how a spec is RENDERED (citra-app-runtime) -------------------
$rendSrc = Join-Path $repo "citra-app-runtime\src"
Copy-Item -Recurse -Force (Join-Path $rendSrc "*") $rend

# --- EXECUTOR: how a spec is QUERIED + CALLED (smart-app-service) ------------
$sa = Join-Path $repo "smart-app-service"
foreach ($f in "panel_data.py","tools_v2_dispatch.py","runtime.py","data_tools.py","models.py","auto_process.py") {
  Copy-Item -Force (Join-Path $sa $f) (Join-Path $exec $f)
}

# --- VALIDATORS: how a spec is CHECKED at publish (the real rules) ----------
foreach ($f in "validators.py","publish_validators.py","data_binding_validator.py") {
  Copy-Item -Force (Join-Path $sa $f) (Join-Path $valid $f)
}

# Manifest: what was copied + when (fail-loud provenance, no hidden staleness).
$stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
$manifest = @"
# runtime-reference — VENDORED SNAPSHOT (do not edit by hand)
Generated: $stamp
Source repo: $repo

renderer/  <- citra-app-runtime/src/        (how the spec RENDERS — PanelRenderer.tsx, types/spec.ts, lib/pages.ts, lib/chartToEcharts.ts, app/.../page.tsx, app/api/*/route.ts)
executor/  <- smart-app-service/*.py         (how the spec is QUERIED + CALLED)
  - panel_data.py        data_source -> query (dashboard KPI/ratio, chart GROUP BY, queue rows, NL vs SQL)
  - tools_v2_dispatch.py agent tool dispatch (what params each tool kind exposes to the LLM; mcp read = NL query+args, mcp_action = input_schema)
  - runtime.py           agent run loop (inputs injection into the prompt, _MAX_TOOL_ITERATIONS, tier->model)
  - data_tools.py        app-owned overlay writes (smart_app_records merge/thread + delta)
  - models.py            canonical AppSpec / AgentSpec Pydantic models (extra='forbid' — the real field contract)
  - auto_process.py      auto-commit policy evaluation (the AutoProcessPolicy condition engine)
validators/ <- smart-app-service/*.py        (how the spec is CHECKED at publish — the REAL rules, not prose)
  - validators.py            the two-layer (JSON-Schema + Pydantic) entry; what's server-stamped vs builder-authored
  - publish_validators.py    every publish gate (W-/H-/T-/S-/D- rules, update_identifier, editable_fields, G-01, …)
  - data_binding_validator.py panel/data-source binding + chart-column checks

Refresh: re-run vendor-runtime-reference.ps1 before rebuilding citra-app-builder:latest.
"@
Set-Content -Path (Join-Path $dest "MANIFEST.md") -Value $manifest -Encoding utf8

$n = (Get-ChildItem -Recurse -File $dest | Measure-Object).Count
Write-Output "vendored $n files into $dest (stamp $stamp)"
