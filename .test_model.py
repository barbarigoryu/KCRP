import boto3, json, urllib.request

lam = boto3.client('lambda', region_name='ap-northeast-2')
cfg = lam.get_function_configuration(FunctionName='kt-sales-training-chatbot')
api_key = cfg['Environment']['Variables']['ANTHROPIC_API_KEY']

PROMPTS_PROMPT = """다음 영업 스크립트를 기반으로 AI 훈련 에이전트 4종의 시스템 프롬프트를 작성해 JSON으로만 응답하세요 (코드 블록 없이):

## 영업 스크립트
---
1단계: 고객 인사 - 안녕하세요 고객님. KT 대리점에 오신 것을 환영합니다.
2단계: 니즈 파악 - 고객님 현재 사용 중인 기기가 어떻게 되세요?
---

{
  "persona_prompt": "고객 페르소나 에이전트 시스템 프롬프트",
  "hint_prompt": "힌트 에이전트 시스템 프롬프트",
  "evaluation_prompt": "평가 에이전트 시스템 프롬프트",
  "recommendation_prompt": "추천 에이전트 시스템 프롬프트"
}"""

payload = json.dumps({
    'model': 'claude-haiku-4-5-20251001',
    'max_tokens': 2500,
    'messages': [{'role': 'user', 'content': PROMPTS_PROMPT}]
}).encode('utf-8')

req = urllib.request.Request(
    'https://api.anthropic.com/v1/messages',
    data=payload,
    headers={
        'Content-Type': 'application/json',
        'x-api-key': api_key,
        'anthropic-version': '2023-06-01'
    },
    method='POST'
)

try:
    with urllib.request.urlopen(req, timeout=40) as r:
        body = json.loads(r.read())
        text = body['content'][0]['text']
        print('model:', body.get('model'))
        print('stop_reason:', body.get('stop_reason'))
        print('output_tokens:', body['usage']['output_tokens'])
        print('text_length:', len(text))
        print('--- response ---')
        print(text[:2000])
except Exception as e:
    print('HTTP ERROR:', e)
    try:
        print(e.read().decode())
    except:
        pass
