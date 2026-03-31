import copy
import os
import uuid as uuid_module
from datetime import date, datetime, timezone, timedelta
from typing import Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from .models import BotMode, ContentEntry, Reminder, TodoItem, STATUS_KR, CONTENT_TYPE_KR
from .sheets import SheetsClient
from .reminders_sheet import RemindersClient
from .todos_sheet import TodosClient
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
_todos: Optional[TodosClient] = None
_undo_state: dict[str, dict] = {}  # key → undo payload (in-memory, TTL 없음)


def init_sheets(sheets: SheetsClient):
    global _sheets
    _sheets = sheets


def init_reminders(reminders: RemindersClient):
    global _reminders
    _reminders = reminders


def init_todos(todos: TodosClient):
    global _todos
    _todos = todos


def _undo_keyboard(label: str = "↩️ 되돌리기") -> tuple[str, InlineKeyboardMarkup]:
    key = uuid_module.uuid4().hex[:12]
    markup = InlineKeyboardMarkup([[InlineKeyboardButton(label, callback_data=f"undo:{key}")]])
    return key, markup


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

    if _reminders is None:
        await update.message.reply_text("❌ 리마인더 초기화 실패. Railway 로그를 확인해주세요.")
        return

    # context.args로 받으면 /remind@botname 형태도 처리됨
    raw = " ".join(context.args) if context.args else ""
    if not raw:
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

    await _handle_remind_natural(update, raw)


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


async def cancel_all_reminders_handler(update: Update, context):
    """/cancel_all_reminders — 모든 리마인더 취소"""
    if not _auth(update):
        return
    active = _reminders.get_all_active()
    if not active:
        await update.message.reply_text("취소할 리마인더가 없어요.")
        return
    for r in active:
        _reminders.deactivate(r.id)
    await update.message.reply_text(f"🔕 리마인더 {len(active)}개 전부 취소했어요.")


async def clear_reminders_sheet_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/clear_reminders — 시트 전체 초기화 (완전 삭제)"""
    if not _auth(update):
        return
    _reminders.clear_all()
    await update.message.reply_text("🗑 Reminders 시트 전체 초기화했어요.")


# ── 투두리스트 ────────────────────────────────────────────────────────────────

def _fmt_due(due_date: str) -> str:
    if not due_date:
        return ""
    try:
        kst = timezone(timedelta(hours=9))
        today = datetime.now(kst).date()
        d = date.fromisoformat(due_date)
        diff = (d - today).days
        if diff < 0:
            return f" ⚠️ {d.strftime('%m/%d')} 마감지남"
        elif diff == 0:
            return " 📌 오늘마감"
        elif diff == 1:
            return f" 🔜 내일마감"
        else:
            return f" ({d.strftime('%m/%d')})"
    except Exception:
        return f" ({due_date})"


async def todos_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/todos — 미완료 투두 목록"""
    if not _auth(update):
        return
    if _todos is None:
        await update.message.reply_text("❌ 투두 초기화 실패.")
        return
    try:
        pending = _todos.get_pending()
    except Exception as e:
        await update.message.reply_text(f"❌ 오류: {e}")
        return
    if not pending:
        await update.message.reply_text("✅ 할 일이 없어요!")
        return
    lines = ["**📋 할 일 목록**\n"]
    for i, t in enumerate(pending, 1):
        lines.append(f"{i}. {t.text}{_fmt_due(t.due_date)}  `{t.id}`")
    lines.append("\n완료: `완료 [내용]` 또는 `/todo_done [ID]`")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def todo_done_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/todo_done [id or text]"""
    if not _auth(update):
        return
    if not context.args:
        await update.message.reply_text("`/todo_done [ID]` 또는 `/todo_done [내용]`", parse_mode="Markdown")
        return
    query = " ".join(context.args)
    item = _todos.find_by_text(query) or next((t for t in _todos.get_pending() if t.id == query), None)
    if not item:
        await update.message.reply_text(f"'{query}'을(를) 찾지 못했어요.")
        return
    _todos.complete(item.id)
    await update.message.reply_text(f"✅ **{item.text}** 완료!", parse_mode="Markdown")


async def todo_del_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/todo_del [id or text]"""
    if not _auth(update):
        return
    if not context.args:
        await update.message.reply_text("`/todo_del [ID]` 또는 `/todo_del [내용]`", parse_mode="Markdown")
        return
    query = " ".join(context.args)
    item = _todos.find_by_text(query) or next((t for t in _todos.get_all() if t.id == query), None)
    if not item:
        await update.message.reply_text(f"'{query}'을(를) 찾지 못했어요.")
        return
    _todos.delete(item.id)
    await update.message.reply_text(f"🗑 **{item.text}** 삭제했어요.", parse_mode="Markdown")


