# Feishu Base local sync

This folder contains a small local mirror for the Feishu Wiki/Base link:

- Source Wiki token: `Z10Ow6rMgiSAISkWfLScdwU6nXe`
- Wiki title: `X03开荒版本测试反馈清单`
- Resolved Base token: `PQTsbEtUPaV80isZ5dRcVFKsn6f`
- Table ID: `tblYhznxxvRtSyY9`
- View ID: `vew9FrF9dW`

## Files

- `feishu_base_sync.py`: fetches records through `lark-cli` and stores them locally.
- `run_feishu_base_sync.ps1`: PowerShell launcher.
- `feishu_base_records.sqlite`: SQLite database created after a successful sync.
- `feishu_base_records.csv`: CSV export created after a successful sync.

## Local tables

The SQLite database has these main tables:

- `records`: one row per Feishu record, with full `fields_json` and raw response JSON.
- `record_fields`: one row per record field, useful for simple local querying.
- `sync_runs`: history of sync attempts.
- `sync_meta`: last successful sync marker.

Records that disappear from the source view/table are retained in `records` with `is_deleted = 1`.

## Run once

```powershell
.\run_feishu_base_sync.ps1 -Once
```

## Keep syncing

```powershell
.\run_feishu_base_sync.ps1 -IntervalSeconds 30
```

This is polling-based. The current local Lark event bus does not expose Base or Bitable record-change events, so true push-style real-time sync is not available from the enabled tools. A short polling interval gives near-real-time updates.

## Current blocker

The initial read is blocked because the installed Feishu connector app has not applied for these Base scopes:

- `base:record:read`
- `base:field:read`

The connector returned:

```text
access denied: app cli_feishu_connector has not applied for the required scope(s)
```

This cannot be fixed by personal re-authorization alone. A Feishu app/admin owner needs to enable the missing scopes for `cli_feishu_connector`; after that, run the sync command again.

## Snapshot fallback

If the Base record API scopes are not available, you can export the linked table as a CSV snapshot after granting these user scopes:

- `drive:export:readonly`
- `docs:document:export`

Export command:

```powershell
New-Item -ItemType Directory -Force -Path .\exports | Out-Null
& "C:\Users\Administrator\plugins\lark-enterprise\scripts\feishu-connector.cmd" lark-cli -- drive +export `
  --token PQTsbEtUPaV80isZ5dRcVFKsn6f `
  --doc-type bitable `
  --file-extension csv `
  --sub-id tblYhznxxvRtSyY9 `
  --file-name feishu_base_export.csv `
  --output-dir .\exports `
  --overwrite `
  --as user
```

The snapshot fallback is useful for a one-time local copy, but it does not provide durable record IDs. The polling sync script remains the better long-running mirror once `base:record:read` is enabled.
