"""
SQLite-backed ephemeral state for line-group-bot.
Replaces in-memory dicts/deques so state survives Render restarts.
"""

import os
import json
import sqlite3
from datetime import datetime, timedelta
from collections import deque

DB_PATH = os.environ.get("STATE_DB_PATH", "/tmp/bot_state.db")
os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)


def _conn():
    return sqlite3.connect(DB_PATH, check_same_thread=False)


def _init():
    with _conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS kv (
                key TEXT PRIMARY KEY,
                value TEXT,
                expires_at TIMESTAMP
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS chat_memory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nickname TEXT,
                text TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        c.commit()


_init()

# ─── Generic KV ───────────────────────────────────────────

def kv_get(key: str, default=None):
    with _conn() as c:
        row = c.execute(
            "SELECT value FROM kv WHERE key = ? AND (expires_at IS NULL OR expires_at > ?)",
            (key, datetime.now().isoformat()),
        ).fetchone()
        if row:
            try:
                return json.loads(row[0])
            except Exception:
                return row[0]
        return default


def kv_set(key: str, value, ttl_seconds: int | None = None):
    expires = None
    if ttl_seconds:
        expires = (datetime.now() + timedelta(seconds=ttl_seconds)).isoformat()
    with _conn() as c:
        c.execute(
            "INSERT INTO kv (key, value, expires_at) VALUES (?, ?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value, expires_at=excluded.expires_at",
            (key, json.dumps(value, ensure_ascii=False), expires),
        )
        c.commit()


def kv_delete(key: str):
    with _conn() as c:
        c.execute("DELETE FROM kv WHERE key = ?", (key,))
        c.commit()


def kv_cleanup():
    with _conn() as c:
        c.execute("DELETE FROM kv WHERE expires_at IS NOT NULL AND expires_at <= ?", (datetime.now().isoformat(),))
        c.commit()


# ─── Domain-specific helpers ──────────────────────────────

# Quiz state
def quiz_get(group_id: str):
    return kv_get(f"quiz:{group_id}")


def quiz_set(group_id: str, data: dict):
    kv_set(f"quiz:{group_id}", data, ttl_seconds=86400)


def quiz_delete(group_id: str):
    kv_delete(f"quiz:{group_id}")


# Vote state
def vote_get(group_id: str):
    return kv_get(f"vote:{group_id}")


def vote_set(group_id: str, data: dict):
    kv_set(f"vote:{group_id}", data, ttl_seconds=86400)


def vote_delete(group_id: str):
    kv_delete(f"vote:{group_id}")


# Translate pending
def translate_get(user_id: str):
    return kv_get(f"translate:{user_id}")


def translate_set(user_id: str, data: dict):
    kv_set(f"translate:{user_id}", data, ttl_seconds=3600)


def translate_delete(user_id: str):
    kv_delete(f"translate:{user_id}")


# Remove BG pending
def remove_bg_get(user_id: str) -> bool:
    return kv_get(f"remove_bg:{user_id}", False)


def remove_bg_set(user_id: str, flag: bool = True):
    if flag:
        kv_set(f"remove_bg:{user_id}", True, ttl_seconds=3600)
    else:
        kv_delete(f"remove_bg:{user_id}")


# Chat memory
def chat_append(nickname: str, text: str, max_len: int = 20):
    with _conn() as c:
        c.execute("INSERT INTO chat_memory (nickname, text) VALUES (?, ?)", (nickname, text))
        # Keep only last max_len rows
        c.execute(
            "DELETE FROM chat_memory WHERE id <= (SELECT id FROM chat_memory ORDER BY id DESC LIMIT 1 OFFSET ?)",
            (max_len,),
        )
        c.commit()


def chat_get(n: int = 8):
    with _conn() as c:
        rows = c.execute(
            "SELECT nickname, text FROM chat_memory ORDER BY id DESC LIMIT ?", (n,)
        ).fetchall()
        return list(reversed(rows))


# Daily cache
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
