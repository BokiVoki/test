"""
인박스 봇 — 아이디어/링크/이미지를 무조건 옵시디언 인박스로 정리.

동작:
- 명령어 없이 보낸 것(텍스트/링크/사진) → 자동으로 옵시디언 노트 생성
- 명령어(/find, /today 등) → 별도 처리 (캡처 안 함)

환경변수:
- INBOX_BOT_TOKEN      : @BotFather 로 새로 발급한 두 번째 봇 토큰
- TELEGRAM_USER_ID     : 본인만 사용하도록 제한 (일정관리봇과 공유 가능)
- ANTHROPIC_API_KEY    : 요약용 (일정관리봇과 공유)
- GITHUB_TOKEN, VAULT_REPO, VAULT_BRANCH : 옵시디언 볼트(GitHub) 저장용
- GOOGLE_CREDENTIALS_JSON, GOOGLE_DRIVE_FOLDER_ID : 사진 업로드용 (선택)
"""
import logging
import os
import re
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

from . import capture, vault

# chat_id → {"path","content","title"} : 방금 저장한 노트 (한 줄 덧붙이기용)
_last_note: dict[int, dict] = {}
# chat_id → path : 지금 '내 생각' 입력 대기 중
_pending_annotate: dict[int, str] = {}
# chat_id → {"bytes","embed","caption","inbox_path"} : 방금 받은 사진 (쇼핑 전환용)
_last_photo: dict[int, dict] = {}

# 앨범(여러 장) 버퍼: media_group_id → {chat_id, file_ids[], caption}
_album_buf: dict[str, dict] = {}

_FCODE = {"Inbox": "I", "Books": "B", "Shopping": "S"}
_FOLDER = {"I": "Inbox", "B": "Books", "S": "Shopping"}


def _stampcb(code: str, note_path: str) -> str:
    """재시작에도 안전한 콜백: {code}:{폴더코드}:{파일 타임스탬프}.
    새 형식(제목 뒤 'YYMMDD-HHMM') / 옛 형식(앞의 'YYYY-MM-DD_HHMM') 둘 다 지원."""
    folder = note_path.split("/")[0]
    fname = note_path.split("/")[-1]
    m = re.search(r"(\d{6}-\d{4})(?:\.md)?$", fname)          # 새 형식: 제목 260708-1359.md
    if not m:
        m = re.match(r"(\d{4}-\d{2}-\d{2}_\d{4})", fname)     # 옛 형식: 2026-07-08_1359_...
    stamp = m.group(1) if m else fname[:15]
    return f"{code}:{_FCODE.get(folder, 'I')}:{stamp}"


def _find_note(folder_code: str, stamp: str) -> str | None:
    """폴더코드+스탬프로 노트 경로를 찾는다. (스탬프가 파일명 어디에 있든 매칭)"""
    folder = _FOLDER.get(folder_code, "Inbox")
    for n in vault.list_folder(folder):
        if stamp in n:
            return f"{folder}/{n}"
    return None


def _del_button(note_path: str) -> InlineKeyboardButton:
    return InlineKeyboardButton("🗑 삭제", callback_data=_stampcb("d", note_path))

_PLACEHOLDER = "_(무엇이 나를 건드렸나? 나중에 한 줄)_"

load_dotenv()

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=os.getenv("LOG_LEVEL", "INFO"),
)
logger = logging.getLogger(__name__)


def _now_kst() -> datetime:
    return datetime.now(timezone(timedelta(hours=9))).replace(tzinfo=None)


def _authorized(update: Update) -> bool:
    allowed = os.getenv("TELEGRAM_USER_ID", "")
    if not allowed:
        return True
    return str(update.effective_user.id) == str(allowed)


# ── 명령어 ──────────────────────────────────────────────────
async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return
    await update.message.reply_text(
        "🧠 **인박스 봇**\n\n"
        "그냥 아무거나 던져줘. 자동으로 옵시디언 인박스에 정리할게.\n"
        "• 링크 → 요약 + '왜 저장했나' 한 줄\n"
        "• 생각/아이디어 → 노트로\n"
        "• 사진 → 저장 + 정리\n"
        "• 사고싶은 거 ('살까'/'얼마' 등) → 🛒 위시리스트로\n"
        "• 읽고싶은 책 ('읽어볼까'/'책추천' 등) → 📚 독서 목록으로\n\n"
        "**명령어**\n"
        "• `/today` — 오늘 모은 것 보기\n"
        "• `/find 키워드` — 인박스에서 찾기\n"
        "• `/shopping` — 사고 싶은 것 목록\n"
        "• `/books` — 읽고 싶은 책 목록",
        parse_mode="Markdown",
    )


