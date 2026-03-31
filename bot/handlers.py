import os
from datetime import date, datetime, timezone, timedelta
from typing import Optional

from telegram import Update
from telegram.ext import ContextTypes

from .models import BotMode, ContentEntry, Reminder, STATUS_KR, CONTENT_TYPE_KR
from .sheets import SheetsClient
from .reminders_sheet import RemindersClient
from . import claude_client
from .mode_prompts import MODE_NAMES

# 모드별 대화 히스토리 (최근 10턴)
_history: dict[str, list[dict]] = {
    "secretary": [],
    "finance": [],
    "consultant": [],
}
MAX_HISTORY = 20  # message 수 (user+assistant 합산)

_current_mode: BotMode = BotMode.SECRETARY
_sheets: Optional[SheetsClient] = None
_reminders: Optional[RemindersClient] = None


def init_sheets(sheets: SheetsClient):
    global _sheets
    _sheets = sheets


def init_reminders(reminders: RemindersClient):
    global _reminders
    _reminders = reminders


def _auth(update: Update) -> bool:
    allowed = os.getenv("TELEGRAM_USER_ID", "")
    if not allowed:
        return True  # 미설정 시 허용 (개발/초기 설정 중)
    return str(update.effective_user.id) == allowed


def _add_history(mode: str, role: str, content: str):
    _history[mode].append({"role": role, "content": content})
    if len(_history[mode]) > MAX_HISTORY:
        _history[mode] = _history[mode][-MAX_HISTORY:]


