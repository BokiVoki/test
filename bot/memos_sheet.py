import json
import os
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional

import gspread
from google.oauth2.service_account import Credentials

from .models import MemoEntry, MEMO_COLUMNS

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def now_kst() -> datetime:
    return datetime.now(timezone(timedelta(hours=9))).replace(tzinfo=None)


class MemosClient:
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
                self._sheet = wb.worksheet("Memos")
            except gspread.WorksheetNotFound:
                self._sheet = wb.add_worksheet("Memos", rows=1000, cols=len(MEMO_COLUMNS))
                self._sheet.append_row(MEMO_COLUMNS)
        return self._sheet

    def add(self, mode: str, content: str) -> MemoEntry:
        entry = MemoEntry(
            id=uuid.uuid4().hex[:8],
            mode=mode,
            content=content,
            created_at=now_kst().strftime("%Y-%m-%dT%H:%M:%S"),
        )
        self._get_sheet().append_row(entry.to_row())
        return entry

    def get_by_mode(self, mode: str, limit: int = 10) -> list[MemoEntry]:
        rows = self._get_sheet().get_all_values()
        entries = [MemoEntry.from_row(r) for r in rows[1:] if r and r[0] and r[1] == mode]
        return entries[-limit:]  # 최신순

    def delete(self, memo_id: str) -> bool:
        sheet = self._get_sheet()
        for i, row in enumerate(sheet.get_all_values()[1:], start=2):
            if row and row[0] == memo_id:
                sheet.delete_rows(i)
                return True
        return False
