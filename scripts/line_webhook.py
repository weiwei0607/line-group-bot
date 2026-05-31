import os
import re
import random
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
    get_checkin_stats, build_summary_text, GOAL_SHEET_ID,
    _get_token, _sheets_get, _sheets_append, _sheets_update
)

app = Flask(__name__)

CHANNEL_SECRET = os.environ["LINE_CHANNEL_SECRET"]
CHANNEL_ACCESS_TOKEN = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
BOT_NAME = os.environ.get("LINE_BOT_NAME", "日文小老師")

handler = WebhookHandler(CHANNEL_SECRET)
configuration = Configuration(access_token=CHANNEL_ACCESS_TOKEN)


# ─── Gemini ───────────────────────────────────────────────

def call_gemini(prompt):
    if not GEMINI_API_KEY:
        return None
    try:
        url = (f"https://generativelanguage.googleapis.com/v1beta/"
               f"models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}")
        resp = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=10)
        return resp.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception:
        return None


# ─── LINE member helpers ───────────────────────────────────

def get_display_name(api_client, group_id, user_id):
    try:
        line_bot_api = MessagingApi(api_client)
        profile = line_bot_api.get_group_member_profile(group_id, user_id)
        return profile.display_name
    except Exception:
        return "某人"


def get_nickname(user_id):
    """Check if user has registered a nickname in Sheets."""
    if not GOAL_SHEET_ID:
        return None
    try:
        token = _get_token()
        rows = _sheets_get(token, "暱稱!A:B")
        for row in rows[1:]:
            if len(row) >= 2 and row[0] == user_id:
                return row[1]
        return None
    except Exception:
        return None


def set_nickname(user_id, nickname):
    if not GOAL_SHEET_ID:
        return False
    try:
        token = _get_token()
        rows = _sheets_get(token, "暱稱!A:B")
        for i, row in enumerate(rows[1:], 2):
            if len(row) >= 1 and row[0] == user_id:
                _sheets_update(token, f"暱稱!B{i}", [[nickname]])
                return True
        _sheets_append(token, "暱稱!A:B", [[user_id, nickname]])
        return True
    except Exception:
        return False


def get_member_label(api_client, group_id, user_id):
    """Get nickname if registered, else LINE display name."""
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
        resp = requests.get(
            f"https://wttr.in/{city}?format=3&lang=zh&m", timeout=8
        )
        weather = resp.text.strip()
        return f"🌤 {weather}\n（資料來源：wttr.in）"
    except Exception:
        return f"天氣查詢失敗，去 Google 查 {city} 天氣吧 😅"


# ─── Auto-reply handlers ───────────────────────────────────

def handle_joke(_text):
    result = call_gemini(
        "請說一個台灣年輕人喜歡的冷笑話，有笑點但很冷，"
        "用繁體中文，不超過 4 行，最後可以加一個 😂"
    )
    return result or "😅 ...笑了嗎（靜音）"


def handle_fun_fact(_text):
    result = call_gemini(
        "請分享一個有趣的冷知識，讓人覺得「認真嗎！？」，"
        "用繁體中文，不超過 3 句，開頭加 🤯"
    )
    return result or "🤯 我腦袋當機了，明天再說一個"


def handle_leave(_text):
    replies = [
        "要去哪裡玩！！快分享行程 🌴",
        "假期來了！去吃好吃的記得帶我 😤",
        "終於可以休息了！計畫好要幹嘛了嗎 ✨",
        "放假最幸福了，好好充電 🔋",
    ]
    return random.choice(replies)


def handle_overtime(_text):
    replies = [
        "辛苦了... 快點做完早點回家 🥺",
        "加班人要補充糖分！！去喝一杯奶茶 🧋",
        "加班是暫時的，下班是永遠的 💪",
        "撐著！今天的加班今天過 😤",
    ]
    return random.choice(replies)


def handle_tired(_text):
    replies = [
        "累了就休息，你已經很努力了 🫶",
        "放下手機睡一覺吧，明天繼續 💤",
        "累是有原因的，代表你有在用力活著 ✨",
        "喝杯水 + 躺平 10 分鐘，馬上好一半 🧘",
    ]
    return random.choice(replies)


def handle_food(_text):
    result = call_gemini(
        "有人不知道要吃什麼，請給一個有趣的台灣美食或餐廳類型推薦，"
        "語氣像在跟朋友聊天，輕鬆幽默，不超過 3 句，加 1 個 emoji"
    )
    return result or "去吃火鍋吧！百吃不厭 🍲"


def handle_travel(text):
    result = call_gemini(
        f"有人說「{text}」，他可能要出去玩了。"
        "請給一些輕鬆有趣的旅遊溫馨提示（充電寶、天氣、訂位等），"
        "台灣年輕人語氣，3 條，加 emoji"
    )
    return result or "出去玩記得：充電寶🔋 防曬🌞 訂位📱"


