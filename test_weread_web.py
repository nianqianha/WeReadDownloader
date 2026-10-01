"""Web-mode tests: the loopback security gate, the JSON API, and controller parity.

Run directly (`python test_weread_web.py`) or under pytest. The parity tests
assert that the headless controller kept the exact bookkeeping the CustomTkinter
GUI needed real bugs to earn: busy refusal, the pending_book queue, the
try/finally around _flush_pending_book, the FAILED path unsticking
placeholders, and the stale-BOOK_INFO bookId guard.
"""
import atexit
import http.client
import importlib.util
import json
import re
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "weread_web.py"
spec = importlib.util.spec_from_file_location("weread_web", SCRIPT)
web = importlib.util.module_from_spec(spec)
spec.loader.exec_module(web)

_TMP_PROFILE = tempfile.TemporaryDirectory(prefix="weread-web-test-")
LOG_DIR = Path(_TMP_PROFILE.name) / "logs"

_SERVER = None


def server():
    """One live loopback server shared by the HTTP tests."""
    global _SERVER
    if _SERVER is None:
        controller = web.WebController(LOG_DIR)
        # Never let a test reach a real worker: the HTTP layer must be exercised
        # offline, and a live --check-auth would hit the network.
        _spy_start(controller, [])
        httpd, session = web.build_server(controller)
        threading.Thread(target=httpd.serve_forever,
                         kwargs={"poll_interval": 0.05}, daemon=True).start()
        _SERVER = _LiveServer(controller, httpd, session)
        atexit.register(_SERVER.close)
    return _SERVER


class _LiveServer:
    def __init__(self, controller, httpd, session):
        self.controller = controller
        self.httpd = httpd
        self.session = session

    @property
    def port(self):
        return self.session.port

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def _call(path, method="GET", body=None, headers=None, port=None):
    """Raw HTTP so nothing silently follows a redirect or rewrites Host."""
    conn = http.client.HTTPConnection("127.0.0.1", port or server().port, timeout=10)
    payload = None
    head = dict(headers or {})
    if body is not None:
        payload = json.dumps(body).encode("utf-8")
        head.setdefault("Content-Type", "application/json")
        head["Content-Length"] = str(len(payload))
    try:
        conn.request(method, path, body=payload, headers=head)
        response = conn.getresponse()
        return response.status, {key.lower(): value for key, value in response.getheaders()}, response.read()
    finally:
        conn.close()


def _auth(token=None, extra=None):
    head = dict(extra or {})
    head["X-Weread-Token"] = token or server().session.token
    return head


# ===== security gate =====

def test_host_header_must_name_this_loopback_port():
    # DNS rebinding: a hostile page resolves its own name to 127.0.0.1 and then
    # sends Host: attacker.example. A host:port we minted ourselves cannot match.
    status, _, _ = _call("/api/state", headers=_auth(extra={"Host": "attacker.example"}))
    assert status == 403, status
    status, _, _ = _call("/api/state", headers=_auth(extra={"Host": f"127.0.0.1:{server().port + 1}"}))
    assert status == 403, status
    # The genuine loopback Host is accepted.
    status, _, body = _call("/api/state", headers=_auth())
    assert status == 200, status
    assert json.loads(body)["ok"] is True


def test_every_request_needs_this_launch_s_token():
    status, _, _ = _call("/api/state", headers={"X-Weread-Token": ""})
    assert status == 401, status
    status, _, _ = _call("/api/state")
    assert status == 401, status
    status, _, _ = _call("/api/state", headers=_auth(token="not-the-token"))
    assert status == 401, status
    # A token from a different launch must not be accepted either.
    other = web._Session(server().port)
    assert other.token != server().session.token
    status, _, _ = _call("/api/state", headers=_auth(token=other.token))
    assert status == 401, status


def test_cross_site_posts_are_refused():
    body = {"keyword": "超神机械师"}
    status, _, _ = _call("/api/search", method="POST", body=body,
                         headers=_auth(extra={"Origin": "http://attacker.example"}))
    assert status == 403, status
    status, _, _ = _call("/api/search", method="POST", body=body,
                         headers=_auth(extra={"Sec-Fetch-Site": "cross-site"}))
    assert status == 403, status
    # Our own origin is fine.
    status, _, _ = _call("/api/auth", method="POST", body={},
                         headers=_auth(extra={"Origin": f"http://127.0.0.1:{server().port}"}))
    assert status == 200, status


def test_launch_url_trades_the_token_for_a_samesite_cookie():
    token = server().session.token
    status, headers, _ = _call(f"/?t={token}", headers={"Host": f"127.0.0.1:{server().port}"})
    assert status == 303, status
    assert headers["location"] == "/"
    cookie = headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=Strict" in cookie, cookie
    assert token in cookie, cookie
    # The cookie alone then opens the page, with no token left in the address bar
    # to leak through the Referer of every cover request.
    status, _, page = _call("/", headers={"Cookie": f"{server().session.cookie_name}={token}"})
    assert status == 200, status
    assert b"<!DOCTYPE html" in page or b"<html" in page, page[:80]


def test_stale_session_cookies_from_earlier_launches_do_not_lock_the_page_out():
    """Regression: cookies are not port-scoped, so a browser keeps every session
    cookie it was ever handed for 127.0.0.1 and sends them all. Taking the first
    one picked a dead token and the page was stuck on 401 forever."""
    live = server()
    stale_one = web._Session(live.port - 1)
    stale_two = web._Session(live.port - 2)
    jar = "; ".join([
        f"{stale_one.cookie_name}={stale_one.token}",
        f"{stale_two.cookie_name}={stale_two.token}",
    ])
    status, _, _ = _call("/api/state", headers={"Cookie": jar})
    assert status == 401, status
    jar = jar + "; " + f"{live.session.cookie_name}={live.session.token}"
    status, _, body = _call("/api/state", headers={"Cookie": jar})
    assert status == 200, status
    assert json.loads(body)["ok"] is True
    # The live launch also names its cookie after its own port, so a fresh launch
    # never has to out-rank a predecessor's.
    assert live.session.cookie_name.endswith(str(live.port))
    assert live.session.cookie_name != stale_one.cookie_name


def test_launch_url_still_hands_over_the_cookie_when_stale_ones_are_present():
    """Regression: the handshake used to be skipped whenever *any* session cookie
    was present. A browser keeps the cookies of every earlier launch, so Edge sat
    on its stale ones, never received this launch's cookie, and every /api/state
    came back 401 -- the page looked alive and was permanently locked out."""
    live = server()
    stale = web._Session(live.port - 3)
    jar = f"{stale.cookie_name}={stale.token}"
    status, headers, _ = _call(f"/?t={live.session.token}", headers={"Cookie": jar})
    assert status == 303, status
    assert live.session.token in headers["set-cookie"], headers
    assert "SameSite=Strict" in headers["set-cookie"]
    # And the freshly issued cookie is what authenticates the API.
    fresh = f"{live.session.cookie_name}={live.session.token}"
    status, _, body = _call("/api/state", headers={"Cookie": jar + "; " + fresh})
    assert status == 200, status
    assert json.loads(body)["ok"] is True
    # Asking again once the live cookie is held serves the page directly.
    status, _, _ = _call(f"/?t={live.session.token}", headers={"Cookie": jar + "; " + fresh})
    assert status == 200, status


