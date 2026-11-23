# -*- coding: utf-8 -*-
"""运行记录面板：技术行的「大白话」实时视图（issue #99）。

守住：
  1. worker 的进度/登录/限流行翻成人话，技术行（[SERVICE]/堆栈/接口返回）不上屏；
  2. 面板只留最近 200 条，长任务不撑爆内存；
  3. 切页（页面重建）后缓存仍在，切回来能看到之前的内容；
  4. 左侧导航有「运行记录」入口。
"""
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "weread"))
SCRIPT = Path(__file__).resolve().parent.parent / "weread" / "desktop_app.py"
spec = importlib.util.spec_from_file_location("desktop_app_runlog", SCRIPT)
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)

_TMP = tempfile.TemporaryDirectory(prefix="weread-runlog-")
app.LOG_DIR = Path(_TMP.name) / "logs"
app.OUTPUT = Path(_TMP.name) / "exports"
app.weread_login.CREDENTIAL_STATE = Path(_TMP.name) / "session-cookies.json"


class PlainLineTests(unittest.TestCase):
    def test_progress_line(self):
        text = app._plain_run_line("[12/155] Fetching chapter UID 8")
        self.assertIn("第 12/155 章", text)

    def test_login_and_throttle(self):
        self.assertIn("登录状态正常", app._plain_run_line("AUTH_OK; shelf entries: 8"))
        self.assertIn("访问频率限制", app._plain_run_line("[THROTTLE] sleeping"))
        self.assertIn("访问频率限制", app._plain_run_line("[RETRY] 426 Upgrade Required"))

    def test_technical_lines_are_hidden(self):
        for line in ("[SERVICE] search took 23 ms", "[CSS] chapter 9 stylesheet",
                     "Traceback (most recent call last):", "[ACL] could not restrict",
                     "some raw json {\"a\":1}"):
            self.assertEqual(app._plain_run_line(line), "", line)

    def test_image_failure_is_plain_language(self):
        text = app._plain_run_line("[IMAGE] chapter 9: dropped 3 images")
        self.assertIn("插图", text)
        self.assertIn("正文没受影响", text)


class RunLogPanelTests(unittest.TestCase):
    def setUp(self):
        from unittest.mock import patch
        import customtkinter as ctk
        # 启动静默检查会起真实 --check-auth 子进程；GUI 测试里禁用。
        ready = patch.object(app.DesktopApp, "_credentials_ready_for_check",
                             lambda self: False)
        ready.start()
        self.addCleanup(ready.stop)
        self.root = ctk.CTk()
        self.instance = app.DesktopApp(self.root)
        self.root.update()

    def tearDown(self):
        try:
            self.root.destroy()
        except Exception:
            pass

    def test_nav_has_a_run_log_entry(self):
        self.assertIn("运行记录", self.instance.nav_items)

    def test_recorded_lines_reach_the_panel_and_survive_a_rebuild(self):
        inst = self.instance
        inst.load_shelf = lambda: None
        inst._handle_line("[3/10] Fetching chapter UID 1")
        inst._handle_line("[SERVICE] took 5 ms")   # 不上屏
        inst._handle_line("AUTH_OK; shelf entries: 8")
        self.assertEqual(len(inst._run_log), 2)
        inst._switch_page("运行记录")
        self.root.update()
        box = inst._run_log_box
        self.assertIsNotNone(box)
        content = box.get("1.0", "end")
        self.assertIn("第 3/10 章", content)
        self.assertIn("登录状态正常", content)
        self.assertNotIn("[SERVICE]", content)
        # 切走再切回：缓存重建
        inst._switch_page("下载工坊")
        inst._switch_page("运行记录")
        self.root.update()
        self.assertIn("第 3/10 章", inst._run_log_box.get("1.0", "end"))

    def test_the_cache_is_bounded(self):
        inst = self.instance
        for i in range(250):
            inst._record_run_line("[1/2] chapter %d" % i)
        self.assertEqual(len(inst._run_log), 200)


if __name__ == "__main__":
    unittest.main()
