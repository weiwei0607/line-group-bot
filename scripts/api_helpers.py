"""
LINE Group Bot — API helpers & command logic utilities.
Extracted from line_webhook.py to keep the webhook entry-point slim.
"""

import os
import re
import base64
import random
import threading
import requests
from collections import deque
from datetime import datetime, timedelta
from goal_tracker import TW_TZ
from linebot.v3.messaging import (
    Configuration, ApiClient, MessagingApi, MessagingApiBlob,
    ReplyMessageRequest, TextMessage, ImageMessage
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
    GOAL_SHEET_ID, _get_token, _sheets_get, _sheets_append, _sheets_update
)

from config import *
from line_push import push_messages, push_text



def _should_log(text: str) -> bool:
    if text.startswith("!"):
        return False
    if text in _SKIP_LOG:
        return False
    if re.match(r'^(叫我|我是|設目標[：:]|抽籤|幫我選|幫我決定|選一個|幫我想目標|QR\s|縮網址\s|找影片\s|改寫\s|配對星座\s|查日文\s|查西文\s|漢字\s|食譜\s|熱量\s|消耗熱量\s|BMI\s|寶可夢\s|找書\s|國家\s|投票\s|投[ABCD]$)', text, re.IGNORECASE):
        return False
    return True


from utils import call_gemini, send_telegram_alert
import logging
logger = logging.getLogger(__name__)


# ─── 智慧翻譯：RapidAPI 為主，Gemini 為 fallback ───────────

def smart_translate(text: str, target: str = "zh-TW") -> str:
    """先嘗試 RapidAPI 翻譯，失敗再用 Gemini"""
    if not text or not text.strip():
        return text
    # 已經是中文直接回傳
    if any(ord(c) > 127 for c in text[:30]):
        return text
    # 1. 嘗試 OpenL Translate
    try:
        tl = target.split("-")[0] if "-" in target else target
        r = requests.post(
            "https://openl-translate.p.rapidapi.com/translate/bulk",
            headers={"Content-Type": "application/json", "x-rapidapi-host": "openl-translate.p.rapidapi.com", "x-rapidapi-key": (_RAPIDAPI_KEYS[0] if _RAPIDAPI_KEYS else "")},
            json={"target_lang": tl, "text": [text]},
            timeout=8,
        )
        if r.status_code == 200:
            d = r.json()
            if isinstance(d, dict):
                txts = d.get("translatedTexts")
                if txts and isinstance(txts, list) and len(txts) > 0 and txts[0]:
                    return txts[0]
                for k in ("translations", "translated_texts", "text", "result"):
                    v = d.get(k)
                    if v and isinstance(v, list) and len(v) > 0:
                        return v[0]
            if isinstance(d, list) and len(d) > 0 and d[0]:
                return d[0]
    except Exception as _exc:
        logger.warning("API error: %s", _exc)
        pass
    # 2. 嘗試 Just Translated（目前 Yandex v1 回 410，保留待修復）
    try:
        tl = target.split("-")[0] if "-" in target else target
        r = requests.get(
            "https://just-translated.p.rapidapi.com/",
            headers={"x-rapidapi-host": "just-translated.p.rapidapi.com", "x-rapidapi-key": (_RAPIDAPI_KEYS[0] if _RAPIDAPI_KEYS else "")},
            params={"lang": tl, "text": text},
            timeout=3,
        )
        if r.status_code == 200:
            d = r.json()
            if isinstance(d, dict):
                for k in ("translatedText", "translation", "text", "result", "translated"):
                    v = d.get(k)
                    if v and isinstance(v, str):
                        return v
            if isinstance(d, str):
                return d
    except Exception as _exc:
        logger.warning("API error: %s", _exc)
        pass
    # 3. Fallback 到 Gemini
    result = call_gemini(f"翻成繁體中文，只給翻譯結果：{text}")
    return result or text


# ─── LINE helpers ─────────────────────────────────────────

def get_display_name(api_client, group_id, user_id):
    try:
        line_bot_api = MessagingApi(api_client)
        profile = line_bot_api.get_group_member_profile(group_id, user_id)
        return profile.display_name
    except Exception as _exc:
        return "某人"


def get_member_label(api_client, group_id, user_id):
    nick = get_nickname(user_id)
    if nick:
        return nick
    return get_display_name(api_client, group_id, user_id)


# ─── Weather ──────────────────────────────────────────────

TW_CITIES = ["台北", "新北", "桃園", "台中", "台南", "高雄",
             "基隆", "花蓮", "台東", "宜蘭", "嘉義", "新竹"]


_WEEKDAY_NAMES = ["一", "二", "三", "四", "五", "六", "日"]
_OM_WMO = {
    0: "☀️ 晴天", 1: "🌤 大致晴", 2: "⛅️ 部分多雲", 3: "☁️ 陰天",
    45: "🌫 有霧", 48: "🌫 有霧",
    51: "🌦 毛毛雨", 53: "🌦 毛毛雨", 55: "🌧 小雨",
    61: "🌧 小雨", 63: "🌧 中雨", 65: "🌧 大雨",
    71: "🌨 小雪", 73: "🌨 中雪", 75: "❄️ 大雪",
    80: "🌦 陣雨", 81: "🌧 陣雨", 82: "⛈ 大陣雨",
    95: "⛈ 雷雨", 96: "⛈ 雷雨夾冰雹", 99: "⛈ 雷雨夾冰雹",
}


def _parse_date_offset(text: str) -> tuple[int, str] | None:
    """從中文文字解析日期偏移，回傳 (offset_days, 描述)"""
    text = text.replace(" ", "").replace("?", "").replace("？", "")
    # 今天
    if re.search(r"^(今天|今日|現在)", text) or text in ["今天", "今日"]:
        return (0, "今天")
    # 明天
    if re.search(r"^(明天|明日)", text) or text in ["明天", "明日"]:
        return (1, "明天")
    # 後天
    if re.search(r"^(後天)", text) or text in ["後天"]:
        return (2, "後天")
    # 大後天
    if re.search(r"^(大後天)", text) or text in ["大後天"]:
        return (3, "大後天")
    # 星期/週/禮拜/周
    weekday_map = {
        "一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5,
        "日": 6, "天": 6,
        "1": 0, "2": 1, "3": 2, "4": 3, "5": 4, "6": 5, "7": 6,
    }
    m = re.search(r"(下?[星期週禮礼拜周])([一二三四五六日天1234567])", text)
    if m:
        prefix = m.group(1)
        day_char = m.group(2)
        target = weekday_map.get(day_char)
        if target is not None:
            today = datetime.now(TW_TZ).weekday()
            diff = (target - today) % 7
            is_next = prefix.startswith("下")
            if is_next:
                offset = diff + 7
            else:
                offset = diff
            name = _WEEKDAY_NAMES[target]
            desc = f"{'下週' if is_next else '本週'}星期{name}"
            return (offset, desc)
    return None


