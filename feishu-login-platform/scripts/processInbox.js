import { closeDb, transaction } from "../src/db.js";
import { markInboxDone, markInboxFailed, nextPendingInbox, upsertBaseAttachment, upsertBaseRecord } from "../src/baseRepository.js";
import { fetchFeishuRecord } from "../src/feishuBaseSync.js";
import { apiFieldsToAttachments, apiFieldsToRecord, rowToAttachments, rowToRecord } from "../src/feishuBaseTransform.js";

function extractRecordId(event) {
  return (
    event.feishu_record_id ||
    event.payload?.event?.record_id ||
    event.payload?.event?.record?.record_id ||
    event.payload?.event?.records?.[0]?.record_id ||
    event.payload?.record_id ||
    null
  );
}

const rows = await nextPendingInbox(Number(process.env.SYNC_BATCH_SIZE || 20));
let done = 0;
let failed = 0;

for (const row of rows) {
  try {
    const recordId = extractRecordId(row);
    if (!recordId) {
      await markInboxDone(row.id);
      done += 1;
      continue;
    }

    const remote = await fetchFeishuRecord(recordId);
    await transaction(async (client) => {
      const payload = remote.data;
      let saved;
      let attachments;

      if (payload.record?.fields) {
        saved = await upsertBaseRecord(
          client,
          apiFieldsToRecord({
            fields: payload.record.fields,
            recordId,
            syncSource: "feishu_event"
          })
        );
        attachments = apiFieldsToAttachments({
          fields: payload.record.fields,
          recordId
        });
      } else {
        const row = payload.data?.[0];
        if (!row) {
          throw new Error(`No record data returned for ${recordId}`);
        }
        saved = await upsertBaseRecord(
          client,
          rowToRecord({
            row,
            rowNo: null,
            recordId,
            fieldIds: payload.field_id_list,
            fieldNames: payload.fields,
            syncSource: "feishu_event"
          })
        );
        attachments = rowToAttachments({
          row,
          rowNo: null,
          recordId,
          fieldIds: payload.field_id_list,
          fieldNames: payload.fields
        });
      }

      for (const attachment of attachments) {
        await upsertBaseAttachment(client, { ...attachment, baseRecordId: saved.id });
      }
    });
    await markInboxDone(row.id);
    done += 1;
  } catch (error) {
    await markInboxFailed(row.id, error.message || error);
    failed += 1;
  }
}

await closeDb();
console.log(JSON.stringify({ picked: rows.length, done, failed }, null, 2));
