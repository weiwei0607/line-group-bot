"""引用（回覆）小棉襖偵測的測試。全 mock，不打 LINE、不碰真實 Sheet。
執行：cd line-group-bot && python3 -W ignore -m unittest discover -s tests
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
os.environ.setdefault("STATE_DB_PATH", "/tmp/_test_state.db")
os.environ.setdefault("LINE_CHANNEL_SECRET", "test-secret")
os.environ.setdefault("LINE_CHANNEL_ACCESS_TOKEN", "test-token")

import memory_store as ms  # noqa: E402
import sent_messages as sm  # noqa: E402
import commands  # noqa: E402
from shared import line_push  # noqa: E402


class FakeRing:
    """假的「bot訊息」分頁。"""
    def __init__(self):
        self.rows = {}      # row number -> list
        self.fail = False

    def get(self, rng):
        if self.fail:
            raise ms.SheetsError("HTTP 500: boom")
        return [self.rows[r] for r in sorted(self.rows)]

    def update(self, rng, values):
        if self.fail:
            raise ms.SheetsError("HTTP 500: boom")
        row = int(rng.split("!")[1].split(":")[0][1:])
        self.rows[row] = [str(x) for x in values[0]]


class Resp:
    status_code = 200

    def __init__(self, body):
        self._b = body

    def json(self):
        if self._b is None:
            raise ValueError("no json")
        return self._b


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.sheet = FakeRing()
        for p in (mock.patch.object(ms, "_values_get", self.sheet.get),
                  mock.patch.object(ms, "_values_update", self.sheet.update),
                  mock.patch.object(ms, "_now_str", lambda: "2026-11-22 10:00"),
                  mock.patch.object(sm, "ASYNC_WRITE", False),
                  mock.patch.object(sm, "_ensure_tab", lambda: None)):
            p.start()
        self.addCleanup(mock.patch.stopall)
        sm.reset()

    def restart(self):
        sm.reset()  # 記憶體清空，只剩 Sheet

    def test_record_then_lookup_memory_and_after_restart(self):
        sm.record([(111, "你好呀")], "C1")
        self.assertEqual(sm.lookup("111", "C1")["text"], "你好呀")
        self.restart()
        self.assertEqual(sm.lookup("111", "C1")["text"], "你好呀")  # 從 Sheet 讀回

    def test_text_clipped_to_200(self):
        sm.record([("1", "字" * 500)], "C1")
        self.assertEqual(len(sm.lookup("1")["text"]), 200)

    def test_ring_keeps_only_latest_200(self):
        for i in range(250):
            sm.record([(str(i), f"m{i}")], "C1")
        self.assertEqual(len(self.sheet.rows), 200)
        self.restart()
        self.assertIsNone(sm.lookup("0"))        # 最舊的被覆蓋
        self.restart()
        self.assertIsNotNone(sm.lookup("249"))
        self.assertIsNotNone(sm.lookup("50"))    # 剛好是最近 200 筆的第一筆

    def test_seq_continues_after_restart(self):
        for i in range(3):
            sm.record([(str(i), "x")], "C1")
        self.restart()
        sm.record([("9", "y")], "C1")
        self.assertEqual(self.sheet.rows[5][0], "4")  # 沒有從頭覆蓋

    def test_other_group_not_matched(self):
        sm.record([("1", "hi")], "C1")
        self.assertIsNone(sm.lookup("1", "C2"))

    def test_unknown_id_returns_none_and_sheet_read_cached(self):
        with mock.patch.object(ms, "_values_get", wraps=self.sheet.get) as g:
            self.assertIsNone(sm.lookup("nope"))
            self.assertIsNone(sm.lookup("nope2"))
        self.assertEqual(g.call_count, 1)

    def test_sheet_failure_never_raises(self):
        self.sheet.fail = True
        sm.record([("1", "hi")], "C1")            # 寫入失敗：不 raise
        self.assertEqual(sm.lookup("1")["text"], "hi")  # 記憶體仍有
        self.restart()
        self.assertIsNone(sm.lookup("1"))         # 讀失敗：回 None，不 raise

    def test_non_string_or_empty_id(self):
        self.assertIsNone(sm.lookup(None))
        self.assertIsNone(sm.lookup(mock.Mock()))
        self.assertIsNone(sm.lookup(""))

    def test_record_from_response_maps_sentMessages_in_order(self):
        msgs = [{"type": "image", "originalContentUrl": "u"}, {"type": "text", "text": "文字"}]
        sm.record_from_response(Resp({"sentMessages": [{"id": "7", "quoteToken": "q"}, {"id": 8}]}), msgs, "C1")
        self.assertEqual(sm.lookup("7")["text"], "[image]")
        self.assertEqual(sm.lookup("8")["text"], "文字")

    def test_record_from_response_bad_body_ignored(self):
        for body in (None, {}, {"sentMessages": "x"}, []):
            sm.record_from_response(Resp(body), [{"type": "text", "text": "a"}], "C1")
        sm.record_from_response(mock.Mock(), [], "C1")


class LinePushHookTests(unittest.TestCase):
    def setUp(self):
        sm.reset()
        mock.patch.object(sm, "ASYNC_WRITE", False).start()
        mock.patch.object(sm, "_persist", lambda e: None).start()
        self.addCleanup(mock.patch.stopall)

    def test_reply_success_records_ids(self):
        resp = Resp({"sentMessages": [{"id": "55"}]})
        resp.status_code = 200
        with mock.patch.object(line_push.requests, "post", return_value=resp):
            self.assertTrue(line_push.reply_text("RT", "哈囉"))
        self.assertEqual(sm._mem["55"]["text"], "哈囉")

    def test_push_success_records_with_group(self):
        resp = Resp({"sentMessages": [{"id": "66"}]})
        with mock.patch.object(line_push.requests, "post", return_value=resp):
            line_push.push_text("Cgroup", "推播")
        self.assertEqual(sm._mem["66"]["group"], "Cgroup")

    def test_reply_unaffected_when_recording_explodes(self):
        resp = Resp({"sentMessages": [{"id": "1"}]})
        with mock.patch.object(line_push.requests, "post", return_value=resp), \
                mock.patch.object(sm, "record", side_effect=RuntimeError("boom")):
            self.assertTrue(line_push.reply_text("RT", "ok"))

    def test_reply_failure_records_nothing(self):
        resp = Resp({"sentMessages": [{"id": "1"}]})
        resp.status_code, resp.text = 400, "bad token"
        with mock.patch.object(line_push.requests, "post", return_value=resp), \
                mock.patch.object(line_push, "_alert_once"):
            self.assertFalse(line_push.reply_text("RT", "ok"))
        self.assertEqual(len(sm._mem), 0)


class HandleMessageQuoteTests(unittest.TestCase):
    def setUp(self):
        sm.reset()
        for p in [
            mock.patch.object(sm, "ASYNC_WRITE", False),
            mock.patch.object(sm, "_persist", lambda e: None),
            mock.patch.object(commands, "rate_limit_check", return_value=True),
            mock.patch.object(commands, "update_last_activity"),
            mock.patch.object(commands, "get_member_label", return_value="毛毛"),
            mock.patch.object(commands, "_remember"),
            mock.patch.object(commands, "_should_log", return_value=False),
            mock.patch.object(commands, "handle_notebook_command", return_value=False),
            mock.patch.object(commands, "handle_link_command", return_value=False),
            mock.patch.object(commands, "handle_mention", return_value="嗨嗨"),
            mock.patch("line_push.reply_text", return_value=True),
        ]:
            p.start()
        self.addCleanup(mock.patch.stopall)

    def _event(self, text, quoted):
        src = mock.Mock(group_id="C1", user_id="U")
        msg = mock.Mock(text=text, quoted_message_id=quoted)
        return mock.Mock(message=msg, reply_token="RT", source=src)

    def test_quote_bot_triggers_and_passes_quoted_text(self):
        sm.record([("100", "今天天氣很好")], "C1")
        commands.handle_message(self._event("真的嗎", "100"))
        commands.handle_mention.assert_called_once_with("真的嗎", member="毛毛", quoted="今天天氣很好")

    def test_quote_someone_else_not_triggered(self):
        sm.record([("100", "bot 說的")], "C1")
        with mock.patch.object(ms, "_values_get", return_value=[]):
            commands.handle_message(self._event("真的嗎", "999"))
        commands.handle_mention.assert_not_called()

    def test_no_quote_unchanged(self):
        commands.handle_message(self._event("大家午安", None))
        commands.handle_mention.assert_not_called()
        commands.handle_message(self._event("小棉襖你好", None))
        commands.handle_mention.assert_called_once_with("小棉襖你好", member="毛毛", quoted=None)

    def test_empty_table_no_data(self):
        with mock.patch.object(ms, "_values_get", return_value=[]):
            commands.handle_message(self._event("嗯", "100"))
        commands.handle_mention.assert_not_called()

    def test_sheet_failure_does_not_break_normal_reply(self):
        def boom(r):
            raise ms.SheetsError("HTTP 503")
        with mock.patch.object(ms, "_values_get", boom):
            commands.handle_message(self._event("嗯", "100"))           # 引用查詢失敗：當作沒被點名
            commands.handle_message(self._event("@小棉襖 嗨", "100"))  # 明確 @ 仍正常回覆
        self.assertEqual(commands.handle_mention.call_count, 1)
        import line_push
        line_push.reply_text.assert_called_once_with("RT", "嗨嗨")

    def test_quote_bot_with_explicit_command_still_runs_command(self):
        sm.record([("100", "x")], "C1")
        with mock.patch.object(commands, "fetch_imdb", return_value="R"), \
                mock.patch.object(commands, "reply") as rep:
            commands.handle_message(self._event("查電影 貓貓", "100"))
        rep.assert_called_once_with("RT", "R")

    def test_prompt_contains_quoted_sentence(self):
        import api_helpers
        with mock.patch.object(api_helpers, "get_memories", return_value=[]), \
                mock.patch.object(api_helpers, "get_personal_memories", return_value=[]), \
                mock.patch.object(api_helpers, "build_memory_context", return_value=""), \
                mock.patch.object(api_helpers, "call_ai", return_value="ok") as ai:
            api_helpers.handle_mention("真的嗎", member="毛毛", quoted="今天天氣很好")
        self.assertIn("回覆你之前說的這句：「今天天氣很好」", ai.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
