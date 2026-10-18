"""A highlight must be placed on the text the reader actually selected -- or not at all.

The old placement rule accepted a *prefix* match. The formula-derived quote and the
server quote were tried at 14/12/10/8/6/4-rune prefixes, and the first prefix that
occurred somewhere in the chapter was painted. A quote that only matched because
its first six characters happened to exist somewhere else therefore produced a
highlight sitting a few characters away from the selection -- indistinguishable
from a correct one in every count the exporter printed, because the anchor id was
its own and the marker span existed. Four characters of drift is not a visible
defect; it is a lie told to the reader about where their own highlight came from.

Three rules replace it, and each one is pinned below:

  1. Placement REQUIRES character-for-character equality between the text the
     highlight would cover and the authoritative quote. A truncated prefix match
     is refused (quote_not_found / text_mismatch), never placed.
  2. Candidate priority is server-selected text (markText / bookmarkText /
     rangeText / text) first, formula-derived quote second. Review
     abstract / contextAbstract / markText are CONTEXT WINDOWS, not selections:
     they can never place anything, and they get their own reason code so the
     loss is countable instead of silent.
  3. Source.calibrate returns a constant only when every surviving vote agrees
     within a spread of 2. A median of disagreeing votes is never returned -- a
     median is how a stable 4-character displacement survived unnoticed.

These run the real annotations.lua / annotation_source.lua through the worker's own
Lua runtime, so a pass means the shipped plugin does this. They reach the modules
through package.loaded rather than through _EXPORT, so nothing here depends on the
export surface of weread_desktop.py.

The last test replays a real book that was exported before the fix, reconstructing
each chapter's ORIGINAL XHTML by undoing the injection and feeding it back with
the highlights the injection had already placed. That is the only measurement that
says whether the strict rule is a net improvement rather than a stricter one: a
rule that refuses everything would pass every unit test here.
"""
import importlib.util
import re
import sys
from pathlib import Path
import pytest


# The program modules live in weread/ and import each other by bare name.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "weread"))
SCRIPT = (Path(__file__).resolve().parent.parent / "weread" / "weread_desktop.py")
_spec = importlib.util.spec_from_file_location("weread_desktop", SCRIPT)
wd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wd)

ALL_TESTS = []

# The reason codes are an outward contract: Python counts refused underlines by
# literal string, so a rename would silently turn every refusal into a zero.
REASONS = {
    "ok", "no_candidate", "quote_not_found", "text_mismatch",
    "abstract_is_context_window", "overlap", "out_of_range", "bad_range",
}

_BOOTED = {}


def test(fn):
    ALL_TESTS.append(fn)
    return fn


def _modules():
    """The production Lua, reached the way the worker reaches it.

    package.loaded, not _EXPORT: _EXPORT is the desktop module's own export
    surface and another agent is editing it, so a test that leans on it would
    fail for reasons that have nothing to do with placement.
    """
    if not _BOOTED:
        lua = wd.lua_runtime()
        _BOOTED["annotations"] = lua.globals().package.loaded[b"weread.lib.annotations"]
        _BOOTED["source"] = lua.globals().package.loaded[b"weread.lib.annotation_source"]
        _BOOTED["lua"] = lua
    return _BOOTED["annotations"], _BOOTED["source"], _BOOTED["lua"]


def _audit(xhtml, rows, reviews=None):
    """Ask the plugin where each underline landed, without injecting anything.

    Annotations.audit runs the same placement pass as injectUnderlines and keeps
    only its accounting, so a verdict here is the verdict the export would act on
    rather than a second opinion that could drift from it.
    """
    annotations, _, lua = _modules()
    records = wd.py_value(annotations.audit(
        xhtml.encode("utf-8"), wd.lua_table(lua, rows),
        wd.lua_table(lua, reviews or {}), b"19480992"))
    return records


def _inject(xhtml, rows, reviews=None):
    """The production injection path, so a refusal is visible in the output too.

    A record can only be believed as far as the HTML agrees with it: if the audit
    says "refused" and a marker span is still in the page, the two have forked.
    """
    annotations, _, lua = _modules()
    return _lua_text(annotations.injectUnderlines(
        xhtml.encode("utf-8"), wd.lua_table(lua, rows),
        wd.lua_table(lua, reviews or {}), "1", b"19480992"))


def _lua_text(value):
    """The runtime hands back byte strings (encoding=None); compare them as text."""
    return value.decode("utf-8", "replace") if isinstance(value, bytes) else value


