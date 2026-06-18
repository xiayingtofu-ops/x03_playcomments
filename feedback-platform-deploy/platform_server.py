from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlencode, urlparse
from urllib.request import Request, urlopen
from http.cookies import SimpleCookie
import csv
import json
import mimetypes
import os
import re
import shutil
import sqlite3
import secrets
import subprocess
import threading
import time
import traceback
import uuid
from datetime import datetime, timedelta, timezone


ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "platform_feedback.db"
UPLOAD_ROOT = ROOT / "platform_uploads"
MAX_BODY_SIZE = 220 * 1024 * 1024
COOKIE_NAME = "x03_session"
DEFAULT_PLANNER_NAMES = [
    "王嘉西",
    "刘洋",
    "张泽臻",
    "姜勋",
    "吴子轩",
    "王宇轩",
    "杨心权",
    "李尧",
    "仝夏瀛",
]
PLANNER_NAME_ALIASES = {
    "张泽臻(小萨)": "张泽臻",
    "王宇轩（实习）": "王宇轩",
    "王宇轩(实习)": "王宇轩",
}
DEFAULT_BASE_URL = "http://127.0.0.1:8765"
DEFAULT_ENTRY_PATH = "/feedback.html"
DEFAULT_FEISHU_CONNECTOR = r"C:\Users\Administrator\plugins\lark-enterprise\scripts\feishu-connector.cmd"
DEFAULT_FEISHU_BASE_TOKEN = "PQTsbEtUPaV80isZ5dRcVFKsn6f"
DEFAULT_FEISHU_TABLE_ID = "tblYhznxxvRtSyY9"
DEFAULT_FEISHU_VIEW_ID = "vew9FrF9dW"
DEFAULT_FEISHU_BOT_WEBHOOK_URL = ""
FEISHU_FIELD_DESCRIPTION = "fldHy9HQO9"
FEISHU_FIELD_REMARK = "fldPTWrwky"
FEISHU_FIELD_SCREENSHOT = "fldmlme6mj"
FEISHU_FIELD_PROBLEM_IMAGE = "fldftLhjJK"
FEISHU_ATTACHMENT_FIELDS = (
    (FEISHU_FIELD_SCREENSHOT, "截图"),
)
SYNC_LOCK = threading.Lock()
PLANNER_NOTE_SYNC_DELAY_SECONDS = 5 * 60
REFRESH_HISTORY_DIR = "refresh_history"


def load_env():
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip().lstrip("\ufeff"), value.strip().strip('"').strip("'"))


load_env()


def runtime_path(env_key, default_path):
    raw = os.getenv(env_key, "").strip()
    if not raw:
        return default_path
    path = Path(raw).expanduser()
    if path.is_absolute():
        return path
    return ROOT / path


DB_PATH = runtime_path("DB_PATH", DB_PATH)
UPLOAD_ROOT = runtime_path("UPLOAD_ROOT", UPLOAD_ROOT)
COOKIE_NAME = os.getenv("COOKIE_NAME", COOKIE_NAME).strip() or COOKIE_NAME


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def iso_after(seconds):
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


def feishu_bot_webhook_url():
    return os.getenv("FEISHU_BOT_WEBHOOK_URL", DEFAULT_FEISHU_BOT_WEBHOOK_URL).strip()


def notify_platform_event(title, details=None, level="info"):
    url = feishu_bot_webhook_url()
    if not url:
        return
    lines = [
        f"[X03 feedback platform] {title}",
        f"Level: {level}",
        f"Time: {now_iso()}",
    ]
    if isinstance(details, dict):
        for key, value in details.items():
            if value is None or value == "":
                continue
            text = str(value)
            if len(text) > 1200:
                text = text[:1200] + "...(truncated)"
            lines.append(f"{key}: {text}")
    elif details:
        lines.append(str(details))
    payload = {
        "msg_type": "text",
        "content": {"text": "\n".join(lines)[:3600]},
    }
    request = Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=5) as response:
            response.read()
    except Exception as exc:
        print(f"Feishu bot webhook failed: {exc}")


def notify_platform_error(title, exc, details=None):
    payload = dict(details or {})
    payload["error"] = str(exc)
    payload["traceback"] = traceback.format_exc(limit=8)
    notify_platform_event(title, payload, level="error")


def safe_filename(name):
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name or "attachment")
    return cleaned.strip(" .") or "attachment"


def compact_notice_text(value, limit=900):
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "...(truncated)"


def human_file_size(size_bytes):
    try:
        size = int(size_bytes or 0)
    except (TypeError, ValueError):
        size = 0
    if size >= 1024 * 1024:
        return f"{size / 1024 / 1024:.1f}MB"
    if size >= 1024:
        return f"{round(size / 1024)}KB"
    return f"{size}B"


def feedback_attachment_notice(attachments):
    items = attachments or []
    if not items:
        return "无附件"
    lines = []
    for index, attachment in enumerate(items[:8], start=1):
        name = attachment.get("filename") or attachment.get("name") or "未命名附件"
        size = human_file_size(attachment.get("size_bytes") or attachment.get("size") or 0)
        path = attachment.get("path") or ""
        lines.append(f"{index}. {name} ({size}) {path}".strip())
    if len(items) > 8:
        lines.append(f"... 还有 {len(items) - 8} 个附件")
    return "\n".join(lines)


