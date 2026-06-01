"""Audio (Shazam) and image (NSFW + remove-bg) handlers."""

import logging
import threading
from linebot.v3.messaging import (
    ApiClient, MessagingApi, MessagingApiBlob,
    ReplyMessageRequest, TextMessage,
)
from state import remove_bg_get, remove_bg_set
from utils import send_telegram_alert


def handle_audio(event, configuration):
    """Shazam: identify song from audio message."""
    reply_token = event.reply_token
    with ApiClient(configuration) as api_client:
        try:
            blob_api = MessagingApiBlob(api_client)
            audio_bytes = blob_api.get_message_content(event.message.id)
        except Exception as exc:
            logging.warning("get_message_content error: %s", exc)
            send_telegram_alert(f"get_message_content (audio) error: {exc}")
            return
        MessagingApi(api_client).reply_message(
            ReplyMessageRequest(
                reply_token=reply_token,
                messages=[TextMessage(text="🎵 辨識中，請稍候...")],
            )
        )

    def _run():
        from api_helpers import shazam_identify
        from commands import _push_messages
        result = shazam_identify(audio_bytes)
        _push_messages([{"type": "text", "text": result}])

    threading.Thread(target=_run, daemon=True).start()


def handle_image(event, configuration):
    """NSFW detection + background removal for image messages."""
    reply_token = event.reply_token
    user_id = event.source.user_id if hasattr(event.source, "user_id") else None

    from state import rate_limit_check
    if user_id and not rate_limit_check(user_id, max_requests=10, window_seconds=60):
        with ApiClient(configuration) as api_client:
            api_client.default_api.reply_message(
                ReplyMessageRequest(
                    reply_token=reply_token,
                    messages=[TextMessage(text="⏳ 圖片發太快了，請稍後再試 👋")],
                )
            )
        return

    with ApiClient(configuration) as api_client:
        try:
            blob_api = MessagingApiBlob(api_client)
            img_bytes = blob_api.get_message_content(event.message.id)
        except Exception as exc:
            logging.warning("get_message_content error: %s", exc)
            send_telegram_alert(f"get_message_content (image) error: {exc}")
            return

        line_api = MessagingApi(api_client)

        # ── 去背 pending flow ──
        if user_id and remove_bg_get(user_id):
            remove_bg_set(user_id, False)
            line_api.reply_message(
                ReplyMessageRequest(
                    reply_token=reply_token,
                    messages=[TextMessage(text="🖼️ 去背處理中，請稍候...")],
                )
            )

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
            line_api.reply_message(
                ReplyMessageRequest(
                    reply_token=reply_token,
                    messages=[TextMessage(text="⚠️ 偵測到不雅圖片，小棉襖不允許這種內容喔！")],
                )
            )
