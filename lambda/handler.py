import json
import os
import datetime
import uuid
import anthropic
import boto3
from concurrent.futures import ThreadPoolExecutor

client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
s3 = boto3.client("s3")

MAX_CONTEXT_MESSAGES = 30
API_KEY    = os.environ.get("TRAINING_API_KEY", "")
DATA_BUCKET = os.environ.get("DATA_BUCKET", "")


def calc_max_turns(stages):
    total_actions = sum(len(s.get("key_actions", [])) for s in stages)
    return max(10, total_actions * 2)


def build_cors_headers():
    return {
        "Access-Control-Allow-Origin":  "*",
        "Access-Control-Allow-Headers": "Content-Type,X-Training-Key",
        "Access-Control-Allow-Methods": "POST, OPTIONS",
        "Content-Type": "application/json",
    }


def ok(body, headers):
    return {"statusCode": 200, "headers": headers,
            "body": json.dumps(body, ensure_ascii=False)}


def err(code, msg, headers):
    return {"statusCode": code, "headers": headers,
            "body": json.dumps({"error": msg}, ensure_ascii=False)}


def _parse_json(text):
    text = text.strip()
    start = text.find('{')
    if start > 0:
        text = text[start:]
    end = text.rfind('}')
    if end >= 0:
        text = text[:end + 1]
    return json.loads(text)


def check_auth(event, headers):
    if not API_KEY:
        return None
    req_headers = event.get("headers", {}) or {}
    provided = req_headers.get("x-training-key", "") or req_headers.get("X-Training-Key", "")
    if provided != API_KEY:
        return err(401, "인증이 필요합니다.", headers)
    return None


# ── list_scripts ──────────────────────────────────────────────────────────────
def handle_list_scripts(body, headers):
    if not DATA_BUCKET:
        return err(500, "DATA_BUCKET 환경변수가 설정되지 않았습니다.", headers)
    scripts = []
    paginator = s3.get_paginator('list_objects_v2')
    for page in paginator.paginate(Bucket=DATA_BUCKET, Prefix='training-configs/'):
        for obj in page.get('Contents', []):
            key = obj['Key']
            if not key.endswith('.json'):
                continue
            try:
                resp = s3.get_object(Bucket=DATA_BUCKET, Key=key)
                data = json.loads(resp['Body'].read())
                scripts.append({
                    'id':           data.get('id', ''),
                    'key':          key,
                    'script_name':  data.get('script_name', '미분류'),
                    'script_title': data.get('script_title', ''),
                    'stage_count':  len(data.get('stages', [])),
                    'created_at':   data.get('created_at', ''),
                })
            except Exception:
                pass
    scripts.sort(key=lambda x: x.get('created_at', ''), reverse=True)
    return ok({'scripts': scripts}, headers)


# ── load_script ───────────────────────────────────────────────────────────────
def handle_load_script(body, headers):
    if not DATA_BUCKET:
        return err(500, "DATA_BUCKET 환경변수가 설정되지 않았습니다.", headers)
    key = body.get('key', '')
    if not key or not key.startswith('training-configs/'):
        return err(400, '잘못된 키입니다.', headers)
    resp = s3.get_object(Bucket=DATA_BUCKET, Key=key)
    data = json.loads(resp['Body'].read())
    return ok(data, headers)


# ── chat (Master Agent + Customer Persona Agent, parallel) ────────────────────
MASTER_SYSTEM = """당신은 영업 훈련 세션의 단계 관리자입니다.

현재 단계: [{stage_id}/{total_stages}] {stage_name}
목표: {description}
핵심 행동 체크리스트:
{key_actions_str}
성공 기준: {success_criteria}

위 대화에서 영업사원이 현재 단계의 핵심 행동들을 충분히 달성했는지 판단하세요.
모든 항목이 완벽하지 않아도 핵심 내용이 달성되고 성공 기준에 부합하면 complete로 판단하세요.

순수 JSON으로만 응답하세요:
{{"stage_complete": true 또는 false, "reason": "판단 근거 한 문장"}}"""


