#!/usr/bin/env python3
"""Conversation watchdog v2: thread-log-sync style over local HTTP POST."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

DEFAULT_PORT = 18888
DEFAULT_MAX_THREADS = 20
DEFAULT_THINKING_THRESHOLD_SECONDS = 90


class Config:
    def __init__(self, args: argparse.Namespace):
        self.port = args.port
        self.log_root = Path(args.log_root).expanduser()
        self.max_threads = max(1, args.max_threads)
        self.thinking_threshold_seconds = max(1, args.thinking_threshold_seconds)
        self.legacy_md = Path(args.legacy_md).expanduser()
        self.legacy_db = Path(args.legacy_db).expanduser()
        self.enable_legacy_mirror = args.enable_legacy_mirror


def now_local() -> dt.datetime:
    return dt.datetime.now().astimezone()


def now_iso() -> str:
    return now_local().isoformat(timespec="seconds")


def sanitize_name(raw: str | None, default: str = "default-thread") -> str:
    value = (raw or "").strip() or default
    invalid = set('<>:"/\\|?*\x00')
    cleaned = "".join("_" if ch in invalid else ch for ch in value).strip()
    return cleaned or default


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def connect_db(path: Path) -> sqlite3.Connection:
    ensure_parent(path)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            reason TEXT NOT NULL,
            action TEXT NOT NULL,
            result TEXT NOT NULL,
            channel TEXT,
            status TEXT,
            message TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS kv_state (
            k TEXT PRIMARY KEY,
            v TEXT NOT NULL
        )
        """
    )
    return conn


