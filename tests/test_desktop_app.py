import importlib.util
import io
import json
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

# Tests must never write into the real user profile: _persist_log/_persist paths
# resolve LOG_DIR/OUTPUT at call time, so redirect them for the whole module.
_TMP_PROFILE = tempfile.TemporaryDirectory(prefix="weread-gui-test-")
app.LOG_DIR = Path(_TMP_PROFILE.name) / "logs"
app.OUTPUT = Path(_TMP_PROFILE.name) / "exports"
# The login dialog persists credentials; keep that out of the real user profile too.
app.weread_login.CREDENTIAL_STATE = Path(_TMP_PROFILE.name) / "session-cookies.json"
# _save_manual_credentials also writes the credential BASE file through
# first_run.write_settings(_settings_path(), ...). Without redirecting that path
# the test's fake values (wrk-manual-key) overwrote the REAL credential file on
# this machine -- the running program then failed every action while the QR
# dialog still said logged-in. first_run resolves its path through
# weread_core.PRIVATE, so patch the module in sys.modules: every importer (the
# app, first_run, later imports) then sees the redirected profile, which is
# stronger than patching one attribute and hoping every helper routes through it.
import weread_core as _weread_core
_weread_core.PRIVATE = Path(_TMP_PROFILE.name) / "WeReadDesktop"
app._settings_path = lambda: (Path(_TMP_PROFILE.name) / "WeReadDesktop"
                              / "weread-live-settings.lua")


def _log_text(instance=None) -> str:
    """Everything the app has written to its log files.

    There is no log panel in the window any more -- it duplicated the
    "查看技术日志" button -- so the log file is the single sink and the thing
    to assert against.
    """
    parts = []
    for path in sorted(app.LOG_DIR.glob("*.log")):
        try:
            parts.append(path.read_text(encoding="utf-8"))
        except OSError:
            pass
    return "\n".join(parts)


def test_wechat_rating_scale_formats_as_ten_point_score():
    assert app.rating_text(888, 23717) == "评分：8.88 / 10  ·  23717 人评分"
    assert app.rating_text(8.7, 42) == "评分：8.7 / 10  ·  42 人评分"
    assert app.rating_text(None, None) == ""


def test_details_sync_identity_for_shelf_and_search_confirmation():
    import customtkinter as ctk
    root = ctk.CTk()
    instance = app.DesktopApp(root)
    try:
        instance._is_busy = lambda: False
        instance._start_book_info = lambda _item: True
        messages = []
        instance._ask = lambda _kind, _title, message, **_kw: messages.append(message) or False
        for item in ({"bookId": "1", "title": "Shelf title"},
                     {"bookId": "2", "title": "Search title", "author": "Search author"}):
            instance._select_book(item)
            instance._show_book_info({"bookId": item["bookId"],
                                      "title": "Detail title", "author": "Detail author"})
            instance.export()
            assert instance.selected_id == item["bookId"]
            assert instance.selected_title == "Detail title"
            assert instance.selected_author == "Detail author"
            assert instance.author_label.cget("text") == "作者：Detail author"
            assert "Detail title" in messages[-1] and "Detail author" in messages[-1]
        instance._show_book_info({"bookId": "1", "title": "Stale", "author": "Stale"})
        assert instance.selected_id == "2"
        assert instance.selected_title == "Detail title"
        assert instance.selected_author == "Detail author"
        instance._show_book_info({"bookId": "2", "author": "  "})
        assert instance.selected_author == "Detail author"
    finally:
        root.destroy()


def test_common_errors_are_translated_to_actionable_chinese():
    assert "服务器实际返回空正文" in app.friendly_error("empty body despite catalog wordCount=41")
    assert "划线无法可靠对应" in app.friendly_error("only 0 embedded marker(s)")
    assert "连接微信读书服务失败" in app.friendly_error("HTTPSConnectionError")
    assert "技术日志" in app.friendly_error("unexpected crash")
    assert "断点" in app.friendly_error("RuntimeError: cannot acquire desktop export lock")
    assert "重试" in app.friendly_error("RuntimeError: WeRead gateway error for /store/search: -1")
    # #66: 失败文案不能落进兜底，用户要知道旧成品没被覆盖。
    preserved = app.friendly_error(
        "RuntimeError: Annotation requests failed; preserved the previous complete export. "
        "Retry to fetch failed chapters.")
    assert "完整成品" in preserved and "重取" in preserved


def test_book_details_are_safe_to_display_when_optional_fields_are_missing():
    assert app.book_detail_lines({"category": "男生小说-游戏竞技"}) == ["分类：男生小说-游戏竞技"]
    assert app.book_detail_lines({}) == ["分类/出版社：暂无信息"]


def test_worker_search_rows_parse_into_result_cards():
    # Regression: rows used to be joined with a literal backslash-t, so the GUI
    # saw a single unsplit field and never rendered a single result card.
    row = "\t".join(("19480992", "超神机械师", "齐佩甲",
                     "https://example.test/s_19480992.jpg", "888", "23718",
                     "男生小说-游戏竞技"))
    parsed = app.parse_search_row(row)
    assert parsed == {
        "bookId": "19480992", "title": "超神机械师", "author": "齐佩甲",
        "cover": "https://example.test/s_19480992.jpg", "newRating": "888",
        "newRatingCount": "23718", "category": "男生小说-游戏竞技",
    }
    assert app.parse_search_row("19480992\\t超神机械师\\t齐佩甲") is None
    assert app.parse_search_row("[3/10] Fetching chapter UID 7") is None
    assert app.parse_search_row("SEARCH_META\t19480992\t男生小说") is None
    # Missing optional tail fields must not crash the parser.
    assert app.parse_search_row("41794030\t道诡异仙\t狐尾的笔\thttps://c/x.jpg\t871\t4074")["category"] == ""


