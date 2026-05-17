# deploy.ps1 - KT iPhone Sales Training Chatbot AWS Deployment
# Usage: .\deploy.ps1 -AnthropicApiKey "sk-ant-..."

param(
    [string]$AnthropicApiKey = "",
    [string]$Region          = "ap-northeast-2",
    [string]$Prefix          = "kt-sales-training"
)

$ErrorActionPreference = "Continue"
$utf8NoBom = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = $utf8NoBom
$OutputEncoding           = $utf8NoBom

function Info { param($m) Write-Host "[INFO]  $m" -ForegroundColor Cyan }
function Ok   { param($m) Write-Host "[OK]    $m" -ForegroundColor Green }
function Warn { param($m) Write-Host "[WARN]  $m" -ForegroundColor Yellow }
function Fail { param($m) Write-Host "[ERROR] $m" -ForegroundColor Red; exit 1 }

function Test-AwsResource {
    param([scriptblock]$Cmd)
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "SilentlyContinue"
    & $Cmd 2>$null | Out-Null
    $ok = ($LASTEXITCODE -eq 0)
    $ErrorActionPreference = $prev
    return $ok
}

$TmpDir = "$PSScriptRoot\.tmp"
if (-not (Test-Path $TmpDir)) { New-Item -ItemType Directory -Path $TmpDir | Out-Null }

# ── Prerequisites ─────────────────────────────────────────────────────────────
Info "Checking prerequisites..."
if (-not (Get-Command aws    -ErrorAction SilentlyContinue)) { Fail "AWS CLI not found." }
if (-not (Get-Command python -ErrorAction SilentlyContinue)) { Fail "Python not found." }
if (-not (Test-AwsResource { aws sts get-caller-identity --region $Region })) { Fail "AWS credentials not configured. Run 'aws configure'." }
Ok "AWS credentials OK"

if (-not $AnthropicApiKey) { $AnthropicApiKey = Read-Host "Enter Anthropic API Key (sk-ant-...)" }
if (-not $AnthropicApiKey) { Fail "Anthropic API Key is required." }

# ── Resource names ────────────────────────────────────────────────────────────
$Suffix     = -join ((97..122) | Get-Random -Count 6 | ForEach-Object { [char]$_ })
$BucketName = "$Prefix-web-$Suffix"
$LambdaName = "$Prefix-chatbot"
$RoleName   = "$Prefix-lambda-role"
Info "Resources: bucket=$BucketName / lambda=$LambdaName"

# ── IAM Role ──────────────────────────────────────────────────────────────────
Info "Setting up IAM role..."
$TrustFile = "$TmpDir\trust.json"
$trustJson = '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"lambda.amazonaws.com"},"Action":"sts:AssumeRole"}]}'
[System.IO.File]::WriteAllText($TrustFile, $trustJson, $utf8NoBom)

$RoleArn = ""
if (Test-AwsResource { aws iam get-role --role-name $RoleName }) {
    Warn "IAM role already exists. Reusing."
    $RoleArn = (aws iam get-role --role-name $RoleName | ConvertFrom-Json).Role.Arn
} else {
    $RoleArn = (aws iam create-role --role-name $RoleName --assume-role-policy-document "file://$TrustFile" | ConvertFrom-Json).Role.Arn
    Ok "IAM role created: $RoleArn"
}
if (-not $RoleArn) { Fail "Failed to get IAM RoleArn." }
aws iam attach-role-policy --role-name $RoleName --policy-arn "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole" | Out-Null
Ok "IAM policy attached"
Info "Waiting for IAM propagation (12s)..."
Start-Sleep -Seconds 12

# ── Lambda Package (Linux x86_64 compatible) ──────────────────────────────────
Info "Building Lambda package (Linux x86_64)..."
$BuildDir = "$PSScriptRoot\build"
if (Test-Path $BuildDir) { Remove-Item $BuildDir -Recurse -Force }
New-Item -ItemType Directory -Path $BuildDir | Out-Null

python -m pip install anthropic -t $BuildDir --quiet `
    --platform manylinux2014_x86_64 --python-version 3.12 `
    --only-binary=:all: --implementation cp
if ($LASTEXITCODE -ne 0) { Fail "pip install failed" }

Copy-Item "$PSScriptRoot\lambda\handler.py" "$BuildDir\handler.py"
$ZipPath = "$PSScriptRoot\lambda.zip"
if (Test-Path $ZipPath) { Remove-Item $ZipPath -Force }
Compress-Archive -Path "$BuildDir\*" -DestinationPath $ZipPath
Ok "Lambda package built"

# ── Deploy Lambda ─────────────────────────────────────────────────────────────
Info "Deploying Lambda function..."
$EnvVars = "Variables={ANTHROPIC_API_KEY=$AnthropicApiKey}"
$ZipArg  = "fileb://$ZipPath"

if (Test-AwsResource { aws lambda get-function --function-name $LambdaName --region $Region }) {
    Warn "Lambda already exists. Updating..."
    aws lambda update-function-code --function-name $LambdaName --zip-file $ZipArg --region $Region | Out-Null
    Start-Sleep -Seconds 5
    aws lambda update-function-configuration --function-name $LambdaName --environment $EnvVars --region $Region | Out-Null
} else {
    aws lambda create-function --function-name $LambdaName --runtime python3.12 --role $RoleArn --handler handler.lambda_handler --zip-file $ZipArg --timeout 30 --memory-size 256 --environment $EnvVars --region $Region | Out-Null
    if ($LASTEXITCODE -ne 0) { Fail "Lambda create failed" }
}
Ok "Lambda deployed"

