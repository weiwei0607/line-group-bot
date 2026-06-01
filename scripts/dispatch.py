"""
LINE Group Bot — Simple command dispatch table.
Extracts the most repetitive if/elif branches from handle_message.
"""

import re
import random
import logging
from api_helpers import (
    fetch_cat_image, fetch_dog_image, fetch_fox_image,
    fetch_shiba_image, fetch_animal_image, fetch_nasa_apod,
    fetch_random_pokemon, fetch_advice, handle_joke,
    handle_fun_fact, fetch_cocktail, fetch_random_meal, fetch_random_movie,
    fetch_exercise, fetch_anime_quote, fetch_trump_quote,
    fetch_meme, fetch_chuck_norris, fetch_quotable,
    fetch_trivia, fetch_number_fact, fetch_astronomy_fact,
    fetch_gold_price, fetch_news, fetch_random_activity,
    fetch_daily_japanese, fetch_daily_kanji, fetch_daily_spanish,
    fetch_movie_quote, get_weather_v2, handle_who_pays,
    handle_draw_lots, handle_pairing, handle_dice, handle_rps,
    handle_countdown, handle_fortune, fetch_waifu,
)

logger = logging.getLogger(__name__)

# ─── Text-only simple handlers ────────────────────────────

def _cmd_fortune(text: str) -> str:
    return handle_fortune()


def _cmd_who_pays(text: str) -> str:
    return handle_who_pays(text)


def _cmd_draw_lots(text: str) -> str:
    return handle_draw_lots(text)


def _cmd_advice(_text: str) -> str:
    return fetch_advice()


def _cmd_joke(_text: str) -> str:
    return handle_joke(_text)


def _cmd_fun_fact(_text: str) -> str:
    return handle_fun_fact(_text)


def _cmd_cocktail(_text: str) -> str:
    return fetch_cocktail()


def _cmd_meal(_text: str) -> str:
    return fetch_random_meal()


def _cmd_exercise(text: str) -> str:
    return fetch_exercise(text)


def _cmd_anime_quote(_text: str) -> str:
    return fetch_anime_quote()


def _cmd_trump_quote(_text: str) -> str:
    return fetch_trump_quote()


def _cmd_meme(_text: str) -> str:
    r = fetch_meme()
    return r[0] if isinstance(r, tuple) else r


def _cmd_chuck_norris(_text: str) -> str:
    return fetch_chuck_norris()


def _cmd_quotable(_text: str) -> str:
    q = fetch_quotable()
    return q if q else "✨ 今天也要加油！"


def _cmd_trivia(_text: str) -> str:
    t = fetch_trivia()
    return t if t else "🎯 題庫暫時空了，晚點再來！"


def _cmd_number_fact(_text: str) -> str:
    return fetch_number_fact()


def _cmd_astronomy(_text: str) -> str:
    return fetch_astronomy_fact()


def _cmd_gold(_text: str) -> str:
    return fetch_gold_price()


def _cmd_news(_text: str) -> str:
    return fetch_news()


def _cmd_activity(_text: str) -> str:
    return fetch_random_activity()


def _cmd_daily_japanese(_text: str) -> str:
    return fetch_daily_japanese()


def _cmd_daily_kanji(_text: str) -> str:
    return fetch_daily_kanji()


def _cmd_daily_spanish(_text: str) -> str:
    return fetch_daily_spanish()


def _cmd_movie_quote(_text: str) -> str:
    return fetch_movie_quote()


def _cmd_waifu(_text: str) -> str:
    r = fetch_waifu()
    return r[0] if isinstance(r, tuple) else r


def _cmd_pokemon(_text: str) -> str:
    return fetch_random_pokemon()


def _cmd_random_movie(_text: str) -> str:
    return fetch_random_movie()


def _cmd_pairing(text: str) -> str:
    return handle_pairing(text)


def _cmd_dice(text: str) -> str:
    return handle_dice(text)


def _cmd_rps(text: str) -> str:
    return handle_rps(text)


def _cmd_countdown(text: str) -> str:
    return handle_countdown(text)


# ─── Image handlers ───────────────────────────────────────

def _cmd_cat(_text: str):
    url = fetch_cat_image()
    text = random.choice(["🐱 貓貓來了！", "喵～ 🐾", "🐱 今日貓貓！"])
    return text, url


def _cmd_dog(_text: str):
    url = fetch_dog_image()
    text = random.choice(["🐶 狗狗來了！", "汪！🐾", "🐶 今日狗狗！"])
    return text, url


def _cmd_fox(_text: str):
    url = fetch_fox_image()
    text = random.choice(["🦊 狐狸來了！", "🦊 今日狐狸！", "啾啾～ 🦊"])
    return text, url


def _cmd_shiba(_text: str):
    url = fetch_shiba_image()
    text = random.choice(["🐕 柴柴！！", "wow such shiba 🐕", "今日柴柴！🐕"])
    return text, url


def _cmd_panda(_text: str):
    url = fetch_animal_image("panda")
    text = random.choice(["🐼 熊貓！", "今日熊貓 🐼", "圓滾滾來了 🐼"])
    return text, url


