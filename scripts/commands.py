"""
LINE Group Bot — Message command handlers.
Extracted from line_webhook.py.
"""

import os
import re
import random
import threading
from goal_tracker import (
    get_cycle_info, get_goals, get_checkin_stats,
    set_nickname, update_last_activity,
    log_chat_message, get_streak,
    set_zodiac, get_all_zodiacs, set_zodiac_by_nickname,
    get_quiz_scores, get_week_chat_logs,
    set_birthday_by_nickname, get_all_nicknames,
)

from api_helpers import *
from api_helpers import _should_log
from linebot.v3.messaging import ApiClient, ReplyMessageRequest, TextMessage, ImageMessage, AudioMessage
from horoscope import _ZODIAC, match_zodiac
from state import translate_get, translate_delete, rate_limit_check, remove_bg_set
from weather import (
    send_morning_greeting, _parse_date_offset, get_weather_v2,
    handle_countdown, handle_translate, translate_text, get_exchange_rate,
    _remember, _async_push,
)
from dispatch import try_dispatch
from handlers.goals import (
    handle_set_goals, handle_checkin, handle_view_goals,
    handle_cycle_progress, handle_today_checkins, handle_last_cycle,
    handle_suggest_goals,
)
from handlers.todos import handle_add_todo, handle_view_todos, handle_complete_todo
from handlers.quick_replies import (
    handle_leave, handle_overtime, handle_tired,
    handle_food, handle_travel, handle_japanese_question,
)
from handlers.quiz import handle_quiz
from handlers.vote import handle_vote

