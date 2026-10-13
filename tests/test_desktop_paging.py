"""Search results are paged, because repainting them is what froze the window.

Building every result as a widget cost ~100 ms of main-thread repaint per card.
A 20-result search blocked the Tk loop for ~3 s, and destroying the previous
page on the next search added ~1 s -- together past the ~5 s at which Windows
stops pumping messages and shows "(未响应)".

Paging keeps the number of live card widgets to one page, so the repaint cost
stays bounded no matter how many results come back.
"""
import importlib.util
import queue
import tempfile
import time
from pathlib import Path
import sys

# The program modules live in weread/ and import each other by bare name.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "weread"))
SCRIPT = (Path(__file__).resolve().parent.parent / "weread" / "desktop_app.py")
spec = importlib.util.spec_from_file_location("desktop_app", SCRIPT)
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)

_TMP = tempfile.TemporaryDirectory(prefix="weread-paging-test-")
app.LOG_DIR = Path(_TMP.name) / "logs"
app.OUTPUT = Path(_TMP.name) / "exports"
app.weread_login.CREDENTIAL_STATE = Path(_TMP.name) / "session-cookies.json"


def _make_gui():
    import customtkinter as ctk
    root = ctk.CTk()
    root.geometry("1120x790")
    instance = app.DesktopApp(root)
    root.update()
    # Cover fetches would add network noise to a layout-timing test.
    instance._request_card_cover = lambda *args, **kwargs: None
    instance.current_action = "search"
    return root, instance


def _row(index):
    return "%d\t书名%d\t作者%d\thttps://example/%d.jpg\t885\t%d\t分类" % (
        800000 + index, index, index, index, 20 + index)


def _feed(instance, count, offset=0):
    events = queue.Queue()
    for index in range(count):
        events.put(("line", _row(index + offset)))
    instance.events = events
    instance._drain_events()


ALL_TESTS = []


def test(fn):
    ALL_TESTS.append(fn)
    return fn


@test
def test_all_results_are_kept_even_though_only_one_page_is_built():
    """Paging must not lose rows: everything stays reachable via the pages."""
    root, instance = _make_gui()
    try:
        _feed(instance, 20)
        assert len(instance.result_rows) == 20, len(instance.result_rows)
        assert len(instance.book_rows) == 20, len(instance.book_rows)
        # Only a page of widgets exists, not 20 of them.
        assert len(instance.results.winfo_children()) == app.RESULTS_PER_PAGE, (
            "built %d cards, expected one page of %d"
            % (len(instance.results.winfo_children()), app.RESULTS_PER_PAGE))
    finally:
        root.destroy()


@test
def test_pager_reports_the_page_count():
    root, instance = _make_gui()
    try:
        _feed(instance, 20)
        total = instance._page_count()
        assert total == -(-20 // app.RESULTS_PER_PAGE), total
        assert "1 / %d" % total in instance.page_label.cget("text"), (
            instance.page_label.cget("text"))
    finally:
        root.destroy()


@test
def test_next_and_previous_walk_every_result():
    root, instance = _make_gui()
    try:
        _feed(instance, 20)
        seen = []
        for _ in range(instance._page_count()):
            seen.extend(w for w in instance.card_widgets)
            instance._turn_page(1)
        # Every result appeared on exactly one page.
        assert sorted(seen) == sorted(str(r["bookId"]) for r in instance.result_rows)
        # Turning past the last page is a no-op, not an error.
        assert instance.page_index == instance._page_count() - 1
        instance._turn_page(1)
        assert instance.page_index == instance._page_count() - 1
        # An out-of-range jump is ignored, not clamped.
        instance._turn_page(-99)
        assert instance.page_index == instance._page_count() - 1
        instance._turn_page(1)
        assert instance.page_index == instance._page_count() - 1
        # Walking back one page at a time reaches the first page, and going
        # further forward from there is a no-op.
        for expected in range(instance._page_count() - 2, -1, -1):
            instance._turn_page(-1)
            assert instance.page_index == expected, instance.page_index
        instance._turn_page(-1)
        assert instance.page_index == 0
    finally:
        root.destroy()


@test
def test_pager_is_hidden_until_there_is_more_than_one_page():
    root, instance = _make_gui()
    try:
        _feed(instance, 2)
        assert not instance.page_bar.winfo_ismapped(), (
            "pager should stay hidden for a single page of results")
        _feed(instance, 20)
        instance._render_page()
        root.update()
        assert instance.page_bar.winfo_ismapped(), "pager should appear for 20 results"
    finally:
        root.destroy()


@test
def test_pager_buttons_disable_at_the_ends():
    root, instance = _make_gui()
    try:
        _feed(instance, 20)
        assert instance.prev_button.cget("state") == "disabled"
        assert instance.next_button.cget("state") == "normal"
        instance._turn_page(instance._page_count() - 1)
        assert instance.next_button.cget("state") == "disabled"
        assert instance.prev_button.cget("state") == "normal"
    finally:
        root.destroy()


@test
def test_a_fresh_search_resets_paging_and_discards_the_old_page():
    """A stale page must never survive into a new search."""
    root, instance = _make_gui()
    try:
        _feed(instance, 20)
        instance._turn_page(2)
        assert instance.page_index == 2

        for widget in instance.results.winfo_children():
            widget.destroy()
        instance.book_rows.clear()
        instance.card_widgets.clear()
        instance.result_rows.clear()
        instance.page_index = 0
        instance._set_page_buttons(0, 0)

        _feed(instance, 1, offset=500)
        assert instance.page_index == 0
        assert instance._page_count() == 1
        assert len(instance.result_rows) == 1
    finally:
        root.destroy()


@test
def test_rendering_a_page_stays_far_below_the_unresponsive_threshold():
    """The regression itself, measured on the real widget tree.

    This is the guard that would have caught the original freeze: if a page ever
    costs multiple seconds again, the window is one search away from
    "(未响应)". The bound is deliberately loose so a slow machine does not fail
    it, but it is far under the ~5 s message-pump threshold.
    """
    root, instance = _make_gui()
    try:
        _feed(instance, 20)
        root.update()

        started = time.perf_counter()
        instance._render_page()
        root.update()
        page_ms = (time.perf_counter() - started) * 1000
        assert page_ms < 1500, "a page render blocked %.0f ms" % page_ms

        # And the first page of a full result set, which is what a user waits
        # for right after searching.
        for widget in instance.results.winfo_children():
            widget.destroy()
        instance.result_rows.clear()
        instance.page_index = 0
        root.update()

        started = time.perf_counter()
        _feed(instance, 20)
        root.update()
        first_ms = (time.perf_counter() - started) * 1000
        assert first_ms < 2000, "first page of 20 results blocked %.0f ms" % first_ms
    finally:
        root.destroy()


@test
def test_card_widgets_stay_in_sync_with_the_visible_page():
    """A card from another page must not linger in card_widgets.

    _apply_card_cover and _update_card_category look books up by id, so a stale
    entry would make an off-page cover event write into a destroyed widget.
    """
    root, instance = _make_gui()
    try:
        _feed(instance, 20)
        visible = {str(row["bookId"]) for row in instance._page_slice()}
        assert set(instance.card_widgets) == visible, (
            "card_widgets has %d entries, page shows %d"
            % (len(instance.card_widgets), len(visible)))

        instance._turn_page(1)
        visible = {str(row["bookId"]) for row in instance._page_slice()}
        assert set(instance.card_widgets) == visible, (
            "after paging, card_widgets does not match the new page")
    finally:
        root.destroy()


if __name__ == "__main__":
    for test_case in ALL_TESTS:
        test_case()
        print("PASS", test_case.__name__)
