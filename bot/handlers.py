import copy
import os
import uuid as uuid_module
from datetime import date, datetime, timezone, timedelta
from typing import Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from .models import BotMode, ContentEntry, Reminder, TodoItem, MemoEntry, STATUS_KR, CONTENT_TYPE_KR
from .sheets import SheetsClient
from .reminders_sheet import RemindersClient
from .todos_sheet import TodosClient
from .memos_sheet import MemosClient
from .inventory_sheet import InventoryClient
from .intake_sheet import IntakeLogClient
from .cycle_sheet import CycleClient, PHASE_INFO
from . import claude_client
from . import figma_client
from .mode_prompts import MODE_NAMES
from .instagram_prompts import INSTAGRAM_AGENT_NAMES

# 모드별 대화 히스토리 (최근 10턴)
_history: dict[str, list[dict]] = {
    "secretary": [],
    "finance": [],
    "consultant": [],
    "instagram_designer": [],
    "instagram_writer": [],
    "instagram_manager": [],
}
MAX_HISTORY = 20  # message 수 (user+assistant 합산)

_current_mode: BotMode = BotMode.SECRETARY
_current_instagram_agent: str = "manager"  # designer | writer | manager
_sheets: Optional[SheetsClient] = None
_reminders: Optional[RemindersClient] = None
_todos: Optional[TodosClient] = None
_memos: Optional[MemosClient] = None
_inventory: Optional[InventoryClient] = None
_intake: Optional[IntakeLogClient] = None
_cycle: Optional[CycleClient] = None
_pending_checkin: dict[int, str] = {}  # chat_id → "morning"|"afternoon"
_undo_state: dict[str, dict] = {}  # key → undo payload (in-memory, TTL 없음)
_pending_snooze: dict[int, str] = {}  # chat_id → reminder_id (직접입력 대기 중)


def init_sheets(sheets: SheetsClient):
    global _sheets
    _sheets = sheets


def init_reminders(reminders: RemindersClient):
    global _reminders
    _reminders = reminders


def init_todos(todos: TodosClient):
    global _todos
    _todos = todos


def init_memos(memos: MemosClient):
    global _memos
    _memos = memos


def init_inventory(inventory: InventoryClient):
    global _inventory
    _inventory = inventory


def init_intake(intake: IntakeLogClient):
    global _intake
    _intake = intake


def init_cycle(cycle: CycleClient):
    global _cycle
    _cycle = cycle


def _reminder_action_keyboard(reminder_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ 완료", callback_data=f"remind:done:{reminder_id}"),
        InlineKeyboardButton("🔁 재알람", callback_data=f"remind:snooze:{reminder_id}"),
    ]])


def _snooze_options_keyboard(reminder_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("10분 뒤", callback_data=f"remind:snooze10m:{reminder_id}"),
            InlineKeyboardButton("1시간 뒤", callback_data=f"remind:snooze1h:{reminder_id}"),
        ],
        [
            InlineKeyboardButton("하루 뒤", callback_data=f"remind:snooze1d:{reminder_id}"),
            InlineKeyboardButton("직접 입력", callback_data=f"remind:snooze_custom:{reminder_id}"),
        ],
    ])


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
    """/remind — Todos 시트에 알람 등록 (구 Reminders와 동일하게 동작)"""
    if not _auth(update):
        return
    raw = " ".join(context.args) if context.args else ""
    if not raw:
        await update.message.reply_text(
            "사용법: `/remind 내일 오전 9시 / 약 먹기`\n\n"
            "또는 자연어로: `투두 내일 9시 약 먹기`\n"
            "반복 예시:\n"
            "• `/remind 매일 저녁 10시 / 스트레칭`\n"
            "• `/remind 매주 월요일 아침 8시 / 주간 계획`",
            parse_mode="Markdown"
        )
        return
    await _handle_todo_natural(update, raw)


async def reminders_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/reminders — 알람이 있는 투두 목록 (Todos 시트 기반)"""
    if not _auth(update):
        return
    if _todos is None:
        await update.message.reply_text("❌ 투두 초기화 실패.")
        return
    all_todos = _todos.get_all()
    active = [t for t in all_todos if not t.done and t.trigger_at]
    if not active:
        await update.message.reply_text("등록된 알람이 없어요.")
        return

    repeat_label = {"none": "", "daily": " 매일", "weekly": " 매주", "monthly": " 매달"}
    lines = ["**🔔 알람 목록**\n"]
    for i, t in enumerate(active, 1):
        try:
            dt = datetime.fromisoformat(t.trigger_at)
            time_str = dt.strftime("%m/%d %H:%M")
        except Exception:
            time_str = t.trigger_at
        rep = repeat_label.get(t.repeat, "")
        lines.append(f"{i}. {t.text}\n   `{time_str}`{rep}  ID: `{t.id}`")
    lines.append("\n취소: `/cancel_reminder [ID]`")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cancel_reminder_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/cancel_reminder [id] — 알람 해제 (투두 항목은 유지)"""
    if not _auth(update):
        return
    if not context.args:
        await update.message.reply_text(
            "`/cancel_reminder [ID]`\n`/reminders`에서 ID 확인하세요.",
            parse_mode="Markdown"
        )
        return
    rid = context.args[0]
    if _todos:
        _todos.clear_trigger(rid)
        await update.message.reply_text(f"🔕 알람 `{rid}` 해제했어요. (투두는 유지)", parse_mode="Markdown")
    elif _reminders:
        _reminders.deactivate(rid)
        await update.message.reply_text(f"리마인더 `{rid}` 취소했어요.", parse_mode="Markdown")