def feedback_replies_notice(record_id):
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT author_name, body, created_at
            FROM platform_record_comments
            WHERE record_id = ?
            ORDER BY created_at DESC
            LIMIT 5
            """,
            (record_id,),
        ).fetchall()
    if not rows:
        return "暂无回复"
    lines = []
    for row in rows:
        author = row["author_name"] or "平台用户"
        body = compact_notice_text(row["body"], 240)
        created_at = row["created_at"] or ""
        lines.append(f"- {author} [{created_at}]: {body}")
    return "\n".join(lines)


def notify_feedback_saved(feedback):
    notify_platform_event(
        "New feedback saved on server",
        {
            "feedback_id": feedback.get("id"),
            "created_at": feedback.get("created_at"),
            "who": feedback.get("proposer") or "平台用户",
            "who_key": feedback.get("proposer_key") or feedback.get("proposer_email") or "",
            "problem_description": compact_notice_text(feedback.get("description"), 1200),
            "replies": feedback_replies_notice(feedback.get("id")),
            "attachments": feedback_attachment_notice(feedback.get("attachments") or []),
        },
        level="info",
    )


def env_bool(key, default=False):
    raw = os.getenv(key)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def feishu_sync_config():
    try:
        interval_hours = max(1, int(os.getenv("FEISHU_SYNC_INTERVAL_HOURS", "24") or "24"))
    except ValueError:
        interval_hours = 24
    return {
        "enabled": env_bool("FEISHU_SYNC_ENABLED", True),
        "writeback_enabled": env_bool("FEISHU_SYNC_WRITEBACK_ENABLED", True),
        "connector": os.getenv("FEISHU_CONNECTOR", DEFAULT_FEISHU_CONNECTOR),
        "base_token": os.getenv("FEISHU_BASE_TOKEN", DEFAULT_FEISHU_BASE_TOKEN),
        "table_id": os.getenv("FEISHU_TABLE_ID", DEFAULT_FEISHU_TABLE_ID),
        "view_id": os.getenv("FEISHU_VIEW_ID", DEFAULT_FEISHU_VIEW_ID),
        "interval_hours": interval_hours,
    }


def feishu_sync_ready(config=None):
    config = config or feishu_sync_config()
    return bool(config["enabled"] and config["connector"] and config["base_token"] and config["table_id"])


def parse_json_output(text):
    stripped = (text or "").strip()
    if not stripped:
        raise ValueError("飞书命令没有返回内容")
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start >= 0 and end > start:
            return json.loads(stripped[start : end + 1])
        start = stripped.find("[")
        end = stripped.rfind("]")
        if start >= 0 and end > start:
            return json.loads(stripped[start : end + 1])
        raise


def run_lark_cli(args, timeout=120):
    config = feishu_sync_config()
    command = [config["connector"], "lark-cli", "--", *args]
    proc = subprocess.run(
        command,
        cwd=ROOT,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=timeout,
    )
    if proc.returncode != 0:
        detail = (proc.stdout + "\n" + proc.stderr).strip()
        raise RuntimeError(detail or f"飞书命令执行失败：{proc.returncode}")
    payload = parse_json_output(proc.stdout)
    if isinstance(payload, dict) and payload.get("ok") is False:
        raise RuntimeError(json.dumps(payload, ensure_ascii=False))
    return payload


def find_bool(payload, key):
    if isinstance(payload, dict):
        if isinstance(payload.get(key), bool):
            return payload[key]
        for value in payload.values():
            found = find_bool(value, key)
            if found is not None:
                return found
    elif isinstance(payload, list):
        for value in payload:
            found = find_bool(value, key)
            if found is not None:
                return found
    return None


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
        if "feishu_record_id" not in columns:
            conn.execute("ALTER TABLE platform_feedback ADD COLUMN feishu_record_id TEXT")
        if "feishu_sync_status" not in columns:
            conn.execute("ALTER TABLE platform_feedback ADD COLUMN feishu_sync_status TEXT NOT NULL DEFAULT 'pending'")
        if "feishu_sync_error" not in columns:
            conn.execute("ALTER TABLE platform_feedback ADD COLUMN feishu_sync_error TEXT")
        if "feishu_synced_at" not in columns:
            conn.execute("ALTER TABLE platform_feedback ADD COLUMN feishu_synced_at TEXT")
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
              receiver_key TEXT,
              record_id TEXT,
              read INTEGER NOT NULL DEFAULT 0,
              created_at TEXT NOT NULL
            )
            """
        )
        notification_columns = {row[1] for row in conn.execute("PRAGMA table_info(platform_notifications)").fetchall()}
        if "receiver_key" not in notification_columns:
            conn.execute("ALTER TABLE platform_notifications ADD COLUMN receiver_key TEXT")
        if "record_id" not in notification_columns:
            conn.execute("ALTER TABLE platform_notifications ADD COLUMN record_id TEXT")
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
            CREATE TABLE IF NOT EXISTS platform_sync_state (
              key TEXT PRIMARY KEY,
              value TEXT NOT NULL,
              updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS platform_team_members (
              id TEXT PRIMARY KEY,
              name TEXT NOT NULL UNIQUE,
              open_id TEXT,
              union_id TEXT,
              email TEXT,
              is_admin INTEGER NOT NULL DEFAULT 0,
              is_planner INTEGER NOT NULL DEFAULT 0,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            )
            """
        )
        team_columns = {row[1] for row in conn.execute("PRAGMA table_info(platform_team_members)").fetchall()}
        added_planner_column = False
        if "is_planner" not in team_columns:
            conn.execute("ALTER TABLE platform_team_members ADD COLUMN is_planner INTEGER NOT NULL DEFAULT 0")
            added_planner_column = True
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
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS platform_record_assignments (
              record_id TEXT PRIMARY KEY,
              planner_name TEXT NOT NULL,
              planner_key TEXT,
              updated_by_key TEXT NOT NULL,
              updated_by_name TEXT NOT NULL,
              updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS platform_record_statuses (
              record_id TEXT PRIMARY KEY,
              status TEXT NOT NULL,
              updated_by_key TEXT NOT NULL,
              updated_by_name TEXT NOT NULL,
              updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS platform_record_priorities (
              record_id TEXT PRIMARY KEY,
              priority TEXT NOT NULL,
              updated_by_key TEXT NOT NULL,
              updated_by_name TEXT NOT NULL,
              updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS platform_record_hidden_tags (
              record_id TEXT NOT NULL,
              tag_label TEXT NOT NULL,
              hidden_by_key TEXT NOT NULL,
              hidden_by_name TEXT NOT NULL,
              hidden_at TEXT NOT NULL,
              PRIMARY KEY (record_id, tag_label)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS platform_record_custom_tags (
              record_id TEXT NOT NULL,
              tag_label TEXT NOT NULL,
              created_by_key TEXT NOT NULL,
              created_by_name TEXT NOT NULL,
              created_at TEXT NOT NULL,
              PRIMARY KEY (record_id, tag_label)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS platform_record_planner_notes (
              record_id TEXT PRIMARY KEY,
              author_key TEXT NOT NULL,
              author_name TEXT NOT NULL,
              body TEXT NOT NULL DEFAULT '',
              synced_body TEXT NOT NULL DEFAULT '',
              dirty_at TEXT NOT NULL,
              sync_after TEXT,
              synced_at TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS platform_deleted_records (
              record_id TEXT PRIMARY KEY,
              deleted_by_key TEXT NOT NULL,
              deleted_by_name TEXT NOT NULL,
              deleted_at TEXT NOT NULL
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
        seed_members = [("仝夏瀛", 1), ("李承轩", 0)]
        for name, is_admin in seed_members:
            conn.execute(
                """
                INSERT OR IGNORE INTO platform_team_members
                  (id, name, is_admin, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                ("tm_" + uuid.uuid5(uuid.NAMESPACE_DNS, f"x03:{name}").hex[:16], name, is_admin, now_iso(), now_iso()),
            )
        for alias, canonical in PLANNER_NAME_ALIASES.items():
            canonical_exists = conn.execute(
                "SELECT 1 FROM platform_team_members WHERE name = ?",
                (canonical,),
            ).fetchone()
            if not canonical_exists:
                conn.execute(
                    """
                    UPDATE platform_team_members
                    SET name = ?, updated_at = ?
                    WHERE name = ?
                    """,
                    (canonical, now_iso(), alias),
                )
        for name in DEFAULT_PLANNER_NAMES:
            conn.execute(
                """
                INSERT OR IGNORE INTO platform_team_members
                  (id, name, is_admin, is_planner, created_at, updated_at)
                VALUES (?, ?, 0, 1, ?, ?)
                """,
                ("tm_" + uuid.uuid5(uuid.NAMESPACE_DNS, f"x03:{name}").hex[:16], name, now_iso(), now_iso()),
            )
        if added_planner_column and DEFAULT_PLANNER_NAMES:
            conn.execute(
                f"""
                UPDATE platform_team_members
                SET is_planner = 1, updated_at = ?
                WHERE name IN ({",".join("?" for _ in DEFAULT_PLANNER_NAMES)})
                """,
                (now_iso(), *DEFAULT_PLANNER_NAMES),
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


def get_notification_payload(user=None):
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        where_clause, where_params = notification_visibility_clause(user)
        settings = conn.execute(
            "SELECT key, label, description, enabled FROM platform_notification_settings ORDER BY rowid ASC"
        ).fetchall()
        notifications = conn.execute(
            f"SELECT * FROM platform_notifications WHERE {where_clause} ORDER BY created_at DESC LIMIT 50",
            where_params,
        ).fetchall()
        unread_count = conn.execute(
            f"SELECT COUNT(*) FROM platform_notifications WHERE {where_clause} AND read = 0",
            where_params,
        ).fetchone()[0]
    items = [row_to_dict(row) for row in notifications]
    return {
        "settings": [row_to_dict(row) for row in settings],
        "notifications": items,
        "unread_count": unread_count,
    }


def update_notification_setting(key, enabled, user=None):
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
    return get_notification_payload(user)


def mark_notifications_read(user=None):
    with sqlite3.connect(DB_PATH) as conn:
        where_clause, where_params = notification_visibility_clause(user)
        conn.execute(f"UPDATE platform_notifications SET read = 1 WHERE {where_clause}", where_params)
    return get_notification_payload(user)


def mark_notification_read(notification_id, user=None):
    notification_id = (notification_id or "").strip()
    if not notification_id:
        raise ValueError("缺少通知")
    with sqlite3.connect(DB_PATH) as conn:
        where_clause, where_params = notification_visibility_clause(user)
        conn.execute(
            f"UPDATE platform_notifications SET read = 1 WHERE id = ? AND {where_clause}",
            [notification_id, *where_params],
        )
    return get_notification_payload(user)


def set_sync_state(**values):
    timestamp = now_iso()
    with sqlite3.connect(DB_PATH) as conn:
        for key, value in values.items():
            conn.execute(
                """
                INSERT INTO platform_sync_state (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                  value = excluded.value,
                  updated_at = excluded.updated_at
                """,
                (key, str(value), timestamp),
            )


def read_sync_state():
    config = feishu_sync_config()
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT key, value FROM platform_sync_state").fetchall()
        pending = conn.execute(
            """
            SELECT COUNT(*) FROM platform_feedback
            WHERE COALESCE(feishu_sync_status, 'pending') != 'synced'
            """
        ).fetchone()[0]
    state = {row["key"]: row["value"] for row in rows}
    state.update(
        {
            "enabled": config["enabled"],
            "writeback_enabled": config["writeback_enabled"],
            "configured": feishu_sync_ready(config),
            "interval_hours": config["interval_hours"],
            "pending_writeback_count": pending,
        }
    )
    return state


def run_git_command(args, cwd):
    proc = subprocess.run(
        ["git", *args],
        cwd=cwd,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=90,
    )
    if proc.returncode != 0:
        detail = (proc.stdout + "\n" + proc.stderr).strip()
        raise RuntimeError(detail or f"git {' '.join(args)} failed")
    return proc.stdout.strip()


def git_root_for_refresh_history():
    try:
        root = run_git_command(["rev-parse", "--show-toplevel"], ROOT)
    except Exception as exc:
        raise RuntimeError("刷新已停止：服务器目录还不是 Git 仓库，无法先保存 GitHub 历史记录。") from exc
    return Path(root)


def copy_history_file(target_dir, source_name):
    source = ROOT / source_name
    if source.exists() and source.is_file():
        shutil.copy2(source, target_dir / source_name)


def write_platform_feedback_dump(target_dir):
    if not DB_PATH.exists():
        return
    dump_path = target_dir / "platform_feedback.sql"
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    try:
        dump_path.write_text("\n".join(conn.iterdump()), encoding="utf-8")
    finally:
        conn.close()


def write_attachment_manifest(target_dir):
    attachment_root = ROOT / "attachments"
    files = []
    for path in iter_files(attachment_root):
        rel = path.relative_to(ROOT).as_posix()
        try:
            stat = path.stat()
            files.append({"path": rel, "size_bytes": stat.st_size, "updated_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat()})
        except OSError:
            files.append({"path": rel})
    (target_dir / "attachments_manifest.json").write_text(json.dumps(files, ensure_ascii=False, indent=2), encoding="utf-8")


def save_refresh_history_to_github(trigger):
    git_root = git_root_for_refresh_history()
    remote = os.getenv("REFRESH_HISTORY_GIT_REMOTE", "origin").strip() or "origin"
    branch = os.getenv("REFRESH_HISTORY_GIT_BRANCH", "").strip()
    if not branch:
        branch = run_git_command(["rev-parse", "--abbrev-ref", "HEAD"], git_root)
    if not branch or branch == "HEAD":
        raise RuntimeError("刷新已停止：当前 Git 分支无法确定，不能推送 GitHub 历史记录。")

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    history_dir = git_root / REFRESH_HISTORY_DIR / timestamp
    history_dir.mkdir(parents=True, exist_ok=False)
    metadata = {
        "created_at": now_iso(),
        "trigger": trigger,
        "server_root": str(ROOT),
        "db_path": str(DB_PATH),
    }
    (history_dir / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    for source_name in ("records_raw.json", "attachment_download_status.csv", "manifest.json", "base_records.csv", "base_attachments.csv"):
        copy_history_file(history_dir, source_name)
    write_platform_feedback_dump(history_dir)
    write_attachment_manifest(history_dir)

    rel_history_dir = history_dir.relative_to(git_root).as_posix()
    run_git_command(["add", rel_history_dir], git_root)
    commit_message = f"Backup feedback refresh history {timestamp} ({trigger})"
    run_git_command(["commit", "-m", commit_message], git_root)
    run_git_command(["push", remote, branch], git_root)
    notify_platform_event(
        "Refresh history pushed to GitHub",
        {"trigger": trigger, "history_dir": rel_history_dir, "remote": remote, "branch": branch},
    )
    return {"history_dir": rel_history_dir, "remote": remote, "branch": branch}


def record_list_page(config, offset, limit=200):
    args = [
        "base",
        "+record-list",
        "--base-token",
        config["base_token"],
        "--table-id",
        config["table_id"],
        "--offset",
        str(offset),
        "--limit",
        str(limit),
        "--format",
        "json",
        "--as",
        "user",
    ]
    if config.get("view_id"):
        args.extend(["--view-id", config["view_id"]])
    return run_lark_cli(args)


def iter_files(root):
    if not root.exists():
        return []
    return [path for path in root.rglob("*") if path.is_file()]


def attachment_file_index():
    files = iter_files(ROOT / "attachments")
    by_record_name = {}
    by_name = {}
    for path in files:
        basename = path.name
        original_tail = basename.split("_", 2)[-1]
        by_name.setdefault(basename, []).append(path)
        by_name.setdefault(original_tail, []).append(path)
        for part in path.parts:
            match = re.match(r"\d+_(rec[^\\/]+)$", part)
            if match:
                record_id = match.group(1)
                by_record_name.setdefault((record_id, basename), path)
                by_record_name.setdefault((record_id, original_tail), path)
                break
    return by_record_name, by_name


def find_cached_attachment(index, record_id, original_name):
    by_record_name, by_name = index
    if not original_name:
        return None
    if (record_id, original_name) in by_record_name:
        return by_record_name[(record_id, original_name)]
    for (cached_record_id, cached_name), path in by_record_name.items():
        if cached_record_id == record_id and cached_name.endswith(original_name):
            return path
    candidates = [path for path in by_name.get(original_name, []) if path.exists()]
    if len(candidates) == 1:
        return candidates[0]
    return None


def find_cached_attachment_by_position(index, record_id, file_index, original_name):
    by_record_name, _ = index
    if not original_name:
        return None
    marker = f"_{file_index:02d}_"
    for (cached_record_id, _), path in by_record_name.items():
        if cached_record_id == record_id and path.exists() and marker in path.name and path.name.endswith(original_name):
            return path
    return None


def download_base_attachment(file_token, output_path, record_id=""):
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_arg = output_path.relative_to(ROOT).as_posix()
    errors = []
    config = feishu_sync_config()
    if record_id:
        try:
            payload = run_lark_cli(
                [
                    "base",
                    "+record-download-attachment",
                    "--base-token",
                    config["base_token"],
                    "--table-id",
                    config["table_id"],
                    "--record-id",
                    record_id,
                    "--file-token",
                    file_token,
                    "--output",
                    output_arg,
                    "--overwrite",
                    "--format",
                    "json",
                    "--as",
                    "user",
                ],
                timeout=120,
            )
            if output_path.exists():
                return payload
            errors.append("base +record-download-attachment: command finished but output file was not found")
        except Exception as exc:
            errors.append(f"base +record-download-attachment: {exc}")
    for command_name in ("+media-download", "+media-preview"):
        for attempt in range(2):
            try:
                payload = run_lark_cli(
                    [
                        "docs",
                        command_name,
                        "--token",
                        file_token,
                        "--output",
                        output_arg,
                        "--as",
                        "user",
                    ],
                    timeout=120,
                )
                if output_path.exists():
                    return payload
                errors.append(f"{command_name}: command finished but output file was not found")
                break
            except Exception as exc:
                message = str(exc)
                errors.append(f"{command_name}: {message}")
                if attempt == 0 and "rate_limited" in message:
                    time.sleep(3)
                    continue
                break
    raise RuntimeError("\n".join(errors))


def attachment_output_path(row_index, record_id, field_name, file_index, file_info):
    output_dir = ROOT / "attachments" / f"{row_index:03d}_{safe_filename(record_id or 'record')}"
    original_name = file_info.get("name") or file_info["file_token"]
    token_hint = safe_filename(file_info["file_token"][:10])
    return output_dir / f"{safe_filename(field_name)}_{file_index:02d}_{token_hint}_{safe_filename(original_name)}"


def legacy_attachment_output_path(row_index, record_id, field_name, file_index, original_name):
    output_dir = ROOT / "attachments" / f"{row_index:03d}_{safe_filename(record_id or 'record')}"
    return output_dir / f"{safe_filename(field_name)}_{file_index:02d}_{safe_filename(original_name)}"


def attachment_name_counts(rows, record_ids, field_indexes):
    counts = {}
    for row_index, row in enumerate(rows, start=1):
        record_id = record_ids[row_index - 1] if row_index - 1 < len(record_ids) else ""
        for field_id, field_name in FEISHU_ATTACHMENT_FIELDS:
            column_index = field_indexes.get(field_id)
            if column_index is None or column_index >= len(row):
                continue
            files = row[column_index] or []
            if not isinstance(files, list):
                continue
            for file_info in files:
                if not isinstance(file_info, dict) or not file_info.get("file_token"):
                    continue
                original_name = file_info.get("name") or file_info["file_token"]
                key = (record_id, field_name, original_name)
                counts[key] = counts.get(key, 0) + 1
    return counts


def rebuild_attachment_status(snapshot_data, download_missing=True):
    field_ids = snapshot_data.get("field_id_list") or []
    rows = snapshot_data.get("data") or []
    record_ids = snapshot_data.get("record_id_list") or []
    field_indexes = {
        field_id: field_ids.index(field_id)
        for field_id, _ in FEISHU_ATTACHMENT_FIELDS
        if field_id in field_ids
    }
    cached_index = attachment_file_index()
    name_counts = attachment_name_counts(rows, record_ids, field_indexes)
    status_rows = []
    downloaded = 0
    reused = 0
    missing = 0

    for row_index, row in enumerate(rows, start=1):
        record_id = record_ids[row_index - 1] if row_index - 1 < len(record_ids) else ""
        for field_id, field_name in FEISHU_ATTACHMENT_FIELDS:
            column_index = field_indexes.get(field_id)
            if column_index is None or column_index >= len(row):
                continue
            files = row[column_index] or []
            if not isinstance(files, list):
                continue
            for file_index, file_info in enumerate(files, start=1):
                if not isinstance(file_info, dict) or not file_info.get("file_token"):
                    continue
                original_name = file_info.get("name") or file_info["file_token"]
                output_path = attachment_output_path(row_index, record_id, field_name, file_index, file_info)
                duplicate_name = name_counts.get((record_id, field_name, original_name), 0) > 1
                legacy_path = legacy_attachment_output_path(row_index, record_id, field_name, file_index, original_name)
                if legacy_path.exists():
                    cached_path = legacy_path
                elif duplicate_name:
                    cached_path = find_cached_attachment_by_position(cached_index, record_id, file_index, original_name)
                else:
                    cached_path = find_cached_attachment(cached_index, record_id, original_name)
                if cached_path and cached_path.exists() and not output_path.exists():
                    output_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(cached_path, output_path)
                status = "downloaded" if output_path.exists() else "missing"
                error = ""
                if cached_path and cached_path.exists():
                    reused += 1
                elif download_missing:
                    try:
                        download_base_attachment(file_info["file_token"], output_path, record_id)
                        status = "downloaded" if output_path.exists() else "missing"
                        if status == "downloaded":
                            downloaded += 1
                        else:
                            error = "download command finished but output file was not found"
                    except Exception as exc:
                        status = "failed"
                        error = str(exc)
                if status != "downloaded":
                    missing += 1
                status_rows.append(
                    {
                        "record_id": record_id,
                        "row_no": row_index,
                        "field_name": field_name,
                        "file_token": file_info["file_token"],
                        "original_name": original_name,
                        "local_path": str(output_path) if output_path.exists() else "",
                        "download_status": status,
                        "download_error": error,
                    }
                )

    status_path = ROOT / "attachment_download_status.csv"
    with status_path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[
                "record_id",
                "row_no",
                "field_name",
                "file_token",
                "original_name",
                "local_path",
                "download_status",
                "download_error",
            ],
        )
        writer.writeheader()
        writer.writerows(status_rows)
    return {
        "total": len(status_rows),
        "reused": reused,
        "downloaded": downloaded,
        "missing": missing,
        "status_csv": str(status_path),
    }


def sync_base_snapshot(trigger="manual"):
    config = feishu_sync_config()
    if not feishu_sync_ready(config):
        message = "飞书多维表同步尚未配置完整"
        notify_platform_event("Base snapshot sync skipped", {"trigger": trigger, "reason": message}, level="warning")
        raise RuntimeError(message)
    if not SYNC_LOCK.acquire(blocking=False):
        message = "同步正在进行中，请稍后再试"
        notify_platform_event("Base snapshot sync skipped", {"trigger": trigger, "reason": message}, level="warning")
        raise RuntimeError(message)
    try:
        history_result = save_refresh_history_to_github(trigger)
        set_sync_state(
            last_snapshot_status="running",
            last_snapshot_message=f"{trigger} sync started after GitHub history backup",
            last_snapshot_started_at=now_iso(),
            last_refresh_history=history_result.get("history_dir"),
        )
        notify_platform_event("Base snapshot sync started", {"trigger": trigger, "history_dir": history_result.get("history_dir")})
        limit = 200
        offset = 0
        first_payload = None
        all_rows = []
        all_record_ids = []
        field_ids = None

        while True:
            payload = record_list_page(config, offset, limit)
            if first_payload is None:
                first_payload = payload
            page_data = (payload or {}).get("data") or {}
            rows = page_data.get("data") or []
            record_ids = page_data.get("record_id_list") or []
            if field_ids is None:
                field_ids = page_data.get("field_id_list") or []
            all_rows.extend(rows)
            all_record_ids.extend(record_ids)
            has_more = find_bool(payload, "has_more")
            if not rows or has_more is False:
                break
            offset += len(rows)
            if has_more is None and len(rows) < limit:
                break

        snapshot = first_payload or {"ok": True, "identity": "user", "data": {}}
        if isinstance(snapshot, dict):
            snapshot.pop("_notice", None)
        snapshot.setdefault("data", {})
        snapshot["data"]["data"] = all_rows
        snapshot["data"]["field_id_list"] = field_ids or []
        snapshot["data"]["record_id_list"] = all_record_ids
        tmp_path = ROOT / "records_raw.json.tmp"
        final_path = ROOT / "records_raw.json"
        tmp_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_path.replace(final_path)
        attachment_summary = rebuild_attachment_status(snapshot["data"], download_missing=True)

        manifest_path = ROOT / "manifest.json"
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                manifest = {}
        else:
            manifest = {}
        manifest.update(
            {
                "base_token": config["base_token"],
                "table_id": config["table_id"],
                "view_id": config["view_id"],
                "record_count": len(all_rows),
                "download_summary": attachment_summary,
                "last_snapshot_sync_at": now_iso(),
            }
        )
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

        success_message = f"已同步 {len(all_rows)} 条多维表记录，附件 {attachment_summary['total']} 个"
        set_sync_state(
            last_snapshot_status="success",
            last_snapshot_message=success_message,
            last_snapshot_at=now_iso(),
            last_snapshot_count=len(all_rows),
            last_attachment_count=attachment_summary["total"],
            last_attachment_missing=attachment_summary["missing"],
        )
        notify_platform_event(
            "Base snapshot sync succeeded",
            {
                "trigger": trigger,
                "records": len(all_rows),
                "attachments": attachment_summary["total"],
                "missing_attachments": attachment_summary["missing"],
            },
        )
        return {"ok": True, "count": len(all_rows), "message": success_message}
    except Exception as exc:
        set_sync_state(
            last_snapshot_status="failed",
            last_snapshot_message=str(exc),
            last_snapshot_at=now_iso(),
        )
        notify_platform_error("Base snapshot sync failed", exc, {"trigger": trigger})
        raise
    finally:
        SYNC_LOCK.release()


def find_created_record_id(payload):
    if isinstance(payload, dict):
        for key in ("record_id", "id", "recordId"):
            value = payload.get(key)
            if isinstance(value, str) and value.startswith("rec"):
                return value
        for value in payload.values():
            found = find_created_record_id(value)
            if found:
                return found
    elif isinstance(payload, list):
        for value in payload:
            found = find_created_record_id(value)
            if found:
                return found
    return ""


def locate_feishu_record(feedback):
    config = feishu_sync_config()
    offset = 0
    limit = 200
    feedback_id = feedback.get("id") or ""
    description = feedback.get("description") or ""
    while True:
        payload = record_list_page(config, offset, limit)
        page_data = (payload or {}).get("data") or {}
        rows = page_data.get("data") or []
        record_ids = page_data.get("record_id_list") or []
        field_ids = page_data.get("field_id_list") or []
        try:
            description_index = field_ids.index(FEISHU_FIELD_DESCRIPTION)
        except ValueError:
            description_index = -1
        try:
            remark_index = field_ids.index(FEISHU_FIELD_REMARK)
        except ValueError:
            remark_index = -1

        for record_id, row in zip(record_ids, rows):
            row_description = row[description_index] if description_index >= 0 and description_index < len(row) else ""
            row_remark = row[remark_index] if remark_index >= 0 and remark_index < len(row) else ""
            if feedback_id and feedback_id in str(row_remark or ""):
                return record_id
            if description and row_description == description:
                return record_id

        has_more = find_bool(payload, "has_more")
        if not rows or has_more is False:
            break
        offset += len(rows)
        if has_more is None and len(rows) < limit:
            break
    return ""


def sync_feedback_to_feishu(feedback):
    config = feishu_sync_config()
    if not config["writeback_enabled"]:
        return {"status": "skipped", "record_id": "", "error": "writeback disabled"}
    if not feishu_sync_ready(config):
        return {"status": "skipped", "record_id": "", "error": "sync not configured"}

    remark = "\n".join(
        [
            "来源：平台提交",
            f"类型：{feedback.get('feedback_type') or ''}",
            f"提交人：{feedback.get('proposer') or ''}",
            f"平台记录ID：{feedback.get('id') or ''}",
        ]
    )
    payload = {
        FEISHU_FIELD_DESCRIPTION: feedback.get("description") or "",
        FEISHU_FIELD_REMARK: remark,
    }
    args = [
        "base",
        "+record-upsert",
        "--base-token",
        config["base_token"],
        "--table-id",
        config["table_id"],
        "--json",
        json.dumps(payload, ensure_ascii=True),
        "--as",
        "user",
    ]
    result = run_lark_cli(args, timeout=90)
    record_id = find_created_record_id(result) or locate_feishu_record(feedback)
    if not record_id:
        raise RuntimeError("飞书写回命令执行完成，但没有返回可定位的记录 ID")
    return {"status": "synced", "record_id": record_id, "raw": result}


def mark_feedback_sync(feedback_id, status, record_id="", error=""):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            UPDATE platform_feedback
            SET feishu_record_id = COALESCE(NULLIF(?, ''), feishu_record_id),
                feishu_sync_status = ?,
                feishu_sync_error = ?,
                feishu_synced_at = ?
            WHERE id = ?
            """,
            (record_id or "", status, error or "", now_iso(), feedback_id),
        )


def list_writeback_logs(user):
    require_admin(user)
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT id, description, proposer, created_at, feishu_record_id,
                   COALESCE(feishu_sync_status, 'pending') AS feishu_sync_status,
                   COALESCE(feishu_sync_error, '') AS feishu_sync_error,
                   feishu_synced_at
            FROM platform_feedback
            ORDER BY created_at DESC
            LIMIT 100
            """
        ).fetchall()
    return [row_to_dict(row) for row in rows]


def retry_writebacks(user, feedback_ids=None, failed_only=False):
    require_admin(user)
    feedback_ids = [str(item).strip() for item in (feedback_ids or []) if str(item).strip()]
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        params = []
        where = ["COALESCE(feishu_sync_status, 'pending') != 'synced'"]
        if failed_only:
            where.append("COALESCE(feishu_sync_status, 'pending') = 'failed'")
        if feedback_ids:
            placeholders = ",".join("?" for _ in feedback_ids)
            where.append(f"id IN ({placeholders})")
            params.extend(feedback_ids)
        rows = conn.execute(
            f"""
            SELECT * FROM platform_feedback
            WHERE {" AND ".join(where)}
            ORDER BY created_at ASC
            """,
            params,
        ).fetchall()

    summary = {"total": len(rows), "synced": 0, "failed": 0, "errors": []}
    notify_platform_event(
        "Feedback writeback sync started",
        {
            "total": summary["total"],
            "failed_only": failed_only,
            "selected_feedback_ids": ", ".join(feedback_ids),
        },
    )
    for row in rows:
        item = row_to_dict(row)
        try:
            result = sync_feedback_to_feishu(item)
            mark_feedback_sync(
                item["id"],
                result.get("status") or "synced",
                result.get("record_id") or "",
                result.get("error") or "",
            )
            if result.get("status") == "synced":
                summary["synced"] += 1
            else:
                summary["failed"] += 1
                summary["errors"].append({"id": item["id"], "error": result.get("error") or "未同步"})
        except Exception as exc:
            mark_feedback_sync(item["id"], "failed", "", str(exc))
            summary["failed"] += 1
            summary["errors"].append({"id": item["id"], "error": str(exc)})
            notify_platform_error("Feedback writeback item failed", exc, {"feedback_id": item["id"]})
    notify_platform_event(
        "Feedback writeback sync finished",
        {
            "total": summary["total"],
            "synced": summary["synced"],
            "failed": summary["failed"],
            "errors": json.dumps(summary["errors"][:5], ensure_ascii=False),
        },
        level="error" if summary["failed"] else "info",
    )
    return summary


def retry_pending_writebacks(user):
    return retry_writebacks(user)


def interaction_user(user):
    if not user:
        return "guest", "平台用户"
    return user.get("open_id") or user.get("union_id") or user.get("name") or "guest", user.get("name") or "飞书用户"


def admin_values():
    raw = os.getenv("PLATFORM_ADMIN_USERS", "")
    return {item.strip().lower() for item in raw.split(",") if item.strip()}


def candidate_user_values(user):
    if not user:
        return set()
    return {
        str(user.get("open_id") or "").lower(),
        str(user.get("union_id") or "").lower(),
        str(user.get("email") or "").lower(),
        str(user.get("name") or "").lower(),
    } - {""}


def notification_user_values(user):
    values = set(candidate_user_values(user))
    user_key, user_name = interaction_user(user)
    values.update({str(user_key or "").lower(), str(user_name or "").lower()})
    values.discard("")
    if not values:
        return values
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, name, open_id, union_id, email FROM platform_team_members"
        ).fetchall()
    for row in rows:
        row_values = {
            str(row[key] or "").lower()
            for key in ("id", "name", "open_id", "union_id", "email")
        } - {""}
        if values & row_values:
            values.update(row_values)
    return values


def notification_visibility_clause(user):
    values = sorted(notification_user_values(user))
    clauses = ["receiver_key IS NULL", "receiver_key = ''"]
    params = []
    if values:
        placeholders = ",".join("?" for _ in values)
        clauses.append(f"LOWER(receiver_key) IN ({placeholders})")
        params.extend(values)
        clauses.append(f"LOWER(receiver) IN ({placeholders})")
        params.extend(values)
    return "(" + " OR ".join(clauses) + ")", params


def normalize_planner_name(value):
    text = (value or "").strip()
    return PLANNER_NAME_ALIASES.get(text, text)


def sync_team_member_from_user(user):
    if not user or not user.get("name"):
        return
    now = now_iso()
    member_id = "tm_" + uuid.uuid5(uuid.NAMESPACE_DNS, f"x03:{user.get('open_id') or user.get('union_id') or user.get('email') or user.get('name')}").hex[:16]
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO platform_team_members
              (id, name, open_id, union_id, email, is_admin, created_at, updated_at)
            VALUES (?, ?, NULLIF(?, ''), NULLIF(?, ''), NULLIF(?, ''), 0, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
              open_id = COALESCE(excluded.open_id, platform_team_members.open_id),
              union_id = COALESCE(excluded.union_id, platform_team_members.union_id),
              email = COALESCE(excluded.email, platform_team_members.email),
              updated_at = excluded.updated_at
            """,
            (
                member_id,
                user.get("name"),
                user.get("open_id") or "",
                user.get("union_id") or "",
                user.get("email") or "",
                now,
                now,
            ),
        )


def sync_team_members_from_auth_sessions():
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT user_json FROM platform_auth_sessions WHERE user_json IS NOT NULL"
        ).fetchall()
    for row in rows:
        try:
            sync_team_member_from_user(json.loads(row["user_json"]))
        except (TypeError, ValueError):
            continue


def db_admin_values():
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT name, open_id, union_id, email
            FROM platform_team_members
            WHERE is_admin = 1
            """
        ).fetchall()
    values = set()
    for row in rows:
        values.update(str(row[key] or "").lower() for key in row.keys())
    return values - {""}


def db_planner_values():
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT name, open_id, union_id, email
            FROM platform_team_members
            WHERE is_planner = 1
            """
        ).fetchall()
    values = set()
    for row in rows:
        values.update(str(row[key] or "").lower() for key in row.keys())
    return values - {""}


def is_admin_user(user):
    if not user:
        return False
    values = admin_values() | db_admin_values()
    if not values:
        return False
    return bool(values & candidate_user_values(user))


def is_planner_user(user):
    if not user:
        return False
    return bool(db_planner_values() & candidate_user_values(user))


def require_admin(user):
    if not is_admin_user(user):
        raise PermissionError("只有平台管理员可以操作平台设置")


def list_team_members():
    sync_team_members_from_auth_sessions()
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT id, name, open_id, union_id, email, is_admin, is_planner, updated_at
            FROM platform_team_members
            ORDER BY rowid ASC
            """
        ).fetchall()
    return [row_to_dict(row) for row in rows]


def list_planner_options():
    sync_team_members_from_auth_sessions()
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT id, name
            FROM platform_team_members
            WHERE is_planner = 1
            ORDER BY name ASC
            """
        ).fetchall()
    return [row_to_dict(row) for row in rows]


def set_team_member_admin(member_id, enabled, user):
    require_admin(user)
    member_id = (member_id or "").strip()
    if not member_id:
        raise ValueError("缺少成员")
    enabled_value = 1 if enabled else 0
    with sqlite3.connect(DB_PATH) as conn:
        if enabled_value == 0:
            current = conn.execute(
                "SELECT is_admin FROM platform_team_members WHERE id = ?",
                (member_id,),
            ).fetchone()
            if not current:
                raise ValueError("成员不存在")
            admin_count = conn.execute(
                "SELECT COUNT(*) FROM platform_team_members WHERE is_admin = 1"
            ).fetchone()[0]
            if current[0] and admin_count <= 1:
                raise ValueError("至少需要保留一名平台管理员")
        updated = conn.execute(
            """
            UPDATE platform_team_members
            SET is_admin = ?, updated_at = ?
            WHERE id = ?
            """,
            (enabled_value, now_iso(), member_id),
        ).rowcount
    if not updated:
        raise ValueError("成员不存在")
    return list_team_members()


def set_team_member_planner(member_id, enabled, user):
    require_admin(user)
    member_id = (member_id or "").strip()
    if not member_id:
        raise ValueError("缺少成员")
    enabled_value = 1 if enabled else 0
    with sqlite3.connect(DB_PATH) as conn:
        updated = conn.execute(
            """
            UPDATE platform_team_members
            SET is_planner = ?, updated_at = ?
            WHERE id = ?
            """,
            (enabled_value, now_iso(), member_id),
        ).rowcount
    if not updated:
        raise ValueError("成员不存在")
    return list_team_members()


def delete_record_tag(record_id, tag_label, user):
    if not (is_admin_user(user) or is_planner_user(user)):
        raise PermissionError("只有策划或管理员可以删除标签")
    record_id = (record_id or "").strip()
    tag_label = (tag_label or "").strip()
    if not record_id or not tag_label:
        raise ValueError("缺少标签信息")
    user_key, user_name = interaction_user(user)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "DELETE FROM platform_record_custom_tags WHERE record_id = ? AND tag_label = ?",
            (record_id, tag_label),
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO platform_record_hidden_tags
              (record_id, tag_label, hidden_by_key, hidden_by_name, hidden_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (record_id, tag_label, user_key, user_name, now_iso()),
        )
    return get_record_interactions(user).get(record_id, {})


def add_record_tag(record_id, tag_label, user):
    if not (is_admin_user(user) or is_planner_user(user)):
        raise PermissionError("只有策划或管理员可以添加标签")
    record_id = (record_id or "").strip()
    tag_label = (tag_label or "").strip()
    if not record_id or not tag_label:
        raise ValueError("缺少标签信息")
    if len(tag_label) > 20:
        raise ValueError("标签最多 20 个字符")
    user_key, user_name = interaction_user(user)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO platform_record_custom_tags
              (record_id, tag_label, created_by_key, created_by_name, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (record_id, tag_label, user_key, user_name, now_iso()),
        )
        conn.execute(
            "DELETE FROM platform_record_hidden_tags WHERE record_id = ? AND tag_label = ?",
            (record_id, tag_label),
        )
    return get_record_interactions(user).get(record_id, {})


def public_user(user):
    if not user:
        return None
    item = dict(user)
    item["is_admin"] = is_admin_user(user)
    item["is_planner"] = is_planner_user(user)
    return item


def get_record_interactions(user=None):
    user_key, _ = interaction_user(user)
    def default_interaction():
        return {
            "like_count": 0,
            "liked_by_me": False,
            "liked_users": [],
            "follow_count": 0,
            "followed_by_me": False,
            "comments": [],
            "assigned_planner": "",
            "assigned_planner_key": "",
            "assigned_status": "",
            "assigned_priority": "",
            "last_updated_at": "",
            "planner_note": "",
            "planner_note_author_key": "",
            "planner_note_author_name": "",
            "planner_note_dirty_at": "",
            "planner_note_sync_after": "",
            "planner_note_synced_at": "",
            "hidden_auto_tags": [],
            "custom_auto_tags": [],
        }

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
        liked_users = conn.execute(
            """
            SELECT record_id, user_key, user_name, created_at
            FROM platform_record_likes
            ORDER BY created_at ASC
            """
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
        assignments = conn.execute(
            """
            SELECT record_id, planner_name, planner_key, updated_by_name, updated_at
            FROM platform_record_assignments
            """
        ).fetchall()
        statuses = conn.execute(
            """
            SELECT record_id, status, updated_by_name, updated_at
            FROM platform_record_statuses
            """
        ).fetchall()
        priorities = conn.execute(
            """
            SELECT record_id, priority, updated_by_name, updated_at
            FROM platform_record_priorities
            """
        ).fetchall()
        planner_notes = conn.execute(
            """
            SELECT record_id, author_key, author_name, body, dirty_at, sync_after, synced_at
            FROM platform_record_planner_notes
            """
        ).fetchall()
        hidden_tags = conn.execute(
            """
            SELECT record_id, tag_label, hidden_at
            FROM platform_record_hidden_tags
            ORDER BY hidden_at ASC
            """
        ).fetchall()
        custom_tags = conn.execute(
            """
            SELECT record_id, tag_label, created_at
            FROM platform_record_custom_tags
            ORDER BY created_at ASC
            """
        ).fetchall()

    data = {}
    def remember_update(record_id, updated_at):
        if not record_id or not updated_at:
            return
        data.setdefault(record_id, default_interaction())
        if updated_at > (data[record_id].get("last_updated_at") or ""):
            data[record_id]["last_updated_at"] = updated_at

    for row in likes:
        item = row_to_dict(row)
        data.setdefault(item["record_id"], default_interaction())
        data[item["record_id"]]["like_count"] = item["like_count"]
        data[item["record_id"]]["liked_by_me"] = bool(item["liked_by_me"])

    for row in liked_users:
        item = row_to_dict(row)
        data.setdefault(item["record_id"], default_interaction())
        data[item["record_id"]]["liked_users"].append(item)

    for row in follows:
        item = row_to_dict(row)
        data.setdefault(item["record_id"], default_interaction())
        data[item["record_id"]]["follow_count"] = item["follow_count"]
        data[item["record_id"]]["followed_by_me"] = bool(item["followed_by_me"])

    for row in comments:
        item = row_to_dict(row)
        data.setdefault(item["record_id"], default_interaction())
        data[item["record_id"]]["comments"].append(item)
        remember_update(item.get("record_id"), item.get("created_at"))

    for row in assignments:
        item = row_to_dict(row)
        data.setdefault(item["record_id"], default_interaction())
        data[item["record_id"]]["assigned_planner"] = item.get("planner_name") or ""
        data[item["record_id"]]["assigned_planner_key"] = item.get("planner_key") or ""
        remember_update(item.get("record_id"), item.get("updated_at"))
    for row in statuses:
        item = row_to_dict(row)
        data.setdefault(item["record_id"], default_interaction())
        data[item["record_id"]]["assigned_status"] = item.get("status") or ""
        remember_update(item.get("record_id"), item.get("updated_at"))
    for row in priorities:
        item = row_to_dict(row)
        data.setdefault(item["record_id"], default_interaction())
        data[item["record_id"]]["assigned_priority"] = item.get("priority") or ""
        remember_update(item.get("record_id"), item.get("updated_at"))
    for row in planner_notes:
        item = row_to_dict(row)
        data.setdefault(item["record_id"], default_interaction())
        data[item["record_id"]]["planner_note"] = item.get("body") or ""
        data[item["record_id"]]["planner_note_author_key"] = item.get("author_key") or ""
        data[item["record_id"]]["planner_note_author_name"] = item.get("author_name") or ""
        data[item["record_id"]]["planner_note_dirty_at"] = item.get("dirty_at") or ""
        data[item["record_id"]]["planner_note_sync_after"] = item.get("sync_after") or ""
        data[item["record_id"]]["planner_note_synced_at"] = item.get("synced_at") or ""
        remember_update(item.get("record_id"), item.get("dirty_at") or item.get("synced_at"))
    for row in hidden_tags:
        item = row_to_dict(row)
        data.setdefault(item["record_id"], default_interaction())
        data[item["record_id"]]["hidden_auto_tags"].append(item.get("tag_label") or "")
        remember_update(item.get("record_id"), item.get("hidden_at"))
    for row in custom_tags:
        item = row_to_dict(row)
        data.setdefault(item["record_id"], default_interaction())
        data[item["record_id"]]["custom_auto_tags"].append(item.get("tag_label") or "")
        remember_update(item.get("record_id"), item.get("created_at"))
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


def delete_record_comment(comment_id, user):
    comment_id = (comment_id or "").strip()
    if not comment_id:
        raise ValueError("缺少评论")
    if not user:
        raise PermissionError("请先登录后再删除评论")
    user_key, _ = interaction_user(user)
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM platform_record_comments WHERE id = ?",
            (comment_id,),
        ).fetchone()
        if not row:
            raise ValueError("评论不存在或已删除")
        comment = row_to_dict(row)
        can_delete = is_admin_user(user) or comment["author_key"] in (candidate_user_values(user) | {user_key})
        if not can_delete:
            raise PermissionError("只能删除自己发表的评论")
        conn.execute("DELETE FROM platform_record_comments WHERE id = ?", (comment_id,))
    return get_record_interactions(user).get(comment["record_id"], {})


def save_planner_note(record_id, body, user):
    record_id = (record_id or "").strip()
    body = (body or "").strip()
    if not record_id:
        raise ValueError("缺少反馈记录")
    if len(body) > 1000:
        raise ValueError("备注最多 1000 个字符")
    user_key, user_name = interaction_user(user)
    if not body:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("DELETE FROM platform_record_planner_notes WHERE record_id = ?", (record_id,))
        return get_record_interactions(user).get(record_id, {})

    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        existing = conn.execute(
            "SELECT synced_body FROM platform_record_planner_notes WHERE record_id = ?",
            (record_id,),
        ).fetchone()
        sync_after = "" if existing and (existing["synced_body"] or "") == body else iso_after(PLANNER_NOTE_SYNC_DELAY_SECONDS)
        conn.execute(
            """
            INSERT INTO platform_record_planner_notes
              (record_id, author_key, author_name, body, synced_body, dirty_at, sync_after, synced_at)
            VALUES (?, ?, ?, ?, '', ?, ?, '')
            ON CONFLICT(record_id) DO UPDATE SET
              author_key = excluded.author_key,
              author_name = excluded.author_name,
              body = excluded.body,
              dirty_at = excluded.dirty_at,
              sync_after = excluded.sync_after
            """,
            (record_id, user_key, user_name, body, now_iso(), sync_after or None),
        )
    return get_record_interactions(user).get(record_id, {})


def sync_due_planner_notes():
    now = now_iso()
    synced = 0
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        due_notes = conn.execute(
            """
            SELECT *
            FROM platform_record_planner_notes
            WHERE body != ''
              AND sync_after IS NOT NULL
              AND sync_after <= ?
              AND body != COALESCE(synced_body, '')
            """,
            (now,),
        ).fetchall()
        for row in due_notes:
            item = row_to_dict(row)
            comment_id = "cmt_" + uuid.uuid4().hex[:16]
            created_at = now_iso()
            conn.execute(
                """
                INSERT INTO platform_record_comments
                  (id, record_id, author_key, author_name, body, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    comment_id,
                    item["record_id"],
                    item["author_key"],
                    item["author_name"],
                    item["body"],
                    created_at,
                ),
            )
            conn.execute(
                """
                UPDATE platform_record_planner_notes
                SET synced_body = body,
                    sync_after = NULL,
                    synced_at = ?
                WHERE record_id = ?
                """,
                (created_at, item["record_id"]),
            )
            synced += 1
    if synced:
        notify_platform_event("Planner note sync finished", {"synced_comments": synced})
    return synced


