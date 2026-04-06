import os

import requests

FIGMA_API_BASE = "https://api.figma.com/v1"


def _headers() -> dict:
    return {"X-Figma-Token": os.getenv("FIGMA_TOKEN", "")}


def is_configured() -> bool:
    return bool(os.getenv("FIGMA_TOKEN")) and bool(os.getenv("FIGMA_FILE_KEY"))


def get_components() -> list[dict]:
    """피그마 파일 컴포넌트 목록 조회"""
    file_key = os.getenv("FIGMA_FILE_KEY", "")
    if not file_key:
        return []
    url = f"{FIGMA_API_BASE}/files/{file_key}/components"
    resp = requests.get(url, headers=_headers(), timeout=10)
    resp.raise_for_status()
    return resp.json().get("meta", {}).get("components", [])


def get_styles() -> list[dict]:
    """피그마 파일 스타일 목록 조회"""
    file_key = os.getenv("FIGMA_FILE_KEY", "")
    if not file_key:
        return []
    url = f"{FIGMA_API_BASE}/files/{file_key}/styles"
    resp = requests.get(url, headers=_headers(), timeout=10)
    resp.raise_for_status()
    return resp.json().get("meta", {}).get("styles", [])


def format_components_summary(components: list[dict]) -> str:
    if not components:
        return "등록된 컴포넌트 없음"
    lines = []
    for c in components[:20]:
        name = c.get("name", "")
        desc = c.get("description", "")
        line = f"• {name}"
        if desc:
            line += f" — {desc[:60]}"
        lines.append(line)
    if len(components) > 20:
        lines.append(f"... 외 {len(components) - 20}개")
    return "\n".join(lines)


def format_styles_summary(styles: list[dict]) -> str:
    if not styles:
        return "등록된 스타일 없음"
    lines = []
    for s in styles[:15]:
        name = s.get("name", "")
        style_type = s.get("style_type", "")
        lines.append(f"• [{style_type}] {name}")
    return "\n".join(lines)
