"""用假的 Sheets 驗證 memory_store，完全不碰真實試算表。
執行：cd line-group-bot && python3 -m unittest tests.test_memory_store -v
"""
import os, re, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
os.environ.setdefault("STATE_DB_PATH", "/tmp/_test_state.db")
import memory_store as ms


class FakeSheet:
    def __init__(self, n_log=0):
        self.tabs = {ms.LOG_TAB: [["time", "member", "msg"]] + [["t", "A", f"m{i}"] for i in range(n_log)],
                     ms.SUMMARY_TAB: [["scope", "summary", "watermark", "updated_at"]]}
        self.fail_update = False

    def add(self, k):
        for i in range(k):
            self.tabs[ms.LOG_TAB].append(["t", "B", f"n{len(self.tabs[ms.LOG_TAB])}"])

    @staticmethod
    def _parse(rng):
        tab, a = rng.split("!")
        m = re.match(r"([A-D])(\d*)(?::([A-D])(\d*))?$", a)
        return tab, m

    def get(self, rng):
        tab, m = self._parse(rng)
        rows = self.tabs[tab]
        if m.group(1) == "A" and m.group(2) == "" and m.group(3) == "A":
            return [[r[0]] for r in rows]
        s = int(m.group(2)); e = int(m.group(4)) if m.group(4) else len(rows)
        return [r for r in rows[s - 1:e] if r]

    def update(self, rng, values):
        if self.fail_update:
            raise ms.SheetsError("HTTP 500: boom")
        tab, m = self._parse(rng)
        s = int(m.group(2))
        rows = self.tabs[tab]
        while len(rows) < s:
            rows.append([])
        rows[s - 1] = [str(x) for x in values[0]]


class T(unittest.TestCase):
    def setUp(self):
        self.sheet = FakeSheet(n_log=0)
        ms._values_get = self.sheet.get
        ms._values_update = self.sheet.update
        ms._ensure_tab = lambda: None
        ms._now_str = lambda: "2026-11-22 10:00"
        ms.reset_cache(); ms._counter.update(n=0, fails=0)
        ms.BOOTSTRAP_ROWS = 200

    def restart(self):
        ms.reset_cache()  # 模擬重啟：記憶體清空，只剩 Sheet

    def test_write_then_read_after_restart(self):
        self.sheet.add(5)
        ctx1 = ms.build_context()
        self.restart()
        ctx2 = ms.build_context()
        self.assertEqual(ctx1, ctx2)
        self.assertIn("n5", ctx2)

    def test_no_compact_below_threshold(self):
        self.sheet.add(30)
        self.assertEqual(ms.compact_if_needed(llm=lambda p: "x" * 50), "skipped")

    def test_compact_over_threshold_and_context(self):
        self.sheet.add(40)
        seen = []
        r = ms.compact_if_needed(llm=lambda p: seen.append(p) or "• 他們聊了很多事情，計畫週末出去玩")
        self.assertEqual(r, "compacted")
        st = ms._read_state()
        self.assertEqual(st["watermark"], 1 + 30)  # 40 列，留最近 10 列原文
        self.restart()
        ctx = ms.build_context()
        self.assertIn("較早的對話摘要", ctx)
        self.assertIn("計畫週末", ctx)
        self.assertIn("n40", ctx)        # 最近原文還在
        self.assertNotIn("：m0\n", ctx)   # 舊的已濃縮，不重複出現
        self.assertEqual(len(self.sheet.tabs[ms.LOG_TAB]), 41)  # 原文一列都沒刪

    def test_llm_failure_keeps_everything(self):
        self.sheet.add(40)
        for bad in (lambda p: None, lambda p: "短", lambda p: (_ for _ in ()).throw(RuntimeError("x"))):
            self.assertEqual(ms.compact_if_needed(llm=bad), "llm_failed")
        st = ms._read_state()
        self.assertEqual(st["watermark"], 1)  # 沒前進
        self.assertIn("n40", ms.build_context())
        self.assertIn("：n1\n", ms.build_context())  # 最舊原文仍會注入

    def test_write_failure_keeps_watermark(self):
        self.sheet.add(40)
        ms.get_state(refresh=True)
        self.sheet.fail_update = True
        self.assertEqual(ms.compact_if_needed(llm=lambda p: "• 這是一份夠長的摘要內容"), "error")
        self.sheet.fail_update = False
        self.assertEqual(ms._read_state()["watermark"], 1)

    def test_batch_char_budget(self):
        self.sheet.tabs[ms.LOG_TAB] += [["t", "A", "字" * 300] for _ in range(60)]
        ms.compact_if_needed(llm=lambda p: self.assertLessEqual(len(p), 8000) or "• 摘要摘要摘要摘要")
        self.assertGreater(ms._read_state()["watermark"], 1)

    def test_second_compaction_merges_old_summary(self):
        self.sheet.add(40)
        ms.compact_if_needed(llm=lambda p: "• 第一版摘要內容喔喔喔")
        self.sheet.add(40)
        got = []
        ms.compact_if_needed(llm=lambda p: got.append(p) or "• 第二版摘要內容喔喔喔")
        self.assertIn("第一版摘要", got[0])

    def test_sheets_down_falls_back_to_local(self):
        def boom(r): raise ms.SheetsError("HTTP 503")
        ms._values_get = boom
        ms.reset_cache()
        import state
        state.chat_append("X", "本機最近訊息")
        self.assertIn("本機最近訊息", ms.build_context())

    def test_bootstrap_skips_old_history(self):
        ms.BOOTSTRAP_ROWS = 50
        self.sheet.add(1000)
        ms.get_state()
        self.assertEqual(ms._read_state()["watermark"], 1001 - 50)


if __name__ == "__main__":
    unittest.main()