async def cancel_all_reminders_handler(update: Update, context):
    """/cancel_all_reminders — 모든 알람 해제"""
    if not _auth(update):
        return
    if _todos:
        _todos.cancel_all_alarms()
        await update.message.reply_text("🔕 모든 알람 해제했어요. (투두 항목은 유지)")
    elif _reminders:
        active = _reminders.get_all_active()
        for r in active:
            _reminders.deactivate(r.id)
        await update.message.reply_text(f"🔕 리마인더 {len(active)}개 전부 취소했어요.")


async def clear_reminders_sheet_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/clear_reminders — Reminders 시트 초기화"""
    if not _auth(update):
        return
    if _reminders:
        _reminders.clear_all()
    await update.message.reply_text("🗑 Reminders 시트 초기화했어요.")


async def migrate_reminders_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/migrate_reminders — Reminders 시트 → Todos 시트로 이전"""
    if not _auth(update):
        return
    if _reminders is None or _todos is None:
        await update.message.reply_text("❌ 초기화 실패.")
        return
    active = _reminders.get_all_active()
    if not active:
        await update.message.reply_text("Reminders 시트에 이전할 항목이 없어요.")
        return
    migrated = 0
    for r in active:
        item = TodoItem(
            text=r.text,
            trigger_at=r.trigger_at,
            repeat=r.repeat,
            created_at=r.created_at,
        )
        _todos.add(item)
        _reminders.deactivate(r.id)
        migrated += 1
    await update.message.reply_text(
        f"✅ {migrated}개 이전 완료! (Reminders → Todos)\n`/reminders`로 확인해보세요.",
        parse_mode="Markdown"
    )


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
    for i, item in enumerate(pending, 1):
        alarm_str = ""
        if item.trigger_at:
            try:
                dt = datetime.fromisoformat(item.trigger_at)
                alarm_str = f" ⏰{dt.strftime('%m/%d %H:%M')}"
            except Exception:
                alarm_str = " ⏰"
        lines.append(f"{i}. {item.text}{_fmt_due(item.due_date)}{alarm_str}  `{item.id}`")
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


_MEMO_SAVE_KEYWORDS = ("기록해줘", "저장해줘", "메모해줘", "기억해줘", "기록해", "저장해", "메모해", "기억해", "적어줘", "노트해줘")
_MEMO_LIST_KEYWORDS = ("기록 목록", "메모 목록", "저장 목록", "기록 보여", "메모 보여", "노트 보여", "기록 뭐", "메모 뭐")
_MEMO_MODE_KR = {"secretary": "비서", "finance": "금융전문가", "consultant": "컨설턴트"}


async def _save_memo(update: Update, mode: str, content: str):
    """대화 내용을 Memos 시트에 저장"""
    entry = _memos.add(mode, content)
    dt = datetime.fromisoformat(entry.created_at).strftime("%m/%d %H:%M")
    key, markup = _undo_keyboard("↩️ 삭제")
    _undo_state[key] = {"type": "delete_memo", "memo_id": entry.id}
    await update.message.reply_text(
        f"📝 저장했어요! ({_MEMO_MODE_KR.get(mode, mode)} / {dt})",
        reply_markup=markup
    )


async def memos_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/memos [mode] — 메모 목록"""
    if not _auth(update):
        return
    mode = _current_mode.value
    if context and context.args:
        arg = context.args[0].lower()
        if arg in ("finance", "금융"):
            mode = "finance"
        elif arg in ("consultant", "컨설턴트"):
            mode = "consultant"
        elif arg in ("secretary", "비서"):
            mode = "secretary"
    try:
        entries = _memos.get_by_mode(mode)
    except Exception as e:
        await update.message.reply_text(f"❌ 오류: {e}")
        return
    if not entries:
        await update.message.reply_text(f"저장된 {_MEMO_MODE_KR.get(mode, mode)} 메모가 없어요.")
        return
    lines = [f"**📝 {_MEMO_MODE_KR.get(mode, mode)} 메모**\n"]
    for i, e in enumerate(entries, 1):
        dt = e.created_at[:10] if e.created_at else ""
        preview = e.content[:60] + ("..." if len(e.content) > 60 else "")
        lines.append(f"{i}. [{dt}] {preview}  `{e.id}`")
    lines.append("\n삭제: `/memo_del [ID]`")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def memo_del_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/memo_del [id]"""
    if not _auth(update):
        return
    if not context.args:
        await update.message.reply_text("`/memo_del [ID]`", parse_mode="Markdown")
        return
    ok = _memos.delete(context.args[0])
    await update.message.reply_text("🗑 삭제했어요." if ok else "찾지 못했어요.")


