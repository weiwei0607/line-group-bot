"""
LINE Group Bot — Centralized configuration.
"""

import os

# ─── LINE ─────────────────────────────────────────────────
CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET", "")
CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_GROUP_ID = os.environ.get("LINE_GROUP_ID", "")

# Validate required env vars at startup
_MISSING = [k for k, v in {
    "LINE_CHANNEL_SECRET": CHANNEL_SECRET,
    "LINE_CHANNEL_ACCESS_TOKEN": CHANNEL_ACCESS_TOKEN,
}.items() if not v]
if _MISSING:
    raise RuntimeError(f"Missing required env vars: {', '.join(_MISSING)}")

# ─── AI / APIs ────────────────────────────────────────────
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
TMDB_API_KEY = os.environ.get("TMDB_API_KEY", "")
NASA_API_KEY = os.environ.get("NASA_API_KEY", "")
OWM_API_KEY = os.environ.get("OWM_API_KEY", "")

# ─── RapidAPI (multi-key rotation) ────────────────────────
_RAPIDAPI_KEYS = [k for k in [
    os.environ.get("RAPIDAPI_KEY", ""),
    os.environ.get("RAPIDAPI_KEY_2", ""),
    os.environ.get("RAPIDAPI_KEY_3", ""),
] if k]

# ─── API Ninjas (multi-key rotation) ──────────────────────
_NINJA_KEYS = [k for k in [
    os.environ.get("NINJA_API_KEY", ""),
    os.environ.get("NINJA_API_KEY_2", ""),
    os.environ.get("NINJA_API_KEY_3", ""),
] if k]

# ─── Telegram alerts ─────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# ─── Bot identity ─────────────────────────────────────────
BOT_NAME = os.environ.get("LINE_BOT_NAME", "日文小老師")
BOT_DISPLAY_NAME = os.environ.get("LINE_BOT_DISPLAY_NAME", "毛毛毛毛太后的小棉襖")
MEMBERS = ["太后", "毛毛", "二毛"]
_MEMBER_ZODIACS = {"太后": "雙子", "毛毛": "金牛", "二毛": "魔羯"}
_MEMBER_BIRTHDAYS = {"太后": "06-07", "毛毛": "04-25", "二毛": "01-04"}

# ─── Skip-log set (commands not worth memorizing) ─────────
_SKIP_LOG = {
    "查目標", "看目標", "目標", "今天第幾天", "幾天了", "進度", "打卡進度",
    "今天打卡了嗎", "今日打卡", "誰打卡了", "我的打卡", "打卡記錄", "我打了幾天",
    "今日運勢", "運勢", "占卜",
    "誰請客", "今天誰請", "誰買單", "今天誰買",
    "冷笑話", "冷知識", "上週期", "總結",
    "來隻貓", "貓貓", "來貓", "來隻狗", "狗狗", "來狗",
    "狐狸", "來隻狐", "柴柴", "柴犬", "來隻柴",
    "熊貓", "來隻熊貓", "無尾熊", "來隻無尾熊",
    "浣熊", "來隻浣熊", "今日宇宙",
    "抽寶可夢", "今日寶可夢", "給我建議", "今日忠告",
    "今日食譜", "隨機食譜", "推薦電影", "今日電影", "隨機電影",
    "待辦", "查提醒", "查待辦",
    "找歌", "查電影", "電影台詞", "在哪看",
    "今日運動", "找運動", "來一題", "今日調酒",
    "動漫語錄", "我好無聊", "川普語錄", "隨機梗圖", "諾里斯",
    "動漫圖", "激勵名言",
    "今日日文單字", "今日日文", "學日文",
    "今日漢字", "漢字練習",
    "今日西文單字", "今日西文", "學西文",
    "金價", "今日金價", "天文冷知識", "科學冷知識", "數字冷知識",
    "新聞", "今日新聞", "最新新聞",
    "去背", "配對星座",
    "配額", "/配額", "api配額", "額度",
    "指令", "說明", "幫助", "help", "功能",
    "積分", "本週積分", "答題積分", "quiz積分",
    "本週總結", "週總結", "本週回顧",
    "投票結果", "取消投票", "來一題", "答案",
}
