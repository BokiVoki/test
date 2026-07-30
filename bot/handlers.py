import copy
import os
import re
import uuid as uuid_module
from datetime import date, datetime, timezone, timedelta
from typing import Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
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
from . import haru_app
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
_undo_state: dict[str, dict] = {}  # key → undo payload (in-memory, TTL 없음)
_pending_snooze: dict[int, str] = {}  # chat_id → reminder_id (직접입력 대기 중)
_pending_archive_memo: dict[int, str] = {}   # chat_id → entry_id (메모 입력 대기)
_pending_archive_rate: dict[int, str] = {}   # chat_id → entry_id (평점 입력 대기)
_pending_search: dict[int, bool] = {}        # chat_id → 검색 키워드 입력 대기
_pending_add_entry: dict[int, dict] = {}     # chat_id → {step, title, type, author, year}
_processed_msg_ids: set[int] = set()  # 웹훅 재전송 중복 방지
_last_selected_entry_id: Optional[str] = None  # 최근 선택한 작품 ID


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
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=PERSISTENT_KEYBOARD)


async def help_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _auth(update):
        return
    text = (
        "**📌 전체 기능 안내**\n\n"

        "**🔀 모드 전환**\n"
        "/secretary — 비서 (기본)\n"
        "/finance — 금융·재무 상담\n"
        "/consultant — 전략·의사결정\n"
        "/instagram — 인스타그램 마케팅팀\n"
        "/mode — 현재 모드 확인\n\n"

        "**📚 콘텐츠 아카이브** (비서 모드)\n"
        "• `소로 레벨링 87화 읽었어`\n"
        "• `무빙 다 봤어 9점` / `파친코 추가해줘`\n"
        "• `판타지 웹툰 추천해줘` / `이번달에 뭐 봤더라`\n"
        "/list [필터] · /stats · /get [제목] · /export\n\n"

        "**📋 투두 & 알람**\n"
        "• `투두 청소하기` — 알람 없는 투두\n"
        "• `투두 약 먹기 매일 9시` — 반복 알람\n"
        "• `투두 청소, 빨래, 장보기` — 여러 개 동시 등록\n"
        "• `투두 물 마시기 2시간마다` — 완료 후 2시간 뒤 재알람\n"
        "• `미완료된 거 뭐 있어` — 목록 조회\n"
        "/todos · /remind · /reminders · /cancel_reminder [ID]\n"
        "/migrate_reminders — 기존 리마인더 → 투두 이전\n\n"

        "**💊 영양제·약 재고**\n"
        "• `비타민C 먹었어` — 복용 기록 + 재고 차감\n"
        "• `셀레늄 얼마나 남았어` — 재고 조회\n"
        "• `오메가3 60정 추가해줘` — 신규 등록\n"
        "• `비타민C 120정 새로 샀어` — 보충(재입고)\n"
        "/inventory · /intake · /setup_supplements\n\n"

        "**🌸 생리주기**\n"
        "• `생리 시작했어` / `생리 끝났어`\n"
        "• `지금 몇 기야` / `다음 생리 언제야`\n"
        "/cycle\n\n"

        "**🧠 ADHD 지원**\n"
        "• `25분 집중할게` — 포모도로 타이머\n"
        "• `발표 준비 어떻게 시작해` — 과제 분해 → 투두 생성\n"


        "**📣 자동 브리핑**\n"
        "09:00 날씨 + 오늘 할 일\n"
        "18:00 오늘 남은 일\n"
        "23:00 오늘 남은 일 + 내일 할 일\n\n"

        "**📝 메모**\n"
        "• `기록해줘` — 현재 대화 요약 저장\n"
        "• `이전에 말한 거 기억해?` — 저장 메모 자동 참고\n"
        "/memos · /memo_del [ID]\n\n"

        "**📱 인스타그램 팀**\n"
        "/designer · /writer · /igmanager\n"
        "에이전트별 대화 내용 중요 결정 자동 저장\n\n"

        "**기타**\n"
        "/import_archive — CSV 일괄 가져오기\n"
        "/clear_reminders — 리마인더 시트 초기화"
    )
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=PERSISTENT_KEYBOARD)


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


# 검색 결과 임시 저장 (번호 선택용)
_search_results: list = []

PERSISTENT_KEYBOARD = ReplyKeyboardMarkup(
    [[KeyboardButton("🔍 검색"), KeyboardButton("🕐 최근검색"), KeyboardButton("➕ 작품추가")],
     [KeyboardButton("📋 할일"), KeyboardButton("💊 영양제"), KeyboardButton("📊 통계")]],
    resize_keyboard=True,
    is_persistent=True,
)

_TYPE_KEYBOARD = InlineKeyboardMarkup([
    [InlineKeyboardButton("📚 책", callback_data="addtype:book"),
     InlineKeyboardButton("📖 웹소설", callback_data="addtype:webnovel"),
     InlineKeyboardButton("🎨 웹툰", callback_data="addtype:webtoon")],
    [InlineKeyboardButton("🖼 만화", callback_data="addtype:manga"),
     InlineKeyboardButton("🎬 영화", callback_data="addtype:movie"),
     InlineKeyboardButton("📺 드라마", callback_data="addtype:drama")],
    [InlineKeyboardButton("🎌 애니", callback_data="addtype:anime"),
     InlineKeyboardButton("🖼 그래픽북", callback_data="addtype:graphic_book"),
     InlineKeyboardButton("🎞 다큐", callback_data="addtype:documentary")],
    [InlineKeyboardButton("🎪 전시", callback_data="addtype:exhibition"),
     InlineKeyboardButton("🎙 팟캐스트", callback_data="addtype:podcast"),
     InlineKeyboardButton("🗂 기타", callback_data="addtype:other")],
])


def _archive_action_keyboard(entry_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📝 메모", callback_data=f"arc:memo:{entry_id}"),
         InlineKeyboardButton("📖 상세", callback_data=f"arc:detail:{entry_id}")],
        [InlineKeyboardButton("✅ 완료", callback_data=f"arc:done:{entry_id}"),
         InlineKeyboardButton("⭐ 평점", callback_data=f"arc:rate:{entry_id}")],
    ])


async def _do_search(update: Update, keyword: str):
    """키워드로 아카이브 검색 후 인라인 버튼으로 출력"""
    global _search_results
    from .models import CONTENT_TYPE_KR, STATUS_KR
    all_entries = _sheets.get_all_entries()
    matches = [e for e in all_entries if keyword.lower() in e.title.lower()]
    if not matches:
        await update.message.reply_text(f"'{keyword}' 포함된 작품을 찾지 못했어요.")
        return
    _search_results = matches
    lines = [f"**🔍 '{keyword}' 검색 결과 {len(matches)}개**\n"]
    buttons = []
    for i, e in enumerate(matches, 1):
        type_kr = CONTENT_TYPE_KR.get(e.type, e.type)
        status_kr = STATUS_KR.get(e.status, e.status)
        rating_str = f" ⭐{e.rating}" if e.rating is not None else ""
        lines.append(f"{i}. {e.title} ({type_kr} · {status_kr}{rating_str})")
        label = f"{i}. {e.title[:25]}{'…' if len(e.title) > 25 else ''}"
        buttons.append([InlineKeyboardButton(label, callback_data=f"arc:sel:{e.id}")])
    markup = InlineKeyboardMarkup(buttons)
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown", reply_markup=markup)


