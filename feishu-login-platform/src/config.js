import dotenv from "dotenv";
import { fileURLToPath } from "node:url";

dotenv.config();

const required = ["FEISHU_APP_ID", "FEISHU_APP_SECRET", "SESSION_SECRET"];

for (const key of required) {
  if (!process.env[key]) {
    throw new Error(`Missing required environment variable: ${key}`);
  }
}

const baseUrl = (process.env.BASE_URL || `http://localhost:${process.env.PORT || 3000}`).replace(/\/$/, "");
const redirectPath = process.env.FEISHU_REDIRECT_PATH || "/auth/feishu/callback";
const defaultExportDir = fileURLToPath(new URL("../../feishu_base_export", import.meta.url));

export const config = {
  port: Number(process.env.PORT || 3000),
  baseUrl,
  sessionSecret: process.env.SESSION_SECRET,
  appName: process.env.APP_NAME || "Feishu Login Platform",
  database: {
    url: process.env.DATABASE_URL || "postgres://postgres:postgres@localhost:5432/feishu_platform",
    ssl: process.env.DATABASE_SSL === "true"
  },
  baseSync: {
    baseToken: process.env.FEISHU_BASE_TOKEN || "NeijbKbZFafkxJsTiolcRWGFnEc",
    tableId: process.env.FEISHU_BASE_TABLE_ID || "tbllTU2raGhLxAW3",
    viewId: process.env.FEISHU_BASE_VIEW_ID || "vew9FrF9dW",
    exportDir: process.env.FEISHU_BASE_EXPORT_DIR || defaultExportDir,
    webhookVerificationToken: process.env.FEISHU_BASE_WEBHOOK_VERIFICATION_TOKEN || "",
    syncDriver: process.env.FEISHU_BASE_SYNC_DRIVER || "api",
    apiBaseUrl: process.env.FEISHU_OPENAPI_BASE_URL || "https://open.feishu.cn/open-apis",
    cliCommand:
      process.env.FEISHU_CONNECTOR_CMD ||
      "C:\\Users\\Administrator\\plugins\\lark-enterprise\\scripts\\feishu-connector.cmd"
  },
  feishu: {
    appId: process.env.FEISHU_APP_ID,
    appSecret: process.env.FEISHU_APP_SECRET,
    oauthMode: process.env.FEISHU_OAUTH_MODE || "v1",
    redirectPath,
    redirectUri: `${baseUrl}${redirectPath}`,
    authUrl: process.env.FEISHU_AUTH_URL || "https://open.feishu.cn/open-apis/authen/v1/index",
    appAccessTokenUrl:
      process.env.FEISHU_APP_ACCESS_TOKEN_URL ||
      "https://open.feishu.cn/open-apis/auth/v3/app_access_token/internal",
    tenantAccessTokenUrl:
      process.env.FEISHU_TENANT_ACCESS_TOKEN_URL ||
      "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
    userAccessTokenUrl:
      process.env.FEISHU_USER_ACCESS_TOKEN_URL ||
      "https://open.feishu.cn/open-apis/authen/v1/access_token",
    oauthTokenUrl:
      process.env.FEISHU_OAUTH_TOKEN_URL ||
      "https://open.feishu.cn/open-apis/authen/v2/oauth/token",
    userInfoUrl:
      process.env.FEISHU_USER_INFO_URL ||
      "https://open.feishu.cn/open-apis/authen/v1/user_info"
  }
};
