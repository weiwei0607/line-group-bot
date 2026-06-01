"""
LINE Group Bot — Shared utilities.
集中管理跨檔案重複的 helper，減少 DRY 違規。
"""

import os
import re
import requests
import logging

# ─── Input sanitization ───────────────────────────────────

_INJECTION_PATTERNS = [
    r"ignore\s+(all\s+)?previous\s+instructions",
    r"system\s*:\s*",
    r"you\s+are\s+now\s+",
    r"new\s+role\s*:\s*",
    r"<\|system\|>",
    r"<\|assistant\|>",
    r"<\|user\|>",
    r"\{\{.*\}\}",
    r"\[SYSTEM\s+",
    r" disregard ",
]
_INJECTION_RE = re.compile("|".join(_INJECTION_PATTERNS), re.IGNORECASE)
_MAX_PROMPT_LEN = 8000


def sanitize_input(text: str) -> str:
    """Strip control chars and flag obvious prompt-injection fragments."""
    if not isinstance(text, str):
        text = str(text)
    # Remove null bytes and most control chars (keep \n, \r, \t)
    text = "".join(ch for ch in text if ch == "\n" or ch == "\r" or ch == "\t" or (ord(ch) >= 32 and ord(ch) < 127) or ord(ch) > 127)
    # Truncate
    if len(text) > _MAX_PROMPT_LEN:
        text = text[:_MAX_PROMPT_LEN] + "\n...[truncated]"
    # Escape injection markers by breaking the pattern
    text = _INJECTION_RE.sub(lambda m: "🚫" + m.group(0)[1:], text)
    return text


# ─── Retry helper ─────────────────────────────────────────

def _retry_http(fn, max_retries=3, backoff=2):
    last_exc = None
    for attempt in range(max_retries):
        try:
            return fn()
        except (requests.ConnectionError, requests.Timeout) as exc:
            last_exc = exc
            if attempt < max_retries - 1:
                import time
                time.sleep(backoff ** attempt)
    raise last_exc


# ─── Gemini ───────────────────────────────────────────────

def call_gemini(prompt: str, timeout: int = 12) -> str | None:
    """呼叫 Gemini 2.5 Flash，回傳文字或 None（帶 3 次重試）"""
    key = os.environ.get("GEMINI_API_KEY", "")
    if not key:
        return None
    prompt = sanitize_input(prompt)
    for attempt in range(3):
        try:
            url = (
                "https://generativelanguage.googleapis.com/v1beta/"
                f"models/gemini-2.5-flash:generateContent?key={key}"
            )
            resp = _retry_http(
                lambda: requests.post(
                    url,
                    json={"contents": [{"parts": [{"text": prompt}]}]},
                    timeout=timeout,
                )
            )
            data = resp.json()
            candidates = data.get("candidates", [])
            if not candidates:
                return None
            parts = candidates[0].get("content", {}).get("parts", [])
            if not parts:
                return None
            return parts[0].get("text", "").strip()
        except Exception as exc:
            logging.warning("call_gemini attempt %s: %s", attempt + 1, exc)
            if attempt == 2:
                send_telegram_alert(f"call_gemini failed after 3 retries: {exc}")
                return None
            import time
            time.sleep(2 ** attempt)
    return None


# ─── LINE Push ────────────────────────────────────────────

def send_line_message(text: str, group_id: str | None = None, mentions: list | None = None) -> None:
    """推送文字訊息到指定群組（預設讀環境變數 LINE_GROUP_ID）
    mentions: [{"userId": "Uxxx", "index": int, "length": int}, ...]
    """
    token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
    gid = group_id or os.environ.get("LINE_GROUP_ID", "")
    if not token or not gid:
        logging.warning("send_line_message: missing token or group_id")
        return
    msg = {"type": "text", "text": text}
    if mentions:
        msg["mention"] = {"mentionees": mentions}
    try:
        _retry_http(
            lambda: requests.post(
                "https://api.line.me/v2/bot/message/push",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                json={"to": gid, "messages": [msg]},
                timeout=10,
            )
        )
    except Exception as exc:
        logging.warning("send_line_message: %s", exc)


def push_to_group(text: str) -> None:
    """send_line_message 的別名，相容舊程式碼"""
    send_line_message(text)


# ─── Telegram Alert ───────────────────────────────────────

def send_telegram_alert(msg: str) -> None:
    """發送 Telegram 告警給管理員"""
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not bot_token or not chat_id:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            data={"chat_id": chat_id, "text": f"🚨 Bot Alert\n\n{msg}"},
            timeout=10,
        )
    except Exception as exc:
        logging.warning("send_telegram_alert: %s", exc)
