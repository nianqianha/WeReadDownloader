"""Exercise the real probe function without credentials, Lua or network access."""
import ast
from contextlib import redirect_stdout
import io
from pathlib import Path
import re
from types import SimpleNamespace
import unittest


class ProbeBookTests(unittest.TestCase):
    def setUp(self):
        path = Path(__file__).resolve().parents[1] / "weread" / "weread_desktop.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        definition = next(node for node in tree.body
                          if isinstance(node, ast.FunctionDef) and node.name == "probe_book")
        self.annotation_calls = []
        api = SimpleNamespace(
            fetch_book=lambda book_id: ({"bookId": book_id}, [{"chapterUid": 1}]),
            chapter_html=lambda book, chapter: ("<html><body>fixture</body></html>", ""),
            annotations=lambda *args: (self.annotation_calls.append(args) or {"underlines": []}, []),
        )
        lua = SimpleNamespace(globals=lambda: {
            b"_EXPORT": {b"inject": lambda *args: ("<body>fixture</body>", "")}
        })
        namespace = {
            "lua_runtime": lambda: lua, "WeRead": lambda settings, runtime: api,
            "lua_table": lambda runtime, value: value, "py_value": lambda value: value,
            "re": re,
        }
        module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[
            ast.alias(name="annotations")], level=0), definition], type_ignores=[])
        exec(compile(ast.fix_missing_locations(module), str(path), "exec"), namespace)
        self.probe = namespace["probe_book"]

    def test_cli_annotation_modes_reach_the_probe(self):
        for thoughts, underlines in [(True, True), (False, True), (False, False)]:
            with self.subTest(thoughts=thoughts, underlines=underlines):
                with redirect_stdout(io.StringIO()) as output:
                    self.probe("fixture-book", None, {}, thoughts, underlines)
                self.assertEqual(self.annotation_calls[-1],
                                 ("fixture-book", 1, thoughts, underlines))
                self.assertIn("PROBE_OK; chapter_uid: 1", output.getvalue())

    def test_defaults_keep_annotations_enabled(self):
        with redirect_stdout(io.StringIO()):
            self.probe("fixture-book", None, {})
        self.assertEqual(self.annotation_calls, [("fixture-book", 1, True, True)])


if __name__ == "__main__":
    unittest.main()