def notification_setting_enabled(conn, key):
    row = conn.execute(
        "SELECT enabled FROM platform_notification_settings WHERE key = ?",
        (key,),
    ).fetchone()
    return True if row is None else bool(row[0])


def create_assignment_notification(conn, record_id, planner, updated_by_name):
    if not notification_setting_enabled(conn, "assignments"):
        return
    notification_id = "ntf_" + uuid.uuid4().hex[:16]
    created_at = now_iso()
    conn.execute(
        """
        INSERT INTO platform_notifications
          (id, type, title, body, receiver, receiver_key, record_id, read, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)
        """,
        (
            notification_id,
            "分配",
            "你收到一条新的反馈分配",
            f"{updated_by_name or '平台用户'} 将反馈 {record_id} 分配给你，点击可查看详情。",
            planner["name"],
            planner["id"],
            record_id,
            created_at,
        ),
    )


def assign_record_planner(record_id, planner_name, user):
    record_id = (record_id or "").strip()
    planner_name = (planner_name or "").strip()
    if not record_id:
        raise ValueError("缺少反馈记录")
    options = list_planner_options()
    by_name = {item["name"]: item for item in options}
    if planner_name and planner_name not in by_name:
        raise ValueError("请选择团队管理中的成员")

    user_key, user_name = interaction_user(user)
    with sqlite3.connect(DB_PATH) as conn:
        previous = conn.execute(
            "SELECT planner_name, planner_key FROM platform_record_assignments WHERE record_id = ?",
            (record_id,),
        ).fetchone()
        if not planner_name:
            conn.execute("DELETE FROM platform_record_assignments WHERE record_id = ?", (record_id,))
        else:
            planner = by_name[planner_name]
            should_notify = not previous or previous[0] != planner["name"] or previous[1] != planner["id"]
            conn.execute(
                """
                INSERT INTO platform_record_assignments
                  (record_id, planner_name, planner_key, updated_by_key, updated_by_name, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(record_id) DO UPDATE SET
                  planner_name = excluded.planner_name,
                  planner_key = excluded.planner_key,
                  updated_by_key = excluded.updated_by_key,
                  updated_by_name = excluded.updated_by_name,
                  updated_at = excluded.updated_at
                """,
                (record_id, planner["name"], planner["id"], user_key, user_name, now_iso()),
            )
            if should_notify:
                create_assignment_notification(conn, record_id, planner, user_name)
    return get_record_interactions(user).get(record_id, {
        "like_count": 0,
        "liked_by_me": False,
        "liked_users": [],
        "follow_count": 0,
        "followed_by_me": False,
        "comments": [],
        "assigned_planner": "",
        "assigned_planner_key": "",
        "assigned_status": "",
        "assigned_priority": "",
    })


