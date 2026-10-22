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
    """把 modal 弹窗替换成自动回答，避免测试卡在对话框。

    说明类消息现在走 _show_notice（自绘对话框），它也会 wait_window；
    只替换 messagebox 会让队列结束提示把测试永久挂住。
    """
    original = app.messagebox.askyesno, app.messagebox.showinfo
    app.messagebox.askyesno = lambda *a, **k: yes
    app.messagebox.showinfo = lambda *a, **k: True
    instance._show_notice = lambda title, message: True
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
        # 不能顺带触发 --book-info：真机上那会让 _start_worker 看到「忙」而静默失败。
        instance._start_queue()
        # 真实现里 export 的 spawn 在飞行中（_spawn_pending=True），此时
        # _flush_pending_book() 不会插一个读详情的 worker。假 start 不设这个标志，
        # 这里手动补上，否则详情 worker 会把 current_action 从 export 改掉。
        instance._spawn_pending = True
        assert instance.queue_running is True
        export_calls = [a for a, k in calls if k == "export"]
        assert export_calls == [["111", "--jobs", "1", "--unified-appendix", "--appendix-scope", "both"]], calls
        assert instance.selected_id == "111"

        # 第一本结束（成功）→ 自动接第二本
        instance.events.put(("done", (instance.job_seq, 0)))
        instance._drain_events()

        # 第二本也自动起 export worker
        export_calls = [a for a, k in calls if k == "export"]
        assert export_calls == [["111", "--jobs", "1", "--unified-appendix", "--appendix-scope", "both"],
                                ["222", "--jobs", "1", "--unified-appendix", "--appendix-scope", "both"]], calls
        # 队列空了，但最后一本还在跑：queue_running 要等它结束才置 False，
        # 否则「队列已全部完成」永远不会显示。
        assert instance.download_queue == []
        assert instance.queue_running is True

        # 最后一本也结束 → 队列收尾
        instance.events.put(("done", (instance.job_seq, 0)))
        instance._drain_events()
        assert instance.queue_running is False
    finally:
        app.messagebox.askyesno, app.messagebox.showinfo = original
        root.destroy()


def test_queue_start_spawns_only_the_export_worker():
    """队列起第一本时只能起 export，不能再插一个 --book-info。

    真机实测的故障：_start_queue 先 _select_book()（它会起一个读详情的 worker），
    紧接着 _start_one() 调 _start_worker() 时 _is_busy() 为真，于是直接返回 False——
    点「开始队列下载」后状态停在「书籍详情已加载」，下载中一直是 0。
    """
    root, instance = _make_gui()
    calls = []
    original = _mock_dialogs(instance)
    try:
        _spy_start(instance, calls)
        instance.result_rows = [_row("111", "书一")]
        instance._card_checks = {"111": __import__("tkinter").BooleanVar(value=True)}
        instance._enqueue_checked()
        instance._start_queue()
        assert [k for _a, k in calls] == ["export"], calls
    finally:
        root.destroy()
        app.messagebox.askyesno, app.messagebox.showinfo = original


def test_queue_continues_after_a_failed_book():
    """一本下载失败也要接着跑下一本，队列不能卡在失败上。

    真机实测：汉书 171 章下完、打包阶段失败，队列里的 三字经 一直没开始。
    原因是续跑代码写在 `elif action == "export"` 里，而失败走的是最前面的
    `if self.last_failure` 分支，永远到不了那段。
    """
    root, instance = _make_gui()
    calls = []
    original = _mock_dialogs(instance)
    try:
        _spy_start(instance, calls)
        instance.result_rows = [_row("111", "书一"), _row("222", "书二")]
        instance._card_checks = {
            "111": __import__("tkinter").BooleanVar(value=True),
            "222": __import__("tkinter").BooleanVar(value=True),
        }
        instance._enqueue_checked()
        instance._start_queue()

        # 第一本失败退出
        instance.last_failure = True
        instance.last_unresolved = False
        instance.events.put(("done", (instance.job_seq, 1)))
        instance._drain_events()

        export_calls = [a for a, k in calls if k == "export"]
        assert [c[0] for c in export_calls] == ["111", "222"], calls
        assert instance.selected_id == "222"
    finally:
        root.destroy()
        app.messagebox.askyesno, app.messagebox.showinfo = original