def handle_chat(body, headers):
    messages          = body.get("messages", [])
    turn_count        = body.get("turn_count", 0)
    current_stage_idx = body.get("current_stage_idx", 0)
    stages            = body.get("stages", [])
    persona_prompt    = body.get("persona_prompt", "")

    if not stages or not persona_prompt:
        return err(400, "스크립트 데이터가 없습니다.", headers)

    max_turns    = calc_max_turns(stages)
    warn_turn    = max(1, max_turns - 3)
    total_stages = len(stages)
    current_stage = stages[current_stage_idx] if current_stage_idx < total_stages else None

    if turn_count >= max_turns:
        return ok({
            "response": (
                "저는... 오늘은 좀 더 생각해봐야 할 것 같아요. "
                "시간이 너무 오래 걸리네요. 다음에 다시 올게요."
                "\n\n(고객이 자리에서 일어나 대리점을 나갑니다.)"
            ),
            "is_failed": True, "is_success": False,
            "turn_count": turn_count, "max_turns": max_turns,
            "current_stage_idx": current_stage_idx,
            "stage_changed": False,
        }, headers)

    def call_master():
        if not current_stage:
            return {"stage_complete": True, "reason": "마지막 단계 완료"}
        key_actions_str = "\n".join(f"- {a}" for a in current_stage.get("key_actions", []))
        system = MASTER_SYSTEM.format(
            stage_id=current_stage.get("id", current_stage_idx + 1),
            total_stages=total_stages,
            stage_name=current_stage.get("name", ""),
            description=current_stage.get("description", ""),
            key_actions_str=key_actions_str,
            success_criteria=current_stage.get("success_criteria", ""),
        )
        for attempt in range(2):
            try:
                resp = client.messages.create(
                    model="claude-haiku-4-5-20251001",
                    max_tokens=200,
                    system=system,
                    messages=(messages[-10:] if len(messages) > 10 else messages),
                )
                if resp.content:
                    return _parse_json(resp.content[0].text)
            except Exception:
                pass
        return {"stage_complete": False, "reason": "판단 오류"}

    def call_persona():
        trimmed = messages[-MAX_CONTEXT_MESSAGES:] if len(messages) > MAX_CONTEXT_MESSAGES else messages
        system = (
            persona_prompt +
            "\n\n[응답 규칙] 실제 고객처럼 짧고 자연스럽게 대화하세요. "
            "한 번에 1~2문장으로만 답하고, 설명이나 나열은 하지 마세요."
        )
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            system=system,
            messages=trimmed,
        )
        return resp.content[0].text if resp.content else "..."

    with ThreadPoolExecutor(max_workers=2) as executor:
        mf = executor.submit(call_master)
        pf = executor.submit(call_persona)
        master_result    = mf.result()
        persona_response = pf.result()

    new_turn_count = turn_count + 1
    stage_complete = master_result.get("stage_complete", False)
    new_stage_idx  = (current_stage_idx + 1) if stage_complete else current_stage_idx
    all_complete   = new_stage_idx >= total_stages
    is_failed      = (new_turn_count >= max_turns) and not all_complete
    is_warning     = (new_turn_count >= warn_turn) and not all_complete and not is_failed

    if is_failed:
        persona_response += (
            "\n\n(고객이 자리에서 일어납니다.) "
            "죄송한데요, 오늘은 시간이 좀 걸리네요. 나중에 다시 올게요."
        )

    return ok({
        "response":          persona_response,
        "turn_count":        new_turn_count,
        "max_turns":         max_turns,
        "current_stage_idx": new_stage_idx,
        "stage_changed":     stage_complete,
        "stage_name":        stages[new_stage_idx].get("name") if new_stage_idx < total_stages else None,
        "master_reason":     master_result.get("reason", ""),
        "is_success":        all_complete,
        "is_failed":         is_failed,
        "is_warning":        is_warning,
    }, headers)


