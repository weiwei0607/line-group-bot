"""
十日目標追蹤 — Google Sheets 存取（含 token 快取）
Sheets 結構:
  Tab「目標」: cycle_id | member | goals | created_at
  Tab「打卡」: timestamp | cycle_id | day | member | content
  Tab「暱稱」: user_id | nickname | zodiac（星座中文）
  Tab「設定」: key | value  （存 last_activity 等全域設定）
"""

import os
import re
import time
import threading
import calendar
import requests
from datetime import datetime, timezone, timedelta

TW_TZ = timezone(timedelta(hours=8))


def _is_valid_line_user_id(uid: str) -> bool:
    """LINE user IDs start with U followed by hex chars (len ≥ 30)."""
    return bool(uid) and isinstance(uid, str) and uid.startswith("U") and len(uid) >= 30 and re.match(r"^U[a-f0-9]+$", uid, re.I) is not None

GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REFRESH_TOKEN = os.environ.get("GOOGLE_REFRESH_TOKEN", "")
GOAL_SHEET_ID = os.environ.get("GOAL_SHEET_ID", "")

# ─── Token cache ──────────────────────────────────────────
_token_cache = {"token": None, "expires_at": 0}
_token_lock = threading.Lock()


def _get_token():
    now = time.time()
    with _token_lock:
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
    with _token_lock:
        _token_cache["token"] = token
        _token_cache["expires_at"] = now + data.get("expires_in", 3600)
    return token


# ─── Nickname cache (10 min TTL, max 100 entries) ─────────
_nickname_cache = {}  # {user_id: (timestamp, nickname_or_None)}
_NICKNAME_TTL = 600
_NICKNAME_MAX = 100

# ─── Nickname rows cache (30 sec TTL) ─────────────────────
_nickname_rows_cache = {}  # {"rows": [...], "ts": timestamp}
_NICKNAME_ROWS_TTL = 30

# ─── Goals cache (in-process, 5 min TTL, max 50 entries) ──
_goals_cache = {}  # {cycle_id: (timestamp, {member: [goals]})}
_GOALS_TTL = 300
_GOALS_MAX = 50


def _trim_cache(cache: dict, max_size: int):
    if len(cache) > max_size:
        # Remove oldest entries by timestamp (assuming tuple format (ts, value))
        sorted_items = sorted(cache.items(), key=lambda x: x[1][0] if isinstance(x[1], tuple) else 0)
        for k, _ in sorted_items[:len(cache) - max_size]:
            del cache[k]


def _now():
    return datetime.now(TW_TZ)


# ─── Retry helper ─────────────────────────────────────────

from shared.retry import retry_http

def _retry_http(fn, max_retries=3, backoff=2):
    return retry_http(max_retries=max_retries, backoff=backoff)(fn)()


# ─── Sheets helpers ───────────────────────────────────────

def _sheets_get(token, range_):
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{GOAL_SHEET_ID}/values/{range_}"
    r = _retry_http(
        lambda: requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=10)
    )
    return r.json().get("values", [])


def _sheets_append(token, range_, values):
    url = (f"https://sheets.googleapis.com/v4/spreadsheets/{GOAL_SHEET_ID}"
           f"/values/{range_}:append?valueInputOption=USER_ENTERED")
    _retry_http(
        lambda: requests.post(
            url,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"values": values},
            timeout=10,
        )
    )


def _sheets_update(token, range_, values):
    url = (f"https://sheets.googleapis.com/v4/spreadsheets/{GOAL_SHEET_ID}"
           f"/values/{range_}?valueInputOption=USER_ENTERED")
    _retry_http(
        lambda: requests.put(
            url,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"values": values},
            timeout=10,
        )
    )


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

def _get_nickname_rows():
    global _nickname_rows_cache
    now = time.time()
    if _nickname_rows_cache and now - _nickname_rows_cache.get("ts", 0) < _NICKNAME_ROWS_TTL:
        return _get_token(), _nickname_rows_cache["rows"]
    token = _get_token()
    rows = _sheets_get(token, "暱稱!A:C")
    _nickname_rows_cache = {"rows": rows, "ts": now}
    return token, rows


def get_nickname(user_id):
    cached = _nickname_cache.get(user_id)
    if cached and time.time() - cached[0] < _NICKNAME_TTL:
        return cached[1]
    if not GOAL_SHEET_ID:
        return None
    try:
        _, rows = _get_nickname_rows()
        for row in rows[1:]:
            if len(row) >= 2 and row[0] == user_id:
                _nickname_cache[user_id] = (time.time(), row[1])
                _trim_cache(_nickname_cache, _NICKNAME_MAX)
                return row[1]
        _nickname_cache[user_id] = (time.time(), None)
        _trim_cache(_nickname_cache, _NICKNAME_MAX)
        return None
    except Exception:
        return None


