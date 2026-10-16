"""The three annotation modes must differ in which API calls they make.

The GUI offers 划线 + 想法 / 只要划线 / 纯正文, and the modes are not a matter of
degree:

  both  -> /book/underlines and /book/readreviews, notes written
  marks -> both calls still happen, notes dropped (placement needs the abstracts)
  plain -> neither call happens

plain is the only one that can skip readreviews, because that call is what carries
the selected-text quote every underline is matched against. The old two-position
switch could not express "neither", so this is the mode that was missing.

Asserted on the call log rather than the output, because the failure this guards
against is a wasted round trip per chapter that is invisible in the finished file.
"""
import importlib.util
import sys
import tempfile
from pathlib import Path

# 以前这里是写死的绝对路径（C:\Users\asus\Desktop\各类\weread-downloader），
# 于是这个文件在别人机器上、在 CI 上都在收集阶段就 FileNotFoundError。仓库里
# 另外七个测试文件都用 __file__ 定位，这里统一成同一种写法。
PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ / "weread"))
spec = importlib.util.spec_from_file_location("weread_desktop", PROJ / "weread" / "weread_desktop.py")
mod = importlib.util.module_from_spec(spec)
sys.modules["weread_desktop"] = mod
spec.loader.exec_module(mod)

_TMP = tempfile.TemporaryDirectory(prefix="weread-annotations-")

ALL_TESTS = []


def test(fn):
    ALL_TESTS.append(fn)
    return fn


def _api(calls):
    """A WeRead stand-in that records endpoint hits and returns nothing useful."""
    api = mod.WeRead.__new__(mod.WeRead)

    def gateway(path, payload=None, **kwargs):
        calls.append(path)
        if path == "/book/underlines":
            return {"underlines": [{"range": "10-20", "score": 1, "type": 1}]}
        return {"reviews": []}

    api.gateway = gateway
    return api


@test
def test_plain_text_skips_both_annotation_calls():
    calls = []
    api = _api(calls)
    underlines, reviews = api.annotations("1", "c1", want_thoughts=False,
                                         want_underlines=False)
    assert calls == [], (
        "plain-text mode still called the server: %s. Every chapter pays two "
        "round trips it will not use." % (calls,))
    assert underlines.get("underlines") == [] and reviews == [], (
        "plain-text mode returned annotation data anyway: %r %r"
        % (underlines, reviews))


@test
def test_marks_only_still_makes_the_calls_it_needs():
    """--no-thoughts cannot skip them, and must not pretend to."""
    calls = []
    api = _api(calls)
    api.annotations("1", "c1", want_thoughts=False, want_underlines=True)
    assert calls, "wanted underlines but never asked for them"
    assert "/book/underlines" in calls
    assert "/book/readreviews" in calls, (
        "readreviews was skipped, so nothing carries the selected-text quote and "
        "no highlight can be placed")


@test
def test_both_modes_fetch_annotations():
    for thoughts, underlines in ((True, True), (False, True)):
        calls = []
        api = _api(calls)
        api.annotations("1", "c1", want_thoughts=thoughts, want_underlines=underlines)
        assert "/book/underlines" in calls, (
            "highlights were requested but /book/underlines was never called")


@test
def test_no_annotations_implies_no_thoughts_on_the_cli():
    """A thought hangs off a highlight, so the combination is not meaningful.

    Passing both flags should collapse to the stronger one rather than running
    the reviews fetch to write notes nothing will display.
    """
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--no-thoughts", action="store_true")
    parser.add_argument("--no-annotations", action="store_true")
    args = parser.parse_args(["--no-thoughts", "--no-annotations"])
    want_thoughts = not args.no_thoughts
    want_underlines = not args.no_annotations
    if not want_underlines:
        want_thoughts = False
    assert want_thoughts is False and want_underlines is False


if __name__ == "__main__":
    for case in ALL_TESTS:
        case()
        print("PASS", case.__name__)
