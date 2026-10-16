"""Shelf failure states must finish without requiring a graphical display."""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock


class ShelfFailuresTests(unittest.TestCase):
    def setUp(self):
        path = Path(__file__).resolve().parents[1] / "weread/desktop_app.py"
        app = next(n for n in ast.parse(path.read_text(encoding="utf-8")).body
                   if isinstance(n, ast.ClassDef) and n.name == "DesktopApp")
        wanted = ("_finish_failed_action", "_handle_spawn_failed", "_show_shelf_error", "_handle_done")
        methods = [n for n in app.body if isinstance(n, ast.FunctionDef) and n.name in wanted]
        namespace = {"ctk": SimpleNamespace(CTkLabel=Mock()),
                     "_font": lambda *args: None}
        exec(compile(ast.fix_missing_locations(ast.Module(body=methods, type_ignores=[])),
                     str(path), "exec"), namespace)
        self.app = SimpleNamespace(status=Mock(), _persist_log=Mock(), _spawn_pending=True,
                                   current_action="shelf", job_seq=1, process=object(), last_failure=False,
                                   pause_button=Mock(), annotations_box=Mock(), _flush_pending_book=Mock())
        self.error = Mock()
        self.app._show_shelf_error = self.error
        self.app._finish_failed_action = lambda action: namespace["_finish_failed_action"](self.app, action)
        self.namespace = namespace

    def test_worker_failure_replaces_the_loading_state(self):
        self.app._finish_failed_action("shelf")
        self.error.assert_called_once_with()

    def test_spawn_failure_replaces_the_loading_state(self):
        self.namespace["_handle_spawn_failed"](self.app, "shelf", "synthetic spawn failure")
        self.error.assert_called_once_with()
        self.assertFalse(self.app._spawn_pending)

    def test_nonzero_exit_without_failed_line_replaces_the_loading_state(self):
        self.namespace["_handle_done"](self.app, 1, 1)
        self.error.assert_called_once_with()
        self.assertIsNone(self.app.process)


if __name__ == "__main__":
    unittest.main()
