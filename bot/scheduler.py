import calendar
import logging
from datetime import datetime, timedelta, timezone

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CallbackContext

logger = logging.getLogger(__name__)


def _now_kst() -> datetime:
    return datetime.now(timezone(timedelta(hours=9))).replace(tzinfo=None)


def _next_trigger(trigger: datetime, repeat: str) -> datetime:
    if repeat == "daily":
        return trigger + timedelta(days=1)
    elif repeat == "weekly":
        return trigger + timedelta(weeks=1)
    elif repeat == "monthly":
        month = trigger.month % 12 + 1
        year = trigger.year + (1 if trigger.month == 12 else 0)
        last_day = calendar.monthrange(year, month)[1]
        return trigger.replace(year=year, month=month, day=min(trigger.day, last_day))
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
                trigger = datetime.fromisoformat(todo.trigger_at)
            except ValueError:
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
                    next_t = _next_trigger(trigger, todo.repeat)
                    todos_client.reschedule(todo.id, next_t.strftime("%Y-%m-%dT%H:%M:%S"))
    except Exception as e:
        logger.error(f"Todo alarm check error: {e}")
