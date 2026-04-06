import json
import os
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional

import gspread
from google.oauth2.service_account import Credentials

from .models import TodoItem, TODO_COLUMNS

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def now_kst() -> datetime:
    return datetime.now(timezone(timedelta(hours=9))).replace(tzinfo=None)


class TodosClient:
    def __init__(self, spreadsheet_id: str):
        creds_json = os.getenv("GOOGLE_CREDENTIALS_JSON")
        if not creds_json:
            raise ValueError("GOOGLE_CREDENTIALS_JSON 환경변수가 설정되지 않았어요.")
        creds = Credentials.from_service_account_info(json.loads(creds_json), scopes=SCOPES)
        self._gc = gspread.authorize(creds)
        self._spreadsheet_id = spreadsheet_id
        self._sheet: Optional[gspread.Worksheet] = None

    def _get_sheet(self) -> gspread.Worksheet:
        if self._sheet is None:
            wb = self._gc.open_by_key(self._spreadsheet_id)
            try:
                self._sheet = wb.worksheet("Todos")
            except gspread.WorksheetNotFound:
                self._sheet = wb.add_worksheet("Todos", rows=500, cols=len(TODO_COLUMNS))
                self._sheet.append_row(TODO_COLUMNS)
        return self._sheet

    def get_all(self) -> list[TodoItem]:
        rows = self._get_sheet().get_all_values()
        return [TodoItem.from_row(r) for r in rows[1:] if r and r[0]]

    def get_pending(self) -> list[TodoItem]:
        items = [t for t in self.get_all() if not t.done]
        # 마감일 있는 것 먼저, 없는 것 뒤로
        return sorted(items, key=lambda t: (t.due_date == "", t.due_date))

    def add(self, item: TodoItem) -> TodoItem:
        if not item.id:
            item.id = uuid.uuid4().hex[:8]
        if not item.created_at:
            item.created_at = now_kst().strftime("%Y-%m-%dT%H:%M:%S")
        self._get_sheet().append_row(item.to_row())
        return item

    def complete(self, todo_id: str) -> bool:
        sheet = self._get_sheet()
        for i, row in enumerate(sheet.get_all_values()[1:], start=2):
            if row and row[0] == todo_id:
                sheet.update_cell(i, 3, "1")
                return True
        return False

    def delete(self, todo_id: str) -> bool:
        sheet = self._get_sheet()
        for i, row in enumerate(sheet.get_all_values()[1:], start=2):
            if row and row[0] == todo_id:
                sheet.delete_rows(i)
                return True
        return False

    def get_with_alarm(self) -> list[TodoItem]:
        """trigger_at이 설정된 미완료 투두 (알람 대기 중)"""
        return [t for t in self.get_all() if not t.done and t.trigger_at]

    def get_by_id(self, todo_id: str) -> Optional[TodoItem]:
        for t in self.get_all():
            if t.id == todo_id:
                return t
        return None

    def clear_trigger(self, todo_id: str):
        """trigger_at 초기화 — 알람만 해제, 투두 항목은 유지"""
        sheet = self._get_sheet()
        for i, row in enumerate(sheet.get_all_values()[1:], start=2):
            if row and row[0] == todo_id:
                sheet.update_cell(i, 6, "")
                return

    def reschedule(self, todo_id: str, new_trigger_at: str):
        """재알람: trigger_at 업데이트, done=0 으로 재활성화"""
        sheet = self._get_sheet()
        for i, row in enumerate(sheet.get_all_values()[1:], start=2):
            if row and row[0] == todo_id:
                sheet.update_cell(i, 6, new_trigger_at)
                sheet.update_cell(i, 3, "0")  # 완료 해제
                return

    def cancel_all_alarms(self):
        """모든 미완료 투두의 trigger_at 초기화"""
        sheet = self._get_sheet()
        for i, row in enumerate(sheet.get_all_values()[1:], start=2):
            if row and row[0] and row[2] != "1" and len(row) > 5 and row[5]:
                sheet.update_cell(i, 6, "")

    def find_by_text(self, text: str) -> Optional[TodoItem]:
        needle = text.lower().strip()
        pending = self.get_pending()
        for t in pending:
            if t.text.lower() == needle:
                return t
        for t in pending:
            if needle in t.text.lower() or t.text.lower() in needle:
                return t
        return None
