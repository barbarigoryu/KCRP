import boto3
import re

region     = 'ap-northeast-2'
account_id = '381492199246'
fn         = 'kt-sales-training-chatbot'
bucket     = 'kt-sales-training-web-zyroij'

apigw = boto3.client('apigatewayv2', region_name=region)
lam   = boto3.client('lambda',       region_name=region)
s3    = boto3.client('s3',           region_name=region)

# 1. HTTP API 생성 (CORS 포함)
api = apigw.create_api(
    Name='kt-sales-training-api',
    ProtocolType='HTTP',
    CorsConfiguration={
        'AllowOrigins': ['*'],
        'AllowMethods': ['POST', 'OPTIONS'],
        'AllowHeaders': ['Content-Type'],
        'MaxAge': 300,
    }
)
api_id = api['ApiId']
print('API ID:', api_id)

# 2. Lambda 통합
integ = apigw.create_integration(
    ApiId=api_id,
    IntegrationType='AWS_PROXY',
    IntegrationUri=f'arn:aws:lambda:{region}:{account_id}:function:{fn}',
    PayloadFormatVersion='2.0',
)

# 3. 라우트 및 스테이지
apigw.create_route(
    ApiId=api_id,
    RouteKey='POST /',
    Target='integrations/' + integ['IntegrationId'],
)
apigw.create_stage(ApiId=api_id, StageName='$default', AutoDeploy=True)

# 4. Lambda 호출 권한 부여
lam.add_permission(
    FunctionName=fn,
    StatementId='apigw-invoke',
    Action='lambda:InvokeFunction',
    Principal='apigateway.amazonaws.com',
    SourceArn=f'arn:aws:execute-api:{region}:{account_id}:{api_id}/*/*/*',
)

# 5. S3 HTML 업데이트
api_url = f'https://{api_id}.execute-api.{region}.amazonaws.com/'
print('API URL:', api_url)

obj  = s3.get_object(Bucket=bucket, Key='index.html')
html = obj['Body'].read().decode('utf-8')
html = re.sub(r"const API = '.+?';", f"const API = '{api_url}';", html)

s3.put_object(
    Bucket=bucket, Key='index.html',
    Body=html.encode('utf-8'),
    ContentType='text/html; charset=utf-8',
    CacheControl='no-cache',
)
print('S3 updated!')
print(f'Open: http://{bucket}.s3-website.{region}.amazonaws.com')