def _get_om_forecast():
    """Open-Meteo 7天預報（台北座標）"""
    try:
        r = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": 25.04, "longitude": 121.53,
                "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weathercode",
                "timezone": "Asia/Taipei", "forecast_days": 7,
            },
            timeout=10,
        )
        d = r.json()["daily"]
        forecast = []
        for i in range(7):
            code = int(d.get("weathercode", [0]*7)[i])
            forecast.append({
                "condition": _OM_WMO.get(code, "天氣不明"),
                "temp_max": round(d["temperature_2m_max"][i]),
                "temp_min": round(d["temperature_2m_min"][i]),
                "rain_prob": round(d["precipitation_probability_max"][i]),
            })
        return forecast
    except Exception as _exc:
        logger.warning("API error: %s", _exc)
        return None


def _weather_advice(condition: str, rain_prob: int, temp_max: int) -> str:
    advice = []
    if rain_prob >= 60:
        advice.append("☔ 記得帶傘！")
    elif rain_prob >= 30:
        advice.append("🌂 可能會下雨，建議帶傘")
    if "晴" in condition and temp_max >= 30:
        advice.append("🧴 天氣炎熱，記得擦防曬")
    elif "晴" in condition and temp_max >= 28:
        advice.append("🌞 天氣不錯，注意防曬")
    if temp_max <= 18:
        advice.append("🧥 氣溫較低，記得穿暖")
    return "\n".join(advice) if advice else ""


def _format_om_weather(offset: int, desc: str) -> str:
    """Open-Meteo 格式化指定日期天氣"""
    forecast = _get_om_forecast()
    if not forecast:
        return "（天氣資料取得失敗）"
    if offset < 0 or offset >= len(forecast):
        return "❌ 目前只支援未來7天的天氣預報"
    day = forecast[offset]
    target = datetime.now(TW_TZ) + timedelta(days=offset)
    date_str = target.strftime("%m/%d")
    weekday = _WEEKDAY_NAMES[target.weekday()]
    advice = _weather_advice(day["condition"], day["rain_prob"], day["temp_max"])
    lines = [
        f"{day['condition']}　最高 {day['temp_max']}° / 最低 {day['temp_min']}°",
        f"降雨機率 {day['rain_prob']}%",
    ]
    if advice:
        lines.append(f"\n💡 出門建議：\n{advice}")
    return f"📅 {date_str}（{weekday}）\n" + "\n".join(lines)


def _format_om_rain_check(offset: int, desc: str) -> str:
    """回答「會不會下雨」"""
    forecast = _get_om_forecast()
    if not forecast:
        return "（天氣資料取得失敗）"
    if offset < 0 or offset >= len(forecast):
        return f"❌ {desc} 超出7天預報範圍，目前只能查未來7天喔"
    day = forecast[offset]
    target_date = (datetime.now(TW_TZ) + timedelta(days=offset)).strftime("%m/%d")
    rain_prob = day["rain_prob"]
    if rain_prob >= 70:
        rain_msg = f"會下雨喔！🌧 降雨機率高達 {rain_prob}%"
    elif rain_prob >= 40:
        rain_msg = f"有可能會下雨 🌦 降雨機率 {rain_prob}%"
    elif rain_prob >= 20:
        rain_msg = f"有小機率下雨 ☁️ 降雨機率 {rain_prob}%"
    else:
        rain_msg = f"應該不會下雨 ☀️ 降雨機率只有 {rain_prob}%"
    advice = _weather_advice(day["condition"], day["rain_prob"], day["temp_max"])
    result = (
        f"📅 {desc}（{target_date}）\n"
        f"{rain_msg}\n"
        f"最高 {day['temp_max']}° / 最低 {day['temp_min']}°　{day['condition']}"
    )
    if advice:
        result += f"\n\n💡 出門建議：\n{advice}"
    return result


def get_weather(text):
    city = "台北"
    for c in TW_CITIES:
        if c in text:
            city = c
            break
    try:
        resp = requests.get(f"https://wttr.in/{city}?format=3&lang=zh&m", timeout=8)
        return f"🌤 {resp.text.strip()}\n（資料來源：wttr.in）"
    except Exception as _exc:
        return f"天氣查詢失敗，去 Google 查 {city} 天氣吧 😅"


# ─── Exchange rate ────────────────────────────────────────

def get_exchange_rate(text):
    pairs = [("USD", "美金", "$"), ("JPY", "日幣", "¥"),
             ("EUR", "歐元", "€"), ("KRW", "韓元", "₩"),
             ("CNY", "人民幣", "¥")]
    targets = [p for p in pairs if p[1] in text or p[0] in text.upper()]
    if not targets:
        targets = [("USD", "美金", "$"), ("JPY", "日幣", "¥"), ("CNY", "人民幣", "¥")]
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
    except Exception as _exc:
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
    except Exception as _exc:
        logger.warning("API error: %s", _exc)
        return None


def fetch_dog_image() -> str | None:
    try:
        r = requests.get("https://dog.ceo/api/breeds/image/random", timeout=8)
        url = r.json().get("message", "")
        return url if url.startswith("https") else None
    except Exception as _exc:
        logger.warning("API error: %s", _exc)
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
    except Exception as _exc:
        return "🎮 寶可夢跑掉了，再抽一次吧！"


def fetch_advice() -> str:
    try:
        r = requests.get("https://api.adviceslip.com/advice", timeout=8)
        advice = r.json()["slip"]["advice"]
        return f"💡 今日忠告（英）\n{advice}"
    except Exception as _exc:
        return "💡 建議你今天多喝水 🫗"