async def _handle_todo_natural(update: Update, text: str):
    """자연어 투두+알람 통합 처리"""
    if _todos is None:
        await update.message.reply_text("❌ 투두 초기화 실패.")
        return

    kst = timezone(timedelta(hours=9))
    now_str = datetime.now(kst).strftime("%Y-%m-%d %H:%M")
    try:
        parsed = claude_client.parse_todo(text, now_str)
    except Exception as e:
        await update.message.reply_text(f"이해하지 못했어요. (`{e}`)")
        return

    action = parsed.get("action", "add")
    todo_text = (parsed.get("text") or text).strip()
    due_date = parsed.get("due_date") or ""
    trigger_at = parsed.get("trigger_at") or ""
    repeat = parsed.get("repeat") or "none"

    try:
        if action == "list":
            await todos_handler(update, None)

        elif action == "add":
            await update.message.chat.send_action("typing")
            item = TodoItem(text=todo_text, due_date=due_date, trigger_at=trigger_at, repeat=repeat)
            _todos.add(item)
            key, markup = _undo_keyboard("↩️ 취소")
            _undo_state[key] = {"type": "delete_todo", "todo_id": item.id}
            if trigger_at:
                try:
                    dt = datetime.fromisoformat(trigger_at)
                    time_label = dt.strftime("%m/%d %H:%M")
                except Exception:
                    time_label = trigger_at
                rep_label = {"daily": " (매일)", "weekly": " (매주)", "monthly": " (매달)"}.get(repeat, "")
                await update.message.reply_text(
                    f"⏰ **{todo_text}** — {time_label}에 알림{rep_label}",
                    parse_mode="Markdown", reply_markup=markup
                )
            else:
                due_str = _fmt_due(due_date)
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

        elif action == "cancel_alarms":
            _todos.cancel_all_alarms()
            await update.message.reply_text("🔕 모든 알람을 해제했어요. (투두 항목은 유지됩니다)")

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

    # ── 리마인더 완료 / 재알람 버튼 ──
    if data.startswith("remind:"):
        await _handle_remind_callback(query, data)
        return

    # ── 포모도로 버튼 ──
    if data.startswith("pomo:"):
        action = data[5:]
        if action == "done":
            await query.edit_message_text("🔚 오늘 집중 수고했어요! 💪")
        elif action == "restart":
            await query.edit_message_text("🍅 새 포모도로를 시작하려면 '포모도로 시작' 이라고 보내주세요!")
        return

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

    elif t == "delete_memo":
        ok = _memos.delete(state["memo_id"])
        await query.edit_message_text("🗑 메모 삭제했어요." if ok else "❌ 이미 삭제됐어요.")


def _now_kst() -> datetime:
    return datetime.now(timezone(timedelta(hours=9))).replace(tzinfo=None)


def _fmt_interval(minutes: int) -> str:
    """분 단위를 사람이 읽기 좋은 문자열로 변환"""
    if minutes < 60:
        return f"{minutes}분"
    elif minutes < 1440:
        h = minutes // 60
        m = minutes % 60
        return f"{h}시간" + (f" {m}분" if m else "")
    else:
        d = minutes // 1440
        h = (minutes % 1440) // 60
        return f"{d}일" + (f" {h}시간" if h else "")