def get_user_id_by_nickname(nickname: str) -> str | None:
    """Reverse lookup: given nickname, return LINE user_id (if registered)."""
    if not GOAL_SHEET_ID or not nickname:
        return None
    try:
        _, rows = _get_nickname_rows()
        for row in rows[1:]:
            if len(row) >= 2 and row[1] == nickname:
                uid = row[0]
                if _is_valid_line_user_id(uid):
                    return uid.upper()
                logging.warning("get_user_id_by_nickname: invalid user_id %r for nickname %r", uid, nickname)
        return None
    except Exception:
        return None


def set_nickname(user_id, nickname):
    if not GOAL_SHEET_ID:
        return False
    try:
        token, rows = _get_nickname_rows()
        for i, row in enumerate(rows[1:], 2):
            if len(row) >= 1 and row[0] == user_id:
                _sheets_update(token, f"暱稱!B{i}", [[nickname]])
                _nickname_cache[user_id] = (time.time(), nickname)
                _trim_cache(_nickname_cache, _NICKNAME_MAX)
                return True
        _sheets_append(token, "暱稱!A:C", [[user_id, nickname, ""]])
        _nickname_cache[user_id] = (time.time(), nickname)
        _trim_cache(_nickname_cache, _NICKNAME_MAX)
        return True
    except Exception:
        return False


def get_zodiac(user_id) -> str | None:
    if not GOAL_SHEET_ID:
        return None
    try:
        _, rows = _get_nickname_rows()
        for row in rows[1:]:
            if len(row) >= 1 and row[0] == user_id:
                return row[2] if len(row) >= 3 and row[2] else None
        return None
    except Exception:
        return None


def set_zodiac(user_id, zodiac: str) -> bool:
    if not GOAL_SHEET_ID:
        return False
    try:
        token, rows = _get_nickname_rows()
        for i, row in enumerate(rows[1:], 2):
            if len(row) >= 1 and row[0] == user_id:
                _sheets_update(token, f"暱稱!C{i}", [[zodiac]])
                return True
        # 沒有暱稱記錄，新增一行
        _sheets_append(token, "暱稱!A:C", [[user_id, "", zodiac]])
        return True
    except Exception:
        return False


def set_zodiac_by_nickname(nickname: str, zodiac: str) -> bool:
    """Set zodiac by nickname (col B match). Used for admin setup."""
    if not GOAL_SHEET_ID:
        return False
    try:
        token, rows = _get_nickname_rows()
        for i, row in enumerate(rows[1:], 2):
            if len(row) >= 2 and row[1] == nickname:
                _sheets_update(token, f"暱稱!C{i}", [[zodiac]])
                return True
        return False
    except Exception:
        return False


def get_all_zodiacs() -> list[tuple[str, str, str]]:
    """Returns list of (user_id, nickname, zodiac) for members with zodiac set."""
    if not GOAL_SHEET_ID:
        return []
    try:
        _, rows = _get_nickname_rows()
        result = []
        for row in rows[1:]:
            if len(row) >= 3 and row[2]:
                uid = row[0]
                nick = row[1] if len(row) >= 2 else uid
                result.append((uid, nick, row[2]))
        return result
    except Exception:
        return []


def get_all_nicknames() -> list[tuple[str, str]]:
    """Returns list of (user_id, nickname) for all registered members."""
    if not GOAL_SHEET_ID:
        return []
    try:
        _, rows = _get_nickname_rows()
        result = []
        for row in rows[1:]:
            if len(row) >= 2 and row[1]:
                result.append((row[0], row[1]))
        return result
    except Exception:
        return []


# ─── Goals ────────────────────────────────────────────────

def set_goals(member, goals: list, cycle_id: str | None = None) -> bool:
    if not GOAL_SHEET_ID:
        return False
    try:
        token = _get_token()
        if cycle_id is None:
            cycle_id, _, _ = get_cycle_info()
        now_str = _now().strftime("%Y-%m-%d %H:%M")
        goals_str = " / ".join(g.strip() for g in goals if g.strip())

        rows = _sheets_get(token, "目標!A:D")
        for i, row in enumerate(rows[1:], 2):
            if len(row) >= 2 and row[0] == cycle_id and row[1] == member:
                _sheets_update(token, f"目標!C{i}:D{i}", [[goals_str, now_str]])
                _goals_cache.pop(cycle_id, None)
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
        _trim_cache(_goals_cache, _GOALS_MAX)
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


