"""
沉默偵測 — 群組超過 48 小時沒人說話就發訊息活化氣氛
由 GitHub Actions 每 6 小時執行一次
"""

import os
import random
from datetime import datetime, timezone, timedelta
from goal_tracker import get_last_activity, TW_TZ
from utils import call_gemini, send_line_message

SILENCE_HOURS = int(os.environ.get("SILENCE_HOURS", "48"))


FALLBACK_MESSAGES = [
    "有人在嗎 👀 群組好安靜喔",
    "大家都去哪了 😢 說句話啊",
    "嗚嗚...好冷清，快來聊聊天 🥺",
    "敲敲門...有人嗎 🚪",
    "這個群組還活著嗎 😂",
]


def main():
    today = _now().strftime("%Y-%m-%d")
    from goal_tracker import get_setting, set_setting
    if get_setting("silence_check_last_run") == today:
        print(f"silence_check already ran on {today}, skipping.")
        return

    last = get_last_activity()
    now = datetime.now(TW_TZ)

    if last is None:
        print("No last_activity found, skipping")
        return

    hours_since = (now - last).total_seconds() / 3600
    print(f"Hours since last activity: {hours_since:.1f}")

    if hours_since < SILENCE_HOURS:
        print(f"Not silent enough ({hours_since:.1f}h < {SILENCE_HOURS}h), skipping")
        return

    msg = call_gemini(
        "你是一個活潑的朋友群 LINE 機器人，群組已經很久沒人說話了。"
        "請發一則有趣的訊息活化氣氛，台灣年輕人語氣，"
        "可以是話題、問題、或有趣事實，不超過 3 句。"
    ) or random.choice(FALLBACK_MESSAGES)

    send_line_message(msg)
    print(f"Sent silence breaker after {hours_since:.1f}h of silence")


    set_setting("silence_check_last_run", today)
if __name__ == "__main__":
    main()
