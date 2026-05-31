import os
import re
import random
import threading
import requests
from datetime import datetime
from goal_tracker import TW_TZ
from flask import Flask, request, abort
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    Configuration, ApiClient, MessagingApi,
    ReplyMessageRequest, TextMessage, ImageMessage
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent, MemberJoinedEvent

from goal_tracker import (
    get_cycle_info, set_goals, get_goals, add_checkin,
    get_checkin_stats, get_checkin_log, get_today_checkins, build_summary_text,
    get_nickname, set_nickname, update_last_activity,
    log_chat_message, get_memories, get_streak,
    get_last_cycle_id, get_next_cycle_id, get_next_cycle_start,
    add_personal_memory, get_personal_memories,
    add_todo, get_todos, complete_todo_by_content,
    GOAL_SHEET_ID, _get_token, _sheets_get, _sheets_append, _sheets_update
)

app = Flask(__name__)

CHANNEL_SECRET = os.environ["LINE_CHANNEL_SECRET"]
CHANNEL_ACCESS_TOKEN = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
TMDB_API_KEY = os.environ.get("TMDB_API_KEY", "")
NASA_API_KEY = os.environ.get("NASA_API_KEY", "DEMO_KEY")

# 支援多把 key 輪班：RAPIDAPI_KEY, RAPIDAPI_KEY_2, RAPIDAPI_KEY_3 ...
RAPIDAPI_KEY = os.environ.get("RAPIDAPI_KEY", "")  # kept for compat
_RAPIDAPI_KEYS = [k for k in [
    os.environ.get("RAPIDAPI_KEY", ""),
    os.environ.get("RAPIDAPI_KEY_2", ""),
    os.environ.get("RAPIDAPI_KEY_3", ""),
] if k]

# 支援多把 key 輪班：NINJA_API_KEY, NINJA_API_KEY_2 ...
NINJA_API_KEY = os.environ.get("NINJA_API_KEY", "")  # kept for compat
_NINJA_KEYS = [k for k in [
    os.environ.get("NINJA_API_KEY", ""),
    os.environ.get("NINJA_API_KEY_2", ""),
    os.environ.get("NINJA_API_KEY_3", ""),
] if k]
BOT_NAME = os.environ.get("LINE_BOT_NAME", "日文小老師")
BOT_DISPLAY_NAME = os.environ.get("LINE_BOT_DISPLAY_NAME", "毛毛毛毛太后的小棉襖")
MEMBERS = ["太后", "毛毛", "二毛"]

handler = WebhookHandler(CHANNEL_SECRET)
configuration = Configuration(access_token=CHANNEL_ACCESS_TOKEN)

# Pure commands that carry no personal content worth memorising
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
    "今日日文單字", "今日漢字", "今日西文單字",
    "金價", "今日金價", "天文冷知識", "科學冷知識", "數字冷知識",
}


def _should_log(text: str) -> bool:
    if text.startswith("!"):
        return False
    if text in _SKIP_LOG:
        return False
    if re.match(r'^(叫我|設目標[：:]|抽籤|幫我選|幫我決定|選一個|幫我想目標)', text):
        return False
    return True


# ─── Gemini ───────────────────────────────────────────────

def call_gemini(prompt):
    if not GEMINI_API_KEY:
        return None
    try:
        url = (f"https://generativelanguage.googleapis.com/v1beta/"
               f"models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}")
        resp = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=12)
        return resp.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception:
        return None


# ─── LINE helpers ─────────────────────────────────────────

def get_display_name(api_client, group_id, user_id):
    try:
        line_bot_api = MessagingApi(api_client)
        profile = line_bot_api.get_group_member_profile(group_id, user_id)
        return profile.display_name
    except Exception:
        return "某人"


def get_member_label(api_client, group_id, user_id):
    nick = get_nickname(user_id)
    if nick:
        return nick
    return get_display_name(api_client, group_id, user_id)


# ─── Weather ──────────────────────────────────────────────

TW_CITIES = ["台北", "新北", "桃園", "台中", "台南", "高雄",
             "基隆", "花蓮", "台東", "宜蘭", "嘉義", "新竹"]


def get_weather(text):
    city = "台北"
    for c in TW_CITIES:
        if c in text:
            city = c
            break
    try:
        resp = requests.get(f"https://wttr.in/{city}?format=3&lang=zh&m", timeout=8)
        return f"🌤 {resp.text.strip()}\n（資料來源：wttr.in）"
    except Exception:
        return f"天氣查詢失敗，去 Google 查 {city} 天氣吧 😅"


# ─── Exchange rate ────────────────────────────────────────

def get_exchange_rate(text):
    pairs = [("USD", "美金", "$"), ("JPY", "日幣", "¥"),
             ("EUR", "歐元", "€"), ("KRW", "韓元", "₩")]
    targets = [p for p in pairs if p[1] in text or p[0] in text.upper()]
    if not targets:
        targets = [("USD", "美金", "$"), ("JPY", "日幣", "¥")]
    try:
        lines = ["💱 即時匯率（對台幣）\n"]
        for code, name, symbol in targets:
            resp = requests.get(f"https://open.er-api.com/v6/latest/{code}", timeout=8)
            twd = resp.json()["rates"].get("TWD", 0)
            if code == "JPY":
                lines.append(f"  {symbol} 100 {name} = {twd*100:.2f} 台幣")
            else:
                lines.append(f"  {symbol} 1 {name} = {twd:.2f} 台幣")
        return "\n".join(lines)
    except Exception:
        return "匯率查詢失敗 😢 試試 Google 匯率"


# ─── Countdown ────────────────────────────────────────────

def handle_countdown(text):
    from datetime import date as date_type
    from goal_tracker import _now, TW_TZ
    now = _now()

    patterns = [
        (r'(\d{4})[/\-.](\d{1,2})[/\-.](\d{1,2})', 3),
        (r'(\d{1,2})[/\-.](\d{1,2})', 2),
    ]
    target = None
    label = ""
    for pattern, groups in patterns:
        match = re.search(pattern, text)
        if match:
            g = match.groups()
            try:
                if groups == 3:
                    target = date_type(int(g[0]), int(g[1]), int(g[2]))
                    label = f"{g[1]}/{g[2]}"
                else:
                    y = now.year
                    m, d = int(g[0]), int(g[1])
                    target = date_type(y, m, d)
                    if target < now.date():
                        target = date_type(y + 1, m, d)
                    label = f"{m}/{d}"
            except ValueError:
                pass
            break

    if not target:
        return "格式：倒數 2026/12/25 或 倒數 12/25"

    days = (target - now.date()).days
    if days < 0:
        return f"那天已經過了 {abs(days)} 天 😅"
    elif days == 0:
        return "🎉 就是今天！！衝啊！"
    elif days == 1:
        return f"📅 距離 {label} 剩 1 天！明天到了！！"
    else:
        return f"📅 距離 {label} 還有 {days} 天"


# ─── Fortune ─────────────────────────────────────────────

def handle_fortune():
    prompt = (
        f"請為以下三個人各生成一則今日運勢，人名：太后、毛毛、二毛。\n"
        f"風格：輕鬆有趣，像星座運勢但更貼近生活，每人 2-3 句話。\n"
        f"格式：\n"
        f"🔮 太后：...\n"
        f"🔮 毛毛：...\n"
        f"🔮 二毛：...\n"
        f"不要太正面或太負面，要有趣、可信、帶一個小建議。"
    )
    result = call_gemini(prompt)
    return result or "🔮 今日運勢：系統占卜中...水晶球破掉了 😅"


# ─── Who pays ────────────────────────────────────────────

def handle_who_pays(text):
    chosen = random.choice(MEMBERS)
    phrases = [
        f"🎰 轉啊轉啊轉... 今天是【{chosen}】！請客！💸",
        f"🎯 命運之輪選中了...{chosen}！不許賴帳 😈",
        f"✨ 今天的幸運請客人是...{chosen}！恭喜恭喜 🎉",
    ]
    return random.choice(phrases)


# ─── Draw lots ───────────────────────────────────────────

def handle_draw_lots(text):
    after = re.sub(r'^(抽籤|幫我選|幫我決定|選一個)\s*', '', text).strip()
    if after:
        options = [o.strip() for o in re.split(r'[/、\s,，]+', after) if o.strip()]
        if len(options) >= 2:
            chosen = random.choice(options)
            return f"🎋 抽籤結果：【{chosen}】！就這個！不准後悔 😤"
    choices = ["✅ 可以！去吧！", "❌ 不行！再想想！", "🤔 再考慮一下", "😅 隨便啦你決定", "🎲 丟銅板決定吧"]
    return f"🎋 {random.choice(choices)}"


# ─── Auto-reply handlers ─────────────────────────────────

def handle_joke(_):
    return call_gemini(
        "請說一個台灣年輕人喜歡的冷笑話，有笑點但很冷，繁體中文，不超過 4 行，最後加一個 😂"
    ) or "😅 ...笑了嗎（靜音）"


def handle_fun_fact(_):
    return call_gemini(
        "請分享一個有趣的冷知識，讓人覺得「認真嗎！？」，繁體中文，不超過 3 句，開頭加 🤯"
    ) or "🤯 我腦袋當機了，明天再說"


# ─── Easter eggs ─────────────────────────────────────────


def fetch_cat_image() -> str | None:
    try:
        r = requests.get("https://api.thecatapi.com/v1/images/search", timeout=8)
        return r.json()[0]["url"]
    except Exception:
        return None


def fetch_dog_image() -> str | None:
    try:
        r = requests.get("https://dog.ceo/api/breeds/image/random", timeout=8)
        url = r.json().get("message", "")
        return url if url.startswith("https") else None
    except Exception:
        return None


_POKEMON_TYPES = {
    "normal": "一般", "fire": "火", "water": "水", "electric": "電",
    "grass": "草", "ice": "冰", "fighting": "格鬥", "poison": "毒",
    "ground": "地面", "flying": "飛行", "psychic": "超能力", "bug": "蟲",
    "rock": "岩石", "ghost": "幽靈", "dragon": "龍", "dark": "惡",
    "steel": "鋼", "fairy": "妖精",
}