async def search_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/search [키워드] — 제목 검색 후 번호로 선택"""
    if not _auth(update):
        return
    if not context.args:
        await update.message.reply_text("`/search 달리기` 처럼 키워드를 입력해주세요.", parse_mode="Markdown")
        return
    await _do_search(update, " ".join(context.args))


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

def _is_important(text: str) -> bool:
    """텍스트에 (중요) 또는 [중요] 포함 여부"""
    t = text.lower()
    return "(중요)" in t or "[중요]" in t or "!!" in t


def _fmt_todo_line(item, index: int = None, show_id: bool = False) -> str:
    """투두 항목 한 줄 포맷 — 중요 항목은 🍎 prefix"""
    important = _is_important(item.text)
    prefix = "🍎 " if important else ""
    num = f"{index}. " if index is not None else "• "
    alarm_str = ""
    if item.trigger_at:
        try:
            dt = datetime.fromisoformat(item.trigger_at)
            alarm_str = f" ⏰{dt.strftime('%m/%d %H:%M')}"
        except Exception:
            alarm_str = " ⏰"
    id_str = f"  `{item.id}`" if show_id else ""
    return f"{num}{prefix}{item.text}{_fmt_due(item.due_date)}{alarm_str}{id_str}"


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
    # 중요 항목 먼저
    important = [t for t in pending if _is_important(t.text)]
    normal = [t for t in pending if not _is_important(t.text)]
    sorted_items = important + normal
    lines = ["**📋 할 일 목록**\n"]
    for i, item in enumerate(sorted_items, 1):
        lines.append(_fmt_todo_line(item, index=i, show_id=True))
    lines.append("\n완료: `완료 3` (번호) 또는 `완료 [내용]` 또는 `/todo_done [ID]`")
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

    # ── 빠른 경로: "완료 N" 또는 "N 완료" 패턴 — Claude 파싱 불필요
    import re as _re
    _quick_match = _re.fullmatch(r'[완료\s\d,]+', text.strip())
    _num_complete = _re.match(r'^(?:완료\s*)?([\d,\s]+)(?:\s*완료)?$', text.strip())
    if _num_complete and '완료' in text:
        nums = [int(n) for n in _re.findall(r'\d+', text)]
        if nums:
            pending = _todos.get_pending()
            imp = [t for t in pending if _is_important(t.text)]
            norm = [t for t in pending if not _is_important(t.text)]
            sorted_pending = imp + norm
            completed_names, not_found = [], []
            for n in nums:
                if 1 <= n <= len(sorted_pending):
                    item = sorted_pending[n - 1]
                    _todos.complete(item.id)
                    completed_names.append(item.text)
                else:
                    not_found.append(str(n))
            lines = [f"✅ **{t}** 완료!" for t in completed_names]
            if not_found:
                lines.append(f"⚠️ {', '.join(not_found)}번 항목을 찾지 못했어요.")
            await update.message.reply_text("\n".join(lines) or "완료할 항목이 없어요.", parse_mode="Markdown")
            return

    kst = timezone(timedelta(hours=9))
    _now_dt = datetime.now(kst)
    _DAY_KR = ["월", "화", "수", "목", "금", "토", "일"]
    now_str = _now_dt.strftime("%Y-%m-%d(%H:%M) ") + _DAY_KR[_now_dt.weekday()] + "요일"
    try:
        parsed = claude_client.parse_todo(text, now_str)
    except Exception as e:
        await update.message.reply_text(f"이해하지 못했어요. (`{e}`)")
        return

    action = parsed.get("action", "add")
    items_data = parsed.get("items", [])
    # 하위 호환
    if not items_data:
        items_data = [{"text": parsed.get("text", text), "due_date": parsed.get("due_date"), "trigger_at": parsed.get("trigger_at"), "repeat": parsed.get("repeat", "none")}]

    def _first_text():
        return (items_data[0].get("text") or text).strip() if items_data else text.strip()

    try:
        if action == "list":
            await todos_handler(update, None)

        elif action == "add":
            await update.message.chat.send_action("typing")
            added = []
            for item_data in items_data:
                todo_text = (item_data.get("text") or "").strip()
                if not todo_text:
                    continue
                due_date = item_data.get("due_date") or ""
                trigger_at = item_data.get("trigger_at") or ""
                repeat = item_data.get("repeat") or "none"
                item = TodoItem(text=todo_text, due_date=due_date, trigger_at=trigger_at, repeat=repeat)
                _todos.add(item)
                added.append(item)

            if not added:
                await update.message.reply_text("추가할 항목을 인식하지 못했어요.")
                return

            if len(added) == 1:
                item = added[0]
                key, markup = _undo_keyboard("↩️ 취소")
                _undo_state[key] = {"type": "delete_todo", "todo_id": item.id}
                rep_label = {"daily": " (매일)", "weekly": " (매주)", "monthly": " (매달)"}.get(item.repeat, "")
                if item.repeat.startswith("after:"):
                    try:
                        mins = int(item.repeat.split(":")[1])
                    except (IndexError, ValueError):
                        mins = 60
                    rep_label = f" ({_fmt_interval(mins)}마다)"
                if item.trigger_at:
                    try:
                        dt = datetime.fromisoformat(item.trigger_at)
                        time_label = dt.strftime("%m/%d %H:%M")
                    except Exception:
                        time_label = item.trigger_at
                    await update.message.reply_text(
                        f"⏰ **{item.text}** — {time_label}에 알림{rep_label}",
                        parse_mode="Markdown", reply_markup=markup
                    )
                else:
                    due_str = _fmt_due(item.due_date)
                    await update.message.reply_text(
                        f"📋 **{item.text}** 추가했어요!{due_str}",
                        parse_mode="Markdown", reply_markup=markup
                    )
            else:
                # 여러 개: 목록으로 출력
                lines = [f"📋 **{len(added)}개** 투두 추가했어요!\n"]
                for item in added:
                    rep_label = {"daily": " 매일", "weekly": " 매주", "monthly": " 매달"}.get(item.repeat, "")
                    if item.repeat.startswith("after:"):
                        try:
                            mins = int(item.repeat.split(":")[1])
                        except (IndexError, ValueError):
                            mins = 60
                        rep_label = f" {_fmt_interval(mins)}마다"
                    if item.trigger_at:
                        try:
                            dt = datetime.fromisoformat(item.trigger_at)
                            time_label = f" — {dt.strftime('%m/%d %H:%M')}"
                        except Exception:
                            time_label = f" — {item.trigger_at}"
                    else:
                        time_label = _fmt_due(item.due_date)
                    lines.append(f"• {item.text}{time_label}{rep_label}")
                await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

        elif action == "complete":
            todo_text = _first_text()
            # 숫자 기반 완료: "완료 3" 또는 "1, 3" 등
            import re as _re
            nums = [int(n) for n in _re.findall(r'\d+', todo_text)] if todo_text else []

            def _complete_or_reschedule(item) -> str:
                """반복 투두면 재스케줄, 아니면 done 처리. 결과 메시지 반환"""
                if item.repeat in ("daily", "weekly", "monthly"):
                    next_t = _next_trigger_manual(item.trigger_at, item.repeat)
                    # 먼저 done=1 표시 후 trigger_at만 업데이트 (reschedule은 done=0 리셋하므로 직접 처리)
                    _todos.complete(item.id)  # done=1
                    _todos.set_trigger(item.id, next_t.strftime("%Y-%m-%dT%H:%M:%S"))  # trigger_at 갱신, done 건드리지 않음
                    repeat_kr = {"daily": "내일", "weekly": "다음 주", "monthly": "다음 달"}[item.repeat]
                    return f"✅ **{item.text}** 완료! ({repeat_kr} {next_t.strftime('%H:%M')} 재알람)"
                elif item.repeat.startswith("after:"):
                    try:
                        minutes = int(item.repeat.split(":")[1])
                    except (IndexError, ValueError):
                        minutes = 60
                    next_t = (_now_kst() + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%S")
                    _todos.reschedule(item.id, next_t)
                    return f"✅ **{item.text}** 완료! ({_fmt_interval(minutes)} 후 재알람)"
                else:
                    _todos.complete(item.id)
                    return f"✅ **{item.text}** 완료!"

            if nums:
                pending = _todos.get_pending()
                imp = [t for t in pending if _is_important(t.text)]
                norm = [t for t in pending if not _is_important(t.text)]
                sorted_pending = imp + norm
                lines = []
                not_found = []
                for n in nums:
                    if 1 <= n <= len(sorted_pending):
                        lines.append(_complete_or_reschedule(sorted_pending[n - 1]))
                    else:
                        not_found.append(str(n))
                if not_found:
                    lines.append(f"⚠️ {', '.join(not_found)}번 항목을 찾지 못했어요.")
                await update.message.reply_text("\n".join(lines) or "완료할 항목이 없어요.", parse_mode="Markdown")
            else:
                item = _todos.find_by_text(todo_text)
                if not item:
                    await update.message.reply_text(f"'{todo_text}'을(를) 찾지 못했어요. `/todos`로 목록 확인해주세요.", parse_mode="Markdown")
                    return
                await update.message.reply_text(_complete_or_reschedule(item), parse_mode="Markdown")

        elif action == "delete":
            todo_text = _first_text()
            item = _todos.find_by_text(todo_text)
            if not item:
                await update.message.reply_text(f"'{todo_text}'을(를) 찾지 못했어요.")
                return
            _todos.delete(item.id)
            await update.message.reply_text(f"🗑 **{item.text}** 삭제했어요.", parse_mode="Markdown")

        elif action == "cancel_alarms":
            cancelled = _todos.cancel_all_alarms()
            key, markup = _undo_keyboard("↩️ 되돌리기")
            _undo_state[key] = {"type": "restore_alarms", "alarms": cancelled}
            n = len(cancelled)
            await update.message.reply_text(
                f"🔕 알람 {n}개를 해제했어요. (투두 항목은 유지됩니다)",
                reply_markup=markup,
            )

    except Exception as e:
        await update.message.reply_text(f"❌ 오류: {e}")


async def _do_record(update: Update, raw: str):
    """콤마 구분 문자열로 아카이브 등록"""
    parts = [p.strip() for p in raw.split(",")]
    title = parts[0] if len(parts) > 0 else ""
    author = parts[1] if len(parts) > 1 else ""
    year_watched = parts[2] if len(parts) > 2 else ""
    publisher = parts[3] if len(parts) > 3 else ""
    if not title:
        await update.message.reply_text("제목을 입력해주세요.")
        return
    # 타입 자동 추론 (간단 키워드)
    t = title.lower()
    if any(k in t for k in ("웹툰", "만화", "manhwa", "comic")):
        ctype = "webtoon"
    elif any(k in t for k in ("영화", "film", "movie")):
        ctype = "movie"
    elif any(k in t for k in ("드라마", "시즌", "ep.", "시리즈")):
        ctype = "drama"
    else:
        ctype = "book"
    today = str(date.today())
    new_entry = ContentEntry(
        title=title,
        type=ctype,
        status="in_progress",
        author=author,
        year_watched=year_watched,
        publisher=publisher,
        date_added=today,
    )
    added = _sheets.add_entry(new_entry)
    type_kr = CONTENT_TYPE_KR.get(added.type, added.type)
    meta = []
    if added.author: meta.append(f"작가: {added.author}")
    if added.year_watched: meta.append(f"{added.year_watched}년")
    if added.publisher: meta.append(added.publisher)
    meta_str = " · ".join(meta) if meta else ""
    await update.message.reply_text(
        f"✅ **{added.title}** ({type_kr}) 등록했어요!\n{meta_str}\n\n평점이나 한줄평도 남길까요?",
        parse_mode="Markdown"
    )


async def record_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/record 제목, 작가, 연도, 출판사"""
    if not _auth(update):
        return
    if not context.args:
        await update.message.reply_text(
            "`/기록 제목, 작가, 연도, 출판사` 형식으로 입력해주세요.\n"
            "예: `/기록 말론 죽다, 사뮈엘 베케트, 2026, 워크룸`\n"
            "작가/연도/출판사는 생략 가능해요.",
            parse_mode="Markdown"
        )
        return
    await _do_record(update, " ".join(context.args))


