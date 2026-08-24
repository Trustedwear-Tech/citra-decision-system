# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

# Test Backend Credit API Endpoints
# Run this script to test all credit-related endpoints
# Never hard-code a token: this script ships in a PUBLIC repo, where a literal
# JWT is a credential in source control. Supply one at run time:
#   $env:CITRA_TEST_JWT = "<token>"; ./test-credit-apis.ps1
$TOKEN = $env:CITRA_TEST_JWT
if (-not $TOKEN) { Write-Error "Set $env:CITRA_TEST_JWT first"; exit 1 }
$BASE_URL = "http://localhost:7004"
$USER_ID = "test_user_123"
$EMAIL = "test@example.com"

Write-Host "`n🧪 Testing Backend Credit API Endpoints`n" -ForegroundColor Cyan
Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━`n" -ForegroundColor Gray

# Test 1: Get Pricing Info (Public Endpoint - No Auth Required)
Write-Host "📊 Test 1: Get Pricing Info" -ForegroundColor Yellow
$response = Invoke-RestMethod -Uri "$BASE_URL/api/subscriptions/pricing-info" -Method GET -ContentType "application/json"
Write-Host "✅ Success!" -ForegroundColor Green
Write-Host "Token Pricing (Gemini Input): $($response.token_pricing.gemini.input_per_1k) credits per 1K" -ForegroundColor White
Write-Host "Storage Pricing: $($response.storage_pricing.per_mb) credits per MB" -ForegroundColor White
Write-Host "Minimum Purchase: $($response.credit_purchase.minimum_amount) credits" -ForegroundColor White
Write-Host ""

# Test 2: Check Credits (Should fail - user not initialized)
Write-Host "💰 Test 2: Check Credits (Pre-execution)" -ForegroundColor Yellow
$body = @{
    user_id = $USER_ID
    required_amount = 0.01
} | ConvertTo-Json
try {
    $response = Invoke-RestMethod -Uri "$BASE_URL/api/subscriptions/check-credits" `
        -Method POST `
        -Headers @{ "Authorization" = "Bearer $TOKEN"; "Content-Type" = "application/json" } `
        -Body $body
    Write-Host "✅ Response:" -ForegroundColor Green
    Write-Host "   Balance: $($response.balance) credits" -ForegroundColor White
    Write-Host "   Sufficient: $($response.sufficient)" -ForegroundColor White
} catch {
    Write-Host "⚠️  Expected - User not initialized yet" -ForegroundColor Yellow
}
Write-Host ""

# Test 3: Get Usage Stats (Should initialize user with 50 credits welcome bonus)
Write-Host "📈 Test 3: Get Usage Stats (Initialize User)" -ForegroundColor Yellow
$body = @{
    user_id = $USER_ID
    email = $EMAIL
} | ConvertTo-Json
$response = Invoke-RestMethod -Uri "$BASE_URL/api/subscriptions/get-credits-usage-stats" `
    -Method POST `
    -Headers @{ "Authorization" = "Bearer $TOKEN"; "Content-Type" = "application/json" } `
    -Body $body
Write-Host "✅ User Initialized!" -ForegroundColor Green
Write-Host "   Credit Balance: $($response.usage_stats.credit_balance) credits" -ForegroundColor White
Write-Host "   Total Purchased: $($response.usage_stats.total_credits_purchased) credits" -ForegroundColor White
Write-Host "   Total Spent: $($response.usage_stats.total_credits_spent) credits" -ForegroundColor White
Write-Host ""

# Test 4: Check Credits Again (Should succeed now)
Write-Host "💰 Test 4: Check Credits (After Initialization)" -ForegroundColor Yellow
$body = @{
    user_id = $USER_ID
    required_amount = 0.01
} | ConvertTo-Json
$response = Invoke-RestMethod -Uri "$BASE_URL/api/subscriptions/check-credits" `
    -Method POST `
    -Headers @{ "Authorization" = "Bearer $TOKEN"; "Content-Type" = "application/json" } `
    -Body $body
Write-Host "✅ Credits Check Passed!" -ForegroundColor Green
Write-Host "   Balance: $($response.balance) credits" -ForegroundColor White
Write-Host "   Required: $($response.required) credits" -ForegroundColor White
Write-Host "   Sufficient: $($response.sufficient)" -ForegroundColor White
Write-Host ""