async def _handle_todo_natural(update: Update, text: str):
    """자연어 투두 처리"""
    if _todos is None:
        await update.message.reply_text("❌ 투두 초기화 실패.")
        return

    kst = timezone(timedelta(hours=9))
    now_str = datetime.now(kst).strftime("%Y년 %m월 %d일")
    try:
        parsed = claude_client.parse_todo(text, now_str)
    except Exception as e:
        await update.message.reply_text(f"이해하지 못했어요. (`{e}`)")
        return

    action = parsed.get("action", "add")
    todo_text = (parsed.get("text") or text).strip()
    due_date = parsed.get("due_date") or ""

    try:
        if action == "list":
            await todos_handler(update, None)

        elif action == "add":
            await update.message.chat.send_action("typing")
            item = TodoItem(text=todo_text, due_date=due_date)
            _todos.add(item)
            due_str = _fmt_due(due_date)
            key, markup = _undo_keyboard("↩️ 취소")
            _undo_state[key] = {"type": "delete_todo", "todo_id": item.id}
            await update.message.reply_text(
                f"📋 **{todo_text}** 추가했어요!{due_str}",
                parse_mode="Markdown", reply_markup=markup
            )

        elif action == "complete":
            item = _todos.find_by_text(todo_text)
            if not item:
                await update.message.reply_text(f"'{todo_text}'을(를) 찾지 못했어요. `/todos`로 목록 확인해주세요.", parse_mode="Markdown")
                return
            _todos.complete(item.id)
            await update.message.reply_text(f"✅ **{item.text}** 완료!", parse_mode="Markdown")

        elif action == "delete":
            item = _todos.find_by_text(todo_text)
            if not item:
                await update.message.reply_text(f"'{todo_text}'을(를) 찾지 못했어요.")
                return
            _todos.delete(item.id)
            await update.message.reply_text(f"🗑 **{item.text}** 삭제했어요.", parse_mode="Markdown")

    except Exception as e:
        await update.message.reply_text(f"❌ 오류: {e}")


async def export_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _auth(update):
        return
    sheet_id = os.getenv("SPREADSHEET_ID", "")
    if sheet_id:
        url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/edit"
        await update.message.reply_text(f"[Google Sheets 열기]({url})", parse_mode="Markdown")
    else:
        await update.message.reply_text("SPREADSHEET_ID 환경변수가 설정되지 않았어요.")


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if not _auth(update):
        return

    data = query.data or ""
    if not data.startswith("undo:"):
        return

    key = data[5:]
    state = _undo_state.pop(key, None)
    if not state:
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text("이미 되돌렸거나 시간이 지났어요.")
        return

    t = state["type"]
    if t == "delete":
        ok = _sheets.delete_entry(state["entry_id"])
        await query.edit_message_text("↩️ 취소했어요!" if ok else "❌ 이미 삭제됐어요.")

    elif t == "restore":
        _sheets.update_entry(state["entry"])
        await query.edit_message_text("↩️ 되돌렸어요!")

    elif t == "cancel_remind":
        _reminders.deactivate(state["reminder_id"])
        await query.edit_message_text("🔕 리마인더 취소했어요!")

    elif t == "cancel_remind_list":
        for rid in state.get("ids", []):
            _reminders.deactivate(rid)
        n = len(state.get("ids", []))
        await query.edit_message_text(f"🔕 리마인더 {n}개 취소했어요!")

    elif t == "delete_todo":
        ok = _todos.delete(state["todo_id"])
        await query.edit_message_text("↩️ 취소했어요!" if ok else "❌ 이미 삭제됐어요.")


_MODE_WORDS: dict[BotMode, tuple] = {
    BotMode.SECRETARY:  ("비서", "secretary"),
    BotMode.FINANCE:    ("금융 모드", "금융전문가", "금융 전문가", "finance"),
    BotMode.CONSULTANT: ("컨설턴트", "consultant", "전략가"),
}
_SWITCH_WORDS = ("전환", "바꿔", "변경", "모드", "켜줘", "해줘", "시작")


def _detect_mode_switch(text: str) -> Optional[BotMode]:
    t = text.strip().lower()
    for mode, words in _MODE_WORDS.items():
        if any(w in t for w in words):
            # "모드" 키워드 포함 → 항상 전환
            if "모드" in t:
                return mode
            # 짧은 메시지(≤12자) → 전환 (예: "금융전문가 전환해", "컨설턴트")
            if len(t) <= 12:
                return mode
    return None


