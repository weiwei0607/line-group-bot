"""Group join handler."""

from linebot.v3.messaging import (
    ApiClient, MessagingApi,
    ReplyMessageRequest, TextMessage,
)


def handle_join(event, configuration):
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
