import json
import os
import uuid
from datetime import date, datetime, timezone, timedelta
from typing import Optional

import gspread
from google.oauth2.service_account import Credentials

from .models import CycleRecord, CYCLE_COLUMNS

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

DEFAULT_CYCLE = 30
DEFAULT_PERIOD = 5

PHASE_INFO = {
    "생리기": {
        "emoji": "🩸",
        "days": "day 1-5",
        "desc": "에스트로겐·프로게스테론 최저. 피로·통증이 올 수 있어요.",
        "supplements": "칼마디(마그네슘), 비타민C 강화",
        "exercise": "가벼운 걷기, 스트레칭, 요가 추천. 고강도는 피하기",
        "work": "루틴 업무·정리 집중. 무리한 결정 피하기",
        "adhd": "집중력↓ — 짧은 포모도로(15분) 권장",
    },
    "여포기": {
        "emoji": "🌱",
        "days": "day 6-13",
        "desc": "에스트로겐 상승. 에너지·집중력이 최고조예요!",
        "supplements": "질유산균, 비타민C, 베르베린 챙기기",
        "exercise": "고강도 운동 최적기. 웨이트, HIIT, 새 운동 도전",
        "work": "새 프로젝트 시작, 창의적 브레인스토밍, 학습 최적기",
        "adhd": "에너지 넘침 — 과몰입 주의, 멀티태스킹 경계",
    },
    "배란기": {
        "emoji": "🌸",
        "days": "day 14-16",
        "desc": "LH 급상승. 에너지·자신감이 최고조예요!",
        "supplements": "셀레늄, 크랜베리 챙기기",
        "exercise": "강도 높은 유산소, 그룹 운동, 경쟁적 스포츠",
        "work": "발표, 협업, 중요 미팅, 네트워킹 최적",
        "adhd": "사교성↑ 집중↓ — 중요 결정은 미팅 전에 메모해두기",
    },
    "황체기": {
        "emoji": "🌙",
        "days": "day 17-30",
        "desc": "프로게스테론 우세. 체온↑, 부종·식욕 증가할 수 있어요.",
        "supplements": "마그네슘(칼마디), 크랜베리, 비타민C 강화",
        "exercise": "중강도 유산소, 필라테스, 수영 추천",
        "work": "디테일 작업·마무리 집중. 새 프로젝트는 다음 주기로",
        "adhd": "불안·예민↑ — 타임블로킹, 작업 목록 구체화 도움",
    },
}


def _now_kst() -> datetime:
    return datetime.now(timezone(timedelta(hours=9))).replace(tzinfo=None)


def _today_kst() -> date:
    return _now_kst().date()