def test_page_script_nonce_matches_the_csp_header():
    token = server().session.token
    status, headers, page = _call("/", headers=_auth(token))
    assert status == 200, status
    text = page.decode("utf-8")
    csp = headers["content-security-policy"]
    match = re.search(r"script-src 'nonce-([A-Za-z0-9_-]+)'", csp)
    assert match, csp
    nonce = match.group(1)
    # A mismatch would let the browser block the page's own script.
    assert f'nonce="{nonce}"' in text, "page script tag does not carry the CSP nonce"
    assert "__CSP_NONCE__" not in text, "nonce placeholder was not substituted"
    assert "default-src 'none'" in csp
    assert "frame-ancestors 'none'" in csp
    # The QR code is served by this same process, so img-src must allow 'self'.
    # Without it the browser blocks the code and the login silently never shows.
    assert re.search(r"img-src[^;]*'self'", csp), csp
    # No token is embedded in the page: authentication is the cookie's job.
    assert token not in text, "the session token leaked into the page body"


def test_page_pulls_in_nothing_external():
    _, _, page = _call("/", headers=_auth())
    text = page.decode("utf-8")
    for pattern in (r"<script[^>]+\bsrc=", r"<link[^>]+stylesheet", r"@import",
                    r"\bon(?:click|error|input|load|change)\s*=", r"cdn\.",
                    r"googleapis", r"unpkg", r"jsdelivr"):
        assert not re.search(pattern, text, re.I), pattern
    # connect-src is 'self', so every request target must be a relative path.
    for target in re.findall(r"""fetch\(\s*['"]([^'"]+)""", text):
        assert target.startswith("/"), target
    # A cover that 404s must degrade to a placeholder, not a broken image.
    assert re.search(r"""addEventListener\(\s*['"]error['"]""", text), \
        "cover load failures are not handled"


# ===== JSON API =====

def test_state_endpoint_exposes_the_documented_shape():
    status, _, body = _call("/api/state", headers=_auth())
    assert status == 200, status
    state = json.loads(body)["state"]
    assert set(state) == {"busy", "action", "status", "auth", "resultCount",
                          "results", "detail", "progress", "jobs", "logSeq",
                          "account", "hasCredentials", "login"}, sorted(state)
    assert isinstance(state["results"], list)
    assert isinstance(state["progress"], float)
    assert state["jobs"] == 1, state["jobs"]
    assert set(state["login"]) == {"state", "message", "otpNeeded", "qrSeq"}, sorted(state["login"])
    assert state["login"]["state"] == "idle"
    assert set(state["detail"]) == {"bookId", "title", "author", "cover",
                                    "categoryText", "ratingText", "intro",
                                    "infoLoaded", "pending", "loading"}, sorted(state["detail"])


def test_search_select_flow_reaches_the_browser_as_json():
    live = server()
    controller = live.controller
    _reset(controller)
    calls = []
    _spy_start(controller, calls)

    status, _, body = _call("/api/search", method="POST", body={"keyword": "超神机械师"},
                            headers=_auth())
    assert status == 200, status
    assert ["--search", "超神机械师"] in [args for args, _action in calls], calls
    assert json.loads(body)["state"]["resultCount"] == "正在搜索微信读书…"

    for row in ("\t".join(("19480992", "超神机械师", "齐佩甲",
                           "https://example.test/s_19480992.jpg", "888", "23718", "")),
                "\t".join(("3004323461", "超神机械师之异神高德", "爱吃包子狗不理",
                           "https://example.test/3004323461.jpg", "", "", ""))):
        controller.events.put(("line", row))
    controller.events.put(("line", "SEARCH_META\t19480992\t男生小说-游戏竞技"))
    controller.events.put(("line", "SEARCH_META\t3004323461\t"))
    controller.events.put(("done", (controller.job_seq, 0)))
    controller._drain_events()

    _, _, body = _call("/api/state", headers=_auth())
    state = json.loads(body)["state"]
    assert [row["bookId"] for row in state["results"]] == ["19480992", "3004323461"]
    assert state["results"][0]["title"] == "超神机械师"
    assert state["results"][0]["ratingText"] == "评分：8.88 / 10  ·  23718 人评分"
    assert state["results"][0]["cover"] == "https://example.test/s_19480992.jpg"
    assert state["results"][0]["category"] == "男生小说-游戏竞技"
    assert state["results"][1]["ratingText"] == "评分：暂无信息"
    assert "找到 2 本书" in state["resultCount"]

    # Clicking a book asks the worker for details, exactly like the GUI does.
    status, _, body = _call("/api/select", method="POST", body={"bookId": "19480992"},
                            headers=_auth())
    assert status == 200, status
    assert ["--book-info", "19480992"] in [args for args, _action in calls], calls
    state = json.loads(body)["state"]
    assert state["detail"]["bookId"] == "19480992"
    assert state["detail"]["title"] == "超神机械师"

    info = {"bookId": "19480992", "title": "超神机械师", "author": "齐佩甲",
            "cover": "https://example.test/t6_19480992.jpg",
            "intro": "韩萧，《星海》骨灰级代练。", "category": "男生小说-游戏竞技",
            "publisher": "", "publishTime": "", "isbn": "",
            "newRating": 888, "newRatingCount": 23718}
    controller.events.put(("line", "BOOK_INFO\t" + json.dumps(info, ensure_ascii=False)))
    controller.events.put(("done", (controller.job_seq, 0)))
    controller._drain_events()

    _, _, body = _call("/api/state", headers=_auth())
    detail = json.loads(body)["state"]["detail"]
    assert "韩萧" in detail["intro"]
    assert "分类：男生小说-游戏竞技" in detail["categoryText"]
    assert detail["ratingText"] == "评分：8.88 / 10  ·  23718 人评分"
    assert detail["infoLoaded"] is True
    assert detail["cover"] == "https://example.test/t6_19480992.jpg"
    # The epoch sentinel must not reach the page as if it were a real date.
    assert "1970" not in detail["categoryText"]


def test_auth_endpoint_reports_the_worker_verdict():
    controller = server().controller
    _reset(controller)
    calls = []
    _spy_start(controller, calls)
    _, _, body = _call("/api/auth", method="POST", body={}, headers=_auth())
    assert json.loads(body)["state"]["auth"] == "正在检查…"
    assert ["--check-auth"] in [args for args, _action in calls], calls

    controller.events.put(("line", "AUTH_OK; shelf entries: 7"))
    controller.events.put(("done", (controller.job_seq, 0)))
    controller._drain_events()
    _, _, body = _call("/api/state", headers=_auth())
    state = json.loads(body)["state"]
    assert state["auth"] == "登录有效", state["auth"]


def test_unknown_route_and_oversized_body_are_handled():
    status, _, _ = _call("/api/nope", headers=_auth())
    assert status == 404, status
    # A body past the cap is refused as empty rather than buffered or hung on.
    status, _, body = _call("/api/search", method="POST",
                            body={"keyword": "x" * (web.MAX_BODY_BYTES + 1)},
                            headers=_auth())
    assert status == 200, status
    state = json.loads(body)["state"]
    assert state["resultCount"] == "搜索结果会显示在这里", state["resultCount"]


