import { execFile } from "node:child_process";
import { mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { promisify } from "node:util";
import { config } from "./config.js";

const execFileAsync = promisify(execFile);
let tenantTokenCache = null;

function ensureCliDriver() {
  if (config.baseSync.syncDriver !== "cli") {
    throw new Error(`Unsupported FEISHU_BASE_SYNC_DRIVER: ${config.baseSync.syncDriver}`);
  }
}

async function requestJson(url, options = {}) {
  const response = await fetch(url, {
    ...options,
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      ...(options.headers || {})
    }
  });
  const payload = await response.json().catch(() => ({}));

  if (!response.ok) {
    throw new Error(`Feishu OpenAPI HTTP ${response.status}: ${JSON.stringify(payload)}`);
  }

  if (payload.code && payload.code !== 0) {
    throw new Error(`Feishu OpenAPI code ${payload.code}: ${payload.msg || JSON.stringify(payload)}`);
  }

  return payload;
}

async function getTenantAccessToken() {
  const now = Date.now();
  if (tenantTokenCache && tenantTokenCache.expiresAt > now + 60_000) {
    return tenantTokenCache.token;
  }

  const payload = await requestJson(config.feishu.tenantAccessTokenUrl, {
    method: "POST",
    body: JSON.stringify({
      app_id: config.feishu.appId,
      app_secret: config.feishu.appSecret
    })
  });

  const token = payload.tenant_access_token || payload.data?.tenant_access_token;
  const expire = payload.expire || payload.data?.expire || 7200;
  if (!token) {
    throw new Error("Feishu tenant_access_token was not returned.");
  }

  tenantTokenCache = {
    token,
    expiresAt: now + Number(expire) * 1000
  };
  return token;
}

async function requestOpenApi(apiPath, options = {}) {
  const token = await getTenantAccessToken();
  const url = new URL(`${config.baseSync.apiBaseUrl}${apiPath}`);
  for (const [key, value] of Object.entries(options.query || {})) {
    if (value !== undefined && value !== null && value !== "") {
      url.searchParams.set(key, String(value));
    }
  }

  return requestJson(url, {
    method: options.method || "GET",
    headers: {
      Authorization: `Bearer ${token}`,
      ...(options.headers || {})
    },
    body: options.body ? JSON.stringify(options.body) : undefined
  });
}

async function downloadBinary(url, outputPath) {
  const token = await getTenantAccessToken();
  const response = await fetch(url, {
    headers: {
      Authorization: `Bearer ${token}`
    }
  });

  if (!response.ok) {
    const body = await response.text().catch(() => "");
    throw new Error(`Feishu attachment download HTTP ${response.status}: ${body}`);
  }

  await mkdir(path.dirname(outputPath), { recursive: true });
  const buffer = Buffer.from(await response.arrayBuffer());
  await writeFile(outputPath, buffer);
  return {
    savedPath: outputPath,
    sizeBytes: buffer.length,
    contentType: response.headers.get("content-type") || ""
  };
}

async function runLarkCli(args) {
  ensureCliDriver();
  const { stdout, stderr } = await execFileAsync(config.baseSync.cliCommand, ["lark-cli", "--", ...args], {
    cwd: config.baseSync.exportDir,
    windowsHide: true,
    maxBuffer: 20 * 1024 * 1024
  });
  const output = stdout || stderr;
  try {
    return JSON.parse(output);
  } catch {
    return { ok: true, raw: output };
  }
}

export async function pushRecordPatchToFeishu(feishuRecordId, patchFields) {
  if (config.baseSync.syncDriver === "api") {
    return requestOpenApi(
      `/bitable/v1/apps/${config.baseSync.baseToken}/tables/${config.baseSync.tableId}/records/${feishuRecordId}`,
      {
        method: "PUT",
        query: { user_id_type: "open_id" },
        body: { fields: patchFields }
      }
    );
  }

  const tempDir = await mkdtemp(path.join(os.tmpdir(), "feishu-base-patch-"));
  const patchPath = path.join(tempDir, "patch.json");
  try {
    await writeFile(patchPath, JSON.stringify(patchFields), "utf8");
    const result = await runLarkCli([
      "base",
      "+record-upsert",
      "--base-token",
      config.baseSync.baseToken,
      "--table-id",
      config.baseSync.tableId,
      "--record-id",
      feishuRecordId,
      "--json",
      `@${patchPath}`
    ]);
    if (result.ok === false) {
      throw new Error(JSON.stringify(result.error || result));
    }
    return result;
  } finally {
    await rm(tempDir, { recursive: true, force: true });
  }
}

export async function fetchFeishuRecord(feishuRecordId) {
  if (config.baseSync.syncDriver === "api") {
    return requestOpenApi(
      `/bitable/v1/apps/${config.baseSync.baseToken}/tables/${config.baseSync.tableId}/records/${feishuRecordId}`,
      {
        method: "GET",
        query: { user_id_type: "open_id" }
      }
    );
  }

  const result = await runLarkCli([
    "base",
    "+record-get",
    "--base-token",
    config.baseSync.baseToken,
    "--table-id",
    config.baseSync.tableId,
    "--record-id",
    feishuRecordId,
    "--format",
    "json"
  ]);
  if (result.ok === false) {
    throw new Error(JSON.stringify(result.error || result));
  }
  return result;
}

export async function downloadFeishuAttachment({ attachment, outputPath }) {
  if (config.baseSync.syncDriver !== "api") {
    const output = outputPath || attachment.localPath;
    const result = await runLarkCli([
      "base",
      "+record-download-attachment",
      "--base-token",
      config.baseSync.baseToken,
      "--table-id",
      config.baseSync.tableId,
      "--record-id",
      attachment.feishuRecordId,
      "--file-token",
      attachment.fileToken,
      "--output",
      output,
      "--overwrite"
    ]);
    if (result.ok === false) {
      throw new Error(JSON.stringify(result.error || result));
    }
    return result;
  }

  const downloadUrl = attachment.tmpUrl || attachment.url;
  if (!downloadUrl) {
    throw new Error(`Attachment ${attachment.fileToken} did not include a downloadable url.`);
  }
  return downloadBinary(downloadUrl, outputPath || attachment.localPath);
}

export async function readJsonFile(filePath) {
  return JSON.parse(await readFile(filePath, "utf8"));
}
