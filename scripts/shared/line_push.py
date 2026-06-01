"""
Shared LINE push/reply helpers (used by both group-bot and family-bot).
"""

import os
import logging
import requests
import time
from linebot.v3.messaging import (
    Configuration, ApiClient, MessagingApi,
    ReplyMessageRequest, TextMessage, ImageMessage, AudioMessage,
)

logger = logging.getLogger(__name__)

_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
_LINE_GROUP_ID = os.environ.get("LINE_GROUP_ID", "")

_configuration = None


def _get_configuration():
    global _configuration
    if _configuration is None:
        _configuration = Configuration(access_token=_CHANNEL_ACCESS_TOKEN)
    return _configuration


def reply_text(reply_token: str, text: str):
    try:
        cfg = _get_configuration()
        with ApiClient(cfg) as api_client:
            MessagingApi(api_client).reply_message(
                ReplyMessageRequest(
                    reply_token=reply_token,
                    messages=[TextMessage(text=text[:4900])],
                )
            )
    except Exception as exc:
        logger.warning("reply_text failed: %s", exc)


def reply_image(reply_token: str, image_url: str, fallback_text: str = "圖片發送失敗"):
    try:
        cfg = _get_configuration()
        with ApiClient(cfg) as api_client:
            MessagingApi(api_client).reply_message(
                ReplyMessageRequest(
                    reply_token=reply_token,
                    messages=[ImageMessage(
                        original_content_url=image_url,
                        preview_image_url=image_url,
                    )],
                )
            )
    except Exception as exc:
        logger.warning("reply_image failed: %s", exc)
        reply_text(reply_token, fallback_text)


def reply_audio(reply_token: str, audio_url: str, duration: int = 5000, fallback_text: str = "語音發送失敗"):
    try:
        cfg = _get_configuration()
        with ApiClient(cfg) as api_client:
            MessagingApi(api_client).reply_message(
                ReplyMessageRequest(
                    reply_token=reply_token,
                    messages=[AudioMessage(
                        original_content_url=audio_url,
                        duration=duration,
                    )],
                )
            )
    except Exception as exc:
        logger.warning("reply_audio failed: %s", exc)
        reply_text(reply_token, fallback_text)


def reply_image_with_text(reply_token: str, image_url: str, text: str):
    try:
        cfg = _get_configuration()
        with ApiClient(cfg) as api_client:
            MessagingApi(api_client).reply_message(
                ReplyMessageRequest(
                    reply_token=reply_token,
                    messages=[
                        ImageMessage(original_content_url=image_url, preview_image_url=image_url),
                        TextMessage(text=text[:4900]),
                    ],
                )
            )
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


def push_messages(to: str, messages: list):
    if not to or not messages:
        return
    try:
        _retry_http(
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
