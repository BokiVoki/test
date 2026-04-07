import json
import os
import time
import uuid
from datetime import date
from typing import Optional

import gspread
from google.oauth2.service_account import Credentials

from .models import ContentEntry, SHEET_COLUMNS, STATUS_KR, CONTENT_TYPE_KR

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

CACHE_TTL = 60  # seconds


class SheetsClient:
    def __init__(self, spreadsheet_id: str):
        creds_json = os.getenv("GOOGLE_CREDENTIALS_JSON")
        if not creds_json:
            raise ValueError("GOOGLE_CREDENTIALS_JSON 환경변수가 설정되지 않았어요.")
        creds_dict = json.loads(creds_json)
        creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
        self._gc = gspread.authorize(creds)
        self._spreadsheet_id = spreadsheet_id
        self._sheet: Optional[gspread.Worksheet] = None
        self._cache: Optional[list[ContentEntry]] = None
        self._cache_time: float = 0

    def _get_sheet(self) -> gspread.Worksheet:
        if self._sheet is None:
            wb = self._gc.open_by_key(self._spreadsheet_id)
            try:
                self._sheet = wb.worksheet("Archive")
            except gspread.WorksheetNotFound:
                self._sheet = wb.add_worksheet("Archive", rows=1000, cols=len(SHEET_COLUMNS))
                self._sheet.append_row(SHEET_COLUMNS)
        return self._sheet

    def _invalidate_cache(self):
        self._cache = None
        self._cache_time = 0

    def get_all_entries(self, force: bool = False) -> list[ContentEntry]:
        now = time.time()
        if not force and self._cache is not None and now - self._cache_time < CACHE_TTL:
            return self._cache

        sheet = self._get_sheet()
        rows = sheet.get_all_values()
        if not rows or len(rows) < 2:
            self._cache = []
        else:
            # rows[0] 은 헤더
            self._cache = [ContentEntry.from_row(r) for r in rows[1:] if r[0]]
        self._cache_time = now
        return self._cache

    def get_titles(self) -> list[str]:
        return [e.title for e in self.get_all_entries() if e.title]

    def get_entry_by_title(self, title: str) -> Optional[ContentEntry]:
        """대소문자 무시, 부분 매칭으로 검색"""
        needle = title.lower().strip()
        entries = self.get_all_entries()
        # 완전 일치 우선
        for e in entries:
            if e.title.lower() == needle:
                return e
        # 부분 일치
        for e in entries:
            if needle in e.title.lower() or e.title.lower() in needle:
                return e
        return None

    def add_entry(self, entry: ContentEntry) -> ContentEntry:
        if not entry.id:
            entry.id = uuid.uuid4().hex[:8]
        today = str(date.today())
        if not entry.date_added:
            entry.date_added = today
        entry.date_updated = today

        sheet = self._get_sheet()
        sheet.append_row(entry.to_row(), value_input_option="USER_ENTERED")
        self._invalidate_cache()
        return entry

    def batch_add_entries(self, entries: list[ContentEntry]) -> int:
        """여러 항목을 한 번의 API 호출로 저장 (import용)"""
        today = str(date.today())
        rows = []
        for entry in entries:
            if not entry.id:
                entry.id = uuid.uuid4().hex[:8]
            if not entry.date_added:
                entry.date_added = today
            entry.date_updated = today
            rows.append(entry.to_row())

        if not rows:
            return 0

        sheet = self._get_sheet()
        sheet.append_rows(rows, value_input_option="USER_ENTERED")
        self._invalidate_cache()
        return len(rows)

    def delete_entry(self, entry_id: str) -> bool:
        sheet = self._get_sheet()
        rows = sheet.get_all_values()
        for i, row in enumerate(rows[1:], start=2):
            if row and row[0] == entry_id:
                sheet.delete_rows(i)
                self._invalidate_cache()
                return True
        return False

    def update_entry(self, entry: ContentEntry) -> None:
        entry.date_updated = str(date.today())
        sheet = self._get_sheet()
        rows = sheet.get_all_values()
        for i, row in enumerate(rows[1:], start=2):  # 1-indexed, skip header
            if row and row[0] == entry.id:
                sheet.update(
                    range_name=f"A{i}:P{i}",
                    values=[entry.to_row()],
                    value_input_option="USER_ENTERED",
                )
                self._invalidate_cache()
                return
        # id로 못 찾으면 새로 추가
        self.add_entry(entry)

    def batch_update_entries(self, entries: list[ContentEntry]) -> int:
        """여러 항목을 한 번의 batch API 호출로 업데이트 (rate limit 방지)"""
        if not entries:
            return 0
        today = str(date.today())
        sheet = self._get_sheet()
        rows = sheet.get_all_values()
        # id → row index 맵 미리 생성
        id_to_row = {row[0]: i for i, row in enumerate(rows[1:], start=2) if row and row[0]}
        batch_data = []
        for entry in entries:
            entry.date_updated = today
            i = id_to_row.get(entry.id)
            if i:
                batch_data.append({
                    "range": f"A{i}:P{i}",
                    "values": [entry.to_row()],
                })
        if batch_data:
            sheet.batch_update(batch_data, value_input_option="USER_ENTERED")
        self._invalidate_cache()
        return len(batch_data)

    def get_recent(
        self,
        n: int = 10,
        filter_type: Optional[str] = None,
        filter_status: Optional[str] = None,
    ) -> list[ContentEntry]:
        entries = self.get_all_entries()
        if filter_type:
            entries = [e for e in entries if e.type == filter_type]
        if filter_status:
            entries = [e for e in entries if e.status == filter_status]
        # date_updated 기준 최신순
        entries = sorted(entries, key=lambda e: e.date_updated or "0", reverse=True)
        return entries[:n]

    def get_stats(self) -> dict:
        entries = self.get_all_entries()
        total = len(entries)
        by_type: dict[str, int] = {}
        by_status: dict[str, int] = {}
        rated = [e.rating for e in entries if e.rating is not None]

        for e in entries:
            by_type[e.type] = by_type.get(e.type, 0) + 1
            by_status[e.status] = by_status.get(e.status, 0) + 1

        return {
            "total": total,
            "by_type": by_type,
            "by_status": by_status,
            "avg_rating": round(sum(rated) / len(rated), 2) if rated else None,
            "rated_count": len(rated),
        }

    def format_list(self, entries: list[ContentEntry]) -> str:
        if not entries:
            return "아직 아무것도 없어요."
        lines = []
        for i, e in enumerate(entries, 1):
            lines.append(f"{i}. {e.summary()}")
        return "\n".join(lines)

    def format_stats(self) -> str:
        stats = self.get_stats()
        lines = [f"📚 총 **{stats['total']}개** 아카이브"]

        if stats["by_type"]:
            type_str = ", ".join(
                f"{CONTENT_TYPE_KR.get(k, k)} {v}개"
                for k, v in sorted(stats["by_type"].items(), key=lambda x: -x[1])
            )
            lines.append(f"종류: {type_str}")

        if stats["by_status"]:
            status_str = ", ".join(
                f"{STATUS_KR.get(k, k)} {v}개"
                for k, v in sorted(stats["by_status"].items(), key=lambda x: -x[1])
            )
            lines.append(f"상태: {status_str}")

        if stats["avg_rating"] is not None:
            lines.append(f"평균 평점: ⭐{stats['avg_rating']} ({stats['rated_count']}개 평가)")

        return "\n".join(lines)
