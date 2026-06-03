#!/usr/bin/env python3
"""
檢查 Google Sheet「暱稱」tab 的 A 列是否都是有效的 LINE userId。
用法：
    cd scripts && python3 check_nickname_sheet.py
需要環境變數：GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_REFRESH_TOKEN, GOAL_SHEET_ID
"""
import os
import sys

# 自動載入 .env（如果有的話）
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

sys.path.insert(0, os.path.dirname(__file__))
from goal_tracker import _get_nickname_rows, _is_valid_line_user_id


def main():
    try:
        _, rows = _get_nickname_rows()
    except Exception as exc:
        print(f"❌ 讀取失敗：{exc}")
        sys.exit(1)

    if not rows:
        print("⚠️ 暱稱表完全空白")
        sys.exit(0)

    header = rows[0]
    print(f"表頭：{header}")
    print("-" * 70)

    invalid = []
    for i, row in enumerate(rows[1:], start=2):
        uid = row[0] if len(row) > 0 else ""
        nick = row[1] if len(row) > 1 else ""
        zodiac = row[2] if len(row) > 2 else ""

        if _is_valid_line_user_id(uid):
            status = "✅"
        else:
            status = "❌"
            invalid.append((i, uid, nick))

        print(f"{status} 第 {i:2} 行 | A={uid!r:35} | B={nick!r:10} | C={zodiac!r}")

    print("-" * 70)
    if invalid:
        print(f"\n⚠️ 發現 {len(invalid)} 筆無效的 userId：")
        for row_num, uid, nick in invalid:
            print(f"   第 {row_num} 行：A 列 = {uid!r}（暱稱 = {nick!r}）")
        print("\n請把這些行的 A 列改成對應成員的 LINE userId（U 開頭，32~33 位 hex）")
        sys.exit(1)
    else:
        print("\n🎉 所有 A 列都是有效的 LINE userId")
        sys.exit(0)


if __name__ == "__main__":
    main()
