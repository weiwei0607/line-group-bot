"""
TTS audio + cron + dedup persistence for line-family-bot.
"""

import os
import sqlite3
import threading
import logging
from datetime import datetime, timedelta

DB_PATH = os.environ.get("TTS_DB_PATH", "./data/tts_store.db")
os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)

_write_lock = threading.Lock()


def _conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _init():
    with _conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS tts_audio (
                filename TEXT PRIMARY KEY,
                audio_blob BLOB,
                mime_type TEXT DEFAULT 'audio/mpeg',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS cron_log (
                task_name TEXT PRIMARY KEY,
                run_date TEXT,
                run_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS webhook_dedup (
                dedup_key TEXT PRIMARY KEY,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS kv (
                key TEXT PRIMARY KEY,
                value TEXT,
                expires_at TIMESTAMP
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_kv_expires ON kv(expires_at)")
        c.commit()


_init()


# ─── TTS ──────────────────────────────────────────────────

def save_tts_audio(filename: str, audio_bytes: bytes, mime_type: str = "audio/mpeg") -> None:
    try:
        with _write_lock, _conn() as c:
            c.execute(
                "INSERT INTO tts_audio (filename, audio_blob, mime_type) VALUES (?, ?, ?) "
                "ON CONFLICT(filename) DO UPDATE SET audio_blob=excluded.audio_blob, mime_type=excluded.mime_type",
                (filename, audio_bytes, mime_type),
            )
            c.execute(
                "DELETE FROM tts_audio WHERE filename IN ("
                "SELECT filename FROM tts_audio ORDER BY created_at DESC LIMIT -1 OFFSET 100"
                ")"
            )
            c.commit()
            _maybe_cleanup()
    except sqlite3.Error as exc:
        logging.warning("tts_store save error: %s", exc)


def get_tts_audio(filename: str) -> tuple[bytes, str] | None:
    try:
        with _conn() as c:
            row = c.execute(
                "SELECT audio_blob, mime_type FROM tts_audio WHERE filename = ?",
                (filename,),
            ).fetchone()
            if row:
                return row[0], row[1]
    except sqlite3.Error as exc:
        logging.warning("tts_store get error: %s", exc)
    return None


def delete_tts_audio(filename: str) -> None:
    try:
        with _write_lock, _conn() as c:
            c.execute("DELETE FROM tts_audio WHERE filename = ?", (filename,))
            c.commit()
    except sqlite3.Error as exc:
        logging.warning("tts_store delete error: %s", exc)


def _maybe_cleanup():
    """Probabilistic cleanup (~1% chance per write) to avoid unbounded growth."""
    import random
    if random.random() > 0.01:
        return
    try:
        with _write_lock, _conn() as c:
            # Old cron_log entries (> 90 days)
            cutoff = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")
            c.execute("DELETE FROM cron_log WHERE run_date <= ?", (cutoff,))
            # Old webhook_dedup entries (> 1 hour)
            c.execute(
                "DELETE FROM webhook_dedup WHERE created_at <= ?",
                ((datetime.now() - timedelta(hours=1)).isoformat(),),
            )
            c.commit()
    except sqlite3.Error as exc:
        logging.warning("tts_store cleanup error: %s", exc)


# ─── Cron idempotency ─────────────────────────────────────

def cron_is_done(task_name: str, date_str: str | None = None) -> bool:
    if date_str is None:
        date_str = datetime.now().strftime("%Y-%m-%d")
    try:
        with _conn() as c:
            row = c.execute(
                "SELECT 1 FROM cron_log WHERE task_name = ? AND run_date = ?",
                (task_name, date_str),
            ).fetchone()
            return row is not None
    except sqlite3.Error as exc:
        logging.warning("cron_is_done error: %s", exc)
        return False


def cron_mark_done(task_name: str, date_str: str | None = None) -> None:
    if date_str is None:
        date_str = datetime.now().strftime("%Y-%m-%d")
    try:
        with _write_lock, _conn() as c:
            c.execute(
                "INSERT INTO cron_log (task_name, run_date) VALUES (?, ?) "
                "ON CONFLICT(task_name) DO UPDATE SET run_date=excluded.run_date, run_at=CURRENT_TIMESTAMP",
                (task_name, date_str),
            )
            c.commit()
            _maybe_cleanup()
    except sqlite3.Error as exc:
        logging.warning("cron_mark_done error: %s", exc)


def cron_try_mark_done(task_name: str, date_str: str | None = None) -> bool:
    """Atomically check-and-set: returns True only if newly marked for this date."""
    if date_str is None:
        date_str = datetime.now().strftime("%Y-%m-%d")
    try:
        with _write_lock, _conn() as c:
            row = c.execute(
                "SELECT 1 FROM cron_log WHERE task_name = ? AND run_date = ?",
                (task_name, date_str),
            ).fetchone()
            if row is not None:
                return False
            c.execute(
                "INSERT INTO cron_log (task_name, run_date) VALUES (?, ?)",
                (task_name, date_str),
            )
            c.commit()
            _maybe_cleanup()
            return True
    except sqlite3.Error as exc:
        logging.warning("cron_try_mark_done error: %s", exc)
        return False


# ─── Webhook deduplication ────────────────────────────────

def webhook_seen(dedup_key: str, ttl_seconds: int = 300) -> bool:
    """Return True if this webhook was already processed."""
    try:
        with _conn() as c:
            row = c.execute(
                "SELECT 1 FROM webhook_dedup WHERE dedup_key = ? AND created_at > ?",
                (dedup_key, (datetime.now() - timedelta(seconds=ttl_seconds)).isoformat()),
            ).fetchone()
            if row:
                return True
            c.execute(
                "INSERT INTO webhook_dedup (dedup_key) VALUES (?) "
                "ON CONFLICT(dedup_key) DO UPDATE SET created_at=CURRENT_TIMESTAMP",
                (dedup_key,),
            )
            # Cleanup old keys
            c.execute(
                "DELETE FROM webhook_dedup WHERE created_at <= ?",
                ((datetime.now() - timedelta(seconds=ttl_seconds * 2)).isoformat(),),
            )
            c.commit()
            _maybe_cleanup()
            return False
    except sqlite3.Error as exc:
        logging.warning("webhook_seen error: %s", exc)
        return False  # fail open


# ─── Generic KV store (for vote, quiz, etc.) ──────────────

def kv_get(key: str, default=None):
    try:
        with _conn() as c:
            row = c.execute(
                "SELECT value FROM kv WHERE key = ? AND (expires_at IS NULL OR expires_at > datetime('now'))",
                (key,),
            ).fetchone()
            if row:
                import json
                try:
                    return json.loads(row[0])
                except (json.JSONDecodeError, TypeError):
                    return row[0]
    except sqlite3.Error as exc:
        logging.warning("kv_get error: %s", exc)
    return default


def kv_set(key: str, value, ttl_seconds: int | None = None):
    import json
    expires = None
    if ttl_seconds:
        expires = (datetime.now() + timedelta(seconds=ttl_seconds)).isoformat()
    try:
        with _write_lock, _conn() as c:
            c.execute(
                "INSERT INTO kv (key, value, expires_at) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, expires_at=excluded.expires_at",
                (key, json.dumps(value, ensure_ascii=False), expires),
            )
            c.commit()
    except sqlite3.Error as exc:
        logging.warning("kv_set error: %s", exc)


def kv_delete(key: str):
    try:
        with _write_lock, _conn() as c:
            c.execute("DELETE FROM kv WHERE key = ?", (key,))
            c.commit()
    except sqlite3.Error as exc:
        logging.warning("kv_delete error: %s", exc)


def webhook_cleanup(ttl_seconds: int = 600) -> None:
    try:
        with _write_lock, _conn() as c:
            c.execute(
                "DELETE FROM webhook_dedup WHERE created_at <= ?",
                ((datetime.now() - timedelta(seconds=ttl_seconds)).isoformat(),),
            )
            c.commit()
    except sqlite3.Error as exc:
        logging.warning("webhook_cleanup error: %s", exc)
