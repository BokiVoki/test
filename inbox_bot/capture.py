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

# 쇼핑/구매 의도 키워드 → 위시리스트로 분류
SHOPPING_KEYWORDS = (
    "사고싶", "사고 싶", "살까", "사야", "구매", "지름", "지를", "질러",
    "갖고싶", "갖고 싶", "가지고싶", "위시", "장바구니", "얼마", "할인", "세일",
    "쿠폰", "최저가", "가격", "직구", "주문", "품절",
)


def is_shopping(text: str) -> bool:
    t = (text or "").lower()
    return any(k in t for k in SHOPPING_KEYWORDS)


def derive_tags(thought: str) -> dict:
    """사용자가 직접 쓴 '내 생각'에서 태그/허브를 뽑는다. 반환 {"tags":[...], "hub": "..."}"""
    if not thought or not thought.strip():
        return {"tags": [], "hub": ""}
    prompt = (
        "다음은 사용자가 직접 남긴 생각/메모야. 이 사람의 관점에서 핵심을 태그로 뽑아줘. JSON만:\n"
        '{ "tags": ["구체적 태그 2~4개 (한국어, #없이, 내용의 핵심 개념)"],'
        ' "hub": "이 생각이 속할 큰 주제 하나 (예: 트렌드, F&B, 브랜딩)" }\n\n'
        f"메모: {thought.strip()}"
    )
    model = os.getenv("INBOX_SUMMARY_MODEL", "claude-sonnet-5")
    for m_name in (model, "claude-haiku-4-5"):
        try:
            resp = _get_client().messages.create(
                model=m_name, max_tokens=300,
                messages=[{"role": "user", "content": prompt}],
            )
            mm = re.search(r"\{.*\}", resp.content[0].text.strip(), re.DOTALL)
            if mm:
                data = json.loads(mm.group())
                data.setdefault("tags", [])
                data.setdefault("hub", "")
                return data
        except Exception as e:
            logger.warning(f"derive_tags 실패 (model={m_name}): {e}")
    return {"tags": [], "hub": ""}


def merge_tags_into_note(content: str, new_tags: list, new_hub: str = "") -> str:
    """기존 노트 frontmatter의 tags에 new_tags를 합치고, hub가 비어있으면 채운다."""
    # tags 병합
    m = re.search(r"^tags: \[(.*)\]\s*$", content, re.MULTILINE)
    existing = []
    if m:
        existing = [t.strip() for t in m.group(1).split(",") if t.strip()]
    merged = existing[:]
    for t in (new_tags or []):
        t = (t or "").strip()
        if t and t not in merged:
            merged.append(t)
    tags_line = "tags: [" + ", ".join(merged) + "]"
    if m:
        content = content[:m.start()] + tags_line + content[m.end():]

    # hub 채우기 (기존에 없을 때만)
    if new_hub and not re.search(r"^hub:", content, re.MULTILINE):
        # tags 라인 뒤에 hub 삽입
        content = re.sub(r"(^tags: \[.*\]\s*$)", r'\1\nhub: "' + new_hub + '"',
                         content, count=1, flags=re.MULTILINE)
        # 그래프 연결용 위키링크도 본문 끝에 추가
        if f"[[{new_hub}]]" not in content:
            content = content.rstrip() + f"\n\n관련: [[{new_hub}]]\n"
    return content


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
    # 링크인데 봇이 내용을 못 읽은 경우 (인스타/로그인 필요 사이트 등)
    has_content = bool((raw.get("description") or "").strip() or (raw.get("text") or "").strip())
    user_added = bool(user_text and user_text.strip() and user_text.strip() != (url or ""))
    if source_type == "link" and not has_content and not user_added:
        domain = re.sub(r"^https?://(www\.)?", "", url or "").split("/")[0]
        return {
            "title": (raw.get("title") or domain or "링크"),
            "summary": "",
            "why": "",
            "tags": [],
            "hub": "",
            "unreadable": True,  # 내용을 못 읽음 → 자동요약 스킵
        }

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
    # 요약 품질이 중요 → 기본 Sonnet. 실패하면 Haiku로 재시도 (없는 것보단 나음)
    primary = os.getenv("INBOX_SUMMARY_MODEL", "claude-sonnet-5")
    for model in (primary, "claude-haiku-4-5"):
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
            logger.warning(f"summarize 실패 (model={model}): {e}")

    # 최종 폴백 — URL을 요약칸에 넣지 않는다 (지저분함 방지)
    return {
        "title": (raw.get("title") or "메모"),
        "summary": (raw.get("description") or "").strip(),
        "why": "",
        "tags": [],
        "hub": "",
    }