def test_book_info_message_parses_and_rejects_malformed_payloads():
    payload = {"bookId": "19480992", "title": "超神机械师", "author": "齐佩甲",
               "cover": "", "intro": "简介", "category": "男生小说-游戏竞技",
               "newRating": 888, "newRatingCount": 23718}
    line = "BOOK_INFO\t" + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    assert app.parse_book_info_message(line) == payload
    assert app.parse_book_info_message("BOOK_INFO\t") is None
    assert app.parse_book_info_message("BOOK_INFO\t{不是 json}") is None
    assert app.parse_book_info_message("BOOK_INFO\t[1,2]") is None
    assert app.parse_book_info_message("SEARCH_META\t1\t分类") is None
    assert app.parse_book_info_message("19480992\t超神机械师") is None
    # Missing optional detail fields still render natural placeholders.
    assert app.book_detail_lines(app.parse_book_info_message(line))[-1] in (
        "分类：男生小说-游戏竞技", "分类/出版社：暂无信息")


def test_search_meta_line_updates_category():
    assert app.parse_search_meta("SEARCH_META\t19480992\t男生小说-游戏竞技") == \
        ("19480992", "男生小说-游戏竞技")
    assert app.parse_search_meta("SEARCH_META\t") is None
    assert app.parse_search_meta("19480992\t超神机械师") is None


def test_worker_failure_lines_are_translated_without_raising():
    for line in ("FAILED RuntimeError: WeRead gateway error for /store/search: -1",
                 "FAILED SSLError HTTPSConnectionPool(host='i.weread.qq.com')",
                 "FAILED ConnectionError: HTTPSConnectionPool failed",
                 "FAILED Timeout: read timed out",
                 "FAILED RuntimeError: an export for this book is already running"):
        message = app.friendly_error(line)
        assert message != app.FALLBACK_ZH, line
        assert not any(token in message for token in ("Traceback", "RuntimeError", "SSLError")), message


def test_log_output_never_contains_credentials():
    raw = ("Authorization: Bearer abc123DEFG456 apiKey=TOPSECRETKEY "
           "wr_skey=SECRETSKEY; wr_ticket=SECRETTICKET cookie: wr_skey=SECRETSKEY")
    scrubbed = app.redact_secrets(raw)
    for secret in ("abc123DEFG456", "TOPSECRETKEY", "SECRETSKEY", "SECRETTICKET"):
        assert secret not in scrubbed, scrubbed
    assert "[已隐藏]" in scrubbed


def test_gui_search_click_detail_flow_survives_worker_failure():
    """End-to-end offline repro: search -> click -> details -> worker failure."""
    import customtkinter as ctk

    root = ctk.CTk()
    root.withdraw()
    fake_bytes = io.BytesIO()
    try:
        import PIL.Image
        PIL.Image.new("RGB", (60, 90), (200, 60, 60)).save(fake_bytes, format="JPEG")
    except Exception:
        fake_bytes = None
    jpeg = fake_bytes.getvalue() if fake_bytes else b""

    class _Response:
        def __init__(self, content):
            self.content = content

        def raise_for_status(self):
            return None

    original_get = app.requests.get
    app.requests.get = lambda *args, **kwargs: _Response(jpeg)
    instance = None
    try:
        instance = app.DesktopApp(root)

        def fake_start(args, action, status):
            # Offline stand-in for the real worker: same bookkeeping, no process.
            instance.job_seq += 1
            instance.current_action = action
            instance.last_failure = False
            instance.last_unresolved = False
            instance.status.set(status)
            return True

        instance._start_worker = fake_start

        # 1. search rows arrive exactly as weread_desktop prints them
        instance.search_entry.delete(0, "end")
        instance.search_entry.insert(0, "超神机械师")
        instance.search()
        search_job = instance.job_seq
        for row in (
            "\t".join(("19480992", "超神机械师", "齐佩甲",
                       "https://example.test/s_19480992.jpg", "888", "23718", "")),
            "\t".join(("3004323461", "超神机械师之异神高德", "爱吃包子狗不理",
                       "https://example.test/3004323461.jpg", "", "", "")),
        ):
            instance.events.put(("line", row))
        instance.events.put(("line", "SEARCH_META\t19480992\t男生小说-游戏竞技"))
        instance.events.put(("line", "SEARCH_META\t3004323461\t"))
        instance.events.put(("done", (search_job, 0)))
        instance._drain_events()

        assert len(instance.book_rows) == 2, instance.book_rows
        assert len(instance.card_widgets) == 2, instance.card_widgets
        assert instance.book_rows["19480992"]["title"] == "超神机械师"
        assert instance.book_rows["19480992"]["author"] == "齐佩甲"
        assert "找到 2 本书" in instance.result_count.cget("text")
        card = instance.card_widgets["19480992"]
        assert card["category"].cget("text") == "分类：男生小说-游戏竞技"
        assert instance.card_widgets["3004323461"]["category"].cget("text") == "分类：暂无信息"
        assert "找到 2 本书" in instance.status.get()

        # covers resolve from the fake HTTP layer
        deadline = time.time() + 5
        while time.time() < deadline and card["image"] is None:
            instance._drain_events()
            time.sleep(0.05)
        assert card["image"] is not None, "result card cover never rendered"

        # 2. 结果行画在 canvas 上（不再是子窗口），点击由整块 canvas 处理
        assert instance.results.canvas.bind("<Button-1>"), "result list lost its click binding"
        instance._select_book(instance.book_rows["19480992"])
        info_job = instance.job_seq
        assert instance.selected_id == "19480992"
        assert instance.id_label.cget("text") == "书籍 ID：19480992"
        assert instance.rating_label.cget("text") == ""

        # 3. worker replies with BOOK_INFO
        info = {"bookId": "19480992", "title": "超神机械师", "author": "齐佩甲",
                "cover": "https://example.test/t6_19480992.jpg",
                "intro": "韩萧，《星海》骨灰级代练。", "category": "男生小说-游戏竞技",
                "publisher": "", "publishTime": "", "isbn": "",
                "newRating": 888, "newRatingCount": 23718}
        instance.events.put(("line", "BOOK_INFO\t" + json.dumps(info, ensure_ascii=False)))
        instance.events.put(("done", (info_job, 0)))
        instance._drain_events()
        assert instance.book_title_label.cget("text") == "超神机械师"
        assert instance.author_label.cget("text") == "作者：齐佩甲"
        assert "分类：男生小说-游戏竞技" in instance.category_label.cget("text")
        assert instance.rating_label.cget("text") == "评分：8.88 / 10  ·  23718 人评分"
        assert instance.id_label.cget("text") == "书籍 ID：19480992"
        assert "韩萧" in instance.intro.get("1.0", "end")
        assert "书籍详情已加载" in instance.status.get()

        # 4. a malformed BOOK_INFO must not break the message loop
        instance.events.put(("line", "BOOK_INFO\t{broken"))
        instance._drain_events()
        assert "暂时读不到" in instance.status.get()

        # 5. a worker failure used to raise AttributeError and kill the poll loop
        instance.events.put(("line", "FAILED RuntimeError: WeRead gateway error for /store/search: -1"))
        instance._poll()
        assert "微信读书服务暂时返回异常" in instance.status.get()
        assert "本次操作未完成" in _log_text()

        # 6. a stale "done" from a superseded worker must not clear live state
        instance.current_action = "search"
        instance.job_seq += 1
        instance.events.put(("done", (instance.job_seq - 1, 1)))
        instance._drain_events()
        assert instance.current_action == "search"
    finally:
        app.requests.get = original_get
        root.destroy()


