"""Prove the stall watcher fires and reports a real stuck stack.

An unverified watchdog is worse than none: it either never fires when it should
or floods the log. This blocks the Tk thread in a sleep (simulating the freeze)
and asserts that [STALL_STACK] lands in the log naming the frame that is
actually running, then asserts it stays quiet while the app is healthy.
"""
import importlib.util
import sys
import tempfile
import time
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent / "weread"
sys.path.insert(0, str(PROJ))
spec = importlib.util.spec_from_file_location("desktop_app", PROJ / "desktop_app.py")
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)

_TMP = tempfile.TemporaryDirectory(prefix="weread-stall-")
app.LOG_DIR = Path(_TMP.name) / "logs"
app.OUTPUT = Path(_TMP.name) / "exports"

import customtkinter as ctk


def test_stall_watcher_records_real_stuck_stack():
    root = ctk.CTk()
    instance = app.DesktopApp(root)

    def log_text():
        return "\n".join(p.read_text(encoding="utf-8", errors="replace")
                         for p in app.LOG_DIR.glob("*.log"))

    def _block():
        time.sleep(3)
        root.quit()

    try:
        root.after(2500, root.quit)
        root.mainloop()
        assert "[STALL_STACK]" not in log_text(), "healthy event pumping reported a stall"
        root.after(0, _block)
        root.mainloop()
        text = log_text()
        assert "[STALL_STACK]" in text, "the blocked callback was not reported"
        assert "_block" in text.split("[STALL_STACK]", 1)[1], text
        main_id = __import__("threading").main_thread().ident
        assert "<no frame" not in instance._stack_of(main_id)
    finally:
        root.destroy()
    assert not instance._stall_watcher.is_alive(), "destroyed window retained its watcher"


if __name__ == "__main__":
    test_stall_watcher_records_real_stuck_stack()
