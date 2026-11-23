"""2026-11-24 審查修復（4 個 critical＋5 個一行修法）的測試。全部 mock，不打外部 API、不碰真實 Sheet。
執行：cd line-group-bot && python3 -W ignore -m unittest discover -s tests -v
"""
import ast
import os
import sys
import tempfile
import threading
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
os.environ.setdefault("STATE_DB_PATH", os.path.join(tempfile.gettempdir(), "_test_state_1124.db"))
os.environ.setdefault("LINE_CHANNEL_SECRET", "test-secret")
os.environ.setdefault("LINE_CHANNEL_ACCESS_TOKEN", "test-token")

SCRIPTS = os.path.join(os.path.dirname(__file__), "..", "scripts")


class FakeResp:
    def __init__(self, status=200, headers=None, body=b"", encoding="utf-8"):
        self.status_code = status
        self.ok = 200 <= status < 300
        self.headers = headers or {}
        self.is_redirect = status in (301, 302, 303, 307, 308) and "Location" in self.headers
        self._body = body
        self.encoding = encoding
        self.read_bytes = 0
        self.closed = False

    def raise_for_status(self):
        if not self.ok:
            import requests
            raise requests.HTTPError(f"{self.status_code}")

    def iter_content(self, chunk_size=16384):
        while True:
            self.read_bytes += chunk_size
            yield b"x" * chunk_size if self._body is None else self._body[:chunk_size]
            if self._body is not None:
                return

    def close(self):
        self.closed = True

    def json(self):
        return {}


# ── critical 1：寫入鎖重入 ──────────────────────────────
class TestWriteLockReentrant(unittest.TestCase):
    def _run_with_cleanup_forced(self, fn):
        done = threading.Event()

        def target():
            with mock.patch("random.random", return_value=0.0):  # 強制觸發 _maybe_cleanup
                fn()
            done.set()

        t = threading.Thread(target=target, daemon=True)
        t.start()
        return done.wait(3)

    def test_state_kv_set_with_cleanup_does_not_deadlock(self):
        import state
        self.assertTrue(self._run_with_cleanup_forced(lambda: state.kv_set("k1124", 1, ttl_seconds=60)),
                        "kv_set 觸發 _maybe_cleanup 時卡死")

    def test_tts_store_lock_is_reentrant(self):
        import tts_store
        lock = tts_store._write_lock
        self.assertTrue(lock.acquire(timeout=1))
        try:
            self.assertTrue(lock.acquire(timeout=1), "tts_store._write_lock 不可重入")
            lock.release()
        finally:
            lock.release()


# ── critical 2：只服務自己的群組 ────────────────────────
class TestSourceAllowed(unittest.TestCase):
    def setUp(self):
        import commands
        self.c = commands

    def src(self, **kw):
        return types.SimpleNamespace(**kw)

    @mock.patch.dict(os.environ, {"LINE_GROUP_ID": "Cmine", "ALLOWED_USER_IDS": ""})
    def test_own_group_allowed(self):
        self.assertTrue(self.c._source_allowed(self.src(group_id="Cmine", user_id="U1")))

    @mock.patch.dict(os.environ, {"LINE_GROUP_ID": "Cmine", "ALLOWED_USER_IDS": ""})
    def test_other_group_and_stranger_dm_blocked(self):
        self.assertFalse(self.c._source_allowed(self.src(group_id="Cother", user_id="U1")))
        self.assertFalse(self.c._source_allowed(self.src(user_id="Ustranger")))
        self.assertFalse(self.c._source_allowed(self.src(room_id="R1", user_id="U1")))

    @mock.patch.dict(os.environ, {"LINE_GROUP_ID": "Cmine", "ALLOWED_USER_IDS": "Ua, Ub"})
    def test_allowlisted_dm(self):
        self.assertTrue(self.c._source_allowed(self.src(user_id="Ub")))

    @mock.patch.dict(os.environ, {"LINE_GROUP_ID": "Cmine", "ALLOWED_USER_IDS": ""})
    def test_handle_message_ignores_stranger_dm(self):
        event = types.SimpleNamespace(
            source=self.src(user_id="Ustranger"),
            reply_token="t",
            message=types.SimpleNamespace(text="!查暱稱", quoted_message_id=None),
        )
        with mock.patch.object(self.c, "reply") as rep, \
             mock.patch.object(self.c, "run_regex_commands") as rx:
            self.c.handle_message(event)
        rep.assert_not_called()
        rx.assert_not_called()


