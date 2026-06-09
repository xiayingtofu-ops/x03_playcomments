from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlencode, urlparse
from urllib.request import Request, urlopen
from http.cookies import SimpleCookie
import json
import mimetypes
import os
import re
import shutil
import sqlite3
import secrets
import uuid
from datetime import datetime, timedelta, timezone


ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "platform_feedback.db"
UPLOAD_ROOT = ROOT / "platform_uploads"
MAX_BODY_SIZE = 220 * 1024 * 1024
COOKIE_NAME = "x03_session"
DEFAULT_BASE_URL = "http://127.0.0.1:8765"


def load_env():
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_env()


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def iso_after(seconds):
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


def safe_filename(name):
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name or "attachment")
    return cleaned.strip(" .") or "attachment"


def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS platform_feedback (
              id TEXT PRIMARY KEY,
              feedback_type TEXT NOT NULL,
              description TEXT NOT NULL,
              proposer TEXT NOT NULL DEFAULT '平台用户',
              proposer_key TEXT,
              proposer_email TEXT,
              status TEXT NOT NULL DEFAULT '未填',
              created_at TEXT NOT NULL
            )
            """
        )
        columns = {row[1] for row in conn.execute("PRAGMA table_info(platform_feedback)").fetchall()}
        if "proposer_key" not in columns:
            conn.execute("ALTER TABLE platform_feedback ADD COLUMN proposer_key TEXT")
        if "proposer_email" not in columns:
            conn.execute("ALTER TABLE platform_feedback ADD COLUMN proposer_email TEXT")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS platform_feedback_attachments (
              id TEXT PRIMARY KEY,
              feedback_id TEXT NOT NULL,
              filename TEXT NOT NULL,
              stored_name TEXT NOT NULL,
              path TEXT NOT NULL,
              mime_type TEXT,
              size_bytes INTEGER NOT NULL DEFAULT 0,
              created_at TEXT NOT NULL,
              FOREIGN KEY(feedback_id) REFERENCES platform_feedback(id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS platform_notification_settings (
              key TEXT PRIMARY KEY,
              label TEXT NOT NULL,
              description TEXT NOT NULL,
              enabled INTEGER NOT NULL DEFAULT 1,
              updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS platform_notifications (
              id TEXT PRIMARY KEY,
              type TEXT NOT NULL,
              title TEXT NOT NULL,
              body TEXT NOT NULL,
              receiver TEXT NOT NULL DEFAULT '未分配',
              read INTEGER NOT NULL DEFAULT 0,
              created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS platform_auth_sessions (
              id TEXT PRIMARY KEY,
              state TEXT,
              user_json TEXT,
              created_at TEXT NOT NULL,
              expires_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS platform_record_likes (
              record_id TEXT NOT NULL,
              user_key TEXT NOT NULL,
              user_name TEXT NOT NULL,
              created_at TEXT NOT NULL,
              PRIMARY KEY (record_id, user_key)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS platform_record_follows (
              record_id TEXT NOT NULL,
              user_key TEXT NOT NULL,
              user_name TEXT NOT NULL,
              created_at TEXT NOT NULL,
              PRIMARY KEY (record_id, user_key)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS platform_record_comments (
              id TEXT PRIMARY KEY,
              record_id TEXT NOT NULL,
              author_key TEXT NOT NULL,
              author_name TEXT NOT NULL,
              body TEXT NOT NULL,
              created_at TEXT NOT NULL
            )
            """
        )
        defaults = [
            ("likes", "收到点赞", "别人赞同我提出的反馈时生成站内通知。"),
            ("comments", "收到评论", "别人评论我提出或关注的反馈时生成站内通知。"),
            ("status_changes", "状态变化", "策划在表格里调整处理状态时生成站内通知。"),
            ("assignments", "分配给我", "新反馈分配到我负责时生成站内通知。"),
        ]
        for key, label, description in defaults:
            conn.execute(
                """
                INSERT OR IGNORE INTO platform_notification_settings
                  (key, label, description, enabled, updated_at)
                VALUES (?, ?, ?, 1, ?)
                """,
                (key, label, description, now_iso()),
            )
        existing = conn.execute("SELECT COUNT(*) FROM platform_notifications").fetchone()[0]
        if not existing:
            conn.execute(
                """
                INSERT INTO platform_notifications
                  (id, type, title, body, receiver, read, created_at)
                VALUES (?, ?, ?, ?, ?, 0, ?)
                """,
                (
                    "ntf_seed_assignment",
                    "分配",
                    "全夏瀛 提交了一条分配给你的反馈",
                    "接收人：未分配",
                    "未分配",
                    "2026-06-05T14:22:00+08:00",
                ),
            )


