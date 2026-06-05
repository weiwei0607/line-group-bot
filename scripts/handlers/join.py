"""Group join handler."""


def handle_join(event, configuration):
    from line_push import reply_text
    reply_text(
        event.reply_token,
        "歡迎加入！我是小棉襖 🧸\n輸入「叫我XXX」先綁定你的暱稱！",
    )
