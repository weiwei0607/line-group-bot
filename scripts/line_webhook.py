import os
import re
import requests
from flask import Flask, request, abort
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    Configuration, ApiClient, MessagingApi,
    ReplyMessageRequest, TextMessage
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent, MemberJoinedEvent

app = Flask(__name__)

CHANNEL_SECRET = os.environ["LINE_CHANNEL_SECRET"]
CHANNEL_ACCESS_TOKEN = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
BOT_NAME = os.environ.get("LINE_BOT_NAME", "日文小老師")

handler = WebhookHandler(CHANNEL_SECRET)
configuration = Configuration(access_token=CHANNEL_ACCESS_TOKEN)

def call_gemini(prompt):
    if not GEMINI_API_KEY:
        return None
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
        resp = requests.post(
            url,
            json={"contents": [{"parts": [{"text": prompt}]}]},
            timeout=10
        )
        return resp.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
    except:
        return None

def handle_japanese_question(text):
    """處理「XX 日文怎麼說」類的問題"""
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
                f"語氣輕鬆友善，像朋友在聊天，不要太正式。"
            )
            return call_gemini(prompt)
    return None

def handle_mention(text):
    """被 tag 或點名時回應"""
    prompt = (
        f"你是一個活潑有趣的朋友群 LINE 機器人，名字叫「{BOT_NAME}」。"
        f"有人在群組裡傳了這段話：「{text}」\n\n"
        f"請用台灣年輕人語氣回應，輕鬆幽默，不超過 3 句。"
        f"如果訊息跟日文有關，可以順便教一個單字。"
        f"不要加『大家好』或自我介紹。"
    )
    return call_gemini(prompt)

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
    text = event.message.text
    reply_token = event.reply_token

    reply_text = None

    # 優先：日文問題
    jp_answer = handle_japanese_question(text)
    if jp_answer:
        reply_text = jp_answer

    # 次要：被 tag 或點名
    elif BOT_NAME in text or "機器人" in text or "bot" in text.lower():
        reply_text = handle_mention(text)

    if not reply_text:
        return

    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=reply_token,
                messages=[TextMessage(text=reply_text)]
            )
        )

@handler.add(MemberJoinedEvent)
def handle_join(event):
    """新成員加入時打招呼"""
    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(
                    text="歡迎！我是群組的日文小老師 🇯🇵\n每天會發一個日文單字，有問題可以問我～"
                )]
            )
        )

@app.route("/health")
def health():
    return "OK"

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
