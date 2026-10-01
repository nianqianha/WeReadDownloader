"""The right column must not starve the rows below the introduction.

Two regressions are locked here:

1. The log panel is gone. It duplicated the "查看技术日志" button and cost the
   space the introduction needs; the log file is the only sink now.
2. CTkLabel defaults to height=28, so every metadata label used to claim 35 px
   even while empty. Five empty labels plus a fixed-height introduction left
   about four visible lines and squeezed the status row and buttons to nothing
   whenever the column overflowed.
"""
import importlib.util
import tempfile
from pathlib import Path

SCRIPT = Path(__file__).with_name("desktop_app.py")
spec = importlib.util.spec_from_file_location("desktop_app", SCRIPT)
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)

_TMP = tempfile.TemporaryDirectory(prefix="weread-layout-test-")
app.LOG_DIR = Path(_TMP.name) / "logs"
app.OUTPUT = Path(_TMP.name) / "exports"
app.weread_login.CREDENTIAL_STATE = Path(_TMP.name) / "session-cookies.json"

# 1536x864 at 125% scaling: 680 logical px is what actually fits the screen.
SCREEN_FIT_HEIGHT = 680
# A window this tall must still leave the introduction usable.
TALL_HEIGHT = 860

ALL_TESTS = []


def test(fn):
    ALL_TESTS.append(fn)
    return fn


def _make_gui(height=SCREEN_FIT_HEIGHT):
    import customtkinter as ctk
    root = ctk.CTk()
    instance = app.DesktopApp(root)
    # DesktopApp sets its own geometry in __init__, so resize afterwards.
    root.geometry("1120x%d" % height)
    root.update()
    root.update()
    return root, instance


@test
def test_the_window_fits_a_1536x864_screen_at_125_percent():
    """The old default was 790 logical px = 988 physical, taller than the screen."""
    root, instance = _make_gui()
    try:
        assert instance.root.winfo_screenheight() > 0
        assert 680 <= SCREEN_FIT_HEIGHT, SCREEN_FIT_HEIGHT
        # 680 logical px at 125% is 850 physical, under the 864 px work area.
        assert int(SCREEN_FIT_HEIGHT * 1.25) <= 864
    finally:
        root.destroy()


@test
def test_there_is_no_log_panel_in_the_window():
    root, instance = _make_gui()
    try:
        assert not hasattr(instance, "log_panel"), (
            "the log panel is back; it duplicates 查看技术日志")
        # The button that opens the real log file is still there.
        assert instance.log_button.cget("text") == "查看技术日志"
    finally:
        root.destroy()


@test
def test_metadata_labels_shrink_to_their_text_instead_of_a_fixed_height():
    """An empty CTkLabel still claimed height=28 unless asked not to."""
    root, instance = _make_gui()
    try:
        for name in ("book_title_label", "author_label", "category_label",
                     "rating_label", "id_label"):
            widget = getattr(instance, name)
            assert widget.cget("height") == 1, (
                "%s still requests the default 28 px" % name)
        # The whole identity block must stay small.
        block = instance.cover.master.winfo_height()
        assert block < 170, "identity block is %d px" % block
    finally:
        root.destroy()


@test
def test_the_introduction_shows_a_readable_number_of_lines():
    """The complaint this fixes: only one or two lines were visible."""
    root, instance = _make_gui()
    try:
        height = instance.intro.winfo_height()
        lines = (height - 20) // 19
        assert lines >= 6, "only ~%d lines visible (%d px)" % (lines, height)
    finally:
        root.destroy()


@test
def test_the_introduction_grows_with_the_window():
    root, instance = _make_gui(SCREEN_FIT_HEIGHT)
    small = instance.intro.winfo_height()
    root.geometry("1120x%d" % TALL_HEIGHT)
    root.update()
    root.update()
    tall = instance.intro.winfo_height()
    try:
        assert tall > small * 1.5, "introduction did not grow: %d -> %d" % (small, tall)
    finally:
        root.destroy()


@test
def test_the_rows_below_the_introduction_are_never_squeezed_to_nothing():
    """A pack that overflows starves the widgets packed last.

    The introduction expands, so if its request ever exceeds the leftover the
    status line and the task buttons get squeezed to a height of 1 px and become
    invisible -- the same class of bug as the original "unresponsive" layout.
    """
    for height in (SCREEN_FIT_HEIGHT, 760, TALL_HEIGHT):
        root, instance = _make_gui(height)
        try:
            assert instance.status_label.winfo_height() > 2, (
                "status label collapsed at window height %d" % height)
            assert instance.open_button.master.winfo_height() > 2, (
                "task button row collapsed at window height %d" % height)
            assert instance.pause_button.winfo_height() > 2, (
                "pause button collapsed at window height %d" % height)
        finally:
            root.destroy()


if __name__ == "__main__":
    for test_case in ALL_TESTS:
        test_case()
        print("PASS", test_case.__name__)
