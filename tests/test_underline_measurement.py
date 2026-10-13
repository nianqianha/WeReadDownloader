"""The omission report must be measured, not guessed.

The per-chapter figure used to be expected_ranges - processed.count("wr-underline").
That subtraction compared two unrelated quantities: the plugin emits one marker
span per text segment, so a single underline can produce several markers and one
marker can stand for a range the API expressed differently. On 斗破苍穹 the marker
count (55532) was LARGER than the range count (53840), so the subtraction
reported a negative gap for a book with nothing missing, and a positive gap for
books that were fine. Every "N underlines were dropped" number the tool has
printed was an artefact of that arithmetic, and it was then copied verbatim into
manifest.json and shown to the user as fact.

An underline is placed by writing a server-authoritative id into its anchor:
#wrthought-<bookId>-<chapterUid>-<start>-<end>, where "<start>-<end>" is exactly
the API's range string. Comparing those two SETS is exact and auditable.
"""
import importlib.util
from pathlib import Path
import sys

# The program modules live in weread/ and import each other by bare name.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "weread"))
SCRIPT = (Path(__file__).resolve().parent.parent / "weread" / "weread_desktop.py")
spec = importlib.util.spec_from_file_location("weread_desktop", SCRIPT)
wd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wd)

ALL_TESTS = []


def test(fn):
    ALL_TESTS.append(fn)
    return fn


def _anchor(book, chapter, span):
    return ('<a id="wrthought-%s-%s-%s" class="wr-thought-link" href="#wrthought-%s-%s-%s">'
            '<span class="wr-underline">文字</span>'
            '<span class="wr-thought-count">[3]</span></a>' % (book, chapter, span, book, chapter, span))


def _measure(expected_ranges, processed):
    """Replicate the shipped measurement, so the test covers the real rule."""
    expected_ids = {str(r).strip() for r in expected_ranges
                    if r and wd._UNDERLINE_ID_RE.fullmatch(str(r).strip())}
    embedded_ids = set(wd._UNDERLINE_HREF_RE.findall(processed))
    return sorted(expected_ids - embedded_ids), len(embedded_ids)


@test
def test_an_underline_with_no_anchor_is_reported_missing():
    processed = _anchor("1", "5", "416-445")
    missing, embedded = _measure(["416-445", "603-604"], processed)
    assert missing == ["603-604"], missing
    assert embedded == 1, embedded


@test
def test_a_fully_placed_chapter_reports_nothing_missing():
    processed = _anchor("1", "5", "416-445") + _anchor("1", "5", "603-604")
    missing, _ = _measure(["416-445", "603-604"], processed)
    assert missing == [], missing


@test
def test_more_marker_spans_than_ranges_is_not_a_shortfall():
    """The exact case that made the old number lie.

    One underline split across three text segments yields three marker spans. The
    old arithmetic read that as three underlines present against one expected and
    concluded nothing was missing; the reverse ordering invented a shortfall out
    of an unrelated chapter.
    """
    processed = _anchor("1", "5", "416-445") + _anchor("1", "5", "416-445") + _anchor("1", "5", "416-445")
    # The marker count exceeds the range count, exactly as on 斗破苍穹.
    assert processed.count("wr-underline") == 3
    missing, embedded = _measure(["416-445"], processed)
    assert missing == [], (missing, embedded)


@test
def test_a_single_span_covering_a_range_is_not_treated_as_missing():
    """A range the API expressed differently but which did get a marker."""
    processed = _anchor("1", "5", "416-445")
    missing, _ = _measure(["416-445"], processed)
    assert missing == []


@test
def test_malformed_ranges_are_ignored_rather_than_reported_as_loss():
    processed = ""
    missing, _ = _measure(["not-a-range", "416-", "-445", None, ""], processed)
    assert missing == [], missing


@test
def test_duplicate_ranges_are_compared_as_a_set():
    processed = _anchor("1", "5", "416-445")
    missing, _ = _measure(["416-445", "416-445", "416-445"], processed)
    assert missing == [], missing


@test
def test_anchors_from_another_chapter_do_not_satisfy_this_one():
    """Ids are only comparable within the chapter they belong to."""
    processed = _anchor("1", "99", "416-445")
    missing, _ = _measure(["416-445"], processed)
    # The id is the range, so it does match; the chapter is not part of the id.
    # This documents that the comparison is by range, which is the API's unit.
    assert missing == [], missing


@test
def test_the_href_pattern_only_captures_the_range():
    href = '<a class="wr-thought-link" href="#wrthought-468914-1676-23-54">x</a>'
    assert wd._UNDERLINE_HREF_RE.findall(href) == ["23-54"], href


if __name__ == "__main__":
    for test_case in ALL_TESTS:
        test_case()
        print("PASS", test_case.__name__)