async def export_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _auth(update):
        return
    sheet_id = os.getenv("SPREADSHEET_ID", "")
    if sheet_id:
        url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/edit"
        await update.message.reply_text(f"[Google Sheets 열기]({url})", parse_mode="Markdown")
    else:
        await update.message.reply_text("SPREADSHEET_ID 환경변수가 설정되지 않았어요.")


async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """사진 메시지 → Google Drive 업로드 → 선택된 작품 메모에 링크 저장"""
    if not _auth(update):
        return
    from . import drive_client as _drive

    if not _drive.is_configured():
        await update.message.reply_text(
            "⚠️ Google Drive 연동이 설정되지 않았어요.\n"
            "`GOOGLE_CREDENTIALS_PATH` 환경변수를 확인해주세요.",
            parse_mode="Markdown"
        )
        return

    chat_id = update.message.chat_id
    # 메모 대기 중 항목 우선, 없으면 최근 선택 작품
    entry_id = _pending_archive_memo.pop(chat_id, None) or _last_selected_entry_id
    if not entry_id:
        await update.message.reply_text(
            "📎 어느 작품에 첨부할까요?\n"
            "먼저 🔍 검색으로 작품을 선택한 뒤 📝 메모 버튼을 눌러주세요."
        )
        return

    await update.message.chat.send_action("upload_photo")

    # 가장 큰 해상도 사진
    photo = update.message.photo[-1]
    tg_file = await context.bot.get_file(photo.file_id)
    file_bytes = bytes(await tg_file.download_as_bytearray())

    stamp = _now_kst().strftime("%Y%m%d_%H%M%S")
    try:
        drive_url = _drive.upload_photo(file_bytes, f"memo_{stamp}.jpg")
    except Exception as e:
        logger.error(f"Drive upload error: {e}")
        await update.message.reply_text(f"❌ 업로드 실패: {e}")
        return

    # 작품 찾기
    entry = next((e for e in _search_results if e.id == entry_id), None)
    if entry is None and _sheets:
        all_entries = _sheets.get_all_entries()
        entry = next((e for e in all_entries if e.id == entry_id), None)

    if not entry:
        await update.message.reply_text(f"⚠️ 작품을 찾지 못했어요. 링크만 저장됐어요:\n{drive_url}")
        return

    date_stamp = _now_kst().strftime("%m/%d")
    caption = update.message.caption or ""
    note_line = f"[{date_stamp}] 📷 {drive_url}" + (f" — {caption}" if caption else "")
    entry.notes = f"{entry.notes}\n{note_line}".strip() if entry.notes else note_line
    _sheets.update_entry(entry)

    await update.message.reply_text(
        f"📷 **{entry.title}** 사진 저장했어요!\n"
        f"[Drive에서 보기]({drive_url})",
        parse_mode="Markdown",
        reply_markup=_archive_action_keyboard(entry_id),
    )