def fetch_random_pokemon() -> str:
    try:
        pid = random.randint(1, 898)
        p = requests.get(f"https://pokeapi.co/api/v2/pokemon/{pid}", timeout=8).json()
        s = requests.get(f"https://pokeapi.co/api/v2/pokemon-species/{pid}", timeout=8).json()
        zh_name = next((n["name"] for n in s["names"] if n["language"]["name"] == "zh-Hant"), p["name"].capitalize())
        types = "／".join(_POKEMON_TYPES.get(t["type"]["name"], t["type"]["name"]) for t in p["types"])
        height, weight = p["height"] / 10, p["weight"] / 10
        flavor = next(
            (f["flavor_text"].replace("\n", "").replace("\f", "")
             for f in s["flavor_text_entries"] if f["language"]["name"] == "zh-Hant"),
            "",
        )
        lines = [f"🎮 今日寶可夢：#{pid} {zh_name}", f"屬性：{types}　身高 {height}m　體重 {weight}kg"]
        if flavor:
            lines.append(f"📖 {flavor[:70]}")
        return "\n".join(lines)
    except Exception:
        return "🎮 寶可夢跑掉了，再抽一次吧！"


def fetch_advice() -> str:
    try:
        r = requests.get("https://api.adviceslip.com/advice", timeout=8)
        advice = r.json()["slip"]["advice"]
        return f"💡 今日忠告（英）\n{advice}"
    except Exception:
        return "💡 建議你今天多喝水 🫗"


def fetch_fox_image() -> str | None:
    try:
        r = requests.get("https://randomfox.ca/floof/", timeout=8)
        return r.json().get("image")
    except Exception:
        return None


def fetch_shiba_image() -> str | None:
    try:
        r = requests.get("https://shibe.online/api/shibes?count=1", timeout=8)
        urls = r.json()
        return urls[0] if urls else None
    except Exception:
        return None


def fetch_animal_image(animal: str) -> str | None:
    try:
        r = requests.get(f"https://some-random-api.com/animal/{animal}", timeout=8)
        return r.json().get("image")
    except Exception:
        return None


def fetch_nasa_apod() -> tuple[str, str | None]:
    try:
        r = requests.get(
            "https://api.nasa.gov/planetary/apod",
            params={"api_key": NASA_API_KEY},
            timeout=10,
        )
        d = r.json()
        title = d.get("title", "")
        explanation = (d.get("explanation") or "")[:100]
        media_type = d.get("media_type", "image")
        url = d.get("url", "") if media_type == "image" else None
        text = f"🌌 今日宇宙：{title}\n{explanation}..."
        return text, url
    except Exception:
        return "🌌 今日宇宙：NASA 暫時無法連線 😢", None


# ─── RapidAPI 共用 helper ─────────────────────────────────

_QUOTA = object()  # sentinel: API quota exceeded
_QUOTA_MSG = "😅 今天 API 額度用完囉，明天再來試試！"

_rapid_idx = 0   # round-robin index for RapidAPI keys
_ninja_idx = 0   # round-robin index for API Ninjas keys


def _rapid(method: str, host: str, path: str, **kwargs):
    global _rapid_idx
    if not _RAPIDAPI_KEYS:
        return None
    extra_headers = kwargs.pop("headers", {})
    n = len(_RAPIDAPI_KEYS)
    for attempt in range(n):
        key = _RAPIDAPI_KEYS[(_rapid_idx + attempt) % n]
        headers = {"x-rapidapi-key": key, "x-rapidapi-host": host, **extra_headers}
        try:
            r = getattr(requests, method)(
                f"https://{host}{path}", headers=headers, timeout=10, **kwargs
            )
            if r.status_code == 429:
                print(f"[RapidAPI] 429 key {(_rapid_idx+attempt)%n+1}/{n}: {host}{path}")
                continue  # try next key
            r.raise_for_status()
            _rapid_idx = (_rapid_idx + attempt + 1) % n  # advance after success
            return r.json()
        except Exception as e:
            print(f"[RapidAPI] {host}{path} → {e}")
            return None
    print(f"[RapidAPI] all {n} keys quota exceeded: {host}{path}")
    return _QUOTA


def _ninja(path: str, **kwargs):
    global _ninja_idx
    if not _NINJA_KEYS:
        return _rapid("get", "api-ninjas.p.rapidapi.com", path, **kwargs)
    n = len(_NINJA_KEYS)
    for attempt in range(n):
        key = _NINJA_KEYS[(_ninja_idx + attempt) % n]
        try:
            r = requests.get(
                f"https://api.api-ninjas.com{path}",
                headers={"X-Api-Key": key},
                timeout=10,
                **kwargs,
            )
            if r.status_code == 429:
                print(f"[api-ninjas] 429 key {(_ninja_idx+attempt)%n+1}/{n}: {path}")
                continue  # try next key
            r.raise_for_status()
            _ninja_idx = (_ninja_idx + attempt + 1) % n  # advance after success
            return r.json()
        except Exception as e:
            print(f"[api-ninjas] {path} → {e}")
            return None
    # all ninja keys exhausted, fall back to RapidAPI
    return _rapid("get", "api-ninjas.p.rapidapi.com", path, **kwargs)


# ─── Translation ──────────────────────────────────────────

_TRANSLATE_PENDING: dict = {}
_QUIZ_STATE: dict = {}  # group_id -> {question, answer}

_LANG_MAP = {
    "日文": "ja", "日語": "ja",
    "英文": "en", "英語": "en",
    "韓文": "ko", "韓語": "ko",
    "義大利文": "it", "意大利文": "it",
    "法文": "fr", "法語": "fr",
    "西班牙文": "es", "西語": "es",
    "德文": "de", "德語": "de",
    "泰文": "th", "泰語": "th",
    "越南文": "vi",
    "葡文": "pt", "葡萄牙文": "pt",
    "俄文": "ru",
    "繁中": "zh-TW", "繁體中文": "zh-TW",
    "簡中": "zh-CN", "簡體中文": "zh-CN",
    "阿拉伯文": "ar",
}
_LANG_DISPLAY = {v: k for k, v in list(_LANG_MAP.items())}


def translate_text(text: str, target_lang: str) -> str:
    d = _rapid(
        "post", "text-translator2.p.rapidapi.com", "/translate",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={"source_language": "auto", "target_language": target_lang, "text": text},
    )
    if d is _QUOTA:
        return _QUOTA_MSG
    if not d:
        return "翻譯失敗，等一下再試 😢"
    translated = (d.get("data") or {}).get("translatedText", "")
    if not translated:
        return "翻譯失敗，等一下再試 😢"
    lang_name = _LANG_DISPLAY.get(target_lang, target_lang)
    return f"🌐 {lang_name}\n{translated}"


def handle_translate(user_id: str, text: str) -> str:
    m = re.match(r'^翻\s+(\S+)\s+(.+)', text)
    if m:
        code = _LANG_MAP.get(m.group(1))
        if not code:
            return f"不認識「{m.group(1)}」\n支援：{'、'.join(list(_LANG_MAP.keys())[:10])}..."
        return translate_text(m.group(2), code)
    m = re.match(r'^翻\s+(\S+)$', text)
    if m:
        code = _LANG_MAP.get(m.group(1))
        if not code:
            return f"不認識「{m.group(1)}」\n支援：{'、'.join(list(_LANG_MAP.keys())[:10])}..."
        _TRANSLATE_PENDING[user_id] = code
        return f"要翻成{m.group(1)}，請傳要翻的文字 👇\n（傳「取消」取消）"
    langs = "、".join(list(_LANG_MAP.keys())[:8]) + "..."
    return f"格式：翻 日文 要翻的文字\n支援：{langs}"


# ─── Weather upgrade ──────────────────────────────────────

def get_weather_v2(text: str) -> str:
    city = "台北"
    for c in TW_CITIES:
        if c in text:
            city = c
            break
    d = _rapid("get", "weatherapi-com.p.rapidapi.com", "/current.json",
               params={"q": city, "lang": "zh"})
    if d is _QUOTA or not d:
        return get_weather(text)  # fall back to free wttr.in
    try:
        loc = d["location"]["name"]
        cur = d["current"]
        return (
            f"🌤 {loc} 天氣\n"
            f"{cur['condition']['text']}　{cur['temp_c']}°C"
            f"（體感 {cur['feelslike_c']}°C）\n"
            f"濕度 {cur['humidity']}%　風速 {cur['wind_kph']} km/h"
        )
    except Exception:
        return get_weather(text)


# ─── Music ───────────────────────────────────────────────

def fetch_spotify_track(query: str) -> str:
    d = _rapid("get", "spotify23.p.rapidapi.com", "/search",
               params={"q": query, "type": "tracks", "numberOfTopResults": "3"})
    if d is _QUOTA:
        return _QUOTA_MSG
    if not d:
        return f"🎵 找不到「{query}」😢"
    try:
        items = d.get("tracks", {}).get("items", [])
        if not items:
            return f"🎵 找不到「{query}」相關的歌"
        lines = ["🎵 找到以下歌曲：\n"]
        for t in items[:3]:
            data = t.get("data", {})
            name = data.get("name", "")
            artists = data.get("artists", {}).get("items", [])
            artist = artists[0].get("profile", {}).get("name", "") if artists else ""
            lines.append(f"• {name} — {artist}")
        return "\n".join(lines)
    except Exception as e:
        print(f"[spotify] {e}")
        return f"🎵 找不到「{query}」😢"


# ─── Movies ──────────────────────────────────────────────

def fetch_imdb(title: str) -> str:
    d = _rapid("get", "imdb8.p.rapidapi.com", "/title/find", params={"q": title})
    if d is _QUOTA:
        return _QUOTA_MSG
    if not d:
        return f"🎬 查不到「{title}」😢"
    try:
        results = d.get("results", [])
        if not results:
            return f"🎬 找不到「{title}」"
        m = results[0]
        year = m.get("year", "")
        rating = m.get("starRating", {}).get("ratingValue", "")
        title_str = m.get("title", "")
        return (
            f"🎬 {title_str}（{year}）\n"
            f"⭐ {rating}/10" if rating else f"🎬 {title_str}（{year}）"
        )
    except Exception as e:
        print(f"[imdb] {e}")
        return f"🎬 查不到「{title}」😢"


def fetch_movie_quote() -> str:
    d = _rapid("get", "movie-quote.p.rapidapi.com", "/", params={"count": "1"})
    if d is _QUOTA:
        return _QUOTA_MSG
    if not d:
        return "🎬 沒有台詞，待會再試"
    try:
        q = d[0] if isinstance(d, list) else d
        content = q.get("quoteText") or q.get("quote", "")
        movie = q.get("quoteMovie") or q.get("movie", "")
        if not content:
            return "🎬 沒有台詞，待會再試"
        return f"🎬 「{content}」\n—《{movie}》"
    except Exception:
        return "🎬 沒有台詞，待會再試"


