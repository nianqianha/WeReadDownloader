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
import sys

# The program modules live in weread/ and import each other by bare name.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "weread"))
PROJ = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("desktop_app", PROJ / "weread" / "desktop_app.py")
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


@test
def test_a_stall_never_reports_a_negative_duration():
    """The duration is read once, so a tick landing mid-report cannot invert it.

    The real log contains "[STALL_STACK] 01:30:06 stuck -4124ms". The guard read
    _last_tick_end, then the log line read it again; a _poll() in between set it
    past the sampler's own clock and the subtraction went negative. This is the
    one line used to tell a suspend from a freeze, so a negative value there
    makes a real stall look like a broken clock.
    """
    root, instance = _make_gui()
    captured = []
    real_persist = instance._persist_log
    real_stack_of = instance._stack_of

    def racing_stack_of(thread_id):
        # This runs between the ">2 s stale" guard and the log line, which is
        # exactly where a real _poll() can land. Stand in for that tick.
        instance._last_tick_end = time.perf_counter() + 30.0
        return real_stack_of(thread_id)

    def racing_persist(msg):
        captured.append(msg)

    def _block():
        # The watchdog only fires when the Tk thread genuinely stops ticking, so
        # the freeze has to be real rather than simulated by idling the loop.
        time.sleep(7.0)

    try:
        instance._modal_depth = 0
        instance._stack_of = racing_stack_of
        instance._persist_log = racing_persist
        root.after(200, _block)
        deadline = time.time() + 20.0
        while not captured and time.time() < deadline:
            try:
                root.update()
            except Exception:
                pass
            time.sleep(0.1)
        stuck_lines = [m for m in captured if m.startswith("[STALL_STACK]")]
        assert stuck_lines, (
            "the sampler never reported, so the race was not exercised:\n"
            + "\n".join(captured))
        for msg in stuck_lines:
            stuck = float(msg.split("stuck ", 1)[1].split("ms", 1)[0])
            assert stuck >= 0, (
                "reported a negative stall duration, so the record is junk: "
                + msg.splitlines()[0])
    finally:
        instance._stack_of = real_stack_of
        instance._persist_log = real_persist
        root.destroy()


if __name__ == "__main__":
    for case in ALL_TESTS:
        case()
        print("PASS", case.__name__)