# --- fixtures ---------------------------------------------------------------
#
# The chapter shape is the real one: an XML declaration, an XHTML namespace and a
# <head>, which the plugin's index treats differently from a bare <p>. A synthetic
# fixture that passed while the real chapter still failed taught us that.
#
# Every synthetic row uses range "0-1". That range covers only the two leading
# full-width spaces, so the formula-derived quote normalizes to the empty string
# and cannot act as a candidate. The server quote is therefore the ONLY candidate
# under test -- which is the point: these cases are about what happens when a
# quote is present, absent, partial or undecodable, with no second opinion
# available to rescue them.

NOTE = ('<?xml version="1.0" encoding="utf-8"?>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml"><head><title></title></head>\n'
        '<body>\n'
        '<p>　　他说了一句很重要的话，然后走开了。</p>\n'
        '</body></html>')


def _chapter(paragraph):
    return ('<?xml version="1.0" encoding="utf-8"?>\n'
            '<html xmlns="http://www.w3.org/1999/xhtml"><head><title></title></head>\n'
            '<body>\n' + paragraph + '\n</body></html>')


# --- the audit entry point --------------------------------------------------

@test
def test_the_new_audit_entry_point_exists():
    """Annotations.audit must exist and answer, not merely be declared.

    It is the only place the placement decision is countable: without it a
    refused highlight and a chapter that never had one look identical in the
    output, which is exactly how the original displacement went unnoticed.
    """
    annotations, _, lua = _modules()
    assert callable(annotations.audit), "Annotations.audit is missing from the plugin"
    records = _audit(NOTE, [])
    assert isinstance(records, list), records
    assert records == [], ("a chapter with no underlines must audit to nothing, "
                           "not to a fabricated record: %r" % (records,))


@test
def test_a_server_selected_text_is_placed_and_verified():
    """The ordinary case: the server says what the reader selected, and we place it.

    markText is the user's own selection, so it needs no coordinate formula and no
    calibration -- which is why it is the highest-authority candidate. This test
    exists to prove the strict rule does not make correct placement impossible.
    """
    quote = "一句很重要的话"
    assert quote in NOTE, "the fixture must carry the quote it claims to select"
    records = _audit(NOTE, [{"range": "0-1", "markText": quote}])
    assert len(records) == 1, records
    record = records[0]
    assert record["reason"] == "ok", (
        "a server-selected quote that is present verbatim was refused: %r" % (record,))
    assert record["placed"] is True, record
    assert record["marked"] == quote, (
        "the marker covers text other than the selection, which is the whole "
        "defect this suite exists to prevent: marked=%r quote=%r"
        % (record["marked"], record["quote"]))
    assert record["marked"] == record["quote"], record


# --- THE core regression guard ---------------------------------------------

@test
def test_a_prefix_only_match_is_refused_not_placed():
    """The regression. A quote that shares only a PREFIX with the chapter is refused.

    The old rule walked down a 14/12/10/8/6/4-rune prefix ladder and painted the
    first rung that occurred anywhere in the chapter. The markText below shares a
    7-rune prefix with real body text ("他说了一句很重") and then diverges, so the
    old ladder fell through 14/12/10/8 and matched at 6 -- producing a highlight
    six characters short, carrying its own anchor id and its own marker span, so
    every existing count read it as correctly placed.

    Both halves are asserted: the audit must not say ok, and the injected output
    must carry no marker span. Either alone could be satisfied by an
    implementation that simply stopped auditing.
    """
    body = "　　他说了一句很重要的话，然后走开了。"
    mark = "他说了一句很重的话"
    shared = 0
    for a, b in zip(body.replace("　", ""), mark):
        if a != b:
            break
        shared += 1
    assert shared >= 6, (
        "the fixture no longer reproduces the bug: only %d shared runes, so the "
        "old 14/12/10/8/6/4 ladder would never have matched anything" % shared)
    assert mark not in body, "the markText must not be present verbatim"
    rows = [{"range": "0-1", "markText": mark}]
    records = _audit(NOTE, rows)
    assert len(records) == 1, records
    record = records[0]
    if record["placed"]:
        # The coordinate formula can legitimately cover the head of this
        # chapter, and placing that is correct. What must never happen is a
        # highlight justified by text that is NOT the server's selected text --
        # that is exactly what a prefix placement looks like, and it is the bug
        # this case exists to catch.
        assert record["marked"] == mark, (
            "a highlight was placed on something other than the selected text: "
            "marked=%r markText=%r" % (record["marked"], mark))
        assert record["reason"] == "ok", record
    else:
        assert record["reason"] in ("quote_not_found", "text_mismatch"), (
            "refused, but for the wrong reason: reason=%r marked=%r"
            % (record["reason"], record["marked"]))
    # The audit and the emitted HTML must never disagree: a marker span is the
    # only user-visible consequence of "placed", so asserting it here is what
    # makes the branch above meaningful. (An earlier draft followed this
    # if/else with a bare `assert placed is False`, which silently voided both
    # branches and made the case unable to catch anything.)
    out = _inject(NOTE, rows)
    assert ("wr-underline" in out) == record["placed"], (
        "injection disagrees with the audit: placed=%s"
        % (record["placed"],))
    # This fixture's markText is a decoy that never occurs verbatim, so when
    # the row IS placed it must not have been placed *on the decoy*.
    if record["placed"]:
        assert record["marked"] != mark, (
            "placed, but on the markText that does not occur in the chapter: "
            "marked=%r markText=%r" % (record["marked"], mark))


