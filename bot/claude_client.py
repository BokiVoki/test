import json
import os
from typing import Optional

import anthropic

from .models import ContentEntry, ParsedIntent, CONTENT_TYPES, STATUS_VALUES
from .mode_prompts import PROMPTS

_client: Optional[anthropic.Anthropic] = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    return _client


def parse_archive_message(text: str, known_titles: list[str]) -> ParsedIntent:
    """
    자연어 메시지를 파싱하여 아카이브 관련 의도를 추출합니다.
    빠른 응답을 위해 Haiku 모델을 사용합니다.
    """
    titles_str = json.dumps(known_titles, ensure_ascii=False)
    system = f"""당신은 콘텐츠 아카이브 비서입니다. 사용자 메시지를 파싱하여 JSON만 반환하세요.

아카이브에 등록된 제목 목록:
{titles_str}

반환할 JSON 스키마:
{{
  "action": "string",        // record_progress | add_new | mark_status | rate | query | recommend | note | unknown
  "title": "string|null",    // 제목 (위 목록에서 유사한 것 매칭)
  "content_type": "string|null", // {CONTENT_TYPES}
  "progress": "string|null", // "87화", "5화", "3장", "Ep.12", "Page 203" 형식
  "status": "string|null",   // {STATUS_VALUES}
  "rating": "number|null",   // 1-10
  "note": "string|null",
  "author": "string|null",   // 작가/감독/제작자
  "year_watched": "string|null", // 감상한 연도 (예: "2026")
  "publisher": "string|null", // 출판사/제작사/방송사
  "query_text": "string|null",
  "recommend_context": "string|null",
  "confidence": "number"     // 0.0-1.0
}}

규칙:
- "봤어", "읽었어", "들었어" 등 → record_progress (status는 in_progress 유지)
- "다 봤어", "완료", "끝냈어", "다 읽었어" → mark_status (status: completed)
- "추가해줘", "등록해줘", "시작할 거야" → add_new
- "중단", "그만 봤어", "드랍" → mark_status (status: dropped)
- "점 줘", "평점", "점수" → rate
- "추천해줘", "뭐 볼까" → recommend
- "보여줘", "목록", "뭐 봤더라" → query
- 제목은 목록과 유사한 것이 있으면 반드시 매칭
- "다 봤어" + 평점이 함께 오면 action은 mark_status, rating에 값 포함
- "작가", "감독" 언급되면 author 필드에 저장
- "년에 봤어", "년도에" 언급되면 year_watched에 연도만 저장

JSON만 반환하고 다른 텍스트는 절대 포함하지 마세요."""

    client = _get_client()
    try:
        resp = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=512,
            system=system,
            messages=[{"role": "user", "content": text}],
        )
        raw = resp.content[0].text.strip()
        # JSON 블록 추출 (마크다운 코드블록 처리)
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        data = json.loads(raw)
        return ParsedIntent(
            action=data.get("action", "unknown"),
            title=data.get("title"),
            content_type=data.get("content_type"),
            progress=data.get("progress"),
            status=data.get("status"),
            rating=data.get("rating"),
            note=data.get("note"),
            author=data.get("author"),
            year_watched=data.get("year_watched"),
            publisher=data.get("publisher"),
            query_text=data.get("query_text"),
            recommend_context=data.get("recommend_context"),
            confidence=float(data.get("confidence", 1.0)),
            raw_message=text,
        )
    except Exception:
        return ParsedIntent(action="unknown", raw_message=text, confidence=0.0)


def chat(mode: str, user_message: str, context: str = "", history: list[dict] | None = None, memo_context: str = "") -> str:
    """
    모드별 시스템 프롬프트로 Claude와 대화합니다.
    memo_context: Memos 시트에서 불러온 저장 메모
    context: 아카이브 데이터 등 기타 컨텍스트
    """
    system = PROMPTS.get(mode, PROMPTS["secretary"])
    if memo_context:
        system = f"{system}\n\n---\n📌 저장된 메모 (과거 대화/결정 요약, 항상 참고):\n{memo_context}"
    if context:
        system = f"{system}\n\n---\n현재 사용자 아카이브 데이터:\n{context}"

    messages = history.copy() if history else []
    messages.append({"role": "user", "content": user_message})

    client = _get_client()
    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        system=system,
        messages=messages,
    )
    return resp.content[0].text.strip()


