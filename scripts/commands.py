"""
LINE Group Bot — Message command handlers.
Extracted from line_webhook.py.
"""

import os
import re
import html as _html
import random
import threading
import requests
from datetime import datetime, timedelta
from goal_tracker import TW_TZ
from goal_tracker import (
    get_cycle_info, set_goals, get_goals, add_checkin,
    get_checkin_stats, get_checkin_log, get_today_checkins, build_summary_text,
    get_nickname, set_nickname, update_last_activity,
    log_chat_message, get_memories, get_streak,
    get_last_cycle_id, get_next_cycle_id, get_next_cycle_start,
    add_personal_memory, get_personal_memories,
    add_todo, get_todos, complete_todo_by_content,
    get_zodiac, set_zodiac, get_all_zodiacs, set_zodiac_by_nickname,
    add_quiz_score, get_quiz_scores, get_week_chat_logs,
    set_birthday_by_nickname, get_today_birthdays,
    get_all_nicknames, get_user_id_by_nickname,
)

from api_helpers import *
from weather import send_morning_greeting, _parse_date_offset, get_weather_v2
from horoscope import fetch_horoscope
from dispatch import try_dispatch

# LINE messaging configuration (local copy to avoid circular imports)
_configuration = Configuration(access_token=os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", ""))

# Replace references to the outer-scope 'configuration' with _configuration
configuration = _configuration



def handle_message(event):
    text = event.message.text.strip()
    reply_token = event.reply_token
    source = event.source
    group_id = getattr(source, 'group_id', None)
    user_id = getattr(source, 'user_id', None)

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
            reply_text = (
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

        # ── 配額說明 ──
        elif text in ("配額", "/配額", "api配額", "額度"):
            reply_text = (
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
            cycle_id, _, _ = get_cycle_info()
            goals_dict = get_goals(cycle_id)
            user_goals = goals_dict.get(member_label)
            reply_text = handle_checkin(member_label, text, user_goals)

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
        elif text in ("查目標", "看目標", "目標"):
            reply_text = handle_view_goals()

        elif text in ("今天第幾天", "幾天了", "進度", "打卡進度"):
            reply_text = handle_cycle_progress()

        elif text in ("今天打卡了嗎", "今日打卡", "誰打卡了"):
            reply_text = handle_today_checkins()

        elif text in ("上週期", "上次總結", "上輪總結"):
            reply_text = handle_last_cycle()

        elif re.match(r'^幫我想目標', text):
            reply_text = handle_suggest_goals(member_label, text)

        elif text in ("我的打卡", "打卡記錄", "我打了幾天"):
            cycle_id, day, total = get_cycle_info()
            stats = get_checkin_stats(cycle_id)
            checked = sorted(stats.get(member_label, []))
            streak = get_streak(member_label)
            if not checked:
                reply_text = f"你這週期還沒打卡喔！快去打卡 💪\n指令：打卡 今天做了XXX"
            else:
                bar = "🟩" * len(checked) + "⬜" * (total - len(checked))
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

        # ── Simple dispatch (fast path for stateless commands) ──
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

        # ── 趣味功能 ──
        elif re.search(r'今日運勢|運勢|占卜', text):
            _async_push(reply_token, "🔮 占星師施法中，請稍候...", fetch_horoscope, text)
            return

        elif re.search(r'誰請客|今天誰請|誰買單|今天誰買', text):
            reply_text = handle_who_pays(text)

        elif re.search(r'^(抽籤|幫我選|幫我決定|選一個)', text):
            reply_text = handle_draw_lots(text)

        elif re.search(r'來隻貓|貓貓|來貓', text):
            reply_image_url = fetch_cat_image()
            reply_text = random.choice(["🐱 貓貓來了！", "喵～ 🐾", "🐱 今日貓貓！"])

        elif re.search(r'來隻狗|狗狗|來狗', text):
            reply_image_url = fetch_dog_image()
            reply_text = random.choice(["🐶 狗狗來了！", "汪！🐾", "🐶 今日狗狗！"])

        elif re.search(r'狐狸|來隻狐', text):
            reply_image_url = fetch_fox_image()
            reply_text = random.choice(["🦊 狐狸來了！", "🦊 今日狐狸！", "啾啾～ 🦊"])

        elif re.search(r'柴柴|柴犬|來隻柴', text):
            reply_image_url = fetch_shiba_image()
            reply_text = random.choice(["🐕 柴柴！！", "wow such shiba 🐕", "今日柴柴！🐕"])

        elif re.search(r'熊貓|來隻熊貓', text):
            reply_image_url = fetch_animal_image("panda")
            reply_text = random.choice(["🐼 熊貓！", "今日熊貓 🐼", "圓滾滾來了 🐼"])

        elif re.search(r'無尾熊|來隻無尾熊|無尾熊', text):
            reply_image_url = fetch_animal_image("koala")
            reply_text = random.choice(["🐨 無尾熊！", "今日無尾熊 🐨", "抱抱樹 🐨"])

        elif re.search(r'浣熊|來隻浣熊', text):
            reply_image_url = fetch_animal_image("raccoon")
            reply_text = random.choice(["🦝 浣熊！", "今日浣熊 🦝", "小偷熊來了 🦝"])

        elif re.search(r'今日宇宙|NASA|宇宙照片', text):
            reply_text, reply_image_url = fetch_nasa_apod()

        elif re.search(r'抽寶可夢|今日寶可夢|來隻寶可夢', text):
            reply_text = fetch_random_pokemon()

        elif re.match(r'^配對', text):
            reply_text = handle_pairing(text)

        elif re.search(r'搖骰子|擲骰子|搖\d*[顆個]骰', text):
            reply_text = handle_dice(text)

        elif re.match(r'^猜拳', text):
            reply_text = handle_rps(text)

        elif re.search(r'給我建議|今日忠告', text):
            reply_text = fetch_advice()

        elif re.search(r'今日食譜|隨機食譜|吃什麼食譜|今天做什麼', text):
            reply_text = fetch_random_meal()

        elif re.search(r'推薦電影|今日電影|隨機電影|看什麼電影', text):
            reply_text = fetch_random_movie()

        elif re.search(r'今日(牡羊|白羊|金牛|雙子|巨蟹|獅子|處女|天秤|天蠍|射手|摩羯|水瓶|雙魚)', text):
            _async_push(reply_token, "🔮 占星師施法中，請稍候...", fetch_horoscope, text)
            return

        # ── 待辦 ──
        elif re.match(r'^提醒(我|\s)', text):
            reply_text = handle_add_todo(member_label, text)

        elif text in ("待辦", "查提醒", "查待辦"):
            reply_text = handle_view_todos()

        elif re.match(r'^完成待辦', text):
            reply_text = handle_complete_todo(member_label, text) or f"格式：完成待辦 [事項名稱]"

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
        elif m := re.match(r'^找歌\s*(.+)$', text):
            reply_text = fetch_spotify_track(m.group(1).strip())

        # ── 查電影 ──
        elif m := re.match(r'^查電影\s*(.+)$', text):
            reply_text = fetch_imdb(m.group(1).strip())

        # ── 電影台詞 ──
        elif text == "電影台詞":
            reply_text = fetch_movie_quote()

        # ── 在哪看 ──
        elif m := re.match(r'^在哪看\s*(.+)$', text):
            reply_text = fetch_streaming(m.group(1).strip())

        # ── 今日運動 / 找運動 ──
        elif re.match(r'^(今日運動|找運動)', text):
            body_part = None
            m2 = re.match(r'^找運動\s*(.+)$', text)
            if m2:
                body_part = m2.group(1).strip()
            reply_text = fetch_exercise(body_part)

        # ── 來一題（A/B/C/D 選擇題）──
        elif text == "來一題":
            question, answer, cat, wrong = "", "", "", []
            # opentdb 優先（有 wrong_answers）
            try:
                import html as _html
                r2 = requests.get("https://opentdb.com/api.php",
                                  params={"amount": 1, "type": "multiple"}, timeout=8)
                res = r2.json().get("results", [])
                if res:
                    question = _html.unescape(res[0].get("question", ""))
                    answer = _html.unescape(res[0].get("correct_answer", ""))
                    cat = res[0].get("category", "")
                    wrong = [_html.unescape(w) for w in res[0].get("incorrect_answers", [])]
            except Exception as _exc:
                print(f'[warn] {_exc}')
                pass
            # API Ninjas fallback（沒有選項，只給填空）
            if not question:
                d = _ninja("/v1/trivia")
                if d and d is not _QUOTA and isinstance(d, list):
                    question = d[0].get("question", "")
                    answer = d[0].get("answer", "")
                    cat = d[0].get("category", "")
            if question:
                q_zh = smart_translate(question)
                a_zh = smart_translate(answer) or answer
                gid = group_id or "default"
                if wrong:
                    # 建立 A/B/C/D 選項
                    choices = [answer] + wrong[:3]
                    random.shuffle(choices)
                    letters = ["A", "B", "C", "D"]
                    opts = {letters[i]: choices[i] for i in range(len(choices))}
                    correct_letter = next(k for k, v in opts.items() if v == answer)
                    # 翻譯選項
                    opts_zh = {}
                    for k, v in opts.items():
                        opts_zh[k] = smart_translate(v) or v
                    correct_zh = opts_zh[correct_letter]
                    _QUIZ_STATE[gid] = {
                        "question": q_zh or question,
                        "answer": correct_zh,
                        "correct_letter": correct_letter,
                        "options": opts_zh,
                    }
                    opts_str = "\n".join(f"  {k}. {v}" for k, v in opts_zh.items())
                    reply_text = (
                        f"🧠 來答題！（{cat}）\n\n{q_zh or question}\n\n"
                        f"{opts_str}\n\n傳「A」「B」「C」「D」作答，傳「答案」看解答"
                    )
                else:
                    _QUIZ_STATE[gid] = {"question": q_zh or question, "answer": a_zh}
                    reply_text = (
                        f"🧠 來答題！（{cat}）\n\n{q_zh or question}\n\n"
                        f"傳「答 你的答案」作答，傳「答案」看解答"
                    )
            else:
                reply_text = "🧠 題庫暫時關閉，待會再試"

        elif m := re.match(r'^([ABCD])$', text.strip().upper()):
            gid = group_id or "default"
            if gid in _QUIZ_STATE and "options" in _QUIZ_STATE[gid]:
                state = _QUIZ_STATE[gid]
                chosen = text.strip().upper()
                if chosen == state["correct_letter"]:
                    _QUIZ_STATE.pop(gid)
                    new_score = add_quiz_score(member_label)
                    reply_text = f"🎉 答對了！答案是 {state['correct_letter']}. {state['answer']}\n{member_label} 本週答對 {new_score} 題！"
                else:
                    chosen_ans = state["options"].get(chosen, chosen)
                    reply_text = f"❌ {chosen}. {chosen_ans} 不對喔，再想想！（傳「答案」放棄）"

        elif m := re.match(r'^答\s+(.+)$', text):
            gid = group_id or "default"
            if gid in _QUIZ_STATE:
                state = _QUIZ_STATE[gid]
                user_ans = m.group(1).strip().lower()
                correct = state["answer"].lower()
                if correct in user_ans or user_ans in correct:
                    _QUIZ_STATE.pop(gid)
                    new_score = add_quiz_score(member_label)
                    reply_text = f"🎉 答對了！答案是：{state['answer']}\n{member_label} 本週答對 {new_score} 題！"
                else:
                    reply_text = "❌ 不對喔，再想想！（傳「答案」放棄）"

        elif text == "答案":
            gid = group_id or "default"
            if gid in _QUIZ_STATE:
                state = _QUIZ_STATE.pop(gid)
                if "correct_letter" in state:
                    reply_text = f"💡 答案是 {state['correct_letter']}. {state['answer']}"
                else:
                    reply_text = f"💡 答案是：{state['answer']}"

        # ── 今日調酒 ──
        elif re.match(r'^今日調酒', text):
            m2 = re.match(r'^今日調酒\s*(.+)$', text)
            name = m2.group(1).strip() if m2 else None
            reply_text = fetch_cocktail(name)

        # ── 動漫語錄 ──
        elif text == "動漫語錄":
            reply_text = fetch_anime_quote()

        # ── 我好無聊 ──
        elif text == "我好無聊":
            reply_text = fetch_random_activity()

        # ── 川普語錄 ──
        elif text == "川普語錄":
            reply_text = fetch_trump_quote()

        # ── 查超英 ──
        elif m := re.match(r'^查超英\s*(.+)$', text):
            reply_text = fetch_superhero(m.group(1).strip())

        # ── 隨機梗圖 ──
        elif text == "隨機梗圖":
            meme_text, meme_url = fetch_meme()
            reply_text = meme_text
            reply_image_url = meme_url

        # ── 諾里斯 ──
        elif text == "諾里斯":
            reply_text = fetch_chuck_norris()

        # ── 摘要 ──
        elif m := re.match(r'^摘要\s*(.+)$', text, re.DOTALL):
            reply_text = fetch_tldr(m.group(1).strip())

        # ── 國家資訊 ──
        elif m := re.match(r'^國家\s*(.+)$', text):
            reply_text = fetch_country(m.group(1).strip())

        # ── 找書 ──
        elif m := re.match(r'^找書\s*(.+)$', text):
            reply_text = fetch_book(m.group(1).strip())

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
        elif m := re.match(r'^查日文\s*(.+)$', text):
            reply_text = fetch_jisho(m.group(1).strip())

        elif text in ("今日日文單字", "日文單字", "學日文"):
            reply_text = fetch_daily_japanese()

        elif m := re.match(r'^漢字\s*([^\s])$', text):
            reply_text = fetch_kanji(m.group(1))

        elif text in ("今日漢字", "學漢字"):
            reply_text = fetch_daily_kanji()

        # ── 西班牙文字典 ──
        elif m := re.match(r'^查西文\s*(.+)$', text):
            reply_text = fetch_spanish(m.group(1).strip())

        elif text in ("今日西文單字", "西文單字", "學西文"):
            reply_text = fetch_daily_spanish()

        # ── 日文問題 ──
        elif (jp := handle_japanese_question(text)):
            reply_text = jp

        # ── 關鍵字回覆（隨機觸發避免煩人）──
        elif re.search(r'冷笑話', text):
            reply_text = handle_joke(text)

        elif re.search(r'冷知識', text):
            reply_text = handle_fun_fact(text)

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
        elif m := re.match(r'^熱量\s+(.+)$', text):
            reply_text = fetch_nutrition(m.group(1).strip())

        # ── 消耗熱量 ──
        elif m := re.match(r'^消耗熱量\s+(.+?)(?:\s+(\d+)分鐘?)?$', text):
            activity = m.group(1).strip()
            duration = int(m.group(2)) if m.group(2) else 30
            reply_text = fetch_calories_burned(activity, duration)

        # ── 金價 ──
        elif text in ("金價", "今日金價", "黃金價格"):
            reply_text = fetch_gold_price()

        # ── 天文冷知識 ──
        elif text in ("天文冷知識", "科學冷知識", "宇宙冷知識"):
            reply_text = fetch_astronomy_fact()

        # ── 數字冷知識 ──
        elif text in ("數字冷知識", "數字趣聞"):
            reply_text = fetch_number_fact()

        # ── 食譜 [食材] ──
        elif m := re.match(r'^食譜\s+(.+)$', text):
            reply_text = fetch_recipe_by_ingredient(m.group(1).strip())

        # ── QR Code ──
        elif m := re.match(r'^QR\s+(.+)$', text, re.IGNORECASE):
            url_or_text = m.group(1).strip()
            qr_text, qr_img = fetch_qr_code(url_or_text)
            reply_text = qr_text
            reply_image_url = qr_img

        # ── 縮網址 ──
        elif m := re.match(r'^縮網址\s+(.+)$', text):
            reply_text = shorten_url(m.group(1).strip())

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

        elif text == "配對星座":
            reply_text = "請傳「配對星座 星座1 星座2」\n例：配對星座 天蠍 金牛"

        # ── 新聞 ──
        elif text in ("新聞", "今日新聞", "最新新聞"):
            reply_text = fetch_news()

        # ── 投票 ──
        elif m := re.match(r'^投票\s+(.+?)(?:\s{1,2}|\s*[,，]\s*)(.+)$', text):
            gid = group_id or "default"
            question = m.group(1).strip()
            raw_opts = re.split(r'[\s,，]+', m.group(2).strip())
            opts = [o for o in raw_opts if o][:4]
            if len(opts) >= 2:
                _VOTE_STATE[gid] = {
                    "question": question,
                    "options": opts,
                    "votes": {},
                    "ts": datetime.now(TW_TZ).isoformat(),
                }
                letters = ["A", "B", "C", "D"]
                opts_str = "\n".join(f"  {letters[i]}. {opts[i]}" for i in range(len(opts)))
                reply_text = (
                    f"📊 投票開始！\n\n{question}\n\n{opts_str}\n\n"
                    f"傳「投A」「投B」... 投票，傳「投票結果」查看"
                )
            else:
                reply_text = "格式：投票 問題 選項1 選項2 選項3\n例：投票 週末去哪 北部 中部 南部"

        elif m := re.match(r'^投([ABCD])$', text.strip().upper()):
            gid = group_id or "default"
            if gid in _VOTE_STATE:
                state = _VOTE_STATE[gid]
                chosen = m.group(1)
                idx = ord(chosen) - ord("A")
                if idx < len(state["options"]):
                    state["votes"][member_label] = chosen
                    opt_name = state["options"][idx]
                    reply_text = f"✅ {member_label} 投了 {chosen}. {opt_name}"
                    # 投票數達到成員數時自動結算
                    if len(state["votes"]) >= max(2, len(MEMBERS)):
                        from collections import Counter
                        cnt = Counter(state["votes"].values())
                        winner_letter = cnt.most_common(1)[0][0]
                        winner_idx = ord(winner_letter) - ord("A")
                        winner_name = state["options"][winner_idx]
                        detail = " / ".join(f"{k}: {v}票" for k, v in sorted(cnt.items()))
                        reply_text += f"\n\n🎉 全員投票完畢！\n結果：{winner_letter}. {winner_name} 勝出\n（{detail}）"
                        _VOTE_STATE.pop(gid)

        elif text == "投票結果":
            gid = group_id or "default"
            if gid in _VOTE_STATE:
                state = _VOTE_STATE[gid]
                from collections import Counter
                cnt = Counter(state["votes"].values())
                lines = [f"📊 {state['question']} 目前票數："]
                letters = ["A", "B", "C", "D"]
                for i, opt in enumerate(state["options"]):
                    letter = letters[i]
                    votes = cnt.get(letter, 0)
                    bar = "█" * votes + "░" * (len(MEMBERS) - votes)
                    who = [n for n, v in state["votes"].items() if v == letter]
                    lines.append(f"  {letter}. {opt}｜{bar} {votes}票 {('（' + '、'.join(who) + '）') if who else ''}")
                reply_text = "\n".join(lines)
            else:
                reply_text = "目前沒有進行中的投票"

        elif text == "取消投票":
            gid = group_id or "default"
            _VOTE_STATE.pop(gid, None)
            reply_text = "投票已取消"

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
                _REMOVE_BG_PENDING.add(user_id)
                reply_text = "請傳一張圖片，我幫你去背 🖼️"

        # ── 待翻譯輸入 ──
        elif user_id and user_id in _TRANSLATE_PENDING:
            if text in ("取消", "算了", "不翻了"):
                _TRANSLATE_PENDING.pop(user_id, None)
                reply_text = "好，取消翻譯 👌"
            else:
                lang_code = _TRANSLATE_PENDING.pop(user_id)
                reply_text = translate_text(text, lang_code)

        # ── 被點名 ──
        elif (BOT_NAME in text or BOT_DISPLAY_NAME in text
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
        line_bot_api.reply_message(
            ReplyMessageRequest(reply_token=reply_token, messages=messages)
        )


def handle_audio(event):
    """Shazam: identify song from audio message."""
    reply_token = event.reply_token
    with ApiClient(configuration) as api_client:
        try:
            blob_api = MessagingApiBlob(api_client)
            audio_bytes = blob_api.get_message_content(event.message.id)
        except Exception as _exc:
            print(f'[warn] {_exc}')
            return
        # Reply immediately, push result after Shazam finishes
        MessagingApi(api_client).reply_message(
            ReplyMessageRequest(reply_token=reply_token,
                                messages=[TextMessage(text="🎵 辨識中，請稍候...")])
        )
    def _run():
        result = shazam_identify(audio_bytes)
        _push_messages([{"type": "text", "text": result}])
    threading.Thread(target=_run, daemon=True).start()


def handle_image(event):
    """NSFW detection + background removal for image messages."""
    reply_token = event.reply_token
    user_id = event.source.user_id if hasattr(event.source, "user_id") else None

    with ApiClient(configuration) as api_client:
        try:
            blob_api = MessagingApiBlob(api_client)
            img_bytes = blob_api.get_message_content(event.message.id)
        except Exception as _exc:
            print(f'[warn] {_exc}')
            return

        line_api = MessagingApi(api_client)

        # ── 去背 pending flow ──
        if user_id and user_id in _REMOVE_BG_PENDING:
            _REMOVE_BG_PENDING.discard(user_id)
            line_api.reply_message(ReplyMessageRequest(
                reply_token=reply_token,
                messages=[TextMessage(text="🖼️ 去背處理中，請稍候...")],
            ))
            def _do_remove_bg():
                result_url = remove_background(img_bytes)
                if result_url:
                    _push_messages([
                        {"type": "text", "text": "✅ 去背完成！"},
                        {"type": "image",
                         "originalContentUrl": result_url,
                         "previewImageUrl": result_url},
                    ])
                else:
                    _push_messages([{"type": "text", "text": "去背失敗，請稍後再試 😢"}])
            threading.Thread(target=_do_remove_bg, daemon=True).start()
            return

        # ── NSFW 自動偵測 ──
        try:
            is_nsfw = check_nsfw(img_bytes)
        except Exception as _exc:
            is_nsfw = False

        if is_nsfw:
            line_api.reply_message(ReplyMessageRequest(
                reply_token=reply_token,
                messages=[TextMessage(text="⚠️ 偵測到不雅圖片，小棉襖不允許這種內容喔！")],
            ))


def handle_join(event):
    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(
                    text="歡迎加入！我是小棉襖 🧸\n輸入「叫我XXX」先綁定你的暱稱！"
                )]
            )
        )
