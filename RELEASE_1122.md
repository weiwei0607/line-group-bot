# 小棉襖 RELEASE 2026-11-22（分支 fix/persistent-memory，尚未 push／merge／部署）

兩個 commit：①對話記憶持久化（見 MEMORY_FIX_1122.md）②本次優化（提案 A1、B1、A2/A3/A4、C1）。
未做：B2（群聊誤觸發）、E1（群組白名單）、D 系列，等她決定。沒有動任何 Sheet 內容、沒有刪任何資料。

## 改了什麼
**A1 冷啟動**
- 新增 `/ping`（`line_webhook.py`，GET/HEAD，只回 `ok`，不碰網路／SQLite）。`/health` 保留給人手動看。
- `.github/workflows/keep_alive.yml`：原本就有（每 5 分鐘），改打 `/ping`、timeout 90 秒（休眠喚醒要 30–60 秒）、
  失敗會在 log 留下 HTTP 狀態與耗時（原本 `|| true` 完全沒痕跡）。沿用既有 secret `RENDER_APP_URL`，檔內沒有金鑰。
  （已有 5 分鐘版本，所以沒另外加 10 分鐘版，免得重複。）
- 新增 `gunicorn.conf.py`（repo 根目錄）：`threads = 4`、`timeout = 60`。gunicorn 會自動讀工作目錄的這個檔，
  Render 的 startCommand／Procfile 完全不用改；仍是 1 個 process（排程鎖假設單 process）。
- 仍建議（她決定）：UptimeRobot 免費版每 5 分鐘 HEAD `<網址>/ping` 當備援。

**B1 12 個壞掉的指令**
- `commands.py` 新增 `run_regex_commands()`，在 `handle_message` 最前面（`try_dispatch` 之前）跑 `_REGEX_DISPATCH`。
  必須比 try_dispatch 先跑：否則「查電影 貓貓」「找歌 笑話」會被 try_dispatch 裡無錨點的「貓貓／笑話」搶走。
  handler 丟例外會 log 並回「查詢出了點狀況」。`國家` 改成需要空白（`國家 日本`），避免「國家隊…」誤觸額度。
- `dispatch.py`：`^漢字` 改成 `^今日漢字|^學漢字|^每日漢字|^漢字$`，`漢字 猫` 才會走查詢。
- 12 個指令：找歌、查電影、在哪看、查超英、國家、找書、查日文、漢字 X、查西文、熱量、食譜 X、縮網址。
- C2 防編事實：`fetch_spotify_track`、`fetch_imdb` 查不到就說「找不到／查不到」，不再叫 Gemini 編歌名、年份、導演、主演。
  `fetch_jisho`、`fetch_kanji`、`fetch_recipe_by_ingredient` 的 AI 備援保留（本來就是解釋／生成），但結尾標註「由 AI 解釋／生成，請再確認」；食譜無結果且 AI 也失敗會說查不到。
- 順手發現並修掉（提案沒寫）：`@小棉襖 xxx` 去掉前綴後，原本的「是否點名 bot」判斷要求文字裡仍有 bot 名字，
  所以 `@小棉襖 嗨`、`@小棉襖 今天有什麼新聞` 這類**沒有其他指令命中的訊息會完全沒有回應**。現在記錄 `addressed`，有 @ 就視為點名。
  （若上線後行為跟以前不同，這是主要原因；要回復就 revert。）

**A2 reply 可靠性**（`shared/line_push.py`、`commands.py`）
- `_reply_messages` 檢查狀態碼；非 2xx → log 狀態碼＋回應前 200 字，回 False；Telegram 告警同一種狀態碼 30 分鐘一次。`reply_text` 回傳 bool。
- 只有「@ 對話／情緒回覆」（使用者在等的）reply 失敗時，改用 push 補送到該群組一次（會用掉少量 push 額度）；其他指令不補送。

**A3/A4 LLM**（`utils.py`）
- `call_groq`：不再吞例外。非 2xx 記 log（狀態碼＋原始回應）；429／其他 4xx 不重試；5xx／逾時重試 1 次；回應格式異常會 log＋告警；
  `finish_reason == "length"` 或 content 為空／None 一律視為失敗（回 None，走 Gemini 備援）。
  預設 `max_tokens` 500→800，加 `reasoning_effort: "low"`；可傳 `system`。
- `call_gemini`：移除外層 3 輪×內層 3 次（最壞 9 個請求、100 秒以上），改最多 2 次且只對 5xx／逾時／連線錯誤重試；
  429／4xx 直接放棄；沒 candidates／沒 parts／MAX_TOKENS 都 log；log 不含網址（網址帶金鑰）。
