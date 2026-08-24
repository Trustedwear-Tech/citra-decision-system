#!/usr/bin/env pwsh
# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

<#
.SYNOPSIS
    Update Milvus schema to include folder_id and entity_id fields

.DESCRIPTION
    This script helps you safely update the Milvus collection schema to add
    the missing folder_id and entity_id fields required for proper filtering.
    
    ⚠️  WARNING: This will DELETE ALL DATA in the collection!
    
.PARAMETER Environment
    Target environment: dev, test, or prod (default: dev)

.PARAMETER Force
    Skip confirmation prompt

.EXAMPLE
    # Update dev environment (with confirmation)
    .\scripts\update_milvus_schema.ps1

.EXAMPLE
    # Update prod environment (with confirmation)
    .\scripts\update_milvus_schema.ps1 -Environment prod

.EXAMPLE
    # Update with no confirmation (dangerous!)
    .\scripts\update_milvus_schema.ps1 -Force
#>

param(
    [Parameter()]
    [ValidateSet('dev', 'test', 'prod')]
    [string]$Environment = 'dev',
    
    [Parameter()]
    [switch]$Force
)

# Set error action preference
$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "═══════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host "  Milvus Schema Update - Add folder_id & entity_id" -ForegroundColor Cyan
Write-Host "═══════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host ""

# Check if we're in the correct directory
if (-not (Test-Path "scripts\setup_milvus_schema.py")) {
    Write-Host "❌ Error: Must run from Citra-Service directory" -ForegroundColor Red
    Write-Host "   Current: $PWD" -ForegroundColor Red
    Write-Host ""
    exit 1
}

# Check if .env exists
if (-not (Test-Path ".env")) {
    Write-Host "❌ Error: .env file not found" -ForegroundColor Red
    Write-Host ""
    exit 1
}

Write-Host "🎯 Target Environment: " -NoNewline
Write-Host $Environment.ToUpper() -ForegroundColor Yellow
Write-Host ""

# Step 1: Validate current schema
Write-Host "📋 Step 1: Validating current schema..." -ForegroundColor Cyan
Write-Host ""

python scripts/setup_milvus_schema.py --validate
$validationResult = $LASTEXITCODE

if ($validationResult -eq 0) {
    Write-Host ""
    Write-Host "✅ Schema is already up to date!" -ForegroundColor Green
    Write-Host "   Both folder_id and entity_id fields exist." -ForegroundColor Green
    Write-Host ""
    exit 0
}

Write-Host ""
Write-Host "⚠️  Schema validation failed - update required" -ForegroundColor Yellow
Write-Host ""

# Step 2: Show current collection info
Write-Host "📊 Step 2: Current collection status..." -ForegroundColor Cyan
Write-Host ""

python scripts/setup_milvus_schema.py --check

Write-Host ""
Write-Host "═══════════════════════════════════════════════════════" -ForegroundColor Red
Write-Host "  ⚠️  WARNING: THIS WILL DELETE ALL DATA!" -ForegroundColor Red
Write-Host "═══════════════════════════════════════════════════════" -ForegroundColor Red
Write-Host ""
Write-Host "The schema update requires recreating the collection." -ForegroundColor Yellow
Write-Host "All documents in the collection will be PERMANENTLY DELETED." -ForegroundColor Yellow
Write-Host ""
Write-Host "You will need to re-upload all your documents after this operation." -ForegroundColor Yellow
Write-Host ""

# Confirmation prompt (unless -Force specified)
if (-not $Force) {
    Write-Host "❓ Do you want to proceed? " -NoNewline -ForegroundColor Cyan
    Write-Host "[Type 'DELETE' to confirm]: " -NoNewline -ForegroundColor Yellow
    $confirmation = Read-Host
    
    if ($confirmation -ne "DELETE") {
        Write-Host ""
        Write-Host "❌ Aborted - confirmation not provided" -ForegroundColor Red
        Write-Host ""
        exit 0
    }
}

Write-Host ""
Write-Host "🚀 Step 3: Recreating collection with new schema..." -ForegroundColor Cyan
Write-Host ""

# Run the force recreate
# The Python script will handle its own confirmation prompt
python scripts/setup_milvus_schema.py --force

$updateResult = $LASTEXITCODE

if ($updateResult -eq 0) {
    Write-Host ""
    Write-Host "═══════════════════════════════════════════════════════" -ForegroundColor Green
    Write-Host "  ✅ Schema Update Complete!" -ForegroundColor Green
    Write-Host "═══════════════════════════════════════════════════════" -ForegroundColor Green
    Write-Host ""
    Write-Host "New fields added:" -ForegroundColor Green
    Write-Host "  • folder_id (VARCHAR 100) - Indexed" -ForegroundColor Green
    Write-Host "  • entity_id (VARCHAR 100) - Indexed" -ForegroundColor Green
    Write-Host ""
    Write-Host "📝 Next Steps:" -ForegroundColor Cyan
    Write-Host "  1. Re-upload all your documents to the collection" -ForegroundColor White
    Write-Host "  2. Test folder filtering functionality" -ForegroundColor White
    Write-Host "  3. Test enterprise entity filtering" -ForegroundColor White
    Write-Host ""
    Write-Host "📚 For more details, see: MILVUS_SCHEMA_UPDATE.md" -ForegroundColor Cyan
    Write-Host ""
} else {
    Write-Host ""
    Write-Host "❌ Schema update failed!" -ForegroundColor Red
    Write-Host ""
    Write-Host "Check the error messages above for details." -ForegroundColor Yellow
    Write-Host ""
    exit 1
}