# ===== 新增：busy 拒绝 / FAILED 恢复 / 过期 payload 红测试 =====

class _BusyProcess:
    """A worker handle that never exits, to simulate a long-running job."""
    def poll(self):
        return None
    def terminate(self):
        pass


def _make_gui():
    import customtkinter as ctk
    root = ctk.CTk()
    root.withdraw()
    return root, app.DesktopApp(root)


def _spy_start(instance, calls, accept=True):
    """Replace _start_worker with an offline recorder (no real subprocess)."""
    def fake_start(args, action, status):
        calls.append((list(args), action))
        instance.job_seq += 1
        instance.current_action = action
        instance.last_failure = False
        instance.last_unresolved = False
        instance.status.set(status)
        return accept
    instance._start_worker = fake_start


def _search_row(book_id="19480992", title="超神机械师"):
    return {
        "bookId": book_id, "title": title, "author": "齐佩甲",
        "cover": "", "newRating": "888", "newRatingCount": "23718",
        "category": "男生小说-游戏竞技",
    }


def test_card_click_during_busy_job_queues_detail_until_done():
    """点卡片时 worker 忙 -> 排队 -> 当前任务 done 后自动发 --book-info。"""
    root, instance = _make_gui()
    calls = []
    try:
        _spy_start(instance, calls)
        instance.process = _BusyProcess()
        instance.current_action = "search"
        row = _search_row()
        instance._select_book(row)

        # 忙时不启动 worker
        assert calls == [], calls
        assert "排队" in instance.status.get(), instance.status.get()
        assert instance.pending_book["bookId"] == "19480992"

        # 当前任务 done -> 触发 flush
        instance.events.put(("done", (instance.job_seq, 0)))
        instance._drain_events()

        # flush 启动了 --book-info
        assert (["--book-info", "19480992"], "book-info") in [(a, k) for a, k in calls], calls
        assert instance.pending_book is None
        assert "正在读取" in instance.category_label.cget("text")
    finally:
        root.destroy()


def test_second_book_click_while_first_loading_loads_correctly():
    """点第一本书加载中时点第二本 -> 第二本排队 -> 第一本 done 后自动加载第二本详情。"""
    root, instance = _make_gui()
    calls = []
    try:
        _spy_start(instance, calls)
        instance.process = _BusyProcess()
        instance.current_action = "search"

        # 先点第一本
        row1 = _search_row("19480992", "超神机械师")
        instance._select_book(row1)
        assert instance.selected_id == "19480992"
        assert instance.pending_book["bookId"] == "19480992"
        assert calls == []

        # 再点第二本（此时仍忙） -> 覆盖 pending
        row2 = _search_row("41794030", "道诡异仙")
        instance._select_book(row2)
        assert instance.selected_id == "41794030"
        assert instance.pending_book["bookId"] == "41794030"

        # 第一本书的 done 到达 -> flush 启动第二本的 book-info
        instance.events.put(("done", (instance.job_seq, 0)))
        instance._drain_events()

        assert instance.pending_book is None
        assert (["--book-info", "41794030"], "book-info") in [(a, k) for a, k in calls], calls
        assert "正在读取" in instance.category_label.cget("text")
    finally:
        root.destroy()


def test_search_while_worker_busy_keeps_existing_results():
    """worker 忙时搜索 -> 保留已有结果、result_count 不卡在 loading。"""
    root, instance = _make_gui()
    calls = []
    try:
        _spy_start(instance, calls)
        instance._add_book_card(_search_row())
        assert len(instance.card_widgets) == 1
        instance.process = _BusyProcess()
        instance.search_entry.delete(0, "end")
        instance.search_entry.insert(0, "道诡异仙")
        instance.search()

        assert calls == [], calls
        assert len(instance.card_widgets) == 1, instance.card_widgets
        assert "19480992" in instance.book_rows
        assert "正在搜索微信读书" not in instance.result_count.cget("text")
        assert "等它结束" in instance.status.get() or "任务" in instance.status.get()
    finally:
        root.destroy()


