# 👥 LINE 朋友群機器人「小棉襖」

![Bot Demo Placeholder](https://via.placeholder.com/800x400/020617/f5f5f4.png?text=LINE+Bot+Demo+Placeholder)

> 一個功能豐富的 LINE 群組機器人，整合 20+ 外部 API、Gemini AI、SQLite 狀態管理與語音合成，部署於 Render。從日文學習、星座運勢、天氣匯率到 AI 語音，涵蓋群組娛樂與生活助理的完整場景。

---

## ✨ 功能矩陣 (Commands Matrix)

| 類別 | 指令範例 | 說明 |
| --- | --- | --- |
| 🎯 **目標打卡** | `設目標：讀書`、`打卡` | 記錄與追蹤週期性目標進度 |
| 📌 **待辦提醒** | `提醒我 明天 買菜` | 自然語言定時提醒與待辦管理 |
| 🎲 **趣味互動** | `抽籤 A / B`、`今日運勢` | 決策工具、抽籤與星座運勢推送 |
| 🤖 **AI 助理** | `@小棉襖 [問題]`、`翻 日文` | Gemini 智能問答、多語言翻譯與改寫 |
| 🎵 **媒體娛樂** | `找歌 [歌名]`、`新聞` | 搜尋 YouTube 影片、音樂與即時新聞 |
| 🌐 **實用工具** | `台北天氣`、`匯率` | 即時天氣預報、匯率換算與計算機 |
| 🔊 **AI 語音** | `說 [文字]` | AI 語音朗讀 (TTS) |
| 🇯🇵 **日文學習** | `查日文 [單字]` | N5 單字推播、漢字解析與翻譯 |

---

## ✨ 詳細功能一覽（50+ 指令）

### 🎯 十日目標 & 打卡
| 指令 | 說明 |
|------|------|
| `設目標：目標1 / 目標2` | 建立週期性目標 |
| `打卡 今天做了XXX` | 記錄目標進度 |
| `完成 目標名稱` | 一次性完成目標 |
| `查目標 / 進度 / 今日打卡` | 查看目前狀態 |
| `幫我想目標` | AI 推薦目標 |

### 📌 待辦提醒
| 指令 | 說明 |
|------|------|
| `提醒我 明天 要做XXX` | 定時提醒（支援自然語言日期） |
| `待辦 / 完成待辦 XXX` | 管理待辦事項 |

### 🎲 趣味互動
| 指令 | 說明 |
|------|------|
| `今日運勢 / 今日天蠍` | 星座運勢（綁定後自動推送） |
| `誰請客 / 抽籤 A / B` | 群組決策工具 |
| `配對 A B / 配對星座 天蠍 金牛` | 人際/星座配對 |
| `搖骰子 / 猜拳 剪刀` | 小遊戲 |
| `貓貓 / 狗狗 / 狐狸 / 柴柴` | 隨機可愛圖片 |

### 🎵 媒體 & 娛樂
| 指令 | 說明 |
|------|------|
| `找歌 [歌名]` | 歌曲搜尋 |
| `找影片 [關鍵字]` | YouTube 搜尋 |
| `查電影 [片名]` | 電影資訊與評分 |
| `電影台詞 / 動漫語錄` | 隨機名言 |
| `新聞` | 今日頭條摘要 |

### 🌐 實用工具
| 指令 | 說明 |
|------|------|
| `天氣 [城市]` | 台灣與國際城市天氣（含未來預報） |
| `[日期]天氣` | 支援「明天/後天/星期三/下週五」 |
| `匯率` | 台幣對主要貨幣即時匯率 |
| `QR [網址]` | 產生 QR Code |
| `縮網址 [URL]` | 縮短網址 |
| `倒數 6/15` | 計算距離目標日期還有幾天 |
| `BMI 165 55` | 計算 BMI |
| `熱量 [食物]` | 查詢食物熱量 |

### 🔊 AI 語音
| 指令 | 說明 |
|------|------|
| `說 [文字]` | AI 語音朗讀（zh-TW-HsiaoChenNeural） |
| `念 [文字] / 唸 [文字] / 讀 [文字]` | 同上 |

### 🤖 AI 整合
| 指令 | 說明 |
|------|------|
| `@小棉襖 [問題]` | Gemini 2.5 Flash 智能問答 |
| `翻 日文 [文字]` | 多語言翻譯（日/英/韓/法/西/德/泰/越） |
| `摘要 [長文]` | 長文摘要 |
| `改寫 [文字]` | 三種風格改寫（嗆辣/撒嬌/正經） |

### 🇯🇵 日文學習
| 指令 | 說明 |
|------|------|
| `XXX日文怎麼說` | 中日翻譯 |
| `查日文 [單字]` | 單字查詢 |
| `今日日文單字` | 每日自動推送 N5 單字 |
| `漢字 [字]` | 漢字解析 |

---

## 🏗️ 技術架構

```
使用者訊息
    ↓
LINE Webhook → Render (Flask + Gunicorn, workers=1)
    ↓
┌─────────────┬─────────────┬─────────────┬─────────────┐
│ 指令分發器   │   AI 模組    │  外部 API   │   狀態管理  │
│ commands.py  │  api_helpers │ weather.py  │   state.py  │
│              │  gemini.py   │  tts_store  │             │
└─────────────┴─────────────┴─────────────┴─────────────┘
    ↓
SQLite (bot_state.db + tts_store.db)
```

| 層級 | 技術 |
|------|------|
| **後端** | Python 3.11, Flask, Gunicorn |
| **LINE SDK** | line-bot-sdk v3 (Messaging API) |
| **AI** | Gemini 2.5 Flash + RapidAPI 輪班 |
| **語音** | edge-tts → imageio-ffmpeg → MPEG-1 44.1kHz MP3 |
| **資料庫** | SQLite (WAL mode) + APScheduler 定時任務 |
| **部署** | Render Web Service (GitHub 自動部署) |
| **監控** | Telegram Bot Alert |

---

## 🔧 核心技術挑戰

### 1. TTS 語音：從 `push_message` 到 `reply_message` 的架構演進
**問題**：edge-tts 輸出 MPEG-2 Layer III 24kHz，LINE 對透過 `push_message` 發送的 `AudioMessage` 有隱性兼容性問題（API 回傳 200 但訊息不顯示）。

**迭代過程**：
1. 嘗試 ffmpeg 轉碼為 MPEG-1 44.1kHz → 發現問題不在格式
2. 嘗試 Catbox 匿名上傳繞過 Cloudflare → URL 正常但訊息仍不顯示
3. 建立 `/test-ffmpeg`、`/test-push-audio`、`!測試語音回覆` 等診斷端點
4. 最終發現 **Family Bot 使用 `reply_message` 成功，Group Bot 使用 `push_message` 失敗**
5. 將 TTS 從「秒回文字 + 背景 thread push 語音」改為「同步 `reply_audio` 直接回覆語音」

**學習**：LINE Messaging API 的 `push` 與 `reply` 雖然文件上支援相同訊息類型，實際行為存在差異。診斷過程中建立的測試端點與 Telegram alert 通道是定位問題的關鍵。

### 2. 非同步架構避免 Webhook 超時
**問題**：AI 生成、語音合成、外部 API 呼叫耗時 2-10 秒，超過 LINE webhook 3 秒 timeout。

**解法**：
- 採用「秒回確認文字 + 背景 thread 推送結果」的雙階段模式
- 對於 TTS 最終改為同步 `reply_audio`（因為 edge-tts 通常 2-3 秒內完成，在 timeout 邊緣內）
- Telegram alert 作為分佈式日誌與診斷通道

### 3. 台灣城市天氣的座標對應
**問題**：用戶輸入「台北天氣」需要對應到正確經緯度，且支援「明天/後天/下週三」等自然語言日期查詢。

**解法**：
- 建立 `_TW_COORDS` 台灣城市座標表
- 日期解析邏輯：今天/明天/後天直接計算，星期幾計算到下一個該星期日期
- 台灣城市走 Open-Meteo（免費），海外城市 fallback 到 wttr.in

### 4. SQLite 並行安全
**問題**：Gunicorn workers=1 + 背景 thread 共享同一 SQLite 連線，可能發生并发寫入衝突。

**解法**：
- 啟用 SQLite WAL (Write-Ahead Logging) 模式
- 所有 DB 操作透過 `get_db()` 取得獨立連線
- 背景任務使用獨立 thread，避免阻塞 webhook 主線程

---

## 🚀 部署方式

### 環境變數

| 變數 | 說明 | 必填 |
|------|------|------|
| `LINE_CHANNEL_ACCESS_TOKEN` | LINE Messaging API token | ✅ |
| `LINE_CHANNEL_SECRET` | LINE Channel Secret | ✅ |
| `LINE_GROUP_ID` | 預設推播目標群組 ID | ✅ |
| `GEMINI_API_KEY` | Google Gemini API key | ✅ |
| `RENDER_EXTERNAL_URL` | Render 部署網址 | ✅ |
| `TELEGRAM_BOT_TOKEN` | Telegram bot token（監控告警） | ❌ |
| `TELEGRAM_CHAT_ID` | Telegram chat ID（監控告警） | ❌ |
| `RAPIDAPI_KEY` | RapidAPI key（翻譯/電影等額度服務） | ❌ |

### 步驟
1. Fork / clone 本專案
2. 在 Render 建立 Web Service，連接 GitHub repo（`render.yaml` 已設定好）
3. 設定上述環境變數
4. 在 LINE Developers Console 設定 Webhook URL：`https://<your-app>.onrender.com/webhook`
5. 機器人加入群組後即可使用

---

## 📁 專案結構

```
line-group-bot/
├── app.py                     # Flask 入口
├── line_webhook.py            # Webhook 路由與驗證
├── requirements.txt           # Python 依賴
├── render.yaml                # Render 部署設定
├── build.sh                   # 安裝 ffmpeg（imageio-ffmpeg fallback）
├── scripts/
│   ├── commands.py            # 指令分發核心（50+ 指令）
│   ├── api_helpers.py         # AI / TTS / 外部 API 整合
│   ├── weather.py             # 天氣查詢（Open-Meteo + wttr.in）
│   ├── state.py               # SQLite 狀態管理
│   ├── tts_store.py           # TTS 音檔 SQLite 持久化
│   └── goal_tracker.py        # 十日目標與打卡邏輯
├── shared/
│   ├── line_push.py           # LINE push/reply 封裝
│   └── alerts.py              # Telegram 監控告警
└── data/                      # SQLite 資料庫
```

---

## 📝 完整指令清單

在群組輸入 `指令` 或 `說明` 即可查看。

---

## 📄 License

MIT License — 歡迎參考與改作。
