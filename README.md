# LINE 朋友群機器人

每天自動發日文學習內容到朋友群，支援自動回覆。

## 功能
- 每天 11:00 發話題 + 日文 N5 單字
- 每週日日文小測驗
- 每 5 天推薦日文學習資源
- 自動回覆「XX 日文怎麼說」
- 被 tag 時 Gemini 回覆

## 部署
- webhook：Render（`render.yaml`）
- 排程推播：GitHub Actions

## 環境變數
- `LINE_CHANNEL_SECRET`
- `LINE_CHANNEL_ACCESS_TOKEN`
- `LINE_GROUP_ID`
- `GEMINI_API_KEY`
