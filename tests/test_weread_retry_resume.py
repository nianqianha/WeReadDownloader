"""Local tests: bounded chapter retries and resume-workspace discovery.

Every network call is replaced by a fake; nothing here touches WeRead.
"""
import importlib.util
import tempfile
from pathlib import Path

import requests
import sys

# The program modules live in weread/ and import each other by bare name.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "weread"))
SCRIPT = (Path(__file__).resolve().parent.parent / "weread" / "weread_desktop.py")
spec = importlib.util.spec_from_file_location("weread_desktop", SCRIPT)
wd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wd)

BOOK_ID = "12345"


class _Throttle(wd.Throttle):
    """Real Throttle with no pause, so tests do not sleep."""

    def backoff(self):
        pass

    def wait_turn(self):
        pass


def _no_sleep():
    delays = []
    return delays, delays.append


def _sequence(*outcomes):
    """Fake attempt: each call raises the next exception, or returns when None."""
    queue = list(outcomes)
    calls = []

    def attempt():
        calls.append(1)
        outcome = queue.pop(0)
        if outcome is not None:
            raise outcome
    return attempt, calls


def _quiet(fn):
    """Run fn silently; return (result, stdout+stderr text) for log assertions."""
    import contextlib, io
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        result = fn()
    return result, out.getvalue() + err.getvalue()


def test_empty_xhtml_body_is_retried_then_succeeds():
    attempt, calls = _sequence(
        RuntimeError("Chapter UID 1194 decoded without an XHTML body"), None)
    unresolved, delays = {}, []
    ok, _ = _quiet(lambda: wd.run_chapter_with_retries(
        "1194", attempt, _Throttle(1), unresolved, sleep=delays.append))
    assert ok is True
    assert unresolved == {}
    assert len(calls) == 2
    assert delays == [wd.CHAPTER_RETRY_BASE_DELAY]


def test_transient_transport_errors_retry_with_exponential_backoff():
    attempt, calls = _sequence(
        requests.exceptions.SSLError("EOF occurred in violation of protocol"),
        requests.exceptions.ReadTimeout("read timed out"),
        None)
    unresolved, delays = {}, []
    ok, _ = _quiet(lambda: wd.run_chapter_with_retries(
        "7", attempt, _Throttle(1), unresolved, sleep=delays.append))
    assert ok is True and unresolved == {}
    assert len(calls) == 3
    assert delays == [2.0, 4.0]


def test_persistent_empty_body_is_marked_incomplete_after_three_attempts():
    failures = [RuntimeError("Chapter UID 1194 decoded without an XHTML body")] * 5
    attempt, calls = _sequence(*failures)
    unresolved, delays = {}, []
    ok, log = _quiet(lambda: wd.run_chapter_with_retries(
        "1194", attempt, _Throttle(1), unresolved, sleep=delays.append))
    assert ok is False
    assert len(calls) == wd.CHAPTER_TRANSIENT_ATTEMPTS == 3
    assert delays == [2.0, 4.0]
    assert "1194" in unresolved
    assert "gave up after 3 attempts" in unresolved["1194"]
    assert "[UNRESOLVED] chapter UID 1194 marked INCOMPLETE" in log


def test_paid_placeholder_is_not_retried():
    attempt, calls = _sequence(RuntimeError("Chapter 9 is marked paid (5) but returned only a placeholder body"))
    unresolved = {}
    ok, _ = _quiet(lambda: wd.run_chapter_with_retries(
        "9", attempt, _Throttle(1), unresolved, sleep=lambda _d: None))
    assert ok is False and len(calls) == 1
    assert "9" in unresolved


def test_rate_limit_keeps_its_own_budget_and_does_not_use_transient_retries():
    limited = RuntimeError("426 Client Error: Upgrade Required")
    attempt, calls = _sequence(limited, limited, None)
    unresolved, delays = {}, []
    ok, _ = _quiet(lambda: wd.run_chapter_with_retries(
        "3", attempt, _Throttle(1), unresolved, sleep=delays.append))
    assert ok is True and unresolved == {}
    assert len(calls) == 3
    assert delays == []


def test_checkpoint_resume_is_found_when_output_is_the_parent_directory():
    with tempfile.TemporaryDirectory() as temp:
        output = Path(temp)
        old = output / "Some Book-12345.work"
        old.mkdir()
        found = wd.resolve_export_workspace(output, BOOK_ID, "Some Book-12345")
        assert found == old


def test_resume_finds_workspace_one_level_below_output():
    with tempfile.TemporaryDirectory() as temp:
        output = Path(temp)
        nested = output / "old-run" / "Other Title-12345.work"
        nested.mkdir(parents=True)
        found = wd.resolve_export_workspace(output, BOOK_ID, "Renamed Title-12345")
        assert found == nested