async def _handle_remind_callback(query, data: str):
    """remind:done:{id} | remind:snooze:{id} | remind:snooze10m/1h/1d/custom:{id}"""
    global _pending_snooze
    if _todos is None:
        await query.edit_message_text("❌ 투두 초기화 실패.")
        return

    parts = data.split(":", 2)  # ["remind", action, id]
    if len(parts) < 3:
        return
    action, todo_id = parts[1], parts[2]

    if action == "done":
        todo_item = _todos.get_by_id(todo_id)
        if todo_item and todo_item.repeat.startswith("after:"):
            # after:N 타입 — 완료 처리 후 지금 시점 기준으로 N분 후 재스케줄
            try:
                minutes = int(todo_item.repeat.split(":")[1])
            except (IndexError, ValueError):
                minutes = 60
            next_t = (_now_kst() + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%S")
            _todos.reschedule(todo_id, next_t)
            # done 플래그는 False로 유지 (반복 항목이므로)
            label = _fmt_interval(minutes)
            await query.edit_message_text(f"✅ 완료! {label} 후에 다시 알려드릴게요.")
        else:
            _todos.complete(todo_id)
            await query.edit_message_text("✅ 완료했어요!")
        # 영양제/약 이름 매칭 → 복용 기록 + 재고 자동차감
        if todo_item:
            _auto_log_intake_from_todo(todo_item.text)

    elif action == "snooze":
        original_text = query.message.text or "🔔 알림"
        await query.edit_message_text(
            f"{original_text}\n\n⏰ 언제 다시 알려드릴까요?",
            reply_markup=_snooze_options_keyboard(todo_id),
        )

    elif action in ("snooze10m", "snooze1h", "snooze1d"):
        delta = {"snooze10m": timedelta(minutes=10), "snooze1h": timedelta(hours=1), "snooze1d": timedelta(days=1)}[action]
        label = {"snooze10m": "10분", "snooze1h": "1시간", "snooze1d": "하루"}[action]
        new_trigger = (_now_kst() + delta).strftime("%Y-%m-%dT%H:%M:%S")
        _todos.reschedule(todo_id, new_trigger)
        await query.edit_message_text(f"⏰ {label} 뒤에 다시 알려드릴게요!")

    elif action == "snooze_custom":
        chat_id = query.message.chat_id
        _pending_snooze[chat_id] = todo_id
        await query.edit_message_text("⏰ 재알람 시간을 입력해주세요.\n예: 30분 뒤, 내일 오전 9시, 3일 후")


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


# ── 인스타그램 팀 핸들러 ─────────────────────────────────────

async def instagram_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """인스타그램 마케팅팀 모드 진입"""
    global _current_mode
    if not _auth(update):
        return
    _current_mode = BotMode.INSTAGRAM
    agent_name = INSTAGRAM_AGENT_NAMES[_current_instagram_agent]
    figma_status = "✅ 연동됨" if figma_client.is_configured() else "⚙️ 미설정 (선택사항)"
    text = (
        "📱 **인스타그램 마케팅팀** (@graphic.fan)\n\n"
        f"현재 에이전트: **{agent_name}**\n"
        f"피그마: {figma_status}\n\n"
        "**에이전트 전환:**\n"
        "/designer — 🎨 디자이너 (피그마 연동, 디자인 스펙)\n"
        "/writer — ✍️ 작가 (캡션·한줄평 작성)\n"
        "/igmanager — 📊 매니저 (통계 분석·이벤트 기획)\n\n"
        "전환 후 바로 말 걸면 돼요!"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def switch_instagram_agent_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/designer /writer /igmanager — 에이전트 전환"""
    global _current_mode, _current_instagram_agent
    if not _auth(update):
        return
    cmd = update.message.text.split()[0].lstrip("/").lower()
    if cmd == "designer":
        _current_instagram_agent = "designer"
    elif cmd == "writer":
        _current_instagram_agent = "writer"
    elif cmd == "igmanager":
        _current_instagram_agent = "manager"
    _current_mode = BotMode.INSTAGRAM
    name = INSTAGRAM_AGENT_NAMES[_current_instagram_agent]
    await update.message.reply_text(f"{name} 에이전트로 전환했어요. 바로 말 걸어주세요!", parse_mode="Markdown")


async def figma_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/figma — 피그마 컴포넌트/스타일 조회"""
    if not _auth(update):
        return
    if not figma_client.is_configured():
        await update.message.reply_text(
            "피그마 연동이 설정되지 않았어요.\n\n"
            "`.env`에 추가해주세요:\n"
            "`FIGMA_TOKEN=your_token`\n"
            "`FIGMA_FILE_KEY=your_file_key`",
            parse_mode="Markdown"
        )
        return
    await update.message.chat.send_action("typing")
    try:
        components = figma_client.get_components()
        styles = figma_client.get_styles()
        lines = [
            f"**피그마 디자인 시스템** ({len(components)}개 컴포넌트, {len(styles)}개 스타일)\n",
            "**컴포넌트**",
            figma_client.format_components_summary(components),
            "\n**스타일**",
            figma_client.format_styles_summary(styles),
        ]
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"피그마 조회 실패: {e}")


_DESIGNER_KEYWORDS = ("디자이너", "디자인", "피그마", "레이아웃", "컬러", "폰트", "비주얼", "포스터", "템플릿", "시안")
_WRITER_KEYWORDS = ("작가", "캡션", "한줄평", "카피", "문구", "글", "해시태그", "훅", "추천작", "소개글")
_MANAGER_KEYWORDS = ("매니저", "통계", "분석", "캘린더", "일정", "이벤트", "전략", "기획", "팔로워", "도달", "릴스")


def _detect_instagram_agent(text: str) -> str | None:
    """자연어에서 에이전트 의도 감지. 감지 못하면 None 반환."""
    t = text.lower()
    if any(k in t for k in _DESIGNER_KEYWORDS):
        return "designer"
    if any(k in t for k in _WRITER_KEYWORDS):
        return "writer"
    if any(k in t for k in _MANAGER_KEYWORDS):
        return "manager"
    return None


async def _handle_instagram(update: Update, text: str):
    """인스타그램 에이전트에게 메시지 라우팅 (자연어 감지 포함)"""
    global _current_instagram_agent

    # 자연어로 에이전트 전환 감지
    detected = _detect_instagram_agent(text)
    if detected and detected != _current_instagram_agent:
        _current_instagram_agent = detected
        name = INSTAGRAM_AGENT_NAMES[detected]
        await update.message.reply_text(f"{name} 에이전트로 자동 전환했어요.", parse_mode="Markdown")

    agent = _current_instagram_agent
    history_key = f"instagram_{agent}"
    await update.message.chat.send_action("typing")

    # 디자이너 모드: 피그마 컴포넌트 컨텍스트 포함
    figma_context = ""
    if agent == "designer" and figma_client.is_configured():
        try:
            components = figma_client.get_components()
            styles = figma_client.get_styles()
            parts = []
            if components:
                parts.append(f"컴포넌트:\n{figma_client.format_components_summary(components)}")
            if styles:
                parts.append(f"스타일:\n{figma_client.format_styles_summary(styles)}")
            figma_context = "\n\n".join(parts)
        except Exception:
            pass

    _add_history(history_key, "user", text)
    reply = claude_client.instagram_chat(agent, text, history=_history[history_key][:-1], figma_context=figma_context)
    _add_history(history_key, "assistant", reply)
    await update.message.reply_text(reply)


async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global _current_mode
    if not _auth(update):
        return
    text = update.message.text.strip()
    chat_id = update.message.chat_id

    # ── 체크인 응답 대기 중 ──
    if chat_id in _pending_checkin:
        checkin_type = _pending_checkin.pop(chat_id)
        await _handle_checkin_response(update, text, checkin_type)
        return

    # ── 재알람 직접 입력 대기 중 ──
    if chat_id in _pending_snooze:
        reminder_id = _pending_snooze.pop(chat_id)
        if _todos is not None:
            try:
                from datetime import timezone, timedelta
                now_str = _now_kst().strftime("%Y-%m-%d %H:%M")
                parsed_list = claude_client.parse_reminder_times(text, now_str)
                if parsed_list:
                    new_trigger = parsed_list[0]["trigger_at"]
                    _todos.reschedule(reminder_id, new_trigger)
                    # 사람이 읽기 좋은 시간 표시
                    try:
                        dt = datetime.fromisoformat(new_trigger)
                        label = dt.strftime("%m/%d %H:%M")
                    except Exception:
                        label = new_trigger
                    await update.message.reply_text(f"⏰ {label}에 다시 알려드릴게요!")
                else:
                    await update.message.reply_text("⚠️ 시간을 이해하지 못했어요. 다시 시도해주세요. (예: 30분 뒤, 내일 오전 9시)")
            except Exception as e:
                await update.message.reply_text(f"❌ 오류: {e}")
        else:
            await update.message.reply_text("⚠️ 시간을 이해하지 못했어요. 다시 시도해주세요.")
        return

    # 자연어 모드 전환 감지 (모든 모드에서 동작)
    new_mode = _detect_mode_switch(text)
    if new_mode:
        _current_mode = new_mode
        name = MODE_NAMES[_current_mode.value]
        await update.message.reply_text(f"{name} 모드로 전환했어요.")
        return

    mode = _current_mode.value
    t = text.lower()

    # 메모 저장 감지 (모든 모드)
    if any(kw in t for kw in _MEMO_SAVE_KEYWORDS):
        # 저장 키워드를 제거한 나머지 텍스트
        remaining = text
        for kw in _MEMO_SAVE_KEYWORDS:
            remaining = remaining.replace(kw, "").replace(kw.replace("줘", ""), "")
        remaining = remaining.strip(" \n.,")

        await update.message.chat.send_action("typing")

        if len(remaining) > 20:
            # 메시지 안에 내용이 있으면 그걸 그대로 저장
            content = remaining
        else:
            # 대화 히스토리 요약
            hist = _history.get(mode, [])
            if not hist:
                await update.message.reply_text(
                    "저장할 내용이 없어요.\n내용을 직접 입력하거나 대화 후 기록해줘 해주세요.\n\n예: `청년적금 35만원, 투자 20만원 기록해줘`",
                    parse_mode="Markdown"
                )
                return
            content = claude_client.summarize_conversation(hist[-10:], mode)

        if not content:
            await update.message.reply_text("요약할 내용이 없어요.")
            return
        await _save_memo(update, mode, content)
        return

    # 메모 목록 감지 (모든 모드)
    if any(kw in t for kw in _MEMO_LIST_KEYWORDS):
        await memos_handler(update, None)
        return

    # 비서 모드
    if mode == "secretary":
        await _handle_secretary(update, context, text)
    elif mode == "instagram":
        await _handle_instagram(update, text)
    else:
        # 금융/컨설턴트 모드: Claude 대화
        await update.message.chat.send_action("typing")
        _add_history(mode, "user", text)
        # 해당 모드의 저장된 메모를 context로 넘겨 Claude가 인식하게 함
        memo_context = ""
        if _memos is not None:
            try:
                recent_memos = _memos.get_by_mode(mode, limit=5)
                if recent_memos:
                    memo_lines = [f"[{m.created_at}] {m.content}" for m in recent_memos]
                    memo_context = "저장된 메모:\n" + "\n---\n".join(memo_lines)
            except Exception:
                pass
        reply = claude_client.chat(mode, text, context=memo_context, history=_history[mode][:-1])
        _add_history(mode, "assistant", reply)
        await update.message.reply_text(reply)


_TODO_WORDS = (
    "투두", "할 일", "할일", "todo", "할거", "할 거",
    # 미완료/조회 자연어
    "미완료", "못 한", "못한", "안 한", "안한",
    "남은 거", "남은거", "뭐 남", "뭐남", "남아있",
    "뭐 해야", "뭐해야", "해야 할", "해야할",
    "아직", "안 끝", "안끝",
)
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


_INVENTORY_KEYWORDS = (
    "영양제", "약 먹", "약먹", "남은 약", "재고", "복용",
    "오늘 뭐 먹었", "뭐 먹었어", "먹었어", "챙겼어", "챙겼나",
    "얼마나 남", "몇 정", "남았어", "약 현황", "영양제 현황",
    "수면", "음수", "물 마",
)
_CYCLE_KEYWORDS = (
    "생리", "월경", "여포기", "배란기", "황체기", "생리기",
    "주기", "생리 주기", "생리 언제", "생리 시작", "생리 끝",
    "몇 기야", "어느 단계", "다음 생리",
)
_POMO_KEYWORDS = ("포모도로", "집중 타이머", "집중 시작", "분 집중", "집중해야", "집중할게")
_BREAKDOWN_KEYWORDS = ("어떻게 시작", "뭐부터 해", "막막해", "쪼개줘", "단계별로", "분해해줘", "시작이 막막")


async def _handle_secretary(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    t = text.lower()

    # 생리주기
    if any(w in t for w in _CYCLE_KEYWORDS):
        await _handle_cycle_natural(update, text)
        return

    # 영양제/약/수면/음수 복용 기록
    if any(w in t for w in _INVENTORY_KEYWORDS):
        await _handle_inventory_natural(update, text)
        return

    # 포모도로 타이머
    if any(w in t for w in _POMO_KEYWORDS):
        await _handle_pomo_natural(update, context, text)
        return

    # 과제 분해
    if any(w in t for w in _BREAKDOWN_KEYWORDS):
        await _handle_breakdown(update, text)
        return

    # 투두/알람 통합 처리 ("투두" 또는 "리마인더" 키워드 모두 포함)
    if any(w in t for w in _TODO_WORDS) or any(w in t for w in _REMINDER_WORDS):
        await _handle_todo_natural(update, text)
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


# ═══════════════════════════════════════════════════════════
# 영양제 재고 관리
# ═══════════════════════════════════════════════════════════

def _auto_log_intake_from_todo(todo_text: str):
    """투두 완료 시 영양제/약 이름 매칭 → 복용 기록 + 재고 차감"""
    if _inventory is None or _intake is None:
        return
    t = todo_text.lower()
    # "아침 영양제" 패턴 → daily 아이템 전부 처리
    if "영양제" in t:
        for item in _inventory.get_daily():
            new_qty = max(0, item.qty - 1)
            _inventory.update_qty(item.id, new_qty)
            _intake.log(item.name, 1, new_qty, "알람완료")
        return
    # 개별 이름 매칭
    for item in _inventory.get_all():
        if item.name.lower() in t or t in item.name.lower():
            new_qty = max(0, item.qty - 1)
            _inventory.update_qty(item.id, new_qty)
            _intake.log(item.name, 1, new_qty, "알람완료")
            return


async def _handle_inventory_natural(update: Update, text: str):
    """자연어 영양제/약 복용 기록 및 재고 조회"""
    if _inventory is None:
        await update.message.reply_text("❌ 재고 관리가 초기화되지 않았어요. `/setup_supplements` 를 실행해주세요.")
        return

    item_names = [item.name for item in _inventory.get_all()]
    kst_str = _now_kst().strftime("%Y-%m-%d %H:%M")
    try:
        parsed = claude_client.parse_intake_message(text, item_names)
    except Exception as e:
        await update.message.reply_text(f"이해하지 못했어요. (`{e}`)")
        return

    action = parsed.get("action", "log")
    items_list = parsed.get("items", [])

    if action == "log":
        if not items_list:
            # "먹었어" 단독 발화 → 오늘 데일리 전체로 해석
            items_list = [{"name": i.name, "qty": 1, "note": ""} for i in _inventory.get_daily()]
        lines = []
        for item_info in items_list:
            name = item_info.get("name", "")
            qty = int(item_info.get("qty", 1) or 1)
            note = item_info.get("note", "") or ""
            inv_item = _inventory.get_by_name(name)
            if not inv_item:
                lines.append(f"❓ '{name}'을(를) 목록에서 찾지 못했어요.")
                continue
            new_qty = max(0, inv_item.qty - qty)
            _inventory.update_qty(inv_item.id, new_qty)
            if _intake:
                _intake.log(inv_item.name, qty, new_qty, note)
            warn = " ⚠️ 곧 소진!" if new_qty <= inv_item.low_threshold else ""
            lines.append(f"✅ {inv_item.name} — {new_qty}정 남음{warn}")
        if lines:
            await update.message.reply_text("\n".join(lines))
        else:
            await update.message.reply_text("복용 기록할 항목을 찾지 못했어요.")

    elif action == "query_stock":
        target = parsed.get("target_name")
        if target:
            inv_item = _inventory.get_by_name(target)
            if not inv_item:
                await update.message.reply_text(f"'{target}'을(를) 목록에서 찾지 못했어요.")
                return
            warn = " ⚠️" if inv_item.qty <= inv_item.low_threshold else ""
            await update.message.reply_text(
                f"💊 **{inv_item.name}**: {inv_item.qty}정 남음{warn}", parse_mode="Markdown"
            )
        else:
            await inventory_handler(update, None)

    elif action == "query_today":
        await intake_handler(update, None)

    elif action == "add_item":
        new_item_info = parsed.get("new_item") or {}
        name = new_item_info.get("name") or (items_list[0].get("name") if items_list else "")
        if not name:
            await update.message.reply_text("이름을 인식하지 못했어요. 예: '오메가3 60정 추가해줘'")
            return
        qty = int(new_item_info.get("qty") or (items_list[0].get("qty") if items_list else 30))
        category = new_item_info.get("category") or "daily"
        daily = bool(new_item_info.get("daily", False))
        note = new_item_info.get("note") or ""
        phases = new_item_info.get("phases") or ""
        low = 7 if category != "prescription" else 5
        item = _inventory.add_item(name, qty, category=category, low_threshold=low, daily=daily, note=note, phases=phases)
        daily_str = " (매일 복용)" if daily else ""
        phase_str = f"\n주기 추천: {phases}" if phases else ""
        await update.message.reply_text(
            f"✅ **{item.name}** 등록했어요!\n{qty}정 · {category}{daily_str}{phase_str}\n\n"
            "매일 알람에 추가하려면 '투두 아침 {이름} 09:00 매일' 이라고 말해줘요.",
            parse_mode="Markdown",
        )

    elif action == "restock":
        if not items_list:
            await update.message.reply_text("보충할 항목을 인식하지 못했어요.")
            return
        lines = []
        for item_info in items_list:
            name = item_info.get("name", "")
            qty = int(item_info.get("qty", 30) or 30)
            inv_item = _inventory.get_by_name(name)
            if not inv_item:
                lines.append(f"❓ '{name}'을(를) 목록에서 찾지 못했어요. '추가해줘'로 신규 등록하세요.")
                continue
            new_qty = inv_item.qty + qty
            _inventory.update_qty(inv_item.id, new_qty)
            lines.append(f"✅ **{inv_item.name}** {inv_item.qty}→{new_qty}정")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def inventory_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/inventory — 전체 재고 현황"""
    if not _auth(update):
        return
    if _inventory is None:
        await update.message.reply_text("❌ 재고 관리 초기화 실패. `/setup_supplements` 를 실행하세요.")
        return
    try:
        items = _inventory.get_all()
    except Exception as e:
        await update.message.reply_text(f"❌ 오류: {e}")
        return
    if not items:
        await update.message.reply_text("재고 데이터가 없어요. `/setup_supplements` 로 초기화하세요.", parse_mode="Markdown")
        return

    low_ids = {i.id for i in _inventory.get_low_stock()}
    cat_labels = {"daily": "매일 복용", "prescription": "처방약", "situational": "상황별", "pms": "PMS"}
    by_cat: dict[str, list] = {}
    for item in items:
        by_cat.setdefault(item.category, []).append(item)

    lines = ["**💊 영양제/약 재고 현황**\n"]
    for cat in ["daily", "prescription", "situational", "pms"]:
        cat_items = by_cat.get(cat, [])
        if not cat_items:
            continue
        lines.append(f"**{cat_labels.get(cat, cat)}**")
        for item in cat_items:
            warn = " ⚠️" if item.id in low_ids else ""
            lines.append(f"  • {item.name}: {item.qty}정{warn}")
    if low_ids:
        low_names = ", ".join(i.name for i in _inventory.get_low_stock())
        lines.append(f"\n⚠️ 부족 주의: {low_names}")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def intake_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/intake — 오늘 복용 내역"""
    if not _auth(update):
        return
    if _intake is None:
        await update.message.reply_text("❌ 복용 기록 초기화 실패.")
        return
    try:
        logs = _intake.get_today()
    except Exception as e:
        await update.message.reply_text(f"❌ 오류: {e}")
        return
    if not logs:
        await update.message.reply_text(f"오늘({_now_kst().strftime('%m/%d')}) 복용 기록이 없어요.")
        return
    lines = [f"**📋 오늘 복용 내역** ({_now_kst().strftime('%m/%d')})\n"]
    for log in logs:
        t = log.taken_at[11:16] if len(log.taken_at) > 11 else ""
        note_str = f" ({log.note})" if log.note else ""
        lines.append(f"• {log.item_name} {log.qty_taken}정{note_str} — {t}")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def setup_supplements_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/setup_supplements — 영양제 초기 데이터 등록"""
    if not _auth(update):
        return
    if _inventory is None:
        await update.message.reply_text("❌ 재고 관리 초기화 실패.")
        return
    await update.message.chat.send_action("typing")
    n = _inventory.setup_initial()
    cycle_ok = _cycle.setup_initial("2026-03-17") if _cycle else False

    todos_created = 0
    if _todos and n > 0:
        daily_items = _inventory.get_daily()
        from datetime import date as _date
        today = _date.today().isoformat()

        # 점심 영양제 (셀레늄, 크랜베리 — 식후)
        LUNCH_SUPPLEMENTS = {"셀레늄", "크랜베리"}
        lunch_items = [i for i in daily_items if i.name in LUNCH_SUPPLEMENTS and i.category not in ("prescription",)]
        morning_items = [i for i in daily_items if i.name not in LUNCH_SUPPLEMENTS and i.category not in ("prescription",)]
        prescription_items = [i for i in daily_items if i.category == "prescription"]

        if morning_items:
            names = "·".join(i.name for i in morning_items)
            _todos.add(TodoItem(text=f"아침 영양제 ({names})", trigger_at=f"{today}T09:00:00", repeat="daily"))
            todos_created += 1
        if lunch_items:
            names = "·".join(i.name for i in lunch_items)
            _todos.add(TodoItem(text=f"점심 영양제 ({names})", trigger_at=f"{today}T12:30:00", repeat="daily"))
            todos_created += 1
        for pres in prescription_items:
            _todos.add(TodoItem(text=pres.name, trigger_at=f"{today}T09:00:00", repeat="daily"))
            todos_created += 1

    if n == 0:
        await update.message.reply_text("이미 재고 데이터가 있어요. (초기화 스킵)")
        return

    msg_lines = [f"✅ 영양제/약 {n}종 등록 완료!"]
    if todos_created:
        msg_lines.append(f"⏰ 데일리 알람 {todos_created}개 생성 (매일 오전 9시)")
    if cycle_ok:
        msg_lines.append("🌸 생리주기 초기 설정 완료 (3/17 기준, 오늘 21일차 황체기)")
    msg_lines.append("\n`/inventory` 재고확인  `/cycle` 주기확인")
    await update.message.reply_text("\n".join(msg_lines), parse_mode="Markdown")


# ═══════════════════════════════════════════════════════════
# 생리주기
# ═══════════════════════════════════════════════════════════

async def _handle_cycle_natural(update: Update, text: str):
    """자연어 생리주기 기록/조회"""
    if _cycle is None:
        await update.message.reply_text("❌ 생리주기 초기화 실패.")
        return
    kst_str = _now_kst().strftime("%Y-%m-%d %H:%M")
    try:
        parsed = claude_client.parse_cycle_message(text, kst_str)
    except Exception as e:
        await update.message.reply_text(f"이해하지 못했어요. (`{e}`)")
        return

    action = parsed.get("action", "query_status")
    date_str = parsed.get("date") or _now_kst().strftime("%Y-%m-%d")
    note = parsed.get("note", "") or ""

    inv_items = _inventory.get_all() if _inventory else None

    if action == "start_period":
        _cycle.start_period(date_str, note)
        status = _cycle.get_current_status()
        await update.message.reply_text(
            f"🩸 생리 시작 기록했어요! ({date_str})\n\n" + CycleClient.format_status(status, inv_items),
            parse_mode="Markdown"
        )
    elif action == "end_period":
        ok = _cycle.end_period(date_str)
        if ok:
            await update.message.reply_text(f"✅ 생리 종료 기록했어요! ({date_str})\n이제 여포기가 시작돼요 🌱")
        else:
            await update.message.reply_text("생리 시작 기록이 없거나 이미 종료 처리됐어요.")
    else:
        status = _cycle.get_current_status()
        await update.message.reply_text(CycleClient.format_status(status, inv_items), parse_mode="Markdown")


async def cycle_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/cycle — 현재 생리주기 단계"""
    if not _auth(update):
        return
    if _cycle is None:
        await update.message.reply_text("❌ 생리주기 초기화 실패.")
        return
    try:
        status = _cycle.get_current_status()
        inv_items = _inventory.get_all() if _inventory else None
    except Exception as e:
        await update.message.reply_text(f"❌ 오류: {e}")
        return
    await update.message.reply_text(CycleClient.format_status(status, inv_items), parse_mode="Markdown")


# ═══════════════════════════════════════════════════════════
# ADHD 지원: 포모도로 & 과제 분해 & 체크인 응답
# ═══════════════════════════════════════════════════════════

async def pomo_done_callback(context):
    """포모도로 완료 알림 job callback"""
    chat_id = context.job.chat_id
    minutes = (context.job.data or {}).get("minutes", 25)
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("🍅 다시 시작", callback_data="pomo:restart"),
        InlineKeyboardButton("🔚 오늘 집중 끝", callback_data="pomo:done"),
    ]])
    await context.bot.send_message(
        chat_id=chat_id,
        text=f"🍅 **{minutes}분 완료!** 정말 잘 했어요!\n5분 쉬고 다시 시작해볼까요?",
        reply_markup=keyboard,
        parse_mode="Markdown",
    )
    context.bot_data.get("pomo_jobs", {}).pop(chat_id, None)
    intake_client = context.bot_data.get("intake_client")
    if intake_client:
        try:
            intake_client.log("포모도로", 1, 0, f"{minutes}분 완료")
        except Exception:
            pass


