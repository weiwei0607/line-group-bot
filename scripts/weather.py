"""Weather API helpers."""
import os
import re
import requests
from collections import deque
from datetime import datetime, timedelta
from goal_tracker import TW_TZ

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