async def today_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return
    today = _now_kst().strftime("%Y-%m-%d")
    names = vault.list_inbox(prefix=today)
    if not names:
        await update.message.reply_text("오늘 모은 게 아직 없어요 🌱")
        return
    lines = [f"📥 **오늘 모은 것** ({len(names)}개)\n"]
    for n in names:
        # 2026-07-07_1430_제목.md → 14:30 제목
        title = n.replace(".md", "")
        parts = title.split("_", 2)
        disp = parts[2] if len(parts) > 2 else title
        tm = parts[1] if len(parts) > 1 else ""
        tm_fmt = f"{tm[:2]}:{tm[2:]}" if len(tm) == 4 else ""
        lines.append(f"• {tm_fmt} {disp.replace('_', ' ')}")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def find_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return
    kw = " ".join(context.args).strip() if context.args else ""
    if not kw:
        await update.message.reply_text("찾을 키워드를 알려줘. 예: `/find 루틴`", parse_mode="Markdown")
        return
    names = vault.list_inbox(prefix=kw)
    if not names:
        await update.message.reply_text(f"'{kw}' 로 찾은 게 없어요.")
        return
    lines = [f"🔍 **'{kw}' 검색** ({len(names)}개)\n"]
    for n in names[:20]:
        title = n.replace(".md", "")
        parts = title.split("_", 2)
        disp = parts[2] if len(parts) > 2 else title
        date = parts[0] if parts else ""
        lines.append(f"• {date} {disp.replace('_', ' ')}")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ── 기본: 캡처 ──────────────────────────────────────────────
async def _delete_recent(update, n: int):
    """인박스 최근 노트 N개 삭제 (사진 첨부도 함께)."""
    if not vault.is_configured():
        await update.message.reply_text("⚠️ 볼트 연결이 안 됐어요.")
        return
    names = vault.list_inbox()[:n]  # 최신순
    if not names:
        await update.message.reply_text("인박스에 지울 게 없어요.")
        return
    deleted = 0
    for name in names:
        path = f"Inbox/{name}"
        content = vault.read_note(path) or ""
        m = re.search(r"!\[\[([^\]]+)\]\]", content)
        if m:
            vault.delete_note(f"Inbox/attachments/{m.group(1)}", commit_msg=f"remove attachment {m.group(1)}")
        if vault.delete_note(path, commit_msg=f"remove {name}"):
            deleted += 1
    await update.message.reply_text(f"🗑 인박스 최근 {deleted}개 삭제했어요 (사진 첨부도 함께).")


async def delete_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return
    query = update.callback_query
    await query.answer()
    parts = query.data.split(":")
    if len(parts) < 3:
        return
    folder = _FOLDER.get(parts[1], "Inbox")
    stamp = parts[2]
    names = [n for n in vault.list_folder(folder) if stamp in n]
    if not names:
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text("앗, 이미 지웠거나 못 찾았어요.")
        return
    deleted = 0
    for name in names:
        path = f"{folder}/{name}"
        content = vault.read_note(path) or ""
        for img in re.findall(r"!\[\[([^\]]+)\]\]", content):
            vault.delete_note(f"Inbox/attachments/{img}", commit_msg=f"remove {img}")
        if vault.delete_note(path, commit_msg=f"remove {name}"):
            deleted += 1
    await query.edit_message_reply_markup(reply_markup=None)
    await query.message.reply_text("🗑 삭제했어요 (사진 첨부도 함께)." if deleted else "삭제 실패 (이미 없을 수도).")


