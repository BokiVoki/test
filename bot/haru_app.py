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


def add_to_pocket(title: str, ws: str | None = None, due: str | None = None,
                  bucket: str = "inbox", endd: str | None = None) -> None:
    """할일 하나를 하루앱에 넣는다. (기본 주머니 bucket='inbox')

    due: 'YYYY-MM-DD' 마감일(선택). bucket: 'inbox'|'active'|'later'.
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
        "due": due or None,
        "today": False,
        "done": False,
        "imp": 1,
        "est": 0,
        "repeat": "none",
        "repday": "",
        "bucket": bucket or "inbox",
        "lastdone": None,
        "memoid": None,
        "sort": 0,
        "owner": OWNER_ID,
    }
    if endd:
        row["endd"] = endd  # 기간 일정 종료일 (todos.endd 컬럼 필요)
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


def list_due_reminders(now_iso: str) -> list[dict]:
    """설정한 시간이 지났는데 아직 안 보낸 ⏰ 할일 알림 목록(하루앱 시계 아이콘).

    now_iso: UTC ISO8601 문자열(예: datetime.now(timezone.utc).isoformat()).
    실패해도 예외 없이 빈 리스트(알림 체크 잡이 죽지 않게).
    반환: [{'id','title'} ...]
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
                ("select", "id,title"),
                ("owner", f"eq.{OWNER_ID}"),
                ("remind_at", "not.is.null"),
                ("remind_at", f"lte.{now_iso}"),
                ("reminded", "eq.false"),
                ("done", "eq.false"),
                ("archived", "eq.false"),
            ],
            timeout=15,
        )
        if resp.status_code >= 300:
            return []
        data = resp.json()
        return data if isinstance(data, list) else []
    except Exception:
        return []


def mark_reminded(todo_id: str) -> None:
    """⏰ 알림을 보낸 할일을 reminded=true로 표시해 중복 발송을 막는다. 실패해도 조용히 삼킴."""
    if not is_configured():
        return
    try:
        requests.patch(
            f"{SUPABASE_URL}/rest/v1/todos",
            headers={
                "apikey": SERVICE_KEY,
                "Authorization": f"Bearer {SERVICE_KEY}",
                "Content-Type": "application/json",
                "Prefer": "return=minimal",
            },
            params=[("id", f"eq.{todo_id}")],
            json={"reminded": True},
            timeout=15,
        )
    except Exception:
        pass


def list_due_sub_reminders(now_iso: str) -> list[dict]:
    """하위 항목(subs jsonb 배열 안) ⏰ 알림 목록(2026-09-30, "하위항목도 가능하게").

    remind_at처럼 컬럼으로 안 뽑혀있고 todos.subs jsonb 배열 안에 항목별로 들어있어서
    PostgREST 필터로 직접 못 거르니, 안 끝난 할일을 통째로 가져와 파이썬에서 훑는다.
    개인 단일사용자 앱이라 데이터량이 적어 매번 전체를 훑어도 무리 없음.
    실패해도 예외 없이 빈 리스트(알림 체크 잡이 죽지 않게).
    반환: [{'todo_id','sub_index','title'} ...]
    """
    if not is_configured():
        return []
    try:
        resp = requests.get(
            f"{SUPABASE_URL}/rest/v1/todos",
            headers={"apikey": SERVICE_KEY, "Authorization": f"Bearer {SERVICE_KEY}"},
            params=[
                ("select", "id,subs"),
                ("owner", f"eq.{OWNER_ID}"),
                ("done", "eq.false"),
                ("archived", "eq.false"),
            ],
            timeout=15,
        )
        if resp.status_code >= 300:
            return []
        data = resp.json()
        out = []
        for row in data if isinstance(data, list) else []:
            subs = row.get("subs") or []
            if not isinstance(subs, list):
                continue
            for i, s in enumerate(subs):
                if not isinstance(s, dict) or s.get("d"):
                    continue
                remind_at = s.get("remindAt")
                if remind_at and not s.get("reminded") and remind_at <= now_iso:
                    out.append({"todo_id": row["id"], "sub_index": i, "title": s.get("t") or "하위 항목"})
        return out
    except Exception:
        return []


def mark_sub_reminded(todo_id: str, sub_index: int) -> None:
    """하위 항목 알림을 보낸 뒤 그 항목만 reminded=true로 표시(읽고-고치고-쓰기 방식 —
    subs가 jsonb 배열이라 그 안 특정 원소만 콕 집어 갱신하는 PostgREST 연산이 없음).
    같은 할일의 다른 하위 항목·다른 필드는 안 건드림. 실패해도 조용히 삼킴."""
    if not is_configured():
        return
    try:
        resp = requests.get(
            f"{SUPABASE_URL}/rest/v1/todos",
            headers={"apikey": SERVICE_KEY, "Authorization": f"Bearer {SERVICE_KEY}"},
            params=[("select", "subs"), ("id", f"eq.{todo_id}")],
            timeout=15,
        )
        if resp.status_code >= 300:
            return
        rows = resp.json()
        if not rows:
            return
        subs = rows[0].get("subs") or []
        if not isinstance(subs, list) or sub_index >= len(subs) or not isinstance(subs[sub_index], dict):
            return
        subs[sub_index]["reminded"] = True
        requests.patch(
            f"{SUPABASE_URL}/rest/v1/todos",
            headers={
                "apikey": SERVICE_KEY,
                "Authorization": f"Bearer {SERVICE_KEY}",
                "Content-Type": "application/json",
                "Prefer": "return=minimal",
            },
            params=[("id", f"eq.{todo_id}")],
            json={"subs": subs},
            timeout=15,
        )
    except Exception:
        pass


def list_open() -> list[dict]:
    """안 끝난·완료함 아닌 할일 전체(추천용). 실패 시 빈 리스트.

    반환: [{'title','due','project','ws','imp','est','bucket','today'} ...]
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
                ("select", "title,due,project,ws,imp,est,bucket,today"),
                ("owner", f"eq.{OWNER_ID}"),
                ("done", "eq.false"),
                ("archived", "eq.false"),
            ],
            timeout=15,
        )
        if resp.status_code >= 300:
            return []
        data = resp.json()
        return data if isinstance(data, list) else []
    except Exception:
        return []