async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    allowed = os.getenv("TELEGRAM_USER_ID", "")

    # TELEGRAM_USER_ID 미설정 시 → 본인 ID 안내 (초기 설정 도우미)
    if not allowed:
        await update.message.reply_text(
            f"👋 안녕하세요!\n\n"
            f"⚙️ **초기 설정이 필요해요.**\n\n"
            f"당신의 Telegram User ID는:\n"
            f"`{user_id}`\n\n"
            f"이 숫자를 `.env` 파일의 `TELEGRAM_USER_ID=` 뒤에 붙여넣으세요:\n"
            f"`TELEGRAM_USER_ID={user_id}`\n\n"
            f"설정 후 봇을 재시작하면 돼요!",
            parse_mode="Markdown"
        )
        return

    if not _auth(update):
        await update.message.reply_text("인증되지 않은 사용자예요.")
        return

    text = (
        "안녕하세요! 저는 당신의 개인 AI 비서예요.\n\n"
        "**현재 모드:** " + MODE_NAMES[_current_mode.value] + "\n\n"
        "**모드 전환:**\n"
        "/secretary · /finance · /consultant\n\n"
        "**비서 모드 명령어:**\n"
        "/list — 최근 아카이브 목록\n"
        "/stats — 통계\n"
        "/done [제목] — 완료 처리\n"
        "/drop [제목] — 중단 처리\n"
        "/get [제목] — 상세 조회\n"
        "/export — 시트 링크\n"
        "/help — 전체 도움말\n\n"
        "또는 그냥 메시지 보내세요. 다 알아들어요!"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def help_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _auth(update):
        return
    text = (
        "**📌 사용 방법**\n\n"
        "**모드 전환**\n"
        "/secretary — 콘텐츠 아카이브 + 일상 비서\n"
        "/finance — 재무 분석, 투자 상담\n"
        "/consultant — 전략, 의사결정 지원\n"
        "/mode — 현재 모드 확인\n\n"
        "**비서 모드 — 자연어 예시**\n"
        "• `소로 레벨링 87화 읽었어`\n"
        "• `무빙 다 봤어, 9점`\n"
        "• `파친코 추가해줘`\n"
        "• `판타지 웹툰 추천해줘`\n"
        "• `이번달에 뭐 봤더라?`\n\n"
        "**비서 모드 — 명령어**\n"
        "/list [필터] — 목록 (예: /list 드라마, /list 완료)\n"
        "/stats — 통계 요약\n"
        "/get [제목] — 상세 조회\n"
        "/done [제목] — 완료 처리\n"
        "/drop [제목] — 중단 처리\n"
        "/export — Google Sheets 링크\n\n"
        "**금융/컨설턴트 모드**\n"
        "모드 전환 후 자유롭게 질문하세요."
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def mode_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _auth(update):
        return
    await update.message.reply_text(
        f"현재 모드: {MODE_NAMES[_current_mode.value]}",
        parse_mode="Markdown"
    )


async def switch_mode_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global _current_mode
    if not _auth(update):
        return
    cmd = update.message.text.split()[0].lstrip("/").lower()
    if cmd == "secretary":
        _current_mode = BotMode.SECRETARY
    elif cmd == "finance":
        _current_mode = BotMode.FINANCE
    elif cmd == "consultant":
        _current_mode = BotMode.CONSULTANT

    name = MODE_NAMES[_current_mode.value]
    await update.message.reply_text(f"{name} 모드로 전환했어요.", parse_mode="Markdown")


async def list_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _auth(update):
        return
    args = context.args or []
    filter_arg = " ".join(args).strip().lower() if args else ""

    # 필터 파싱
    type_map = {v.lower(): k for k, v in CONTENT_TYPE_KR.items()}
    type_map.update({k: k for k in CONTENT_TYPE_KR})
    status_map = {v: k for k, v in STATUS_KR.items()}
    status_map.update({k: k for k in STATUS_KR})

    filter_type = type_map.get(filter_arg)
    filter_status = status_map.get(filter_arg)

    entries = _sheets.get_recent(n=10, filter_type=filter_type, filter_status=filter_status)
    text = _sheets.format_list(entries)
    await update.message.reply_text(text, parse_mode="Markdown")


async def stats_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _auth(update):
        return
    text = _sheets.format_stats()
    await update.message.reply_text(text, parse_mode="Markdown")


async def get_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _auth(update):
        return
    if not context.args:
        await update.message.reply_text("조회할 제목을 입력해주세요. 예: `/get 소로 레벨링`", parse_mode="Markdown")
        return
    title = " ".join(context.args)
    entry = _sheets.get_entry_by_title(title)
    if not entry:
        await update.message.reply_text(f"'{title}'을(를) 찾지 못했어요.")
        return
    await update.message.reply_text(_format_entry_detail(entry), parse_mode="Markdown")


async def done_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _auth(update):
        return
    if not context.args:
        await update.message.reply_text("완료 처리할 제목을 입력해주세요. 예: `/done 무빙`", parse_mode="Markdown")
        return
    title = " ".join(context.args)
    entry = _sheets.get_entry_by_title(title)
    if not entry:
        await update.message.reply_text(f"'{title}'을(를) 찾지 못했어요.")
        return
    entry.status = "completed"
    entry.date_completed = str(date.today())
    _sheets.update_entry(entry)
    await update.message.reply_text(f"**{entry.title}** 완료 처리했어요! 평점도 남겨두실 건가요?", parse_mode="Markdown")


async def drop_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _auth(update):
        return
    if not context.args:
        await update.message.reply_text("중단 처리할 제목을 입력해주세요. 예: `/drop 제목`", parse_mode="Markdown")
        return
    title = " ".join(context.args)
    entry = _sheets.get_entry_by_title(title)
    if not entry:
        await update.message.reply_text(f"'{title}'을(를) 찾지 못했어요.")
        return
    entry.status = "dropped"
    _sheets.update_entry(entry)
    await update.message.reply_text(f"**{entry.title}** 중단 처리했어요.", parse_mode="Markdown")


async def import_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _auth(update):
        return
    import sys, io
    from pathlib import Path

    csv_path = Path(__file__).resolve().parent.parent / "data" / "archive.csv"
    if not csv_path.exists():
        await update.message.reply_text("data/archive.csv 파일이 없어요.")
        return

    await update.message.reply_text("⏳ 아카이브 import 시작... 잠깐만요!")

    # stdout 캡처
    old_stdout = sys.stdout
    sys.stdout = buf = io.StringIO()
    try:
        sys.path.insert(0, str(csv_path.parent.parent))
        from scripts.import_archive import run_import
        run_import(str(csv_path), dry_run=False)
    except Exception as e:
        sys.stdout = old_stdout
        await update.message.reply_text(f"❌ 오류: {e}")
        return
    finally:
        sys.stdout = old_stdout

    output = buf.getvalue()
    lines = [l for l in output.strip().split("\n") if l.strip()]
    summary = "\n".join(lines[-5:]) if len(lines) > 5 else output
    _sheets._invalidate_cache()
    await update.message.reply_text(f"✅ Import 완료!\n```\n{summary}\n```", parse_mode="Markdown")


async def remind_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/remind 내일 오전 9시 / 약 먹기"""
    if not _auth(update):
        return
    text = update.message.text[len("/remind"):].strip()
    if not text:
        await update.message.reply_text(
            "사용법: `/remind 내일 오전 9시 / 약 먹기`\n\n"
            "슬래시(/)로 시간과 내용을 구분해요.\n"
            "반복 예시:\n"
            "• `/remind 매일 저녁 10시 / 스트레칭`\n"
            "• `/remind 매주 월요일 아침 8시 / 주간 계획`\n"
            "• `/remind 매달 1일 오전 9시 / 월세 확인`",
            parse_mode="Markdown"
        )
        return

    await update.message.chat.send_action("typing")

    kst = timezone(timedelta(hours=9))
    now_str = datetime.now(kst).strftime("%Y년 %m월 %d일 %H시 %M분")
    try:
        parsed = claude_client.parse_reminder_time(text, now_str)
        trigger_at = parsed["trigger_at"]
        repeat = parsed.get("repeat", "none")
        # "/" 뒤 텍스트가 있으면 그것을 우선 사용
        if "/" in text:
            reminder_text = text.split("/", 1)[1].strip()
        else:
            reminder_text = parsed.get("reminder_text", text)
    except Exception:
        await update.message.reply_text(
            "시간을 이해하지 못했어요.\n예: `/remind 내일 오전 9시 / 약 먹기`",
            parse_mode="Markdown"
        )
        return

    reminder = Reminder(text=reminder_text, trigger_at=trigger_at, repeat=repeat)
    _reminders.add_reminder(reminder)

    dt = datetime.fromisoformat(trigger_at)
    repeat_label = {"none": "", "daily": " (매일 반복)", "weekly": " (매주 반복)", "monthly": " (매달 반복)"}.get(repeat, "")
    await update.message.reply_text(
        f"🔔 리마인더 등록!\n\n"
        f"**내용:** {reminder_text}\n"
        f"**시간:** {dt.strftime('%Y-%m-%d %H:%M')}{repeat_label}",
        parse_mode="Markdown"
    )


async def reminders_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/reminders — 등록된 리마인더 목록"""
    if not _auth(update):
        return
    active = _reminders.get_all_active()
    if not active:
        await update.message.reply_text("등록된 리마인더가 없어요.")
        return

    repeat_label = {"none": "", "daily": " 매일", "weekly": " 매주", "monthly": " 매달"}
    lines = ["**🔔 리마인더 목록**\n"]
    for i, r in enumerate(active, 1):
        try:
            dt = datetime.fromisoformat(r.trigger_at)
            time_str = dt.strftime("%m/%d %H:%M")
        except Exception:
            time_str = r.trigger_at
        rep = repeat_label.get(r.repeat, "")
        lines.append(f"{i}. {r.text}\n   `{time_str}`{rep}  ID: `{r.id}`")
    lines.append("\n취소: `/cancel_reminder [ID]`")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cancel_reminder_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/cancel_reminder [id]"""
    if not _auth(update):
        return
    if not context.args:
        await update.message.reply_text(
            "`/cancel_reminder [ID]`\n`/reminders`에서 ID 확인하세요.",
            parse_mode="Markdown"
        )
        return
    rid = context.args[0]
    _reminders.deactivate(rid)
    await update.message.reply_text(f"리마인더 `{rid}` 취소했어요.", parse_mode="Markdown")


async def export_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _auth(update):
        return
    sheet_id = os.getenv("SPREADSHEET_ID", "")
    if sheet_id:
        url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/edit"
        await update.message.reply_text(f"[Google Sheets 열기]({url})", parse_mode="Markdown")
    else:
        await update.message.reply_text("SPREADSHEET_ID 환경변수가 설정되지 않았어요.")


async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _auth(update):
        return
    text = update.message.text.strip()
    mode = _current_mode.value

    # 비서 모드: 아카이브 관련 파싱 먼저 시도
    if mode == "secretary":
        await _handle_secretary(update, text)
    else:
        # 금융/컨설턴트 모드: Claude 대화
        await update.message.chat.send_action("typing")
        _add_history(mode, "user", text)
        reply = claude_client.chat(mode, text, history=_history[mode][:-1])
        _add_history(mode, "assistant", reply)
        await update.message.reply_text(reply)


async def _handle_secretary(update: Update, text: str):
    known_titles = _sheets.get_titles()
    intent = claude_client.parse_archive_message(text, known_titles)

    if intent.action == "unknown" or intent.confidence < 0.5:
        # 일반 비서 대화로 처리
        await update.message.chat.send_action("typing")
        _add_history("secretary", "user", text)
        reply = claude_client.chat("secretary", text, history=_history["secretary"][:-1])
        _add_history("secretary", "assistant", reply)
        await update.message.reply_text(reply)
        return

    if intent.action in ("record_progress", "add_new", "mark_status", "rate", "note"):
        await _handle_archive_action(update, intent)
    elif intent.action == "recommend":
        await update.message.chat.send_action("typing")
        entries = _sheets.get_all_entries()
        reply = claude_client.get_recommendations(text, entries)
        await update.message.reply_text(reply)
    elif intent.action == "query":
        await update.message.chat.send_action("typing")
        entries = _sheets.get_all_entries()
        reply = claude_client.answer_query(text, entries)
        await update.message.reply_text(reply)
    else:
        # 낮은 confidence → 되묻기
        if intent.title and intent.confidence < 0.7:
            await update.message.reply_text(
                f"혹시 **{intent.title}** 말씀하시는 건가요?",
                parse_mode="Markdown"
            )
        else:
            await update.message.reply_text("잘 이해하지 못했어요. 다시 말씀해주시겠어요?")


async def _handle_archive_action(update, intent):
    today = str(date.today())
    entry = None

    if intent.title:
        entry = _sheets.get_entry_by_title(intent.title)

    if intent.action == "add_new":
        if entry:
            await update.message.reply_text(
                f"**{entry.title}**은(는) 이미 아카이브에 있어요. ({STATUS_KR.get(entry.status, '')})",
                parse_mode="Markdown"
            )
            return
        new_entry = ContentEntry(
            title=intent.title or "제목 없음",
            type=intent.content_type or "other",
            status=intent.status or "not_started",
            progress=intent.progress or "",
            notes=intent.note or "",
            date_added=today,
        )
        added = _sheets.add_entry(new_entry)
        type_kr = CONTENT_TYPE_KR.get(added.type, added.type)
        status_kr = STATUS_KR.get(added.status, added.status)
        await update.message.reply_text(
            f"**{added.title}** ({type_kr}) 추가했어요! 상태: {status_kr}",
            parse_mode="Markdown"
        )
        return

    if not entry:
        # 새로 등록하면서 진행상황 업데이트
        new_entry = ContentEntry(
            title=intent.title or "제목 없음",
            type=intent.content_type or "other",
            status=intent.status or "in_progress",
            progress=intent.progress or "",
            notes=intent.note or "",
            date_added=today,
        )
        if intent.rating is not None:
            new_entry.rating = intent.rating
        added = _sheets.add_entry(new_entry)
        await update.message.reply_text(
            f"**{added.title}** 새로 등록했어요! {added.progress or ''}",
            parse_mode="Markdown"
        )
        return

    # 기존 항목 업데이트
    if intent.progress:
        old_progress = entry.progress
        entry.progress = intent.progress
        log_entry = f"[{today}: {intent.progress}]"
        entry.raw_log = f"{entry.raw_log} {log_entry}".strip()

    if intent.status:
        entry.status = intent.status
        if intent.status == "completed" and not entry.date_completed:
            entry.date_completed = today

    if intent.rating is not None:
        entry.rating = intent.rating

    if intent.note:
        entry.notes = f"{entry.notes}\n{intent.note}".strip() if entry.notes else intent.note

    _sheets.update_entry(entry)
    await update.message.reply_text(_build_update_reply(entry, intent), parse_mode="Markdown")


def _build_update_reply(entry: ContentEntry, intent) -> str:
    parts = []
    if intent.status == "completed":
        parts.append(f"**{entry.title}** 완료 처리했어요!")
        if not intent.rating:
            parts.append("평점도 남겨두실 건가요?")
    elif intent.progress:
        parts.append(f"**{entry.title}** → {entry.progress} 업데이트했어요.")
    elif intent.rating is not None:
        parts.append(f"**{entry.title}** 평점 ⭐{entry.rating} 저장했어요.")
    elif intent.note:
        parts.append(f"**{entry.title}** 메모 추가했어요.")
    else:
        parts.append(f"**{entry.title}** 업데이트했어요.")
    return " ".join(parts)


def _format_entry_detail(entry: ContentEntry) -> str:
    type_kr = CONTENT_TYPE_KR.get(entry.type, entry.type)
    status_kr = STATUS_KR.get(entry.status, entry.status)
    lines = [
        f"**{entry.title}**",
        f"종류: {type_kr} · 상태: {status_kr}",
    ]
    if entry.progress:
        lines.append(f"진행: {entry.progress}")
    if entry.rating is not None:
        lines.append(f"평점: ⭐{entry.rating}")
    if entry.tags:
        lines.append(f"태그: {entry.tags}")
    if entry.notes:
        lines.append(f"메모: {entry.notes}")
    if entry.date_added:
        lines.append(f"등록일: {entry.date_added}")
    if entry.source:
        lines.append(f"출처: {entry.source}")
    return "\n".join(lines)
