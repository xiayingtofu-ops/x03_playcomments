import { readFile } from "node:fs/promises";
import path from "node:path";
import { closeDb, transaction } from "../src/db.js";
import { config } from "../src/config.js";
import { upsertBaseAttachment, upsertBaseRecord } from "../src/baseRepository.js";
import { rowToAttachments, rowToRecord } from "../src/feishuBaseTransform.js";

function parseCsv(text) {
  const rows = [];
  let row = [];
  let cell = "";
  let inQuotes = false;

  for (let index = 0; index < text.length; index += 1) {
    const char = text[index];
    const next = text[index + 1];

    if (char === '"' && inQuotes && next === '"') {
      cell += '"';
      index += 1;
    } else if (char === '"') {
      inQuotes = !inQuotes;
    } else if (char === "," && !inQuotes) {
      row.push(cell);
      cell = "";
    } else if ((char === "\n" || char === "\r") && !inQuotes) {
      if (char === "\r" && next === "\n") {
        index += 1;
      }
      row.push(cell);
      if (row.some((value) => value !== "")) {
        rows.push(row);
      }
      row = [];
      cell = "";
    } else {
      cell += char;
    }
  }

  if (cell || row.length) {
    row.push(cell);
    rows.push(row);
  }

  const [headers, ...records] = rows;
  return records.map((values) => Object.fromEntries(headers.map((header, index) => [header, values[index] || ""])));
}

const exportDir = path.resolve(config.baseSync.exportDir);
const recordsRaw = JSON.parse(await readFile(path.join(exportDir, "records_raw.json"), "utf8"));
const statusCsvPath = path.join(exportDir, "attachment_download_status.csv");
let statusByToken = new Map();

try {
  const statusRows = parseCsv(await readFile(statusCsvPath, "utf8"));
  statusByToken = new Map(statusRows.map((row) => [row.file_token, row]));
} catch {
  statusByToken = new Map();
}

const payload = recordsRaw.data;
let recordCount = 0;
let attachmentCount = 0;

await transaction(async (client) => {
  for (let index = 0; index < payload.data.length; index += 1) {
    const row = payload.data[index];
    const recordId = payload.record_id_list[index];
    const record = rowToRecord({
      row,
      rowNo: index + 1,
      recordId,
      fieldIds: payload.field_id_list,
      fieldNames: payload.fields,
      syncSource: "import"
    });
    const saved = await upsertBaseRecord(client, record);
    recordCount += 1;

    const attachments = rowToAttachments({
      row,
      rowNo: index + 1,
      recordId,
      fieldIds: payload.field_id_list,
      fieldNames: payload.fields,
      statusByToken
    });

    for (const attachment of attachments) {
      await upsertBaseAttachment(client, {
        ...attachment,
        baseRecordId: saved.id,
        storageProvider: attachment.localPath ? "local" : "pending"
      });
      attachmentCount += 1;
    }
  }
});

await closeDb();
console.log(JSON.stringify({ importedRecords: recordCount, importedAttachments: attachmentCount }, null, 2));