def test_multiple_workspace_candidates_for_one_book_is_an_error():
    with tempfile.TemporaryDirectory() as temp:
        output = Path(temp)
        (output / "B" / "A-12345.work").mkdir(parents=True)
        (output / "C" / "Renamed-12345.work").mkdir(parents=True)
        try:
            wd.resolve_export_workspace(output, BOOK_ID, "A-12345")
        except RuntimeError as exc:
            assert "2 workspaces match book 12345" in str(exc)
        else:
            raise AssertionError("ambiguous workspaces must raise, not pick one")


def test_other_books_workspaces_are_ignored_and_new_path_is_returned():
    with tempfile.TemporaryDirectory() as temp:
        output = Path(temp)
        (output / "Other-99999.work").mkdir()
        (output / "plain-folder").mkdir()
        expected = output / "Fresh-12345.work"
        assert wd.resolve_export_workspace(output, BOOK_ID, "Fresh-12345") == expected
        assert not expected.exists()


def test_depth_limit_stops_search_before_deep_folders():
    with tempfile.TemporaryDirectory() as temp:
        output = Path(temp)
        deep = output / "a" / "b" / "X-12345.work"
        deep.mkdir(parents=True)
        assert wd.find_book_workspaces(output, BOOK_ID) == []



def _offline_export(output_root, chapter_responses):
    """Run export_book against a fake WeRead; returns (exception|None, log, fetched_uids)."""
    import contextlib, io
    fetched = []

    class FakeWeRead:
        def __init__(self, _settings, _lua):
            pass

        def fetch_book(self, _book_id):
            return ({"title": "Fake Book"},
                    [{"chapterUid": 1, "wordCount": 0},
                     {"chapterUid": 1194, "wordCount": 0}])

        def chapter_html(self, _book, chapter):
            uid = str(chapter["chapterUid"])
            fetched.append(uid)
            outcome = chapter_responses.pop(0)
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome, ""

        def persist_cookies(self):
            pass

    saved = (wd.WeRead, wd._set_private_acl, wd.time.sleep)
    wd.WeRead = FakeWeRead
    wd._set_private_acl = lambda _path, _directory: None
    wd.time.sleep = lambda _d: None
    out, err = io.StringIO(), io.StringIO()
    error = None
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            wd.export_book(BOOK_ID, output_root, {"cookies": {}}, jobs=1)
    except RuntimeError as exc:
        error = exc
    finally:
        wd.WeRead, wd._set_private_acl, wd.time.sleep = saved
    return error, out.getvalue() + err.getvalue(), fetched


def _seed_completed_chapter(workspace, uid, index):
    """Write a checkpoint that resume considers complete: body, review row, css."""
    stem = f"chapter-{index:05d}"
    (workspace).mkdir(parents=True, exist_ok=True)
    (workspace / (stem + ".body")).write_text("<p>done</p>", encoding="utf-8")
    (workspace / (stem + ".css")).write_text("", encoding="utf-8")
    import json
    (workspace / (stem + ".json")).write_text(json.dumps({
        "chapter_uid": str(uid), "reviews": [], "underline_ranges": 0,
        "embedded_markers": 0}), encoding="utf-8")


def test_export_resumes_from_parent_output_and_refuses_epub_when_chapter_stays_empty():
    with tempfile.TemporaryDirectory() as temp:
        output_root = Path(temp) / "exports"
        workspace = output_root / "old-run" / "Fake Book-12345.work"
        _seed_completed_chapter(workspace, 1, 1)
        empty = "<html><head></head><p>no body here</p></html>"
        error, log, fetched = _offline_export(
            output_root, [empty, empty, empty])
        assert error is not None, "an incomplete book must not publish an EPUB"
        assert "remain unresolved" in str(error)
        assert fetched == ["1194"] * 3, fetched
        assert "[RESUME] Reusing 1 complete chapter(s)" in log, log
        assert "[UNRESOLVED] chapter UID 1194 marked INCOMPLETE" in log
        assert not any(output_root.rglob("*.epub")), "no EPUB may exist for an incomplete book"
        assert (workspace / "failed_chapters.json").is_file()
        assert "1194" in (workspace / "failed_chapters.json").read_text(encoding="utf-8")


if __name__ == "__main__":
    for test in (
        test_empty_xhtml_body_is_retried_then_succeeds,
        test_transient_transport_errors_retry_with_exponential_backoff,
        test_persistent_empty_body_is_marked_incomplete_after_three_attempts,
        test_paid_placeholder_is_not_retried,
        test_rate_limit_keeps_its_own_budget_and_does_not_use_transient_retries,
        test_checkpoint_resume_is_found_when_output_is_the_parent_directory,
        test_resume_finds_workspace_one_level_below_output,
        test_multiple_workspace_candidates_for_one_book_is_an_error,
        test_other_books_workspaces_are_ignored_and_new_path_is_returned,
        test_depth_limit_stops_search_before_deep_folders,
        test_export_resumes_from_parent_output_and_refuses_epub_when_chapter_stays_empty,
    ):
        test()
        print("PASS", test.__name__)
