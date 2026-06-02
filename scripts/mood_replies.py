"""
情緒回覆模組 — 檢測用戶情緒並用 AI 生成回覆
"""

from utils import call_ai


def detect_mood(text: str) -> str | None:
    """檢測訊息情緒，回傳情緒類型或 None"""
    t = text.lower()
    if any(k in t for k in ["想打死", "討厭你", "生氣", "氣死", "很火", "氣炸", "火大"]):
        return "angry"
    if any(k in t for k in ["好煩", "煩死", "壓力大", "好累", "煩躁", "煩", "壓力"]):
        return "frustrated"
    if any(k in t for k in ["難過", "傷心", "想哭", "沮喪", "心痛", "失落", "憂鬱"]):
        return "sad"
    if any(k in t for k in ["好無聊", "無聊", "不知道做什麼", "很閒", "沒事做"]):
        return "bored"
    if any(k in t for k in ["愛你", "好棒", "謝謝你", "最棒", "讚", "厲害", "太強"]):
        return "happy"
    return None


_MOOD_PROMPTS = {
    "angry": "對方好像有點生氣或在開玩笑說想懲罰你。請撒嬌道歉、輕鬆化解，不要正經八百。",
    "frustrated": "對方覺得煩躁或壓力大。請溫暖安慰，可以提議轉移注意力（笑話、電影、活動）。",
    "sad": "對方難過或傷心。請溫柔陪伴，表達關心，不要給建議。",
    "bored": "對方覺得無聊。請推薦有趣的事情或提議玩遊戲。",
    "happy": "對方開心或稱讚你。請開心回應，表達感謝。",
}

_FALLBACKS = {
    "angry": "嗚嗚對不起嘛 🥺 我做錯什麼了...",
    "frustrated": "抱抱 🤗 要不要聽個冷笑話轉換心情？",
    "sad": "我在這裡陪你 💙 要說說發生什麼事了嗎？",
    "bored": "那來玩個遊戲？或我推薦一部電影 🎬",
    "happy": "嘿嘿我也愛你 💕 有什麼需要儘管說！",
}


def handle_mood_mention(text: str, member: str | None, mood: str) -> str:
    """用 AI 生成情緒化回覆，失敗時用 fallback"""
    prompt = (
        f"你是一個活潑可愛的朋友群 LINE 機器人，名字叫「小棉襖」。\n"
        f"{'傳訊息的是 ' + member + '，' if member else ''}"
        f"他說：「{text}」\n"
        f"{_MOOD_PROMPTS.get(mood, '請輕鬆自然地回應。')}\n"
        f"用台灣年輕人語氣，繁體中文，不超過 3 句，不要加『大家好』或自我介紹。"
    )
    return call_ai(prompt) or _FALLBACKS.get(mood, "我聽到了！但現在腦袋有點卡，等等再跟你聊 😅")
