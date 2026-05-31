import os
import requests
import random
from datetime import datetime, timezone, timedelta

CHANNEL_ACCESS_TOKEN = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
GROUP_ID = os.environ["LINE_GROUP_ID"]
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

now_tw = datetime.now(timezone.utc) + timedelta(hours=8)
weekday = now_tw.weekday()  # 0=週一, 6=週日
day_of_year = now_tw.timetuple().tm_yday
is_weekend = weekday >= 5
is_sunday = weekday == 6

# 推薦資源清單，依天數輪流出現（每 5 天出現一次）
JP_RESOURCES = [
    ("Comprehensible Japanese（初級）", "https://youtube.com/@cijapanese\n→ 搜尋 Beginner playlist，純日文但語速慢、有字幕，超適合初學"),
    ("Japanese Ammo with Misa", "https://youtube.com/@JapaneseAmmowithMisa\n→ 台灣人也很喜歡！用日英雙語解釋文法，很清楚"),
    ("JapanesePod101", "https://youtube.com/@JapanesePod101\n→ 短片很多，找 Absolute Beginner 系列從頭看"),
    ("NHK Easy Japanese", "https://www.nhk.or.jp/lesson/\n→ NHK 官方免費課程，有繁中介面，專為初學者設計"),
    ("Speak Japanese Naturally", "https://youtube.com/@SpeakJapaneseNaturally\n→ 日常生活場景對話，N5/N4 程度，短片易吸收"),
]

def call_gemini(prompt):
    if not GEMINI_API_KEY:
        return None
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
        resp = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=15)
        return resp.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
    except:
        return None

def get_group_message():
    if is_sunday:
        return None  # 週日只發日文測驗
    if is_weekend:
        prompt = (
            "你是一個活潑有趣的朋友群機器人，請生成一則週末訊息，"
            "風格輕鬆台灣年輕人語氣。可以是出來玩的號召、週末挑戰、或話題開場。"
            "長度不超過 4 句，加一個 emoji，不要加『大家好』開頭。"
        )
    else:
        content_types = [
            "每日一問（輕鬆有趣，讓大家回答，例如：『最近讓你開心的一件小事是什麼？』）",
            "一個冷知識或奇怪但有趣的事實（讓人想說『認真假的』那種）",
            "一個小挑戰（例如：傳一首今天心情歌、說一個最近想做的事）",
            "一個輕鬆的「選一個」問題（例如：山 vs 海、早起 vs 熬夜）",
            "今日隨機鼓勵（輕鬆有趣版本，不要太雞湯）",
        ]
        prompt = (
            f"你是一個活潑有趣的朋友群機器人，今天請生成：{random.choice(content_types)}\n"
            "風格：台灣年輕人語氣，輕鬆不正式，帶點幽默。"
            "長度不超過 4 句，加 1-2 個 emoji。不要加『大家好』開頭。"
        )
    result = call_gemini(prompt)
    return result or random.choice(["大家今天過得怎樣 👀", "有人嗎還是都在擺爛 😂", "說說話啊 🫡"])

def get_japanese_content():
    if is_sunday:
        # 週日出測驗
        prompt = (
            "請為日文 N5 初學者出 3 道測驗題，題型可以是選擇題或填空題。"
            "難度非常簡單（基本問候、數字、常見單字）。\n"
            "格式：\n"
            "📝 本週日文小測驗\n\n"
            "Q1. ___\n(A)___ (B)___ (C)___\n\n"
            "Q2. ___\n\n"
            "Q3. ___\n\n"
            "✅ 答案：\n1.___ 2.___ 3.___\n\n"
            "答案折疊放在最下面，中間空幾行。不要多餘說明。"
        )
    else:
        prompt = (
            "請生成一個日文 N5 入門單字學習內容，格式如下，不要多餘文字：\n\n"
            "🇯🇵 今日日文\n"
            "【單字】日文（假名）\n"
            "【意思】中文意思\n"
            "【例句】簡單日文例句\n"
            "【翻譯】例句中文翻譯\n"
            "【發音小提示】一句話說明發音或記憶方法（選填，如果有趣的話）"
        )
    result = call_gemini(prompt)
    return result or "🇯🇵 今日日文：（生成失敗，明天繼續！）"

def should_show_resource():
    # 每 5 天推薦一次資源
    return day_of_year % 5 == 0

def get_resource():
    idx = (day_of_year // 5) % len(JP_RESOURCES)
    name, info = JP_RESOURCES[idx]
    return f"📺 本週日文推薦資源\n\n【{name}】\n{info}"

def send_line_message(text):
    requests.post(
        "https://api.line.me/v2/bot/message/push",
        headers={
            "Authorization": f"Bearer {CHANNEL_ACCESS_TOKEN}",
            "Content-Type": "application/json",
        },
        json={"to": GROUP_ID, "messages": [{"type": "text", "text": text}]},
    )

# 發送群組互動訊息
group_msg = get_group_message()
if group_msg:
    send_line_message(group_msg)

# 發送日文學習內容
jp_content = get_japanese_content()
send_line_message(jp_content)

# 每 5 天推薦一次學習資源
if should_show_resource() and not is_sunday:
    send_line_message(get_resource())