def row_to_dict(row):
    return {key: row[key] for key in row.keys()}


def list_feedback():
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM platform_feedback ORDER BY created_at DESC"
        ).fetchall()
        attachments = conn.execute(
            "SELECT * FROM platform_feedback_attachments ORDER BY created_at ASC"
        ).fetchall()

    by_feedback = {}
    for attachment in attachments:
        item = row_to_dict(attachment)
        by_feedback.setdefault(item["feedback_id"], []).append(item)

    feedback = []
    for index, row in enumerate(rows):
        item = row_to_dict(row)
        item["sort_no"] = 1000000 - index
        item["attachments"] = by_feedback.get(item["id"], [])
        feedback.append(item)
    return feedback


def get_feedback(feedback_id):
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM platform_feedback WHERE id = ?", (feedback_id,)
        ).fetchone()
        if not row:
            return None
        attachments = conn.execute(
            "SELECT * FROM platform_feedback_attachments WHERE feedback_id = ? ORDER BY created_at ASC",
            (feedback_id,),
        ).fetchall()
    item = row_to_dict(row)
    item["sort_no"] = 1000001
    item["attachments"] = [row_to_dict(attachment) for attachment in attachments]
    return item


def get_notification_payload():
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        settings = conn.execute(
            "SELECT key, label, description, enabled FROM platform_notification_settings ORDER BY rowid ASC"
        ).fetchall()
        notifications = conn.execute(
            "SELECT * FROM platform_notifications ORDER BY created_at DESC LIMIT 20"
        ).fetchall()
    items = [row_to_dict(row) for row in notifications]
    return {
        "settings": [row_to_dict(row) for row in settings],
        "notifications": items,
        "unread_count": sum(1 for item in items if not item["read"]),
    }


def update_notification_setting(key, enabled):
    with sqlite3.connect(DB_PATH) as conn:
        updated = conn.execute(
            """
            UPDATE platform_notification_settings
            SET enabled = ?, updated_at = ?
            WHERE key = ?
            """,
            (1 if enabled else 0, now_iso(), key),
        ).rowcount
    if not updated:
        raise ValueError("通知配置不存在")
    return get_notification_payload()


def mark_notifications_read():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("UPDATE platform_notifications SET read = 1")
    return get_notification_payload()


def interaction_user(user):
    if not user:
        return "guest", "平台用户"
    return user.get("open_id") or user.get("union_id") or user.get("name") or "guest", user.get("name") or "飞书用户"


def admin_values():
    raw = os.getenv("PLATFORM_ADMIN_USERS", "")
    return {item.strip().lower() for item in raw.split(",") if item.strip()}


def is_admin_user(user):
    if not user:
        return False
    values = admin_values()
    if not values:
        return False
    candidates = {
        str(user.get("open_id") or "").lower(),
        str(user.get("union_id") or "").lower(),
        str(user.get("email") or "").lower(),
        str(user.get("name") or "").lower(),
    }
    return bool(values & candidates)


def public_user(user):
    if not user:
        return None
    item = dict(user)
    item["is_admin"] = is_admin_user(user)
    return item