async def ocr_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """📝 글 인식: 노트의 이미지를 비전으로 읽어 글을 노트에 넣고, 읽은 글을 확인용으로 보냄."""
    if not _authorized(update):
        return
    query = update.callback_query
    await query.answer("글 읽는 중…")
    parts = query.data.split(":")
    if len(parts) < 3:
        return
    path = _find_note(parts[1], parts[2])
    if not path:
        await query.message.reply_text("앗, 그 노트를 못 찾았어요 (지웠나요?).")
        return
    content = vault.read_note(path) or ""
    m = re.search(r"!\[\[([^\]]+)\]\]", content)
    if not m:
        await query.message.reply_text("이 노트엔 이미지가 없어요.")
        return
    await context.bot.send_chat_action(chat_id=query.message.chat_id, action="typing")
    img_bytes = vault.read_binary(f"Inbox/attachments/{m.group(1)}")
    if not img_bytes:
        await query.message.reply_text("이미지를 못 불러왔어요.")
        return
    ocr = capture.read_photo_text(img_bytes, "")
    text = (ocr.get("text") or "").strip()
    if not text:
        await query.message.reply_text("글을 못 읽었어요 (글이 적거나 흐릿할 수 있어요).")
        return
    section = "\n## 📄 인식한 글\n" + text + "\n"
    if "## 📄 인식한 글" in content:
        content = re.sub(r"\n## 📄 인식한 글\n.*?(?=\n## |\Z)", section, content, flags=re.DOTALL)
    else:
        content = content.rstrip() + "\n" + section
    # 옵시디언용 정리: 읽은 글에서 뽑은 태그/허브를 노트 frontmatter에 합치고 그래프 링크(관련: [[허브]])까지
    extra = ""
    try:
        tags = ocr.get("tags") or []
        hub = (ocr.get("hub") or "").strip()
        if tags or hub:
            content = capture.merge_tags_into_note(content, tags, hub)
            if tags:
                extra = "\n🏷 " + " ".join("#" + t for t in tags)
            if hub:
                extra += f"\n🗂 {hub}"
    except Exception as e:
        logger.warning(f"ocr 태깅 실패: {e}")
    try:
        vault.write_note(path, content, commit_msg=f"ocr: {path.split('/')[-1]}")
    except Exception as e:
        await query.message.reply_text(f"❌ 메모 저장 실패: {e}")
        return
    preview = text if len(text) <= 3500 else text[:3500] + "\n…(너무 길어 여기선 잘림 — 노트엔 전체 저장됨)"
    await query.message.reply_text(
        "📝 글 읽어서 메모에 넣고 옵시디언용으로 정리했어요. 확인해봐요:" + extra + "\n\n" + preview
    )


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return
    text = (update.message.text or "").strip()
    if not text:
        return
    chat_id = update.effective_chat.id

    # ── 삭제 명령: '삭제' / '삭제 3' / '최근삭제 3' → 인박스 최근 N개 삭제 ──
    dm = re.match(r"^(?:삭제|최근삭제|인박스\s*삭제)\s*(\d+)?$", text)
    if dm:
        n = int(dm.group(1)) if dm.group(1) else 1
        await _delete_recent(update, max(1, min(n, 30)))
        return

    # ── '내 생각' 덧붙이기 대기 중이면 → 직전 노트에 추가 + 내 말에서 태그 재추출 ──
    if chat_id in _pending_annotate:
        path = _pending_annotate.pop(chat_id)
        note = _last_note.get(chat_id)
        if note and note.get("path") == path:
            new_content = note["content"].replace(_PLACEHOLDER, text.strip())
            if _PLACEHOLDER not in note["content"]:
                # 이미 내 생각이 있으면 아래에 덧붙임
                new_content = note["content"].rstrip() + f"\n\n{text.strip()}\n"
            await context.bot.send_chat_action(chat_id=chat_id, action="typing")
            # 내가 쓴 말에서 태그/허브 뽑아 노트에 반영 (진짜 내 관점으로 분류)
            extra = ""
            try:
                dv = capture.derive_tags(text)
                if dv.get("tags") or dv.get("hub"):
                    new_content = capture.merge_tags_into_note(new_content, dv.get("tags", []), dv.get("hub", ""))
                    if dv.get("tags"):
                        extra = "\n🏷 " + " ".join("#" + t for t in dv["tags"])
                    if dv.get("hub"):
                        extra += f"\n🗂 {dv['hub']}"
            except Exception as e:
                logger.warning(f"annotate 태깅 실패: {e}")
            try:
                vault.write_note(path, new_content, commit_msg=f"annotate: {note.get('title','')}")
                _last_note[chat_id]["content"] = new_content
                await update.message.reply_text(
                    "✍️ 네 생각 넣고, 그 말에서 태그도 뽑았어요. 이게 진짜 알맹이예요 🙂" + extra
                )
            except Exception as e:
                await update.message.reply_text(f"❌ 덧붙이기 실패: {e}")
            return

    if not vault.is_configured():
        await update.message.reply_text(
            "⚠️ 아직 볼트(GitHub) 연결이 안 됐어요. GITHUB_TOKEN, VAULT_REPO 환경변수를 설정해줘."
        )
        return

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    url = capture.find_url(text)
    try:
        # 읽고싶은 책 → 독서 목록으로 (책 의도를 쇼핑보다 먼저 판정)
        if capture.is_book(text):
            raw = capture.fetch_link(url) if url else {}
            if url:
                raw["url"] = url
            bk = capture.parse_book(text, raw)
            path, content = capture.build_book_note(bk, url=url or "", user_text=text)
            title = bk.get("title", "읽고 싶은 책")
            vault.write_note(path, content, commit_msg=f"book: {title}")
            _last_note[chat_id] = {"path": path, "content": content, "title": title}
            author = bk.get("author", "")
            reply = f"📚 **{title}** 읽고싶은 책에 넣었어요"
            if author:
                reply += f"\n✍️ {author}"
            await update.message.reply_text(reply, parse_mode="Markdown")
            return

        # 사고싶은 것 → 쇼핑 위시리스트로
        if capture.is_shopping(text):
            raw = capture.fetch_link(url) if url else {}
            if url:
                raw["url"] = url
            sp = capture.parse_shopping(text, raw)
            path, content = capture.build_shopping_note(sp, url=url or "", user_text=text)
            title = sp.get("item", "사고 싶은 것")
            vault.write_note(path, content, commit_msg=f"shopping: {title}")
            _last_note[chat_id] = {"path": path, "content": content, "title": title}
            price = sp.get("price", "")
            reply = f"🛒 **{title}** 위시리스트에 넣었어요"
            if price:
                reply += f"\n💰 {price}"
            await update.message.reply_text(reply, parse_mode="Markdown")
            return

        if url:
            raw = capture.fetch_link(url)
            parsed = capture.summarize("link", url, raw, text)
            path, content = capture.build_note("link", parsed, url=url, user_text=text)
        else:
            parsed = capture.summarize("idea", "", {}, text)
            path, content = capture.build_note("idea", parsed, user_text=text)

        vault.write_note(path, content, commit_msg=f"inbox: {parsed.get('title','메모')}")
    except Exception as e:
        logger.exception("캡처 실패")
        await update.message.reply_text(f"❌ 저장 실패: {e}")
        return

    _last_note[chat_id] = {"path": path, "content": content, "title": parsed.get("title", "메모")}

    title = parsed.get("title", "메모")
    hub = parsed.get("hub", "")
    tags = parsed.get("tags", [])
    reply = f"✅ **{title}** 저장했어요"
    if hub:
        reply += f"\n🗂 주제: {hub}"
    if tags:
        reply += f"\n🏷 {' '.join('#'+t for t in tags)}"
    reply += "\n\n_요약은 참고용이에요. 뭐가 널 건드렸는지 한 줄 남기면 그게 진짜 기록이 돼요._"
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("✍️ 내 생각 한 줄 남기기", callback_data="annotate")],
        [_del_button(path)],
    ])
    await update.message.reply_text(reply, parse_mode="Markdown", reply_markup=markup)


