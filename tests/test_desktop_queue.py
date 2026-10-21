"""排队下载（方案A）：勾选 -> 加入队列 -> 开始队列下载 -> 自动续下一本。

这些测试不启动真实 worker，而是用 _spy_start 把 _start_worker 替换成离线记录器，
并通过 event 队列驱动 _handle_done，验证队列的入队顺序与"上一本结束后自动接下一本"。
"""
import importlib.util
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "weread"))
SCRIPT = (Path(__file__).resolve().parent.parent / "weread" / "desktop_app.py")
spec = importlib.util.spec_from_file_location("desktop_app", SCRIPT)
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)

_TMP_PROFILE = tempfile.TemporaryDirectory(prefix="weread-queue-test-")
app.LOG_DIR = Path(_TMP_PROFILE.name) / "logs"
app.OUTPUT = Path(_TMP_PROFILE.name) / "exports"
import weread_core as _weread_core
_weread_core.PRIVATE = Path(_TMP_PROFILE.name) / "WeReadDesktop"
app._settings_path = lambda: (Path(_TMP_PROFILE.name) / "WeReadDesktop"
                              / "weread-live-settings.lua")


def _make_gui():
    import customtkinter as ctk
    root = ctk.CTk()
    root.withdraw()
    return root, app.DesktopApp(root)


def _spy_start(instance, calls, accept=True):
    def fake_start(args, action, status):
        calls.append((list(args), action))
        instance.job_seq += 1
        instance.current_action = action
        instance.last_failure = False
        instance.last_unresolved = False
        instance.status.set(status)
        return accept
    instance._start_worker = fake_start


def _row(book_id, title):
    return {"bookId": book_id, "title": title, "author": "作者" + book_id,
            "cover": "", "newRating": "800", "newRatingCount": "100",
            "category": "分类"}


class _BusyProcess:
    def poll(self):
        return None


def _mock_dialogs(instance, yes=True):
    """把 _ask 的 modal 弹窗替换成自动回答，避免测试卡在对话框。"""
    original = app.messagebox.askyesno, app.messagebox.showinfo
    app.messagebox.askyesno = lambda *a, **k: yes
    app.messagebox.showinfo = lambda *a, **k: True
    return original


def test_enqueue_preserves_check_order():
    """勾选顺序进入 download_queue，且保持顺序。"""
    root, instance = _make_gui()
    try:
        r1 = _row("111", "书一")
        r2 = _row("222", "书二")
        r3 = _row("333", "书三")
        instance.result_rows = [r1, r2, r3]
        instance._card_checks = {
            "111": __import__("tkinter").BooleanVar(value=True),
            "222": __import__("tkinter").BooleanVar(value=False),
            "333": __import__("tkinter").BooleanVar(value=True),
        }
        instance._enqueue_checked()
        assert [b["bookId"] for b in instance.download_queue] == ["111", "333"], \
            instance.download_queue
        # 已加入的不会重复入队
        instance._enqueue_checked()
        assert [b["bookId"] for b in instance.download_queue] == ["111", "333"]
    finally:
        root.destroy()


def test_queue_auto_continues_after_first_book_done():
    """开始队列 -> 起第一本 -> 第一本 done -> 自动起第二本。"""
    root, instance = _make_gui()
    calls = []
    original = _mock_dialogs(instance)
    try:
        _spy_start(instance, calls)
        r1 = _row("111", "书一")
        r2 = _row("222", "书二")
        instance.result_rows = [r1, r2]
        instance._card_checks = {
            "111": __import__("tkinter").BooleanVar(value=True),
            "222": __import__("tkinter").BooleanVar(value=True),
        }
        instance._enqueue_checked()
        assert len(instance.download_queue) == 2

        # 启动队列：队首 111 应被 pop 并起 export worker。
        # （注：_select_book 还会顺带触发一次 --book-info，属既有行为，不计入断言。）
        instance._start_queue()
        assert instance.queue_running is True
        export_calls = [a for a, k in calls if k == "export"]
        assert export_calls == [["111", "--jobs", "1", "--unified-appendix"]], calls
        assert instance.selected_id == "111"

        # 第一本结束（成功）→ 自动接第二本
        instance.events.put(("done", (instance.job_seq, 0)))
        instance._drain_events()

        # 第二本也自动起 export worker
        export_calls = [a for a, k in calls if k == "export"]
        assert export_calls == [["111", "--jobs", "1", "--unified-appendix"],
                                ["222", "--jobs", "1", "--unified-appendix"]], calls
        # 队列已空且标记结束
        assert instance.download_queue == []
        assert instance.queue_running is False
    finally:
        app.messagebox.askyesno, app.messagebox.showinfo = original
        root.destroy()


def test_queue_continues_through_partial_stop():
    """第一本停在断点（code!=0、无未解决章节）→ 队列仍继续下一本，不被卡死。"""
    root, instance = _make_gui()
    calls = []
    original = _mock_dialogs(instance)
    try:
        _spy_start(instance, calls)
        r1 = _row("111", "书一")
        r2 = _row("222", "书二")
        instance.result_rows = [r1, r2]
        instance._card_checks = {
            "111": __import__("tkinter").BooleanVar(value=True),
            "222": __import__("tkinter").BooleanVar(value=True),
        }
        instance._enqueue_checked()
        instance._start_queue()
        export_calls = [a for a, k in calls if k == "export"]
        assert export_calls == [["111", "--jobs", "1", "--unified-appendix"]], calls

        # 第一本以"暂停/断点"状态结束（code!=0, 非未解决）
        instance.last_unresolved = False
        instance.events.put(("done", (instance.job_seq, 1)))
        instance._drain_events()

        # 第二本仍被自动起
        export_calls = [a for a, k in calls if k == "export"]
        assert export_calls == [["111", "--jobs", "1", "--unified-appendix"],
                                ["222", "--jobs", "1", "--unified-appendix"]], calls
    finally:
        app.messagebox.askyesno, app.messagebox.showinfo = original
        root.destroy()


def test_empty_queue_start_shows_info_not_crash():
    """空队列点「开始队列下载」→ 弹 info，不抛错、不启动 worker。"""
    import tkinter
    root, instance = _make_gui()
    calls = []
    original = _mock_dialogs(instance)
    try:
        _spy_start(instance, calls)
        instance._start_queue()
        assert calls == [], calls
        assert instance.queue_running is False
    finally:
        app.messagebox.askyesno, app.messagebox.showinfo = original
        root.destroy()
