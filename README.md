# KT 영업 교육 AI 플랫폼

KT 대리점 영업사원을 위한 AI 기반 영업 훈련 시스템입니다.  
영업 스크립트를 업로드하면 AI가 단계·에이전트 프롬프트를 자동 추출하고, AI 고객 페르소나와 실전 롤플레이 훈련을 제공합니다.

---

## 주요 기능

| 기능 | 설명 |
|---|---|
| **스크립트 분석** | `.txt/.md` 영업 대본 업로드 → AI가 단계·Key Action·에이전트 프롬프트 자동 추출 |
| **AI 훈련 Agent** | AI 고객과 단계별 롤플레이 대화 · 실시간 힌트 · 자동 단계 진행 |
| **훈련 평가** | 세션 종료 후 단계별 완료 여부·누락 항목 피드백·진행률 제공 |
| **맞춤 훈련 추천** | 약점 기반 페르소나 변형 / 반론 대응 / 시나리오 퀴즈 추천 |
| **훈련 기록** | 과거 훈련 결과·평가 내용·전체 대화 이력 조회 |

---

## 아키텍처

```
[S3 Static Website]          [API Gateway HTTP API]     [Lambda (Python 3.12)]
  index.html       ──POST──▶  kt-sales-training-api  ──▶  handler.py
  script-analyzer.html                                      │
  training.html               X-Training-Key 인증           ├─ Anthropic Claude API
  training-results.html                                     │   ├─ Haiku 4.5 (Master·Persona·Hint·Recommend)
                                                            │   └─ Sonnet 4.6 (Evaluate)
                                                            └─ S3 (ap-northeast-2)
                                                                ├─ training-configs/   ← 스크립트 JSON
                                                                └─ training-sessions/  ← 훈련 기록 JSON
```

### 멀티 에이전트 구조

훈련 대화 1턴마다 **Master + Persona** 에이전트가 병렬 실행됩니다.

| 에이전트 | 모델 | 역할 |
|---|---|---|
| **Master** | Haiku | 단계별 Key Action 달성 여부 판정 → 단계 자동 진행 |
| **Persona** | Haiku | AI 고객 역할 (1~2문장 짧은 응답) |
| **Hint** | Haiku | 실시간 코칭 힌트 (대화 흐름과 별도) |
| **Evaluation** | Sonnet | 세션 종료 후 단계별 체크리스트 평가 |
| **Recommendation** | Haiku | 약점 분석 기반 맞춤 훈련 추천 |

---

## 프로젝트 구조

```
KCRP/
├── frontend/
│   ├── index.html              # 메인 랜딩 페이지
│   ├── script-analyzer.html    # 스크립트 분석기
│   ├── training.html           # 훈련 Agent (4-screen SPA)
│   └── training-results.html   # 훈련 기록 조회
├── lambda/
│   ├── handler.py              # Lambda 핸들러 (전체 API 로직)
│   └── requirements.txt        # anthropic SDK
├── setup_apigw.py              # API Gateway 최초 생성 (1회 실행)
├── setup_analyzer.py           # 데이터 버킷·API Key 초기 설정 (1회 실행)
├── deploy_lambda.ps1           # Lambda 코드 배포
└── deploy_frontend.ps1         # 프론트엔드 S3 업로드
```

---

## 사전 준비

- AWS CLI 설정 (`aws configure`) — IAM 권한: Lambda, API Gateway, S3, STS
- Python 3.12+
- PowerShell 5.1+ (Windows)
- Anthropic API Key

---

## 초기 설정

> **최초 1회만 실행합니다.**

### 1. Lambda 함수 생성

AWS 콘솔 또는 CLI로 Lambda 함수를 먼저 생성합니다.

| 항목 | 값 |
|---|---|
| 함수명 | `kt-sales-training-chatbot` |
| 런타임 | Python 3.12 |
| 제한 시간 | 120초 |
| 메모리 | 256MB 이상 |
| 환경변수 | `ANTHROPIC_API_KEY` = (Anthropic API 키) |

