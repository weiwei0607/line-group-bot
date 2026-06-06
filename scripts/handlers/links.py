"""
Link scraper + shared link library for group bot.
"""

import os
import re
import requests
from datetime import datetime, timezone, timedelta

TW_TZ = timezone(timedelta(hours=8))
_SHEET_ID = os.environ.get("GOAL_SHEET_ID", "").strip()
_TAB = "連結庫"

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
        requests.post(
            f"https://sheets.googleapis.com/v4/spreadsheets/{_SHEET_ID}/values/{_TAB}!A1:append?valueInputOption=USER_ENTERED",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"values": [["日期", "時間", "成員", "標題", "URL", "摘要"]]},
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
        print(f"[Links] Append failed: {e}")
        return False


def extract_urls(text: str) -> list[str]:
    pattern = r'https?://[^\s\u3000<>"{}|\\^`\[\]]+'
    found = re.findall(pattern, text)
    return list(dict.fromkeys(found))


def fetch_title(url: str, timeout: int = 8) -> tuple[str, str]:
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        }
        r = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True)
        r.raise_for_status()
        html = r.text
        title = url
        m = re.search(r'<title[^>]*>(.*?)</title>', html, re.IGNORECASE | re.DOTALL)
        if m:
            import html as _html
            title = _html.unescape(m.group(1)).strip().replace("\n", " ").replace("\r", "")
            if len(title) > 200:
                title = title[:200]
        snippet = ""
        md = re.search(r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)', html, re.IGNORECASE)
        if not md:
            md = re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']description["\']', html, re.IGNORECASE)
        if md:
            snippet = md.group(1).strip()
        else:
            mp = re.search(r'<p[^>]*>(.*?)</p>', html, re.IGNORECASE | re.DOTALL)
            if mp:
                import html as _html
                snippet = re.sub(r'<[^>]+>', '', mp.group(1))
                snippet = _html.unescape(snippet).strip().replace("\n", " ")
                snippet = snippet[:200]
        return title, snippet
    except Exception as e:
        print(f"[Links] Fetch failed for {url}: {e}")
        return url, ""


def save_link(member: str, url: str, title: str = "", snippet: str = "") -> bool:
    if not title:
        title, snippet = fetch_title(url)
    return _append_row([_today(), _now(), member, title, url, snippet])


def list_links(limit: int = 10) -> list[dict]:
    rows = _read_all()
    links = []
    for r in reversed(rows):
        if len(r) >= 5:
            links.append({
                "date": r[0] if len(r) > 0 else "",
                "time": r[1] if len(r) > 1 else "",
                "member": r[2] if len(r) > 2 else "",
                "title": r[3] if len(r) > 3 else "",
                "url": r[4] if len(r) > 4 else "",
                "snippet": r[5] if len(r) > 5 else "",
            })
        if len(links) >= limit:
            break
    return links


def search_links(keyword: str) -> list[dict]:
    rows = _read_all()
    kw = keyword.lower()
    results = []
    for r in reversed(rows):
        if len(r) >= 5:
            title = r[3] if len(r) > 3 else ""
            url = r[4] if len(r) > 4 else ""
            snippet = r[5] if len(r) > 5 else ""
            if kw in title.lower() or kw in url.lower() or kw in snippet.lower():
                results.append({
                    "date": r[0] if len(r) > 0 else "",
                    "time": r[1] if len(r) > 1 else "",
                    "member": r[2] if len(r) > 2 else "",
                    "title": title,
                    "url": url,
                    "snippet": snippet,
                })
    return results


# ── webhook handlers ─────────────────────────────

def handle_link_command(reply_token: str, text: str, member: str, reply_fn) -> bool:
    urls = extract_urls(text)
    if urls and not text.startswith(("記事", "筆記", "找記事", "刪除記事", "連結庫", "找連結")):
        saved = []
        for url in urls:
            if save_link(member or "朋友", url):
                saved.append(url)
        if saved:
            reply_fn(reply_token, f"🔗 已儲存 {len(saved)} 個連結到連結庫")
            return True

    if text == "連結庫":
        links = list_links(10)
        if not links:
            reply_fn(reply_token, "🔗 連結庫還沒有內容\n用法：直接貼網址到群組，我會自動存")
            return True
        lines = ["🔗 連結庫（最近10則）："]
        for li in links:
            snippet = f" — {li['snippet'][:20]}" if li.get("snippet") else ""
            lines.append(f"• {li['date']} {li['title']}{snippet}")
        lines.append("\n🔍 找連結 關鍵字")
        reply_fn(reply_token, "\n".join(lines))
        return True

    m = re.match(r"^(?:找連結|搜尋連結)\s+(.+)", text)
    if m:
        kw = m.group(1)
        results = search_links(kw)
        if not results:
            reply_fn(reply_token, f"🔍 找不到「{kw}」相關連結")
            return True
        lines = [f"🔍 「{kw}」搜尋結果："]
        for r in results[:10]:
            lines.append(f"• {r['title']}\n  {r['url']}")
        reply_fn(reply_token, "\n".join(lines))
        return True

    return False
