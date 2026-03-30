"""
기존 CSV/엑셀 아카이브 파일을 Google Sheets Archive 탭으로 일괄 import하는 스크립트.

사용법:
    python scripts/import_archive.py --file my_archive.csv
    python scripts/import_archive.py --file my_archive.xlsx --dry-run
    python scripts/import_archive.py --file data/archive.csv
"""

import argparse
import os
import re
import sys
import uuid
from datetime import date
from pathlib import Path

# 프로젝트 루트를 sys.path에 추가
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

import pandas as pd

from bot.models import ContentEntry, CONTENT_TYPES, STATUS_VALUES, CONTENT_TYPE_KR, STATUS_KR
from bot.sheets import SheetsClient


# ─── 컬럼 이름 자동 매핑 ──────────────────────────────────────────────────────
COLUMN_ALIASES: dict[str, str] = {
    # 제목
    "title": "title", "제목": "title", "이름": "title", "name": "title",
    # 종류
    "type": "type", "종류": "type", "카테고리": "type", "category": "type", "분류": "type",
    # 상태
    "status": "status", "상태": "status",
    # 진행
    "progress": "progress", "진행": "progress", "현재": "progress", "회차": "progress",
    "챕터": "progress", "chapter": "progress", "episode": "progress", "ep": "progress",
    "page": "progress", "페이지": "progress",
    # 평점 (별점 emoji 포함)
    "rating": "rating", "평점": "rating", "점수": "rating", "score": "rating",
    "score /5": "rating", "score/5": "rating", "별점": "rating",
    # 메모/요약
    "notes": "notes", "메모": "notes", "note": "notes", "감상": "notes",
    "리뷰": "notes", "summary": "notes", "요약": "notes",
    # 태그
    "tags": "tags", "태그": "tags", "tag": "tags",
    # 출처/링크
    "source": "source", "출처": "source", "링크": "source", "link": "source", "url": "source",
    # 날짜
    "date_added": "date_added", "등록일": "date_added", "추가일": "date_added",
    "created time": "date_added", "created_time": "date_added", "날짜": "date_added",
    "date_completed": "date_completed", "완료일": "date_completed",
    # 발행/출판사
    "publisher": "publisher",
    # 제작자/참여자
    "만들고 참여한 사람": "creator", "creator": "creator",
    "감독": "creator", "저자": "creator", "작가": "creator",
    # 연도 (notes에 추가)
    "year": "year",
}

# ─── 종류 매핑 ───────────────────────────────────────────────────────────────
TYPE_MAP: dict[str, str] = {
    # 한글
    **{v.lower(): k for k, v in CONTENT_TYPE_KR.items()},
    # 영문
    "film": "movie",
    "movie": "movie",
    "book": "book",
    "tv series": "drama",
    "tv_series": "drama",
    "drama": "drama",
    "anime": "anime",
    "animation": "anime",
    "manga": "manga",
    "webtoon": "webtoon",
    "article": "article",
    "podcast": "podcast",
    "game": "game",
    "graphic book": "graphic_book",
    "graphic_book": "graphic_book",
    "graphicbook": "graphic_book",
    "documentary": "documentary",
    "exhibition": "exhibition",
    "전시": "exhibition",
    "program": "other",
    "theatre": "other",
    "theater": "other",
    "youtube": "article",
    "other": "other",
    # 내부값 그대로 통과
    **{k: k for k in CONTENT_TYPES},
}

# ─── 상태 매핑 ───────────────────────────────────────────────────────────────
STATUS_MAP: dict[str, str] = {
    **{v: k for k, v in STATUS_KR.items()},
    **{k: k for k in STATUS_VALUES},
    "완주": "completed",
    "진행중": "in_progress",
    "진행 중": "in_progress",
    "궁금": "not_started",
    "보류": "on_hold",
    "중단": "dropped",
    "dropped": "dropped",
}


