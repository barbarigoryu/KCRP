# deploy_lambda.ps1 - Lambda 함수 코드만 업데이트
# Usage: .\deploy_lambda.ps1

param(
    [string]$Region     = "ap-northeast-2",
    [string]$LambdaName = "kt-sales-training-chatbot"
)

$utf8NoBom = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = $utf8NoBom
$OutputEncoding           = $utf8NoBom

function Info { param($m) Write-Host "[INFO]  $m" -ForegroundColor Cyan }
function Ok   { param($m) Write-Host "[OK]    $m" -ForegroundColor Green }
function Fail { param($m) Write-Host "[ERROR] $m" -ForegroundColor Red; exit 1 }

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
Ok "Package built"

Info "Uploading to Lambda: $LambdaName ..."
aws lambda update-function-code --function-name $LambdaName --zip-file "fileb://$ZipPath" --region $Region | Out-Null
if ($LASTEXITCODE -ne 0) { Fail "Lambda update failed" }

Ok "Lambda updated successfully"

Remove-Item $BuildDir -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item $ZipPath  -Force         -ErrorAction SilentlyContinue
