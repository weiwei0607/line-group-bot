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


# ─── LLM 共用：時間、告警去重 ─────────────────────────────

import time as _time
import threading as _threading
from datetime import datetime as _datetime, timezone as _timezone, timedelta as _timedelta

TW_TZ = _timezone(_timedelta(hours=8))
_WEEKDAYS = "一二三四五六日"


def taipei_now() -> _datetime:
    return _datetime.now(TW_TZ)


def today_line(now: _datetime | None = None) -> str:
    """給 prompt 用的『今天日期』一行（台北時區）。"""
    now = now or taipei_now()
    return f"{now.year}年{now.month}月{now.day}日 星期{_WEEKDAYS[now.weekday()]}（台北時間）"


_alert_lock = _threading.Lock()
_alert_last: dict = {}
ALERT_COOLDOWN = 30 * 60


def alert_once(key: str, msg: str, cooldown: int = ALERT_COOLDOWN) -> bool:
    """同一類告警 cooldown 秒內只發一次 Telegram。回傳這次有沒有發。"""
    now = _time.time()
    with _alert_lock:
        last = _alert_last.get(key)
        if last is not None and now - last < cooldown:
            return False
        _alert_last[key] = now
    send_telegram_alert(msg)
    return True


# ─── Groq ─────────────────────────────────────────────────

GROQ_MODEL = "openai/gpt-oss-120b"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
LLM_DEADLINE = 20  # call_ai 整條鏈（Groq→Gemini）的總時限（秒）


def _build_messages(prompt: str, system: str | None) -> list:
    msgs = []
    if system:
        msgs.append({"role": "system", "content": system})
    msgs.append({"role": "user", "content": prompt})
    return msgs


def call_groq(prompt: str, max_tokens: int = 800, system: str | None = None,
              timeout: int = 10) -> str | None:
    """呼叫 Groq (openai/gpt-oss-120b)，回傳文字或 None。

    不吞例外：每種失敗都 log（帶狀態碼與原始回應前 200 字）。
    - 429 / 其他 4xx：不重試。5xx / 逾時 / 連線錯誤：重試 1 次。
    - finish_reason == "length"（被截斷）或內容為空：視為失敗（回 None），
      避免半截回覆被當成完整答案（尤其是記憶摘要）。
    推理模型的思考 token 也算進 max_tokens，所以預設 800 並用 reasoning_effort=low。
    """
    key = os.environ.get("GROQ_API_KEY", "")
    if not key:
        return None
    prompt = sanitize_input(prompt)
    payload = {
        "model": GROQ_MODEL,
        "messages": _build_messages(prompt, system),
        "max_tokens": max_tokens,
        "reasoning_effort": "low",
    }
    for attempt in range(2):
        try:
            resp = requests.post(
                GROQ_URL,
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json=payload,
                timeout=timeout,
            )
        except (requests.Timeout, requests.ConnectionError) as exc:
            logging.warning("call_groq attempt %s network error: %s", attempt + 1, exc)
            if attempt == 0:
                continue
            alert_once("groq_net", f"Groq 連不上：{type(exc).__name__}: {str(exc)[:150]}")
            return None
        except Exception as exc:
            logging.warning("call_groq unexpected %s: %s", type(exc).__name__, exc)
            return None

        if not resp.ok:
            raw = (resp.text or "")[:200]
            logging.warning("call_groq HTTP %s: %s", resp.status_code, raw)
            if resp.status_code >= 500 and attempt == 0:
                continue
            if resp.status_code != 429:
                alert_once(f"groq_{resp.status_code}", f"Groq HTTP {resp.status_code}：{raw}")
            return None

        try:
            data = resp.json()
            choice = data["choices"][0]
            content = (choice.get("message") or {}).get("content")
            finish = choice.get("finish_reason")
        except Exception as exc:
            logging.warning("call_groq bad body (%s): %s", exc, (resp.text or "")[:200])
            alert_once("groq_body", f"Groq 回應格式異常：{(resp.text or '')[:200]}")
            return None
        if finish == "length":
            logging.warning("call_groq truncated (finish_reason=length, max_tokens=%s)", max_tokens)
            return None
        content = (content or "").strip()
        if not content:
            logging.warning("call_groq empty content (finish_reason=%s)", finish)
            return None
        return content
    return None


def call_ai(prompt: str, max_tokens: int = 800, system: str | None = None) -> str | None:
    """Groq 優先，失敗時 fallback 到 Gemini。整條鏈總時限 LLM_DEADLINE 秒。"""
    t0 = _time.time()
    result = call_groq(prompt, max_tokens=max_tokens, system=system)
    if result:
        return result
    remaining = LLM_DEADLINE - (_time.time() - t0)
    if remaining < 3:
        logging.warning("call_ai: deadline exhausted after Groq, skip Gemini")
        return None
    if system:
        prompt = f"{system}\n\n{prompt}"
    return call_gemini(prompt, timeout=int(min(12, remaining)))


# ─── Gemini ───────────────────────────────────────────────

def call_gemini(prompt: str, timeout: int = 12) -> str | None:
    """呼叫 Gemini 2.5 Flash，回傳文字或 None。

    最多 2 次嘗試，且只對 5xx／逾時／連線錯誤重試；429 與其他 4xx 直接放棄
    （額度用完重試只會更快燒光每分鐘額度）。失敗 log 帶原始回應，Telegram 告警 30 分鐘去重。
    """
    key = os.environ.get("GEMINI_API_KEY", "")
    if not key:
        return None
    prompt = sanitize_input(prompt)
    url = ("https://generativelanguage.googleapis.com/v1beta/"
           f"models/gemini-2.5-flash:generateContent?key={key}")
    for attempt in range(2):
        try:
            resp = requests.post(
                url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=timeout)
        except (requests.Timeout, requests.ConnectionError) as exc:
            logging.warning("call_gemini attempt %s network error: %s", attempt + 1, exc)
            if attempt == 0:
                _time.sleep(1)
                continue
            alert_once("gemini_net", f"call_gemini 連不上：{type(exc).__name__}: {str(exc)[:150]}")
            return None
        except Exception as exc:
            logging.warning("call_gemini unexpected %s: %s", type(exc).__name__, exc)
            return None

        if not resp.ok:
            raw = (resp.text or "")[:200]
            # URL 含金鑰，只 log 狀態碼與回應本文
            logging.warning("call_gemini HTTP %s: %s", resp.status_code, raw)
            if resp.status_code >= 500 and attempt == 0:
                _time.sleep(1)
                continue
            alert_once(f"gemini_{resp.status_code}", f"call_gemini HTTP {resp.status_code}：{raw}")
            return None

        try:
            data = resp.json()
            candidates = data.get("candidates", [])
            if not candidates:
                logging.warning("call_gemini no candidates: %s", (resp.text or "")[:200])
                return None
            cand = candidates[0]
            if cand.get("finishReason") == "MAX_TOKENS":
                logging.warning("call_gemini truncated (MAX_TOKENS)")
                return None
            parts = cand.get("content", {}).get("parts", [])
            if not parts:
                logging.warning("call_gemini no parts (finishReason=%s)", cand.get("finishReason"))
                return None
            return parts[0].get("text", "").strip() or None
        except Exception as exc:
            logging.warning("call_gemini bad body (%s): %s", exc, (resp.text or "")[:200])
            return None
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
        resp = _retry_http(
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
        logging.info("send_line_message: status=%s body=%s", resp.status_code, resp.text[:200])
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
