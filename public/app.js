const state = {
  users: [],
  feedback: [],
  notifications: [],
  notificationPrefs: {},
  currentUserId: localStorage.getItem("pf-user") || "u-cora",
  activeView: "feed",
  query: "",
  ownerFilter: "",
  statusFilter: ""
};

const statusOptions = ["待评估", "已收录", "处理中", "已解决", "暂不处理"];
const priorityOptions = ["P0", "P1", "P2", "P3"];
const notificationLabels = {
  likes: "收到点赞",
  comments: "收到评论",
  statusChanges: "状态变化",
  assignments: "分配给我"
};

const els = {
  currentUser: document.querySelector("#current-user"),
  composeOwner: document.querySelector("#compose-owner"),
  feed: document.querySelector("#feedback-feed"),
  table: document.querySelector("#feedback-table"),
  notifications: document.querySelector("#notification-list"),
  unreadCount: document.querySelector("#unread-count"),
  settings: document.querySelector("#settings-grid"),
  feedSearch: document.querySelector("#feed-search"),
  ownerFilter: document.querySelector("#owner-filter"),
  statusFilter: document.querySelector("#status-filter"),
  composeDialog: document.querySelector("#compose-dialog"),
  composeForm: document.querySelector("#compose-form")
};

async function request(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options
  });

  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error || "请求失败");
  }

  return res.json();
}

function userName(id) {
  return state.users.find((user) => user.id === id)?.name || "未分配";
}

function formatTime(value) {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit"
  }).format(new Date(value));
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function filteredFeedback() {
  const query = state.query.toLowerCase();
  return state.feedback
    .filter((item) => {
      if (!query) return true;
      return [item.title, item.content, item.category, item.module, ...item.tags]
        .join(" ")
        .toLowerCase()
        .includes(query);
    })
    .sort((a, b) => new Date(b.updatedAt) - new Date(a.updatedAt));
}

function plannerFeedback() {
  return state.feedback.filter((item) => {
    const ownerMatch = state.ownerFilter ? item.ownerId === state.ownerFilter : true;
    const statusMatch = state.statusFilter ? item.status === state.statusFilter : true;
    return ownerMatch && statusMatch;
  });
}

function renderUserControls() {
  const options = state.users
    .map((user) => `<option value="${user.id}" ${user.id === state.currentUserId ? "selected" : ""}>${escapeHtml(user.name)} · ${user.role === "designer" ? "策划" : "玩家"}</option>`)
    .join("");

  els.currentUser.innerHTML = options;
  els.composeOwner.innerHTML = state.users
    .filter((user) => user.role === "designer")
    .map((user) => `<option value="${user.id}">${escapeHtml(user.name)}</option>`)
    .join("");

  els.ownerFilter.innerHTML = [
    `<option value="">全部负责人</option>`,
    ...state.users
      .filter((user) => user.role === "designer")
      .map((user) => `<option value="${user.id}" ${user.id === state.ownerFilter ? "selected" : ""}>${escapeHtml(user.name)}</option>`)
  ].join("");
}

function renderFeed() {
  const items = filteredFeedback();
  if (!items.length) {
    els.feed.innerHTML = `<div class="empty">没有匹配的反馈。</div>`;
    return;
  }

  els.feed.innerHTML = items.map((item) => {
    const hasLiked = item.likes.includes(state.currentUserId);
    const comments = item.comments.map((comment) => `
      <div class="comment">
        <div class="comment-meta">
          <strong>${escapeHtml(comment.author?.name || "匿名")}</strong>
          <span>${formatTime(comment.createdAt)}</span>
        </div>
        <div>${escapeHtml(comment.body)}</div>
      </div>
    `).join("");

    return `
      <article class="feedback-card" data-id="${item.id}">
        <div class="card-meta">
          <span class="pill">${escapeHtml(item.category)}</span>
          <span class="pill">${escapeHtml(item.module)}</span>
          <span class="pill status">${escapeHtml(item.status)}</span>
          <span class="pill priority">${escapeHtml(item.priority)}</span>
        </div>
        <h3>${escapeHtml(item.title)}</h3>
        <p>${escapeHtml(item.content)}</p>
        <div class="card-footer">
          <div>
            <strong>${escapeHtml(item.author?.name || "匿名")}</strong>
            <span>提出 · ${formatTime(item.createdAt)} · 负责人 ${escapeHtml(item.owner?.name || "未分配")}</span>
          </div>
          <div class="actions">
            <button class="small-button like-button ${hasLiked ? "active" : ""}" data-action="like" type="button">赞同 ${item.likeCount}</button>
            <button class="small-button" data-action="focus-comment" type="button">评论 ${item.commentCount}</button>
          </div>
        </div>
        <div class="comments">${comments}</div>
        <form class="comment-form" data-action="comment">
          <input name="body" placeholder="补充信息或讨论处理方式" required />
          <button class="small-button" type="submit">发送</button>
        </form>
      </article>
    `;
  }).join("");
}