def test_check_auth_while_worker_busy_does_not_stick_badge():
    """worker 忙时检查登录 -> 徽标不变、不卡在"正在检查…"。"""
    root, instance = _make_gui()
    calls = []
    try:
        _spy_start(instance, calls)
        instance.process = _BusyProcess()
        instance.check_auth()
        assert calls == [], calls
        assert instance.auth_badge.cget("text") == "尚未检查", instance.auth_badge.cget("text")
        assert "等它结束" in instance.status.get() or "任务" in instance.status.get()
    finally:
        root.destroy()


def test_failed_book_info_request_restores_detail_placeholders():
    """FAILED book-info -> 详情标签恢复为行数据占位，不卡 loading。"""
    root, instance = _make_gui()
    calls = []
    try:
        _spy_start(instance, calls)
        row = _search_row()
        instance._select_book(row)
        job = instance.job_seq
        assert instance.category_label.cget("text") == "正在读取详情…"

        instance.events.put(("line", "FAILED RuntimeError: WeRead gateway error for /store/book?bookId=1: -1"))
        instance.events.put(("done", (job, 1)))
        instance._drain_events()

        assert instance.category_label.cget("text") != "正在读取详情…"
        assert "分类：男生小说-游戏竞技" in instance.category_label.cget("text")
        assert instance.rating_label.cget("text") == "评分：8.88 / 10  ·  23718 人评分"
        assert "正在读取简介" not in instance.intro.get("1.0", "end")
        assert "微信读书服务暂时返回异常" in instance.status.get()
    finally:
        root.destroy()


def test_search_done_without_results_clears_loading_counter():
    """空结果/失败搜索 -> result_count 复位，不卡 loading 文本。"""
    root, instance = _make_gui()
    calls = []
    try:
        _spy_start(instance, calls)
        instance.search_entry.delete(0, "end")
        instance.search_entry.insert(0, "不存在的书名")
        instance.search()
        job = instance.job_seq
        assert instance.result_count.cget("text") == "正在搜索微信读书…"

        # 空结果成功
        instance.events.put(("done", (job, 0)))
        instance._drain_events()
        assert instance.result_count.cget("text") == "没有找到匹配的书籍", instance.result_count.cget("text")

        # 失败搜索
        instance.search()
        job = instance.job_seq
        instance.events.put(("line", "FAILED Timeout: read timed out"))
        instance.events.put(("done", (job, 1)))
        instance._drain_events()
        assert instance.result_count.cget("text") == "搜索没有完成；请稍后再试"
        assert "连接微信读书超时" in instance.status.get()
    finally:
        root.destroy()


def test_export_while_worker_busy_is_rejected_before_dialog():
    """worker 忙时导出 -> 不弹 dialog、不启动 worker、progress 不被重置。"""
    root, instance = _make_gui()
    calls = []
    dialogs = []
    original_ask = app.messagebox.askyesno
    original_info = app.messagebox.showinfo
    try:
        _spy_start(instance, calls)
        instance.selected_id = "19480992"
        instance.selected_title = "超神机械师"
        instance.selected_author = "齐佩甲"
        instance.process = _BusyProcess()
        instance.progress.set(0.4)
        app.messagebox.askyesno = lambda *a, **k: dialogs.append(a) or True
        app.messagebox.showinfo = lambda *a, **k: dialogs.append(a) or True

        instance.export()

        assert dialogs == [], dialogs
        assert calls == [], calls
        assert "等它结束" in instance.status.get(), instance.status.get()
        assert instance.progress.get() == 0.4, "running progress must not be reset"
    finally:
        app.messagebox.askyesno = original_ask
        app.messagebox.showinfo = original_info
        root.destroy()


def test_stale_book_info_payload_for_another_book_is_ignored():
    """另一本书的 BOOK_INFO 到达 -> 被忽略，不覆盖当前选中书籍的详情。"""
    root, instance = _make_gui()
    calls = []
    try:
        _spy_start(instance, calls)
        row = _search_row()
        instance._select_book(row)
        stale = {"bookId": "41794030", "title": "道诡异仙", "author": "狐尾的笔",
                 "cover": "", "intro": "另一本书的简介", "category": "玄幻",
                 "newRating": 871, "newRatingCount": 4074}
        instance.events.put(("line", "BOOK_INFO\t" + json.dumps(stale, ensure_ascii=False)))
        instance._drain_events()
        assert instance.book_title_label.cget("text") == "超神机械师", instance.book_title_label.cget("text")
        assert "另一本书的简介" not in instance.intro.get("1.0", "end")

        fresh = {"bookId": "19480992", "title": "超神机械师", "author": "齐佩甲",
                 "cover": "", "intro": "韩萧，《星海》骨灰级代练。", "category": "男生小说-游戏竞技",
                 "newRating": 888, "newRatingCount": 23718}
        instance.events.put(("line", "BOOK_INFO\t" + json.dumps(fresh, ensure_ascii=False)))
        instance._drain_events()
        assert "韩萧" in instance.intro.get("1.0", "end")
        assert instance.info_loaded is True
    finally:
        root.destroy()


def _jpeg_bytes(color=(200, 60, 60)):
    import PIL.Image
    buf = io.BytesIO()
    PIL.Image.new("RGB", (60, 90), color).save(buf, format="JPEG")
    return buf.getvalue()


