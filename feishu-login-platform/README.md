# 飞书登录平台

一个完整的飞书网页授权登录示例，包含登录页、后端回调接口、用户会话管理和 Docker 部署配置。

## 功能

- 登录页：点击按钮跳转飞书授权页。
- 授权回调：`GET /auth/feishu/callback` 接收飞书返回的 `code` 和 `state`。
- Token 交换：后端使用 `code` 换取用户访问凭证，再获取用户信息。
- 会话管理：使用 HttpOnly Cookie 和服务端文件会话存储。
- 受保护接口：`GET /api/protected/profile` 需要登录后访问。
- 部署配置：内置 `Dockerfile` 和 `docker-compose.yml`。

## 本地运行

```bash
npm install
npm start
```

访问：

```text
http://localhost:3000
```

## 飞书开放平台配置

在飞书自建应用后台配置网页应用登录，并把重定向 URL 设置为：

```text
https://aily.feishu.cn/anyclaw/oauth/feishu/callback
```

如果部署到线上，把 `.env` 中的 `BASE_URL` 改成公网 HTTPS 域名，例如：

```text
BASE_URL=https://login.example.com
```

飞书后台的回调地址也要同步改为：

```text
https://login.example.com/anyclaw/oauth/feishu/callback
```

## 环境变量

当前 `.env` 已按你提供的信息填好。上线前请把 `SESSION_SECRET` 换成一段足够长的随机字符串。

```text
PORT=3000
BASE_URL=http://localhost:3000
SESSION_SECRET=replace-with-a-long-random-secret
FEISHU_APP_ID=cli_xxxxxxxxxxxxxxxx
FEISHU_APP_SECRET=replace-with-your-app-secret
FEISHU_REDIRECT_PATH=/auth/feishu/callback
FEISHU_OAUTH_MODE=v1
```

`FEISHU_OAUTH_MODE=v1` 会使用你给出的授权入口：

```text
https://open.feishu.cn/open-apis/authen/v1/index?app_id=xxx
```

如果你的应用切到飞书新版 OAuth token 接口，可把它改成：

```text
FEISHU_OAUTH_MODE=v2
```

## 接口

- `GET /api/auth/login`：生成 `state` 并跳转到飞书授权页。
- `GET /auth/feishu/callback`：处理飞书授权回调。
- `GET /api/auth/me`：读取当前登录用户。
- `POST /api/auth/logout`：退出登录并清理会话。
- `GET /api/protected/profile`：登录后可访问的示例业务接口。
- `GET /api/health`：健康检查。

## Docker 部署

```bash
docker compose up -d --build
```

生产环境建议：

- 使用 HTTPS 域名，并更新 `BASE_URL`。
- 不要把 `.env` 提交到仓库。
- 多实例部署时，把文件会话存储替换为 Redis 或数据库会话存储。
- App Secret 泄露后应在飞书开放平台重置。
