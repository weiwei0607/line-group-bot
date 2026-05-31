"""
一次性腳本：在 Google Sheets 建立缺少的 tab
執行：python3 create_sheet_tab.py
"""

import os
import requests
from goal_tracker import _get_token, GOAL_SHEET_ID

TABS_NEEDED = ["目標", "打卡", "暱稱", "設定", "記憶", "聊天記錄", "個人記憶"]


def get_existing_tabs(token):
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{GOAL_SHEET_ID}"
    r = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=10)
    sheets = r.json().get("sheets", [])
    return {s["properties"]["title"] for s in sheets}


def create_tab(token, title):
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{GOAL_SHEET_ID}:batchUpdate"
    r = requests.post(
        url,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"requests": [{"addSheet": {"properties": {"title": title}}}]},
        timeout=10,
    )
    return r.status_code == 200


if __name__ == "__main__":
    token = _get_token()
    existing = get_existing_tabs(token)
    print(f"已存在的 tabs：{existing}")

    for tab in TABS_NEEDED:
        if tab not in existing:
            ok = create_tab(token, tab)
            print(f"建立 [{tab}]：{'✅' if ok else '❌'}")
        else:
            print(f"[{tab}] 已存在，跳過")
