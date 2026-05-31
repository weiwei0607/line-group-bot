import os
import re
import random
import threading
import requests
from flask import Flask, request, abort
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    Configuration, ApiClient, MessagingApi,
    ReplyMessageRequest, TextMessage
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent, MemberJoinedEvent

from goal_tracker import (
    get_cycle_info, set_goals, get_goals, add_checkin,
    get_checkin_stats, get_checkin_log, get_today_checkins, build_summary_text,
    get_nickname, set_nickname, update_last_activity,
    log_chat_message, get_memories, get_streak,
    get_last_cycle_id, add_personal_memory, get_personal_memories,
    GOAL_SHEET_ID, _get_token, _sheets_get, _sheets_append, _sheets_update
)

app = Flask(__name__)

CHANNEL_SECRET = os.environ["LINE_CHANNEL_SECRET"]
CHANNEL_ACCESS_TOKEN = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
BOT_NAME = os.environ.get("LINE_BOT_NAME", "日文小老師")
BOT_DISPLAY_NAME = os.environ.get("LINE_BOT_DISPLAY_NAME", "毛毛毛毛太后的小棉襖")
MEMBERS = ["太后", "毛毛", "二毛"]

handler = WebhookHandler(CHANNEL_SECRET)
configuration = Configuration(access_token=CHANNEL_ACCESS_TOKEN)

# Pure commands that carry no personal content worth memorising
_SKIP_LOG = {
    "查目標", "看目標", "目標", "今天第幾天", "幾天了", "進度", "打卡進度",
    "今天打卡了嗎", "今日打卡", "誰打卡了", "我的打卡", "打卡記錄", "我打了幾天",
    "今日運勢", "運勢", "占卜",
    "誰請客", "今天誰請", "誰買單", "今天誰買",
    "冷笑話", "冷知識", "上週期", "總結",
}


def _should_log(text: str) -> bool:
    if text.startswith("!"):
        return False
    if text in _SKIP_LOG:
        return False
    if re.match(r'^(叫我|設目標[：:]|抽籤|幫我選|幫我決定|選一個|幫我想目標)', text):
        return False
    return True


# ─── Gemini ───────────────────────────────────────────────

def call_gemini(prompt):
    if not GEMINI_API_KEY:
        return None
    try:
        url = (f"https://generativelanguage.googleapis.com/v1beta/"
               f"models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}")
        resp = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=12)
        return resp.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception:
        return None


# ─── LINE helpers ─────────────────────────────────────────

def get_display_name(api_client, group_id, user_id):
    try:
        line_bot_api = MessagingApi(api_client)
        profile = line_bot_api.get_group_member_profile(group_id, user_id)
        return profile.display_name
    except Exception:
        return "某人"


def get_member_label(api_client, group_id, user_id):
    nick = get_nickname(user_id)
    if nick:
        return nick
    return get_display_name(api_client, group_id, user_id)


# ─── Weather ──────────────────────────────────────────────

TW_CITIES = ["台北", "新北", "桃園", "台中", "台南", "高雄",
             "基隆", "花蓮", "台東", "宜蘭", "嘉義", "新竹"]


def get_weather(text):
    city = "台北"
    for c in TW_CITIES:
        if c in text:
            city = c
            break
    try:
        resp = requests.get(f"https://wttr.in/{city}?format=3&lang=zh&m", timeout=8)
        return f"🌤 {resp.text.strip()}\n（資料來源：wttr.in）"
    except Exception:
        return f"天氣查詢失敗，去 Google 查 {city} 天氣吧 😅"


# ─── Exchange rate ────────────────────────────────────────

def get_exchange_rate(text):
    pairs = [("USD", "美金", "$"), ("JPY", "日幣", "¥"),
             ("EUR", "歐元", "€"), ("KRW", "韓元", "₩")]
    targets = [p for p in pairs if p[1] in text or p[0] in text.upper()]
    if not targets:
        targets = [("USD", "美金", "$"), ("JPY", "日幣", "¥")]
    try:
        lines = ["💱 即時匯率（對台幣）\n"]
        for code, name, symbol in targets:
            resp = requests.get(f"https://open.er-api.com/v6/latest/{code}", timeout=8)
            twd = resp.json()["rates"].get("TWD", 0)
            if code == "JPY":
                lines.append(f"  {symbol} 100 {name} = {twd*100:.2f} 台幣")
            else:
                lines.append(f"  {symbol} 1 {name} = {twd:.2f} 台幣")
        return "\n".join(lines)
    except Exception:
        return "匯率查詢失敗 😢 試試 Google 匯率"