def parse_star_rating(val) -> float | None:
    """⭐⭐⭐ → 6.0, SUPERSUPER → 10.0, 빈값 → None"""
    if val is None or str(val).strip() in ("", "nan", "None", "-"):
        return None
    val = str(val).strip()
    # SUPERSUPER = 최고점
    if val.upper() in ("SUPERSUPER", "SUPER SUPER"):
        return 10.0
    # 별점 카운팅
    stars = val.count("⭐")
    if stars > 0:
        return float(stars * 2)  # /5 → /10 변환
    # 숫자 직접 입력
    try:
        r = float(val)
        # /5 스케일인 경우 /10으로 변환
        if r <= 5:
            return round(r * 2, 1)
        return round(min(r, 10), 1)
    except (ValueError, TypeError):
        return None


def parse_korean_date(val: str) -> str:
    """
    "2020년 3월 17일 오전 9:52" → "2020-03-17"
    """
    m = re.match(r"(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일", str(val))
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    # ISO 날짜
    m2 = re.match(r"(\d{4})-(\d{2})-(\d{2})", str(val))
    if m2:
        return m2.group(0)
    return ""


def normalize_type(val: str) -> str:
    if not val or str(val).strip() in ("", "nan", "None"):
        return "other"
    # 복수 종류 처리: "Book, Film" → 첫 번째 사용
    first = str(val).split(",")[0].strip()
    return TYPE_MAP.get(first.lower(), "other")


def normalize_status(val: str) -> str:
    if not val or str(val).strip() in ("", "nan", "None"):
        return "not_started"
    v = str(val).strip()
    return STATUS_MAP.get(v, STATUS_MAP.get(v.lower(), "not_started"))


def map_columns(df: pd.DataFrame) -> dict[str, str]:
    """파일의 컬럼명을 내부 필드명으로 매핑"""
    mapping: dict[str, str] = {}
    for col in df.columns:
        key = str(col).strip().lower()
        if key in COLUMN_ALIASES:
            internal = COLUMN_ALIASES[key]
            # 같은 내부 필드로 여러 컬럼이 매핑되는 경우 첫 번째만 사용
            if internal not in mapping.values():
                mapping[col] = internal
    return mapping


def load_file(file_path: str) -> pd.DataFrame:
    p = Path(file_path)
    if not p.exists():
        raise FileNotFoundError(f"파일을 찾을 수 없어요: {file_path}")
    suffix = p.suffix.lower()
    if suffix == ".csv":
        for enc in ("utf-8", "utf-8-sig", "cp949", "euc-kr"):
            try:
                return pd.read_csv(file_path, encoding=enc)
            except (UnicodeDecodeError, Exception):
                continue
        raise ValueError("CSV 파일 인코딩을 인식할 수 없어요.")
    elif suffix in (".xlsx", ".xls"):
        return pd.read_excel(file_path)
    else:
        raise ValueError(f"지원하지 않는 파일 형식: {suffix} (csv, xlsx, xls만 가능)")


def build_entry(row: pd.Series, col_map: dict[str, str]) -> ContentEntry:
    raw: dict[str, str] = {}
    for col, field in col_map.items():
        val = row.get(col, "")
        raw[field] = "" if (val is None or str(val).strip() in ("nan", "None")) else str(val).strip()

    today = str(date.today())

    # 날짜 파싱
    date_added = parse_korean_date(raw.get("date_added", "")) or today
    date_completed = parse_korean_date(raw.get("date_completed", "")) if raw.get("date_completed") else ""

    # 평점 파싱 (별점 or SUPERSUPER)
    rating = parse_star_rating(raw.get("rating"))

    # notes 조합: 원본 summary + creator + publisher + year
    notes_parts = []
    if raw.get("notes"):
        notes_parts.append(raw["notes"])
    if raw.get("creator"):
        notes_parts.append(f"참여: {raw['creator']}")
    if raw.get("publisher"):
        notes_parts.append(f"출판: {raw['publisher']}")
    if raw.get("year"):
        notes_parts.append(f"연도: {raw['year']}")
    notes = "\n".join(notes_parts)

    # 완료 날짜 자동 설정
    status = normalize_status(raw.get("status", ""))
    if status == "completed" and not date_completed:
        date_completed = date_added  # 등록일을 완료일로 fallback

    return ContentEntry(
        id=uuid.uuid4().hex[:8],
        title=raw.get("title", "제목 없음"),
        type=normalize_type(raw.get("type", "")),
        status=status,
        progress=raw.get("progress", ""),
        rating=rating,
        notes=notes,
        tags=raw.get("tags", ""),
        date_added=date_added,
        date_updated=today,
        date_completed=date_completed,
        source=raw.get("source", ""),
        raw_log="",
    )


