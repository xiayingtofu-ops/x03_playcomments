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

## Feishu Login

Copy `.env.example` to `.env`, then fill in the Feishu self-built app credentials:

```powershell
Copy-Item .env.example .env
```

```text
PUBLIC_BASE_URL=http://127.0.0.1:8765
FEISHU_REDIRECT_PATH=/auth/feishu/callback
FEISHU_AUTH_REQUIRED=true
FEISHU_APP_ID=cli_xxxxxxxxxxxxxxxx
FEISHU_APP_SECRET=replace-with-your-app-secret
```

Configure this redirect URL in Feishu Open Platform:

```text
http://127.0.0.1:8765/auth/feishu/callback
```

With `FEISHU_AUTH_REQUIRED=true`, opening or refreshing `/platform.html` redirects to Feishu login before the platform is shown. If credentials are not configured yet, the page stays available and shows a configuration prompt instead of redirecting to a broken login flow.

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
