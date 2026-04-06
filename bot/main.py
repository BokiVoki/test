import logging
import os

from dotenv import load_dotenv
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from .sheets import SheetsClient
from .reminders_sheet import RemindersClient
from .todos_sheet import TodosClient
from .memos_sheet import MemosClient
from .inventory_sheet import InventoryClient
from .intake_sheet import IntakeLogClient
from .cycle_sheet import CycleClient
from .scheduler import check_reminders_job, send_checkin_job
from . import handlers

load_dotenv()

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=os.getenv("LOG_LEVEL", "INFO"),
)
logger = logging.getLogger(__name__)


def main():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN 환경변수가 설정되지 않았어요.")

    spreadsheet_id = os.getenv("SPREADSHEET_ID")
    if not spreadsheet_id:
        raise ValueError("SPREADSHEET_ID 환경변수가 설정되지 않았어요.")

    sheets = SheetsClient(spreadsheet_id=spreadsheet_id)
    handlers.init_sheets(sheets)
    logger.info("Google Sheets 연결 완료")

    reminders = RemindersClient(spreadsheet_id=spreadsheet_id)
    handlers.init_reminders(reminders)
    logger.info("Reminders 연결 완료")

    todos = TodosClient(spreadsheet_id=spreadsheet_id)
    handlers.init_todos(todos)
    logger.info("Todos 연결 완료")

    memos = MemosClient(spreadsheet_id=spreadsheet_id)
    handlers.init_memos(memos)
    logger.info("Memos 연결 완료")

    inventory = InventoryClient(spreadsheet_id=spreadsheet_id)
    handlers.init_inventory(inventory)
    logger.info("Inventory 연결 완료")

    intake = IntakeLogClient(spreadsheet_id=spreadsheet_id)
    handlers.init_intake(intake)
    logger.info("IntakeLog 연결 완료")

    cycle = CycleClient(spreadsheet_id=spreadsheet_id)
    handlers.init_cycle(cycle)
    logger.info("Cycle 연결 완료")

    user_id = os.getenv("TELEGRAM_USER_ID", "")

    app = Application.builder().token(token).build()
    app.bot_data["reminders_client"] = reminders  # 기존 명령어 호환용
    app.bot_data["todos_client"] = todos
    app.bot_data["intake_client"] = intake
    app.bot_data["cycle_client"] = cycle
    app.bot_data["user_id"] = user_id

    # 모드 전환 (텔레그램 명령어는 영어/숫자만 가능)
    app.add_handler(CommandHandler("secretary", handlers.switch_mode_handler))
    app.add_handler(CommandHandler("finance", handlers.switch_mode_handler))
    app.add_handler(CommandHandler("consultant", handlers.switch_mode_handler))
    app.add_handler(CommandHandler("mode", handlers.mode_handler))

    # 비서 명령어
    app.add_handler(CommandHandler("start", handlers.start_handler))
    app.add_handler(CommandHandler("help", handlers.help_handler))
    app.add_handler(CommandHandler("list", handlers.list_handler))
    app.add_handler(CommandHandler("stats", handlers.stats_handler))
    app.add_handler(CommandHandler("get", handlers.get_handler))
    app.add_handler(CommandHandler("done", handlers.done_handler))
    app.add_handler(CommandHandler("drop", handlers.drop_handler))
    app.add_handler(CommandHandler("export", handlers.export_handler))
    app.add_handler(CommandHandler("import_archive", handlers.import_handler))

    # 리마인더
    app.add_handler(CommandHandler("remind", handlers.remind_handler))
    app.add_handler(CommandHandler("reminders", handlers.reminders_handler))
    app.add_handler(CommandHandler("cancel_reminder", handlers.cancel_reminder_handler))
    app.add_handler(CommandHandler("cancel_all_reminders", handlers.cancel_all_reminders_handler))
    app.add_handler(CommandHandler("clear_reminders", handlers.clear_reminders_sheet_handler))

    # 투두리스트
    app.add_handler(CommandHandler("todos", handlers.todos_handler))
    app.add_handler(CommandHandler("todo_done", handlers.todo_done_handler))
    app.add_handler(CommandHandler("todo_del", handlers.todo_del_handler))

    # 메모
    app.add_handler(CommandHandler("memos", handlers.memos_handler))
    app.add_handler(CommandHandler("memo_del", handlers.memo_del_handler))

    # 영양제/생리주기/ADHD
    app.add_handler(CommandHandler("inventory", handlers.inventory_handler))
    app.add_handler(CommandHandler("intake", handlers.intake_handler))
    app.add_handler(CommandHandler("cycle", handlers.cycle_handler))
    app.add_handler(CommandHandler("setup_supplements", handlers.setup_supplements_handler))

    # 1분마다 투두 알람 + 생리주기 단계 알림 체크
    app.job_queue.run_repeating(check_reminders_job, interval=60, first=10)

    # 오전 10시 / 오후 3시 체크인 (KST = UTC+9)
    import datetime as _dt
    app.job_queue.run_daily(
        send_checkin_job, time=_dt.time(1, 0, tzinfo=_dt.timezone.utc),  # KST 10:00
        data={"type": "morning"},
    )
    app.job_queue.run_daily(
        send_checkin_job, time=_dt.time(6, 0, tzinfo=_dt.timezone.utc),  # KST 15:00
        data={"type": "afternoon"},
    )

    # 인라인 버튼 콜백 (되돌리기/취소)
    app.add_handler(CallbackQueryHandler(handlers.callback_handler, pattern=r"^(undo:|remind:|pomo:)"))

    # 자연어 메시지 (모든 텍스트)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.message_handler))

    logger.info("봇 시작. 폴링 중...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
