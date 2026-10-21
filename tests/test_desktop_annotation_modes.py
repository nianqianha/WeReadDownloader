"""Run the real callbacks without a display, account or worker process.

The three dropdown rows are not three degrees of one switch: 「注释和想法」 asks
the worker for the unified appendix (notes and thoughts into the book's end
appendix — the form the reference EPUB uses), 「只要划线」 keeps highlights and
drops note text, 「纯正文」 asks for neither. This file pins the exact worker
arguments each row produces, and that the appendix flag reaches only the export
that writes a book -- the first-chapter probe has nothing to append to.
"""
import ast
from pathlib import Path
from types import MethodType, SimpleNamespace
import unittest


class DesktopAnnotationModesTests(unittest.TestCase):
    def setUp(self):
        path = Path(__file__).resolve().parents[1] / "weread/desktop_app.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        app = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "DesktopApp")
        methods = [n for n in app.body if isinstance(n, ast.FunctionDef)
                   and n.name in ("probe", "export", "_start_one", "_queue_args",
                                  "_annotation_mode", "_annotation_flags")]
        # The mode table is module-level, so exec of the methods alone would not see
        # it. Pull the class and the two assignments in too rather than importing
        # desktop_app (which drags in customtkinter).
        mode_class = next(n for n in tree.body if isinstance(n, ast.ClassDef)
                          and n.name == "_AnnotationMode")
        mode_tables = [n for n in tree.body if isinstance(n, ast.Assign)
                       and any(getattr(t, "id", "") in ("_ANNOTATION_MODES",
                                                        "_DEFAULT_ANNOTATION_MODE")
                               for t in n.targets)]
        nodes = [mode_class] + mode_tables + methods
        namespace = {}
        exec(compile(ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[])),
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
        self.app._start_one = MethodType(namespace["_start_one"], self.app)
        self.app._queue_args = MethodType(namespace["_queue_args"], self.app)
        self.app._annotation_mode = MethodType(namespace["_annotation_mode"], self.app)
        self.app._annotation_flags = MethodType(namespace["_annotation_flags"], self.app)
        self.methods = namespace

    def test_each_displayed_mode_reaches_both_callbacks_with_correct_flags(self):
        rows = (
            ("注释和想法", [], True),
            ("只要划线", ["--no-thoughts"], False),
            ("纯正文", ["--no-annotations"], False),
        )
        for label, flags, appendix in rows:
            for action in ("probe", "export"):
                with self.subTest(label=label, action=action):
                    self.app.annotations_var = SimpleNamespace(get=lambda label=label: label)
                    self.starts.clear()
                    self.confirmations.clear()
                    self.methods[action](self.app)
                    self.assertEqual(len(self.starts), 1)
                    arguments = self.starts[0][0]
                    self.assertEqual([a for a in arguments if a.startswith("--no-")], flags)
                    self.assertEqual("--unified-appendix" in arguments,
                                     appendix and action == "export",
                                     arguments)
                    self.assertIn("123", arguments)
                    if action == "export":
                        self.assertEqual(len(self.confirmations), 1)

    def test_the_default_row_is_the_unified_appendix(self):
        """默认档必须是「注释和想法」——用户要的正是这本书的形态。"""
        self.assertEqual(self.methods["_DEFAULT_ANNOTATION_MODE"].label, "注释和想法")

    def test_an_unknown_label_falls_back_instead_of_raising(self):
        """旧版本留下的标签不能让导出崩掉（W3 就是这么坏的）。"""
        self.app.annotations_var = SimpleNamespace(get=lambda: "both")
        mode = self.app._annotation_mode()
        self.assertEqual(mode.label, "注释和想法")
        self.assertIn("--unified-appendix", self.app._annotation_flags(appendix=True))
        self.assertNotIn("--unified-appendix", self.app._annotation_flags(appendix=False))


if __name__ == "__main__":
    unittest.main()
