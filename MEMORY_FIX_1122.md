# 小棉襖對話記憶修復（2026-11-22，分支 fix/persistent-memory，尚未 push／部署）

## 根因（兩個疊在一起）
1. 「最近對話」存在本機 SQLite：`scripts/state.py:13`（`./data/bot_state.db`），`chat_append` 在 state.py:200，只留 20 列（`max_len=20`），
   `weather.py:704 _get_recent_context(8)` 只拿最近 8 則給 `api_helpers.py:1040 handle_mention`。
   Render 免費方案磁碟是暫時的，部署／重啟／閒置休眠後喚醒都會清空，所以「一段時間就沒有了」。
   （render.yaml 沒掛 disk；`.github/workflows/keep_alive.yml` 只能減少休眠，擋不住部署重啟。）
2. 真正的長期記憶（Google Sheet「記憶」「個人記憶」分頁）靠 `scripts/daily_summary.py` 每天濃縮，
   但 `.github/workflows/daily_summary.yml` 的 cron 在 2026-08-04 commit 10bbbc9（「停用重複的每日自動推播」）被拿掉，只剩手動觸發。
   也就是說 8/4 之後沒有任何 compact 在跑，長期記憶停止更新。她猜「沒有定時 compact」是對的。
   另外 `get_memories`／`get_personal_memories` 失敗時 `except: return []`，Sheets/OAuth 壞掉會靜默變成「沒記憶」。

## 改了什麼
- 新增 `scripts/memory_store.py`：
  - 原文沿用「聊天記錄」分頁（本來每則都會寫，持久）。
  - 新增「對話摘要」分頁一列 `scope | summary | watermark | updated_at`，watermark = 已濃縮到「聊天記錄」第幾列。
  - 注入 context = 摘要 + watermark 之後的原文（最多 40 則，每則截 200 字）。
  - compact：watermark 後原文 > 30 則時，把除了最近 10 則以外的部分 + 舊摘要交給 `utils.call_ai`（Groq gpt-oss-120b，失敗退 Gemini）濃縮，
    成功才前進 watermark；單批最多 150 列／約 5500 字，不超過 sanitize_input 的 8000 字上限。每 5 則訊息檢查一次，在背景 thread 跑，不拖慢回覆。
  - LLM 回空／太短／丟例外、或 Sheet 寫入失敗：watermark 不動，原文一列都不刪（只是下次再試）；連續失敗 3 次發一次 Telegram。
  - Sheets 錯誤帶原始回應（`HTTP 400: ...`），不像舊 helper 會吞掉。
  - Sheets 整個掛掉時退回舊的本機 SQLite 最近 8 則，不會比現在更差。
  - 第一次啟用只濃縮最近 200 列，更舊的歷史留在「聊天記錄」不動（舊的由「記憶」分頁覆蓋）。
- `scripts/commands.py`：背景寫聊天記錄改呼叫 `memory_store.log_and_compact`（先 `log_chat_message`，再每 5 則檢查 compact）。
- `scripts/api_helpers.py`：`handle_mention` 的最近對話改用 `memory_store.build_context()`。
- `tests/test_memory_store.py`：9 個測試，用假 Sheets，不碰真實試算表。
  `cd line-group-bot && python3 -W ignore -m unittest tests.test_memory_store -v`（已通過）。

## 需要新增的 env
- 必要：無（沿用 GOAL_SHEET_ID、GOOGLE_CLIENT_ID／SECRET／REFRESH_TOKEN、GROQ_API_KEY／GEMINI_API_KEY）。
- 選用：`MEMORY_COMPACT_THRESHOLD`（預設 30）、`MEMORY_KEEP_RECENT`（預設 10）。
- 前提：Google OAuth 的 scope 要能「建立分頁」（spreadsheets 全權限）。若 refresh token 只有讀寫值權限，
  請手動在試算表新增「對話摘要」分頁（程式會自己寫表頭與第 2 列）。

## 上線步驟（她同意後）
1. `git checkout main && git merge fix/persistent-memory && git push`（Render 自動部署）。
2. 部署完成後在群組講一句 `@小棉襖 嗨`，確認有回覆；試算表應出現「對話摘要」分頁，第 2 列 scope=main。
3. 驗證 compact：群組講超過 30 則一般訊息後，看「對話摘要」summary 有內容、watermark 變大；
   Render Logs 搜 `memory_store: compacted`。
4. 驗證持久：Render 手動 Restart，再 `@小棉襖 我們剛剛聊了什麼`，應該還記得。
5. 建議另外（她決定）：把 `.github/workflows/daily_summary.yml` 的 `schedule: - cron: '30 15 * * *'`（台北 23:30）加回來，
   恢復「記憶／個人記憶」每日濃縮。這個腳本只寫 Sheet、不發 LINE 訊息，所以不會造成推播轟炸。本分支沒動它。

## 已知限制
- 目前只有單一群組（scope 固定 main）；bot 自己的回覆沒有寫進「聊天記錄」。
- watermark 是列號：若手動在「聊天記錄」刪列，watermark 要一起調（或把「對話摘要」第 2 列刪掉讓它重新初始化）。
- `tts_store.py` 的 cron_log／webhook_dedup 也在暫時磁碟，重啟後 cron 可能重複跑一次，與記憶無關，沒處理。

## 回滾
- 程式：`git revert` 該 merge commit（或 Render Dashboard 回到上一個 deploy），行為回到舊的 SQLite 記憶。
- 資料：新增的只有「對話摘要」分頁，可保留或刪除，不影響其他分頁；「聊天記錄」原文從未被改動／刪除。
