"""
LINE Group Bot — Shared utilities.
集中管理跨檔案重複的 helper，減少 DRY 違規。
"""

import os
import requests

# ─── Gemini ───────────────────────────────────────────────

def call_gemini(prompt: str, timeout: int = 12) -> str | None:
    """呼叫 Gemini 2.5 Flash，回傳文字或 None"""
    key = os.environ.get("GEMINI_API_KEY", "")
    if not key:
        return None
    try:
        url = (
            "https://generativelanguage.googleapis.com/v1beta/"
            f"models/gemini-2.5-flash:generateContent?key={key}"
        )
        resp = requests.post(
            url,
            json={"contents": [{"parts": [{"text": prompt}]}]},
            timeout=timeout,
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
        print(f"[warn] call_gemini: {exc}")
        return None


# ─── LINE Push ────────────────────────────────────────────

def send_line_message(text: str, group_id: str | None = None) -> None:
    """推送文字訊息到指定群組（預設讀環境變數 LINE_GROUP_ID）"""
    token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
    gid = group_id or os.environ.get("LINE_GROUP_ID", "")
    if not token or not gid:
        print("[warn] send_line_message: missing token or group_id")
        return
    try:
        requests.post(
            "https://api.line.me/v2/bot/message/push",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json={"to": gid, "messages": [{"type": "text", "text": text}]},
            timeout=10,
        )
    except Exception as exc:
        print(f"[warn] send_line_message: {exc}")


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
        print(f"[warn] send_telegram_alert: {exc}")
