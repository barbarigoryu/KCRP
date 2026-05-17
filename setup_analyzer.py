"""
setup_analyzer.py
- Creates a private S3 data bucket for Athena query targets
- Updates Lambda env vars: DATA_BUCKET, TRAINING_API_KEY
- Uploads all frontend HTML files to the web S3 bucket

Usage: python setup_analyzer.py
"""
import boto3
import json
import os
import re
import secrets
import sys

REGION      = 'ap-northeast-2'
LAMBDA_NAME = 'kt-sales-training-chatbot'
WEB_BUCKET  = 'kt-sales-training-web-zyroij'
API_NAME    = 'kt-sales-training-api'

s3     = boto3.client('s3',            region_name=REGION)
lam    = boto3.client('lambda',        region_name=REGION)
apigw  = boto3.client('apigatewayv2',  region_name=REGION)
sts    = boto3.client('sts',           region_name=REGION)

# ── 1. Resolve account / data bucket name ─────────────────────────────────────
account_id  = sts.get_caller_identity()['Account']
data_bucket = f'kt-sales-training-data-{account_id[-6:]}'
print(f'[INFO] Data bucket target: {data_bucket}')

# ── 2. Create data bucket (private, no public access) ─────────────────────────
try:
    s3.head_bucket(Bucket=data_bucket)
    print(f'[WARN] Data bucket already exists: {data_bucket}')
except s3.exceptions.ClientError as e:
    if e.response['Error']['Code'] == '404':
        if REGION == 'us-east-1':
            s3.create_bucket(Bucket=data_bucket)
        else:
            s3.create_bucket(
                Bucket=data_bucket,
                CreateBucketConfiguration={'LocationConstraint': REGION}
            )
        s3.put_public_access_block(
            Bucket=data_bucket,
            PublicAccessBlockConfiguration={
                'BlockPublicAcls': True,
                'IgnorePublicAcls': True,
                'BlockPublicPolicy': True,
                'RestrictPublicBuckets': True,
            }
        )
        print(f'[OK]   Data bucket created: {data_bucket}')
    else:
        raise

# ── 3. Update Lambda env vars (DATA_BUCKET + TRAINING_API_KEY) ────────────────
current  = lam.get_function_configuration(FunctionName=LAMBDA_NAME)
env_vars = current.get('Environment', {}).get('Variables', {})

env_vars['DATA_BUCKET'] = data_bucket

if 'TRAINING_API_KEY' not in env_vars or not env_vars['TRAINING_API_KEY']:
    training_key = secrets.token_urlsafe(32)
    env_vars['TRAINING_API_KEY'] = training_key
    print(f'[OK]   Generated new TRAINING_API_KEY')
else:
    training_key = env_vars['TRAINING_API_KEY']
    print(f'[INFO] TRAINING_API_KEY already set (reusing)')

lam.update_function_configuration(
    FunctionName=LAMBDA_NAME,
    Environment={'Variables': env_vars}
)
print(f'[OK]   Lambda env updated: DATA_BUCKET={data_bucket}')

# ── 4. Fetch current API URL ───────────────────────────────────────────────────
apis = apigw.get_apis()['Items']
api_matches = [a for a in apis if a['Name'] == API_NAME]
if not api_matches:
    print(f'[ERROR] API Gateway "{API_NAME}" not found. Run setup_apigw.py first.')
    sys.exit(1)

api_id  = api_matches[0]['ApiId']
api_url = f'https://{api_id}.execute-api.{REGION}.amazonaws.com/'
print(f'[INFO] API URL: {api_url}')

# ── 5. Upload all frontend files ──────────────────────────────────────────────
frontend_dir = os.path.join(os.path.dirname(__file__), 'frontend')

# Files that need API URL + API Key injected
for filename in ['training.html', 'script-analyzer.html', 'training-results.html']:
    with open(os.path.join(frontend_dir, filename), encoding='utf-8') as f:
        html = f.read()
    html = re.sub(r"const API\b\s*=\s*'.+?';",     f"const API = '{api_url}';",         html)
    html = re.sub(r"const API_KEY\s*=\s*'.+?';", f"const API_KEY = '{training_key}';", html)
    s3.put_object(
        Bucket=WEB_BUCKET, Key=filename,
        Body=html.encode('utf-8'),
        ContentType='text/html; charset=utf-8',
        CacheControl='no-cache',
    )
    print(f'[OK]   {filename} uploaded to s3://{WEB_BUCKET}/')

# Main page (no API URL/Key needed)
with open(os.path.join(frontend_dir, 'index.html'), encoding='utf-8') as f:
    html = f.read()
s3.put_object(
    Bucket=WEB_BUCKET, Key='index.html',
    Body=html.encode('utf-8'),
    ContentType='text/html; charset=utf-8',
    CacheControl='no-cache',
)
print(f'[OK]   index.html uploaded to s3://{WEB_BUCKET}/')

# ── Done ──────────────────────────────────────────────────────────────────────
web_url = f'http://{WEB_BUCKET}.s3-website.{REGION}.amazonaws.com'
print()
print('=' * 60)
print('  Setup complete!')
print('=' * 60)
print(f'  Data bucket : {data_bucket}')
print(f'  API Key     : {training_key}')
print(f'  Web URL     : {web_url}')
print()
print('  NOTE: Redeploy Lambda code to activate all handlers:')
print('        .\\deploy_lambda.ps1')
print()