def fetch_streaming(title: str) -> str:
    d = None
    for country in ["tw", "us"]:
        d = _rapid("get", "streaming-availability.p.rapidapi.com", "/shows/search/title",
                   params={"title": title, "country": country})
        if d and d is not _QUOTA:
            break
    if d is _QUOTA:
        return _QUOTA_MSG
    if not d:
        return f"🍿 找不到「{title}」的串流資訊"
    try:
        items = d if isinstance(d, list) else [d]
        if not items:
            return f"🍿「{title}」目前沒有串流上架"
        show = items[0]
        title_show = show.get("title", title)
        services = show.get("streamingInfo", {})
        if not services:
            return f"🍿《{title_show}》目前沒有串流上架"
        lines = [f"🍿《{title_show}》可在："]
        for svc in list(services.keys())[:5]:
            lines.append(f"  • {svc.capitalize()}")
        return "\n".join(lines)
    except Exception as e:
        print(f"[streaming] {e}")
        return "🍿 查詢失敗，待會再試 😢"


# ─── Exercise ────────────────────────────────────────────

_BODY_PARTS = {
    "背部": "back", "胸部": "chest", "腿部": "upper legs",
    "手臂": "upper arms", "肩膀": "shoulders", "腹部": "waist",
    "有氧": "cardio", "小腿": "lower legs",
}


def fetch_exercise(body_part: str = None) -> str:
    if body_part:
        en = _BODY_PARTS.get(body_part, body_part)
        data = _rapid("get", "exercisedb.p.rapidapi.com", f"/exercises/bodyPart/{en}",
                      params={"limit": "15", "offset": "0"})
    else:
        data = _rapid("get", "exercisedb.p.rapidapi.com", "/exercises",
                      params={"limit": "20", "offset": str(random.randint(0, 800))})
    if data is _QUOTA:
        return _QUOTA_MSG
    if not data or not isinstance(data, list):
        return "💪 找不到運動，待會再試"
    ex = random.choice(data)
    name = ex.get("name", "")
    bp = ex.get("bodyPart", "")
    target = ex.get("target", "")
    equipment = ex.get("equipment", "")
    return (
        f"💪 今日運動：{name}\n"
        f"部位：{bp}　目標：{target}\n"
        f"器材：{'徒手' if equipment == 'body weight' else equipment}"
    )


# ─── Trivia ──────────────────────────────────────────────

def fetch_trivia() -> str:
    d = _ninja("/v1/trivia")
    if d is _QUOTA:
        return _QUOTA_MSG
    if not d or not isinstance(d, list):
        return "🧠 題庫暫時關閉，待會再試"
    q = d[0]
    return (
        f"🧠 來一題！（{q.get('category', '')}）\n\n"
        f"{q.get('question', '')}\n\n"
        f"答案：{q.get('answer', '')}"
    )


# ─── Cocktail ────────────────────────────────────────────

def fetch_cocktail(name: str = None) -> str:
    params = {"name": name} if name else {}
    d = _ninja("/v1/cocktail", params=params)
    if d is _QUOTA:
        return _QUOTA_MSG
    if d and isinstance(d, list):
        c = d[0]
        return (
            f"🍹 今日調酒：{c.get('name', '')}\n"
            f"材料：{'、'.join(c.get('ingredients', [])[:5])}\n"
            f"做法：{(c.get('instructions') or '')[:100]}..."
        )
    # Fallback to TheCocktailDB
    try:
        r = requests.get("https://www.thecocktaildb.com/api/json/v1/1/random.php", timeout=8)
        drink = r.json()["drinks"][0]
        ings = [drink.get(f"strIngredient{i}", "") for i in range(1, 8) if drink.get(f"strIngredient{i}")]
        return (
            f"🍹 今日調酒：{drink['strDrink']}（{drink.get('strCategory', '')}）\n"
            f"材料：{'、'.join(ings[:5])}\n"
            f"做法：{(drink.get('strInstructions') or '')[:100]}..."
        )
    except Exception:
        return "🍹 調酒師不在，待會再試"


# ─── Anime Quotes ────────────────────────────────────────

def fetch_anime_quote() -> str:
    d = _rapid("get", "anime-quotes4.p.rapidapi.com", "/")
    if d is _QUOTA:
        return _QUOTA_MSG
    if not d:
        return "🌸 動漫語錄暫時失靈，待會再試"
    try:
        item = random.choice(d) if isinstance(d, list) else d
        quote = item.get("quote") or item.get("content", "")
        char = item.get("char") or item.get("character", "")
        anime = item.get("anime", "")
        if not quote:
            return "🌸 動漫語錄暫時失靈，待會再試"
        line = f"🌸 「{quote}」"
        if char:
            line += f"\n— {char}"
        if anime:
            line += f"《{anime}》"
        return line
    except Exception:
        return "🌸 動漫語錄暫時失靈，待會再試"


# ─── Random Activity ─────────────────────────────────────

_FALLBACK_ACTIVITIES = [
    "整理一個抽屜或衣櫃 🗂️",
    "寫一封信給未來的自己 ✉️",
    "學 3 個新單字（任何語言）📚",
    "做 10 分鐘伸展 🧘",
    "畫一幅塗鴉，不管好不好看 🎨",
    "整理手機相簿，刪掉 10 張沒用的照片 📱",
    "讀一篇你一直想讀的文章 📰",
    "打電話給好久不見的朋友 📞",
]


def fetch_random_activity() -> str:
    d = _rapid("get", "bored-api.p.rapidapi.com", "/api/activity")
    if d is _QUOTA or not d or not isinstance(d, dict):
        return f"🎲 無聊的話可以：\n{random.choice(_FALLBACK_ACTIVITIES)}"
    if d.get("activity"):
        return (
            f"🎲 無聊的話可以：\n{d['activity']}\n"
            f"類型：{d.get('type', '')}　人數：{d.get('participants', 1)}"
        )
    return f"🎲 無聊的話可以：\n{random.choice(_FALLBACK_ACTIVITIES)}"


# ─── Trump Quotes ────────────────────────────────────────

def fetch_trump_quote() -> str:
    try:
        r = requests.get("https://api.tronalddump.io/random/quote", timeout=8,
                         headers={"Accept": "application/json"})
        quote = r.json().get("value", "")
        if quote:
            return f"🦅 川普語錄\n「{quote}」"
    except Exception:
        pass
    return "🦅 川普去打高爾夫了，待會再問"


# ─── SuperHero ───────────────────────────────────────────

def fetch_superhero(name: str) -> str:
    d = _rapid("get", "superhero-search.p.rapidapi.com", f"/api/{name}")
    if d is _QUOTA:
        return _QUOTA_MSG
    if not d:
        return f"🦸 找不到「{name}」，試試英文名字"
    try:
        hero = d[0] if isinstance(d, list) else d
        hname = hero.get("name", name)
        bio = hero.get("biography") or {}
        stats = hero.get("powerstats") or {}
        alignment = bio.get("alignment", "")
        align = "英雄 🦸" if alignment == "good" else ("反派 🦹" if alignment == "bad" else "😐")
        return (
            f"🦸 {hname}（{bio.get('publisher', '')}）{align}\n"
            f"力量 {stats.get('strength', '?')}　速度 {stats.get('speed', '?')}\n"
            f"智力 {stats.get('intelligence', '?')}　能量 {stats.get('power', '?')}"
        )
    except Exception as e:
        print(f"[superhero] {e}")
        return f"🦸 查不到「{name}」，試試英文名字"


# ─── Meme ────────────────────────────────────────────────

def fetch_meme() -> tuple:
    d = _rapid("get", "memeados.p.rapidapi.com", "/rdmeme/")
    if d is _QUOTA:
        return _QUOTA_MSG, None
    if d:
        try:
            url = d.get("url") or d.get("image", "")
            title = d.get("title", "")
            if url and url.startswith("http"):
                return (f"😂 {title}" if title else "😂 今日梗圖"), url
        except Exception:
            pass
    return "😂 梗圖機壞了，待會再試", None


# ─── Chuck Norris ─────────────────────────────────────────

def fetch_chuck_norris() -> str:
    try:
        r = requests.get("https://api.chucknorris.io/jokes/random", timeout=8)
        joke = r.json().get("value", "")
        if joke:
            return f"💪 查克諾里斯冷知識\n{joke}"
    except Exception:
        pass
    return "💪 查克諾里斯太強了，API 被他打掛了"


# ─── TLDR ────────────────────────────────────────────────

def fetch_tldr(text: str) -> str:
    d = _rapid(
        "post", "tldrthis.p.rapidapi.com", "/v1/article/summarize/",
        headers={"Content-Type": "application/json"},
        json={"article_html": text, "max_sentences": 3},
    )
    if d is not _QUOTA and d:
        sentences = d.get("summary", [])
        if sentences:
            return "📝 摘要：\n" + " ".join(sentences)
    return call_gemini(f"請用 3 句話摘要以下文字：\n{text[:1000]}") or "摘要失敗 😢"


