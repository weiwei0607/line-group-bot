"""
每日群組總結 — 由 GitHub Actions 每天 23:30 (Asia/Taipei) 執行
讀取今天的聊天記錄：
  1. 生成群組摘要 → 存入 記憶 tab
  2. 為每位出現的成員生成個人摘要 → 存入 個人記憶 tab
"""

from utils import call_gemini
from goal_tracker import get_today_chat_logs, add_memory, add_personal_memory, _now
from config import MEMBERS


def main():
    today = _now().strftime("%Y-%m-%d")
    from goal_tracker import get_setting, set_setting
    if get_setting("daily_summary_last_run") == today:
        print(f"daily_summary already ran on {today}, skipping.")
        return

    today = _now().strftime("%Y-%m-%d")
    logs = get_today_chat_logs()

    if not logs:
        print("No chat logs today, skipping summary")
        return

    logs_text = "\n".join(f"{member}：{msg}" for member, msg in logs)

    # 1. Group summary
    group_summary = call_gemini(
        f"以下是今天群組的對話記錄：\n{logs_text}\n\n"
        "請提取今天群組的重要資訊，用條列式整理，繁體中文，簡短：\n"
        "• 大家做了什麼 / 發生什麼事\n"
        "• 有什麼計畫或決定\n"
        "• 有趣話題或值得記住的資訊\n"
        "沒有相關資訊的項目可略過。整體不超過 6 條。"
    )
    if group_summary:
        add_memory(today, group_summary)
        print(f"Saved group summary for {today}")

    # 2. Personal summaries for members who spoke today
    speakers = {m for m, _ in logs}
    for member in MEMBERS:
        if member not in speakers:
            continue
        member_logs = [(m, msg) for m, msg in logs if m == member]
        if not member_logs:
            continue
        member_text = "\n".join(f"{msg}" for _, msg in member_logs)
        personal = call_gemini(
            f"以下是{member}今天在群組說的話：\n{member_text}\n\n"
            f"請提取關於{member}個人的重要資訊（情緒狀態、計畫、喜好、困擾、近況），"
            "用 2-4 條條列式整理，繁體中文，簡短。"
            "只記錄有意義的個人資訊，若今天沒說什麼就回應「無」。"
        )
        if personal and personal.strip() != "無":
            add_personal_memory(member, personal)
            print(f"Saved personal memory for {member}")

    print(f"Done ({len(logs)} messages, {len(speakers)} speakers)")


    set_setting("daily_summary_last_run", today)
if __name__ == "__main__":
    main()
