"""2026-11-22 可靠性／指令修復／prompt 護欄的測試。全部用 mock，不打任何外部 API、不碰真實 Sheet。
執行：cd line-group-bot && python3 -W ignore -m unittest discover -s tests -v
"""
import datetime
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
os.environ.setdefault("STATE_DB_PATH", "/tmp/_test_state.db")
os.environ.setdefault("LINE_CHANNEL_SECRET", "test-secret")
os.environ.setdefault("LINE_CHANNEL_ACCESS_TOKEN", "test-token")

import utils  # noqa: E402


class FakeResp:
    def __init__(self, status=200, body=None, text=None):
        self.status_code = status
        self.ok = 200 <= status < 300
        self._body = body
        self.text = text if text is not None else str(body)

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


def groq_ok(content="hi", finish="stop"):
    return FakeResp(200, {"choices": [{"message": {"content": content}, "finish_reason": finish}]})


class GroqTests(unittest.TestCase):
    def setUp(self):
        os.environ["GROQ_API_KEY"] = "k"
        utils._alert_last.clear()
        self.alert = mock.patch.object(utils, "send_telegram_alert").start()
        self.addCleanup(mock.patch.stopall)

    def test_success_and_payload(self):
        with mock.patch.object(utils.requests, "post", return_value=groq_ok(" 你好 ")) as p:
            self.assertEqual(utils.call_groq("x", max_tokens=1234, system="SYS"), "你好")
        j = p.call_args.kwargs["json"]
        self.assertEqual(j["max_tokens"], 1234)
        self.assertEqual(j["reasoning_effort"], "low")
        self.assertEqual(j["messages"][0], {"role": "system", "content": "SYS"})

    def test_429_not_retried_and_logged(self):
        with mock.patch.object(utils.requests, "post", return_value=FakeResp(429, text="rate limited")) as p, \
                self.assertLogs(level="WARNING") as cm:
            self.assertIsNone(utils.call_groq("x"))
        self.assertEqual(p.call_count, 1)
        self.assertTrue(any("HTTP 429" in l and "rate limited" in l for l in cm.output))

    def test_404_logs_raw_body_and_alerts(self):
        with mock.patch.object(utils.requests, "post", return_value=FakeResp(404, text="model_not_found")), \
                self.assertLogs(level="WARNING") as cm:
            self.assertIsNone(utils.call_groq("x"))
        self.assertTrue(any("model_not_found" in l for l in cm.output))
        self.alert.assert_called_once()

    def test_5xx_retried_once(self):
        with mock.patch.object(utils.requests, "post",
                               side_effect=[FakeResp(503, text="x"), groq_ok("ok")]) as p:
            self.assertEqual(utils.call_groq("x"), "ok")
        self.assertEqual(p.call_count, 2)

    def test_timeout_retried_once_then_none(self):
        with mock.patch.object(utils.requests, "post", side_effect=utils.requests.Timeout("t")) as p, \
                self.assertLogs(level="WARNING"):
            self.assertIsNone(utils.call_groq("x"))
        self.assertEqual(p.call_count, 2)

    def test_truncated_is_failure(self):
        with mock.patch.object(utils.requests, "post", return_value=groq_ok("半截", finish="length")), \
                self.assertLogs(level="WARNING") as cm:
            self.assertIsNone(utils.call_groq("x"))
        self.assertTrue(any("truncated" in l for l in cm.output))

    def test_null_content_and_bad_body(self):
        with mock.patch.object(utils.requests, "post", return_value=groq_ok(None)), self.assertLogs(level="WARNING"):
            self.assertIsNone(utils.call_groq("x"))
        with mock.patch.object(utils.requests, "post", return_value=FakeResp(200, {"error": "x"})), \
                self.assertLogs(level="WARNING"):
            self.assertIsNone(utils.call_groq("x"))

    def test_alert_dedup(self):
        with mock.patch.object(utils.requests, "post", return_value=FakeResp(401, text="bad key")), \
                self.assertLogs(level="WARNING"):
            utils.call_groq("x")
            utils.call_groq("x")
        self.assertEqual(self.alert.call_count, 1)