def test_epoch_publish_time_is_treated_as_unknown():
    # WeRead answers 1970-01-01 (epoch) when it has no publication date; showing
    # that raw value reads like a real date from 1970.
    for junk in ("1970-01-01", "1970/01/01", "1970年1月1日", "1970-1-1", 0, "0", "None"):
        lines = app.book_detail_lines({"category": "玄幻", "publishTime": junk})
        assert lines == ["分类：玄幻"], (junk, lines)
    # A real date still shows, and empty stays absent.
    assert app.book_detail_lines({"category": "玄幻", "publishTime": "2019-03-01"}) == \
        ["分类：玄幻", "出版时间：2019-03-01"]
    # 接口常带零点时间：只留日期，否则「出版时间」太长，整行被挤成两行。
    assert app.book_detail_lines({"publishTime": "2018-04-01 00:00:00"}) == \
        ["出版时间：2018-04-01"]
    assert app.book_detail_lines({"publishTime": ""}) == ["分类/出版社：暂无信息"]


def test_zero_rating_is_treated_as_unrated():
    # newRating=0 means "nobody has scored it yet", not "this book scores zero".
    assert app.rating_text(0, 0) == ""
    assert app.rating_text("0", "0") == ""
    assert app.rating_text(0.0, 5) == ""
    assert app.card_rating_text(0, 0) == "评分：暂无信息"
    assert app.card_rating_text("0", "12") == "评分：暂无信息"
    # Real scores keep working (WeRead ships tenths x100, i.e. 888 -> 8.88).
    assert app.rating_text(888, 23717) == "评分：8.88 / 10  ·  23717 人评分"
    assert app.rating_text(871, 4074) == "评分：8.71 / 10  ·  4074 人评分"
    assert app.rating_text(8.7, 42) == "评分：8.7 / 10  ·  42 人评分"
    assert app.card_rating_text(888, 23717) == "评分：8.88 / 10  ·  23717 人评分"
    # A real score with an unknown voter count keeps the score and drops the count.
    assert app.rating_text(888, 0) == "评分：8.88 / 10"


def test_missing_readable_chapters_error_is_translated():
    # /book/info worked but the catalog has nothing readable; this used to fall
    # through to the generic FALLBACK_ZH text in the status bar.
    message = app.friendly_error("FAILED RuntimeError No readable chapters in WeRead catalog")
    assert message != app.FALLBACK_ZH
    assert "章节" in message, message


def test_switching_books_after_a_cover_loaded_keeps_the_cover_label_usable():
    """Regression: the second book's detail pane died on a stale Tk image name.

    CTkLabel._update_image() is a no-op when the image is set to None, so the
    inner Tk label kept pointing at the previous CTkImage. Once that object was
    garbage collected Tk deleted the image and every later text update raised
    `TclError: image "pyimageN" doesn't exist` -- which aborted
    _start_book_info before the worker was ever spawned.
    """
    import gc
    root, instance = _make_gui()
    calls = []
    try:
        _spy_start(instance, calls)
        # First book: the cover really renders, so a real CTkImage/pyimageN exists.
        # _apply_detail_cover now receives an already-decoded image, matching what
        # _download_cover puts on the queue once it decodes off the Tk thread.
        instance._apply_detail_cover(app._decode_cover(
            _jpeg_bytes(), (instance.DETAIL_COVER_W, instance.DETAIL_COVER_H)))
        assert instance.cover_image is not None, "cover image never applied"

        # Selecting another book clears the image reference.
        instance._select_book(_search_row("41794030", "道诡异仙"))
        gc.collect()

        # The second cover must not explode on the dangling image name.
        instance._apply_detail_cover(app._decode_cover(
            _jpeg_bytes((60, 120, 200)),
            (instance.DETAIL_COVER_W, instance.DETAIL_COVER_H)))
        assert instance.cover_image is not None
        assert "TclError" not in _log_text()

        # And the second book's worker must actually have been started.
        assert (["--book-info", "41794030"], "book-info") in [(a, k) for a, k in calls], calls
    finally:
        root.destroy()


def test_failed_book_info_never_reports_an_epoch_publish_time():
    """The restore path must not resurrect 1970-01-01 as if it were real."""
    root, instance = _make_gui()
    try:
        _spy_start(instance, [])
        row = _search_row()
        row["publishTime"] = "1970-01-01"
        row["newRating"] = 0
        instance._select_book(row)
        job = instance.job_seq
        instance.events.put(("line", "FAILED RuntimeError: WeRead gateway error for /book/info: -1"))
        instance.events.put(("done", (job, 1)))
        instance._drain_events()
        detail = instance.category_label.cget("text")
        assert "1970" not in detail, detail
        assert instance.rating_label.cget("text") != "评分：0.0 / 10"
    finally:
        root.destroy()


def _open_login(instance):
    """Build the login window without touching the network."""
    started = []
    instance._start_qr_login = lambda: started.append(True)
    instance.open_login()
    return started


def _blank_credentials_file():
    path = Path(app.weread_login.CREDENTIAL_STATE)
    if path.exists():
        path.unlink()


def test_login_window_offers_both_scan_and_manual_entry():
    """Both login routes must exist: the user asked for manual API Key input."""
    root, instance = _make_gui()
    try:
        _blank_credentials_file()
        started = _open_login(instance)
        assert started == [True], "the QR fetch must start on open"
        assert instance.login_dialog is not None
        # QR panel is the default view, manual panel is available and switchable.
        assert instance.login_qr_frame.winfo_manager() == "pack"
        assert instance.login_manual_frame.winfo_manager() == ""
        instance.login_mode.set("manual")
        instance._switch_login_mode()
        assert instance.login_manual_frame.winfo_manager() == "pack"
        assert instance.login_qr_frame.winfo_manager() == ""
        instance.login_mode.set("qr")
        instance._switch_login_mode()
        assert instance.login_qr_frame.winfo_manager() == "pack"
    finally:
        instance.close_login()
        root.destroy()