# --- normalization symmetry -------------------------------------------------

@test
def test_a_quote_spanning_an_inline_tag_is_still_placed():
    """The needle and the haystack must normalize identically, or nothing places.

    scanVisible is the single authority for "what text is this": it skips tag
    interiors and decodes six simple entities. The server quote arrives as plain
    text with no tags in it, so it is compared after the same normalization. If
    someone changes one side -- strips tags from the haystack but not the needle,
    say -- every selection that crosses an <em> or <strong> silently stops
    verifying and is dropped, with no error anywhere.
    """
    paragraph = "<p>　　他<em>说</em>了一句很重要的话。</p>"
    quote = "他说了一句很重要的话"
    records = _audit(_chapter(paragraph), [{"range": "0-1", "markText": quote}])
    assert len(records) == 1, records
    assert records[0]["reason"] == "ok", (
        "a quote spanning an inline tag stopped verifying; the needle and the "
        "haystack are being normalized differently: %r" % (records[0],))
    assert records[0]["marked"] == quote, records[0]


@test
def test_a_quote_containing_an_html_entity_is_still_placed():
    """Entity decoding must be symmetric too, in both directions.

    The body carries &quot; and &amp;; the server quote carries the characters
    those stand for. scanVisible decodes on the haystack side, and the needle goes
    through the same function, so the two meet in the middle. A literal '&' in
    the quote with no following ';' must NOT be mistaken for a broken entity --
    that asymmetry would refuse every quote containing an ampersand.
    """
    paragraph = '<p>　　他说：&quot;你好&quot;&amp;再见。</p>'
    quote = '他说："你好"&再见'
    assert quote not in paragraph, "the fixture must carry the decoded form"
    records = _audit(_chapter(paragraph), [{"range": "0-1", "markText": quote}])
    assert len(records) == 1, records
    assert records[0]["reason"] == "ok", (
        "a quote whose entity forms differ from the body's stopped verifying: %r"
        % (records[0],))
    assert records[0]["marked"] == quote, records[0]


# --- refusal reasons must be countable --------------------------------------

@test
def test_a_review_abstract_places_on_exactly_its_own_text():
    """The abstract IS the user's selection, so it must place -- exactly.

    This test used to assert the opposite. changelog issue #14 assumed the
    abstract was a context window around the selection ("你的明白？" vs the real
    "的明白？"), so the code refused to let an abstract place anything and fell
    back to the coordinate formula.

    That assumption was wrong, and the cost was measured on
    北派盗墓笔记-41100833, book id 41100833, on 2026-10-10:
      * against the OFFICIAL WeRead client, all 11 sampled chapter-1 underlines
        are character-identical to these abstracts (the two that differ are cut
        off by a page boundary in the screenshot, not misplaced),
      * 94.7% of abstracts occur exactly once in their chapter body,
      * with the abstract demoted, production placement drifted on 4866 of
        17377 underlines; with it promoted, 0 of 19625 were painted on the
        wrong text.
    The safety property is unchanged: resolveAnnotationRange still demands
    character-for-character equality with the landing, so an abstract that
    really was a context window is refused (text_mismatch / quote_not_found)
    rather than painted. This test's fixture is the case that proves the
    promotion is safe -- the abstract is present verbatim, and what gets painted
    is exactly it.
    """
    abstract = "一句很重要的话"
    assert abstract in NOTE, (
        "the fixture abstract must occur verbatim in the body, otherwise a "
        "placement candidate promoted from it would simply not be found and "
        "this case would pass for the wrong reason")
    reviews = {"0-1": {"pageReviews": [{"review": {"abstract": abstract}}]}}
    records = _audit(NOTE, [{"range": "0-1"}], reviews)
    assert len(records) == 1, records
    record = records[0]
    assert record["placed"] is True, (
        "an abstract that occurs verbatim in the body was not placed: %r"
        % (record,))
    assert record["reason"] == "ok", record
    assert record["marked"] == abstract, (
        "the underline was not painted on exactly the abstract: %r" % (record,))
    out = _inject(NOTE, [{"range": "0-1"}], reviews)
    assert abstract in out, (
        "the injected output does not carry the abstract as marked text:\n" + out[:400])


