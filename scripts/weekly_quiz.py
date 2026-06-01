import os
import requests

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

def get_quiz():
    if not GEMINI_API_KEY:
        return "（API key 未設定）"
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
        payload = {
            "contents": [{
                "parts": [{
                    "text": (
                        "請為以下三種語言各出 3 道測驗題，格式清楚，答案放在最後。\n\n"
                        "語言和難度：\n"
                        "- 日文：JLPT N5 入門（基本單字/問候語/數字，非常簡單）\n"
                        "- 西班牙文：A1 零基礎（顏色/數字/基本問候，選擇題）\n"
                        "- 英文：進階詞彙（IELTS/學術，選擇題或造句）\n\n"
                        "格式範例：\n"
                        "🇯🇵 日文小測驗\n"
                        "Q1. ___\n"
                        "Q2. ___\n"
                        "Q3. ___\n\n"
                        "🇪🇸 西班牙文小測驗\n"
                        "Q1. ___\n"
                        "...\n\n"
                        "🇬🇧 英文小測驗\n"
                        "Q1. ___\n"
                        "...\n\n"
                        "✅ 答案\n"
                        "日文：1. ___ 2. ___ 3. ___\n"
                        "西班牙文：1. ___ 2. ___ 3. ___\n"
                        "英文：1. ___ 2. ___ 3. ___"
                    )
                }]
            }]
        }
        resp = requests.post(url, json=payload, timeout=15)
        data = resp.json()
        return data["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception as e:
        return f"（測驗生成失敗：{e}）"

if __name__ == "__main__":
    from datetime import datetime, timezone, timedelta
    from goal_tracker import get_setting, set_setting, TW_TZ
    today = datetime.now(TW_TZ).strftime("%Y-%m-%d")
    if get_setting("weekly_quiz_last_run") == today:
        print(f"Weekly quiz already ran on {today}, skipping.")
        exit(0)

    quiz_content = get_quiz()

    message = (
        "📝 本週語言小測驗\n\n"
        "試著作答，再看答案！答案在最下面。\n\n"
        "━━━━━━━━━━━━━━━\n\n"
        + quiz_content
    )

    if len(message) > 4096:
        message = message[:4090] + "..."

    requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        data={"chat_id": CHAT_ID, "text": message},
        timeout=10,
    )
    set_setting("weekly_quiz_last_run", today)