async def shopping_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return
    names = vault.list_folder("Shopping")
    if not names:
        await update.message.reply_text("아직 위시리스트가 비어있어요 🛒")
        return
    lines = [f"🛒 **사고 싶은 것** ({len(names)}개)\n"]
    for n in names[:25]:
        title = n.replace(".md", "")
        parts = title.split("_", 2)
        item = parts[2].replace("_", " ") if len(parts) > 2 else title
        date = parts[0] if parts else ""
        lines.append(f"• {item}  _{date}_")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def books_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return
    names = vault.list_folder("Books")
    if not names:
        await update.message.reply_text("아직 읽고싶은 책 목록이 비어있어요 📚")
        return
    lines = [f"📚 **읽고 싶은 책** ({len(names)}개)\n"]
    for n in names[:25]:
        title = n.replace(".md", "")
        parts = title.split("_", 2)
        item = parts[2].replace("_", " ") if len(parts) > 2 else title
        date = parts[0] if parts else ""
        lines.append(f"• {item}  _{date}_")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def annotate_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    note = _last_note.get(chat_id)
    if not note:
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text("앗, 이 노트를 잊어버렸어요. 새로 하나 던져줘요!")
        return
    _pending_annotate[chat_id] = note["path"]
    await query.edit_message_reply_markup(reply_markup=None)
    await query.message.reply_text("✍️ 뭐가 널 건드렸어? / 왜 남겼어? 한 줄이면 돼요.")


