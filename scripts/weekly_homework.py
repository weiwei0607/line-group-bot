import os
import requests

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

def get_homework():
    if not GEMINI_API_KEY:
        return "（API key 未設定）"
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
        payload = {
            "contents": [{
                "parts": [{
                    "text": (
                        "你是語言學習老師，請幫學生設計本週三種語言的作業，內容要具體可執行。\n\n"
                        "學生程度：\n"
                        "- 日文：N5 完全入門（可能認識五十音，幾乎沒有詞彙）\n"
                        "- 西班牙文：A1 零基礎（完全沒學過）\n"
                        "- 英文：進階（TOEIC 915，需要加強口說表達和商務寫作）\n\n"
                        "每個語言出以下三項作業，格式嚴格按照下方，不要多餘說明：\n\n"
                        "🇯🇵 本週日文作業\n"
                        "👂 聽力：[具體建議，例如：YouTube 搜尋「○○」看 10 分鐘，或推薦免費資源名稱]\n"
                        "✍️ 寫作：[具體寫作任務，例如：用日文寫出今天做的 3 件事，每句用「〜ました」結尾]\n"
                        "🎯 本週重點：[一個具體文法或表達重點]\n\n"
                        "🇪🇸 本週西班牙文作業\n"
                        "👂 聽力：[具體建議，推薦 Dreaming Spanish Beginner 或其他免費 A1 資源]\n"
                        "✍️ 寫作：[具體寫作任務，例如：用西班牙文寫出 5 個顏色和對應的物品]\n"
                        "🎯 本週重點：[一個具體詞彙主題或文法]\n\n"
                        "🇬🇧 本週英文作業\n"
                        "👂 聽說：[具體建議，例如：看一集 TED Talk 後用英文寫 3 句心得，或練習某個口說情境]\n"
                        "✍️ 寫作：[具體寫作任務，例如：寫一封 5 句的商務 email 請求延長截止日期]\n"
                        "🎯 本週重點：[一個商務表達或寫作技巧]\n\n"
                        "作業完成後傳給 Claude Code 批改！"
                    )
                }]
            }]
        }
        resp = requests.post(url, json=payload, timeout=15)
        data = resp.json()
        return data["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception as e:
        return f"（作業生成失敗：{e}）"

if __name__ == "__main__":
    homework = get_homework()

    message = (
        "📋 本週語言作業來囉！\n\n"
        "完成後傳給 Claude Code 批改，週日會有小測驗。\n\n"
        "━━━━━━━━━━━━━━━\n\n"
        + homework
    )

    if len(message) > 4096:
        message = message[:4090] + "..."

    requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        data={"chat_id": CHAT_ID, "text": message},
        timeout=10,
    )
