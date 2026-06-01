"""
Shared alert utilities for both bots.
Telegram alerts, logging helpers.
"""

import logging
import os
import requests

logger = logging.getLogger(__name__)


def send_telegram_alert(msg: str, prefix: str = "🚨 Bot Alert") -> None:
    """Send a Telegram alert to the admin chat."""
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not bot_token or not chat_id:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            data={"chat_id": chat_id, "text": f"{prefix}\n\n{msg}"[:4000]},
            timeout=10,
        )
    except Exception as exc:
        logger.warning("send_telegram_alert: %s", exc)
