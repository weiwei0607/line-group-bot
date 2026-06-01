"""
SQLite-backed ephemeral state for line-group-bot.
Replaces in-memory dicts/deques so state survives Render restarts.
"""

import os
import json
import sqlite3
import threading
import logging
from datetime import datetime, timedelta

DB_PATH = os.environ.get("STATE_DB_PATH", "./data/bot_state.db")
os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)

_write_lock = threading.Lock()


def _conn():
    # Each call creates a fresh connection on the *current* thread, then
    # closes it when the context-manager exits.  No cross-thread sharing
    # means the default check_same_thread=True is fine and safe.
    conn = sqlite3.connect(DB_PATH, timeout=10.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _init():
    with _conn() as c:
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS kv (
                key TEXT PRIMARY KEY,
                value TEXT,
                expires_at TIMESTAMP
            )
            """
        )
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_memory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nickname TEXT,
                text TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS rate_limit (
                user_id TEXT PRIMARY KEY,
                count INTEGER DEFAULT 0,
                window_start TIMESTAMP
            )
            """
        )
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_kv_expires ON kv(expires_at)"
        )
        c.commit()


_init()


# ─── Generic KV ───────────────────────────────────────────


def kv_get(key: str, default=None):
    try:
        with _conn() as c:
            row = c.execute(
                "SELECT value FROM kv WHERE key = ? AND (expires_at IS NULL OR expires_at > datetime('now'))",
                (key,),
            ).fetchone()
            if row:
                try:
                    return json.loads(row[0])
                except (json.JSONDecodeError, TypeError):
                    return row[0]
    except sqlite3.Error as exc:
        logging.warning("state kv_get error: %s", exc)
    return default


def kv_set(key: str, value, ttl_seconds: int | None = None):
    expires = None
    if ttl_seconds:
        expires = (datetime.utcnow() + timedelta(seconds=ttl_seconds)).isoformat()
    try:
        with _write_lock, _conn() as c:
            c.execute(
                "INSERT INTO kv (key, value, expires_at) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, expires_at=excluded.expires_at",
                (key, json.dumps(value, ensure_ascii=False), expires),
            )
            c.commit()
            _maybe_cleanup()
    except sqlite3.Error as exc:
        logging.warning("state kv_set error: %s", exc)


def kv_delete(key: str):
    try:
        with _write_lock, _conn() as c:
            c.execute("DELETE FROM kv WHERE key = ?", (key,))
            c.commit()
    except sqlite3.Error as exc:
        logging.warning("state kv_delete error: %s", exc)


def kv_cleanup():
    try:
        with _write_lock, _conn() as c:
            c.execute("DELETE FROM kv WHERE expires_at IS NOT NULL AND expires_at <= datetime('now')")
            c.commit()
    except sqlite3.Error as exc:
        logging.warning("state kv_cleanup error: %s", exc)


def _maybe_cleanup():
    """Probabilistic cleanup (~1% chance per write) to avoid unbounded growth."""
    import random
    if random.random() > 0.01:
        return
    try:
        with _write_lock, _conn() as c:
            # Expired KV
            c.execute("DELETE FROM kv WHERE expires_at IS NOT NULL AND expires_at <= datetime('now')")
            # Old rate_limit entries (window older than 7 days)
            cutoff = (datetime.utcnow() - timedelta(days=7)).isoformat()
            c.execute("DELETE FROM rate_limit WHERE window_start <= ?", (cutoff,))
            # Old chat_memory beyond 100 rows
            c.execute(
                "DELETE FROM chat_memory WHERE id <= ("
                "SELECT id FROM chat_memory ORDER BY id DESC LIMIT 1 OFFSET 100"
                ")"
            )
            c.commit()
    except sqlite3.Error as exc:
        logging.warning("state cleanup error: %s", exc)


# ─── Domain-specific helpers ──────────────────────────────


def quiz_get(group_id: str):
    return kv_get(f"quiz:{group_id}")


