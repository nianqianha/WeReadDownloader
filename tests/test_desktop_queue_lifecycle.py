"""Queue recovery must survive worker and delayed-detail boundaries."""
import pytest

from test_desktop_queue import app, _make_gui, _mock_dialogs, _row, _spy_start


@pytest.mark.parametrize("failure", ["rejected", "spawn-error"])
def test_failed_start_keeps_queue_head_and_can_retry(failure):
    root, instance = _make_gui()
    original = _mock_dialogs(instance)
    try:
        calls = []
        _spy_start(instance, calls, accept=failure != "rejected")
        instance.download_queue = [_row("111", "first"), _row("222", "second")]
        instance._start_queue()
        if failure == "spawn-error":
            instance._handle_spawn_failed("export", "OSError")
        assert not instance.queue_running and instance.queue_paused
        assert [book["bookId"] for book in instance.download_queue] == ["111", "222"]
        assert instance.pause_button.cget("state") == "normal"
        assert not instance._is_busy()
        _spy_start(instance, calls)
        instance._resume_queue()
        assert calls[-1][0][0] == "111"
        assert instance.queue_running and not instance.queue_paused
    finally:
        app.messagebox.askyesno, app.messagebox.showinfo = original
        root.destroy()


def test_pending_spawn_pause_terminates_the_adopted_process():
    root, instance = _make_gui()
    class Process:
        stdout = None
        terminated = 0
        def terminate(self):
            self.terminated += 1
        def poll(self):
            return 1 if self.terminated else None
        def wait(self):
            return 1
    try:
        process = Process()
        instance.current_action = "export"
        instance._spawn_pending = True
        instance.stop()
        instance._handle_spawned("export", process)
        instance.reader.join(timeout=2)
        assert process.terminated == 1
        assert not instance.reader.is_alive()
        instance._drain_events()
        assert not instance._pause_requested
        assert not instance._is_busy()
    finally:
        root.destroy()


def test_detail_completion_preserves_both_queue_resume_entries():
    root, instance = _make_gui()
    original = _mock_dialogs(instance)
    try:
        calls = []
        _spy_start(instance, calls)
        instance.download_queue = [_row("111", "first"), _row("222", "second")]
        instance._start_queue()
        instance.pending_book = None
        instance._pause_requested = True
        instance._handle_done(instance.job_seq, 1)
        assert instance.queue_paused and not instance.queue_running
        instance.current_action = "book-info"
        instance.job_seq += 1
        instance._begin_job("book-info")
        instance._handle_done(instance.job_seq, 0)
        assert instance.pause_button.cget("text") == "继续队列"
        assert instance.pause_button.cget("state") == "normal"
        assert instance.queue_paused and not instance.queue_running
        instance._show_queue()
        pending = list(root.winfo_children())
        resume = []
        while pending:
            widget = pending.pop()
            if isinstance(widget, app.ctk.CTkButton) and widget.cget("text") == "继续队列":
                resume.append(widget)
            pending.extend(widget.winfo_children())
        assert len(resume) == 2
        assert all(button.cget("state") == "normal" for button in resume)
        # Invoke the queue-dialog entry, not the main-window button.
        next(button for button in resume if button is not instance.pause_button).invoke()
        assert instance.queue_running and not instance.queue_paused
        assert calls[-1][0][0] == "111"
    finally:
        app.messagebox.askyesno, app.messagebox.showinfo = original
        root.destroy()


def test_pause_keeps_the_running_book_after_browsing_another_one():
    """暂停时插入队列的必须是正在下载的那本，不是用户正在浏览的那本。"""
    root, instance = _make_gui()
    original = _mock_dialogs(instance)
    try:
        calls = []
        _spy_start(instance, calls)
        # 详情补载会起自己的 job，与本测试要验证的队首归属无关。
        instance._flush_pending_book = lambda: None
        instance.download_queue = [_row("111", "first"), _row("222", "second")]
        instance._start_queue()
        assert calls[-1][0][0] == "111"
        # 下载中点了另一本书：selected_* 变成浏览的那本。
        instance._select_book(_row("999", "browsed"), load_info=False)
        assert instance.selected_id == "999"
        instance._pause_requested = True
        instance._handle_done(instance.job_seq, 1)
        assert [book["bookId"] for book in instance.download_queue] == ["111", "222"]
        assert instance.queue_results == []
        instance._resume_queue()
        assert calls[-1][0][0] == "111"
        instance._handle_done(instance.job_seq, 0)   # 111 完成，222 开始
        assert instance.queue_results[0][0] == "first"
        instance._handle_done(instance.job_seq, 0)   # 222 完成，队列结束
        assert [row[0] for row in instance.queue_results] == ["first", "second"]
        assert not instance.queue_running
    finally:
        app.messagebox.askyesno, app.messagebox.showinfo = original
        root.destroy()


def test_pause_message_survives_the_pending_detail_reload():
    """_handle_done 的 finally 会补载详情；暂停提示不能被“正在加载”覆盖。"""
    root, instance = _make_gui()
    original = _mock_dialogs(instance)
    try:
        calls = []
        _spy_start(instance, calls)
        instance.download_queue = [_row("111", "first"), _row("222", "second")]
        instance._start_queue()
        # 下载中浏览了另一本：pending_book 让 finally 去补载详情，状态会被改写。
        instance._select_book(_row("999", "browsed"), load_info=False)
        assert instance.pending_book is not None
        instance._pause_requested = True
        instance._handle_done(instance.job_seq, 1)
        status = instance.status.get()
        assert "队列已暂停" in status and "first" in status, status
        assert "正在加载" not in status
    finally:
        app.messagebox.askyesno, app.messagebox.showinfo = original
        root.destroy()
