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


# 책/독서 의도 키워드 → 읽고싶은 책 목록으로 분류
BOOK_KEYWORDS = (
    "읽고싶", "읽고 싶", "읽어보", "읽어볼", "읽을", "독서", "완독",
    "책추천", "책 추천", "도서", "베스트셀러", "저자", "소설", "에세이", "북토크",
)


def is_book(text: str) -> bool:
    t = (text or "").lower()
    return any(k in t for k in BOOK_KEYWORDS)


def parse_book(text: str, raw: dict, image_bytes: bytes = None) -> dict:
    """책 정보 추출. 사진이 있으면 표지/제목/저자를 비전으로 읽는다.
    반환: {"title","author","why","tags"}"""
    import base64 as _b64

    material = (
        f"사용자 메모/캡션: {text or '(없음)'}\n"
        f"링크: {raw.get('url','') if raw else ''}\n"
        f"페이지 제목: {raw.get('title','') if raw else ''}\n"
        f"페이지 설명: {raw.get('description','') if raw else ''}"
    )
    ask = (
        "이건 사용자가 저장한 책/만화 추천이야. **사용자 캡션이 이 저장의 의도**니 그걸 최우선으로 반영해. JSON만 반환:\n"
        "{\n"
        '  "title": "노트 제목 — 사용자 캡션을 핵심으로. 사진 속 프로그램명/시리즈명/코너명(예: 9コマ)은 제목으로 쓰지 마.",\n'
        '  "author": "저자 또는 추천인 (예: 이해인 편집장). 모르면 빈 문자열",\n'
        '  "items": ["사진/내용에 여러 책·만화가 있으면 각 제목을 배열로. 한 권이면 그 하나. 없으면 빈 배열 []"],\n'
        '  "why": "어떤 추천인지/왜 읽고 싶은지 한 줄 (한국어)",\n'
        '  "tags": ["짧은 개념어 태그 2~3개. 공백 없이 한 단어씩(예: 순정만화, 에세이, 자기계발). 문장·구는 금지"]\n'
        "}\n"
        "사진에 여러 만화·책 표지가 격자(grid)로 있으면, 표지 하나하나를 순서대로 훑으며 "
        "각 제목을 최대한 읽어 items에 담아. 일본어 제목이면 원제 그대로 또는 한국어 번역으로. "
        "작거나 흐릿해도 읽히는 만큼 적고, 정말 못 읽는 것만 건너뛰어.\n\n"
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

    for m_name in _model_chain(bool(image_bytes)):
        try:
            resp = _get_client().messages.create(
                model=m_name, max_tokens=700,
                messages=[{"role": "user", "content": content}],
            )
            mm = re.search(r"\{.*\}", resp.content[0].text.strip(), re.DOTALL)
            if mm:
                data = json.loads(mm.group())
                data.setdefault("tags", [])
                data.setdefault("items", [])
                # 캡션이 있으면 제목이 화면 시리즈명으로 새지 않게 보정
                if text and text.strip() and not (data.get("title") or "").strip():
                    data["title"] = text.strip()
                return data
        except Exception as e:
            logger.warning(f"parse_book 실패 (model={m_name}): {e}")
    return {"title": (text or "읽고 싶은 책")[:40], "author": "", "why": "", "items": [], "tags": ["독서"]}


def build_book_note(parsed: dict, url: str = "", user_text: str = "",
                    image_embed: str = "") -> tuple[str, str]:
    """읽고싶은 책 노트 생성 → Books/ 폴더에 저장."""
    now = _now_kst()
    stamp_file = now.strftime("%Y-%m-%d_%H%M")
    stamp_human = now.strftime("%Y-%m-%d %H:%M")
    title = (parsed.get("title") or "읽고 싶은 책").strip()
    path = _note_path("Books", title, now)

    tags = _clean_tags(parsed.get("tags"))
    if "독서" not in tags:
        tags = ["독서"] + tags
    tags_yaml = "[" + ", ".join(tags) + "]"
    author = (parsed.get("author") or "").strip()
    why = (parsed.get("why") or "").strip()
    items = [i.strip() for i in (parsed.get("items") or []) if i and i.strip()]

    lines = [
        "---",
        "type: to-read",
        f"created: {stamp_human}",
        "status: 읽고싶음",
        f'author: "{author}"',
        f"tags: {tags_yaml}",
        "---",
        "",
        f"# 📚 {title}",
        "",
    ]
    if author:
        lines.append(f"✍️ {author}")
        lines.append("")

    # 여러 권 추천이면 각각 체크박스, 한 권이면 단일 체크
    if len(items) > 1:
        lines.append("## 추천 목록")
        for it in items:
            lines.append(f"- [ ] {it}")
        lines.append("")
    else:
        single = items[0] if items else title
        lines.append(f"- [ ] {single}")
        lines.append("")

    if image_embed:
        lines.append(f"![[{image_embed}]]")
        lines.append("")
    lines.append("## 왜 읽고 싶어?")
    if user_text and user_text.strip():
        lines.append(user_text.strip())
    elif why:
        lines.append(f"_{why}_ (자동)")
    else:
        lines.append("_(나중에 한 줄)_")
    lines.append("")
    if url:
        lines.append(f"🔗 [링크]({url})")
        lines.append("")
    return path, "\n".join(lines)


def derive_tags(thought: str) -> dict:
    """사용자가 직접 쓴 '내 생각'에서 태그/허브를 뽑는다. 반환 {"tags":[...], "hub": "..."}"""
    if not thought or not thought.strip():
        return {"tags": [], "hub": ""}
    prompt = (
        "다음은 사용자가 직접 남긴 생각/메모야. 이 사람의 관점에서 핵심을 태그로 뽑아줘. JSON만:\n"
        '{ "tags": ["짧은 개념어 태그 2~4개. 반드시 공백 없는 한 단어(또는 붙여쓴 복합어). '
        '이 메모의 요약이 아니라 속성/종류를 나타내는 라벨(예: 자기성찰, 관계, 성장)"],'
        ' "hub": "이 생각이 속할 더 큰 카테고리 하나 — 여러 메모가 공유할 넓은 주제(예: 트렌드, F&B, 브랜딩). '
        '이 메모만의 구체 요약이 아님" }\n\n'
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
    # tags 병합 (기존 태그도 공백 있으면 이 기회에 정리)
    m = re.search(r"^tags: \[(.*)\]\s*$", content, re.MULTILINE)
    existing = []
    if m:
        existing = _clean_tags(m.group(1).split(","))
    merged = existing[:]
    for t in _clean_tags(new_tags):
        if t not in merged:
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


def _model_chain(has_image: bool) -> list:
    """사진이 있으면 OCR에 강한 비전 모델 우선, 없으면 요약 모델."""
    if has_image:
        return [os.getenv("INBOX_VISION_MODEL", "claude-sonnet-5"), "claude-haiku-4-5"]
    return [os.getenv("INBOX_SUMMARY_MODEL", "claude-sonnet-5"), "claude-haiku-4-5"]


def _clean_tags(tags) -> list:
    """옵시디언 태그 규칙에 맞게 정리. 공백이 있으면 태그로 인식이 안 되므로(#내 태그 X) 무조건 제거,
    중복/빈 값도 정리. 모델이 실수로 공백 태그를 만들어도 여기서 걸러진다(안전장치)."""
    out, seen = [], set()
    for t in (tags or []):
        t = re.sub(r"\s+", "", (t or "").strip())
        t = re.sub(r"[,\[\]#]", "", t)
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out


def _slugify(title: str) -> str:
    """파일명용 — 한글/공백 유지, 파일시스템·옵시디언 금지문자만 제거.
    (옵시디언 그래프에서 노드 이름으로 그대로 보이므로 읽히게 둔다. 밑줄 X)"""
    s = re.sub(r"[\\/:*?\"<>|#^\[\]]", "", title or "")
    s = re.sub(r"\s+", " ", s).strip().rstrip(". ")
    return s[:60] or "무제"


def _note_path(folder: str, title: str, now: datetime) -> str:
    """그래프에서 잘 읽히는 노트 경로: '폴더/제목 YYMMDD-HHMM.md'.
    날짜를 뒤에 짧게 붙여 제목이 앞에 오게 하고(요점이 보임), 중복 방지 + 삭제/글인식 매칭용."""
    stamp = now.strftime("%y%m%d-%H%M")
    return f"{folder}/{_slugify(title)} {stamp}.md"


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
        '  "tags": ["짧은 개념어 태그 2~4개. 공백 없는 한 단어(또는 붙여쓴 복합어). '
        '내용의 요약이 아니라 종류/속성 라벨(예: 감상, 인터뷰, 유튜브, 방법론, 창의력)"],\n'
        '  "hub": "이 내용이 속할 더 큰 카테고리 하나(공백 없이 짧게, 예: 글쓰기, 브랜딩, 자기계발). '
        '여러 노트가 재사용할 넓은 주제이지, 이 노트만의 구체 요약이 아님"\n'
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
def read_photo_text(image_bytes: bytes, caption: str = "") -> dict:
    """텍스트 위주 사진(스크린샷/글 캡처)인지 판단하고, 맞으면 글을 그대로 읽는다.
    반환: {"is_text": bool, "title": str, "text": str, "tags": [..], "hub": str}"""
    import base64 as _b64
    ask = (
        "이 사진을 봐. 글(텍스트)이 화면 대부분을 차지하는 캡처인지 판단해 "
        "(예: SNS 게시물·글·기사·메모 스크린샷). JSON만 반환:\n"
        "{\n"
        '  "is_text": true/false,\n'
        '  "title": "내용을 대표하는 짧은 제목(한국어). 캡션 있으면 반영",\n'
        '  "text": "사진 속 글을 최대한 그대로 옮겨적기. 줄바꿈/문단 유지. is_text=false면 빈 문자열",\n'
        '  "tags": ["짧은 개념어 태그 1~3개. 공백 없는 한 단어(내용 요약이 아니라 속성 라벨, 예: 감상, 인터뷰, 자기계발)"],\n'
        '  "hub": "이 글이 속할 더 큰 카테고리 한 단어(없으면 빈 문자열, 예: 글쓰기, 자기계발)"\n'
        "}\n"
        "글이 길어도 잘리지 말고 최대한 옮겨. 오타/이모지도 원문대로.\n"
        f"사용자 캡션: {caption or '(없음)'}"
    )
    content = [
        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                     "data": _b64.b64encode(image_bytes).decode("ascii")}},
        {"type": "text", "text": ask},
    ]
    for m_name in _model_chain(True):
        try:
            resp = _get_client().messages.create(
                model=m_name, max_tokens=3000,
                messages=[{"role": "user", "content": content}],
            )
            mm = re.search(r"\{.*\}", resp.content[0].text.strip(), re.DOTALL)
            if mm:
                data = json.loads(mm.group())
                return {
                    "is_text": bool(data.get("is_text")),
                    "title": (data.get("title") or "").strip(),
                    "text": (data.get("text") or "").strip(),
                    "tags": data.get("tags") or [],
                    "hub": (data.get("hub") or "").strip(),
                }
        except Exception as e:
            logger.warning(f"read_photo_text 실패 (model={m_name}): {e}")
    return {"is_text": False, "title": "", "text": "", "tags": [], "hub": ""}