def fetch_fox_image() -> str | None:
    try:
        r = requests.get("https://randomfox.ca/floof/", timeout=8)
        return r.json().get("image")
    except Exception as _exc:
        logger.warning("API error: %s", _exc)
        return None


def fetch_shiba_image() -> str | None:
    try:
        r = requests.get("https://dog.ceo/api/breed/shiba/images/random", timeout=8)
        return r.json().get("message")
    except Exception as _exc:
        logger.warning("API error: %s", _exc)
        return None


def fetch_animal_image(animal: str) -> str | None:
    try:
        r = requests.get(f"https://some-random-api.com/animal/{animal}", timeout=8)
        return r.json().get("image")
    except Exception as _exc:
        logger.warning("API error: %s", _exc)
        return None


def fetch_nasa_apod() -> tuple[str, str | None]:
    cached = _daily_cached("nasa_apod")
    if cached:
        return cached
    for attempt in range(3):
        try:
            r = requests.get(
                "https://api.nasa.gov/planetary/apod",
                params={"api_key": NASA_API_KEY},
                timeout=25 if attempt == 0 else 40,
            )
            d = r.json()
            title = d.get("title", "")
            explanation = (d.get("explanation") or "")[:400]
            media_type = d.get("media_type", "image")
            url = d.get("url", "") if media_type == "image" else None
            text = f"🌌 今日宇宙：{title}\n{explanation}..."
            result = (text, url)
            _daily_cache_set("nasa_apod", result)
            return result
        except requests.exceptions.Timeout:
            if attempt < 2:
                import time
                time.sleep(2)
                continue
            return "🌌 NASA 伺服器回應較慢，請稍後再試 🌌", None
        except Exception as _exc:
            return "🌌 今日宇宙：NASA 暫時無法連線 😢", None
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
                logger.warning("[RapidAPI] 429 key %s/%s: %s%s", (_rapid_idx+attempt)%n+1, n, host, path)
                continue  # try next key
            r.raise_for_status()
            _rapid_idx = (_rapid_idx + attempt + 1) % n  # advance after success
            return r.json()
        except Exception as e:
            logger.warning("[RapidAPI] %s", "host}{path} → {e")
            return None
    logger.warning("[RapidAPI] all %s keys quota exceeded: %s%s", n, host, path)
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
                logger.warning("[api-ninjas] 429 key %s/%s: %s", (_ninja_idx+attempt)%n+1, n, path)
                continue  # try next key
            r.raise_for_status()
            _ninja_idx = (_ninja_idx + attempt + 1) % n  # advance after success
            return r.json()
        except Exception as e:
            logger.warning("[api-ninjas] %s", "path} → {e")
            return None
    # all ninja keys exhausted, fall back to RapidAPI
    return _rapid("get", "api-ninjas.p.rapidapi.com", path, **kwargs)


# ─── Translation ──────────────────────────────────────────

_TRANSLATE_PENDING: dict = {}
_QUIZ_STATE: dict = {}  # group_id -> {question, answer, options?, correct_letter?}
_VOTE_STATE: dict = {}  # group_id -> {question, options: list, votes: {nick: option}, ts}

# ─── 短期對話記憶 ──────────────────────────────────────────
_CHAT_MEMORY: deque = deque(maxlen=20)  # (nickname, text) tuples, rolling window

def _remember(nickname: str, text: str):
    _CHAT_MEMORY.append((nickname, text))

def _get_recent_context(n: int = 8) -> str:
    recent = list(_CHAT_MEMORY)[-n:]
    if not recent:
        return ""
    return "\n".join(f"{nick}：{msg}" for nick, msg in recent)

# ─── 當日 Cache ────────────────────────────────────────────
_DAILY_CACHE: dict = {}  # (key, date_str) -> result

def _daily_cached(key: str):
    date_str = datetime.now(TW_TZ).strftime("%Y-%m-%d")
    return _DAILY_CACHE.get((key, date_str))

def _daily_cache_set(key: str, value):
    date_str = datetime.now(TW_TZ).strftime("%Y-%m-%d")
    _DAILY_CACHE[(key, date_str)] = value
    # 清掉昨天的 key
    for k in list(_DAILY_CACHE.keys()):
        if k[1] != date_str:
            del _DAILY_CACHE[k]


# ─── Async reply → push helper ────────────────────────────

# LINE messaging configuration (local copy for _async_push placeholder reply)
_configuration = None

def _get_configuration():
    global _configuration
    if _configuration is None:
        token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
        _configuration = Configuration(access_token=token)
    return _configuration


def _async_push(reply_token: str, placeholder: str, fn, *args):
    """Reply ⏳ immediately, run fn(*args) in background, push actual result."""
    cfg = _get_configuration()
    with ApiClient(cfg) as api_client:
        MessagingApi(api_client).reply_message(
            ReplyMessageRequest(reply_token=reply_token,
                                messages=[TextMessage(text=placeholder)])
        )
    def _run():
        try:
            result = fn(*args)
            if isinstance(result, tuple):
                text_r, img_r = result[0], result[1] if len(result) > 1 else None
            else:
                text_r, img_r = result, None
            msgs = []
            if text_r:
                msgs.append({"type": "text", "text": str(text_r)[:4900]})
            if img_r:
                msgs.append({"type": "image",
                             "originalContentUrl": img_r,
                             "previewImageUrl": img_r})
            push_messages(LINE_GROUP_ID, msgs)
        except Exception as e:
            logger.warning("async_push error: %s", e)
            send_telegram_alert(f"async_push 失敗：{e}")
    threading.Thread(target=_run, daemon=True).start()


def push_to_group(text: str):
    push_text(LINE_GROUP_ID, text)


def _fetch_weather_for_greeting():
    try:
        d = get_owm_weather("台北")
        if d:
            desc = d["weather"][0].get("description", "")
            temp = round(d["main"]["temp"])
            humidity = d["main"]["humidity"]
            rain = d.get("rain", {}).get("1h", 0) or d.get("rain", {}).get("3h", 0)
            info = f"台北天氣：{desc} {temp}°C 濕度{humidity}%"
            if rain:
                info += f" 有雨{rain}mm，記得帶傘"
            return info
    except Exception as e:
        logger.warning("API error: %s", e)
    return ""


