import crypto from "node:crypto";
import path from "node:path";
import { fileURLToPath } from "node:url";
import express from "express";
import session from "express-session";
import helmet from "helmet";
import { config } from "./config.js";
import { buildFeishuAuthUrl, exchangeCodeForUser } from "./feishuClient.js";
import { FileSessionStore } from "./fileSessionStore.js";
import { UserStore } from "./userStore.js";
import { enqueueInboxEvent, getRecord, listRecords, updatePlatformRecord } from "./baseRepository.js";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const rootDir = path.resolve(__dirname, "..");
const publicDir = path.join(rootDir, "public");
const dataDir = path.join(rootDir, "data");

const app = express();
const userStore = new UserStore(path.join(dataDir, "users.json"));

app.set("trust proxy", 1);
app.use(
  helmet({
    contentSecurityPolicy: {
      directives: {
        defaultSrc: ["'self'"],
        imgSrc: ["'self'", "data:", "https:"],
        styleSrc: ["'self'", "'unsafe-inline'"],
        scriptSrc: ["'self'"],
        connectSrc: ["'self'"]
      }
    }
  })
);
app.use(express.json());
app.use(
  session({
    name: "feishu.sid",
    secret: config.sessionSecret,
    resave: false,
    saveUninitialized: false,
    store: new FileSessionStore({ filePath: path.join(dataDir, "sessions.json") }),
    cookie: {
      httpOnly: true,
      sameSite: "lax",
      secure: config.baseUrl.startsWith("https://"),
      maxAge: 24 * 60 * 60 * 1000
    }
  })
);

function requireLogin(req, res, next) {
  if (!req.session.user) {
    res.status(401).json({ error: "未登录" });
    return;
  }
  next();
}

app.get("/api/health", (req, res) => {
  res.json({
    ok: true,
    app: config.appName,
    time: new Date().toISOString()
  });
});

app.get("/api/auth/me", (req, res) => {
  if (!req.session.user) {
    res.status(401).json({ authenticated: false });
    return;
  }
  res.json({ authenticated: true, user: req.session.user });
});

app.get("/api/auth/login", (req, res) => {
  const state = crypto.randomBytes(24).toString("hex");
  req.session.oauthState = state;
  req.session.save((error) => {
    if (error) {
      res.status(500).json({ error: "会话保存失败" });
      return;
    }
    res.redirect(buildFeishuAuthUrl(state));
  });
});

app.get(config.feishu.redirectPath, async (req, res, next) => {
  try {
    const { code, state, error, error_description: errorDescription } = req.query;

    if (error) {
      res.redirect(`/login-error.html?message=${encodeURIComponent(errorDescription || error)}`);
      return;
    }

    if (!code || !state || state !== req.session.oauthState) {
      res.redirect("/login-error.html?message=%E7%99%BB%E5%BD%95%E7%8A%B6%E6%80%81%E6%A0%A1%E9%AA%8C%E5%A4%B1%E8%B4%A5");
      return;
    }

    delete req.session.oauthState;
    const user = await exchangeCodeForUser(String(code));
    const savedUser = await userStore.upsert(user);
    req.session.user = savedUser;

    req.session.save((saveError) => {
      if (saveError) {
        next(saveError);
        return;
      }
      res.redirect("/dashboard.html");
    });
  } catch (callbackError) {
    next(callbackError);
  }
});

app.post("/api/auth/logout", requireLogin, (req, res) => {
  req.session.destroy((error) => {
    if (error) {
      res.status(500).json({ error: "退出登录失败" });
      return;
    }
    res.clearCookie("feishu.sid");
    res.json({ ok: true });
  });
});

app.get("/api/protected/profile", requireLogin, (req, res) => {
  res.json({
    user: req.session.user,
    session: {
      expiresAt: req.session.cookie.expires
    }
  });
});

app.get("/api/base/records", requireLogin, async (req, res, next) => {
  try {
    const result = await listRecords({
      limit: req.query.limit,
      offset: req.query.offset,
      status: req.query.status,
      priority: req.query.priority,
      planner: req.query.planner
    });
    res.json(result);
  } catch (error) {
    next(error);
  }
});

app.get("/api/base/records/:recordId", requireLogin, async (req, res, next) => {
  try {
    const record = await getRecord(req.params.recordId);
    if (!record) {
      res.status(404).json({ error: "Record not found" });
      return;
    }
    res.json({ record });
  } catch (error) {
    next(error);
  }
});

app.patch("/api/base/records/:recordId", requireLogin, async (req, res, next) => {
  try {
    const fields = req.body?.fields;
    if (!fields || typeof fields !== "object" || Array.isArray(fields)) {
      res.status(400).json({ error: "Body must be { fields: {...} }" });
      return;
    }

    const result = await updatePlatformRecord({
      feishuRecordId: req.params.recordId,
      fields,
      createdBy: req.session.user?.openId || req.session.user?.email || req.session.user?.name || ""
    });

    if (!result) {
      res.status(404).json({ error: "Record not found" });
      return;
    }

    res.json({ ok: true, record: result.record, outbox: result.outbox });
  } catch (error) {
    next(error);
  }
});

app.post("/api/webhooks/feishu/base", async (req, res, next) => {
  try {
    if (req.body?.challenge) {
      res.json({ challenge: req.body.challenge });
      return;
    }

    const expectedToken = config.baseSync.webhookVerificationToken;
    if (expectedToken && req.body?.token && req.body.token !== expectedToken) {
      res.status(401).json({ error: "Invalid webhook token" });
      return;
    }

    const event = req.body?.event || {};
    const header = req.body?.header || {};
    const eventId =
      header.event_id ||
      req.body?.uuid ||
      req.body?.event_id ||
      `${header.event_type || req.body?.type || "feishu_event"}:${event.record_id || crypto.randomUUID()}`;

    const saved = await enqueueInboxEvent({
      eventId,
      eventType: header.event_type || req.body?.type || "feishu.base.record_changed",
      baseToken: event.base_token || event.app_token || config.baseSync.baseToken,
      tableId: event.table_id || config.baseSync.tableId,
      feishuRecordId: event.record_id || event.record?.record_id || null,
      payload: req.body
    });

    res.json({ ok: true, queued: Boolean(saved) });
  } catch (error) {
    next(error);
  }
});

app.use(express.static(publicDir, { extensions: ["html"] }));

app.use((req, res) => {
  res.status(404).type("text/plain").send("Not found");
});

app.use((error, req, res, next) => {
  console.error(error);
  if (req.path.startsWith("/api/")) {
    res.status(500).json({ error: error.message || "服务异常" });
    return;
  }
  res.redirect(`/login-error.html?message=${encodeURIComponent(error.message || "服务异常")}`);
});

app.listen(config.port, () => {
  console.log(`${config.appName} running at ${config.baseUrl}`);
  console.log(`Feishu callback URL: ${config.feishu.redirectUri}`);
});
