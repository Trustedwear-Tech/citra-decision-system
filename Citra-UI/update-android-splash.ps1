# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

# Update Android Splash Screen with Citra Logo
# This script copies the citra-logo.png to all Android drawable density folders

Write-Host "Updating Android Splash Screen Logo..." -ForegroundColor Cyan

$sourceLogo = ".\assets\citra-logo.png"
$drawableDirs = @(
    ".\android\app\src\main\res\drawable-hdpi",
    ".\android\app\src\main\res\drawable-mdpi", 
    ".\android\app\src\main\res\drawable-xhdpi",
    ".\android\app\src\main\res\drawable-xxhdpi",
    ".\android\app\src\main\res\drawable-xxxhdpi"
)

# Check if source logo exists
if (-not (Test-Path $sourceLogo))
{
    Write-Host "Error: Source logo not found at $sourceLogo" -ForegroundColor Red
    exit 1
}

Write-Host "Found source logo: $sourceLogo" -ForegroundColor Green

# Backup existing splash screens
$backupDir = ".\android\app\src\main\res\backup-splash-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
Write-Host "Creating backup at: $backupDir" -ForegroundColor Yellow
New-Item -ItemType Directory -Path $backupDir -Force | Out-Null

foreach ($dir in $drawableDirs)
{
    $splashFile = Join-Path $dir "splashscreen_logo.png"
    
    if (Test-Path $splashFile)
    {
        # Backup existing file
        $density = Split-Path $dir -Leaf
        $backupFile = Join-Path $backupDir "$density-splashscreen_logo.png"
        Copy-Item $splashFile $backupFile -Force
        Write-Host "  Backed up $density" -ForegroundColor Gray
        
        # Copy new logo
        Copy-Item $sourceLogo $splashFile -Force
        Write-Host "Updated $density/splashscreen_logo.png" -ForegroundColor Green
    }
    else
    {
        Write-Host "Skipped $dir (file not found)" -ForegroundColor Yellow
    }
}

Write-Host ""
Write-Host "Splash screen logos updated successfully!" -ForegroundColor Green
Write-Host "Backup location: $backupDir" -ForegroundColor Cyan
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Yellow
Write-Host "  1. Run: npx expo run:android" -ForegroundColor White
Write-Host "  2. Or rebuild your app to see the changes" -ForegroundColor White
Write-Host ""
Write-Host "Tip: For best results in Expo projects, you can also run:" -ForegroundColor Cyan
Write-Host "     npx expo prebuild --clean" -ForegroundColor White
Write-Host "     This regenerates all native files from app.json config" -ForegroundColor Gray
