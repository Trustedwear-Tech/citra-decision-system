# Re-vendor the runtime-reference snapshot the citra-app-builder pod learns from.
# =============================================================================
# The builder is TAUGHT (SKILL.md) that skills/citra-system/runtime-reference is
# the source of truth for how a spec renders/executes/validates — so a stale
# snapshot makes the builder author against retired contracts (the 2026-07
# ontology review found it a full feature-generation behind: no check_evaluate,
# no modality='api', wrong review-gate logic). Run this BEFORE rebuilding
# citra-app-builder:latest, as MANIFEST.md instructs.
#
# Mechanism: every file already present in the snapshot is refreshed from its
# live source (mapping below). Files are never added/removed automatically —
# add a file by copying it once manually, then this script keeps it fresh.
#
# Usage:  .\vendor-runtime-reference.ps1   (from smart-app-service/)

$ErrorActionPreference = "Stop"
$svc  = $PSScriptRoot
$repo = (Resolve-Path (Join-Path $svc "..")).Path
$ref  = Join-Path $svc "skills\citra-system\runtime-reference"

if (-not (Test-Path $ref)) { throw "runtime-reference not found at $ref" }

# Vendored subtree -> live source root
$map = @{
    "executor"   = $svc                                          # smart-app-service/*.py
    "validators" = $svc                                          # smart-app-service/*.py
    "renderer"   = (Join-Path $repo "citra-app-runtime\src")     # citra-app-runtime/src/**
}

$synced = 0; $missing = @()
Get-ChildItem -Path $ref -Recurse -File | Where-Object {
    $_.Name -ne "MANIFEST.md" -and $_.FullName -notmatch "__pycache__"
} | ForEach-Object {
    $rel  = $_.FullName.Substring($ref.Length + 1)               # e.g. renderer\components\X.tsx
    $head = $rel.Split([IO.Path]::DirectorySeparatorChar)[0]
    $tail = $rel.Substring($head.Length + 1)
    $src  = Join-Path $map[$head] $tail
    # -LiteralPath everywhere: Next.js route dirs contain [slug]/[[...page]] which
    # PowerShell would otherwise expand as wildcards and report as missing.
    if (Test-Path -LiteralPath $src) {
        Copy-Item -LiteralPath $src -Destination $_.FullName -Force
        $synced++
    } else {
        $missing += "$rel  (expected at $src)"
    }
}

# Vendored snapshots must not carry bytecode.
Get-ChildItem -Path $ref -Recurse -Directory -Filter "__pycache__" |
    Remove-Item -Recurse -Force -Confirm:$false

# Stamp the manifest so drift is datable.
$manifest = Join-Path $ref "MANIFEST.md"
$stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
(Get-Content $manifest -Raw) -replace "Generated: .*", "Generated: $stamp" |
    Set-Content $manifest -Encoding utf8

Write-Host "Synced $synced file(s) into runtime-reference (manifest stamped $stamp)."
if ($missing.Count -gt 0) {
    Write-Host "MISSING SOURCES (vendored file has no live counterpart - retired? remove it or fix the map):" -ForegroundColor Yellow
    $missing | ForEach-Object { Write-Host "  $_" -ForegroundColor Yellow }
    exit 1
}
