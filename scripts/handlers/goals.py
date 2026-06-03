"""
Goal / cycle management handlers.
Extracted from api_helpers.py to reduce file size.
"""

import re
import random
import threading
from datetime import datetime
from goal_tracker import (
    TW_TZ,
    get_cycle_info,
    get_goals,
    get_checkin_stats,
    get_checkin_log,
    get_completed_goals,
    is_goal_completed,
    set_goals,
    add_checkin,
    get_streak,
    get_last_cycle_id,
    build_summary_text,
    get_next_cycle_id,
    get_next_cycle_start,
    get_today_checkins,
    get_memories,
)
from utils import call_gemini
from config import MEMBERS


def handle_suggest_goals(member, text):
    topic = re.sub(r'^幫我想目標\s*', '', text).strip()
    cycle_id, day, total = get_cycle_info()
    current = get_goals(cycle_id).get(member, [])
    memories = get_memories(days=7)

    context_parts = []
    if current:
        context_parts.append(f"{member} 目前設的目標：{' / '.join(current)}")
    if topic:
        context_parts.append(f"方向偏好：{topic}")
    if memories:
        context_parts.append("群組最近狀況：" + "；".join(c[:80] for _, c in memories[-3:]))
    context = "\n".join(context_parts)

    result = call_gemini(
        f"請幫{member}想 3-5 個適合十天週期的個人目標。\n"
        f"{context}\n\n"
        "要求：具體可執行、適合每天打卡確認、有挑戰性但不過分。\n"
        "例如：每天走 6000 步、每天背 5 個單字、每天記帳、每天做 10 分鐘伸展\n"
        "格式：每行一個目標加 emoji，結尾附一行提醒如何設定。"
    )
    suffix = "\n\n💡 設定指令：設目標：目標1 / 目標2 / 目標3"
    return (result or "建議：\n💧 每天喝 2000ml 水\n🚶 每天走 6000 步\n📚 每天讀 20 分鐘書") + suffix


def handle_last_cycle():
    last_id = get_last_cycle_id()
    return build_summary_text(last_id)


def parse_goals(text_after_prefix):
    text = re.sub(r'^\d+[.、]\s*', '', text_after_prefix.strip(), flags=re.MULTILINE)
    parts = re.split(r'[\n/、；;]+', text)
    return [p.strip() for p in parts if p.strip()][:5]


def handle_set_goals(member, text):
    after = re.sub(r'^設目標[：:]\s*', '', text).strip()
    if not after:
        return "目標內容不能空白！\n格式：設目標：目標1 / 目標2 / 目標3"

    goals = parse_goals(after)
    if not goals:
        return "沒讀到目標，試試：設目標：目標1 / 目標2 / 目標3"

    cycle_id, day, total = get_cycle_info()
    goals_preview = "\n".join(f"  {i+1}. {g}" for i, g in enumerate(goals))

    if day >= total:
        # 最後一天：存到下一週期
        next_id = get_next_cycle_id()
        next_start = get_next_cycle_start()
        threading.Thread(target=set_goals, args=(member, goals, next_id), daemon=True).start()
        return (
            f"✅ {member} 的下輪目標設定完成！\n\n"
            f"{goals_preview}\n\n"
            f"📅 {next_start} 週期開始生效 🎯"
        )

    threading.Thread(target=set_goals, args=(member, goals), daemon=True).start()
    return (
        f"✅ {member} 的十日目標設定完成！\n\n"
        f"{goals_preview}\n\n"
        f"📅 現在第 {day}/{total} 天，加油！💪"
    )