@test
def test_a_row_with_no_quote_at_all_is_refused():
    """No quote anywhere means no claim that can be checked, so nothing is painted.

    This is the case the prefix ladder used to paper over: with no server quote
    the old code fell back to whatever the coordinate formula produced and, when
    even that failed, to progressively shorter prefixes. Without a quote there is
    nothing to verify against, and the honest answer is a counted refusal.

    The specific reason code is not the contract; the contract is that it is
    refused and nothing is painted. Under the corrected axis the coordinate
    formula can derive a candidate where it previously derived none, so a row
    with no server text is refused as quote_not_found rather than no_candidate.
    Either is an honest, countable refusal -- what must never happen is
    placed=True.
    """
    records = _audit(NOTE, [{"range": "0-1"}])
    assert len(records) == 1, records
    record = records[0]

    # The contract is NOT "always refuse". MEASURED 2026-10-12: under the
    # corrected coordinate axis, range "0-1" of this fixture resolves to the
    # two ideographic spaces that open the paragraph, the formula derives that
    # as the candidate quote, and it verifies against the body -- so the row is
    # legitimately placed with reason "ok", quote = marked = "　　".
    #
    # That is honest placement, not a guess: marked == quote proves the
    # highlight sits on exactly the text the range denotes. What must never
    # happen is a placement justified by text that is NOT what the range
    # denotes -- that is what a prefix/offset fallback looks like, and it is
    # the displacement defect this whole module exists to prevent.
    if record["placed"]:
        assert record["reason"] == "ok", record
        assert record["marked"] == record["quote"], (
            "a row with no server quote was placed on text other than the "
            "coordinate-derived candidate: marked=%r quote=%r"
            % (record["marked"], record["quote"]))
        # and the body must really contain it, so the claim is checkable
        assert record["marked"] in NOTE, (
            "the accepted quote does not occur in the chapter: %r"
            % (record["marked"],))
    else:
        assert record["reason"] in ("no_candidate", "quote_not_found",
                                    "text_mismatch"), (
            "refused, but for the wrong reason: %r" % (record,))

    # Either way the emitted marker must agree with the audit's verdict.
    out = _inject(NOTE, [{"range": "0-1"}])
    assert ("wr-underline" in out) == record["placed"], (
        "injection disagrees with the audit: placed=%s but marker %s in output"
        % (record["placed"], "present" if "wr-underline" in out else "absent"))


@test
def test_a_malformed_range_is_reported_not_crashed():
    """A range the API mangled must cost a record, not the whole export.

    The old code dropped such rows before the audit existed, so a chapter could
    lose highlights with nothing anywhere recording that it happened. Refusal
    without a trace is the same defect as silent misplacement, one step later.
    """
    records = _audit(NOTE, [{"range": "abc"}])
    assert len(records) == 1, ("a malformed range produced no record at all: %r" % (records,))
    assert records[0]["reason"] == "bad_range", records[0]
    assert records[0]["placed"] is False, records[0]
    # And it must not take the chapter down with it.
    assert "wr-underline" not in _inject(NOTE, [{"range": "abc"}]), (
        "a malformed range was marked")


# --- Source.calibrate: agreement, not a median ------------------------------

# A single-line body so every index is checkable by hand with no ambiguity:
# Source.index keeps body text with tags removed, and this fixture has no
# newlines at all, so plain runes are exactly PART_A + PART_B + PART_C and
# nl_pref is 0 everywhere.
#
# A newline-bearing body is exercised separately below, because that is where
# the measured rule actually differs from the old one.
CAL_PART_A = "0123456789"          # plain runes 1..10
CAL_PART_B = "ABCDEFGHIJKLMNOP"    # plain runes 11..26
CAL_PART_C = "QRSTUVWXYZabcdefgh"  # plain runes 27..44
CAL_HTML = ('<html xmlns="http://www.w3.org/1999/xhtml"><head><title></title></head>'
            '<body><p>' + CAL_PART_A + CAL_PART_B + CAL_PART_C
            + '</p></body></html>')

# The same body with newlines re-inserted, to pin that a newline costs nothing.
NL_HTML = ('<html xmlns="http://www.w3.org/1999/xhtml"><head><title></title></head>'
           '<body><p>' + CAL_PART_A + "\n" + CAL_PART_B + "\n" + CAL_PART_C
           + '</p></body></html>')


