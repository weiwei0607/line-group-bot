"""Audio (Shazam) and image (NSFW + remove-bg) handlers."""

import os
import logging
import threading
import requests as _requests
from state import remove_bg_get, remove_bg_set
from utils import send_telegram_alert


def _download_content(message_id: str) -> bytes | None:
    """Download message content from LINE using requests (avoids urllib3 hang)."""
    token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
    try:
        resp = _requests.get(
            f"https://api-data.line.me/v2/bot/message/{message_id}/content",
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.content
    except Exception as exc:
        logging.warning("download_content error: %s", exc)
        return None


def handle_audio(event, configuration):
    """Shazam: identify song from audio message."""
    from line_push import reply_text
    reply_token = event.reply_token

    audio_bytes = _download_content(event.message.id)
    if not audio_bytes:
        send_telegram_alert(f"get_message_content (audio) failed for {event.message.id}")
        return

    reply_text(reply_token, "🎵 辨識中，請稍候...")

    def _run():
        from api_helpers import shazam_identify
        from commands import _push_messages
        result = shazam_identify(audio_bytes)
        _push_messages([{"type": "text", "text": result}])

    threading.Thread(target=_run, daemon=True).start()


def handle_image(event, configuration):
    """NSFW detection + background removal for image messages."""
    from line_push import reply_text
    reply_token = event.reply_token
    user_id = event.source.user_id if hasattr(event.source, "user_id") else None

    from state import rate_limit_check
    if user_id and not rate_limit_check(user_id, max_requests=10, window_seconds=60):
        reply_text(reply_token, "⏳ 圖片發太快了，請稍後再試 👋")
        return

    img_bytes = _download_content(event.message.id)
    if not img_bytes:
        send_telegram_alert(f"get_message_content (image) failed for {event.message.id}")
        return

    # ── 去背 pending flow ──
    if user_id and remove_bg_get(user_id):
        remove_bg_set(user_id, False)
        reply_text(reply_token, "🖼️ 去背處理中，請稍候...")

        def _do_remove_bg():
            from api_helpers import remove_background
            from commands import _push_messages
            result_url = remove_background(img_bytes)
            if result_url:
                _push_messages([
                    {"type": "text", "text": "✅ 去背完成！"},
                    {"type": "image",
                     "originalContentUrl": result_url,
                     "previewImageUrl": result_url},
                ])
            else:
                _push_messages([{"type": "text", "text": "去背失敗，請稍後再試 😢"}])

        threading.Thread(target=_do_remove_bg, daemon=True).start()
        return

    # ── NSFW 自動偵測 ──
    try:
        from api_helpers import check_nsfw
        is_nsfw = check_nsfw(img_bytes)
    except Exception:
        is_nsfw = False

    if is_nsfw:
        reply_text(reply_token, "⚠️ 偵測到不雅圖片，小棉襖不允許這種內容喔！")
