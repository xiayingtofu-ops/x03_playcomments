#!/usr/bin/env python3
"""Mirror a Feishu Base table/view into a local SQLite database."""

from __future__ import annotations

import argparse
import csv
import json
import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_CONNECTOR = r"C:\Users\Administrator\plugins\lark-enterprise\scripts\feishu-connector.cmd"
DEFAULT_BASE_TOKEN = "PQTsbEtUPaV80isZ5dRcVFKsn6f"
DEFAULT_TABLE_ID = "tblYhznxxvRtSyY9"
DEFAULT_VIEW_ID = "vew9FrF9dW"


class SyncError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_json_output(text: str) -> Any:
    stripped = text.strip()
    if not stripped:
        raise SyncError("lark-cli returned empty output")

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


def cli_record_list(
    connector: str,
    base_token: str,
    table_id: str,
    view_id: str | None,
    offset: int,
    limit: int,
) -> Any:
    cmd = [
        connector,
        "lark-cli",
        "--",
        "base",
        "+record-list",
        "--base-token",
        base_token,
        "--table-id",
        table_id,
        "--offset",
        str(offset),
        "--limit",
        str(limit),
        "--format",
        "json",
        "--as",
        "user",
    ]
    if view_id:
        cmd.extend(["--view-id", view_id])

    proc = subprocess.run(
        cmd,
        cwd=Path(__file__).resolve().parent,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )

    if proc.returncode != 0:
        detail = (proc.stdout + "\n" + proc.stderr).strip()
        raise SyncError(detail or f"lark-cli exited with code {proc.returncode}")

    payload = parse_json_output(proc.stdout)
    if isinstance(payload, dict) and payload.get("ok") is False:
        raise SyncError(json.dumps(payload, ensure_ascii=False, indent=2))
    return payload


def find_record_items(payload: Any) -> list[dict[str, Any]]:
    candidates: list[list[dict[str, Any]]] = []

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            for key in ("items", "records", "record_list", "data"):
                value = node.get(key)
                if isinstance(value, list) and all(isinstance(x, dict) for x in value):
                    if any(("record_id" in x or "fields" in x or "id" in x) for x in value):
                        candidates.append(value)
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            if all(isinstance(x, dict) for x in node):
                if any(("record_id" in x or "fields" in x or "id" in x) for x in node):
                    candidates.append(node)
            for value in node:
                visit(value)

    visit(payload)
    if not candidates:
        return []
    return max(candidates, key=len)


def find_bool(payload: Any, key: str) -> bool | None:
    if isinstance(payload, dict):
        if key in payload and isinstance(payload[key], bool):
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


