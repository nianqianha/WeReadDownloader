# -*- coding: utf-8 -*-
"""A nav item must switch pages when clicked ANYWHERE on it, not only on the
bare button canvas.

The bug this locks: the sidebar row was a CTkButton with a 3px accent bar and
two CTkLabels packed on top of it. Hover still worked (the button's canvas
paints it), so the rail looked alive, but every click landed on a child label
and the button's command never ran -- "mouse over it reacts, clicking does
nothing". Binding the children as well did not help: CustomTkinter's bind()
does not reliably register on those child frames/labels in this build.

The fix: the row is ONE childless CTkButton. CTkButton's command is wired by
CustomTkinter to its own canvas (which is why the top bar buttons work), so a
click anywhere on the row fires it. The glyph and label live in the button's
text, and the reference's 3px green selection bar is a 3px left border.
"""
import importlib.util
import tempfile
from pathlib import Path
import sys

# The program modules live in weread/ and import each other by bare name.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "weread"))
SCRIPT = (Path(__file__).resolve().parent.parent / "weread" / "desktop_app.py")
spec = importlib.util.spec_from_file_location("desktop_app", SCRIPT)
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)

_TMP = tempfile.TemporaryDirectory(prefix="weread-navclick-")
app.LOG_DIR = Path(_TMP.name) / "logs"
app.OUTPUT = Path(_TMP.name) / "exports"
app.weread_login.CREDENTIAL_STATE = Path(_TMP.name) / "session-cookies.json"

ALL = []


def test(fn):
    ALL.append(fn)
    return fn


def _make_instance():
    import customtkinter as ctk
    root = ctk.CTk()
    instance = app.DesktopApp(root)
    root.geometry("1120x680")
    root.update()
    root.update()
    return root, instance


def _click_target(item):
    """The widget a real click reaches on a CTkButton.

    A CTkButton owns an internal CTkCanvas plus a Label. CustomTkinter wires
    the button's command to that canvas, which is what a physical click hits --
    event_generate() addressed to the button itself does NOT fire the command,
    and asserting with it would pass a broken row or fail a working one.
    """
    for child in item.winfo_children():
        if type(child).__name__ == "CTkCanvas":
            return child
    return item


@test
def test_invoking_the_nav_item_switches_the_page():
    """The row's command must run -- this is what a real click does."""
    root, inst = _make_instance()
    try:
        for name in ("我的书架", "下载记录", "统计分析", "划线清单", "设置", "下载工坊"):
            inst.nav_items[name].invoke()   # exactly what a click triggers
            root.update()
            assert inst._current_page == name, (
                "invoking %s did not switch pages (page=%s)"
                % (name, inst._current_page))
    finally:
        root.destroy()


@test
def test_a_click_reaching_the_row_switches_the_page():
    """A press/release delivered to the row's canvas -- a real click's path."""
    root, inst = _make_instance()
    try:
        root.focus_force()
        root.update()
        for name in ("我的书架", "统计分析", "设置", "下载工坊"):
            target = _click_target(inst.nav_items[name])
            assert target.winfo_viewable(), "navigation click target is not mapped"
            assert target.bind("<Button-1>") or target.bind("<ButtonRelease-1>"), \
                "navigation target lost its click binding"
            # CTk 6 activates on release only while the pointer is inside.
            target.event_generate("<Enter>", x=40, y=10)
            target.event_generate("<Button-1>", x=40, y=10)
            root.update()
            target.event_generate("<ButtonRelease-1>", x=40, y=10)
            root.update()
            assert inst._current_page == name, (
                "a click on %s did not switch pages (page=%s)"
                % (name, inst._current_page))
            target.event_generate("<Leave>", x=40, y=10)
    finally:
        root.destroy()


@test
def test_every_nav_row_is_a_single_clickable_button():
    """All six rows must be one CTkButton each: nothing stacked over a child."""
    root, inst = _make_instance()
    try:
        expected = ("下载工坊", "我的书架", "下载记录",
                    "统计分析", "划线清单", "设置")
        assert set(inst.nav_items) == set(expected), sorted(inst.nav_items)
        for name, item in inst.nav_items.items():
            assert isinstance(item, app.ctk.CTkButton), (
                "%s is %s, not a CTkButton -- its command may not fire"
                % (name, type(item).__name__))
            # The row's own content is drawn by the button, so there must be no
            # extra bars/labels of ours stacked on it. (A CTkButton internally
            # owns a canvas + a label; those are the button, not additions.)
            for child in item.winfo_children():
                assert type(child).__name__ in ("CTkCanvas", "Label"), (
                    "%s has an unexpected child %s; it will swallow clicks"
                    % (name, type(child).__name__))
    finally:
        root.destroy()


@test
def test_the_selected_row_shows_the_active_look():
    """Selection must be visible: filled, bright, bold, with the accent bar."""
    root, inst = _make_instance()
    try:
        inst._switch_page("划线清单")
        root.update()
        active_item = inst.nav_items["划线清单"]
        assert active_item._nav_active is True
        assert active_item.cget("border_width") == 3, active_item.cget("border_width")
        assert active_item.cget("text_color") == inst.SIDE_TXT_HI
        # and the row it left behind must be demoted
        workshop = inst.nav_items["下载工坊"]
        assert workshop._nav_active is False
        assert workshop.cget("border_width") == 0
        assert workshop.cget("text_color") == inst.SIDE_TXT
    finally:
        root.destroy()


def main() -> int:
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
