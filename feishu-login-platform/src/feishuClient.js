import { config } from "./config.js";

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
    throw new Error(`Feishu API HTTP ${response.status}: ${JSON.stringify(payload)}`);
  }

  if (payload.code && payload.code !== 0) {
    throw new Error(payload.msg || `Feishu API returned code ${payload.code}`);
  }

  return payload;
}

export function buildFeishuAuthUrl(state) {
  const url = new URL(config.feishu.authUrl);
  url.searchParams.set("app_id", config.feishu.appId);
  url.searchParams.set("redirect_uri", config.feishu.redirectUri);
  url.searchParams.set("state", state);
  return url.toString();
}

async function getAppAccessToken() {
  const payload = await requestJson(config.feishu.appAccessTokenUrl, {
    method: "POST",
    body: JSON.stringify({
      app_id: config.feishu.appId,
      app_secret: config.feishu.appSecret
    })
  });

  const token = payload.app_access_token || payload.data?.app_access_token;
  if (!token) {
    throw new Error("Feishu app_access_token was not returned.");
  }
  return token;
}

async function exchangeCodeV1(code) {
  const appAccessToken = await getAppAccessToken();
  const payload = await requestJson(config.feishu.userAccessTokenUrl, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${appAccessToken}`
    },
    body: JSON.stringify({
      grant_type: "authorization_code",
      code
    })
  });

  const data = payload.data || payload;
  if (!data.access_token) {
    throw new Error("Feishu user access_token was not returned.");
  }
  return data;
}

async function exchangeCodeV2(code) {
  const payload = await requestJson(config.feishu.oauthTokenUrl, {
    method: "POST",
    body: JSON.stringify({
      grant_type: "authorization_code",
      client_id: config.feishu.appId,
      client_secret: config.feishu.appSecret,
      code,
      redirect_uri: config.feishu.redirectUri
    })
  });

  const data = payload.data || payload;
  if (!data.access_token) {
    throw new Error("Feishu OAuth access_token was not returned.");
  }
  return data;
}

async function getUserInfo(accessToken) {
  const payload = await requestJson(config.feishu.userInfoUrl, {
    method: "GET",
    headers: {
      Authorization: `Bearer ${accessToken}`
    }
  });
  return payload.data || payload;
}

export async function exchangeCodeForUser(code) {
  const tokenData =
    config.feishu.oauthMode === "v2"
      ? await exchangeCodeV2(code)
      : await exchangeCodeV1(code);

  const info = await getUserInfo(tokenData.access_token).catch(() => tokenData);

  return {
    openId: info.open_id || tokenData.open_id,
    unionId: info.union_id || tokenData.union_id,
    userId: info.user_id || tokenData.user_id,
    tenantKey: info.tenant_key || tokenData.tenant_key,
    name: info.name || info.en_name || tokenData.name || "Feishu User",
    email: info.email || tokenData.email || "",
    avatarUrl: info.avatar_url || tokenData.avatar_url || "",
    raw: {
      tokenData,
      userInfo: info
    }
  };
}
