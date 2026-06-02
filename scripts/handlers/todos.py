"""
Todo / reminder management handlers.
Extracted from api_helpers.py.
"""

import re
from datetime import datetime, timedelta
from goal_tracker import TW_TZ, add_todo, get_todos, complete_todo_by_content


def _parse_reminder_date(s: str) -> str | None:
    today = datetime.now(TW_TZ).date()
    if s in ["今天"]:
        return today.strftime("%Y-%m-%d")
    if s in ["明天", "明日"]:
        return (today + timedelta(days=1)).strftime("%Y-%m-%d")
    if s in ["後天"]:
        return (today + timedelta(days=2)).strftime("%Y-%m-%d")
    m = re.match(r'^(\d{1,2})[/月](\d{1,2})日?$', s)
    if m:
        try:
            from datetime import date as _d
            mo, dy = int(m.group(1)), int(m.group(2))
            t = _d(today.year, mo, dy)
            if t < today:
                t = _d(today.year + 1, mo, dy)
            return t.strftime("%Y-%m-%d")
        except ValueError:
            return None
    return None


_TIME_EXPR = r'(?:今晚|今天晚上|晚上|早上|上午|下午|中午|凌晨|傍晚)(?:\d+|[零一二三四五六七八九十百]+)點\S*'


def _extract_reminder(text: str) -> tuple | None:
    """Parse reminder text, supporting with or without spaces."""
    # Pattern 1: 提醒我 明天 交報告 / 提醒我明天交報告
    m = re.match(r'^提醒我\s*(今天|明天|後天|明日)\s*(.*)', text)
    if m:
        return (None, m.group(1), m.group(2).strip())
    m = re.match(r'^提醒我\s*(\d{1,2}[/月]\d{1,2}日?)\s*(.*)', text)
    if m:
        return (None, m.group(1), m.group(2).strip())
    # Pattern 1b: 提醒我 晚上九點半 做事 → 今天
    m = re.match(rf'^提醒我\s*({_TIME_EXPR})\s*(.*)', text)
    if m:
        content = f"{m.group(1)} {m.group(2)}".strip()
        return (None, "今天", content)
    # Pattern 2: 提醒 太后 明天 交報告 / 提醒太后明天交報告
    m = re.match(r'^提醒\s*(\S+?)\s*(今天|明天|後天|明日)\s*(.*)', text)
    if m:
        return (m.group(1), m.group(2), m.group(3).strip())
    m = re.match(r'^提醒\s*(\S+?)\s*(\d{1,2}[/月]\d{1,2}日?)\s*(.*)', text)
    if m:
        return (m.group(1), m.group(2), m.group(3).strip())
    # Pattern 2b: 提醒 爸爸 晚上九點半 做事 → 今天
    m = re.match(rf'^提醒\s*(\S+?)\s*({_TIME_EXPR})\s*(.*)', text)
    if m:
        content = f"{m.group(2)} {m.group(3)}".strip()
        return (m.group(1), "今天", content)
    return None


def handle_add_todo(member: str, text: str) -> str:
    parsed = _extract_reminder(text)
    if not parsed:
        return "格式：提醒 [人名] [日期] [事項]\n或：提醒我 明天 要做XXX\n日期支援：今天/明天/後天/6/5"
    target, date_s, content = parsed
    if target is None:
        target = member or "你"
    if not content.strip():
        return "請加上提醒內容！\n例：提醒我明天 交報告"

    date_str = _parse_reminder_date(date_s)
    if not date_str:
        return f"看不懂日期「{date_s}」\n支援：今天/明天/後天/6月5日/6/5"

    ok = add_todo(target, date_str, content, member or "")
    if not ok:
        return "記錄失敗，等一下再試 😢"
    date_display = date_str[5:].replace("-", "/")
    by_str = f"（{member} 幫你記的）" if target != member and member else ""
    return f"✅ 已幫 {target} 記下！\n📅 {date_display}：{content}{by_str}\n前一天晚上和當天都會提醒 🔔"


def handle_view_todos() -> str:
    todos = get_todos(status="待辦")
    if not todos:
        return "🎉 目前沒有待辦事項！"
    today = datetime.now(TW_TZ).strftime("%Y-%m-%d")
    lines = ["📋 待辦事項：\n"]
    for t in sorted(todos, key=lambda x: x["date"]):
        date_display = t["date"][5:].replace("-", "/")
        overdue = " ⚠️ 逾期" if t["date"] < today else ""
        by = f"（{t['created_by']} 記的）" if t['created_by'] and t['created_by'] != t['member'] else ""
        lines.append(f"• {t['member']}｜{date_display} {t['content']}{overdue}{by}")
    return "\n".join(lines)


def handle_complete_todo(member: str, text: str) -> str | None:
    content = re.sub(r'^完成待辦\s*', '', text).strip()
    if not content:
        return None
    result = complete_todo_by_content(member, content)
    if result:
        return f"✅ 完成！「{result['content']}」從待辦清單移除 🎉"
    return f"找不到「{content}」在你的待辦裡"
