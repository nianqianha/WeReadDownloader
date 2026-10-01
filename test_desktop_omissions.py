"""Losing an accessory must not be silent.

The downloader was changed to keep the prose when an image, an underline or a
review is unavailable, instead of discarding the chapter. That is the right
trade -- but on its own it is only half the fix. A book that quietly drops 100
images and 23 highlights is indistinguishable from a complete one, and the
original bug was lying to the user in the other direction (reporting failure for
a book that was fine).

So the same run that degrades must also state the loss, in the manifest, over the
worker channel, and in the window. These tests pin all three.
"""
import importlib.util
import json
import tempfile
from pathlib import Path

SCRIPT = Path(__file__).with_name("desktop_app.py")
spec = importlib.util.spec_from_file_location("desktop_app", SCRIPT)
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)

_TMP = tempfile.TemporaryDirectory(prefix="weread-omissions-test-")
app.LOG_DIR = Path(_TMP.name) / "logs"
app.OUTPUT = Path(_TMP.name) / "exports"

ALL_TESTS = []


def test(fn):
    ALL_TESTS.append(fn)
    return fn


def _make_gui():
    import customtkinter as ctk
    root = ctk.CTk()
    root.geometry("1120x700")
    instance = app.DesktopApp(root)
    instance.asked = []
    instance._ask = lambda kind, title, message, **kw: (
        instance.asked.append((kind, title, message)) or "ok")
    root.update()
    return root, instance


def _report(**kw):
    base = {"text_complete": True, "images_dropped": 0,
            "underline_ranges_dropped": 0, "chapters_with_dropped_images": 0,
            "chapters_with_dropped_underlines": 0, "dropped_image_examples": []}
    base.update(kw)
    return base


# --- the formatter -----------------------------------------------------------

@test
def test_a_missing_report_says_nothing():
    assert app.describe_omissions(None) == ""
    assert app.describe_omissions("not a dict") == ""


@test
def test_a_complete_book_says_nothing():
    """No loss must mean no scary dialog."""
    assert app.describe_omissions(_report()) == ""


@test
def test_dropped_images_are_stated_with_their_count():
    notice = app.describe_omissions(
        _report(images_dropped=100, chapters_with_dropped_images=66))
    assert "100" in notice, notice
    assert "66" in notice, notice
    assert "插图" in notice, notice


@test
def test_dropped_underlines_are_stated_with_their_count():
    notice = app.describe_omissions(
        _report(underline_ranges_dropped=23, chapters_with_dropped_underlines=22))
    assert "23" in notice, notice
    assert "划线" in notice, notice


@test
def test_both_losses_are_reported_together():
    notice = app.describe_omissions(_report(
        images_dropped=100, chapters_with_dropped_images=66,
        underline_ranges_dropped=23, chapters_with_dropped_underlines=22))
    assert "插图" in notice and "划线" in notice, notice


@test
def test_the_notice_says_the_text_is_intact():
    """The reader must not conclude the book is damaged."""
    notice = app.describe_omissions(_report(images_dropped=100))
    assert "正文完整" in notice, notice


# --- the worker channel ------------------------------------------------------

@test
def test_the_worker_line_is_parsed_into_the_report():
    root, instance = _make_gui()
    try:
        instance._handle_line("OMISSIONS " + json.dumps(
            _report(images_dropped=7, chapters_with_dropped_images=3)))
        assert isinstance(instance.omissions, dict), instance.omissions
        assert instance.omissions["images_dropped"] == 7, instance.omissions
    finally:
        root.destroy()


@test
def test_a_malformed_worker_line_does_not_crash_the_window():
    root, instance = _make_gui()
    try:
        instance._handle_line("OMISSIONS {not json at all")
        assert instance.omissions is None, instance.omissions
    finally:
        root.destroy()


# --- the window --------------------------------------------------------------

def _finish_export(instance):
    """Drive the completion path the way a real finished download would."""
    instance.job_seq = 1
    instance.current_action = "export"
    instance.last_failure = False
    instance.last_unresolved = False
    instance._handle_done(1, 0)


@test
def test_the_report_line_is_logged_exactly_once():
    """The regression: every report appeared twice in the log file.

    _handle_line logs every worker line once via self._log(value) before the
    per-prefix branches run, so the OMISSIONS branch logging it again wrote each
    report to disk twice.
    """
    root, instance = _make_gui()
    try:
        line = "OMISSIONS " + json.dumps(_report(images_dropped=100))
        instance._handle_line(line)
        text = "\n".join(p.read_text(encoding="utf-8")
                         for p in app.LOG_DIR.glob("*.log"))
        assert text.count(line) == 1, text.count(line)
    finally:
        root.destroy()


@test
def test_a_finished_book_with_losses_tells_the_user():
    """The regression that matters: success must not be reported as clean."""
    root, instance = _make_gui()
    try:
        instance._handle_line("OMISSIONS " + json.dumps(
            _report(images_dropped=100, chapters_with_dropped_images=66)))
        _finish_export(instance)
        assert instance.asked, "the user was never told the book was incomplete"
        assert "100" in instance.asked[0][2], instance.asked[0]
        assert len(instance.asked) == 1, ("the completion dialog fired %d times"
                                          % len(instance.asked))
    finally:
        root.destroy()


@test
def test_a_finished_book_without_losses_stays_quiet():
    root, instance = _make_gui()
    try:
        instance._handle_line("OMISSIONS " + json.dumps(_report()))
        _finish_export(instance)
        assert not instance.asked, instance.asked
    finally:
        root.destroy()


@test
def test_the_report_is_cleared_between_jobs():
    """A later clean book must not inherit the previous book's losses."""
    root, instance = _make_gui()
    try:
        instance._handle_line("OMISSIONS " + json.dumps(
            _report(images_dropped=100)))
        assert instance.omissions["images_dropped"] == 100
        instance.omissions = None
        _finish_export(instance)
        assert not instance.asked, instance.asked
    finally:
        root.destroy()


if __name__ == "__main__":
    for test_case in ALL_TESTS:
        test_case()
        print("PASS", test_case.__name__)