# ── S3 Bucket ─────────────────────────────────────────────────────────────────
Info "Creating S3 bucket: $BucketName"
if ($Region -eq "us-east-1") {
    aws s3api create-bucket --bucket $BucketName --region $Region | Out-Null
} else {
    aws s3api create-bucket --bucket $BucketName --region $Region --create-bucket-configuration "LocationConstraint=$Region" | Out-Null
}
if ($LASTEXITCODE -ne 0) { Fail "S3 bucket creation failed" }

aws s3api put-public-access-block --bucket $BucketName --public-access-block-configuration "BlockPublicAcls=false,IgnorePublicAcls=false,BlockPublicPolicy=false,RestrictPublicBuckets=false" | Out-Null

$PolicyFile = "$TmpDir\bucket-policy.json"
$bucketPolicy = @"
{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":"*","Action":"s3:GetObject","Resource":"arn:aws:s3:::$BucketName/*"}]}
"@
[System.IO.File]::WriteAllText($PolicyFile, $bucketPolicy.Trim(), $utf8NoBom)
aws s3api put-bucket-policy --bucket $BucketName --policy "file://$PolicyFile" | Out-Null
aws s3 website "s3://$BucketName" --index-document index.html | Out-Null
Ok "S3 bucket configured"

# ── API Gateway + HTML via Python (avoids PowerShell JSON quoting issues) ─────
Info "Setting up API Gateway and uploading frontend..."

$AccountId = (aws sts get-caller-identity | ConvertFrom-Json).Account

$setupScript = @"
import boto3, re

region     = '$Region'
account_id = '$AccountId'
fn         = '$LambdaName'
bucket     = '$BucketName'
api_key    = '$AnthropicApiKey'

apigw = boto3.client('apigatewayv2', region_name=region)
lam   = boto3.client('lambda',       region_name=region)
s3    = boto3.client('s3',           region_name=region)

api = apigw.create_api(
    Name='$Prefix-api',
    ProtocolType='HTTP',
    CorsConfiguration={
        'AllowOrigins': ['*'],
        'AllowMethods': ['POST', 'OPTIONS'],
        'AllowHeaders': ['Content-Type'],
        'MaxAge': 300,
    }
)
api_id = api['ApiId']
integ = apigw.create_integration(
    ApiId=api_id, IntegrationType='AWS_PROXY',
    IntegrationUri=f'arn:aws:lambda:{region}:{account_id}:function:{fn}',
    PayloadFormatVersion='2.0'
)
apigw.create_route(ApiId=api_id, RouteKey='POST /', Target='integrations/'+integ['IntegrationId'])
apigw.create_stage(ApiId=api_id, StageName='\$default', AutoDeploy=True)
lam.add_permission(
    FunctionName=fn, StatementId='apigw-invoke',
    Action='lambda:InvokeFunction', Principal='apigateway.amazonaws.com',
    SourceArn=f'arn:aws:execute-api:{region}:{account_id}:{api_id}/*/*/*'
)

api_url = f'https://{api_id}.execute-api.{region}.amazonaws.com/'
print('API_URL=' + api_url)

with open(r'$PSScriptRoot\frontend\index.html', encoding='utf-8') as f:
    html = f.read()
html = re.sub(r"const API = '.+?';", f"const API = '{api_url}';", html)
s3.put_object(Bucket=bucket, Key='index.html', Body=html.encode('utf-8'),
              ContentType='text/html; charset=utf-8', CacheControl='no-cache')
print('S3_URL=http://{}.s3-website.{}.amazonaws.com'.format(bucket, region))
"@

$setupScript | Out-File "$TmpDir\setup.py" -Encoding utf8
$output = python "$TmpDir\setup.py"
if ($LASTEXITCODE -ne 0) { Fail "API Gateway setup failed" }

$ApiUrl = ($output | Where-Object { $_ -match '^API_URL=' }) -replace 'API_URL=', ''
$WebUrl = ($output | Where-Object { $_ -match '^S3_URL=' })  -replace 'S3_URL=', ''
Ok "API Gateway and frontend deployed"

# ── Cleanup ───────────────────────────────────────────────────────────────────
Remove-Item $BuildDir -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item $ZipPath  -Force         -ErrorAction SilentlyContinue
Remove-Item $TmpDir   -Recurse -Force -ErrorAction SilentlyContinue

# ── Done ──────────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "======================================================" -ForegroundColor Green
Write-Host "  Deployment Complete!" -ForegroundColor Green
Write-Host "======================================================" -ForegroundColor Green
Write-Host "  Website URL : $WebUrl"  -ForegroundColor Yellow
Write-Host "  API URL     : $ApiUrl"  -ForegroundColor Yellow
Write-Host "  S3 Bucket   : $BucketName" -ForegroundColor Yellow
Write-Host "  Lambda Func : $LambdaName" -ForegroundColor Yellow
Write-Host ""
Write-Host "  Open the Website URL in your browser to start training." -ForegroundColor Cyan
Write-Host ""
