"""
持久化對話記憶（摘要 + 最近原文）。

為什麼要有這個檔：
  舊版「最近對話」存在 state.py 的本機 SQLite（./data/bot_state.db）。
  Render 免費方案的磁碟是暫時的，每次部署、重啟、休眠喚醒都會清空，所以記憶會不見。

做法：
  - 原文：沿用 goal_tracker.log_chat_message 寫進 Google Sheet「聊天記錄」分頁（本來就有、持久）。
  - 摘要：Google Sheet「對話摘要」分頁，一列：scope | summary | watermark | updated_at
      watermark = 已被濃縮進 summary 的「聊天記錄」最後一列（列號，含表頭，所以最小是 1）。
  - 注入 context = 摘要 + watermark 之後的原文（最近 N 則）。
  - compact：watermark 之後的原文超過 THRESHOLD 則時，把「除了最近 KEEP 則」的部分
    連同舊摘要交給 LLM 濃縮成新摘要，成功才前進 watermark。
    LLM 或寫入失敗 -> watermark 不動，原文都還在，不會掉資料。

不需要新的金鑰，沿用 GOAL_SHEET_ID / GOOGLE_* 。
可選環境變數：MEMORY_COMPACT_THRESHOLD（預設 30）、MEMORY_KEEP_RECENT（預設 10）。
"""

import logging
import os
import threading
import time

import requests

logger = logging.getLogger(__name__)

SUMMARY_TAB = "對話摘要"
LOG_TAB = "聊天記錄"
SCOPE = "main"  # 目前 bot 只服務一個群組；之後要多群組就改成 group_id

THRESHOLD = int(os.environ.get("MEMORY_COMPACT_THRESHOLD", "30") or 30)
KEEP_RECENT = int(os.environ.get("MEMORY_KEEP_RECENT", "10") or 10)
CHECK_EVERY = 5            # 每 N 則訊息檢查一次要不要 compact
MAX_TAIL_READ = 400        # 一次最多讀幾列原文
MAX_BATCH_ROWS = 150       # 一次最多濃縮幾列
MAX_BATCH_CHARS = 5500     # 送 LLM 的原文上限（utils.sanitize_input 會在 8000 字截斷）
MSG_CLIP = 200             # 單則訊息上限字數
MAX_SUMMARY_CHARS = 1500
CONTEXT_TAIL_LINES = 40    # 注入 context 的原文上限
BOOTSTRAP_ROWS = 200       # 第一次啟用時，只濃縮最近這麼多列，更舊的留在 Sheet 不動
STATE_TTL = 300
FAIL_ALERT_AFTER = 3

_cache = {"state": None, "ts": 0.0}
_cache_lock = threading.Lock()
_compact_lock = threading.Lock()
_counter = {"n": 0, "fails": 0}


class SheetsError(RuntimeError):
    """Sheets API 失敗，訊息帶原始回應。"""


# ─── Sheets low-level（會檢查 HTTP 狀態，不像 goal_tracker 的 helper 會吞錯） ──

def _sheet_id() -> str:
    from goal_tracker import GOAL_SHEET_ID
    return GOAL_SHEET_ID


def _request(method: str, url: str, **kw):
    from goal_tracker import _get_token
    token = _get_token()
    headers = {"Authorization": f"Bearer {token}"}
    if "json" in kw:
        headers["Content-Type"] = "application/json"
    resp = requests.request(method, url, headers=headers, timeout=10, **kw)
    if not resp.ok:
        raise SheetsError(f"HTTP {resp.status_code}: {resp.text[:300]}")
    return resp.json()


def _values_get(range_: str) -> list:
    sid = _sheet_id()
    if not sid:
        raise SheetsError("GOAL_SHEET_ID not set")
    data = _request("GET", f"https://sheets.googleapis.com/v4/spreadsheets/{sid}/values/{range_}")
    return data.get("values", [])


def _values_update(range_: str, values: list) -> None:
    sid = _sheet_id()
    _request(
        "PUT",
        f"https://sheets.googleapis.com/v4/spreadsheets/{sid}/values/{range_}?valueInputOption=RAW",
        json={"values": values},
    )


