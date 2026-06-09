# X03 Feedback Platform

This folder contains the local feedback platform built from the Feishu Base export.

## Run

```powershell
python platform_server.py
```

Open:

```text
http://127.0.0.1:8765/platform.html
```

## Main Files

- `platform.html`: feedback plaza, submit dialog, notification settings, and planner table UI.
- `platform_server.py`: local API server for submitted feedback, uploads, and notification settings.
- `records_raw.json`: exported Feishu Base records used by the viewer.
- `attachment_download_status.csv`: metadata that maps exported attachments to local files.
- `viewer.html`: read-only exported Base viewer.

Runtime files are intentionally ignored by Git:

- `platform_feedback.db`
- `platform_uploads/`
- `attachments/`
