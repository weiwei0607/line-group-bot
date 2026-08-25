import os
import logging
import json
import time
import uuid
import threading
from datetime import datetime, timedelta
from collections import defaultdict
from goal_tracker import TW_TZ

class _JsonFormatter(logging.Formatter):
    def format(self, record):
        obj = {
            "ts": datetime.fromtimestamp(record.created, TW_TZ).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if hasattr(record, "req_id"):
            obj["req_id"] = record.req_id
        if record.exc_info:
            obj["exc"] = self.formatException(record.exc_info)
        return json.dumps(obj, ensure_ascii=False, default=str)

_handler = logging.StreamHandler()
_handler.setFormatter(_JsonFormatter())
logging.basicConfig(level=logging.INFO, handlers=[_handler])

# ── Performance metrics ───────────────────────────────────────────
class _Metrics:
    def __init__(self, max_samples=5000):
        self._lock = threading.Lock()
        self._max = max_samples
        self._data = defaultdict(list)  # endpoint -> [dur_ms, ...]

    def record(self, endpoint: str, dur_ms: float):
        with self._lock:
            arr = self._data[endpoint]
            arr.append(dur_ms)
            if len(arr) > self._max:
                self._data[endpoint] = arr[-self._max:]

    def snapshot(self):
        with self._lock:
            out = {}
            for ep, arr in self._data.items():
                s = sorted(arr)
                n = len(s)
                out[ep] = {
                    "n": n,
                    "p50": s[n // 2] if n else 0,
                    "p95": s[int(n * 0.95)] if n else 0,
                    "p99": s[int(n * 0.99)] if n else 0,
                    "max": s[-1] if n else 0,
                }
            return out

_METRICS = _Metrics()
from flask import Flask, request, abort, g, Response
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

from config import CHANNEL_SECRET, CHANNEL_ACCESS_TOKEN, assert_web_env
assert_web_env()
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
    extra = {"req_id": g.request_id}
    logger.info(
        "[req:%s] %s %s -> %d in %.1fms",
        g.request_id, request.method, request.path,
        response.status_code, duration_ms,
        extra=extra,
    )
    endpoint = f"{request.method} {request.path}"
    _METRICS.record(endpoint, duration_ms)
    return response

def _verify_line_signature(body: str, signature: str) -> bool:
    import hmac, hashlib, base64
    secret = os.environ.get("LINE_CHANNEL_SECRET", "")
    if not secret or not signature:
        return False
    expected = base64.b64encode(
        hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest()
    ).decode()
    return hmac.compare_digest(signature, expected)


def _dispatch_callback(body: str, signature: str):
    try:
        handler.handle(body, signature)
    except Exception as exc:
        import traceback
        logging.error("Callback processing error: %s", exc)
        send_telegram_alert(f"webhook處理失敗：{type(exc).__name__}: {str(exc)[:200]}\n{traceback.format_exc()[-300:]}")


@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers.get("X-Line-Signature", "")
    body = request.get_data(as_text=True)

    if not _verify_line_signature(body, signature):
        return "Invalid signature", 400

    # Deduplication
    import hashlib
    from state import kv_get, kv_set
    dedup_key = f"webhook:{hashlib.sha256(body.encode()).hexdigest()[:32]}"
    if kv_get(dedup_key):
        return "OK", 200
    kv_set(dedup_key, True, ttl_seconds=60)

    # Return 200 immediately, process in background
    threading.Thread(target=_dispatch_callback, args=(body, signature), daemon=True).start()
    return "OK"

@app.route("/")
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

    # 4. TTS (edge-tts) availability
    try:
        import edge_tts
        checks["edge_tts"] = "ok"
    except Exception as e:
        checks["edge_tts"] = f"fail: {e}"

    # 5. RENDER_EXTERNAL_URL
    checks["render_url"] = os.environ.get("RENDER_EXTERNAL_URL", "NOT_SET")

    all_ok = all(v == "ok" for k, v in checks.items() if isinstance(v, str) and k != "render_url")
    status = 200 if all_ok else 503
    return checks, status

@app.route("/version")
def version():
    return {"version": "mp3-fix-20250602"}


@app.route("/daily_push", methods=["POST"])
def daily_push():
    """早安推播，由 GitHub Actions 每天 07:50 呼叫"""
    cron_secret = os.environ.get("CRON_SECRET", "")
    token = request.headers.get("Authorization", "").replace("Bearer ", "").strip()
    if not cron_secret or not token or token != cron_secret:
        abort(403)

    from weather import send_morning_greeting
    try:
        send_morning_greeting()
    except Exception as e:
        logger.error("daily_push error: %s", e)
        return str(e), 500
    return "OK"


def _debug_guard():
    """除錯端點的門禁。

    /test-push-audio 會真的推播到 LINE 群、/test-ffmpeg 會開 subprocess，
    先前兩個都沒有任何驗證，知道 Render 網址的人就能觸發。
    現在要帶 ?key= 且對得上 DEBUG_ENDPOINT_KEY 才放行；
    沒設這個環境變數就等於整組關閉。
    """
    expected = os.environ.get("DEBUG_ENDPOINT_KEY", "")
    if not expected:
        abort(404)
    if request.args.get("key", "") != expected:
        abort(404)


@app.route("/test-push-audio")
def test_push_audio():
    """Push a pre-generated standard 44.1kHz MP3 to the group to test if AudioMessage works in push."""
    _debug_guard()
    try:
        from line_push import push_messages
        from linebot.v3.messaging import AudioMessage
        import os
        base_url = os.environ.get("RENDER_EXTERNAL_URL", "")
        target = os.environ.get("LINE_GROUP_ID", "")
        if not base_url:
            return {"error": "RENDER_EXTERNAL_URL not set"}, 500
        if not target:
            return {"error": "LINE_GROUP_ID not set"}, 500
        audio_url = f"{base_url}/static/test_44k.mp3"
        push_messages(target, [AudioMessage(original_content_url=audio_url, duration=2000)])
        return {"ok": True, "audio_url": audio_url, "target": target[:30]}
    except Exception as exc:
        import traceback
        return {"error": str(exc), "traceback": traceback.format_exc()}, 500


@app.route("/test-tts")
def test_tts():
    """Directly test edge-tts generation without going through LINE webhook."""
    try:
        from api_helpers import text_to_speech, save_tts_audio
        result = text_to_speech("測試語音", "zh-TW")
        if not result:
            return {"error": "text_to_speech returned None (edge-tts may not be installed)"}, 500
        audio_bytes, mime = result
        fname = save_tts_audio(audio_bytes, mime)
        base_url = os.environ.get("RENDER_EXTERNAL_URL", "")
        return {
            "ok": True,
            "filename": fname,
            "audio_url": f"{base_url}/tts/{fname}" if base_url else None,
            "render_url_set": bool(base_url),
        }
    except Exception as exc:
        import traceback
        return {"error": str(exc), "traceback": traceback.format_exc()}, 500


@app.route("/test-ffmpeg")
def test_ffmpeg():
    """Test ffmpeg availability and conversion on Render."""
    _debug_guard()
    try:
        import subprocess, imageio_ffmpeg, os, tempfile
        ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()
        # Create a small dummy audio (1kHz sine wave, 1 sec, 24kHz)
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            wav_path = f.name
        # Generate raw PCM
        import math, struct
        sample_rate = 24000
        duration = 1
        samples = [int(32767 * math.sin(2 * math.pi * 1000 * t / sample_rate)) for t in range(sample_rate * duration)]
        with open(wav_path, "wb") as f:
            f.write(b'RIFF')
            f.write(struct.pack('<I', 36 + len(samples) * 2))
            f.write(b'WAVEfmt ')
            f.write(struct.pack('<IHHIIHH', 16, 1, 1, sample_rate, sample_rate * 2, 2, 16))
            f.write(b'data')
            f.write(struct.pack('<I', len(samples) * 2))
            for s in samples:
                f.write(struct.pack('<h', s))
        mp3_path = wav_path.replace(".wav", ".mp3")
        result = subprocess.run(
            [ffmpeg_path, "-y", "-i", wav_path, "-codec:a", "libmp3lame", "-ar", "44100", "-b:a", "128k", mp3_path],
            capture_output=True, timeout=15,
        )
        try: os.remove(wav_path)
        except OSError: pass
        if result.returncode == 0 and os.path.exists(mp3_path):
            with open(mp3_path, "rb") as f:
                data = f.read()
            try: os.remove(mp3_path)
            except OSError: pass
            # Check MPEG version
            import struct
            frame_start = 0
            if data[:3] == b'ID3':
                id3_size = struct.unpack('>I', b'\x00' + data[6:9])[0]
                id3_size = ((id3_size & 0x7f000000) >> 3) | ((id3_size & 0x007f0000) >> 2) | ((id3_size & 0x00007f00) >> 1) | (id3_size & 0x0000007f)
                frame_start = 10 + id3_size
            mpeg_ver = "unknown"
            sample_rate_out = 0
            if len(data) > frame_start + 4:
                hdr = data[frame_start:frame_start+4]
                if hdr[0] == 0xff and (hdr[1] & 0xe0) == 0xe0:
                    mpeg_ver = {0: "2.5", 1: "reserved", 2: "2", 3: "1"}.get((hdr[1] >> 3) & 0x03, "unknown")
                    sr_idx = (hdr[2] >> 2) & 0x03
                    sr_list = {"1": [44100, 48000, 32000], "2": [22050, 24000, 16000], "2.5": [11025, 12000, 8000]}.get(mpeg_ver, [0, 0, 0])
                    sample_rate_out = sr_list[sr_idx] if sr_idx < len(sr_list) else 0
            return {
                "ok": True,
                "ffmpeg_path": ffmpeg_path,
                "ffmpeg_exists": os.path.exists(ffmpeg_path),
                "mpeg_version": mpeg_ver,
                "sample_rate": sample_rate_out,
                "output_size": len(data),
            }
        else:
            stderr = result.stderr.decode('utf-8', errors='replace') if result.stderr else ""
            return {
                "ok": False,
                "ffmpeg_path": ffmpeg_path,
                "ffmpeg_exists": os.path.exists(ffmpeg_path),
                "returncode": result.returncode,
                "stderr": stderr[:500],
            }, 500
    except Exception as exc:
        import traceback
        return {"error": str(exc), "traceback": traceback.format_exc()}, 500


@app.route("/metrics")
def metrics():
    return {"metrics": _METRICS.snapshot()}

def handle_exception(e):
    import traceback
    logging.error("Unhandled exception: %s", e)
    traceback.print_exc()


# 佔住排程鎖的 socket。必須保持模組層級的參照，讓它跟著 process 活著。
_SCHEDULER_LOCK_SOCKET = None


def _is_scheduler_primary() -> bool:
    """用綁 localhost 埠的方式確保只有一個 process 會啟動排程。

    這個 socket 一定要一直開著。先前寫成 finally: s.close()，鎖只存在幾微秒，
    後面每個 process 都能重新綁成功，等於沒有鎖。目前 gunicorn 是 --workers 1
    所以還沒出事，但只要 worker 數量調大或新舊 process 短暫重疊，
    每個排程工作就會被執行多次（群組收到重複推播）。
    """
    global _SCHEDULER_LOCK_SOCKET
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 50000))
    except socket.error:
        s.close()
        return False
    _SCHEDULER_LOCK_SOCKET = s  # 故意不關，持有到 process 結束
    return True