def get_recommendations(query: str, entries: list[ContentEntry]) -> str:
    """
    아카이브 전체를 컨텍스트로 넘겨 추천을 받습니다.
    """
    if not entries:
        archive_text = "아직 아카이브가 비어있어요."
    else:
        lines = []
        for e in entries:
            parts = [f"- {e.title} ({e.type})"]
            if e.rating:
                parts.append(f"평점 {e.rating}")
            if e.status:
                parts.append(e.status)
            if e.tags:
                parts.append(f"태그: {e.tags}")
            if e.notes:
                parts.append(f"메모: {e.notes[:50]}")
            lines.append(" | ".join(parts))
        archive_text = "\n".join(lines)

    prompt = f"""사용자의 콘텐츠 아카이브:
{archive_text}

사용자 요청: {query}

아카이브를 참고해서:
1. 아카이브 내에서 아직 완료 안 한 것 중 관련 있는 것 추천 (있다면)
2. 아카이브 취향 분석 기반 새로운 추천 2-3개

간결하게 한국어로 답변해주세요."""

    return chat("secretary", prompt)


def parse_reminder_times(user_input: str, now_kst_str: str) -> list[dict]:
    """
    자연어 리마인더 입력을 파싱. 여러 날짜도 처리.
    "내일, 모레, 글피 오전 9시 / 약 먹기" → [{trigger_at, repeat, reminder_text}, ...]
    """
    client = _get_client()
    resp = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=400,
        messages=[{
            "role": "user",
            "content": (
                f"현재 한국 시간: {now_kst_str}\n"
                f"리마인더 요청: \"{user_input}\"\n\n"
                "다음 JSON 배열만 반환하세요 (다른 텍스트 없이):\n"
                "[\n"
                "  {\"trigger_at\": \"YYYY-MM-DDTHH:MM:SS\", \"repeat\": \"none\", \"reminder_text\": \"알림 내용\"},\n"
                "  ...\n"
                "]\n\n"
                "규칙:\n"
                "- repeat 값: none | daily | weekly | monthly\n"
                "- 시간 없으면 09:00:00 기본값\n"
                "- '내일, 모레, 글피' 처럼 여러 날짜면 각각 별도 항목으로\n"
                "- '매일', '매주', '매달' → repeat 설정\n"
                "- '/' 앞은 시간/날짜, '/' 뒤는 reminder_text\n"
                "- reminder_text는 간결하게"
            ),
        }],
    )
    import re
    raw = resp.content[0].text.strip()
    m = re.search(r'\[.*\]', raw, re.DOTALL)
    if m:
        return json.loads(m.group())
    # 단일 객체 응답 fallback
    m2 = re.search(r'\{.*?\}', raw, re.DOTALL)
    if m2:
        return [json.loads(m2.group())]
    raise ValueError(f"파싱 실패: {raw}")


def parse_reminder_time(user_input: str, now_kst_str: str) -> dict:
    """
    자연어 리마인더 입력을 파싱합니다.
    예: "내일 오전 9시 / 약 먹기" → {"trigger_at": "...", "repeat": "none", "reminder_text": "약 먹기"}
    """
    client = _get_client()
    resp = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=200,
        messages=[{
            "role": "user",
            "content": (
                f"현재 한국 시간: {now_kst_str}\n"
                f"리마인더 요청: \"{user_input}\"\n\n"
                "다음 JSON만 반환하세요 (다른 텍스트 없이):\n"
                "{\n"
                "  \"trigger_at\": \"YYYY-MM-DDTHH:MM:SS\",\n"
                "  \"repeat\": \"none\",\n"
                "  \"reminder_text\": \"알림 내용\"\n"
                "}\n\n"
                "규칙:\n"
                "- repeat 값: none | daily | weekly | monthly\n"
                "- 시간 없으면 09:00:00 기본값\n"
                "- '매일', '매주', '매달' 키워드로 repeat 결정\n"
                "- reminder_text는 간결하게 (예: '약 먹기', '주간 계획 세우기')\n"
                "- '/' 앞은 시간, '/' 뒤는 reminder_text로 사용"
            ),
        }],
    )
    import re
    raw = resp.content[0].text.strip()
    m = re.search(r'\{.*?\}', raw, re.DOTALL)
    if m:
        return json.loads(m.group())
    raise ValueError(f"시간 파싱 실패: {raw}")


