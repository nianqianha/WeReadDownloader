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

root = ctk.CTk()
root.geometry("1120x680")
instance = app.DesktopApp(root)
root.update()

results = []


def log_text():
    out = []
    for p in app.LOG_DIR.glob("*.log"):
        out.append(p.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(out)


# 1. Healthy: the watcher must stay quiet.
time.sleep(3.0)
quiet = "[STALL_STACK]" not in log_text()
results.append(("stays quiet while healthy", quiet, ""))

# 2. Blocked: the Tk thread is held in a sleep; the watcher must name it.
def _block():
    time.sleep(6.0)


instance.root.after(300, lambda: (_block(), root.quit()))
t0 = time.perf_counter()
# Pump events in small steps so the blocking callback actually runs.
for _ in range(80):
    try:
        root.update()
    except Exception:
        pass
    time.sleep(0.1)
    if time.perf_counter() - t0 > 9:
        break

text = log_text()
fired = "[STALL_STACK]" in text
results.append(("fires when the Tk thread is stuck", fired, ""))

# The stack is on the line AFTER the header, so search the whole entry.
tail = text.split("[STALL_STACK]", 1)[1] if fired else ""
names_blocked = ("sleep" in tail) or ("_block" in tail)
results.append(("names the stuck frame", names_blocked, tail.strip()[:170]))

# 3. _stack_of must work for the live main thread.
main_id = threading_id = __import__("threading").main_thread().ident
stack = instance._stack_of(main_id)
results.append(("_stack_of returns a usable stack", "<no frame" not in stack, stack[:110]))

try:
    root.destroy()
except Exception:
    pass

print("%-42s %s" % ("CHECK", "RESULT"))
ok = True
for name, passed, detail in results:
    ok = ok and passed
    print("%-42s %-5s %s" % (name, "PASS" if passed else "FAIL", detail))
print()
print("ALL PASS" if ok else "SOME FAILED")