def assign_record_status(record_id, status, user):
    record_id = (record_id or "").strip()
    status = (status or "").strip()
    if not record_id:
        raise ValueError("缺少反馈记录")
    if not status:
        raise ValueError("请选择处理状态")

    user_key, user_name = interaction_user(user)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO platform_record_statuses
              (record_id, status, updated_by_key, updated_by_name, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(record_id) DO UPDATE SET
              status = excluded.status,
              updated_by_key = excluded.updated_by_key,
              updated_by_name = excluded.updated_by_name,
              updated_at = excluded.updated_at
            """,
            (record_id, status, user_key, user_name, now_iso()),
        )
    return get_record_interactions(user).get(record_id, {
        "like_count": 0,
        "liked_by_me": False,
        "liked_users": [],
        "follow_count": 0,
        "followed_by_me": False,
        "comments": [],
        "assigned_planner": "",
        "assigned_planner_key": "",
        "assigned_status": status,
        "assigned_priority": "",
    })


def assign_record_priority(record_id, priority, user):
    record_id = (record_id or "").strip()
    priority = (priority or "").strip()
    if not record_id:
        raise ValueError("缺少反馈记录")
    if not priority:
        raise ValueError("请选择优先级")

    user_key, user_name = interaction_user(user)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO platform_record_priorities
              (record_id, priority, updated_by_key, updated_by_name, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(record_id) DO UPDATE SET
              priority = excluded.priority,
              updated_by_key = excluded.updated_by_key,
              updated_by_name = excluded.updated_by_name,
              updated_at = excluded.updated_at
            """,
            (record_id, priority, user_key, user_name, now_iso()),
        )
    return get_record_interactions(user).get(record_id, {
        "like_count": 0,
        "liked_by_me": False,
        "liked_users": [],
        "follow_count": 0,
        "followed_by_me": False,
        "comments": [],
        "assigned_planner": "",
        "assigned_planner_key": "",
        "assigned_status": "",
        "assigned_priority": priority,
    })


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


