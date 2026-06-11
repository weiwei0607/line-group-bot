"""診斷打卡問題：檢查 member_label、目標、打卡流程"""
import os
import sys
import requests
from datetime import datetime, timezone, timedelta

required = ["GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REFRESH_TOKEN", "GOAL_SHEET_ID"]
missing = [k for k in required if not os.environ.get(k)]
if missing:
    print(f"❌ 缺少環境變數: {', '.join(missing)}")
    sys.exit(1)

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

print(f"📅 目前週期: {current_cycle}")
print(f"📅 今天: {now.strftime('%Y-%m-%d %H:%M')}\n")

# 讀取暱稱
print("👤 『暱稱』工作表：")
url = f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values/暱稱!A:C?access_token={token}"
rows = requests.get(url, timeout=10).json().get("values", [])
if len(rows) <= 1:
    print("   (空的)")
else:
    for r in rows[1:]:
        print(f"   {r[0][:20]}... | {r[1] if len(r) > 1 else '(無)'} | {r[2] if len(r) > 2 else '(無)'}")

# 讀取目標
print(f"\n🎯 『目標』工作表（週期 {current_cycle}）：")
url = f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values/目標!A:D?access_token={token}"
rows = requests.get(url, timeout=10).json().get("values", [])
current_goals = [r for r in rows[1:] if len(r) >= 1 and r[0] == current_cycle]
if not current_goals:
    print("   ⚠️ 本週期沒有任何目標！")
else:
    for r in current_goals:
        print(f"   {r[1]}: {r[2]}")

# 讀取打卡
print(f"\n📝 『打卡』工作表（週期 {current_cycle}）：")
url = f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values/打卡!A:E?access_token={token}"
rows = requests.get(url, timeout=10).json().get("values", [])
current_checkins = [r for r in rows[1:] if len(r) >= 2 and r[1] == current_cycle]
if not current_checkins:
    print("   本週期還沒有打卡記錄")
else:
    for r in current_checkins[-5:]:
        print(f"   {r[0]} | {r[3]}: {r[4]}")

print("\n💡 常見問題：")
print("   如果『目標』表有資料，但打卡時 bot 說『你還沒設目標』，")
print("   通常是因為『暱稱』改變了，導致打卡時的 member 名稱和設目標時不一致。")