# ─── Countdown ────────────────────────────────────────────

def handle_countdown(text):
    from datetime import date as date_type
    from goal_tracker import _now, TW_TZ
    now = _now()

    patterns = [
        (r'(\d{4})[/\-.](\d{1,2})[/\-.](\d{1,2})', 3),
        (r'(\d{1,2})[/\-.](\d{1,2})', 2),
    ]
    target = None
    label = ""
    for pattern, groups in patterns:
        match = re.search(pattern, text)
        if match:
            g = match.groups()
            try:
                if groups == 3:
                    target = date_type(int(g[0]), int(g[1]), int(g[2]))
                    label = f"{g[1]}/{g[2]}"
                else:
                    y = now.year
                    m, d = int(g[0]), int(g[1])
                    target = date_type(y, m, d)
                    if target < now.date():
                        target = date_type(y + 1, m, d)
                    label = f"{m}/{d}"
            except ValueError:
                pass
            break

    if not target:
        return "格式：倒數 2026/12/25 或 倒數 12/25"

    days = (target - now.date()).days
    if days < 0:
        return f"那天已經過了 {abs(days)} 天 😅"
    elif days == 0:
        return "🎉 就是今天！！衝啊！"
    elif days == 1:
        return f"📅 距離 {label} 剩 1 天！明天到了！！"
    else:
        return f"📅 距離 {label} 還有 {days} 天"


# ─── Fortune ─────────────────────────────────────────────

def handle_fortune():
    prompt = (
        f"請為以下三個人各生成一則今日運勢，人名：太后、毛毛、二毛。\n"
        f"風格：輕鬆有趣，像星座運勢但更貼近生活，每人 2-3 句話。\n"
        f"格式：\n"
        f"🔮 太后：...\n"
        f"🔮 毛毛：...\n"
        f"🔮 二毛：...\n"
        f"不要太正面或太負面，要有趣、可信、帶一個小建議。"
    )
    result = call_gemini(prompt)
    return result or "🔮 今日運勢：系統占卜中...水晶球破掉了 😅"


# ─── Who pays ────────────────────────────────────────────

def handle_who_pays(text):
    chosen = random.choice(MEMBERS)
    phrases = [
        f"🎰 轉啊轉啊轉... 今天是【{chosen}】！請客！💸",
        f"🎯 命運之輪選中了...{chosen}！不許賴帳 😈",
        f"✨ 今天的幸運請客人是...{chosen}！恭喜恭喜 🎉",
    ]
    return random.choice(phrases)


# ─── Draw lots ───────────────────────────────────────────

def handle_draw_lots(text):
    after = re.sub(r'^(抽籤|幫我選|幫我決定|選一個)\s*', '', text).strip()
    if after:
        options = [o.strip() for o in re.split(r'[/、\s,，]+', after) if o.strip()]
        if len(options) >= 2:
            chosen = random.choice(options)
            return f"🎋 抽籤結果：【{chosen}】！就這個！不准後悔 😤"
    choices = ["✅ 可以！去吧！", "❌ 不行！再想想！", "🤔 再考慮一下", "😅 隨便啦你決定", "🎲 丟銅板決定吧"]
    return f"🎋 {random.choice(choices)}"


# ─── Auto-reply handlers ─────────────────────────────────

def handle_joke(_):
    return call_gemini(
        "請說一個台灣年輕人喜歡的冷笑話，有笑點但很冷，繁體中文，不超過 4 行，最後加一個 😂"
    ) or "😅 ...笑了嗎（靜音）"


def handle_fun_fact(_):
    return call_gemini(
        "請分享一個有趣的冷知識，讓人覺得「認真嗎！？」，繁體中文，不超過 3 句，開頭加 🤯"
    ) or "🤯 我腦袋當機了，明天再說"


def handle_leave(_):
    return random.choice([
        "要去哪裡玩！！快分享行程 🌴",
        "假期來了！去吃好吃的記得帶我 😤",
        "終於可以休息了！計畫好要幹嘛了嗎 ✨",
        "放假最幸福了，好好充電 🔋",
    ])