class GeminiTests(unittest.TestCase):
    def setUp(self):
        os.environ["GEMINI_API_KEY"] = "g"
        utils._alert_last.clear()
        mock.patch.object(utils, "send_telegram_alert").start()
        mock.patch.object(utils._time, "sleep").start()
        self.addCleanup(mock.patch.stopall)

    @staticmethod
    def ok(text="hello", finish="STOP"):
        return FakeResp(200, {"candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": finish}]})

    def test_success(self):
        with mock.patch.object(utils.requests, "post", return_value=self.ok()):
            self.assertEqual(utils.call_gemini("x"), "hello")

    def test_429_single_request(self):
        with mock.patch.object(utils.requests, "post", return_value=FakeResp(429, text="quota")) as p, \
                self.assertLogs(level="WARNING"):
            self.assertIsNone(utils.call_gemini("x"))
        self.assertEqual(p.call_count, 1)

    def test_persistent_5xx_capped_at_two_requests(self):
        with mock.patch.object(utils.requests, "post", return_value=FakeResp(500, text="boom")) as p, \
                self.assertLogs(level="WARNING"):
            self.assertIsNone(utils.call_gemini("x"))
        self.assertEqual(p.call_count, 2)  # 以前最壞 9 個請求

    def test_max_tokens_and_no_candidates_are_none(self):
        with mock.patch.object(utils.requests, "post", return_value=self.ok("cut", "MAX_TOKENS")), \
                self.assertLogs(level="WARNING"):
            self.assertIsNone(utils.call_gemini("x"))
        with mock.patch.object(utils.requests, "post", return_value=FakeResp(200, {"candidates": []})), \
                self.assertLogs(level="WARNING"):
            self.assertIsNone(utils.call_gemini("x"))

    def test_call_ai_falls_back_to_gemini_and_passes_system(self):
        with mock.patch.object(utils, "call_groq", return_value=None), \
                mock.patch.object(utils, "call_gemini", return_value="G") as g:
            self.assertEqual(utils.call_ai("P", system="S"), "G")
        self.assertIn("S", g.call_args.args[0])
        self.assertIn("P", g.call_args.args[0])

    def test_call_ai_deadline_skips_gemini(self):
        times = iter([0, 19])
        with mock.patch.object(utils, "call_groq", return_value=None), \
                mock.patch.object(utils, "call_gemini") as g, \
                mock.patch.object(utils._time, "time", side_effect=lambda: next(times)):
            self.assertIsNone(utils.call_ai("P"))
        g.assert_not_called()


class TodayLineTests(unittest.TestCase):
    def test_taipei_weekday(self):
        now = datetime.datetime(2026, 11, 22, 8, 0, tzinfo=utils.TW_TZ)
        self.assertEqual(utils.today_line(now), "2026年11月22日 星期日（台北時間）")


class LineReplyTests(unittest.TestCase):
    def setUp(self):
        from shared import line_push
        self.lp = line_push
        self.lp._alert_times.clear()
        self.tg = mock.patch.object(line_push, "_telegram_alert").start()
        self.addCleanup(mock.patch.stopall)

    def test_non_2xx_logged_and_false(self):
        with mock.patch.object(self.lp.requests, "post", return_value=FakeResp(400, text="Invalid reply token")), \
                self.assertLogs(level="WARNING") as cm:
            self.assertFalse(self.lp.reply_text("tok", "hi"))
        self.assertTrue(any("400" in l and "Invalid reply token" in l for l in cm.output))
        self.tg.assert_called_once()

    def test_alert_deduped(self):
        with mock.patch.object(self.lp.requests, "post", return_value=FakeResp(400, text="x")), \
                self.assertLogs(level="WARNING"):
            self.lp.reply_text("tok", "a")
            self.lp.reply_text("tok", "b")
        self.assertEqual(self.tg.call_count, 1)

    def test_ok_and_exception(self):
        with mock.patch.object(self.lp.requests, "post", return_value=FakeResp(200, {})):
            self.assertTrue(self.lp.reply_text("tok", "hi"))
        with mock.patch.object(self.lp.requests, "post", side_effect=RuntimeError("x")), \
                mock.patch.object(self.lp.time, "sleep"), self.assertLogs(level="WARNING"):
            self.assertFalse(self.lp.reply_text("tok", "hi"))