def _ensure_tab() -> None:
    """建立「對話摘要」分頁（已存在就略過）。只動這個新分頁。"""
    sid = _sheet_id()
    try:
        _request(
            "POST",
            f"https://sheets.googleapis.com/v4/spreadsheets/{sid}:batchUpdate",
            json={"requests": [{"addSheet": {"properties": {"title": SUMMARY_TAB}}}]},
        )
    except SheetsError as exc:
        if "already exists" not in str(exc):
            raise
    _values_update(f"{SUMMARY_TAB}!A1:D1", [["scope", "summary", "watermark", "updated_at"]])


# ─── State ────────────────────────────────────────────────

def _now_str() -> str:
    from goal_tracker import _now
    return _now().strftime("%Y-%m-%d %H:%M")


def _read_state():
    """回傳 {'summary','watermark','updated_at'}，沒有就回 None。出錯會 raise。"""
    try:
        rows = _values_get(f"{SUMMARY_TAB}!A2:D2")
    except SheetsError as exc:
        if "Unable to parse range" in str(exc) or "HTTP 400" in str(exc):
            _ensure_tab()  # 分頁還不存在
            rows = []
        else:
            raise
    if not rows or not rows[0] or rows[0][0] != SCOPE:
        return None
    r = rows[0] + [""] * 4
    try:
        wm = max(1, int(r[2]))
    except (TypeError, ValueError):
        raise SheetsError(f"bad watermark in {SUMMARY_TAB}: {r[2]!r}")
    return {"summary": r[1], "watermark": wm, "updated_at": r[3]}


def _write_state(summary: str, watermark: int) -> dict:
    _values_update(
        f"{SUMMARY_TAB}!A2:D2",
        [[SCOPE, summary, watermark, _now_str()]],
    )
    state = {"summary": summary, "watermark": watermark, "updated_at": _now_str()}
    with _cache_lock:
        _cache["state"], _cache["ts"] = state, time.time()
    return state


def _init_state() -> dict:
    """第一次啟用：watermark 放在『最近 BOOTSTRAP_ROWS 列』之前，避免一次吞整份歷史。"""
    col = _values_get(f"{LOG_TAB}!A:A")
    total = len(col)  # 含表頭
    wm = max(1, total - BOOTSTRAP_ROWS)
    logger.info("memory_store: init state, log rows=%s watermark=%s", total, wm)
    return _write_state("", wm)


def get_state(refresh: bool = False) -> dict:
    with _cache_lock:
        st, ts = _cache["state"], _cache["ts"]
    if st is not None and not refresh and time.time() - ts < STATE_TTL:
        return st
    st = _read_state()
    if st is None:
        st = _init_state()
    with _cache_lock:
        _cache["state"], _cache["ts"] = st, time.time()
    return st


def reset_cache() -> None:
    with _cache_lock:
        _cache["state"], _cache["ts"] = None, 0.0


def fetch_tail(watermark: int, limit: int = MAX_TAIL_READ) -> list:
    """watermark 之後的原文，回傳 [(member, message), ...]"""
    start = watermark + 1
    rows = _values_get(f"{LOG_TAB}!A{start}:C{start + limit - 1}")
    out = []
    for r in rows:
        if len(r) >= 3:
            out.append((r[1], r[2]))
    return out


# ─── Read path：給 handle_mention 用 ──────────────────────

def _fmt(lines) -> str:
    return "\n".join(f"{n}：{str(m)[:MSG_CLIP]}" for n, m in lines)


def build_context() -> str:
    """摘要 + 最近原文。Sheets 失敗時退回本機 SQLite 的最近 8 則（舊行為）。"""
    try:
        st = get_state()
        tail = fetch_tail(st["watermark"])[-CONTEXT_TAIL_LINES:]
        parts = []
        if st["summary"]:
            parts.append("【較早的對話摘要】\n" + st["summary"])
        if tail:
            parts.append("【最近聊天記錄】\n" + _fmt(tail))
        return "\n\n".join(parts)
    except Exception as exc:
        logger.warning("memory_store.build_context fallback to local: %s", exc)
        try:
            from state import chat_get
            recent = chat_get(8)
            return ("【最近聊天記錄】\n" + _fmt(recent)) if recent else ""
        except Exception:
            return ""


