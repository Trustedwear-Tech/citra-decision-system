# Firebase Deployment Script for Windows
# This script builds the app and deploys to Firebase prod or test site
# Usage: .\deploy-simple.ps1 [-Target prod|test|both]  (prompts if omitted)
param([string]$Target)

# Ask for deployment target when not passed as a parameter
if (-not $Target) {
    $Target = Read-Host "Which site to deploy to? (prod/test/both) [both]"
    if ($Target -eq "") { $Target = "both" }
}

# Validate target
if ($Target -notin @("prod", "test", "both")) {
    Write-Host "Error: Target must be 'prod', 'test', or 'both'." -ForegroundColor Red
    exit 1
}

Write-Host "Starting Firebase deployment to $Target..." -ForegroundColor Green

# Firebase auth:
#   - Local (default): use your interactive `firebase login` session (the
#     console-cached credentials). Run `firebase login` once, or
#     `firebase login --reauth` if it expires. This refreshes on demand and
#     does NOT go stale like the CI token did.
#   - CI / headless: set $env:FIREBASE_TOKEN and it is passed as --token.
#
# The legacy .firebase-token file is intentionally NO LONGER read: those
# `firebase login:ci` tokens expire fast (7-day refresh-token cap when the
# OAuth consent screen is in "Testing"), which is what kept breaking deploys.
# To force token auth, set FIREBASE_TOKEN explicitly.
$firebaseToken = $env:FIREBASE_TOKEN
if ($firebaseToken) {
    Write-Host "Using FIREBASE_TOKEN env var for authentication (CI mode)" -ForegroundColor Green
} else {
    Write-Host "Using interactive 'firebase login' session (console credentials)" -ForegroundColor Green
    Write-Host "  If deploy fails with an auth error, run: firebase login --reauth" -ForegroundColor DarkGray
}

# Determine targets
if ($Target -eq "both") {
    $targets = @("test", "prod")
} else {
    $targets = @($Target)
}

# Step 1: Generate build timestamp for cache busting
$buildTimestamp = Get-Date -Format "yyyyMMddHHmmss"
Write-Host "Build timestamp: $buildTimestamp" -ForegroundColor Yellow

# Step 2: Backup current .env file
Write-Host "Backing up current .env file..." -ForegroundColor Yellow
if (Test-Path ".env") {
    Copy-Item ".env" ".env.backup" -Force
    Write-Host "Current .env backed up to .env.backup" -ForegroundColor Green
}