def handle_pairing(text) -> str:
    m = re.match(r'^配對\s+(.+)', text)
    if m:
        parts = [p.strip() for p in re.split(r'[\s和與跟＆&×x]+', m.group(1)) if p.strip()]
        a = parts[0] if len(parts) >= 1 else random.choice(MEMBERS)
        b = parts[1] if len(parts) >= 2 else random.choice([x for x in MEMBERS if x != a] or MEMBERS)
    else:
        a, b = random.sample(MEMBERS, 2)
    score = random.randint(0, 100)
    if score >= 90:
        label = "天生一對！！宇宙安排的 💑"
    elif score >= 75:
        label = "超配的！好感度爆表 🥰"
    elif score >= 60:
        label = "有點曖昧... 要不要試試看 👀"
    elif score >= 40:
        label = "普通朋友，但誰說普通不好 😊"
    elif score >= 20:
        label = "還需要多培養感情 😅"
    else:
        label = "宇宙說：緣分不夠，但可以努力 😂"
    bar = "❤️" * (score // 10) + "🤍" * (10 - score // 10)
    return f"💘 配對係數\n{a} × {b}\n{bar}\n{score}%　{label}"


def handle_dice(text) -> str:
    m = re.search(r'搖?(\d+)\s*[顆個]', text)
    n = min(int(m.group(1)), 10) if m else 1
    results = [random.randint(1, 6) for _ in range(n)]
    faces = ["⚀", "⚁", "⚂", "⚃", "⚄", "⚅"]
    if n == 1:
        return f"🎲 擲出了：{faces[results[0]-1]}（{results[0]} 點）"
    return f"🎲 擲出 {n} 顆：\n{'  '.join(faces[r-1] for r in results)}\n合計：{sum(results)} 點"


def handle_rps(text) -> str:
    user_map = {"剪刀": "✂️", "石頭": "🪨", "布": "📄"}
    user_choice = next((k for k in user_map if k in text), None)
    bot_choice = random.choice(list(user_map))
    bot_emoji = user_map[bot_choice]
    if user_choice is None:
        return f"猜拳要說：猜拳 剪刀 / 石頭 / 布\n（我出了{bot_emoji}，你出什麼？）"
    user_emoji = user_map[user_choice]
    wins = {"剪刀": "布", "石頭": "剪刀", "布": "石頭"}
    if user_choice == bot_choice:
        result = "平手！再來！"
    elif wins[user_choice] == bot_choice:
        result = random.choice(["你贏了！！", "輸了！不服氣！ 😤", "嗚嗚認輸"])
    else:
        result = random.choice(["哈我贏了 😈", "幸運是我的 🎊", "再猜！！"])
    return f"{user_emoji} VS {bot_emoji}\n{result}"


# ─── Health / Utility ─────────────────────────────────────

def calc_bmi(height_cm: float, weight_kg: float) -> dict:
    bmi = round(weight_kg / (height_cm / 100) ** 2, 1)
    if bmi < 18.5:
        cat = "體重過輕 😟"
    elif bmi < 24:
        cat = "正常範圍 😊"
    elif bmi < 27:
        cat = "過重 😐"
    elif bmi < 30:
        cat = "輕度肥胖 😬"
    else:
        cat = "中重度肥胖 ⚠️"
    return {"bmi": bmi, "category": cat}


def fetch_nutrition(query: str) -> str:
    d = _ninja("/v1/nutrition", params={"query": query})
    if d is _QUOTA:
        return _QUOTA_MSG
    if not d or not isinstance(d, list):
        return f"查不到「{query}」的熱量資料，試試英文食物名"
    items = d.get("items", d) if isinstance(d, dict) else d
    if not items:
        return f"查不到「{query}」的熱量資料"
    lines = [f"🔥 熱量查詢：{query}\n"]
    for it in items[:3]:
        lines.append(
            f"• {it.get('name', '')}（{it.get('serving_size_g', 100)}g）\n"
            f"  {round(it.get('calories', 0))} 卡　"
            f"蛋白質 {round(it.get('protein_g', 0))}g　"
            f"脂肪 {round(it.get('fat_total_g', 0))}g"
        )
    return "\n".join(lines)


def fetch_calories_burned(activity: str, duration_min: int = 30) -> str:
    d = _ninja("/v1/caloriesburned", params={"activity": activity, "duration": str(duration_min)})
    if d is _QUOTA:
        return _QUOTA_MSG
    if not d or not isinstance(d, list):
        return call_gemini(f"做「{activity}」{duration_min}分鐘大約消耗多少卡路里？用繁體中文簡短回答")
    lines = [f"🏃 消耗熱量：{activity}（{duration_min}分鐘）\n"]
    for it in d[:3]:
        cal_h = it.get("calories_per_hour", 0)
        total = round(cal_h * duration_min / 60)
        lines.append(f"• {it.get('name', activity)}：約 {total} 卡")
    return "\n".join(lines)


def fetch_gold_price() -> str:
    d = _rapid("get", "gold-price-live.p.rapidapi.com", "/get_metal_prices")
    if d is _QUOTA:
        return _QUOTA_MSG
    if not d:
        return "🪙 金價查詢失敗，待會再試"
    try:
        metals = d.get("metals", d)
        gold = metals.get("XAU") or metals.get("gold") or metals.get("GOLD")
        silver = metals.get("XAG") or metals.get("silver") or metals.get("SILVER")
        if not gold:
            return "🪙 金價資料解析失敗"
        lines = [f"🪙 今日金價\n\n黃金：${gold} USD/盎司"]
        try:
            r2 = requests.get("https://open.er-api.com/v6/latest/USD", timeout=8)
            rate = r2.json()["rates"].get("TWD", 31)
            lines.append(f"約 NT$ {round(float(gold) * rate):,} 元/盎司")
        except Exception:
            pass
        if silver:
            lines.append(f"白銀：${silver} USD/盎司")
        return "\n".join(lines)
    except Exception as e:
        print(f"[gold] {e}")
        return "🪙 金價查詢失敗"


def fetch_number_fact() -> str:
    try:
        r = requests.get("http://numbersapi.com/random/trivia", params={"json": "true"}, timeout=8)
        text = r.json().get("text", "")
        if text:
            zh = call_gemini(f"翻成繁體中文，保持趣味，只給翻譯：{text}")
            return f"🔢 {zh}"
    except Exception:
        pass
    return call_gemini("給我一個關於數字的有趣冷知識，用繁體中文") or "數字冷知識暫時失靈"


def fetch_astronomy_fact() -> str:
    d = _ninja("/v1/facts", params={"category": "science"})
    if d is _QUOTA:
        return _QUOTA_MSG
    if d and isinstance(d, list):
        fact = d[0].get("fact", "")
        if fact:
            zh = call_gemini(f"翻成繁體中文，保持趣味，只給翻譯：{fact}")
            return f"🔭 {zh}"
    return call_gemini("給我一個有趣的天文或科學冷知識，用繁體中文") or "天文冷知識暫時失靈"


def fetch_recipe_by_ingredient(ingredients: str) -> str:
    d = _rapid("get", "spoonacular-recipe-food-nutrition-v1.p.rapidapi.com",
               "/recipes/findByIngredients",
               params={"ingredients": ingredients, "number": "3", "ranking": "1"})
    if d is _QUOTA:
        return _QUOTA_MSG
    if d and isinstance(d, list):
        lines = [f"🍳 用「{ingredients}」可以做：\n"]
        for r in d[:3]:
            title = r.get("title", "")
            lines.append(f"• {title}")
        return "\n".join(lines)
    return call_gemini(f"根據食材「{ingredients}」推薦一道料理和簡單做法，用繁體中文")


# ─── Free APIs (no key) ───────────────────────────────────

def fetch_country(name: str) -> str:
    try:
        r = requests.get(
            f"https://restcountries.com/v3.1/name/{requests.utils.quote(name)}",
            params={"fields": "name,capital,population,languages,currencies,flags,region"},
            timeout=10,
        )
        r.raise_for_status()
        c = r.json()[0]
        cap = (c.get("capital") or ["—"])[0]
        pop = f"{c.get('population', 0):,}"
        langs = "、".join(list((c.get("languages") or {}).values())[:3])
        currs = "、".join(
            f"{v.get('name', k)}（{v.get('symbol', '')}）"
            for k, v in (c.get("currencies") or {}).items()
        )
        region = c.get("region", "")
        common = c["name"]["common"]
        return (
            f"🌍 {common}（{region}）\n"
            f"首都：{cap}\n"
            f"人口：{pop}\n"
            f"語言：{langs or '—'}\n"
            f"貨幣：{currs or '—'}"
        )
    except Exception:
        return f"😢 找不到「{name}」的資料"


def fetch_book(query: str) -> str:
    try:
        r = requests.get(
            "https://openlibrary.org/search.json",
            params={"q": query, "limit": 3, "fields": "title,author_name,first_publish_year,subject"},
            timeout=10,
        )
        docs = r.json().get("docs", [])
        if not docs:
            return f"📚 找不到「{query}」相關書籍"
        lines = [f"📚 找到 {len(docs)} 本相關書籍：\n"]
        for d in docs:
            title = d.get("title", "—")
            authors = "、".join((d.get("author_name") or [])[:2]) or "—"
            year = d.get("first_publish_year", "")
            year_str = f"（{year}）" if year else ""
            lines.append(f"• {title}{year_str}\n  作者：{authors}")
        return "\n".join(lines)
    except Exception:
        return "📚 書籍查詢暫時失敗"


def fetch_waifu() -> tuple:
    try:
        r = requests.get("https://api.waifu.im/search", params={"is_nsfw": "false"}, timeout=10)
        items = r.json().get("images", [])
        if items:
            return "🌸 動漫圖來了！", items[0].get("url")
    except Exception:
        pass
    return "🌸 動漫圖暫時抓不到", None


def fetch_quotable() -> str:
    try:
        r = requests.get("https://api.quotable.io/random", timeout=8)
        d = r.json()
        q = d.get("content", "")
        a = d.get("author", "")
        if q:
            return f'✨ "{q}"\n\n— {a}'
    except Exception:
        pass
    return ""


# ─── Language Learning ────────────────────────────────────

_JLPT_N5_WORDS = [
    "食べる", "飲む", "行く", "来る", "見る", "聞く", "話す", "読む", "書く", "買う",
    "起きる", "寝る", "食べ物", "飲み物", "学校", "電車", "友達", "家族", "先生",
    "毎日", "今日", "明日", "昨日", "時間", "場所", "電話", "料理", "音楽", "映画",
    "天気", "勉強", "仕事", "休み", "旅行", "言葉", "日本語", "英語",
]

_JLPT_N5_KANJI = [
    "日", "月", "火", "水", "木", "金", "土", "山", "川", "田",
    "人", "口", "目", "耳", "手", "足", "上", "下", "中", "大",
    "小", "年", "時", "学", "校", "先", "生", "家", "車", "電",
    "花", "犬", "猫", "魚", "鳥", "空", "海", "雨", "雪", "風",
]


def _jisho_lookup(word: str) -> dict | None:
    try:
        r = requests.get(
            "https://jisho.org/api/v1/search/words",
            params={"keyword": word},
            timeout=10,
        )
        data = r.json().get("data", [])
        if not data:
            return None
        entry = data[0]
        readings = entry.get("japanese", [{}])
        senses = entry.get("senses", [{}])
        meanings_en = []
        for s in senses[:3]:
            meanings_en.extend(s.get("english_definitions", [])[:2])
        return {
            "word": readings[0].get("word") or readings[0].get("reading", word),
            "reading": readings[0].get("reading", ""),
            "meanings_en": meanings_en[:4],
            "jlpt": entry.get("jlpt", []),
            "common": entry.get("is_common", False),
        }
    except Exception:
        return None


def _kanji_lookup(char: str) -> dict | None:
    try:
        r = requests.get(f"https://kanjiapi.dev/v1/kanji/{char}", timeout=8)
        if r.status_code != 200:
            return None
        d = r.json()
        return {
            "kanji": d.get("kanji", ""),
            "meanings": d.get("meanings", [])[:4],
            "kun": d.get("kun_readings", [])[:3],
            "on": d.get("on_readings", [])[:3],
            "jlpt": d.get("jlpt"),
            "strokes": d.get("stroke_count"),
        }
    except Exception:
        return None


def fetch_jisho(word: str) -> str:
    d = _jisho_lookup(word)
    if not d:
        return call_gemini(
            f"用繁體中文解釋日文單字「{word}」的意思和讀音，格式簡潔"
        ) or f"找不到「{word}」的資料"
    jlpt = f"  {d['jlpt'][0].upper()}" if d.get("jlpt") else ""
    common = "  ★常用" if d.get("common") else ""
    meanings = call_gemini(
        f"把這些英文意思翻成繁體中文，逗號分隔，只給翻譯：{', '.join(d['meanings_en'])}"
    ) if d["meanings_en"] else "—"
    return (
        f"🇯🇵 {d['word']}（{d['reading']}）{jlpt}{common}\n"
        f"意思：{meanings}"
    )


def fetch_daily_japanese() -> str:
    word = random.choice(_JLPT_N5_WORDS)
    d = _jisho_lookup(word)
    if not d:
        return call_gemini(
            "給我一個 JLPT N5 日文單字，包含：單字、假名讀音、繁體中文意思、例句"
        ) or "今日單字查詢失敗"
    jlpt = d["jlpt"][0].upper() if d.get("jlpt") else "N5"
    meanings = call_gemini(
        f"把這些英文意思翻成繁體中文，逗號分隔，只給翻譯：{', '.join(d['meanings_en'])}"
    ) if d["meanings_en"] else "—"
    return (
        f"📖 今日日文單字（{jlpt}）\n\n"
        f"✏️ {d['word']}　読み：{d['reading']}\n"
        f"意思：{meanings}\n\n"
        f"試著造個句子看看！"
    )


def fetch_kanji(char: str) -> str:
    d = _kanji_lookup(char)
    if not d:
        return call_gemini(f"用繁體中文解釋日文漢字「{char}」的讀音和意思") or f"找不到「{char}」"
    jlpt = f"JLPT N{d['jlpt']}" if d.get("jlpt") else "—"
    on = "、".join(d["on"]) or "—"
    kun = "、".join(d["kun"]) or "—"
    meanings = "、".join(d["meanings"]) or "—"
    return (
        f"🈶 {d['kanji']}\n"
        f"音讀：{on}\n"
        f"訓讀：{kun}\n"
        f"意思：{meanings}\n"
        f"筆畫：{d['strokes']}　{jlpt}"
    )


def fetch_daily_kanji() -> str:
    char = random.choice(_JLPT_N5_KANJI)
    d = _kanji_lookup(char)
    if not d:
        return call_gemini(
            f"用繁體中文介紹日文漢字「{char}」，包含讀音、意思和例句"
        ) or "今日漢字查詢失敗"
    jlpt = f"JLPT N{d['jlpt']}" if d.get("jlpt") else "N5"
    on = "、".join(d["on"]) or "—"
    kun = "、".join(d["kun"]) or "—"
    meanings = "、".join(d["meanings"]) or "—"
    example = call_gemini(
        f"用「{char}」造一個簡單日文例句，附假名讀音和繁體中文翻譯，一行即可"
    ) or ""
    lines = [
        f"🈶 今日漢字：{d['kanji']}（{jlpt}）\n",
        f"音讀：{on}　訓讀：{kun}",
        f"意思：{meanings}　筆畫：{d['strokes']}",
    ]
    if example:
        lines.append(f"\n例句：{example}")
    return "\n".join(lines)


def fetch_spanish(word: str) -> str:
    try:
        r = requests.get(
            f"https://api.dictionaryapi.dev/api/v2/entries/es/{word}",
            timeout=8,
        )
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, list) and data:
                entry = data[0]
                meanings = entry.get("meanings", [])
                defs = []
                for m in meanings[:2]:
                    for defn in m.get("definitions", [])[:2]:
                        df = defn.get("definition", "")
                        if df:
                            defs.append(df)
                if defs:
                    phonetic = f"  [{entry.get('phonetic', '')}]" if entry.get("phonetic") else ""
                    defs_zh = call_gemini(
                        f"把以下西班牙文解釋翻成繁體中文，條列，只給翻譯：\n" + "\n".join(defs[:3])
                    ) or "\n".join(defs[:2])
                    return f"🇪🇸 {entry.get('word', word)}{phonetic}\n\n{defs_zh}"
    except Exception:
        pass
    return call_gemini(
        f"用繁體中文解釋西班牙文單字「{word}」的意思、詞性和一個例句"
    ) or f"找不到「{word}」的資料"