class CycleClient:
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
                self._sheet = wb.worksheet("Cycle")
            except gspread.WorksheetNotFound:
                self._sheet = wb.add_worksheet("Cycle", rows=500, cols=len(CYCLE_COLUMNS))
                self._sheet.append_row(CYCLE_COLUMNS)
        return self._sheet

    def get_all(self) -> list[CycleRecord]:
        rows = self._get_sheet().get_all_values()
        return [CycleRecord.from_row(r) for r in rows[1:] if r and r[0]]

    def get_latest(self) -> Optional[CycleRecord]:
        all_records = self.get_all()
        return all_records[-1] if all_records else None

    def start_period(self, date_str: str = None, note: str = "") -> CycleRecord:
        date_str = date_str or _today_kst().isoformat()
        records = self.get_all()
        sheet = self._get_sheet()

        # 이전 주기 cycle_length 자동 계산
        if records:
            prev = records[-1]
            try:
                prev_start = date.fromisoformat(prev.start_date)
                new_start = date.fromisoformat(date_str)
                length = (new_start - prev_start).days
                # 이전 행 cycle_length 업데이트
                for i, row in enumerate(sheet.get_all_values()[1:], start=2):
                    if row and row[0] == prev.id:
                        sheet.update_cell(i, 4, str(length))
                        break
            except Exception:
                pass

        now_str = _now_kst().strftime("%Y-%m-%dT%H:%M:%S")
        record = CycleRecord(
            id=uuid.uuid4().hex[:8],
            start_date=date_str,
            end_date="",
            cycle_length=0,
            note=note,
            created_at=now_str,
        )
        sheet.append_row(record.to_row())
        return record

    def end_period(self, date_str: str = None) -> bool:
        date_str = date_str or _today_kst().isoformat()
        sheet = self._get_sheet()
        latest = self.get_latest()
        if not latest:
            return False
        if latest.end_date:
            return False  # 이미 종료됨
        for i, row in enumerate(sheet.get_all_values()[1:], start=2):
            if row and row[0] == latest.id:
                sheet.update_cell(i, 3, date_str)
                return True
        return False

    def setup_initial(self, start_date: str = "2026-03-17") -> bool:
        """초기 주기 데이터 삽입. 이미 데이터 있으면 False 반환."""
        if self.get_all():
            return False
        now_str = _now_kst().strftime("%Y-%m-%dT%H:%M:%S")
        record = CycleRecord(
            id=uuid.uuid4().hex[:8],
            start_date=start_date,
            end_date="2026-03-21",
            cycle_length=0,
            note="초기 설정",
            created_at=now_str,
        )
        self._get_sheet().append_row(record.to_row())
        return True

    def _estimate_cycle_length(self) -> int:
        records = self.get_all()
        lengths = [r.cycle_length for r in records if r.cycle_length > 0]
        if not lengths:
            return DEFAULT_CYCLE
        return round(sum(lengths[-3:]) / len(lengths[-3:]))

    @staticmethod
    def get_phase(cycle_day: int, period_length: int = DEFAULT_PERIOD) -> str:
        if cycle_day <= period_length:
            return "생리기"
        elif cycle_day <= 13:
            return "여포기"
        elif cycle_day <= 16:
            return "배란기"
        else:
            return "황체기"

    def get_current_status(self) -> dict:
        latest = self.get_latest()
        if not latest:
            return {"error": "생리 기록이 없어요. '생리 시작했어'로 등록해주세요."}

        today = _today_kst()
        try:
            start = date.fromisoformat(latest.start_date)
        except ValueError:
            return {"error": "날짜 형식 오류"}

        cycle_day = (today - start).days + 1
        cycle_length = self._estimate_cycle_length()
        phase = self.get_phase(cycle_day)
        next_period = start + timedelta(days=cycle_length)
        days_until = (next_period - today).days
        in_period = (not latest.end_date) and cycle_day <= DEFAULT_PERIOD

        return {
            "cycle_day": cycle_day,
            "phase": phase,
            "phase_info": PHASE_INFO.get(phase, {}),
            "next_period_date": next_period.isoformat(),
            "days_until_next": days_until,
            "in_period": in_period,
            "pms_alert": cycle_day >= 21,
            "cycle_length": cycle_length,
        }

    @staticmethod
    def format_status(status: dict, inventory_items: list = None) -> str:
        if "error" in status:
            return f"❌ {status['error']}"
        phase = status["phase"]
        info = status.get("phase_info", {})
        emoji = info.get("emoji", "")
        cycle_day = status["cycle_day"]
        next_date = status["next_period_date"][5:].replace("-", "/")  # MM/DD
        days_until = status["days_until_next"]
        days_str = f"{days_until}일 후" if days_until >= 0 else f"{abs(days_until)}일 지남"
        pms_str = "\n⚠️ PMS 구간 — 에프람/뉴프람/인데놀 챙기세요" if status["pms_alert"] else ""

        # 영양제 추천: Inventory 데이터 우선, 없으면 하드코딩 텍스트 fallback
        if inventory_items:
            phase_items = [i for i in inventory_items if i.matches_phase(phase)]
            if phase_items:
                supp_str = ", ".join(i.name for i in phase_items)
            else:
                supp_str = info.get("supplements", "")
        else:
            supp_str = info.get("supplements", "")

        lines = [
            f"{emoji} **{phase}** ({cycle_day}일차)",
            f"📅 다음 생리: {next_date} ({days_str})",
            "",
            f"✨ **이 시기 특징**\n{info.get('desc', '')}",
            f"\n💊 **챙길 영양제**\n{supp_str}",
            f"\n🏃 **운동 방향**\n{info.get('exercise', '')}",
            f"\n💼 **업무/집중 방향**\n{info.get('work', '')}",
            f"\n🧠 **ADHD 팁**\n{info.get('adhd', '')}",
        ]
        if pms_str:
            lines.append(pms_str)
        return "\n".join(lines)