def get_checkin_log(cycle_id=None) -> dict:
    """Returns {member: {day: [content, ...]}} for content-based goal matching."""
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
                member, day, content = row[3], int(row[2]), row[4]
                result.setdefault(member, {}).setdefault(day, []).append(content)
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


# ─── Cycle helpers ───────────────────────────────────────

def get_next_cycle_id() -> str:
    now = _now()
    d, y, m = now.day, now.year, now.month
    if d <= 10:
        return f"{y}-{m:02d}-11"
    elif d <= 20:
        return f"{y}-{m:02d}-21"
    else:
        next_m = m + 1 if m < 12 else 1
        next_y = y if m < 12 else y + 1
        return f"{next_y}-{next_m:02d}-01"


def get_cycle_total(cycle_id) -> int:
    """Returns total days in a cycle given its ID like '2026-05-01'."""
    y, m, start = (int(x) for x in cycle_id.split('-'))
    if start <= 20:
        return 10
    return calendar.monthrange(y, m)[1] - 20


def get_last_cycle_id() -> str:
    """Returns the cycle_id of the cycle immediately before the current one."""
    now = _now()
    d, y, m = now.day, now.year, now.month
    if d <= 10:
        prev_m = m - 1 if m > 1 else 12
        prev_y = y if m > 1 else y - 1
        return f"{prev_y}-{prev_m:02d}-21"
    elif d <= 20:
        return f"{y}-{m:02d}-01"
    else:
        return f"{y}-{m:02d}-11"


# ─── Summary ──────────────────────────────────────────────

def _goal_keyword(goal: str) -> str:
    import re as _re
    key = _re.sub(r'^每天\s*', '', goal).strip()
    key = _re.sub(r'一件東西$|一次$|一下$|一個$', '', key).strip()
    return key or goal


def _goal_days_from_log(member_log: dict, goal: str) -> int:
    kw = _goal_keyword(goal).lower()
    return sum(1 for contents in member_log.values() if any(kw in c.lower() for c in contents))


def build_summary_text(cycle_id=None) -> str:
    if cycle_id is None:
        cycle_id, day, total = get_cycle_info()
    else:
        day, total = 0, get_cycle_total(cycle_id)

    goals = get_goals(cycle_id)
    log = get_checkin_log(cycle_id)
    stats = get_checkin_stats(cycle_id)
    completed = get_completed_goals(cycle_id)
    all_members = sorted(set(list(goals.keys()) + list(stats.keys())))

    lines = [f"📊 十日目標週期總結（{cycle_id}）\n"]
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
                    cnt = _goal_days_from_log(member_log, g)
                    bar = "🟩" * cnt + "⬜" * max(0, total - cnt)
                    emoji = "🏆" if cnt >= total * 0.8 else ("✅" if cnt >= total * 0.5 else "⚠️")
                    lines.append(f"  {emoji} {kw}｜{bar} {cnt}/{total}")
        else:
            checked = len(stats.get(member, []))
            bar = "".join("🟩" if d in set(stats.get(member, [])) else "⬜" for d in range(1, total + 1))
            lines.append(f"  打卡｜{bar} {checked}/{total}")
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
    except Exception as _exc:
        import logging; logging.getLogger(__name__).warning("Silent error: %s", _exc)


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
    """Returns last N days of group summaries as [(date, content), ...]"""
    if not GOAL_SHEET_ID:
        return []
    try:
        token = _get_token()
        rows = _sheets_get(token, "記憶!A:B")
        data = [(r[0], r[1]) for r in rows[1:] if len(r) >= 2]
        return data[-days:]
    except Exception:
        return []


# ─── Personal memory ──────────────────────────────────────

def add_personal_memory(member, content) -> bool:
    """Store a personal note in 個人記憶 tab (date | member | content)."""
    if not GOAL_SHEET_ID:
        return False
    try:
        token = _get_token()
        date_str = _now().strftime("%Y-%m-%d")
        rows = _sheets_get(token, "個人記憶!A:C")
        for i, row in enumerate(rows[1:], 2):
            if len(row) >= 2 and row[0] == date_str and row[1] == member:
                _sheets_update(token, f"個人記憶!C{i}", [[content]])
                return True
        _sheets_append(token, "個人記憶!A:C", [[date_str, member, content]])
        return True
    except Exception:
        return False