# ===== controller parity with the GUI =====

class _BusyProcess:
    def poll(self):
        return None

    def terminate(self):
        pass


def _controller():
    return web.WebController(LOG_DIR)


def _reset(controller):
    controller.process = None
    controller.current_action = ""
    controller.job_seq = 0
    controller.last_failure = False
    controller.book_rows.clear()
    controller._category_done.clear()
    controller.result_count = "搜索结果会显示在这里"
    controller.status = "准备就绪。第一步：检查登录状态。"
    controller.auth = "尚未检查"
    controller.selected.update({"bookId": "", "title": "", "author": "", "cover": "",
                                "categoryText": "", "ratingText": "", "intro": "",
                                "infoLoaded": False, "pending": False, "loading": False})
    controller.book_info = {}
    controller.pending_book = None
    controller.info_loaded = False
    controller.events.queue.clear()
    _spy_start(controller, [])


def _spy_start(controller, calls, accept=True):
    def fake_start(args, action, status):
        calls.append((list(args), action))
        controller.job_seq += 1
        controller.current_action = action
        controller.last_failure = False
        controller.last_unresolved = False
        controller.status = status
        return accept
    controller._start_worker = fake_start


def _row(book_id="19480992", title="超神机械师"):
    return {"bookId": book_id, "title": title, "author": "齐佩甲", "cover": "",
            "newRating": "888", "newRatingCount": "23718", "category": "男生小说-游戏竞技"}


def test_select_while_busy_queues_the_detail_request():
    controller = _controller()
    calls = []
    _spy_start(controller, calls)
    controller.process = _BusyProcess()
    controller._add_book_card(_row())
    controller.select("19480992")

    assert calls == [], calls
    assert "排队" in controller.status
    assert controller.pending_book["bookId"] == "19480992"
    assert controller.snapshot()["detail"]["pending"] is True

    controller.events.put(("done", (controller.job_seq, 0)))
    controller._drain_events()
    assert (["--book-info", "19480992"], "book-info") in calls, calls
    assert controller.pending_book is None
    assert controller.snapshot()["detail"]["pending"] is False


def test_second_book_while_the_first_is_loading_supersedes_the_queue():
    controller = _controller()
    calls = []
    _spy_start(controller, calls)
    controller.process = _BusyProcess()
    controller._add_book_card(_row("19480992", "超神机械师"))
    controller._add_book_card(_row("41794030", "道诡异仙"))

    controller.select("19480992")
    controller.select("41794030")
    assert controller.pending_book["bookId"] == "41794030"

    controller.events.put(("done", (controller.job_seq, 0)))
    controller._drain_events()
    assert (["--book-info", "41794030"], "book-info") in calls, calls
    assert controller.snapshot()["detail"]["bookId"] == "41794030"


def test_search_and_auth_are_refused_while_busy_without_losing_state():
    controller = _controller()
    calls = []
    _spy_start(controller, calls)
    controller._add_book_card(_row())
    controller.process = _BusyProcess()

    controller.search("道诡异仙")
    assert calls == [], calls
    assert "19480992" in controller.book_rows, "a refused search must not clear results"
    assert "正在搜索微信读书…" not in controller.result_count

    controller.check_auth()
    assert calls == [], calls
    assert controller.auth == "尚未检查", controller.auth


def test_failed_book_info_restores_the_row_placeholders():
    controller = _controller()
    calls = []
    _spy_start(controller, calls)
    controller._add_book_card(_row())
    controller.select("19480992")
    assert controller.selected["categoryText"] == "正在读取分类、评分、简介和封面…"
    job = controller.job_seq

    controller.events.put(("line", "FAILED RuntimeError: WeRead gateway error for /book/info: -1"))
    controller.events.put(("done", (job, 1)))
    controller._drain_events()

    assert controller.selected["categoryText"] != "正在读取分类、评分、简介和封面…"
    assert "分类：男生小说-游戏竞技" in controller.selected["categoryText"]
    assert controller.selected["ratingText"] == "评分：8.88 / 10  ·  23718 人评分"
    assert "正在读取书籍简介" not in controller.selected["intro"]
    assert "微信读书服务暂时返回异常" in controller.status


def test_failed_book_info_never_resurrects_an_epoch_date():
    controller = _controller()
    _spy_start(controller, [])
    row = _row()
    row["publishTime"] = "1970-01-01"
    row["newRating"] = 0
    controller._add_book_card(row)
    controller.select("19480992")
    job = controller.job_seq
    controller.events.put(("line", "FAILED RuntimeError: WeRead gateway error for /book/info: -1"))
    controller.events.put(("done", (job, 1)))
    controller._drain_events()
    assert "1970" not in controller.selected["categoryText"]
    assert controller.selected["ratingText"] != "评分：0.0 / 10"


def test_stale_book_info_for_another_book_is_ignored():
    controller = _controller()
    _spy_start(controller, [])
    controller._add_book_card(_row("19480992", "超神机械师"))
    controller._add_book_card(_row("41794030", "道诡异仙"))
    controller.select("19480992")

    stale = {"bookId": "41794030", "title": "道诡异仙", "author": "狐尾的笔",
             "cover": "", "intro": "另一本书的简介", "category": "玄幻",
             "newRating": 871, "newRatingCount": 4074}
    controller.events.put(("line", "BOOK_INFO\t" + json.dumps(stale, ensure_ascii=False)))
    controller._drain_events()
    assert controller.selected["title"] == "超神机械师", controller.selected["title"]
    assert "另一本书的简介" not in controller.selected["intro"]

    fresh = {"bookId": "19480992", "title": "超神机械师", "author": "齐佩甲",
             "cover": "", "intro": "韩萧，《星海》骨灰级代练。", "category": "男生小说-游戏竞技",
             "newRating": 888, "newRatingCount": 23718}
    controller.events.put(("line", "BOOK_INFO\t" + json.dumps(fresh, ensure_ascii=False)))
    controller._drain_events()
    assert "韩萧" in controller.selected["intro"]
    assert controller.info_loaded is True


def test_malformed_book_info_never_breaks_the_message_loop():
    controller = _controller()
    _spy_start(controller, [])
    controller._add_book_card(_row())
    controller.select("19480992")
    controller.events.put(("line", "BOOK_INFO\t{broken"))
    controller.events.put(("line", "BOOK_INFO\t"))
    controller._drain_events()
    assert "暂时读不到" in controller.status
    # And the loop keeps running afterwards.
    controller.events.put(("line", "AUTH_OK; shelf entries: 7"))
    controller._drain_events()
    assert controller.auth == "登录有效"


def test_empty_and_failed_searches_clear_the_loading_counter():
    controller = _controller()
    _spy_start(controller, [])
    controller.search("不存在的书名")
    assert controller.result_count == "正在搜索微信读书…"

    controller.events.put(("done", (controller.job_seq, 0)))
    controller._drain_events()
    assert controller.result_count == "没有找到匹配的书籍", controller.result_count

    controller.search("不存在的书名")
    controller.events.put(("line", "FAILED Timeout: read timed out"))
    controller.events.put(("done", (controller.job_seq, 1)))
    controller._drain_events()
    assert controller.result_count == "搜索没有完成；请稍后再试"
    assert "连接微信读书超时" in controller.status