def test_login_qr_image_is_kept_referenced_so_later_text_updates_survive():
    """Regression: a GC'd CTkImage makes every later text update die with TclError."""
    from PIL import Image as PILImage
    root, instance = _make_gui()
    try:
        _open_login(instance)
        before = len(instance._image_refs)
        image = PILImage.new("RGB", (64, 64), (10, 20, 30))
        instance.events.put(("login", ("qr", image)))
        instance._drain_events()
        assert len(instance._image_refs) == before + 1, "the QR image must be retained"
        # The label now shows the code; updating text must not raise.
        instance.login_qr_status.configure(text="请用微信扫一扫。")
        instance.login_qr_holder.configure(text="")
        assert instance.login_qr_status.cget("text") == "请用微信扫一扫。"
    finally:
        instance.close_login()
        root.destroy()


def test_login_manual_entry_refuses_incomplete_credentials():
    """No half-written state file: both fields are required before saving."""
    root, instance = _make_gui()
    try:
        _blank_credentials_file()
        _open_login(instance)
        instance.login_mode.set("manual")
        instance._switch_login_mode()

        instance.manual_api_key.delete(0, "end")
        instance.manual_skey.delete(0, "end")
        instance.manual_api_key.insert(0, "wrk-key-only")
        instance._save_manual_credentials()
        assert "wr_skey" in instance.login_manual_status.cget("text")
        assert not Path(app.weread_login.CREDENTIAL_STATE).exists()

        instance.manual_api_key.delete(0, "end")
        instance.manual_skey.insert(0, "wr_skey-only")
        instance._save_manual_credentials()
        assert "API Key" in instance.login_manual_status.cget("text")
        assert not Path(app.weread_login.CREDENTIAL_STATE).exists()
    finally:
        instance.close_login()
        root.destroy()


def test_login_manual_entry_saves_credentials_and_updates_status():
    root, instance = _make_gui()
    try:
        _blank_credentials_file()
        _open_login(instance)
        instance.login_mode.set("manual")
        instance._switch_login_mode()
        instance.manual_api_key.delete(0, "end")
        instance.manual_skey.delete(0, "end")
        instance.manual_api_key.insert(0, "wrk-manual-key")
        instance.manual_skey.insert(0, "manual-skey")
        instance._save_manual_credentials()

        # Read back through the loader, not as raw JSON: the session file is
        # DPAPI-sealed, so parsing it here is what used to pin the credential
        # store to cleartext.
        saved = app.weread_login.load_saved_credentials()
        assert saved["api_key"] == "wrk-manual-key"
        assert saved["cookies"]["wr_skey"] == "manual-skey"
        raw = Path(app.weread_login.CREDENTIAL_STATE).read_bytes()
        assert b"manual-skey" not in raw, "the session cookie was stored in cleartext"
        assert app.weread_login.has_credentials()
        assert instance.auth_badge.cget("text") == "登录有效"
        assert instance.login_dialog is None, "a successful save closes the window"
    finally:
        instance.close_login()
        root.destroy()


def test_login_success_event_persists_credentials_and_closes_the_window():
    root, instance = _make_gui()
    try:
        _blank_credentials_file()
        _open_login(instance)
        credentials = {
            "cookies": {"wr_skey": "scanned-skey", "wr_vid": "VID"},
            "api_key": "wrk-scanned",
            "account": {"name": "齐佩甲", "login_method": "qr"},
        }
        instance.events.put(("login", ("done", credentials)))
        instance._drain_events()

        # Loader, not raw JSON: the session file is sealed at rest.
        saved = app.weread_login.load_saved_credentials()
        assert saved["cookies"]["wr_skey"] == "scanned-skey"
        assert saved["cookies"]["wr_vid"] == "VID"
        assert "齐佩甲" in instance.status.get()
        assert instance.login_dialog is None
    finally:
        instance.close_login()
        root.destroy()


def test_login_events_arriving_after_close_are_ignored():
    """A poll that finishes after the window closed must not touch dead widgets."""
    root, instance = _make_gui()
    try:
        _blank_credentials_file()
        _open_login(instance)
        instance.close_login()
        assert instance.login_dialog is None
        assert instance.login_cancel.is_set(), "closing must stop the poller"
        instance.events.put(("login", ("qr", object())))
        instance.events.put(("login", ("error", "迟到的错误")))
        instance.events.put(("login", ("done", {"cookies": {}, "api_key": "x"})))
        instance._drain_events()  # must not raise
    finally:
        root.destroy()


def test_login_poll_error_is_surfaced_without_stopping_the_app():
    root, instance = _make_gui()
    try:
        _blank_credentials_file()
        _open_login(instance)
        instance.events.put(("login", ("error", "二维码已过期，请点击“重新获取二维码”。")))
        instance._drain_events()
        assert "已过期" in instance.login_qr_status.cget("text")
        assert instance.login_dialog is not None, "an expired code keeps the window open"
    finally:
        instance.close_login()
        root.destroy()


def test_auth_failure_without_credentials_points_at_the_login_window():
    """A failed auth check must end with the login window one click away."""
    root, instance = _make_gui()
    try:
        _blank_credentials_file()
        calls = []
        _spy_start(instance, calls)
        asked = []
        real_askyesno = app.messagebox.askyesno

        def fake_ask(title, message, **kwargs):
            asked.append((title, message))
            return False

        app.messagebox.askyesno = fake_ask
        try:
            # Go through the real button so _start_worker records the auth action.
            instance.check_auth()
            assert calls and calls[0][1] == "auth"
            job = instance.job_seq
            instance.events.put(("line", "FAILED RuntimeError: no credentials"))
            instance.events.put(("done", (job, 1)))
            instance._drain_events()
        finally:
            app.messagebox.askyesno = real_askyesno

        assert instance.auth_badge.cget("text") == "需要登录"
        assert "登录" in instance.status.get()
        assert asked and "登录窗口" in asked[0][1]
    finally:
        root.destroy()