def _fetch_birthdays_for_greeting(today_mmdd: str):
    nicks = get_today_birthdays()
    if not nicks:
        nicks = [nick for nick, bd in _MEMBER_BIRTHDAYS.items() if bd == today_mmdd]
    return nicks


def send_morning_greeting():
    today = datetime.now(TW_TZ)
    weekdays = ["週一", "週二", "週三", "週四", "週五", "週六", "週日"]
    weekday = weekdays[today.weekday()]
    date_str = today.strftime(f"%-m月%-d日 {weekday}")
    today_mmdd = today.strftime("%m-%d")

    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=2) as ex:
        f_weather = ex.submit(_fetch_weather_for_greeting)
        f_bdays = ex.submit(_fetch_birthdays_for_greeting, today_mmdd)
        weather_info = f_weather.result()
        birthday_nicks = f_bdays.result()

    birthday_str = f"今天是 {'、'.join(birthday_nicks)} 的生日！" if birthday_nicks else ""

    holiday_hint = call_gemini(
        f"今天是{date_str}，台灣有沒有節日或特別紀念日？"
        "如果有，只回應節日名稱（例如「端午節」「父親節」）；沒有就回「無」。"
    ) or "無"
    holiday_str = "" if "無" in holiday_hint else f"今天是{holiday_hint.strip()}！"

    msg = call_gemini(
        f"今天是{date_str}。{weather_info}\n"
        f"{holiday_str}{birthday_str}\n"
        "幫我寫一則給朋友群的早安問候，"
        "輕鬆活潑、120字以內、繁體中文，"
        "加入天氣提醒，如有節日或生日也自然帶入。"
        "結尾加一個 emoji。不要加大家好。"
    ) or f"☀️ 早安！今天是{date_str}，{weather_info or '新的一天開始了'}！{birthday_str}"

    push_to_group(msg)
    logger.info("Morning greeting sent at %s", today.strftime("%H:%M"))


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

_OWM_CITY_MAP = {
    "台北": "Taipei,TW", "新北": "New Taipei,TW", "桃園": "Taoyuan,TW",
    "台中": "Taichung,TW", "台南": "Tainan,TW", "高雄": "Kaohsiung,TW",
    "基隆": "Keelung,TW", "花蓮": "Hualien,TW", "台東": "Taitung,TW",
    "宜蘭": "Yilan,TW", "嘉義": "Chiayi,TW", "新竹": "Hsinchu,TW",
}

_OWM_ICON = {
    "clear sky": "☀️", "few clouds": "🌤", "scattered clouds": "⛅",
    "broken clouds": "🌥", "overcast clouds": "☁️",
    "light rain": "🌦", "moderate rain": "🌧", "heavy rain": "🌧",
    "thunderstorm": "⛈", "snow": "❄️", "mist": "🌫", "fog": "🌫",
    "haze": "🌫", "drizzle": "🌦",
}


def get_owm_weather(city_zh: str = "台北") -> dict | None:
    q = _OWM_CITY_MAP.get(city_zh, f"{city_zh},TW")
    try:
        r = requests.get(
            "https://api.openweathermap.org/data/2.5/weather",
            params={"q": q, "appid": OWM_API_KEY, "units": "metric", "lang": "zh_tw"},
            timeout=8,
        )
        if r.status_code == 200:
            return r.json()
    except Exception as _exc:
        logger.warning("API error: %s", _exc)
        pass
    return None


def format_owm_weather(city_zh: str = "台北") -> str:
    d = get_owm_weather(city_zh)
    if not d:
        return get_weather(city_zh)
    try:
        desc = d["weather"][0].get("description", "")
        desc_en = d["weather"][0].get("main", "").lower()
        icon = next((v for k, v in _OWM_ICON.items() if k in desc_en or k in desc.lower()), "🌡")
        temp = round(d["main"]["temp"])
        feels = round(d["main"]["feels_like"])
        humidity = d["main"]["humidity"]
        wind = round(d["wind"]["speed"] * 3.6)  # m/s → km/h
        rain = d.get("rain", {}).get("1h", 0) or d.get("rain", {}).get("3h", 0)
        rain_str = f"　降雨 {rain}mm" if rain else ""
        return (
            f"{icon} {city_zh} 天氣\n"
            f"{desc}　{temp}°C（體感 {feels}°C）\n"
            f"濕度 {humidity}%　風速 {wind} km/h{rain_str}"
        )
    except Exception as _exc:
        return get_weather(city_zh)


def get_weather_v2(text: str) -> str:
    # 檢查是否有日期關鍵詞
    date_result = _parse_date_offset(text)
    if date_result:
        offset, desc = date_result
        if offset >= 7:
            return f"❌ {desc} 超出7天預報範圍，目前只能查未來7天喔"
        if any(k in text for k in ["下雨", "會不會", "帶傘", "雨"]):
            return _format_om_rain_check(offset, desc)
        return f"🌡 {desc}天氣\n\n{_format_om_weather(offset, desc)}"
    # 無日期，維持原有邏輯
    city = "台北"
    for c in TW_CITIES:
        if c in text:
            city = c
            break
    return format_owm_weather(city)


# ─── Music ───────────────────────────────────────────────

def fetch_spotify_track(query: str) -> str:
    d = _rapid("get", "spotify23.p.rapidapi.com", "/search",
               params={"q": query, "type": "tracks", "numberOfTopResults": "3"})
    if d is _QUOTA:
        return _QUOTA_MSG
    if not d:
        return call_gemini(f"列出3首和「{query}」相關的歌，格式：🎵 歌名 — 歌手，每行一首") or f"🎵 找不到「{query}」😢"
    try:
        items = d.get("tracks", {}).get("items", [])
        if not items:
            return call_gemini(f"列出3首和「{query}」相關的歌，格式：🎵 歌名 — 歌手，每行一首") or f"🎵 找不到「{query}」相關的歌"
        lines = ["🎵 找到以下歌曲：\n"]
        for t in items[:3]:
            data = t.get("data", {})
            name = data.get("name", "")
            artists = data.get("artists", {}).get("items", [])
            artist = artists[0].get("profile", {}).get("name", "") if artists else ""
            lines.append(f"• {name} — {artist}")
        return "\n".join(lines)
    except Exception as e:
        logger.warning("[spotify] %s", "e")
        return call_gemini(f"列出3首和「{query}」相關的歌，格式：🎵 歌名 — 歌手，每行一首") or f"🎵 找不到「{query}」😢"