def test_search_meta_fills_categories_and_done_stops_the_pending_flag():
    controller = _controller()
    _spy_start(controller, [])
    controller.search("超神机械师")
    controller._add_book_card({**_row("19480992"), "category": ""})
    controller._add_book_card({**_row("3004323461"), "category": ""})
    assert controller.book_rows["19480992"]["categoryPending"] is True

    controller.events.put(("line", "SEARCH_META\t19480992\t男生小说-游戏竞技"))
    controller._drain_events()
    assert controller.book_rows["19480992"]["category"] == "男生小说-游戏竞技"
    assert controller.book_rows["19480992"]["categoryPending"] is False

    # A book whose SEARCH_META never arrives must stop claiming to be loading.
    controller.events.put(("done", (controller.job_seq, 0)))
    controller._drain_events()
    assert controller.book_rows["3004323461"]["categoryPending"] is False
    assert controller.book_rows["3004323461"]["category"] == ""


def test_superseded_done_must_not_reset_live_state():
    controller = _controller()
    _spy_start(controller, [])
    controller.current_action = "search"
    controller.job_seq += 1
    controller.events.put(("done", (controller.job_seq - 1, 1)))
    controller._drain_events()
    assert controller.current_action == "search"


# ===== phase 2: probe / export / progress / log panel =====

def test_session_file_is_named_per_port_so_instances_do_not_clobber():
    """Two launches used to share one fixed filename, so the second overwrote the
    first instance's recovery hint and its user had no URL left to open."""
    first = web._session_file(40001)
    second = web._session_file(40002)
    assert first != second
    assert first.name.endswith("40001.txt") and second.name.endswith("40002.txt")
    assert web.LOG_DIR in first.parents

    with tempfile.TemporaryDirectory(prefix="weread-sess-") as temp:
        original = web.LOG_DIR
        web.LOG_DIR = Path(temp)
        try:
            session = web._Session(40003)
            web._write_session_file(session)
            mine = web._session_file(40003)
            other = web._session_file(40004)
            assert mine.is_file()
            assert not other.exists()
            body = mine.read_text(encoding="utf-8")
            assert f"port=40003" in body and session.token in body
            web._clear_session_file(40003)
            assert not mine.exists()
        finally:
            web.LOG_DIR = original


def test_probe_and_export_need_a_selected_book_first():
    controller = _controller()
    calls = []
    _spy_start(controller, calls)
    controller.probe()
    assert calls == [], calls
    assert "选择一本书" in controller.status, controller.status
    controller.export()
    assert calls == [], calls
    assert "选择一本书" in controller.status


def test_probe_and_export_reach_the_worker_with_the_gui_arguments():
    controller = _controller()
    calls = []
    _spy_start(controller, calls)
    controller._add_book_card(_row())
    controller.select("19480992")
    calls.clear()

    controller.probe()
    assert (["--probe-book", "19480992"], "probe") in calls, calls
    assert "第一章" in controller.status

    controller.process = _BusyProcess()
    calls.clear()
    controller.export()
    assert calls == [], "a busy worker must refuse a second job"
    assert "等它结束" in controller.status

    controller.process = None
    controller.set_jobs(3)
    controller.export()
    assert (["19480992", "--jobs", "3"], "export") in calls, calls
    assert controller.progress == 0.0
    # Concurrency is clamped to the range the GUI's combo box offered.
    controller.set_jobs(99)
    assert controller.jobs == web.MAX_JOBS
    controller.set_jobs(0)
    assert controller.jobs == 1
    controller.set_jobs("nonsense")
    assert controller.jobs == 1


def test_export_is_refused_while_another_action_runs():
    controller = _controller()
    calls = []
    _spy_start(controller, calls)
    controller._add_book_card(_row())
    controller.select("19480992")
    calls.clear()
    controller.process = _BusyProcess()
    controller.export()
    controller.probe()
    assert calls == [], calls
    assert "等它结束" in controller.status, controller.status


def test_pause_terminates_the_worker_and_keeps_the_progress():
    class _Terminable(_BusyProcess):
        terminated = False

        def terminate(self):
            type(self).terminated = True

    controller = _controller()
    _spy_start(controller, [])
    controller._add_book_card(_row())
    controller.select("19480992")
    controller.process = _Terminable()
    controller.progress = 0.42
    controller.pause()
    assert _Terminable.terminated is True
    assert "保留" in controller.status, controller.status
    assert controller.progress == 0.42, "pausing must not rewind the bar"
    # With nothing running, pause is a no-op rather than an error.
    controller.process = None
    before = controller.status
    controller.pause()
    assert controller.status == before


def test_chapter_progress_and_worker_protocol_lines_drive_the_status():
    controller = _controller()
    _spy_start(controller, [])
    controller._add_book_card(_row())
    controller.select("19480992")
    controller.process = _BusyProcess()
    controller.current_action = "export"
    job = controller.job_seq

    controller.events.put(("line", "[RESUME] Reusing 118 complete chapter(s) by UID; 890 remain"))
    controller.events.put(("line", "[3/1483] Fetching chapter UID 7"))
    controller._drain_events()
    assert controller.progress == 3 / 1483
    assert "第 3/1483" in controller.status, controller.status

    controller.events.put(("line", "[RETRY] chapter UID 991 attempt 1/3 failed; retrying in 5s"))
    controller._drain_events()
    assert "自动重试" in controller.status, controller.status
    # A [RETRY] line carrying a fraction must not be mistaken for a counter.
    assert controller.progress == 3 / 1483

    controller.events.put(("line", "[UNRESOLVED] chapter UID 115: paid; will continue"))
    controller._drain_events()
    assert controller.last_unresolved is True
    assert "无法安全确认" in controller.status

    controller.events.put(("line", "PROBE_OK; chapter_uid: 1"))
    controller._drain_events()
    assert "首章检查通过" in controller.status


def test_export_done_reports_the_three_verdicts():
    controller = _controller()

    def run_export(lines, code):
        _reset(controller)
        calls = []
        _spy_start(controller, calls)
        controller._add_book_card(_row())
        controller.select("19480992")
        controller.process = _BusyProcess()
        controller.current_action = "export"
        for line in lines:
            controller.events.put(("line", line))
        controller.events.put(("done", (controller.job_seq, code)))
        controller._drain_events()
        return controller.status

    assert "下载和文件校验完成" in run_export([], 0)
    assert "没有生成不完整 EPUB" in run_export(["[INCOMPLETE] 2 unresolved chapter(s) remain"], 1)
    assert "可稍后点“继续下载”" in run_export([], 1)
    # A FAILED line already put a friendly sentence in the status field; the
    # verdict must not overwrite it with a generic one. The raw worker text only
    # ever reaches the on-disk log.
    assert "连接微信读书超时" in run_export(["FAILED Timeout: read timed out"], 1)


