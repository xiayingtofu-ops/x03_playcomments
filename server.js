import { createServer } from "node:http";
import { randomUUID } from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const dataDir = path.join(__dirname, "data");
const dataFile = path.join(dataDir, "store.json");
const publicDir = path.join(__dirname, "public");
const port = process.env.PORT || 3000;

const now = () => new Date().toISOString();

const seed = {
  users: [
    { id: "u-alice", name: "林语", role: "designer", areas: ["战斗", "数值"] },
    { id: "u-ben", name: "周策", role: "designer", areas: ["关卡", "任务"] },
    { id: "u-cora", name: "陈诺", role: "player", areas: [] }
  ],
  feedback: [
    {
      id: "fb-001",
      title: "新手引导第 3 步不知道该点哪里",
      content: "提示箭头不明显，第一次进战斗时我停了大概半分钟才发现按钮。",
      category: "体验",
      module: "新手引导",
      ownerId: "u-alice",
      status: "待评估",
      priority: "P2",
      authorId: "u-cora",
      likes: ["u-alice", "u-ben"],
      followers: ["u-cora"],
      tags: ["新手", "战斗"],
      createdAt: "2026-05-25T09:12:00.000Z",
      updatedAt: "2026-05-26T11:10:00.000Z"
    },
    {
      id: "fb-002",
      title: "副本结算界面希望能看到本局伤害构成",
      content: "现在只能看到总伤害，不方便判断技能搭配是不是有效。",
      category: "建议",
      module: "副本",
      ownerId: "u-ben",
      status: "已收录",
      priority: "P3",
      authorId: "u-alice",
      likes: ["u-cora"],
      followers: ["u-alice", "u-cora"],
      tags: ["结算", "数据展示"],
      createdAt: "2026-05-24T14:22:00.000Z",
      updatedAt: "2026-05-28T08:31:00.000Z"
    },
    {
      id: "fb-003",
      title: "每日任务刷新时间缺少说明",
      content: "不知道是 0 点还是 5 点刷新，建议在任务页补一个小提示。",
      category: "问题",
      module: "任务",
      ownerId: "u-ben",
      status: "处理中",
      priority: "P1",
      authorId: "u-cora",
      likes: ["u-alice", "u-ben", "u-cora"],
      followers: ["u-cora"],
      tags: ["任务", "文案"],
      createdAt: "2026-05-22T10:45:00.000Z",
      updatedAt: "2026-05-28T16:05:00.000Z"
    }
  ],
  comments: [
    {
      id: "cm-001",
      feedbackId: "fb-001",
      authorId: "u-alice",
      body: "同意，下一版可以把高亮和阻断层做得更明确。",
      createdAt: "2026-05-26T10:03:00.000Z"
    },
    {
      id: "cm-002",
      feedbackId: "fb-003",
      authorId: "u-ben",
      body: "任务配置是 5 点刷新，我先把提示文案补到排期里。",
      createdAt: "2026-05-28T16:05:00.000Z"
    }
  ],
  notificationPrefs: {
    "u-alice": { likes: true, comments: true, statusChanges: true, assignments: true },
    "u-ben": { likes: false, comments: true, statusChanges: true, assignments: true },
    "u-cora": { likes: true, comments: true, statusChanges: true, assignments: false }
  },
  notifications: [
    {
      id: "nt-001",
      userId: "u-cora",
      type: "statusChanges",
      title: "每日任务刷新时间缺少说明 状态更新为 处理中",
      read: false,
      createdAt: "2026-05-28T16:05:00.000Z",
      feedbackId: "fb-003"
    }
  ]
};

const mimeTypes = {
  ".html": "text/html; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".svg": "image/svg+xml"
};

async function ensureStore() {
  await fs.mkdir(dataDir, { recursive: true });
  try {
    await fs.access(dataFile);
  } catch {
    await fs.writeFile(dataFile, JSON.stringify(seed, null, 2));
  }
}

async function readStore() {
  await ensureStore();
  return JSON.parse(await fs.readFile(dataFile, "utf8"));
}

async function writeStore(store) {
  await fs.writeFile(dataFile, JSON.stringify(store, null, 2));
}

function sendJson(res, status, payload) {
  res.writeHead(status, { "Content-Type": "application/json; charset=utf-8" });
  res.end(JSON.stringify(payload));
}