# ─── Movies ──────────────────────────────────────────────

def fetch_imdb(title: str) -> str:
    d = _rapid("get", "imdb8.p.rapidapi.com", "/title/find", params={"q": title})
    if d is _QUOTA:
        return _QUOTA_MSG
    if not d:
        return call_gemini(f"用繁體中文簡介電影「{title}」，包含上映年份、導演、主演、一句評價，格式精簡") or f"🎬 查不到「{title}」😢"
    try:
        results = d.get("results", [])
        if not results:
            return call_gemini(f"用繁體中文簡介電影「{title}」，包含上映年份、導演、主演、一句評價，格式精簡") or f"🎬 找不到「{title}」"
        m = results[0]
        year = m.get("year", "")
        rating = m.get("starRating", {}).get("ratingValue", "")
        title_str = m.get("title", "")
        return (
            f"🎬 {title_str}（{year}）\n"
            f"⭐ {rating}/10" if rating else f"🎬 {title_str}（{year}）"
        )
    except Exception as e:
        logger.warning("[imdb] %s", "e")
        return call_gemini(f"用繁體中文簡介電影「{title}」，包含上映年份、導演、主演、一句評價，格式精簡") or f"🎬 查不到「{title}」😢"


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
    except Exception as _exc:
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
    _gemini_streaming = lambda: call_gemini(
        f"「{title}」這部電影/劇現在在哪些平台可以看？列出平台名稱，繁體中文回答，格式：🍿《{title}》可在：• Netflix • Disney+ 等"
    ) or f"🍿 找不到「{title}」的串流資訊"
    if not d:
        return _gemini_streaming()
    try:
        items = d if isinstance(d, list) else [d]
        if not items:
            return _gemini_streaming()
        show = items[0]
        title_show = show.get("title", title)
        services = show.get("streamingInfo", {})
        if not services:
            return _gemini_streaming()
        lines = [f"🍿《{title_show}》可在："]
        for svc in list(services.keys())[:5]:
            lines.append(f"  • {svc.capitalize()}")
        return "\n".join(lines)
    except Exception as e:
        logger.warning("[streaming] %s", "e")
        return _gemini_streaming()


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

def _format_cocktail(drink_name: str, category: str, ingredients: list, instructions: str) -> str:
    ings_str = "、".join(ingredients[:8])
    zh_steps = call_gemini(
        f"把以下調酒做法翻成繁體中文，條列步驟，簡潔，不超過150字：\n{instructions}"
    ) if instructions else ""
    lines = [f"🍹 {drink_name}"]
    if category:
        lines[0] += f"（{category}）"
    lines.append(f"材料：{ings_str}")
    if zh_steps:
        lines.append(f"\n做法：\n{zh_steps}")
    elif instructions:
        lines.append(f"做法：{instructions[:300]}")
    return "\n".join(lines)


def fetch_cocktail(name: str = None) -> str:
    params = {"name": name} if name else {}
    d = _ninja("/v1/cocktail", params=params)
    if d is _QUOTA:
        return _QUOTA_MSG
    if d and isinstance(d, list):
        c = d[0]
        return _format_cocktail(
            c.get("name", ""), "",
            c.get("ingredients", []),
            c.get("instructions", ""),
        )
    # Fallback to TheCocktailDB
    try:
        url = "https://www.thecocktaildb.com/api/json/v1/1/random.php"
        if name:
            url = f"https://www.thecocktaildb.com/api/json/v1/1/search.php?s={name}"
        r = requests.get(url, timeout=8)
        drink = r.json()["drinks"][0]
        ings = [drink[f"strIngredient{i}"] for i in range(1, 16) if drink.get(f"strIngredient{i}")]
        return _format_cocktail(
            drink["strDrink"], drink.get("strCategory", ""),
            ings, drink.get("strInstructions", ""),
        )
    except Exception as _exc:
        return "🍹 調酒師不在，待會再試"


# ─── Anime Quotes ────────────────────────────────────────

def _parse_anime_quote(item: dict) -> str | None:
    quote = item.get("quote") or item.get("content", "")
    char = item.get("char") or item.get("character", "")
    anime = item.get("anime") or item.get("title", "")
    if not quote:
        return None
    line = f"🌸 「{quote}」"
    if char:
        line += f"\n— {char}"
    if anime:
        line += f"《{anime}》"
    return line


def fetch_anime_quote() -> str:
    # Primary: animechan.io (free, no key)
    try:
        r = requests.get("https://animechan.io/api/v1/quotes/random", timeout=8)
        if r.status_code == 200:
            data = r.json()
            item = data.get("data", data)
            result = _parse_anime_quote(item)
            if result:
                return result
    except Exception as _exc:
        logger.warning("API error: %s", _exc)
        pass

    # Fallback: Gemini
    return call_gemini(
        "給我一句動漫裡的經典語錄，格式：\n🌸 「語錄」\n— 角色名《作品名》"
    ) or "🌸 動漫語錄暫時失靈，待會再試"


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
    except Exception as _exc:
        logger.warning("API error: %s", _exc)
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
        logger.warning("[superhero] %s", "e")
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
        except Exception as _exc:
            logger.warning("API error: %s", _exc)
            pass
    return "😂 梗圖機壞了，待會再試", None


# ─── Chuck Norris ─────────────────────────────────────────

def fetch_chuck_norris() -> str:
    try:
        r = requests.get("https://api.chucknorris.io/jokes/random", timeout=8)
        joke = r.json().get("value", "")
        if joke:
            return f"💪 查克諾里斯冷知識\n{joke}"
    except Exception as _exc:
        logger.warning("API error: %s", _exc)
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
    cached = _daily_cached("gold_price")
    if cached:
        return cached
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
        except Exception as _exc:
            logger.warning("API error: %s", _exc)
            pass
        if silver:
            lines.append(f"白銀：${silver} USD/盎司")
        result = "\n".join(lines)
        _daily_cache_set("gold_price", result)
        return result
    except Exception as e:
        logger.warning("[gold] %s", "e")
        return "🪙 金價查詢失敗"


