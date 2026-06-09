import crypto from "node:crypto";
import { query, transaction } from "./db.js";
import { config } from "./config.js";

export function stableHash(value) {
  return crypto.createHash("sha256").update(JSON.stringify(value)).digest("hex");
}

function recordParams(record) {
  const rawFields = record.rawFields || {};
  return [
    record.feishuRecordId,
    record.baseToken || config.baseSync.baseToken,
    record.tableId || config.baseSync.tableId,
    record.viewId || config.baseSync.viewId,
    record.rowNo ?? null,
    record.description || "",
    record.priority || "",
    record.status || "",
    record.remark || "",
    record.plannerName || "",
    record.proposerName || "",
    Number(record.likeCount || 0),
    record.feishuCreatedTime || "",
    record.likeUsers || "",
    record.rating || "",
    Number(record.screenshotCount || 0),
    Number(record.problemImageCount || 0),
    Number(record.attachmentCount || 0),
    JSON.stringify(rawFields),
    record.fieldHash || stableHash(rawFields),
    record.syncSource || "import"
  ];
}

export async function upsertBaseRecord(client, record) {
  const result = await client.query(
    `
      INSERT INTO base_records (
        feishu_record_id, base_token, table_id, view_id, row_no,
        description, priority, status, remark, planner_name, proposer_name,
        like_count, feishu_created_time, like_users, rating,
        screenshot_count, problem_image_count, attachment_count,
        raw_fields, field_hash, sync_source, last_sync_at, updated_at
      )
      VALUES (
        $1, $2, $3, $4, $5,
        $6, $7, $8, $9, $10, $11,
        $12, $13, $14, $15,
        $16, $17, $18,
        $19::jsonb, $20, $21, now(), now()
      )
      ON CONFLICT (feishu_record_id) DO UPDATE SET
        base_token = EXCLUDED.base_token,
        table_id = EXCLUDED.table_id,
        view_id = EXCLUDED.view_id,
        row_no = EXCLUDED.row_no,
        description = EXCLUDED.description,
        priority = EXCLUDED.priority,
        status = EXCLUDED.status,
        remark = EXCLUDED.remark,
        planner_name = EXCLUDED.planner_name,
        proposer_name = EXCLUDED.proposer_name,
        like_count = EXCLUDED.like_count,
        feishu_created_time = EXCLUDED.feishu_created_time,
        like_users = EXCLUDED.like_users,
        rating = EXCLUDED.rating,
        screenshot_count = EXCLUDED.screenshot_count,
        problem_image_count = EXCLUDED.problem_image_count,
        attachment_count = EXCLUDED.attachment_count,
        raw_fields = EXCLUDED.raw_fields,
        field_hash = EXCLUDED.field_hash,
        sync_source = EXCLUDED.sync_source,
        last_sync_at = now(),
        updated_at = now(),
        deleted_at = NULL
      RETURNING *
    `,
    recordParams(record)
  );
  return result.rows[0];
}

export async function upsertBaseAttachment(client, attachment) {
  const result = await client.query(
    `
      INSERT INTO base_attachments (
        base_record_id, feishu_record_id, row_no, field_id, field_name,
        file_token, original_name, size_bytes, mime_type, storage_provider,
        storage_bucket, storage_key, local_path, public_url, checksum,
        download_status, download_error, updated_at
      )
      VALUES (
        $1, $2, $3, $4, $5,
        $6, $7, $8, $9, $10,
        $11, $12, $13, $14, $15,
        $16, $17, now()
      )
      ON CONFLICT (file_token) DO UPDATE SET
        base_record_id = EXCLUDED.base_record_id,
        feishu_record_id = EXCLUDED.feishu_record_id,
        row_no = EXCLUDED.row_no,
        field_id = EXCLUDED.field_id,
        field_name = EXCLUDED.field_name,
        original_name = EXCLUDED.original_name,
        size_bytes = EXCLUDED.size_bytes,
        mime_type = COALESCE(EXCLUDED.mime_type, base_attachments.mime_type),
        storage_provider = EXCLUDED.storage_provider,
        storage_bucket = EXCLUDED.storage_bucket,
        storage_key = EXCLUDED.storage_key,
        local_path = EXCLUDED.local_path,
        public_url = EXCLUDED.public_url,
        checksum = COALESCE(EXCLUDED.checksum, base_attachments.checksum),
        download_status = EXCLUDED.download_status,
        download_error = EXCLUDED.download_error,
        updated_at = now()
      RETURNING *
    `,
    [
      attachment.baseRecordId,
      attachment.feishuRecordId,
      attachment.rowNo ?? null,
      attachment.fieldId,
      attachment.fieldName,
      attachment.fileToken,
      attachment.originalName || "",
      attachment.sizeBytes ? Number(attachment.sizeBytes) : null,
      attachment.mimeType || null,
      attachment.storageProvider || "local",
      attachment.storageBucket || null,
      attachment.storageKey || null,
      attachment.localPath || null,
      attachment.publicUrl || null,
      attachment.checksum || null,
      attachment.downloadStatus || "pending",
      attachment.downloadError || ""
    ]
  );
  return result.rows[0];
}

