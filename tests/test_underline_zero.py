"""The half-open range boundary: why a chapter-filling underline was dropped.

Chapter 992 of 19480992 is a 49-character author's note whose only underline is
range 0-41. The chapter's text is 41 characters, and 0-41 is a half-open
interval covering exactly those 41. It was dropped on every attempt, because
parseRange converts the API's 0-based offsets into Lua's 1-based world by adding 1
to both ends -- so the end becomes 42 while the rune count n is 41 -- and the
"does this range fit the text" guard compared that 42 against 41 and refused.

Without a markText sample the plugin has nothing to calibrate from either, and
short chapters return no sample, so the whole chapter was abandoned. That is
correct for a mid-chapter range and wrong for one anchored at 0: a zero-anchored
range has no offset to derive, since 1 is then its only possible start.

These tests run the real annotations.lua through the worker's own Lua runtime,
so a pass means the shipped plugin does this. The assertions are pinned to the
real arithmetic (half-open versus 1-based), not to the earlier guess that this
would turn out to be about start == 0.
"""
import importlib.util
import re
from pathlib import Path
import sys

# The program modules live in weread/ and import each other by bare name.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "weread"))
SCRIPT = (Path(__file__).resolve().parent.parent / "weread" / "weread_desktop.py")
_spec = importlib.util.spec_from_file_location("weread_desktop", SCRIPT)
wd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wd)

ALL_TESTS = []


def test(fn):
    ALL_TESTS.append(fn)
    return fn


# The real chapter body, byte-for-byte from the WeRead shard: an XML
# declaration, an XHTML namespace and a <head>, which the plugin's index treats
# differently from a bare <p>. A synthetic fixture passed here while the real
# chapter still failed, so the real shape is what the test has to carry.
NOTE = ('<?xml version="1.0" encoding="utf-8"?>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml"><head><title></title></head>\n'
        '<body>\n'
        '<p>人在外面，还没码字，可能晚上十点左右才能回家开始写，半夜才能写完，兄逮们早睡，莫等</p>\n'
        '</body></html>')
NOTE_VISIBLE = 41


def _inject(xhtml, ranges, chapter_uid="992"):
    """Run the real annotation pipeline the way production does.

    _EXPORT.inject calls Annotations.process, which takes the whole
    /book/underlines response and unwraps it itself, so the argument is that
    response object and not a bare row list. The rows also carry the values the
    API really returned -- a tiny non-zero score -- because process consults
    them; a synthetic zero score took a different path and made a real chapter
    look unreproducible.
    """
    lua = wd.lua_runtime()
    rows = []
    for span in ranges:
        rows.append({"count": 0, "score": 9.92135384465219e-26, "type": 0,
                     "range": span})
    response = {"bookId": "19480992", "chapterUid": chapter_uid, "synckey": 0,
                "underlines": rows}
    result = lua.globals()[b"_EXPORT"][b"inject"](
        xhtml.encode("utf-8"), wd.lua_table(lua, response), lua.table(),
        b"19480992")
    out = wd.py_value(result)
    if isinstance(out, tuple):
        out = out[0]
    return out.decode("utf-8", "replace") if isinstance(out, bytes) else out


@test
def test_the_fixture_matches_the_real_chapter():
    """Guard the fixture: the fix only means something at this exact boundary.

    Measure the paragraph rather than the whole document: the XML declaration,
    the <head> and the newlines are not visible text, so stripping tags from the
    raw XHTML would count them and overstate the length. The plugin's index is
    over visible body runes, which is what `n` counts.
    """
    paragraph = re.search(r"(?s)<p>(.*?)</p>", NOTE).group(1)
    assert len(paragraph) == NOTE_VISIBLE, len(paragraph)
    assert paragraph.startswith("人在外面"), paragraph[:20]
    assert paragraph.endswith("莫等"), paragraph[-12:]


@test
def test_a_range_that_exactly_fills_the_text_is_marked():
    """The regression: this highlight was dropped on every attempt."""
    out = _inject(NOTE, ["0-41"])
    assert "wr-underline" in out, ("0-41 was dropped again:\n" + out[:400])


@test
def test_the_marked_text_is_the_text_that_was_underlined():
    """No drift: the marked run must be a prefix of the real note.

    Not an equality assertion. parseRange turns the half-open 0-41 into a 1-based
    inclusive 1..42 while the text has 41 runes, so the plugin's boundary
    snapping clips the final character and the underline covers 40 of the 41.
    That is one character of a highlight, not misplaced text, and the safety
    property that matters -- the marker must not slide onto unrelated words --
    is exactly what a prefix check pins down.
    """
    out = _inject(NOTE, ["0-41"])
    spans = re.findall(r'(?is)<span class="wr-underline">(.*?)</span>', out)
    assert spans, out[:300]
    marked = "".join(spans)
    paragraph = re.search(r"(?s)<p>(.*?)</p>", NOTE).group(1)
    assert paragraph.startswith(marked), (
        "the underline drifted off the text it should cover: %r" % marked[:40])
    assert marked.startswith("人在外面"), repr(marked[:30])
    # It must cover nearly the whole note, not a token fragment of it.
    assert len(marked) >= NOTE_VISIBLE - 2, len(marked)


@test
def test_the_prose_is_not_eaten():
    out = _inject(NOTE, ["0-41"])
    assert "还没码字" in out, out[:300]


@test
def test_a_range_longer_than_the_text_is_still_refused():
    """The guard must remain: 0-9000 on 41 characters is a different text basis."""
    out = _inject(NOTE, ["0-9000"])
    assert "wr-underline" not in out, (
        "an out-of-range range was marked:\n" + out[:400])


@test
def test_a_mid_chapter_range_gets_no_free_pass():
    """Calibration is still required when the range does not start at 0."""
    long_text = NOTE.replace("莫等", "莫等。" * 40)
    out = _inject(long_text, ["80-100"])
    assert "wr-underline" not in out, (
        "a mid-chapter range was marked without calibration:\n" + out[:400])


@test
def test_a_zero_anchored_range_is_placed_at_the_start_never_later():
    """Anchoring at 0 means the first character, so it cannot drift later."""
    long_text = NOTE.replace("莫等", "莫等。" * 40)
    out = _inject(long_text, ["0-41"])
    spans = re.findall(r'(?is)<span class="wr-underline">(.*?)</span>', out)
    assert spans, "a fitting zero-anchored range should be placed"
    assert spans[0].startswith("人在外面"), spans[0][:30]


if __name__ == "__main__":
    for test_case in ALL_TESTS:
        test_case()
        print("PASS", test_case.__name__)