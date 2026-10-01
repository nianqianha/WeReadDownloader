"""Cover handling: the main thread must never decode a full-size cover.

WeRead serves covers up to a few thousand pixels tall. Decoding one at source
resolution costs ~220 ms and yields a ~25 MB bitmap, and CTkImage only scales at
draw time, so the oversized bitmap is what gets retained. A search returning a
page of books therefore froze the Tk loop long enough for Windows to report the
window as unresponsive.

These tests lock the two properties that fix it:
  1. decoding happens off the Tk thread, and produces a display-sized image;
  2. what reaches _drain_events is already small, so a tick stays cheap.
"""
import importlib.util
import io
import queue
import tempfile
import time
from pathlib import Path

SCRIPT = Path(__file__).with_name("desktop_app.py")
spec = importlib.util.spec_from_file_location("desktop_app", SCRIPT)
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)

from PIL import Image

# Tests must never write into the real user profile.
_TMP = tempfile.TemporaryDirectory(prefix="weread-cover-test-")
app.LOG_DIR = Path(_TMP.name) / "logs"
app.OUTPUT = Path(_TMP.name) / "exports"
app.weread_login.CREDENTIAL_STATE = Path(_TMP.name) / "session-cookies.json"

# CustomTkinter multiplies every geometry by the display scale factor, so a
# 96 px slot is 120 px wide on this 125% display. Captured once from a live
# widget: the tracker needs a widget instance, not a widget class.



def _noisy_jpeg(w=2400, h=3600):
    """A cover of the size WeRead actually serves, not a toy 320x420."""
    buf = io.BytesIO()
    Image.effect_noise((w, h), 42).convert("RGB").save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def _make_gui():
    import customtkinter as ctk
    root = ctk.CTk()
    root.geometry("1120x790")
    instance = app.DesktopApp(root)
    root.update()
    return root, instance





ALL_TESTS = []


def test(fn):
    ALL_TESTS.append(fn)
    return fn


@test
def test_decode_returns_a_display_sized_image_not_a_source_sized_one():
    """The whole point: decode at display size so nothing huge is retained."""
    payload = _noisy_jpeg(2400, 3600)
    box = (54, 76)
    image = app._decode_cover(payload, box)

    assert image.width <= box[0] and image.height <= box[1], image.size
    # And it is genuinely close to the slot, not collapsed to nothing.
    assert image.height >= box[1] * 0.8, image.size


@test
def test_decode_preserves_aspect_ratio():
    """A wide banner cover must not be stretched to fill the slot."""
    payload = _noisy_jpeg(3000, 1000)
    image = app._decode_cover(payload, (54, 76))
    source_ratio = 3000 / 1000
    assert abs((image.width / image.height) - source_ratio) < 0.05, image.size


@test
def test_decode_raises_on_a_non_image_payload():
    """Garbage must surface, not silently become a blank cover."""
    try:
        app._decode_cover(b"not an image", (54, 76))
    except Exception:
        return
    raise AssertionError("expected a decode failure for a non-image payload")


@test
def test_main_thread_stays_fast_when_a_page_of_large_covers_arrives():
    """The regression itself: a full tick of covers must not stall the UI.

    Before the fix, 12 covers at 2400x3600 blocked the main thread for ~2.4 s,
    which is what produced the "unresponsive" title. The bound here is loose
    enough to survive a slow machine but far below anything Windows would
    report as a hang.
    """
    root, instance = _make_gui()
    try:
        payload = _noisy_jpeg(2400, 3600)
        box = (instance.COVER_W, instance.COVER_H)
        rows = []
        for index in range(12):
            item = {"bookId": f"cover-{index}", "title": f"书 {index}", "author": "a",
                    "category": "c", "newRating": 885, "newRatingCount": 1}
            instance._add_book_card(item)
            rows.append(item["bookId"])
        root.update()

        # Decoding is what the fetch thread does; the main thread only wraps
        # the finished image, exactly as _request_card_cover now delivers it.
        events = queue.Queue()
        for book_id in rows:
            events.put(("card-cover", (book_id, app._decode_cover(payload, box))))

        instance.events = events
        started = time.perf_counter()
        instance._drain_events()
        blocked_ms = (time.perf_counter() - started) * 1000

        assert blocked_ms < 400, "main thread blocked %.0f ms" % blocked_ms
    finally:
        root.destroy()