def fetch_daily_spanish() -> str:
    return call_gemini(
        "給我一個 A1-A2 等級的西班牙文單字，格式：\n"
        "📖 單字：xxx\n"
        "詞性：xxx\n"
        "中文意思：xxx\n"
        "例句：xxx（附中文翻譯）\n"
        "記憶技巧：一句話"
    ) or "今日西文單字查詢失敗"


def fetch_pokemon_detail(name: str) -> str:
    try:
        r = requests.get(
            f"https://pokeapi.co/api/v2/pokemon/{name.lower()}",
            timeout=10,
        )
        r.raise_for_status()
        d = r.json()
        pname = d["name"].capitalize()
        types = "、".join(t["type"]["name"].capitalize() for t in d["types"])
        height = d["height"] / 10
        weight = d["weight"] / 10
        abilities = "、".join(a["ability"]["name"].replace("-", " ").title() for a in d["abilities"][:3])
        sprite = d["sprites"]["front_default"]
        stats = {s["stat"]["name"]: s["base_stat"] for s in d["stats"]}
        hp = stats.get("hp", "?")
        atk = stats.get("attack", "?")
        spd = stats.get("speed", "?")
        return (
            f"🎮 #{d['id']} {pname}\n"
            f"屬性：{types}\n"
            f"身高：{height}m  體重：{weight}kg\n"
            f"特性：{abilities}\n"
            f"HP:{hp} 攻擊:{atk} 速度:{spd}",
            sprite,
        )
    except Exception:
        return f"找不到寶可夢「{name}」，確認英文名或 ID 是否正確", None


def fetch_random_meal() -> str:
    try:
        r = requests.get("https://www.themealdb.com/api/json/v1/1/random.php", timeout=8)
        meal = r.json()["meals"][0]
        name = meal["strMeal"]
        area = meal.get("strArea", "")
        category = meal.get("strCategory", "")
        ingredients = []
        for i in range(1, 21):
            ing = (meal.get(f"strIngredient{i}") or "").strip()
            if not ing:
                break
            measure = (meal.get(f"strMeasure{i}") or "").strip()
            ingredients.append(f"{measure} {ing}".strip())
        ing_str = "、".join(ingredients[:8]) + ("..." if len(ingredients) > 8 else "")
        return f"🍽️ 今日食譜：{name}\n地區：{area}　類型：{category}\n食材：{ing_str}"
    except Exception:
        return "🍽️ 廚師跑掉了，明天再說 😅"


def fetch_random_movie() -> str:
    if not TMDB_API_KEY:
        return "🎬 需要設定 TMDB_API_KEY（去 themoviedb.org 免費申請）"
    try:
        page = random.randint(1, 8)
        r = requests.get(
            "https://api.themoviedb.org/3/discover/movie",
            params={"api_key": TMDB_API_KEY, "language": "zh-TW",
                    "sort_by": "popularity.desc", "page": page},
            timeout=8,
        )
        movies = [m for m in r.json().get("results", []) if m.get("overview")]
        if not movies:
            return "🎬 電影院關門了，待會再試 😅"
        movie = random.choice(movies)
        title = movie["title"]
        orig = movie.get("original_title", "")
        year = (movie.get("release_date") or "")[:4]
        rating = movie.get("vote_average", 0)
        overview = (movie.get("overview") or "")[:80]
        title_line = f"《{title}》" + (f"（{orig}）" if orig != title else "")
        return f"🎬 今日電影推薦\n{title_line}\n{year}　⭐ {rating}/10\n{overview}..."
    except Exception:
        return "🎬 電影院關門了，待會再試 😅"


_ZODIAC = {
    "牡羊": "aries", "白羊": "aries", "金牛": "taurus", "雙子": "gemini",
    "巨蟹": "cancer", "獅子": "leo", "處女": "virgo", "天秤": "libra",
    "天蠍": "scorpio", "射手": "sagittarius", "摩羯": "capricorn",
    "水瓶": "aquarius", "雙魚": "pisces",
}
_ZODIAC_ZH = {v: k for k, v in _ZODIAC.items()}


def fetch_horoscope(text) -> str:
    if not RAPIDAPI_KEY:
        return "🔮 需要設定 RAPIDAPI_KEY（RapidAPI 免費申請）"
    sign_zh = next((k for k in _ZODIAC if k in text), None)
    if not sign_zh:
        signs = " / ".join(_ZODIAC.keys())
        return f"🔮 請說「今日天蠍」「今日牡羊」...\n支援：{signs}"
    sign_en = _ZODIAC[sign_zh]
    try:
        r = requests.post(
            f"https://aztro.p.rapidapi.com/?sign={sign_en}&day=today",
            headers={"X-RapidAPI-Key": RAPIDAPI_KEY, "X-RapidAPI-Host": "aztro.p.rapidapi.com"},
            timeout=8,
        )
        d = r.json()
        desc = d.get("description", "")
        mood = d.get("mood", "")
        color = d.get("color", "")
        lucky = d.get("lucky_number", "")
        compat = _ZODIAC_ZH.get(d.get("compatibility", "").lower(), d.get("compatibility", ""))
        return (
            f"🔮 今日{sign_zh}座運勢\n\n"
            f"{desc}\n\n"
            f"心情：{mood}　幸運色：{color}\n"
            f"幸運數字：{lucky}　速配星座：{compat}座"
        )
    except Exception:
        return "🔮 占星師在睡覺，待會再問 😴"


def _parse_reminder_date(s: str) -> str | None:
    from datetime import timedelta
    today = datetime.now(TW_TZ).date()
    if s in ["今天"]:
        return today.strftime("%Y-%m-%d")
    if s in ["明天", "明日"]:
        return (today + timedelta(days=1)).strftime("%Y-%m-%d")
    if s in ["後天"]:
        return (today + timedelta(days=2)).strftime("%Y-%m-%d")
    m = re.match(r'^(\d{1,2})[/月](\d{1,2})日?$', s)
    if m:
        try:
            from datetime import date as _d
            mo, dy = int(m.group(1)), int(m.group(2))
            t = _d(today.year, mo, dy)
            if t < today:
                t = _d(today.year + 1, mo, dy)
            return t.strftime("%Y-%m-%d")
        except ValueError:
            return None
    return None


