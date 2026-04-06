from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class BotMode(Enum):
    SECRETARY = "secretary"
    FINANCE = "finance"
    CONSULTANT = "consultant"
    INSTAGRAM = "instagram"


CONTENT_TYPES = [
    "book", "webtoon", "drama", "movie", "anime",
    "manga", "article", "podcast", "game",
    "graphic_book", "documentary", "exhibition", "other"
]

CONTENT_TYPE_KR = {
    "book": "책",
    "webtoon": "웹툰",
    "drama": "드라마",
    "movie": "영화",
    "anime": "애니",
    "manga": "만화",
    "article": "아티클",
    "podcast": "팟캐스트",
    "game": "게임",
    "graphic_book": "그래픽북",
    "documentary": "다큐",
    "exhibition": "전시",
    "other": "기타",
}

STATUS_VALUES = ["not_started", "in_progress", "completed", "dropped", "on_hold"]

STATUS_KR = {
    "not_started": "시작 전",
    "in_progress": "진행 중",
    "completed": "완료",
    "dropped": "중단",
    "on_hold": "보류",
}

# Archive 시트 컬럼 순서 (A~M)
SHEET_COLUMNS = [
    "id", "title", "type", "status", "progress",
    "rating", "notes", "tags", "date_added", "date_updated",
    "date_completed", "source", "raw_log"
]


@dataclass
class ContentEntry:
    id: str = ""
    title: str = ""
    type: str = "other"
    status: str = "not_started"
    progress: str = ""
    rating: Optional[float] = None
    notes: str = ""
    tags: str = ""
    date_added: str = ""
    date_updated: str = ""
    date_completed: str = ""
    source: str = ""
    raw_log: str = ""

    def to_row(self) -> list:
        return [
            self.id,
            self.title,
            self.type,
            self.status,
            self.progress,
            self.rating if self.rating is not None else "",
            self.notes,
            self.tags,
            self.date_added,
            self.date_updated,
            self.date_completed,
            self.source,
            self.raw_log,
        ]

    @classmethod
    def from_row(cls, row: list) -> "ContentEntry":
        # 부족한 컬럼은 빈 문자열로 채움
        padded = (row + [""] * len(SHEET_COLUMNS))[:len(SHEET_COLUMNS)]
        rating = None
        if padded[5] not in ("", None):
            try:
                rating = float(padded[5])
            except (ValueError, TypeError):
                pass
        return cls(
            id=padded[0],
            title=padded[1],
            type=padded[2] or "other",
            status=padded[3] or "not_started",
            progress=padded[4],
            rating=rating,
            notes=padded[6],
            tags=padded[7],
            date_added=padded[8],
            date_updated=padded[9],
            date_completed=padded[10],
            source=padded[11],
            raw_log=padded[12],
        )

    def summary(self) -> str:
        parts = [f"**{self.title}**"]
        if self.type in CONTENT_TYPE_KR:
            parts.append(f"({CONTENT_TYPE_KR[self.type]})")
        if self.progress:
            parts.append(f"· {self.progress}")
        if self.rating is not None:
            parts.append(f"· ⭐{self.rating}")
        if self.status in STATUS_KR:
            parts.append(f"· {STATUS_KR[self.status]}")
        return " ".join(parts)


REMINDER_COLUMNS = ["id", "text", "trigger_at", "repeat", "active", "created_at"]


@dataclass
class Reminder:
    id: str = ""
    text: str = ""
    trigger_at: str = ""   # "2024-01-15T09:00:00" KST
    repeat: str = "none"   # none / daily / weekly / monthly
    active: bool = True
    created_at: str = ""

    def to_row(self) -> list:
        return [
            self.id,
            self.text,
            self.trigger_at,
            self.repeat,
            "1" if self.active else "0",
            self.created_at,
        ]

    @classmethod
    def from_row(cls, row: list) -> "Reminder":
        padded = (row + [""] * 6)[:6]
        return cls(
            id=padded[0],
            text=padded[1],
            trigger_at=padded[2],
            repeat=padded[3] or "none",
            active=(padded[4] != "0"),
            created_at=padded[5],
        )


MEMO_COLUMNS = ["id", "mode", "content", "created_at"]


@dataclass
class MemoEntry:
    id: str = ""
    mode: str = ""        # secretary / finance / consultant
    content: str = ""
    created_at: str = ""

    def to_row(self) -> list:
        return [self.id, self.mode, self.content, self.created_at]

    @classmethod
    def from_row(cls, row: list) -> "MemoEntry":
        padded = (row + [""] * 4)[:4]
        return cls(id=padded[0], mode=padded[1], content=padded[2], created_at=padded[3])


TODO_COLUMNS = ["id", "text", "done", "due_date", "created_at", "trigger_at", "repeat"]