def normalize_record(record: dict[str, Any]) -> tuple[str, dict[str, Any], dict[str, Any]]:
    record_id = record.get("record_id") or record.get("id") or record.get("recordId")
    if not record_id:
        raise SyncError(f"record without record_id: {record}")

    fields = record.get("fields")
    if not isinstance(fields, dict):
        fields = {
            key: value
            for key, value in record.items()
            if key not in {"record_id", "id", "recordId", "created_time", "last_modified_time"}
        }
    return str(record_id), fields, record


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS sync_runs (
            sync_id TEXT PRIMARY KEY,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            status TEXT NOT NULL,
            record_count INTEGER DEFAULT 0,
            message TEXT
        );

        CREATE TABLE IF NOT EXISTS records (
            record_id TEXT PRIMARY KEY,
            fields_json TEXT NOT NULL,
            raw_json TEXT NOT NULL,
            is_deleted INTEGER NOT NULL DEFAULT 0,
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            last_sync_id TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS record_fields (
            record_id TEXT NOT NULL,
            field_name TEXT NOT NULL,
            value_json TEXT NOT NULL,
            value_text TEXT,
            last_sync_id TEXT NOT NULL,
            PRIMARY KEY (record_id, field_name),
            FOREIGN KEY (record_id) REFERENCES records(record_id)
        );

        CREATE INDEX IF NOT EXISTS idx_records_deleted ON records(is_deleted);
        CREATE INDEX IF NOT EXISTS idx_record_fields_name ON record_fields(field_name);

        CREATE TABLE IF NOT EXISTS sync_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """
    )


def value_to_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value)


def upsert_records(
    conn: sqlite3.Connection,
    records: list[dict[str, Any]],
    sync_id: str,
) -> int:
    now = utc_now()
    count = 0
    for record in records:
        record_id, fields, raw = normalize_record(record)
        fields_json = json.dumps(fields, ensure_ascii=False, sort_keys=True)
        raw_json = json.dumps(raw, ensure_ascii=False, sort_keys=True)

        conn.execute(
            """
            INSERT INTO records (
                record_id, fields_json, raw_json, is_deleted,
                first_seen_at, last_seen_at, last_sync_id
            )
            VALUES (?, ?, ?, 0, ?, ?, ?)
            ON CONFLICT(record_id) DO UPDATE SET
                fields_json = excluded.fields_json,
                raw_json = excluded.raw_json,
                is_deleted = 0,
                last_seen_at = excluded.last_seen_at,
                last_sync_id = excluded.last_sync_id
            """,
            (record_id, fields_json, raw_json, now, now, sync_id),
        )

        for field_name, value in fields.items():
            conn.execute(
                """
                INSERT INTO record_fields (
                    record_id, field_name, value_json, value_text, last_sync_id
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(record_id, field_name) DO UPDATE SET
                    value_json = excluded.value_json,
                    value_text = excluded.value_text,
                    last_sync_id = excluded.last_sync_id
                """,
                (
                    record_id,
                    str(field_name),
                    json.dumps(value, ensure_ascii=False, sort_keys=True),
                    value_to_text(value),
                    sync_id,
                ),
            )

        conn.execute(
            "DELETE FROM record_fields WHERE record_id = ? AND last_sync_id != ?",
            (record_id, sync_id),
        )
        count += 1

    conn.execute(
        "UPDATE records SET is_deleted = 1 WHERE last_sync_id != ?",
        (sync_id,),
    )
    return count


def export_csv(conn: sqlite3.Connection, csv_path: Path, include_deleted: bool = False) -> None:
    where = "" if include_deleted else "WHERE is_deleted = 0"
    rows = conn.execute(
        f"SELECT record_id, fields_json, is_deleted, last_seen_at FROM records {where} ORDER BY record_id"
    ).fetchall()

    field_names: list[str] = []
    seen: set[str] = set()
    parsed_rows: list[tuple[str, dict[str, Any], int, str]] = []
    for record_id, fields_json, is_deleted, last_seen_at in rows:
        fields = json.loads(fields_json)
        parsed_rows.append((record_id, fields, is_deleted, last_seen_at))
        for name in fields:
            if name not in seen:
                seen.add(name)
                field_names.append(name)

    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["record_id", *field_names, "_is_deleted", "_last_seen_at"],
        )
        writer.writeheader()
        for record_id, fields, is_deleted, last_seen_at in parsed_rows:
            row = {"record_id": record_id, "_is_deleted": is_deleted, "_last_seen_at": last_seen_at}
            row.update({name: value_to_text(fields.get(name)) for name in field_names})
            writer.writerow(row)


def sync_once(args: argparse.Namespace) -> int:
    connector = args.connector or os.environ.get("FEISHU_CONNECTOR") or DEFAULT_CONNECTOR
    all_records: list[dict[str, Any]] = []
    offset = 0

    while True:
        payload = cli_record_list(
            connector=connector,
            base_token=args.base_token,
            table_id=args.table_id,
            view_id=args.view_id,
            offset=offset,
            limit=args.limit,
        )
        page_records = find_record_items(payload)
        all_records.extend(page_records)

        has_more = find_bool(payload, "has_more")
        if has_more is False or not page_records:
            break
        offset += len(page_records)
        if has_more is None and len(page_records) < args.limit:
            break

    sync_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    db_path = Path(args.db).resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path)
    try:
        ensure_schema(conn)
        conn.execute(
            "INSERT INTO sync_runs(sync_id, started_at, status) VALUES (?, ?, ?)",
            (sync_id, utc_now(), "running"),
        )
        count = upsert_records(conn, all_records, sync_id)
        conn.execute(
            """
            UPDATE sync_runs
            SET finished_at = ?, status = ?, record_count = ?, message = ?
            WHERE sync_id = ?
            """,
            (utc_now(), "success", count, "ok", sync_id),
        )
        conn.execute(
            """
            INSERT INTO sync_meta(key, value) VALUES ('last_success_sync_id', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (sync_id,),
        )
        conn.commit()
        if args.csv:
            export_csv(conn, Path(args.csv).resolve(), args.include_deleted)
        return count
    except Exception as exc:
        conn.rollback()
        ensure_schema(conn)
        conn.execute(
            """
            INSERT OR REPLACE INTO sync_runs(sync_id, started_at, finished_at, status, message)
            VALUES (?, ?, ?, ?, ?)
            """,
            (sync_id, utc_now(), utc_now(), "failed", str(exc)),
        )
        conn.commit()
        raise
    finally:
        conn.close()


def record_failed_run(args: argparse.Namespace, message: str) -> None:
    db_path = Path(args.db).resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        ensure_schema(conn)
        sync_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ_failed")
        conn.execute(
            """
            INSERT OR REPLACE INTO sync_runs(sync_id, started_at, finished_at, status, message)
            VALUES (?, ?, ?, ?, ?)
            """,
            (sync_id, utc_now(), utc_now(), "failed", message),
        )
        conn.commit()
    finally:
        conn.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Sync a Feishu Base table/view to SQLite.")
    parser.add_argument("--base-token", default=DEFAULT_BASE_TOKEN)
    parser.add_argument("--table-id", default=DEFAULT_TABLE_ID)
    parser.add_argument("--view-id", default=DEFAULT_VIEW_ID)
    parser.add_argument("--db", default="feishu_base_records.sqlite")
    parser.add_argument("--csv", default="feishu_base_records.csv")
    parser.add_argument("--connector", default=os.environ.get("FEISHU_CONNECTOR", DEFAULT_CONNECTOR))
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--watch", action="store_true", help="Keep syncing on an interval.")
    parser.add_argument("--interval", type=int, default=30, help="Watch interval in seconds.")
    parser.add_argument("--include-deleted", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    while True:
        try:
            count = sync_once(args)
            print(f"[{utc_now()}] synced {count} records into {Path(args.db).resolve()}")
        except Exception as exc:
            print(f"[{utc_now()}] sync failed: {exc}", file=sys.stderr)
            record_failed_run(args, str(exc))
            if not args.watch:
                return 1
        if not args.watch:
            return 0
        time.sleep(max(5, args.interval))


if __name__ == "__main__":
    raise SystemExit(main())
