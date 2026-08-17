# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

# Start smart-app-service with the shared project venv activated.
#
# Run from anywhere:
#     C:\Github\Citra-AI\smart-app-service\start.ps1
# or, when already in this folder:
#     .\start.ps1
#
# The Python venv is shared across the Citra-AI Python services and lives at
# Citra-Service\myenv (NOT inside this folder). This script activates it,
# moves into smart-app-service, and launches main.py (uvicorn on :9100).

$ErrorActionPreference = 'Stop'

$svcDir   = $PSScriptRoot
$venvAct  = Join-Path (Split-Path $svcDir -Parent) 'Citra-Service\myenv\Scripts\Activate.ps1'

if (-not (Test-Path $venvAct)) {
    Write-Error "venv not found at $venvAct - create it or fix the path."
    exit 1
}

Write-Host "Activating venv: $venvAct" -ForegroundColor Cyan
& $venvAct

Set-Location $svcDir
Write-Host "Starting smart-app-service (python main.py) in $svcDir" -ForegroundColor Cyan
python main.py