def get_kv(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT v FROM kv_state WHERE k = ?", (key,)).fetchone()
    return row[0] if row else None


def set_kv(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO kv_state(k, v) VALUES(?, ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
        (key, value),
    )


def insert_event(
    conn: sqlite3.Connection,
    *,
    reason: str,
    action: str,
    result: str,
    channel: str | None,
    status: str | None,
    message: str | None,
    timestamp: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO events(timestamp, reason, action, result, channel, status, message)
        VALUES(?, ?, ?, ?, ?, ?, ?)
        """,
        (timestamp or now_iso(), reason, action, result, channel, status, message),
    )


def log_paths(cfg: Config, thread_name: str) -> tuple[Path, Path]:
    safe = sanitize_name(thread_name)
    return cfg.log_root / f"{safe}.log", cfg.log_root / f"{safe}.sqlite"


def append_human_log(log_path: Path, role: str, content: str, ts: str) -> None:
    ensure_parent(log_path)
    with log_path.open("a", encoding="utf-8") as f:
        f.write(f"\n**{role}** [{ts}]:\n{content}\n")
        f.write("-" * 40 + "\n")


def stem_mtime(root: Path, stem: str) -> float:
    mtimes = []
    for ext in (".log", ".sqlite"):
        p = root / f"{stem}{ext}"
        if p.exists():
            mtimes.append(p.stat().st_mtime)
    return max(mtimes) if mtimes else 0.0


def prune_threads(cfg: Config, protected: set[str]) -> list[str]:
    cfg.log_root.mkdir(parents=True, exist_ok=True)
    stems = {p.stem for p in cfg.log_root.glob("*.log")} | {p.stem for p in cfg.log_root.glob("*.sqlite")}
    ordered = sorted(stems, key=lambda s: stem_mtime(cfg.log_root, s), reverse=True)
    keep = set(ordered[: cfg.max_threads]) | {sanitize_name(x) for x in protected}

    removed: list[str] = []
    for stem in ordered:
        if stem in keep:
            continue
        for ext in (".log", ".sqlite"):
            p = cfg.log_root / f"{stem}{ext}"
            if p.exists():
                p.unlink()
        removed.append(stem)
    return removed


def maybe_insert_heartbeat(conn: sqlite3.Connection, cfg: Config) -> None:
    last_iso = get_kv(conn, "last_activity_ts")
    if not last_iso:
        return
    try:
        last = dt.datetime.fromisoformat(last_iso)
    except ValueError:
        return
    idle_seconds = (now_local() - last).total_seconds()
    if idle_seconds < cfg.thinking_threshold_seconds:
        return
    insert_event(
        conn,
        reason="thinking-update",
        action="heartbeat",
        result="ok",
        channel="status",
        status="思考中",
        message=f"思考中 @ {now_iso()}",
    )


def mirror_legacy(cfg: Config, role: str, content: str, ts: str) -> None:
    ensure_parent(cfg.legacy_md)
    with cfg.legacy_md.open("w", encoding="utf-8") as f:
        f.write(f"**{role}** [{ts}]:\n\n{content}\n")

    ensure_parent(cfg.legacy_db)
    conn = sqlite3.connect(cfg.legacy_db)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            role TEXT,
            content TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute("INSERT INTO messages(role, content) VALUES(?, ?)", (role, content))
    conn.commit()
    conn.close()


class Handler(BaseHTTPRequestHandler):
    server_version = "conversation-watchdog-v2/1.0"

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            self._json(200, {"status": "ok", "service": "conversation-watchdog-v2", "time": now_iso()})
            return
        self._json(404, {"status": "error", "message": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/log":
            self._json(404, {"status": "error", "message": "not found"})
            return

        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        try:
            payload: dict[str, Any] = json.loads(raw.decode("utf-8")) if raw else {}
        except json.JSONDecodeError as exc:
            self._json(400, {"status": "error", "message": f"invalid json: {exc}"})
            return

        role = str(payload.get("role", "UNKNOWN"))
        content = str(payload.get("content", "")).strip()
        thread_name = sanitize_name(str(payload.get("thread_name") or payload.get("window_title") or "default-thread"))
        channel = str(payload.get("channel", "final"))
        ts = now_iso()

        if not content:
            self._json(400, {"status": "error", "message": "empty content"})
            return

        log_path, db_path = log_paths(self.server.cfg, thread_name)
        conn = connect_db(db_path)
        try:
            maybe_insert_heartbeat(conn, self.server.cfg)
            append_human_log(log_path, role, content, ts)
            insert_event(
                conn,
                reason="assistant-output",
                action="write",
                result="ok",
                channel=channel,
                status=None,
                message=content,
                timestamp=ts,
            )
            set_kv(conn, "last_activity_ts", ts)
            conn.commit()
        finally:
            conn.close()

        if self.server.cfg.enable_legacy_mirror:
            mirror_legacy(self.server.cfg, role, content, ts)

        removed = prune_threads(self.server.cfg, {thread_name})
        self._json(
            200,
            {
                "status": "success",
                "thread": thread_name,
                "log_file": str(log_path),
                "db_file": str(db_path),
                "pruned": removed,
            },
        )

    def _json(self, code: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: Any) -> None:
        return


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Thread-log-sync style local watchdog server")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--log-root", default="~/Desktop/conversation_threads")
    parser.add_argument("--max-threads", type=int, default=DEFAULT_MAX_THREADS)
    parser.add_argument("--thinking-threshold-seconds", type=int, default=DEFAULT_THINKING_THRESHOLD_SECONDS)
    parser.add_argument("--legacy-md", default="~/Desktop/conversation_log.md")
    parser.add_argument("--legacy-db", default="~/Desktop/conversation_log.db")
    parser.add_argument("--enable-legacy-mirror", action="store_true")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    cfg = Config(args)
    cfg.log_root.mkdir(parents=True, exist_ok=True)

    server = ThreadingHTTPServer(("", cfg.port), Handler)
    server.cfg = cfg  # type: ignore[attr-defined]

    print(
        json.dumps(
            {
                "status": "running",
                "port": cfg.port,
                "log_root": str(cfg.log_root),
                "max_threads": cfg.max_threads,
                "legacy_mirror": cfg.enable_legacy_mirror,
            },
            ensure_ascii=False,
        )
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
