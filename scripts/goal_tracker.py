"""
十日目標追蹤 — Google Sheets 存取（含 token 快取）
Sheets 結構:
  Tab「目標」: cycle_id | member | goals | created_at
  Tab「打卡」: timestamp | cycle_id | day | member | content
  Tab「暱稱」: user_id | nickname
  Tab「設定」: key | value  （存 last_activity 等全域設定）
"""

import os
import time
import calendar
import requests
from datetime import datetime, timezone, timedelta

TW_TZ = timezone(timedelta(hours=8))

GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REFRESH_TOKEN = os.environ.get("GOOGLE_REFRESH_TOKEN", "")
GOAL_SHEET_ID = os.environ.get("GOAL_SHEET_ID", "")

# ─── Token cache ──────────────────────────────────────────
_token_cache = {"token": None, "expires_at": 0}


def _get_token():
    now = time.time()
    if _token_cache["token"] and _token_cache["expires_at"] > now + 60:
        return _token_cache["token"]
    r = requests.post("https://oauth2.googleapis.com/token", data={
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "refresh_token": GOOGLE_REFRESH_TOKEN,
        "grant_type": "refresh_token",
    }, timeout=10)
    data = r.json()
    token = data.get("access_token")
    _token_cache["token"] = token
    _token_cache["expires_at"] = now + data.get("expires_in", 3600)
    return token


# ─── Nickname cache (10 min TTL) ─────────────────────────
_nickname_cache = {}  # {user_id: (timestamp, nickname_or_None)}
_NICKNAME_TTL = 600

# ─── Goals cache (in-process, 5 min TTL) ─────────────────
_goals_cache = {}  # {cycle_id: (timestamp, {member: [goals]})}
_GOALS_TTL = 300


def _now():
    return datetime.now(TW_TZ)


# ─── Sheets helpers ───────────────────────────────────────

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


# ─── Cycle logic ──────────────────────────────────────────

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


# ─── Nickname ─────────────────────────────────────────────

def get_nickname(user_id):
    cached = _nickname_cache.get(user_id)
    if cached and time.time() - cached[0] < _NICKNAME_TTL:
        return cached[1]
    if not GOAL_SHEET_ID:
        return None
    try:
        token = _get_token()
        rows = _sheets_get(token, "暱稱!A:B")
        for row in rows[1:]:
            if len(row) >= 2 and row[0] == user_id:
                _nickname_cache[user_id] = (time.time(), row[1])
                return row[1]
        _nickname_cache[user_id] = (time.time(), None)
        return None
    except Exception:
        return None


def set_nickname(user_id, nickname):
    if not GOAL_SHEET_ID:
        return False
    try:
        token = _get_token()
        rows = _sheets_get(token, "暱稱!A:B")
        for i, row in enumerate(rows[1:], 2):
            if len(row) >= 1 and row[0] == user_id:
                _sheets_update(token, f"暱稱!B{i}", [[nickname]])
                _nickname_cache[user_id] = (time.time(), nickname)
                return True
        _sheets_append(token, "暱稱!A:B", [[user_id, nickname]])
        _nickname_cache[user_id] = (time.time(), nickname)
        return True
    except Exception:
        return False


# ─── Goals ────────────────────────────────────────────────

def set_goals(member, goals: list) -> bool:
    if not GOAL_SHEET_ID:
        return False
    try:
        token = _get_token()
        cycle_id, _, _ = get_cycle_info()
        now_str = _now().strftime("%Y-%m-%d %H:%M")
        goals_str = " / ".join(g.strip() for g in goals if g.strip())

        rows = _sheets_get(token, "目標!A:D")
        for i, row in enumerate(rows[1:], 2):
            if len(row) >= 2 and row[0] == cycle_id and row[1] == member:
                _sheets_update(token, f"目標!C{i}:D{i}", [[goals_str, now_str]])
                _goals_cache.pop(cycle_id, None)  # invalidate cache
                return True

        _sheets_append(token, "目標!A:D", [[cycle_id, member, goals_str, now_str]])
        _goals_cache.pop(cycle_id, None)
        return True
    except Exception:
        return False


def get_goals(cycle_id=None) -> dict:
    """Returns {member: [goal1, goal2, ...]}"""
    if not GOAL_SHEET_ID:
        return {}
    if cycle_id is None:
        cycle_id, _, _ = get_cycle_info()

    # Check cache
    cached = _goals_cache.get(cycle_id)
    if cached and time.time() - cached[0] < _GOALS_TTL:
        return cached[1]

    try:
        token = _get_token()
        rows = _sheets_get(token, "目標!A:C")
        result = {}
        for row in rows[1:]:
            if len(row) >= 3 and row[0] == cycle_id:
                goals = [g.strip() for g in row[2].split("/") if g.strip()]
                result[row[1]] = goals
        _goals_cache[cycle_id] = (time.time(), result)
        return result
    except Exception:
        return {}


# ─── Check-ins ────────────────────────────────────────────

def add_checkin(member, content) -> tuple:
    """Returns (day_in_cycle, total_days) or (0, 0) on error."""
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
    """Returns {member: [day1, day2, ...]}"""
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


