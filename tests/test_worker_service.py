"""常驻 worker（--serve）：GUI 复用同一个进程，避免每次点击都付 1 秒启动。

协议是一行一个 JSON 对象：
    请求  {"id": 7, "cmd": "search", "args": {"keyword": "..."}}
    响应  {"id": 7, "event": "begin"}
          {"id": 7, "line": "<引擎输出的一行>"}
          {"id": 7, "event": "end", "code": 0}

这里不启动真实引擎（需要 lua 插件），只验证协议的两端：引擎侧的
_FrameWriter/serve_forever 分帧，以及 GUI 侧的 service_request_for 与
_ServiceClient 的读写与失败回落。
"""
import importlib.util
import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "weread"))


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


engine = _load("weread_desktop_service_under_test", ROOT / "weread" / "weread_desktop.py")
gui = _load("desktop_app_service_under_test", ROOT / "weread" / "desktop_app.py")


def test_frame_writer_tags_every_line_with_the_request_id():
    records = []
    writer = engine._FrameWriter(records.append, 7)
    writer.write("第一行\n第二")
    writer.write("行\n")
    writer.flush()
    assert records == [
        {"id": 7, "line": "第一行"},
        {"id": 7, "line": "第二行"},
    ], records


def test_frame_writer_flushes_a_trailing_partial_line():
    records = []
    writer = engine._FrameWriter(records.append, 3)
    writer.write("没有换行的最后一行")
    writer.flush()
    assert records == [{"id": 3, "line": "没有换行的最后一行"}], records


def test_service_argv_maps_each_command():
    assert engine.service_argv("auth", {}) == ["--check-auth"]
    assert engine.service_argv("shelf", {}) == ["--shelf"]
    assert engine.service_argv("search", {"keyword": "三字经"}) == ["--search", "三字经"]
    assert engine.service_argv("book-info", {"bookId": "1"}) == ["--book-info", "1"]
    assert engine.service_argv("probe", {"bookId": "1"}) == ["--probe-book", "1"]
    assert engine.service_argv("probe", {"bookId": "1", "chapter": "9"}) == \
        ["--probe-book", "1", "--probe-chapter", "9"]
    assert engine.service_argv("nope", {}) is None


def test_serve_forever_frames_output_and_keeps_going_after_a_failure(monkeypatch):
    """Two requests in one process: the second must still be answered."""
    calls = []

    def fake_main():
        calls.append(list(sys.argv[1:]))
        print("line one")
        print("line two")
        if "--book-info" in sys.argv:
            print("FAILED Boom synthetic", file=sys.stderr)
            return 1
        return 0

    monkeypatch.setattr(engine, "lua_runtime", lambda: object())
    monkeypatch.setattr(engine, "main", fake_main)

    requests = "\n".join([
        json.dumps({"id": 1, "cmd": "search", "args": {"keyword": "书"}}),
        json.dumps({"id": 2, "cmd": "book-info", "args": {"bookId": "42"}}),
        json.dumps({"cmd": "quit"}),
    ]) + "\n"
    old_stdin, old_stdout = sys.stdin, sys.stdout
    out = io.StringIO()
    sys.stdin = io.StringIO(requests)
    sys.stdout = out
    try:
        code = engine.serve_forever()
    finally:
        sys.stdin, sys.stdout = old_stdin, old_stdout

    assert code == 0
    records = [json.loads(line) for line in out.getvalue().splitlines() if line.strip()]
    assert records[0] == {"event": "ready"}
    first = [r for r in records if r.get("id") == 1]
    second = [r for r in records if r.get("id") == 2]
    assert {"id": 1, "event": "begin"} in first
    assert {"id": 1, "line": "line one"} in first
    assert {"id": 1, "event": "end", "code": 0} in first
    assert {"id": 2, "line": "FAILED Boom synthetic"} in second
    assert {"id": 2, "event": "end", "code": 1} in second
    # Each request ran main() with its own argv, in order.
    assert calls == [["--search", "书"], ["--book-info", "42"]], calls


def test_gui_maps_argv_to_service_requests():
    assert gui.service_request_for("auth", ["--check-auth"]) == ("auth", {})
    assert gui.service_request_for("shelf", ["--shelf"]) == ("shelf", {})
    assert gui.service_request_for("search", ["--search", "汉书"]) == \
        ("search", {"keyword": "汉书"})
    assert gui.service_request_for("book-info", ["--book-info", "1"]) == \
        ("book-info", {"bookId": "1"})
    assert gui.service_request_for("probe", ["--probe-book", "1", "--probe-chapter", "2"]) == \
        ("probe", {"bookId": "1", "chapter": "2"})
    # Exports and login stay on their own processes.
    assert gui.service_request_for("export", ["123", "--jobs", "1"]) is None


class _FakeProcess:
    def __init__(self, lines):
        self.stdout = io.StringIO("".join(json.dumps(line) + "\n" for line in lines))
        self.stdin = io.StringIO()
        self.killed = False

    def poll(self):
        return None

    def kill(self):
        self.killed = True


def test_service_client_streams_lines_and_returns_the_exit_code():
    client = gui._ServiceClient()
    client._process = _FakeProcess([
        {"id": 5, "event": "begin"},
        {"id": 5, "line": "A"},
        {"id": 9, "line": "stale request, ignored"},
        {"id": 5, "line": "B"},
        {"id": 5, "event": "end", "code": 0},
    ])
    seen = []
    code = client.request(5, "search", {"keyword": "书"}, seen.append)
    assert code == 0
    assert seen == ["A", "B"], seen
    sent = json.loads(client._process.stdin.getvalue())
    assert sent == {"id": 5, "cmd": "search", "args": {"keyword": "书"}}, sent


def test_service_client_reports_a_dead_process_as_failure():
    class _Dead(_FakeProcess):
        def __init__(self):
            super().__init__([])

    client = gui._ServiceClient()
    client._process = _Dead()
    seen = []
    code = client.request(1, "auth", {}, seen.append)
    assert code == -1
    assert seen and seen[0].startswith("FAILED "), seen
    assert client._process is None, "a dead service must be discarded so the next call restarts it"


def test_a_warm_request_counts_as_busy_until_it_finishes():
    """常驻请求没有 process 可判活，_is_busy 必须认 job 号，否则能叠第二个任务。"""
    import customtkinter as ctk
    root = ctk.CTk()
    root.withdraw()
    try:
        instance = gui.DesktopApp(root)
        instance._service_job = 7
        assert instance._is_busy() is True
        instance.job_seq = 7
        instance.current_action = "search"
        instance.events.put(("done", (7, 0)))
        instance._drain_events()
        assert instance._service_job is None
        assert instance._is_busy() is False
    finally:
        root.destroy()


def test_a_search_goes_through_the_warm_service_instead_of_spawning():
    import customtkinter as ctk
    root = ctk.CTk()
    root.withdraw()
    try:
        instance = gui.DesktopApp(root)
        route = []
        instance._start_service_job = lambda *a, **k: route.append(("service", a[2])) or True
        instance._spawn_worker = lambda *a, **k: route.append(("spawn", a[1])) or True
        assert instance._start_worker(["--search", "书"], "search", "正在搜索…") is True
        assert instance._start_worker(["123", "--jobs", "1"], "export", "下载中…") is True
        assert route == [("service", "search"), ("spawn", "export")], route
    finally:
        root.destroy()
