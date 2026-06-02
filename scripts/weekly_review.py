"""
週末覆盤 — 由 GitHub Actions 每週日 20:00 (Asia/Taipei) 執行
內容：本週期打卡排名 + 加油語
"""

from goal_tracker import (
    get_cycle_info, get_goals, get_checkin_stats,
    get_cycle_total, _now, TW_TZ,
)
from utils import call_gemini, send_line_message
from config import MEMBERS


def main():
    cycle_id, day, _ = get_cycle_info()
    total_days = get_cycle_total(cycle_id)

    goals = get_goals(cycle_id)
    stats = get_checkin_stats(cycle_id)

    # 統計每個人
    rankings = []
    for member in MEMBERS:
        member_goals = goals.get(member, [])
        checked_days = stats.get(member, [])
        checked_count = len(checked_days)

        if member_goals:
            rate = checked_count / total_days if total_days > 0 else 0
        else:
            rate = 0
            checked_count = 0

        rankings.append({
            "member": member,
            "checked": checked_count,
            "total": total_days,
            "rate": rate,
            "has_goals": bool(member_goals),
        })

    # 排序：完成率高的在前
    rankings.sort(key=lambda x: (-x["rate"], -x["checked"]))

    # 生成統計文字
    lines = [f"🎯 十日目標週期覆盤（{cycle_id}）\n"]

    for i, r in enumerate(rankings, 1):
        medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else "🌟"
        if not r["has_goals"]:
            lines.append(f"{medal} {r['member']}：還沒設目標 😅")
        else:
            bar = "🟩" * r["checked"] + "⬜" * max(0, r["total"] - r["checked"])
            lines.append(f"{medal} {r['member']}：{bar} {r['checked']}/{r['total']} ({r['rate']*100:.0f}%)")

    # 找出 MVP 和需要加油的人
    champion = rankings[0] if rankings and rankings[0]["has_goals"] else None
    strugglers = [r for r in rankings if r["has_goals"] and r["rate"] < 0.5]
    all_perfect = all(r["rate"] == 1.0 for r in rankings if r["has_goals"])

    lines.append("")
    if champion and champion["rate"] > 0:
        lines.append(f"🏆 本週 MVP：{champion['member']}（{champion['checked']}/{champion['total']} 天）")
    if strugglers:
        names = "、".join(r["member"] for r in strugglers)
        lines.append(f"💪 {names} 再加把勁！")
    if all_perfect and len([r for r in rankings if r["has_goals"]]) > 0:
        lines.append("🎉 大家都滿勤！太強了！")

    stats_text = "\n".join(lines)

    # 用 Gemini 潤飾
    polished = call_gemini(
        f"以下是朋友群十日目標的週末打卡統計：\n{stats_text}\n\n"
        "請幫我改寫成一則溫馨有趣的群組訊息，"
        "稱讚表現好的人、鼓勵需要加油的人，"
        "語氣像好朋友在聊天，不要教訓人，"
        "繁體中文，250字以內。"
    )

    msg = polished or stats_text
    send_line_message(msg)
    print(f"Weekly review sent at {_now().strftime('%H:%M')}")


if __name__ == "__main__":
    main()