def handle_add_todo(member: str, text: str) -> str:
    # 提醒我 明天 交報告
    m = re.match(r'^提醒我\s+(\S+)\s+(.+)', text)
    if m:
        target, date_s, content = member or "你", m.group(1), m.group(2)
    else:
        # 提醒 太后 明天 交報告
        m = re.match(r'^提醒\s+(\S+)\s+(\S+)\s+(.+)', text)
        if not m:
            return "格式：提醒 [人名] [日期] [事項]\n或：提醒我 明天 要做XXX\n日期支援：今天/明天/後天/6/5"
        target, date_s, content = m.group(1), m.group(2), m.group(3)

    date_str = _parse_reminder_date(date_s)
    if not date_str:
        return f"看不懂日期「{date_s}」\n支援：今天/明天/後天/6月5日/6/5"

    ok = add_todo(target, date_str, content, member or "")
    if not ok:
        return "記錄失敗，等一下再試 😢"
    date_display = date_str[5:].replace("-", "/")
    by_str = f"（{member} 幫你記的）" if target != member and member else ""
    return f"✅ 已幫 {target} 記下！\n📅 {date_display}：{content}{by_str}\n前一天晚上和當天都會提醒 🔔"


def handle_view_todos() -> str:
    todos = get_todos(status="待辦")
    if not todos:
        return "🎉 目前沒有待辦事項！"
    today = datetime.now(TW_TZ).strftime("%Y-%m-%d")
    lines = ["📋 待辦事項：\n"]
    for t in sorted(todos, key=lambda x: x["date"]):
        date_display = t["date"][5:].replace("-", "/")
        overdue = " ⚠️ 逾期" if t["date"] < today else ""
        by = f"（{t['created_by']} 記的）" if t["created_by"] and t["created_by"] != t["member"] else ""
        lines.append(f"• {t['member']}｜{date_display} {t['content']}{overdue}{by}")
    return "\n".join(lines)


def handle_complete_todo(member: str, text: str) -> str | None:
    content = re.sub(r'^完成待辦\s*', '', text).strip()
    if not content:
        return None
    result = complete_todo_by_content(member, content)
    if result:
        return f"✅ 完成！「{result['content']}」從待辦清單移除 🎉"
    return f"找不到「{content}」在你的待辦裡"


def handle_leave(_):
    return random.choice([
        "要去哪裡玩！！快分享行程 🌴",
        "假期來了！去吃好吃的記得帶我 😤",
        "終於可以休息了！計畫好要幹嘛了嗎 ✨",
        "放假最幸福了，好好充電 🔋",
    ])


def handle_overtime(_):
    return random.choice([
        "辛苦了... 快點做完早點回家 🥺",
        "加班人要補充糖分！！去喝一杯奶茶 🧋",
        "加班是暫時的，下班是永遠的 💪",
        "撐著！今天的加班今天過 😤",
    ])


def handle_tired(_):
    return random.choice([
        "累了就休息，你已經很努力了 🫶",
        "放下手機睡一覺吧，明天繼續 💤",
        "喝杯水 + 躺平 10 分鐘，馬上好一半 🧘",
        "累是有原因的，代表你有在用力活著 ✨",
    ])


def handle_food(_):
    return call_gemini(
        "有人不知道要吃什麼，請給一個有趣的台灣美食推薦，"
        "語氣像在跟朋友聊天，輕鬆幽默，不超過 3 句，加 1 個 emoji"
    ) or "去吃火鍋吧！百吃不厭 🍲"


def handle_travel(text):
    return call_gemini(
        f"有人說「{text}」，他可能要出去玩了。"
        "請給輕鬆有趣的旅遊溫馨提示，台灣年輕人語氣，3 條，加 emoji"
    ) or "出去玩記得：充電寶🔋 防曬🌞 訂位📱"


def handle_japanese_question(text):
    for pattern in [r"(.+?)日文怎麼說", r"(.+?)用日文怎麼說",
                    r"日文(.+?)怎麼說", r"(.+?)日語怎麼說"]:
        match = re.search(pattern, text)
        if match:
            word = match.group(1).strip()
            return call_gemini(
                f"有人問「{word}」日文怎麼說，請用以下格式回答（簡短）：\n"
                f"「{word}」的日文是：日文（假名）\n例句：一句日文\n翻譯：中文翻譯\n"
                f"語氣輕鬆友善。"
            )
    return None


def handle_mention(text, member=None):
    group_mems = get_memories(days=14)
    personal_mems = get_personal_memories(member, days=14) if member else []

    context = ""
    if group_mems:
        context += "【群組近況】\n"
        context += "\n".join(f"[{d}] {c}" for d, c in group_mems)
        context += "\n\n"
    if personal_mems:
        context += f"【{member} 的個人記憶】\n"
        context += "\n".join(f"[{d}] {c}" for d, c in personal_mems)
        context += "\n\n"

    return call_gemini(
        f"你是一個活潑有趣的朋友群 LINE 機器人，名字叫「小棉襖」。\n"
        f"{context}"
        f"{'傳訊息的是 ' + member + '，' if member else ''}"
        f"他說：「{text}」\n"
        f"請結合你對這個群組和這個人的了解，用台灣年輕人語氣回應，輕鬆幽默，不超過 3 句。"
        f"不要加『大家好』或自我介紹。"
    )


def handle_suggest_goals(member, text):
    topic = re.sub(r'^幫我想目標\s*', '', text).strip()
    cycle_id, day, total = get_cycle_info()
    current = get_goals(cycle_id).get(member, [])
    memories = get_memories(days=7)

    context_parts = []
    if current:
        context_parts.append(f"{member} 目前設的目標：{' / '.join(current)}")
    if topic:
        context_parts.append(f"方向偏好：{topic}")
    if memories:
        context_parts.append("群組最近狀況：" + "；".join(c[:80] for _, c in memories[-3:]))
    context = "\n".join(context_parts)

    result = call_gemini(
        f"請幫{member}想 3-5 個適合十天週期的個人目標。\n"
        f"{context}\n\n"
        "要求：具體可執行、適合每天打卡確認、有挑戰性但不過分。\n"
        "例如：每天走 6000 步、每天背 5 個單字、每天記帳、每天做 10 分鐘伸展\n"
        "格式：每行一個目標加 emoji，結尾附一行提醒如何設定。"
    )
    suffix = "\n\n💡 設定指令：設目標：目標1 / 目標2 / 目標3"
    return (result or "建議：\n💧 每天喝 2000ml 水\n🚶 每天走 6000 步\n📚 每天讀 20 分鐘書") + suffix


def handle_last_cycle():
    last_id = get_last_cycle_id()
    return build_summary_text(last_id)


# ─── Goal command handlers ────────────────────────────────

def parse_goals(text_after_prefix):
    text = re.sub(r'^\d+[.、]\s*', '', text_after_prefix.strip(), flags=re.MULTILINE)
    parts = re.split(r'[\n/、；;]+', text)
    return [p.strip() for p in parts if p.strip()][:5]


def handle_set_goals(member, text):
    after = re.sub(r'^設目標[：:]\s*', '', text).strip()
    if not after:
        return "目標內容不能空白！\n格式：設目標：目標1 / 目標2 / 目標3"

    goals = parse_goals(after)
    if not goals:
        return "沒讀到目標，試試：設目標：目標1 / 目標2 / 目標3"

    cycle_id, day, total = get_cycle_info()
    goals_preview = "\n".join(f"  {i+1}. {g}" for i, g in enumerate(goals))

    if day >= total:
        # 最後一天：存到下一週期
        next_id = get_next_cycle_id()
        next_start = get_next_cycle_start()
        threading.Thread(target=set_goals, args=(member, goals, next_id), daemon=True).start()
        return (
            f"✅ {member} 的下輪目標設定完成！\n\n"
            f"{goals_preview}\n\n"
            f"📅 {next_start} 週期開始生效 🎯"
        )

    threading.Thread(target=set_goals, args=(member, goals), daemon=True).start()
    return (
        f"✅ {member} 的十日目標設定完成！\n\n"
        f"{goals_preview}\n\n"
        f"📅 現在第 {day}/{total} 天，加油！💪"
    )


def handle_checkin(member, text, user_goals=None):
    content = re.sub(r'^打卡\s*', '', text).strip() or "打卡"
    day, total = add_checkin(member, content)
    if day == 0:
        return "打卡失敗 😢 等一下再試試？"

    streak = get_streak(member)
    streak_msg = ""
    if streak >= 7:
        streak_msg = f"🔥🔥 連續 {streak} 天！神人！"
    elif streak >= 5:
        streak_msg = f"🔥 連續 {streak} 天！太厲害了！"
    elif streak >= 3:
        streak_msg = f"🔥 連續 {streak} 天！繼續衝！"
    elif streak == 2:
        streak_msg = "連兩天了！保持！"

    if user_goals:
        goals_str = " / ".join(user_goals[:3])
        enc = call_gemini(
            f"有人叫{member}，他的十日目標是：{goals_str}。\n"
            f"今天他打卡說：「{content}」（第{day}/{total}天）\n"
            f"請給一句輕鬆真誠的鼓勵，提到他的目標，台灣年輕人語氣，不超過 2 句。"
        )
        if not enc:
            enc = random.choice(["太棒了！繼續保持！", "你最行！衝衝衝！", "很好！繼續！"])
    else:
        enc = random.choice(["太棒了！", "繼續保持！", "你最行！", "衝衝衝！", "很好！"])

    # 用實際打卡天數畫進度條，不用「今天第幾天」
    stats = get_checkin_stats()
    checked_count = len(stats.get(member, []))
    bar = "🟩" * checked_count + "⬜" * max(0, total - checked_count)
    parts = [f"✅ {member} 打卡成功！", f"📝 {content}", bar, f"第 {day}/{total} 天｜{enc}"]
    if streak_msg:
        parts.append(streak_msg)
    return "\n".join(parts)


def _goal_keyword(goal: str) -> str:
    """Extract core keyword from goal like '每天duolingo' → 'duolingo'."""
    key = re.sub(r'^每天\s*', '', goal).strip()
    key = re.sub(r'一件東西$|一次$|一下$|一個$', '', key).strip()
    return key or goal


def _goal_days(log: dict, goal: str, total: int) -> int:
    """Count days where any check-in content mentions the goal keyword."""
    kw = _goal_keyword(goal).lower()
    count = 0
    for contents in log.values():
        if any(kw in c.lower() for c in contents):
            count += 1
    return count


