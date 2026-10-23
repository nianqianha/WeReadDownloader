"""The stall watchdog must not cry wolf about a dialog the user has not answered.

Driving the real window produced log entries that were impossible to act on:

    [STALL] 02:46:34 gap=110ms   blocked=87663ms action=-   <- a "下载完成" dialog
    [STALL] 02:52:40 gap=78456ms blocked=0ms    action=-   <- the QR login window
    [STALL] 02:27:20 gap=95813ms blocked=0ms    action=-   <- the real one

A modal takes over the Tk main loop until it is answered, so the thread is
"stuck" for exactly as long as the user takes to read it. Those two entries were
87 s and 78 s of a person deciding whether to click 是(Y). The third is the
defect they were hiding.

So: while a dialog is up, log [STALL-MODAL] and keep the stack sampler quiet.
A [STALL] line must mean something is actually wrong.
"""
import importlib.util
import json
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

_T = tempfile.TemporaryDirectory(prefix="weread-stall-modal-")
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
    return "\n".join(p.read_text(encoding="utf-8", errors="replace")
                     for p in sorted(app.LOG_DIR.glob("*.log")))


@test
def test_the_window_knows_it_is_not_currently_in_a_dialog():
    root, instance = _make_gui()
    try:
        assert instance._modal_depth == 0, "the counter must start at zero"
    finally:
        root.destroy()


@test
def test_an_open_dialog_is_counted_and_released_even_if_it_raises():
    """The counter is the only thing standing between a dialog and a false stall,
    so a failure inside messagebox must not leave it stuck high."""
    root, instance = _make_gui()
    try:
        from tkinter import messagebox
        real = messagebox.askokcancel
        try:
            messagebox.askokcancel = lambda *a, **k: "ok"
            assert instance._ask("askokcancel", "t", "m") == "ok"
            assert instance._modal_depth == 0, "counter not released after a normal answer"

            def _boom(*a, **k):
                raise RuntimeError("messagebox exploded")
            messagebox.askokcancel = _boom
            assert instance._ask("askokcancel", "t", "m") is False
            assert instance._modal_depth == 0, "counter left high after an exception"
        finally:
            messagebox.askokcancel = real
    finally:
        root.destroy()


@test
def test_a_dialog_wait_is_not_reported_as_a_freeze():
    """The whole point: hold the loop the way a modal does, and require that the
    log says STALL-MODAL rather than STALL."""
    root, instance = _make_gui()
    try:
        # The first two ticks are the startup grace (the initial paint is not a
        # freeze); consume them before measuring.
        for _ in range(2):
            instance._poll()
        # Simulate the main loop being held inside a dialog, exactly as
        # messagebox does: the tick never completes and no work is drained.
        instance._modal_depth += 1
        try:
            instance._last_tick_end = time.perf_counter() - 3.0
            for _ in range(3):
                instance._poll()
                time.sleep(0.05)
        finally:
            instance._modal_depth -= 1
        text = _logs()
        assert "[STALL-MODAL]" in text, "a held dialog was not labelled as one"
        assert "[STALL] " not in text, (
            "a dialog wait was still reported as a real stall:\n" + text)
    finally:
        root.destroy()


@test
def test_a_real_freeze_is_still_reported_as_a_stall():
    """The other half: with no dialog up, a wedged thread must still be caught."""
    root, instance = _make_gui()
    try:
        for _ in range(2):
            instance._poll()   # consume the startup grace
        instance._modal_depth = 0
        instance._last_tick_end = time.perf_counter() - 3.0
        instance._poll()
        time.sleep(0.2)
        text = _logs()
        assert "[STALL] " in text, (
            "a genuine stall stopped being reported; the watchdog is now blind:\n" + text)
    finally:
        root.destroy()


if __name__ == "__main__":
    for case in ALL_TESTS:
        case()
        print("PASS", case.__name__)