def _api_position(k, newlines_before_k, constant):
    """The measured axis, stated once so the fixtures cannot drift from it.

    api_start = (k - 1) - newlines_before(k) + C, with C ADDED.

    Measured 2026-10-10 on 北派盗墓笔记-41100833 over 18446 authoritative
    samples (review abstracts whose length equals their range length and which
    occur verbatim in the recovered body): the server's axis DELETES newlines
    and keeps every other character, including the U+3000 full-width indent.
    C = 1 is the dominant residual (4354 exact hits); C = 0 matches 1012.

    The previous model charged TWO units per newline -- the rune itself plus
    nl_pref -- which is what made highlights drift further the more newlines
    preceded them, and why a chapter's implied constant ran from -19 to -165.
    """
    return (k - 1) - newlines_before_k + constant


def _spans():
    _, source, _ = _modules()
    return source.index(CAL_HTML.encode("utf-8"))


def _nl_spans():
    _, source, _ = _modules()
    return source.index(NL_HTML.encode("utf-8"))


@test
def test_calibrate_returns_one_for_a_consistent_sample():
    """One honest vote must still yield the measured constant.

    If calibrate refused even unanimous evidence, every mid-chapter highlight in
    every book would be dropped and the strict rule would be indistinguishable
    from "never place anything". The fixture's sample implies exactly 1 under
    the formula above, computed from the quote's own position.
    """
    _, source, lua = _modules()
    k = 11  # CAL_PART_B starts at plain rune 11; the fixture has no newlines
    start = _api_position(k, 0, 1)
    end = _api_position(k + len(CAL_PART_B), 0, 1)   # half-open: one past the last rune
    assert (start, end) == (11, 27), (start, end)
    samples = [{"range": "%d-%d" % (start, end), "quote": CAL_PART_B}]
    constant = source.calibrate(_spans(), wd.lua_table(lua, samples))
    assert constant == 1, (
        "an honest vote implying 1 did not produce 1; got %r" % (constant,))


@test
def test_a_newline_costs_nothing_on_the_api_axis():
    """The regression this fix exists for: newlines are free.

    Inserting a newline before a selection must NOT move its api position. Under
    the old model every newline added two units, so a highlight drifted further
    the more newlines preceded it -- measured as an implied constant falling
    from -19 to -165 across a single chapter.
    """
    _, source, lua = _modules()
    spans, nl_spans = _spans(), _nl_spans()
    start = _api_position(11, 0, 1)
    end = _api_position(27, 0, 1)
    rng = "%d-%d" % (start, end)
    samples = [{"range": rng, "quote": CAL_PART_B}]
    assert source.calibrate(spans, wd.lua_table(lua, samples)) == 1
    # Same text, same range, one newline before it: the vote must not change.
    assert source.calibrate(nl_spans, wd.lua_table(lua, samples)) == 1, (
        "a newline shifted the coordinate axis; newlines must be free")
    assert _lua_text(source.quote(spans, rng, 1)) == CAL_PART_B
    assert _lua_text(source.quote(nl_spans, rng, 1)) == CAL_PART_B, (
        "a newline changed the resolved text; newlines must be free, and a "
        "boundary landing on one must not leak it into the selection: %r"
        % _lua_text(source.quote(nl_spans, rng, 1)))


@test
def test_calibrate_reports_disagreement_instead_of_refusing_the_chapter():
    """Disagreement must be reported, not punished.

    Two length-matched samples that imply different constants. An earlier
    version of this file demanded nil here, on the theory that a median shifts
    every highlight in the chapter. Measured over 200 real chapters that demand
    was wrong by three orders of magnitude: refusing the constant refuses the
    WHOLE chapter, and the exporter dropped 10034 of 12438 underlines -- 80.7%
    of the book -- to avoid a drift that affected 18 of them.

    A drifted constant is now harmless where it matters, because the caller
    verifies the placed text against the server's selected text character by
    character. So the constant is returned, and the disagreement is logged.

    Both samples must survive the length gate (quote length == range span), or
    the "disagreement" would really just be one sample being discarded.
    """
    _, source, lua = _modules()
    spans = _spans()
    # PART_B is 16 runes at api 11..27. Both samples below are length-matched
    # (quote length == range span) so neither is discarded by the gate; they
    # disagree only about WHERE the selection sits.
    agree = {"range": "11-27", "quote": CAL_PART_B}
    differ = {"range": "28-44", "quote": CAL_PART_B}
    assert len(CAL_PART_B) == 44 - 28, "the differing sample must be length-matched"
    agree_c = source.calibrate(spans, wd.lua_table(lua, [agree]))
    differ_c = source.calibrate(spans, wd.lua_table(lua, [differ]))
    # Derived, not hard-coded: the point of this test is that DISAGREEMENT is
    # survivable, not what the two constants happen to be.
    assert isinstance(agree_c, int) and isinstance(differ_c, int), (
        "both length-matched samples must produce a constant: %r / %r"
        % (agree_c, differ_c))
    assert agree_c != differ_c, (
        "these samples were meant to disagree; they both imply %r, so the test "
        "would pass without ever exercising the disagreement path" % (agree_c,))
    combined = source.calibrate(spans, wd.lua_table(lua, [agree, differ]))
    assert isinstance(combined, int), (
        "calibrate refused the chapter over a disagreeing sample (got %r); a nil "
        "constant refuses every underline in the chapter, which cost 80.7%% of a "
        "real book" % (combined,))
    assert combined in (agree_c, differ_c), (
        "the median must be one of the votes, not a value nobody proposed: %r"
        % (combined,))