def handle_view_goals():
    cycle_id, day, total = get_cycle_info()
    goals = get_goals(cycle_id)
    stats = get_checkin_stats(cycle_id)

    if not goals and not stats:
        return (
            f"本週期（第 {day}/{total} 天）還沒有人設目標！\n\n"
            f"輸入：設目標：目標1 / 目標2 / 目標3\n"
            f"可設 3–5 個目標"
        )

    log = get_checkin_log(cycle_id)
    lines = [f"🎯 本週期目標（第 {day}/{total} 天）\n"]
    all_members = sorted(set(list(goals.keys()) + list(stats.keys())))
    for member in all_members:
        member_goals = goals.get(member, [])
        member_log = log.get(member, {})
        lines.append(f"👤 {member}")
        if member_goals:
            for g in member_goals:
                cnt = _goal_days(member_log, g, total)
                bar = "🟩" * cnt + "⬜" * max(0, total - cnt)
                kw = _goal_keyword(g)
                lines.append(f"  {kw}｜{bar} {cnt}/{total}")
        else:
            checked = stats.get(member, [])
            bar = "🟩" * len(checked) + "⬜" * max(0, total - len(checked))
            lines.append(f"  打卡｜{bar} {len(checked)}/{total}")
        lines.append("")
    return "\n".join(lines).strip()


def handle_cycle_progress():
    cycle_id, day, total = get_cycle_info()
    stats = get_checkin_stats(cycle_id)
    lines = [f"📅 十日週期第 {day}/{total} 天\n"]
    if stats:
        for member, days in sorted(stats.items()):
            bar = "🟩" * len(days) + "⬜" * (total - len(days))
            lines.append(f"{member}：{bar} {len(days)}/{total}")
    else:
        lines.append("還沒有人打卡 😶")
    return "\n".join(lines)


def handle_today_checkins():
    cycle_id, day, total = get_cycle_info()
    checkins = get_today_checkins(cycle_id)
    if not checkins:
        return f"今天（第 {day} 天）還沒有人打卡！快去打卡 💪"
    lines = [f"📋 今日打卡（第 {day}/{total} 天）\n"]
    for member, content in checkins.items():
        lines.append(f"✅ {member}：{content}")
    # Check who hasn't checked in
    goals = get_goals(cycle_id)
    missing = [m for m in goals if m not in checkins]
    if missing:
        lines.append(f"\n還沒打卡：{' / '.join(missing)} 快去！")
    return "\n".join(lines)


# ─── Main message handler ─────────────────────────────────

@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers.get("X-Line-Signature", "")
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return "OK"