def fetch_number_fact() -> str:
    try:
        r = requests.get("http://numbersapi.com/random/trivia", params={"json": "true"}, timeout=8)
        text = r.json().get("text", "")
        if text:
            zh = smart_translate(text)
            return f"🔢 {zh}"
    except Exception as _exc:
        logger.warning("API error: %s", _exc)
        pass
    return call_gemini("給我一個關於數字的有趣冷知識，用繁體中文") or "數字冷知識暫時失靈"


def fetch_astronomy_fact() -> str:
    d = _ninja("/v1/facts", params={"category": "science"})
    if d is _QUOTA:
        return _QUOTA_MSG
    if d and isinstance(d, list):
        fact = d[0].get("fact", "")
        if fact:
            zh = smart_translate(fact)
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
    except Exception as _exc:
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
    except Exception as _exc:
        return "📚 書籍查詢暫時失敗"


def fetch_waifu() -> tuple:
    try:
        r = requests.get("https://api.waifu.im/search", params={"is_nsfw": "false"}, timeout=10)
        items = r.json().get("images", [])
        if items:
            return "🌸 動漫圖來了！", items[0].get("url")
    except Exception as _exc:
        logger.warning("API error: %s", _exc)
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
    except Exception as _exc:
        logger.warning("API error: %s", _exc)
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
    except Exception as _exc:
        logger.warning("API error: %s", _exc)
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
    except Exception as _exc:
        logger.warning("API error: %s", _exc)
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
    except Exception as _exc:
        logger.warning("API error: %s", _exc)
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
    except Exception as _exc:
        return f"找不到寶可夢「{name}」，確認英文名或 ID 是否正確", None


# ─── Social / Utility ─────────────────────────────────────

def fetch_qr_code(url: str) -> tuple:
    encoded = requests.utils.quote(url, safe="")
    img_url = f"https://api.qrserver.com/v1/create-qr-code/?size=300x300&data={encoded}"
    return f"📱 QR Code 產生好了！", img_url


def shorten_url(url: str) -> str:
    try:
        r = requests.get(
            "https://tinyurl.com/api-create.php",
            params={"url": url},
            timeout=8,
        )
        short = r.text.strip()
        if short.startswith("http"):
            return f"🔗 縮短後：{short}"
    except Exception as _exc:
        logger.warning("API error: %s", _exc)
        pass
    return "短網址產生失敗 😢"


def fetch_youtube(query: str) -> str:
    d = _rapid("get", "youtube-v31.p.rapidapi.com", "/search",
               params={"q": query, "part": "snippet", "type": "video",
                       "maxResults": "3", "regionCode": "TW"})
    if d is _QUOTA:
        return _QUOTA_MSG
    if not d:
        return f"🎬 找不到「{query}」相關影片"
    try:
        items = d.get("items", [])
        if not items:
            return f"🎬 找不到「{query}」相關影片"
        lines = [f"🎬 找到以下影片：\n"]
        for item in items[:3]:
            snippet = item.get("snippet", {})
            vid_id = item.get("id", {}).get("videoId", "")
            title = snippet.get("title", "")
            channel = snippet.get("channelTitle", "")
            if vid_id:
                lines.append(f"• {title}\n  {channel}\n  https://youtu.be/{vid_id}")
        return "\n".join(lines)
    except Exception as e:
        logger.warning("[youtube] %s", "e")
        return f"🎬 找不到「{query}」相關影片"


def rewrite_text(text: str) -> str:
    result = call_gemini(
        f"把以下文字改寫成三種風格，每種一行，加上標籤，用繁體中文：\n"
        f"原文：{text}\n\n"
        f"1. 🔥 嗆辣版：（改寫）\n"
        f"2. 🥺 撒嬌版：（改寫）\n"
        f"3. 🎩 正經版：（改寫）"
    )
    return result or "改寫失敗 😢"


_ZODIAC_EN = {
    "牡羊": "aries", "金牛": "taurus", "雙子": "gemini",
    "巨蟹": "cancer", "獅子": "leo", "處女": "virgo",
    "天秤": "libra", "天蠍": "scorpio", "射手": "sagittarius",
    "摩羯": "capricorn", "水瓶": "aquarius", "雙魚": "pisces",
}


def match_zodiac(sign1: str, sign2: str) -> str:
    s1 = sign1.replace("座", "")
    s2 = sign2.replace("座", "")
    e1 = _ZODIAC_EN.get(s1)
    e2 = _ZODIAC_EN.get(s2)
    if e1 and e2:
        d = _rapid("get", "starmatch-ai.p.rapidapi.com", "/api/compatibility",
                   params={"sign1": e1, "sign2": e2})
        if d and d is not _QUOTA:
            try:
                score = d.get("compatibilityScore") or d.get("score", "")
                desc = d.get("description") or d.get("summary", "")
                if desc:
                    desc_zh = smart_translate(desc)
                    return f"💕 {s1}座 × {s2}座\n\n配對指數：{score}\n\n{desc_zh}"
            except Exception as _exc:
                logger.warning("API error: %s", _exc)
                pass
    return call_gemini(
        f"分析{s1}座和{s2}座的配對，包含：配對指數（%）、優點、挑戰，"
        f"輕鬆有趣，用繁體中文，150字以內"
    ) or f"查不到{s1}座×{s2}座的配對資料"


def fetch_news() -> str:
    try:
        r = requests.get(
            "https://saurav.tech/NewsAPI/top-headlines/category/general/tw.json",
            timeout=10,
        )
        articles = r.json().get("articles", [])[:5]
        if articles:
            lines = ["📰 今日新聞頭條：\n"]
            for a in articles:
                lines.append(f"• {a.get('title', '')}")
            return "\n".join(lines)
    except Exception as _exc:
        logger.warning("API error: %s", _exc)
        pass
    d = _rapid("get", "bing-news-search1.p.rapidapi.com", "/news/search",
               params={"q": "台灣 新聞", "count": "5", "mkt": "zh-TW"})
    if d is _QUOTA:
        return _QUOTA_MSG
    if d:
        try:
            items = d.get("value", [])[:5]
            if items:
                lines = ["📰 今日新聞頭條：\n"]
                for item in items:
                    lines.append(f"• {item.get('name', '')}")
                return "\n".join(lines)
        except Exception as _exc:
            logger.warning("API error: %s", _exc)
            pass
    return call_gemini("給我5則今天台灣的重要新聞頭條，每則一行，用繁體中文") or "新聞暫時無法取得"


