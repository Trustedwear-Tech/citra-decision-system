# Test Credit System APIs
# Run this script while server is running on port 7004

$baseUrl = "http://localhost:7004/api"
# Never hard-code a token: this script ships in a PUBLIC repo, where a literal
# JWT is a credential in source control. Supply one at run time:
#   $env:CITRA_TEST_JWT = "<token>"; ./test-apis.ps1
$token = $env:CITRA_TEST_JWT
if (-not $token) { Write-Error "Set $env:CITRA_TEST_JWT first"; exit 1 }
$headers = @{
    "Authorization" = "Bearer $token"
    "Content-Type" = "application/json"
}

Write-Host "`n==============================================`n" -ForegroundColor Cyan
Write-Host "  Credit System API Test Suite" -ForegroundColor Cyan
Write-Host "`n==============================================`n" -ForegroundColor Cyan

# Test 1: Pricing Info (Public)
Write-Host "[1/5] Testing Pricing Info (public endpoint)..." -ForegroundColor Yellow
try {
    $pricing = Invoke-RestMethod -Uri "$baseUrl/pricing-info" -Method GET
    Write-Host "✅ SUCCESS" -ForegroundColor Green
    Write-Host "   Gemini input: Rs.$($pricing.pricing.token_pricing.gemini.input_per_1k)/1K tokens"
    Write-Host "   Storage: Rs.$($pricing.pricing.storage_pricing.per_mb)/MB"
    Write-Host "   Min purchase: Rs.$($pricing.pricing.credit_purchase.minimum_amount)"
} catch {
    Write-Host "❌ FAILED: $($_.Exception.Message)" -ForegroundColor Red
}

# Test 2: Get Usage Stats (should initialize new user with welcome bonus)
Write-Host "`n[2/5] Testing Get Usage Stats (auto-initialize new user)..." -ForegroundColor Yellow
$newUser = @{
    user_id = "test_api_$((Get-Date).Ticks)"
    email = "test_$(Get-Random)@example.com"
} | ConvertTo-Json
try {
    $stats = Invoke-RestMethod -Uri "$baseUrl/get-credits-usage-stats" -Method POST -Headers $headers -Body $newUser
    Write-Host "✅ SUCCESS" -ForegroundColor Green
    Write-Host "   Balance: Rs.$($stats.usage_stats.credit_balance)"
    Write-Host "   Low balance warning: $($stats.low_balance_warning)"
    $testUserId = ($newUser | ConvertFrom-Json).user_id
} catch {
    Write-Host "❌ FAILED: $($_.Exception.Message)" -ForegroundColor Red
}

# Test 3: Check Credits
Write-Host "`n[3/5] Testing Check Credits..." -ForegroundColor Yellow
$checkBody = @{
    user_id = $testUserId
    required_amount = 0.01
} | ConvertTo-Json
try {
    $check = Invoke-RestMethod -Uri "$baseUrl/check-credits" -Method POST -Headers $headers -Body $checkBody
    Write-Host "✅ SUCCESS" -ForegroundColor Green
    Write-Host "   Sufficient: $($check.sufficient)"
    Write-Host "   Balance: Rs.$($check.balance)"
    Write-Host "   Required: Rs.$($check.required)"
} catch {
    Write-Host "❌ FAILED: $($_.Exception.Message)" -ForegroundColor Red
}

# Test 4: Track Query Usage
Write-Host "`n[4/5] Testing Track Query Usage..." -ForegroundColor Yellow
$queryBody = @{
    user_id = $testUserId
    email = ($newUser | ConvertFrom-Json).email
    model = "gemini"
    input_tokens = 1000
    output_tokens = 500
    query_id = "test_query_$(Get-Random)"
} | ConvertTo-Json
try {
    $track = Invoke-RestMethod -Uri "$baseUrl/track-query-usage" -Method POST -Headers $headers -Body $queryBody
    Write-Host "✅ SUCCESS" -ForegroundColor Green
    Write-Host "   Cost: Rs.$($track.cost)"
    Write-Host "   Remaining balance: Rs.$($track.remaining_balance)"
    Write-Host "   Tokens used: $($track.tokens_used)"
} catch {
    Write-Host "❌ FAILED: $($_.Exception.Message)" -ForegroundColor Red
}

# Test 5: Track Upload Usage
Write-Host "`n[5/5] Testing Track Upload Usage..." -ForegroundColor Yellow
$uploadBody = @{
    user_id = $testUserId
    email = ($newUser | ConvertFrom-Json).email
    file_id = "test_file_$(Get-Random)"
    filename = "test_document.pdf"
    file_size_bytes = 5242880  # 5 MB
    file_type = "application/pdf"
} | ConvertTo-Json
try {
    $upload = Invoke-RestMethod -Uri "$baseUrl/track-upload-usage" -Method POST -Headers $headers -Body $uploadBody
    Write-Host "✅ SUCCESS" -ForegroundColor Green
    Write-Host "   Cost: Rs.$($upload.cost)"
    Write-Host "   Remaining balance: Rs.$($upload.remaining_balance)"
    Write-Host "   File size: $($upload.file_size_mb) MB"
} catch {
    Write-Host "❌ FAILED: $($_.Exception.Message)" -ForegroundColor Red
}

# Final Stats
Write-Host "`n==============================================`n" -ForegroundColor Cyan
Write-Host "Getting final usage stats..." -ForegroundColor Yellow
try {
    $final = Invoke-RestMethod -Uri "$baseUrl/get-credits-usage-stats" -Method POST -Headers $headers -Body $newUser
    Write-Host "✅ Final Balance: Rs.$($final.usage_stats.credit_balance)" -ForegroundColor Green
    Write-Host "   Total consumed: Rs.$($final.usage_stats.total_credits_consumed)"
    Write-Host "   Total queries: $($final.usage_stats.query_usage.total_queries)"
    Write-Host "   Total uploads: $($final.usage_stats.file_upload_usage.total_files)"
    Write-Host "   Recent transactions: $($final.usage_stats.recent_transactions.Count)"
} catch {
    Write-Host "❌ FAILED: $($_.Exception.Message)" -ForegroundColor Red
}

Write-Host "`n==============================================`n" -ForegroundColor Cyan