def handle_overtime(_):
    return random.choice([
        "辛苦了... 快點做完早點回家 🥺",
        "加班人要補充糖分！！去喝一杯奶茶 🧋",
        "加班是暫時的，下班是永遠的 💪",
        "撐著！今天的加班今天過 😤",
    ])


def handle_tired(_):
    return random.choice([
        "累了就休息，你已經很努力了 🫶",
        "放下手機睡一覺吧，明天繼續 💤",
        "喝杯水 + 躺平 10 分鐘，馬上好一半 🧘",
        "累是有原因的，代表你有在用力活著 ✨",
    ])


def handle_food(_):
    return call_gemini(
        "有人不知道要吃什麼，請給一個有趣的台灣美食推薦，"
        "語氣像在跟朋友聊天，輕鬆幽默，不超過 3 句，加 1 個 emoji"
    ) or "去吃火鍋吧！百吃不厭 🍲"


def handle_travel(text):
    return call_gemini(
        f"有人說「{text}」，他可能要出去玩了。"
        "請給輕鬆有趣的旅遊溫馨提示，台灣年輕人語氣，3 條，加 emoji"
    ) or "出去玩記得：充電寶🔋 防曬🌞 訂位📱"


def handle_japanese_question(text):
    for pattern in [r"(.+?)日文怎麼說", r"(.+?)用日文怎麼說",
                    r"日文(.+?)怎麼說", r"(.+?)日語怎麼說"]:
        match = re.search(pattern, text)
        if match:
            word = match.group(1).strip()
            return call_gemini(
                f"有人問「{word}」日文怎麼說，請用以下格式回答（簡短）：\n"
                f"「{word}」的日文是：日文（假名）\n例句：一句日文\n翻譯：中文翻譯\n"
                f"語氣輕鬆友善。"
            )
    return None


def handle_mention(text, member=None):
    group_mems = get_memories(days=14)
    personal_mems = get_personal_memories(member, days=14) if member else []

    context = ""
    if group_mems:
        context += "【群組近況】\n"
        context += "\n".join(f"[{d}] {c}" for d, c in group_mems)
        context += "\n\n"
    if personal_mems:
        context += f"【{member} 的個人記憶】\n"
        context += "\n".join(f"[{d}] {c}" for d, c in personal_mems)
        context += "\n\n"

    return call_gemini(
        f"你是一個活潑有趣的朋友群 LINE 機器人，名字叫「小棉襖」。\n"
        f"{context}"
        f"{'傳訊息的是 ' + member + '，' if member else ''}"
        f"他說：「{text}」\n"
        f"請結合你對這個群組和這個人的了解，用台灣年輕人語氣回應，輕鬆幽默，不超過 3 句。"
        f"不要加『大家好』或自我介紹。"
    )


def handle_suggest_goals(member, text):
    topic = re.sub(r'^幫我想目標\s*', '', text).strip()
    cycle_id, day, total = get_cycle_info()
    current = get_goals(cycle_id).get(member, [])
    memories = get_memories(days=7)

    context_parts = []
    if current:
        context_parts.append(f"{member} 目前設的目標：{' / '.join(current)}")
    if topic:
        context_parts.append(f"方向偏好：{topic}")
    if memories:
        context_parts.append("群組最近狀況：" + "；".join(c[:80] for _, c in memories[-3:]))
    context = "\n".join(context_parts)

    result = call_gemini(
        f"請幫{member}想 3-5 個適合十天週期的個人目標。\n"
        f"{context}\n\n"
        "要求：具體可執行、適合每天打卡確認、有挑戰性但不過分。\n"
        "例如：每天走 6000 步、每天背 5 個單字、每天記帳、每天做 10 分鐘伸展\n"
        "格式：每行一個目標加 emoji，結尾附一行提醒如何設定。"
    )
    suffix = "\n\n💡 設定指令：設目標：目標1 / 目標2 / 目標3"
    return (result or "建議：\n💧 每天喝 2000ml 水\n🚶 每天走 6000 步\n📚 每天讀 20 分鐘書") + suffix


def handle_last_cycle():
    last_id = get_last_cycle_id()
    return build_summary_text(last_id)


# ─── Goal command handlers ────────────────────────────────

