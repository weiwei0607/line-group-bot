"""
LINE Group Bot — Centralized configuration.
"""

from shared.config import env

# ─── LINE ─────────────────────────────────────────────────
CHANNEL_SECRET = env("LINE_CHANNEL_SECRET", "")
CHANNEL_ACCESS_TOKEN = env("LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_GROUP_ID = env("LINE_GROUP_ID", "")

# Validate required env vars — only enforce when running as web server
def assert_web_env():
    """Call this from the Flask app entry point, not from scripts."""
    _missing = [k for k, v in {
        "LINE_CHANNEL_SECRET": CHANNEL_SECRET,
        "LINE_CHANNEL_ACCESS_TOKEN": CHANNEL_ACCESS_TOKEN,
    }.items() if not v]
    if _missing:
        raise RuntimeError(f"Missing required env vars: {', '.join(_missing)}")

# ─── AI / APIs ────────────────────────────────────────────
GEMINI_API_KEY = env("GEMINI_API_KEY", "")
TMDB_API_KEY = env("TMDB_API_KEY", "")
NASA_API_KEY = env("NASA_API_KEY", "")
OWM_API_KEY = env("OWM_API_KEY", "")

# ─── RapidAPI (multi-key rotation) ────────────────────────
_RAPIDAPI_KEYS = [k for k in [
    env("RAPIDAPI_KEY", ""),
    env("RAPIDAPI_KEY_2", ""),
    env("RAPIDAPI_KEY_3", ""),
] if k]

# ─── API Ninjas (multi-key rotation) ──────────────────────
_NINJA_KEYS = [k for k in [
    env("NINJA_API_KEY", ""),
    env("NINJA_API_KEY_2", ""),
    env("NINJA_API_KEY_3", ""),
] if k]

# ─── Telegram alerts ─────────────────────────────────────
TELEGRAM_BOT_TOKEN = env("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = env("TELEGRAM_CHAT_ID", "")

# ─── Bot identity ─────────────────────────────────────────
BOT_NAME = env("LINE_BOT_NAME", "日文小老師")
BOT_DISPLAY_NAME = env("LINE_BOT_DISPLAY_NAME", "毛毛毛毛太后的小棉襖")
MEMBERS = ["太后", "毛毛", "二毛"]
_MEMBER_ZODIACS = {"太后": "雙子", "毛毛": "金牛", "二毛": "魔羯"}
_MEMBER_BIRTHDAYS = {"太后": "06-07", "毛毛": "04-25", "二毛": "01-04"}

# ─── Scheduler ────────────────────────────────────────────
DAILY_PUSH_HOUR = int(env("DAILY_PUSH_HOUR", "8"))
DAILY_PUSH_MINUTE = int(env("DAILY_PUSH_MINUTE", "0"))
CHECKIN_REMINDER_HOUR = int(env("CHECKIN_REMINDER_HOUR", "20"))
CHECKIN_REMINDER_MINUTE = int(env("CHECKIN_REMINDER_MINUTE", "0"))

# ─── Debug ────────────────────────────────────────────────
DEBUG = env("DEBUG", "").lower() in ("1", "true", "yes")

# ─── Sheets (Google) ──────────────────────────────────────
GOOGLE_CREDENTIALS_JSON = env("GOOGLE_CREDENTIALS_JSON", "")
GOOGLE_CLIENT_ID = env("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = env("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REFRESH_TOKEN = env("GOOGLE_REFRESH_TOKEN", "")
SPREADSHEET_ID = env("SPREADSHEET_ID", "")

# ─── Render / hosting ─────────────────────────────────────
RENDER_EXTERNAL_URL = env("RENDER_EXTERNAL_URL", "")
PORT = int(env("PORT", "8080"))

# ─── Cron / Health check secret ───────────────────────────
CRON_SECRET = env("CRON_SECRET", "")

# ─── Derived constants ────────────────────────────────────
RAPIDAPI_KEYS = _RAPIDAPI_KEYS
NINJA_KEYS = _NINJA_KEYS

# Convenience aliases used by older imports
LINE_CHANNEL_SECRET = CHANNEL_SECRET
LINE_CHANNEL_ACCESS_TOKEN = CHANNEL_ACCESS_TOKEN