# ── hint (Real-time Coach Agent) ──────────────────────────────────────────────
def handle_hint(body, headers):
    messages           = body.get("messages", [])
    current_stage_idx  = body.get("current_stage_idx", 0)
    stages             = body.get("stages", [])
    hint_prompt        = body.get("hint_prompt", "")
    last_master_reason = body.get("last_master_reason", "")

    if not hint_prompt:
        return err(400, "hint_prompt가 없습니다.", headers)

    current_stage = stages[current_stage_idx] if current_stage_idx < len(stages) else {}
    system = (
        hint_prompt +
        f"\n\n현재 단계: [{current_stage_idx + 1}/{len(stages)}] {current_stage.get('name', '')}\n"
        f"성공 기준: {current_stage.get('success_criteria', '')}\n"
        f"핵심 행동: {', '.join(current_stage.get('key_actions', []))}"
    )
    if last_master_reason:
        system += f"\n\n마스터 평가: {last_master_reason}"

    hint_messages = messages[-10:] if len(messages) > 10 else messages
    if not hint_messages:
        hint_messages = [{"role": "user", "content": "훈련을 막 시작했습니다. 첫 번째 단계를 어떻게 시작해야 할까요?"}]

    resp = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=400,
        system=system,
        messages=hint_messages,
    )
    if not resp.content:
        return err(500, "힌트 생성에 실패했습니다.", headers)
    return ok({"hint": resp.content[0].text}, headers)


# ── evaluate (Evaluation Agent) ───────────────────────────────────────────────
def handle_evaluate(body, headers):
    messages          = body.get("messages", [])
    stages            = body.get("stages", [])
    evaluation_prompt = body.get("evaluation_prompt", "")
    final_stage_idx   = body.get("final_stage_idx", 0)
    is_success        = body.get("is_success", False)

    if not evaluation_prompt:
        return err(400, "evaluation_prompt가 없습니다.", headers)

    conv_text = "\n".join(
        f"{'영업사원' if m['role'] == 'user' else '고객'}: {m['content']}"
        for m in messages
    )
    stages_text = "\n".join(
        f"[단계 {s.get('id', i+1)}] {s.get('name', '')}: {s.get('success_criteria', '')}"
        for i, s in enumerate(stages)
    )
    result_text = "성공 (모든 단계 완료)" if is_success else f"미완료 ({final_stage_idx + 1}/{len(stages)} 단계에서 종료)"
    user_msg = (
        f"## 훈련 결과: {result_text}\n\n"
        f"## 진행 단계 기준\n{stages_text}\n\n"
        f"## 전체 대화 내용\n{conv_text}"
    )
    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1500,
        system=evaluation_prompt,
        messages=[{"role": "user", "content": user_msg}],
    )
    if not resp.content:
        return err(500, "평가 생성에 실패했습니다.", headers)
    return ok({"evaluation": resp.content[0].text}, headers)


# ── recommend (Recommendation Agent) ─────────────────────────────────────────
def handle_recommend(body, headers):
    evaluation_result     = body.get("evaluation_result", "")
    recommendation_prompt = body.get("recommendation_prompt", "")

    if not recommendation_prompt:
        return err(400, "recommendation_prompt가 없습니다.", headers)

    resp = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=800,
        system=recommendation_prompt,
        messages=[{"role": "user", "content": f"다음 평가 결과를 기반으로 맞춤 훈련을 추천해주세요:\n\n{evaluation_result}"}],
    )
    if not resp.content:
        return err(500, "추천 생성에 실패했습니다.", headers)
    return ok({"recommendation": resp.content[0].text}, headers)