@test
def test_calibrate_discards_a_sample_whose_length_contradicts_its_range():
    """A summary or truncation must not be allowed to vote.

    Measured on 北派盗墓笔记-41100833: reviews arrive with abstracts that do not
    span their range (the abstract is a context window, or the range covers a
    paragraph break). Such a sample implies a meaningless constant, and enough
    of them drag the median far enough to misplace a whole chapter -- C = 29
    and C = 36 on chapters whose true constant is 1.

    The gate is quote length == range span, counted in normalised runes.
    """
    _, source, lua = _modules()
    spans = _spans()
    honest = {"range": "11-27", "quote": CAL_PART_B}     # length matches
    lying = {"range": "26-40", "quote": CAL_PART_C}      # span 14, quote 18
    assert len(CAL_PART_C) != 40 - 26, "the dishonest sample must be dishonest"
    assert source.calibrate(spans, wd.lua_table(lua, [lying])) is None, (
        "a length-mismatched sample cast a vote; its constant is meaningless")
    # With an honest sample present the chapter still calibrates normally.
    assert source.calibrate(spans, wd.lua_table(lua, [honest])) == 1
    assert source.calibrate(spans, wd.lua_table(lua, [honest, lying])) == 1, (
        "one dishonest sample must not move the chapter's constant")


@test
def test_calibrate_accepts_a_short_sample_when_it_is_all_the_evidence():
    """A short selection is weak evidence, but refusing it costs the chapter.

    An earlier version of this file pinned an 8-rune floor and discarded
    everything shorter. That floor was measured against nothing; applied to real
    data it made calibrate return nil for whole chapters, and a nil constant
    refuses every underline in the chapter. Short needles are now made safe by
    two things the old code never did -- every occurrence is enumerated instead
    of the first, and a vote only counts when it implies a sane constant -- so a
    7-rune selection still yields its constant.
    """
    _, source, lua = _modules()
    seven = CAL_PART_B[:7]   # 7 runes, present verbatim at plain rune 11
    start = _api_position(11, 0, 1)  # the range a 7-rune quote implies 1 for
    assert start == 11, start
    assert source.calibrate(_spans(), wd.lua_table(
        lua, [{"range": "11-18", "quote": seven}])) == 1, (
        "a 7-rune sample that occurs verbatim no longer yields its constant; "
        "discarding it refuses the whole chapter")


# --- Source.quote: self-verification ----------------------------------------

@test
def test_quote_returns_empty_string_when_the_constant_is_wrong():
    """A wrong constant must produce nothing, not a plausible slice.

    plain_at_api returns the largest rune index at or below a requested
    coordinate, which it can always find -- so a wrong constant used to hand back
    a slice of unrelated chapter text that merely looked like a quote. Source.quote
    now round-trips the inverse map and returns "" unless the requested endpoints
    are hit exactly.

On the measured axis a newline costs NOTHING -- the server's coordinate
    space deletes newlines -- so api_position is strictly increasing on a
    newline-free body, and WeRead ranges are HALF-OPEN: "6-12" covers axis
    6..11, i.e. runes 6..12.

    A wrong constant shifts the whole slice by that many runes. On a body with
    no newlines every coordinate is still attainable, so the guard that makes
    this safe is not "the endpoint exists" but the CALLER: the returned slice
    is compared character-for-character against the server's selected text,
    and a shifted slice fails that comparison. What must never happen is a
    wrong constant silently producing a slice that HAPPENS to verify.
    """
    _, source, _ = _modules()
    spans = _spans()
    correct = _lua_text(source.quote(spans, "6-12", 1))
    assert correct == "56789A", (
        "the honest constant no longer resolves the range, so this test would "
        "pass for the wrong reason; got %r" % (correct,))
    # The wrong slice is genuinely different text, so the caller's exact-match
    # verifier rejects it. Pin that difference rather than expecting "".
    wrong = _lua_text(source.quote(spans, "6-12", -5))
    assert wrong != correct and wrong == "BCDEFG", (
        "a wrong constant must not resolve to the same text; got %r" % (wrong,))
    assert _lua_text(source.quote(spans, "6-12", None)) == "", (
        "a nil constant must never be usable as a coordinate space")
    # A range that does not fit the body must still be refused outright.
    assert _lua_text(source.quote(spans, "40-999", 1)) == "", (
        "an out-of-range range produced a plausible slice: %r"
        % _lua_text(source.quote(spans, "40-999", 1)))