async def _flush_album(context: ContextTypes.DEFAULT_TYPE):
    """앨범(여러 장) 버퍼를 하나의 노트로 저장. 다운로드/업로드는 여기서 한 번에."""
    gid = context.job.data
    entry = _album_buf.pop(gid, None)
    if not entry or not entry.get("file_ids"):
        return
    chat_id = entry["chat_id"]
    caption = entry.get("caption", "")
    stamp = _now_kst().strftime("%Y%m%d_%H%M%S")
    embeds = []
    for i, fid in enumerate(entry["file_ids"]):
        try:
            tgf = await context.bot.get_file(fid)
            data = bytes(await tgf.download_as_bytearray())
            iname = f"inbox_{stamp}_{i + 1}.jpg"
            vault.write_binary(f"Inbox/attachments/{iname}", data, commit_msg=f"inbox image: {iname}")
            embeds.append(iname)
        except Exception as e:
            logger.warning(f"앨범 사진 업로드 실패({i}): {e}")
    if not embeds:
        await context.bot.send_message(chat_id=chat_id, text="❌ 앨범 사진 업로드에 실패했어요.")
        return
    try:
        if caption:
            light = capture.summarize("idea", "", {}, caption)
            parsed = {
                "title": light.get("title") or caption[:20],
                "summary": "", "why": "",
                "tags": light.get("tags") or ["사진"],
                "hub": light.get("hub", ""),
            }
        else:
            parsed = {"title": f"사진 {_now_kst().strftime('%m/%d %H:%M')} ({len(embeds)}장)",
                      "summary": "", "why": "", "tags": ["사진"], "hub": ""}
        path, content = capture.build_note("image", parsed, user_text=caption, image_embed=embeds[0])
        if len(embeds) > 1:
            extra = "\n".join(f"![[{e}]]" for e in embeds[1:])
            content = content.replace(f"![[{embeds[0]}]]", f"![[{embeds[0]}]]\n{extra}", 1)
        vault.write_note(path, content, commit_msg=f"inbox album: {parsed['title']} ({len(embeds)}장)")
        _last_note[chat_id] = {"path": path, "content": content, "title": parsed["title"]}
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("✍️ 내 생각", callback_data="annotate")],
            [_del_button(path)],
        ])
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"✅ 사진 {len(embeds)}장을 한 노트로 저장했어요\n\n_한 줄 남기면 그게 진짜 기록이 돼요._",
            parse_mode="Markdown", reply_markup=markup,
        )
    except Exception as e:
        logger.exception("앨범 저장 실패")
        await context.bot.send_message(chat_id=chat_id, text=f"❌ 앨범 저장 실패: {e}")


