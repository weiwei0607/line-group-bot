"""
Shared notebook for group bot — uses Google Sheets (OAuth2, same as goal_tracker).
"""

import os
import re
import requests
from datetime import datetime, timezone, timedelta

TW_TZ = timezone(timedelta(hours=8))
_SHEET_ID = os.environ.get("GOAL_SHEET_ID", "").strip()
_TAB = "記事本"

_token_cache = {"token": None, "expires_at": 0}


def _get_token():
    import time
    now = time.time()
    if _token_cache["token"] and _token_cache["expires_at"] > now + 60:
        return _token_cache["token"]
    cid = os.environ.get("GOOGLE_CLIENT_ID", "")
    csec = os.environ.get("GOOGLE_CLIENT_SECRET", "")
    rt = os.environ.get("GOOGLE_REFRESH_TOKEN", "")
    if not all([cid, csec, rt]):
        return None
    r = requests.post("https://oauth2.googleapis.com/token", data={
        "client_id": cid, "client_secret": csec,
        "refresh_token": rt, "grant_type": "refresh_token",
    }, timeout=10)
    data = r.json()
    if not r.ok or not data.get("access_token"):
        return None
    _token_cache["token"] = data["access_token"]
    _token_cache["expires_at"] = now + data.get("expires_in", 3600)
    return _token_cache["token"]


def _today():
    return datetime.now(TW_TZ).strftime("%Y-%m-%d")


def _now():
    return datetime.now(TW_TZ).strftime("%H:%M")


def _ensure_tab():
    token = _get_token()
    if not token or not _SHEET_ID:
        return
    try:
        meta = requests.get(
            f"https://sheets.googleapis.com/v4/spreadsheets/{_SHEET_ID}",
            headers={"Authorization": f"Bearer {token}"}, timeout=10,
        ).json()
        titles = [s["properties"]["title"] for s in meta.get("sheets", [])]
        if _TAB in titles:
            return
        requests.post(
            f"https://sheets.googleapis.com/v4/spreadsheets/{_SHEET_ID}:batchUpdate",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"requests": [{"addSheet": {"properties": {"title": _TAB}}}]},
            timeout=10,
        )
        # Add header
        requests.post(
            f"https://sheets.googleapis.com/v4/spreadsheets/{_SHEET_ID}/values/{_TAB}!A1:append?valueInputOption=USER_ENTERED",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"values": [["日期", "時間", "成員", "標題", "內容", "標籤"]]},
            timeout=10,
        )
    except Exception:
        pass


def _read_all():
    token = _get_token()
    if not token or not _SHEET_ID:
        return []
    _ensure_tab()
    try:
        r = requests.get(
            f"https://sheets.googleapis.com/v4/spreadsheets/{_SHEET_ID}/values/{_TAB}!A2:F1000",
            headers={"Authorization": f"Bearer {token}"}, timeout=10,
        )
        return r.json().get("values", [])
    except Exception:
        return []


def _append_row(row):
    token = _get_token()
    if not token or not _SHEET_ID:
        return False
    _ensure_tab()
    try:
        requests.post(
            f"https://sheets.googleapis.com/v4/spreadsheets/{_SHEET_ID}/values/{_TAB}!A1:append?valueInputOption=USER_ENTERED",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"values": [row]}, timeout=10,
        )
        return True
    except Exception as e:
        print(f"[Notebook] Append failed: {e}")
        return False


def add_note(member: str, title: str, content: str, tags: str = "") -> bool:
    return _append_row([_today(), _now(), member, title, content, tags])


def list_notes(limit: int = 10) -> list[dict]:
    rows = _read_all()
    notes = []
    for r in reversed(rows):
        if len(r) >= 4:
            notes.append({
                "date": r[0] if len(r) > 0 else "",
                "time": r[1] if len(r) > 1 else "",
                "member": r[2] if len(r) > 2 else "",
                "title": r[3] if len(r) > 3 else "",
                "content": r[4] if len(r) > 4 else "",
                "tags": r[5] if len(r) > 5 else "",
            })
        if len(notes) >= limit:
            break
    return notes