function getBody(req) {
  return new Promise((resolve, reject) => {
    let body = "";
    req.on("data", (chunk) => {
      body += chunk;
      if (body.length > 1_000_000) {
        req.destroy();
        reject(new Error("请求体过大"));
      }
    });
    req.on("end", () => {
      if (!body) {
        resolve({});
        return;
      }
      try {
        resolve(JSON.parse(body));
      } catch {
        reject(new Error("JSON 格式错误"));
      }
    });
  });
}

function publicFeedback(item, store) {
  const author = store.users.find((user) => user.id === item.authorId);
  const owner = store.users.find((user) => user.id === item.ownerId);
  const comments = store.comments
    .filter((comment) => comment.feedbackId === item.id)
    .map((comment) => ({
      ...comment,
      author: store.users.find((user) => user.id === comment.authorId)
    }));

  return {
    ...item,
    author,
    owner,
    likeCount: item.likes.length,
    commentCount: comments.length,
    comments
  };
}

function notify(store, userId, type, title, feedbackId) {
  const prefs = store.notificationPrefs[userId] || {};
  if (!prefs[type]) return;
  store.notifications.unshift({
    id: randomUUID(),
    userId,
    type,
    title,
    read: false,
    createdAt: now(),
    feedbackId
  });
}

function route(method, pathname, pattern) {
  if (method !== pattern.method) return null;
  const match = pathname.match(pattern.regex);
  return match ? match.groups || {} : null;
}

