"""
캡처 처리: 링크/텍스트/이미지를 옵시디언 노트(마크다운)로 변환.
- 링크: 페이지 내용을 가져와 Claude가 요약 + '왜 저장했나' 한 줄 생성
- 텍스트: 그 자체를 아이디어 노트로 정리
- 이미지: Drive 업로드 후 노트에 임베드
"""
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone

import anthropic
import requests

logger = logging.getLogger(__name__)

_URL_RE = re.compile(r"https?://[^\s]+")
_client = None


def _now_kst() -> datetime:
    return datetime.now(timezone(timedelta(hours=9))).replace(tzinfo=None)


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    return _client


def find_url(text: str):
    m = _URL_RE.search(text or "")
    return m.group(0) if m else None


def _slugify(title: str) -> str:
    """파일명용 — 한글 유지, 특수문자 제거, 공백 → _"""
    s = re.sub(r"[\\/:*?\"<>|#\[\]]", "", title or "").strip()
    s = re.sub(r"\s+", "_", s)
    return s[:50] or "무제"


# ── 링크 내용 가져오기 ──────────────────────────────────────
def fetch_link(url: str) -> dict:
    """
    URL의 제목/설명/본문 일부를 best-effort로 추출.
    반환: {"title": str, "description": str, "text": str}
    """
    # 유튜브는 oEmbed로 제목/채널만 확실히
    if "youtube.com" in url or "youtu.be" in url:
        try:
            r = requests.get(
                "https://www.youtube.com/oembed",
                params={"url": url, "format": "json"},
                timeout=10,
            )
            if r.status_code == 200:
                j = r.json()
                return {
                    "title": j.get("title", ""),
                    "description": f"YouTube · {j.get('author_name', '')}",
                    "text": "",
                }
        except Exception:
            pass

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
        )
    }
    try:
        r = requests.get(url, headers=headers, timeout=12)
        html = r.text
    except Exception as e:
        logger.warning(f"링크 fetch 실패: {e}")
        return {"title": "", "description": "", "text": ""}

    def _meta(prop_patterns):
        for pat in prop_patterns:
            m = re.search(pat, html, re.IGNORECASE)
            if m:
                return m.group(1).strip()
        return ""

    title = _meta([
        r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']',
        r"<title[^>]*>([^<]+)</title>",
    ])
    desc = _meta([
        r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)["\']',
    ])
    # 본문 텍스트 대략 추출 (태그 제거)
    body = re.sub(r"<script.*?</script>|<style.*?</style>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    body = re.sub(r"<[^>]+>", " ", body)
    body = re.sub(r"\s+", " ", body).strip()
    return {"title": title, "description": desc, "text": body[:3000]}


# ── Claude 요약 ─────────────────────────────────────────────
def summarize(source_type: str, url: str, raw: dict, user_text: str) -> dict:
    """
    반환: {"title","summary","why","tags":[...],"hub"}
    source_type: "link" | "idea"
    """
    if source_type == "link":
        material = (
            f"URL: {url}\n"
            f"제목: {raw.get('title','')}\n"
            f"설명: {raw.get('description','')}\n"
            f"본문 일부: {raw.get('text','')[:2000]}\n"
            f"사용자가 덧붙인 말: {user_text or '(없음)'}"
        )
    else:
        material = f"사용자가 보낸 생각/아이디어:\n{user_text}"

    prompt = (
        "다음 내용을 옵시디언 노트로 정리해줘. JSON만 반환:\n"
        "{\n"
        '  "title": "짧고 명확한 제목 (한국어, 15자 내외)",\n'
        '  "summary": "핵심 2~3줄 요약 (한국어)",\n'
        '  "why": "이걸 왜 저장했을지/어디에 쓸모있을지 한 줄 추측 (한국어)",\n'
        '  "tags": ["태그2~4개 (한국어, #없이)"],\n'
        '  "hub": "이 내용이 속할 만한 큰 주제 하나 (예: 루틴과 습관, 글쓰기, 브랜딩)"\n'
        "}\n\n"
        f"내용:\n{material}"
    )
    # 요약 품질이 중요 → 기본 Sonnet (환경변수로 조정 가능)
    model = os.getenv("INBOX_SUMMARY_MODEL", "claude-sonnet-5")
    try:
        resp = _get_client().messages.create(
            model=model,
            max_tokens=600,
            messages=[{"role": "user", "content": prompt}],
        )
        raw_txt = resp.content[0].text.strip()
        m = re.search(r"\{.*\}", raw_txt, re.DOTALL)
        if m:
            data = json.loads(m.group())
            data.setdefault("tags", [])
            return data
    except Exception as e:
        logger.warning(f"summarize 실패: {e}")

    # 폴백
    return {
        "title": (raw.get("title") or (user_text or "메모")[:20]),
        "summary": raw.get("description", "") or (user_text or ""),
        "why": "",
        "tags": [],
        "hub": "",
    }


# ── 마크다운 노트 생성 ──────────────────────────────────────
def build_note(source_type: str, parsed: dict, url: str = "", user_text: str = "",
               image_url: str = "") -> tuple[str, str]:
    """
    반환: (파일경로, 마크다운 내용)
    파일경로: Inbox/2026-07-07_1430_제목.md
    """
    now = _now_kst()
    stamp_file = now.strftime("%Y-%m-%d_%H%M")
    stamp_human = now.strftime("%Y-%m-%d %H:%M")
    title = parsed.get("title") or "메모"
    path = f"Inbox/{stamp_file}_{_slugify(title)}.md"

    tags = parsed.get("tags") or []
    tags_yaml = "[" + ", ".join(tags) + "]" if tags else "[]"
    hub = (parsed.get("hub") or "").strip()

    lines = [
        "---",
        f"type: {source_type}",
        f"created: {stamp_human}",
    ]
    if url:
        lines.append(f"source: {url}")
    lines.append(f"tags: {tags_yaml}")
    if hub:
        lines.append(f'hub: "{hub}"')
    lines.append("---")
    lines.append("")
    lines.append(f"# {title}")
    lines.append("")

    # 사용자가 덧붙인 말(진짜 신호)을 맨 위, 눈에 띄게
    if user_text and user_text.strip() and user_text.strip() != url:
        lines.append(f"## ✍️ 내 생각")
        lines.append(user_text.strip())
        lines.append("")
    else:
        # 아직 내 생각이 없으면 나중에 채우도록 자리 남겨둠
        lines.append("## ✍️ 내 생각")
        lines.append("_(무엇이 나를 건드렸나? 나중에 한 줄)_")
        lines.append("")

    if image_url:
        lines.append(f"![]({image_url})")
        lines.append("")

    # 자동 요약은 '보조'로 아래에, AI 생성임을 표시
    summary = (parsed.get("summary") or "").strip()
    why = (parsed.get("why") or "").strip()
    if summary or why:
        lines.append("---")
        lines.append("### 🤖 자동 요약 (참고용)")
        if summary:
            lines.append(summary)
        if why:
            lines.append(f"\n💡 쓸모 추측: {why}")
        lines.append("")

    if url:
        lines.append(f"🔗 [원본 링크]({url})")
        lines.append("")

    if hub:
        # 옵시디언 그래프 연결용 위키링크
        lines.append(f"관련: [[{hub}]]")
        lines.append("")

    return path, "\n".join(lines)