def test_probe_done_reports_both_verdicts():
    controller = _controller()
    for code, expected in ((0, "首章检查通过"), (1, "第一章没能检查通过")):
        _reset(controller)
        _spy_start(controller, [])
        controller._add_book_card(_row())
        controller.select("19480992")
        controller.process = _BusyProcess()
        controller.current_action = "probe"
        controller.events.put(("done", (controller.job_seq, code)))
        controller._drain_events()
        assert expected in controller.status, (code, controller.status)


def test_last_unresolved_is_cleared_when_the_next_job_starts():
    controller = _controller()
    _spy_start(controller, [])
    controller._add_book_card(_row())
    controller.select("19480992")
    controller.process = _BusyProcess()
    controller.current_action = "export"
    controller.events.put(("line", "[UNRESOLVED] chapter UID 115: paid"))
    controller._drain_events()
    assert controller.last_unresolved is True
    controller.process = None
    controller.probe()
    assert controller.last_unresolved is False, "a stale verdict must not colour the next job"


def test_log_endpoint_serves_only_new_lines_and_signals_an_overrun():
    controller = server().controller
    _reset(controller)
    # The live controller is shared with the other HTTP tests, so start from
    # wherever its ring currently is rather than assuming an empty log.
    base = controller.log_seq
    controller._log("first line")
    controller._log("second line")

    status, _, body = _call(f"/api/log?since={base}", headers=_auth())
    assert status == 200, status
    payload = json.loads(body)
    assert payload["lines"] == ["first line", "second line"], payload
    assert payload["reset"] is False

    status, _, body = _call(f"/api/log?since={payload['seq']}", headers=_auth())
    assert json.loads(body)["lines"] == []

    # A client that fell further behind than the ring holds must be told to reset
    # rather than be handed a gap it would silently append onto.
    for index in range(web.LOG_LIMIT + 25):
        controller._log(f"flood {index}")
    status, _, body = _call(f"/api/log?since={base + 2}", headers=_auth())
    payload = json.loads(body)
    assert payload["reset"] is True, payload
    assert len(payload["lines"]) == web.LOG_LIMIT, len(payload["lines"])


def test_log_panel_never_shows_credentials():
    controller = _controller()
    # A raw worker line goes straight to the panel, so this is the path that
    # proves the panel redacts rather than only the file.
    controller.events.put(("line", "Authorization: Bearer abc123DEFG456 wr_skey=SECRETSKEY"))
    # A FAILED line never reaches the panel at all: only the friendly sentence
    # does, and the raw text goes to the on-disk log (redacted).
    controller.events.put(("line", "FAILED Timeout: read timed out"))
    controller._drain_events()
    joined = "\n".join(text for _seq, text in controller.log_lines)
    for secret in ("abc123DEFG456", "SECRETSKEY"):
        assert secret not in joined, joined
    assert "[已隐藏]" in joined, joined
    assert "连接微信读书超时" in joined, joined


def test_reveal_only_ever_opens_the_two_owned_folders():
    controller = _controller()
    opened = []
    original = web.os.startfile
    web.os.startfile = lambda path: opened.append(path)
    try:
        controller.reveal("output")
        controller.reveal("logs")
        assert len(opened) == 2, opened
        assert opened[0] == str(web.OUTPUT), opened
        assert opened[1] == str(controller.log_dir), opened
        # Anything else is refused, so no caller-supplied path can be opened.
        for hostile in ("..", "C:\\Windows", "output;calc", "", None, 0):
            opened.clear()
            controller.reveal(hostile)
            assert opened == [], (hostile, opened)
    finally:
        web.os.startfile = original


def test_probe_export_and_pause_endpoints_are_reachable_over_http():
    controller = server().controller
    _reset(controller)
    calls = []
    _spy_start(controller, calls)
    controller._add_book_card(_row())
    controller.select("19480992")
    calls.clear()

    status, _, body = _call("/api/probe", method="POST", body={}, headers=_auth())
    assert status == 200, status
    assert ["--probe-book", "19480992"] in [args for args, _a in calls], calls

    _call("/api/jobs", method="POST", body={"jobs": 2}, headers=_auth())
    assert json.loads(_call("/api/state", headers=_auth())[2])["state"]["jobs"] == 2

    status, _, _ = _call("/api/pause", method="POST", body={}, headers=_auth())
    assert status == 200, status
    status, _, _ = _call("/api/probe", method="POST", body={}, headers=_auth())
    assert status == 200, status
    status, _, _ = _call("/api/reveal", method="POST", body={"target": "nope"}, headers=_auth())
    assert status == 200, status


# ===== QR / manual login =====

def test_login_events_drive_the_state_machine():
    controller = _controller()
    # LOGIN_QR is staged by reading the worker's PNG, so the path has to exist.
    with tempfile.TemporaryDirectory(prefix="weread-login-fsm-") as temp:
        png = Path(temp) / "qr.png"
        png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"payload-bytes")
        controller._handle_login_line(f"LOGIN_QR {png}")
        assert controller.login["state"] == "qr"
        assert controller.login["qrSeq"] == 1
        assert "扫描" in controller.login["message"], controller.login["message"]
        assert controller.login["otpNeeded"] is False

        controller._handle_login_line("LOGIN_OTP 请输入手机上显示的 4 位验证码。")
        assert controller.login["state"] == "otp"
        assert controller.login["otpNeeded"] is True
        assert "验证码" in controller.login["message"]

        controller._handle_login_line("LOGIN_OK 软小五")
        assert controller.login["state"] == "done"
        assert controller.login["otpNeeded"] is False
        assert controller.account == "软小五"
        assert "登录成功" in controller.login["message"]
        # A late line from a finished attempt must not resurrect the old state.
        controller._handle_login_line("LOGIN_OTP 迟到的验证码请求")
        assert controller.login["state"] == "done"


def test_an_unreadable_qr_png_reports_an_error_instead_of_a_broken_image():
    """A vanished temp file must say so, not leave the page on a dead <img>."""
    controller = _controller()
    controller._handle_login_line(r"LOGIN_QR C:\definitely\not\here.png")
    assert controller.login["state"] == "error"
    assert "二维码" in controller.login["message"], controller.login["message"]
    assert controller.qr_png() is None
    assert controller.login["qrSeq"] == 1, "the attempt still consumed a sequence"


def test_login_failure_and_a_stray_exit_both_explain_themselves():
    controller = _controller()
    controller._handle_login_line("LOGIN_FAIL 二维码已过期，请点击“重新获取二维码”。")
    assert controller.login["state"] == "error"
    assert "过期" in controller.login["message"]

    controller._reset = None  # guard against accidental reuse
    fresh = _controller()
    fresh.login.update(state="qr", message="请扫码")
    fresh._handle_login_exit()
    assert fresh.login["state"] == "error"
    assert "意外结束" in fresh.login["message"]
    # A completed login is not overwritten by the exit event.
    done = _controller()
    done.login.update(state="done", message="登录成功")
    done._handle_login_exit()
    assert done.login["state"] == "done"