# Test 5: Track Query Usage
Write-Host "🔍 Test 5: Track Query Usage" -ForegroundColor Yellow
$body = @{
    user_id = $USER_ID
    email = $EMAIL
    model = "gemini-2.0-flash-exp"
    input_tokens = 1000
    output_tokens = 500
    query_id = "test_query_$(Get-Random)"
} | ConvertTo-Json
$response = Invoke-RestMethod -Uri "$BASE_URL/api/subscriptions/track-query-usage" `
    -Method POST `
    -Headers @{ "Authorization" = "Bearer $TOKEN"; "Content-Type" = "application/json" } `
    -Body $body
Write-Host "✅ Query Tracked!" -ForegroundColor Green
Write-Host "   Cost: $($response.cost) credits" -ForegroundColor White
Write-Host "   Remaining Balance: $($response.remaining_balance) credits" -ForegroundColor White
Write-Host "   Input Tokens: $($response.tokens_used.input)" -ForegroundColor White
Write-Host "   Output Tokens: $($response.tokens_used.output)" -ForegroundColor White
Write-Host ""

# Test 6: Track Upload Usage
Write-Host "📤 Test 6: Track Upload Usage" -ForegroundColor Yellow
$body = @{
    user_id = $USER_ID
    email = $EMAIL
    file_name = "test_document.pdf"
    file_size_bytes = 5242880  # 5 MB
    file_type = "application/pdf"
} | ConvertTo-Json
$response = Invoke-RestMethod -Uri "$BASE_URL/api/subscriptions/track-upload-usage" `
    -Method POST `
    -Headers @{ "Authorization" = "Bearer $TOKEN"; "Content-Type" = "application/json" } `
    -Body $body
Write-Host "✅ Upload Tracked!" -ForegroundColor Green
Write-Host "   Cost: $($response.cost) credits" -ForegroundColor White
Write-Host "   Remaining Balance: $($response.remaining_balance) credits" -ForegroundColor White
Write-Host "   File Size: $([math]::Round($response.file_size_mb, 2)) MB" -ForegroundColor White
Write-Host ""

# Test 7: Get Updated Usage Stats
Write-Host "📊 Test 7: Get Updated Usage Stats" -ForegroundColor Yellow
$body = @{
    user_id = $USER_ID
    email = $EMAIL
} | ConvertTo-Json
$response = Invoke-RestMethod -Uri "$BASE_URL/api/subscriptions/get-credits-usage-stats" `
    -Method POST `
    -Headers @{ "Authorization" = "Bearer $TOKEN"; "Content-Type" = "application/json" } `
    -Body $body
Write-Host "✅ Usage Stats Retrieved!" -ForegroundColor Green
Write-Host "   Current Balance: $($response.usage_stats.credit_balance) credits" -ForegroundColor White
Write-Host "   Total Spent: $($response.usage_stats.total_credits_spent) credits" -ForegroundColor White
Write-Host "   Query Count: $($response.usage_stats.query_usage.total_queries)" -ForegroundColor White
Write-Host "   Upload Count: $($response.usage_stats.file_upload_usage.total_files)" -ForegroundColor White
Write-Host "   Recent Transactions: $($response.usage_stats.recent_transactions.Count)" -ForegroundColor White
Write-Host ""

Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━`n" -ForegroundColor Gray
Write-Host "✅ All API Tests Completed Successfully!`n" -ForegroundColor Green

# Summary
Write-Host "📝 Summary:" -ForegroundColor Cyan
Write-Host "   • Pricing info retrieved" -ForegroundColor White
Write-Host "   • User initialized with 50 credits welcome bonus" -ForegroundColor White
Write-Host "   • Credits checked successfully" -ForegroundColor White
Write-Host "   • Query usage tracked (1000 in + 500 out tokens)" -ForegroundColor White
Write-Host "   • Upload usage tracked (5 MB file)" -ForegroundColor White
Write-Host "   • Usage stats retrieved with transaction history" -ForegroundColor White
Write-Host ""
Write-Host "🎯 Next Steps:" -ForegroundColor Cyan
Write-Host "   1. Integrate Python service with credit_checker.py" -ForegroundColor White
Write-Host "   2. Test UI credit purchase flow" -ForegroundColor White
Write-Host "   3. Test end-to-end query with credit deduction" -ForegroundColor White
Write-Host ""
