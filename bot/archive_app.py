"""일정 비서봇의 아카이브(책/웹툰/영화 등) → 하루앱 Supabase `archive` 표로 거울복사.

봇의 Archive 시트(Google Sheets)는 그대로 원본으로 남고, 여기서는 같은 내용을
Supabase `archive` 테이블에도 매번 같이 써서(add/update/delete) 하루앱 웹앱에서
바로 보고 고칠 수 있게 한다. 실패해도 봇의 시트 기능 자체는 안 깨지게
(mirror는 best-effort) 예외를 삼키고 조용히 넘어간다 — 호출부에서 신경 안 써도 됨.

환경변수는 haru_app.py 와 동일한 것을 공유(SUPABASE_URL/SUPABASE_SERVICE_KEY/HARU_OWNER_ID).
"""
import os

import requests

from .models import ContentEntry


def _base_url(raw: str) -> str:
    u = (raw or "").strip().rstrip("/")
    for suffix in ("/rest/v1", "/rest"):
        if u.endswith(suffix):
            u = u[: -len(suffix)].rstrip("/")
    return u


SUPABASE_URL = _base_url(os.getenv("SUPABASE_URL", ""))
SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")
OWNER_ID = os.getenv("HARU_OWNER_ID", "")


def is_configured() -> bool:
    return bool(SUPABASE_URL and SERVICE_KEY and OWNER_ID)


def _headers(upsert: bool = False) -> dict:
    h = {
        "apikey": SERVICE_KEY,
        "Authorization": f"Bearer {SERVICE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal",
    }
    if upsert:
        h["Prefer"] = "resolution=merge-duplicates,return=minimal"
    return h


def _row(entry: ContentEntry) -> dict:
    return {
        "id": entry.id,
        "owner": OWNER_ID,
        "title": entry.title,
        "type": entry.type,
        "status": entry.status,
        "progress": entry.progress,
        "rating": entry.rating,
        "notes": entry.notes,
        "tags": entry.tags,
        "date_added": entry.date_added,
        "date_updated": entry.date_updated,
        "date_completed": entry.date_completed,
        "source": entry.source,
        "raw_log": entry.raw_log,
        "author": entry.author,
        "year_watched": entry.year_watched,
        "publisher": entry.publisher,
    }


def upsert_entry(entry: ContentEntry) -> None:
    """항목 하나를 Supabase에 저장(있으면 갱신, 없으면 새로 생성). 실패해도 조용히 넘어감."""
    if not is_configured() or not entry.id:
        return
    try:
        requests.post(
            f"{SUPABASE_URL}/rest/v1/archive?on_conflict=id",
            headers=_headers(upsert=True),
            json=_row(entry),
            timeout=15,
        )
    except Exception:
        pass


def batch_upsert(entries: list[ContentEntry]) -> None:
    if not is_configured() or not entries:
        return
    try:
        requests.post(
            f"{SUPABASE_URL}/rest/v1/archive?on_conflict=id",
            headers=_headers(upsert=True),
            json=[_row(e) for e in entries if e.id],
            timeout=20,
        )
    except Exception:
        pass


def delete_entry(entry_id: str) -> None:
    if not is_configured() or not entry_id:
        return
    try:
        requests.delete(
            f"{SUPABASE_URL}/rest/v1/archive",
            headers=_headers(),
            params=[("id", f"eq.{entry_id}")],
            timeout=15,
        )
    except Exception:
        pass