# Process each target
foreach ($currentTarget in $targets) {
    Write-Host "Processing $currentTarget..." -ForegroundColor Yellow

    # Step 3: Copy environment file and inject build timestamp
    if ($currentTarget -eq "test") {
        Write-Host "Setting up test environment..." -ForegroundColor Yellow
        $envFile = ".env.test"
    } else {
        Write-Host "Setting up prod environment..." -ForegroundColor Yellow
        $envFile = ".env.production"
    }

    if (Test-Path $envFile) {
        $envContent = Get-Content $envFile -Raw
        $envContent = $envContent -replace "__BUILD_TIMESTAMP__", $buildTimestamp
        $envContent | Set-Content ".env"
        Write-Host "$currentTarget environment configured with build timestamp" -ForegroundColor Green
    } else {
        Write-Host "Error: $envFile file not found!" -ForegroundColor Red
        # Restore original .env
        if (Test-Path ".env.backup") {
            Move-Item ".env.backup" ".env" -Force
        }
        exit 1
    }

    # Step 4: Clear all caches to ensure fresh build
    Write-Host "Clearing all caches..." -ForegroundColor Yellow
    if (Test-Path "dist") {
        Remove-Item -Recurse -Force "dist"
        Write-Host "Removed old dist folder" -ForegroundColor Green
    }
    if (Test-Path "node_modules\.cache") {
        Remove-Item -Recurse -Force "node_modules\.cache"
        Write-Host "Cleared node_modules cache" -ForegroundColor Green
    }
    if (Test-Path ".expo") {
        Remove-Item -Recurse -Force ".expo"
        Write-Host "Cleared .expo cache" -ForegroundColor Green
    }
    Write-Host "All caches cleared" -ForegroundColor Green

    # Step 5: SEO content - using static files only (no dynamic generation)
    # The IntroScreen is personalized based on user profession, so we use pre-built static SEO pages
    Write-Host "Skipping dynamic SEO generation (using static-seo files only)..." -ForegroundColor Yellow
    Write-Host "Static SEO files in public/static-seo/ will be copied during build" -ForegroundColor Cyan

    # Step 6: Build the app with Metro bundler
    Write-Host "Building app for $currentTarget..." -ForegroundColor Yellow
    Write-Host "Using Metro bundler for $currentTarget build" -ForegroundColor Cyan

    # Set environment variables for build
    $env:NODE_ENV = $currentTarget
    $env:CI = "1"

    # Run the actual build command
    Write-Host "Running: npx expo export --platform web --clear" -ForegroundColor White
    npx expo export --platform web --clear

    if ($LASTEXITCODE -ne 0) {
        Write-Host "Build failed for $currentTarget!" -ForegroundColor Red
        # Restore original .env
        if (Test-Path ".env.backup") {
            Move-Item ".env.backup" ".env" -Force
        }
        exit 1
    }
    Write-Host "Metro build completed successfully for $currentTarget" -ForegroundColor Green

    # Step 7: Verify build output
    # Note: Expo automatically copies all files from public/ to dist/
    # This includes index.html, static-seo/, static-pages/, and all other public assets
    Write-Host "Verifying build output..." -ForegroundColor Yellow
    if (Test-Path "dist") {
        Write-Host "dist folder created successfully" -ForegroundColor Green
        
        # Check SEO files
        if (Test-Path "dist\static-seo") {
            $seoFiles = Get-ChildItem -Path "dist\static-seo" -Filter "*.html" | Measure-Object
            Write-Host "SEO HTML files copied: $($seoFiles.Count)" -ForegroundColor Cyan
        }
        
        if (Test-Path "dist\static") {
            $jsFiles = Get-ChildItem -Path "dist\static" -Recurse -Filter "*.js" | Measure-Object
            $cssFiles = Get-ChildItem -Path "dist\static" -Recurse -Filter "*.css" | Measure-Object
            Write-Host "JS files generated: $($jsFiles.Count)" -ForegroundColor Cyan
            Write-Host "CSS files generated: $($cssFiles.Count)" -ForegroundColor Cyan
        }
        
        # List main files
        $allFiles = Get-ChildItem -Path "dist" -Filter "*.*" | Select-Object -First 10
        Write-Host "Main files in dist:" -ForegroundColor Cyan
        foreach ($file in $allFiles) {
            Write-Host "  - $($file.Name)" -ForegroundColor White
        }
    } else {
        Write-Host "dist folder not created - build may have failed for $currentTarget" -ForegroundColor Red
        # Restore original .env
        if (Test-Path ".env.backup") {
            Move-Item ".env.backup" ".env" -Force
        }
        exit 1
    }

    # Step 9: Deploy to Firebase
    Write-Host "Deploying to Firebase ($currentTarget target)..." -ForegroundColor Yellow

    # Deploy command based on target
    $deployArgs = @("deploy", "--only", "hosting:$currentTarget", "--project", "citra-ai-6b291")
    if ($firebaseToken) {
        $deployArgs += @("--token", $firebaseToken)
    }
    Write-Host "Running deploy command: firebase deploy --only hosting:$currentTarget --project citra-ai-6b291" -ForegroundColor Cyan

    # Execute the command (token passed as argument, never echoed)
    firebase @deployArgs

    if ($LASTEXITCODE -ne 0) {
        Write-Host "Firebase deployment failed for $currentTarget!" -ForegroundColor Red
        # Restore original .env
        if (Test-Path ".env.backup") {
            Move-Item ".env.backup" ".env" -Force
        }
        exit 1
    } else {
        Write-Host "Firebase deployment successful for $currentTarget!" -ForegroundColor Green
        if ($currentTarget -eq "prod") {
            Write-Host "Your prod site is now live at: https://citra-ai.web.app" -ForegroundColor Cyan
        } else {
            Write-Host "Your test site is now live at: https://citra-ai-test.web.app" -ForegroundColor Cyan
        }
    }
    Write-Host "###################################################################" -ForegroundColor Cyan
    Write-Host "###################################################################" -ForegroundColor Cyan
    Write-Host "###################################################################" -ForegroundColor Cyan
    Write-Host "###################################################################" -ForegroundColor Cyan
    Write-Host "###################################################################" -ForegroundColor Cyan
}

# Step 10: Restore original .env file
Write-Host "Restoring original environment..." -ForegroundColor Yellow
if (Test-Path ".env.backup") {
    Move-Item ".env.backup" ".env" -Force
    Write-Host "Original .env restored" -ForegroundColor Green
}

Write-Host "Deployment process completed!" -ForegroundColor Green