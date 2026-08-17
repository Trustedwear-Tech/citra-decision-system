# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

# install-and-run.ps1 - Quick setup script for new features (Windows)

Write-Host "🚀 Citra AI - Feature Installation Script" -ForegroundColor Cyan
Write-Host "==============================================" -ForegroundColor Cyan
Write-Host ""

# Check if npm is installed
$npmInstalled = Get-Command npm -ErrorAction SilentlyContinue
if (-not $npmInstalled) {
    Write-Host "❌ Error: npm is not installed" -ForegroundColor Red
    Write-Host "Please install Node.js and npm first" -ForegroundColor Yellow
    exit 1
}

# Install dependencies
Write-Host "📦 Installing dependencies..." -ForegroundColor Yellow
npm install

# Check if installation succeeded
if ($LASTEXITCODE -eq 0) {
    Write-Host "✅ Dependencies installed successfully!" -ForegroundColor Green
    Write-Host ""
    Write-Host "🎉 Setup Complete!" -ForegroundColor Green
    Write-Host ""
    Write-Host "📋 New Features Available:" -ForegroundColor Cyan
    # Hidden: Project Management is hidden from the UI.
    # Write-Host "  ✅ Enhanced Kanban Board (Project Management → Kanban Board tab)" -ForegroundColor White
    Write-Host "  ✅ Resource badges on task cards" -ForegroundColor White
    Write-Host "  ✅ Drag-and-drop task management" -ForegroundColor White
    Write-Host "  ✅ Over-allocation warnings" -ForegroundColor White
    Write-Host ""
    Write-Host "🚀 To start the app, run:" -ForegroundColor Cyan
    Write-Host "  npm start" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "📚 Documentation:" -ForegroundColor Cyan
    Write-Host "  - Quick Start: QUICK_START_GUIDE.md" -ForegroundColor White
    Write-Host "  - Features: IMPLEMENTATION_STATUS.md" -ForegroundColor White
    Write-Host "  - Chat Integration: CHAT_INTEGRATION_GUIDE.md" -ForegroundColor White
    Write-Host ""
} else {
    Write-Host "❌ Installation failed" -ForegroundColor Red
    Write-Host "Please check the error messages above" -ForegroundColor Yellow
    exit 1
}