def get_record_interactions(user=None):
    user_key, _ = interaction_user(user)
    def default_interaction():
        return {"like_count": 0, "liked_by_me": False, "follow_count": 0, "followed_by_me": False, "comments": []}

    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        likes = conn.execute(
            """
            SELECT record_id, COUNT(*) AS like_count,
                   SUM(CASE WHEN user_key = ? THEN 1 ELSE 0 END) AS liked_by_me
            FROM platform_record_likes
            GROUP BY record_id
            """,
            (user_key,),
        ).fetchall()
        follows = conn.execute(
            """
            SELECT record_id, COUNT(*) AS follow_count,
                   SUM(CASE WHEN user_key = ? THEN 1 ELSE 0 END) AS followed_by_me
            FROM platform_record_follows
            GROUP BY record_id
            """,
            (user_key,),
        ).fetchall()
        comments = conn.execute(
            """
            SELECT * FROM platform_record_comments
            ORDER BY created_at ASC
            """
        ).fetchall()

    data = {}
    for row in likes:
        item = row_to_dict(row)
        data.setdefault(item["record_id"], default_interaction())
        data[item["record_id"]]["like_count"] = item["like_count"]
        data[item["record_id"]]["liked_by_me"] = bool(item["liked_by_me"])

    for row in follows:
        item = row_to_dict(row)
        data.setdefault(item["record_id"], default_interaction())
        data[item["record_id"]]["follow_count"] = item["follow_count"]
        data[item["record_id"]]["followed_by_me"] = bool(item["followed_by_me"])

    for row in comments:
        item = row_to_dict(row)
        data.setdefault(item["record_id"], default_interaction())
        data[item["record_id"]]["comments"].append(item)
    return data


def toggle_record_like(record_id, user):
    if not record_id:
        raise ValueError("缺少反馈记录")
    user_key, user_name = interaction_user(user)
    with sqlite3.connect(DB_PATH) as conn:
        exists = conn.execute(
            "SELECT 1 FROM platform_record_likes WHERE record_id = ? AND user_key = ?",
            (record_id, user_key),
        ).fetchone()
        if exists:
            conn.execute(
                "DELETE FROM platform_record_likes WHERE record_id = ? AND user_key = ?",
                (record_id, user_key),
            )
        else:
            conn.execute(
                """
                INSERT INTO platform_record_likes (record_id, user_key, user_name, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (record_id, user_key, user_name, now_iso()),
            )
    return get_record_interactions(user).get(record_id, {"like_count": 0, "liked_by_me": False, "follow_count": 0, "followed_by_me": False, "comments": []})


def toggle_record_follow(record_id, user):
    if not record_id:
        raise ValueError("缺少反馈记录")
    user_key, user_name = interaction_user(user)
    with sqlite3.connect(DB_PATH) as conn:
        exists = conn.execute(
            "SELECT 1 FROM platform_record_follows WHERE record_id = ? AND user_key = ?",
            (record_id, user_key),
        ).fetchone()
        if exists:
            conn.execute(
                "DELETE FROM platform_record_follows WHERE record_id = ? AND user_key = ?",
                (record_id, user_key),
            )
        else:
            conn.execute(
                """
                INSERT INTO platform_record_follows (record_id, user_key, user_name, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (record_id, user_key, user_name, now_iso()),
            )
    return get_record_interactions(user).get(record_id, {"like_count": 0, "liked_by_me": False, "follow_count": 0, "followed_by_me": False, "comments": []})


def add_record_comment(record_id, body, user):
    record_id = (record_id or "").strip()
    body = (body or "").strip()
    if not record_id:
        raise ValueError("缺少反馈记录")
    if len(body) < 1:
        raise ValueError("请输入评论内容")
    if len(body) > 1000:
        raise ValueError("评论最多 1000 个字符")
    user_key, user_name = interaction_user(user)
    comment = {
        "id": "cmt_" + uuid.uuid4().hex[:16],
        "record_id": record_id,
        "author_key": user_key,
        "author_name": user_name,
        "body": body,
        "created_at": now_iso(),
    }
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO platform_record_comments
              (id, record_id, author_key, author_name, body, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                comment["id"],
                comment["record_id"],
                comment["author_key"],
                comment["author_name"],
                comment["body"],
                comment["created_at"],
            ),
        )
    return comment


def public_base_url():
    return (os.getenv("PUBLIC_BASE_URL") or os.getenv("BASE_URL") or DEFAULT_BASE_URL).rstrip("/")


def redirect_path():
    return os.getenv("FEISHU_REDIRECT_PATH", "/auth/feishu/callback")


def feishu_redirect_url():
    return public_base_url() + redirect_path()


def feishu_auth_configured():
    return bool(os.getenv("FEISHU_APP_ID") and os.getenv("FEISHU_APP_SECRET"))


def feishu_auth_required():
    value = os.getenv("FEISHU_AUTH_REQUIRED", "true").strip().lower()
    return value not in {"0", "false", "no", "off"}