@dataclass
class TodoItem:
    id: str = ""
    text: str = ""
    done: bool = False
    due_date: str = ""    # "2024-01-15" or "" — 마감일 표시용
    created_at: str = ""
    trigger_at: str = ""  # "2024-01-15T09:00:00" KST — 알람 시각 (없으면 "")
    repeat: str = "none"  # none / daily / weekly / monthly

    def to_row(self) -> list:
        return [
            self.id, self.text, "1" if self.done else "0",
            self.due_date, self.created_at, self.trigger_at, self.repeat,
        ]

    @classmethod
    def from_row(cls, row: list) -> "TodoItem":
        padded = (row + [""] * 7)[:7]
        return cls(
            id=padded[0], text=padded[1],
            done=(padded[2] == "1"),
            due_date=padded[3], created_at=padded[4],
            trigger_at=padded[5], repeat=padded[6] or "none",
        )


# ── 영양제/약 재고 ──────────────────────────────────────────────
INVENTORY_COLUMNS = ["id", "name", "category", "qty", "low_threshold", "daily", "note", "updated_at"]


@dataclass
class InventoryItem:
    id: str = ""
    name: str = ""            # "비타민C", "아토목세틴 18mg"
    category: str = "daily"  # daily / situational / prescription / pms
    qty: int = 0
    low_threshold: int = 7
    daily: bool = False       # 매일 복용 여부 (데일리 투두 생성 기준)
    note: str = ""
    updated_at: str = ""

    def to_row(self) -> list:
        return [
            self.id, self.name, self.category, str(self.qty),
            str(self.low_threshold), "1" if self.daily else "0",
            self.note, self.updated_at,
        ]

    @classmethod
    def from_row(cls, row: list) -> "InventoryItem":
        padded = (row + [""] * 8)[:8]
        try:
            qty = int(padded[3])
        except (ValueError, TypeError):
            qty = 0
        try:
            low = int(padded[4])
        except (ValueError, TypeError):
            low = 7
        return cls(
            id=padded[0], name=padded[1], category=padded[2] or "daily",
            qty=qty, low_threshold=low,
            daily=(padded[5] == "1"),
            note=padded[6], updated_at=padded[7],
        )


# ── 복용 기록 ────────────────────────────────────────────────────
INTAKE_LOG_COLUMNS = ["id", "item_name", "qty_taken", "qty_after", "taken_at", "note"]


@dataclass
class IntakeLogItem:
    id: str = ""
    item_name: str = ""
    qty_taken: int = 1
    qty_after: int = 0
    taken_at: str = ""   # "2026-04-06T09:00:00" KST
    note: str = ""       # "포모도로", "집중체크인", "수면", "음수" 등

    def to_row(self) -> list:
        return [
            self.id, self.item_name, str(self.qty_taken),
            str(self.qty_after), self.taken_at, self.note,
        ]

    @classmethod
    def from_row(cls, row: list) -> "IntakeLogItem":
        padded = (row + [""] * 6)[:6]
        try:
            qt = int(padded[2])
        except (ValueError, TypeError):
            qt = 1
        try:
            qa = int(padded[3])
        except (ValueError, TypeError):
            qa = 0
        return cls(
            id=padded[0], item_name=padded[1],
            qty_taken=qt, qty_after=qa,
            taken_at=padded[4], note=padded[5],
        )


# ── 생리주기 ─────────────────────────────────────────────────────
CYCLE_COLUMNS = ["id", "start_date", "end_date", "cycle_length", "note", "created_at"]


@dataclass
class CycleRecord:
    id: str = ""
    start_date: str = ""   # "2026-03-17"
    end_date: str = ""     # "2026-03-21" or "" (진행 중)
    cycle_length: int = 0  # 다음 주기까지 일수 (0 = 미확정)
    note: str = ""
    created_at: str = ""

    def to_row(self) -> list:
        return [
            self.id, self.start_date, self.end_date,
            str(self.cycle_length), self.note, self.created_at,
        ]

    @classmethod
    def from_row(cls, row: list) -> "CycleRecord":
        padded = (row + [""] * 6)[:6]
        try:
            cl = int(padded[3])
        except (ValueError, TypeError):
            cl = 0
        return cls(
            id=padded[0], start_date=padded[1], end_date=padded[2],
            cycle_length=cl, note=padded[4], created_at=padded[5],
        )


@dataclass
class ParsedIntent:
    action: str = "unknown"
    # record_progress / add_new / mark_status / rate / query / recommend / note / unknown
    title: Optional[str] = None
    content_type: Optional[str] = None
    progress: Optional[str] = None
    status: Optional[str] = None
    rating: Optional[float] = None
    note: Optional[str] = None
    query_text: Optional[str] = None
    recommend_context: Optional[str] = None
    confidence: float = 1.0
    raw_message: str = ""