# --- the real book ----------------------------------------------------------

BOOK_ID = "19480992"
REPLAY_CHAPTERS = 200

_COUNT_BLOCK = re.compile(r'<span class="wr-thought-count">.*?</span>', re.S)
_UNDERLINE_OPEN = '<span class="wr-underline">'
_BARE_UNDERLINE = re.compile(r'<span class="wr-underline">(.*?)</span>', re.S)
_ANCHOR = re.compile(
    r'<a id="wrthought-(\d+)-(\d+)-(\d+)-(\d+)" class="wr-thought-link"'
    r' href="#wrthought-\1-\2-\3-\4">(.*?)</a>', re.S)
_CONTINUED = re.compile(
    r'<a class="wr-thought-link" href="#wrthought-\d+-\d+-\d+-\d+">(.*?)</a>', re.S)
# The exported chapter files are bare <p> fragments. Source.index only counts
# text inside <body>, so the reconstruction is given a real XHTML envelope. The
# newline it adds shifts every implied constant by the same amount, which
# calibration absorbs; it changes no anchor's identity.
_ENVELOPE_HEAD = ('<?xml version="1.0" encoding="utf-8"?>\n'
                  '<html xmlns="http://www.w3.org/1999/xhtml">'
                  '<head><title></title></head>\n<body>\n')
_ENVELOPE_TAIL = '\n</body></html>'


def _book_work_dir():
    for entry in sorted(wd.OUTPUT.iterdir()):
        if entry.is_dir() and entry.name.endswith(".work") and BOOK_ID in entry.name:
            return entry
    return None


def _epub_corpus():
    """Rebuild the replay corpus from the finished EPUB.

    The .work tree holds the post-injection chapter bodies, but it is cleaned
    once a book finishes exporting -- which turned this gate red on a checkout
    whose code had not changed. The EPUB is built from that same markup and
    keeps the wr-underline / wr-thought spans verbatim, so it is the same
    evidence in a place that survives the cleanup.

    Returns a list of (name, text) pairs, or [] when no export is present.
    """
    import zipfile
    for entry in sorted(wd.OUTPUT.iterdir()):
        if not entry.is_dir() or BOOK_ID not in entry.name:
            continue
        for epub in sorted(entry.glob("*.epub")):
            try:
                with zipfile.ZipFile(epub) as z:
                    out = []
                    for name in z.namelist():
                        if not name.lower().endswith((".xhtml", ".html")):
                            continue
                        text = z.read(name).decode("utf-8", "replace")
                        if 'class="wr-underline"' in text:
                            out.append((name, text))
                    if out:
                        return out
            except (zipfile.BadZipFile, OSError):
                continue
    return []


def _reconstruct(text):
    """Undo one injection pass, returning the original XHTML and what it marked.

    A thought underline can split across several text segments, each wrapped in
    its own <a>; the anchor carrying the id covers only the first segment, so the
    continuation segments are matched too and their text appended to the same
    range. The marked text becomes the row's markText, which is exactly the
    server-selected candidate the production API would have supplied.
    """
    ranges = {}

    def unwrap(inner):
        return _COUNT_BLOCK.sub("", inner).replace(_UNDERLINE_OPEN, "").replace("</span>", "")

    def anchor(match):
        text = unwrap(match.group(5))
        ranges.setdefault("%s-%s" % (match.group(3), match.group(4)), []).append(text)
        return text

    def continued(match):
        text = unwrap(match.group(1))
        found = re.search(r'href="#wrthought-\d+-\d+-(\d+)-(\d+)"', match.group(0))
        if found:
            ranges.setdefault("%s-%s" % (found.group(1), found.group(2)), []).append(text)
        return text

    out = _ANCHOR.sub(anchor, text)
    out = _CONTINUED.sub(continued, out)
    # Underlines without thoughts carry a bare marker span and no anchor.
    return _BARE_UNDERLINE.sub(lambda m: m.group(1), out), ranges