def entry_path():
    return os.getenv("ENTRY_PATH", DEFAULT_ENTRY_PATH)


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
    sync_team_member_from_user(user)
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
    notify_feedback_saved(item)
    try:
        sync_result = sync_feedback_to_feishu(item)
        mark_feedback_sync(
            feedback_id,
            sync_result.get("status") or "synced",
            sync_result.get("record_id") or "",
            sync_result.get("error") or "",
        )
        if sync_result.get("status") != "synced":
            notify_platform_event(
                "Feedback writeback did not sync",
                {
                    "feedback_id": feedback_id,
                    "status": sync_result.get("status"),
                    "error": sync_result.get("error"),
                },
                level="warning",
            )
    except Exception as exc:
        mark_feedback_sync(feedback_id, "failed", "", str(exc))
        notify_platform_error("Feedback writeback failed after local submit", exc, {"feedback_id": feedback_id})
    item = get_feedback(feedback_id)
    item["attachments"] = saved_files
    return item


def remove_feedback_attachment_file(attachment):
    path_value = attachment.get("path") if isinstance(attachment, dict) else attachment["path"]
    if not path_value:
        return
    file_path = (ROOT / path_value).resolve()
    upload_root = UPLOAD_ROOT.resolve()
    if str(file_path).startswith(str(upload_root)) and file_path.exists() and file_path.is_file():
        file_path.unlink(missing_ok=True)


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