def parse_todo(user_input: str, now_kst_str: str) -> dict:
    """
    자연어 투두+알람 파싱. 여러 개 동시 등록 지원.
    반환: {
      "action": "add|complete|delete|list|cancel_alarms",
      "items": [
        {"text": "...", "due_date": "YYYY-MM-DD or null",
         "trigger_at": "YYYY-MM-DDTHH:MM:SS or null", "repeat": "none"}
      ]
    }
    단일 투두도 items 배열로 반환.
    """
    client = _get_client()
    resp = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=600,
        messages=[{
            "role": "user",
            "content": (
                f"현재 한국 시간: {now_kst_str}\n"
                f"투두/알람 요청: \"{user_input}\"\n\n"
                "다음 JSON만 반환하세요:\n"
                "{\n"
                "  \"action\": \"add\",\n"
                "  \"items\": [\n"
                "    {\"text\": \"할 일 내용\", \"due_date\": null, \"trigger_at\": null, \"repeat\": \"none\"}\n"
                "  ]\n"
                "}\n\n"
                "action 규칙:\n"
                "- add: 추가/등록/할 일 생성/리마인더 설정\n"
                "- complete: 완료/했어/끝냈어/체크 (items에 완료할 항목)\n"
                "- delete: 삭제/지워/제거 (items에 삭제할 항목)\n"
                "- list: 목록/보여/리스트/미완료/남은/못 한/안 한/뭐 해야/확인/조회\n"
                "- cancel_alarms: 알람 전부 취소/알람 다 꺼\n"
                "여러 개 등록: '투두 A, B, C' 또는 줄바꿈 → items 배열에 각각 추가\n"
                "공통 시간/반복이 있으면 각 item에 동일하게 적용\n"
                "trigger_at 규칙:\n"
                "- 구체적인 시각 언급 시 → 해당 시각 (예: '내일 오전 9시' → 내일T09:00:00)\n"
                "- 날짜만 언급 시 → 해당 날 09:00:00\n"
                "- 시간/날짜 없으면 → null\n"
                "due_date: '~까지' 마감일. trigger_at 있으면 null\n"
                "repeat: 매일→daily, 매주→weekly, 매달→monthly\n"
                "  N분마다/N시간마다/N일마다 → after:분수\n"
                "  예: 2시간마다→after:120, 30분마다→after:30\n"
                "  없으면→none\n"
                "text: 핵심 할 일만 (투두/시간/날짜/마다 키워드 제외)"
            ),
        }],
    )
    import re
    raw = resp.content[0].text.strip()
    m = re.search(r'\{.*\}', raw, re.DOTALL)
    if m:
        result = json.loads(m.group())
        # 하위 호환: 구버전 단일 text 필드 → items 변환
        if "text" in result and "items" not in result:
            result["items"] = [{"text": result.pop("text"),
                                 "due_date": result.pop("due_date", None),
                                 "trigger_at": result.pop("trigger_at", None),
                                 "repeat": result.pop("repeat", "none")}]
        return result
    raise ValueError(f"파싱 실패: {raw}")