def get_personal_memories(member, days=14) -> list:
    """Returns last N days of personal notes for a member as [(date, content), ...]"""
    if not GOAL_SHEET_ID:
        return []
    try:
        token = _get_token()
        rows = _sheets_get(token, "個人記憶!A:C")
        data = [(r[0], r[2]) for r in rows[1:]
                if len(r) >= 3 and r[1] == member]
        return data[-days:]
    except Exception:
        return []


# ─── Todo / Reminder ─────────────────────────────────────

def add_todo(member: str, date_str: str, content: str, created_by: str) -> bool:
    if not GOAL_SHEET_ID:
        return False
    try:
        token = _get_token()
        now_str = _now().strftime("%Y-%m-%d %H:%M")
        _sheets_append(token, "待辦!A:F", [[now_str, date_str, member, content, "待辦", created_by]])
        return True
    except Exception:
        return False


def get_todos(member: str | None = None, status: str | None = "待辦") -> list[dict]:
    if not GOAL_SHEET_ID:
        return []
    try:
        token = _get_token()
        rows = _sheets_get(token, "待辦!A:F")
        result = []
        for i, row in enumerate(rows[1:], 2):
            if len(row) < 5:
                continue
            todo = {
                "row": i,
                "date": row[1] if len(row) > 1 else "",
                "member": row[2] if len(row) > 2 else "",
                "content": row[3] if len(row) > 3 else "",
                "status": row[4] if len(row) > 4 else "待辦",
                "created_by": row[5] if len(row) > 5 else "",
            }
            if member and todo["member"] != member:
                continue
            if status and todo["status"] != status:
                continue
            result.append(todo)
        return result
    except Exception:
        return []


def complete_todo_by_content(member: str, content: str) -> dict | None:
    """Mark matching pending todo as done. Returns todo dict or None."""
    if not GOAL_SHEET_ID:
        return None
    try:
        token = _get_token()
        todos = get_todos(member=member, status="待辦")
        matched = next(
            (t for t in todos if content in t["content"] or t["content"] in content),
            None,
        )
        if not matched:
            return None
        _sheets_update(token, f"待辦!E{matched['row']}", [["已完成"]])
        return matched
    except Exception:
        return None


def get_todos_by_date(date_str: str) -> list[dict]:
    """Get all pending todos due on a specific date."""
    return [t for t in get_todos(status="待辦") if t["date"] == date_str]


def get_overdue_todos() -> list[dict]:
    """Get todos past their due date that are still pending."""
    today = _now().strftime("%Y-%m-%d")
    return [t for t in get_todos(status="待辦") if t["date"] < today]


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

def get_setting(key: str, default=None):
    """Read a key from the 設定 tab."""
    if not GOAL_SHEET_ID:
        return default
    try:
        token = _get_token()
        rows = _sheets_get(token, "設定!A:B")
        for row in rows:
            if len(row) >= 2 and row[0] == key:
                return row[1]
    except Exception:
        pass
    return default


def set_setting(key: str, value: str) -> bool:
    """Write a key-value pair to the 設定 tab."""
    if not GOAL_SHEET_ID:
        return False
    try:
        token = _get_token()
        rows = _sheets_get(token, "設定!A:B")
        for i, row in enumerate(rows, 1):
            if len(row) >= 1 and row[0] == key:
                _sheets_update(token, f"設定!B{i}", [[value]])
                return True
        _sheets_append(token, "設定!A:B", [[key, value]])
        return True
    except Exception as _exc:
        import logging; logging.getLogger(__name__).warning("set_setting error: %s", _exc)
    return False


def update_last_activity():
    """Store current timestamp as last group activity."""
    set_setting("last_activity", _now().strftime("%Y-%m-%d %H:%M:%S"))


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


# ─── Quiz scores ──────────────────────────────────────────

def _week_start() -> str:
    """Returns Monday's date string of the current week."""
    now = _now()
    monday = now - timedelta(days=now.weekday())
    return monday.strftime("%Y-%m-%d")


def add_quiz_score(nickname: str) -> int:
    """Record a correct quiz answer. Returns new weekly total for this user."""
    if not GOAL_SHEET_ID:
        return 0
    try:
        token = _get_token()
        week = _week_start()
        rows = _sheets_get(token, "積分!A:C")
        for i, row in enumerate(rows[1:], 2):
            if len(row) >= 2 and row[0] == week and row[1] == nickname:
                new_score = int(row[2]) + 1 if len(row) >= 3 else 1
                _sheets_update(token, f"積分!C{i}", [[new_score]])
                return new_score
        _sheets_append(token, "積分!A:C", [[week, nickname, 1]])
        return 1
    except Exception:
        return 0


