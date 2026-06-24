"""
gdrive.py — Google Drive helper (used by run_job.py)

Auth: service account JSON pasted into GOOGLE_SERVICE_JSON env var.
"""

import os
import json
from pathlib import Path

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

SCOPES = ["https://www.googleapis.com/auth/drive"]


def _service():
    raw = os.environ.get("GOOGLE_SERVICE_JSON", "").strip()
    if not raw:
        raise RuntimeError("GOOGLE_SERVICE_JSON env var not set.")
    info = json.loads(raw)
    creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def upload_to_drive(local_path: Path, folder_id: str, filename: str) -> str:
    """Upload file → Drive folder. Returns file ID."""
    svc = _service()
    media = MediaFileUpload(str(local_path), mimetype="video/mp4", resumable=True)
    f = svc.files().create(
        body={"name": filename, "parents": [folder_id]},
        media_body=media,
        fields="id",
    ).execute()
    file_id = f.get("id")
    print(f"  [GDrive] Uploaded {filename} → {file_id}")
    return file_id


def download_from_drive(file_id: str, dest_path: Path) -> None:
    """Download Drive file by ID to local path."""
    svc = _service()
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    req = svc.files().get_media(fileId=file_id)
    with open(dest_path, "wb") as fh:
        dl = MediaIoBaseDownload(fh, req)
        done = False
        while not done:
            _, done = dl.next_chunk()
    print(f"  [GDrive] Downloaded {file_id} → {dest_path}")
