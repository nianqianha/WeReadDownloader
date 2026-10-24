"""技术日志的后台写盘（_LogWriter）必须保序、落盘、且不阻塞调用方。

背景：_persist_log 原来在 Tk 线程里逐行 open/write，实测被磁盘/杀软卡住
18 秒（desktop-general.log 的 [STALL_STACK]）。现在调用方只入队，由守护线程
批量落盘；flush() 提供确定性同步点。
"""
import importlib.util
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "weread"))
SCRIPT = (Path(__file__).resolve().parent.parent / "weread" / "desktop_app.py")
spec = importlib.util.spec_from_file_location("desktop_app", SCRIPT)
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)

_TMP = tempfile.TemporaryDirectory(prefix="weread-logwriter-")
app.LOG_DIR = Path(_TMP.name) / "logs"


def _make_instance():
    import customtkinter as ctk
    root = ctk.CTk()
    instance = app.DesktopApp(root)
    return root, instance


def test_persist_log_lands_in_the_right_file_after_flush():
    root, instance = _make_instance()
    try:
        instance.selected_id = ""
        instance._persist_log("general line")
        instance.selected_id = "22297629"
        instance._persist_log("book line")
        app._LOG_WRITER.flush()
        general = (app.LOG_DIR / "desktop-general.log").read_text(encoding="utf-8")
        book = (app.LOG_DIR / "desktop-22297629.log").read_text(encoding="utf-8")
        assert "general line" in general
        assert "book line" in book
        assert "book line" not in general and "general line" not in book
    finally:
        root.destroy()


def test_persist_log_redacts_before_writing():
    root, instance = _make_instance()
    try:
        instance.selected_id = ""
        instance._persist_log("wr_skey=supersecret123 value")
        app._LOG_WRITER.flush()
        text = (app.LOG_DIR / "desktop-general.log").read_text(encoding="utf-8")
        assert "supersecret123" not in text
    finally:
        root.destroy()


def test_persist_log_returns_fast_under_slow_disk():
    """写盘慢不能拖住 Tk 线程：这是本次改造的核心验收。"""
    root, instance = _make_instance()
    try:
        instance.selected_id = ""
        slow = app._LogWriter._write_file   # 钳住真实写盘函数制造阻塞
        calls = []

        def clogged(book_id, lines):
            calls.append(1)
            time.sleep(1.0)
            slow(book_id, lines)

        # 必须在写入前钳住：后台线程排空极快，事后补丁追不上。
        app._LogWriter._write_file = staticmethod(clogged)
        try:
            start = time.perf_counter()
            for i in range(5):
                instance._persist_log("line %d" % i)
            elapsed = time.perf_counter() - start
            # 5 行若同步写盘要 5 秒；入队必须毫秒级返回。
            assert elapsed < 0.5, elapsed
        finally:
            app._LOG_WRITER.flush()
            app._LogWriter._write_file = staticmethod(slow)
        text = (app.LOG_DIR / "desktop-general.log").read_text(encoding="utf-8")
        assert all(("line %d" % i) in text for i in range(5))
        assert len(calls) >= 1
    finally:
        root.destroy()


def test_flush_is_safe_from_any_thread_and_repeatable():
    app._LOG_WRITER.flush()
    app._LOG_WRITER.flush()
    done = threading.Event()

    def flush_later():
        app._LOG_WRITER.flush()
        done.set()

    threading.Thread(target=flush_later).start()
    assert done.wait(5)
