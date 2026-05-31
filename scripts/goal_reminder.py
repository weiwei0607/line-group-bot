"""
十日目標提醒 — 由 GitHub Actions 每天 21:00 (Asia/Taipei) 執行
邏輯：
  每天：催促今天還沒打卡的人
  第 5 天：中期進度提醒
  倒數第 2 天：最後衝刺提醒
  最後一天：發送週期總結
"""

import os
import requests
from goal_tracker import (
    get_cycle_info, get_checkin_stats, get_goals,
    get_today_checkins, build_summary_text
)

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


def build_daily_checkin_reminder(cycle_id, day, total):
    """每天晚上提醒還沒打卡的人。"""
    goals = get_goals(cycle_id)
    today_checkins = get_today_checkins(cycle_id)

    if not goals:
        return None  # 沒人設目標就不提醒

    missing = [m for m in goals if m not in today_checkins]
    done = [m for m in goals if m in today_checkins]

    if not missing:
        # 所有人都打卡了
        lines = [f"🎉 今天大家都打卡了！（第 {day}/{total} 天）\n"]
        for member, content in today_checkins.items():
            lines.append(f"✅ {member}：{content}")
        lines.append("\n繼續保持！💪")
        return "\n".join(lines)
    else:
        lines = [f"⏰ 今日打卡提醒（第 {day}/{total} 天）\n"]
        if done:
            for m in done:
                lines.append(f"✅ {m} 已打卡")
        for m in missing:
            lines.append(f"❌ {m} 還沒打卡！")
        lines.append(f"\n快去打卡！輸入：打卡 今天做了XXX")
        return "\n".join(lines)


def build_midcycle_reminder(cycle_id, day, total):
    stats = get_checkin_stats(cycle_id)
    goals = get_goals(cycle_id)
    all_members = sorted(set(list(goals.keys()) + list(stats.keys())))

    lines = [f"📣 十日目標中期提醒（第 {day}/{total} 天）\n"]
    for member in all_members:
        count = len(stats.get(member, []))
        bar = "🟩" * count + "⬜" * (day - count)
        status = "✅" if count >= day // 2 else "⚠️"
        lines.append(f"{status} {member}：{bar} {count}/{day} 天")
        if not goals.get(member):
            lines.append("   （還沒設目標！快去設）")

    lines.append("\n後半段加油！目標就在前方 💪")
    return "\n".join(lines)


def build_final_reminder(cycle_id, day, total):
    stats = get_checkin_stats(cycle_id)
    goals = get_goals(cycle_id)
    all_members = sorted(set(list(goals.keys()) + list(stats.keys())))

    lines = [f"⚡ 最後衝刺！週期還剩 {total - day + 1} 天\n"]
    for member in all_members:
        count = len(stats.get(member, []))
        bar = "🟩" * count + "⬜" * (total - count)
        lines.append(f"  {member}：{bar} {count}/{total}")
    lines.append("\n還沒打卡的趕快補！🏃")
    return "\n".join(lines)


def main():
    cycle_id, day, total = get_cycle_info()
    sent = []

    # 每天都發：今日打卡提醒
    daily_msg = build_daily_checkin_reminder(cycle_id, day, total)
    if daily_msg:
        send_line_message(daily_msg)
        sent.append("daily checkin reminder")

    # 第 5 天額外發：中期提醒
    if day == 5:
        send_line_message(build_midcycle_reminder(cycle_id, day, total))
        sent.append("mid-cycle reminder")

    # 倒數第 2 天：最後衝刺
    elif day == total - 1 and total > 3:
        send_line_message(build_final_reminder(cycle_id, day, total))
        sent.append("final sprint reminder")

    # 最後一天：週期總結
    elif day == total:
        send_line_message(build_summary_text(cycle_id))
        sent.append("cycle summary")

    print(f"Day {day}/{total}: sent {sent}")


if __name__ == "__main__":
    main()
