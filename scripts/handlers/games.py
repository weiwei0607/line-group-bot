"""
Mini-game handlers: pairing, dice, rock-paper-scissors.
Extracted from api_helpers.py.
"""

import re
import random
from config import MEMBERS


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
        result = random.choice(["你贏了！", "輸了！不服氣！ 😤", "嗚嗚認輸"])
    else:
        result = random.choice(["哈我贏了 😈", "幸運是我的 🎊", "再猜！！"])
    return f"{user_emoji} VS {bot_emoji}\n{result}"