def parse_goals(text_after_prefix):
    text = re.sub(r'^\d+[.、]\s*', '', text_after_prefix.strip(), flags=re.MULTILINE)
    parts = re.split(r'[\n/、；;]+', text)
    return [p.strip() for p in parts if p.strip()][:5]


def handle_set_goals(member, text):
    after = re.sub(r'^設目標[：:]\s*', '', text).strip()
    if not after:
        return "目標內容不能空白！\n格式：設目標：目標1 / 目標2 / 目標3"

    goals = parse_goals(after)
    if not goals:
        return "沒讀到目標，試試：設目標：目標1 / 目標2 / 目標3"

    cycle_id, day, total = get_cycle_info()

    def _do_set():
        set_goals(member, goals)
    threading.Thread(target=_do_set, daemon=True).start()

    goals_preview = "\n".join(f"  {i+1}. {g}" for i, g in enumerate(goals))
    return (
        f"✅ {member} 的十日目標設定完成！\n\n"
        f"{goals_preview}\n\n"
        f"📅 現在第 {day}/{total} 天，加油！💪"
    )


def handle_checkin(member, text, user_goals=None):
    content = re.sub(r'^打卡\s*', '', text).strip() or "打卡"
    day, total = add_checkin(member, content)
    if day == 0:
        return "打卡失敗 😢 等一下再試試？"

    streak = get_streak(member)
    streak_msg = ""
    if streak >= 7:
        streak_msg = f"🔥🔥 連續 {streak} 天！神人！"
    elif streak >= 5:
        streak_msg = f"🔥 連續 {streak} 天！太厲害了！"
    elif streak >= 3:
        streak_msg = f"🔥 連續 {streak} 天！繼續衝！"
    elif streak == 2:
        streak_msg = "連兩天了！保持！"

    if user_goals:
        goals_str = " / ".join(user_goals[:3])
        enc = call_gemini(
            f"有人叫{member}，他的十日目標是：{goals_str}。\n"
            f"今天他打卡說：「{content}」（第{day}/{total}天）\n"
            f"請給一句輕鬆真誠的鼓勵，提到他的目標，台灣年輕人語氣，不超過 2 句。"
        )
        if not enc:
            enc = random.choice(["太棒了！繼續保持！", "你最行！衝衝衝！", "很好！繼續！"])
    else:
        enc = random.choice(["太棒了！", "繼續保持！", "你最行！", "衝衝衝！", "很好！"])

    # 用實際打卡天數畫進度條，不用「今天第幾天」
    stats = get_checkin_stats()
    checked_count = len(stats.get(member, []))
    bar = "🟩" * checked_count + "⬜" * max(0, total - checked_count)
    parts = [f"✅ {member} 打卡成功！", f"📝 {content}", bar, f"第 {day}/{total} 天｜{enc}"]
    if streak_msg:
        parts.append(streak_msg)
    return "\n".join(parts)


def _goal_keyword(goal: str) -> str:
    """Extract core keyword from goal like '每天duolingo' → 'duolingo'."""
    key = re.sub(r'^每天\s*', '', goal).strip()
    key = re.sub(r'一件東西$|一次$|一下$|一個$', '', key).strip()
    return key or goal


def _goal_days(log: dict, goal: str, total: int) -> int:
    """Count days where any check-in content mentions the goal keyword."""
    kw = _goal_keyword(goal).lower()
    count = 0
    for contents in log.values():
        if any(kw in c.lower() for c in contents):
            count += 1
    return count


def handle_view_goals():
    cycle_id, day, total = get_cycle_info()
    goals = get_goals(cycle_id)
    stats = get_checkin_stats(cycle_id)

    if not goals and not stats:
        return (
            f"本週期（第 {day}/{total} 天）還沒有人設目標！\n\n"
            f"輸入：設目標：目標1 / 目標2 / 目標3\n"
            f"可設 3–5 個目標"
        )

    log = get_checkin_log(cycle_id)
    lines = [f"🎯 本週期目標（第 {day}/{total} 天）\n"]
    all_members = sorted(set(list(goals.keys()) + list(stats.keys())))
    for member in all_members:
        member_goals = goals.get(member, [])
        member_log = log.get(member, {})
        lines.append(f"👤 {member}")
        if member_goals:
            for g in member_goals:
                cnt = _goal_days(member_log, g, total)
                bar = "🟩" * cnt + "⬜" * max(0, total - cnt)
                kw = _goal_keyword(g)
                lines.append(f"  {kw}｜{bar} {cnt}/{total}")
        else:
            checked = stats.get(member, [])
            bar = "🟩" * len(checked) + "⬜" * max(0, total - len(checked))
            lines.append(f"  打卡｜{bar} {len(checked)}/{total}")
        lines.append("")
    return "\n".join(lines).strip()


