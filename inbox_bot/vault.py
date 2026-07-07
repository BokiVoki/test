"""
옵시디언 볼트 = GitHub 저장소.
봇이 만든 마크다운 노트를 GitHub Contents API로 커밋한다.

환경변수:
- GITHUB_TOKEN : 볼트 저장소에 쓰기 권한이 있는 Personal Access Token
- VAULT_REPO   : "owner/repo" 형식 (예: "BokiVoki/obsidian-vault")
- VAULT_BRANCH : 기본 "main"
"""
import base64
import logging
import os

import requests

logger = logging.getLogger(__name__)

_API = "https://api.github.com"


def is_configured() -> bool:
    return bool(os.getenv("GITHUB_TOKEN") and os.getenv("VAULT_REPO"))


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {os.getenv('GITHUB_TOKEN')}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _repo() -> str:
    return os.getenv("VAULT_REPO", "")


def _branch() -> str:
    return os.getenv("VAULT_BRANCH", "main")


def write_note(path: str, content: str, commit_msg: str = "") -> str:
    """
    볼트 저장소에 마크다운 파일을 생성/갱신한다.
    같은 path가 이미 있으면 덮어쓴다 (sha 조회 후 업데이트).
    반환: 저장된 파일의 GitHub 웹 URL
    """
    repo = _repo()
    branch = _branch()
    url = f"{_API}/repos/{repo}/contents/{path}"

    # 기존 파일 sha 확인 (있으면 업데이트, 없으면 생성)
    sha = None
    try:
        r = requests.get(url, headers=_headers(), params={"ref": branch}, timeout=15)
        if r.status_code == 200:
            sha = r.json().get("sha")
    except Exception as e:
        logger.warning(f"sha 조회 실패(무시): {e}")

    body = {
        "message": commit_msg or f"inbox: {path}",
        "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
        "branch": branch,
    }
    if sha:
        body["sha"] = sha

    resp = requests.put(url, headers=_headers(), json=body, timeout=20)
    if resp.status_code not in (200, 201):
        raise RuntimeError(f"GitHub 저장 실패 ({resp.status_code}): {resp.text[:300]}")
    return resp.json().get("content", {}).get("html_url", "")


def write_binary(path: str, data: bytes, commit_msg: str = "") -> str:
    """볼트 저장소에 바이너리 파일(이미지 등)을 커밋한다."""
    repo = _repo()
    branch = _branch()
    url = f"{_API}/repos/{repo}/contents/{path}"

    sha = None
    try:
        r = requests.get(url, headers=_headers(), params={"ref": branch}, timeout=15)
        if r.status_code == 200:
            sha = r.json().get("sha")
    except Exception:
        pass

    body = {
        "message": commit_msg or f"inbox: {path}",
        "content": base64.b64encode(data).decode("ascii"),
        "branch": branch,
    }
    if sha:
        body["sha"] = sha

    resp = requests.put(url, headers=_headers(), json=body, timeout=30)
    if resp.status_code not in (200, 201):
        raise RuntimeError(f"이미지 저장 실패 ({resp.status_code}): {resp.text[:200]}")
    return resp.json().get("content", {}).get("html_url", "")


def delete_note(path: str, commit_msg: str = "") -> bool:
    """볼트에서 파일 삭제 (위시리스트로 이동할 때 원본 인박스 노트 제거용)."""
    repo = _repo()
    branch = _branch()
    url = f"{_API}/repos/{repo}/contents/{path}"
    try:
        r = requests.get(url, headers=_headers(), params={"ref": branch}, timeout=15)
        if r.status_code != 200:
            return False
        sha = r.json().get("sha")
        resp = requests.delete(
            url, headers=_headers(),
            json={"message": commit_msg or f"remove {path}", "sha": sha, "branch": branch},
            timeout=20,
        )
        return resp.status_code == 200
    except Exception as e:
        logger.warning(f"노트 삭제 실패: {e}")
        return False


def list_folder(folder: str, prefix: str = "") -> list[str]:
    """폴더의 .md 파일명 목록. prefix로 필터 (파일명에 포함되면 매칭)."""
    repo = _repo()
    branch = _branch()
    url = f"{_API}/repos/{repo}/contents/{folder}"
    try:
        r = requests.get(url, headers=_headers(), params={"ref": branch}, timeout=15)
        if r.status_code != 200:
            return []
        names = [item["name"] for item in r.json()
                 if item.get("type") == "file" and item["name"].endswith(".md")]
    except Exception as e:
        logger.warning(f"{folder} 목록 조회 실패: {e}")
        return []
    if prefix:
        needle = prefix.lower()
        names = [n for n in names if needle in n.lower()]
    return sorted(names, reverse=True)


def list_inbox(prefix: str = "") -> list[str]:
    """Inbox 폴더의 파일명 목록. prefix로 필터."""
    return list_folder("Inbox", prefix)
