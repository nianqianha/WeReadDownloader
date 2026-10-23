"""Run the real confirmation and deletion callbacks on disposable files."""
import ast
from pathlib import Path
import shutil
from types import MethodType, SimpleNamespace

import pytest


def callbacks(answer):
    source = Path(__file__).resolve().parents[1] / "weread/desktop_app.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    app_class = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "DesktopApp")
    methods = [n for n in app_class.body if isinstance(n, ast.FunctionDef)
               and n.name in ("_ask", "_delete_exports", "_delete_thoughts")]
    def ask(*args, **kwargs):
        assert instance._modal_depth == 1
        assert kwargs["parent"] is instance.root
        if isinstance(answer, Exception):
            raise answer
        return answer
    namespace = {"Path": Path, "shutil": shutil,
                 "messagebox": SimpleNamespace(askyesno=ask)}
    future = ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0)
    exec(compile(ast.fix_missing_locations(ast.Module(body=[future] + methods, type_ignores=[])),
                 str(source), "exec"), namespace)
    messages = []
    instance = SimpleNamespace(root=object(), _modal_depth=0, _persist_log=messages.append,
                               _refresh_page=lambda page: None,
                               status=SimpleNamespace(set=messages.append))
    for name in ("_ask", "_delete_exports", "_delete_thoughts"):
        setattr(instance, name, MethodType(namespace[name], instance))
    return instance


@pytest.mark.parametrize("answer", [False, RuntimeError("dialog unavailable")])
@pytest.mark.parametrize("operation", ["exports", "thoughts"])
def test_refusal_or_dialog_failure_preserves_files(tmp_path, answer, operation):
    instance = callbacks(answer)
    folder = tmp_path / "book"
    folder.mkdir()
    book = folder / "book.epub"
    notes = folder / "reviews.jsonl"
    book.write_bytes(b"synthetic book")
    notes.write_bytes(b"synthetic notes")
    sentinel = tmp_path / "outside.txt"
    sentinel.write_bytes(b"outside")
    if operation == "exports":
        instance._delete_exports([("book", 1, 0, "done", folder)])
    else:
        instance._delete_thoughts(folder)
    assert book.read_bytes() == b"synthetic book"
    assert notes.read_bytes() == b"synthetic notes"
    assert sentinel.read_bytes() == b"outside"
    assert instance._modal_depth == 0


def test_explicit_confirmation_still_deletes_only_selected_export(tmp_path):
    instance = callbacks(True)
    folder = tmp_path / "selected"
    folder.mkdir()
    (folder / "book.epub").write_bytes(b"synthetic book")
    other = tmp_path / "other.epub"
    other.write_bytes(b"keep")
    instance._delete_exports([("selected", 1, 0, "done", folder)])
    assert not folder.exists()
    assert other.read_bytes() == b"keep"
    assert instance._modal_depth == 0
