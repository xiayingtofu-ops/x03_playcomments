from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import unquote, urlparse
import json
import mimetypes
import re
import shutil
import sqlite3
import uuid
from datetime import datetime, timezone


ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "platform_feedback.db"
UPLOAD_ROOT = ROOT / "platform_uploads"
MAX_BODY_SIZE = 220 * 1024 * 1024


def now_iso():
    return datetime.now(timezone.utc).isoformat()


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
              status TEXT NOT NULL DEFAULT '未填',
              created_at TEXT NOT NULL
            )
            """
        )
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


def create_feedback(fields, files):
    description = (fields.get("description") or "").strip()
    feedback_type = (fields.get("feedback_type") or "战斗模块").strip()
    proposer = (fields.get("proposer") or "平台用户").strip()

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
            INSERT INTO platform_feedback (id, feedback_type, description, proposer, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (feedback_id, feedback_type, description, proposer, "未填", created_at),
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


class PlatformHandler(BaseHTTPRequestHandler):
    server_version = "PlatformFeedback/0.1"

    def send_json(self, status, payload):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/platform-feedback":
            self.send_json(200, {"feedback": list_feedback()})
            return
        if parsed.path == "/api/notification-settings":
            self.send_json(200, get_notification_payload())
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
                feedback = create_feedback(fields, files)
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

            self.send_json(404, {"error": "接口不存在"})
        except ValueError as exc:
            self.send_json(400, {"error": str(exc)})
        except Exception as exc:
            self.send_json(500, {"error": f"保存失败：{exc}"})

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