def get_today_checkins(cycle_id=None) -> dict:
    """Returns {member: content} for today's check-ins only."""
    if not GOAL_SHEET_ID:
        return {}
    try:
        token = _get_token()
        if cycle_id is None:
            cycle_id, day, _ = get_cycle_info()
        else:
            _, day, _ = get_cycle_info()
        rows = _sheets_get(token, "打卡!A:E")
        result = {}
        for row in rows[1:]:
            if len(row) >= 5 and row[1] == cycle_id and int(row[2]) == day:
                result[row[3]] = row[4]
        return result
    except Exception:
        return {}


# ─── Streak ───────────────────────────────────────────────

def get_streak(member, cycle_id=None) -> int:
    """Returns consecutive days checked in ending at today."""
    if cycle_id is None:
        cycle_id, day, _ = get_cycle_info()
    else:
        _, day, _ = get_cycle_info()
    stats = get_checkin_stats(cycle_id)
    checked = set(stats.get(member, []))
    streak = 0
    for d in range(day, 0, -1):
        if d in checked:
            streak += 1
        else:
            break
    return streak


# ─── Summary ──────────────────────────────────────────────

def build_summary_text(cycle_id=None) -> str:
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
        emoji = "🏆" if count >= total * 0.8 else ("✅" if count >= total * 0.5 else "⚠️")
        lines.append(f"{emoji} {member}（{count}/{total} 天）")
        if member_goals:
            for i, g in enumerate(member_goals, 1):
                lines.append(f"  目標 {i}：{g}")
        else:
            lines.append("  （未設目標）")
        lines.append("")

    lines.append("大家這十天辛苦了！下個週期繼續加油 💪")
    return "\n".join(lines).strip()


# ─── Group memory ────────────────────────────────────────

def log_chat_message(member, message) -> None:
    """Log a group message to 聊天記錄 tab (call in background thread)."""
    if not GOAL_SHEET_ID:
        return
    try:
        token = _get_token()
        now_str = _now().strftime("%Y-%m-%d %H:%M")
        _sheets_append(token, "聊天記錄!A:C", [[now_str, member, message]])
    except Exception:
        pass


def get_today_chat_logs() -> list:
    """Returns today's chat logs as [(member, message), ...]"""
    if not GOAL_SHEET_ID:
        return []
    try:
        token = _get_token()
        rows = _sheets_get(token, "聊天記錄!A:C")
        today = _now().strftime("%Y-%m-%d")
        result = []
        for row in rows[1:]:
            if len(row) >= 3 and row[0].startswith(today):
                result.append((row[1], row[2]))
        return result[-200:]  # cap at 200 messages
    except Exception:
        return []


def add_memory(date_str, content) -> bool:
    """Store a daily summary in 記憶 tab."""
    if not GOAL_SHEET_ID:
        return False
    try:
        token = _get_token()
        rows = _sheets_get(token, "記憶!A:B")
        for i, row in enumerate(rows[1:], 2):
            if len(row) >= 1 and row[0] == date_str:
                _sheets_update(token, f"記憶!B{i}", [[content]])
                return True
        _sheets_append(token, "記憶!A:B", [[date_str, content]])
        return True
    except Exception:
        return False


def get_memories(days=5) -> list:
    """Returns last N days of summaries as [(date, content), ...]"""
    if not GOAL_SHEET_ID:
        return []
    try:
        token = _get_token()
        rows = _sheets_get(token, "記憶!A:B")
        data = [(r[0], r[1]) for r in rows[1:] if len(r) >= 2]
        return data[-days:]
    except Exception:
        return []


# ─── Next cycle info ──────────────────────────────────────

def get_next_cycle_start() -> str:
    """Returns next cycle start date string like '6/11' or '7/1'."""
    import calendar as cal
    now = _now()
    d, y, m = now.day, now.year, now.month
    last_day = cal.monthrange(y, m)[1]

    if d <= 10:
        return f"{m}/11"
    elif d <= 20:
        return f"{m}/21"
    else:
        next_m = m + 1 if m < 12 else 1
        next_y = y if m < 12 else y + 1
        return f"{next_m}/1"


# ─── Last activity (silence detection) ───────────────────

def update_last_activity():
    """Store current timestamp as last group activity."""
    if not GOAL_SHEET_ID:
        return
    try:
        token = _get_token()
        now_str = _now().strftime("%Y-%m-%d %H:%M:%S")
        rows = _sheets_get(token, "設定!A:B")
        for i, row in enumerate(rows, 1):
            if len(row) >= 1 and row[0] == "last_activity":
                _sheets_update(token, f"設定!B{i}", [[now_str]])
                return
        _sheets_append(token, "設定!A:B", [["last_activity", now_str]])
    except Exception:
        pass


def get_last_activity():
    """Returns last activity datetime or None."""
    if not GOAL_SHEET_ID:
        return None
    try:
        token = _get_token()
        rows = _sheets_get(token, "設定!A:B")
        for row in rows:
            if len(row) >= 2 and row[0] == "last_activity":
                return datetime.strptime(row[1], "%Y-%m-%d %H:%M:%S").replace(tzinfo=TW_TZ)
        return None
    except Exception:
        return None
