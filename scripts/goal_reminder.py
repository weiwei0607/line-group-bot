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
from datetime import datetime, timedelta, timezone
from goal_tracker import (
    get_cycle_info, get_checkin_stats, get_goals,
    get_today_checkins, build_summary_text, get_next_cycle_start,
    get_todos_by_date, get_overdue_todos, TW_TZ,
)

MEMBERS = ["太后", "毛毛", "二毛"]

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


def build_next_cycle_reminder():
    next_start = get_next_cycle_start()
    return (
        f"📅 週期即將結束，記得去訂下一輪（{next_start} 開始）的目標！\n\n"
        f"指令：設目標：目標1 / 目標2 / 目標3\n"
        f"趁現在反思這輪做得如何，下輪更精準！💪"
    )


def build_day1_reminder(cycle_id, total):
    goals = get_goals(cycle_id)
    missing = [m for m in MEMBERS if m not in goals]
    if not missing:
        return None
    missing_str = " / ".join(missing)
    return (
        f"🎯 新的十日週期開始！（共 {total} 天）\n\n"
        f"⚠️ {missing_str} 還沒設目標！\n\n"
        f"指令：設目標：目標1 / 目標2 / 目標3\n"
        f"趁現在想清楚這十天要做什麼 💪"
    )


def check_todos():
    now = datetime.now(TW_TZ)
    today = now.strftime("%Y-%m-%d")
    tomorrow = (now + timedelta(days=1)).strftime("%Y-%m-%d")

    # 前一天提醒（明天要做的）
    tmr_todos = get_todos_by_date(tomorrow)
    if tmr_todos:
        lines = ["⏰ 明天待辦提醒！\n"]
        for t in tmr_todos:
            lines.append(f"📌 {t['member']}：{t['content']}")
        lines.append("\n記得完成後傳「完成待辦 [事項]」")
        send_line_message("\n".join(lines))

    # 當天提醒（今天要做的）
    today_todos = get_todos_by_date(today)
    if today_todos:
        lines = ["📢 今日待辦！快去做！\n"]
        for t in today_todos:
            lines.append(f"📌 {t['member']}：{t['content']}")
        send_line_message("\n".join(lines))

    # 公審：逾期未完成
    overdue = get_overdue_todos()
    if overdue:
        lines = ["📢 公審時間！\n"]
        for t in overdue:
            lines.append(f"⚠️ {t['member']} 說要做「{t['content']}」，到現在還沒做！！")
        lines.append("\n快來問問他們怎麼了 👀👀👀")
        send_line_message("\n".join(lines))


def main():
    cycle_id, day, total = get_cycle_info()
    sent = []

    # 第 1 天：催未設目標的人
    if day == 1:
        day1_msg = build_day1_reminder(cycle_id, total)
        if day1_msg:
            send_line_message(day1_msg)
            sent.append("day1 goal reminder")

    # 每天都發：今日打卡提醒
    daily_msg = build_daily_checkin_reminder(cycle_id, day, total)
    if daily_msg:
        send_line_message(daily_msg)
        sent.append("daily checkin reminder")

    # 第 5 天額外發：中期提醒
    if day == 5:
        send_line_message(build_midcycle_reminder(cycle_id, day, total))
        sent.append("mid-cycle reminder")

    # 倒數第 2 天：最後衝刺 + 提醒設下輪目標
    elif day == total - 1 and total > 3:
        send_line_message(build_final_reminder(cycle_id, day, total))
        send_line_message(build_next_cycle_reminder())
        sent.append("final sprint reminder + next cycle")

    # 最後一天：週期總結 + 再次提醒設下輪目標
    elif day == total:
        send_line_message(build_summary_text(cycle_id))
        send_line_message(build_next_cycle_reminder())
        sent.append("cycle summary + next cycle")

    # 待辦提醒 + 公審
    check_todos()
    sent.append("todo check")

    print(f"Day {day}/{total}: sent {sent}")


if __name__ == "__main__":
    main()