def test_auth_failure_with_stale_credentials_asks_for_a_rescan():
    """Credentials present but rejected: do not claim the user has never logged in."""
    root, instance = _make_gui()
    try:
        app.weread_login.save_credentials({
            "cookies": {"wr_skey": "stale-skey"}, "api_key": "wrk-stale"})
        _spy_start(instance, [])
        asked = []
        real_askyesno = app.messagebox.askyesno
        app.messagebox.askyesno = lambda *a, **k: asked.append(a) or False
        try:
            instance.check_auth()
            job = instance.job_seq
            instance.events.put(("done", (job, 1)))  # clean non-zero exit, no FAILED line
            instance._drain_events()
        finally:
            app.messagebox.askyesno = real_askyesno

        assert instance.auth_badge.cget("text") == "检查失败"
        assert "过期" in instance.status.get()
        assert not asked, "a user who has credentials must not be nagged to log in"
    finally:
        _blank_credentials_file()
        root.destroy()


def test_a_burst_of_worker_lines_cannot_block_a_single_refresh_tick():
    """A big burst must spill over several ticks, or the window stops responding."""
    root, instance = _make_gui()
    try:
        burst = app.MAX_EVENTS_PER_TICK * 3 + 7
        for _ in range(burst):
            instance.events.put(("line", "Search metadata received."))
        instance._drain_events()
        # One tick consumes at most the cap; the remainder stays queued.
        assert instance.events.qsize() == burst - app.MAX_EVENTS_PER_TICK
        ticks = 1
        while instance.events.qsize() and ticks < 50:
            instance._drain_events()
            ticks += 1
        assert instance.events.qsize() == 0
        assert ticks > 1, "a burst must not be drained inside a single tick"
    finally:
        root.destroy()


def test_worker_lines_still_reach_the_log_file_after_batching():
    """Batching the flush must not swallow the text.

    The window no longer shows a log panel, so the log file is the only place a
    worker line can be read back from.
    """
    root, instance = _make_gui()
    try:
        instance.events.put(("line", "AUTH_OK; shelf entries: 7"))
        instance._drain_events()
        assert "AUTH_OK" in _log_text()
    finally:
        root.destroy()


def test_login_button_shows_the_saved_account_name_at_startup():
    """A signed-in machine should not still be asking the user to log in."""
    app.weread_login.save_credentials({
        "cookies": {"wr_skey": "skey"}, "api_key": "wrk-key",
        "account": {"name": "钦小糖"}})
    try:
        root, instance = _make_gui()
        try:
            assert instance.login_button.cget("text") == "钦小糖"
        finally:
            root.destroy()
    finally:
        _blank_credentials_file()


def test_login_button_switches_to_the_account_name_after_scanning():
    root, instance = _make_gui()
    try:
        _blank_credentials_file()
        _open_login(instance)
        instance.events.put(("login", ("done", {
            "cookies": {"wr_skey": "s"}, "api_key": "k",
            "account": {"name": "齐佩甲"}})))
        instance._drain_events()
        assert instance.login_button.cget("text") == "齐佩甲"
        # The button stays a way back into the login window.
        assert instance.login_button.cget("command") is not None
    finally:
        instance.close_login()
        root.destroy()


def test_login_button_truncates_a_long_name_and_restores_the_call_to_action():
    root, instance = _make_gui()
    try:
        instance._set_login_identity("一二三四五六七八九十")
        assert instance.login_button.cget("text") == "一二三四五六七八…"
        instance._set_login_identity("")          # signed in, name unknown
        assert instance.login_button.cget("text") == "已登录"
        instance._set_login_identity(None)        # signed out
        assert instance.login_button.cget("text") == "登录 / 切换账号"
    finally:
        root.destroy()


def test_login_button_returns_to_the_call_to_action_when_signed_out():
    root, instance = _make_gui()
    try:
        _blank_credentials_file()
        instance._set_login_identity("钦小糖")
        real_askyesno = app.messagebox.askyesno
        app.messagebox.askyesno = lambda *a, **k: False
        try:
            _spy_start(instance, [])
            instance.check_auth()
            job = instance.job_seq
            instance.events.put(("done", (job, 1)))
            instance._drain_events()
        finally:
            app.messagebox.askyesno = real_askyesno
        assert instance.login_button.cget("text") == "登录 / 切换账号"
    finally:
        root.destroy()


def test_login_button_keeps_the_name_when_only_the_session_expired():
    """An expired session is still the same account, so the name stays put."""
    app.weread_login.save_credentials({
        "cookies": {"wr_skey": "stale"}, "api_key": "wrk-stale",
        "account": {"name": "钦小糖"}})
    try:
        # Credentials must exist before startup, so the name gets restored.
        root, instance = _make_gui()
        try:
            assert instance.login_button.cget("text") == "钦小糖"
            _spy_start(instance, [])
            instance.check_auth()
            job = instance.job_seq
            instance.events.put(("done", (job, 1)))
            instance._drain_events()
            assert instance.login_button.cget("text") == "钦小糖"
            assert "顶栏的账号名按钮" in instance.status.get()
        finally:
            root.destroy()
    finally:
        _blank_credentials_file()