function renderTable() {
  const items = plannerFeedback();
  if (!items.length) {
    els.table.innerHTML = `<tr><td colspan="6">没有符合条件的条目。</td></tr>`;
    return;
  }

  els.table.innerHTML = items.map((item) => `
    <tr data-id="${item.id}">
      <td>
        <strong>${escapeHtml(item.title)}</strong>
        <span>${escapeHtml(item.content)}</span>
      </td>
      <td>${escapeHtml(item.module)}</td>
      <td>${escapeHtml(userName(item.ownerId))}</td>
      <td>
        <select class="priority-select" data-field="priority">
          ${priorityOptions.map((option) => `<option ${option === item.priority ? "selected" : ""}>${option}</option>`).join("")}
        </select>
      </td>
      <td>${item.likeCount} 赞 · ${item.commentCount} 评</td>
      <td>
        <select class="status-select" data-field="status">
          ${statusOptions.map((option) => `<option ${option === item.status ? "selected" : ""}>${option}</option>`).join("")}
        </select>
      </td>
    </tr>
  `).join("");
}

function renderNotifications() {
  const current = state.notifications
    .filter((item) => item.userId === state.currentUserId)
    .sort((a, b) => new Date(b.createdAt) - new Date(a.createdAt));
  const unread = current.filter((item) => !item.read).length;

  els.unreadCount.textContent = `${unread} 未读`;
  els.notifications.innerHTML = current.length
    ? current.map((item) => `
      <button class="notification ${item.read ? "" : "unread"}" data-id="${item.id}" type="button">
        <strong>${escapeHtml(item.title)}</strong>
        <span>${formatTime(item.createdAt)}</span>
      </button>
    `).join("")
    : `<div class="empty">暂时没有通知。</div>`;
}

function renderSettings() {
  const prefs = state.notificationPrefs[state.currentUserId] || {};
  els.settings.innerHTML = `
    <section class="settings-card">
      <h3>${escapeHtml(userName(state.currentUserId))} 的通知规则</h3>
      <p>用于控制站内通知是否生成，MVP 先不接邮件和企业 IM。</p>
      ${Object.entries(notificationLabels).map(([key, label]) => `
        <div class="switch-row">
          <span>${label}</span>
          <label class="switch" aria-label="${label}">
            <input type="checkbox" data-pref="${key}" ${prefs[key] ? "checked" : ""} />
            <span class="slider"></span>
          </label>
        </div>
      `).join("")}
    </section>
    <section class="settings-card">
      <h3>后续可扩展</h3>
      <p>接入游戏账号、截图附件、版本号、复现步骤模板、导出需求池，以及和项目管理工具联动。</p>
    </section>
  `;
}

function renderViews() {
  document.querySelectorAll(".view").forEach((view) => view.classList.remove("active"));
  document.querySelector(`#${state.activeView}-view`).classList.add("active");
  document.querySelectorAll(".nav-item").forEach((item) => item.classList.toggle("active", item.dataset.view === state.activeView));
}

function render() {
  renderUserControls();
  renderViews();
  renderFeed();
  renderTable();
  renderNotifications();
  renderSettings();
}

