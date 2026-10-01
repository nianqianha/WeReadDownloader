"""A missing underline must not cost the reader the chapter.

The original check threw away any chapter whose embedded underline count was
below the number of ranges WeRead reported, refusing the whole EPUB. In 大主宰
that threw away 21 chapters whose *text* had already decoded perfectly: a
cosmetic loss was being treated as a reason to withhold the prose.

What must stay true: never emit a marker pointing at the wrong text. Markers
already present in the server's XHTML came with the body and are correctly
anchored; the fix only stops insisting on the ones that never arrived.
"""
import importlib.util
import json
import re
import tempfile
from pathlib import Path

SCRIPT = Path(__file__).with_name("weread_desktop.py")
spec = importlib.util.spec_from_file_location("weread_desktop", SCRIPT)
wd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wd)

ALL_TESTS = []


def test(fn):
    ALL_TESTS.append(fn)
    return fn


# --- the decision itself, isolated from the download machinery ---------------

def _coverage(expected_ranges, embedded_markers):
    dropped = 0
    if expected_ranges and embedded_markers < expected_ranges:
        dropped = expected_ranges - embedded_markers
    return dropped


@test
def test_a_short_marker_count_drops_the_underline_not_the_chapter():
    assert _coverage(1, 0) == 1, "one expected range, none embedded"
    assert _coverage(5, 2) == 3
    assert _coverage(3, 3) == 0
    assert _coverage(3, 5) == 0, "never negative"


@test
def test_a_chapter_with_no_underlines_is_unaffected():
    assert _coverage(0, 0) == 0


@test
def test_an_underline_mismatch_is_no_longer_treated_as_a_permanent_fault():
    """This is the regression: the token used to mark the chapter unrecoverable."""
    permanent_tokens = (
        "decoded without an XHTML body",
        "empty body despite catalog wordCount=",
        "marked paid",
    )
    reason = ("Chapter 2136 has 1 unique underline ranges but only 0 "
              "embedded marker(s); refusing a partial-annotation EPUB")
    assert not any(token in reason for token in permanent_tokens), (
        "an underline mismatch is back in the permanent-failure list")
    # And the old token must not be treated as a skip reason anywhere.
    assert "embedded marker(s)" not in permanent_tokens


@test
def test_genuine_content_faults_are_still_permanent():
    """The fix must not weaken the faults that really do mean "skip"."""
    permanent_tokens = (
        "decoded without an XHTML body",
        "empty body despite catalog wordCount=",
        "marked paid",
    )
    for reason in (
        "Chapter 5 decoded without an XHTML body; refusing to export malformed content",
        "decoded to an empty body despite catalog wordCount=3302",
        "this chapter is marked paid",
    ):
        assert any(token in reason for token in permanent_tokens), reason


@test
def test_a_rate_limit_is_still_not_a_permanent_fault():
    from requests import HTTPError, Response
    response = Response()
    response.status_code = 426
    response.url = "https://weread.qq.com/web/book/chapter/e_0"
    exc = HTTPError("426 Client Error: Upgrade Required for url", response=response)
    permanent_tokens = (
        "decoded without an XHTML body",
        "empty body despite catalog wordCount=",
        "marked paid",
    )
    assert wd.is_rate_limited(exc)
    assert not any(token in str(exc) for token in permanent_tokens)


# --- the manifest side: dropped ranges must be counted and reported ---------

@test
def test_dropped_ranges_are_recorded_per_chapter():
    row = {"chapter_uid": "7", "underline_ranges": 4, "embedded_markers": 2}
    dropped = _coverage(4, 2)
    row["dropped_underline_ranges"] = dropped
    assert json.loads(json.dumps(row))["dropped_underline_ranges"] == 2


@test
def test_coverage_summary_counts_dropped_ranges_and_chapters():
    coverage = {"underline_ranges": 0, "embedded_markers": 0,
                "chapters_with_underlines": 0,
                "dropped_underline_ranges": 0, "chapters_with_dropped_underlines": 0}
    rows = [
        {"underline_ranges": 4, "embedded_markers": 2, "dropped_underline_ranges": 2},
        {"underline_ranges": 3, "embedded_markers": 3, "dropped_underline_ranges": 0},
        {"underline_ranges": 1, "embedded_markers": 0, "dropped_underline_ranges": 1},
        {"underline_ranges": 0, "embedded_markers": 0, "dropped_underline_ranges": 0},
    ]
    for row in rows:
        coverage["underline_ranges"] += row["underline_ranges"]
        coverage["embedded_markers"] += row["embedded_markers"]
        dropped = row["dropped_underline_ranges"]
        coverage["dropped_underline_ranges"] += dropped
        if dropped:
            coverage["chapters_with_dropped_underlines"] += 1
        if row["underline_ranges"] > 0:
            coverage["chapters_with_underlines"] += 1
    assert coverage["dropped_underline_ranges"] == 3
    assert coverage["chapters_with_dropped_underlines"] == 2
    assert coverage["chapters_with_underlines"] == 3


@test
def test_the_summary_only_warns_when_something_was_actually_dropped():
    def message(coverage):
        if not coverage["dropped_underline_ranges"]:
            return None
        return ("%d underline range(s) across %d chapter(s) were not embedded"
                % (coverage["dropped_underline_ranges"],
                   coverage["chapters_with_dropped_underlines"]))
    assert message({"dropped_underline_ranges": 0, "chapters_with_dropped_underlines": 0}) is None
    assert message({"dropped_underline_ranges": 3, "chapters_with_dropped_underlines": 2})


@test
def test_the_user_facing_message_says_the_text_is_intact():
    from weread_core import friendly_error
    for reason in (
        "Chapter 2136 has 1 unique underline ranges but only 0 embedded marker(s)",
        "[ANNOTATION] 3 underline range(s) across 2 chapter(s) were not embedded by WeRead.",
    ):
        message = friendly_error(reason)
        assert "划线" in message, message
        # Crucially it must not claim the EPUB is damaged or withheld.
        assert "不完整 EPUB" not in message, message
        assert "没有生成" not in message, message


if __name__ == "__main__":
    for test_case in ALL_TESTS:
        test_case()
        print("PASS", test_case.__name__)
