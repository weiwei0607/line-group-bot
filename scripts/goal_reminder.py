"""
十日目標提醒 — 由 GitHub Actions 每天 21:00 (Asia/Taipei) 執行
邏輯：
  第 5 天：中期進度提醒
  第 total-2 天（倒數第 2 天）：最後衝刺提醒
  最後一天（total 天）：發送週期總結
"""

import os
import requests
from goal_tracker import get_cycle_info, get_checkin_stats, get_goals, build_summary_text

CHANNEL_ACCESS_TOKEN = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
GROUP_ID = os.environ["LINE_GROUP_ID"]


def send_line_message(text):
    requests.post(
        "https://api.line.me/v2/bot/message/push",
        headers={
            "Authorization": f"Bearer {CHANNEL_ACCESS_TOKEN}",
            "Content-Type": "application/json",
        },
        json={"to": GROUP_ID, "messages": [{"type": "text", "text": text}]},
        timeout=10,
    )


def build_midcycle_reminder(cycle_id, day, total):
    stats = get_checkin_stats(cycle_id)
    goals = get_goals(cycle_id)
    all_members = sorted(set(list(goals.keys()) + list(stats.keys())))

    lines = [f"📣 十日目標中期提醒（第 {day}/{total} 天）\n"]
    for member in all_members:
        count = len(stats.get(member, []))
        member_goals = goals.get(member, [])
        status = "✅" if count >= day // 2 else "⚠️"
        lines.append(f"{status} {member}：已打卡 {count} 天")
        if not member_goals:
            lines.append(f"   （還沒設目標！）")

    lines.append("\n後半段加油！目標就在前方 💪")
    return "\n".join(lines)


def build_final_reminder(cycle_id, day, total):
    stats = get_checkin_stats(cycle_id)
    lines = [f"⚡ 最後衝刺！週期剩 {total - day + 1} 天\n"]
    if stats:
        for member, days in sorted(stats.items()):
            lines.append(f"  {member}：{len(days)} 天打卡")
    lines.append("\n還沒打卡的趕快補！🏃")
    return "\n".join(lines)


def main():
    cycle_id, day, total = get_cycle_info()

    if day == 5:
        msg = build_midcycle_reminder(cycle_id, day, total)
        send_line_message(msg)
        print(f"Sent mid-cycle reminder (day {day}/{total})")

    elif day == total - 1 and total > 3:
        msg = build_final_reminder(cycle_id, day, total)
        send_line_message(msg)
        print(f"Sent final sprint reminder (day {day}/{total})")

    elif day == total:
        msg = build_summary_text(cycle_id)
        send_line_message(msg)
        print(f"Sent cycle summary (day {day}/{total})")

    else:
        print(f"No reminder today (day {day}/{total})")


if __name__ == "__main__":
    main()
