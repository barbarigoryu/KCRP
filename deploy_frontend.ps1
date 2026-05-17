# deploy_frontend.ps1 - 프론트엔드 HTML만 S3에 업데이트
# Usage: .\deploy_frontend.ps1

param(
    [string]$Region  = "ap-northeast-2",
    [string]$Bucket  = "kt-sales-training-web-zyroij",
    [string]$ApiName = "kt-sales-training-api",
    [string]$LambdaName = "kt-sales-training-chatbot"
)

$utf8NoBom = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = $utf8NoBom
$OutputEncoding           = $utf8NoBom

function Info { param($m) Write-Host "[INFO]  $m" -ForegroundColor Cyan }
function Ok   { param($m) Write-Host "[OK]    $m" -ForegroundColor Green }
function Fail { param($m) Write-Host "[ERROR] $m" -ForegroundColor Red; exit 1 }

Info "Fetching API Gateway URL..."
$ApiEndpoint = aws apigatewayv2 get-apis --region $Region --query "Items[?Name=='$ApiName'].ApiEndpoint" --output text
if (-not $ApiEndpoint) { Fail "API Gateway '$ApiName' not found. Check -ApiName parameter." }
$ApiUrl = $ApiEndpoint.TrimEnd('/') + '/'
Ok "API URL: $ApiUrl"

Info "Fetching TRAINING_API_KEY from Lambda..."
$LambdaEnvJson = aws lambda get-function-configuration --function-name $LambdaName --region $Region --query "Environment.Variables" --output json
$LambdaEnv = $LambdaEnvJson | ConvertFrom-Json
$TrainingKey = $LambdaEnv.TRAINING_API_KEY
if (-not $TrainingKey) {
    Write-Host "[WARN]  TRAINING_API_KEY not set in Lambda — API auth will be disabled in HTML" -ForegroundColor Yellow
    $TrainingKey = "SET_API_KEY_HERE"
} else {
    Ok "TRAINING_API_KEY retrieved"
}

Info "Uploading frontend files to S3: $Bucket ..."

$updateScript = @"
import boto3, re

bucket       = '$Bucket'
api_url      = '$ApiUrl'
training_key = '$TrainingKey'
region       = '$Region'

s3 = boto3.client('s3', region_name=region)

# Files that need API URL + API Key injected
for filename in ['training.html', 'script-analyzer.html', 'training-results.html']:
    with open(r'$PSScriptRoot\frontend\\' + filename, encoding='utf-8') as f:
        html = f.read()
    html = re.sub(r"const API\b\s*=\s*'.+?';",     f"const API = '{api_url}';",         html)
    html = re.sub(r"const API_KEY\s*=\s*'.+?';", f"const API_KEY = '{training_key}';", html)
    s3.put_object(Bucket=bucket, Key=filename, Body=html.encode('utf-8'),
                  ContentType='text/html; charset=utf-8', CacheControl='no-cache')
    print(f'uploaded {filename}')

# Main page (no API URL/Key needed)
with open(r'$PSScriptRoot\frontend\index.html', encoding='utf-8') as f:
    html = f.read()
s3.put_object(Bucket=bucket, Key='index.html', Body=html.encode('utf-8'),
              ContentType='text/html; charset=utf-8', CacheControl='no-cache')
print('uploaded index.html')
"@

$updateScript | Out-File "$PSScriptRoot\.tmp_upload.py" -Encoding utf8
python "$PSScriptRoot\.tmp_upload.py"
if ($LASTEXITCODE -ne 0) { Fail "S3 upload failed" }
Remove-Item "$PSScriptRoot\.tmp_upload.py" -Force

Ok "Frontend updated successfully"
Write-Host "  Open: http://$Bucket.s3-website.$Region.amazonaws.com" -ForegroundColor Yellow
