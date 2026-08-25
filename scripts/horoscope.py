"""Horoscope API helpers."""
import os
import random
import requests
import logging
from datetime import datetime
from weather import _rapid
from config import TMDB_API_KEY, RAPIDAPI_KEYS, _MEMBER_ZODIACS
from goal_tracker import TW_TZ, get_all_zodiacs
from utils import call_gemini


def _smart_translate(text, target="zh-TW"):
    from api_helpers import smart_translate
    return smart_translate(text, target)
from weather import _daily_cached, _daily_cache_set
from weather import _QUOTA, _QUOTA_MSG

logger = logging.getLogger(__name__)

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
                    desc_zh = _smart_translate(desc)
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
    # 新聞抓不到就明說，不要讓 Gemini 編。模型沒有即時資訊，
    # 生出來的「今日頭條」是假的，推到群裡會被當真。
    return "📰 目前抓不到新聞來源，等一下再試一次吧"


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
            headers={"X-RapidAPI-Key": RAPIDAPI_KEYS[0] if RAPIDAPI_KEYS else "",
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
        key = RAPIDAPI_KEYS[0] if RAPIDAPI_KEYS else ""
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
        key = RAPIDAPI_KEYS[0] if RAPIDAPI_KEYS else ""
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
        key = RAPIDAPI_KEYS[0] if RAPIDAPI_KEYS else ""
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
            desc = _smart_translate(desc_en)
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
            desc = _smart_translate(desc_en)
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
