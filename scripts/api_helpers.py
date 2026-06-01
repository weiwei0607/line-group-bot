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

# Re-export weather/horoscope functions so existing imports keep working
from weather import (
    fetch_cat_image, fetch_dog_image, fetch_fox_image,
    fetch_shiba_image, fetch_animal_image, fetch_nasa_apod,
    fetch_random_pokemon, fetch_advice, handle_joke,
    handle_fun_fact,
    get_weather_v2, handle_who_pays,
    handle_draw_lots, handle_countdown, handle_fortune,
    _parse_date_offset, send_morning_greeting,
)
from horoscope import (
    fetch_horoscope, fetch_news, fetch_random_meal,
    fetch_random_movie, shazam_identify, remove_background, check_nsfw,
)
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
        logger.warning("[spotify] %s", e)
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
        logger.warning("[imdb] %s", e)
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
        logger.warning("[streaming] %s", e)
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
        logger.warning("[superhero] %s", e)
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
        logger.warning("[gold] %s", e)
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
        logger.warning("[youtube] %s", e)
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
    completed = get_completed_goals(cycle_id)
    lines = [f"🎯 本週期目標（第 {day}/{total} 天）\n"]
    all_members = sorted(set(list(goals.keys()) + list(stats.keys())))
    for member in all_members:
        member_goals = goals.get(member, [])
        member_log = log.get(member, {})
        lines.append(f"👤 {member}")
        if member_goals:
            for g in member_goals:
                kw = _goal_keyword(g)
                if is_goal_completed(member, g, completed):
                    lines.append(f"  ✅ {kw}｜已完成")
                else:
                    cnt = _goal_days(member_log, g, total)
                    bar = "🟩" * cnt + "⬜" * max(0, total - cnt)
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
    goals = get_goals(cycle_id)
    if not checkins:
        return f"今天（第 {day} 天）還沒有人打卡！快去打卡 💪"
    lines = [f"📋 今日打卡（第 {day}/{total} 天）\n"]
    for member, content in checkins.items():
        lines.append(f"✅ {member}：{content}")
    # Check who hasn't checked in (include all members with goals)
    missing = [m for m in goals if m not in checkins]
    if missing:
        lines.append(f"\n還沒打卡：{' / '.join(missing)} 快去！")
    return "\n".join(lines)



# ─── Backward-compatible push alias ─────────────────────
def push_to_group(text: str):
    push_text(LINE_GROUP_ID, text)