# ── list_sessions (훈련 기록 목록) ───────────────────────────────────────────
def handle_list_sessions(body, headers):
    if not DATA_BUCKET:
        return err(500, "DATA_BUCKET 환경변수가 설정되지 않았습니다.", headers)
    sessions = []
    paginator = s3.get_paginator('list_objects_v2')
    for page in paginator.paginate(Bucket=DATA_BUCKET, Prefix='training-sessions/'):
        for obj in page.get('Contents', []):
            key = obj['Key']
            if not key.endswith('.json'):
                continue
            try:
                resp = s3.get_object(Bucket=DATA_BUCKET, Key=key)
                data = json.loads(resp['Body'].read())
                sessions.append({
                    'id':              data.get('id', ''),
                    'key':             key,
                    'script_name':     data.get('script_name', ''),
                    'created_at':      data.get('created_at', ''),
                    'is_success':      data.get('is_success', False),
                    'turn_count':      data.get('turn_count', 0),
                    'max_turns':       data.get('max_turns', 0),
                    'final_stage_idx': data.get('final_stage_idx', 0),
                    'total_stages':    data.get('total_stages', 0),
                    'evaluation':      data.get('evaluation', ''),
                })
            except Exception:
                pass
    sessions.sort(key=lambda x: x.get('created_at', ''), reverse=True)
    return ok({'sessions': sessions}, headers)


# ── load_session (훈련 기록 상세 — 대화 포함) ─────────────────────────────────
def handle_load_session(body, headers):
    if not DATA_BUCKET:
        return err(500, "DATA_BUCKET 환경변수가 설정되지 않았습니다.", headers)
    key = body.get('key', '')
    if not key or not key.startswith('training-sessions/'):
        return err(400, '잘못된 키입니다.', headers)
    resp = s3.get_object(Bucket=DATA_BUCKET, Key=key)
    data = json.loads(resp['Body'].read())
    return ok(data, headers)


# ── save_session (훈련 세션 기록 저장) ────────────────────────────────────────
def handle_save_session(body, headers):
    if not DATA_BUCKET:
        return err(500, "DATA_BUCKET 환경변수가 설정되지 않았습니다.", headers)
    now       = datetime.datetime.utcnow()
    record_id = str(uuid.uuid4())[:8]
    key = (
        f"training-sessions/"
        f"year={now.year}/month={now.month:02d}/"
        f"{now.strftime('%Y%m%d_%H%M%S')}_{record_id}.json"
    )
    record = {
        "id":              record_id,
        "script_id":       body.get("script_id", ""),
        "script_name":     body.get("script_name", ""),
        "created_at":      now.isoformat() + "Z",
        "is_success":      body.get("is_success", False),
        "turn_count":      body.get("turn_count", 0),
        "max_turns":       body.get("max_turns", 0),
        "final_stage_idx": body.get("final_stage_idx", 0),
        "total_stages":    len(body.get("stages", [])),
        "evaluation":      body.get("evaluation", ""),
        "messages":        body.get("messages", []),
    }
    s3.put_object(
        Bucket=DATA_BUCKET, Key=key,
        Body=json.dumps(record, ensure_ascii=False, indent=2),
        ContentType="application/json",
    )
    return ok({"success": True, "id": record_id, "key": key}, headers)


# ── analyze (script-analyzer.html용) ─────────────────────────────────────────
STAGES_PROMPT = """다음 영업 스크립트에서 제목과 진행 단계만 추출해 JSON으로만 응답하세요 (코드 블록 없이):

## 영업 스크립트
---
{script}
---

{{
  "script_title": "스크립트 제목",
  "stages": [
    {{
      "id": 1,
      "name": "단계명",
      "description": "이 단계에서 달성해야 할 목표",
      "key_actions": ["핵심 행동 1", "핵심 행동 2"],
      "success_criteria": "이 단계의 성공 기준"
    }}
  ]
}}"""