async def _handle_pomo_natural(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    """자연어 포모도로 타이머 시작"""
    chat_id = update.message.chat_id
    pomo_jobs = context.bot_data.setdefault("pomo_jobs", {})
    if chat_id in pomo_jobs and not pomo_jobs[chat_id].removed:
        await update.message.reply_text("⏱️ 지금 집중 중이에요! 완료 후 다시 시작해주세요.")
        return

    t = text.lower()
    minutes = 25
    import re as _re
    m = _re.search(r'(\d+)\s*분', t)
    if m:
        minutes = int(m.group(1))
    minutes = max(1, min(120, minutes))  # 1~120분 범위 제한

    await update.message.reply_text(f"🍅 **{minutes}분 집중 시작!** 화이팅! 🔥", parse_mode="Markdown")
    job = context.job_queue.run_once(
        pomo_done_callback,
        when=minutes * 60,
        chat_id=chat_id,
        data={"minutes": minutes},
    )
    pomo_jobs[chat_id] = job
    if _intake:
        try:
            _intake.log("포모도로", 1, 0, f"{minutes}분 시작")
        except Exception:
            pass


async def _handle_breakdown(update: Update, text: str):
    """과제를 ADHD 친화적 단계로 분해 후 투두 등록"""
    await update.message.chat.send_action("typing")
    prompt = (
        f'사용자가 시작하기 어려운 과제: "{text}"\n\n'
        "ADHD 친화적 과제 분해:\n"
        "- 각 단계는 10분 이내 완료 가능\n"
        "- 5-7개의 구체적 액션 아이템\n"
        "- 첫 단계는 특히 쉽고 작게 (시작의 장벽 낮추기)\n"
        "JSON 배열만 반환: [\"단계1\", \"단계2\", ...]"
    )
    try:
        raw = claude_client.chat("secretary", prompt)
        import re as _re, json as _json
        m = _re.search(r'\[.*?\]', raw, re.DOTALL)
        steps = _json.loads(m.group()) if m else [s.strip("•- ") for s in raw.split('\n') if s.strip()][:7]
    except Exception:
        steps = []

    if not steps:
        await update.message.reply_text("과제를 분해하지 못했어요. 좀 더 구체적으로 말씀해주세요.")
        return

    if _todos:
        for step in steps:
            step_text = step.lstrip("0123456789.-) ").strip()
            if step_text:
                _todos.add(TodoItem(text=step_text))

    lines = ["📋 **과제를 쪼갰어요!** 투두에 추가됐어요.\n"]
    for i, step in enumerate(steps, 1):
        lines.append(f"{i}. {step.lstrip('0123456789.-) ').strip()}")
    lines.append("\n첫 번째 것부터 시작해봐요! 💪  `/todos`")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def _handle_checkin_response(update: Update, text: str, checkin_type: str):
    """오전/오후 체크인 응답 처리"""
    kst_str = _now_kst().strftime("%Y-%m-%d %H:%M")
    try:
        parsed = claude_client.parse_checkin_response(text)
    except Exception:
        parsed = {"focus": "unknown", "note": text}

    focus = parsed.get("focus", "unknown")
    sleep_h = parsed.get("sleep_hours")
    water = parsed.get("water_glasses")

    # 기록
    if _intake:
        try:
            if sleep_h is not None:
                _intake.log("수면", int(sleep_h), 0, f"{checkin_type}체크인")
            if water is not None:
                _intake.log("음수", int(water), 0, f"{checkin_type}체크인")
            _intake.log("집중체크인", 1, 0, focus)
        except Exception:
            pass

    # 응답 메시지
    if focus == "good":
        reply = "👍 좋아요! 지금 집중 모드 유지해봐요. 포모도로 시작할까요? ('포모도로 시작')"
    elif focus == "bad":
        reply = "괜찮아요 🌱 지금 가장 작은 할 일 하나만 골라볼까요? ('투두 보여줘')"
    else:
        reply = "알겠어요! 기록해뒀어요 📝"
    if water is not None and int(water) < 3:
        reply += "\n💧 물 조금 더 챙겨요!"
    await update.message.reply_text(reply)


async def send_daily_checkin(bot, chat_id: int, checkin_type: str):
    """체크인 메시지 발송 (scheduler에서 호출)"""
    _pending_checkin[chat_id] = checkin_type
    if checkin_type == "morning":
        text = (
            "🧠 **오전 체크인!**\n\n"
            "아래 내용 간단히 알려줘:\n"
            "1️⃣ 어젯밤 몇 시간 잤어?\n"
            "2️⃣ 물 몇 잔 마셨어?\n"
            "3️⃣ 지금 집중 상태는? (좋음/보통/힘듦)"
        )
    else:
        text = (
            "🧠 **오후 체크인!**\n\n"
            "지금 상태 알려줘:\n"
            "1️⃣ 오늘 물 총 몇 잔?\n"
            "2️⃣ 집중 상태는? (좋음/보통/힘듦)"
        )
    await bot.send_message(chat_id=chat_id, text=text, parse_mode="Markdown")