def parse_intake_message(text: str, item_names: list[str]) -> dict:
    """
    영양제/약 복용 관련 자연어 파싱.
    반환: {
      "action": "log|query_stock|query_today|add_item|restock",
      "items": [{"name": "비타민C", "qty": 1, "note": ""}],
      "target_name": null,
      "new_item": {"name": "...", "qty": 30, "category": "daily|situational|prescription|pms", "daily": false, "note": ""}
    }
    """
    import re
    client = _get_client()
    names_str = ", ".join(item_names) if item_names else "없음"
    resp = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=500,
        messages=[{
            "role": "user",
            "content": (
                f"등록된 영양제/약 목록: {names_str}\n"
                f"사용자 입력: \"{text}\"\n\n"
                "다음 JSON만 반환하세요:\n"
                "{\n"
                "  \"action\": \"log\",\n"
                "  \"items\": [{\"name\": \"비타민C\", \"qty\": 1, \"note\": \"\"}],\n"
                "  \"target_name\": null,\n"
                "  \"new_item\": null\n"
                "}\n\n"
                "action 규칙:\n"
                "- log: 먹었어/복용/챙겼어/먹음/마셨어\n"
                "- query_stock: 남은/재고/얼마나/몇 정/현황\n"
                "- query_today: 오늘 뭐 먹었어/오늘 복용 내역/오늘 뭐 챙겼어\n"
                "- add_item: 영양제 추가/등록/새로 생겼어/새로 샀어 + 목록에 없는 이름\n"
                "- restock: 보충/재입고/다시 샀어/구매했어 + 목록에 이미 있는 이름\n"
                "items 규칙:\n"
                "- name은 목록에서 가장 유사한 이름으로 매칭\n"
                "- 여러 항목 동시 처리 가능\n"
                "- qty 기본값: 1 (restock은 실제 수량)\n"
                "- query_stock/query_today: items는 빈 배열, target_name에 대상 영양제명 (전체면 null)\n"
                "new_item 규칙 (action=add_item일 때):\n"
                "- name: 정확한 영양제/약 이름\n"
                "- qty: 개수 (언급 없으면 30)\n"
                "- category: daily(매일복용)/situational(상황별)/prescription(처방약)/pms\n"
                "- daily: 매일 먹는다면 true\n"
                "- note: 용량, 복용법 등 메모\n"
                "- phases: 해당 영양제가 권장되는 생리주기 단계. 쉼표구분 '생리기,황체기' 형식\n"
                "  생리기(day1-5), 여포기(day6-13), 배란기(day14-16), 황체기(day17-30), all(항상), 모르면 ''\n"
                "  힌트: 마그네슘/칼슘→생리기,황체기 / 철분→생리기 / 프로바이오틱스→여포기 / 항산화→배란기 / PMS약→황체기\n"
                "action=restock일 때: items에 기존 이름 + qty에 추가 수량"
            ),
        }],
    )
    raw = resp.content[0].text.strip()
    m = re.search(r'\{.*\}', raw, re.DOTALL)
    if m:
        return json.loads(m.group())
    return {"action": "query_stock", "items": [], "target_name": None, "new_item": None}


def parse_cycle_message(text: str, now_str: str) -> dict:
    """
    생리주기 관련 자연어 파싱.
    반환: {
      "action": "start_period|end_period|query_status|query_next",
      "date": "2026-04-06",
      "note": ""
    }
    """
    import re
    client = _get_client()
    resp = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=200,
        messages=[{
            "role": "user",
            "content": (
                f"현재 한국 시간: {now_str}\n"
                f"사용자 입력: \"{text}\"\n\n"
                "다음 JSON만 반환하세요:\n"
                "{\n"
                "  \"action\": \"query_status\",\n"
                "  \"date\": \"2026-04-06\",\n"
                "  \"note\": \"\"\n"
                "}\n\n"
                "action 규칙:\n"
                "- start_period: 생리 시작/시작했어/생리했어/생리 왔어\n"
                "- end_period: 생리 끝/끝났어\n"
                "- query_status: 지금 몇 기야/어느 단계/현재 주기/지금 어때\n"
                "- query_next: 생리 언제야/다음 생리/예정일\n"
                "date 규칙: 명시적 날짜 있으면 변환, 없으면 오늘 날짜\n"
                "note: 증상이나 메모 있으면 포함"
            ),
        }],
    )
    raw = resp.content[0].text.strip()
    m = re.search(r'\{.*\}', raw, re.DOTALL)
    if m:
        return json.loads(m.group())
    return {"action": "query_status", "date": now_str[:10], "note": ""}


def parse_checkin_response(text: str) -> dict:
    """
    체크인 응답 파싱 (수면시간, 음수량, 집중도).
    반환: {"sleep_hours": float|null, "water_glasses": int|null, "focus": "good|okay|bad", "note": str}
    """
    import re
    client = _get_client()
    resp = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=200,
        messages=[{
            "role": "user",
            "content": (
                f"체크인 응답: \"{text}\"\n\n"
                "다음 JSON만 반환하세요:\n"
                "{\n"
                "  \"sleep_hours\": 7.5,\n"
                "  \"water_glasses\": 3,\n"
                "  \"focus\": \"good\",\n"
                "  \"note\": \"\"\n"
                "}\n\n"
                "규칙:\n"
                "- sleep_hours: 수면 시간 언급 없으면 null\n"
                "- water_glasses: 음수량 언급 없으면 null\n"
                "- focus: 좋음/잘됨/활발 → good, 보통/그냥 → okay, 산만/힘듦/못됨/없음 → bad\n"
                "  언급 없으면 okay\n"
                "- note: 원문 또는 추가 내용"
            ),
        }],
    )
    raw = resp.content[0].text.strip()
    m = re.search(r'\{.*\}', raw, re.DOTALL)
    if m:
        return json.loads(m.group())
    return {"sleep_hours": None, "water_glasses": None, "focus": "okay", "note": text}


