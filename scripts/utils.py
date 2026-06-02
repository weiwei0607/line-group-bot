"""
LINE Group Bot — Shared utilities.
集中管理跨檔案重複的 helper，減少 DRY 違規。
"""

import os
import re
import requests
import logging

# ─── Input sanitization ───────────────────────────────────

from shared.security import sanitize_input


# ─── Retry helper ─────────────────────────────────────────

from shared.retry import retry_http


def _retry_http(fn, max_retries=3, backoff=2):
    return retry_http(max_retries=max_retries, backoff=backoff)(fn)()


# ─── Groq ─────────────────────────────────────────────────

def call_groq(prompt: str) -> str | None:
    """呼叫 Groq (llama-3.3-70b-versatile)，回傳文字或 None。"""
    key = os.environ.get("GROQ_API_KEY", "")
    if not key:
        return None
    prompt = sanitize_input(prompt)
    try:
        resp = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": prompt}], "max_tokens": 500},
            timeout=10,
        )
        return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception:
        return None


def call_ai(prompt: str) -> str | None:
    """Groq 優先，失敗時 fallback 到 Gemini。"""
    result = call_groq(prompt)
    if result:
        return result
    return call_gemini(prompt)


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
            resp.raise_for_status()
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

from shared.alerts import send_telegram_alert as _send_telegram_alert_raw


def send_telegram_alert(msg: str) -> None:
    """發送 Telegram 告警給管理員"""
    return _send_telegram_alert_raw(msg, prefix="🚨 Bot Alert")
