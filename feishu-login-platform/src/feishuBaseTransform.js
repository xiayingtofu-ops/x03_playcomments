import crypto from "node:crypto";
import { config } from "./config.js";

export const FIELD_IDS = {
  description: "fldHy9HQO9",
  screenshot: "fldmlme6mj",
  priority: "flddZqPy4y",
  status: "fldxB7QmPi",
  remark: "fldPTWrwky",
  planner: "fldubdP27N",
  proposer: "fldqXT3xTr",
  likeCount: "fldP1f1suK",
  createdTime: "fldvRZllDS",
  likeUsers: "fldoj9ibxW",
  problemImage: "fldftLhjJK",
  rating: "fldbBFvDCK"
};

export const FIELD_NAMES = {
  description: "描述",
  screenshot: "截图",
  priority: "优先级",
  status: "状态",
  remark: "备注",
  planner: "策划",
  proposer: "提出人",
  likeCount: "点赞人数",
  createdTime: "创建时间",
  likeUsers: "点赞/取消人员列表",
  problemImage: "问题描述图片",
  rating: "评分"
};

export function stableHash(value) {
  return crypto.createHash("sha256").update(JSON.stringify(value)).digest("hex");
}

export function scalar(value) {
  if (value === null || value === undefined) {
    return "";
  }
  if (!Array.isArray(value)) {
    return String(value);
  }
  return value
    .map((item) => {
      if (item && typeof item === "object") {
        return item.name || item.id || JSON.stringify(item);
      }
      return String(item);
    })
    .join(", ");
}

export function rowToRecord({ row, rowNo, recordId, fieldIds, fieldNames, syncSource = "import" }) {
  const idToName = Object.fromEntries(fieldIds.map((fieldId, index) => [fieldId, fieldNames[index]]));
  const cellById = Object.fromEntries(fieldIds.map((fieldId, index) => [fieldId, row[index] ?? null]));
  const rawFields = Object.fromEntries(Object.entries(cellById).map(([fieldId, value]) => [idToName[fieldId] || fieldId, value]));
  const screenshots = cellById[FIELD_IDS.screenshot] || [];
  const problemImages = cellById[FIELD_IDS.problemImage] || [];

  return {
    feishuRecordId: recordId,
    baseToken: config.baseSync.baseToken,
    tableId: config.baseSync.tableId,
    viewId: config.baseSync.viewId,
    rowNo,
    description: scalar(cellById[FIELD_IDS.description]),
    priority: scalar(cellById[FIELD_IDS.priority]),
    status: scalar(cellById[FIELD_IDS.status]),
    remark: scalar(cellById[FIELD_IDS.remark]),
    plannerName: scalar(cellById[FIELD_IDS.planner]),
    proposerName: scalar(cellById[FIELD_IDS.proposer]),
    likeCount: Number(cellById[FIELD_IDS.likeCount] || 0),
    feishuCreatedTime: scalar(cellById[FIELD_IDS.createdTime]),
    likeUsers: scalar(cellById[FIELD_IDS.likeUsers]),
    rating: scalar(cellById[FIELD_IDS.rating]),
    screenshotCount: Array.isArray(screenshots) ? screenshots.length : 0,
    problemImageCount: Array.isArray(problemImages) ? problemImages.length : 0,
    attachmentCount:
      (Array.isArray(screenshots) ? screenshots.length : 0) + (Array.isArray(problemImages) ? problemImages.length : 0),
    rawFields,
    fieldHash: stableHash(rawFields),
    syncSource
  };
}

export function rowToAttachments({ row, rowNo, recordId, fieldIds, fieldNames, statusByToken = new Map() }) {
  const idToName = Object.fromEntries(fieldIds.map((fieldId, index) => [fieldId, fieldNames[index]]));
  const cellById = Object.fromEntries(fieldIds.map((fieldId, index) => [fieldId, row[index] ?? null]));
  const attachmentFieldIds = [FIELD_IDS.screenshot, FIELD_IDS.problemImage];
  const attachments = [];

  for (const fieldId of attachmentFieldIds) {
    const values = cellById[fieldId] || [];
    if (!Array.isArray(values)) {
      continue;
    }
    for (const item of values) {
      if (!item || typeof item !== "object" || !item.file_token) {
        continue;
      }
      const status = statusByToken.get(item.file_token) || {};
      attachments.push({
        feishuRecordId: recordId,
        rowNo,
        fieldId,
        fieldName: idToName[fieldId] || fieldId,
        fileToken: item.file_token,
        originalName: item.name || "",
        sizeBytes: item.size || null,
        localPath: status.local_path || "",
        downloadStatus: status.download_status || "pending",
        downloadError: status.download_error || ""
      });
    }
  }

  return attachments;
}

export function apiFieldsToRecord({ fields, rowNo = null, recordId, syncSource = "feishu_event" }) {
  const screenshots = fields[FIELD_NAMES.screenshot] || [];
  const problemImages = fields[FIELD_NAMES.problemImage] || [];

  return {
    feishuRecordId: recordId,
    baseToken: config.baseSync.baseToken,
    tableId: config.baseSync.tableId,
    viewId: config.baseSync.viewId,
    rowNo,
    description: scalar(fields[FIELD_NAMES.description]),
    priority: scalar(fields[FIELD_NAMES.priority]),
    status: scalar(fields[FIELD_NAMES.status]),
    remark: scalar(fields[FIELD_NAMES.remark]),
    plannerName: scalar(fields[FIELD_NAMES.planner]),
    proposerName: scalar(fields[FIELD_NAMES.proposer]),
    likeCount: Number(fields[FIELD_NAMES.likeCount] || 0),
    feishuCreatedTime: scalar(fields[FIELD_NAMES.createdTime]),
    likeUsers: scalar(fields[FIELD_NAMES.likeUsers]),
    rating: scalar(fields[FIELD_NAMES.rating]),
    screenshotCount: Array.isArray(screenshots) ? screenshots.length : 0,
    problemImageCount: Array.isArray(problemImages) ? problemImages.length : 0,
    attachmentCount:
      (Array.isArray(screenshots) ? screenshots.length : 0) + (Array.isArray(problemImages) ? problemImages.length : 0),
    rawFields: fields,
    fieldHash: stableHash(fields),
    syncSource
  };
}

export function apiFieldsToAttachments({ fields, rowNo = null, recordId, statusByToken = new Map() }) {
  const attachments = [];
  const attachmentFields = [
    { fieldId: FIELD_IDS.screenshot, fieldName: FIELD_NAMES.screenshot },
    { fieldId: FIELD_IDS.problemImage, fieldName: FIELD_NAMES.problemImage }
  ];

  for (const { fieldId, fieldName } of attachmentFields) {
    const values = fields[fieldName] || [];
    if (!Array.isArray(values)) {
      continue;
    }
    for (const item of values) {
      if (!item || typeof item !== "object" || !item.file_token) {
        continue;
      }
      const status = statusByToken.get(item.file_token) || {};
      attachments.push({
        feishuRecordId: recordId,
        rowNo,
        fieldId,
        fieldName,
        fileToken: item.file_token,
        originalName: item.name || "",
        sizeBytes: item.size || null,
        mimeType: item.type || item.mime_type || "",
        localPath: status.local_path || "",
        publicUrl: item.url || item.tmp_url || "",
        tmpUrl: item.tmp_url || "",
        url: item.url || "",
        downloadStatus: status.download_status || "pending",
        downloadError: status.download_error || ""
      });
    }
  }

  return attachments;
}
