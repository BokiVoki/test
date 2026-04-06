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