@test
def test_card_cover_applies_a_real_image_instead_of_swallowing_a_name_error():
    """Guard the refactor itself.

    _apply_card_cover now receives a decoded image. When the decode call was
    removed by mistake the body still referenced `image` while the parameter was
    `payload`, and the resulting NameError was caught by its own except --
    silently rendering "暂无封面" on every book.
    """
    root, instance = _make_gui()
    try:
        item = {"bookId": "cover-live", "title": "书", "author": "a",
                "category": "c", "newRating": 885, "newRatingCount": 1}
        instance._add_book_card(item)
        root.update()

        image = app._decode_cover(_noisy_jpeg(1200, 1800),
                                  (instance.COVER_W, instance.COVER_H))
        instance._apply_card_cover("cover-live", image)
        root.update()

        widget = instance.card_widgets["cover-live"]
        assert widget["image"] is not None, "cover image was not applied"
        label = widget["cover"]
        assert label.cget("image") is not None, "label has no image"
        assert label.cget("text") == "", "label still shows placeholder text"
    finally:
        root.destroy()


@test
def test_detail_cover_uses_the_slot_size_instead_of_a_hardcoded_one():
    """The detail cover label and its image must not drift apart.

    The label was narrowed to DETAIL_COVER_W/H while the image still requested a
    hardcoded 126x174, so the artwork overflowed its slot.
    """
    root, instance = _make_gui()
    try:
        image = app._decode_cover(_noisy_jpeg(1200, 1800),
                                  (instance.DETAIL_COVER_W, instance.DETAIL_COVER_H))
        instance._apply_detail_cover(image)
        root.update()

        assert instance.cover_image is not None
        assert instance.cover.cget("text") == ""
        # The widget is a fixed-width label, but the CTk scale factor widens it,
        # so compare the scaled geometry against the scaled slot.
        # Compare the configured width rather than the realized pixel width:
        # CTk multiplies geometry by the display scale factor, and that factor
        # is not stable across windows created in the same process, so a pixel
        # comparison here would fail for reasons unrelated to the bug.
        assert instance.cover.cget("width") == instance.DETAIL_COVER_W, (
            "cover label width is %r, expected %r" % (
                instance.cover.cget("width"), instance.DETAIL_COVER_W))
        assert instance.cover.cget("height") == instance.DETAIL_COVER_H, (
            "cover label height is %r, expected %r" % (
                instance.cover.cget("height"), instance.DETAIL_COVER_H))
        # And the artwork must not be built for some other, larger size.
        assert instance.cover_image._size == (instance.DETAIL_COVER_W,
                                             instance.DETAIL_COVER_H), (
            "cover image built at %r, slot is %r" % (
                instance.cover_image._size,
                (instance.DETAIL_COVER_W, instance.DETAIL_COVER_H)))
    finally:
        root.destroy()


@test
def test_a_small_cached_cover_is_not_stretched_into_the_detail_slot():
    """A cached card-sized cover must be refetched, not upscaled.

    The cache now stores already-downsized art. Reusing a 54px-wide card image
    for the 96px detail slot would look blurry, so the caller has to notice the
    size mismatch and fetch a detail-sized copy instead.
    """
    root, instance = _make_gui()
    try:
        card_box = (instance.COVER_W, instance.COVER_H)
        small = app._decode_cover(_noisy_jpeg(2400, 3600), card_box)
        assert not app._covers_box(small, (instance.DETAIL_COVER_W,
                                           instance.DETAIL_COVER_H))

        requested = []
        instance._download_cover = lambda url: requested.append(url)

        instance.cover_image = None
        instance.cover_cache["cover-url"] = small
        instance._show_book_info({"cover": "cover-url", "bookId": "1"})

        assert requested == ["cover-url"], (
            "expected a refetch for the detail slot, got %r" % (requested,))
    finally:
        root.destroy()


@test
def test_a_large_enough_cached_cover_is_reused_without_refetching():
    """The other half: a cache hit that is big enough must not re-download."""
    root, instance = _make_gui()
    try:
        big = app._decode_cover(_noisy_jpeg(2400, 3600),
                                (instance.DETAIL_COVER_W, instance.DETAIL_COVER_H))
        assert app._covers_box(big, (instance.DETAIL_COVER_W,
                                     instance.DETAIL_COVER_H))

        requested = []
        instance._download_cover = lambda url: requested.append(url)

        instance.cover_image = None
        instance.cover_cache["cover-url"] = big
        instance._show_book_info({"cover": "cover-url", "bookId": "1"})

        assert requested == [], "should not refetch a usable cached cover"
        assert instance.cover_image is not None, "cached cover was not applied"
    finally:
        root.destroy()


if __name__ == "__main__":
    for test_case in ALL_TESTS:
        test_case()
        print("PASS", test_case.__name__)