@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    text = event.message.text.strip()
    reply_token = event.reply_token
    source = event.source
    group_id = getattr(source, 'group_id', None)
    user_id = getattr(source, 'user_id', None)

    # Update last activity + log message in background
    threading.Thread(target=update_last_activity, daemon=True).start()

    reply_text = None
    reply_image_url = None

    with ApiClient(configuration) as api_client:
        member_label = get_member_label(api_client, group_id, user_id)
        if _should_log(text):
            threading.Thread(
                target=log_chat_message, args=(member_label, text), daemon=True
            ).start()

        # ── 指令清單 ──
        if text in ("指令", "說明", "幫助", "help", "功能"):
            reply_text = (
                "📖 小棉襖指令清單\n"
                "\n🎯 十日目標\n"
                "設目標：目標1 / 目標2\n"
                "打卡 今天做了XXX\n"
                "查目標 / 進度 / 今日打卡\n"
                "我的打卡 / 上週期\n"
                "幫我想目標\n"
                "\n📌 待辦提醒\n"
                "提醒我 明天 要做XXX\n"
                "待辦 / 完成待辦 XXX\n"
                "\n🎲 趣味\n"
                "今日運勢 / 今日天蠍（任意星座）\n"
                "誰請客 / 抽籤 A / B\n"
                "配對 A B / 搖骰子 / 猜拳 剪刀\n"
                "貓貓 / 狗狗 / 狐狸 / 柴柴\n"
                "熊貓 / 無尾熊 / 浣熊\n"
                "今日宇宙 / 抽寶可夢\n"
                "今日食譜 / 推薦電影\n"
                "冷笑話 / 冷知識 / 給我建議\n"
                "動漫圖 / 激勵名言\n"
                "\n🎵 媒體 & 娛樂\n"
                "找歌 [歌名]\n"
                "查電影 [片名]\n"
                "電影台詞\n"
                "在哪看 [片名]\n"
                "動漫語錄\n"
                "川普語錄\n"
                "隨機梗圖\n"
                "諾里斯\n"
                "\n💪 生活\n"
                "今日運動 / 找運動 [部位]\n"
                "今日調酒 / 今日調酒 馬丁尼\n"
                "來一題 → 答 xxx → 答案\n"
                "我好無聊\n"
                "BMI 165 55\n"
                "熱量 [食物]\n"
                "消耗熱量 [活動] 30分鐘\n"
                "食譜 [食材]\n"
                "金價\n"
                "天文冷知識 / 數字冷知識\n"
                "\n📖 查詢\n"
                "國家 [名稱]\n"
                "找書 [書名]\n"
                "寶可夢 [英文名/ID]\n"
                "\n🌐 翻譯\n"
                "翻 日文 [文字]\n"
                "翻 英文 [文字]\n"
                "（支援：日/英/韓/法/西/德/泰/越等）\n"
                "摘要 [長文]\n"
                "\n🔧 實用\n"
                "匯率 / 天氣 [城市]\n"
                "倒數 6/15\n"
                "XXX日文怎麼說\n"
                "查日文 [單字]\n"
                "今日日文單字\n"
                "漢字 [字]\n"
                "今日漢字\n"
                "查西文 [單字]\n"
                "今日西文單字\n"
                "叫我 [暱稱]\n"
                "@小棉襖 問任何問題"
            )

        # ── 隱藏指令 ──
        elif text == "!groupid":
            reply_text = f"Group ID: {group_id or '非群組訊息'}"

        # ── 暱稱登記 ──
        elif nick_match := re.match(r'^叫我\s*(.+)$', text):
            nickname = nick_match.group(1).strip()
            ok = set_nickname(user_id, nickname)
            reply_text = f"好的！之後叫你「{nickname}」了 👋" if ok else "登記失敗，等一下再試 😢"

        # ── 十日目標：設目標 ──
        elif re.match(r'^設目標[：:]', text):
            reply_text = handle_set_goals(member_label, text)

        # ── 十日目標：打卡 ──
        elif text.startswith("打卡"):
            cycle_id, _, _ = get_cycle_info()
            goals_dict = get_goals(cycle_id)
            user_goals = goals_dict.get(member_label)
            reply_text = handle_checkin(member_label, text, user_goals)

        # ── 十日目標：查詢 ──
        elif text in ("查目標", "看目標", "目標"):
            reply_text = handle_view_goals()

        elif text in ("今天第幾天", "幾天了", "進度", "打卡進度"):
            reply_text = handle_cycle_progress()

        elif text in ("今天打卡了嗎", "今日打卡", "誰打卡了"):
            reply_text = handle_today_checkins()

        elif text in ("上週期", "上次總結", "上輪總結"):
            reply_text = handle_last_cycle()

        elif re.match(r'^幫我想目標', text):
            reply_text = handle_suggest_goals(member_label, text)

        elif text in ("我的打卡", "打卡記錄", "我打了幾天"):
            cycle_id, day, total = get_cycle_info()
            stats = get_checkin_stats(cycle_id)
            checked = sorted(stats.get(member_label, []))
            streak = get_streak(member_label)
            if not checked:
                reply_text = f"你這週期還沒打卡喔！快去打卡 💪\n指令：打卡 今天做了XXX"
            else:
                bar = "🟩" * len(checked) + "⬜" * (total - len(checked))
                days_str = "、".join(f"第{d}天" for d in checked)
                streak_line = f"🔥 目前連續 {streak} 天" if streak >= 2 else ""
                lines = [
                    f"📋 {member_label} 的打卡記錄",
                    f"{bar}  {len(checked)}/{total} 天",
                    days_str,
                ]
                if streak_line:
                    lines.append(streak_line)
                reply_text = "\n".join(lines)

        # ── 趣味功能 ──
        elif re.search(r'今日運勢|運勢|占卜', text):
            reply_text = handle_fortune()

        elif re.search(r'誰請客|今天誰請|誰買單|今天誰買', text):
            reply_text = handle_who_pays(text)

        elif re.search(r'^(抽籤|幫我選|幫我決定|選一個)', text):
            reply_text = handle_draw_lots(text)

        elif re.search(r'來隻貓|貓貓|來貓', text):
            reply_image_url = fetch_cat_image()
            reply_text = random.choice(["🐱 貓貓來了！", "喵～ 🐾", "🐱 今日貓貓！"])

        elif re.search(r'來隻狗|狗狗|來狗', text):
            reply_image_url = fetch_dog_image()
            reply_text = random.choice(["🐶 狗狗來了！", "汪！🐾", "🐶 今日狗狗！"])

        elif re.search(r'狐狸|來隻狐', text):
            reply_image_url = fetch_fox_image()
            reply_text = random.choice(["🦊 狐狸來了！", "🦊 今日狐狸！", "啾啾～ 🦊"])

        elif re.search(r'柴柴|柴犬|來隻柴', text):
            reply_image_url = fetch_shiba_image()
            reply_text = random.choice(["🐕 柴柴！！", "wow such shiba 🐕", "今日柴柴！🐕"])

        elif re.search(r'熊貓|來隻熊貓', text):
            reply_image_url = fetch_animal_image("panda")
            reply_text = random.choice(["🐼 熊貓！", "今日熊貓 🐼", "圓滾滾來了 🐼"])

        elif re.search(r'無尾熊|來隻無尾熊|無尾熊', text):
            reply_image_url = fetch_animal_image("koala")
            reply_text = random.choice(["🐨 無尾熊！", "今日無尾熊 🐨", "抱抱樹 🐨"])

        elif re.search(r'浣熊|來隻浣熊', text):
            reply_image_url = fetch_animal_image("raccoon")
            reply_text = random.choice(["🦝 浣熊！", "今日浣熊 🦝", "小偷熊來了 🦝"])

        elif re.search(r'今日宇宙|NASA|宇宙照片', text):
            reply_text, reply_image_url = fetch_nasa_apod()

        elif re.search(r'抽寶可夢|今日寶可夢|來隻寶可夢', text):
            reply_text = fetch_random_pokemon()

        elif re.match(r'^配對', text):
            reply_text = handle_pairing(text)

        elif re.search(r'搖骰子|擲骰子|搖\d*[顆個]骰', text):
            reply_text = handle_dice(text)

        elif re.match(r'^猜拳', text):
            reply_text = handle_rps(text)

        elif re.search(r'給我建議|今日忠告', text):
            reply_text = fetch_advice()

        elif re.search(r'今日食譜|隨機食譜|吃什麼食譜|今天做什麼', text):
            reply_text = fetch_random_meal()

        elif re.search(r'推薦電影|今日電影|隨機電影|看什麼電影', text):
            reply_text = fetch_random_movie()

        elif re.search(r'今日(牡羊|白羊|金牛|雙子|巨蟹|獅子|處女|天秤|天蠍|射手|摩羯|水瓶|雙魚)', text):
            reply_text = fetch_horoscope(text)

        # ── 待辦 ──
        elif re.match(r'^提醒(我|\s)', text):
            reply_text = handle_add_todo(member_label, text)

        elif text in ("待辦", "查提醒", "查待辦"):
            reply_text = handle_view_todos()

        elif re.match(r'^完成待辦', text):
            reply_text = handle_complete_todo(member_label, text) or f"格式：完成待辦 [事項名稱]"

        elif re.search(r'倒數|還有幾天|距離', text):
            reply_text = handle_countdown(text)

        # ── 實用功能 ──
        elif re.search(r'匯率|美金|日幣|換錢|外幣', text):
            reply_text = get_exchange_rate(text)

        elif re.search(r'天氣', text):
            reply_text = get_weather_v2(text)

        # ── 翻譯 ──
        elif re.match(r'^翻\s', text):
            reply_text = handle_translate(user_id, text)

        # ── 找歌 ──
        elif m := re.match(r'^找歌\s*(.+)$', text):
            reply_text = fetch_spotify_track(m.group(1).strip())

        # ── 查電影 ──
        elif m := re.match(r'^查電影\s*(.+)$', text):
            reply_text = fetch_imdb(m.group(1).strip())

        # ── 電影台詞 ──
        elif text == "電影台詞":
            reply_text = fetch_movie_quote()

        # ── 在哪看 ──
        elif m := re.match(r'^在哪看\s*(.+)$', text):
            reply_text = fetch_streaming(m.group(1).strip())

        # ── 今日運動 / 找運動 ──
        elif re.match(r'^(今日運動|找運動)', text):
            body_part = None
            m2 = re.match(r'^找運動\s*(.+)$', text)
            if m2:
                body_part = m2.group(1).strip()
            reply_text = fetch_exercise(body_part)

        # ── 來一題（互動問答，有狀態）──
        elif text == "來一題":
            d = _ninja("/v1/trivia")
            if d is _QUOTA:
                reply_text = _QUOTA_MSG
            elif d and isinstance(d, list):
                q = d[0]
                question = q.get("question", "")
                answer = q.get("answer", "")
                gid = group_id or "default"
                _QUIZ_STATE[gid] = {"question": question, "answer": answer}
                cat = q.get("category", "")
                reply_text = (
                    f"🧠 來答題！（{cat}）\n\n{question}\n\n"
                    f"傳「答 你的答案」作答，傳「答案」看解答"
                )
            else:
                reply_text = "🧠 題庫暫時關閉，待會再試"

        elif m := re.match(r'^答\s+(.+)$', text):
            gid = group_id or "default"
            if gid in _QUIZ_STATE:
                state = _QUIZ_STATE[gid]
                user_ans = m.group(1).strip().lower()
                correct = state["answer"].lower()
                if correct in user_ans or user_ans in correct:
                    _QUIZ_STATE.pop(gid)
                    reply_text = f"🎉 答對了！答案是：{state['answer']}"
                else:
                    reply_text = "❌ 不對喔，再想想！（傳「答案」放棄）"

        elif text == "答案":
            gid = group_id or "default"
            if gid in _QUIZ_STATE:
                state = _QUIZ_STATE.pop(gid)
                reply_text = f"💡 答案是：{state['answer']}"

        # ── 今日調酒 ──
        elif re.match(r'^今日調酒', text):
            m2 = re.match(r'^今日調酒\s*(.+)$', text)
            name = m2.group(1).strip() if m2 else None
            reply_text = fetch_cocktail(name)

        # ── 動漫語錄 ──
        elif text == "動漫語錄":
            reply_text = fetch_anime_quote()

        # ── 我好無聊 ──
        elif text == "我好無聊":
            reply_text = fetch_random_activity()

        # ── 川普語錄 ──
        elif text == "川普語錄":
            reply_text = fetch_trump_quote()

        # ── 查超英 ──
        elif m := re.match(r'^查超英\s*(.+)$', text):
            reply_text = fetch_superhero(m.group(1).strip())

        # ── 隨機梗圖 ──
        elif text == "隨機梗圖":
            meme_text, meme_url = fetch_meme()
            reply_text = meme_text
            reply_image_url = meme_url

        # ── 諾里斯 ──
        elif text == "諾里斯":
            reply_text = fetch_chuck_norris()

        # ── 摘要 ──
        elif m := re.match(r'^摘要\s*(.+)$', text, re.DOTALL):
            reply_text = fetch_tldr(m.group(1).strip())

        # ── 國家資訊 ──
        elif m := re.match(r'^國家\s*(.+)$', text):
            reply_text = fetch_country(m.group(1).strip())

        # ── 找書 ──
        elif m := re.match(r'^找書\s*(.+)$', text):
            reply_text = fetch_book(m.group(1).strip())

        # ── 動漫圖 ──
        elif text == "動漫圖":
            reply_text, reply_image_url = fetch_waifu()

        # ── 激勵名言 ──
        elif text in ("激勵名言", "今日名言"):
            q = fetch_quotable()
            reply_text = q if q else "✨ 今天也要加油！"

        # ── 寶可夢詳細 ──
        elif m := re.match(r'^寶可夢\s*(.+)$', text):
            result = fetch_pokemon_detail(m.group(1).strip())
            if isinstance(result, tuple):
                reply_text, reply_image_url = result
            else:
                reply_text = result

        # ── 日文字典 ──
        elif m := re.match(r'^查日文\s*(.+)$', text):
            reply_text = fetch_jisho(m.group(1).strip())

        elif text in ("今日日文單字", "日文單字", "學日文"):
            reply_text = fetch_daily_japanese()

        elif m := re.match(r'^漢字\s*([^\s])$', text):
            reply_text = fetch_kanji(m.group(1))

        elif text in ("今日漢字", "學漢字"):
            reply_text = fetch_daily_kanji()

        # ── 西班牙文字典 ──
        elif m := re.match(r'^查西文\s*(.+)$', text):
            reply_text = fetch_spanish(m.group(1).strip())

        elif text in ("今日西文單字", "西文單字", "學西文"):
            reply_text = fetch_daily_spanish()

        # ── 日文問題 ──
        elif (jp := handle_japanese_question(text)):
            reply_text = jp

        # ── 關鍵字回覆（隨機觸發避免煩人）──
        elif re.search(r'冷笑話', text):
            reply_text = handle_joke(text)

        elif re.search(r'冷知識', text):
            reply_text = handle_fun_fact(text)

        elif re.search(r'特休|年假|請假|休假', text) and random.random() < 0.65:
            reply_text = handle_leave(text)

        elif re.search(r'加班', text) and random.random() < 0.6:
            reply_text = handle_overtime(text)

        elif re.search(r'好累|超累|累死|累爆', text) and random.random() < 0.55:
            reply_text = handle_tired(text)

        elif re.search(r'吃什麼|午餐|晚餐|要吃', text) and random.random() < 0.65:
            reply_text = handle_food(text)

        elif re.search(r'出去玩|要去玩|去旅遊|旅行', text) and random.random() < 0.7:
            reply_text = handle_travel(text)

        # ── BMI ──
        elif m := re.match(r'^BMI\s+(\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)$', text, re.IGNORECASE):
            h, w = float(m.group(1)), float(m.group(2))
            r2 = calc_bmi(h, w)
            reply_text = f"⚖️ BMI 計算\n身高 {h}cm / 體重 {w}kg\n\nBMI：{r2['bmi']}\n{r2['category']}"

        elif re.match(r'^BMI$', text, re.IGNORECASE):
            reply_text = "請傳「BMI 身高 體重」\n例：BMI 165 55"

        # ── 熱量 ──
        elif m := re.match(r'^熱量\s+(.+)$', text):
            reply_text = fetch_nutrition(m.group(1).strip())

        # ── 消耗熱量 ──
        elif m := re.match(r'^消耗熱量\s+(.+?)(?:\s+(\d+)分鐘?)?$', text):
            activity = m.group(1).strip()
            duration = int(m.group(2)) if m.group(2) else 30
            reply_text = fetch_calories_burned(activity, duration)

        # ── 金價 ──
        elif text in ("金價", "今日金價", "黃金價格"):
            reply_text = fetch_gold_price()

        # ── 天文冷知識 ──
        elif text in ("天文冷知識", "科學冷知識", "宇宙冷知識"):
            reply_text = fetch_astronomy_fact()

        # ── 數字冷知識 ──
        elif text in ("數字冷知識", "數字趣聞"):
            reply_text = fetch_number_fact()

        # ── 食譜 [食材] ──
        elif m := re.match(r'^食譜\s+(.+)$', text):
            reply_text = fetch_recipe_by_ingredient(m.group(1).strip())

        # ── 待翻譯輸入 ──
        elif user_id and user_id in _TRANSLATE_PENDING:
            if text in ("取消", "算了", "不翻了"):
                _TRANSLATE_PENDING.pop(user_id, None)
                reply_text = "好，取消翻譯 👌"
            else:
                lang_code = _TRANSLATE_PENDING.pop(user_id)
                reply_text = translate_text(text, lang_code)

        # ── 被點名 ──
        elif (BOT_NAME in text or BOT_DISPLAY_NAME in text
              or "機器人" in text or "小棉襖" in text or "bot" in text.lower()):
            reply_text = handle_mention(text, member=member_label)

        if not reply_text and not reply_image_url:
            return

        messages = []
        if reply_text:
            messages.append(TextMessage(text=reply_text))
        if reply_image_url:
            messages.append(ImageMessage(
                original_content_url=reply_image_url,
                preview_image_url=reply_image_url,
            ))

        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message(
            ReplyMessageRequest(reply_token=reply_token, messages=messages)
        )


@handler.add(MemberJoinedEvent)
def handle_join(event):
    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(
                    text="歡迎加入！我是小棉襖 🧸\n輸入「叫我XXX」先綁定你的暱稱！"
                )]
            )
        )


@app.route("/health")
def health():
    return "OK"


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
