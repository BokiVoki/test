import json
import os
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional

import gspread
from google.oauth2.service_account import Credentials

from .models import IntakeLogItem, INTAKE_LOG_COLUMNS

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def _now_kst() -> datetime:
    return datetime.now(timezone(timedelta(hours=9))).replace(tzinfo=None)


def _today_kst() -> str:
    return _now_kst().strftime("%Y-%m-%d")


class IntakeLogClient:
    def __init__(self, spreadsheet_id: str):
        creds_json = os.getenv("GOOGLE_CREDENTIALS_JSON")
        if not creds_json:
            raise ValueError("GOOGLE_CREDENTIALS_JSON 환경변수가 없어요.")
        creds = Credentials.from_service_account_info(json.loads(creds_json), scopes=SCOPES)
        self._gc = gspread.authorize(creds)
        self._spreadsheet_id = spreadsheet_id
        self._sheet: Optional[gspread.Worksheet] = None

    def _get_sheet(self) -> gspread.Worksheet:
        if self._sheet is None:
            wb = self._gc.open_by_key(self._spreadsheet_id)
            try:
                self._sheet = wb.worksheet("IntakeLog")
            except gspread.WorksheetNotFound:
                self._sheet = wb.add_worksheet("IntakeLog", rows=2000, cols=len(INTAKE_LOG_COLUMNS))
                self._sheet.append_row(INTAKE_LOG_COLUMNS)
        return self._sheet

    def log(self, item_name: str, qty_taken: int = 1, qty_after: int = 0, note: str = "") -> IntakeLogItem:
        entry = IntakeLogItem(
            id=uuid.uuid4().hex[:8],
            item_name=item_name,
            qty_taken=qty_taken,
            qty_after=qty_after,
            taken_at=_now_kst().strftime("%Y-%m-%dT%H:%M:%S"),
            note=note,
        )
        self._get_sheet().append_row(entry.to_row())
        return entry

    def get_today(self, date_str: str = None) -> list[IntakeLogItem]:
        date_str = date_str or _today_kst()
        rows = self._get_sheet().get_all_values()
        result = []
        for r in rows[1:]:
            if r and r[0] and len(r) > 4 and r[4].startswith(date_str):
                result.append(IntakeLogItem.from_row(r))
        return result

    def get_by_item(self, item_name: str, n: int = 10) -> list[IntakeLogItem]:
        needle = item_name.lower()
        rows = self._get_sheet().get_all_values()
        result = [IntakeLogItem.from_row(r) for r in rows[1:] if r and r[0] and len(r) > 1 and needle in r[1].lower()]
        return result[-n:]

    def get_recent(self, n: int = 20) -> list[IntakeLogItem]:
        rows = self._get_sheet().get_all_values()
        return [IntakeLogItem.from_row(r) for r in rows[1:] if r and r[0]][-n:]