async function handleApi(req, res, url) {
  const pathname = url.pathname;

  if (route(req.method, pathname, { method: "GET", regex: /^\/api\/bootstrap$/ })) {
    const store = await readStore();
    sendJson(res, 200, {
      users: store.users,
      feedback: store.feedback.map((item) => publicFeedback(item, store)),
      notifications: store.notifications,
      notificationPrefs: store.notificationPrefs
    });
    return;
  }

  if (route(req.method, pathname, { method: "GET", regex: /^\/api\/feedback$/ })) {
    const store = await readStore();
    const ownerId = url.searchParams.get("ownerId");
    const status = url.searchParams.get("status");
    const q = url.searchParams.get("q");
    let items = store.feedback;

    if (ownerId) items = items.filter((item) => item.ownerId === ownerId);
    if (status) items = items.filter((item) => item.status === status);
    if (q) {
      const keyword = q.toLowerCase();
      items = items.filter((item) =>
        [item.title, item.content, item.category, item.module, ...item.tags]
          .join(" ")
          .toLowerCase()
          .includes(keyword)
      );
    }

    sendJson(res, 200, items.map((item) => publicFeedback(item, store)));
    return;
  }

  if (route(req.method, pathname, { method: "POST", regex: /^\/api\/feedback$/ })) {
    const store = await readStore();
    const body = await getBody(req);
    const title = String(body.title || "").trim();
    const content = String(body.content || "").trim();

    if (!title || !content) {
      sendJson(res, 400, { error: "标题和描述必填" });
      return;
    }

    const authorId = body.authorId || store.users[0].id;
    const item = {
      id: randomUUID(),
      title,
      content,
      category: body.category || "建议",
      module: body.module || "未分类",
      ownerId: body.ownerId || store.users.find((user) => user.role === "designer")?.id,
      status: "待评估",
      priority: body.priority || "P3",
      authorId,
      likes: [],
      followers: [authorId],
      tags: Array.isArray(body.tags) ? body.tags : String(body.tags || "").split(",").map((tag) => tag.trim()).filter(Boolean),
      createdAt: now(),
      updatedAt: now()
    };

    store.feedback.unshift(item);
    notify(store, item.ownerId, "assignments", `${item.title} 分配给你`, item.id);
    await writeStore(store);
    sendJson(res, 201, publicFeedback(item, store));
    return;
  }

  const likeParams = route(req.method, pathname, { method: "POST", regex: /^\/api\/feedback\/(?<id>[^/]+)\/like$/ });
  if (likeParams) {
    const store = await readStore();
    const body = await getBody(req);
    const item = store.feedback.find((entry) => entry.id === likeParams.id);
    const userId = body.userId;

    if (!item || !userId) {
      sendJson(res, 404, { error: "条目或用户不存在" });
      return;
    }

    const hadLiked = item.likes.includes(userId);
    item.likes = hadLiked ? item.likes.filter((id) => id !== userId) : [...item.likes, userId];
    item.updatedAt = now();

    if (!hadLiked && item.authorId !== userId) {
      const user = store.users.find((entry) => entry.id === userId);
      notify(store, item.authorId, "likes", `${user?.name || "有人"} 赞同了你的反馈`, item.id);
    }

    await writeStore(store);
    sendJson(res, 200, publicFeedback(item, store));
    return;
  }

  const commentParams = route(req.method, pathname, { method: "POST", regex: /^\/api\/feedback\/(?<id>[^/]+)\/comments$/ });
  if (commentParams) {
    const store = await readStore();
    const body = await getBody(req);
    const item = store.feedback.find((entry) => entry.id === commentParams.id);
    const commentBody = String(body.body || "").trim();
    const authorId = body.authorId;

    if (!item || !commentBody || !authorId) {
      sendJson(res, 400, { error: "评论内容和用户必填" });
      return;
    }

    const comment = {
      id: randomUUID(),
      feedbackId: item.id,
      authorId,
      body: commentBody,
      createdAt: now()
    };

    store.comments.push(comment);
    item.updatedAt = now();
    [...new Set([item.authorId, item.ownerId, ...item.followers])]
      .filter((id) => id !== authorId)
      .forEach((id) => {
        const user = store.users.find((entry) => entry.id === authorId);
        notify(store, id, "comments", `${user?.name || "有人"} 评论了 ${item.title}`, item.id);
      });

    await writeStore(store);
    sendJson(res, 201, publicFeedback(item, store));
    return;
  }

  const feedbackParams = route(req.method, pathname, { method: "PATCH", regex: /^\/api\/feedback\/(?<id>[^/]+)$/ });
  if (feedbackParams) {
    const store = await readStore();
    const body = await getBody(req);
    const item = store.feedback.find((entry) => entry.id === feedbackParams.id);

    if (!item) {
      sendJson(res, 404, { error: "条目不存在" });
      return;
    }

    const oldStatus = item.status;
    ["status", "priority", "ownerId", "module", "category"].forEach((field) => {
      if (body[field]) item[field] = body[field];
    });
    item.updatedAt = now();

    if (oldStatus !== item.status) {
      [...new Set([item.authorId, ...item.followers])]
        .forEach((id) => notify(store, id, "statusChanges", `${item.title} 状态更新为 ${item.status}`, item.id));
    }

    await writeStore(store);
    sendJson(res, 200, publicFeedback(item, store));
    return;
  }

  const prefsParams = route(req.method, pathname, { method: "PATCH", regex: /^\/api\/users\/(?<id>[^/]+)\/notification-prefs$/ });
  if (prefsParams) {
    const store = await readStore();
    const body = await getBody(req);
    const user = store.users.find((entry) => entry.id === prefsParams.id);

    if (!user) {
      sendJson(res, 404, { error: "用户不存在" });
      return;
    }

    store.notificationPrefs[user.id] = {
      likes: Boolean(body.likes),
      comments: Boolean(body.comments),
      statusChanges: Boolean(body.statusChanges),
      assignments: Boolean(body.assignments)
    };

    await writeStore(store);
    sendJson(res, 200, store.notificationPrefs[user.id]);
    return;
  }

  const readParams = route(req.method, pathname, { method: "PATCH", regex: /^\/api\/notifications\/(?<id>[^/]+)\/read$/ });
  if (readParams) {
    const store = await readStore();
    const notification = store.notifications.find((entry) => entry.id === readParams.id);

    if (!notification) {
      sendJson(res, 404, { error: "通知不存在" });
      return;
    }

    notification.read = true;
    await writeStore(store);
    sendJson(res, 200, notification);
    return;
  }

  sendJson(res, 404, { error: "接口不存在" });
}

async function serveStatic(req, res, url) {
  const requestPath = decodeURIComponent(url.pathname === "/" ? "/index.html" : url.pathname);
  const filePath = path.normalize(path.join(publicDir, requestPath));

  if (!filePath.startsWith(publicDir)) {
    res.writeHead(403);
    res.end("Forbidden");
    return;
  }

  try {
    const content = await fs.readFile(filePath);
    const ext = path.extname(filePath);
    res.writeHead(200, { "Content-Type": mimeTypes[ext] || "application/octet-stream" });
    res.end(content);
  } catch {
    res.writeHead(404, { "Content-Type": "text/plain; charset=utf-8" });
    res.end("Not found");
  }
}

const server = createServer(async (req, res) => {
  try {
    const url = new URL(req.url, `http://${req.headers.host}`);

    if (url.pathname.startsWith("/api/")) {
      await handleApi(req, res, url);
      return;
    }

    await serveStatic(req, res, url);
  } catch (error) {
    sendJson(res, 500, { error: error.message || "服务异常" });
  }
});

server.listen(port, () => {
  console.log(`Game feedback MVP running at http://localhost:${port}`);
});
