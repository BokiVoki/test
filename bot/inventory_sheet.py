import json
import os
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional

import gspread
from google.oauth2.service_account import Credentials

from .models import InventoryItem, INVENTORY_COLUMNS

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

INITIAL_INVENTORY = [
    {"name": "질유산균",       "category": "daily",        "qty": 70,  "low_threshold": 7,  "daily": True,  "note": "프로바이오틱스 30억cfu, 아연 8.5mg, 셀렌 55ug",      "phases": "여포기"},
    {"name": "셀레늄",         "category": "daily",        "qty": 65,  "low_threshold": 7,  "daily": True,  "note": "200mcg — 과잉 주의(상한 400mcg)",                    "phases": "배란기"},
    {"name": "베르베린",       "category": "daily",        "qty": 50,  "low_threshold": 7,  "daily": True,  "note": "400mg, 식전 복용 권장",                              "phases": "여포기"},
    {"name": "크랜베리",       "category": "daily",        "qty": 130, "low_threshold": 14, "daily": True,  "note": "비타민C 100mg, 비타민E 1.4mg 포함",                  "phases": "배란기,황체기"},
    {"name": "비타민C",        "category": "daily",        "qty": 127, "low_threshold": 14, "daily": True,  "note": "1000mg",                                             "phases": "all"},
    {"name": "아토목세틴 18mg","category": "prescription", "qty": 20,  "low_threshold": 5,  "daily": True,  "note": "ADHD — 처방대로",                                    "phases": ""},
    {"name": "멜라토닌 2mg",   "category": "situational",  "qty": 37,  "low_threshold": 5,  "daily": False, "note": "수면 필요 시, 취침 30분 전",                          "phases": ""},
    {"name": "디만노스 500mg", "category": "situational",  "qty": 50,  "low_threshold": 7,  "daily": False, "note": "요로감염 증상 시",                                    "phases": ""},
    {"name": "칼마디",         "category": "situational",  "qty": 17,  "low_threshold": 5,  "daily": False, "note": "칼슘 300mg, 마그네슘 150mg, 비타민D. 생리 기간 중 강화","phases": "생리기,황체기"},
    {"name": "에프람정 5mg",   "category": "pms",          "qty": 28,  "low_threshold": 5,  "daily": False, "note": "PMS — 황체기 후반 처방대로",                          "phases": "황체기"},
    {"name": "뉴프람정 5mg",   "category": "pms",          "qty": 19,  "low_threshold": 5,  "daily": False, "note": "에스시탈로프람 — PMS 처방대로",                       "phases": "황체기"},
    {"name": "인데놀 10mg",    "category": "pms",          "qty": 22,  "low_threshold": 5,  "daily": False, "note": "PMS — 처방대로",                                      "phases": "황체기"},
]


def _now_kst() -> datetime:
    return datetime.now(timezone(timedelta(hours=9))).replace(tzinfo=None)


class InventoryClient:
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
                self._sheet = wb.worksheet("Inventory")
            except gspread.WorksheetNotFound:
                self._sheet = wb.add_worksheet("Inventory", rows=200, cols=len(INVENTORY_COLUMNS))
                self._sheet.append_row(INVENTORY_COLUMNS)
        return self._sheet

    def get_all(self) -> list[InventoryItem]:
        rows = self._get_sheet().get_all_values()
        return [InventoryItem.from_row(r) for r in rows[1:] if r and r[0]]

    def get_by_name(self, name: str) -> Optional[InventoryItem]:
        needle = name.lower().replace(" ", "")
        items = self.get_all()
        # 완전 일치 우선
        for item in items:
            if item.name.lower().replace(" ", "") == needle:
                return item
        # 부분 일치
        for item in items:
            if needle in item.name.lower().replace(" ", "") or item.name.lower().replace(" ", "") in needle:
                return item
        return None

    def get_low_stock(self) -> list[InventoryItem]:
        return [i for i in self.get_all() if i.qty <= i.low_threshold]

    def get_daily(self) -> list[InventoryItem]:
        return [i for i in self.get_all() if i.daily]

    def update_qty(self, item_id: str, new_qty: int) -> bool:
        new_qty = max(0, new_qty)  # 0 미만 클리핑
        sheet = self._get_sheet()
        now_str = _now_kst().strftime("%Y-%m-%dT%H:%M:%S")
        for i, row in enumerate(sheet.get_all_values()[1:], start=2):
            if row and row[0] == item_id:
                sheet.update(f"D{i}:H{i}", [[str(new_qty), row[4], row[5], row[6], now_str]])
                return True
        return False

    def get_by_phase(self, phase: str) -> list[InventoryItem]:
        """특정 단계에 태그된 영양제 목록 반환"""
        return [i for i in self.get_all() if i.matches_phase(phase)]

    def update_phases(self, item_id: str, phases: str) -> bool:
        """phases 컬럼 업데이트"""
        sheet = self._get_sheet()
        for i, row in enumerate(sheet.get_all_values()[1:], start=2):
            if row and row[0] == item_id:
                # I열(9번째) = phases
                sheet.update_cell(i, 9, phases)
                return True
        return False

    def add_item(
        self,
        name: str,
        qty: int,
        category: str = "daily",
        low_threshold: int = 7,
        daily: bool = False,
        note: str = "",
        phases: str = "",
    ) -> InventoryItem:
        """새 영양제/약을 시트에 추가. 이미 같은 이름이 있으면 qty만 업데이트."""
        existing = self.get_by_name(name)
        if existing:
            self.update_qty(existing.id, existing.qty + qty)
            existing.qty += qty
            return existing
        now_str = _now_kst().strftime("%Y-%m-%dT%H:%M:%S")
        item = InventoryItem(
            id=uuid.uuid4().hex[:8],
            name=name,
            category=category,
            qty=qty,
            low_threshold=low_threshold,
            daily=daily,
            note=note,
            updated_at=now_str,
            phases=phases,
        )
        self._get_sheet().append_row(item.to_row())
        return item

    def setup_initial(self) -> int:
        """시트가 비어있을 때만 초기 데이터 삽입. 삽입된 행 수 반환."""
        existing = self.get_all()
        if existing:
            return 0
        now_str = _now_kst().strftime("%Y-%m-%dT%H:%M:%S")
        rows = []
        for d in INITIAL_INVENTORY:
            item = InventoryItem(
                id=uuid.uuid4().hex[:8],
                name=d["name"],
                category=d["category"],
                qty=d["qty"],
                low_threshold=d["low_threshold"],
                daily=d["daily"],
                note=d["note"],
                updated_at=now_str,
                phases=d.get("phases", ""),
            )
            rows.append(item.to_row())
        self._get_sheet().append_rows(rows)
        return len(rows)