def get_quiz_scores(week: str = None) -> dict:
    """Returns {nickname: score} for the given week (defaults to current week)."""
    if not GOAL_SHEET_ID:
        return {}
    try:
        token = _get_token()
        week = week or _week_start()
        rows = _sheets_get(token, "積分!A:C")
        return {
            row[1]: int(row[2])
            for row in rows[1:]
            if len(row) >= 3 and row[0] == week
        }
    except Exception:
        return {}


# ─── Weekly chat summary ──────────────────────────────────

def get_week_chat_logs() -> list:
    """Returns last 7 days of chat logs as [(member, message), ...]"""
    if not GOAL_SHEET_ID:
        return []
    try:
        token = _get_token()
        rows = _sheets_get(token, "聊天記錄!A:C")
        cutoff = (_now() - timedelta(days=7)).strftime("%Y-%m-%d")
        result = [
            (row[1], row[2])
            for row in rows[1:]
            if len(row) >= 3 and row[0] >= cutoff
        ]
        return result[-300:]
    except Exception:
        return []


# ─── Birthdays ────────────────────────────────────────────

def set_birthday(user_id: str, birthday: str) -> bool:
    """Store birthday (MM-DD) in col D of 暱稱 tab."""
    if not GOAL_SHEET_ID:
        return False
    try:
        token, rows = _get_nickname_rows()
        for i, row in enumerate(rows[1:], 2):
            if len(row) >= 1 and row[0] == user_id:
                _sheets_update(token, f"暱稱!D{i}", [[birthday]])
                return True
        _sheets_append(token, "暱稱!A:D", [[user_id, "", "", birthday]])
        return True
    except Exception:
        return False


def set_birthday_by_nickname(nickname: str, birthday: str) -> bool:
    """Set birthday by nickname."""
    if not GOAL_SHEET_ID:
        return False
    try:
        token, rows = _get_nickname_rows()
        for i, row in enumerate(rows[1:], 2):
            if len(row) >= 2 and row[1] == nickname:
                _sheets_update(token, f"暱稱!D{i}", [[birthday]])
                return True
        return False
    except Exception:
        return False


def get_today_birthdays() -> list[str]:
    """Returns list of nicknames whose birthday is today (MM-DD)."""
    if not GOAL_SHEET_ID:
        return []
    try:
        token, rows = _get_nickname_rows()
        today = _now().strftime("%m-%d")
        return [
            row[1]
            for row in rows[1:]
            if len(row) >= 4 and row[3] == today and row[1]
        ]
    except Exception:
        return []


# ─── Goal completion (one-time goals) ─────────────────────

def complete_goal(member: str, goal: str, cycle_id: str | None = None) -> bool:
    """Mark a specific goal as completed for the cycle."""
    if not GOAL_SHEET_ID:
        return False
    if cycle_id is None:
        cycle_id, _, _ = get_cycle_info()
    try:
        token = _get_token()
        now_str = _now().strftime("%Y-%m-%d %H:%M")
        _sheets_append(token, "完成!A:D", [[cycle_id, member, goal, now_str]])
        return True
    except Exception:
        return False


def get_completed_goals(cycle_id: str | None = None) -> dict[str, set[str]]:
    """Returns {member: {goal1, goal2, ...}} for completed goals."""
    if not GOAL_SHEET_ID:
        return {}
    if cycle_id is None:
        cycle_id, _, _ = get_cycle_info()
    try:
        token = _get_token()
        rows = _sheets_get(token, "完成!A:D")
        result: dict[str, set[str]] = {}
        for row in rows[1:]:
            if len(row) >= 3 and row[0] == cycle_id:
                member, goal = row[1], row[2]
                result.setdefault(member, set()).add(goal)
        return result
    except Exception:
        return {}


def is_goal_completed(member: str, goal: str, completed: dict[str, set[str]] | None = None) -> bool:
    """Check if a goal (or its keyword variant) is marked completed."""
    if completed is None:
        completed = get_completed_goals()
    member_completed = completed.get(member, set())
    # Exact match
    if goal in member_completed:
        return True
    # Keyword fuzzy match
    kw = _goal_keyword(goal).lower()
    for c in member_completed:
        if kw in c.lower() or c.lower() in kw:
            return True
    return False
