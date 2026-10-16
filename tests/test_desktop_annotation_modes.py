"""Run the real callbacks without a display, account or worker process."""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest


class DesktopAnnotationModesTests(unittest.TestCase):
    def setUp(self):
        path = Path(__file__).resolve().parents[1] / "weread/desktop_app.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        app = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "DesktopApp")
        methods = [n for n in app.body if isinstance(n, ast.FunctionDef)
                   and n.name in ("probe", "export")]
        namespace = {}
        exec(compile(ast.fix_missing_locations(ast.Module(body=methods, type_ignores=[])),
                     str(path), "exec"), namespace)
        self.starts = []
        self.confirmations = []
        self.app = SimpleNamespace(
            selected_id="123", selected_title="Synthetic book", selected_author="Test",
            _is_busy=lambda: False, status=SimpleNamespace(set=lambda value: None),
            jobs=SimpleNamespace(get=lambda: "1"), progress=SimpleNamespace(set=lambda value: None),
            _start_worker=lambda *args: self.starts.append(args) or True,
            _ask=lambda *args: self.confirmations.append(args) or True,
        )
        self.methods = namespace

    def test_each_displayed_mode_reaches_both_callbacks_with_correct_flags(self):
        for label, flags in (("划线 + 想法", []), ("只要划线", ["--no-thoughts"]),
                             ("纯正文", ["--no-annotations"])):
            for action in ("probe", "export"):
                with self.subTest(label=label, action=action):
                    self.app.annotations_var = SimpleNamespace(get=lambda label=label: label)
                    self.starts.clear()
                    self.confirmations.clear()
                    self.methods[action](self.app)
                    self.assertEqual(len(self.starts), 1)
                    arguments = self.starts[0][0]
                    self.assertEqual([a for a in arguments if a.startswith("--no-")], flags)
                    self.assertIn("123", arguments)
                    if action == "export":
                        self.assertEqual(len(self.confirmations), 1)


if __name__ == "__main__":
    unittest.main()