def test_otp_is_only_forwarded_to_a_live_login_and_empty_codes_are_refused():
    controller = _controller()
    controller.login.update(state="otp", otpNeeded=True)
    controller.submit_login_otp("")
    assert "请先输入" in controller.login["message"], controller.login["message"]

    written = []

    class _Live:
        def __init__(self):
            self.stdin = type("S", (), {"write": lambda _s, t: written.append(t),
                                         "flush": lambda _s: None})()
            self._code = None

        def poll(self):
            return self._code

    process = _Live()
    controller.login_process = process
    controller.submit_login_otp(" 1234 ")
    assert written == ["1234\n"], written
    assert controller.login["otpNeeded"] is False
    assert "校验" in controller.login["message"]

    # Nothing running: say so rather than writing into a dead pipe.
    written.clear()
    controller.login_process = None
    controller.submit_login_otp("1234")
    assert written == []
    assert "已经结束" in controller.login["message"], controller.login["message"]


def test_manual_credentials_mirror_the_gui_contract():
    controller = _controller()
    with tempfile.TemporaryDirectory(prefix="weread-manual-") as temp:
        target = Path(temp) / "session-cookies.json"
        original = web.SESSION_COOKIES
        web.SESSION_COOKIES = target
        try:
            controller.save_manual_credentials("", "skey")
            assert controller.login["state"] == "error"
            assert "API Key" in controller.login["message"], controller.login["message"]
            assert not target.exists(), "an incomplete save must not touch the file"

            controller.save_manual_credentials("wrk-abc", "")
            assert "wr_skey" in controller.login["message"], controller.login["message"]
            assert not target.exists()

            controller.save_manual_credentials("wrk-abc", "skey-value")
            assert controller.login["state"] == "done"
            assert controller.auth == "登录有效"
            state = json.loads(target.read_text(encoding="utf-8"))
            assert state["cookies"] == {"wr_skey": "skey-value"}
            assert state["api_key"] == "wrk-abc"
            assert state["account"]["login_method"] == "manual"
            assert controller.has_credentials() is True
        finally:
            web.SESSION_COOKIES = original


def test_qr_png_is_streamed_untouched_and_requires_the_session():
    # The HTTP call reaches the live server, so the QR has to be staged there.
    controller = server().controller
    _reset(controller)
    with tempfile.TemporaryDirectory(prefix="weread-qr-") as temp:
        png = Path(temp) / "qr.png"
        png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"payload-bytes")
        expected = png.read_bytes()
        controller._handle_login_line(f"LOGIN_QR {png}")

        assert controller.qr_png() == expected
        status, headers, body = _call("/api/login/qr.png", headers=_auth())
        assert status == 200, status
        assert headers["content-type"] == "image/png", headers
        assert body == expected, "the image must reach the page byte for byte"

        # The regression this design exists for: once staged, the bytes are held
        # in memory, so a temp file that becomes unreadable afterwards (antivirus,
        # a lingering worker handle) can no longer stop the page from showing it.
        png.unlink()
        status, _, body = _call("/api/login/qr.png", headers=_auth())
        assert status == 200, status
        assert body == expected

        # Same gate as every other endpoint.
        status, _, _ = _call("/api/login/qr.png")
        assert status == 401, status
        status, _, _ = _call("/api/login/qr.png", headers=_auth(extra={"Host": "evil.example"}))
        assert status == 403, status

        controller._drop_qr()
        assert not png.exists(), "the PNG must not be left in temp"
        assert controller.qr_png() is None
        status, _, _ = _call("/api/login/qr.png", headers=_auth())
        assert status == 404, status


def test_login_endpoints_are_reachable_over_http():
    controller = server().controller
    _reset(controller)
    calls = []
    _spy_start(controller, calls)
    # Pretend the worker answered so no real login is started from the test.
    controller.login_process = None
    for path, body in (("/api/login/cancel", {}),
                       ("/api/login/otp", {"code": "1234"}),
                       ("/api/login/manual", {"apiKey": "", "skey": ""})):
        status, _, _ = _call(path, method="POST", body=body, headers=_auth())
        assert status == 200, (path, status)
    _, _, raw = _call("/api/state", headers=_auth())
    state = json.loads(raw)["state"]
    assert "login" in state and "hasCredentials" in state


def test_auth_failure_points_at_the_login_dialog_instead_of_a_dead_end():
    controller = _controller()
    _spy_start(controller, [])
    controller.process = _BusyProcess()
    controller.current_action = "auth"
    controller.events.put(("done", (controller.job_seq, 1)))
    controller._drain_events()
    # Both branches must name a concrete next step, exactly like the GUI: with
    # stored credentials it says "re-scan", without them it says "log in".
    if controller.has_credentials():
        assert controller.auth == "检查失败", controller.auth
        assert "重新扫码" in controller.status, controller.status
    else:
        assert controller.auth == "需要登录", controller.auth
        assert "登录" in controller.status and "扫码" in controller.status, controller.status
        assert controller.login["message"], "the dialog needs a reason to open"


def test_web_bundle_never_gains_the_login_dependencies():
    """The QR flow exists, but requests/qrcode/PIL must stay in the worker.

    weread_login.py imports requests and renders the code with qrcode+PIL. If any
    of those became reachable from the web package, the frozen bundle would grow a
    native imaging extension again and lose the sub-second start.
    """
    probe = (
        "import importlib.util, sys\n"
        f"spec = importlib.util.spec_from_file_location('weread_web_probe', {str(SCRIPT)!r})\n"
        "module = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(module)\n"
        "banned = ('tkinter', '_tkinter', 'PIL', 'qrcode', 'customtkinter',\n"
        "          'requests', 'lxml', 'lupa', 'urllib3', 'certifi')\n"
        "print(','.join(sorted({k.split('.')[0] for k in sys.modules\n"
        "                      if k.split('.')[0] in banned})))\n"
    )
    result = subprocess.run([sys.executable, "-c", probe], capture_output=True,
                            text=True, cwd=str(HERE), timeout=120)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "", f"web mode pulled in {result.stdout.strip()}"


def test_page_wires_the_login_dialog_without_external_resources():
    _, _, page = _call("/", headers=_auth())
    text = page.decode("utf-8")
    for control in ("login-button", "login-dialog", "tab-qr", "tab-manual",
                    "qr-holder", "otp-row", "manual-api-key", "manual-skey",
                    "login-refresh", "manual-save"):
        assert f'id="{control}"' in text, control
    for path in ("/api/login/begin", "/api/login/otp", "/api/login/cancel",
                 "/api/login/manual", "/api/login/qr.png"):
        assert f"'{path}" in text, path
    # The same two channels the GUI offers, with the same field labels.
    assert "扫码登录" in text and "手动输入凭证" in text
    assert "wr_skey（必填）" in text and "API Key（必填）" in text
    assert "不进入日志或安装包" in text
    # Phase 1's "not available yet" wording must be gone now that it ships.
    assert "将在后续版本开放" not in text
    for pattern in (r"<script[^>]+\bsrc=", r"<link[^>]+stylesheet", r"@import",
                    r"\bon(?:click|error|input|load|change|keydown)\s*=", r"cdn\."):
        assert not re.search(pattern, text, re.I), pattern