def summarize_conversation(history: list[dict], mode: str) -> str:
    """대화 히스토리를 핵심 내용으로 요약"""
    if not history:
        return ""
    conv = "\n".join(
        f"{'사용자' if m['role'] == 'user' else '봇'}: {m['content']}"
        for m in history
    )
    mode_kr = {"finance": "금융", "consultant": "컨설턴트", "secretary": "비서"}.get(mode, mode)
    client = _get_client()
    resp = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=600,
        messages=[{
            "role": "user",
            "content": (
                f"다음은 {mode_kr} 관련 대화예요. 핵심 내용을 구조화해서 메모 형식으로 요약해주세요.\n"
                "날짜/수치/결론 위주로, 나중에 다시 봤을 때 바로 이해할 수 있게.\n\n"
                f"{conv}"
            ),
        }],
    )
    return resp.content[0].text.strip()


def detect_important_decision(assistant_reply: str) -> bool:
    """에이전트 답변에 중요 결정/피드백이 포함됐는지 빠르게 판단 (Haiku)"""
    client = _get_client()
    resp = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=10,
        messages=[{
            "role": "user",
            "content": (
                "다음 인스타그램 팀 에이전트 답변에 나중에 기억해야 할 중요한 결정, 확정된 방향, "
                "구체적 수치/스펙, 피드백이 포함됐나요? yes 또는 no로만 답하세요.\n\n"
                f"{assistant_reply[:800]}"
            ),
        }],
    )
    return resp.content[0].text.strip().lower().startswith("yes")


def summarize_instagram_decision(reply: str, agent: str) -> str:
    """인스타그램 에이전트 답변에서 핵심 결정/피드백만 추출"""
    agent_kr = {"designer": "디자이너", "writer": "작가", "manager": "매니저"}.get(agent, agent)
    client = _get_client()
    resp = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=300,
        messages=[{
            "role": "user",
            "content": (
                f"인스타그램 {agent_kr} 에이전트 답변에서 중요 결정/확정 사항/수치를 "
                "한 줄~세 줄로 요약해주세요. 없으면 빈 문자열만 반환.\n\n"
                f"{reply[:1000]}"
            ),
        }],
    )
    return resp.content[0].text.strip()


def answer_query(query: str, entries: list[ContentEntry]) -> str:
    """
    자연어 쿼리에 맞게 아카이브를 필터링/요약합니다.
    """
    if not entries:
        return "아직 아카이브가 비어있어요."

    lines = [f"- {e.title} | {e.type} | {e.status} | 진행: {e.progress} | 평점: {e.rating or '-'} | 날짜: {e.date_updated}" for e in entries]
    archive_text = "\n".join(lines)

    prompt = f"""아카이브:
{archive_text}

질문: {query}

질문에 맞게 관련 항목만 추려서 간결하게 한국어로 답변해주세요."""

    return chat("secretary", prompt)


def instagram_chat(agent: str, user_message: str, history: list[dict] | None = None, figma_context: str = "", memo_context: str = "") -> str:
    """인스타그램 팀 에이전트 대화"""
    from .instagram_prompts import INSTAGRAM_PROMPTS
    system = INSTAGRAM_PROMPTS.get(agent, INSTAGRAM_PROMPTS["manager"])
    if figma_context:
        system = f"{system}\n\n---\n현재 피그마 컴포넌트 현황:\n{figma_context}"
    if memo_context:
        system = f"{system}\n\n---\n이전 저장 메모 (중요 결정/피드백):\n{memo_context}"

    messages = (history or [])[:]
    messages.append({"role": "user", "content": user_message})

    client = _get_client()
    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2048,
        system=system,
        messages=messages,
    )
    return resp.content[0].text.strip()
