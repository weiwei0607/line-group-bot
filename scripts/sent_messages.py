"""
記住「小棉襖自己送出的訊息 id」，用來判斷使用者是不是在引用（回覆）小棉襖。

LINE 官方文件（Messaging API reference）：
  - 收到的 text message event，使用者引用某則訊息時會帶 message.quotedMessageId（String，
    「Only included when the received message quotes a past message」）。SDK 欄位：quoted_message_id。
  - reply／push 成功回 200 與 {"sentMessages": [{"id": <Number，範例為字串>, "quoteToken": ...}]}，
    陣列順序對應送出的 messages。

儲存：
  - 記憶體 OrderedDict（最近 RING_SIZE 筆）＋ Google Sheet 分頁「bot訊息」環狀緩衝（同一份試算表、不新增服務）。
    欄位：seq | message_id | group_id | text(前200字) | sent_at。第 N 筆寫在第 ((N-1) % 200)+2 列，舊的自動被覆蓋。
  - 重啟（Render 暫時磁碟／休眠）後記憶體清空，靠 Sheet 查。
  - 寫入在背景 thread，任何失敗只 log，絕不影響正常回覆。
"""

import contextvars
import logging
import threading
import time
from collections import OrderedDict

logger = logging.getLogger(__name__)

TAB = "bot訊息"
RING_SIZE = 200
TEXT_CLIP = 200
LOOKUP_CACHE_TTL = 60
ASYNC_WRITE = True  # 測試時關掉，改同步寫

# handle_message 設定目前事件所屬群組；reply API 回應本身不帶群組 id
current_group = contextvars.ContextVar("sent_messages_group", default=None)

_mem = OrderedDict()   # message_id(str) -> {"group","text","ts"}
_lock = threading.Lock()
_seq = {"n": None}
_sheet_cache = {"ts": 0.0}


def reset() -> None:
    with _lock:
        _mem.clear()
        _seq["n"] = None
        _sheet_cache["ts"] = 0.0


def _ms():
    import memory_store
    return memory_store


def _now() -> str:
    try:
        return _ms()._now_str()
    except Exception:
        return time.strftime("%Y-%m-%d %H:%M")


def _ensure_tab() -> None:
    ms = _ms()
    try:
        ms._request(
            "POST",
            f"https://sheets.googleapis.com/v4/spreadsheets/{ms._sheet_id()}:batchUpdate",
            json={"requests": [{"addSheet": {"properties": {"title": TAB}}}]},
        )
    except ms.SheetsError as exc:
        if "already exists" not in str(exc):
            raise
    ms._values_update(f"{TAB}!A1:E1", [["seq", "message_id", "group_id", "text", "sent_at"]])


def _remember(mid: str, group, text: str, ts: str) -> None:
    with _lock:
        _mem.pop(mid, None)
        _mem[mid] = {"group": group, "text": text, "ts": ts}
        while len(_mem) > RING_SIZE:
            _mem.popitem(last=False)


def record(items, group_id=None) -> None:
    """items: [(message_id, text), ...]。立刻進記憶體，Sheet 在背景寫。永不 raise。"""
    try:
        group = group_id or current_group.get()
        if not group:
            import os
            group = os.environ.get("LINE_GROUP_ID", "") or None
        entries = []
        for mid, text in items:
            if mid in (None, ""):
                continue
            mid, text, ts = str(mid), str(text or "")[:TEXT_CLIP], _now()
            _remember(mid, group, text, ts)
            entries.append((mid, group or "", text, ts))
        if not entries:
            return
        if ASYNC_WRITE:
            threading.Thread(target=_persist, args=(entries,), daemon=True).start()
        else:
            _persist(entries)
    except Exception as exc:
        logger.warning("sent_messages.record failed: %s", exc)


def _persist(entries) -> None:
    try:
        ms = _ms()
        with _lock:
            if _seq["n"] is None:
                try:
                    col = ms._values_get(f"{TAB}!A2:A{RING_SIZE + 1}")
                except ms.SheetsError as exc:
                    if "HTTP 400" in str(exc) or "Unable to parse range" in str(exc):
                        _ensure_tab()
                        col = []
                    else:
                        raise
                nums = [int(r[0]) for r in col if r and str(r[0]).isdigit()]
                _seq["n"] = max(nums) if nums else 0
            for mid, group, text, ts in entries:
                _seq["n"] += 1
                n = _seq["n"]
                row = (n - 1) % RING_SIZE + 2
                ms._values_update(f"{TAB}!A{row}:E{row}", [[n, mid, group, text, ts]])
    except Exception as exc:
        logger.warning("sent_messages persist failed (reply unaffected): %s", exc)


def lookup(message_id, group_id=None):
    """回傳 {"group","text","ts"}（這是小棉襖送出的）或 None（不是／查不到／Sheet 失敗）。永不 raise。"""
    try:
        if not isinstance(message_id, str) or not message_id:
            return None
        entry = _mem.get(message_id)
        if entry is None and time.time() - _sheet_cache["ts"] > LOOKUP_CACHE_TTL:
            _sheet_cache["ts"] = time.time()  # 失敗也算，避免每則引用都打 Sheet
            ms = _ms()
            for r in ms._values_get(f"{TAB}!A2:E{RING_SIZE + 1}"):
                if len(r) >= 4 and r[1]:
                    _remember(str(r[1]), r[2] or None, r[3], r[4] if len(r) > 4 else "")
            entry = _mem.get(message_id)
        if entry is None:
            return None
        if group_id and entry.get("group") and entry["group"] != group_id:
            return None
        return entry
    except Exception as exc:
        logger.warning("sent_messages.lookup failed: %s", exc)
        return None


def record_from_response(resp, messages, group_id=None) -> None:
    """從 reply／push 的 HTTP 回應取 sentMessages（與 messages 同順序）並記錄。永不 raise。"""
    try:
        data = resp.json()
        sent = data.get("sentMessages") if isinstance(data, dict) else None
        if not isinstance(sent, list):
            return
        items = []
        for m, s in zip(messages, sent):
            if not isinstance(s, dict):
                continue
            if isinstance(m, dict):
                mtype, text = m.get("type"), m.get("text")
            else:
                mtype, text = getattr(m, "type", None), getattr(m, "text", None)
            items.append((s.get("id"), text if mtype == "text" and text else f"[{mtype or 'message'}]"))
        record(items, group_id)
    except Exception as exc:
        logger.debug("record_from_response skipped: %s", exc)
