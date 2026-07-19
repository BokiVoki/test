"""하루(Haru) 웹앱 주머니(inbox)로 할일 던지기.

일정 비서봇에서 "앱 ..." 메시지를 받으면 이 모듈로 Supabase todos 테이블에
bucket='inbox' 로 바로 insert 한다. (RLS 우회를 위해 service key 사용)

환경변수:
  SUPABASE_URL         예) https://xxxx.supabase.co
  SUPABASE_SERVICE_KEY service_role 키 (비밀!) — Railway 에만 넣기
  HARU_OWNER_ID        할일 소유자(로그인 계정)의 uuid
  HARU_WORKSPACE       (선택) 기본 워크스페이스. 기본값 '일'
"""
import os
import uuid

import requests

def _base_url(raw: str) -> str:
    """SUPABASE_URL 을 정규화한다.

    실수로 뒤에 '/rest/v1' 또는 '/rest/v1/' 를 붙여 넣어도 떼어내서
    프로젝트 루트 URL(https://xxxx.supabase.co)만 남긴다.
    """
    u = (raw or "").strip().rstrip("/")
    for suffix in ("/rest/v1", "/rest"):
        if u.endswith(suffix):
            u = u[: -len(suffix)].rstrip("/")
    return u


SUPABASE_URL = _base_url(os.getenv("SUPABASE_URL", ""))
SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")
OWNER_ID = os.getenv("HARU_OWNER_ID", "")
WORKSPACE = os.getenv("HARU_WORKSPACE", "일")


def is_configured() -> bool:
    return bool(SUPABASE_URL and SERVICE_KEY and OWNER_ID)


def add_to_pocket(title: str, ws: str | None = None) -> None:
    """할일 하나를 하루앱 주머니(bucket='inbox')에 넣는다.

    실패하면 예외를 던진다(호출부에서 사용자에게 알림).
    """
    if not is_configured():
        raise RuntimeError(
            "하루앱 연결이 안 됐어요. Railway에 SUPABASE_URL / "
            "SUPABASE_SERVICE_KEY / HARU_OWNER_ID 를 넣어주세요."
        )
    title = (title or "").strip()
    if not title:
        raise ValueError("빈 내용은 넣을 수 없어요.")

    row = {
        "id": str(uuid.uuid4()),
        "title": title,
        "ws": ws or WORKSPACE,
        "project": "",
        "due": None,
        "today": False,
        "done": False,
        "imp": 1,
        "est": 0,
        "repeat": "none",
        "repday": "",
        "bucket": "inbox",
        "lastdone": None,
        "memoid": None,
        "sort": 0,
        "owner": OWNER_ID,
    }
    resp = requests.post(
        f"{SUPABASE_URL}/rest/v1/todos",
        headers={
            "apikey": SERVICE_KEY,
            "Authorization": f"Bearer {SERVICE_KEY}",
            "Content-Type": "application/json",
            "Prefer": "return=minimal",
        },
        json=row,
        timeout=15,
    )
    if resp.status_code >= 300:
        raise RuntimeError(f"주머니 저장 실패 ({resp.status_code}): {resp.text[:200]}")


def list_due(cutoff_iso: str) -> list[dict]:
    """마감일이 cutoff_iso(YYYY-MM-DD) 이하인, 안 끝난·완료함 아닌 할일 목록.

    브리핑용. 실패해도 예외 없이 빈 리스트를 돌려준다(브리핑을 깨지 않기 위해).
    반환: [{'title','due','project','ws'} ...] due 오름차순.
    """
    if not is_configured():
        return []
    try:
        resp = requests.get(
            f"{SUPABASE_URL}/rest/v1/todos",
            headers={
                "apikey": SERVICE_KEY,
                "Authorization": f"Bearer {SERVICE_KEY}",
            },
            params=[
                ("select", "title,due,project,ws"),
                ("owner", f"eq.{OWNER_ID}"),
                ("done", "eq.false"),
                ("archived", "eq.false"),
                ("due", "not.is.null"),
                ("due", f"lte.{cutoff_iso}"),
                ("order", "due.asc"),
            ],
            timeout=15,
        )
        if resp.status_code >= 300:
            return []
        data = resp.json()
        return data if isinstance(data, list) else []
    except Exception:
        return []
