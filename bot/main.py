import logging
import os

from dotenv import load_dotenv
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
)

from .sheets import SheetsClient
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

    app = Application.builder().token(token).build()

    # 모드 전환
    app.add_handler(CommandHandler(["비서", "secretary"], handlers.switch_mode_handler))
    app.add_handler(CommandHandler(["금융", "finance"], handlers.switch_mode_handler))
    app.add_handler(CommandHandler(["컨설턴트", "consultant"], handlers.switch_mode_handler))
    app.add_handler(CommandHandler("모드", handlers.mode_handler))

    # 비서 명령어
    app.add_handler(CommandHandler("start", handlers.start_handler))
    app.add_handler(CommandHandler("help", handlers.help_handler))
    app.add_handler(CommandHandler("list", handlers.list_handler))
    app.add_handler(CommandHandler("stats", handlers.stats_handler))
    app.add_handler(CommandHandler("get", handlers.get_handler))
    app.add_handler(CommandHandler("done", handlers.done_handler))
    app.add_handler(CommandHandler("drop", handlers.drop_handler))
    app.add_handler(CommandHandler("export", handlers.export_handler))

    # 자연어 메시지 (모든 텍스트)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.message_handler))

    logger.info("봇 시작. 폴링 중...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