def test_queue_selected_book_loads_its_details_after_the_download():
    """队列选中的书，详情要在下载结束后补加载。

    真机实测：队列起书时为了避免和下载抢 worker，跳过了读详情；当时写的是
    pending_book=None，于是面板永远停在「下载开始后加载」，看起来一直没加载出来。
    """
    root, instance = _make_gui()
    calls = []
    original = _mock_dialogs(instance)
    try:
        _spy_start(instance, calls)
        instance.result_rows = [_row("111", "书一")]
        instance._card_checks = {"111": __import__("tkinter").BooleanVar(value=True)}
        instance._enqueue_checked()
        instance._start_queue()
        assert instance.pending_book is not None, "详情被丢掉了"
        assert instance.pending_book["bookId"] == "111"
        assert not any(k == "book-info" for _a, k in calls), calls

        instance.events.put(("done", (instance.job_seq, 0)))
        instance._drain_events()
        assert any(k == "book-info" for _a, k in calls), calls
    finally:
        root.destroy()
        app.messagebox.askyesno, app.messagebox.showinfo = original


class _FakeLabel:
    def __init__(self, text):
        self.text = text

    def cget(self, key):
        return self.text

    def configure(self, **kw):
        if "text" in kw:
            self.text = kw["text"]


def test_a_category_that_never_arrived_is_recorded_not_left_loading():
    """搜索结束后没拿到分类的卡片，选中时重建不能又回到「分类加载中…」。"""
    root, instance = _make_gui()
    try:
        label = _FakeLabel("分类加载中…")
        instance.book_rows = {"111": {"bookId": "111", "title": "书一"}}
        instance.card_widgets = {"111": {"category": label}}
        instance._finish_card_categories()
        assert label.text == "分类：暂无信息", label.text
        assert instance.book_rows["111"]["category"] == "暂无信息"
    finally:
        root.destroy()


def test_pausing_a_queue_stops_there_instead_of_skipping_ahead():
    """队列里暂停 = 停当前这本、队列原地不动（书插回队首）。"""
    root, instance = _make_gui()
    calls = []
    original = _mock_dialogs(instance)
    try:
        _spy_start(instance, calls)
        instance.result_rows = [_row("111", "书一"), _row("222", "书二")]
        instance._card_checks = {
            "111": __import__("tkinter").BooleanVar(value=True),
            "222": __import__("tkinter").BooleanVar(value=True),
        }
        instance._enqueue_checked()
        instance._start_queue()
        instance._spawn_pending = True
        instance.current_action = "export"
        instance.process = type("P", (), {"terminate": lambda self: None,
                                          "poll": lambda self: None})()

        instance.stop()                      # 用户点「暂停下载」
        assert instance._pause_requested is True
        instance.last_failure = False
        instance.events.put(("done", (instance.job_seq, 0)))
        instance._drain_events()

        assert instance.queue_paused is True
        assert [a[0] for a, k in calls if k == "export"] == ["111"], calls
        assert instance.download_queue[0]["bookId"] == "111", instance.download_queue
        assert instance.pause_button.cget("text") == "继续队列"

        instance._spawn_pending = False
        instance._resume_queue()
        assert [a[0] for a, k in calls if k == "export"] == ["111", "111"], calls
        assert instance.queue_paused is False
    finally:
        root.destroy()
        app.messagebox.askyesno, app.messagebox.showinfo = original


def test_unchecking_a_running_queue_book_skips_it():
    """运行中取消勾选 = 轮到它时跳过，不是照样下载。"""
    root, instance = _make_gui()
    calls = []
    original = _mock_dialogs(instance)
    try:
        _spy_start(instance, calls)
        instance.result_rows = [_row("111", "书一"), _row("222", "书二")]
        instance._card_checks = {
            "111": __import__("tkinter").BooleanVar(value=True),
            "222": __import__("tkinter").BooleanVar(value=True),
        }
        instance._enqueue_checked()
        instance._start_queue()
        instance._spawn_pending = True

        instance._card_checks["222"].set(False)
        instance._on_card_check("222")

        instance.last_failure = False
        instance.events.put(("done", (instance.job_seq, 0)))
        instance._drain_events()
        assert [a[0] for a, k in calls if k == "export"] == ["111"], calls
        assert instance.queue_running is False
    finally:
        root.destroy()
        app.messagebox.askyesno, app.messagebox.showinfo = original