# ─── Compact ──────────────────────────────────────────────

def _default_llm(prompt: str):
    from utils import call_ai
    return call_ai(prompt)


def _build_prompt(old_summary: str, lines: list) -> str:
    return (
        "你是朋友群聊天機器人「小棉襖」的記憶整理員。\n"
        "請把【舊摘要】和【新對話】合併成一份新的記憶摘要，繁體中文，條列式，"
        "600 字以內。保留：誰做了什麼、計畫與決定、每個人的近況／喜好／困擾、"
        "重要的人名與日期、群組內的梗。刪掉：寒暄、重複、指令操作。"
        "只輸出摘要本身，不要加前言。\n\n"
        f"【舊摘要】\n{old_summary or '（無）'}\n\n"
        f"【新對話】\n{_fmt(lines)}"
    )


def _pick_batch(old_summary: str, rows: list) -> list:
    """從 rows 前面取一批，讓整個 prompt 不超過字數上限（至少 1 列）。"""
    budget = MAX_BATCH_CHARS - len(old_summary) - 400
    picked, used = [], 0
    for n, m in rows[:MAX_BATCH_ROWS]:
        cost = len(n) + min(len(str(m)), MSG_CLIP) + 2
        if picked and used + cost > budget:
            break
        picked.append((n, m))
        used += cost
    return picked


def compact_if_needed(llm=None) -> str:
    """
    回傳狀態字串：skipped / compacted / llm_failed / busy / error。
    任何失敗都不會動 watermark，也不會刪原文。
    """
    llm = llm or _default_llm
    if not _compact_lock.acquire(blocking=False):
        return "busy"
    try:
        st = get_state(refresh=True)
        tail = fetch_tail(st["watermark"])
        if len(tail) <= THRESHOLD:
            return "skipped"
        candidates = tail[: len(tail) - KEEP_RECENT]
        batch = _pick_batch(st["summary"], candidates)
        new_summary = None
        try:
            new_summary = llm(_build_prompt(st["summary"], batch))
        except Exception as exc:
            logger.warning("memory_store: llm raised %s: %s", type(exc).__name__, exc)
        new_summary = (new_summary or "").strip()
        if len(new_summary) < 10:
            return _on_fail("llm returned empty/too short: %r" % new_summary[:80])
        if len(new_summary) > MAX_SUMMARY_CHARS:
            logger.warning("memory_store: summary %s chars, truncating", len(new_summary))
            new_summary = new_summary[:MAX_SUMMARY_CHARS]
        try:
            _write_state(new_summary, st["watermark"] + len(batch))
        except Exception as exc:
            return _on_fail(f"write state failed: {exc}", status="error")
        _counter["fails"] = 0
        logger.info("memory_store: compacted %s rows, watermark %s -> %s",
                    len(batch), st["watermark"], st["watermark"] + len(batch))
        return "compacted"
    except Exception as exc:
        return _on_fail(f"{type(exc).__name__}: {exc}", status="error")
    finally:
        _compact_lock.release()


def _on_fail(detail: str, status: str = "llm_failed") -> str:
    _counter["fails"] += 1
    logger.warning("memory_store compact %s (consecutive=%s): %s", status, _counter["fails"], detail)
    if _counter["fails"] == FAIL_ALERT_AFTER:
        try:
            from utils import send_telegram_alert
            send_telegram_alert(f"對話記憶 compact 連續失敗 {FAIL_ALERT_AFTER} 次（原文未丟失）：{detail[:300]}")
        except Exception:
            pass
    return status


def log_and_compact(member: str, text: str) -> None:
    """取代 commands.py 裡的 log_chat_message 背景任務：先寫原文，每 N 則檢查一次 compact。"""
    from goal_tracker import log_chat_message
    log_chat_message(member, text)
    _counter["n"] += 1
    if _counter["n"] % CHECK_EVERY == 1:
        try:
            compact_if_needed()
        except Exception as exc:
            logger.warning("memory_store.log_and_compact: %s", exc)