class MemorySummaryNotTruncatedTests(unittest.TestCase):
    def test_truncated_groq_does_not_write_summary(self):
        import memory_store as ms
        os.environ["GROQ_API_KEY"] = "k"
        os.environ.pop("GEMINI_API_KEY", None)
        mock.patch.object(utils, "send_telegram_alert").start()
        self.addCleanup(mock.patch.stopall)
        with mock.patch.object(utils.requests, "post", return_value=groq_ok("半截摘要半截摘要半截摘要", "length")) as p, \
                self.assertLogs(level="WARNING"):
            out = ms._default_llm("prompt")
        self.assertIsNone(out)
        self.assertEqual(p.call_args.kwargs["json"]["max_tokens"], 2000)


# ─── B1 / C1 ──────────────────────────────────────────────

import api_helpers  # noqa: E402
import commands  # noqa: E402
import dispatch  # noqa: E402

# 說明文字教大家用的 12 個指令 → 應該呼叫的函式名
TWELVE = [
    ("找歌 周杰倫", "fetch_spotify_track", "周杰倫"),
    ("查電影 奧本海默", "fetch_imdb", "奧本海默"),
    ("在哪看 鬼滅之刃", "fetch_streaming", "鬼滅之刃"),
    ("查超英 蝙蝠俠", "fetch_superhero", "蝙蝠俠"),
    ("國家 日本", "fetch_country", "日本"),
    ("找書 小王子", "fetch_book", "小王子"),
    ("查日文 猫", "fetch_jisho", "猫"),
    ("漢字 猫", "fetch_kanji", "猫"),
    ("查西文 gato", "fetch_spanish", "gato"),
    ("熱量 apple", "fetch_nutrition", "apple"),
    ("食譜 雞蛋 番茄", "fetch_recipe_by_ingredient", "雞蛋 番茄"),
    ("縮網址 https://example.com/a", "shorten_url", "https://example.com/a"),
]


class TwelveCommandsTests(unittest.TestCase):
    def test_every_command_is_claimed_with_right_handler(self):
        for text, fn_name, arg in TWELVE:
            with self.subTest(text=text):
                with mock.patch.object(commands, fn_name, return_value="R") as fn:
                    self.assertEqual(commands.run_regex_commands(text), "R")
                fn.assert_called_once_with(arg)

    def _event(self, text):
        src = mock.Mock(group_id="G", user_id="U")
        return mock.Mock(message=mock.Mock(text=text), reply_token="RT", source=src)

    def test_handle_message_runs_regex_commands_before_keyword_dispatch(self):
        # 「查電影 貓貓」：try_dispatch 的無錨點關鍵字（貓貓）會搶，所以 regex 指令要先跑
        for text, fn in (("查電影 貓貓", "fetch_imdb"), ("找歌 笑話", "fetch_spotify_track")):
            with mock.patch.object(commands, fn, return_value="R"), \
                    mock.patch.object(commands, "reply") as rep, \
                    mock.patch.object(commands, "rate_limit_check", return_value=True):
                commands.handle_message(self._event(text))
            rep.assert_called_once_with("RT", "R")

    def test_at_mention_prefix_stripped_still_reaches_handle_mention(self):
        # 以前：@小棉襖 嗨 → 去前綴後沒有 bot 名字 → 沒有任何回應
        patches = [
            mock.patch.object(commands, "rate_limit_check", return_value=True),
            mock.patch.object(commands, "update_last_activity"),
            mock.patch.object(commands, "get_member_label", return_value="毛毛"),
            mock.patch.object(commands, "_remember"),
            mock.patch.object(commands, "_should_log", return_value=False),
            mock.patch.object(commands, "handle_notebook_command", return_value=False),
            mock.patch.object(commands, "handle_link_command", return_value=False),
            mock.patch.object(commands, "handle_mention", return_value="嗨嗨"),
            mock.patch("line_push.reply_text", return_value=True),
        ]
        for p in patches:
            p.start()
        self.addCleanup(mock.patch.stopall)
        import line_push
        commands.handle_message(self._event("@小棉襖 今天心情普通"))
        commands.handle_mention.assert_called_once()
        line_push.reply_text.assert_called_once_with("RT", "嗨嗨")

    def test_conversation_reply_failure_falls_back_to_push(self):
        for p in [
            mock.patch.object(commands, "rate_limit_check", return_value=True),
            mock.patch.object(commands, "update_last_activity"),
            mock.patch.object(commands, "get_member_label", return_value="毛毛"),
            mock.patch.object(commands, "_remember"),
            mock.patch.object(commands, "_should_log", return_value=False),
            mock.patch.object(commands, "handle_notebook_command", return_value=False),
            mock.patch.object(commands, "handle_link_command", return_value=False),
            mock.patch.object(commands, "handle_mention", return_value="嗨嗨"),
            mock.patch("line_push.reply_text", return_value=False),
            mock.patch("line_push.push_text"),
        ]:
            p.start()
        self.addCleanup(mock.patch.stopall)
        import line_push
        commands.handle_message(self._event("@小棉襖 聊聊"))
        line_push.push_text.assert_called_once_with("G", "嗨嗨")

    def test_kanji_lookup_not_swallowed_by_daily(self):
        # 修好前 dispatch 的 ^漢字 會吃掉「漢字 猫」，回隨機今日漢字
        self.assertEqual(dispatch.try_dispatch("漢字 猫"), (None, None))
        with mock.patch.object(dispatch, "fetch_daily_kanji", return_value="DAILY"):
            for t in ("漢字", "今日漢字", "學漢字", "每日漢字"):
                self.assertEqual(dispatch.try_dispatch(t), ("DAILY", None))

    def test_non_commands_not_claimed(self):
        for t in ("嗨", "我想查電影", "國家隊今天贏了", "熱量好高", "找歌"):
            self.assertIsNone(commands.run_regex_commands(t), t)

    def test_handler_exception_gives_friendly_message(self):
        with mock.patch.object(commands, "fetch_imdb", side_effect=RuntimeError("x")), \
                self.assertLogs(level="WARNING"):
            self.assertIn("晚點", commands.run_regex_commands("查電影 abc"))