def test_results_are_paged_so_a_long_list_does_not_rebuild_every_poll():
    """The web list must mount one page, not every result.

    Mounting every result meant rebuilding the whole list on each 800 ms poll,
    with one cover request per card. Paging bounds both, and matches the
    desktop app's behaviour.
    """
    _, _, page = _call("/", headers=_auth())
    text = page.decode("utf-8")
    for control in ("result-pager", "page-prev", "page-next", "page-label"):
        assert f'id="{control}"' in text, control
    assert "RESULTS_PER_PAGE" in text
    # The pager starts hidden and is only shown when there is more than one page.
    assert 'id="result-pager" hidden' in text
    assert "function turnPage" in text
    assert "pageIndex = 0;" in text, "a new search must reset to the first page"
    # Both buttons must actually be wired.
    assert "turnPage(-1)" in text and "turnPage(1)" in text
    # The render key must cover the page, not the whole result set: rebuilding
    # on every row of every page is what made the tab crawl.
    assert "JSON.stringify(page)" in text
    assert "JSON.stringify(rows)" not in text


def test_the_pager_clamps_when_results_shrink():
    """A stale page index must not leave the list blank."""
    _, _, page = _call("/", headers=_auth())
    text = page.decode("utf-8")
    assert "if (pageIndex > total - 1)" in text, "page index is not clamped"


def test_favicon_is_served_and_gated_like_everything_else():
    status, headers, body = _call("/favicon.ico", headers=_auth())
    assert status == 200, status
    assert headers["content-type"] == "image/x-icon", headers
    assert body[:4] == b"\x00\x00\x01\x00", body[:8]  # ICO magic
    # Same token and Host gate as every other route.
    status, _, _ = _call("/favicon.ico")
    assert status == 401, status
    status, _, _ = _call("/favicon.ico", headers=_auth(extra={"Host": "evil.example"}))
    assert status == 403, status
    _, _, page = _call("/", headers=_auth())
    text = page.decode("utf-8")
    assert '<link rel="icon" href="/favicon.ico"' in text, "the tab icon is never declared"


def test_the_stale_first_use_dialog_is_gone():
    """It still told users to hand-place weread-live-settings.lua, which the QR
    login made obsolete -- so it was removed rather than left to mislead."""
    _, _, page = _call("/", headers=_auth())
    text = page.decode("utf-8")
    assert "首次使用" not in text
    assert "help-dialog" not in text and "help-button" not in text
    # The stale *instruction* is what had to go. The logout confirm still has to
    # name weread-live-settings.lua, to say what it does not delete.
    assert "如果检查登录失败" not in text, "stale credential instructions remain"
    assert "凭证文件应位于" not in text, "stale credential instructions remain"
    # And the JS must not still try to bind listeners to the removed nodes,
    # which would throw on load and kill every handler on the page.
    assert "el('help-" not in text


def test_a_dead_local_service_is_reported_inside_the_login_dialog():
    """Regression: the failure text only reached the status bar, which a modal
    covers, so a dead server left the dialog on '正在获取二维码…' forever."""
    _, _, page = _call("/", headers=_auth())
    text = page.decode("utf-8")
    body = text.split("function reportFailure", 1)[1].split("function post", 1)[0]
    assert "login-dialog" in body, body
    assert "login-status" in body, body
    assert "无法连接本地服务" in body, body


# ===== logout =====

def test_logout_drops_the_qr_session_and_says_what_survives():
    controller = _controller()
    with tempfile.TemporaryDirectory(prefix="weread-logout-") as temp:
        target = Path(temp) / "session-cookies.json"
        original = web.SESSION_COOKIES
        web.SESSION_COOKIES = target
        try:
            controller.save_manual_credentials("wrk-abc", "skey-value")
            assert target.is_file()
            assert controller.has_credentials() is True

            controller.logout()
            assert not target.exists(), "the QR session overlay must be removed"
            assert controller.account == ""
            assert controller.auth == "需要登录"
            # The engine keeps weread-live-settings.lua as a mandatory base, so a
            # logout that claimed to sign the user out would be a lie.
            assert "weread-live-settings.lua" in controller.status, controller.status
            assert "仍保留" in controller.status, controller.status
            assert controller.has_credentials() is False
        finally:
            web.SESSION_COOKIES = original


def test_logout_without_a_stored_session_is_harmless():
    controller = _controller()
    with tempfile.TemporaryDirectory(prefix="weread-logout-") as temp:
        target = Path(temp) / "session-cookies.json"
        original = web.SESSION_COOKIES
        web.SESSION_COOKIES = target
        try:
            controller.logout()
            assert not target.exists()
            assert controller.auth == "需要登录"
            assert "没有可退出" in controller.status, controller.status
        finally:
            web.SESSION_COOKIES = original


def test_logout_endpoint_and_page_wiring():
    controller = server().controller
    _reset(controller)
    # The live endpoint deletes the real credential file, so point the module at a
    # temp path for the duration. Every other test redirects LOG_DIR/OUTPUT for
    # exactly this reason; logout must not be the one that writes through.
    with tempfile.TemporaryDirectory(prefix="weread-logout-http-") as temp:
        original = web.SESSION_COOKIES
        web.SESSION_COOKIES = Path(temp) / "session-cookies.json"
        try:
            web.SESSION_COOKIES.write_text(
                json.dumps({"cookies": {"wr_skey": "x"}, "api_key": "wrk-x",
                            "wr_ticket": "", "wr_wrpa": "",
                            "account": {"name": "someone"}}),
                encoding="utf-8")
            status, _, _ = _call("/api/logout", method="POST", body={}, headers=_auth())
            assert status == 200, status
            assert not web.SESSION_COOKIES.exists(), "the endpoint must delete the overlay"
        finally:
            web.SESSION_COOKIES = original
    _, _, page = _call("/", headers=_auth())
    text = page.decode("utf-8")
    for control in ("logout-button", "logout-dialog", "logout-confirm", "logout-cancel"):
        assert f'id="{control}"' in text, control
    assert "'/api/logout'" in text
    # The confirm has to name both files, or the user cannot tell what survives.
    assert "session-cookies.json" in text
    assert "weread-live-settings.lua" in text
    assert "不会被删除" in text


def test_page_wires_every_download_control_without_external_resources():
    _, _, page = _call("/", headers=_auth())
    text = page.decode("utf-8")
    for control in ("probe-button", "export-button", "pause-button", "jobs-select",
                    "open-output-button", "open-logs-button", "progress-fill"):
        assert f'id="{control}"' in text, control
    # The live log panel is gone, matching the desktop app: it duplicated
    # "打开日志文件夹" and polled /api/log once a second for text the log file
    # already holds.
    for gone in ('id="log-toggle"', 'id="log-panel"', 'id="log-body"'):
        assert gone not in text, gone
    assert "function setLogPanel" not in text, "dead log-panel JS is still present"
    # "function renderLogin(" begins with "function renderLog", so include the
    # opening paren to avoid matching the login renderer.
    assert "function renderLog(" not in text, "dead log rendering is still present"
    assert "function fetchLog" not in text, "the page still polls /api/log"
    # Opening the log file must still be reachable.
    assert 'id="open-logs-button"' in text
    for path in ("/api/probe", "/api/export", "/api/pause", "/api/jobs",
                 "/api/reveal", "/api/log"):
        assert f"'{path}" in text, path
    # Phase 1's "not available yet" wording must be gone now that it ships.
    assert "将在后续版本开放" not in text
    # Still nothing loaded from outside, and still no inline handlers.
    for pattern in (r"<script[^>]+\bsrc=", r"<link[^>]+stylesheet", r"@import",
                    r"\bon(?:click|error|input|load|change)\s*=", r"cdn\."):
        assert not re.search(pattern, text, re.I), pattern