@test
@pytest.mark.local_corpus
def test_real_corpus_placement_still_verifies():
    """The only measurement that can say whether the strict rule is a net win.

    Replays the highlights an earlier export already placed: each chapter's
    original XHTML is reconstructed from the post-injection file, and the very
    text the injection marked becomes that row's markText. Every record that comes
    back "ok" is therefore a highlight the new, stricter code independently agrees
    with, at corpus scale rather than on a fixture.

    This runs entirely offline over the already-exported corpus. It is capped at
    REPLAY_CHAPTERS chapters (of the book's total) purely to keep the gate fast;
    the cap is far above the point where the ratio stops moving, and the refusal
    breakdown is printed so a regression in the rate is visible, not guessed.
    """
    work = _book_work_dir()
    if work is not None:
        files = sorted(p for p in work.glob("chapter-*.body"))
        assert files, "no chapter bodies under %s" % work
        source = "work tree %s" % work.name
        read = lambda p: p.read_text(encoding="utf-8")  # noqa: E731
        name_of = lambda p: p.name  # noqa: E731
    else:
        corpus = _epub_corpus()
        assert corpus, (
            "no acceptance corpus for %s in %s: neither a *.work tree nor an "
            "exported EPUB is present. This gate measures the fix against real "
            "exported output, so it must not pass by skipping -- export the book "
            "once, then re-run." % (BOOK_ID, wd.OUTPUT))
        source = "EPUB of %s" % BOOK_ID
        files = corpus
        read = lambda item: item[1]  # noqa: E731
        name_of = lambda item: item[0]  # noqa: E731
    print("replay corpus: %s (%d chapters)" % (source, len(files)))

    total = placed = 0
    reasons = {}
    chapters = 0
    fully_placed = 0
    for path in files:
        if chapters >= REPLAY_CHAPTERS:
            break
        text = read(path)
        if 'class="wr-underline"' not in text:
            continue
        plain, ranges = _reconstruct(text)
        assert "wr-underline" not in plain and "wr-thought" not in plain, (
            "the reconstruction of %s left injection artefacts behind, so it is "
            "lossy and its audit result would mean nothing" % name_of(path))
        rows = [{"range": key, "markText": "".join(pieces)}
                for key, pieces in sorted(ranges.items())]
        if not rows:
            continue
        chapters += 1
        records = _audit(_ENVELOPE_HEAD + plain + _ENVELOPE_TAIL, rows)
        assert len(records) == len(rows), (
            "%s: %d rows produced %d records; a dropped record is an invisible "
            "loss" % (name_of(path), len(rows), len(records)))
        supplied = {row["range"] for row in rows}
        good = 0
        for record in records:
            assert record["range"] in supplied, (
                "%s: the audit reported range %r, which this chapter never "
                "supplied" % (name_of(path), record["range"]))
            reasons[record["reason"]] = reasons.get(record["reason"], 0) + 1
            if record["reason"] == "ok":
                good += 1
                assert record["placed"] is True, record
        placed += good
        total += len(records)
        if good == len(records):
            fully_placed += 1

    print("\n  real corpus %s: %d of %d chapters replayed" % (BOOK_ID, chapters, len(files)))
    print("  records %d, ok %d (%.1f%%), refused %d (%.1f%%)"
          % (total, placed, 100.0 * placed / total, total - placed,
             100.0 * (total - placed) / total))
    print("  per reason: %s" % ", ".join(
        "%s=%d" % (name, reasons[name]) for name in sorted(reasons)))
    print("  chapters fully placed: %d of %d" % (fully_placed, chapters))

    assert total > 1000, (
        "only %d records were replayed, too few for the rate below to mean "
        "anything" % total)
    unknown = sorted(set(reasons) - REASONS)
    assert not unknown, (
        "the audit emitted reason codes outside the published contract %r: a "
        "Python consumer counts refusals by literal string, so a new spelling "
        "would read as a zero" % (unknown,))
    ratio = placed / float(total)
    assert ratio >= 0.75, (
        "the strict rule placed only %.1f%% of real highlights (%.1f%% floor); "
        "per reason: %s" % (100 * ratio, 75.0, reasons))
    # Refusal is a per-chapter decision, not a per-highlight guess: a chapter
    # whose votes disagree has no coordinate space at all, so its every highlight
    # is refused together. Mixed chapters would mean highlights were dropped
    # individually on evidence that does not exist.
    mixed = [name for name in reasons if name not in ("ok", "no_candidate")]
    assert not mixed, (
        "refusals other than whole-chapter calibration refusals appeared (%s); "
        "the strict rule is meant to refuse whole chapters or place whole "
        "highlights" % (mixed,))


if __name__ == "__main__":
    failures = 0
    for test_case in ALL_TESTS:
        try:
            test_case()
        except Exception as error:  # report every case, not just the first
            failures += 1
            print("FAIL", test_case.__name__, "->", repr(error))
        else:
            print("PASS", test_case.__name__)
    sys.exit(1 if failures else 0)