# ─── Shazam ───────────────────────────────────────────────

def shazam_identify(audio_bytes: bytes) -> str:
    audio_b64 = base64.b64encode(audio_bytes).decode("utf-8")
    for host in ["shazam.p.rapidapi.com", "shazam-core.p.rapidapi.com"]:
        path = "/songs/v2/detect" if "shazam.p.rapidapi.com" == host else "/v1/songs/detect"
        d = _rapid("post", host, path,
                   headers={"Content-Type": "text/plain"},
                   data=audio_b64)
        if d is _QUOTA:
            continue
        if not d:
            continue
        try:
            track = (d.get("track") or
                     d.get("matches", [{}])[0] if isinstance(d.get("matches"), list) else {})
            if isinstance(d.get("track"), dict):
                track = d["track"]
            title = track.get("title", "") or track.get("name", "")
            artist = track.get("subtitle", "") or track.get("artist", "")
            if title:
                return f"🎵 找到了！\n\n《{title}》\n{artist}"
        except Exception as e:
            logger.warning("[shazam] parse error: %s", e)
    return "🎵 聽不出來這首歌，音訊可能太短或品質不夠"


# ─── Background Removal ───────────────────────────────────

_REMOVE_BG_PENDING: set = set()

def remove_background(img_bytes: bytes) -> str | None:
    import io
    d = _rapid("post", "background-remover3.p.rapidapi.com", "/v1.0/removebg/1.0.0",
               files={"image": ("image.jpg", io.BytesIO(img_bytes), "image/jpeg")})
    if d is _QUOTA or not d:
        return None
    try:
        url = d.get("url") or d.get("result_url") or d.get("output_url")
        return url
    except Exception as _exc:
        logger.warning("API error: %s", _exc)
        return None


# ─── NSFW Detection ───────────────────────────────────────

def check_nsfw(img_bytes: bytes) -> bool:
    import io
    d = _rapid("post", "nsfw-image-classification1.p.rapidapi.com", "/v1/results",
               files={"image": ("img.jpg", io.BytesIO(img_bytes), "image/jpeg")})
    if d is _QUOTA or not d:
        return False
    try:
        results = d if isinstance(d, list) else d.get("results", [])
        for item in results:
            if item.get("className") in ("Sexy", "Porn", "Hentai"):
                if float(item.get("probability", 0)) > 0.7:
                    return True
    except Exception as _exc:
        logger.warning("API error: %s", _exc)
        pass
    return False


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
    except Exception as _exc:
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
    except Exception as _exc:
        return "🎬 電影院關門了，待會再試 😅"


_ZODIAC = {
    "牡羊": "aries", "白羊": "aries", "金牛": "taurus", "雙子": "gemini",
    "巨蟹": "cancer", "獅子": "leo", "處女": "virgo", "天秤": "libra",
    "天蠍": "scorpio", "射手": "sagittarius", "摩羯": "capricorn",
    "水瓶": "aquarius", "雙魚": "pisces",
}
_ZODIAC_ZH = {v: k for k, v in _ZODIAC.items()}


_RASHI_MAP = {
    "aries": "mesha", "taurus": "vrsabha", "gemini": "mithuna",
    "cancer": "karka", "leo": "simha", "virgo": "kanya",
    "libra": "tula", "scorpio": "vrschika", "sagittarius": "dhanu",
    "capricorn": "makara", "aquarius": "kumbha", "pisces": "mina",
}

def _fetch_horoscope_aztro(sign_en: str) -> dict | None:
    try:
        r = requests.post(
            f"https://aztro.p.rapidapi.com/?sign={sign_en}&day=today",
            headers={"X-RapidAPI-Key": _RAPIDAPI_KEYS[0] if _RAPIDAPI_KEYS else "",
                     "X-RapidAPI-Host": "aztro.p.rapidapi.com"},
            timeout=8,
        )
        if r.status_code == 429:
            return None
        return r.json()
    except Exception as _exc:
        logger.warning("API error: %s", _exc)
        return None

def _fetch_horoscope_advanced(sign_en: str) -> dict | None:
    try:
        key = _RAPIDAPI_KEYS[0] if _RAPIDAPI_KEYS else ""
        if not key:
            return None
        r = requests.get(
            "https://daily-horoscope-advanced-api.p.rapidapi.com/api/Daily-Horoscope-New/",
            headers={"x-rapidapi-host": "daily-horoscope-advanced-api.p.rapidapi.com", "x-rapidapi-key": key},
            params={"zodiacSign": sign_en.capitalize(), "timePeriod": "today"},
            timeout=8,
        )
        if r.status_code == 200:
            d = r.json()
            return {"prediction": d.get("prediction", ""), "source": "Advanced"}
    except Exception as _exc:
        logger.warning("API error: %s", _exc)
        pass
    return None

def _fetch_horoscope_basic(sign_en: str) -> dict | None:
    try:
        key = _RAPIDAPI_KEYS[0] if _RAPIDAPI_KEYS else ""
        if not key:
            return None
        r = requests.get(
            "https://daily-horoscope-api.p.rapidapi.com/api/Daily-Horoscope-English/",
            headers={"x-rapidapi-host": "daily-horoscope-api.p.rapidapi.com", "x-rapidapi-key": key},
            params={"zodiacSign": sign_en.capitalize(), "timePeriod": "today"},
            timeout=8,
        )
        if r.status_code == 200:
            d = r.json()
            return {
                "prediction": d.get("prediction", ""),
                "color": d.get("color", "").split(",")[0].strip() if d.get("color") else "",
                "number": d.get("number", "").split(",")[0].strip() if d.get("number") else "",
                "source": "Basic",
            }
    except Exception as _exc:
        logger.warning("API error: %s", _exc)
        pass
    return None