# ── 쇼핑/위시리스트 ─────────────────────────────────────────
def parse_shopping(text: str, raw: dict, image_bytes: bytes = None) -> dict:
    """
    상품 정보 추출. 사진이 있으면 Claude 비전으로 화면 속 상품/가격까지 읽는다.
    반환: {"item","price","where","reason","tags"}
    """
    import base64 as _b64

    material = (
        f"사용자 메모/캡션: {text or '(없음)'}\n"
        f"링크: {raw.get('url','') if raw else ''}\n"
        f"페이지 제목: {raw.get('title','') if raw else ''}\n"
        f"페이지 설명: {raw.get('description','') if raw else ''}"
    )
    ask = (
        "이건 사용자가 '사고 싶어서' 저장한 거야. 상품 정보를 뽑아서 JSON만 반환:\n"
        "{\n"
        '  "item": "상품명 (한국어, 간결히)",\n'
        '  "price": "가격 (숫자+원, 모르면 빈 문자열)",\n'
        '  "where": "판매처/브랜드/사이트 (모르면 빈 문자열)",\n'
        '  "reason": "왜 사고 싶어 보이는지 한 줄 (한국어)",\n'
        '  "tags": ["카테고리 태그 2~3개 (예: 패션, 가전, 뷰티)"]\n'
        "}\n"
        "사진이 있으면 사진 속 글자(상품명·가격)를 최대한 읽어줘.\n\n"
        f"{material}"
    )

    content = []
    if image_bytes:
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": "image/jpeg",
                       "data": _b64.b64encode(image_bytes).decode("ascii")},
        })
    content.append({"type": "text", "text": ask})

    model = os.getenv("INBOX_SUMMARY_MODEL", "claude-sonnet-5")
    for m_name in (model, "claude-haiku-4-5"):
        try:
            resp = _get_client().messages.create(
                model=m_name,
                max_tokens=500,
                messages=[{"role": "user", "content": content}],
            )
            raw_txt = resp.content[0].text.strip()
            mm = re.search(r"\{.*\}", raw_txt, re.DOTALL)
            if mm:
                data = json.loads(mm.group())
                data.setdefault("tags", [])
                return data
        except Exception as e:
            logger.warning(f"parse_shopping 실패 (model={m_name}): {e}")

    return {"item": (text or "사고 싶은 것")[:30], "price": "", "where": "",
            "reason": "", "tags": ["쇼핑"]}


def build_shopping_note(parsed: dict, url: str = "", user_text: str = "",
                        image_embed: str = "") -> tuple[str, str]:
    """쇼핑 위시리스트 노트 생성 → Shopping/ 폴더에 저장."""
    now = _now_kst()
    stamp_file = now.strftime("%Y-%m-%d_%H%M")
    stamp_human = now.strftime("%Y-%m-%d %H:%M")
    item = (parsed.get("item") or "사고 싶은 것").strip()
    path = f"Shopping/{stamp_file}_{_slugify(item)}.md"

    tags = parsed.get("tags") or []
    if "쇼핑" not in tags:
        tags = ["쇼핑"] + tags
    tags_yaml = "[" + ", ".join(tags) + "]"
    price = (parsed.get("price") or "").strip()
    where = (parsed.get("where") or "").strip()
    reason = (parsed.get("reason") or "").strip()

    lines = [
        "---",
        "type: shopping",
        f"created: {stamp_human}",
        "status: 고민중",
        f'price: "{price}"',
        f"tags: {tags_yaml}",
        "---",
        "",
        f"# 🛒 {item}",
        "",
        "- [ ] 살까 말까?",
        "",
        f"💰 가격: {price or '?'}",
        f"🏬 어디서: {where or (url or '?')}",
        "",
    ]

    if image_embed:
        lines.append(f"![[{image_embed}]]")
        lines.append("")

    lines.append("## ✍️ 왜 갖고 싶어?")
    if user_text and user_text.strip():
        lines.append(user_text.strip())
    elif reason:
        lines.append(f"_{reason}_ (자동 추측)")
    else:
        lines.append("_(나중에 한 줄)_")
    lines.append("")

    if url:
        lines.append(f"🔗 [상품 링크]({url})")
        lines.append("")

    return path, "\n".join(lines)


# ── 마크다운 노트 생성 ──────────────────────────────────────
def build_note(source_type: str, parsed: dict, url: str = "", user_text: str = "",
               image_url: str = "", image_embed: str = "") -> tuple[str, str]:
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

    if image_embed:
        # 볼트에 직접 넣은 이미지 → 옵시디언 위키링크 임베드 (확실히 보임)
        lines.append(f"![[{image_embed}]]")
        lines.append("")
    elif image_url:
        lines.append(f"![]({image_url})")
        lines.append("")

    # 자동 요약은 '보조'로 아래에, AI 생성임을 표시
    summary = (parsed.get("summary") or "").strip()
    why = (parsed.get("why") or "").strip()
    if parsed.get("unreadable"):
        # 봇이 내용을 못 읽은 경우 (인스타/로그인 필요 사이트 등)
        lines.append("---")
        lines.append("_🔒 이 링크는 봇이 내용을 못 읽었어요 (로그인이 필요한 사이트일 수 있어요). "
                     "위에 내 생각을 남겨두면 그게 기록이 돼요._")
        lines.append("")
    elif summary or why:
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
