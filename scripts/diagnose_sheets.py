"""診斷 Google Sheets 連線與打卡功能"""
import os
import sys
import requests

# 檢查環境變數
required = ["GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REFRESH_TOKEN", "GOAL_SHEET_ID"]
missing = [k for k in required if not os.environ.get(k)]
if missing:
    print(f"❌ 缺少環境變數: {', '.join(missing)}")
    sys.exit(1)

CLIENT_ID = os.environ["GOOGLE_CLIENT_ID"]
CLIENT_SECRET = os.environ["GOOGLE_CLIENT_SECRET"]
REFRESH_TOKEN = os.environ["GOOGLE_REFRESH_TOKEN"]
SHEET_ID = os.environ["GOAL_SHEET_ID"]

print(f"✅ 環境變數齊全")
print(f"   GOAL_SHEET_ID: {SHEET_ID[:10]}...{SHEET_ID[-10:]}")

# 1. 測試 OAuth token 取得
print("\n🔑 測試 Google OAuth...")
r = requests.post("https://oauth2.googleapis.com/token", data={
    "client_id": CLIENT_ID,
    "client_secret": CLIENT_SECRET,
    "refresh_token": REFRESH_TOKEN,
    "grant_type": "refresh_token",
}, timeout=10)

if not r.ok:
    print(f"❌ OAuth 失敗: HTTP {r.status_code}")
    print(f"   回應: {r.text}")
    print("\n💡 解決方式：需要重新取得 GOOGLE_REFRESH_TOKEN")
    print("   請執行專案中的 Google OAuth 授權流程，或檢查 Google 帳號是否變更過密碼/安全設定。")
    sys.exit(1)

data = r.json()
token = data.get("access_token")
if not token:
    print(f"❌ OAuth 回傳沒有 access_token: {data}")
    sys.exit(1)

print(f"✅ OAuth 成功，access_token 有效")

# 2. 測試 Sheets API 連線
print("\n📊 測試 Sheets API...")
url = f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}?access_token={token}"
r = requests.get(url, timeout=10)
if not r.ok:
    print(f"❌ 無法讀取試算表: HTTP {r.status_code}")
    print(f"   回應: {r.text}")
    print("\n💡 可能原因：")
    print("   - GOAL_SHEET_ID 錯誤")
    print("   - 試算表被刪除")
    print("   - Google 服務帳號沒有試算表權限")
    sys.exit(1)

sheet_info = r.json()
print(f"✅ 試算表可讀取: '{sheet_info.get('properties', {}).get('title')}'")

# 3. 檢查必要的 tabs
print("\n📋 檢查必要工作表...")
tabs = [s["properties"]["title"] for s in sheet_info.get("sheets", [])]
required_tabs = ["打卡", "目標", "暱稱"]
for t in required_tabs:
    status = "✅" if t in tabs else "❌"
    print(f"   {status} {t}")

if "打卡" not in tabs:
    print("\n💡 缺少『打卡』工作表！請在試算表中手動建立，或檢查 tab 名稱是否被改過。")
    sys.exit(1)

# 4. 測試讀取打卡資料
print("\n📝 測試讀取打卡資料...")
url = f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values/打卡!A:E?access_token={token}"
r = requests.get(url, timeout=10)
if not r.ok:
    print(f"❌ 無法讀取打卡資料: HTTP {r.status_code}")
    print(f"   {r.text}")
else:
    rows = r.json().get("values", [])
    print(f"✅ 打卡表共有 {len(rows)} 行資料（含標題）")
    if len(rows) > 1:
        print(f"   最新幾筆：")
        for row in rows[-3:]:
            print(f"     {row}")
    else:
        print("   目前只有標題行，還沒有打卡記錄")

# 5. 測試寫入打卡（寫入後立刻刪除）
print("\n✏️  測試寫入打卡...")
from datetime import datetime, timezone, timedelta
now = datetime.now(timezone(timedelta(hours=8)))
date_str = now.strftime("%Y-%m-%d %H:%M")
cycle_id = f"{now.year}-{now.month:02d}-01"

append_url = f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values/打卡!A:E:append?valueInputOption=USER_ENTERED&access_token={token}"
r = requests.post(
    append_url,
    headers={"Content-Type": "application/json"},
    json={"values": [[date_str, cycle_id, 99, "🤖診斷測試", "測試打卡內容"]]},
    timeout=10,
)
if not r.ok:
    print(f"❌ 寫入失敗: HTTP {r.status_code}")
    print(f"   {r.text}")
    print("\n💡 可能原因：試算表權限為『僅檢視』，或 Google API 配額用完。")
else:
    print("✅ 寫入成功！")
    # 嘗試刪除剛寫入的測試行（透過清空內容）
    # 先找出剛寫入的行號
    r = requests.get(url, timeout=10)
    rows = r.json().get("values", [])
    for i, row in enumerate(rows, 1):
        if len(row) >= 4 and row[3] == "🤖診斷測試":
            clear_url = f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values/打卡!A{i}:E{i}:clear?access_token={token}"
            requests.post(clear_url, headers={"Content-Type": "application/json"}, json={}, timeout=10)
            print(f"   已清理測試資料（第 {i} 行）")
            break

print("\n🎉 診斷完成！如果以上全部顯示 ✅，那 Google Sheets 連線是正常的。")
print("   若群組打卡仍失敗，請檢查 Render 的 log 看是否有其他錯誤訊息。")
