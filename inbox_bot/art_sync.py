"""그림공부(하루앱) → 옵시디언 볼트 한 방향 동기화.

하루앱(Supabase art_artists/art_works)이 원본이고, 여기서는 그 내용을
옵시디언 노트로 미러링만 한다(웹앱에서 하루앱 쪽으로 다시 반영되진 않음).
작가 노트 태그 + 그림 자체 태그를 합쳐서(효과 태그) 그림 노트에 적어두므로
"작가에 태그를 달면 그 작가 그림들에 다 적용"이 옵시디언 쪽에도 그대로 보인다.

환경변수(일정봇의 SUPABASE_* 와 이름을 맞췄다 — 같은 값을 인박스봇 서비스에도 넣으면 됨):
  SUPABASE_URL, SUPABASE_SERVICE_KEY, HARU_OWNER_ID
"""
import logging
import os

import requests

from . import capture, vault

logger = logging.getLogger(__name__)


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
    return bool(SUPABASE_URL and SERVICE_KEY and OWNER_ID) and vault.is_configured()


def _get(table: str, select: str) -> list:
    resp = requests.get(
        f"{SUPABASE_URL}/rest/v1/{table}",
        headers={"apikey": SERVICE_KEY, "Authorization": f"Bearer {SERVICE_KEY}"},
        params=[("select", select), ("owner", f"eq.{OWNER_ID}")],
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def _effective_tags(artist: dict, work: dict) -> list:
    return capture._clean_tags((artist.get("tags") or []) + (work.get("tags") or []))


def _artist_note(artist: dict) -> str:
    tags = capture._clean_tags(artist.get("tags") or [])
    lines = [
        "---", "type: artist", f"art_id: {artist['id']}",
        "tags: [" + ", ".join(tags) + "]", "---", "",
        f"# {artist.get('name','')}", "",
    ]
    note = (artist.get("note") or "").strip()
    lines.append(note if note else "_(아직 이 작가에 대한 공부 메모 없음)_")
    lines.append("")
    return "\n".join(lines)


def _work_note(artist: dict, work: dict) -> str:
    tags = _effective_tags(artist, work)
    lines = [
        "---", "type: artwork", f"art_id: {work['id']}",
        f'year: "{work.get("year","")}"', f'genre: "{work.get("genre","")}"',
        f'medium: "{work.get("medium","")}"',
        "tags: [" + ", ".join(tags) + "]", "---", "",
        f"# {work.get('title','')}", "",
        f"아티스트: [[{artist.get('name','')}]]", "",
    ]
    if work.get("image_url"):
        lines.append(f"![]({work['image_url']})")
        lines.append("")
    lines.append("## 공부한 것")
    note = (work.get("note") or "").strip()
    lines.append(note if note else "_(아직 없음)_")
    lines.append("")
    if work.get("source_url"):
        lines.append(f"🔗 [출처]({work['source_url']})")
        lines.append("")
    return "\n".join(lines)


def sync_art() -> tuple[int, int]:
    """하루앱의 작가/그림을 옵시디언 노트로 미러링. 내용이 바뀐 노트만 커밋.
    반환: (작가 갱신 수, 그림 갱신 수)"""
    if not is_configured():
        raise RuntimeError(
            "그림공부 동기화 연결이 안 됐어요. Railway(인박스봇)에 "
            "SUPABASE_URL / SUPABASE_SERVICE_KEY / HARU_OWNER_ID 를 넣어주세요."
        )
    artists = _get("art_artists", "id,name,tags,note")
    works = _get("art_works", "id,artist_id,title,year,genre,medium,image_url,source_url,tags,note")
    by_id = {a["id"]: a for a in artists}

    a_changed = w_changed = 0
    for a in artists:
        name = (a.get("name") or "").strip()
        if not name:
            continue
        path = f"Artists/{capture._slugify(name)}.md"
        content = _artist_note(a)
        if vault.read_note(path) != content:
            vault.write_note(path, content, commit_msg=f"art sync: {name}")
            a_changed += 1

    for w in works:
        a = by_id.get(w.get("artist_id"))
        if not a:
            continue
        title = (w.get("title") or "").strip()
        if not title:
            continue
        path = f"Artworks/{capture._slugify(a.get('name',''))} - {capture._slugify(title)}.md"
        content = _work_note(a, w)
        if vault.read_note(path) != content:
            vault.write_note(path, content, commit_msg=f"art sync: {a.get('name','')} - {title}")
            w_changed += 1

    return a_changed, w_changed
