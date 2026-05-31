"""
十日目標追蹤 — Google Sheets 存取
Sheets 結構:
  Tab「目標」: cycle_id | member | goals | created_at
  Tab「打卡」: timestamp | cycle_id | day | member | content
"""

import os
import calendar
import requests
from datetime import datetime, timezone, timedelta

TW_TZ = timezone(timedelta(hours=8))

GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REFRESH_TOKEN = os.environ.get("GOOGLE_REFRESH_TOKEN", "")
GOAL_SHEET_ID = os.environ.get("GOAL_SHEET_ID", "")


def _now():
    return datetime.now(TW_TZ)


def _get_token():
    r = requests.post("https://oauth2.googleapis.com/token", data={
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "refresh_token": GOOGLE_REFRESH_TOKEN,
        "grant_type": "refresh_token",
    }, timeout=10)
    return r.json().get("access_token")


def _sheets_get(token, range_):
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{GOAL_SHEET_ID}/values/{range_}"
    r = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=10)
    return r.json().get("values", [])


def _sheets_append(token, range_, values):
    url = (f"https://sheets.googleapis.com/v4/spreadsheets/{GOAL_SHEET_ID}"
           f"/values/{range_}:append?valueInputOption=USER_ENTERED")
    requests.post(url,
                  headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                  json={"values": values}, timeout=10)


def _sheets_update(token, range_, values):
    url = (f"https://sheets.googleapis.com/v4/spreadsheets/{GOAL_SHEET_ID}"
           f"/values/{range_}?valueInputOption=USER_ENTERED")
    requests.put(url,
                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                 json={"values": values}, timeout=10)


def get_cycle_info(date=None):
    """Returns (cycle_id, day_in_cycle, total_days)"""
    if date is None:
        date = _now()
    d, y, m = date.day, date.year, date.month
    last_day = calendar.monthrange(y, m)[1]

    if d <= 10:
        start, end = 1, 10
    elif d <= 20:
        start, end = 11, 20
    else:
        start, end = 21, last_day

    cycle_id = f"{y}-{m:02d}-{start:02d}"
    return cycle_id, d - start + 1, end - start + 1


def set_goals(member, goals: list) -> bool:
    """Store 3-5 goals for member in current cycle."""
    if not GOAL_SHEET_ID:
        return False
    try:
        token = _get_token()
        cycle_id, _, _ = get_cycle_info()
        now_str = _now().strftime("%Y-%m-%d %H:%M")
        goals_str = " / ".join(g.strip() for g in goals if g.strip())

        rows = _sheets_get(token, "目標!A:D")
        for i, row in enumerate(rows[1:], 2):  # skip header row
            if len(row) >= 2 and row[0] == cycle_id and row[1] == member:
                _sheets_update(token, f"目標!C{i}:D{i}", [[goals_str, now_str]])
                return True

        _sheets_append(token, "目標!A:D", [[cycle_id, member, goals_str, now_str]])
        return True
    except Exception:
        return False


def get_goals(cycle_id=None) -> dict:
    """Returns {member: [goal1, goal2, ...]} for given cycle."""
    if not GOAL_SHEET_ID:
        return {}
    try:
        token = _get_token()
        if cycle_id is None:
            cycle_id, _, _ = get_cycle_info()
        rows = _sheets_get(token, "目標!A:C")
        result = {}
        for row in rows[1:]:
            if len(row) >= 3 and row[0] == cycle_id:
                goals = [g.strip() for g in row[2].split("/") if g.strip()]
                result[row[1]] = goals
        return result
    except Exception:
        return {}


def add_checkin(member, content) -> tuple:
    """Log today's check-in. Returns (day_in_cycle, total_days) or (0, 0) on error."""
    if not GOAL_SHEET_ID:
        return 0, 0
    try:
        token = _get_token()
        now = _now()
        cycle_id, day, total = get_cycle_info(now)
        date_str = now.strftime("%Y-%m-%d %H:%M")
        _sheets_append(token, "打卡!A:E", [[date_str, cycle_id, day, member, content]])
        return day, total
    except Exception:
        return 0, 0


def get_checkin_stats(cycle_id=None) -> dict:
    """Returns {member: [day1, day2, ...]} — which days each member checked in."""
    if not GOAL_SHEET_ID:
        return {}
    try:
        token = _get_token()
        if cycle_id is None:
            cycle_id, _, _ = get_cycle_info()
        rows = _sheets_get(token, "打卡!A:E")
        result = {}
        for row in rows[1:]:
            if len(row) >= 5 and row[1] == cycle_id:
                member, day = row[3], int(row[2])
                if member not in result:
                    result[member] = []
                if day not in result[member]:
                    result[member].append(day)
        return result
    except Exception:
        return {}


def build_summary_text(cycle_id=None) -> str:
    """Build a human-readable cycle summary."""
    if cycle_id is None:
        cycle_id, day, total = get_cycle_info()
    else:
        day, total = 0, 0

    goals = get_goals(cycle_id)
    stats = get_checkin_stats(cycle_id)
    all_members = sorted(set(list(goals.keys()) + list(stats.keys())))

    lines = [f"📊 十日目標週期總結（{cycle_id}）\n"]
    for member in all_members:
        member_goals = goals.get(member, [])
        checked_days = sorted(stats.get(member, []))
        count = len(checked_days)
        lines.append(f"👤 {member}")
        if member_goals:
            for i, g in enumerate(member_goals, 1):
                lines.append(f"  目標 {i}：{g}")
        else:
            lines.append("  （未設目標）")
        lines.append(f"  打卡：{count} 天 {'✅' if count >= total * 0.7 else '⚠️'}")
        lines.append("")

    return "\n".join(lines).strip()
