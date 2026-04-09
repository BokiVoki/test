import calendar
import logging
import urllib.request
from datetime import date, datetime, timedelta, timezone

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CallbackContext

logger = logging.getLogger(__name__)


def _now_kst() -> datetime:
    return datetime.now(timezone(timedelta(hours=9))).replace(tzinfo=None)


_WMO_KR = {
    0: "맑음", 1: "대체로 맑음", 2: "구름 조금", 3: "흐림",
    45: "안개", 48: "안개",
    51: "이슬비", 53: "이슬비", 55: "이슬비",
    61: "비", 63: "비", 65: "강한 비",
    71: "눈", 73: "눈", 75: "강한 눈",
    77: "싸락눈",
    80: "소나기", 81: "소나기", 82: "강한 소나기",
    85: "눈 소나기", 86: "눈 소나기",
    95: "뇌우", 96: "뇌우", 99: "강한 뇌우",
}

def _fetch_weather(city: str = "Seoul") -> str:
    """Open-Meteo 날씨 (API키 불필요, 서울 고정)"""
    import json as _json
    # 서울 좌표
    lat, lon = 37.5665, 126.9780
    url = (
        f"https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}"
        f"&current=temperature_2m,relative_humidity_2m,weathercode"
        f"&timezone=Asia%2FSeoul"
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            data = _json.loads(r.read().decode("utf-8"))
        cur = data["current"]
        temp = round(cur["temperature_2m"])
        hum  = cur["relative_humidity_2m"]
        code = cur["weathercode"]
        desc = _WMO_KR.get(code, "알 수 없음")
        return f"{desc} {temp}°C 습도 {hum}%"
    except Exception as e:
        logger.warning(f"날씨 조회 실패: {e}")
        return "날씨 정보 없음"


async def send_briefing_job(context: CallbackContext):
    """오전/오후6시/밤11시 브리핑"""
    from . import handlers as _handlers
    user_id = context.bot_data.get("user_id")
    briefing_type = context.job.data.get("type", "morning")
    todos_client = context.bot_data.get("todos_client")
    if not user_id:
        return
    try:
        await _handlers.send_briefing(context.bot, int(user_id), briefing_type, todos_client)
    except Exception as e:
        logger.error(f"Briefing job error: {e}")


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

    # ── 생리주기 단계 알림 (하루 1회, 단계 변경 시) ──
    try:
        cycle_client = context.bot_data.get("cycle_client")
        if cycle_client and user_id:
            status = cycle_client.get_current_status()
            if "error" not in status:
                today_str = date.today().isoformat()
                phase = status.get("phase", "")
                cycle_day = status.get("cycle_day", 0)
                notified = context.bot_data.setdefault("phase_notified", {})

                # 단계 전환 알림 (여포기 day6, 배란기 day14, 황체기 day17, PMS day21)
                alert_days = {6: "🌱 여포기", 14: "🌸 배란기", 17: "🌙 황체기", 21: "⚠️ PMS 구간"}
                for trigger_day, label in alert_days.items():
                    key = f"{today_str}_{trigger_day}"
                    if cycle_day == trigger_day and key not in notified:
                        notified[key] = True
                        phase_info = cycle_client.format_status(status)
                        msg = f"{label} 시작!\n\n{phase_info}"
                        if trigger_day == 21:
                            msg = f"⚠️ **PMS 구간 진입** (황체기 {cycle_day}일차)\n에프람/뉴프람/인데놀 챙기세요!\n\n{phase_info}"
                        await context.bot.send_message(
                            chat_id=int(user_id),
                            text=msg,
                            parse_mode="Markdown",
                        )
    except Exception as e:
        logger.error(f"Cycle phase alert error: {e}")


async def send_checkin_job(context: CallbackContext):
    """오전(10시)/오후(15시) 체크인 알림"""
    from . import handlers as _handlers
    user_id = context.bot_data.get("user_id")
    checkin_type = context.job.data.get("type", "morning")
    if user_id:
        try:
            await _handlers.send_daily_checkin(context.bot, int(user_id), checkin_type)
        except Exception as e:
            logger.error(f"Checkin job error: {e}")
