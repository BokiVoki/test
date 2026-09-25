import calendar
import logging
from datetime import datetime, timedelta, timezone

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CallbackContext

logger = logging.getLogger(__name__)


def _now_kst() -> datetime:
    return datetime.now(timezone(timedelta(hours=9))).replace(tzinfo=None)


async def send_haru_daily_job(context: CallbackContext):
    """매일 11시 하루앱 체크인(오늘 추천 + 마감)."""
    from . import handlers as _handlers
    user_id = context.bot_data.get("user_id")
    if not user_id:
        return
    try:
        await _handlers.send_haru_daily(context.bot, int(user_id))
    except Exception as e:
        logger.error(f"Haru daily job error: {e}")


def _next_trigger(trigger: datetime, repeat: str, now: datetime = None) -> datetime:
    if repeat == "daily":
        return trigger + timedelta(days=1)
    elif repeat == "weekly":
        return trigger + timedelta(weeks=1)
    elif repeat == "monthly":
        month = trigger.month % 12 + 1
        year = trigger.year + (1 if trigger.month == 12 else 0)
        last_day = calendar.monthrange(year, month)[1]
        return trigger.replace(year=year, month=month, day=min(trigger.day, last_day))
    elif repeat.startswith("after:"):
        # "after:N" → 완료/발화 시점(now)에서 N분 후
        try:
            minutes = int(repeat.split(":")[1])
        except (IndexError, ValueError):
            minutes = 60
        base = now if now is not None else trigger
        return base + timedelta(minutes=minutes)
    return trigger


async def check_reminders_job(context: CallbackContext):
    todos_client = context.bot_data.get("todos_client")
    user_id = context.bot_data.get("user_id")
    if not todos_client or not user_id:
        return

    try:
        now = _now_kst()
        for todo in todos_client.get_with_alarm():
            try:
                import re as _re
                _ts = todo.trigger_at.strip().replace(" ", "T")
                _ts = _re.sub(r'T(\d):', r'T0\1:', _ts)
                trigger = datetime.fromisoformat(_ts)
            except (ValueError, AttributeError):
                continue
            if trigger <= now:
                keyboard = InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton("✅ 완료", callback_data=f"remind:done:{todo.id}"),
                        InlineKeyboardButton("🔁 재알람", callback_data=f"remind:snooze:{todo.id}"),
                    ]
                ])
                await context.bot.send_message(
                    chat_id=int(user_id),
                    text=f"🔔 {todo.text}",
                    reply_markup=keyboard,
                )
                if todo.repeat == "none":
                    todos_client.clear_trigger(todo.id)
                else:
                    next_t = _next_trigger(trigger, todo.repeat, now=now)
                    todos_client.reschedule(todo.id, next_t.strftime("%Y-%m-%dT%H:%M:%S"))
    except Exception as e:
        logger.error(f"Todo alarm check error: {e}")

    # ── 생리주기 단계 알림 (단계 진입일에 주기당 1회, 시트에 영구 기록) ──
    try:
        cycle_client = context.bot_data.get("cycle_client")
        if cycle_client and user_id:
            status = cycle_client.get_current_status()
            if "error" not in status:
                cycle_day = status.get("cycle_day", 0)
                # 단계 전환 알림 (여포기 day6, 배란기 day14, 황체기 day17, PMS day21)
                alert_days = {6: "🌱 여포기", 14: "🌸 배란기", 17: "🌙 황체기", 21: "⚠️ PMS 구간"}
                if cycle_day in alert_days:
                    # 이번 주기에 이미 보냈는지 시트에서 확인 (재시작해도 유지 → 중복 방지)
                    notified_days = cycle_client.get_notified_days()
                    if cycle_day not in notified_days:
                        cycle_client.mark_notified(cycle_day)
                        label = alert_days[cycle_day]
                        phase_info = cycle_client.format_status(status)
                        if cycle_day == 21:
                            msg = (f"⚠️ **PMS 구간 진입** (황체기 {cycle_day}일차)\n"
                                   f"에프람/뉴프람/인데놀 챙기세요!\n\n{phase_info}")
                        else:
                            msg = f"{label} 시작!\n\n{phase_info}"
                        await context.bot.send_message(
                            chat_id=int(user_id),
                            text=msg,
                            parse_mode="Markdown",
                        )
    except Exception as e:
        logger.error(f"Cycle phase alert error: {e}")
