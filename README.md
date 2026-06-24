# 90SecondFinance Assembler — GitHub Actions

Renders YouTube Shorts automatically, triggered by n8n via repository_dispatch.

## Repo structure

```
.github/workflows/render.yml   ← GHA workflow
fonts/
  Montserrat-BlackItalic.ttf   ← commit your font here
music/
  track1.mp3                   ← commit your background music here
assembler_pro_v2.py            ← core assembler (patched for Linux)
run_job.py                     ← GHA entry point
gdrive.py                      ← Google Drive upload/download
requirements.txt
```

## GitHub Secrets required

Set these under repo Settings → Secrets and variables → Actions:

| Secret | Value |
|--------|-------|
| `PEXELS_API_KEY` | Your Pexels API key |
| `GOOGLE_SERVICE_JSON` | Full contents of service account JSON (one line) |
| `GDRIVE_OUTPUT_FOLDER_ID` | Drive folder ID for finished MP4s |
| `N8N_WEBHOOK_URL` | Your n8n webhook URL (receives driveFileId when done) |

## n8n trigger (HTTP Request node)

```
Method: POST
URL: https://api.github.com/repos/YOUR_USERNAME/YOUR_REPO/dispatches
Header: Authorization: Bearer YOUR_GITHUB_PAT
Header: Accept: application/vnd.github.v3+json
Body:
{
  "event_type": "render_job",
  "client_payload": {
    "filename": "{{ $json.filename }}",
    "title": "{{ $json.title }}",
    "body": "{{ $json.body }}",
    "hashtags": {{ $json.hashtags }},
    "visual_plan": {{ $json.visual_plan }},
    "audioFileId": "{{ $json.audioFileId }}"
  }
}
```

n8n receives the callback at your Webhook node when rendering is complete:
```json
{ "filename": "...", "title": "...", "driveFileId": "1xABC...", "status": "done" }
```

Use `driveFileId` in your YouTube upload step.
