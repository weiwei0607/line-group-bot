"""
週末覆盤 — 由 GitHub Actions 每週日 20:00 (Asia/Taipei) 執行
內容：本週期打卡排名 + 加油語
支援：沒設目標、單一目標、多目標
"""

from goal_tracker import (
    get_cycle_info, get_goals, get_checkin_stats,
    get_checkin_log, get_completed_goals,
    get_cycle_total, _goal_keyword, _goal_days_from_log,
    is_goal_completed, _now,
)
from utils import call_gemini, send_line_message
from config import MEMBERS


def main():
    cycle_id, day, _ = get_cycle_info()
    total_days = get_cycle_total(cycle_id)

    goals = get_goals(cycle_id)
    stats = get_checkin_stats(cycle_id)
    log = get_checkin_log(cycle_id)
    completed = get_completed_goals(cycle_id)

    # 統計每個人
    rankings = []
    for member in MEMBERS:
        member_goals = goals.get(member, [])
        member_log = log.get(member, {})
        checked_days = stats.get(member, [])

        if member_goals:
            # 有設目標：計算每個目標的完成天數
            goal_details = []
            total_goal_days = 0
            for g in member_goals:
                if is_goal_completed(member, g, completed):
                    cnt = total_days
                else:
                    cnt = _goal_days_from_log(member_log, g)
                total_goal_days += cnt
                goal_details.append({
                    "keyword": _goal_keyword(g),
                    "days": cnt,
                    "completed": is_goal_completed(member, g, completed),
                })
            # 綜合完成率 = 所有目標完成天數 / (目標數 * 週期天數)
            rate = total_goal_days / (len(member_goals) * total_days) if total_days > 0 else 0
            rankings.append({
                "member": member,
                "rate": rate,
                "checked": len(checked_days),
                "total": total_days,
                "has_goals": True,
                "goal_details": goal_details,
            })
        else:
            # 沒設目標：只顯示打卡天數
            rankings.append({
                "member": member,
                "rate": 0,
                "checked": len(checked_days),
                "total": total_days,
                "has_goals": False,
                "goal_details": [],
            })

    # 排序：完成率高的在前
    rankings.sort(key=lambda x: (-x["rate"], -x["checked"]))

    # 生成統計文字
    lines = [f"🎯 十日目標週期覆盤（第 {day}/{total_days} 天）\n"]

    for i, r in enumerate(rankings, 1):
        medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else "🌟"
        if not r["has_goals"]:
            if r["checked"] > 0:
                lines.append(f"{medal} {r['member']}：還沒設目標，但打卡了 {r['checked']}/{r['total']} 天 📝")
            else:
                lines.append(f"{medal} {r['member']}：這週還沒設目標，下週一起來挑戰 💪")
        else:
            # 簡要顯示主要目標進度
            goal_parts = []
            for gd in r["goal_details"]:
                if gd["completed"]:
                    goal_parts.append(f"{gd['keyword']} ✅")
                else:
                    goal_parts.append(f"{gd['keyword']} {gd['days']}/{total_days}")
            goal_str = " ｜ ".join(goal_parts)
            lines.append(f"{medal} {r['member']}：{goal_str}")

    # MVP 和加油名單
    champion = next((r for r in rankings if r["has_goals"]), None)
    strugglers = [r for r in rankings if r["has_goals"] and r["rate"] < 0.5]
    no_goalers = [r for r in rankings if not r["has_goals"]]
    all_perfect = all(r["rate"] == 1.0 for r in rankings if r["has_goals"])

    lines.append("")
    if champion and champion["rate"] > 0:
        lines.append(f"🏆 本週 MVP：{champion['member']}！綜合完成率 {champion['rate']*100:.0f}%")
    if strugglers:
        names = "、".join(r["member"] for r in strugglers)
        lines.append(f"💪 {names} 再加把勁！")
    if no_goalers:
        names = "、".join(r["member"] for r in no_goalers)
        lines.append(f"📝 {names} 下週記得設目標喔！")
    if all_perfect and len([r for r in rankings if r["has_goals"]]) > 0:
        lines.append("🎉 大家都滿勤！太強了！")

    stats_text = "\n".join(lines)

    # 用 Gemini 潤飾
    polished = call_gemini(
        f"以下是朋友群十日目標的週末打卡統計：\n{stats_text}\n\n"
        "請幫我改寫成一則溫馨有趣的群組訊息，"
        "稱讚表現好的人、鼓勵需要加油的人、提醒還沒設目標的人，"
        "語氣像好朋友在聊天，不要教訓人，"
        "繁體中文，250字以內。"
    )

    msg = polished or stats_text
    send_line_message(msg)
    print(f"Weekly review sent at {_now().strftime('%H:%M')}")


if __name__ == "__main__":
    main()
