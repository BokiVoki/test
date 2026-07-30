"""구글 캘린더 연동 — 봇이 서비스계정으로 이벤트를 만든다.

기존 Sheets/Drive와 같은 서비스계정(GOOGLE_CREDENTIALS_JSON)을 재사용한다.
사용하려면:
  1. Google Cloud 프로젝트에서 **Google Calendar API** 사용 설정
  2. 내 구글 캘린더를 서비스계정 이메일(creds의 client_email)에 공유
     — 권한: '일정 변경'(Make changes to events)
  3. 환경변수 GOOGLE_CALENDAR_ID = 그 캘린더 ID (기본 캘린더면 내 gmail 주소)
"""
import json
import os
from datetime import date, timedelta

CAL_ID = os.getenv("GOOGLE_CALENDAR_ID", "")
TZ = "Asia/Seoul"


def is_configured() -> bool:
    return bool(os.getenv("GOOGLE_CREDENTIALS_JSON") and CAL_ID)


def _service():
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    creds = service_account.Credentials.from_service_account_info(
        json.loads(os.getenv("GOOGLE_CREDENTIALS_JSON")),
        scopes=["https://www.googleapis.com/auth/calendar"],
    )
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def create_all_day(summary: str, start_date: str, end_date_inclusive: str) -> dict:
    """종일 이벤트(하루 또는 기간). 구글은 end가 '다음날'(배타적)이라 +1일 해준다."""
    end_excl = (date.fromisoformat(end_date_inclusive) + timedelta(days=1)).isoformat()
    body = {
        "summary": summary,
        "start": {"date": start_date},
        "end": {"date": end_excl},
    }
    return _service().events().insert(calendarId=CAL_ID, body=body).execute()


def create_timed(summary: str, start_iso: str, end_iso: str) -> dict:
    """시간 지정 이벤트. start/end 는 'YYYY-MM-DDTHH:MM:SS'."""
    body = {
        "summary": summary,
        "start": {"dateTime": start_iso, "timeZone": TZ},
        "end": {"dateTime": end_iso, "timeZone": TZ},
    }
    return _service().events().insert(calendarId=CAL_ID, body=body).execute()