def test_the_add_button_follows_the_checked_count():
    """结果卡片上的「加入队列 N 本」跟着勾选数走，0 本时不可点。

    顶栏原来有独立的「加入队列」「开始队列下载」两个按钮，加上登录/刷新/状态
    一共七个，125% 缩放下最后一个被压成半个字。队列入口收进结果卡片后，这个
    计数按钮就是唯一的入口，状态不能和勾选脱节。
    """
    import tkinter
    root, instance = _make_gui()
    try:
        instance.result_rows = [_row("111", "书一")]
        checked = tkinter.BooleanVar(value=False)
        instance._card_checks = {"111": checked}

        instance._refresh_queue_add()
        assert instance.queue_add_button.cget("state") == "disabled"
        assert "0 本" in instance.queue_add_button.cget("text")

        checked.set(True)
        instance._on_card_check("111")
        assert instance.queue_add_button.cget("state") == "normal"
        assert "1 本" in instance.queue_add_button.cget("text")

        checked.set(False)
        instance._on_card_check("111")
        assert instance.queue_add_button.cget("state") == "disabled"
    finally:
        root.destroy()


def test_a_queue_does_not_stall_on_the_omission_dialog():
    """队列里一本下完带「略去部分」说明时，不能弹模态把队列卡住。

    实机 1.0.5：三字经 下完弹出「下载成功（附说明）」，队列按钮停在
    「运行中」，下一本不再开始——_ask() 会阻塞 Tk 主循环，而续跑代码就写在
    这行弹窗之后。队列无人值守，说明进日志和状态栏即可。
    """
    import tkinter
    root, instance = _make_gui()
    calls = []
    asked = []
    original = _mock_dialogs(instance)
    real_ask = instance._ask
    instance._ask = lambda *a, **k: asked.append(a[1] if len(a) > 1 else "") or "yes"
    instance._show_notice = lambda title, message: asked.append(title)
    old_notice = app.describe_omissions
    app.describe_omissions = lambda om, compact=False: "1 条划线没有定位，已略去。"
    try:
        _spy_start(instance, calls)
        instance.result_rows = [_row("111", "书一"), _row("222", "书二")]
        instance._card_checks = {
            "111": tkinter.BooleanVar(value=True),
            "222": tkinter.BooleanVar(value=True),
        }
        instance._enqueue_checked()
        instance._start_queue()
        instance._spawn_pending = True

        instance.omissions = {"unplaced_underline_total": 1}
        instance.events.put(("done", (instance.job_seq, 0)))
        instance._drain_events()

        assert not any("附说明" in title for title in asked), (
            "队列运行时弹了模态：%r" % (asked,))
        assert [k for _a, k in calls].count("export") == 2, (
            "第一本下完后队列没有继续：%r" % (calls,))
    finally:
        app.describe_omissions = old_notice
        root.destroy()
        app.messagebox.askyesno, app.messagebox.showinfo = original


def test_a_failed_queue_book_is_reported_not_called_all_done():
    """队列里有书没完成时，结束语必须说出来，不能显示"全部完成"。"""
    root, instance = _make_gui()
    calls = []
    original = _mock_dialogs(instance)
    try:
        _spy_start(instance, calls)
        instance.result_rows = [_row("111", "书一")]
        instance._card_checks = {"111": __import__("tkinter").BooleanVar(value=True)}
        instance._enqueue_checked()
        instance._start_queue()
        instance._spawn_pending = True

        instance.last_failure = True
        instance.last_failure_status = "下载没有完成"
        instance.events.put(("done", (instance.job_seq, 1)))
        instance._drain_events()

        text = instance.status.get()
        assert "全部完成" not in text, text
        assert ("未完成" in text) or ("没完成" in text), text
    finally:
        root.destroy()
        app.messagebox.askyesno, app.messagebox.showinfo = original


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
        assert export_calls == [["111", "--jobs", "1", "--unified-appendix", "--appendix-scope", "both"]], calls

        # 第一本以"暂停/断点"状态结束（code!=0, 非未解决）
        instance.last_unresolved = False
        instance.events.put(("done", (instance.job_seq, 1)))
        instance._drain_events()

        # 第二本仍被自动起
        export_calls = [a for a, k in calls if k == "export"]
        assert export_calls == [["111", "--jobs", "1", "--unified-appendix", "--appendix-scope", "both"],
                                ["222", "--jobs", "1", "--unified-appendix", "--appendix-scope", "both"]], calls
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