PROMPTS_PROMPT = """다음 영업 스크립트를 기반으로 AI 훈련 에이전트 4종의 시스템 프롬프트를 작성하세요.
각 프롬프트는 핵심만 담아 간결하게 작성하고, 순수 JSON만 반환하세요 (코드 블록 없이).

## 영업 스크립트
---
{script}
---

{{
  "persona_prompt": "고객 페르소나 프롬프트 (고객 상황·성격·반응 패턴, 단계별 예상 반응 포함. 500자 이내)",
  "hint_prompt": "힌트 에이전트 프롬프트 (현재 단계 파악 후 다음 행동을 구체적으로 안내. 300자 이내)",
  "evaluation_prompt": "평가 에이전트 프롬프트 (단계 완료 여부·핵심 멘트 체크리스트로 점수·피드백 제공. 300자 이내)",
  "recommendation_prompt": "추천 에이전트 프롬프트 (약점 분석 후 페르소나 변형·반론 대응·퀴즈 등 맞춤 훈련 추천. 300자 이내)"
}}"""


def handle_analyze_stages(body, headers):
    script_content = body.get("script_content", "").strip()
    if not script_content:
        return err(400, "스크립트 파일이 없습니다.", headers)
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=2000,
        messages=[{"role": "user", "content": STAGES_PROMPT.format(script=script_content)}],
    )
    if not response.content:
        return err(500, "분석 실패", headers)
    return ok(_parse_json(response.content[0].text), headers)


def handle_analyze_prompts(body, headers):
    script_content = body.get("script_content", "").strip()
    if not script_content:
        return err(400, "스크립트 파일이 없습니다.", headers)
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=4096,
        messages=[{"role": "user", "content": PROMPTS_PROMPT.format(script=script_content)}],
    )
    if not response.content:
        return err(500, "분석 실패", headers)
    return ok(_parse_json(response.content[0].text), headers)


# ── save (스크립트 저장) ───────────────────────────────────────────────────────
def handle_save(body, headers):
    if not DATA_BUCKET:
        return err(500, "DATA_BUCKET 환경변수가 설정되지 않았습니다.", headers)
    data        = body.get("data", {})
    script_name = body.get("script_name", "unknown")
    now         = datetime.datetime.utcnow()
    record_id   = str(uuid.uuid4())[:8]
    key = (
        f"training-configs/"
        f"year={now.year}/month={now.month:02d}/"
        f"{now.strftime('%Y%m%d_%H%M%S')}_{record_id}.json"
    )
    record = {"id": record_id, "script_name": script_name, "created_at": now.isoformat() + "Z", **data}
    s3.put_object(
        Bucket=DATA_BUCKET, Key=key,
        Body=json.dumps(record, ensure_ascii=False, indent=2),
        ContentType="application/json",
    )
    return ok({"success": True, "id": record_id, "key": key, "bucket": DATA_BUCKET}, headers)


# ── Entry point ───────────────────────────────────────────────────────────────
def lambda_handler(event, context):
    headers = build_cors_headers()
    http_method = (
        event.get("requestContext", {}).get("http", {}).get("method")
        or event.get("httpMethod", "")
    )
    if http_method == "OPTIONS":
        return {"statusCode": 200, "headers": headers, "body": ""}

    auth_err = check_auth(event, headers)
    if auth_err:
        return auth_err

    try:
        body   = json.loads(event.get("body") or "{}")
        action = body.get("action", "chat")
        if   action == "list_scripts":    return handle_list_scripts(body, headers)
        elif action == "load_script":     return handle_load_script(body, headers)
        elif action == "chat":            return handle_chat(body, headers)
        elif action == "hint":            return handle_hint(body, headers)
        elif action == "evaluate":        return handle_evaluate(body, headers)
        elif action == "recommend":       return handle_recommend(body, headers)
        elif action == "analyze_stages":  return handle_analyze_stages(body, headers)
        elif action == "analyze_prompts": return handle_analyze_prompts(body, headers)
        elif action == "save":            return handle_save(body, headers)
        elif action == "save_session":    return handle_save_session(body, headers)
        elif action == "list_sessions":   return handle_list_sessions(body, headers)
        elif action == "load_session":    return handle_load_session(body, headers)
        else:                             return handle_chat(body, headers)
    except json.JSONDecodeError as exc:
        return err(500, f"JSON 파싱 오류: {exc}", headers)
    except Exception as exc:
        return err(500, str(exc), headers)