def test_credentials_never_reach_the_log_file():
    controller = _controller()
    raw = ("Authorization: Bearer abc123DEFG456 apiKey=TOPSECRETKEY "
           "wr_skey=SECRETSKEY; wr_ticket=SECRETTICKET")
    controller.events.put(("line", "FAILED " + raw))
    controller._drain_events()
    written = "".join(path.read_text(encoding="utf-8")
                      for path in LOG_DIR.glob("desktop-*.log"))
    for secret in ("abc123DEFG456", "TOPSECRETKEY", "SECRETSKEY", "SECRETTICKET"):
        assert secret not in written, written
    assert "[已隐藏]" in written


def test_pump_survives_a_broken_event_and_keeps_draining():
    controller = _controller()
    _spy_start(controller, [])
    controller._add_book_card(_row())

    seen = []
    original = controller._handle_line

    def patched(value):
        seen.append(value)
        if len(seen) == 1:
            raise RuntimeError("event handler blew up")
        original(value)

    controller._handle_line = patched
    controller.events.put(("line", "AUTH_OK; shelf entries: 7"))
    controller.events.put(("line", "AUTH_OK; shelf entries: 8"))
    pump = threading.Thread(target=controller.pump, daemon=True)
    pump.start()
    deadline = time.time() + 10
    while time.time() < deadline and controller.auth != "登录有效":
        time.sleep(0.02)
    controller.stop()
    pump.join(timeout=5)

    assert len(seen) == 2, seen
    assert controller.auth == "登录有效"
    assert "[CONTROLLER_ERROR]" in "".join(
        path.read_text(encoding="utf-8") for path in LOG_DIR.glob("desktop-*.log"))


def test_source_stays_free_of_gui_and_engine_libraries():
    """The whole point of web mode: a frozen bundle with no native GUI extension."""
    probe = (
        "import importlib.util, sys\n"
        f"spec = importlib.util.spec_from_file_location('weread_web_probe', {str(SCRIPT)!r})\n"
        "module = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(module)\n"
        "banned = ('tkinter', '_tkinter', 'PIL', 'customtkinter', 'darkdetect',\n"
        "          'requests', 'lxml', 'lupa')\n"
        "print(','.join(sorted({k.split('.')[0] for k in sys.modules\n"
        "                      if k.split('.')[0] in banned})))\n"
    )
    result = subprocess.run([sys.executable, "-c", probe], capture_output=True,
                            text=True, cwd=str(HERE), timeout=120)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "", f"web mode pulled in {result.stdout.strip()}"


ALL_TESTS = [
    test_host_header_must_name_this_loopback_port,
    test_every_request_needs_this_launch_s_token,
    test_cross_site_posts_are_refused,
    test_launch_url_trades_the_token_for_a_samesite_cookie,
    test_stale_session_cookies_from_earlier_launches_do_not_lock_the_page_out,
    test_launch_url_still_hands_over_the_cookie_when_stale_ones_are_present,
    test_page_script_nonce_matches_the_csp_header,
    test_page_pulls_in_nothing_external,
    test_state_endpoint_exposes_the_documented_shape,
    test_search_select_flow_reaches_the_browser_as_json,
    test_auth_endpoint_reports_the_worker_verdict,
    test_unknown_route_and_oversized_body_are_handled,
    test_select_while_busy_queues_the_detail_request,
    test_second_book_while_the_first_is_loading_supersedes_the_queue,
    test_search_and_auth_are_refused_while_busy_without_losing_state,
    test_failed_book_info_restores_the_row_placeholders,
    test_failed_book_info_never_resurrects_an_epoch_date,
    test_stale_book_info_for_another_book_is_ignored,
    test_malformed_book_info_never_breaks_the_message_loop,
    test_empty_and_failed_searches_clear_the_loading_counter,
    test_search_meta_fills_categories_and_done_stops_the_pending_flag,
    test_superseded_done_must_not_reset_live_state,
    test_probe_and_export_need_a_selected_book_first,
    test_session_file_is_named_per_port_so_instances_do_not_clobber,
    test_probe_and_export_reach_the_worker_with_the_gui_arguments,
    test_export_is_refused_while_another_action_runs,
    test_pause_terminates_the_worker_and_keeps_the_progress,
    test_chapter_progress_and_worker_protocol_lines_drive_the_status,
    test_export_done_reports_the_three_verdicts,
    test_probe_done_reports_both_verdicts,
    test_last_unresolved_is_cleared_when_the_next_job_starts,
    test_log_endpoint_serves_only_new_lines_and_signals_an_overrun,
    test_log_panel_never_shows_credentials,
    test_reveal_only_ever_opens_the_two_owned_folders,
    test_probe_export_and_pause_endpoints_are_reachable_over_http,
    test_page_wires_every_download_control_without_external_resources,
    test_login_events_drive_the_state_machine,
    test_login_failure_and_a_stray_exit_both_explain_themselves,
    test_otp_is_only_forwarded_to_a_live_login_and_empty_codes_are_refused,
    test_manual_credentials_mirror_the_gui_contract,
    test_qr_png_is_streamed_untouched_and_requires_the_session,
    test_an_unreadable_qr_png_reports_an_error_instead_of_a_broken_image,
    test_login_endpoints_are_reachable_over_http,
    test_auth_failure_points_at_the_login_dialog_instead_of_a_dead_end,
    test_web_bundle_never_gains_the_login_dependencies,
    test_page_wires_the_login_dialog_without_external_resources,
    test_favicon_is_served_and_gated_like_everything_else,
    test_the_stale_first_use_dialog_is_gone,
    test_a_dead_local_service_is_reported_inside_the_login_dialog,
    test_logout_drops_the_qr_session_and_says_what_survives,
    test_logout_without_a_stored_session_is_harmless,
    test_logout_endpoint_and_page_wiring,
    test_credentials_never_reach_the_log_file,
    test_pump_survives_a_broken_event_and_keeps_draining,
    test_source_stays_free_of_gui_and_engine_libraries,
    test_results_are_paged_so_a_long_list_does_not_rebuild_every_poll,
    test_the_pager_clamps_when_results_shrink,
]


if __name__ == "__main__":
    failed = 0
    for test in ALL_TESTS:
        try:
            test()
        except Exception as exc:
            failed += 1
            print(f"FAIL {test.__name__}: {type(exc).__name__}: {exc}")
        else:
            print("PASS", test.__name__)
    print(f"\n{len(ALL_TESTS) - failed}/{len(ALL_TESTS)} passed")
    raise SystemExit(1 if failed else 0)
