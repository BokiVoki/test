"""
Google Drive 사진 업로드 클라이언트.
Sheets와 동일한 서비스 계정 credentials 사용.
환경변수: GOOGLE_DRIVE_FOLDER_ID (업로드 대상 폴더 ID, 없으면 루트)
"""
import io
import logging
import os

logger = logging.getLogger(__name__)


def _get_service():
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    creds_path = os.getenv("GOOGLE_CREDENTIALS_PATH", "credentials.json")
    scopes = [
        "https://www.googleapis.com/auth/drive",
        "https://www.googleapis.com/auth/spreadsheets",
    ]
    creds = service_account.Credentials.from_service_account_file(
        creds_path, scopes=scopes
    )
    return build("drive", "v3", credentials=creds)


def is_configured() -> bool:
    creds_path = os.getenv("GOOGLE_CREDENTIALS_PATH", "credentials.json")
    return os.path.exists(creds_path)


def upload_photo(file_bytes: bytes, filename: str, mimetype: str = "image/jpeg") -> str:
    """
    사진을 Google Drive에 업로드하고 공개 뷰 URL을 반환.
    반환: "https://drive.google.com/uc?id={file_id}"
    """
    from googleapiclient.http import MediaIoBaseUpload

    folder_id = os.getenv("GOOGLE_DRIVE_FOLDER_ID", "")
    service = _get_service()

    file_metadata: dict = {"name": filename}
    if folder_id:
        file_metadata["parents"] = [folder_id]

    media = MediaIoBaseUpload(io.BytesIO(file_bytes), mimetype=mimetype, resumable=False)
    file = service.files().create(
        body=file_metadata,
        media_body=media,
        fields="id",
    ).execute()
    file_id = file.get("id")

    # 누구나 볼 수 있도록 권한 설정
    service.permissions().create(
        fileId=file_id,
        body={"type": "anyone", "role": "reader"},
    ).execute()

    return f"https://drive.google.com/uc?id={file_id}"