def feishu_authorize_url(state):
    app_id = os.getenv("FEISHU_APP_ID")
    if not app_id:
        raise ValueError("缺少 FEISHU_APP_ID")
    query = urlencode(
        {
            "app_id": app_id,
            "redirect_uri": feishu_redirect_url(),
            "state": state,
        },
        quote_via=quote,
    )
    return f"https://open.feishu.cn/open-apis/authen/v1/index?{query}"


def post_json(url, payload, bearer=None):
    headers = {"Content-Type": "application/json; charset=utf-8"}
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urlopen(request, timeout=12) as response:
        return json.loads(response.read().decode("utf-8"))


def get_json(url, bearer):
    request = Request(url, headers={"Authorization": f"Bearer {bearer}"})
    with urlopen(request, timeout=12) as response:
        return json.loads(response.read().decode("utf-8"))


def feishu_exchange_code(code):
    app_token_response = post_json(
        "https://open.feishu.cn/open-apis/auth/v3/app_access_token/internal",
        {
            "app_id": os.getenv("FEISHU_APP_ID"),
            "app_secret": os.getenv("FEISHU_APP_SECRET"),
        },
    )
    if app_token_response.get("code") != 0:
        raise ValueError(app_token_response.get("msg") or "获取飞书应用凭证失败")

    access_response = post_json(
        "https://open.feishu.cn/open-apis/authen/v1/access_token",
        {"grant_type": "authorization_code", "code": code},
        bearer=app_token_response.get("app_access_token"),
    )
    if access_response.get("code") != 0:
        raise ValueError(access_response.get("msg") or "飞书授权码交换失败")

    user_access_token = (access_response.get("data") or {}).get("access_token")
    if not user_access_token:
        raise ValueError("飞书没有返回用户访问凭证")

    user_response = get_json(
        "https://open.feishu.cn/open-apis/authen/v1/user_info",
        bearer=user_access_token,
    )
    if user_response.get("code") != 0:
        raise ValueError(user_response.get("msg") or "获取飞书用户信息失败")
    data = user_response.get("data") or {}
    return {
        "open_id": data.get("open_id"),
        "union_id": data.get("union_id"),
        "name": data.get("name") or data.get("en_name") or "飞书用户",
        "avatar_url": data.get("avatar_url"),
        "email": data.get("email"),
        "mobile": data.get("mobile"),
        "tenant_key": data.get("tenant_key"),
        "login_at": now_iso(),
    }


def create_login_session():
    session_id = secrets.token_urlsafe(32)
    state = secrets.token_urlsafe(24)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO platform_auth_sessions (id, state, user_json, created_at, expires_at)
            VALUES (?, ?, NULL, ?, ?)
            """,
            (session_id, state, now_iso(), iso_after(600)),
        )
    return session_id, state


def get_auth_session(session_id):
    if not session_id:
        return None
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM platform_auth_sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
    if not row:
        return None
    item = row_to_dict(row)
    try:
        expired = datetime.fromisoformat(item["expires_at"]) < datetime.now(timezone.utc)
    except ValueError:
        expired = True
    if expired:
        delete_auth_session(session_id)
        return None
    if item.get("user_json"):
        item["user"] = json.loads(item["user_json"])
    return item


def complete_auth_session(session_id, state, user):
    session = get_auth_session(session_id)
    if not session or not session.get("state") or session.get("state") != state:
        raise ValueError("登录状态已失效，请重新登录")
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            UPDATE platform_auth_sessions
            SET state = NULL, user_json = ?, expires_at = ?
            WHERE id = ?
            """,
            (json.dumps(user, ensure_ascii=False), iso_after(30 * 24 * 60 * 60), session_id),
        )


def delete_auth_session(session_id):
    if not session_id:
        return
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM platform_auth_sessions WHERE id = ?", (session_id,))


def parse_content_disposition(value):
    result = {}
    for key, raw in re.findall(r'([a-zA-Z_-]+)="([^"]*)"', value or ""):
        result[key.lower()] = raw
    return result