def run_import(file_path: str, dry_run: bool = False, skip_duplicates: bool = True):
    print(f"📂 파일 로딩 중: {file_path}")
    df = load_file(file_path)
    # 빈 행 제거
    df = df.dropna(how="all")
    print(f"   → {len(df)}행 발견, 컬럼: {list(df.columns)}")

    col_map = map_columns(df)
    if not col_map:
        print("\n⚠️  인식된 컬럼이 없어요.")
        print("지원 컬럼명: 제목/title/name, 종류/type, 상태/status,")
        print("  score/5/평점, summary/메모, created time/날짜,")
        print("  만들고 참여한 사람/creator, publisher")
        return

    print(f"\n✅ 컬럼 매핑:")
    for col, field in col_map.items():
        print(f"   {col!r} → {field}")

    if "title" not in col_map.values():
        print("\n❌ '제목(title/name)' 컬럼이 없어요.")
        return

    if not dry_run:
        spreadsheet_id = os.getenv("SPREADSHEET_ID")
        if not spreadsheet_id:
            raise ValueError("SPREADSHEET_ID 환경변수를 설정해주세요.")
        sheets = SheetsClient(spreadsheet_id=spreadsheet_id)
        existing_titles = [t.lower() for t in sheets.get_titles()]
    else:
        existing_titles = []

    added = skipped = errors = 0

    print(f"\n{'[DRY RUN] ' if dry_run else ''}Import 시작...\n")
    for i, (_, row) in enumerate(df.iterrows(), 1):
        try:
            entry = build_entry(row, col_map)
            if not entry.title or entry.title in ("제목 없음", ""):
                skipped += 1
                continue

            if skip_duplicates and entry.title.lower() in existing_titles:
                print(f"  SKIP  [{i:3d}] {entry.title}")
                skipped += 1
                continue

            rating_str = f"⭐{entry.rating}" if entry.rating else "-"
            print(
                f"  {'(DRY)' if dry_run else 'ADD  '} [{i:3d}] "
                f"{entry.title[:30]:<30} | {entry.type:<12} | {entry.status:<12} | {rating_str}"
            )

            if not dry_run:
                sheets.add_entry(entry)
                existing_titles.append(entry.title.lower())
            added += 1
        except Exception as e:
            print(f"  ERROR [{i:3d}] 행 {i}: {e}")
            errors += 1

    print(f"\n{'─'*50}")
    print(f"추가: {added}개 | 스킵: {skipped}개 | 오류: {errors}개")
    if dry_run:
        print("(DRY RUN — 실제로 저장되지 않았어요. --dry-run 없이 재실행하세요.)")


def main():
    parser = argparse.ArgumentParser(description="콘텐츠 아카이브 CSV/엑셀 → Google Sheets import")
    parser.add_argument("--file", "-f", required=True, help="CSV 또는 엑셀 파일 경로")
    parser.add_argument("--dry-run", action="store_true", help="미리보기 (실제 저장 안 함)")
    parser.add_argument("--no-skip-duplicates", action="store_true", help="중복 제목도 추가")
    args = parser.parse_args()

    run_import(
        file_path=args.file,
        dry_run=args.dry_run,
        skip_duplicates=not args.no_skip_duplicates,
    )


if __name__ == "__main__":
    main()