def _start_scheduler():
    """行程內排程，預設關閉。

    這些工作先前全部是死的：每個都寫 `from state import cron_is_done`，
    但 state.py 根本沒有這些函式，一觸發就 ImportError（而且 import 在 try
    之外，連 Telegram 警報都發不出來），所以只是靜靜什麼都沒做。

    實際在跑的是 GitHub Actions（早安走 /daily-push，其餘走各自的 workflow）。
    因此這裡就算修好也不能直接打開，兩邊都跑會讓群組收到重複訊息。
    要啟用得先確認對應的 workflow 已停用，再設 ENABLE_INPROCESS_SCHEDULER=1。
    """
    if os.environ.get("ENABLE_INPROCESS_SCHEDULER", "") != "1":
        logging.info("In-process scheduler disabled (GitHub Actions handles cron)")
        return
    if not _is_scheduler_primary():
        logging.info("Scheduler skipped: another process already holds the lock")
        return
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.cron import CronTrigger
        import pytz
        tz = pytz.timezone("Asia/Taipei")
        scheduler = BackgroundScheduler(timezone=tz)
        def _safe_morning():
            from tts_store import cron_try_mark_done
            if not cron_try_mark_done("morning_greeting"):
                return
            try:
                send_morning_greeting()
            except Exception as e:
                send_telegram_alert(f"早安提醒失敗：{e}")
        scheduler.add_job(_safe_morning, CronTrigger(hour=8, minute=0, timezone=tz), misfire_grace_time=3600, max_instances=1, coalesce=True)

        def _weekly_sunday_push():
            from tts_store import cron_try_mark_done
            if not cron_try_mark_done("weekly_sunday_push"):
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
            except Exception as e:
                send_telegram_alert(f"週日推播失敗：{e}")

        scheduler.add_job(_weekly_sunday_push, CronTrigger(day_of_week="sun", hour=21, minute=0, timezone=tz), misfire_grace_time=3600, max_instances=1, coalesce=True)

        def _check_cycle_end():
            from tts_store import cron_try_mark_done
            if not cron_try_mark_done("check_cycle_end"):
                return
            try:
                from goal_tracker import get_cycle_info, build_summary_text
                _, day, total = get_cycle_info()
                if day == total:
                    summary = build_summary_text()
                    push_to_group(f"🎊 十日目標週期結束！\n\n{summary}")
            except Exception as e:
                send_telegram_alert(f"週期結束檢查失敗：{e}")

        scheduler.add_job(_check_cycle_end, CronTrigger(hour=22, minute=0, timezone=tz), misfire_grace_time=3600, max_instances=1, coalesce=True)

        def _silence_check():
            from tts_store import cron_try_mark_done
            if not cron_try_mark_done("silence_check"):
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
            except Exception as e:
                send_telegram_alert(f"沉默偵測失敗：{e}")

        scheduler.add_job(_silence_check, CronTrigger(hour=12, minute=0, timezone=tz), misfire_grace_time=3600, max_instances=1, coalesce=True)

        def _goal_reminder():
            from tts_store import cron_try_mark_done
            if not cron_try_mark_done("goal_reminder"):
                return
            try:
                from goal_reminder import main as goal_reminder_main
                goal_reminder_main()
            except Exception as e:
                send_telegram_alert(f"十日目標提醒失敗：{e}")

        scheduler.add_job(_goal_reminder, CronTrigger(hour=21, minute=0, timezone=tz), misfire_grace_time=3600, max_instances=1, coalesce=True)

        scheduler.start()
        logging.info("Scheduler started, all jobs scheduled")
        import atexit
        atexit.register(lambda: scheduler.shutdown(wait=False))
    except ImportError:
        logging.info("apscheduler not installed, skipping")
    except Exception as e:
        logging.error("Scheduler failed to start: %s", e)


# ─── TTS 語音檔案路由 ───
@app.route("/tts/<filename>")
def tts_file(filename):
    from api_helpers import get_tts_audio
    result = get_tts_audio(filename)
    if result is None:
        abort(404)
    data, mime = result
    return Response(data, mimetype=mime)


_start_scheduler()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, threaded=True)