async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return
    if not vault.is_configured():
        await update.message.reply_text("⚠️ 볼트(GitHub) 연결이 아직 안 됐어요.")
        return

    caption = (update.message.caption or "").strip()
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    chat_id = update.effective_chat.id

    # ── 앨범(여러 장) → file_id만 잽싸게 버퍼, 실제 저장은 flush에서 한 번에 ──
    gid = getattr(update.message, "media_group_id", None)
    if gid and getattr(context, "job_queue", None):
        entry = _album_buf.setdefault(str(gid), {"chat_id": chat_id, "file_ids": [], "caption": ""})
        try:
            entry["file_ids"].append(update.message.photo[-1].file_id)
        except Exception:
            pass
        if caption and not entry["caption"]:
            entry["caption"] = caption
        name = f"album:{gid}"
        for j in context.job_queue.get_jobs_by_name(name):
            j.schedule_removal()
        context.job_queue.run_once(_flush_album, when=4.0, data=str(gid), name=name)
        return

    # 사진을 볼트에 직접 넣기 (옵시디언에서 확실히 보임)
    image_embed = ""
    buf = b""
    try:
        photo = update.message.photo[-1]
        tg_file = await photo.get_file()
        buf = bytes(await tg_file.download_as_bytearray())
        img_name = f"inbox_{_now_kst().strftime('%Y%m%d_%H%M%S')}.jpg"
        vault.write_binary(f"Inbox/attachments/{img_name}", buf,
                           commit_msg=f"inbox image: {img_name}")
        image_embed = img_name
    except Exception as e:
        logger.warning(f"사진 볼트 업로드 실패(계속 진행): {e}")

    # 캡션에 책 의도가 있으면 → 바로 읽고싶은 책으로 (표지/제목/저자 읽음)
    if caption and capture.is_book(caption):
        try:
            bk = capture.parse_book(caption, {}, image_bytes=buf or None)
            path, content = capture.build_book_note(bk, user_text=caption, image_embed=image_embed)
            title = bk.get("title", "읽고 싶은 책")
            vault.write_note(path, content, commit_msg=f"book: {title}")
            _last_note[chat_id] = {"path": path, "content": content, "title": title}
            author = bk.get("author", "")
            reply = f"📚 **{title}** 읽고싶은 책에 넣었어요"
            if author:
                reply += f"\n✍️ {author}"
            await update.message.reply_text(reply, parse_mode="Markdown")
        except Exception as e:
            logger.exception("책 사진 저장 실패")
            await update.message.reply_text(f"❌ 저장 실패: {e}")
        return

    # 캡션에 쇼핑 의도가 있으면 → 바로 위시리스트로 (사진 속 상품/가격도 읽음)
    if caption and capture.is_shopping(caption):
        try:
            sp = capture.parse_shopping(caption, {}, image_bytes=buf or None)
            path, content = capture.build_shopping_note(sp, user_text=caption, image_embed=image_embed)
            title = sp.get("item", "사고 싶은 것")
            vault.write_note(path, content, commit_msg=f"shopping: {title}")
            _last_note[chat_id] = {"path": path, "content": content, "title": title}
            price = sp.get("price", "")
            reply = f"🛒 **{title}** 위시리스트에 넣었어요"
            if price:
                reply += f"\n💰 {price}"
            await update.message.reply_text(reply, parse_mode="Markdown")
        except Exception as e:
            logger.exception("쇼핑 사진 저장 실패")
            await update.message.reply_text(f"❌ 저장 실패: {e}")
        return

    try:
        # 캡션이 있으면 제목/태그만 뽑고, 요약은 만들지 않음 (캡션 중복 방지)
        if caption:
            light = capture.summarize("idea", "", {}, caption)
            parsed = {
                "title": light.get("title") or caption[:20],
                "summary": "",   # 캡션이 곧 '내 생각' — 중복 요약 안 함
                "why": "",
                "tags": light.get("tags") or ["사진"],
                "hub": light.get("hub", ""),
            }
        else:
            parsed = {"title": f"사진 {_now_kst().strftime('%m/%d %H:%M')}",
                      "summary": "", "why": "", "tags": ["사진"], "hub": ""}
        path, content = capture.build_note(
            "image", parsed, user_text=caption, image_embed=image_embed,
        )
        vault.write_note(path, content, commit_msg=f"inbox: {parsed.get('title','사진')}")
    except Exception as e:
        logger.exception("사진 캡처 실패")
        await update.message.reply_text(f"❌ 저장 실패: {e}")
        return

    _last_note[chat_id] = {"path": path, "content": content, "title": parsed.get("title", "사진")}
    # 쇼핑 전환 버튼용으로 사진 정보 잠깐 보관
    _last_photo[chat_id] = {"bytes": buf, "embed": image_embed, "caption": caption, "inbox_path": path}

    reply = "✅ 사진 저장했어요"
    if not image_embed:
        reply += "\n(⚠️ 이미지 업로드는 실패 — 메모만 저장됨)"
    reply += "\n\n_한 줄 남기거나, 종류에 맞는 버튼을 눌러요._"
    reply += "\n\n_글(스크린샷)이면 📝 글 인식을 눌러요._"
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("📝 글 인식", callback_data=_stampcb("o", path)),
         InlineKeyboardButton("✍️ 내 생각", callback_data="annotate")],
        [InlineKeyboardButton("🛒 사고싶은 거", callback_data="toshop"),
         InlineKeyboardButton("📚 읽고싶은 책", callback_data="toread")],
        [_del_button(path)],
    ])
    await update.message.reply_text(reply, parse_mode="Markdown", reply_markup=markup)


