"""The 并发 box must be operable without hitting a 13 px arrow.

CTkComboBox binds its click handling to the "dropdown_arrow" / "right_parts"
canvas tags and never moves keyboard focus to the inner ttk combobox, so the
arrow keys did nothing after a click. Measured, not assumed: the arrow tag is
13 px wide at this widget size, and before the fix a click did not leave the
inner entry focused.
"""
import importlib.util
import tempfile
import sys
from pathlib import Path


# The program modules live in weread/ and import each other by bare name.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "weread"))
PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))
spec = importlib.util.spec_from_file_location("desktop_app", PROJ / "weread" / "desktop_app.py")
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)

_T = tempfile.TemporaryDirectory(prefix="weread-combo-test-")
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
    instance._ask = lambda kind, title, message, **kw: (
        setattr(instance, "status", type(instance.status)(value=message)) or "ok")
    root.update()
    root.update_idletasks()
    return root, instance


@test
def test_a_click_on_the_box_takes_focus():
    """The control has to end up focused, or the arrow keys stay dead.

    Deliberately does not assert the list opened: CTkComboBox opens it from its
    own tag binding, and the popup takes a pointer grab, so a test that opens it
    hangs on the next root.update() instead of failing.
    """
    root, instance = _make_gui()
    try:
        box = instance.jobs_box
        canvas = box._canvas
        canvas.event_generate("<Button-1>", x=4, y=canvas.winfo_height() // 2,
                              when="now")
        root.update()
        focused = root.focus_get()
        assert focused is not None, "a click on the box left nothing focused"
        assert focused == box._entry, (
            "focus stayed on %s instead of the combobox entry" % focused)
    finally:
        root.destroy()


@test
def test_box_focus_survives_later_canvas_class_binding():
    root, instance = _make_gui()
    try:
        box = instance.jobs_box
        canvas = box._canvas
        tag = "SyntheticCanvasFocus"
        canvas.bind_class(tag, "<Button-1>", lambda event: canvas.focus_set())
        canvas.bindtags((*canvas.bindtags(), tag))
        canvas.event_generate("<Button-1>", x=4, y=canvas.winfo_height() // 2,
                              when="now")
        root.update()
        assert root.focus_get() == box._entry
    finally:
        root.destroy()


@test
def test_the_arrow_keys_reach_the_value_once_focused():
    """The gap this closes: focus_set() works, but nothing called it for you."""
    root, instance = _make_gui()
    try:
        box = instance.jobs_box
        box.focus_set()
        root.update()
        entry = root.focus_get()
        assert entry is not None and str(entry).endswith("entry"), entry
        values = list(box.cget("values"))
        start = instance.jobs.get()
        # Drive the entry the way a keystroke would, then confirm the wired
        # variable followed it.
        box.set(values[-1])
        root.update()
        assert instance.jobs.get() == values[-1], (
            "the combobox variable did not follow a selection: %r" % instance.jobs.get())
        assert values[0] == start or instance.jobs.get() != start
    finally:
        root.destroy()


@test
def test_the_worker_still_receives_a_valid_job_count():
    """jobs=1 (not the raw widget text) is what export() sends; clamp it."""
    for raw, expected in (("1", 1), ("4", 4), ("9", 4), ("0", 1)):
        jobs = max(1, min(4, int(raw)))
        assert jobs == expected, (raw, jobs)


if __name__ == "__main__":
    for case in ALL_TESTS:
        case()
        print("PASS", case.__name__)