def handle_japanese_question(text):
    patterns = [
        r"(.+?)日文怎麼說",
        r"(.+?)用日文怎麼說",
        r"日文(.+?)怎麼說",
        r"(.+?)日語怎麼說",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            word = match.group(1).strip()
            prompt = (
                f"有人問「{word}」日文怎麼說，請用以下格式回答（簡短）：\n"
                f"「{word}」的日文是：日文（假名）\n"
                f"例句：一個簡單的日文句子\n"
                f"翻譯：中文翻譯\n\n"
                f"語氣輕鬆友善，像朋友在聊天。"
            )
            return call_gemini(prompt)
    return None


def handle_mention(text):
    prompt = (
        f"你是一個活潑有趣的朋友群 LINE 機器人，名字叫「{BOT_NAME}」。"
        f"有人在群組裡傳了這段話：「{text}」\n\n"
        f"請用台灣年輕人語氣回應，輕鬆幽默，不超過 3 句。"
        f"如果訊息跟日文有關，可以順便教一個單字。"
        f"不要加『大家好』或自我介紹。"
    )
    return call_gemini(prompt)


# ─── Goal command handlers ────────────────────────────────

def parse_goals(text_after_prefix):
    """Parse 3–5 goals from text. Supports: line breaks, /, 、, numbered lists."""
    text = text_after_prefix.strip()
    # Remove numbered prefixes like "1. " or "1、"
    text = re.sub(r'^\d+[.、]\s*', '', text, flags=re.MULTILINE)
    # Split by newline, /, 、, semicolon
    parts = re.split(r'[\n/、；;]+', text)
    goals = [p.strip() for p in parts if p.strip()]
    return goals[:5]  # max 5


def handle_set_goals(member, text):
    """Handle 設目標：... command. Returns reply string."""
    after = re.sub(r'^設目標[：:]\s*', '', text).strip()
    if not after:
        return "目標內容不能空白！格式：設目標：目標1 / 目標2 / 目標3"

    goals = parse_goals(after)
    if len(goals) < 1:
        return "沒讀到目標，試試：設目標：目標1 / 目標2 / 目標3"

    cycle_id, day, total = get_cycle_info()
    ok = set_goals(member, goals)
    if not ok:
        return "目標儲存失敗 😢 等一下再試試？"

    goals_preview = "\n".join(f"  {i+1}. {g}" for i, g in enumerate(goals))
    return (
        f"✅ {member} 的十日目標已設定！\n\n"
        f"{goals_preview}\n\n"
        f"目前是第 {day}/{total} 天，加油！💪"
    )


def handle_checkin(member, text):
    """Handle 打卡 ... command. Returns reply string."""
    content = re.sub(r'^打卡\s*', '', text).strip()
    if not content:
        content = "打卡"

    day, total = add_checkin(member, content)
    if day == 0:
        return "打卡失敗 😢 等一下再試試？"

    encouragements = ["太棒了！", "繼續保持！", "你最行！", "衝衝衝！"]
    enc = random.choice(encouragements)
    return (
        f"✅ {member} 打卡成功！{enc}\n"
        f"📝 {content}\n"
        f"📅 今天第 {day}/{total} 天"
    )


def handle_view_goals():
    """Handle 查目標 command. Returns reply string."""
    cycle_id, day, total = get_cycle_info()
    goals = get_goals(cycle_id)
    stats = get_checkin_stats(cycle_id)

    if not goals and not stats:
        return f"目前週期（第 {day}/{total} 天）還沒有人設目標喔！\n輸入：設目標：目標1 / 目標2"

    lines = [f"🎯 本週期目標（第 {day}/{total} 天）\n"]
    all_members = sorted(set(list(goals.keys()) + list(stats.keys())))
    for member in all_members:
        member_goals = goals.get(member, [])
        checked_days = stats.get(member, [])
        lines.append(f"👤 {member}（打卡 {len(checked_days)} 天）")
        if member_goals:
            for i, g in enumerate(member_goals, 1):
                lines.append(f"  {i}. {g}")
        else:
            lines.append("  （未設目標）")
        lines.append("")

    return "\n".join(lines).strip()


def handle_cycle_progress():
    cycle_id, day, total = get_cycle_info()
    stats = get_checkin_stats(cycle_id)
    lines = [f"📅 現在是十日週期第 {day}/{total} 天\n"]
    if stats:
        for member, days in sorted(stats.items()):
            bar = "🟩" * len(days) + "⬜" * (total - len(days))
            lines.append(f"{member}：{bar} {len(days)}/{total}")
    else:
        lines.append("還沒有人打卡 😶")
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

    reply_text = None

    with ApiClient(configuration) as api_client:
        # ── 隱藏指令：拿 group ID ──
        if text == "!groupid":
            reply_text = f"Group ID: {group_id or '非群組訊息'}"

        # ── 暱稱登記 ──
        elif nick_match := re.match(r'^叫我\s*(.+)$', text):
            nickname = nick_match.group(1).strip()
            ok = set_nickname(user_id, nickname)
            reply_text = f"好的！之後叫你「{nickname}」了 👋" if ok else "登記失敗，等一下再試 😢"

        # ── 十日目標指令 ──
        elif re.match(r'^設目標[：:]', text):
            member = get_member_label(api_client, group_id, user_id)
            reply_text = handle_set_goals(member, text)

        elif text.startswith("打卡"):
            member = get_member_label(api_client, group_id, user_id)
            reply_text = handle_checkin(member, text)

        elif text in ("查目標", "看目標", "目標"):
            reply_text = handle_view_goals()

        elif text in ("今天第幾天", "幾天了", "進度"):
            reply_text = handle_cycle_progress()

        # ── 日文問題 ──
        elif handle_japanese_question(text):
            reply_text = handle_japanese_question(text)

        # ── 關鍵字自動回覆 ──
        elif re.search(r'冷笑話', text):
            reply_text = handle_joke(text)

        elif re.search(r'冷知識', text):
            reply_text = handle_fun_fact(text)

        elif re.search(r'天氣', text):
            reply_text = get_weather(text)

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
        elif BOT_NAME in text or "機器人" in text or "bot" in text.lower():
            reply_text = handle_mention(text)

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
                    text="歡迎！我是群組的日文小老師 🇯🇵\n每天會發日文單字，遇到不知道吃什麼、要查天氣都可以問我～"
                )]
            )
        )


@app.route("/health")
def health():
    return "OK"


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