def quiz_set(group_id: str, data: dict):
    kv_set(f"quiz:{group_id}", data, ttl_seconds=86400)


def quiz_delete(group_id: str):
    kv_delete(f"quiz:{group_id}")


def vote_get(group_id: str):
    return kv_get(f"vote:{group_id}")


def vote_set(group_id: str, data: dict):
    kv_set(f"vote:{group_id}", data, ttl_seconds=86400)


def vote_delete(group_id: str):
    kv_delete(f"vote:{group_id}")


def translate_get(user_id: str):
    return kv_get(f"translate:{user_id}")


def translate_set(user_id: str, data: dict):
    kv_set(f"translate:{user_id}", data, ttl_seconds=3600)


def translate_delete(user_id: str):
    kv_delete(f"translate:{user_id}")


def remove_bg_get(user_id: str) -> bool:
    return kv_get(f"remove_bg:{user_id}", False)


def remove_bg_set(user_id: str, flag: bool = True):
    if flag:
        kv_set(f"remove_bg:{user_id}", True, ttl_seconds=3600)
    else:
        kv_delete(f"remove_bg:{user_id}")


# ─── Chat memory ──────────────────────────────────────────


def chat_append(nickname: str, text: str, max_len: int = 20):
    try:
        with _write_lock, _conn() as c:
            c.execute("INSERT INTO chat_memory (nickname, text) VALUES (?, ?)", (nickname, text))
            c.execute(
                "DELETE FROM chat_memory WHERE id <= ("
                "SELECT id FROM chat_memory ORDER BY id DESC LIMIT 1 OFFSET ?"
                ")",
                (max_len,),
            )
            c.commit()
            _maybe_cleanup()
    except sqlite3.Error as exc:
        logging.warning("state chat_append error: %s", exc)


def chat_get(n: int = 8):
    try:
        with _conn() as c:
            rows = c.execute(
                "SELECT nickname, text FROM chat_memory ORDER BY id DESC LIMIT ?", (n,)
            ).fetchall()
            return list(reversed(rows))
    except sqlite3.Error as exc:
        logging.warning("state chat_get error: %s", exc)
        return []


# ─── Daily cache ──────────────────────────────────────────


def daily_get(key: str, date_str: str | None = None):
    if date_str is None:
        from goal_tracker import TW_TZ

        date_str = datetime.now(TW_TZ).strftime("%Y-%m-%d")
    return kv_get(f"daily:{key}:{date_str}")


def daily_set(key: str, value, date_str: str | None = None):
    if date_str is None:
        from goal_tracker import TW_TZ

        date_str = datetime.now(TW_TZ).strftime("%Y-%m-%d")
    kv_set(f"daily:{key}:{date_str}", value, ttl_seconds=172800)


def daily_cleanup():
    kv_cleanup()


# ─── Rate limiting ────────────────────────────────────────


def rate_limit_check(user_id: str, max_requests: int = 30, window_seconds: int = 60) -> bool:
    """Return True if user is allowed, False if rate-limited."""
    now = datetime.utcnow().isoformat()
    window_start = (datetime.utcnow() - timedelta(seconds=window_seconds)).isoformat()
    try:
        with _write_lock, _conn() as c:
            row = c.execute(
                "SELECT count, window_start FROM rate_limit WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            if row:
                count, ws = row
                if ws and ws < window_start:
                    c.execute(
                        "UPDATE rate_limit SET count = 1, window_start = ? WHERE user_id = ?",
                        (now, user_id),
                    )
                    c.commit()
                    return True
                if count >= max_requests:
                    return False
                c.execute(
                    "UPDATE rate_limit SET count = count + 1 WHERE user_id = ?",
                    (user_id,),
                )
                c.commit()
                return True
            c.execute(
                "INSERT INTO rate_limit (user_id, count, window_start) VALUES (?, 1, ?)",
                (user_id, now),
            )
            c.commit()
            _maybe_cleanup()
            return True
    except sqlite3.Error as exc:
        logging.warning("state rate_limit_check error: %s", exc)
        return True  # fail open