def update_feedback(feedback_id, fields, files, user):
    feedback_id = (feedback_id or fields.get("record_id") or "").strip()
    if not feedback_id:
        raise ValueError("缺少反馈记录")
    item = get_feedback(feedback_id)
    if not item:
        raise FileNotFoundError("记录不存在")
    if not can_delete_feedback(item, user):
        raise PermissionError("你没有权限编辑这条记录")

    description = (fields.get("description") or "").strip()
    if len(description) < 10:
        raise ValueError("请至少输入 10 个字符")

    keep_raw = (fields.get("keep_attachment_ids") or "").strip()
    if keep_raw:
        try:
            keep_ids = set(json.loads(keep_raw))
        except ValueError:
            keep_ids = {part.strip() for part in keep_raw.split(",") if part.strip()}
    else:
        keep_ids = {attachment["id"] for attachment in item.get("attachments", [])}

    existing_attachments = item.get("attachments", [])
    valid_keep_ids = {attachment["id"] for attachment in existing_attachments if attachment["id"] in keep_ids}
    if len(valid_keep_ids) + len(files) > 9:
        raise ValueError("附件最多保留 9 个")

    now = now_iso()
    upload_dir = UPLOAD_ROOT / feedback_id
    upload_dir.mkdir(parents=True, exist_ok=True)
    removed_attachments = [attachment for attachment in existing_attachments if attachment["id"] not in valid_keep_ids]

    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "UPDATE platform_feedback SET description = ? WHERE id = ?",
            (description, feedback_id),
        )
        for attachment in removed_attachments:
            conn.execute("DELETE FROM platform_feedback_attachments WHERE id = ?", (attachment["id"],))

        start_index = len(valid_keep_ids) + 1
        for index, file_item in enumerate(files, start=start_index):
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
                    now,
                ),
            )

    for attachment in removed_attachments:
        remove_feedback_attachment_file(attachment)

    return get_feedback(feedback_id)


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
        conn.execute("DELETE FROM platform_record_assignments WHERE record_id = ?", (feedback_id,))
        conn.execute("DELETE FROM platform_record_statuses WHERE record_id = ?", (feedback_id,))
        conn.execute("DELETE FROM platform_record_priorities WHERE record_id = ?", (feedback_id,))
        conn.execute("DELETE FROM platform_feedback_attachments WHERE feedback_id = ?", (feedback_id,))
        conn.execute("DELETE FROM platform_feedback WHERE id = ?", (feedback_id,))

    upload_dir = (UPLOAD_ROOT / feedback_id).resolve()
    if str(upload_dir).startswith(str(UPLOAD_ROOT.resolve())):
        shutil.rmtree(upload_dir, ignore_errors=True)
    return item


