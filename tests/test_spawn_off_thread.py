# -*- coding: utf-8 -*-
"""The worker spawn must never run on the Tk thread.

MEASURED 2026-10-12 (desktop-general.log, two independent launches):
clicking 刷新 blocked the window for 22,975 ms and 26,299 ms -- Windows painted
"(未响应)" -- with five [STALL_STACK] samples all sitting in
subprocess._execute_child called from _start_worker inside a button callback.
Cause: Windows Defender scans a freshly deployed 13 MB exe plus its 3211-file
plugin tree before the first Popen returns, and that scan ran on the Tk thread.

These tests lock the fix: _start_worker hands the Popen to a background
thread, the window stays responsive while the spawn is in flight, and a
second click cannot race a second process.
"""
import importlib.util
import queue
import subprocess
import tempfile
import threading
import time
from pathlib import Path
import sys

# The program modules live in weread/ and import each other by bare name.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "weread"))
SCRIPT = (Path(__file__).resolve().parent.parent / "weread" / "desktop_app.py")
spec = importlib.util.spec_from_file_location("desktop_app", SCRIPT)
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)

_TMP = tempfile.TemporaryDirectory(prefix="weread-spawn-test-")
app.LOG_DIR = Path(_TMP.name) / "logs"
app.OUTPUT = Path(_TMP.name) / "exports"
app.weread_login.CREDENTIAL_STATE = Path(_TMP.name) / "session-cookies.json"

ALL = []


def test(fn):
    ALL.append(fn)
    return fn


class _FakeProc:
    """Stands in for a Popen object; alive until kill() is called."""

    def __init__(self):
        self.stdout = None
        self._alive = True

    def poll(self):
        return None if self._alive else 0

    def kill(self):
        self._alive = False

    def wait(self, *a, **k):
        return 0


def _make_instance():
    import customtkinter as ctk
    root = ctk.CTk()
    instance = app.DesktopApp(root)
    root.update()
    return root, instance


@test
def test_spawn_runs_off_the_tk_thread():
    """_start_worker must return before the Popen happens, not after it."""
    root, inst = _make_instance()
    try:
        calls = []
        real_popen = subprocess.Popen

        def slow_popen(*a, **k):
            # Longer than a Tk tick: if this ran on the Tk thread, the window
            # would freeze for the whole duration.
            time.sleep(0.6)
            calls.append(threading.current_thread())
            return _FakeProc()

        subprocess.Popen = slow_popen
        try:
            t0 = time.perf_counter()
            started = inst._start_worker(["123"], "export", "…")
            returned_ms = (time.perf_counter() - t0) * 1000.0
        finally:
            subprocess.Popen = real_popen
        assert started, "_start_worker refused to start"
        assert returned_ms < 400, (
            "_start_worker blocked for %.0f ms; the Popen still runs on the "
            "calling thread" % returned_ms)
        # Let the spawn thread finish and the event be adopted. update() alone
        # does not run _poll's after-callback promptly, so drain the queue the
        # way _poll would, on the Tk thread, until the process is adopted.
        for _ in range(400):
            root.update()
            inst._drain_events()
            time.sleep(0.01)
            if calls and not inst._spawn_pending:
                break
        assert calls, "the Popen never ran"
        assert calls[0] is not threading.main_thread(), (
            "the Popen ran on the Tk thread")
        # Adoption happened iff the pending flag cleared after the spawn; the
        # fake process ends immediately, so its "done" event may already have
        # reset self.process back to None -- that is correct behaviour, not a
        # failure to adopt.
        assert not inst._spawn_pending, (
            "the spawn finished but the app still thinks one is pending")
    finally:
        root.destroy()


@test
def test_a_pending_spawn_counts_as_busy():
    """A second click while the spawn is in flight must not start a second one."""
    root, inst = _make_instance()
    try:
        real_popen = subprocess.Popen
        gate = threading.Event()

        def gated_popen(*a, **k):
            gate.wait(timeout=10)
            return _FakeProc()

        subprocess.Popen = gated_popen
        try:
            assert inst._start_worker(["123"], "export", "…")
            assert inst.pause_button.cget("state") == "normal"
            # _is_busy() must already be True even though no process exists.
            assert inst._is_busy(), "a pending spawn does not count as busy"
            assert not inst._start_worker(["456"], "export", "…"), (
                "a second start was accepted while the first spawn was pending")
        finally:
            gate.set()
            subprocess.Popen = real_popen
            for _ in range(400):
                root.update()
                inst._drain_events()
                time.sleep(0.01)
                if not inst._spawn_pending:
                    break
    finally:
        root.destroy()


@test
def test_spawn_failure_unsticks_the_app():
    """An OSError off-thread must clear the pending flag and reach the queue."""
    root, inst = _make_instance()
    try:
        real_popen = subprocess.Popen

        def broken_popen(*a, **k):
            raise OSError("simulated Defender block")

        subprocess.Popen = broken_popen
        try:
            assert inst._start_worker(["123"], "export", "…")
            for _ in range(400):
                root.update()
                inst._drain_events()
                time.sleep(0.01)
                if not inst._spawn_pending:
                    break
        finally:
            subprocess.Popen = real_popen
        assert not inst._spawn_pending, "a failed spawn left the app marked busy"
        assert inst.current_action == "", "a failed spawn left the action set"
        assert "没有启动" in str(inst.status.get()), inst.status.get()
    finally:
        root.destroy()


def main() -> int:
    import customtkinter as ctk
    failures = 0
    for fn in ALL:
        try:
            fn()
            print("PASS", fn.__name__)
        except AssertionError as exc:
            failures += 1
            print("FAIL", fn.__name__, "-", exc)
        except Exception as exc:
            failures += 1
            print("FAIL", fn.__name__, "-", f"{type(exc).__name__}: {exc}")
    print()
    print("%d/%d passed" % (len(ALL) - failures, len(ALL)))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