# LINE messaging configuration (local copy to avoid circular imports)
_configuration = Configuration(access_token=os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", ""))

# Replace references to the outer-scope 'configuration' with _configuration
configuration = _configuration

_HELP_TEXT = (
    "📖 小棉襖指令清單\n"
    "\n🎯 十日目標\n"
    "設目標：目標1 / 目標2\n"
    "打卡 今天做了XXX\n"
    "完成 目標名稱（一次性的目標直接完成）\n"
    "查目標 / 進度 / 今日打卡\n"
    "我的打卡 / 上週期\n"
    "幫我想目標\n"
    "\n📌 待辦提醒\n"
    "提醒我 明天 要做XXX\n"
    "待辦 / 完成待辦 XXX\n"
    "\n🎲 趣味\n"
    "今日運勢 / 今日天蠍（任意星座）\n"
    "誰請客 / 抽籤 A / B\n"
    "配對 A B / 搖骰子 / 猜拳 剪刀\n"
    "配對星座 天蠍 金牛\n"
    "貓貓 / 狗狗 / 狐狸 / 柴柴\n"
    "熊貓 / 無尾熊 / 浣熊\n"
    "今日宇宙 / 抽寶可夢\n"
    "今日食譜 / 推薦電影\n"
    "冷笑話 / 冷知識 / 給我建議\n"
    "動漫圖 / 激勵名言\n"
    "\n🎵 媒體 & 娛樂\n"
    "找歌 [歌名]\n"
    "找影片 [關鍵字]（YouTube）\n"
    "查電影 [片名]\n"
    "電影台詞\n"
    "在哪看 [片名]\n"
    "動漫語錄\n"
    "川普語錄\n"
    "隨機梗圖\n"
    "諾里斯\n"
    "新聞（今日頭條）\n"
    "\n💪 生活\n"
    "今日運動 / 找運動 [部位]\n"
    "今日調酒 / 今日調酒 馬丁尼\n"
    "來一題 → 答 xxx → 答案\n"
    "積分（本週答題排行）\n"
    "我好無聊\n"
    "BMI 165 55\n"
    "熱量 [食物]\n"
    "消耗熱量 [活動] 30分鐘\n"
    "食譜 [食材]\n"
    "金價\n"
    "天文冷知識 / 數字冷知識\n"
    "\n📖 查詢\n"
    "國家 [名稱]\n"
    "找書 [書名]\n"
    "寶可夢 [英文名/ID]\n"
    "\n🔊 語音\n"
    "說 [文字] / 念 [文字] / 唸 [文字] / 讀 [文字]\n"
    "\n🌐 翻譯\n"
    "翻 日文 [文字]\n"
    "翻 英文 [文字]\n"
    "（支援：日/英/韓/法/西/德/泰/越等）\n"
    "摘要 [長文]\n"
    "\n🔧 實用\n"
    "匯率 / 天氣 [城市]\n"
    "[日期]天氣 / [日期]會下雨嗎 — 今天/明天/後天/星期三/下週五\n"
    "倒數 6/15\n"
    "QR [網址或文字]\n"
    "縮網址 [URL]\n"
    "改寫 [文字]（嗆辣/撒嬌/正經三版）\n"
    "去背（傳圖後自動去背）\n"
    "傳音訊 → 自動辨識歌曲（Shazam）\n"
    "\n🇯🇵 日文學習\n"
    "XXX日文怎麼說\n"
    "查日文 [單字]\n"
    "今日日文單字\n"
    "漢字 [字]\n"
    "今日漢字\n"
    "\n🇪🇸 西文學習\n"
    "查西文 [單字]\n"
    "今日西文單字\n"
    "\n⚙️ 設定\n"
    "本週總結\n"
    "叫我 [暱稱]\n"
    "我是 [星座]（綁定星座，今日運勢自動跑）\n"
    "@小棉襖 問任何問題\n"
    "\n輸入「配額」查看今日額度說明"
)

_QUOTA_TEXT = (
    "📊 API 額度說明\n"
    "\n✅ 完全免費（無限）\n"
    "天氣 / 匯率 / 倒數\n"
    "QR碼 / 縮網址\n"
    "寶可夢 / 國家 / 找書\n"
    "查日文 / 漢字 / 查西文\n"
    "諾里斯 / 川普語錄\n"
    "動漫圖 / 激勵名言\n"
    "冷笑話 / 給我建議\n"
    "\n⚡ 有每日額度（RapidAPI 輪班）\n"
    "找歌 / 查電影 / 電影台詞 / 在哪看\n"
    "今日運動 / 找運動\n"
    "今日調酒 / 來一題\n"
    "動漫語錄 / 隨機梗圖\n"
    "今日宇宙（NASA）\n"
    "熱量 / 消耗熱量 / 金價\n"
    "天文冷知識 / 數字冷知識\n"
    "食譜 / 找影片（YouTube）\n"
    "配對星座 / 今日運勢\n"
    "翻譯 / 摘要\n"
    "Shazam聽歌 / 去背\n"
    "\n🤖 Gemini AI（有每日限制）\n"
    "改寫 / 新聞 / 日西文每日單字\n"
    "各功能 AI fallback\n"
    "\n額度用完會自動提示，明天重置 🔄"
)



# ─── Simple text dispatch ─────────────────────────────
_TEXT_DISPATCH = {
    "配額": lambda: _QUOTA_TEXT,
    "/配額": lambda: _QUOTA_TEXT,
    "api配額": lambda: _QUOTA_TEXT,
    "額度": lambda: _QUOTA_TEXT,
    "查目標": lambda: handle_view_goals(),
    "看目標": lambda: handle_view_goals(),
    "目標": lambda: handle_view_goals(),
    "今天第幾天": lambda: handle_cycle_progress(),
    "幾天了": lambda: handle_cycle_progress(),
    "進度": lambda: handle_cycle_progress(),
    "打卡進度": lambda: handle_cycle_progress(),
    "今天打卡了嗎": lambda: handle_today_checkins(),
    "今日打卡": lambda: handle_today_checkins(),
    "誰打卡了": lambda: handle_today_checkins(),
    "上週期": lambda: handle_last_cycle(),
    "上次總結": lambda: handle_last_cycle(),
    "上輪總結": lambda: handle_last_cycle(),
    "待辦": lambda: handle_view_todos(),
    "查提醒": lambda: handle_view_todos(),
    "查待辦": lambda: handle_view_todos(),
    "電影台詞": lambda: fetch_movie_quote(),
    "動漫語錄": lambda: fetch_anime_quote(),
    "我好無聊": lambda: fetch_random_activity(),
    "川普語錄": lambda: fetch_trump_quote(),
    "諾里斯": lambda: fetch_chuck_norris(),
    "今日日文單字": lambda: fetch_daily_japanese(),
    "日文單字": lambda: fetch_daily_japanese(),
    "學日文": lambda: fetch_daily_japanese(),
    "今日漢字": lambda: fetch_daily_kanji(),
    "學漢字": lambda: fetch_daily_kanji(),
    "今日西文單字": lambda: fetch_daily_spanish(),
    "西文單字": lambda: fetch_daily_spanish(),
    "學西文": lambda: fetch_daily_spanish(),
    "金價": lambda: fetch_gold_price(),
    "今日金價": lambda: fetch_gold_price(),
    "黃金價格": lambda: fetch_gold_price(),
    "天文冷知識": lambda: fetch_astronomy_fact(),
    "科學冷知識": lambda: fetch_astronomy_fact(),
    "宇宙冷知識": lambda: fetch_astronomy_fact(),
    "數字冷知識": lambda: fetch_number_fact(),
    "數字趣聞": lambda: fetch_number_fact(),
    "配對星座": lambda: "請傳「配對星座 星座1 星座2」\n例：配對星座 天蠍 金牛",
    "新聞": lambda: fetch_news(),
    "今日新聞": lambda: fetch_news(),
    "最新新聞": lambda: fetch_news(),
}

_REGEX_DISPATCH = [
    (re.compile(r"^找歌\s*(.+)$"), lambda match: fetch_spotify_track(match.group(1).strip())),
    (re.compile(r"^查電影\s*(.+)$"), lambda match: fetch_imdb(match.group(1).strip())),
    (re.compile(r"^在哪看\s*(.+)$"), lambda match: fetch_streaming(match.group(1).strip())),
    (re.compile(r"^查超英\s*(.+)$"), lambda match: fetch_superhero(match.group(1).strip())),
    (re.compile(r"^國家\s*(.+)$"), lambda match: fetch_country(match.group(1).strip())),
    (re.compile(r"^找書\s*(.+)$"), lambda match: fetch_book(match.group(1).strip())),
    (re.compile(r"^查日文\s*(.+)$"), lambda match: fetch_jisho(match.group(1).strip())),
    (re.compile(r"^漢字\s*([^\s])$"), lambda match: fetch_kanji(match.group(1))),
    (re.compile(r"^查西文\s*(.+)$"), lambda match: fetch_spanish(match.group(1).strip())),
    (re.compile(r"^熱量\s+(.+)$"), lambda match: fetch_nutrition(match.group(1).strip())),
    (re.compile(r"^食譜\s+(.+)$"), lambda match: fetch_recipe_by_ingredient(match.group(1).strip())),
    (re.compile(r"^縮網址\s+(.+)$"), lambda match: shorten_url(match.group(1).strip())),
]


def _push_messages(messages: list):
    """Push messages to the default group."""
    gid = os.environ.get("LINE_GROUP_ID", "")
    if gid:
        from line_push import push_messages
        push_messages(gid, messages)


def handle_message(event):
    text = event.message.text.strip()
    reply_token = event.reply_token
    source = event.source
    group_id = getattr(source, 'group_id', None)
    user_id = getattr(source, 'user_id', None)

    # Rate limiting (30 requests / 60s per user)
    if user_id and not rate_limit_check(user_id, max_requests=30, window_seconds=60):
        with ApiClient(configuration) as api_client:
            api_client.default_api.reply_message(
                ReplyMessageRequest(reply_token=reply_token, messages=[TextMessage(text="⏳ 你發太快了，請稍後再試 👋")])
            )
        return

    # ── Fast-path: simple stateless dispatch ──
    disp_text, disp_img = try_dispatch(text)
    if disp_text is not None:
        if disp_img:
            with ApiClient(configuration) as api_client:
                MessagingApi(api_client).reply_message(
                    ReplyMessageRequest(
                        reply_token=reply_token,
                        messages=[
                            TextMessage(text=disp_text[:4900]),
                            ImageMessage(original_content_url=disp_img, preview_image_url=disp_img),
                        ],
                    )
                )
        else:
            reply(reply_token, disp_text)
        return

    # Update last activity + log message in background
    threading.Thread(target=update_last_activity, daemon=True).start()

    reply_text = None
    reply_image_url = None

    with ApiClient(configuration) as api_client:
        member_label = get_member_label(api_client, group_id, user_id)
        _remember(member_label, text)
        if _should_log(text):
            threading.Thread(
                target=log_chat_message, args=(member_label, text), daemon=True
            ).start()

        # ── 指令清單 ──
        if text in ("指令", "說明", "幫助", "help", "功能"):
            reply_text = _HELP_TEXT

        # ── 隱藏指令 ──
        elif text == "!groupid":
            reply_text = f"Group ID: {group_id or '非群組訊息'}"

        elif text == "!測試早安":
            reply_text = "⏳ 生成早安訊息中..."
            threading.Thread(target=send_morning_greeting, daemon=True).start()

        elif m := re.match(r'^!設星座\s+(\S+)\s+(.+)$', text):
            nick_target, sign_input = m.group(1).strip(), m.group(2).strip().rstrip("座")
            matched = next((k for k in _ZODIAC if k.rstrip("座") == sign_input or k == sign_input), None)
            if matched:
                ok = set_zodiac_by_nickname(nick_target, matched)
                reply_text = f"✅ {nick_target} → {matched}" if ok else f"找不到暱稱「{nick_target}」，請確認他有登記「叫我」"
            else:
                reply_text = f"不認識「{sign_input}」，請用：{'、'.join(_ZODIAC.keys())}"

        elif text == "!查星座":
            members = get_all_zodiacs()
            if members:
                lines = ["🔮 目前星座綁定："]
                for _, nick, zodiac in members:
                    lines.append(f"  {nick}：{zodiac}")
                reply_text = "\n".join(lines)
            else:
                reply_text = "還沒有人綁定星座"

        elif text == "!查暱稱":
            nicks = get_all_nicknames()
            if nicks:
                lines = ["👤 已登記暱稱："]
                for uid, nick in nicks:
                    lines.append(f"  {nick}：{uid}")
                reply_text = "\n".join(lines)
            else:
                reply_text = "還沒有人登記暱稱"

        elif m := re.match(r'^!設暱稱\s+(\S+)\s+(.+)$', text):
            nick_target, uid = m.group(1).strip(), m.group(2).strip()
            if uid.startswith("U") and len(uid) > 10:
                ok = set_nickname(uid, nick_target)
                reply_text = f"✅ 已將 {uid} 設為「{nick_target}」" if ok else "設定失敗 😢"
            else:
                reply_text = f"userId 格式不對：{uid}（應該是 U 開頭的長字串）"

        elif m := re.match(r'^!設生日\s+(\S+)\s+(\d{1,2})[/月](\d{1,2})$', text):
            nick_b, mo, dy = m.group(1), m.group(2).zfill(2), m.group(3).zfill(2)
            ok = set_birthday_by_nickname(nick_b, f"{mo}-{dy}")
            reply_text = f"🎂 {nick_b} 生日設為 {mo}/{dy}" if ok else f"找不到暱稱「{nick_b}」"

        elif m := re.match(r'^!測試語音回覆\s*(.+)$', text):
            # 測試用 reply_token 直接回覆 AudioMessage（與 Family Bot 相同方式）
            to_speak = m.group(1).strip()[:10]  # 限制短文字避免超時
            base_url = os.environ.get("RENDER_EXTERNAL_URL", "")
            if not base_url:
                reply_text = "🔊 需要設定 RENDER_EXTERNAL_URL"
            else:
                try:
                    from api_helpers import text_to_speech, save_tts_audio
                    tts_result = text_to_speech(to_speak, "zh-TW")
                    if tts_result:
                        audio_bytes, mime = tts_result
                        fname = save_tts_audio(audio_bytes, mime)
                        duration = min(len(to_speak) * 300 + 1000, 60000)
                        audio_url = f"{base_url}/tts/{fname}"
                        # 直接 reply AudioMessage（不先發文字）
                        from linebot.v3.messaging import AudioMessage
                        from line_push import reply_audio
                        reply_audio(event.reply_token, audio_url, duration)
                        reply_text = None  # 已經用掉 reply_token
                    else:
                        reply_text = "🔊 語音生成失敗"
                except Exception as exc:
                    import logging
                    logging.exception("測試語音回覆失敗: %s", exc)
                    reply_text = f"🔊 測試失敗: {exc}"

        # ── TTS 語音 ──
        elif m := re.match(r"^(?:念|唸|說|讀)(?:出來)?\s*(.+)", text):
            to_speak = m.group(1).strip()
            base_url = os.environ.get("RENDER_EXTERNAL_URL", "")
            if not base_url:
                reply_text = "🔊 需要設定 RENDER_EXTERNAL_URL 才能發送語音"
            else:
                # 同步生成 + reply_audio（與 Family Bot 相同方式，避免 push_message 發 AudioMessage 的問題）
                try:
                    from api_helpers import text_to_speech, save_tts_audio
                    from line_push import reply_audio
                    tts_result = text_to_speech(to_speak, "zh-TW")
                    if tts_result:
                        audio_bytes, mime = tts_result
                        fname = save_tts_audio(audio_bytes, mime)
                        duration = min(len(to_speak) * 300 + 1000, 60000)
                        audio_url = f"{base_url}/tts/{fname}"
                        reply_audio(event.reply_token, audio_url, duration)
                        reply_text = None  # 已經用掉 reply_token
                    else:
                        reply_text = "🔊 語音合成暫時失敗，請稍後再試 😢"
                except Exception as exc:
                    import logging
                    logging.exception("TTS failed: %s", exc)
                    reply_text = "🔊 語音發送時出錯"

        # ── 積分榜 ──
        elif text in ("積分", "本週積分", "答題積分", "quiz積分"):
            scores = get_quiz_scores()
            if scores:
                ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
                lines = ["🏆 本週答題積分："]
                medals = ["🥇", "🥈", "🥉"]
                for i, (nick, s) in enumerate(ranked):
                    m_icon = medals[i] if i < 3 else "  "
                    lines.append(f"{m_icon} {nick}：{s} 題")
                reply_text = "\n".join(lines)
            else:
                reply_text = "本週還沒有人答對過題目，輸入「來一題」開始！"

        # ── 本週總結 ──
        elif text in ("本週總結", "週總結", "本週回顧"):
            logs = get_week_chat_logs()
            if logs:
                sample = logs[-80:]
                chat_text = "\n".join(f"{n}：{m}" for n, m in sample)
                _async_push(reply_token, "📝 Gemini 整理中...", lambda: call_gemini(
                    f"以下是朋友群最近一週的聊天記錄（節選）：\n{chat_text}\n\n"
                    "請用輕鬆幽默的語氣，100字以內，整理這週大家聊了什麼、有什麼有趣的事，繁體中文。"
                ) or "這週大家都很忙，聊得不多 😅")
                return
            else:
                reply_text = "這週還沒有聊天記錄"

        # ── 暱稱登記 ──
        elif nick_match := re.match(r'^叫我\s*(.+)$', text):
            nickname = nick_match.group(1).strip()
            ok = set_nickname(user_id, nickname)
            reply_text = f"好的！之後叫你「{nickname}」了 👋" if ok else "登記失敗，等一下再試 😢"

        # ── 星座綁定 ──
        elif m := re.match(r'^我是\s*(.{2,3}座?)$', text):
            sign_input = m.group(1).strip().rstrip("座")
            matched = next((k for k in _ZODIAC if k.rstrip("座") == sign_input or k == sign_input + "座" or k == sign_input), None)
            if matched and user_id:
                ok = set_zodiac(user_id, matched)
                reply_text = f"✅ 已幫你綁定「{matched}」！\n之後輸入「今日運勢」會自動幫你出來 🔮" if ok else "綁定失敗，等一下再試 😢"
            elif matched is None:
                signs = " / ".join(_ZODIAC.keys())
                reply_text = f"不認識這個星座，請用：\n{signs}"

        # ── 十日目標：設目標 ──
        elif re.match(r'^設目標[：:]', text):
            reply_text = handle_set_goals(member_label, text)

        # ── 十日目標：打卡 ──
        elif text.startswith("打卡"):
            try:
                cycle_id, _, _ = get_cycle_info()
                goals_dict = get_goals(cycle_id)
                user_goals = goals_dict.get(member_label)
                reply_text = handle_checkin(member_label, text, user_goals)
            except Exception as exc:
                import logging
                logging.exception("handle_checkin error for %s: %s", member_label, exc)
                reply_text = f"打卡處理時出錯：{type(exc).__name__}，請稍後再試 😢"

        # ── 十日目標：完成單項目標 ──
        elif text.startswith("完成 "):
            goal_input = text[3:].strip()
            cycle_id, _, _ = get_cycle_info()
            goals_dict = get_goals(cycle_id)
            user_goals = goals_dict.get(member_label, [])
            if not user_goals:
                reply_text = "你這週期還沒設目標，先輸入：設目標：目標1 / 目標2"
            else:
                matched = None
                for g in user_goals:
                    if goal_input.lower() in g.lower() or g.lower() in goal_input.lower():
                        matched = g
                        break
                if matched:
                    from goal_tracker import complete_goal
                    if complete_goal(member_label, matched):
                        reply_text = f"✅ 已將「{matched}」標記為完成！"
                    else:
                        reply_text = "標記失敗，請稍後再試 😢"
                else:
                    goals_preview = "\n".join(f"  • {g}" for g in user_goals)
                    reply_text = f"找不到符合的目標，你的目標是：\n{goals_preview}"

        # ── 十日目標：查詢 ──




        elif re.match(r'^幫我想目標', text):
            reply_text = handle_suggest_goals(member_label, text)

        elif text in ("我的打卡", "打卡記錄", "我打了幾天"):
            cycle_id, day, total = get_cycle_info()
            stats = get_checkin_stats(cycle_id)
            checked = sorted(stats.get(member_label, []))
            streak = get_streak(member_label)
            if not checked:
                reply_text = "你這週期還沒打卡喔！快去打卡 💪\n指令：打卡 今天做了XXX"
            else:
                bar = "".join("🟩" if d in set(checked) else "⬜" for d in range(1, total + 1))
                days_str = "、".join(f"第{d}天" for d in checked)
                streak_line = f"🔥 目前連續 {streak} 天" if streak >= 2 else ""
                lines = [
                    f"📋 {member_label} 的打卡記錄",
                    f"{bar}  {len(checked)}/{total} 天",
                    days_str,
                ]
                if streak_line:
                    lines.append(streak_line)
                reply_text = "\n".join(lines)

        # ── 趣味功能（fallback for partial matches not caught by dispatch）──
        # ── 待辦 ──
        elif re.match(r'^提醒(我|\s)', text):
            reply_text = handle_add_todo(member_label, text)


        elif re.match(r'^完成待辦', text):
            reply_text = handle_complete_todo(member_label, text) or "格式：完成待辦 [事項名稱]"

        elif re.search(r'倒數|還有幾天|距離', text):
            reply_text = handle_countdown(text)

        # ── 實用功能 ──
        elif re.search(r'匯率|美金|日幣|人民幣|換錢|外幣', text):
            reply_text = get_exchange_rate(text)

        # ── 天氣 ──
        elif (
            any(k in text for k in ("天氣", "下雨", "會下雨", "帶傘", "氣溫", "溫度", "適合出門", "出門嗎"))
            or (_parse_date_offset(text) and ("嗎" in text or "?" in text or "？" in text))
        ):
            reply_text = get_weather_v2(text)

        # ── 翻譯 ──
        elif re.match(r'^翻\s', text):
            reply_text = handle_translate(user_id, text)

        # ── 找歌 ──

        # ── 查電影 ──

        # ── 電影台詞 ──

        # ── 在哪看 ──

        # ── 今日運動 / 找運動 ──
        # ── 來一題（A/B/C/D 選擇題）──
        elif (quiz_result := handle_quiz(text, group_id, member_label)) is not None:
            reply_text = quiz_result


        # ── 今日調酒 ──
        # ── 動漫語錄 ──

        # ── 我好無聊 ──

        # ── 川普語錄 ──

        # ── 查超英 ──

        # ── 隨機梗圖 ──
        elif text == "隨機梗圖":
            meme_text, meme_url = fetch_meme()
            reply_text = meme_text
            reply_image_url = meme_url

        # ── 諾里斯 ──

        # ── 摘要 ──
        elif m := re.match(r'^摘要\s*(.+)$', text, re.DOTALL):
            reply_text = fetch_tldr(m.group(1).strip())

        # ── 國家資訊 ──

        # ── 找書 ──

        # ── 動漫圖 ──
        elif text == "動漫圖":
            reply_text, reply_image_url = fetch_waifu()

        # ── 激勵名言 ──
        elif text in ("激勵名言", "今日名言"):
            q = fetch_quotable()
            reply_text = q if q else "✨ 今天也要加油！"

        # ── 寶可夢詳細 ──
        elif m := re.match(r'^寶可夢\s*(.+)$', text):
            result = fetch_pokemon_detail(m.group(1).strip())
            if isinstance(result, tuple):
                reply_text, reply_image_url = result
            else:
                reply_text = result

        # ── 日文字典 ──




        # ── 西班牙文字典 ──


        # ── 日文問題 ──
        elif (jp := handle_japanese_question(text)):
            reply_text = jp

        # ── 關鍵字回覆（隨機觸發避免煩人）──
        elif re.search(r'特休|年假|請假|休假', text) and random.random() < 0.65:
            reply_text = handle_leave(text)

        elif re.search(r'加班', text) and random.random() < 0.6:
            reply_text = handle_overtime(text)

        elif re.search(r'好累|超累|累死|累爆', text) and random.random() < 0.55:
            reply_text = handle_tired(text)

        elif re.search(r'吃什麼|午餐|晚餐|要吃', text) and random.random() < 0.65:
            reply_text = handle_food(text)

        elif re.search(r'出去玩|要去玩|去旅遊|旅行', text) and random.random() < 0.7:
            reply_text = handle_travel(text)

        # ── BMI ──
        elif m := re.match(r'^BMI\s+(\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)$', text, re.IGNORECASE):
            h, w = float(m.group(1)), float(m.group(2))
            r2 = calc_bmi(h, w)
            reply_text = f"⚖️ BMI 計算\n身高 {h}cm / 體重 {w}kg\n\nBMI：{r2['bmi']}\n{r2['category']}"

        elif re.match(r'^BMI$', text, re.IGNORECASE):
            reply_text = "請傳「BMI 身高 體重」\n例：BMI 165 55"

        # ── 熱量 ──

        # ── 消耗熱量 ──
        elif m := re.match(r'^消耗熱量\s+(.+?)(?:\s+(\d+)分鐘?)?$', text):
            activity = m.group(1).strip()
            duration = int(m.group(2)) if m.group(2) else 30
            reply_text = fetch_calories_burned(activity, duration)

        # ── 金價 ──

        # ── 天文冷知識 ──

        # ── 數字冷知識 ──

        # ── 食譜 [食材] ──

        # ── QR Code ──
        elif m := re.match(r'^QR\s+(.+)$', text, re.IGNORECASE):
            url_or_text = m.group(1).strip()
            qr_text, qr_img = fetch_qr_code(url_or_text)
            reply_text = qr_text
            reply_image_url = qr_img

        # ── 縮網址 ──

        # ── YouTube 搜尋 ──
        elif m := re.match(r'^找影片\s+(.+)$', text):
            _async_push(reply_token, "🎬 搜尋影片中...", fetch_youtube, m.group(1).strip())
            return

        # ── 改寫文案 ──
        elif m := re.match(r'^改寫\s+(.+)$', text, re.DOTALL):
            _async_push(reply_token, "✍️ 改寫中，請稍候...", rewrite_text, m.group(1).strip())
            return

        # ── 星座配對 ──
        elif m := re.match(r'^配對星座\s+(\S+)\s+(\S+)$', text):
            s1, s2 = m.group(1).strip(), m.group(2).strip()
            _async_push(reply_token, "💫 星座配對計算中...", match_zodiac, s1, s2)
            return


        # ── 新聞 ──

        elif (vote_result := handle_vote(text, group_id, member_label)) is not None:
            reply_text = vote_result


        # ── 隨機挑戰 ──
        elif re.search(r'隨機挑戰|幫我想挑戰|給我挑戰', text):
            _async_push(reply_token, "🎯 出題中...", lambda: call_gemini(
                "幫我想一個有趣的7天個人小挑戰，適合上班族或學生，具體可執行，"
                "格式：\n🎯 本週挑戰：[挑戰名稱]\n\n每天要做：[具體行動]\n\n為什麼值得試：[一句話]\n\n加油！"
            ) or "🎯 本週挑戰：每天喝 2000ml 水\n\n每天要做：用水杯計量，睡前確認\n\n為什麼值得試：改善皮膚和精神狀態 💪")
            return

        # ── 去背（等待圖片） ──
        elif text == "去背":
            if user_id:
                remove_bg_set(user_id, True)
                reply_text = "請傳一張圖片，我幫你去背 🖼️"

        # ── 待翻譯輸入 ──
        elif user_id and translate_get(user_id) is not None:
            if text in ("取消", "算了", "不翻了"):
                translate_delete(user_id)
                reply_text = "好，取消翻譯 👌"
            else:
                lang_code = translate_get(user_id)
                translate_delete(user_id)
                reply_text = translate_text(text, lang_code)

        # ── 情緒回覆（提到小棉襖 + 有情緒關鍵詞，且還沒被其他指令處理）──
        if reply_text is None:
            from mood_replies import detect_mood, handle_mood_mention
            mood = detect_mood(text)
            if mood and (BOT_NAME in text or BOT_DISPLAY_NAME in text
                         or "機器人" in text or "小棉襖" in text or "bot" in text.lower()):
                reply_text = handle_mood_mention(text, member_label, mood)

        # ── 被點名（一般對話，且還沒被其他指令處理）──
        if reply_text is None:
            if (BOT_NAME in text or BOT_DISPLAY_NAME in text
                  or "機器人" in text or "小棉襖" in text or "bot" in text.lower()):
                reply_text = handle_mention(text, member=member_label)

        if not reply_text and not reply_image_url:
            return

        messages = []
        if reply_text:
            messages.append(TextMessage(text=reply_text))
        if reply_image_url:
            messages.append(ImageMessage(
                original_content_url=reply_image_url,
                preview_image_url=reply_image_url,
            ))

        line_bot_api = MessagingApi(api_client)
        try:
            line_bot_api.reply_message(
                ReplyMessageRequest(reply_token=reply_token, messages=messages)
            )
        except Exception as exc:
            import logging, requests
            logging.warning("reply failed, falling back to push: %s", exc)
            _gid = os.environ.get("LINE_GROUP_ID", "")
            token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
            if _gid and token and reply_text:
                requests.post(
                    "https://api.line.me/v2/bot/message/push",
                    headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                    json={"to": _gid, "messages": [{"type": "text", "text": reply_text}]},
                    timeout=10,
                )


def handle_audio(event):
    """Shazam: identify song from audio message."""
    from handlers.media import handle_audio as _handle_audio
    _handle_audio(event, configuration)


def handle_image(event):
    """NSFW detection + background removal for image messages."""
    from handlers.media import handle_image as _handle_image
    _handle_image(event, configuration)


def handle_join(event):
    from handlers.join import handle_join as _handle_join
    _handle_join(event, configuration)
