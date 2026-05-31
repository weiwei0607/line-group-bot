"""
每日群組總結 — 由 GitHub Actions 每天 23:30 (Asia/Taipei) 執行
讀取今天的聊天記錄，用 Gemini 生成摘要，存入記憶 tab
"""

import os
import requests
from goal_tracker import get_today_chat_logs, add_memory, _now

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")


def call_gemini(prompt):
    if not GEMINI_API_KEY:
        return None
    try:
        url = (f"https://generativelanguage.googleapis.com/v1beta/"
               f"models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}")
        resp = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=15)
        return resp.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception:
        return None


def main():
    today = _now().strftime("%Y-%m-%d")
    logs = get_today_chat_logs()

    if not logs:
        print("No chat logs today, skipping summary")
        return

    logs_text = "\n".join(f"{member}：{msg}" for member, msg in logs)

    prompt = (
        f"以下是今天群組的對話記錄：\n{logs_text}\n\n"
        "請提取今天群組的重要資訊，用條列式整理，繁體中文，簡短：\n"
        "• 大家做了什麼 / 發生什麼事\n"
        "• 有什麼計畫或決定\n"
        "• 有趣話題或值得記住的資訊\n"
        "沒有相關資訊的項目可略過。整體不超過 6 條。"
    )

    summary = call_gemini(prompt)
    if not summary:
        print("Gemini failed, skipping")
        return

    add_memory(today, summary)
    print(f"Saved daily summary for {today} ({len(logs)} messages)")


if __name__ == "__main__":
    main()