async def toshop_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """일반 사진 노트를 쇼핑 위시리스트로 전환 (사진 속 상품/가격을 비전으로 읽음)."""
    if not _authorized(update):
        return
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    photo = _last_photo.get(chat_id)
    if not photo:
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text("앗, 그 사진을 잊어버렸어요. 다시 보내줘요!")
        return
    await query.edit_message_reply_markup(reply_markup=None)
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")
    try:
        sp = capture.parse_shopping(photo.get("caption", ""), {}, image_bytes=photo.get("bytes") or None)
        path, content = capture.build_shopping_note(
            sp, user_text=photo.get("caption", ""), image_embed=photo.get("embed", ""),
        )
        title = sp.get("item", "사고 싶은 것")
        vault.write_note(path, content, commit_msg=f"shopping: {title}")
        # 원래 인박스 노트는 제거 (위시리스트로 이동)
        if photo.get("inbox_path"):
            vault.delete_note(photo["inbox_path"], commit_msg=f"move to shopping: {title}")
        _last_note[chat_id] = {"path": path, "content": content, "title": title}
        _last_photo.pop(chat_id, None)
        price = sp.get("price", "")
        reply = f"🛒 **{title}** 위시리스트로 옮겼어요"
        if price:
            reply += f"\n💰 {price}"
        where = sp.get("where", "")
        if where:
            reply += f"\n🏬 {where}"
        await query.message.reply_text(reply, parse_mode="Markdown")
    except Exception as e:
        logger.exception("쇼핑 전환 실패")
        await query.message.reply_text(f"❌ 전환 실패: {e}")


async def toread_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """일반 사진 노트를 읽고싶은 책으로 전환 (표지/제목/저자를 비전으로 읽음)."""
    if not _authorized(update):
        return
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    photo = _last_photo.get(chat_id)
    if not photo:
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text("앗, 그 사진을 잊어버렸어요. 다시 보내줘요!")
        return
    await query.edit_message_reply_markup(reply_markup=None)
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")
    try:
        bk = capture.parse_book(photo.get("caption", ""), {}, image_bytes=photo.get("bytes") or None)
        path, content = capture.build_book_note(
            bk, user_text=photo.get("caption", ""), image_embed=photo.get("embed", ""),
        )
        title = bk.get("title", "읽고 싶은 책")
        vault.write_note(path, content, commit_msg=f"book: {title}")
        if photo.get("inbox_path"):
            vault.delete_note(photo["inbox_path"], commit_msg=f"move to books: {title}")
        _last_note[chat_id] = {"path": path, "content": content, "title": title}
        _last_photo.pop(chat_id, None)
        author = bk.get("author", "")
        reply = f"📚 **{title}** 읽고싶은 책으로 옮겼어요"
        if author:
            reply += f"\n✍️ {author}"
        await query.message.reply_text(reply, parse_mode="Markdown")
    except Exception as e:
        logger.exception("책 전환 실패")
        await query.message.reply_text(f"❌ 전환 실패: {e}")


def main():
    token = os.getenv("INBOX_BOT_TOKEN")
    if not token:
        raise ValueError("INBOX_BOT_TOKEN 환경변수가 설정되지 않았어요.")

    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CommandHandler("help", start_handler))
    app.add_handler(CommandHandler("today", today_handler))
    app.add_handler(CommandHandler("find", find_handler))
    app.add_handler(CommandHandler("shopping", shopping_handler))
    app.add_handler(CommandHandler("books", books_handler))

    app.add_handler(CallbackQueryHandler(delete_callback, pattern=r"^d:"))
    app.add_handler(CallbackQueryHandler(ocr_callback, pattern=r"^o:"))
    app.add_handler(CallbackQueryHandler(annotate_callback, pattern=r"^annotate$"))
    app.add_handler(CallbackQueryHandler(toshop_callback, pattern=r"^toshop$"))
    app.add_handler(CallbackQueryHandler(toread_callback, pattern=r"^toread$"))
    app.add_handler(MessageHandler(filters.PHOTO, photo_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    logger.info("인박스 봇 시작. 폴링 중...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
