"""
晚安問候 — 由 GitHub Actions 每天 22:00 (Asia/Taipei) 執行
內容：明天天氣預報 + 睡前問候
"""

import os
from datetime import datetime, timedelta
from goal_tracker import TW_TZ, _now
from weather import _get_om_forecast, _OM_WMO
from utils import call_gemini, send_line_message
from config import _MEMBER_BIRTHDAYS

_WEEKDAY_NAMES = ["週一", "週二", "週三", "週四", "週五", "週六", "週日"]


def _tomorrow_birthdays() -> list[str]:
    """回傳明天生日的 nickname 列表"""
    tomorrow = _now() + timedelta(days=1)
    tomorrow_mmdd = tomorrow.strftime("%m-%d")
    return [nick for nick, bd in _MEMBER_BIRTHDAYS.items() if bd == tomorrow_mmdd]


def _tomorrow_weather() -> str:
    """回傳明天天氣文字描述"""
    forecast = _get_om_forecast()
    if not forecast or len(forecast) < 2:
        return ""
    day = forecast[1]
    advice = []
    if day["rain_prob"] >= 60:
        advice.append("明天會下雨，記得帶傘 ☔")
    elif day["rain_prob"] >= 30:
        advice.append("明天可能會下雨，建議帶傘 🌂")
    if "晴" in day["condition"] and day["temp_max"] >= 30:
        advice.append("明天很熱，注意防曬 🧴")
    elif day["temp_max"] <= 18:
        advice.append("明天氣溫較低，記得穿暖 🧥")

    lines = [
        f"{day['condition']}　最高 {day['temp_max']}° / 最低 {day['temp_min']}°",
        f"降雨機率 {day['rain_prob']}%",
    ]
    if advice:
        lines.append("\n".join(advice))
    return "\n".join(lines)


def main():
    tomorrow = _now() + timedelta(days=1)
    weekday = _WEEKDAY_NAMES[tomorrow.weekday()]
    date_str = tomorrow.strftime(f"%-m月%-d日 {weekday}")

    weather_str = _tomorrow_weather()
    birthday_nicks = _tomorrow_birthdays()
    birthday_str = f"明天是 {'、'.join(birthday_nicks)} 的生日 🎂" if birthday_nicks else ""

    prompt_parts = [f"明天是{date_str}。"]
    if weather_str:
        prompt_parts.append(f"明天天氣：{weather_str}")
    if birthday_str:
        prompt_parts.append(birthday_str)
    prompt_parts.append(
        "幫我寫一則給朋友群的晚安問候，"
        "輕鬆溫馨、100字以內、繁體中文，"
        "順帶提醒明天天氣注意事項，"
        "結尾加一個晚安 emoji。"
    )

    msg = call_gemini("\n".join(prompt_parts))
    if not msg:
        # fallback
        parts = [f"🌙 晚安！明天{date_str}"]
        if weather_str:
            parts.append(weather_str)
        if birthday_str:
            parts.append(birthday_str)
        parts.append("好夢 💤")
        msg = "\n".join(parts)

    send_line_message(msg)
    print(f"Evening greeting sent at {_now().strftime('%H:%M')}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        print(f"Evening greeting failed: {e}")
        print(traceback.format_exc())
        raise
