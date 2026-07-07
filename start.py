"""
Railway 런처 — 환경변수 BOT_ROLE 로 어떤 봇을 켤지 결정한다.
두 Railway 서비스가 똑같은 시작 명령(worker: python start.py)을 쓰고,
BOT_ROLE 값으로만 구분한다. (커스텀 시작명령어 불필요 → PATH 문제 회피)

- BOT_ROLE=inbox  → 인박스 봇 (inbox_bot.main)
- 그 외 / 미설정   → 일정관리 봇 (bot.main)
"""
import os


def main():
    role = os.getenv("BOT_ROLE", "schedule").strip().lower()
    if role == "inbox":
        from inbox_bot.main import main as run
    else:
        from bot.main import main as run
    run()


if __name__ == "__main__":
    main()