async function refresh() {
  const data = await request("/api/bootstrap");
  state.users = data.users;
  state.feedback = data.feedback;
  state.notifications = data.notifications;
  state.notificationPrefs = data.notificationPrefs;
  if (!state.users.some((user) => user.id === state.currentUserId)) {
    state.currentUserId = state.users[0]?.id;
  }
  render();
}

document.querySelectorAll(".nav-item").forEach((button) => {
  button.addEventListener("click", () => {
    state.activeView = button.dataset.view;
    render();
  });
});

els.currentUser.addEventListener("change", () => {
  state.currentUserId = els.currentUser.value;
  localStorage.setItem("pf-user", state.currentUserId);
  render();
});

els.feedSearch.addEventListener("input", () => {
  state.query = els.feedSearch.value;
  renderFeed();
});

els.ownerFilter.addEventListener("change", () => {
  state.ownerFilter = els.ownerFilter.value;
  renderTable();
});

els.statusFilter.addEventListener("change", () => {
  state.statusFilter = els.statusFilter.value;
  renderTable();
});

document.querySelector("#open-compose").addEventListener("click", () => els.composeDialog.showModal());
document.querySelector("#close-compose").addEventListener("click", () => els.composeDialog.close());
document.querySelector("#cancel-compose").addEventListener("click", () => els.composeDialog.close());

els.composeForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(els.composeForm);
  const payload = {
    title: form.get("title"),
    content: form.get("content"),
    category: form.get("category"),
    module: form.get("module"),
    ownerId: form.get("ownerId"),
    tags: String(form.get("tags") || "").split(",").map((tag) => tag.trim()).filter(Boolean),
    authorId: state.currentUserId
  };

  await request("/api/feedback", {
    method: "POST",
    body: JSON.stringify(payload)
  });
  els.composeForm.reset();
  els.composeDialog.close();
  await refresh();
});

els.feed.addEventListener("click", async (event) => {
  const button = event.target.closest("button");
  if (!button) return;
  const card = event.target.closest(".feedback-card");
  if (!card) return;

  if (button.dataset.action === "like") {
    await request(`/api/feedback/${card.dataset.id}/like`, {
      method: "POST",
      body: JSON.stringify({ userId: state.currentUserId })
    });
    await refresh();
  }

  if (button.dataset.action === "focus-comment") {
    card.querySelector(".comment-form input")?.focus();
  }
});

els.feed.addEventListener("submit", async (event) => {
  const form = event.target.closest(".comment-form");
  if (!form) return;
  event.preventDefault();
  const card = event.target.closest(".feedback-card");
  const body = new FormData(form).get("body");

  await request(`/api/feedback/${card.dataset.id}/comments`, {
    method: "POST",
    body: JSON.stringify({ body, authorId: state.currentUserId })
  });
  form.reset();
  await refresh();
});

els.table.addEventListener("change", async (event) => {
  const select = event.target.closest("select[data-field]");
  if (!select) return;
  const row = event.target.closest("tr");

  await request(`/api/feedback/${row.dataset.id}`, {
    method: "PATCH",
    body: JSON.stringify({ [select.dataset.field]: select.value })
  });
  await refresh();
});

els.notifications.addEventListener("click", async (event) => {
  const item = event.target.closest(".notification");
  if (!item) return;
  await request(`/api/notifications/${item.dataset.id}/read`, { method: "PATCH" });
  await refresh();
});

els.settings.addEventListener("change", async (event) => {
  const input = event.target.closest("input[data-pref]");
  if (!input) return;
  const prefs = { ...(state.notificationPrefs[state.currentUserId] || {}) };
  prefs[input.dataset.pref] = input.checked;

  await request(`/api/users/${state.currentUserId}/notification-prefs`, {
    method: "PATCH",
    body: JSON.stringify(prefs)
  });
  await refresh();
});

refresh().catch((error) => {
  document.body.innerHTML = `<main class="main"><div class="empty">${escapeHtml(error.message)}</div></main>`;
});