class NoMadeUpFactsTests(unittest.TestCase):
    def _no_gemini(self):
        return mock.patch.object(api_helpers, "call_gemini",
                                 side_effect=AssertionError("不該呼叫 Gemini 編事實"))

    def test_spotify_and_imdb_say_not_found(self):
        with self._no_gemini(), mock.patch.object(api_helpers, "_rapid", return_value=None):
            self.assertIn("找不到", api_helpers.fetch_spotify_track("zzz"))
            self.assertIn("查不到", api_helpers.fetch_imdb("zzz"))
        with self._no_gemini(), mock.patch.object(api_helpers, "_rapid", return_value={"tracks": {"items": []}}):
            self.assertIn("找不到", api_helpers.fetch_spotify_track("zzz"))
        with self._no_gemini(), mock.patch.object(api_helpers, "_rapid", return_value={"results": []}):
            self.assertIn("找不到", api_helpers.fetch_imdb("zzz"))

    def test_ai_fallbacks_are_labelled(self):
        with mock.patch.object(api_helpers, "_jisho_lookup", return_value=None), \
                mock.patch.object(api_helpers, "call_gemini", return_value="AI說明"):
            self.assertIn("由 AI", api_helpers.fetch_jisho("x"))
        with mock.patch.object(api_helpers, "_kanji_lookup", return_value=None), \
                mock.patch.object(api_helpers, "call_gemini", return_value="AI說明"):
            self.assertIn("由 AI", api_helpers.fetch_kanji("x"))
        with mock.patch.object(api_helpers, "_rapid", return_value=None), \
                mock.patch.object(api_helpers, "call_gemini", return_value="AI食譜"):
            self.assertIn("由 AI", api_helpers.fetch_recipe_by_ingredient("蛋"))
        with mock.patch.object(api_helpers, "_rapid", return_value=None), \
                mock.patch.object(api_helpers, "call_gemini", return_value=None):
            self.assertIn("查不到", api_helpers.fetch_recipe_by_ingredient("蛋"))


