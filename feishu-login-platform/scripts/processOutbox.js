import { closeDb } from "../src/db.js";
import { markOutboxDone, markOutboxFailed, nextPendingOutbox } from "../src/baseRepository.js";
import { pushRecordPatchToFeishu } from "../src/feishuBaseSync.js";

const rows = await nextPendingOutbox(Number(process.env.SYNC_BATCH_SIZE || 20));
let done = 0;
let failed = 0;

for (const row of rows) {
  try {
    await pushRecordPatchToFeishu(row.feishu_record_id, row.patch_fields);
    await markOutboxDone(row.id);
    done += 1;
  } catch (error) {
    await markOutboxFailed(row.id, error.message || error);
    failed += 1;
  }
}

await closeDb();
console.log(JSON.stringify({ picked: rows.length, done, failed }, null, 2));