def _fetch_horoscope_rashifal(sign_en: str) -> dict | None:
    try:
        key = _RAPIDAPI_KEYS[0] if _RAPIDAPI_KEYS else ""
        if not key:
            return None
        rashi = _RASHI_MAP.get(sign_en)
        if not rashi:
            return None
        r = requests.get(
            "https://zodiac-horoscope-api-rashifal.p.rapidapi.com/astro/rashi/daily",
            headers={"x-rapidapi-host": "zodiac-horoscope-api-rashifal.p.rapidapi.com", "x-rapidapi-key": key},
            params={"rashi": rashi, "day": "today", "lang": "en"},
            timeout=8,
        )
        if r.status_code == 200:
            d = r.json()
            return {"prediction": d.get("desc", ""), "source": "Rashifal"}
    except Exception as _exc:
        logger.warning("API error: %s", _exc)
        pass
    return None

def _fetch_horoscope_v2(sign_en: str) -> dict | None:
    d = _rapid("get", "horoscope-astrology.p.rapidapi.com", "/today",
               params={"sunsign": sign_en})
    if d is _QUOTA or not d:
        return None
    return d


def fetch_horoscope_for_sign(sign_zh: str) -> str:
    """Generate horoscope for a single zodiac sign."""
    sign_en = _ZODIAC.get(sign_zh)
    if not sign_en:
        return f"不認識「{sign_zh}」座"
    data = (_fetch_horoscope_advanced(sign_en)
            or _fetch_horoscope_basic(sign_en)
            or _fetch_horoscope_rashifal(sign_en)
            or _fetch_horoscope_aztro(sign_en)
            or _fetch_horoscope_v2(sign_en))
    if data:
        desc_en = data.get("description") or data.get("horoscope") or data.get("prediction", "")
        mood = data.get("mood", "")
        color = data.get("color", "")
        lucky = data.get("lucky_number", "") or data.get("luckyNumber", "") or data.get("number", "")
        compat_raw = data.get("compatibility", "") or data.get("luckySign", "")
        compat = _ZODIAC_ZH.get(str(compat_raw).lower(), compat_raw)
        if desc_en:
            desc = smart_translate(desc_en)
            lines = [f"🔮 {sign_zh}座：{desc}"]
            extra = []
            if mood:
                extra.append(f"心情：{mood}")
            if color:
                extra.append(f"幸運色：{color}")
            if lucky:
                extra.append(f"幸運數字：{lucky}")
            if compat:
                extra.append(f"速配：{compat}座")
            if extra:
                lines.append("　".join(extra))
            return "\n".join(lines)
    return call_gemini(
        f"用輕鬆有趣的風格給{sign_zh}座今日運勢，包含：整體運勢、幸運色、幸運數字，"
        f"繁體中文，80字以內，格式：🔮 {sign_zh}座：[運勢內容]"
    ) or f"🔮 {sign_zh}座運勢查詢失敗"


def fetch_horoscope(text) -> str:
    sign_zh = next((k for k in _ZODIAC if k in text), None)
    if not sign_zh:
        cache_key = "horoscope_all"
        cached = _daily_cached(cache_key)
        if cached:
            return cached
        # 優先用 sheet，沒有就用 hardcoded 預設
        members = get_all_zodiacs() or [(None, nick, zodiac + "座") for nick, zodiac in _MEMBER_ZODIACS.items()]
        if members:
            today = datetime.now(TW_TZ).strftime("%-m/%-d")
            results = [None] * len(members)
            def _fetch_one(i, zodiac):
                results[i] = fetch_horoscope_for_sign(zodiac)
            threads = [threading.Thread(target=_fetch_one, args=(i, z), daemon=True)
                       for i, (_, _, z) in enumerate(members)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=15)
            fetch_time = datetime.now(TW_TZ).strftime("%H:%M")
            lines = [f"🔮 今日運勢（{today} 資料時間 {fetch_time}）\n"]
            for i, (_, nick, _) in enumerate(members):
                lines.append(f"【{nick}】{results[i] or '查詢失敗'}")
            output = "\n\n".join(lines)
            _daily_cache_set(cache_key, output)
            return output
        signs = " / ".join(_ZODIAC.keys())
        return (f"🔮 還沒有人綁定星座！\n"
                f"輸入「我是天蠍」綁定你的星座\n\n"
                f"或說「今日天蠍」直接查詢\n支援：{signs}")
    sign_en = _ZODIAC[sign_zh]

    # 嘗試多個 API 輪班
    data = (_fetch_horoscope_advanced(sign_en)
            or _fetch_horoscope_basic(sign_en)
            or _fetch_horoscope_rashifal(sign_en)
            or _fetch_horoscope_aztro(sign_en)
            or _fetch_horoscope_v2(sign_en))

    if data:
        desc_en = data.get("description") or data.get("horoscope") or data.get("prediction", "")
        mood = data.get("mood", "")
        color = data.get("color", "")
        lucky = data.get("lucky_number", "") or data.get("luckyNumber", "")
        compat_raw = data.get("compatibility", "") or data.get("luckySign", "")
        compat = _ZODIAC_ZH.get(str(compat_raw).lower(), compat_raw)
        if desc_en:
            desc = smart_translate(desc_en)
            lines = [f"🔮 今日{sign_zh}座運勢\n\n{desc}"]
            extra = []
            if mood:
                extra.append(f"心情：{mood}")
            if color:
                extra.append(f"幸運色：{color}")
            if lucky:
                extra.append(f"幸運數字：{lucky}")
            if compat:
                extra.append(f"速配：{compat}座")
            if extra:
                lines.append("　".join(extra))
            return "\n\n".join(lines)

    # Gemini fallback
    return call_gemini(
        f"用輕鬆有趣的風格給{sign_zh}座今日運勢，包含：整體運勢、心情、幸運色、幸運數字，"
        f"繁體中文，150字以內"
    ) or "🔮 占星師在睡覺，待會再問 😴"


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
    recent_chat = _get_recent_context(8)

    context = ""
    if recent_chat:
        context += "【最近聊天記錄】\n"
        context += recent_chat
        context += "\n\n"
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
        f"請結合上面的對話脈絡和你對這個群組的了解，用台灣年輕人語氣回應，輕鬆幽默，不超過 3 句。"
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