def test_the_program_actually_runs_when_executed_as_a_script():
    """desktop_app.py must have an `if __name__ == "__main__"` block.

    Without it the module defines main() and stops. Running the file as a script
    exits 0 immediately with no window, no log line, and no traceback -- there is
    nothing to report, because nothing raised. PyInstaller executes the entry
    script as __main__ too, so the frozen GUI was equally dead.

    That is not hypothetical: moving the modules into weread/ dropped this guard,
    and the whole 35-file suite still passed, because every test imports
    desktop_app and calls into it directly. Nothing ran it as a program.

    Parsed, not grepped: a commented-out `# if __name__ == "__main__":` would
    satisfy a substring search.
    """
    import ast
    from pathlib import Path

    src = (Path(__file__).resolve().parent.parent / "weread" / "desktop_app.py")
    tree = ast.parse(src.read_text(encoding="utf-8", errors="replace"))

    guards = [node for node in tree.body
              if isinstance(node, ast.If)
              and isinstance(node.test, ast.Compare)
              and isinstance(node.test.left, ast.Name)
              and node.test.left.id == "__name__"]
    assert guards, (
        "desktop_app.py has no top-level `if __name__ == \"__main__\":` block, so "
        "running it -- directly or as the frozen exe -- defines main() and exits "
        "without ever starting the window")

    # The guard is useless unless it actually reaches main().
    called_main = any(
        isinstance(child, ast.Call)
        and isinstance(child.func, ast.Name)
        and child.func.id == "main"
        for guard in guards for child in ast.walk(guard)
    )
    assert called_main, (
        "the __main__ guard exists but never calls main(), so the program still "
        "does nothing when run")


def test_the_real_icon_beats_customtkinters_placeholder():
    """iconbitmap must be cleared before iconphoto, or the icon silently loses.

    CustomTkinter installs its own iconbitmap (an HICON) on the toplevel, and on
    Windows a bitmap icon outranks a photo one -- so calling iconphoto alone left
    the title bar showing CTk's blue placeholder no matter what artwork was
    loaded. Found by screenshot: every test passed while the window displayed the
    wrong icon. Isolated it by comparing tk.Tk() against ctk.CTk().
    """
    import customtkinter as ctk

    root = ctk.CTk()
    root.geometry("1120x680")
    instance = app.DesktopApp(root)
    root.update()
    calls = []
    try:
        root.iconbitmap = lambda *a: calls.append(("iconbitmap", a))
        root.iconphoto = lambda *a, **k: calls.append(("iconphoto", a))
        instance._app_icon_photo = lambda size=32: object()
        instance._set_window_icon(root)

        assert calls, "the window icon was never applied"
        first = calls[0]
        assert first[0] == "iconbitmap", (
            "iconphoto was set before clearing CTk's iconbitmap, so CTk's icon "
            "wins: first call was %r" % (calls,))
        assert first[1] == ("",), (
            "iconbitmap must be cleared with an empty path, got %r" % (first[1],))
        assert any(c[0] == "iconphoto" for c in calls), (
            "the photo icon was never set: %r" % (calls,))
    finally:
        root.destroy()


ALL_TESTS = [
    test_the_real_icon_beats_customtkinters_placeholder,
    test_the_program_actually_runs_when_executed_as_a_script,
    test_wechat_rating_scale_formats_as_ten_point_score,
    test_common_errors_are_translated_to_actionable_chinese,
    test_book_details_are_safe_to_display_when_optional_fields_are_missing,
    test_worker_search_rows_parse_into_result_cards,
    test_book_info_message_parses_and_rejects_malformed_payloads,
    test_search_meta_line_updates_category,
    test_worker_failure_lines_are_translated_without_raising,
    test_log_output_never_contains_credentials,
    test_gui_search_click_detail_flow_survives_worker_failure,
    # 新增：busy 拒绝 / FAILED 恢复 / 过期 payload
    test_card_click_during_busy_job_queues_detail_until_done,
    test_second_book_click_while_first_loading_loads_correctly,
    test_search_while_worker_busy_keeps_existing_results,
    test_check_auth_while_worker_busy_does_not_stick_badge,
    test_failed_book_info_request_restores_detail_placeholders,
    test_search_done_without_results_clears_loading_counter,
    test_export_while_worker_busy_is_rejected_before_dialog,
    test_stale_book_info_payload_for_another_book_is_ignored,
    # 新增：真实 API 脏数据 + 封面图片生命周期
    test_epoch_publish_time_is_treated_as_unknown,
    test_zero_rating_is_treated_as_unrated,
    test_missing_readable_chapters_error_is_translated,
    test_switching_books_after_a_cover_loaded_keeps_the_cover_label_usable,
    test_failed_book_info_never_reports_an_epoch_publish_time,
    # 新增：登录窗口（扫码 + 手动输入）
    test_login_window_offers_both_scan_and_manual_entry,
    test_login_qr_image_is_kept_referenced_so_later_text_updates_survive,
    test_login_manual_entry_refuses_incomplete_credentials,
    test_login_manual_entry_saves_credentials_and_updates_status,
    test_login_success_event_persists_credentials_and_closes_the_window,
    test_login_events_arriving_after_close_are_ignored,
    test_login_poll_error_is_surfaced_without_stopping_the_app,
    test_auth_failure_without_credentials_points_at_the_login_window,
    test_auth_failure_with_stale_credentials_asks_for_a_rescan,
    # 新增：登录成功后按钮显示账号名
    test_login_button_shows_the_saved_account_name_at_startup,
    test_login_button_switches_to_the_account_name_after_scanning,
    test_login_button_truncates_a_long_name_and_restores_the_call_to_action,
    test_login_button_returns_to_the_call_to_action_when_signed_out,
    test_login_button_keeps_the_name_when_only_the_session_expired,
    # 新增：worker 输出突发时主线程不得卡死
    test_a_burst_of_worker_lines_cannot_block_a_single_refresh_tick,
    test_worker_lines_still_reach_the_log_file_after_batching,
]


if __name__ == "__main__":
    for test in ALL_TESTS:
        test()
        print("PASS", test.__name__)