# ── critical 3：抓網址標題不打內網、不整包下載 ──────────
class TestFetchTitle(unittest.TestCase):
    def setUp(self):
        from handlers import links
        self.links = links

    def test_localhost_and_private_not_fetched(self):
        with mock.patch.object(self.links.requests, "get") as g:
            for u in ("http://127.0.0.1:8080/admin", "http://localhost/x",
                      "http://169.254.169.254/latest/meta-data/", "http://10.0.0.5/", "file:///etc/passwd"):
                self.assertEqual(self.links.fetch_title(u), (u, ""), u)
        g.assert_not_called()

    def test_redirect_to_private_is_blocked(self):
        first = FakeResp(302, {"Location": "http://127.0.0.1/secret"})
        with mock.patch.object(self.links, "_host_is_public", side_effect=lambda h: h != "127.0.0.1"), \
             mock.patch.object(self.links.requests, "get", return_value=first) as g:
            self.assertEqual(self.links.fetch_title("http://example.com/r"), ("http://example.com/r", ""))
        self.assertEqual(g.call_count, 1)

    def test_large_body_read_is_capped(self):
        big = FakeResp(200, {"Content-Type": "text/html"}, body=None)  # 無限串流
        with mock.patch.object(self.links, "_host_is_public", return_value=True), \
             mock.patch.object(self.links.requests, "get", return_value=big):
            self.links.fetch_title("http://example.com/huge")
        self.assertLessEqual(big.read_bytes, self.links._MAX_FETCH_BYTES + 16384)
        self.assertTrue(big.closed)

    def test_non_html_skipped(self):
        vid = FakeResp(200, {"Content-Type": "video/mp4"}, body=None)
        with mock.patch.object(self.links, "_host_is_public", return_value=True), \
             mock.patch.object(self.links.requests, "get", return_value=vid):
            self.assertEqual(self.links.fetch_title("http://example.com/a.mp4"), ("http://example.com/a.mp4", ""))
        self.assertEqual(vid.read_bytes, 0)

    def test_normal_page_title(self):
        page = FakeResp(200, {"Content-Type": "text/html; charset=utf-8"},
                        body="<html><title>好文章</title><p>內文</p></html>".encode())
        with mock.patch.object(self.links, "_host_is_public", return_value=True), \
             mock.patch.object(self.links.requests, "get", return_value=page):
            self.assertEqual(self.links.fetch_title("http://example.com/p"), ("好文章", "內文"))


# ── critical 4：Sheets 寫入失敗不能回成功 ────────────────
class TestSheetsWriteFailure(unittest.TestCase):
    def test_goal_append_raises_on_error(self):
        import goal_tracker
        with mock.patch.object(goal_tracker.requests, "post", return_value=FakeResp(500)), \
             mock.patch("time.sleep"):
            with self.assertRaises(Exception):
                goal_tracker._sheets_append("tok", "打卡!A:E", [["x"]])

    def test_goal_update_raises_on_error(self):
        import goal_tracker
        with mock.patch.object(goal_tracker.requests, "put", return_value=FakeResp(403)), \
             mock.patch("time.sleep"):
            with self.assertRaises(Exception):
                goal_tracker._sheets_update("tok", "目標!C2:D2", [["x", "y"]])

    def _append_row_fails(self, mod):
        with mock.patch.object(mod, "_get_token", return_value="tok"), \
             mock.patch.object(mod, "_SHEET_ID", "sheet"), \
             mock.patch.object(mod, "_ensure_tab"), \
             mock.patch.object(mod.requests, "post", return_value=FakeResp(403)):
            return mod._append_row(["a", "b"])

    def test_notebook_append_reports_failure(self):
        from handlers import notebook
        self.assertFalse(self._append_row_fails(notebook))

    def test_links_append_reports_failure(self):
        from handlers import links
        self.assertFalse(self._append_row_fails(links))


# ── 一行修法 ────────────────────────────────────────────
class TestOneLineFixes(unittest.TestCase):
    def _module_imports(self, filename):
        tree = ast.parse(open(os.path.join(SCRIPTS, filename), encoding="utf-8").read())
        names = set()
        for node in tree.body:
            if isinstance(node, ast.Import):
                names.update(a.name for a in node.names)
        return names

    def test_weather_imports_threading(self):
        self.assertIn("threading", self._module_imports("weather.py"))

    def test_horoscope_imports_base64(self):
        self.assertIn("base64", self._module_imports("horoscope.py"))

    def test_handle_message_has_no_local_os(self):
        """函式內 `import os` 會讓整個 handle_message 的 os 變區域變數，前面用到 os 就 UnboundLocalError。"""
        import commands
        self.assertNotIn("os", commands.handle_message.__code__.co_varnames)

    def test_vote_is_saved(self):
        from handlers import vote
        store = {}
        with mock.patch.object(vote, "vote_get", side_effect=lambda g: __import__("copy").deepcopy(store.get(g))), \
             mock.patch.object(vote, "vote_set", side_effect=lambda g, d: store.__setitem__(g, d)), \
             mock.patch.object(vote, "vote_delete", side_effect=lambda g: store.pop(g, None)), \
             mock.patch.object(vote, "MEMBERS", ["甲", "乙", "丙"]):
            vote.handle_vote("投票 週末去哪 北部 中部 南部", "G", "甲")
            vote.handle_vote("投A", "G", "甲")
            self.assertEqual(store["G"]["votes"], {"甲": "A"})

    def test_lai_yi_ti_goes_to_quiz(self):
        import dispatch
        text, _ = dispatch.try_dispatch("來一題")
        self.assertIsNone(text, "「來一題」被 dispatch 搶走，互動答題走不到")


if __name__ == "__main__":
    unittest.main()