def build_text_note(parsed: dict, user_text: str = "", image_embed: str = "") -> tuple[str, str]:
    """텍스트 캡처(스크린샷 OCR) 전용 노트 — 읽은 글을 본문 그대로."""
    now = _now_kst()
    stamp_file = now.strftime("%Y-%m-%d_%H%M")
    stamp_human = now.strftime("%Y-%m-%d %H:%M")
    title = (parsed.get("title") or "글 캡처").strip()
    path = _note_path("Inbox", title, now)
    tags = _clean_tags(parsed.get("tags")) or ["글", "캡처"]
    tags_yaml = "[" + ", ".join(tags) + "]"
    hub = (parsed.get("hub") or "").strip()

    lines = ["---", "type: text-capture", f"created: {stamp_human}", f"tags: {tags_yaml}"]
    if hub:
        lines.append(f'hub: "{hub}"')
    lines += ["---", "", f"# {title}", ""]
    if user_text and user_text.strip():
        lines += ["## ✍️ 내 생각", user_text.strip(), ""]
    lines += ["## 📄 내용", parsed.get("text", "").strip(), ""]
    if image_embed:
        lines += [f"![[{image_embed}]]", ""]
    if hub:
        lines += [f"관련: [[{hub}]]", ""]
    return path, "\n".join(lines)


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
        "이건 사용자가 '사고 싶어서' 저장한 거야. **사용자 캡션이 저장 의도**니 상품명은 캡션을 최우선으로 반영해. JSON만 반환:\n"
        "{\n"
        '  "item": "상품명 (한국어, 간결히). 사진 속 앱/사이트 UI 문구나 광고 문구는 상품명으로 쓰지 마.",\n'
        '  "price": "가격 (숫자+원, 모르면 빈 문자열)",\n'
        '  "where": "판매처/브랜드/사이트 (모르면 빈 문자열)",\n'
        '  "reason": "왜 사고 싶어 보이는지 한 줄 (한국어)",\n'
        '  "tags": ["짧은 카테고리 태그 2~3개. 공백 없이(예: 패션, 가전, 뷰티)"]\n'
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

    for m_name in _model_chain(bool(image_bytes)):
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
                # 캡션이 있는데 상품명이 비면 캡션으로 보정
                if text and text.strip() and not (data.get("item") or "").strip():
                    data["item"] = text.strip()
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
    path = _note_path("Shopping", item, now)

    tags = _clean_tags(parsed.get("tags"))
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
    path = _note_path("Inbox", title, now)

    tags = _clean_tags(parsed.get("tags"))
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
