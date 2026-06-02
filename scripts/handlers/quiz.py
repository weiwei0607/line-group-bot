"""Quiz game handler (來一題 / A/B/C/D / 答 xxx / 答案)."""

import html as _html
import logging
import random
import requests
from state import quiz_get, quiz_set, quiz_delete
from goal_tracker import add_quiz_score
from utils import send_telegram_alert


def _ninja(path: str):
    """Quick helper: call API Ninjas (assumes _ninja is available from api_helpers)."""
    # Import lazily to avoid circular deps
    from api_helpers import _ninja as ninja_fn
    return ninja_fn(path)


def _smart_translate(text: str) -> str:
    from api_helpers import smart_translate
    return smart_translate(text)


def handle_quiz(text: str, group_id: str, member_label: str) -> str | None:
    """Handle quiz commands. Returns reply_text or None."""
    import re

    if text == "來一題":
        question, answer, cat, wrong = "", "", "", []
        try:
            r2 = requests.get(
                "https://opentdb.com/api.php",
                params={"amount": 1, "type": "multiple"},
                timeout=8,
            )
            res = r2.json().get("results", [])
            if res:
                question = _html.unescape(res[0].get("question", ""))
                answer = _html.unescape(res[0].get("correct_answer", ""))
                cat = res[0].get("category", "")
                wrong = [_html.unescape(w) for w in res[0].get("incorrect_answers", [])]
        except Exception as exc:
            logging.warning("opentdb error: %s", exc)
            send_telegram_alert(f"opentdb error: {exc}")

        if not question:
            d = _ninja("/v1/trivia")
            if d and isinstance(d, list):
                question = d[0].get("question", "")
                answer = d[0].get("answer", "")
                cat = d[0].get("category", "")

        if question:
            q_zh = _smart_translate(question)
            a_zh = _smart_translate(answer) or answer
            gid = group_id or "default"
            if wrong:
                choices = [answer] + wrong[:3]
                random.shuffle(choices)
                letters = ["A", "B", "C", "D"]
                opts = {letters[i]: choices[i] for i in range(len(choices))}
                correct_letter = next(k for k, v in opts.items() if v == answer)
                opts_zh = {k: _smart_translate(v) or v for k, v in opts.items()}
                correct_zh = opts_zh[correct_letter]
                quiz_set(gid, {
                    "question": q_zh or question,
                    "answer": correct_zh,
                    "correct_letter": correct_letter,
                    "options": opts_zh,
                })
                opts_str = "\n".join(f"  {k}. {v}" for k, v in opts_zh.items())
                return (
                    f"🧠 來答題！（{cat}）\n\n{q_zh or question}\n\n"
                    f"{opts_str}\n\n傳「A」「B」「C」「D」作答，傳「答案」看解答"
                )
            else:
                quiz_set(gid, {"question": q_zh or question, "answer": a_zh})
                return (
                    f"🧠 來答題！（{cat}）\n\n{q_zh or question}\n\n"
                    f"傳「答 你的答案」作答，傳「答案」看解答"
                )
        else:
            return "🧠 題庫暫時關閉，待會再試"

    m = re.match(r'^([ABCD])$', text.strip().upper())
    if m:
        gid = group_id or "default"
        qstate = quiz_get(gid)
        if qstate and "options" in qstate:
            chosen = text.strip().upper()
            if chosen == qstate["correct_letter"]:
                quiz_delete(gid)
                new_score = add_quiz_score(member_label)
                return f"🎉 答對了！答案是 {qstate['correct_letter']}. {qstate['answer']}\n{member_label} 本週答對 {new_score} 題！"
            else:
                chosen_ans = qstate["options"].get(chosen, chosen)
                return f"❌ {chosen}. {chosen_ans} 不對喔，再想想！（傳「答案」放棄）"

    m = re.match(r'^答\s+(.+)$', text)
    if m:
        gid = group_id or "default"
        qstate = quiz_get(gid)
        if qstate:
            user_ans = m.group(1).strip().lower()
            correct = qstate["answer"].lower()
            if correct in user_ans or user_ans in correct:
                quiz_delete(gid)
                new_score = add_quiz_score(member_label)
                return f"🎉 答對了！答案是：{qstate['answer']}\n{member_label} 本週答對 {new_score} 題！"
            else:
                return "❌ 不對喔，再想想！（傳「答案」放棄）"

    if text == "答案":
        gid = group_id or "default"
        state = quiz_get(gid)
        if state is not None:
            quiz_delete(gid)
            if "correct_letter" in state:
                return f"💡 答案是 {state['correct_letter']}. {state['answer']}"
            else:
                return f"💡 答案是：{state['answer']}"

    return None