IAM 실행 역할에 다음 S3 권한을 추가하세요.

```json
{
  "Effect": "Allow",
  "Action": ["s3:PutObject", "s3:GetObject", "s3:ListBucket"],
  "Resource": [
    "arn:aws:s3:::kt-sales-training-data-*",
    "arn:aws:s3:::kt-sales-training-data-*/*"
  ]
}
```

### 2. S3 웹 버킷 생성

```bash
# 버킷 생성 (이름은 전역 고유해야 함)
aws s3 mb s3://kt-sales-training-web-<suffix> --region ap-northeast-2

# 정적 웹사이트 호스팅 활성화
aws s3 website s3://kt-sales-training-web-<suffix> \
  --index-document index.html

# 퍼블릭 읽기 버킷 정책 적용
aws s3api put-bucket-policy --bucket kt-sales-training-web-<suffix> \
  --policy '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":"*","Action":"s3:GetObject","Resource":"arn:aws:s3:::kt-sales-training-web-<suffix>/*"}]}'
```

`setup_apigw.py` 및 `setup_analyzer.py` 상단의 상수를 실제 값으로 수정합니다.

```python
REGION      = 'ap-northeast-2'
LAMBDA_NAME = 'kt-sales-training-chatbot'
WEB_BUCKET  = 'kt-sales-training-web-<suffix>'
API_NAME    = 'kt-sales-training-api'
```

### 3. API Gateway 생성

```bash
python setup_apigw.py
```

### 4. 데이터 버킷 생성 및 API Key 발급

```bash
python setup_analyzer.py
```

이 스크립트가 수행하는 작업:
- 프라이빗 데이터 버킷 생성 (`kt-sales-training-data-<account_id_suffix>`)
- Lambda 환경변수 설정 (`DATA_BUCKET`, `TRAINING_API_KEY`)
- 프론트엔드 HTML에 API URL·Key 주입 후 S3 업로드

출력 예시:
```
[OK]   Data bucket created: kt-sales-training-data-<suffix>
[OK]   Generated new TRAINING_API_KEY
[OK]   Lambda env updated: DATA_BUCKET=kt-sales-training-data-<suffix>
[INFO] API URL: https://xxxxxxxxxx.execute-api.ap-northeast-2.amazonaws.com/
[OK]   training.html uploaded to s3://kt-sales-training-web-<suffix>/
...
  API Key : <generated_key>
  Web URL : http://kt-sales-training-web-<suffix>.s3-website.ap-northeast-2.amazonaws.com
```

---

## 배포

### Lambda 코드 업데이트

```powershell
.\deploy_lambda.ps1
```

Linux 호환 패키지를 빌드(`manylinux2014_x86_64`)하여 Lambda에 업로드합니다.

### 프론트엔드 업데이트

```powershell
.\deploy_frontend.ps1
```

Lambda에서 API URL·API Key를 읽어 HTML에 자동 주입 후 S3에 업로드합니다.

---

## 스크립트 JSON 형식

분석기에서 저장하는 훈련 스크립트의 구조입니다.

```json
{
  "id": "fe0b5429",
  "script_name": "아이폰 판매 스크립트",
  "script_title": "아이폰 판매 스크립트",
  "created_at": "2026-05-17T00:21:11.801947Z",
  "stages": [
    {
      "id": 1,
      "name": "고객맞이 / 관계형성",
      "description": "고객을 맞이하고 신뢰 관계를 형성하는 단계",
      "key_actions": ["친근한 목소리로 고객 맞이", "..."],
      "success_criteria": "고객과 신뢰 관계 형성, 주요 불편점 1개 이상 파악"
    }
  ],
  "persona_prompt":       "고객 페르소나 시스템 프롬프트",
  "hint_prompt":          "힌트 에이전트 시스템 프롬프트",
  "evaluation_prompt":    "평가 에이전트 시스템 프롬프트",
  "recommendation_prompt":"추천 에이전트 시스템 프롬프트"
}
```

