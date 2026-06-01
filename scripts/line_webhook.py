import os
import logging
import time
import uuid
from datetime import datetime, timedelta
from goal_tracker import TW_TZ

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
from flask import Flask, request, abort, g
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.webhooks import (
    MessageEvent, TextMessageContent, MemberJoinedEvent,
    AudioMessageContent, ImageMessageContent,
)

from goal_tracker import (
    get_cycle_info, set_goals, get_goals, add_checkin,
    get_checkin_stats, get_checkin_log, get_today_checkins, build_summary_text,
    get_nickname, set_nickname, update_last_activity,
    log_chat_message, get_memories, get_streak,
    get_last_cycle_id, get_next_cycle_id, get_next_cycle_start,
    add_personal_memory, get_personal_memories,
    add_todo, get_todos, complete_todo_by_content,
    get_zodiac, set_zodiac, get_all_zodiacs, set_zodiac_by_nickname,
    add_quiz_score, get_quiz_scores, get_week_chat_logs,
    set_birthday_by_nickname, get_today_birthdays,
    get_last_activity,
    GOAL_SHEET_ID
)

from config import CHANNEL_SECRET, CHANNEL_ACCESS_TOKEN
from linebot.v3.messaging import Configuration

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 2 * 1024 * 1024  # 2MB max payload

handler = WebhookHandler(CHANNEL_SECRET)
configuration = Configuration(access_token=CHANNEL_ACCESS_TOKEN)

from api_helpers import *

# Import command handlers from commands module
from commands import handle_message, handle_audio, handle_image, handle_join
import logging
logger = logging.getLogger(__name__)

# Register handlers
handler.add(MessageEvent, message=TextMessageContent)(handle_message)
handler.add(MessageEvent, message=AudioMessageContent)(handle_audio)
handler.add(MessageEvent, message=ImageMessageContent)(handle_image)
handler.add(MemberJoinedEvent)(handle_join)


@app.before_request
def _before_request():
    g.request_id = uuid.uuid4().hex[:12]
    g.start_time = time.time()


@app.after_request
def _after_request(response):
    duration_ms = (time.time() - g.start_time) * 1000
    logger.info(
        "[req:%s] %s %s -> %d in %.1fms",
        g.request_id, request.method, request.path,
        response.status_code, duration_ms,
    )
    return response

@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers.get("X-Line-Signature", "")
    body = request.get_data(as_text=True)

    # Deduplication
    import hashlib
    from state import kv_get, kv_set
    dedup_key = f"webhook:{hashlib.sha256(body.encode()).hexdigest()[:32]}"
    if kv_get(dedup_key):
        return "OK", 200
    kv_set(dedup_key, True, ttl_seconds=60)

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return "OK"

@app.route("/health")
def health():
    checks = {}
    # 1. SQLite state DB
    try:
        from state import kv_get
        kv_get("__health__")
        checks["state_db"] = "ok"
    except Exception as e:
        checks["state_db"] = f"fail: {e}"

    # 2. LINE API connectivity (lightweight bot info check)
    try:
        import requests
        token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
        if token:
            r = requests.get(
                "https://api.line.me/v2/bot/info",
                headers={"Authorization": f"Bearer {token}"},
                timeout=5,
            )
            checks["line_api"] = "ok" if r.status_code == 200 else f"warn: {r.status_code}"
        else:
            checks["line_api"] = "skip: no token"
    except Exception as e:
        checks["line_api"] = f"fail: {e}"

    # 3. Google Sheets token refresh check
    try:
        from goal_tracker import _get_token
        _get_token()
        checks["sheets"] = "ok"
    except Exception as e:
        checks["sheets"] = f"fail: {e}"

    all_ok = all(v == "ok" for v in checks.values())
    status = 200 if all_ok else 503
    return checks, status

def handle_exception(e):
    import traceback
    logging.error("Unhandled exception: %s", e)
    traceback.print_exc()


