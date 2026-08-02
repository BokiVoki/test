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
import re
import uuid
from datetime import datetime, timezone

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


def _post(table: str, row: dict) -> None:
    resp = requests.post(
        f"{SUPABASE_URL}/rest/v1/{table}",
        headers={
            "apikey": SERVICE_KEY, "Authorization": f"Bearer {SERVICE_KEY}",
            "Content-Type": "application/json", "Prefer": "return=minimal",
        },
        json=row, timeout=15,
    )
    if resp.status_code >= 300:
        raise RuntimeError(f"{table} 저장 실패 ({resp.status_code}): {resp.text[:200]}")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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


# ── 옛 볼트 폴더('작가 공부' 등, 니들보스에서 옮긴 것)를 art_artists로 가져오기 ──
def _parse_artist_note(fallback_name: str, content: str) -> tuple[str, list, str, bool]:
    """작가 노트 원문에서 (이름, 태그, 본문메모, artist노트여부)를 뽑는다.
    형식: '# 작가이름' 다음 줄에 '#artist #스타일' 같은 인라인 태그, 그 아래 자유 텍스트.
    frontmatter(tags: [...])가 있으면 그쪽을 우선. '#artist' 태그가 없으면(README 등
    작가노트가 아닌 파일) is_artist=False — 가져오기에서 걸러내는 용도."""
    lines = content.splitlines()
    body_start, fm_tags = 0, []
    if lines and lines[0].strip() == "---":
        try:
            end = lines.index("---", 1)
            for l in lines[1:end]:
                m = re.match(r"tags:\s*\[(.*)\]", l.strip())
                if m:
                    fm_tags = [t.strip().lstrip("#") for t in m.group(1).split(",") if t.strip()]
            body_start = end + 1
        except ValueError:
            pass
    body = "\n".join(lines[body_start:])

    m_h1 = re.search(r"^#\s+(.+)$", body, re.MULTILINE)
    name = (m_h1.group(1).strip() if m_h1 else fallback_name) or fallback_name
    if m_h1:
        body = body[: m_h1.start()] + body[m_h1.end():]

    inline_tags = re.findall(r"(?<!\S)#([^\s#\[\]]+)", body)
    raw_tags = fm_tags or inline_tags
    is_artist = any(t.strip().lower() == "artist" for t in raw_tags)
    tags = capture._clean_tags([t for t in raw_tags if t.strip().lower() != "artist"])

    return name.strip(), tags, body.strip(), is_artist


def _walk_md(folder: str):
    """folder 이하를 재귀적으로 훑어 (하위폴더 경로 조각들, 파일명) 을 낸다.
    폴더 구조 자체가 카테고리로 쓰인 경우가 있어서(예: '작가 공부/다다,포스트모더니즘,팝/작가.md')
    상위 폴더 이름들도 같이 돌려준다(폴더명이 태그로 흡수될 수 있게)."""
    for item in vault.list_dir(folder):
        name = item.get("name", "")
        if item.get("type") == "file" and name.endswith(".md"):
            yield [], name
        elif item.get("type") == "dir":
            for sub_parts, fname in _walk_md(f"{folder}/{name}"):
                yield [name] + sub_parts, fname


def import_artists_from_vault(folder: str = "작가 공부") -> tuple[int, int, list]:
    """옵시디언 볼트의 옛 작가노트 폴더(하위 폴더까지 재귀)를 하루앱 art_artists로 가져온다.
    하위 폴더 이름(쉼표로 여러 개면 각각)은 태그로 흡수한다(예: '다다,포스트모더니즘,팝' → 3개 태그).
    이미 같은 이름의 작가가 있으면 건너뜀(중복 방지, 여러 번 돌려도 안전).
    이미지는 옮기지 않는다(Private 볼트 이미지는 웹앱에서 바로 못 씀 — 텍스트만).
    반환: (새로 만든 수, 건너뛴 수, 예시 [(이름, 태그)...])"""
    if not is_configured():
        raise RuntimeError(
            "연결이 안 됐어요. Railway(인박스봇)에 SUPABASE_URL / SUPABASE_SERVICE_KEY / "
            "HARU_OWNER_ID 를 넣어주세요."
        )
    existing = _get("art_artists", "name")
    existing_names = {(e.get("name") or "").strip().lower() for e in existing}

    created = skipped = 0
    examples = []
    for sub_parts, fname in _walk_md(folder):
        subfolder = "/".join([folder] + sub_parts)
        content = vault.read_note(f"{subfolder}/{fname}")
        if content is None:
            continue
        name, tags, note, is_artist = _parse_artist_note(fname[:-3], content)
        if not is_artist:
            continue  # '#artist' 태그가 없는 파일(README 등)은 작가노트가 아니므로 건너뜀
        if not name or name.strip().lower() in existing_names:
            skipped += 1
            continue
        folder_tags = [t.strip() for part in sub_parts for t in part.split(",") if t.strip()]
        tags = capture._clean_tags(tags + folder_tags)
        now = _now_iso()
        row = {
            "id": f"ar{uuid.uuid4().hex[:12]}", "owner": OWNER_ID, "name": name,
            "tags": tags, "note": note, "created": now, "updated": now,
        }
        _post("art_artists", row)
        existing_names.add(name.strip().lower())
        created += 1
        if len(examples) < 3:
            examples.append((name, tags))

    return created, skipped, examples