S3 저장 경로: `training-configs/year=YYYY/month=MM/YYYYMMDD_HHMMSS_<id>.json`  
훈련 기록 경로: `training-sessions/year=YYYY/month=MM/YYYYMMDD_HHMMSS_<id>.json`

---

## Lambda API 액션 목록

모든 요청은 `POST /`로 단일 엔드포인트에 전송하며, `action` 필드로 라우팅합니다.  
`X-Training-Key` 헤더 인증이 필요합니다 (`TRAINING_API_KEY` 환경변수).

| action | 설명 |
|---|---|
| `list_scripts` | 훈련 스크립트 목록 조회 |
| `load_script` | 스크립트 JSON 로드 |
| `chat` | 훈련 대화 (Master + Persona 병렬) |
| `hint` | 실시간 코칭 힌트 |
| `evaluate` | 훈련 세션 평가 |
| `recommend` | 맞춤 훈련 추천 |
| `save_session` | 훈련 기록 저장 |
| `list_sessions` | 훈련 기록 목록 조회 |
| `load_session` | 훈련 기록 상세 (대화 포함) |
| `analyze_stages` | 스크립트 → 단계 추출 |
| `analyze_prompts` | 스크립트 → 에이전트 프롬프트 생성 |
| `save` | 스크립트 JSON 저장 |

---

## 훈련 흐름

```
스크립트 선택
    ↓
대화 시작 (고객이 대리점 입장)
    ↓
[영업사원 발화] ──────────────────────────────────────┐
    ↓                                                │
Master Agent (Haiku) ──병렬──  Persona Agent (Haiku) │
Key Action 달성 여부 판정      고객 응답 생성 (1~2문장)  │
    ↓                                                │
단계 완료? ── Yes ──▶ 다음 단계                        │
    │                                                │
    No                                               │
    ↓                                                │
턴 제한 초과? (max_turns = Key Action 수 × 2, 최소 10)  │
    │── Yes ──▶ 실패 종료                              │
    └── No ──────────────────────────────────────────┘
        (💡 힌트 요청 가능, 대화 흐름과 별도)
    ↓
모든 단계 완료 → 성공 종료
    ↓
Evaluation Agent (Sonnet) → 단계별 O/X 체크 + 진행률
    ↓
[💾 저장] [나가기] [다시 도전] [맞춤 훈련 추천]
    ↓
Recommendation Agent (Haiku) → 페르소나 변형 / 반론 대응 / 퀴즈 추천
```

---

## 화면 구성

| 페이지 | URL | 설명 |
|---|---|---|
| 메인 | `index.html` | 메뉴 랜딩 |
| 스크립트 분석 | `script-analyzer.html` | 대본 업로드 → 분석 → 저장 |
| 훈련 Agent | `training.html` | 4-screen SPA (선택·대화·평가·추천) |
| 훈련 기록 | `training-results.html` | 이력 조회·대화 보기 모달 |

---

## 기술 스택

- **Backend**: Python 3.12 · AWS Lambda · API Gateway HTTP API (v2)
- **Frontend**: Vanilla HTML/CSS/JS · AWS S3 Static Website
- **AI**: Anthropic Claude Haiku 4.5 · Sonnet 4.6
- **Storage**: AWS S3 (Athena 호환 파티셔닝)
- **Infra**: AWS IAM · boto3

---

## 주의사항

- `setup_apigw.py`의 `account_id`는 실제 AWS 계정 ID로 교체해야 합니다.
- API Key(`TRAINING_API_KEY`)는 `setup_analyzer.py`가 자동 생성합니다. 재생성하려면 Lambda 환경변수에서 해당 키를 삭제 후 재실행하세요.
- API Gateway 통합 제한 시간은 **29초**입니다. 분석 작업은 `analyze_stages`와 `analyze_prompts`로 분리 병렬 호출하여 이 제한을 우회합니다.
