# -*- coding: utf-8 -*-
"""下载卡片的档位要记住（issue #98）。

标注方式 / 译文 / 划线以前只活在内存变量里，重启就回默认。现在三个都写进
ui-prefs.json，启动时恢复；非法值回退默认，不能让导出崩（W3 的教训）。
另外启动时静默检查登录：只改右上角角标，不弹提示、不抢状态栏。
"""
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "weread"))
import weread_core

SCRIPT = Path(__file__).resolve().parent.parent / "weread" / "desktop_app.py"
spec = importlib.util.spec_from_file_location("desktop_app_prefs", SCRIPT)
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)


class ExportPrefsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        app.LOG_DIR = root / "logs"
        app.OUTPUT = root / "exports"
        app.weread_login.CREDENTIAL_STATE = root / "session-cookies.json"
        self.core_patch = patch.object(weread_core, "PRIVATE", root)
        self.core_patch.start()
        self.addCleanup(self.core_patch.stop)
        import customtkinter as ctk
        self.root = ctk.CTk()
        self.instance = app.DesktopApp(self.root)
        self.root.update()

    def tearDown(self):
        # 每个用例把偏好清回空：本文件改过的档位不能泄漏给同进程里之后新建的
        # DesktopApp（queue 等测试期望默认档，曾因此全量组合运行时拿到「纯正文」）。
        try:
            weread_core.save_ui_prefs({})
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass

    def test_changing_the_download_card_persists_all_three_prefs(self):
        self.instance.annotations_var.set("纯正文")
        self.root.update()
        self.instance.translation_var.set("仅译文")
        self.root.update()
        self.instance.underline_var.set(False)
        self.root.update()
        saved = weread_core.load_ui_prefs()
        self.assertEqual(saved.get("annotation_mode"), "纯正文")
        self.assertEqual(saved.get("translation_mode"), "仅译文")
        self.assertEqual(saved.get("underline_marks"), False)

    def test_prefs_are_restored_on_the_next_launch(self):
        weread_core.save_ui_prefs({"annotation_mode": "注释",
                                   "translation_mode": "仅原文",
                                   "underline_marks": False})
        # 新实例（同一进程模拟重启；PRIVATE 仍指向临时目录）
        new_root = __import__("customtkinter").CTk()
        try:
            inst = app.DesktopApp(new_root)
            new_root.update()
            self.assertEqual(inst.annotations_var.get(), "注释")
            self.assertEqual(inst.translation_var.get(), "仅原文")
            self.assertFalse(bool(inst.underline_var.get()))
        finally:
            new_root.destroy()

    def test_an_unknown_saved_annotation_label_falls_back_to_default(self):
        weread_core.save_ui_prefs({"annotation_mode": "both"})
        new_root = __import__("customtkinter").CTk()
        try:
            inst = app.DesktopApp(new_root)
            new_root.update()
            self.assertEqual(inst.annotations_var.get(), "注释和想法")
            self.assertEqual(inst._annotation_mode().label, "注释和想法")
        finally:
            new_root.destroy()

    def test_silent_auth_only_touches_the_badge_not_the_status_bar(self):
        inst = self.instance
        inst.status.set("原来的状态文字")
        inst._silent_auth_pending = True
        # 模拟一个失败的静默检查回调
        app.DesktopApp._handle_done  # noqa: B018 - ensure method exists
        inst.auth_badge.configure(text="检查中…")
        # 直接调用处理逻辑的 auth 分支：用 _handle_done 需要 job 号匹配
        inst.job_seq = 7
        inst.current_action = "auth"
        inst.process = SimpleNamespace()
        inst._service_job = None
        with patch.object(inst, "_report_login_problem") as fail:
            inst._handle_done(7, 1)
        fail.assert_not_called()
        self.assertEqual(inst.auth_badge.cget("text"), "需要登录")
        self.assertEqual(inst.status.get(), "原来的状态文字")
        self.assertFalse(inst._silent_auth_pending)

    def test_a_manual_auth_still_shows_the_friendly_error(self):
        inst = self.instance
        inst.job_seq = 8
        inst.current_action = "auth"
        inst.process = SimpleNamespace()
        inst._silent_auth_pending = False
        with patch.object(inst, "_report_login_problem") as fail:
            inst._handle_done(8, 1)
        fail.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