- `call_ai`：整條鏈總時限 20 秒，Groq 用掉太多就不再呼叫 Gemini。
- Telegram 告警 `alert_once` 同類 30 分鐘一次。
- 記憶摘要（`memory_store._default_llm`）`max_tokens=2000`；被截斷＝失敗＝不寫入、watermark 不動（原文不丟）。有測試。
- 使用者看到的失敗訊息改為「剛剛連不上腦袋 😵 晚點再 @ 我一次吧」（舊的「腦袋有點卡」像個性、看不出可重試）。

**C1 prompt 護欄**（`api_helpers.py`）
- 人設與規則移到 system：「沒有即時資訊；新聞、匯率、金價、天氣、股價、比分、票房、節日、上映日不知道就說不知道、不准編；沒給節日就是沒有」。
- user 內容開頭加【今天】2026年11月22日 星期日（台北時間）；群組近況／個人記憶標「舊資料，僅供參考」（這兩個其實是 8/4 以前的資料，見提案 A5，沒動資料來源）。
- 進 LLM 前的輕量攔截 `pre_llm_guard`（只攔問句型）：今天幾號／星期幾 → 直接用系統日期答；今天什麼節日 → 查 `festivals.py`；
  新聞／頭條問句 → 走 `fetch_news()`（真資料，抓不到就說抓不到）；股價／比分／票房／匯率／上映問句 → 固定回「查不到，不敢亂講」。閒聊（「我今天好累」「新聞好煩」）不受影響，有測試。
- `dispatch.py` 新聞正則加入「今天／最近有什麼新聞」問句型。

## 測試
`cd line-group-bot && python3 -W ignore -m unittest discover -s tests`：46 個全過（原 test_memory_store 9 個＋新 test_reliability_1122 37 個）。
全 mock，不打外部 API、不碰真實 Sheet。涵蓋：Groq/Gemini 狀態碼與重試次數、截斷視為失敗、告警去重、reply 非 2xx、
12 指令逐一被正確 handler 認領、regex 先於關鍵字、漢字不被搶、Spotify/IMDb 不編、prompt 含日期與護欄、新聞/日期/節日攔截、/ping、gunicorn.conf、keep_alive 內容。
未實測：真實 LINE／Render（靜態＋mock 驗證），所以請照下面步驟上線後驗證。

## 上線步驟（她審過之後）
1. `git checkout main && git merge fix/persistent-memory && git push`（Render 自動部署）。
2. 確認 GitHub Secrets 有 `RENDER_APP_URL`（既有）。到 Actions 手動跑一次 Keep Alive，看 log 有 `ping status=200`。
3. 瀏覽器開 `<Render網址>/ping` 應顯示 `ok`。
4. Render Logs 確認啟動 log 有 `Using worker: gthread`（threads 生效）。
5. 依 MEMORY_FIX_1122.md 驗證記憶（「對話摘要」分頁、compact）。
6. 新增 env：無必要項目。

## 上線後在群組驗證
- `查電影 奧本海默`（有結果或「查不到」，不該是編的簡介或沒反應）；`找歌 周杰倫`；`查日文 猫`；`漢字 猫`（要查那個字，不是隨機）；`漢字`（才是隨機今日漢字）；`縮網址 https://example.com`；`熱量 apple`
- `查電影 貓貓`（應該查電影，不是貓圖）
- `@小棉襖 嗨`（以前可能沒反應，現在要回）
- `@小棉襖 今天有什麼新聞`（真頭條或「抓不到」，不是編的）
- `@小棉襖 今天幾號`／`今天是什麼節日`（日期正確、節日查表）
- `@小棉襖 台積電股價多少`（回查不到）
- Render Logs：搜 `call_groq`／`call_gemini`／`LINE_REPLY`，現在失敗會有狀態碼；RapidAPI 額度會因 12 指令復活而開始消耗，用完走既有 `_QUOTA_MSG`。
- 久沒人講話後第一句 @：觀察是否有回（保活效果）。

## 回滾
- 全部：`git revert` 本次 commit（或 Render Dashboard 回上一個 deploy）。只想關 threads：刪 `gunicorn.conf.py`。
- keep_alive 要回舊行為：把 `/ping` 改回 `/health`。
- 沒有資料遷移，沒有新分頁（「對話摘要」分頁屬於記憶那個 commit，可保留）。
