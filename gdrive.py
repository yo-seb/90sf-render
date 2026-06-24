"""
gdrive.py — Google Drive helper for GHA

Downloads use the service account (read access to audio/ folder).
Uploads use OAuth (personal account) because service accounts
have no storage quota on personal My Drive.
"""

import os
import json
from pathlib import Path

from google.oauth2 import service_account
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

SCOPES = ["https://www.googleapis.com/auth/drive"]


def _service_account_service():
    raw = os.environ.get("GOOGLE_SERVICE_JSON", "").strip()
    if not raw:
        raise RuntimeError("GOOGLE_SERVICE_JSON env var not set.")
    info = json.loads(raw)
    creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def _oauth_service():
    creds = Credentials(
        token=None,
        refresh_token=os.environ.get("GDRIVE_REFRESH_TOKEN"),
        client_id=os.environ.get("GDRIVE_OAUTH_CLIENT_ID"),
        client_secret=os.environ.get("GDRIVE_OAUTH_CLIENT_SECRET"),
        token_uri="https://oauth2.googleapis.com/token",
        scopes=SCOPES,
    )
    # Force a refresh to get a valid access token
    creds.refresh(Request())
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def upload_to_drive(local_path: Path, folder_id: str, filename: str) -> str:
    """Upload file to Drive as the OAuth user. Returns file ID."""
    svc = _oauth_service()
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
    """Download Drive file via service account. Returns nothing."""
    svc = _service_account_service()
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    req = svc.files().get_media(fileId=file_id)
    with open(dest_path, "wb") as fh:
        dl = MediaIoBaseDownload(fh, req)
        done = False
        while not done:
            _, done = dl.next_chunk()
    print(f"  [GDrive] Downloaded {file_id} → {dest_path}")
