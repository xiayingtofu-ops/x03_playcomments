# Feishu Base Sync Platform

This project now contains a PostgreSQL-backed sync layer for the Feishu Base export.

## What Is Stored

- `base_records`: one row per Feishu Base record.
- `base_attachments`: one row per image/video attachment, with local path or object-storage fields.
- `sync_inbox`: Feishu webhook events waiting to be processed.
- `sync_outbox`: platform-originated changes waiting to be pushed back to Feishu Base.

## Local/Server Setup

1. Configure environment:

```text
DATABASE_URL=postgres://postgres:postgres@localhost:5432/feishu_platform
FEISHU_BASE_TOKEN=NeijbKbZFafkxJsTiolcRWGFnEc
FEISHU_BASE_TABLE_ID=tbllTU2raGhLxAW3
FEISHU_BASE_VIEW_ID=vew9FrF9dW
FEISHU_BASE_EXPORT_DIR=C:\Users\Administrator\Documents\New project\feishu_base_export
FEISHU_BASE_SYNC_DRIVER=api
```

2. Create schema:

```bash
npm run db:setup
```

3. Import the current export:

```bash
npm run db:import
```

4. Run the platform:

```bash
npm start
```

## API Surface

- `GET /api/base/records`
- `GET /api/base/records/:recordId`
- `PATCH /api/base/records/:recordId`
- `POST /api/webhooks/feishu/base`

`PATCH /api/base/records/:recordId` accepts:

```json
{
  "fields": {
    "状态": "处理中",
    "备注": "平台侧更新"
  }
}
```

The PATCH endpoint updates PostgreSQL first and creates a `sync_outbox` row. A worker then pushes the change to Feishu.

## Workers

Push platform changes to Feishu:

```bash
npm run sync:outbox
```

Process Feishu webhook events:

```bash
npm run sync:inbox
```

In production, run both commands on a schedule or as long-running workers. The default driver uses Feishu OpenAPI with `tenant_access_token`.

## Required Feishu App Permissions

The API driver needs Feishu app identity scopes. At minimum:

- Read records: `bitable:app:readonly` or `base:record:read`
- Update records: `bitable:app` or the corresponding Base record write permission
- Read/download attachments: Base attachment/media read permissions for the same app

The app also needs access to the target Base. If OpenAPI returns code `99991672`, open the permission link returned in the error, approve the missing scopes, then retry the worker.

## Important Sync Notes

- Feishu Base webhooks are inbound to this platform. Platform-to-Feishu changes should use the Base record update API.
- The outbox table prevents data loss when Feishu writes fail or are rate-limited.
- The inbox table deduplicates webhook events by event id.
- Use `field_hash` and `sync_source` to avoid circular sync loops.
- Attachments should be moved from local storage to S3/OSS/MinIO for production; keep `file_token` as the Feishu-side identity.
