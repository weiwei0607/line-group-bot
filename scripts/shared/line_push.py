"""
Shared LINE push/reply helpers (used by both group-bot and family-bot).
"""

import os
import logging
import requests
import time
from linebot.v3.messaging import Configuration

logger = logging.getLogger(__name__)

_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
_LINE_GROUP_ID = os.environ.get("LINE_GROUP_ID", "")

_configuration = None


def _get_configuration():
    global _configuration
    if _configuration is None:
        _configuration = Configuration(access_token=_CHANNEL_ACCESS_TOKEN)
    return _configuration


_alert_times: dict = {}
_ALERT_COOLDOWN = 30 * 60


def _alert_once(key: str, msg: str):
    """同一種失敗 30 分鐘內只發一次 Telegram，避免洗版。"""
    now = time.time()
    last = _alert_times.get(key)
    if last is not None and now - last < _ALERT_COOLDOWN:
        return
    _alert_times[key] = now
    _telegram_alert(msg)


def _reply_messages(reply_token: str, messages: list) -> bool:
    """Use requests directly to avoid urllib3 hanging issues in LINE Bot SDK.

    回傳 True＝LINE 接受（2xx）。非 2xx 會 log 狀態碼與回應前 200 字，並回 False
    （reply token 過期／失效時 LINE 回 400，以前這裡完全沒痕跡）。
    """
    if not reply_token or not messages:
        return False
    try:
        resp = _retry_http(
            lambda: requests.post(
                "https://api.line.me/v2/bot/message/reply",
                headers={
                    "Authorization": f"Bearer {_CHANNEL_ACCESS_TOKEN}",
                    "Content-Type": "application/json",
                },
                json={"replyToken": reply_token, "messages": messages},
                timeout=15,
            )
        )
    except Exception as exc:
        logger.warning("_reply_messages failed: %s", exc)
        raise
    if resp is not None and not (200 <= resp.status_code < 300):
        body = (getattr(resp, "text", "") or "")[:200]
        logger.warning("[LINE_REPLY] status=%s body=%s", resp.status_code, body)
        _alert_once(f"reply_{resp.status_code}", f"⚠️ LINE reply 失敗 HTTP {resp.status_code}：{body}")
        return False
    _record_sent(resp, messages, None)
    return True


def _record_sent(resp, messages, group_id):
    """把 bot 送出的 message id 記下來（用來判斷使用者是不是在引用小棉襖）。失敗不影響送訊。"""
    try:
        import sent_messages
        sent_messages.record_from_response(resp, messages, group_id)
    except Exception as exc:
        logger.debug("record sent messages skipped: %s", exc)


def reply_text(reply_token: str, text: str) -> bool:
    """回傳是否送出成功（呼叫端可據此決定要不要用 push 補送）。"""
    try:
        return bool(_reply_messages(reply_token, [{"type": "text", "text": text[:4900]}]))
    except Exception as exc:
        logger.warning("reply_text failed: %s", exc)
        return False


def reply_image(reply_token: str, image_url: str, fallback_text: str = "圖片發送失敗"):
    try:
        _reply_messages(reply_token, [{"type": "image", "originalContentUrl": image_url, "previewImageUrl": image_url}])
    except Exception as exc:
        logger.warning("reply_image failed: %s", exc)
        reply_text(reply_token, fallback_text)


def reply_audio(reply_token: str, audio_url: str, duration: int = 5000, fallback_text: str = "語音發送失敗"):
    try:
        _reply_messages(reply_token, [{"type": "audio", "originalContentUrl": audio_url, "duration": duration}])
    except Exception as exc:
        logger.warning("reply_audio failed: %s", exc)
        reply_text(reply_token, fallback_text)


def reply_image_with_text(reply_token: str, image_url: str, text: str):
    try:
        _reply_messages(reply_token, [
            {"type": "image", "originalContentUrl": image_url, "previewImageUrl": image_url},
            {"type": "text", "text": text[:4900]},
        ])
    except Exception as exc:
        logger.warning("reply_image_with_text failed: %s", exc)
        reply_text(reply_token, text)


def _retry_http(fn, max_retries=3, backoff=2):
    last_exc = None
    for attempt in range(max_retries):
        try:
            return fn()
        except (requests.ConnectionError, requests.Timeout) as exc:
            last_exc = exc
            if attempt < max_retries - 1:
                time.sleep(backoff ** attempt)
    raise last_exc


def _telegram_alert(msg: str):
    """Send alert to Telegram if credentials are available."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": msg},
            timeout=10,
        )
    except Exception:
        pass


def push_messages(to: str, messages: list):
    if not to or not messages:
        return
    try:
        resp = _retry_http(
            lambda: requests.post(
                "https://api.line.me/v2/bot/message/push",
                headers={
                    "Authorization": f"Bearer {_CHANNEL_ACCESS_TOKEN}",
                    "Content-Type": "application/json",
                },
                json={"to": to, "messages": messages},
                timeout=30,
            )
        )
        if resp is not None and resp.status_code not in (200, 201):
            short_to = to[:20] + "..."
            msg = f"[LINE_PUSH] status={resp.status_code} to={short_to}"
            logger.warning(msg)
            print(msg)
            if resp.status_code == 429:
                _telegram_alert(f"⚠️ LINE push quota 已用完（429）\nto={short_to}\n請至 LINE Console 確認本月用量")
            elif resp.status_code >= 500:
                _telegram_alert(f"⚠️ LINE API 伺服器錯誤（{resp.status_code}）\nto={short_to}")
        else:
            short_to = to[:20] + "..."
            logger.info("[LINE_PUSH] start to=%s len=%d", short_to, len(str(messages)))
            _record_sent(resp, messages, to if to.startswith("C") else None)
    except Exception as exc:
        logger.warning("push_messages failed: %s", exc)


def push_text(to: str, text: str):
    push_messages(to, [{"type": "text", "text": text}])


def push_text_to_group(text: str):
    """Push text to the default LINE_GROUP_ID."""
    if _LINE_GROUP_ID:
        push_text(_LINE_GROUP_ID, text)


def push_audio(to: str, audio_url: str, duration: int = 5000):
    push_messages(to, [{"type": "audio", "originalContentUrl": audio_url, "duration": duration}])