def list_deleted_record_ids():
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute("SELECT record_id FROM platform_deleted_records").fetchall()
    return [row[0] for row in rows]


def delete_record(record_id, user):
    record_id = (record_id or "").strip()
    if not record_id:
        raise ValueError("缺少反馈记录")
    require_admin(user)
    item = get_feedback(record_id)
    if item:
        delete_feedback(record_id, user)
        return {"mode": "deleted"}
    user_key, user_name = interaction_user(user)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO platform_deleted_records
              (record_id, deleted_by_key, deleted_by_name, deleted_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(record_id) DO UPDATE SET
              deleted_by_key = excluded.deleted_by_key,
              deleted_by_name = excluded.deleted_by_name,
              deleted_at = excluded.deleted_at
            """,
            (record_id, user_key, user_name, now_iso()),
        )
        conn.execute("DELETE FROM platform_record_likes WHERE record_id = ?", (record_id,))
        conn.execute("DELETE FROM platform_record_follows WHERE record_id = ?", (record_id,))
        conn.execute("DELETE FROM platform_record_comments WHERE record_id = ?", (record_id,))
        conn.execute("DELETE FROM platform_record_assignments WHERE record_id = ?", (record_id,))
        conn.execute("DELETE FROM platform_record_statuses WHERE record_id = ?", (record_id,))
        conn.execute("DELETE FROM platform_record_priorities WHERE record_id = ?", (record_id,))
        conn.execute("DELETE FROM platform_record_planner_notes WHERE record_id = ?", (record_id,))
    return {"mode": "hidden"}


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
        if parsed.path in {"", "/", "/platform.html", "/feedback.html", entry_path()} and feishu_auth_required() and feishu_auth_configured():
            session = get_auth_session(self.cookie_value(COOKIE_NAME))
            if not session or not session.get("user"):
                session_id, state = create_login_session()
                self.redirect(feishu_authorize_url(state), cookie_session_id=session_id)
                return

        if parsed.path == "/api/platform-feedback":
            self.send_json(200, {"feedback": list_feedback()})
            return
        if parsed.path == "/api/notification-settings":
            session = get_auth_session(self.cookie_value(COOKIE_NAME))
            self.send_json(200, get_notification_payload((session or {}).get("user")))
            return
        if parsed.path == "/api/sync/status":
            self.send_json(200, {"sync": read_sync_state()})
            return
        if parsed.path == "/api/team-members":
            try:
                session = get_auth_session(self.cookie_value(COOKIE_NAME))
                require_admin((session or {}).get("user"))
                self.send_json(200, {"members": list_team_members()})
            except PermissionError as exc:
                self.send_json(403, {"error": str(exc)})
            return
        if parsed.path == "/api/planner-options":
            self.send_json(200, {"members": list_planner_options()})
            return
        if parsed.path == "/api/sync/writeback-logs":
            try:
                session = get_auth_session(self.cookie_value(COOKIE_NAME))
                self.send_json(200, {"logs": list_writeback_logs((session or {}).get("user"))})
            except PermissionError as exc:
                self.send_json(403, {"error": str(exc)})
            return
        if parsed.path == "/api/record-interactions":
            session = get_auth_session(self.cookie_value(COOKIE_NAME))
            self.send_json(200, {"records": get_record_interactions((session or {}).get("user"))})
            return
        if parsed.path == "/api/deleted-records":
            self.send_json(200, {"record_ids": list_deleted_record_ids()})
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
                self.redirect(f"{entry_path()}?login=success")
            except Exception as exc:
                self.redirect(f"{entry_path()}?auth_error={quote(str(exc))}", clear_cookie=True)
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

            if parsed.path == "/api/platform-feedback/update":
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
                feedback = update_feedback(fields.get("record_id"), fields, files, (session or {}).get("user"))
                self.send_json(200, {"feedback": feedback})
                return

            if parsed.path == "/api/notification-settings":
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length)
                payload = json.loads(body.decode("utf-8") or "{}")
                session = get_auth_session(self.cookie_value(COOKIE_NAME))
                self.send_json(200, update_notification_setting(payload.get("key"), payload.get("enabled"), (session or {}).get("user")))
                return

            if parsed.path == "/api/notifications/read-all":
                session = get_auth_session(self.cookie_value(COOKIE_NAME))
                self.send_json(200, mark_notifications_read((session or {}).get("user")))
                return

            if parsed.path == "/api/notifications/read":
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                session = get_auth_session(self.cookie_value(COOKIE_NAME))
                self.send_json(200, mark_notification_read(payload.get("id"), (session or {}).get("user")))
                return

            if parsed.path == "/api/sync/base":
                session = get_auth_session(self.cookie_value(COOKIE_NAME))
                require_admin((session or {}).get("user"))
                result = sync_base_snapshot(trigger="manual")
                self.send_json(200, {"sync": read_sync_state(), "result": result})
                return

            if parsed.path == "/api/sync/writeback-pending":
                session = get_auth_session(self.cookie_value(COOKIE_NAME))
                result = retry_pending_writebacks((session or {}).get("user"))
                self.send_json(200, {"sync": read_sync_state(), "result": result})
                return

            if parsed.path == "/api/sync/writeback-retry":
                session = get_auth_session(self.cookie_value(COOKIE_NAME))
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                result = retry_writebacks(
                    (session or {}).get("user"),
                    payload.get("feedback_ids") or [],
                    bool(payload.get("failed_only")),
                )
                self.send_json(200, {"sync": read_sync_state(), "logs": list_writeback_logs((session or {}).get("user")), "result": result})
                return

            if parsed.path == "/api/team-members/admin":
                session = get_auth_session(self.cookie_value(COOKIE_NAME))
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                members = set_team_member_admin(
                    payload.get("member_id"),
                    payload.get("is_admin"),
                    (session or {}).get("user"),
                )
                self.send_json(200, {"members": members})
                return

            if parsed.path == "/api/team-members/planner":
                session = get_auth_session(self.cookie_value(COOKIE_NAME))
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                members = set_team_member_planner(
                    payload.get("member_id"),
                    payload.get("is_planner"),
                    (session or {}).get("user"),
                )
                self.send_json(200, {"members": members})
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

            if parsed.path == "/api/record-comments/delete":
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                session = get_auth_session(self.cookie_value(COOKIE_NAME))
                interaction = delete_record_comment(payload.get("comment_id"), (session or {}).get("user"))
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

            if parsed.path == "/api/record-tags/delete":
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                session = get_auth_session(self.cookie_value(COOKIE_NAME))
                interaction = delete_record_tag(
                    payload.get("record_id"),
                    payload.get("tag"),
                    (session or {}).get("user"),
                )
                self.send_json(200, {"interaction": interaction})
                return

            if parsed.path == "/api/record-tags":
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                session = get_auth_session(self.cookie_value(COOKIE_NAME))
                interaction = add_record_tag(
                    payload.get("record_id"),
                    payload.get("tag"),
                    (session or {}).get("user"),
                )
                self.send_json(201, {"interaction": interaction})
                return

            if parsed.path == "/api/planner-remarks":
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                session = get_auth_session(self.cookie_value(COOKIE_NAME))
                interaction = save_planner_note(
                    payload.get("record_id"),
                    payload.get("body"),
                    (session or {}).get("user"),
                )
                self.send_json(200, {"interaction": interaction})
                return

            if parsed.path == "/api/record-planner":
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                session = get_auth_session(self.cookie_value(COOKIE_NAME))
                interaction = assign_record_planner(
                    payload.get("record_id"),
                    payload.get("planner"),
                    (session or {}).get("user"),
                )
                self.send_json(200, {"interaction": interaction})
                return

            if parsed.path == "/api/record-status":
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                session = get_auth_session(self.cookie_value(COOKIE_NAME))
                interaction = assign_record_status(
                    payload.get("record_id"),
                    payload.get("status"),
                    (session or {}).get("user"),
                )
                self.send_json(200, {"interaction": interaction})
                return

            if parsed.path == "/api/record-priority":
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                session = get_auth_session(self.cookie_value(COOKIE_NAME))
                interaction = assign_record_priority(
                    payload.get("record_id"),
                    payload.get("priority"),
                    (session or {}).get("user"),
                )
                self.send_json(200, {"interaction": interaction})
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
        except FileNotFoundError as exc:
            self.send_json(404, {"error": str(exc)})
        except PermissionError as exc:
            self.send_json(403, {"error": str(exc)})
        except ValueError as exc:
            self.send_json(400, {"error": str(exc)})
        except Exception as exc:
            notify_platform_error("API request failed", exc, {"method": "POST", "path": parsed.path})
            self.send_json(500, {"error": f"保存失败：{exc}"})

    def do_DELETE(self):
        parsed = urlparse(self.path)
        try:
            match = re.fullmatch(r"/api/records/([^/]+)", parsed.path)
            if match:
                session = get_auth_session(self.cookie_value(COOKIE_NAME))
                result = delete_record(unquote(match.group(1)), (session or {}).get("user"))
                self.send_json(200, {"ok": True, **result})
                return
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
            notify_platform_error("API request failed", exc, {"method": "DELETE", "path": parsed.path})
            self.send_json(500, {"error": f"删除失败：{exc}"})

    def serve_static(self, request_path):
        relative = unquote(request_path.lstrip("/")) or "platform.html"
        if relative == "feedback.html":
            relative = "platform.html"
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
        if target.suffix == ".html":
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
        self.end_headers()
        with target.open("rb") as file_obj:
            shutil.copyfileobj(file_obj, self.wfile)


def scheduled_sync_loop():
    while True:
        config = feishu_sync_config()
        interval_seconds = config["interval_hours"] * 60 * 60
        time.sleep(interval_seconds)
        if not feishu_sync_ready(config):
            continue
        try:
            sync_base_snapshot(trigger="scheduled")
        except Exception as exc:
            notify_platform_error("Scheduled Base snapshot sync loop failed", exc)
            continue


def planner_note_sync_loop():
    while True:
        time.sleep(30)
        try:
            sync_due_planner_notes()
        except Exception as exc:
            notify_platform_error("Planner note sync loop failed", exc)
            continue


def main():
    init_db()
    sync_team_members_from_auth_sessions()
    UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
    threading.Thread(target=scheduled_sync_loop, daemon=True).start()
    threading.Thread(target=planner_note_sync_loop, daemon=True).start()
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8765"))
    server = ThreadingHTTPServer((host, port), PlatformHandler)
    print(f"Platform feedback server: http://{host}:{port}{entry_path()}")
    server.serve_forever()


if __name__ == "__main__":
    main()