def handle_checkin(member, text, user_goals=None):
    # 沒設目標不能打卡
    if not user_goals:
        return (
            "你還沒設目標喔！先輸入「設目標：目標1 / 目標2」才能打卡 💪\n"
            "不知道設什麼？可以說「幫我想目標」讓我幫你想 😊"
        )

    content = re.sub(r'^打卡\s*', '', text).strip() or "打卡"
    day, total = add_checkin(member, content)
    if day == 0:
        return "打卡失敗 😢 等一下再試試？"

    # 判斷這次打卡對應哪個目標，並計算該目標的進度
    log = get_checkin_log()
    member_log = log.get(member, {})

    matched_goal = None
    for g in user_goals:
        kw = _goal_keyword(g).lower()
        if kw in content.lower() or content.lower() in g.lower():
            matched_goal = g
            break

    if matched_goal:
        goal_kw = _goal_keyword(matched_goal).lower()
        checked_days = {d for d, contents in member_log.items() if any(goal_kw in c.lower() for c in contents)}
        # 計算該目標的連續天數
        streak = 0
        for d in range(day, 0, -1):
            if d in checked_days:
                streak += 1
            else:
                break
    else:
        stats = get_checkin_stats()
        checked_days = set(stats.get(member, []))
        streak = get_streak(member)

    streak_msg = ""
    if streak >= 7:
        streak_msg = f"🔥🔥 連續 {streak} 天！神人！"
    elif streak >= 5:
        streak_msg = f"🔥 連續 {streak} 天！太厲害了！"
    elif streak >= 3:
        streak_msg = f"🔥 連續 {streak} 天！繼續衝！"
    elif streak == 2:
        streak_msg = "連兩天了！保持！"

    goals_str = " / ".join(user_goals[:3])
    enc = call_gemini(
        f"有人叫{member}，他的十日目標是：{goals_str}。\n"
        f"今天他打卡說：「{content}」（第{day}/{total}天）\n"
        f"請給一句輕鬆真誠的鼓勵，提到他的目標，台灣年輕人語氣，不超過 2 句。"
    )
    if not enc:
        enc = random.choice(["太棒了！繼續保持！", "你最行！衝衝衝！", "很好！繼續！"])

    # 進度條：只顯示當前打卡目標的完成天數
    bar = "".join("🟩" if d in checked_days else "⬜" for d in range(1, total + 1))
    checked_count = len(checked_days)
    parts = [f"✅ {member} 打卡成功！", f"📝 {content}", bar, f"第 {day}/{total} 天 | 共 {checked_count} 天｜{enc}"]
    if streak_msg:
        parts.append(streak_msg)
    return "\n".join(parts)


def _goal_keyword(goal: str) -> str:
    """Extract core keyword from goal like '每天duolingo' → 'duolingo'."""
    key = re.sub(r'^每天\s*', '', goal).strip()
    key = re.sub(r'一件東西$|一次$|一下$|一個$', '', key).strip()
    return key or goal


def _goal_days(log: dict, goal: str, total: int) -> int:
    """Count days where any check-in content mentions the goal keyword."""
    kw = _goal_keyword(goal).lower()
    count = 0
    for contents in log.values():
        if any(kw in c.lower() for c in contents):
            count += 1
    return count


def handle_view_goals():
    cycle_id, day, total = get_cycle_info()
    goals = get_goals(cycle_id)
    stats = get_checkin_stats(cycle_id)

    if not goals and not stats:
        return (
            f"本週期（第 {day}/{total} 天）還沒有人設目標！\n\n"
            f"輸入：設目標：目標1 / 目標2 / 目標3\n"
            f"可設 3–5 個目標"
        )

    log = get_checkin_log(cycle_id)
    completed = get_completed_goals(cycle_id)
    lines = [f"🎯 本週期目標（第 {day}/{total} 天）\n"]
    all_members = sorted(set(list(goals.keys()) + list(stats.keys())))
    for member in all_members:
        member_goals = goals.get(member, [])
        member_log = log.get(member, {})
        lines.append(f"👤 {member}")
        if member_goals:
            for g in member_goals:
                kw = _goal_keyword(g)
                if is_goal_completed(member, g, completed):
                    lines.append(f"  ✅ {kw}｜已完成")
                else:
                    cnt = _goal_days(member_log, g, total)
                    bar = "🟩" * cnt + "⬜" * max(0, total - cnt)
                    lines.append(f"  {kw}｜{bar} {cnt}/{total}")
        else:
            checked = set(stats.get(member, []))
            bar = "".join("🟩" if d in checked else "⬜" for d in range(1, total + 1))
            lines.append(f"  打卡｜{bar} {len(checked)}/{total}")
        lines.append("")
    return "\n".join(lines).strip()


def handle_cycle_progress():
    cycle_id, day, total = get_cycle_info()
    stats = get_checkin_stats(cycle_id)
    lines = [f"📅 十日週期第 {day}/{total} 天\n"]
    if stats:
        for member, days in sorted(stats.items()):
            checked = set(days)
            bar = "".join("🟩" if d in checked else "⬜" for d in range(1, total + 1))
            lines.append(f"{member}：{bar} {len(days)}/{total}")
    else:
        lines.append("還沒有人打卡 😶")
    return "\n".join(lines)


def handle_today_checkins():
    cycle_id, day, total = get_cycle_info()
    checkins = get_today_checkins(cycle_id)
    goals = get_goals(cycle_id)
    if not checkins:
        return f"今天（第 {day} 天）還沒有人打卡！快去打卡 💪"
    lines = [f"📋 今日打卡（第 {day}/{total} 天）\n"]
    for member, content in checkins.items():
        lines.append(f"✅ {member}：{content}")
    # Check who hasn't checked in (include all members with goals)
    missing = [m for m in goals if m not in checkins]
    if missing:
        lines.append(f"\n還沒打卡：{' / '.join(missing)} 快去！")
    return "\n".join(lines)