def handle_cycle_progress():
    cycle_id, day, total = get_cycle_info()
    stats = get_checkin_stats(cycle_id)
    lines = [f"📅 十日週期第 {day}/{total} 天\n"]
    if stats:
        for member, days in sorted(stats.items()):
            bar = "🟩" * len(days) + "⬜" * (total - len(days))
            lines.append(f"{member}：{bar} {len(days)}/{total}")
    else:
        lines.append("還沒有人打卡 😶")
    return "\n".join(lines)


def handle_today_checkins():
    cycle_id, day, total = get_cycle_info()
    checkins = get_today_checkins(cycle_id)
    if not checkins:
        return f"今天（第 {day} 天）還沒有人打卡！快去打卡 💪"
    lines = [f"📋 今日打卡（第 {day}/{total} 天）\n"]
    for member, content in checkins.items():
        lines.append(f"✅ {member}：{content}")
    # Check who hasn't checked in
    goals = get_goals(cycle_id)
    missing = [m for m in goals if m not in checkins]
    if missing:
        lines.append(f"\n還沒打卡：{' / '.join(missing)} 快去！")
    return "\n".join(lines)


# ─── Main message handler ─────────────────────────────────

@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers.get("X-Line-Signature", "")
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return "OK"


@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    text = event.message.text.strip()
    reply_token = event.reply_token
    source = event.source
    group_id = getattr(source, 'group_id', None)
    user_id = getattr(source, 'user_id', None)

    # Update last activity + log message in background
    threading.Thread(target=update_last_activity, daemon=True).start()

    reply_text = None

    with ApiClient(configuration) as api_client:
        member_label = get_member_label(api_client, group_id, user_id)
        if _should_log(text):
            threading.Thread(
                target=log_chat_message, args=(member_label, text), daemon=True
            ).start()

        # ── 隱藏指令 ──
        if text == "!groupid":
            reply_text = f"Group ID: {group_id or '非群組訊息'}"

        # ── 暱稱登記 ──
        elif nick_match := re.match(r'^叫我\s*(.+)$', text):
            nickname = nick_match.group(1).strip()
            ok = set_nickname(user_id, nickname)
            reply_text = f"好的！之後叫你「{nickname}」了 👋" if ok else "登記失敗，等一下再試 😢"

        # ── 十日目標：設目標 ──
        elif re.match(r'^設目標[：:]', text):
            reply_text = handle_set_goals(member_label, text)

        # ── 十日目標：打卡 ──
        elif text.startswith("打卡"):
            cycle_id, _, _ = get_cycle_info()
            goals_dict = get_goals(cycle_id)
            user_goals = goals_dict.get(member_label)
            reply_text = handle_checkin(member_label, text, user_goals)

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

        # ── 趣味功能 ──
        elif re.search(r'今日運勢|運勢|占卜', text):
            reply_text = handle_fortune()

        elif re.search(r'誰請客|今天誰請|誰買單|今天誰買', text):
            reply_text = handle_who_pays(text)

        elif re.search(r'^(抽籤|幫我選|幫我決定|選一個)', text):
            reply_text = handle_draw_lots(text)

        elif re.search(r'倒數|還有幾天|距離', text):
            reply_text = handle_countdown(text)

        # ── 實用功能 ──
        elif re.search(r'匯率|美金|日幣|換錢|外幣', text):
            reply_text = get_exchange_rate(text)

        elif re.search(r'天氣', text):
            reply_text = get_weather(text)

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

        # ── 被點名 ──
        elif (BOT_NAME in text or BOT_DISPLAY_NAME in text
              or "機器人" in text or "小棉襖" in text or "bot" in text.lower()):
            reply_text = handle_mention(text, member=member_label)

        if not reply_text:
            return

        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=reply_token,
                messages=[TextMessage(text=reply_text)]
            )
        )


@handler.add(MemberJoinedEvent)
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


@app.route("/health")
def health():
    return "OK"


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