async def migrate_archive_fields_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/migrate_archive_fields — notes에 섞인 참여:/연도:/출판: 을 전용 열로 분리 (배치처리)"""
    if not _auth(update):
        return
    import re as _re
    await update.message.reply_text("⏳ 아카이브 마이그레이션 시작...")
    entries = _sheets.get_all_entries(force=True)
    to_update = []
    for entry in entries:
        changed = False
        notes_lines = entry.notes.split("\n") if entry.notes else []
        remaining = []
        for line in notes_lines:
            m_author = _re.match(r'^참여\s*[:：]\s*(.+)', line.strip())
            m_year   = _re.match(r'^연도\s*[:：]\s*(.+)', line.strip())
            m_pub    = _re.match(r'^출판\s*[:：]\s*(.+)', line.strip())
            if m_author and not entry.author:
                entry.author = m_author.group(1).strip()
                changed = True
            elif m_year and not entry.year_watched:
                entry.year_watched = m_year.group(1).strip()
                changed = True
            elif m_pub and not entry.publisher:
                entry.publisher = m_pub.group(1).strip()
                changed = True
            else:
                remaining.append(line)
        if changed:
            entry.notes = "\n".join(remaining).strip()
            to_update.append(entry)
    # 한 번의 batch API 호출로 전체 업데이트
    updated = _sheets.batch_update_entries(to_update)
    await update.message.reply_text(
        f"✅ 마이그레이션 완료!\n{updated}개 항목에서 참여/연도/출판 분리했어요.\n\n"
        "Google Sheets N열(author), O열(year\\_watched), P열(publisher) 헤더를 추가해주세요.",
        parse_mode="Markdown"
    )


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

    # ── 작품 종류 선택 버튼 ──
    if data.startswith("addtype:"):
        chosen_type = data.split(":", 1)[1]
        chat_id = query.message.chat_id
        if chat_id not in _pending_add_entry:
            await query.answer("세션이 만료됐어요. 다시 ➕ 작품추가를 눌러주세요.", show_alert=True)
            return
        _pending_add_entry[chat_id]["type"] = chosen_type
        _pending_add_entry[chat_id]["step"] = "author"
        type_kr = CONTENT_TYPE_KR.get(chosen_type, chosen_type)
        title = _pending_add_entry[chat_id]["title"]
        await query.edit_message_text(
            f"📖 **{title}** ({type_kr})\n\n✏️ 작가/감독/원작자를 입력해주세요 (없으면 '-'):",
            parse_mode="Markdown",
        )
        return

    # ── 포모도로 버튼 ──
    if data.startswith("pomo:"):
        action = data[5:]
        if action == "done":
            await query.edit_message_text("🔚 오늘 집중 수고했어요! 💪")
        elif action == "restart":
            await query.edit_message_text("🍅 새 포모도로를 시작하려면 '포모도로 시작' 이라고 보내주세요!")
        return

    if data.startswith("undo:"):
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

        elif t == "restore_alarms":
            alarms = state.get("alarms", [])
            _todos.restore_alarms(alarms)
            await query.edit_message_text(f"↩️ 알람 {len(alarms)}개를 복구했어요!")

    # ── 아카이브 검색 결과 버튼 ──
    elif data.startswith("arc:"):
        parts = data.split(":", 2)
        if len(parts) < 3:
            return
        arc_action, entry_id = parts[1], parts[2]
        entry = next((e for e in _search_results if e.id == entry_id), None)
        if entry is None and _sheets:
            all_entries = _sheets.get_all_entries()
            entry = next((e for e in all_entries if e.id == entry_id), None)

        if arc_action == "sel":
            if not entry:
                await query.edit_message_text("항목을 찾지 못했어요. 다시 검색해주세요.")
                return
            global _last_selected_entry_id
            _last_selected_entry_id = entry_id
            type_kr = CONTENT_TYPE_KR.get(entry.type, entry.type)
            status_kr = STATUS_KR.get(entry.status, entry.status)
            rating_str = f" ⭐{entry.rating}" if entry.rating is not None else ""
            author_str = f"\n작가: {entry.author}" if entry.author else ""
            msg = f"**{entry.title}**{author_str}\n{type_kr} · {status_kr}{rating_str}"
            await query.edit_message_text(msg, parse_mode="Markdown",
                                          reply_markup=_archive_action_keyboard(entry_id))

        elif arc_action == "detail":
            if not entry:
                await query.answer("항목을 찾지 못했어요.", show_alert=True)
                return
            await query.edit_message_text(_format_entry_detail(entry), parse_mode="Markdown",
                                          reply_markup=_archive_action_keyboard(entry_id))

        elif arc_action == "done":
            if not entry:
                await query.answer("항목을 찾지 못했어요.", show_alert=True)
                return
            entry.status = "completed"
            if not entry.date_completed:
                entry.date_completed = str(date.today())
            _sheets.update_entry(entry)
            await query.edit_message_text(f"✅ **{entry.title}** 완료 처리했어요!", parse_mode="Markdown")

        elif arc_action == "memo":
            if not entry:
                await query.answer("항목을 찾지 못했어요.", show_alert=True)
                return
            chat_id = query.message.chat_id
            _pending_archive_memo[chat_id] = entry_id
            await query.edit_message_text(
                f"📝 **{entry.title}** 메모를 입력해주세요:",
                parse_mode="Markdown"
            )

        elif arc_action == "rate":
            if not entry:
                await query.answer("항목을 찾지 못했어요.", show_alert=True)
                return
            chat_id = query.message.chat_id
            _pending_archive_rate[chat_id] = entry_id
            rate_buttons = [
                [InlineKeyboardButton(f"⭐{r}", callback_data=f"arc:rate_val:{entry_id}:{r}")
                 for r in [1, 2, 3, 4, 5]],
                [InlineKeyboardButton(f"⭐{r}", callback_data=f"arc:rate_val:{entry_id}:{r}")
                 for r in [6, 7, 8, 9, 10]],
            ]
            await query.edit_message_text(
                f"⭐ **{entry.title}** 평점을 선택해주세요 (1~10):",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(rate_buttons)
            )

        elif arc_action == "rate_val":
            sub = data.split(":")
            if len(sub) < 4:
                return
            entry_id2, rating_val = sub[2], sub[3]
            entry2 = next((e for e in _search_results if e.id == entry_id2), None)
            if entry2 is None and _sheets:
                all_entries = _sheets.get_all_entries()
                entry2 = next((e for e in all_entries if e.id == entry_id2), None)
            if entry2 and _sheets:
                try:
                    entry2.rating = float(rating_val)
                    _sheets.update_entry(entry2)
                    await query.edit_message_text(
                        f"⭐ **{entry2.title}** 평점 {entry2.rating} 저장했어요!",
                        parse_mode="Markdown",
                        reply_markup=_archive_action_keyboard(entry_id2)
                    )
                except Exception:
                    await query.answer("저장 실패", show_alert=True)


def _now_kst() -> datetime:
    return datetime.now(timezone(timedelta(hours=9))).replace(tzinfo=None)


def _parse_trigger_dt(trigger_at: str) -> datetime:
    """trigger_at 문자열 파싱 — 공백/T 구분자, 한 자리 시각 모두 처리"""
    import re as _re
    s = trigger_at.strip().replace(" ", "T")
    # "T9:00:00" → "T09:00:00" (한 자리 시간 패딩)
    s = _re.sub(r'T(\d):', r'T0\1:', s)
    return datetime.fromisoformat(s)


def _next_trigger_manual(trigger_at: str, repeat: str) -> datetime:
    """수동 완료 시 다음 트리거 — 오늘 기준 다음 주기 같은 시각"""
    now = _now_kst()
    try:
        trigger = _parse_trigger_dt(trigger_at)
        t_time = trigger.time()  # 원래 시각 (HH:MM:SS) 보존
    except Exception:
        t_time = now.time()
    if repeat == "daily":
        next_date = now.date() + timedelta(days=1)
    elif repeat == "weekly":
        next_date = now.date() + timedelta(weeks=1)
    elif repeat == "monthly":
        import calendar as _cal
        y, m = now.year, now.month + 1
        if m > 12:
            y, m = y + 1, 1
        d = min(now.day, _cal.monthrange(y, m)[1])
        next_date = now.date().replace(year=y, month=m, day=d)
    else:
        next_date = now.date() + timedelta(days=1)
    return datetime.combine(next_date, t_time)


def _load_memo_context(mode: str, query: str = "", limit: int = 20) -> str:
    """
    Memos 시트에서 mode별 메모를 불러와 query와 관련된 것만 반환.
    query가 없거나 트리거 단어 없으면 빈 문자열 반환 (토큰 절약).
    """
    if _memos is None:
        return ""

    # 메모 참고가 필요한 트리거 단어
    MEMO_TRIGGER_WORDS = (
        "아까", "이전에", "저번에", "전에", "기억", "메모", "기록",
        "말했", "얘기했", "정했", "결정했", "확정", "했던", "봤던",
        "지난번", "지난달", "지난주", "어제", "예전",
    )
    q = query.lower()
    has_trigger = any(w in q for w in MEMO_TRIGGER_WORDS)

    # 트리거 없으면 메모 주입 스킵 (속도·비용 절약)
    if not has_trigger:
        return ""

    try:
        all_memos = _memos.get_by_mode(mode, limit=limit)
        if not all_memos:
            return ""

        # 키워드 관련성 점수: query 단어가 메모 내용에 몇 개 포함되는지
        query_words = [w for w in q.split() if len(w) > 1]
        if query_words:
            scored = []
            for m in all_memos:
                content_lower = m.content.lower()
                score = sum(1 for w in query_words if w in content_lower)
                scored.append((score, m))
            # 관련 있는 것 우선, 최근 3개
            scored.sort(key=lambda x: (-x[0], -all_memos.index(x[1])))
            relevant = [m for score, m in scored if score > 0][:3]
            if not relevant:
                # 관련 키워드 없으면 가장 최근 2개만
                relevant = all_memos[:2]
        else:
            relevant = all_memos[:3]

        lines = [f"[{m.created_at[:10]}] {m.content}" for m in relevant]
        return "\n---\n".join(lines)
    except Exception:
        return ""


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
            label = _fmt_interval(minutes)
            await query.edit_message_text(f"✅ 완료! {label} 후에 다시 알려드릴게요.")
        elif todo_item and todo_item.repeat in ("daily", "weekly", "monthly"):
            # 반복 투두 — 오늘 기준 다음 주기 같은 시각으로 재스케줄
            next_t = _next_trigger_manual(todo_item.trigger_at, todo_item.repeat)
            _todos.reschedule(todo_id, next_t.strftime("%Y-%m-%dT%H:%M:%S"))
            repeat_kr = {"daily": "내일", "weekly": "다음 주", "monthly": "다음 달"}[todo_item.repeat]
            await query.edit_message_text(f"✅ 완료! {repeat_kr} {next_t.strftime('%H:%M')}에 다시 알려드릴게요.")
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


async def _handle_instagram(update: Update, text: str, memo_context: str = ""):
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
    memo_mode = f"instagram_{agent}"  # Memos 시트에 저장할 mode 키

    # 메모 저장 요청 감지
    t = text.lower()
    if any(kw in t for kw in _MEMO_SAVE_KEYWORDS):
        remaining = text
        for kw in _MEMO_SAVE_KEYWORDS:
            remaining = remaining.replace(kw, "").replace(kw.replace("줘", ""), "")
        remaining = remaining.strip(" \n.,")
        await update.message.chat.send_action("typing")
        if len(remaining) > 20:
            content = remaining
        else:
            hist = _history.get(history_key, [])
            if not hist:
                await update.message.reply_text("저장할 내용이 없어요. 대화 후 기록해줘 해주세요.")
                return
            content = claude_client.summarize_conversation(hist[-10:], memo_mode)
        if content:
            await _save_memo(update, memo_mode, content)
        return

    await update.message.chat.send_action("typing")

    # memo_context: 상위에서 _load_memo_context(mode, query)로 전달받음
    # (트리거 단어 있을 때만 주입, 없으면 빈 문자열)

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
    reply = claude_client.instagram_chat(
        agent, text,
        history=_history[history_key][:-1],
        figma_context=figma_context,
        memo_context=memo_context,
    )
    _add_history(history_key, "assistant", reply)
    await update.message.reply_text(reply)

    # 자동 메모 저장: 중요 결정/피드백 감지 시 자동 저장
    if _memos is not None:
        try:
            if claude_client.detect_important_decision(reply):
                summary = claude_client.summarize_instagram_decision(reply, agent)
                if summary:
                    _memos.add(memo_mode, summary)
        except Exception:
            pass


async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global _current_mode
    if not _auth(update):
        return

    # 웹훅 재전송 중복 방지
    msg_id = update.message.message_id
    if msg_id in _processed_msg_ids:
        return
    _processed_msg_ids.add(msg_id)
    if len(_processed_msg_ids) > 500:  # 메모리 정리
        _processed_msg_ids.clear()

    text = update.message.text.strip()
    chat_id = update.message.chat_id

    # ── "앱 ..." → 하루 웹앱 주머니로 바로 던지기 ──
    # "앱" 뒤 공백(또는 줄바꿈) 다음의 모든 내용을 주머니(inbox)에 넣는다.
    if text[:1] == "앱" and len(text) > 1 and text[1] in (" ", "\n", "\t"):
        pocket_text = text[1:].strip()
        if not pocket_text:
            await update.message.reply_text("📥 주머니에 넣을 내용을 '앱' 뒤에 적어주세요. (예: 앱 사과 사기)")
            return
        title, due = parse_pocket_due(pocket_text, _now_kst().date())
        try:
            haru_app.add_to_pocket(title, due=due)
            msg = f"📥 주머니에 담았어요\n· {title}"
            if due:
                msg += f"\n📅 마감 {due[5:].replace('-', '/')}"
            await update.message.reply_text(msg)
        except Exception as e:
            await update.message.reply_text(f"❌ 주머니 저장 실패: {e}")
        return

    # ── "마감" → 하루앱 마감 임박 할일 즉시 확인 ──
    if text in ("마감", "마감?", "하루마감", "마감임박", "마감확인"):
        await _send_haru_deadlines(update)
        return

    # ── "추천/체크인" → 11시 하루앱 체크인(오늘 추천 + 마감) 즉시 확인 ──
    if text in ("추천", "오늘추천", "오늘뭐", "오늘뭐하지", "체크인", "하루체크인"):
        if haru_app.is_configured():
            await send_haru_daily(update.get_bot(), chat_id)
        else:
            await update.message.reply_text("하루앱 연결이 안 돼 있어요 (SUPABASE 환경변수 확인).")
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

    # ── 작품 추가 단계별 입력 대기 중 ──
    if chat_id in _pending_add_entry:
        state = _pending_add_entry[chat_id]
        step = state.get("step")

        if step == "title":
            state["title"] = text.strip()
            state["step"] = "type"
            await update.message.reply_text(
                f"📂 **{state['title']}** — 종류를 선택해주세요:",
                parse_mode="Markdown",
                reply_markup=_TYPE_KEYBOARD,
            )
            return

        elif step == "author":
            state["author"] = "" if text.strip() in ("-", "없음", "skip") else text.strip()
            state["step"] = "year"
            await update.message.reply_text(
                "📅 시작한 연도를 입력해주세요 (예: 2026 · 없으면 '-'):"
            )
            return

        elif step == "year":
            raw = text.strip()
            state["year"] = "" if raw in ("-", "없음", "skip") else raw
            # 저장
            del _pending_add_entry[chat_id]
            from .models import ContentEntry
            entry = ContentEntry(
                title=state["title"],
                type=state.get("type", "other"),
                status="in_progress",
                author=state.get("author", ""),
                year_watched=state.get("year", ""),
                date_added=str(date.today()),
            )
            _sheets.add_entry(entry)
            type_kr = CONTENT_TYPE_KR.get(entry.type, entry.type)
            author_str = f" · {entry.author}" if entry.author else ""
            year_str = f" ({entry.year_watched})" if entry.year_watched else ""
            await update.message.reply_text(
                f"✅ **{entry.title}**{year_str} 추가했어요!\n{type_kr}{author_str} · 읽는 중",
                parse_mode="Markdown",
                reply_markup=_archive_action_keyboard(entry.id),
            )
            return

    # ── 아카이브 메모 입력 대기 중 ──
    if chat_id in _pending_archive_memo:
        entry_id = _pending_archive_memo.pop(chat_id)
        entry = next((e for e in _search_results if e.id == entry_id), None)
        if entry is None and _sheets:
            # ID로 직접 조회
            all_entries = _sheets.get_all_entries()
            entry = next((e for e in all_entries if e.id == entry_id), None)
        if entry:
            stamp = _now_kst().strftime("%m/%d")
            new_note = f"[{stamp}] {text}"
            entry.notes = f"{entry.notes}\n{new_note}".strip() if entry.notes else new_note
            _sheets.update_entry(entry)
            await update.message.reply_text(
                f"📝 **{entry.title}** 메모 추가했어요!\n`{new_note}`",
                parse_mode="Markdown",
            )
        else:
            await update.message.reply_text("⚠️ 항목을 찾지 못했어요. 다시 검색해주세요.")
        return

    # ── 아카이브 평점 텍스트 입력 대기 중 ──
    if chat_id in _pending_archive_rate:
        entry_id = _pending_archive_rate.pop(chat_id)
        entry = next((e for e in _search_results if e.id == entry_id), None)
        if entry is None and _sheets:
            all_entries = _sheets.get_all_entries()
            entry = next((e for e in all_entries if e.id == entry_id), None)
        if entry:
            import re as _re_rate
            m = _re_rate.search(r'[\d.]+', text)
            if m:
                try:
                    entry.rating = float(m.group())
                    _sheets.update_entry(entry)
                    await update.message.reply_text(
                        f"⭐ **{entry.title}** 평점 {entry.rating} 저장했어요!",
                        parse_mode="Markdown",
                    )
                    return
                except ValueError:
                    pass
        await update.message.reply_text("⚠️ 숫자로 평점을 입력해주세요. 예: `8.5`", parse_mode="Markdown")
        return

    # ── 검색어 입력 대기 중 ──
    if chat_id in _pending_search:
        del _pending_search[chat_id]
        await _do_search(update, text)
        return

    # ── 영구 키보드 버튼 ──
    if text == "🔍 검색":
        _pending_search[chat_id] = True
        await update.message.reply_text("검색할 작품 제목(또는 키워드)을 입력해주세요:")
        return
    if text == "🕐 최근검색":
        if not _last_selected_entry_id:
            await update.message.reply_text("아직 선택한 작품이 없어요. 먼저 🔍 검색으로 작품을 찾아보세요.")
            return
        entry = next((e for e in _search_results if e.id == _last_selected_entry_id), None)
        if entry is None and _sheets:
            all_entries = _sheets.get_all_entries()
            entry = next((e for e in all_entries if e.id == _last_selected_entry_id), None)
        if not entry:
            await update.message.reply_text("최근 작품을 찾지 못했어요. 다시 검색해주세요.")
            return
        from .models import CONTENT_TYPE_KR, STATUS_KR
        type_kr = CONTENT_TYPE_KR.get(entry.type, entry.type)
        status_kr = STATUS_KR.get(entry.status, entry.status)
        rating_str = f" ⭐{entry.rating}" if entry.rating is not None else ""
        author_str = f"\n작가: {entry.author}" if entry.author else ""
        msg = f"**{entry.title}**{author_str}\n{type_kr} · {status_kr}{rating_str}"
        await update.message.reply_text(msg, parse_mode="Markdown",
                                        reply_markup=_archive_action_keyboard(_last_selected_entry_id))
        return
    if text == "➕ 작품추가":
        _pending_add_entry[chat_id] = {"step": "title"}
        await update.message.reply_text("📖 작품 제목을 입력해주세요:")
        return
    if text == "📋 할일":
        await todos_handler(update, context)
        return
    if text == "💊 영양제":
        await inventory_handler(update, context)
        return
    if text == "📊 통계":
        await stats_handler(update, context)
        return

    # ── 한글 커맨드 감지 (텔레그램 한글 커맨드 미지원 대비) ──
    import re as _re2
    _search_kw_m = _re2.match(r'^/?검색\s+(.+)', text.strip())
    if _search_kw_m:
        await _do_search(update, _search_kw_m.group(1).strip())
        return
    _record_m = _re2.match(r'^/?기록\s+(.+)', text.strip(), _re2.DOTALL)
    if _record_m:
        await _do_record(update, _record_m.group(1).strip())
        return

    # ── 검색 결과 번호 선택: "N 메모 - ...", "N 보여줘", "N 완료" ──
    def _apply_search_action(entry, action_text: str) -> bool | None:
        """검색 결과 항목에 액션 적용. 처리됐으면 True 반환"""
        _memo_m2 = _re2.match(r'^(?:메모\s*)?[-–]\s*(.*)', action_text, _re2.DOTALL)
        if _memo_m2 or action_text.startswith("메모"):
            raw = _memo_m2.group(1).strip() if _memo_m2 else _re2.sub(r'^메모\s*', '', action_text).strip()
            if raw:
                stamp = _now_kst().strftime("%m/%d")
                new_note = f"[{stamp}] {raw}"
                entry.notes = f"{entry.notes}\n{new_note}".strip() if entry.notes else new_note
                _sheets.update_entry(entry)
                return ("memo", new_note, entry.title)
        if any(kw in action_text for kw in ("보여줘", "보여", "상세", "알려줘")):
            return ("detail", entry)
        if "완료" in action_text:
            entry.status = "completed"
            from datetime import date as _date2
            if not entry.date_completed:
                entry.date_completed = str(_date2.today())
            _sheets.update_entry(entry)
            return ("done", entry.title)
        _rm = _re2.search(r'(?:평점|⭐)\s*([\d.]+)', action_text)
        if _rm:
            try:
                entry.rating = float(_rm.group(1))
                _sheets.update_entry(entry)
                return ("rate", entry.rating, entry.title)
            except ValueError:
                pass
        return None

    if _search_results:
        _sel = _re2.match(r'^(\d+)\s+(.*)', text.strip(), _re2.DOTALL)
        # 결과 1개이고 번호 없이 "메모 - ..." 형식
        _no_num_memo = _re2.match(r'^(?:메모\s*)?[-–]\s*(.*)', text.strip(), _re2.DOTALL) or text.strip().startswith("메모")
        if _sel:
            sel_n = int(_sel.group(1))
            sel_rest = _sel.group(2).strip()
            if 1 <= sel_n <= len(_search_results):
                result = _apply_search_action(_search_results[sel_n - 1], sel_rest)
                if result:
                    if result[0] == "memo":
                        await update.message.reply_text(f"📝 **{result[2]}** 메모 추가했어요!\n`{result[1]}`", parse_mode="Markdown")
                    elif result[0] == "detail":
                        await update.message.reply_text(_format_entry_detail(result[1]), parse_mode="Markdown")
                    elif result[0] == "done":
                        await update.message.reply_text(f"✅ **{result[1]}** 완료 처리했어요!", parse_mode="Markdown")
                    elif result[0] == "rate":
                        await update.message.reply_text(f"⭐ **{result[2]}** 평점 {result[1]} 저장했어요!", parse_mode="Markdown")
                    return
        elif _no_num_memo and len(_search_results) == 1:
            # 검색 결과 1개일 때 번호 생략 허용
            result = _apply_search_action(_search_results[0], text.strip())
            if result:
                if result[0] == "memo":
                    await update.message.reply_text(f"📝 **{result[2]}** 메모 추가했어요!\n`{result[1]}`", parse_mode="Markdown")
                elif result[0] == "detail":
                    await update.message.reply_text(_format_entry_detail(result[1]), parse_mode="Markdown")
                elif result[0] == "done":
                    await update.message.reply_text(f"✅ **{result[1]}** 완료 처리했어요!", parse_mode="Markdown")
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

    # 메모 컨텍스트 로드 (트리거 단어 있을 때만 관련 메모 주입)
    memo_context = _load_memo_context(mode, query=text)

    # 비서 모드
    if mode == "secretary":
        await _handle_secretary(update, context, text, memo_context=memo_context)
    elif mode == "instagram":
        await _handle_instagram(update, text, memo_context=memo_context)
    else:
        # 금융/컨설턴트 모드: Claude 대화
        await update.message.chat.send_action("typing")
        _add_history(mode, "user", text)
        reply = claude_client.chat(mode, text, memo_context=memo_context, history=_history[mode][:-1])
        _add_history(mode, "assistant", reply)
        await update.message.reply_text(reply)


_TODO_WORDS = (
    "투두", "할 일", "할일", "todo", "할거", "할 거",
    # 완료 처리
    "완료", "다했어", "다 했어", "끝냈어", "끝났어", "했어", "완성",
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
    # 신규 등록 / 보충
    "새로 샀", "새로샀", "샀어", "구매했", "추가해줘", "추가해 줘",
    "등록해줘", "등록해 줘", "보충", "재입고", "새로 생겼",
)
_CYCLE_KEYWORDS = (
    "생리", "월경", "여포기", "배란기", "황체기", "생리기",
    "주기", "생리 주기", "생리 언제", "생리 시작", "생리 끝",
    "몇 기야", "어느 단계", "다음 생리",
)
_POMO_KEYWORDS = ("포모도로", "집중 타이머", "집중 시작", "분 집중", "집중해야", "집중할게")
_BREAKDOWN_KEYWORDS = ("어떻게 시작", "뭐부터 해", "막막해", "쪼개줘", "단계별로", "분해해줘", "시작이 막막")


async def _handle_secretary(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, memo_context: str = ""):
    t = text.lower()

    # 투두/알람은 최우선 — "투두"가 있으면 다른 키워드보다 먼저 처리
    if any(w in t for w in _TODO_WORDS) or any(w in t for w in _REMINDER_WORDS):
        await _handle_todo_natural(update, text)
        return

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

    known_titles = _sheets.get_titles()
    intent = claude_client.parse_archive_message(text, known_titles)

    if intent.action == "unknown" or intent.confidence < 0.5:
        # 일반 비서 대화로 처리
        await update.message.chat.send_action("typing")
        _add_history("secretary", "user", text)
        reply = claude_client.chat("secretary", text, memo_context=memo_context, history=_history["secretary"][:-1])
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
            status=intent.status or "in_progress",
            progress=intent.progress or "",
            notes=intent.note or "",
            author=intent.author or "",
            year_watched=intent.year_watched or "",
            publisher=intent.publisher or "",
            date_added=today,
        )
        added = _sheets.add_entry(new_entry)
        type_kr = CONTENT_TYPE_KR.get(added.type, added.type)
        status_kr = STATUS_KR.get(added.status, added.status)
        key, markup = _undo_keyboard()
        _undo_state[key] = {"type": "delete", "entry_id": added.id}
        meta = []
        if added.author: meta.append(f"작가: {added.author}")
        if added.year_watched: meta.append(f"{added.year_watched}년")
        if added.publisher: meta.append(added.publisher)
        meta_str = " · ".join(meta)
        await update.message.reply_text(
            f"✅ **{added.title}** ({type_kr}) 추가했어요!\n{meta_str}",
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
        kst = _now_kst()
        stamp = kst.strftime("%m/%d")
        new_note = f"[{stamp}] {intent.note}"
        entry.notes = f"{entry.notes}\n{new_note}".strip() if entry.notes else new_note

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
    if entry.author:
        lines.append(f"참여: {entry.author}")
    if entry.publisher:
        lines.append(f"출판/제작: {entry.publisher}")
    if entry.year_watched:
        lines.append(f"감상연도: {entry.year_watched}")
    if entry.progress:
        lines.append(f"진행: {entry.progress}")
    if entry.rating is not None:
        lines.append(f"평점: ⭐{entry.rating}")
    if entry.tags:
        lines.append(f"태그: {entry.tags}")
    if entry.notes:
        note_lines = entry.notes.strip().split("\n")
        if len(note_lines) == 1:
            lines.append(f"메모: {entry.notes}")
        else:
            lines.append(f"메모 ({len(note_lines)}개):")
            for nl in note_lines[-5:]:
                lines.append(f"  • {nl}")
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



def _fmt_haru_items(items, limit=15):
    out = []
    for d in items[:limit]:
        pj = f" · {d.get('project')}" if d.get("project") else ""
        due = d.get("due") or ""
        dtxt = f" (~{due[5:10]})" if due else ""
        out.append(f"• {d.get('title','')}{pj}{dtxt}")
    extra = len(items) - limit
    if extra > 0:
        out.append(f"  … 외 {extra}개")
    return "\n".join(out)


def _haru_deadline_buckets(today, tomorrow):
    """하루앱 마감을 밀림/오늘/내일로 분류해서 (over, today, tomorrow) 튜플 반환."""
    ts, tos = today.isoformat(), tomorrow.isoformat()
    items = haru_app.list_due(tos)
    over = [d for d in items if (d.get("due") or "")[:10] < ts]
    td = [d for d in items if (d.get("due") or "")[:10] == ts]
    tm = [d for d in items if (d.get("due") or "")[:10] == tos]
    return over, td, tm


_WDAY_KR = ["월", "화", "수", "목", "금", "토", "일"]


def _resolve_md(today, mo, da):
    """월/일 → 가장 가까운 미래(또는 오늘)의 ISO 날짜. 이미 지났으면 내년."""
    if not (1 <= mo <= 12 and 1 <= da <= 31):
        return None
    for yr in (today.year, today.year + 1):
        try:
            cand = date(yr, mo, da)
        except ValueError:
            return None
        if cand >= today:
            return cand.isoformat()
    return None


def parse_pocket_due(text, today):
    """'앱 <내용> <날짜>' 에서 끝에 붙은 날짜를 떼어낸다.

    지원: 오늘/내일/모레/글피, 요일(월~일), M/D · M.D · M-D, M월 D일.
    반환: (내용, due_iso 또는 None). 날짜 못 찾으면 (원문, None).
    """
    s = (text or "").strip()

    kw = {"오늘": 0, "내일": 1, "모레": 2, "글피": 3}
    for k, off in kw.items():
        if s.endswith(k):
            title = s[: -len(k)].strip()
            if title:
                return title, (today + timedelta(days=off)).isoformat()

    m = re.search(r"(월|화|수|목|금|토|일)요일$", s)
    if m:
        title = s[: m.start()].strip()
        if title:
            target = _WDAY_KR.index(m.group(1))
            delta = (target - today.weekday()) % 7
            delta = delta or 7
            return title, (today + timedelta(days=delta)).isoformat()

    m = re.search(r"(\d{1,2})\s*월\s*(\d{1,2})\s*일?$", s)
    if m:
        title = s[: m.start()].strip()
        iso = _resolve_md(today, int(m.group(1)), int(m.group(2)))
        if title and iso:
            return title, iso

    m = re.search(r"(\d{1,2})[/.\-](\d{1,2})$", s)
    if m:
        title = s[: m.start()].strip()
        iso = _resolve_md(today, int(m.group(1)), int(m.group(2)))
        if title and iso:
            return title, iso

    return s, None


async def _send_haru_deadlines(update):
    """하루앱(Supabase) 마감 임박(밀림/오늘/내일) 할일을 즉시 보여준다."""
    if not haru_app.is_configured():
        await update.message.reply_text(
            "하루앱 연결이 안 돼 있어요. Railway에 SUPABASE_URL / "
            "SUPABASE_SERVICE_KEY / HARU_OWNER_ID 를 넣어주세요."
        )
        return
    today = _now_kst().date()
    tomorrow = today + timedelta(days=1)
    over, td, tm = _haru_deadline_buckets(today, tomorrow)
    if not (over or td or tm):
        await update.message.reply_text("🗓 마감 임박 할일이 없어요 🎉")
        return
    parts = ["🗓 **하루앱 마감**"]
    if over:
        parts.append(f"⚠️ 밀림 ({len(over)})\n" + _fmt_haru_items(over))
    if td:
        parts.append(f"오늘 ({len(td)})\n" + _fmt_haru_items(td))
    if tm:
        parts.append(f"내일 ({len(tm)})\n" + _fmt_haru_items(tm))
    await update.message.reply_text("\n\n".join(parts), parse_mode="Markdown")


def _haru_recommend(today, limit=6):
    """하루앱 오늘 추천 — 앱과 같은 우선순위 점수로 상위 N개."""
    opens = haru_app.list_open()

    def pri(d):
        s = (d.get("imp") or 1) * 25
        due = (d.get("due") or "")[:10]
        if due:
            try:
                delta = (datetime.fromisoformat(due).date() - today).days
                if delta < 0:
                    s += 100
                elif delta <= 1:
                    s += 60
                elif delta <= 3:
                    s += 30
            except Exception:
                pass
        est = d.get("est") or 0
        if est and est <= 20:
            s += 10
        if d.get("today"):
            s += 15
        return s

    cand = [d for d in opens if d.get("bucket") == "active" or d.get("today")]
    cand.sort(key=pri, reverse=True)
    return cand[:limit]


async def send_haru_daily(bot, chat_id):
    """매일 11시 하루앱 체크인: 오늘 추천 + 마감."""
    if not haru_app.is_configured():
        return
    today = _now_kst().date()
    tomorrow = today + timedelta(days=1)
    rec = _haru_recommend(today)
    over, td, _tm = _haru_deadline_buckets(today, tomorrow)

    parts = []
    if rec:
        parts.append("🎯 **오늘 추천** (우선순위순)\n" + _fmt_haru_items(rec, limit=6))
    if over or td:
        dl = ["🗓 **마감**"]
        if over:
            dl.append(f"⚠️ 밀림 ({len(over)})\n" + _fmt_haru_items(over))
        if td:
            dl.append(f"오늘 ({len(td)})\n" + _fmt_haru_items(td))
        parts.append("\n".join(dl))

    if not parts:
        text = "🌞 **11시 하루 체크인**\n\n오늘 잡힌 할일이 없어요. 여유롭게 가요 😌"
    else:
        text = "🌞 **11시 하루 체크인**\n\n" + "\n\n".join(parts)
    await bot.send_message(chat_id=chat_id, text=text, parse_mode="Markdown")


async def deadlines_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _auth(update):
        return
    await _send_haru_deadlines(update)


async def send_briefing(bot, chat_id: int, briefing_type: str, todos_client=None):
    """브리핑 발송: morning / evening / night"""
    from .scheduler import _fetch_weather
    today = _now_kst().date()
    tomorrow = today + timedelta(days=1)

    def _fmt_todos(items) -> str:
        if not items:
            return "없어요 🎉"
        # 중요 항목 먼저
        imp = [t for t in items if _is_important(t.text)]
        nml = [t for t in items if not _is_important(t.text)]
        sorted_items = (imp + nml)[:10]
        lines = []
        for t in sorted_items:
            prefix = "🍎 " if _is_important(t.text) else "• "
            due = f" (~{t.due_date[5:]})" if t.due_date else ""
            alarm = ""
            if t.trigger_at:
                try:
                    dt = datetime.fromisoformat(t.trigger_at)
                    alarm = f" ⏰{dt.strftime('%H:%M')}"
                except Exception:
                    pass
            lines.append(f"{prefix}{t.text}{due}{alarm}")
        if len(items) > 15:
            lines.append(f"  … 외 {len(items)-15}개")
        return "\n".join(lines)

    all_todos = todos_client.get_all() if todos_client else []
    undone = [t for t in all_todos if not t.done]

    def _is_fixed_daily(t) -> bool:
        """매일 반복 고정 알림 — 브리핑에서 제외"""
        return t.repeat == "daily"

    # 오늘 할 일 판정
    def _is_today(t):
        today_str = today.isoformat()
        tomorrow_str = tomorrow.isoformat()
        # trigger_at / due_date 가 오늘이면 항상 포함
        for field in (t.trigger_at, t.due_date):
            if field and field[:10] == today_str:
                return True
        # daily: 알람이 오늘 발사되어 내일로 재스케줄됐을 경우에도 표시
        #   단, 사용자가 직접 완료(done=1)하면 already filtered out above
        if t.repeat == "daily" and t.trigger_at and t.trigger_at[:10] == tomorrow_str:
            return True
        # after:N: 완료 후 N분뒤 재알람 → 오늘~내일 범위면 포함
        if t.repeat.startswith("after:") and t.trigger_at:
            return t.trigger_at[:10] <= tomorrow_str
        # weekly/monthly: trigger_at이 오늘인 경우만 (위에서 이미 처리됨)
        # 날짜 없는 일반 투두
        return not t.trigger_at and not t.due_date

    def _is_tomorrow(t):
        for field in (t.trigger_at, t.due_date):
            if field and field[:10] == tomorrow.isoformat():
                return True
        return False

    if briefing_type == "morning":
        weather = _fetch_weather("Seoul")
        today_todos = [t for t in undone if _is_today(t) and not _is_fixed_daily(t)]

        # 생리주기 한 줄 요약
        cycle_line = ""
        if _cycle:
            try:
                status = _cycle.get_current_status()
                if "error" not in status:
                    phase = status["phase"]
                    info = status.get("phase_info", {})
                    emoji = info.get("emoji", "")
                    day = status["cycle_day"]
                    pms = " ⚠️ PMS 구간" if status.get("pms_alert") else ""
                    cycle_line = f"\n{emoji} **생리주기**: {phase} {day}일차{pms}"
            except Exception:
                pass

        text = (
            f"☀️ **굿모닝 브리핑**\n\n"
            f"🌤 날씨: {weather}"
            f"{cycle_line}\n\n"
            f"📋 **오늘 할 일** ({len(today_todos)}개)\n"
            f"{_fmt_todos(today_todos)}"
        )

    elif briefing_type == "evening":
        remaining = [t for t in undone if _is_today(t) and not _is_fixed_daily(t)]
        text = (
            f"🌆 **저녁 브리핑**\n\n"
            f"📋 **오늘 남은 일** ({len(remaining)}개)\n"
            f"{_fmt_todos(remaining)}"
        )

    else:  # night
        remaining_today = [t for t in undone if _is_today(t) and not _is_fixed_daily(t)]
        tomorrow_todos = [t for t in undone if _is_tomorrow(t) and not _is_fixed_daily(t)]
        text = (
            f"🌙 **밤 브리핑**\n\n"
            f"📋 **오늘 남은 일** ({len(remaining_today)}개)\n"
            f"{_fmt_todos(remaining_today)}\n\n"
            f"📋 **내일 할 일** ({len(tomorrow_todos)}개)\n"
            f"{_fmt_todos(tomorrow_todos)}"
        )

    await bot.send_message(chat_id=chat_id, text=text, parse_mode="Markdown")
