"""同一张插图只落盘一次。

汉书实测：EPUB 里 1569 个图片条目、686 张唯一图片、重复 94 MB。原因是同名
图片有两条来源（章节图片包 tar 一份、正文远程 URL 再一份），以前按"章节号-
文件名"各存一份。这里锁住按内容哈希复用的行为。
"""
import importlib.util
import io
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "weread"))
_spec = importlib.util.spec_from_file_location(
    "weread_desktop_dedupe_under_test", ROOT / "weread" / "weread_desktop.py")
engine = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(engine)

JPEG = b"\xff\xd8\xff" + b"\x00" * 64


def _workspace():
    tmp = tempfile.TemporaryDirectory(prefix="weread-dedupe-")
    root = Path(tmp.name) / "workspace"
    (root / "images").mkdir(parents=True)
    return tmp, root


def test_the_same_bytes_saved_under_two_names_produce_one_file():
    tmp, root = _workspace()
    try:
        engine._IMAGE_DIGESTS.clear()
        used = set()
        stream = io.BytesIO(JPEG[12:])          # the caller already read the head
        first = engine._save_image_stream(stream, JPEG[:12], root, used, "CB_book_1.jpg", "10")
        stream = io.BytesIO(JPEG[12:])
        second = engine._save_image_stream(stream, JPEG[:12], root, used, "CB_book_1.jpg", "20")
        assert first and second
        assert first == second, (first, second)
        assert len(list((root / "images").iterdir())) == 1, list((root / "images").iterdir())
    finally:
        tmp.cleanup()


def test_a_different_picture_still_gets_its_own_file():
    tmp, root = _workspace()
    try:
        engine._IMAGE_DIGESTS.clear()
        used = set()
        other = b"\xff\xd8\xff" + b"\x01" * 64
        one = engine._save_image_stream(io.BytesIO(JPEG[12:]), JPEG[:12], root, used, "a.jpg", "10")
        two = engine._save_image_stream(io.BytesIO(other[12:]), other[:12], root, used, "b.jpg", "10")
        assert one != two
        assert len(list((root / "images").iterdir())) == 2
    finally:
        tmp.cleanup()


def test_the_cover_helper_reuses_a_picture_already_written():
    tmp, root = _workspace()
    try:
        engine._IMAGE_DIGESTS.clear()
        first = engine._save_image(JPEG, root, set(), "cover-source.jpg")
        second = engine._save_image(JPEG, root, set(), "another-name.jpg")
        assert first == second, (first, second)
    finally:
        tmp.cleanup()


def test_packaging_writes_one_copy_and_rewrites_the_reference():
    """重新打包旧书时也要去重：正文引用改到留下的那一份。"""
    import zipfile
    tmp, root = _workspace()
    try:
        images = root / "images"
        (images / "10-CB_a.jpg").write_bytes(JPEG)
        (images / "20-remote_a.jpg").write_bytes(JPEG)   # same bytes, other name
        first = root / "c1.body"
        second = root / "c2.body"
        first.write_bytes(b'<p><img src="../images/10-CB_a.jpg" alt="x"/></p>')
        second.write_bytes(b'<p><img src="../images/20-remote_a.jpg" alt="x"/></p>')
        epub = root / "out.epub"
        engine.write_epub(
            epub,
            {"bookId": "1", "title": "T", "author": "A"},
            [{"chapterUid": "1", "title": "一"}, {"chapterUid": "2", "title": "二"}],
            [first, second], "body{}", images, None)
        with zipfile.ZipFile(epub) as archive:
            names = archive.namelist()
            images_written = [n for n in names if n.startswith("OEBPS/images/")]
            assert len(images_written) == 1, images_written
            kept = images_written[0].split("/")[-1]
            one = archive.read("OEBPS/text/chapter-0001.xhtml")
            two = archive.read("OEBPS/text/chapter-0002.xhtml")
        assert ("../images/" + kept).encode() in one
        assert ("../images/" + kept).encode() in two, "第二处引用没有改到留下的文件"
    finally:
        tmp.cleanup()
