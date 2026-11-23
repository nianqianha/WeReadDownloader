# -*- coding: utf-8 -*-
"""下载完成后可开启「在资源管理器里定位成品 EPUB」（issue #97）。

三件事：偏好落盘且能读回、勾选后完成才定位、没勾选绝不动（自动弹窗对批量下载
是打扰）。定位优先 explorer /select 选中文件，文件不在时退回打开导出目录。
"""
import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "weread"))
import weread_core

SCRIPT = Path(__file__).resolve().parent.parent / "weread" / "desktop_app.py"
spec = importlib.util.spec_from_file_location("desktop_app", SCRIPT)
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)


class PrefsTests(unittest.TestCase):
    def test_roundtrip_and_tolerance(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        with patch.object(weread_core, "PRIVATE", Path(temp.name)):
            self.assertEqual(weread_core.load_ui_prefs(), {})
            weread_core.save_ui_prefs({"open_output_when_done": True})
            self.assertEqual(weread_core.load_ui_prefs(),
                             {"open_output_when_done": True})
            # 损坏的文件不能把界面启动搞崩
            weread_core.prefs_path().write_text("not json", encoding="utf-8")
            self.assertEqual(weread_core.load_ui_prefs(), {})


class CompletionOpenTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root_dir = Path(self.temp.name)
        app.LOG_DIR = self.root_dir / "logs"
        app.OUTPUT = self.root_dir / "exports"
        app.weread_login.CREDENTIAL_STATE = self.root_dir / "session-cookies.json"
        patcher = patch.object(weread_core, "PRIVATE", self.root_dir)
        patcher.start()
        self.addCleanup(patcher.stop)
        import customtkinter as ctk
        self.ctk = ctk
        self.root = ctk.CTk()
        self.instance = app.DesktopApp(self.root)
        self.root.update()

    def tearDown(self):
        try:
            self.root.destroy()
        except Exception:
            pass

    def _visit_settings(self):
        """设置页是懒建的：勾选框只在这一页创建后才存在。"""
        self.instance.load_shelf = lambda: None
        self.instance._switch_page("设置")
        self.root.update()

    def test_default_is_off_and_unchecked(self):
        self.assertFalse(self.instance._open_when_done())
        self._visit_settings()
        self.assertFalse(bool(self.instance.open_when_done_var.get()))

    def test_checking_the_box_persists_the_preference(self):
        self._visit_settings()
        self.instance.open_when_done_var.set(True)
        self.instance._save_open_when_done()
        saved = weread_core.load_ui_prefs()
        self.assertTrue(saved.get("open_output_when_done"))
        self.assertTrue(self.instance._open_when_done())

    def test_done_line_remembers_the_artifact_path(self):
        artifact = str(self.root_dir / "exports" / "书-1" / "书 - full.epub")
        self.instance._handle_line("DONE " + artifact)
        self.assertEqual(self.instance._last_artifact, artifact)

    def test_reveal_passes_select_and_path_as_separate_arguments(self):
        """issue #100：合成一个字符串会被 explorer 在空格处截断、跳到「文档」库。"""
        epub = self.root_dir / "书 - full.epub"   # 路径含空格，正是触发条件
        epub.write_bytes(b"x")
        calls = []
        with patch.object(app.subprocess, "Popen", lambda argv: calls.append(argv)):
            self.instance._reveal_output(str(epub))
        # 必须是三个 argv：explorer /select, <path>；不是两个（/select,<path>）
        self.assertEqual(calls, [["explorer", "/select,", str(epub)]])
        self.assertNotIn("/select,%s" % epub, calls[0])

    def test_reveal_falls_back_to_the_file_folder_then_export_root(self):
        """定位失败时先开成品所在目录，最后才退回导出根目录。"""
        artifact_dir = self.root_dir / "book dir"
        artifact_dir.mkdir(exist_ok=True)
        missing = artifact_dir / "missing.epub"
        opened = []
        with patch.object(app.subprocess, "Popen", side_effect=OSError("fail")), \
                patch.object(app.os, "startfile", lambda p: opened.append(p)), \
                patch.object(self.instance, "open_output",
                             lambda: opened.append("ROOT")):
            # 文件不存在时走 parent.is_dir() 分支
            self.instance._reveal_output(str(missing))
            self.assertEqual(opened, [str(artifact_dir)])
        # 父目录也不存在时最后退回导出根目录
        opened.clear()
        with patch.object(self.instance, "open_output", lambda: opened.append("ROOT")):
            self.instance._reveal_output(str(self.root_dir / "no" / "such.epub"))
        self.assertEqual(opened, ["ROOT"])


if __name__ == "__main__":
    unittest.main()
