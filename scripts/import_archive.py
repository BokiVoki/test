"""
기존 CSV/엑셀 아카이브 파일을 Google Sheets Archive 탭으로 일괄 import하는 스크립트.

사용법:
    python scripts/import_archive.py --file my_archive.csv
    python scripts/import_archive.py --file my_archive.xlsx --dry-run
"""

import argparse
import os
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
# 파일에 있는 컬럼명 → 내부 필드명
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
    # 평점
    "rating": "rating", "평점": "rating", "점수": "rating", "score": "rating",
    # 메모
    "notes": "notes", "메모": "notes", "note": "notes", "감상": "notes", "리뷰": "notes",
    # 태그
    "tags": "tags", "태그": "tags", "tag": "tags",
    # 출처
    "source": "source", "출처": "source", "링크": "source", "link": "source", "url": "source",
    # 날짜
    "date_added": "date_added", "등록일": "date_added", "추가일": "date_added",
    "date_completed": "date_completed", "완료일": "date_completed",
}

# 종류 한글 → 영어 변환
TYPE_KR_TO_EN: dict[str, str] = {v.lower(): k for k, v in CONTENT_TYPE_KR.items()}
TYPE_KR_TO_EN.update({k: k for k in CONTENT_TYPES})

# 상태 한글 → 영어 변환
STATUS_KR_TO_EN: dict[str, str] = {v: k for k, v in STATUS_KR.items()}
STATUS_KR_TO_EN.update({k: k for k in STATUS_VALUES})


def normalize_type(val: str) -> str:
    if not val:
        return "other"
    v = str(val).strip().lower()
    return TYPE_KR_TO_EN.get(v, "other")


def normalize_status(val: str) -> str:
    if not val:
        return "not_started"
    v = str(val).strip()
    return STATUS_KR_TO_EN.get(v, STATUS_KR_TO_EN.get(v.lower(), "not_started"))


def normalize_rating(val) -> float | None:
    if val is None or str(val).strip() in ("", "nan", "None", "-"):
        return None
    try:
        r = float(val)
        return round(min(max(r, 0), 10), 1)
    except (ValueError, TypeError):
        return None


def map_columns(df: pd.DataFrame) -> dict[str, str]:
    """파일의 컬럼명을 내부 필드명으로 매핑 반환"""
    mapping: dict[str, str] = {}
    for col in df.columns:
        key = str(col).strip().lower()
        if key in COLUMN_ALIASES:
            internal = COLUMN_ALIASES[key]
            if internal not in mapping.values():  # 중복 방지
                mapping[col] = internal
    return mapping


def load_file(file_path: str) -> pd.DataFrame:
    p = Path(file_path)
    if not p.exists():
        raise FileNotFoundError(f"파일을 찾을 수 없어요: {file_path}")
    suffix = p.suffix.lower()
    if suffix == ".csv":
        # 인코딩 자동 감지 (utf-8 → cp949 순서)
        for enc in ("utf-8", "utf-8-sig", "cp949", "euc-kr"):
            try:
                return pd.read_csv(file_path, encoding=enc)
            except (UnicodeDecodeError, Exception):
                continue
        raise ValueError("CSV 파일 인코딩을 인식할 수 없어요.")
    elif suffix in (".xlsx", ".xls"):
        return pd.read_excel(file_path)
    else:
        raise ValueError(f"지원하지 않는 파일 형식이에요: {suffix} (csv, xlsx, xls만 가능)")


def build_entry(row: pd.Series, col_map: dict[str, str]) -> ContentEntry:
    data: dict[str, str] = {}
    for col, field in col_map.items():
        val = row.get(col, "")
        data[field] = "" if (val is None or str(val).strip() in ("nan", "None")) else str(val).strip()

    today = str(date.today())
    return ContentEntry(
        id=uuid.uuid4().hex[:8],
        title=data.get("title", "제목 없음"),
        type=normalize_type(data.get("type", "")),
        status=normalize_status(data.get("status", "")),
        progress=data.get("progress", ""),
        rating=normalize_rating(data.get("rating")),
        notes=data.get("notes", ""),
        tags=data.get("tags", ""),
        date_added=data.get("date_added", today),
        date_updated=today,
        date_completed=data.get("date_completed", ""),
        source=data.get("source", ""),
        raw_log="",
    )


def run_import(file_path: str, dry_run: bool = False, skip_duplicates: bool = True):
    print(f"📂 파일 로딩 중: {file_path}")
    df = load_file(file_path)
    print(f"   → {len(df)}행 발견, 컬럼: {list(df.columns)}")

    col_map = map_columns(df)
    if not col_map:
        print("\n⚠️  인식된 컬럼이 없어요.")
        print("파일의 컬럼명과 지원 컬럼 목록을 확인해주세요:")
        print("  제목/title, 종류/type, 상태/status, 진행/progress,")
        print("  평점/rating, 메모/notes, 태그/tags, 출처/source")
        return

    print(f"\n✅ 컬럼 매핑:")
    for col, field in col_map.items():
        print(f"   {col!r} → {field}")

    if "title" not in col_map.values():
        print("\n❌ '제목(title)' 컬럼이 없어요. 가장 중요한 컬럼이에요.")
        return

    if not dry_run:
        spreadsheet_id = os.getenv("SPREADSHEET_ID")
        if not spreadsheet_id:
            raise ValueError("SPREADSHEET_ID 환경변수를 설정해주세요.")
        sheets = SheetsClient(spreadsheet_id=spreadsheet_id)
        existing_titles = [t.lower() for t in sheets.get_titles()]
    else:
        existing_titles = []

    added = 0
    skipped = 0
    errors = 0

    print(f"\n{'[DRY RUN] ' if dry_run else ''}Import 시작...\n")
    for i, (_, row) in enumerate(df.iterrows(), 1):
        try:
            entry = build_entry(row, col_map)
            if not entry.title or entry.title == "제목 없음":
                skipped += 1
                continue

            if skip_duplicates and entry.title.lower() in existing_titles:
                print(f"  SKIP  [{i:3d}] {entry.title} (이미 존재)")
                skipped += 1
                continue

            print(f"  {'(DRY)' if dry_run else 'ADD  '} [{i:3d}] {entry.title} | {entry.type} | {entry.status} | {entry.progress or '-'}")
            if not dry_run:
                sheets.add_entry(entry)
                existing_titles.append(entry.title.lower())
            added += 1
        except Exception as e:
            print(f"  ERROR [{i:3d}] 행 {i}: {e}")
            errors += 1

    print(f"\n{'─'*40}")
    print(f"추가: {added}개 | 스킵: {skipped}개 | 오류: {errors}개")
    if dry_run:
        print("(DRY RUN - 실제로 저장되지 않았어요. --dry-run 없이 재실행하세요.)")


def main():
    parser = argparse.ArgumentParser(description="콘텐츠 아카이브 CSV/엑셀 → Google Sheets import")
    parser.add_argument("--file", "-f", required=True, help="임포트할 CSV 또는 엑셀 파일 경로")
    parser.add_argument("--dry-run", action="store_true", help="실제로 저장하지 않고 결과만 미리 보기")
    parser.add_argument("--no-skip-duplicates", action="store_true", help="중복 제목도 추가")
    args = parser.parse_args()

    run_import(
        file_path=args.file,
        dry_run=args.dry_run,
        skip_duplicates=not args.no_skip_duplicates,
    )


if __name__ == "__main__":
    main()
