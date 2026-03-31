import calendar
import logging
from datetime import datetime, timedelta, timezone

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
    reminders_client = context.bot_data.get("reminders_client")
    user_id = context.bot_data.get("user_id")
    if not reminders_client or not user_id:
        return

    try:
        now = _now_kst()
        for r in reminders_client.get_all_active():
            if not r.trigger_at:
                continue
            try:
                trigger = datetime.fromisoformat(r.trigger_at)
            except ValueError:
                continue
            if trigger <= now:
                await context.bot.send_message(chat_id=int(user_id), text=f"🔔 {r.text}")
                if r.repeat == "none":
                    reminders_client.deactivate(r.id)
                else:
                    next_t = _next_trigger(trigger, r.repeat)
                    reminders_client.update_trigger(r.id, next_t.strftime("%Y-%m-%dT%H:%M:%S"))
    except Exception as e:
        logger.error(f"Reminder check error: {e}")
