"""
每日群組總結 — 由 GitHub Actions 每天 23:30 (Asia/Taipei) 執行
讀取今天的聊天記錄：
  1. 生成群組摘要 → 存入 記憶 tab
  2. 為每位出現的成員生成個人摘要 → 存入 個人記憶 tab

手動測試：
  FORCE_RUN=1 python daily_summary.py
"""

import os
import sys
import traceback

import os
from utils import call_gemini, send_telegram_alert
from goal_tracker import get_today_chat_logs, add_memory, add_personal_memory, _now
from config import MEMBERS

GOAL_SHEET_ID = os.environ.get("GOAL_SHEET_ID", "")


FORCE_RUN = os.environ.get("FORCE_RUN", "") in ("1", "true", "yes")


def main():
    now = _now()
    today = now.strftime("%Y-%m-%d")
    from goal_tracker import get_setting, set_setting

    # 防重複（除非手動強制執行）
    if not FORCE_RUN and get_setting("daily_summary_last_run") == today:
        print(f"daily_summary already ran on {today}, skipping. (set FORCE_RUN=1 to override)")
        return

    print(f"[{now}] Starting daily_summary for {today}")
    print(f"GOAL_SHEET_ID set: {bool(GOAL_SHEET_ID)}")

    # 讀取聊天記錄
    try:
        logs = get_today_chat_logs()
    except Exception as exc:
        err = f"get_today_chat_logs failed: {type(exc).__name__}: {exc}\n{traceback.format_exc()[-500:]}"
        print(err)
        send_telegram_alert(err)
        return

    print(f"Chat logs today: {len(logs)} messages")
    if not logs:
        print("No chat logs today, skipping summary")
        # 沒記錄也標記 done，避免一直重試
        set_setting("daily_summary_last_run", today)
        return

    logs_text = "\n".join(f"{member}：{msg}" for member, msg in logs)

    # 1. Group summary
    try:
        print("Generating group summary...")
        group_summary = call_gemini(
            f"以下是今天群組的對話記錄：\n{logs_text}\n\n"
            "請提取今天群組的重要資訊，用條列式整理，繁體中文，簡短：\n"
            "• 大家做了什麼 / 發生什麼事\n"
            "• 有什麼計畫或決定\n"
            "• 有趣話題或值得記住的資訊\n"
            "沒有相關資訊的項目可略過。整體不超過 6 條。"
        )
        if group_summary:
            ok = add_memory(today, group_summary)
            print(f"Saved group summary for {today}: ok={ok}")
        else:
            print("WARNING: group_summary is empty (Gemini returned None)")
            send_telegram_alert("daily_summary: Gemini returned None for group summary")
    except Exception as exc:
        err = f"Group summary failed: {type(exc).__name__}: {exc}\n{traceback.format_exc()[-500:]}"
        print(err)
        send_telegram_alert(err)

    # 2. Personal summaries for members who spoke today
    speakers = {m for m, _ in logs}
    print(f"Speakers today: {speakers}")
    for member in MEMBERS:
        if member not in speakers:
            continue
        member_logs = [(m, msg) for m, msg in logs if m == member]
        if not member_logs:
            continue
        member_text = "\n".join(f"{msg}" for _, msg in member_logs)
        try:
            print(f"Generating personal summary for {member}...")
            personal = call_gemini(
                f"以下是{member}今天在群組說的話：\n{member_text}\n\n"
                f"請提取關於{member}個人的重要資訊（情緒狀態、計畫、喜好、困擾、近況），"
                "用 2-4 條條列式整理，繁體中文，簡短。"
                "只記錄有意義的個人資訊，若今天沒說什麼就回應「無」。"
            )
            if personal and personal.strip() != "無":
                ok = add_personal_memory(member, personal)
                print(f"Saved personal memory for {member}: ok={ok}")
            else:
                print(f"No meaningful personal info for {member}")
        except Exception as exc:
            err = f"Personal summary for {member} failed: {type(exc).__name__}: {exc}\n{traceback.format_exc()[-500:]}"
            print(err)
            send_telegram_alert(err)

    print(f"Done ({len(logs)} messages, {len(speakers)} speakers)")
    set_setting("daily_summary_last_run", today)


if __name__ == "__main__":
    main()