def _start_scheduler():
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.cron import CronTrigger
        import pytz
        tz = pytz.timezone("Asia/Taipei")
        scheduler = BackgroundScheduler(timezone=tz)
        def _safe_morning():
            from state import cron_is_done, cron_mark_done
            if cron_is_done("morning_greeting"):
                return
            try:
                send_morning_greeting()
                cron_mark_done("morning_greeting")
            except Exception as e:
                send_telegram_alert(f"早安提醒失敗：{e}")
        scheduler.add_job(_safe_morning, CronTrigger(hour=8, minute=0, timezone=tz), misfire_grace_time=3600, max_instances=1, coalesce=True)

        def _weekly_sunday_push():
            from state import cron_is_done, cron_mark_done
            if cron_is_done("weekly_sunday_push"):
                return
            try:
                scores = get_quiz_scores()
                if scores:
                    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
                    medals = ["🥇", "🥈", "🥉"]
                    lines = ["🏆 本週答題積分結算！"]
                    for i, (nick, s) in enumerate(ranked):
                        lines.append(f"{medals[i] if i < 3 else '  '} {nick}：{s} 題")
                    push_to_group("\n".join(lines))

                logs = get_week_chat_logs()
                if logs:
                    sample = logs[-80:]
                    chat_text = "\n".join(f"{n}：{m}" for n, m in sample)
                    summary = call_gemini(
                        f"以下是朋友群這週的聊天記錄（節選）：\n{chat_text}\n\n"
                        "請用輕鬆幽默的語氣，100字以內，整理這週大家聊了什麼、有什麼有趣的事，繁體中文。"
                    )
                    if summary:
                        push_to_group(f"📝 本週群組回顧\n\n{summary}")
                cron_mark_done("weekly_sunday_push")
            except Exception as e:
                send_telegram_alert(f"週日推播失敗：{e}")

        scheduler.add_job(_weekly_sunday_push, CronTrigger(day_of_week="sun", hour=21, minute=0, timezone=tz), misfire_grace_time=3600, max_instances=1, coalesce=True)

        def _check_cycle_end():
            from state import cron_is_done, cron_mark_done
            if cron_is_done("check_cycle_end"):
                return
            try:
                from goal_tracker import get_cycle_info, build_summary_text
                _, day, total = get_cycle_info()
                if day == total:
                    summary = build_summary_text()
                    push_to_group(f"🎊 十日目標週期結束！\n\n{summary}")
                cron_mark_done("check_cycle_end")
            except Exception as e:
                send_telegram_alert(f"週期結束檢查失敗：{e}")

        scheduler.add_job(_check_cycle_end, CronTrigger(hour=22, minute=0, timezone=tz), misfire_grace_time=3600, max_instances=1, coalesce=True)

        def _silence_check():
            from state import cron_is_done, cron_mark_done
            if cron_is_done("silence_check"):
                return
            try:
                last = get_last_activity()
                if not last:
                    return
                from datetime import timedelta
                if datetime.now(TW_TZ) - last > timedelta(days=3):
                    msg = call_gemini(
                        "朋友群已經沉寂超過3天了，幫我寫一則輕鬆有趣的話來炒熱氣氛，"
                        "可以問問題或發起話題，繁體中文，不超過60字"
                    ) or "欸你們還活著嗎 👀 說點什麼吧！"
                    push_to_group(msg)
                cron_mark_done("silence_check")
            except Exception as e:
                send_telegram_alert(f"沉默偵測失敗：{e}")

        scheduler.add_job(_silence_check, CronTrigger(hour=12, minute=0, timezone=tz), misfire_grace_time=3600, max_instances=1, coalesce=True)

        scheduler.start()
        logging.info("Scheduler started, all jobs scheduled")
        import atexit
        atexit.register(lambda: scheduler.shutdown(wait=False))
    except ImportError:
        logging.info("apscheduler not installed, skipping")
    except Exception as e:
        logging.error("Scheduler failed to start: %s", e)


_start_scheduler()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, threaded=True)