export async function listRecords({ limit = 50, offset = 0, status, priority, planner } = {}) {
  const clauses = ["deleted_at IS NULL"];
  const params = [];

  if (status) {
    params.push(status);
    clauses.push(`status = $${params.length}`);
  }
  if (priority) {
    params.push(priority);
    clauses.push(`priority = $${params.length}`);
  }
  if (planner) {
    params.push(planner);
    clauses.push(`planner_name = $${params.length}`);
  }

  params.push(Math.min(Number(limit) || 50, 200), Number(offset) || 0);
  const result = await query(
    `
      SELECT *
      FROM base_records
      WHERE ${clauses.join(" AND ")}
      ORDER BY row_no NULLS LAST, id
      LIMIT $${params.length - 1}
      OFFSET $${params.length}
    `,
    params
  );

  const count = await query(`SELECT COUNT(*)::int AS count FROM base_records WHERE ${clauses.join(" AND ")}`, params.slice(0, -2));
  return { records: result.rows, total: count.rows[0].count };
}

export async function getRecord(feishuRecordId) {
  const record = await query("SELECT * FROM base_records WHERE feishu_record_id = $1", [feishuRecordId]);
  if (!record.rows[0]) {
    return null;
  }
  const attachments = await query("SELECT * FROM base_attachments WHERE feishu_record_id = $1 ORDER BY field_name, original_name", [
    feishuRecordId
  ]);
  return { ...record.rows[0], attachments: attachments.rows };
}

export async function updatePlatformRecord({ feishuRecordId, fields, createdBy }) {
  return transaction(async (client) => {
    const current = await client.query("SELECT * FROM base_records WHERE feishu_record_id = $1 FOR UPDATE", [feishuRecordId]);
    if (!current.rows[0]) {
      return null;
    }

    const rawFields = { ...(current.rows[0].raw_fields || {}), ...fields };
    const fieldHash = stableHash(rawFields);
    const mapped = mapWritableFields(fields);
    const update = await client.query(
      `
        UPDATE base_records SET
          description = COALESCE($2, description),
          priority = COALESCE($3, priority),
          status = COALESCE($4, status),
          remark = COALESCE($5, remark),
          raw_fields = $6::jsonb,
          field_hash = $7,
          sync_source = 'platform',
          updated_at = now()
        WHERE feishu_record_id = $1
        RETURNING *
      `,
      [
        feishuRecordId,
        mapped.description,
        mapped.priority,
        mapped.status,
        mapped.remark,
        JSON.stringify(rawFields),
        fieldHash
      ]
    );

    const outbox = await client.query(
      `
        INSERT INTO sync_outbox (base_record_id, feishu_record_id, operation, patch_fields, created_by)
        VALUES ($1, $2, 'update', $3::jsonb, $4)
        RETURNING *
      `,
      [update.rows[0].id, feishuRecordId, JSON.stringify(fields), createdBy || ""]
    );

    return { record: update.rows[0], outbox: outbox.rows[0] };
  });
}

export function mapWritableFields(fields) {
  return {
    description: fields.description ?? fields["描述"],
    priority: fields.priority ?? fields["优先级"],
    status: fields.status ?? fields["状态"],
    remark: fields.remark ?? fields["备注"]
  };
}

export async function enqueueInboxEvent({ eventId, eventType, baseToken, tableId, feishuRecordId, payload }) {
  const result = await query(
    `
      INSERT INTO sync_inbox (event_id, event_type, base_token, table_id, feishu_record_id, payload)
      VALUES ($1, $2, $3, $4, $5, $6::jsonb)
      ON CONFLICT (event_id) DO NOTHING
      RETURNING *
    `,
    [eventId, eventType, baseToken || null, tableId || null, feishuRecordId || null, JSON.stringify(payload)]
  );
  return result.rows[0] || null;
}

export async function nextPendingOutbox(limit = 20) {
  const result = await query(
    `
      SELECT *
      FROM sync_outbox
      WHERE status IN ('pending', 'failed') AND next_retry_at <= now()
      ORDER BY created_at
      LIMIT $1
    `,
    [limit]
  );
  return result.rows;
}

export async function markOutboxDone(id) {
  await query("UPDATE sync_outbox SET status = 'done', error = '', pushed_at = now() WHERE id = $1", [id]);
}

export async function markOutboxFailed(id, error) {
  await query(
    `
      UPDATE sync_outbox
      SET status = 'failed',
          retry_count = retry_count + 1,
          next_retry_at = now() + ((retry_count + 1) * interval '1 minute'),
          error = $2
      WHERE id = $1
    `,
    [id, String(error).slice(0, 4000)]
  );
}

export async function nextPendingInbox(limit = 20) {
  const result = await query(
    `
      SELECT *
      FROM sync_inbox
      WHERE status IN ('pending', 'failed')
      ORDER BY received_at
      LIMIT $1
    `,
    [limit]
  );
  return result.rows;
}

export async function markInboxDone(id) {
  await query("UPDATE sync_inbox SET status = 'done', error = '', processed_at = now() WHERE id = $1", [id]);
}

export async function markInboxFailed(id, error) {
  await query(
    "UPDATE sync_inbox SET status = 'failed', retry_count = retry_count + 1, error = $2 WHERE id = $1",
    [id, String(error).slice(0, 4000)]
  );
}
