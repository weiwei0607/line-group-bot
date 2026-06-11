"""檢查 Google Sheets 目標與打卡資料"""
import os
import sys
import requests
from datetime import datetime, timezone, timedelta

required = ["GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REFRESH_TOKEN", "GOAL_SHEET_ID"]
missing = [k for k in required if not os.environ.get(k)]
if missing:
    print(f"❌ 缺少環境變數: {', '.join(missing)}")
    sys.exit(1)

# 取得 access token
r = requests.post("https://oauth2.googleapis.com/token", data={
    "client_id": os.environ["GOOGLE_CLIENT_ID"],
    "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
    "refresh_token": os.environ["GOOGLE_REFRESH_TOKEN"],
    "grant_type": "refresh_token",
}, timeout=10)
token = r.json()["access_token"]
SHEET_ID = os.environ["GOAL_SHEET_ID"]

TW_TZ = timezone(timedelta(hours=8))
now = datetime.now(TW_TZ)
d = now.day
y, m = now.year, now.month
if d <= 10:
    start = 1
elif d <= 20:
    start = 11
else:
    start = 21
current_cycle = f"{y}-{m:02d}-{start:02d}"

print(f"📅 目前週期: {current_cycle} (今天 {now.strftime('%Y-%m-%d')})")

# 讀取目標
print("\n🎯 『目標』工作表內容：")
url = f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values/目標!A:D?access_token={token}"
rows = requests.get(url, timeout=10).json().get("values", [])
if len(rows) <= 1:
    print("   (空的！沒有任何目標記錄)")
else:
    print(f"   共 {len(rows)-1} 筆記錄：")
    current_goals = [r for r in rows[1:] if len(r) >= 1 and r[0] == current_cycle]
    if current_goals:
        print(f"   ✅ 本週期 ({current_cycle}) 有 {len(current_goals)} 人設目標：")
        for r in current_goals:
            print(f"      - {r[1]}: {r[2]}")
    else:
        print(f"   ⚠️ 本週期 ({current_cycle}) 沒有人設目標！")
    print(f"\n   所有歷史記錄：")
    for r in rows[1:]:
        print(f"      {r[0]} | {r[1]} | {r[2]}")

# 讀取打卡
print("\n📝 『打卡』工作表內容：")
url = f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values/打卡!A:E?access_token={token}"
rows = requests.get(url, timeout=10).json().get("values", [])
if len(rows) <= 1:
    print("   (空的！沒有任何打卡記錄)")
else:
    current_checkins = [r for r in rows[1:] if len(r) >= 2 and r[1] == current_cycle]
    print(f"   共 {len(rows)-1} 筆記錄，本週期有 {len(current_checkins)} 筆")
    if current_checkins:
        print("   本週期打卡：")
        for r in current_checkins[-5:]:
            print(f"      {r[0]} | {r[3]}: {r[4]}")

print("\n💡 結論：")
if current_goals:
    print("   目標有設定，應該可以正常打卡。")
else:
    print("   『目標』工作表是空的或本週期沒目標！")
    print("   請在群組重新輸入：設目標：目標1 / 目標2")
    print("   設好目標後就能打卡了 ✅")
