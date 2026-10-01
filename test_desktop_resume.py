"""A machine that goes to sleep is not an application that hangs.

Reported as: leave the app alone for a while and it is usually "(未响应)" when
you come back, but hammering the restart button never shows it. The log had the
answer, and it was not a freeze:

    [STALL] 02:27:20 gap=95813ms blocked=0ms action=-    queue=0
    [STALL] 02:30:04 gap=45287ms blocked=0ms action=auth queue=0
    [STALL] 02:31:09 gap=26940ms blocked=0ms action=-    queue=0

blocked=0ms with an empty queue means the Tk thread was not executing anything at
all, yet the wall clock moved 26-96 s, and every timestamp is between 02:27 and
02:31. That is the OS suspending the process. Restarting quickly cannot produce
it, which is why only long idles show it.

The wall clock cannot tell a suspend from a wedge: both look like a large gap.
The sampler thread can, because it is not blocked by whatever wedges the Tk
thread. It asks for time.sleep(0.2) and measures what it actually got.

So: a suspend is logged as [RESUME] with a repaint, and a genuine wedge is still
logged as [STALL] with its stack. Both halves are asserted, because confusing
them in either direction is the defect.
"""
import importlib.util
import tempfile
import time
from pathlib import Path

PROJ = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("desktop_app", PROJ / "desktop_app.py")
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)

_T = tempfile.TemporaryDirectory(prefix="weread-resume-")
app.LOG_DIR = Path(_T.name) / "logs"
app.OUTPUT = Path(_T.name) / "exports"
app.weread_login.CREDENTIAL_STATE = Path(_T.name) / "session-cookies.json"

ALL_TESTS = []


def test(fn):
    ALL_TESTS.append(fn)
    return fn


def _make_gui():
    import customtkinter as ctk
    root = ctk.CTk()
    root.geometry("1120x680")
    instance = app.DesktopApp(root)
    root.update()
    return root, instance


def _logs():
    out = []
    for path in sorted(app.LOG_DIR.glob("desktop-*.log")):
        out.append(path.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(out)


def _clear_logs():
    for path in app.LOG_DIR.glob("desktop-*.log"):
        path.write_text("", encoding="utf-8")


@test
def test_a_sampler_overrun_is_reported_as_a_resume_not_a_stall():
    """The 02:27 case: wall clock jumped, no thread was running."""
    root, instance = _make_gui()
    try:
        instance._modal_depth = 0
        _clear_logs()  # drop anything the construction itself logged
        # The sampler saw its own 0.2 s sleep come back 95 s late.
        instance._resume_mark = time.perf_counter()
        # The main loop's next tick sees the matching 95 s gap.
        instance._last_tick_end = time.perf_counter() - 95.0
        instance._poll()
        time.sleep(0.3)
        text = _logs()
        assert "[RESUME]" in text, (
            "a system suspend was not recognised as one:\n" + text)
        assert "[STALL] " not in text, (
            "a machine going to sleep was still blamed on the app:\n" + text)
    finally:
        root.destroy()


@test
def test_a_real_wedge_is_still_reported_as_a_stall():
    """The other half: the sampler's sleep was punctual, so the Tk thread is stuck."""
    root, instance = _make_gui()
    try:
        instance._modal_depth = 0
        _clear_logs()
        instance._resume_mark = 0.0  # sampler kept running, nothing suspended
        instance._last_tick_end = time.perf_counter() - 3.0
        instance._poll()
        time.sleep(0.3)
        text = _logs()
        assert "[STALL] " in text, (
            "a genuine freeze stopped being reported; the watchdog is blind:\n"
            + text)
        assert "[RESUME]" not in text, (
            "a real wedge was excused as a system suspend:\n" + text)
    finally:
        root.destroy()


@test
def test_the_resume_mark_is_consumed_so_one_sleep_logs_one_line():
    """A suspend must not be reported forever, once per tick, after waking."""
    root, instance = _make_gui()
    try:
        instance._modal_depth = 0
        instance._resume_mark = time.perf_counter()
        assert instance._consume_resume() is True
        assert instance._consume_resume() is False, (
            "the mark survived being consumed and would re-report the suspend")
    finally:
        root.destroy()


@test
def test_resume_triggers_a_redraw():
    """A window coming back from suspend still holds pre-suspend pixels."""
    root, instance = _make_gui()
    try:
        calls = []
        instance._flush_log_panel = lambda: calls.append("flush")
        instance._note_resume(gap_ms=95000.0)
        root.update()
        assert calls, (
            "no redraw was scheduled after a resume; the window keeps stale pixels")
    finally:
        root.destroy()


@test
def test_building_the_window_is_not_logged_as_a_stall():
    """Building the UI costs over a second and is not a freeze.

    The first tick used to be charged for _build(), so every single launch wrote
    a [STALL] line with a 1.3 s gap. A marker that fires on every start teaches
    the reader to ignore the marker, which is how the 02:27 suspend went
    unnoticed.
    """
    _clear_logs()
    root, instance = _make_gui()
    try:
        instance._poll()
        time.sleep(0.3)
        text = _logs()
        assert "[STALL]" not in text, (
            "launching the app logged a stall, so the marker is noise:\n" + text)
    finally:
        root.destroy()


if __name__ == "__main__":
    for case in ALL_TESTS:
        case()
        print("PASS", case.__name__)
