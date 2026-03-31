import json
import os
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional

import gspread
from google.oauth2.service_account import Credentials

from .models import Reminder, REMINDER_COLUMNS

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def now_kst() -> datetime:
    kst = timezone(timedelta(hours=9))
    return datetime.now(kst).replace(tzinfo=None)


class RemindersClient:
    def __init__(self, spreadsheet_id: str):
        creds_json = os.getenv("GOOGLE_CREDENTIALS_JSON")
        if not creds_json:
            raise ValueError("GOOGLE_CREDENTIALS_JSON 환경변수가 설정되지 않았어요.")
        creds_dict = json.loads(creds_json)
        creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
        self._gc = gspread.authorize(creds)
        self._spreadsheet_id = spreadsheet_id
        self._sheet: Optional[gspread.Worksheet] = None

    def _get_sheet(self) -> gspread.Worksheet:
        if self._sheet is None:
            wb = self._gc.open_by_key(self._spreadsheet_id)
            try:
                self._sheet = wb.worksheet("Reminders")
            except gspread.WorksheetNotFound:
                self._sheet = wb.add_worksheet("Reminders", rows=500, cols=len(REMINDER_COLUMNS))
                self._sheet.append_row(REMINDER_COLUMNS)
        return self._sheet

    def get_all_active(self) -> list[Reminder]:
        sheet = self._get_sheet()
        rows = sheet.get_all_values()
        if len(rows) < 2:
            return []
        return [
            Reminder.from_row(r)
            for r in rows[1:]
            if r and r[0] and (len(r) <= 4 or r[4] != "0")
        ]

    def add_reminder(self, reminder: Reminder) -> Reminder:
        if not reminder.id:
            reminder.id = uuid.uuid4().hex[:8]
        if not reminder.created_at:
            reminder.created_at = now_kst().strftime("%Y-%m-%dT%H:%M:%S")
        self._get_sheet().append_row(reminder.to_row())
        return reminder

    def update_trigger(self, reminder_id: str, new_trigger_at: str):
        sheet = self._get_sheet()
        for i, row in enumerate(sheet.get_all_values()[1:], start=2):
            if row and row[0] == reminder_id:
                sheet.update_cell(i, 3, new_trigger_at)
                return

    def deactivate(self, reminder_id: str):
        sheet = self._get_sheet()
        for i, row in enumerate(sheet.get_all_values()[1:], start=2):
            if row and row[0] == reminder_id:
                sheet.update_cell(i, 5, "0")
                return

    def clear_all(self):
        """헤더 제외 전체 행 삭제"""
        sheet = self._get_sheet()
        rows = sheet.get_all_values()
        if len(rows) > 1:
            sheet.delete_rows(2, len(rows))