def _cmd_koala(_text: str):
    url = fetch_animal_image("koala")
    text = random.choice(["🐨 無尾熊！", "今日無尾熊 🐨", "抱抱樹 🐨"])
    return text, url


def _cmd_raccoon(_text: str):
    url = fetch_animal_image("raccoon")
    text = random.choice(["🦝 浣熊！", "今日浣熊 🦝", "小偷熊來了 🦝"])
    return text, url


def _cmd_nasa(_text: str):
    return fetch_nasa_apod()


# ─── Dispatch table ───────────────────────────────────────

_SIMPLE_HANDLERS = [
    (re.compile(r'^(今日)?運勢|占卜$'), _cmd_fortune),
    (re.compile(r'誰請客|今天誰請|誰買單|今天誰買'), _cmd_who_pays),
    (re.compile(r'^抽籤|^幫我(選|決定)|^選一個'), _cmd_draw_lots),
    (re.compile(r'^給我建議|^今日忠告'), _cmd_advice),
    (re.compile(r'笑話|冷笑話'), _cmd_joke),
    (re.compile(r'冷知識'), _cmd_fun_fact),
    (re.compile(r'^今日調酒|^調酒|^雞湯'), _cmd_cocktail),
    (re.compile(r'^今日食譜|^隨機食譜|^吃什麼食譜|^今天做什麼'), _cmd_meal),
    (re.compile(r'運動|健身|^今日運動|^找運動'), _cmd_exercise),
    (re.compile(r'動漫語錄|二次元語錄'), _cmd_anime_quote),
    (re.compile(r'川普語錄|美國總統語錄|總統語錄'), _cmd_trump_quote),
    (re.compile(r'梗圖|隨機梗圖'), _cmd_meme),
    (re.compile(r'諾里斯|chuck|norris'), _cmd_chuck_norris),
    (re.compile(r'名人語錄|激勵名言|今日名言'), _cmd_quotable),
    (re.compile(r'來一題|今日題目|抽題目'), _cmd_trivia),
    (re.compile(r'數字冷知識|數字趣聞|數字趣談'), _cmd_number_fact),
    (re.compile(r'天文冷知識|科學冷知識|宇宙冷知識'), _cmd_astronomy),
    (re.compile(r'金價|今日金價|黃金價格'), _cmd_gold),
    (re.compile(r'新聞|今日新聞|最新新聞|頭條'), _cmd_news),
    (re.compile(r'無聊|我好無聊|打發時間'), _cmd_activity),
    (re.compile(r'^配對'), _cmd_pairing),
    (re.compile(r'搖骰子|擲骰子|搖\d*[顆個]骰'), _cmd_dice),
    (re.compile(r'^猜拳'), _cmd_rps),
    (re.compile(r'倒數|還有幾天|距離'), _cmd_countdown),
]

_IMAGE_HANDLERS = [
    (re.compile(r'來隻貓|貓貓|來貓'), _cmd_cat),
    (re.compile(r'來隻狗|狗狗|來狗'), _cmd_dog),
    (re.compile(r'狐狸|來隻狐'), _cmd_fox),
    (re.compile(r'柴柴|柴犬|來隻柴'), _cmd_shiba),
    (re.compile(r'熊貓|來隻熊貓'), _cmd_panda),
    (re.compile(r'無尾熊|來隻無尾熊'), _cmd_koala),
    (re.compile(r'浣熊|來隻浣熊'), _cmd_raccoon),
    (re.compile(r'今日宇宙|NASA|宇宙照片'), _cmd_nasa),
]

_DAILY_HANDLERS = [
    (re.compile(r'日語|日文單字|學日文|每日日語|今日日文'), _cmd_daily_japanese),
    (re.compile(r'漢字|學漢字|每日漢字|今日漢字'), _cmd_daily_kanji),
    (re.compile(r'西語|西班牙語|西文單字|學西文|每日西語|今日西文'), _cmd_daily_spanish),
    (re.compile(r'電影台詞|電影名言'), _cmd_movie_quote),
    (re.compile(r'waifu|動漫圖|來張動漫圖|來張圖'), _cmd_waifu),
    (re.compile(r'抽寶可夢|今日寶可夢|來隻寶可夢'), _cmd_pokemon),
    (re.compile(r'推薦電影|今日電影|隨機電影|看什麼電影'), _cmd_random_movie),
]


def try_dispatch(text: str):
    """
    Try to dispatch a simple command.
    Returns (reply_text, reply_image_url) or (None, None) if no match.
    """
    # Simple text handlers
    for pattern, handler in _SIMPLE_HANDLERS:
        if pattern.search(text):
            return handler(text), None

    # Daily handlers
    for pattern, handler in _DAILY_HANDLERS:
        if pattern.search(text):
            return handler(text), None

    # Image handlers
    for pattern, handler in _IMAGE_HANDLERS:
        if pattern.search(text):
            result = handler(text)
            if isinstance(result, tuple):
                return result
            return result, None

    return None, None
