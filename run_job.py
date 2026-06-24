"""
run_job.py — GitHub Actions entry point for one render job.

Called by render.yml like:
    python run_job.py

Reads JOB_PAYLOAD env var (JSON string set by the workflow from
github.event.client_payload).

Flow:
  1. Parse job payload
  2. Set up working dirs under /tmp/assembler-work
  3. Copy fonts/ and music/ from the repo checkout into working dirs
  4. Download audio MP3 from Google Drive (via audioFileId)
  5. Write keywords_cache/<filename>.json so assembler picks up visual_plan
  6. Call assembler_pro_v2.build_video()
  7. Upload finished MP4 to Google Drive output folder
  8. POST callback to n8n webhook with driveFileId
"""

import os
import sys
import json
import shutil
import requests
from pathlib import Path

# ── 1. Must set env vars BEFORE importing the assembler ──────────────────────
# The assembler reads these at module level to set BASE_DIR and FONTS_DIR.
WORK_DIR = "/tmp/assembler-work"
REPO_DIR = Path(__file__).parent  # root of the GHA checkout

os.environ["ASSEMBLER_BASE_DIR"]   = WORK_DIR
os.environ["ASSEMBLER_FONTS_DIR"]  = str(REPO_DIR / "fonts")

# Now safe to import — globals will be patched correctly.
import assembler_pro_v2 as assembler
from gdrive import download_from_drive, upload_to_drive

# ── 2. Parse job payload ──────────────────────────────────────────────────────
raw_payload = os.environ.get("JOB_PAYLOAD", "")
if not raw_payload:
    print("[FATAL] JOB_PAYLOAD env var is empty. Aborting.")
    sys.exit(1)

try:
    payload = json.loads(raw_payload)
except json.JSONDecodeError as e:
    print(f"[FATAL] JOB_PAYLOAD is not valid JSON: {e}")
    sys.exit(1)

filename     = payload.get("filename", "").strip()
title        = payload.get("title", filename)
body         = payload.get("body", "")
hashtags     = payload.get("hashtags", [])
visual_plan  = payload.get("visual_plan", [])
audio_file_id = payload.get("audioFileId", "").strip()
audio_url     = payload.get("audioUrl", "").strip()

if not filename:
    print("[FATAL] payload.filename is missing.")
    sys.exit(1)

print(f"[run_job] filename={filename}")
print(f"[run_job] title={title}")
print(f"[run_job] visual_plan segments={len(visual_plan)}")

# ── 3. Copy music from repo into working dir ──────────────────────────────────
# Music lives in the repo at ./music/*.mp3|wav|m4a
# The assembler looks for music in MUSIC_DIR = WORK_DIR/music
music_src = REPO_DIR / "music"
music_dst = Path(WORK_DIR) / "music"
music_dst.mkdir(parents=True, exist_ok=True)

if music_src.exists():
    for f in music_src.iterdir():
        if f.suffix.lower() in (".mp3", ".wav", ".m4a"):
            shutil.copy2(f, music_dst / f.name)
            print(f"[run_job] Copied music: {f.name}")
else:
    print("[run_job] WARNING: ./music/ folder not found in repo — video will have no background music")

# ── 4. Download audio from Google Drive ──────────────────────────────────────
audio_dir  = Path(WORK_DIR) / "audio"
audio_dir.mkdir(parents=True, exist_ok=True)
audio_path = audio_dir / f"{filename}.mp3"

if audio_file_id:
    print(f"[run_job] Downloading audio from Drive: {audio_file_id}")
    download_from_drive(audio_file_id, audio_path)
elif audio_url:
    print(f"[run_job] Downloading audio from URL: {audio_url[:60]}...")
    r = requests.get(audio_url, timeout=120, stream=True)
    r.raise_for_status()
    with open(audio_path, "wb") as f:
        for chunk in r.iter_content(chunk_size=256 * 1024):
            if chunk:
                f.write(chunk)
else:
    print("[FATAL] Neither audioFileId nor audioUrl provided in payload.")
    sys.exit(1)

if not audio_path.exists() or audio_path.stat().st_size < 1000:
    print(f"[FATAL] Audio file missing or too small: {audio_path}")
    sys.exit(1)

print(f"[run_job] Audio ready: {audio_path} ({audio_path.stat().st_size // 1024} KB)")

# ── 5. Write keyword cache so assembler picks up the visual_plan ──────────────
# assembler.load_keyword_cache() looks for keywords_cache/<filename>.json
if visual_plan:
    kw_dir = Path(WORK_DIR) / "keywords_cache"
    kw_dir.mkdir(parents=True, exist_ok=True)
    kw_path = kw_dir / f"{filename}.json"
    kw_path.write_text(
        json.dumps({"visual_plan": visual_plan}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"[run_job] Wrote keyword cache: {kw_path}")

# ── 6. Build the job dict and call the assembler ─────────────────────────────
job_dict = {
    "filename":        filename,
    "title":           title,
    "body":            body,
    "hashtags":        hashtags,
    "visual_plan":     visual_plan,
    "audioFile":       str(audio_path),
    "status":          "ready_for_assembly",
}

print("[run_job] Starting assembler.build_video() ...")
output_path = assembler.build_video(job_dict)

if not output_path or not Path(str(output_path)).exists():
    print("[FATAL] assembler.build_video() returned no output.")
    sys.exit(1)

print(f"[run_job] Video rendered: {output_path}")

# ── 7. Upload finished MP4 to Google Drive ────────────────────────────────────
gdrive_folder = os.environ.get("GDRIVE_OUTPUT_FOLDER_ID", "").strip()
drive_file_id = None

if gdrive_folder:
    print(f"[run_job] Uploading to Drive folder: {gdrive_folder}")
    drive_file_id = upload_to_drive(
        local_path=Path(str(output_path)),
        folder_id=gdrive_folder,
        filename=f"{filename}.mp4",
    )
    print(f"[run_job] Drive file ID: {drive_file_id}")
else:
    print("[run_job] GDRIVE_OUTPUT_FOLDER_ID not set — skipping Drive upload")

# ── 8. Callback to n8n ────────────────────────────────────────────────────────
n8n_webhook = os.environ.get("N8N_WEBHOOK_URL", "").strip()

if n8n_webhook:
    callback_data = {
        "filename":    filename,
        "title":       title,
        "driveFileId": drive_file_id,
        "status":      "done",
    }
    print(f"[run_job] Calling n8n webhook...")
    try:
        resp = requests.post(n8n_webhook, json=callback_data, timeout=30)
        print(f"[run_job] n8n response: {resp.status_code}")
    except Exception as e:
        print(f"[run_job] WARNING: n8n callback failed: {e}")
        # Don't exit(1) — video is already on Drive, callback failure is not fatal
else:
    print("[run_job] N8N_WEBHOOK_URL not set — skipping callback")

print("[run_job] All done.")