def parse_multipart(content_type, body):
    match = re.search(r"boundary=(?P<boundary>[^;]+)", content_type or "")
    if not match:
        raise ValueError("缺少上传边界")
    boundary = match.group("boundary").strip().strip('"').encode("utf-8")
    marker = b"--" + boundary
    fields = {}
    files = []

    for part in body.split(marker):
        part = part.strip(b"\r\n")
        if not part or part == b"--":
            continue
        if part.endswith(b"--"):
            part = part[:-2].rstrip(b"\r\n")
        if b"\r\n\r\n" not in part:
            continue
        header_blob, data = part.split(b"\r\n\r\n", 1)
        data = data.rstrip(b"\r\n")
        headers = {}
        for line in header_blob.decode("utf-8", "replace").split("\r\n"):
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            headers[key.lower().strip()] = value.strip()

        disposition = parse_content_disposition(headers.get("content-disposition"))
        name = disposition.get("name")
        filename = disposition.get("filename")
        if not name:
            continue
        if filename:
            files.append(
                {
                    "field": name,
                    "filename": filename,
                    "mime_type": headers.get("content-type", "application/octet-stream"),
                    "content": data,
                }
            )
        else:
            fields[name] = data.decode("utf-8", "replace")
    return fields, files


def create_feedback(fields, files, user=None):
    description = (fields.get("description") or "").strip()
    feedback_type = (fields.get("feedback_type") or "战斗模块").strip()
    user_key, user_name = interaction_user(user)
    proposer = (user_name if user else (fields.get("proposer") or "平台用户")).strip()
    proposer_key = user_key if user else ""
    proposer_email = (user or {}).get("email") or ""

    if len(description) < 10:
        raise ValueError("请至少输入 10 个字符")
    if len(files) > 9:
        raise ValueError("附件最多上传 9 个")

    feedback_id = "pfb_" + uuid.uuid4().hex[:16]
    created_at = now_iso()
    upload_dir = UPLOAD_ROOT / feedback_id
    upload_dir.mkdir(parents=True, exist_ok=True)

    saved_files = []
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO platform_feedback
              (id, feedback_type, description, proposer, proposer_key, proposer_email, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (feedback_id, feedback_type, description, proposer, proposer_key, proposer_email, "未填", created_at),
        )

        for index, file_item in enumerate(files, start=1):
            attachment_id = "pfa_" + uuid.uuid4().hex[:16]
            original_name = safe_filename(file_item["filename"])
            stored_name = f"{index:02d}_{uuid.uuid4().hex[:8]}_{original_name}"
            file_path = upload_dir / stored_name
            file_path.write_bytes(file_item["content"])
            rel_path = f"platform_uploads/{feedback_id}/{stored_name}"
            size = file_path.stat().st_size
            conn.execute(
                """
                INSERT INTO platform_feedback_attachments
                  (id, feedback_id, filename, stored_name, path, mime_type, size_bytes, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    attachment_id,
                    feedback_id,
                    original_name,
                    stored_name,
                    rel_path,
                    file_item["mime_type"],
                    size,
                    created_at,
                ),
            )
            saved_files.append(
                {
                    "id": attachment_id,
                    "feedback_id": feedback_id,
                    "filename": original_name,
                    "stored_name": stored_name,
                    "path": rel_path,
                    "mime_type": file_item["mime_type"],
                    "size_bytes": size,
                    "created_at": created_at,
                }
            )

    item = get_feedback(feedback_id)
    item["attachments"] = saved_files
    return item


def can_delete_feedback(item, user):
    if not item or not user:
        return False
    if is_admin_user(user):
        return True
    user_key, user_name = interaction_user(user)
    candidates = {
        user_key,
        user.get("open_id") or "",
        user.get("union_id") or "",
        user.get("email") or "",
    }
    candidates = {value for value in candidates if value}
    owner_keys = {item.get("proposer_key") or "", item.get("proposer_email") or ""}
    owner_keys = {value for value in owner_keys if value}
    if candidates & owner_keys:
        return True
    return bool(item.get("proposer") and item.get("proposer") == user_name)


def delete_feedback(feedback_id, user):
    feedback_id = (feedback_id or "").strip()
    if not feedback_id:
        raise ValueError("缺少反馈记录")
    item = get_feedback(feedback_id)
    if not item:
        raise FileNotFoundError("记录不存在")
    if not can_delete_feedback(item, user):
        raise PermissionError("你没有权限删除这条记录")

    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM platform_record_likes WHERE record_id = ?", (feedback_id,))
        conn.execute("DELETE FROM platform_record_follows WHERE record_id = ?", (feedback_id,))
        conn.execute("DELETE FROM platform_record_comments WHERE record_id = ?", (feedback_id,))
        conn.execute("DELETE FROM platform_feedback_attachments WHERE feedback_id = ?", (feedback_id,))
        conn.execute("DELETE FROM platform_feedback WHERE id = ?", (feedback_id,))

    upload_dir = (UPLOAD_ROOT / feedback_id).resolve()
    if str(upload_dir).startswith(str(UPLOAD_ROOT.resolve())):
        shutil.rmtree(upload_dir, ignore_errors=True)
    return item


class PlatformHandler(BaseHTTPRequestHandler):
    server_version = "PlatformFeedback/0.1"

    def send_json(self, status, payload):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def cookie_value(self, name):
        cookie = SimpleCookie(self.headers.get("Cookie"))
        morsel = cookie.get(name)
        return morsel.value if morsel else None

    def set_session_cookie(self, session_id, max_age=30 * 24 * 60 * 60):
        parts = [
            f"{COOKIE_NAME}={session_id}",
            "Path=/",
            "HttpOnly",
            "SameSite=Lax",
            f"Max-Age={max_age}",
        ]
        if public_base_url().startswith("https://"):
            parts.append("Secure")
        self.send_header("Set-Cookie", "; ".join(parts))

    def clear_session_cookie(self):
        self.send_header(
            "Set-Cookie",
            f"{COOKIE_NAME}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0",
        )

    def redirect(self, location, cookie_session_id=None, clear_cookie=False):
        self.send_response(302)
        self.send_header("Location", location)
        if cookie_session_id:
            self.set_session_cookie(cookie_session_id)
        if clear_cookie:
            self.clear_session_cookie()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path in {"", "/", "/platform.html"} and feishu_auth_required() and feishu_auth_configured():
            session = get_auth_session(self.cookie_value(COOKIE_NAME))
            if not session or not session.get("user"):
                session_id, state = create_login_session()
                self.redirect(feishu_authorize_url(state), cookie_session_id=session_id)
                return

        if parsed.path == "/api/platform-feedback":
            self.send_json(200, {"feedback": list_feedback()})
            return
        if parsed.path == "/api/notification-settings":
            self.send_json(200, get_notification_payload())
            return
        if parsed.path == "/api/record-interactions":
            session = get_auth_session(self.cookie_value(COOKIE_NAME))
            self.send_json(200, {"records": get_record_interactions((session or {}).get("user"))})
            return
        if parsed.path == "/api/auth/me":
            session = get_auth_session(self.cookie_value(COOKIE_NAME))
            self.send_json(
                200,
                {
                    "authenticated": bool(session and session.get("user")),
                    "user": public_user((session or {}).get("user")),
                    "configured": feishu_auth_configured(),
                    "login_url": "/api/auth/feishu/login",
                    "logout_url": "/api/auth/logout",
                    "redirect_url": feishu_redirect_url(),
                },
            )
            return
        if parsed.path == "/api/auth/feishu/login":
            try:
                if not feishu_auth_configured():
                    self.send_json(
                        500,
                        {
                            "error": "飞书登录尚未配置，请先设置 FEISHU_APP_ID 和 FEISHU_APP_SECRET。",
                            "redirect_url": feishu_redirect_url(),
                        },
                    )
                    return
                session_id, state = create_login_session()
                self.redirect(feishu_authorize_url(state), cookie_session_id=session_id)
            except Exception as exc:
                self.send_json(500, {"error": str(exc)})
            return
        if parsed.path == redirect_path():
            query = parse_qs(parsed.query)
            code = (query.get("code") or [""])[0]
            state = (query.get("state") or [""])[0]
            session_id = self.cookie_value(COOKIE_NAME)
            try:
                if not code:
                    raise ValueError("飞书没有返回授权码")
                user = feishu_exchange_code(code)
                complete_auth_session(session_id, state, user)
                self.redirect("/platform.html?login=success")
            except Exception as exc:
                self.redirect(f"/platform.html?auth_error={quote(str(exc))}", clear_cookie=True)
            return
        self.serve_static(parsed.path)

    def do_POST(self):
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/platform-feedback":
                length = int(self.headers.get("Content-Length", "0"))
                if length > MAX_BODY_SIZE:
                    self.send_json(413, {"error": "上传内容过大"})
                    return
                body = self.rfile.read(length)
                content_type = self.headers.get("Content-Type", "")
                if content_type.startswith("multipart/form-data"):
                    fields, files = parse_multipart(content_type, body)
                else:
                    fields = json.loads(body.decode("utf-8") or "{}")
                    files = []
                session = get_auth_session(self.cookie_value(COOKIE_NAME))
                feedback = create_feedback(fields, files, (session or {}).get("user"))
                self.send_json(201, {"feedback": feedback})
                return

            if parsed.path == "/api/notification-settings":
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length)
                payload = json.loads(body.decode("utf-8") or "{}")
                self.send_json(200, update_notification_setting(payload.get("key"), payload.get("enabled")))
                return

            if parsed.path == "/api/notifications/read-all":
                self.send_json(200, mark_notifications_read())
                return

            if parsed.path == "/api/record-likes":
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                session = get_auth_session(self.cookie_value(COOKIE_NAME))
                interaction = toggle_record_like(payload.get("record_id"), (session or {}).get("user"))
                self.send_json(200, {"interaction": interaction})
                return

            if parsed.path == "/api/record-follows":
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                session = get_auth_session(self.cookie_value(COOKIE_NAME))
                interaction = toggle_record_follow(payload.get("record_id"), (session or {}).get("user"))
                self.send_json(200, {"interaction": interaction})
                return

            if parsed.path == "/api/record-comments":
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                session = get_auth_session(self.cookie_value(COOKIE_NAME))
                comment = add_record_comment(payload.get("record_id"), payload.get("body"), (session or {}).get("user"))
                interaction = get_record_interactions((session or {}).get("user")).get(payload.get("record_id"), {})
                self.send_json(201, {"comment": comment, "interaction": interaction})
                return

            if parsed.path == "/api/auth/logout":
                session_id = self.cookie_value(COOKIE_NAME)
                delete_auth_session(session_id)
                self.send_response(200)
                self.clear_session_cookie()
                data = json.dumps({"ok": True}).encode("utf-8")
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return

            self.send_json(404, {"error": "接口不存在"})
        except PermissionError as exc:
            self.send_json(403, {"error": str(exc)})
        except ValueError as exc:
            self.send_json(400, {"error": str(exc)})
        except Exception as exc:
            self.send_json(500, {"error": f"保存失败：{exc}"})

    def do_DELETE(self):
        parsed = urlparse(self.path)
        try:
            match = re.fullmatch(r"/api/platform-feedback/([^/]+)", parsed.path)
            if match:
                session = get_auth_session(self.cookie_value(COOKIE_NAME))
                delete_feedback(unquote(match.group(1)), (session or {}).get("user"))
                self.send_json(200, {"ok": True})
                return
            self.send_json(404, {"error": "接口不存在"})
        except FileNotFoundError as exc:
            self.send_json(404, {"error": str(exc)})
        except PermissionError as exc:
            self.send_json(403, {"error": str(exc)})
        except ValueError as exc:
            self.send_json(400, {"error": str(exc)})
        except Exception as exc:
            self.send_json(500, {"error": f"删除失败：{exc}"})

    def serve_static(self, request_path):
        relative = unquote(request_path.lstrip("/")) or "platform.html"
        target = (ROOT / relative).resolve()
        if not str(target).startswith(str(ROOT)) or not target.exists() or target.is_dir():
            self.send_error(404)
            return

        mime_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if target.suffix in {".html", ".css", ".js", ".json", ".csv"}:
            mime_type += "; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", mime_type)
        self.send_header("Content-Length", str(target.stat().st_size))
        self.end_headers()
        with target.open("rb") as file_obj:
            shutil.copyfileobj(file_obj, self.wfile)


def main():
    init_db()
    UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", 8765), PlatformHandler)
    print("Platform feedback server: http://127.0.0.1:8765/platform.html")
    server.serve_forever()


if __name__ == "__main__":
    main()