class MentionPromptTests(unittest.TestCase):
    NOW = datetime.datetime(2026, 11, 22, 9, 0, tzinfo=utils.TW_TZ)

    def _run(self, text, ai="AI回", **kw):
        with mock.patch.object(api_helpers, "get_memories", return_value=[]), \
                mock.patch.object(api_helpers, "get_personal_memories", return_value=[]), \
                mock.patch.object(api_helpers, "build_memory_context", return_value="【摘要】x"), \
                mock.patch.object(api_helpers, "call_ai", return_value=ai) as m:
            out = api_helpers.handle_mention(text, member="毛毛")
        return out, m

    def test_prompt_has_date_and_guardrails(self):
        out, m = self._run("你覺得我該不該換工作")
        self.assertEqual(out, "AI回")
        prompt = m.call_args.args[0]
        system = m.call_args.kwargs["system"]
        self.assertRegex(prompt, r"【今天】\d{4}年\d{1,2}月\d{1,2}日 星期[一二三四五六日]（台北時間）")
        self.assertIn("不知道就說不知道", system)
        self.assertIn("新聞", system)
        self.assertIn("不准自己補", system)

    def test_llm_failure_gives_friendly_retry_message(self):
        out, _ = self._run("聊聊", ai=None)
        self.assertEqual(out, api_helpers._FIXED_LLM_ERROR)
        self.assertIn("再 @ 我", out)

    def test_guard_news_goes_to_query_path_not_llm(self):
        with mock.patch.object(api_helpers, "fetch_news", return_value="NEWS") as n, \
                mock.patch.object(api_helpers, "call_ai") as ai:
            self.assertEqual(api_helpers.handle_mention("今天有什麼新聞"), "NEWS")
        n.assert_called_once()
        ai.assert_not_called()

    def test_guard_date_and_festival_answered_from_tables(self):
        g = api_helpers.pre_llm_guard
        self.assertIn("2026年11月22日 星期日", g("今天幾號", now=self.NOW))
        self.assertIn("星期日", g("今天星期幾", now=self.NOW))
        self.assertIn("沒有特別的節日", g("今天是什麼節日", now=self.NOW))
        qixi = datetime.datetime(2026, 8, 19, 9, 0, tzinfo=utils.TW_TZ)
        self.assertIn("七夕", g("今天有什麼節日", now=qixi))

    def test_guard_live_facts_refused_but_chat_passes(self):
        g = api_helpers.pre_llm_guard
        self.assertIn("查不到", g("台積電股價多少"))
        self.assertIn("查不到", g("奧本海默票房多少？"))
        for chat in ("你覺得我該換工作嗎", "我今天好累", "這部電影好看", "新聞好煩喔我不想看"):
            self.assertIsNone(g(chat, now=self.NOW), chat)

    def test_dispatch_news_question_forms(self):
        with mock.patch.object(dispatch, "fetch_news", return_value="NEWS"):
            for t in ("今天有什麼新聞", "今日有啥新聞", "最近有沒有頭條", "新聞"):
                self.assertEqual(dispatch.try_dispatch(t), ("NEWS", None), t)


class PingAndConfigTests(unittest.TestCase):
    def test_ping_is_light_and_ok(self):
        import line_webhook
        c = line_webhook.app.test_client()
        r = c.get("/ping")
        self.assertEqual((r.status_code, r.get_data(as_text=True)), (200, "ok"))
        self.assertEqual(c.head("/ping").status_code, 200)

    def test_gunicorn_conf_adds_threads(self):
        ns = {}
        path = os.path.join(os.path.dirname(__file__), "..", "gunicorn.conf.py")
        exec(open(path).read(), ns)
        self.assertGreaterEqual(ns["threads"], 2)
        self.assertGreaterEqual(ns["timeout"], 30)

    def test_keep_alive_hits_ping_without_secrets_inline(self):
        p = os.path.join(os.path.dirname(__file__), "..", ".github", "workflows", "keep_alive.yml")
        txt = open(p).read()
        self.assertIn("/ping", txt)
        self.assertNotIn("/health", txt)
        self.assertIn("secrets.RENDER_APP_URL", txt)


if __name__ == "__main__":
    unittest.main()