async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global _current_mode
    if not _auth(update):
        return
    text = update.message.text.strip()

    # 자연어 모드 전환 감지 (모든 모드에서 동작)
    new_mode = _detect_mode_switch(text)
    if new_mode:
        _current_mode = new_mode
        name = MODE_NAMES[_current_mode.value]
        await update.message.reply_text(f"{name} 모드로 전환했어요.")
        return

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


_TODO_WORDS = ("투두", "할 일", "할일", "todo", "할거", "할 거")
_REMINDER_WORDS = ("리마인더", "알람", "알림", "remind")
_REMINDER_LIST_KEYWORDS = ("목록", "뭐 있", "있어", "보여", "알려줘", "리스트", "list")
_REMINDER_CANCEL_ALL_KEYWORDS = ("전부 취소", "다 취소", "모두 취소", "전부취소", "다취소", "모두취소", "전체 취소", "다 지워", "전부 지워", "모두 지워")


async def _handle_remind_natural(update: Update, text: str):
    """자연어 리마인더 등록 (여러 날짜 지원)"""
    if _reminders is None:
        await update.message.reply_text("❌ 리마인더 초기화 실패.")
        return

    await update.message.chat.send_action("typing")
    kst = timezone(timedelta(hours=9))
    now_str = datetime.now(kst).strftime("%Y년 %m월 %d일 %H시 %M분")
    try:
        items = claude_client.parse_reminder_times(text, now_str)
    except Exception as e:
        await update.message.reply_text(f"시간을 이해하지 못했어요. (`{e}`)")
        return

    registered = []
    for item in items:
        try:
            reminder = Reminder(
                text=item.get("reminder_text", text),
                trigger_at=item["trigger_at"],
                repeat=item.get("repeat", "none"),
            )
            _reminders.add_reminder(reminder)
            registered.append(reminder)
        except Exception as e:
            await update.message.reply_text(f"❌ 저장 실패: {e}")
            return

    repeat_label = {"none": "", "daily": " 매일", "weekly": " 매주", "monthly": " 매달"}
    lines = [f"🔔 리마인더 {len(registered)}개 등록!"]
    for r in registered:
        try:
            dt = datetime.fromisoformat(r.trigger_at)
            ts = dt.strftime("%m/%d %H:%M")
        except Exception:
            ts = r.trigger_at
        rep = repeat_label.get(r.repeat, "")
        lines.append(f"• {r.text} — {ts}{rep}")

    key, markup = _undo_keyboard("↩️ 전체 취소" if len(registered) > 1 else "↩️ 취소")
    # 전체 취소 undo: 등록된 모든 reminder id 저장
    _undo_state[key] = {"type": "cancel_remind_list", "ids": [r.id for r in registered]}
    await update.message.reply_text("\n".join(lines), reply_markup=markup)


def _is_reminder_intent(t: str, keywords: tuple) -> bool:
    has_reminder_word = any(w in t for w in _REMINDER_WORDS)
    has_keyword = any(k in t for k in keywords)
    return has_reminder_word and has_keyword


async def _handle_secretary(update: Update, text: str):
    t = text.lower()

    # 투두 관련 메시지
    if any(w in t for w in _TODO_WORDS):
        await _handle_todo_natural(update, text)
        return

    # 리마인더 관련 메시지: 취소 → 목록 → 그 외는 등록으로 처리
    if any(w in t for w in _REMINDER_WORDS):
        if _is_reminder_intent(t, _REMINDER_CANCEL_ALL_KEYWORDS):
            await cancel_all_reminders_handler(update, None)
        elif _is_reminder_intent(t, _REMINDER_LIST_KEYWORDS):
            await reminders_handler(update, None)
        else:
            await _handle_remind_natural(update, text)
        return

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
        key, markup = _undo_keyboard()
        _undo_state[key] = {"type": "delete", "entry_id": added.id}
        await update.message.reply_text(
            f"**{added.title}** ({type_kr}) 추가했어요! 상태: {status_kr}",
            parse_mode="Markdown", reply_markup=markup
        )
        return

    if not entry:
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
        key, markup = _undo_keyboard()
        _undo_state[key] = {"type": "delete", "entry_id": added.id}
        await update.message.reply_text(
            f"**{added.title}** 새로 등록했어요! {added.progress or ''}",
            parse_mode="Markdown", reply_markup=markup
        )
        return

    # 기존 항목 업데이트 — 이전 상태 저장
    old_entry = copy.deepcopy(entry)

    if intent.progress:
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
    key, markup = _undo_keyboard()
    _undo_state[key] = {"type": "restore", "entry": old_entry}
    await update.message.reply_text(
        _build_update_reply(entry, intent), parse_mode="Markdown", reply_markup=markup
    )


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
