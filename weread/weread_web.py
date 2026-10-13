#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Web interface mode for WeRead desktop exports.

The browser draws the UI; this process only speaks HTTP. That is the whole
point of the mode: a bundle containing PIL, tkinter, _tkinter or the Tcl DLLs
makes the frozen executable sit in a multi-second AV deep scan before its first
line of user code runs (measured 5.2-6.4 s frozen versus 0.108 s for a pure
stdlib http.server), while a stdlib-only bundle starts in well under a second.

So nothing here may import a GUI toolkit or an imaging library. The download
engine still runs in the separate hidden worker process, driven over UTF-8
stdio exactly as the CustomTkinter GUI drives it, and no WeRead credential is
bundled here.

Covers are loaded by the browser straight from the WeRead CDN, so the server
never touches image bytes either -- a cover that fails to load degrades to a
placeholder in the page instead of an exception, mirroring the GUI's "a broken
cover must not take the app down" behaviour.
"""
from __future__ import annotations

import collections
import json
import os
import queue
import re
import secrets
import subprocess
import sys
import threading
import time
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.append(str(_HERE))

from weread_core import (  # noqa: E402
    BUNDLE,
    LOG_DIR,
    OUTPUT,
    PRIVATE,
    WORKER_NAME,
    book_detail_lines,
    card_rating_text,
    friendly_error,
    is_first_instance,
    parse_book_info_message,
    parse_search_meta,
    parse_search_row,
    rating_text,
    redact_secrets,
    worker_command,
)

UI_NAME = "webui.html"
FAVICON_NAME = "weread-web.ico"
UI_NONCE_TOKEN = "__CSP_NONCE__"
MAX_BODY_BYTES = 64 * 1024
LOG_LIMIT = 600
MAX_JOBS = 4
# The credential overlay the engine merges over weread-live-settings.lua. The web
# bundle reads and writes it with nothing but json: weread_login.py itself pulls
# in requests/qrcode/PIL, which is exactly what must stay out of this package.
SESSION_COOKIES = PRIVATE / "session-cookies.json"


class WebController:
    """Headless port of DesktopApp's logic layer.

    The GUI mutates Tk widgets in place; here every field is plain data that the
    browser re-reads as a JSON snapshot. The bookkeeping that took real bugs to
    find is preserved one-for-one: _is_busy() refusing a second job,
    pending_book queueing a detail request behind a running worker, the
    try/finally in _handle_done(), the FAILED path unsticking placeholders, and
    the stale-BOOK_INFO bookId guard.

    Every worker action the GUI can start is wired here -- search, book-info,
    auth, probe and export -- so no action exists in the page that the
    controller cannot serve.
    """

    def __init__(self, log_dir: Path) -> None:
        self.log_dir = Path(log_dir)
        self.lock = threading.RLock()
        self.events: "queue.Queue[tuple]" = queue.Queue()
        self._stop = threading.Event()

        self.process: subprocess.Popen | None = None
        self.reader: threading.Thread | None = None
        self.current_action = ""
        self.job_seq = 0
        self.last_failure = False
        self.last_unresolved = False

        self.book_rows: dict[str, dict] = {}
        self._category_done: set[str] = set()
        self.result_count = "搜索结果会显示在这里"
        self.status = "准备就绪。第一步：检查登录状态。"
        self.auth = "尚未检查"
        self.progress = 0.0
        self.jobs = 1
        # Ideas are off by default: on a heavily annotated book the readreviews
        # batches are the single slowest part of an export, and most readers only
        # open them occasionally. Underlines are never affected by this switch.
        self.want_thoughts = False
        # (seq, text) newest last, so the page can ask for only what it has not
        # seen instead of re-downloading the whole panel on every poll.
        self.log_lines: collections.deque = collections.deque(maxlen=LOG_LIMIT)
        self.log_seq = 0

        self.selected: dict = {
            "bookId": "", "title": "", "author": "", "cover": "",
            "categoryText": "", "ratingText": "", "intro": "",
            "infoLoaded": False, "pending": False, "loading": False,
        }
        self.book_info: dict = {}
        self.pending_book: dict | None = None
        self.info_loaded = False

        # QR login runs in the worker (it owns requests/qrcode/PIL); this side only
        # supervises the subprocess and republishes its line events as state.
        self.login_process: subprocess.Popen | None = None
        self.login_reader: threading.Thread | None = None
        self._qr_path: str = ""
        self._qr_bytes: bytes | None = None
        self.login: dict = {
            "state": "idle",   # idle | starting | qr | otp | done | error
            "message": "",
            "otpNeeded": False,
            "qrSeq": 0,
        }
        self.account = self._stored_account()

    # ----- credentials overlay (json only, no weread_login import) -----

    def _stored_credentials(self) -> dict:
        try:
            state = json.loads(SESSION_COOKIES.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return state if isinstance(state, dict) else {}

    def _stored_account(self) -> str:
        account = self._stored_credentials().get("account")
        if isinstance(account, dict):
            return str(account.get("name") or "")
        return ""

    def has_credentials(self) -> bool:
        """Mirror of weread_login.has_credentials(): both halves the engine wants."""
        stored = self._stored_credentials()
        cookies = stored.get("cookies")
        skey = cookies.get("wr_skey") if isinstance(cookies, dict) else None
        return bool(stored.get("api_key")) and bool(skey)

    def _write_credentials(self, payload: dict) -> None:
        SESSION_COOKIES.parent.mkdir(parents=True, exist_ok=True)
        temp = SESSION_COOKIES.with_suffix(SESSION_COOKIES.suffix + ".tmp")
        temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp, SESSION_COOKIES)

    def save_manual_credentials(self, api_key: str, skey: str) -> None:
        """The manual channel: two fields, no network, therefore no third-party import.

        Field names, validation order and the written schema match the GUI's
        manual tab exactly, so either frontend can read what the other wrote.
        """
        api_key = str(api_key or "").strip()
        skey = str(skey or "").strip()
        if not api_key:
            self.login.update(state="error", message="请填写 API Key。", otpNeeded=False)
            return
        if not skey:
            self.login.update(state="error",
                              message="请填写 wr_skey。缺少它无法下载章节正文。",
                              otpNeeded=False)
            return
        try:
            self._write_credentials({
                "cookies": {"wr_skey": skey},
                "api_key": api_key,
                "wr_ticket": "",
                "wr_wrpa": "",
                "account": {"name": "", "user_vid": "", "login_method": "manual",
                            "login_time": int(time.time())},
            })
        except OSError as exc:
            self.login.update(state="error", message=f"保存失败：{exc}", otpNeeded=False)
            return
        self.account = ""
        self.auth = "登录有效"
        self.login.update(state="done", message="凭证已保存。现在可以检查登录状态。",
                          otpNeeded=False)

    # ----- QR login supervision -------------------------------------

    def _drop_qr(self) -> None:
        path, self._qr_path = self._qr_path, ""
        self._qr_bytes = None
        if not path:
            return
        try:
            Path(path).unlink()
        except OSError:
            pass

    def _stop_login_process(self) -> None:
        process = self.login_process
        self.login_process = None
        if process is None or process.poll() is not None:
            return
        try:
            if process.stdin is not None:
                process.stdin.write("cancel\n")
                process.stdin.flush()
                process.stdin.close()
        except (OSError, ValueError):
            pass
        process.terminate()
        try:
            process.wait(timeout=5)
        except Exception:
            pass

    def start_login(self) -> None:
        with self.lock:
            self._stop_login_process()
            self._drop_qr()
            command = worker_command(["--login"])
            if command is None:
                self.login.update(state="error", otpNeeded=False,
                                  message=f"没有找到 {WORKER_NAME}。请确认程序目录完整"
                                           "（WeReadDesktop 与 WeReadWorker 同一层），或重新解压一次。")
                return
            env = os.environ.copy()
            env["PYTHONIOENCODING"] = "utf-8"
            try:
                self.login_process = subprocess.Popen(
                    command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                    errors="replace", bufsize=1, env=env,
                    cwd=str(Path(command[0]).parent),
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except OSError as exc:
                self._persist_log(f"[LOGIN_START_ERROR] {type(exc).__name__}: {exc}")
                self.login_process = None
                self.login.update(state="error", otpNeeded=False,
                                  message="登录组件没能启动。请重新解压程序后重试。")
                return
            self.login.update(state="starting", message="正在获取二维码…", otpNeeded=False)
            self.login_reader = threading.Thread(target=self._read_login_output,
                                                args=(self.login_process,), daemon=True)
            self.login_reader.start()

    def _read_login_output(self, process) -> None:
        try:
            if process.stdout is not None:
                for raw in process.stdout:
                    self._handle_login_line(raw.rstrip("\r\n"))
        except Exception:
            self._persist_log("[LOGIN_STREAM_ERROR] " + redact_secrets(
                "".join(traceback.format_exception(*sys.exc_info()))))
        finally:
            try:
                process.wait()
            except Exception:
                pass
            self.events.put(("login-exit", 0))

    def _handle_login_line(self, line: str) -> None:
        with self.lock:
            if self.login["state"] == "done":
                # A finished login must not be walked backwards by a line that was
                # already in flight when the credentials landed.
                return
        verb, _, rest = line.partition(" ")
        if verb == "LOGIN_QR":
            path = rest.strip()
            # Read the PNG once, here, while the worker still owns a closed
            # handle on it. Serving the bytes from memory afterwards means the
            # page can re-fetch as often as it likes without racing a temp file
            # that antivirus or a lingering handle could make unreadable.
            try:
                payload = Path(path).read_bytes()
            except OSError as exc:
                self._persist_log(f"[QR_READ_ERROR] {type(exc).__name__}: {exc}")
                payload = None
            with self.lock:
                self._drop_qr()
                self._qr_path = path
                self._qr_bytes = payload
                self.login["qrSeq"] += 1
                if payload is None:
                    self.login.update(state="error", otpNeeded=False,
                                      message="二维码图片读取失败，请点“重新获取二维码”。")
                else:
                    self.login.update(state="qr", otpNeeded=False,
                                      message="请用手机微信读书扫描下方二维码。")
        elif verb == "LOGIN_OTP":
            with self.lock:
                self.login.update(state="otp", otpNeeded=True,
                                  message=rest.strip() or "请输入手机上的 4 位验证码。")
        elif verb == "LOGIN_OK":
            with self.lock:
                self._drop_qr()
                self.account = rest.strip()
                self.login.update(state="done", otpNeeded=False,
                                  message=f"登录成功：{self.account}" if self.account
                                           else "登录成功。现在可以检查登录状态。")
        elif verb == "LOGIN_FAIL":
            with self.lock:
                self._drop_qr()
                self.login.update(state="error", otpNeeded=False,
                                  message=rest.strip() or "扫码登录没有成功。")

    def _handle_login_exit(self) -> None:
        with self.lock:
            if self.login["state"] in ("done", "error"):
                return
            if self.login["state"] != "idle":
                self.login.update(state="error", otpNeeded=False,
                                  message="登录组件意外结束，请点击“重新获取二维码”。")

    def submit_login_otp(self, code: str) -> None:
        code = str(code or "").strip()
        with self.lock:
            process = self.login_process
            if not code:
                self.login["message"] = "请先输入手机上的 4 位验证码。"
                return
            if process is None or process.poll() is not None:
                self.login["message"] = "登录已经结束，请重新获取二维码。"
                return
            try:
                process.stdin.write(code + "\n")
                process.stdin.flush()
            except (OSError, ValueError):
                self.login["message"] = "验证码没有送达到登录组件，请重新获取二维码。"
                return
            self.login.update(message="正在校验验证码…", otpNeeded=False)

    def cancel_login(self) -> None:
        with self.lock:
            self._stop_login_process()
            self._drop_qr()
            if self.login["state"] not in ("done", "error"):
                self.login.update(state="idle", message="", otpNeeded=False)

    def qr_png(self) -> bytes | None:
        """Hand the worker's PNG over untouched.

        The bytes are never decoded here: the browser does that, which is how the
        page can show a QR code while this package stays free of PIL. They were
        read once when the worker reported the path, so this never touches the
        filesystem and cannot fail on a temp file that has become unreadable.
        """
        with self.lock:
            return self._qr_bytes

    def logout(self) -> None:
        """Drop the QR session overlay -- and be honest about what survives.

        The engine treats weread-live-settings.lua as a mandatory base and only
        merges session-cookies.json over it, so deleting the JSON is a *refresh
        reset*, not a full logout: the base file keeps its own session. Saying so
        is the whole point, otherwise "退出登录" would leave the user still signed
        in and looking for a button that still says they are.
        """
        if not SESSION_COOKIES.is_file():
            self.account = ""
            self.auth = "需要登录"
            self.status = "当前没有可退出的扫码登录会话。"
            return
        try:
            # Deliberately not weread_login.clear_credentials(): importing
            # weread_login here would pull requests/qrcode/PIL into the web
            # bundle, which stays json-only on purpose (see SESSION_COOKIES).
            SESSION_COOKIES.unlink()
        except OSError as exc:
            self._persist_log(f"[LOGOUT_ERROR] {type(exc).__name__}: {exc}")
            self.status = f"退出登录失败：{exc}"
            return
        self.account = ""
        self.auth = "需要登录"
        self.login.update(state="idle", message="", otpNeeded=False)
        self.status = ("已退出扫码登录。本机的 weread-live-settings.lua 仍保留着一份会话，"
                       "要彻底注销请自行删除该文件。")

    def _report_login_problem(self) -> None:
        """Turn a failed auth check into one concrete next step, like the GUI."""
        self.auth = "检查失败"
        if self.has_credentials():
            self.status = "凭证已过期或无效。点顶栏的账号名按钮重新扫码。"
            self.login["message"] = ""
            return
        self.auth = "需要登录"
        self.status = "还没有登录凭证。点“登录 / 切换账号”扫码，或手动输入 API Key。"
        self.login["message"] = "这台电脑还没有微信读书登录凭证。请扫码，或手动输入 API Key。"

    # ----- logging -------------------------------------------------

    def _persist_log(self, text: str) -> None:
        try:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            safe_id = self.selected.get("bookId") or "general"
            with (self.log_dir / f"desktop-{safe_id}.log").open("a", encoding="utf-8") as stream:
                stream.write(redact_secrets(text) + "\n")
        except OSError:
            pass

    def _log(self, text: str) -> None:
        # Redact before anything reaches the page, not just the file: the panel is
        # what a user is most likely to paste into a support thread.
        scrubbed = redact_secrets(text)
        self._persist_log(scrubbed)
        self.log_seq += 1
        self.log_lines.append((self.log_seq, scrubbed))

    def log_since(self, seq: int) -> dict:
        """Only the lines the page has not seen, plus a reset flag if it fell behind."""
        with self.lock:
            try:
                since = int(seq)
            except (TypeError, ValueError):
                since = 0
            entries = list(self.log_lines)
            # The ring dropped lines this client never got, so it must clear its
            # panel instead of appending onto a gap it cannot see. A client that
            # has seen nothing yet (since=0) simply gets everything.
            reset = bool(entries) and since > 0 and since < entries[0][0] - 1
            return {
                "lines": [text for entry_seq, text in entries if entry_seq > since or reset],
                "reset": reset,
                "seq": self.log_seq,
            }

    # ----- worker process -----------------------------------------

    def _is_busy(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def _start_worker(self, args: list[str], action: str, status: str) -> bool:
        if self._is_busy():
            self.status = "当前还有一个任务在运行，请等它结束再操作。"
            return False
        command = worker_command(args)
        if command is None:
            self._log(f"下载组件未找到：{WORKER_NAME}")
            self.status = (f"没有找到 {WORKER_NAME}。请确认程序目录完整"
                           "（WeReadDesktop 与 WeReadWorker 两个文件夹都在同一层），"
                           "或重新解压一次程序。")
            return False
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        try:
            self.process = subprocess.Popen(
                command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", bufsize=1, env=env,
                cwd=str(Path(command[0]).parent),
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except OSError as exc:
            self._persist_log(f"[WORKER_START_ERROR] {type(exc).__name__}: {exc}")
            self.status = "程序没能启动下载组件。请重新解压程序后重试。"
            return False
        self.job_seq += 1
        job = self.job_seq
        self.current_action = action
        self.last_failure = False
        self.last_unresolved = False
        self.status = status
        self.reader = threading.Thread(target=self._read_output, args=(self.process, job), daemon=True)
        self.reader.start()
        return True

    def _read_output(self, process, job: int) -> None:
        try:
            if process.stdout is not None:
                for line in process.stdout:
                    self.events.put(("line", line.rstrip("\r\n")))
        finally:
            try:
                code = process.wait()
            except Exception:
                code = -1
            self.events.put(("done", (job, code)))

    def pump(self) -> None:
        """Drain worker output on one thread; the GUI did this in root.after()."""
        while not self._stop.is_set():
            try:
                self._drain_events()
            except Exception:
                self._persist_log("[CONTROLLER_ERROR] " + redact_secrets(
                    "".join(traceback.format_exception(*sys.exc_info()))))
            self._stop.wait(0.05)

    def _drain_events(self) -> None:
        with self.lock:
            while True:
                try:
                    kind, value = self.events.get_nowait()
                except queue.Empty:
                    break
                if kind == "done":
                    self._handle_done(*value)
                elif kind == "login-exit":
                    self._handle_login_exit()
                else:
                    self._handle_line(value)

    def stop(self) -> None:
        self._stop.set()
        with self.lock:
            self._stop_login_process()
            process = self.process
        if process is not None and process.poll() is None:
            # Same contract as the GUI's pause button: chapters already written
            # stay on disk and the next run resumes them by UID.
            process.terminate()
            try:
                process.wait(timeout=5)
            except Exception:
                pass

    # ----- worker output -> user facing state ---------------------

    def _handle_line(self, value: str) -> None:
        info = parse_book_info_message(value)
        if info is not None:
            self._persist_log("Book metadata received.")
            self._show_book_info(info)
            return
        if value.startswith("BOOK_INFO"):
            self._persist_log("[BOOK_INFO_PARSE_ERROR] " + value[:200])
            self.status = "书籍详情暂时读不到；仍可以继续检查或下载。"
            return
        meta = parse_search_meta(value)
        if meta is not None:
            self._persist_log("Search metadata received.")
            self._update_card_category(*meta)
            return
        if value.startswith("FAILED "):
            self.last_failure = True
            self._persist_log(value)
            friendly = friendly_error(value)
            self._log("本次操作未完成：" + friendly)
            self.status = friendly
            return
        self._log(value)
        if self.current_action == "search":
            row = parse_search_row(value)
            if row is not None:
                self._add_book_card(row)
        if value.startswith("AUTH_OK"):
            # The GUI shows this the moment the line arrives rather than waiting
            # for the worker to exit, so the badge stays responsive.
            self.auth = "登录有效"
            self.status = "登录状态正常，可以搜索书籍。"
        if value.startswith("[RESUME]"):
            self.status = value.replace("[RESUME]", "已找到本地进度：", 1).strip()
        if value.startswith("[") and "/" in value:
            try:
                current, total = value[1:].split("]", 1)[0].split("/", 1)
                self.progress = int(current) / max(1, int(total))
                self.status = f"正在下载第 {current}/{total} 个目录章节；本地断点会自动复用。"
            except ValueError:
                pass  # a [RETRY] line with a fraction in it is not a chapter counter
        if value.startswith("[RETRY]"):
            self.status = "网络或数据校验出现短暂问题，程序正在自动重试。"
        if value.startswith("[UNRESOLVED]"):
            self.last_unresolved = True
            self.status = "有一章的正文或划线无法安全确认。已记录并继续检查其它章节。"
        if value.startswith("[INCOMPLETE]"):
            self.last_unresolved = True
            self.status = "扫描完成，但仍有章节需要处理，所以没有生成不完整 EPUB。点击“查看技术日志”可查看章节 UID。"
        if value.startswith("PROBE_OK;"):
            self.status = "首章检查通过，可以开始下载。"

    def _handle_done(self, job: int, code: int) -> None:
        if job != self.job_seq:
            return  # a finished, superseded worker must not reset live state
        action = self.current_action
        self.process = None
        self.current_action = ""
        try:
            if self.last_failure:
                self._finish_failed_action(action)
            elif action == "search":
                if code != 0:
                    self.status = "搜索没有完成。请稍后再试，或点“检查登录”确认登录状态。"
                elif not self.book_rows:
                    self.status = "没有找到匹配的书籍。换一个书名或作者关键词试试。"
                else:
                    self.status = f"找到 {len(self.book_rows)} 本书。点击左侧任意一本查看详情。"
                if not self.book_rows:
                    self.result_count = "没有找到匹配的书籍" if code == 0 else "搜索没有完成；请稍后再试"
                self._finish_card_categories()
            elif action == "book-info":
                if not self.info_loaded:
                    self._restore_detail_placeholders()
                    self.status = "书籍详情暂时读不到；仍可以继续检查或下载。"
                elif code != 0:
                    self.status = "书籍详情没能加载出来。你仍可以点“先检查第一章”或直接开始下载。"
            elif action == "auth":
                if code == 0:
                    self.auth = "登录有效"
                    self.account = self._stored_account() or self.account
                    self.status = "登录状态正常，可以搜索书籍。"
                else:
                    self._report_login_problem()
            elif action == "probe":
                if code == 0:
                    self.status = "首章检查通过，可以开始下载。"
                else:
                    self.status = "第一章没能检查通过。详情和技术日志里有具体原因，断点不会丢失。"
            elif action == "export":
                if code == 0:
                    self.status = "下载和文件校验完成。"
                elif self.last_unresolved:
                    self.status = "扫描到末尾，但有章节数据不完整；没有生成不完整 EPUB。点击“查看技术日志”查看具体章节。"
                else:
                    self.status = "下载暂时停止；已完成章节已保存，可稍后点“继续下载”。"
        finally:
            self._flush_pending_book()

    def _finish_failed_action(self, action: str) -> None:
        # The FAILED line already put a friendly message in the status field;
        # here we only unstick the placeholders that would otherwise stay loading.
        if action == "search":
            self._finish_card_categories()
            if not self.book_rows:
                self.result_count = "搜索没有完成；请稍后再试"
        elif action == "book-info":
            if not self.info_loaded:
                self._restore_detail_placeholders()
        elif action == "auth":
            self.auth = "检查失败"

    # ----- search results -----------------------------------------

    def _add_book_card(self, item: dict) -> None:
        book_id = str(item.get("bookId") or "")
        if not book_id:
            return
        row = dict(item)
        # WeRead ships the category in a separate SEARCH_META line, so an empty
        # category here means "still loading", exactly like the GUI's label.
        row["categoryPending"] = not str(row.get("category") or "").strip()
        self.book_rows[book_id] = row
        self.result_count = f"找到 {len(self.book_rows)} 本书 · 点击一项查看详情"

    def _update_card_category(self, book_id: str, category: str) -> None:
        row = self.book_rows.get(book_id)
        if row is None:
            return
        row["category"] = category
        row["categoryPending"] = False
        self._category_done.add(book_id)

    def _finish_card_categories(self) -> None:
        for book_id, row in self.book_rows.items():
            if book_id not in self._category_done:
                row["categoryPending"] = False

    # ----- selected book ------------------------------------------

    def select(self, book_id: str) -> None:
        item = self.book_rows.get(str(book_id or ""))
        if item is None:
            self.status = "请先搜索，并从结果中选择一本书。"
            return
        self.selected["bookId"] = str(item.get("bookId", ""))
        self.selected["title"] = str(item.get("title", ""))
        self.selected["author"] = str(item.get("author", ""))
        self.selected["cover"] = str(item.get("cover") or "")
        self.selected["categoryText"] = ""
        self.selected["ratingText"] = ""
        self.selected["intro"] = ""
        self.selected["infoLoaded"] = False
        self.selected["pending"] = False
        self.selected["loading"] = False
        self.info_loaded = False
        self.book_info = dict(item)
        if self._is_busy():
            self.pending_book = dict(item)
            self.selected["pending"] = True
            self.selected["categoryText"] = "详情已排队，当前任务结束后自动加载"
            self.selected["intro"] = "当前有任务正在运行；这本书的详情会在它结束后自动加载。"
            self.status = "已选择书籍；详情已排队，当前任务结束后自动加载。"
            return
        self.pending_book = None
        self._start_book_info(item)

    def _start_book_info(self, item: dict) -> bool:
        self.selected["loading"] = True
        self.selected["categoryText"] = "正在读取分类、评分、简介和封面…"
        self.selected["ratingText"] = ""
        self.selected["intro"] = "正在读取书籍简介…"
        self.status = "已选择书籍，正在加载详情。"
        started = False
        try:
            started = self._start_worker(
                ["--book-info", str(item.get("bookId", ""))], "book-info", "正在加载书籍详情…")
        except Exception as exc:
            self._persist_log(f"[BOOK_INFO_START_ERROR] {type(exc).__name__}: {exc}")
        if not started:
            self._restore_detail_placeholders()
            self.status = "详情查询没有启动。请查看技术日志后重试。"
        return started

    def _restore_detail_placeholders(self) -> None:
        info = self.book_info or {}
        self.selected["categoryText"] = "    ·    ".join(book_detail_lines(info))
        self.selected["ratingText"] = (rating_text(info.get("newRating"), info.get("newRatingCount"))
                                       or "评分：暂无信息")
        self.selected["intro"] = "详情暂时没有读取到；可稍后重试，或直接点“先检查第一章”。"

    def _flush_pending_book(self) -> None:
        item = self.pending_book
        if item is None:
            return
        if self._is_busy():
            return
        self.pending_book = None
        self.selected["pending"] = False
        self._start_book_info(item)

    def _show_book_info(self, data: dict) -> None:
        payload_id = str(data.get("bookId") or "").strip()
        if payload_id and self.selected["bookId"] and payload_id != self.selected["bookId"]:
            self._persist_log(f"[BOOK_INFO_STALE] ignored payload for {payload_id}; "
                              f"{self.selected['bookId']} selected")
            return
        self.book_info.update(data)
        self.selected["title"] = str(data.get("title") or "").strip() or self.selected["title"]
        self.selected["author"] = str(data.get("author") or "").strip() or self.selected["author"]
        self.selected["categoryText"] = "    ·    ".join(book_detail_lines(data))
        self.selected["ratingText"] = (rating_text(data.get("newRating"), data.get("newRatingCount"))
                                       or "评分：暂无信息")
        self.selected["bookId"] = str(data.get("bookId") or self.selected["bookId"])
        self.selected["cover"] = str(data.get("cover") or "").strip() or self.selected["cover"]
        intro = str(data.get("intro") or "").strip()
        self.selected["intro"] = intro or "暂无简介。"
        self.status = "书籍详情已加载。请核对封面、书名和作者后再下载。"
        self.info_loaded = True
        self.selected["infoLoaded"] = True
        self.selected["loading"] = False

    # ----- entry points --------------------------------------------

    def search(self, keyword: str) -> None:
        keyword = str(keyword or "").strip()
        if not keyword:
            self.status = "请在搜索框输入书名或关键词。"
            return
        if self._is_busy():
            self.status = "当前有任务正在运行，等它结束再搜索。"
            return
        self.book_rows.clear()
        self._category_done.clear()
        self.result_count = "正在搜索微信读书…"
        self.status = "正在搜索。稍等片刻，结果会出现在左侧。"
        started = False
        try:
            started = self._start_worker(["--search", keyword], "search", "正在搜索…")
        except Exception as exc:
            self._persist_log(f"[SEARCH_START_ERROR] {type(exc).__name__}: {exc}")
        if not started:
            self.result_count = "搜索没有启动；请稍后再试"
            self.status = "搜索没有启动。请查看技术日志后重试。"

    def check_auth(self) -> None:
        if self._is_busy():
            self.status = "当前有任务正在运行，等它结束再检查登录。"
            return
        self.auth = "正在检查…"
        if not self._start_worker(["--check-auth"], "auth", "正在安全检查本机登录状态…"):
            self.auth = "检查失败"
            self.status = "登录检查没有启动。请检查程序目录后重试。"

    def probe(self) -> None:
        if not self.selected["bookId"]:
            self.status = "先搜索并从结果中选择一本书，再进行章节检查。"
            return
        if self._is_busy():
            self.status = "当前有任务正在运行，等它结束再检查第一章。"
            return
        self._start_worker(["--probe-book", self.selected["bookId"]] + ([] if self.want_thoughts else ["--no-thoughts"]),
                           "probe",
                           "正在检查这本书的第一章、划线" + ("和想法…" if self.want_thoughts else "…（已跳过想法）"))

    def export(self) -> None:
        if not self.selected["bookId"]:
            self.status = "先搜索并从结果中选择一本书，检查详情后再开始下载。"
            return
        if self._is_busy():
            self.status = "当前有任务正在运行，等它结束再开始下载。"
            return
        self.progress = 0.0
        args = [self.selected["bookId"], "--jobs", str(self.jobs)]
        if not self.want_thoughts:
            args.append("--no-thoughts")
        if not self._start_worker(args, "export",
                                  "正在准备下载并检查本地断点…"):
            self.status = "下载没有启动。请检查程序目录后重试。"

    def pause(self) -> None:
        """Same contract as the GUI's pause button: written chapters stay on disk
        and the next export resumes them by UID."""
        if self._is_busy():
            self.process.terminate()
            self.status = "已请求暂停，已写入的章节会保留，下次可继续下载。"

    def set_jobs(self, jobs) -> None:
        try:
            value = int(jobs)
        except (TypeError, ValueError):
            return
        self.jobs = max(1, min(MAX_JOBS, value))

    def set_thoughts(self, enabled) -> None:
        """Only meaningful while idle: a running worker already carries its flag."""
        if self._is_busy():
            return
        self.want_thoughts = bool(enabled)

    def reveal(self, target: str) -> None:
        """Open one of the two directories this program owns.

        The request only ever names a folder; the path comes from the whitelist
        below, so nothing a caller sends can turn into an arbitrary open.
        """
        folders = {"output": OUTPUT, "logs": self.log_dir}
        folder = folders.get(str(target or ""))
        if folder is None:
            self.status = "未知的目录请求。"
            return
        try:
            folder.mkdir(parents=True, exist_ok=True)
            os.startfile(str(folder))
        except OSError as exc:
            self._persist_log(f"[OPEN_DIR_ERROR] {type(exc).__name__}: {exc}")
            self.status = f"无法自动打开目录，请手动前往 {OUTPUT}"

    # ----- browser facing snapshot ---------------------------------

    def snapshot(self) -> dict:
        with self.lock:
            results = []
            for book_id, row in self.book_rows.items():
                results.append({
                    "bookId": book_id,
                    "title": str(row.get("title") or "").strip() or "未命名书籍",
                    "author": str(row.get("author") or "").strip() or "作者未知",
                    "cover": str(row.get("cover") or ""),
                    "category": str(row.get("category") or ""),
                    "categoryPending": bool(row.get("categoryPending")),
                    "ratingText": card_rating_text(row.get("newRating"), row.get("newRatingCount")),
                })
            return {
                "busy": self._is_busy(),
                "action": self.current_action,
                "status": self.status,
                "auth": self.auth,
                "resultCount": self.result_count,
                "results": results,
                "detail": dict(self.selected),
                "progress": self.progress,
                "jobs": self.jobs,
                "wantThoughts": self.want_thoughts,
                "logSeq": self.log_seq,
                "account": self.account,
                "hasCredentials": self.has_credentials(),
                "login": dict(self.login),
            }


class _Session:
    """One random port and one random token per launch.

    The token is minted at startup, dies with the process, and is only ever
    accepted over loopback together with a matching Host header, so it
    authorises the browser tab this launch opened and nothing else.
    """

    COOKIE_PREFIX = "weread_session_"
    LOOPBACK = ("127.0.0.1", "localhost", "[::1]")

    def __init__(self, port: int) -> None:
        self.port = port
        self.token = secrets.token_urlsafe(32)
        self.allowed_hosts = frozenset(f"{host}:{port}" for host in self.LOOPBACK)
        self.allowed_origins = frozenset(f"http://{host}:{port}" for host in self.LOOPBACK)
        # Cookies are not port-scoped, so a browser keeps every session cookie it
        # was ever handed for 127.0.0.1. Carrying the port in the name keeps this
        # launch's cookie distinct from a previous run's, instead of leaving the
        # stale one to be sent first and rejected forever.
        self.cookie_name = f"{self.COOKIE_PREFIX}{port}"

    def host_ok(self, value: str) -> bool:
        return bool(value) and value.strip().lower() in self.allowed_hosts

    def origin_ok(self, value: str) -> bool:
        return value.strip().lower() in self.allowed_origins

    def token_ok(self, candidate: str) -> bool:
        return bool(candidate) and secrets.compare_digest(candidate, self.token)

    def is_session_cookie(self, name: str) -> bool:
        return name.strip().startswith(self.COOKIE_PREFIX)

    def cookie_header(self) -> str:
        return f"{self.cookie_name}={self.token}; Path=/; HttpOnly; SameSite=Strict"


def load_ui() -> bytes:
    """Read the single-file page out of the bundle (or the source tree)."""
    path = BUNDLE / UI_NAME
    if not path.is_file():
        path = _HERE / UI_NAME
    html = path.read_text(encoding="utf-8")
    if UI_NONCE_TOKEN not in html:
        raise RuntimeError(f"{UI_NAME} is missing the {UI_NONCE_TOKEN} placeholder")
    return html.encode("utf-8")


def load_favicon() -> bytes | None:
    """The tab icon, so the page does not open with a blank document glyph."""
    # assets/ sits at the repository root, one level above this module.
    for path in (BUNDLE / FAVICON_NAME, _HERE.parent / "assets" / FAVICON_NAME):
        try:
            if path.is_file():
                return path.read_bytes()
        except OSError:
            continue
    return None


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "WeReadDesktop"
    sys_version = ""

    controller: WebController
    session: _Session

    def log_message(self, fmt, *args) -> None:
        # console=False leaves nowhere to print, and the request line carries the
        # launch token in its query string, so drop the query before logging.
        text = re.sub(r"\?[^ \"]*", "?<redacted>", fmt % args)
        self.controller._persist_log("[HTTP] " + redact_secrets(text))

    # ----- security gate -------------------------------------------

    def _cookie_tokens(self) -> list[str]:
        raw = self.headers.get("Cookie", "")
        found = []
        for chunk in raw.split(";"):
            name, _, value = chunk.strip().partition("=")
            if self.session.is_session_cookie(name) and value:
                found.append(value)
        return found

    def _query_token(self) -> str:
        values = parse_qs(urlsplit(self.path).query).get("t", [])
        return values[0] if values else ""

    def _tokens(self) -> list[str]:
        candidates = []
        header = self.headers.get("X-Weread-Token", "").strip()
        if header:
            candidates.append(header)
        query = self._query_token()
        if query:
            candidates.append(query)
        candidates.extend(self._cookie_tokens())
        return candidates

    def _holds_live_cookie(self) -> bool:
        """True only when the cookie for *this* launch is already held.

        Cookies are not port-scoped, so a browser keeps the session cookie of
        every launch it has ever seen. Accepting one of those as "already
        authenticated" would skip the handshake below, leave this launch without
        a usable cookie, and lock the page out on 401 forever.
        """
        return any(self.session.token_ok(value) for value in self._cookie_tokens())

    def _guard(self) -> tuple[str, str]:
        """Reject everything that is not this loopback session talking to itself.

        Host is checked first because it is what stops DNS rebinding: a hostile
        page resolves its own name to 127.0.0.1 and then sends Host:
        attacker.example, which cannot match a host:port minted from our own
        random port. The token then proves the caller holds the value handed to
        the browser at launch. For unsafe methods Origin and Sec-Fetch-Site close
        the remaining CSRF hole, since a cross-site page can neither read the
        SameSite=Strict cookie nor add a header without a preflight we never
        answer.
        """
        session = self.session
        if not session.host_ok(self.headers.get("Host", "")):
            return "403", "只接受来自本机 127.0.0.1 的访问。"
        # Every candidate is checked instead of the first one: a browser that
        # still holds session cookies from earlier launches sends them all.
        if not any(session.token_ok(value) for value in self._tokens()):
            return "401", "会话已失效。请关闭本页面并重新启动程序。"
        if self.command not in ("GET", "HEAD"):
            origin = self.headers.get("Origin", "")
            if origin and not session.origin_ok(origin):
                return "403", "拒绝跨站请求。"
            if self.headers.get("Sec-Fetch-Site", "") == "cross-site":
                return "403", "拒绝跨站请求。"
        return "", ""

    # ----- plumbing ------------------------------------------------

    def _send(self, status: int, body: bytes, content_type: str,
              headers: dict | None = None, nonce: str | None = None) -> None:
        if nonce is None:
            nonce = secrets.token_urlsafe(16)
        self.send_response(int(status))
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        # Keeps the launch token out of any Referer sent to the WeRead CDN.
        self.send_header("Referrer-Policy", "no-referrer")
        # The page is one self-contained file, so the only script allowed to run
        # is its single inline block carrying this response's nonce. img-src needs
        # 'self' because the QR code is served by this same process -- the worker
        # renders it, this side only streams the bytes.
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; img-src 'self' https: data:; "
            "style-src 'self' 'unsafe-inline'; "
            f"script-src 'nonce-{nonce}'; connect-src 'self'; "
            "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _send_error_text(self, status: str, message: str) -> None:
        self._send(int(status), message.encode("utf-8"), "text/plain; charset=utf-8")

    def _read_json(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self.close_connection = True
            return {}
        if length <= 0:
            return {}
        if length > MAX_BODY_BYTES:
            # The unread body would still be sitting in the socket, and the next
            # keep-alive read would parse it as a request line. Refuse the
            # payload and retire the connection instead.
            self.close_connection = True
            return {}
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def do_GET(self) -> None:
        self._dispatch()

    def do_POST(self) -> None:
        self._dispatch()

    def _dispatch(self) -> None:
        status, message = self._guard()
        if status:
            self._send_error_text(status, message)
            return
        path = urlsplit(self.path).path
        query = parse_qs(urlsplit(self.path).query)
        if self.command == "GET":
            if path == "/":
                self._serve_ui()
            elif path == "/api/state":
                self._send_json(200, {"ok": True, "state": self.controller.snapshot()})
            elif path == "/api/log":
                since = (query.get("since") or ["0"])[0]
                self._send_json(200, dict(self.controller.log_since(since), ok=True))
            elif path == "/api/login/qr.png":
                self._serve_login_qr()
            elif path == "/favicon.ico":
                icon = load_favicon()
                if icon is None:
                    self._send_error_text("404", "没有图标。")
                else:
                    self._send(200, icon, "image/x-icon")
            else:
                self._send_error_text("404", "没有这个地址。")
            return
        payload = self._read_json()
        with self.controller.lock:
            if path == "/api/search":
                self.controller.search(payload.get("keyword", ""))
            elif path == "/api/select":
                self.controller.select(payload.get("bookId", ""))
            elif path == "/api/auth":
                self.controller.check_auth()
            elif path == "/api/probe":
                self.controller.probe()
            elif path == "/api/export":
                self.controller.export()
            elif path == "/api/pause":
                self.controller.pause()
            elif path == "/api/jobs":
                self.controller.set_jobs(payload.get("jobs", 1))
            elif path == "/api/thoughts":
                self.controller.set_thoughts(payload.get("enabled", False))
            elif path == "/api/reveal":
                self.controller.reveal(payload.get("target", ""))
            elif path == "/api/login/begin":
                self.controller.start_login()
            elif path == "/api/login/otp":
                self.controller.submit_login_otp(payload.get("code", ""))
            elif path == "/api/login/cancel":
                self.controller.cancel_login()
            elif path == "/api/login/manual":
                self.controller.save_manual_credentials(
                    payload.get("apiKey", ""), payload.get("skey", ""))
            elif path == "/api/logout":
                self.controller.logout()
            elif path == "/api/shutdown":
                self._send_json(200, {"ok": True, "stopping": True})
                threading.Thread(target=self._stop_server, daemon=True).start()
                return
            else:
                self._send_error_text("404", "没有这个地址。")
                return
            state = self.controller.snapshot()
        self._send_json(200, {"ok": True, "state": state})

    def _serve_login_qr(self) -> None:
        """Stream the worker's PNG straight through to the page.

        The bytes are never decoded on this side; the browser renders the code.
        That is what lets a login-capable page ship in a package with no imaging
        library in it. Cache-busting rides on qrSeq so a re-fetched code is not
        served from the browser's cache.
        """
        png = self.controller.qr_png()
        if png is None:
            self._send_error_text("404", "还没有可显示的二维码。")
            return
        self._send(200, png, "image/png")

    def _serve_ui(self) -> None:
        if self._query_token() and not self._holds_live_cookie():
            # Hand the token to the browser as a SameSite=Strict cookie, then
            # drop it from the address bar: a token sitting in the URL would
            # otherwise ride along in the Referer of every cover request.
            self._send(303, b"", "text/plain; charset=utf-8",
                       {"Location": "/", "Set-Cookie": self.session.cookie_header()})
            return
        try:
            template = load_ui()
        except (OSError, RuntimeError) as exc:
            self._send_error_text("500", f"界面文件读取失败：{exc}")
            return
        nonce = secrets.token_urlsafe(16)
        html = template.decode("utf-8").replace(UI_NONCE_TOKEN, nonce).encode("utf-8")
        # The same nonce must appear in the script tag and in the CSP header,
        # otherwise the browser blocks the page's own script.
        self._send(200, html, "text/html; charset=utf-8", nonce=nonce)

    def _stop_server(self) -> None:
        # Let the response flush before the socket closes under the browser.
        threading.Event().wait(0.25)
        self.server.shutdown()


def build_server(controller: WebController) -> tuple[ThreadingHTTPServer, _Session]:
    class Handler(_Handler):
        pass

    Handler.controller = controller
    # Port 0 asks the OS for a free port, so a second launch cannot collide
    # with, or be mistaken for, an already running instance.
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    httpd.daemon_threads = True
    session = _Session(httpd.server_address[1])
    Handler.session = session
    return httpd, session


def _session_file(port: int) -> Path:
    # One file per port: a fixed name would let a second launch overwrite the
    # first instance's recovery hint, leaving its user with no URL to open.
    return LOG_DIR / f"web-session-{port}.txt"


def _write_session_file(session: _Session) -> None:
    """Leave a recovery hint in the per-user private directory.

    It holds a loopback port, the PID and the launch URL. That token grants
    nothing off this machine, to another user, or after the process exits, and
    no WeRead cookie or API key is ever written to it.
    """
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        _session_file(session.port).write_text(
            f"pid={os.getpid()}\nport={session.port}\n"
            f"url=http://127.0.0.1:{session.port}/?t={session.token}\n",
            encoding="utf-8")
    except OSError:
        pass


def _clear_session_file(port: int) -> None:
    try:
        _session_file(port).unlink()
    except OSError:
        pass


def _open_browser(controller: WebController, entry: str) -> None:
    try:
        webbrowser.open(entry)
    except Exception as exc:  # a headless box has no browser to open
        controller._persist_log(f"[WEB_BROWSER_ERROR] {type(exc).__name__}: {exc}")


def _already_running_notice() -> None:
    """Tell the user why nothing happened.

    The web build is windowed, so there is no console to print to and exiting
    silently would look like a failed launch. MessageBoxW is a plain user32
    call, so this still pulls in no tkinter and no PIL.
    """
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(
            None,
            "网页版已经在运行了。\n\n"
            "请切换到浏览器里已经打开的那个页面；"
            "如果找不到，就先关掉它再重新启动。",
            "微信读书 · 网页版", 0x40)
    except Exception:
        pass


def main() -> int:
    if getattr(sys, "frozen", False) and len(sys.argv) > 1:
        raise SystemExit("The web-mode executable does not accept worker commands.")
    if not is_first_instance("WeReadDownloader-Web"):
        # A second server would mean another random port, another browser tab
        # and another writer for the same session file. Deliberately does not
        # read the running instance's web-session-*.txt to reuse its token.
        _already_running_notice()
        return 0
    controller = WebController(LOG_DIR)
    httpd, session = build_server(controller)
    threading.Thread(target=controller.pump, daemon=True).start()
    _write_session_file(session)
    controller._persist_log(f"[WEB] local server ready on 127.0.0.1:{session.port}")
    entry = f"http://127.0.0.1:{session.port}/?t={session.token}"
    # Hand the page to the browser from a side thread: the server must already be
    # answering requests, and a browser cold start must not sit in front of that.
    threading.Thread(target=_open_browser, args=(controller, entry), daemon=True).start()
    try:
        httpd.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        controller.stop()
        httpd.server_close()
        _clear_session_file(session.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