def search_notes(keyword: str) -> list[dict]:
    rows = _read_all()
    kw = keyword.lower()
    results = []
    for r in reversed(rows):
        if len(r) >= 4:
            title = r[3] if len(r) > 3 else ""
            content = r[4] if len(r) > 4 else ""
            if kw in title.lower() or kw in content.lower():
                results.append({
                    "date": r[0] if len(r) > 0 else "",
                    "time": r[1] if len(r) > 1 else "",
                    "member": r[2] if len(r) > 2 else "",
                    "title": title,
                    "content": content,
                    "tags": r[5] if len(r) > 5 else "",
                })
    return results


def delete_note(title: str) -> bool:
    token = _get_token()
    if not token or not _SHEET_ID:
        return False
    rows = _read_all()
    for i, r in enumerate(rows, start=2):
        if len(r) > 3 and r[3] == title:
            try:
                meta = requests.get(
                    f"https://sheets.googleapis.com/v4/spreadsheets/{_SHEET_ID}",
                    headers={"Authorization": f"Bearer {token}"}, timeout=10,
                ).json()
                sheet_id = None
                for s in meta.get("sheets", []):
                    if s["properties"]["title"] == _TAB:
                        sheet_id = s["properties"]["sheetId"]
                        break
                if sheet_id is None:
                    return False
                requests.post(
                    f"https://sheets.googleapis.com/v4/spreadsheets/{_SHEET_ID}:batchUpdate",
                    headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                    json={"requests": [{"deleteDimension": {
                        "range": {"sheetId": sheet_id, "dimension": "ROWS", "startIndex": i - 1, "endIndex": i}
                    }}]}, timeout=10,
                )
                return True
            except Exception as e:
                print(f"[Notebook] Delete failed: {e}")
                return False
    return False


# ── webhook handlers ─────────────────────────────

def handle_notebook_command(reply_token: str, text: str, member: str, reply_fn) -> bool:
    if text == "記事本":
        notes = list_notes(10)
        if not notes:
            reply_fn(reply_token, "📝 記事本還沒有內容\n用法：記事 標題 內容")
            return True
        lines = ["📝 記事本（最近10則）："]
        for n in notes:
            tags = f" [{n['tags']}]" if n.get("tags") else ""
            lines.append(f"• {n['date']} {n['title']}{tags}")
        lines.append("\n🔍 找記事 關鍵字\n🗑️ 刪除記事 標題")
        reply_fn(reply_token, "\n".join(lines))
        return True

    m = re.match(r"^(?:記事|筆記)\s+(.+)", text)
    if m:
        rest = m.group(1)
        parts = rest.split(None, 1)
        if len(parts) < 2:
            reply_fn(reply_token, "用法：記事 標題 內容")
            return True
        title, content = parts[0], parts[1]
        ok = add_note(member or "朋友", title, content)
        reply_fn(reply_token, f"✅ 已新增記事「{title}」" if ok else "❌ 新增失敗")
        return True

    m = re.match(r"^(?:找記事|搜尋記事|記事搜尋)\s+(.+)", text)
    if m:
        kw = m.group(1)
        results = search_notes(kw)
        if not results:
            reply_fn(reply_token, f"🔍 找不到「{kw}」相關記事")
            return True
        lines = [f"🔍 「{kw}」搜尋結果："]
        for r in results[:10]:
            lines.append(f"• {r['date']} {r['title']} — {r['content'][:30]}...")
        reply_fn(reply_token, "\n".join(lines))
        return True

    m = re.match(r"^(?:刪除記事)\s+(.+)", text)
    if m:
        title = m.group(1)
        ok = delete_note(title)
        reply_fn(reply_token, f"🗑️ 已刪除「{title}」" if ok else f"❌ 找不到「{title}」")
        return True

    return False
