#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Beginner-friendly CustomTkinter GUI for WeRead desktop exports.

This process only draws the UI and talks to a separate hidden worker process
over UTF-8 stdio. It deliberately never imports the download engine, so the
GUI bundle stays free of LuaJIT/lxml and of any WeRead credentials.
"""
from __future__ import annotations

import io
import shutil
import json
import datetime
import os
import queue
import re
import subprocess
import sys
import threading
import time
import traceback
import tkinter as tk
from pathlib import Path
from tkinter import messagebox

import requests
import customtkinter as ctk
from PIL import Image

import weread_login
SKILLS_URL = "https://weread.qq.com/r/weread-skills"
# Helpers from first_run, used by check_credentials_on_start() to say in the
# status bar what is missing, and by the inline credential card in the download
# panel. The wizard itself is no longer a startup gate: the main window always
# opens. first_run stays in the GUI bundle and is still declared as a
# hiddenimport in WeReadDesktop.spec -- see the comment there for why pyinstaller
# does not see the relative import on its own.
try:
    from .first_run import (_settings_path, _credentials_usable, _has_api_key,
                            _validate_creds, _build_settings_lua,
                            write_settings, _ensure_credentials_file)
except ImportError:
    from first_run import (_settings_path, _credentials_usable, _has_api_key,
                           _validate_creds, _build_settings_lua,
                           write_settings, _ensure_credentials_file)

# Every user-facing sentence about a worker line lives in weread_core so the
# web-mode server can reuse the exact same phrasing without pulling tkinter and
# PIL into its frozen bundle. Appending this directory is required because the
# test suite and verify_book2_e2e.py load this file by path, which -- unlike a
# normal import -- does not put its own directory on sys.path.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.append(str(_HERE))

from weread_core import (  # noqa: E402
    FALLBACK_ZH,
    FROZEN,
    LOG_DIR,
    OUTPUT,
    SEARCH_FIELDS,
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

# The refresh loop must never try to catch up on an unbounded burst: the worker
# can emit hundreds of lines at once (search rows, per-chapter download
# progress), and handling all of them in one main-thread pass blocks Tk long
# enough for Windows to report the window as unresponsive. The rest is picked
# up on the next 100 ms tick.
# The window title and the single-instance lookup: a second launch finds the
# running window by this exact caption and brings it forward.
#
# Display name stays Chinese on purpose -- the whole interface is Chinese, and
# switching only the caption to English makes the program look misinstalled.
# The English name lives where it belongs: the process, the install directory
# (WeReadDownloader) and the installer (AppName), all of which read the same
# constant so they cannot drift apart.
APP_TITLE = "WeReadDownloader"
PRODUCT_NAME = "WeReadDownloader"
SINGLE_INSTANCE_KEY = "WeReadDownloader-Graphics"

# Type scale. The console voice is the OS UI sans at every size -- the old
# serif display face was part of the editorial look and is gone with it.
SANS = "Microsoft YaHei UI"
MONO = "Cascadia Mono"

MAX_EVENTS_PER_TICK = 200

# Log a stall marker when the main thread is blocked this long, so a real
# session produces the evidence instead of a reconstruction. Windows shows
# "(未响应)" at roughly five seconds, so 1200 ms catches the tail end of it.
STALL_LOG_MS = 1200.0

# Wall time this process was not on the CPU before we call the gap a sleep. The
# machine suspending itself is not a defect and shows up in the log as gaps of
# 26-96 s with blocked=0ms and an empty queue, which is what a real freeze does
# not look like.
RESUME_MIN_MS = 2000.0

# Result cards per page. Repainting a CTkScrollableFrame costs roughly 80 ms per
# child widget on the main thread, so keeping a page small is what stops a long
# result list from freezing the window.
RESULTS_PER_PAGE = 6


def _font(size: int, weight: str = "normal", family: str = SANS) -> ctk.CTkFont:
    """The single place a font is created, so the type scale stays coherent."""
    return ctk.CTkFont(family=family, size=size, weight=weight)


def _decode_cover(payload: bytes, box: tuple):
    """Decode a cover at display size, not at source size.

    WeRead serves covers up to a few thousand pixels tall. Decoding one at full
    resolution costs ~220 ms and yields a 25 MB bitmap, while CTkImage only
    scales at draw time -- so the oversized bitmap is what gets retained.
    draft() lets libjpeg decode straight to a smaller size, and thumbnail()
    then makes that reduction real, which is what keeps the UI responsive and
    the cache small.
    """
    image = Image.open(io.BytesIO(payload))
    image.draft("RGB", box)  # no-op for non-JPEG, which is fine
    image = image.convert("RGB")
    image.thumbnail(box, Image.LANCZOS)
    return image


def _covers_box(image, box) -> bool:
    """Is this decoded cover already large enough for `box`?

    The cache stores one downsized image per URL, but the card slot and the
    detail slot want different sizes, so a cache hit is only reusable when it
    was decoded for a slot at least this large. Comparing against the long edge
    keeps this correct for portrait and landscape art alike -- a 2400x3600 cover
    decoded for the 54x76 card slot comes out 51x76, and asking whether 51
    reaches 96 would wrongly reject a perfectly good detail image.
    """
    if image is None:
        return False
    return max(image.size) >= max(box)


def describe_omissions(report, compact: bool = False) -> str:
    """Plain-language statement of what a finished book is missing.

    The downloader keeps the prose when an accessory is unavailable, so the
    book is usable -- but a reader who is never told that images or highlights
    went missing will assume the book is whole. Returns "" only when nothing
    was actually lost.

    `compact` is the one-line form for the status bar. The full paragraph is
    three wrapped lines, and the status bar sits directly above the task buttons
    in a packed column: at the default window height that growth pushed 退出登录 /
    查看技术日志 / 打开下载目录 below the bottom of the window, where they were
    laid out at full size but could not be clicked. The dialog and the log file
    still carry the whole statement.
    """
    if not isinstance(report, dict):
        return ""
    lines = []
    images = int(report.get("images_dropped") or 0)
    if images:
        chapters = int(report.get("chapters_with_dropped_images") or 0)
        lines.append("插图：%d 张（分布在 %d 章）已被微信读书删除，无法再取回。"
                     % (images, chapters))
    dropped = int(report.get("underline_ranges_dropped") or 0)
    if dropped:
        # The worker emits chapters_with_unplaced_underlines and keeps
        # chapters_with_dropped_underlines only for older builds. Read the new
        # name first and fall back, so the window agrees with the log instead of
        # reporting the underlines as missing from 0 chapters.
        chapters = int(
            report.get("chapters_with_unplaced_underlines")
            or report.get("chapters_with_dropped_underlines") or 0)
        lines.append("划线：%d 条（分布在 %d 章）在原文里没有位置标记，为避免标错句子已略去。"
                     % (dropped, chapters))
    if not lines:
        return ""
    if compact:
        # One line, no embedded newlines, so the status label cannot grow the
        # packed column and push the buttons out of the window.
        summary = "；".join(line.split("：", 1)[-1].rstrip("。") for line in lines)
        return "正文完整，但有附带内容取不回：%s。完整说明见完成提示与技术日志。" % summary
    return ("正文完整，但有部分附带内容无法取回：\n\n"
            + "\n".join("· " + line for line in lines)
            + "\n\n这些都没有补全：微信读书那边已无数据，或原文没有可用的位置标记，"
              "硬填会把划线标到错误的句子上。")


class DesktopApp:
    """Enterprise console layout: dark rail, white top bar, carded content.

    Everything the reference design defines is built for real: a dark sidebar
    with brand + nav + a green bar on the active item, a white top bar that
    carries the global search and account controls, a stat-card row, carded
    results/detail panels, and a full-width download card at the bottom.

    Surfaces stay as they were: cool grey ground, white cards, hairline rules,
    one muted green accent. SERIF is gone too -- the console voice is the OS
    UI sans at every size.
    """

    # surfaces
    BG = "#F0F2F5"           # cool grey page ground
    CARD = "#FFFFFF"         # primary surface
    SUNKEN = "#F7F8FA"       # nested surface: export panel, intro, inputs
    HAIRLINE = "#E3E6EA"     # 1px structural rule
    HAIRLINE_SOFT = "#EEF0F3"

    # ink
    INK = "#1F2937"          # primary text, near-black
    MUTED = "#4B5563"        # secondary
    FAINT = "#8B95A3"        # meta / tertiary

    # the one accent
    ACCENT = "#2D6A4F"
    ACCENT_HOVER = "#256048"
    ACCENT_TINT = "#EAF3EE"
    ACCENT_ON_TINT = "#256048"
    BLUE = ACCENT            # legacy alias; nothing should reference it now

    # semantic, all desaturated
    GREEN = "#2D6A4F"
    GREEN_TINT = "#EAF3EE"
    RED = "#C0392F"
    RED_TINT = "#FDEAEC"
    AMBER = "#B7791F"
    AMBER_TINT = "#FDF5E6"

    # the dark navigation rail (reference design's sidebar). CustomTkinter only
    # accepts hex colours, so the reference's rgba(255,255,255,.09) is flattened
    # onto the rail's own gradient here.
    SIDE_BG = "#1D2632"
    SIDE_BG_2 = "#232E3C"
    SIDE_TXT = "#AEB8C5"
    SIDE_TXT_HI = "#FFFFFF"
    SIDE_ACTIVE = "#2A3646"
    SIDE_HOVER = "#242F3D"

    RADIUS = 8
    COVER_W, COVER_H = 54, 76
    # Small on purpose: the selected card in the list already shows the cover,
    # and every pixel here goes to the introduction instead.
    DETAIL_COVER_W, DETAIL_COVER_H = 72, 88

    def __init__(self, root: ctk.CTk):
        self.root = root
        root.report_callback_exception = self._handle_callback_exception
        root.title(APP_TITLE)
        # The window's own icon, so the title bar and the taskbar button show the
        # same art as the file and the shortcut. Without iconphoto() Tk shows its
        # default widget icon no matter what the exe carries.
        self._set_window_icon(root)
        # 680 logical px = 850 physical, which fits a 1536x864 screen at 125%
        # scaling. The previous 790 was 988 physical px -- taller than the
        # display itself, so part of the window was always off-screen.
        root.geometry("1120x680")
        root.minsize(980, 580)
        # NOTE (2026-10-13): building while withdrawn (root.withdraw()) together
        # with a splash cover in main() was tried to stop the launch from
        # painting region by region. Both were reverted at the user's request.
        # Measurement is why: the ~1.4 s first paint is a FIXED CustomTkinter/Tk
        # cost -- 201 widgets painted in 876 ms and 1201 in 827 ms -- so no
        # layout change removes it, and hiding it costs a second window. The
        # visible build is the accepted trade.
        ctk.set_appearance_mode("light")
        ctk.set_default_color_theme("blue")
        self.events: queue.Queue[tuple] = queue.Queue()
        self.process: subprocess.Popen | None = None
        # True between "the user clicked" and "the off-thread Popen returned";
        # see _start_worker for why the spawn no longer runs on the Tk thread.
        self._spawn_pending = False
        self.reader: threading.Thread | None = None
        self.current_action = ""
        self.job_seq = 0
        self.last_failure = False
        self.last_unresolved = False
        # What the last finished book is missing. A degraded download is only
        # better than a failed one if the reader is told what went missing.
        self.omissions = None
        self.selected_id = ""
        self.selected_title = ""
        self.selected_author = ""
        self.book_info: dict = {}
        self.cover_image = None
        self.book_rows: dict[str, dict] = {}
        self.pending_book: dict | None = None
        self.info_loaded = False
        # Every CTkImage we hand to a label must stay referenced for the whole
        # session: CTkLabel._update_image() is a no-op for image=None, so the
        # inner Tk label keeps pointing at the previous image name. Once that
        # CTkImage is garbage collected Tk deletes the image and every later
        # text update dies with `TclError: image "pyimageN" doesn't exist`.
        self._image_refs: list = []
        self.card_widgets: dict[str, dict] = {}
        self.cover_cache: dict[str, bytes | None] = {}
        self.cover_slots = threading.Semaphore(4)
        # Search results are kept in full but only one page is ever built into
        # widgets, so the redraw cost per page stays bounded.
        self.result_rows: list[dict] = []
        self.page_index = 0
        self.jobs = tk.StringVar(value="1")
        self.status = tk.StringVar(value="准备就绪。第一步：检查登录状态。")
        # Non-zero while a modal dialog owns the main loop, so the stall
        # watchdog can tell a waiting dialog from a wedged thread.
        self._modal_depth = 0
        self.login_dialog = None
        self.login_thread = None
        self.login_cancel = threading.Event()
        self.login_otp = ""
        self._pending_otp = ""
        self._log_buffer: list[str] = []
        # Set by the sampler thread when its own sleep comes back late, which is
        # how a machine suspend is told apart from a wedged Tk thread. Consumed
        # by the next tick so it is reported exactly once.
        self._resume_mark = 0.0
        self._build()
        self._restore_login_identity()
        # After the build, not before: building the window costs over a second,
        # and charging that to the first tick logged a 1.3 s [STALL] on every
        # single launch. The loop starts here.
        self._last_tick_end = time.perf_counter()
        self._stall_watcher = threading.Thread(
            target=self._watch_for_stalls, name="stall-watch", daemon=True)
        self._stall_watcher.start()
        self.root.after(100, self._poll)

    def _app_icon_path(self):
        """Locate weread-app.ico in both the source and the frozen layout."""
        candidates = []
        here = Path(__file__).resolve().parent
        if getattr(sys, "frozen", False):
            # PyInstaller onefile unpacks into _MEIPASS; onedir keeps the exe
            # directory. Check both, then the install tree next to it.
            mei = Path(getattr(sys, "_MEIPASS", "") or here)
            exe_dir = Path(sys.executable).resolve().parent
            for base in (mei, exe_dir, exe_dir.parent):
                candidates.append(base / "assets" / "weread-app.ico")
                candidates.append(base / "weread-app.ico")
        else:
            # This module lives in weread/, while assets/ sits at the repo root.
            candidates.append(here.parent / "assets" / "weread-app.ico")
            candidates.append(here / "assets" / "weread-app.ico")
        return next((c for c in candidates if c.is_file()), None)

    def _app_icon_pil(self, size=32):
        """One frame of the app icon as a PIL RGBA image, cached per size.

        The .ico already carries 16..256px frames, so the real frame is pulled
        out rather than downscaling the 256px one into a blur at 16px.
        """
        cache = getattr(self, "_icon_pil_cache", None)
        if cache is None:
            cache = self._icon_pil_cache = {}
        if size in cache:
            return cache[size]
        path = self._app_icon_path()
        if path is None:
            return None
        try:
            from PIL import Image
            img = Image.open(path).convert("RGBA").resize(
                (size, size), Image.LANCZOS)
        except Exception:
            return None
        cache[size] = img
        return img

    def _app_icon_photo(self, size=32):
        """The same icon as a Tk PhotoImage, which is what iconphoto() takes.

        Tk's PhotoImage reads GIF/PPM/PGM but not raw RGBA bytes, so the frame is
        re-encoded as GIF on the way through.
        """
        cache = getattr(self, "_icon_photo_cache", None)
        if cache is None:
            cache = self._icon_photo_cache = {}
        if size in cache:
            return cache[size]
        img = self._app_icon_pil(size)
        if img is None:
            return None
        try:
            import tkinter as _tk
            buf = io.BytesIO()
            img.save(buf, format="GIF")
            buf.seek(0)
            photo = _tk.PhotoImage(data=buf.read())
        except Exception:
            return None
        cache[size] = photo
        return photo

    def _set_window_icon(self, root):
        """Give the window the app icon, in both source and frozen layouts.

        The exe's own icon is not enough: Tk draws its default widget icon in
        the title bar and the taskbar unless iconphoto() is called. So the .ico
        has to be found at run time as well.
        """
        if self._app_icon_path() is None:
            print("[ICON] weread-app.ico not found; using the Tk default",
                  file=sys.stderr)
            return
        frames = [self._app_icon_photo(s) for s in (48, 32, 24, 16)]
        frames = [f for f in frames if f is not None]
        if not frames:
            print("[ICON] could not read any icon frame", file=sys.stderr)
            return
        try:
            # Clear CustomTkinter's default icon first. On Windows a window's
            # iconbitmap (an HICON) outranks iconphoto, and CTk installs its own
            # blue placeholder -- so setting the photo alone silently did
            # nothing and the title bar kept showing CTk's icon. Verified by
            # isolating it: tk.Tk() honoured iconphoto, ctk.CTk() did not until
            # iconbitmap was cleared.
            try:
                root.iconbitmap("")
            except Exception:
                pass
            # keep them alive: a garbage-collected PhotoImage makes Tk raise
            # "image doesn't exist" on the next update
            self._icon_refs = frames
            root.iconphoto(True, *frames)
        except Exception as exc:
            # Never fatal: a missing icon must not stop the window from opening,
            # and this runs before the log writer's own state exists.
            print("[ICON] could not apply the window icon: %s: %s"
                  % (type(exc).__name__, exc), file=sys.stderr)

    def _restore_login_identity(self):
        """Show the already-saved account on the login button at startup."""
        stored = weread_login.load_saved_credentials()
        ready = bool(stored.get("api_key")) and bool(stored.get("cookies", {}).get("wr_skey"))
        if not ready:
            return
        self._set_login_identity((stored.get("account") or {}).get("name", ""))
        self._refresh_logout_button()

    def _set_login_identity(self, name=None):
        """Put the account name on the login button once we know who is signed in.

        name=None means signed out, name="" means signed in without a known
        display name (manual entry), otherwise it is the account's name.
        """
        if name is None:
            self.login_button.configure(text="登录 / 切换账号")
            return
        name = str(name).strip()
        if not name:
            self.login_button.configure(text="已登录")
            return
        if len(name) > 8:
            name = name[:8] + "…"
        self.login_button.configure(text=name)

    def _section(self, parent, numeral: str, title: str, padx: int = 0,
                 pady: int = 0, grid_at=None):
        """A numbered section heading closed by a hairline rule.

        The numeral is set in the accent so the three steps read as a sequence
        without needing boxes around each one. `grid_at` places the heading with
        grid() for containers whose other children are gridded -- Tk forbids
        mixing the two managers in one parent.

        `pady` is the breathing room above the heading; without it the title sat
        flush against the panel's top edge, which read as cramped.
        """
        head = ctk.CTkFrame(parent, fg_color="transparent")
        if grid_at is None:
            head.pack(fill="x", padx=padx, pady=(pady, 0))
        else:
            head.grid(**grid_at)
        row = ctk.CTkFrame(head, fg_color="transparent")
        row.pack(fill="x")
        ctk.CTkLabel(row, text=numeral, text_color=self.ACCENT,
                     font=_font(12, "bold")).pack(side="left", padx=(0, 8))
        ctk.CTkLabel(row, text=title, text_color=self.INK,
                     font=_font(15, "bold")).pack(side="left")
        ctk.CTkFrame(head, height=1, fg_color=self.HAIRLINE).pack(fill="x", pady=(8, 0))
        return head

    def _nav_item(self, parent, text: str, glyph: str, active: bool = False, command=None):
        """One sidebar entry in the reference design's dark rail.

        A single CTkButton with no child widgets. That is deliberate and it is
        the whole point of this method:

        MEASURED 2026-10-12 -- a CTkButton with a bar and two labels packed on
        top of it looked right and hovered correctly, but clicking did nothing:
        the children sit above the button's canvas, so clicks reached a label
        and the button's command never ran. Binding the children too ("+") did
        not fix it either, because CustomTkinter's bind() here does not report
        or reliably register on those child frames/labels.

        CTkButton's own command is wired by CustomTkinter to its own canvas,
        which is exactly why the top bar's 刷新 / 搜索 buttons work. So the row
        is one button: the glyph and label live in its text, and the reference's
        3px green selection bar becomes a 3px left border.
        """
        item = ctk.CTkButton(
            parent,
            text=("%s   %s" % (glyph, text)),
            command=command,
            fg_color=self.SIDE_ACTIVE if active else "transparent",
            hover_color=self.SIDE_HOVER,
            text_color=self.SIDE_TXT_HI if active else self.SIDE_TXT,
            font=_font(13, "bold" if active else "normal"),
            anchor="w",
            corner_radius=6,
            height=38,
            border_width=3 if active else 0,
            border_color=self.ACCENT if active else self.SIDE_BG,
        )
        item.pack(fill="x", pady=1)
        item._nav_active = active
        item._nav_text = text
        item._nav_glyph = glyph
        return item

    def _switch_page(self, page_name: str):
        """Switch to the specified page."""
        if page_name == self._current_page:
            return
        # Update nav item states. Each row is one CTkButton (see _nav_item), so
        # the selected look is rebuilt by configuring the button itself: fill,
        # text colour, weight, and a 3px left border as the selection bar.
        for name, item in self.nav_items.items():
            is_active = name == page_name
            item._nav_active = is_active
            item.configure(
                fg_color=self.SIDE_ACTIVE if is_active else "transparent",
                text_color=self.SIDE_TXT_HI if is_active else self.SIDE_TXT,
                font=_font(13, "bold" if is_active else "normal"),
                border_width=3 if is_active else 0,
                border_color=self.ACCENT if is_active else self.SIDE_BG,
            )

        # Hide current page, show new page
        if self._current_page in self.pages:
            self.pages[self._current_page].pack_forget()
        self._current_page = page_name
        if page_name not in self.pages:
            # Build on first visit; the builder registers the page in self.pages.
            self._create_page(page_name)
        self.pages[page_name].pack(fill="both", expand=True)

    def _scan_exports(self):
        """Every export folder, classified, with its disk cost.

        Three kinds show up on a real machine and the UI must not blur them:

          done    -- holds a published .epub; this is what the reader wants
          partial -- a `.work` tree: the resume scratch space. MEASURED on this
                     machine: 5 of 12 folders were `.work`, 677 MB, invisible
                     in the old list. Deleting one is safe -- it only costs the
                     ability to resume, and a re-download starts from zero.
          stale   -- `_stale-*` leftovers from a superseded workspace. Nothing
                     reads them; they are pure reclaimable space.

        Each row is (name, size_mb, mtime, kind, path).
        """
        rows = []
        try:
            OUTPUT.mkdir(parents=True, exist_ok=True)
            for entry in sorted(OUTPUT.iterdir(), key=lambda p: p.stat().st_mtime,
                                reverse=True):
                if not entry.is_dir():
                    continue
                try:
                    total = sum(f.stat().st_size
                                for f in entry.rglob("*") if f.is_file())
                except OSError:
                    total = 0
                mtime = datetime.datetime.fromtimestamp(entry.stat().st_mtime)
                if entry.name.endswith(".work"):
                    kind = "partial"
                elif entry.name.startswith("_stale-"):
                    kind = "stale"
                else:
                    # A finished-looking folder with no .epub is a download that
                    # never completed: on this machine 无名之町-3300020514 sat
                    # there empty and still looked like a book in the list.
                    kind = "done" if next(entry.glob("*.epub"), None) else "failed"
                rows.append((entry.name, total / 1048576.0, mtime, kind, entry))
        except OSError:
            pass
        return rows

    def _read_history(self):
        """DONE lines from every worker log, newest first: (stamp, summary).

        Two mismatched conventions existed before this fix: the worker writes
        per-book logs (desktop-<bookId>.log) whose DONE line carries a PATH, and
        the history page read only desktop-general.log expecting a JSON payload.
        The page was therefore always empty no matter how many downloads
        succeeded -- this machine's first download had DONE/EPUB/IMPORT_BUNDLE
        lines sitting in desktop-26796447.log while the page read a file that
        never receives them.

        Both shapes are accepted now, and every desktop-*.log is scanned:
          DONE {json with title/chapters}   -> the payload the page was built for
          DONE <path>                       -> what the worker actually prints
        """
        rows = []
        try:
            if not LOG_DIR.is_dir():
                return rows
            logs = sorted(LOG_DIR.glob("desktop-*.log"))
            # newest file first, so a book downloaded today outranks one from
            # last week even before the per-line sort below
            logs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            for log_path in logs:
                try:
                    text = log_path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                for line in text.splitlines():
                    if not line.startswith("DONE "):
                        continue
                    body = line[5:].strip()
                    try:
                        payload = json.loads(body)
                        title = str(payload.get("title") or "未知书名")
                        chapters = payload.get("chapters") or payload.get("total") or "?"
                        rows.append(("完成", f"{title} · {chapters} 章"))
                        continue
                    except (ValueError, TypeError):
                        pass
                    # the path form: name the book from the export folder, and
                    # say which artifact was produced
                    p = Path(body.replace("/", "\\"))
                    book = p.parent.name if p.parent.name else body
                    if book.endswith(".work"):
                        book = book[:-5]
                    artifact = "EPUB" if body.lower().endswith(".epub") else (
                        "导入包" if body.lower().endswith(".zip") else "文件")
                    # strip a bookId suffix for display, as the scan page does
                    display = re.sub(r"-\d{6,}$", "", book) or book
                    rows.append(("完成", f"{display} · {artifact}"))
        except OSError:
            pass
        # newest first by the file they came from; within one file, last line wins
        return list(reversed(rows))[:200]

    def _read_annotation_notes(self):
        """Unplaced-underline decisions from the log, newest first."""
        rows = []
        try:
            log_path = Path(LOG_DIR) / "desktop-general.log"
            if not log_path.is_file():
                return rows
            for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
                # The engine's actual phrasing: "[ANNOTATION] chapter UID N: text
                # kept, X of Y underline(s) not placed by WeRead: ..."
                if line.startswith("[ANNOTATION]") and "not placed" in line:
                    rows.append(("拒绝标注", line[:200]))
                elif line.startswith("[UNRESOLVED]") or line.startswith("[INCOMPLETE]"):
                    rows.append(("章节存疑", line[:200]))
        except OSError:
            pass
        return rows[:200]

    def _open_export(self, name: str):
        """Open one export folder; the name comes from our own scan."""
        safe = Path(name).name  # never trust more than the basename
        target = OUTPUT / safe
        if target.is_dir():
            os.startfile(str(target))

    def _create_page(self, page_name: str):
        """Create page content on demand.

        All pages live under the same content area (self.page_host, the frame
        below the top bar). The download workshop is built eagerly in _build;
        the other five are built here on first visit and cached.
        """
        builders = {
            "我的书架": self._create_bookshelf_page,
            "下载记录": self._create_download_history_page,
            "统计分析": self._create_statistics_page,
            "划线清单": self._create_highlights_page,
            "设置": self._create_settings_page,
        }
        builder = builders.get(page_name)
        if builder is not None and page_name not in self.pages:
            builder(self.page_host)

    def _page_scaffold(self, host, title: str):
        """Shared chrome for the non-workshop pages: heading + one card."""
        page = ctk.CTkFrame(host, fg_color=self.BG, corner_radius=0)
        self.pages[title] = page
        head = ctk.CTkFrame(page, fg_color="transparent")
        head.pack(fill="x")
        ctk.CTkLabel(head, text=title, text_color=self.INK,
                     font=_font(18, "bold")).pack(side="left")
        ctk.CTkFrame(page, height=1, fg_color=self.HAIRLINE).pack(fill="x", pady=(12, 0))
        card = ctk.CTkFrame(page, fg_color=self.CARD, corner_radius=self.RADIUS,
                            border_width=1, border_color=self.HAIRLINE)
        card.pack(fill="both", expand=True, pady=(14, 0))
        return page, card

    def _refresh_page(self, page_name: str):
        """Rebuild a page in place, so deletes and new downloads show up."""
        old = self.pages.pop(page_name, None)
        if old is not None:
            old.pack_forget()
            old.destroy()
        self._create_page(page_name)
        if page_name in self.pages:
            self.pages[page_name].pack(fill="both", expand=True)

    def _create_bookshelf_page(self, host):
        """书架 = 微信里的真书架（可下载）+ 本机已导出（可清理）。

        The shelf half is the honest "我的书架": the account's own books, each
        clickable straight into a download, exactly like a search result. The
        export half below it is what this machine holds, with the reclaim and
        delete tools the disk needed.
        """
        page, card = self._page_scaffold(self.page_host, "我的书架")

        # ---- half 1: the real WeRead shelf --------------------------------
        head = ctk.CTkFrame(card, fg_color="transparent")
        head.pack(fill="x", padx=16, pady=(14, 0))
        ctk.CTkLabel(head, text="微信书架", text_color=self.INK,
                     font=_font(13, "bold")).pack(side="left")
        ctk.CTkLabel(head, text="点书名即可下载，与搜索结果一致",
                     text_color=self.FAINT, font=_font(11)).pack(side="left", padx=(10, 0))
        self._quiet_button(head, "刷新书架", 84, self.load_shelf).pack(side="right")

        self.shelf_frame = ctk.CTkScrollableFrame(card, fg_color=self.SUNKEN,
                                                  corner_radius=8)
        self.shelf_frame.pack(fill="x", padx=14, pady=(10, 0))
        self.shelf_frame.configure(height=210)
        ctk.CTkLabel(self.shelf_frame, text="正在读取微信书架…",
                     text_color=self.FAINT, font=_font(12)).pack(expand=True, pady=40)
        self.load_shelf()

        # ---- half 2: what this machine already holds ----------------------
        rows = self._scan_exports()
        if not rows:
            return
        ctk.CTkFrame(card, height=1, fg_color=self.HAIRLINE).pack(
            fill="x", padx=16, pady=(14, 0))
        head2 = ctk.CTkFrame(card, fg_color="transparent")
        head2.pack(fill="x", padx=16, pady=(10, 0))
        ctk.CTkLabel(head2, text="本机已导出", text_color=self.INK,
                     font=_font(13, "bold")).pack(side="left")
        ctk.CTkLabel(head2, text="删除会永久移除文件，请谨慎",
                     text_color=self.FAINT, font=_font(11)).pack(side="left", padx=(10, 0))

        done = [r for r in rows if r[3] == "done"]
        failed = [r for r in rows if r[3] == "failed"]
        reclaimable = [r for r in rows if r[3] in ("partial", "stale", "failed")]
        reclaim_mb = sum(r[1] for r in reclaimable)

        if reclaimable:
            bar = ctk.CTkFrame(card, fg_color=self.AMBER_TINT, corner_radius=6)
            bar.pack(fill="x", padx=14, pady=(10, 0))
            what = []
            if failed:
                what.append("%d 个未完成" % len(failed))
            if len(rows) - len(done) - len(failed):
                what.append("%d 个半成品或残留"
                            % (len(rows) - len(done) - len(failed)))
            ctk.CTkLabel(bar, text="可回收 %.0f MB：%s，清理不影响已完成的书。"
                         % (reclaim_mb, "、".join(what)),
                         text_color=self.AMBER, font=_font(12),
                         anchor="w").pack(side="left", fill="x", expand=True,
                                          padx=14, pady=10)
            self._quiet_button(bar, "全部清理", 96,
                               lambda: self._delete_exports(reclaimable)
                               ).pack(side="right", padx=(0, 14))

        local = ctk.CTkScrollableFrame(card, fg_color=self.CARD, corner_radius=0)
        local.pack(fill="both", expand=True, padx=8, pady=(8, 10))
        for name, size_mb, mtime, kind, path in rows:
            self._build_local_row(local, name, size_mb, kind, path)

    def _build_local_row(self, parent, name, size_mb, kind, path):
        """One row of the on-machine export list (delete / 删想法 / open)."""
        row = ctk.CTkFrame(parent, fg_color="transparent", height=44)
        row.pack(fill="x", pady=1)
        row.pack_propagate(False)
        if kind == "done":
            glyph, tint = "▤", self.GREEN
        elif kind == "failed":
            glyph, tint = "⚠", self.RED
        elif kind == "partial":
            glyph, tint = "◔", self.AMBER
        else:
            glyph, tint = "⌫", self.FAINT
        ctk.CTkLabel(row, text=glyph, width=30, text_color=tint,
                     font=_font(13)).pack(side="left")
        suffix = {"done": "", "failed": "（未完成）",
                  "partial": "（半成品）", "stale": "（残留）"}[kind]
        ctk.CTkLabel(row, text=name + suffix,
                     text_color=self.INK if kind == "done" else self.FAINT,
                     anchor="w", font=_font(12)
                     ).pack(side="left", fill="x", expand=True)

        thoughts_mb = 0.0
        if kind == "done":
            reviews = path / "reviews.jsonl"
            if reviews.is_file():
                try:
                    thoughts_mb = reviews.stat().st_size / 1048576.0
                except OSError:
                    thoughts_mb = 0.0
        if thoughts_mb >= 1.0:
            ctk.CTkLabel(row, text="想法 %.0f MB" % thoughts_mb,
                         text_color=self.AMBER, font=_font(11)
                         ).pack(side="right", padx=(0, 8))
        ctk.CTkLabel(row, text="%.1f MB" % size_mb, text_color=self.FAINT,
                     font=_font(11)).pack(side="right", padx=(0, 6))

        ctk.CTkButton(row, text="删除", width=48, height=26,
                      corner_radius=6, fg_color=self.CARD,
                      hover_color=self.RED_TINT,
                      border_width=1, border_color="#D5D9DF",
                      text_color=self.RED, font=_font(11),
                      command=lambda r=(name, size_mb, None, kind, path):
                      self._delete_exports([r])
                      ).pack(side="right", padx=(0, 4))
        if thoughts_mb >= 1.0:
            ctk.CTkButton(row, text="删想法", width=60, height=26,
                          corner_radius=6, fg_color=self.CARD,
                          hover_color=self.AMBER_TINT,
                          border_width=1, border_color="#D5D9DF",
                          text_color=self.AMBER, font=_font(11),
                          command=lambda p=path: self._delete_thoughts(p)
                          ).pack(side="right", padx=(0, 4))
        ctk.CTkButton(row, text="打开", width=48, height=26,
                      corner_radius=6, fg_color=self.CARD, hover_color=self.SUNKEN,
                      border_width=1, border_color="#D5D9DF", text_color=self.MUTED,
                      font=_font(11),
                      command=lambda n=name: self._open_export(n)
                      ).pack(side="right")

    def load_shelf(self):
        """Ask the worker for the account's shelf; the reply lands on the queue."""
        if self._is_busy():
            self.status.set("当前有任务正在运行，等它结束再刷新书架。")
            return
        if not self._start_worker(["--shelf"], "shelf", "正在读取微信书架…"):
            self.status.set("书架读取没有启动。请检查登录状态后重试。")

    def _show_shelf(self, rows):
        """Render the account's shelf into the shelf frame."""
        if not hasattr(self, "shelf_frame") or not self.shelf_frame.winfo_exists():
            return
        for widget in self.shelf_frame.winfo_children():
            widget.destroy()
        if not rows:
            ctk.CTkLabel(self.shelf_frame,
                         text="书架是空的，或登录状态已过期。\n\n点「刷新书架」重试，或先「检查登录」。",
                         text_color=self.FAINT, font=_font(12), justify="center"
                         ).pack(expand=True, pady=40)
            return
        for book in rows:
            self._build_shelf_row(book)
        self.status.set("已读取微信书架，共 %d 本。" % len(rows))

    def _build_shelf_row(self, book):
        """One shelf row: cover, title, author, category, and a download action."""
        book_id = str(book.get("bookId") or "")
        if not book_id:
            return
        title = str(book.get("title") or "").strip() or "未命名书籍"
        author = str(book.get("author") or "").strip() or "作者未知"
        category = str(book.get("category") or "").strip()

        row = ctk.CTkFrame(self.shelf_frame, fg_color=self.CARD, corner_radius=6,
                           border_width=1, border_color=self.HAIRLINE, height=56)
        row.pack(fill="x", pady=2, padx=2)
        row.pack_propagate(False)

        cover = ctk.CTkLabel(row, text="", width=36, height=48, corner_radius=3,
                             fg_color=self.SUNKEN)
        cover.pack(side="left", padx=(10, 10), pady=4)

        text_col = ctk.CTkFrame(row, fg_color="transparent")
        text_col.pack(side="left", fill="both", expand=True, pady=6)
        ctk.CTkLabel(text_col, text=title, text_color=self.INK, anchor="w",
                     font=_font(13, "bold")).pack(fill="x")
        meta = author + (" · " + category if category else "")
        ctk.CTkLabel(text_col, text=meta, text_color=self.FAINT, anchor="w",
                     font=_font(11)).pack(fill="x", pady=(1, 0))

        ctk.CTkButton(row, text="下载", width=64, height=30,
                      corner_radius=6, fg_color=self.ACCENT,
                      hover_color=self.ACCENT_HOVER, text_color="#FFFFFF",
                      font=_font(12),
                      command=lambda b=book_id, t=title:
                      self._download_from_shelf(b, t)
                      ).pack(side="right", padx=(0, 12))

        def choose(_event=None, b=book_id, t=title):
            self._download_from_shelf(b, t)
        for widget in (row, cover, text_col, *text_col.winfo_children()):
            widget.bind("<Button-1>", choose)

        url = str(book.get("cover") or "").strip()
        if url:
            self._request_shelf_cover(cover, url)

    def _request_shelf_cover(self, label, url):
        """Load a cover in the background; failures leave the placeholder."""
        def fetch():
            image = None
            try:
                response = requests.get(url, timeout=(5, 15))
                response.raise_for_status()
                if len(response.content) <= 8 * 1024 * 1024:
                    image = _decode_cover(response.content, (36, 48))
            except Exception:
                image = None
            self.events.put(("shelf-cover", (label, image)))
        threading.Thread(target=fetch, daemon=True).start()

    def _download_from_shelf(self, book_id, title):
        """Pick a shelf book and go straight to the download confirmation.

        Deliberately does NOT wait for the book-info fetch: the click means
        "download this", so the confirmation comes up immediately and the
        detail pane fills in behind it. export() owns the busy check and says
        so itself, which is a clearer answer than silently doing nothing.
        """
        if not book_id:
            return
        self._select_book({"bookId": book_id, "title": title})
        self._switch_page("下载工坊")
        self.export()

    def _delete_thoughts(self, path):
        """Drop a finished book's note data but keep its EPUB.

        MEASURED on this machine: 超神机械师-19480992 held a 183 MB
        reviews.jsonl next to a 10 MB EPUB -- 18x the book itself, for notes
        most readers never open. Deleting that single file gives back almost
        the whole folder while the readable book stays.

        The bundle zip embeds its own copy of the notes, so it goes too; the
        reader can still re-download them later with the notes mode on.
        """
        folder = Path(path)
        targets = [folder / "reviews.jsonl", folder / "*.weread.zip"]
        victims, total = [], 0
        for pattern in targets:
            try:
                if pattern.name.startswith("*"):
                    found = list(folder.glob(pattern.name))
                else:
                    found = [pattern] if pattern.is_file() else []
                for f in found:
                    size = f.stat().st_size
                    victims.append((f, size))
                    total += size
            except OSError:
                continue
        if not victims:
            self.status.set("这本书没有可删除的想法数据。")
            return
        mb = total / 1048576.0
        if not self._ask("askyesno", "删除想法数据",
                         "删除《%s》的想法数据（约 %.0f MB）？\n\n"
                         "EPUB 正文和划线会完整保留，只丢笔记正文与打包副本；\n"
                         "之后选「划线 + 想法」重新下载可以再取回。\n\n继续吗？"
                         % (folder.name, mb)):
            return
        removed, failed = 0, 0
        for f, _size in victims:
            try:
                f.unlink()
                removed += 1
            except Exception as exc:
                failed += 1
                self._persist_log("[DELETE_THOUGHTS_FAILED] %s: %s" % (f, exc))
        if removed:
            self._persist_log("[DELETE_THOUGHTS] %s: removed %d file(s), %.0f MB"
                              % (folder.name, removed, mb))
        self._refresh_page("我的书架")
        if failed:
            self.status.set("想法数据已删除，但有 %d 个文件删除失败（可能仍被占用）。" % failed)
        else:
            self.status.set("已删除《%s》的想法数据，回收约 %.0f MB，EPUB 保留。"
                            % (folder.name, mb))

    def _delete_exports(self, rows):
        """Delete export folders after a confirmation that says what is lost.

        The confirmation is not decoration. Deleting a finished book removes
        its EPUB permanently, while deleting a .work tree only costs the
        ability to resume, so the two must not share one wording.
        """
        if not rows:
            return
        finished = [r for r in rows if r[3] == "done"]
        others = [r for r in rows if r[3] != "done"]
        total_mb = sum(r[1] for r in rows)

        if finished and others:
            message = ("将删除 %d 项，共约 %.0f MB。\n\n"
                       "其中 %d 本是已完成的书（EPUB 会永久删除），"
                       "%d 项是半成品或残留。\n\n继续吗？"
                       % (len(rows), total_mb, len(finished), len(others)))
        elif finished:
            if len(finished) == 1:
                message = ("删除《%s》？\n\n它的 EPUB 和全部附带数据会永久删除，"
                           "无法恢复。\n\n继续吗？" % finished[0][0])
            else:
                message = ("删除 %d 本已完成的书？\n\n它们的 EPUB 会永久删除，"
                           "共约 %.0f MB，无法恢复。\n\n继续吗？"
                           % (len(finished), total_mb))
        else:
            message = ("清理 %d 项半成品或残留，共约 %.0f MB？\n\n"
                       "已完成的书不受影响；下次下载同一本书会从头开始，"
                       "不能续传。\n\n继续吗？" % (len(others), total_mb))

        if not self._ask("askyesno", "确认删除", message):
            return

        removed, failed = [], []
        for name, _size, _mtime, _kind, path in rows:
            try:
                shutil.rmtree(str(path))
                removed.append(name)
            except Exception as exc:
                failed.append((name, str(exc)))
                self._persist_log("[DELETE_FAILED] %s: %s" % (name, exc))
        if removed:
            self._persist_log("[DELETE] removed %d export folder(s): %s"
                              % (len(removed), ", ".join(removed)))
        self._refresh_page("我的书架")
        if failed:
            self.status.set("已删除 %d 项，%d 项删除失败（可能仍被占用），详见技术日志。"
                            % (len(removed), len(failed)))
        else:
            self.status.set("已删除 %d 项，回收约 %.0f MB。"
                            % (len(removed), total_mb))

    def _create_download_history_page(self, host):
        """下载记录：the worker log's DONE lines, newest first."""
        page, card = self._page_scaffold(self.page_host, "下载记录")
        rows = self._read_history()
        if not rows:
            ctk.CTkLabel(card, text="还没有下载记录。\n\n每次完成的下载会在这里留一条。",
                         text_color=self.FAINT, font=_font(13), justify="center"
                         ).pack(expand=True)
            return
        list_frame = ctk.CTkScrollableFrame(card, fg_color=self.CARD, corner_radius=0)
        list_frame.pack(fill="both", expand=True, padx=12, pady=10)
        for stamp, line in rows:
            row = ctk.CTkFrame(list_frame, fg_color="transparent")
            row.pack(fill="x", pady=2)
            ctk.CTkLabel(row, text=stamp, text_color=self.GREEN, width=130,
                         font=_font(11, "normal", MONO), anchor="w").pack(side="left")
            ctk.CTkLabel(row, text=line, text_color=self.MUTED, anchor="w",
                         font=_font(12)).pack(side="left", fill="x", expand=True)

    def _create_statistics_page(self, host):
        """统计分析：what the exports directory holds, and what it wastes."""
        page, card = self._page_scaffold(self.page_host, "统计分析")
        rows = self._scan_exports()
        done = [r for r in rows if r[3] == "done"]
        failed = [r for r in rows if r[3] == "failed"]
        reclaimable = [r for r in rows if r[3] in ("partial", "stale", "failed")]
        total_mb = sum(r[1] for r in rows)
        reclaim_mb = sum(r[1] for r in reclaimable)
        # Note data is the other half of the real cost: 183 MB beside a 10 MB
        # EPUB on this machine. Show it so "where did my disk go" has an answer.
        thoughts_mb = 0.0
        for _n, _s, _m, kind, path in rows:
            if kind == "done":
                reviews = path / "reviews.jsonl"
                if reviews.is_file():
                    try:
                        thoughts_mb += reviews.stat().st_size / 1048576.0
                    except OSError:
                        pass
        newest = max((r[2] for r in done), default=None)
        stats = ctk.CTkFrame(card, fg_color="transparent")
        stats.pack(fill="x", padx=18, pady=(16, 8))
        for key, value, tint in (("已完成的书", str(len(done)), self.GREEN),
                                 ("想法数据", "%.0f MB" % thoughts_mb, self.AMBER),
                                 ("占用总计", "%.1f MB" % total_mb, self.AMBER),
                                 ("可回收", "%.1f MB" % reclaim_mb,
                                  self.RED if reclaim_mb > 200 else self.GREEN)):
            box = ctk.CTkFrame(stats, fg_color=self.SUNKEN, corner_radius=6)
            box.pack(side="left", fill="x", expand=True, padx=(0, 12))
            ctk.CTkLabel(box, text=key, text_color=self.FAINT, height=1,
                         font=_font(11)).pack(anchor="w", padx=14, pady=(10, 2))
            ctk.CTkLabel(box, text=value, text_color=tint, height=1,
                         font=_font(17, "bold")).pack(anchor="w", padx=14, pady=(0, 10))
        if newest:
            ctk.CTkLabel(card, text="最近完成：%s" % newest.strftime("%Y-%m-%d %H:%M"),
                         text_color=self.FAINT, font=_font(12), anchor="w"
                         ).pack(anchor="w", padx=18, pady=(4, 0))

        note = "这些数字来自本机导出目录的实时扫描。"
        if reclaim_mb > 200:
            note += ("\n\n可回收 %.0f MB 是未完成、半成品与残留目录，"
                     "在「我的书架」里可以安全清理。" % reclaim_mb)
        if thoughts_mb > 100:
            note += ("\n\n想法数据占 %.0f MB（正文之外的笔记与打包副本）；"
                     "在「我的书架」里对某本书点「删想法」可只释放这部分，保留 EPUB。"
                     % thoughts_mb)
        ctk.CTkLabel(card, text=note, text_color=self.FAINT, font=_font(12),
                     justify="left").pack(anchor="w", padx=18, pady=(8, 16))

    def _create_highlights_page(self, host):
        """划线清单：what the engine refused to place, from the log."""
        page, card = self._page_scaffold(self.page_host, "划线清单")
        rows = self._read_annotation_notes()
        if not rows:
            ctk.CTkLabel(card, text="没有需要留意的划线记录。\n\n"
                                    "当某条划线无法安全落点时，引擎会拒绝标注而不是标错位置，\n"
                                    "这类决定会记录在这里供你复核。",
                         text_color=self.FAINT, font=_font(13), justify="center"
                         ).pack(expand=True)
            return
        list_frame = ctk.CTkScrollableFrame(card, fg_color=self.CARD, corner_radius=0)
        list_frame.pack(fill="both", expand=True, padx=12, pady=10)
        for stamp, line in rows:
            row = ctk.CTkFrame(list_frame, fg_color="transparent")
            row.pack(fill="x", pady=2)
            ctk.CTkLabel(row, text=stamp, text_color=self.AMBER, width=130,
                         font=_font(11, "normal", MONO), anchor="w").pack(side="left")
            ctk.CTkLabel(row, text=line, text_color=self.MUTED, anchor="w",
                         font=_font(12), wraplength=560, justify="left"
                         ).pack(side="left", fill="x", expand=True)

    def _create_settings_page(self, host):
        """设置：real, small, and honest about what each item does."""
        page, card = self._page_scaffold(self.page_host, "设置")

        def folder_row(label, path, opener):
            row = ctk.CTkFrame(card, fg_color="transparent")
            row.pack(fill="x", padx=18, pady=(12, 0))
            ctk.CTkLabel(row, text=label, text_color=self.INK, anchor="w",
                         font=_font(13, "bold")).pack(fill="x")
            ctk.CTkLabel(row, text=str(path), text_color=self.FAINT, anchor="w",
                         font=_font(11, "normal", MONO), wraplength=560,
                         justify="left").pack(fill="x")
            self._quiet_button(row, "打开文件夹", 110, opener).pack(anchor="w", pady=(6, 0))

        folder_row("下载目录", OUTPUT, self.open_output)
        folder_row("技术日志", LOG_DIR, self.open_logs)
        ctk.CTkFrame(card, height=1, fg_color=self.HAIRLINE).pack(fill="x", padx=18, pady=(14, 0))
        ctk.CTkLabel(card, text="登录", text_color=self.INK, anchor="w",
                     font=_font(13, "bold")).pack(anchor="w", padx=18, pady=(12, 0))
        ctk.CTkLabel(card, text="扫码凭证只保存在本机；「退出登录」删除的是会话文件，"
                                "weread-live-settings.lua 里的基础凭证不受影响。",
                     text_color=self.FAINT, anchor="w", font=_font(12),
                     wraplength=560, justify="left").pack(anchor="w", padx=18, pady=(2, 0))
        btns = ctk.CTkFrame(card, fg_color="transparent")
        btns.pack(fill="x", padx=18, pady=(8, 16))
        self._quiet_button(btns, "登录 / 切换账号", 136, self.open_login).pack(side="left")
        self._quiet_button(btns, "检查登录状态", 118, self.check_auth).pack(side="left", padx=(8, 0))

    def _ask(self, kind: str, title: str, message: str, **kwargs) -> str:
        """Run a modal dialog with the main window as its parent, and log it.

        A modal takes over the Tk main loop until the user answers, so the
        window stops pumping messages and Windows paints it as "(未响应)". Two
        things reduce that risk: parenting the dialog to the root (so it centres
        and raises instead of hiding behind the window), and writing a marker
        line to the log so a stall that *was* a dialog is provable afterwards
        rather than guessed at.

        It also raises self._modal_depth for the duration. The stall watchdog
        cannot otherwise tell "the user has not answered a dialog yet" from
        "the main thread is wedged": during a GUI pass, dialog waits produced
        87 s and 78 s entries that looked exactly like the real 95 s startup
        stall, and the genuine one was buried in that noise.
        """
        self._persist_log("[MODAL:%s] %s | %s" % (kind, title, message.splitlines()[0]))
        self._modal_depth += 1
        try:
            return getattr(messagebox, kind)(title, message, parent=self.root, **kwargs)
        except Exception:
            return "no"
        finally:
            self._modal_depth -= 1

    def _quiet_button(self, parent, text: str, width: int, command, **kwargs):
        """Secondary action: outlined on the card surface, never a filled slab."""
        return ctk.CTkButton(
            parent, text=text, width=width, height=34, command=command,
            corner_radius=self.RADIUS, fg_color=self.CARD,
            hover_color=self.SUNKEN, text_color=self.MUTED,
            border_width=1, border_color="#D5D9DF",
            font=_font(13), **kwargs)

    def _stat_card(self, parent, key: str, initial: str, tint: str):
        """One of the reference design's statistic cards: grey label, big number.

        The value is kept in a dict keyed by `key` so later updates go through
        _set_stat() instead of reaching into the widget tree.
        """
        card = ctk.CTkFrame(parent, fg_color=self.CARD, corner_radius=self.RADIUS,
                            border_width=1, border_color=self.HAIRLINE)
        card.pack(side="left", fill="both", expand=True, padx=(0, 12))
        ctk.CTkLabel(card, text=key, text_color=self.FAINT, height=1,
                     font=_font(11), anchor="w").pack(fill="x", padx=16, pady=(8, 1))
        value = ctk.CTkLabel(card, text=initial, text_color=tint, height=1,
                             font=_font(18, "bold"), anchor="w")
        value.pack(fill="x", padx=16, pady=(0, 8))
        return value

    def _build(self):
        self._stats = {}
        # ---- overall structure: dark rail + white top bar + content -----
        self.root.configure(fg_color=self.BG)
        shell = ctk.CTkFrame(self.root, fg_color=self.BG, corner_radius=0)
        shell.pack(fill="both", expand=True)

        # ---- sidebar (the reference's dark rail) -------------------------
        side = ctk.CTkFrame(shell, width=216, corner_radius=0, fg_color=self.SIDE_BG)
        side.pack(side="left", fill="y")
        side.pack_propagate(False)

        # No brand block in the rail. The window title already carries the name,
        # and this used to repeat it a second time right beside it. The logo that
        # sat here was a CTkLabel painted with ACCENT -- a green "读" placeholder,
        # not the artwork in weread-app.ico, so it matched neither the shortcut
        # nor the title bar. One name and one icon, both in the title bar.

        ctk.CTkFrame(side, height=1, fg_color="#2A3646").pack(fill="x", padx=16, pady=(14, 10))

        nav = ctk.CTkFrame(side, fg_color="transparent")
        nav.pack(fill="both", expand=True, padx=10)
        self.nav_items = {}
        self.nav_items["下载工坊"] = self._nav_item(nav, "下载工坊", "◇", active=True,
                                                    command=lambda: self._switch_page("下载工坊"))
        self.nav_items["我的书架"] = self._nav_item(nav, "我的书架", "▤",
                                                    command=lambda: self._switch_page("我的书架"))
        self.nav_items["下载记录"] = self._nav_item(nav, "下载记录", "↺",
                                                    command=lambda: self._switch_page("下载记录"))
        self.nav_items["统计分析"] = self._nav_item(nav, "统计分析", "▥",
                                                    command=lambda: self._switch_page("统计分析"))
        self.nav_items["划线清单"] = self._nav_item(nav, "划线清单", "≡",
                                                    command=lambda: self._switch_page("划线清单"))

        ctk.CTkFrame(side, height=1, fg_color="#2A3646").pack(fill="x", padx=16, pady=8)
        self.nav_items["设置"] = self._nav_item(nav, "设置", "⚙",
                                                command=lambda: self._switch_page("设置"))

        # ---- right side: top bar + content ------------------------------
        main = ctk.CTkFrame(shell, fg_color=self.BG, corner_radius=0)
        main.pack(side="left", fill="both", expand=True)

        topbar = ctk.CTkFrame(main, height=52, corner_radius=0, fg_color=self.CARD)
        topbar.pack(fill="x")
        topbar.pack_propagate(False)
        # The global search lives in the top bar, as in the reference design;
        # it drives the same search() the old panel input did.
        self.search_entry = ctk.CTkEntry(
            topbar, placeholder_text="搜索书名、作者或关键词…", height=34,
            corner_radius=6, fg_color=self.SUNKEN, border_color="#DDE1E6",
            text_color=self.INK, font=_font(13))
        self.search_entry.pack(side="left", fill="x", expand=True,
                               padx=(24, 8), pady=(0, 0), ipadx=0)
        self.search_entry.bind("<Return>", lambda _event: self.search())
        self.search_button = ctk.CTkButton(
            topbar, text="搜索", width=60, height=34, command=self.search,
            corner_radius=6, fg_color=self.ACCENT, hover_color=self.ACCENT_HOVER,
            text_color="#FFFFFF", font=_font(13))
        self.search_button.pack(side="left", pady=(0, 0))

        self.auth_badge = ctk.CTkLabel(topbar, text="尚未检查", text_color=self.MUTED,
                                       fg_color=self.SUNKEN, corner_radius=6,
                                       padx=10, pady=5, font=_font(12))
        self.auth_badge.pack(side="right", padx=(0, 24))
        self.refresh_button = self._quiet_button(topbar, "刷新", 70, self.check_auth)
        self.refresh_button.configure(height=32)
        self.refresh_button.pack(side="right", padx=(0, 10))
        self.login_button = ctk.CTkButton(
            topbar, text="登录 / 切换账号", width=136, height=34,
            corner_radius=6, fg_color=self.ACCENT,
            hover_color=self.ACCENT_HOVER, text_color="#FFFFFF",
            font=_font(13), command=self.open_login)
        self.login_button.pack(side="right")

        # ---- page container ----------------------------------------------
        # Every sidebar entry owns one page frame stacked in page_host. The
        # download workshop's content is built directly into its frame below;
        # the other five are created on demand by _create_page().
        self.pages = {}
        self._current_page = "下载工坊"

        self.page_host = ctk.CTkFrame(main, fg_color=self.BG, corner_radius=0)
        self.page_host.pack(fill="both", expand=True, padx=24, pady=(10, 10))

        page = ctk.CTkFrame(self.page_host, fg_color=self.BG, corner_radius=0)
        page.pack(fill="both", expand=True)
        self.pages["下载工坊"] = page

        head_row = ctk.CTkFrame(page, fg_color="transparent")
        head_row.pack(fill="x")
        ctk.CTkLabel(head_row, text="下载工坊", text_color=self.INK,
                     font=_font(18, "bold")).pack(side="left")
        self.result_count = ctk.CTkLabel(head_row, text="搜索结果会显示在这里",
                                         text_color=self.FAINT, font=_font(12))
        self.result_count.pack(side="left", padx=(16, 0), pady=(4, 0))

        # ---- stat cards ---------------------------------------------------
        stats = ctk.CTkFrame(page, fg_color="transparent", height=70)
        stats.pack(fill="x", pady=(10, 0))
        stats.pack_propagate(False)
        self._stats["books"] = self._stat_card(stats, "已导出书籍", "—", self.GREEN)
        self._stats["marks"] = self._stat_card(stats, "累计划线", "—", self.AMBER)
        self._stats["active"] = self._stat_card(stats, "下载中", "0", self.GREEN)

        # ---- main grid: results card + detail card -----------------------
        self.body = ctk.CTkFrame(page, fg_color="transparent")
        self.body.pack(fill="both", expand=True, pady=(10, 0))
        self.body.grid_columnconfigure(0, weight=0)
        self.body.grid_columnconfigure(1, weight=1)
        self.body.grid_rowconfigure(0, weight=1)

        left = ctk.CTkFrame(self.body, fg_color=self.CARD, corner_radius=self.RADIUS,
                            border_width=1, border_color=self.HAIRLINE, width=380)
        left.grid(row=0, column=0, sticky="nsw", padx=(0, 14))
        left.pack_propagate(False)
        self._section(left, "①", "搜索结果", padx=18, pady=10)
        self.results = ctk.CTkScrollableFrame(
            left, fg_color=self.CARD, corner_radius=0,
            scrollbar_button_color=self.HAIRLINE, scrollbar_button_hover_color=self.ACCENT_TINT)
        self.results.pack(fill="both", expand=True, padx=(10, 6), pady=(0, 4))

        # Results are paged rather than all built at once. Repainting a
        # CTkScrollableFrame is synchronous and its cost grows with the number
        # of child widgets: building one page of 20 costs ~2 s of blocked main
        # thread, and a second search adds ~1 s destroying the old cards --
        # together enough for Windows to declare the window unresponsive.
        self.page_bar = ctk.CTkFrame(left, fg_color="transparent")
        self.page_bar.pack(side="bottom", fill="x", padx=18, pady=(0, 10))
        self.prev_button = self._quiet_button(
            self.page_bar, "上一页", 84, lambda: self._turn_page(-1))
        self.prev_button.pack(side="left")
        self.page_label = ctk.CTkLabel(
            self.page_bar, text="", text_color=self.MUTED, font=_font(12))
        self.page_label.pack(side="left", padx=12)
        self.next_button = self._quiet_button(
            self.page_bar, "下一页", 84, lambda: self._turn_page(1))
        self.next_button.pack(side="left")
        self._set_page_buttons(0, 0)

        right = ctk.CTkFrame(self.body, fg_color=self.CARD, corner_radius=self.RADIUS,
                             border_width=1, border_color=self.HAIRLINE)
        right.grid(row=0, column=1, sticky="nsew")

        self._section(right, "②", "确认书籍", padx=20, pady=10)
        info_row = ctk.CTkFrame(right, fg_color=self.CARD)
        info_row.pack(fill="x", padx=20, pady=(2, 0))
        self.cover = ctk.CTkLabel(info_row, text="封面",
                                  width=self.DETAIL_COVER_W, height=self.DETAIL_COVER_H,
                                  fg_color=self.SUNKEN, corner_radius=6,
                                  text_color=self.FAINT, font=_font(12))
        self.cover.pack(side="left", anchor="n", padx=(0, 16))
        meta = ctk.CTkFrame(info_row, fg_color=self.CARD)
        meta.pack(side="left", fill="both", expand=True)
        # CTkLabel defaults to height=28, so every label here claimed 35 px even
        # while empty. Asking for height=1 lets each one shrink to its text,
        # which is what hands the space to the introduction.
        self.book_title_label = ctk.CTkLabel(
            meta, text="先从左侧搜索并点击一本书", text_color=self.INK, height=1,
            font=_font(18, "bold"), justify="left", anchor="w", wraplength=540)
        self.book_title_label.pack(fill="x", anchor="w", pady=(0, 4))
        # Author and category share a line, rating and id share the next one:
        # the introduction needs the height more than a five-line metadata
        # block does.
        self.author_label = ctk.CTkLabel(meta, text="", text_color=self.MUTED, height=1,
                                         font=_font(12), anchor="w", wraplength=540)
        self.author_label.pack(fill="x", pady=(0, 2))
        self.category_label = ctk.CTkLabel(meta, text="", text_color=self.MUTED, height=1,
                                           font=_font(12), anchor="w", wraplength=540)
        self.category_label.pack(fill="x", pady=(0, 2))
        self.rating_label = ctk.CTkLabel(meta, text="", text_color=self.AMBER, height=1,
                                         font=_font(12), anchor="w")
        self.rating_label.pack(fill="x", pady=2)
        self.id_label = ctk.CTkLabel(meta, text="", text_color=self.FAINT, height=1,
                                     font=_font(11, "normal", MONO), anchor="w")
        self.id_label.pack(fill="x", pady=(1, 0))

        ctk.CTkFrame(right, height=1, fg_color=self.HAIRLINE_SOFT).pack(fill="x", padx=20, pady=(9, 0))
        ctk.CTkLabel(right, text="简介", text_color=self.FAINT, height=1,
                     font=_font(12)).pack(anchor="w", padx=20, pady=(6, 2))
        # The introduction is the one part of this column worth real estate, so
        # it expands into whatever the fixed rows leave over. The requested
        # height is deliberately small: pack hands the slack to an expanding
        # child, but if the request alone were larger than the leftover the
        # rows below would be squeezed to nothing.
        self.intro = ctk.CTkTextbox(
            right, height=90, fg_color=self.SUNKEN, corner_radius=8,
            border_width=1, border_color=self.HAIRLINE,
            wrap="word", text_color=self.MUTED, font=_font(13))
        self.intro.pack(fill="both", expand=True, padx=20, pady=(0, 0))
        self.intro.insert("1.0", "点击左侧搜索结果后，这里会显示简介和书籍详情。")
        self.intro.configure(state="disabled")

        # ---- III download: its own card, as in the reference design ------
        # The right column keeps book identity + intro; the action zone moves
        # below both columns so it spans the full width, exactly like the
        # reference's bottom "③ 下载到电脑" panel.
        export = ctk.CTkFrame(page, fg_color=self.CARD, corner_radius=self.RADIUS,
                              border_width=1, border_color=self.HAIRLINE)
        export.pack(fill="x", pady=(10, 0))
        export.grid_columnconfigure(0, weight=0)
        self._section(export, "③", "下载到电脑",
                      grid_at={"row": 0, "column": 0, "columnspan": 4, "sticky": "w",
                               "padx": 18, "pady": (8, 0)})
        # 并发 rides on the section row instead of taking a row of its own, so
        # the introduction keeps the vertical space.
        ctk.CTkLabel(export, text="并发", text_color=self.FAINT,
                     font=_font(12)).grid(row=0, column=1, sticky="e",
                                          padx=(0, 6), pady=(12, 0))
        self.jobs_box = ctk.CTkComboBox(
            export, values=["1", "2", "3", "4"], width=56, height=28,
            corner_radius=6, fg_color=self.CARD, border_color="#D5D9DF",
            text_color=self.INK, font=_font(12), dropdown_fg_color=self.CARD,
            dropdown_text_color=self.INK, button_color=self.HAIRLINE,
            button_hover_color=self.SUNKEN, variable=self.jobs, state="readonly")
        self.jobs_box.grid(row=0, column=2, sticky="e", padx=(0, 6), pady=(12, 0))
        self._make_combo_keyboard_reachable(self.jobs_box)
        # 标注 has three states, not two, because the note text and the highlights
        # are separable. A plain-text export wants neither; --no-thoughts wants
        # highlights but no notes; the default wants both. Two switches could not
        # say that without an invalid combination (notes without highlights, since
        # a note hangs off a highlight).
        self.annotations_var = ctk.StringVar(value="both")
        self.annotations_box = ctk.CTkComboBox(
            export, values=("划线 + 想法", "只要划线", "纯正文"),
            variable=self.annotations_var, width=104, height=28, state="readonly",
            dropdown_font=_font(12), font=_font(12),
            # Not fg_color="transparent": that is a Frame-only colour in
            # CustomTkinter, and a CTkComboBox rejects it outright with
            # "transparency is not allowed for this attribute". The top bar is
            # CARD, so use that and let the border carry the outline.
            fg_color=self.CARD, border_width=1, border_color=self.HAIRLINE,
            text_color=self.INK, button_color=self.HAIRLINE,
            button_hover_color=self.SUNKEN)
        self.annotations_box.grid(row=0, column=3, sticky="e", padx=(0, 10), pady=(12, 0))
        self._make_combo_keyboard_reachable(self.annotations_box)
        self.annotations_box.configure(state="disabled")
        self.probe_button = ctk.CTkButton(
            export, text="先检查第一章", command=self.probe, height=36,
            corner_radius=6, fg_color=self.CARD, hover_color=self.SUNKEN,
            border_width=1, border_color="#D5D9DF",
            text_color=self.MUTED, font=_font(13))
        self.probe_button.grid(row=1, column=0, padx=(18, 6), pady=(6, 10), sticky="ew")
        self.export_button = ctk.CTkButton(
            export, text="开始下载 / 继续下载", height=36, command=self.export,
            corner_radius=6, fg_color=self.ACCENT, hover_color=self.ACCENT_HOVER,
            text_color="#FFFFFF", font=_font(13))
        self.export_button.grid(row=1, column=1, columnspan=3, padx=(6, 18),
                                pady=(6, 10), sticky="ew")

        # --- credentials, inline ------------------------------------------------
        # No dialog, no second window: when the credentials are incomplete this
        # panel takes the place of the buttons and says what is missing. It
        # hides itself again the moment load_credentials() would succeed, so the
        # window is the only UI a new user ever has to look at.
        self.creds_panel = ctk.CTkFrame(export, fg_color=self.SUNKEN,
                                         corner_radius=8)
        self.creds_panel.grid(row=1, column=0, columnspan=4, sticky="ew",
                              padx=18, pady=(6, 10))
        self.creds_panel.grid_remove()          # shown only when incomplete

        ctk.CTkLabel(self.creds_panel, text="需要两个凭证才能下载",
                     text_color=self.INK, anchor="w",
                     font=_font(14, "bold")).pack(fill="x", padx=18, pady=(14, 4))

        self.creds_hint = ctk.CTkLabel(
            self.creds_panel, text="", text_color=self.FAINT, anchor="w",
            font=_font(12), wraplength=560, justify="left")
        self.creds_hint.pack(fill="x", padx=18)

        # wr_skey only: the QR login fills it, so a field is the wrong control.
        self.scan_only_frame = ctk.CTkFrame(self.creds_panel, fg_color="transparent")
        ctk.CTkLabel(self.scan_only_frame,
                     text="wr_skey 由「扫码登录」自动获取，无需手动填写。",
                     text_color=self.FAINT, anchor="w", font=_font(12),
                     wraplength=520, justify="left").pack(fill="x", pady=(10, 6))
        self.scan_now_button = ctk.CTkButton(
            self.scan_only_frame, text="去扫码登录", height=34, command=self.open_login,
            corner_radius=6, fg_color=self.ACCENT, hover_color=self.ACCENT_HOVER,
            text_color="#FFFFFF", font=_font(13))
        self.scan_now_button.pack(anchor="w")

        # Both fields: first run, or a credential file that cannot be read.
        self.fields_frame = ctk.CTkFrame(self.creds_panel, fg_color="transparent")
        ctk.CTkLabel(self.fields_frame, text="API Key（以 wrk- 开头）",
                     text_color=self.INK, anchor="w",
                     font=_font(12)).pack(fill="x", pady=(12, 0))
        ctk.CTkLabel(self.fields_frame,
                     text="在 weread.qq.com/r/weread-skills 登录后复制。不需要开开发者模式。",
                     text_color=self.FAINT, anchor="w", font=_font(11)
                     ).pack(fill="x", pady=(0, 4))
        self.creds_api_entry = ctk.CTkEntry(self.fields_frame, width=520, height=34,
                                            placeholder_text="wrk-xxxxxxxxxxxx")
        self.creds_api_entry.pack(fill="x")
        ctk.CTkLabel(self.fields_frame, text="wr_skey",
                     text_color=self.INK, anchor="w",
                     font=_font(12)).pack(fill="x", pady=(10, 0))
        ctk.CTkLabel(self.fields_frame,
                     text="扫码登录后自动填入；只有扫码失败时才需要手动粘贴。",
                     text_color=self.FAINT, anchor="w", font=_font(11)
                     ).pack(fill="x", pady=(0, 4))
        self.creds_skey_entry = ctk.CTkEntry(self.fields_frame, width=520, height=34)
        self.creds_skey_entry.pack(fill="x")
        creds_buttons = ctk.CTkFrame(self.fields_frame, fg_color="transparent")
        creds_buttons.pack(fill="x", pady=(12, 6))
        ctk.CTkButton(creds_buttons, text="保存", width=96, height=32,
                      command=self._save_creds_inline,
                      corner_radius=6, fg_color=self.ACCENT,
                      hover_color=self.ACCENT_HOVER, text_color="#FFFFFF",
                      font=_font(13)).pack(side="left")
        ctk.CTkButton(creds_buttons, text="打开 skills 页面", width=140, height=32,
                      command=lambda: __import__("webbrowser").open(SKILLS_URL),
                      corner_radius=6, fg_color=self.CARD, hover_color=self.SUNKEN,
                      border_width=1, border_color="#D5D9DF",
                      text_color=self.MUTED, font=_font(13)).pack(side="left",
                                                                 padx=(10, 0))
        self.creds_error = ctk.CTkLabel(self.fields_frame, text="", anchor="w",
                                        text_color=self.RED, font=_font(12),
                                        wraplength=520, justify="left")
        self.creds_error.pack(fill="x", pady=(4, 10))
        self.creds_api_entry.bind("<Return>", lambda _e: self.creds_skey_entry.focus_set())
        self.creds_skey_entry.bind("<Return>", lambda _e: self._save_creds_inline())

        # progress + status live on the same card, below the buttons
        self.progress = ctk.CTkProgressBar(
            export, height=6, corner_radius=3, progress_color=self.ACCENT,
            fg_color=self.HAIRLINE_SOFT)
        self.progress.grid(row=2, column=0, columnspan=4, sticky="ew",
                           padx=18, pady=(0, 6))
        self.progress.set(0)
        self.status_label = ctk.CTkLabel(
            export, textvariable=self.status, text_color=self.MUTED, height=1,
            font=_font(12), justify="left", anchor="w")
        self.status_label.grid(row=3, column=0, columnspan=4, sticky="w",
                               padx=18, pady=(0, 6))

        task_row = ctk.CTkFrame(export, fg_color="transparent")
        task_row.grid(row=4, column=0, columnspan=4, sticky="ew", padx=18, pady=(0, 10))
        self.pause_button = ctk.CTkButton(
            task_row, text="暂停下载", width=104, height=32, corner_radius=6,
            fg_color=self.RED_TINT, hover_color="#F6DEDE", text_color=self.RED,
            font=_font(12), command=self.stop, state="disabled")
        self.pause_button.pack(side="left")
        self.open_button = self._quiet_button(task_row, "打开下载目录", 128, self.open_output)
        self.open_button.pack(side="right")
        self.log_button = self._quiet_button(task_row, "查看技术日志", 118, self.open_logs)
        self.log_button.pack(side="right", padx=8)
        self.logout_button = self._quiet_button(
            task_row, "退出登录", 92, self.logout)
        self.logout_button.pack(side="right", padx=(0, 8))
        self.logout_button.configure(state="disabled")
        # No log panel in the window: it duplicated the "查看技术日志" button
        # and cost the space the book introduction actually needs. Worker lines
        # still go to the log file via _log()/_persist_log().

    @staticmethod
    def _make_combo_keyboard_reachable(box):
        """Let the 并发 box be driven from the keyboard, not just the arrow.

        CTkComboBox binds its own click handling to the "dropdown_arrow" /
        "right_parts" canvas tags, which is what opens the list, and that part
        works. What it never does is move keyboard focus to the inner ttk
        combobox, so after opening the list the arrow keys still did nothing.

        So this only takes focus; it deliberately does not open the dropdown
        itself. Opening it here would fire a second, competing open on every
        arrow click and would grab the pointer from a control that already
        handles its own clicks. Guarded because it reaches into customtkinter
        internals: a different layout must keep the stock behaviour, not raise.
        """
        canvas = getattr(box, "_canvas", None)
        if canvas is None:
            return

        def _take_focus(_event=None):
            try:
                box.focus_set()      # CTkComboBox forwards this to its entry
            except Exception:
                pass

        try:
            canvas.bind("<Button-1>", _take_focus, add="+")
        except Exception:
            pass

    def _handle_callback_exception(self, exc_type, exc_value, exc_traceback):
        """Must never raise: an exception escaping a Tk callback closes the app."""
        try:
            details = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))
            self._persist_log("[UI_CALLBACK_ERROR] " + redact_secrets(details))
        except Exception:
            try:
                self._persist_log("[UI_CALLBACK_ERROR] <详情无法写出>")
            except Exception:
                pass
        try:
            self.status.set("界面操作遇到问题，程序仍在运行。请重试；详细信息已写入技术日志。")
        except Exception:
            pass

    def _persist_log(self, text: str) -> None:
        try:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            safe_id = self.selected_id or "general"
            with (LOG_DIR / f"desktop-{safe_id}.log").open("a", encoding="utf-8") as stream:
                stream.write(redact_secrets(text) + "\n")
        except OSError:
            pass

    def _log(self, text: str) -> None:
        """Persist one worker line to the log file.

        There is no log panel in the window any more, so this no longer has to
        batch widget updates: the file is the single sink, and "查看技术日志"
        opens it. The buffer is kept only so a burst of worker output cannot
        make the write-per-line cost visible in one tick.
        """
        self._persist_log(text)
        self._log_buffer.append(redact_secrets(text))

    def _flush_log_panel(self) -> None:
        """Drop the buffered lines once per tick.

        The panel they used to feed is gone; the text is already in the log
        file. Clearing here keeps a burst of worker output from growing the
        buffer without bound between ticks.
        """
        self._log_buffer.clear()

    def _is_busy(self) -> bool:
        # A spawn in flight counts as busy: the user cannot start a second
        # worker while the first is still being created off-thread.
        if self._spawn_pending:
            return True
        return self.process is not None and self.process.poll() is None

    def _start_worker(self, args: list[str], action: str, status: str) -> bool:
        if self._is_busy():
            self.status.set("当前还有一个任务在运行，请等它结束再操作。")
            return False
        command = worker_command(args)
        if command is None:
            self._log(f"下载组件未找到：{WORKER_NAME}")
            self._ask("showerror", 
                "缺少下载组件",
                f"没有找到 {WORKER_NAME}。\n\n请确认程序目录完整（WeReadDesktop 与 WeReadWorker "
                "两个文件夹都在同一层），或重新解压一次程序。")
            return False
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        # The Popen itself runs OFF the Tk thread. MEASURED 2026-10-12: doing it
        # on the Tk thread inside a button callback froze the window for 23-26 s
        # ([SPAWN] ... the window was blocked for that long, five [STALL_STACK]
        # samples all sitting in subprocess._execute_child), because Windows
        # Defender scans a freshly deployed 13 MB exe plus a 3211-file plugin
        # tree before letting the first launch through. Everything below that
        # can block is state the user can see; the actual spawn cannot.
        #
        # While the spawn is in flight the app counts as busy, so a second
        # click cannot race a second process. The widgets flip when the process
        # actually exists, via the same event queue every worker line uses.
        self._spawn_pending = True
        self.current_action = action
        self.status.set(status)
        self._spawn_started = time.perf_counter()

        def spawn():
            spawn_started = time.perf_counter()
            try:
                process = subprocess.Popen(
                    command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, encoding="utf-8", errors="replace", bufsize=1, env=env,
                    cwd=str(Path(command[0]).parent),
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except OSError as exc:
                self.events.put(("spawn-failed", (action, f"{type(exc).__name__}: {exc}")))
                return
            spawn_ms = (time.perf_counter() - spawn_started) * 1000.0
            if spawn_ms > 500:
                self._persist_log(
                    "[SPAWN] worker %s took %.0f ms to start (spawned off the Tk "
                    "thread; the window was never blocked)" % (action, spawn_ms))
            self.events.put(("spawned", (action, process)))

        threading.Thread(target=spawn, daemon=True).start()
        return True

    def _handle_spawned(self, action: str, process) -> None:
        """Adopt a worker started off the Tk thread; runs on the Tk thread."""
        if not self._spawn_pending:
            # A newer action superseded this spawn; kill the orphan.
            try:
                process.kill()
            except Exception:
                pass
            return
        self._spawn_pending = False
        self.process = process
        self.job_seq += 1
        job = self.job_seq
        self.last_failure = False
        # A running worker already carries its own annotation decision, so
        # changing it now would silently not apply to this job.
        self.annotations_box.configure(state="disabled")
        self.last_unresolved = False
        self.omissions = None
        self.pause_button.configure(state="normal" if action == "export" else "disabled")
        self.reader = threading.Thread(target=self._read_output, args=(self.process, job), daemon=True)
        self.reader.start()

    def _handle_spawn_failed(self, action: str, detail: str) -> None:
        """The off-thread Popen raised; tell the user and unstick the widgets."""
        self._spawn_pending = False
        self.current_action = ""
        self._persist_log(f"[WORKER_START_ERROR] {detail}")
        self.status.set("任务没有启动。请查看技术日志后重试。")

    def _read_output(self, process, job: int):
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

    def _poll(self):
        # The refresh loop must survive anything the worker sends, otherwise the
        # window looks dead (or closes) after a failed search / detail request.
        #
        # Stall watchdog: Windows calls a window "not responding" when its
        # message pump goes unpumped for about five seconds, so the useful
        # number is the gap between the end of one tick and the start of the
        # next. Four separate causes were ruled out by measurement, so this
        # records the real one to the log file instead of guessing again.
        now = time.perf_counter()
        gap_ms = (now - self._last_tick_end) * 1000.0
        self._last_tick_end = now
        started = now
        try:
            self._drain_events()
        except Exception:
            self._handle_callback_exception(*sys.exc_info())
        finally:
            blocked_ms = (time.perf_counter() - started) * 1000.0
            if self._consume_resume():
                # The machine suspended us. The gap is wall time the Tk loop
                # never got a chance to run for, not a blocked thread.
                self._note_resume(gap_ms)
            elif self._modal_depth > 0:
                # A modal dialog holds the main loop on purpose until it is
                # answered, so that time is the user's, not a defect. Logging it
                # as a stall buried the real ones: a dialog left open for 87 s
                # produced an entry indistinguishable from a frozen thread.
                if gap_ms > STALL_LOG_MS or blocked_ms > STALL_LOG_MS:
                    self._persist_log(
                        "[STALL-MODAL] %s gap=%.0fms blocked=%.0fms action=%s "
                        "(a dialog is waiting for an answer)"
                        % (time.strftime("%H:%M:%S"), gap_ms, blocked_ms,
                           self.current_action or "-"))
            elif gap_ms > STALL_LOG_MS or blocked_ms > STALL_LOG_MS:
                # Timestamped, because a stall has to be correlated with the
                # sampler's own output to be worth anything.
                self._persist_log(
                    "[STALL] %s gap=%.0fms blocked=%.0fms action=%s queue=%d"
                    % (time.strftime("%H:%M:%S"), gap_ms, blocked_ms,
                       self.current_action or "-", self.events.qsize()))
            try:
                self.root.after(100, self._poll)
            except Exception:
                pass

    def _consume_resume(self):
        """True once if the sampler saw the process get suspended since last tick.

        The sampler asks for time.sleep(0.2) and measures what it really got. A
        machine that suspends makes that return tens of seconds late; a thread
        that is merely wedged anywhere in the process does not, because the
        sampler keeps running. That is what separates "the OS took the CPU away"
        from "the Tk thread is stuck", which the wall clock cannot tell apart.
        The mark is only ever set on a real overrun, so it is consumed as-is.
        """
        if not self._resume_mark:
            return False
        self._resume_mark = 0.0
        return True

    def _note_resume(self, gap_ms):
        """Record a sleep as one, and repaint, because that is the real need.

        Windows marks a window "(未响应)" from the message pump going quiet, and a
        machine that suspends for a minute stops the pump without the app being at
        fault. The window also comes back holding whatever was on screen before
        the suspend, so the redraw is what the user actually needs.
        """
        self._persist_log(
            "[RESUME] %s gap=%.0fms action=%s "
            "(the OS suspended this process; the Tk loop was not blocked)"
            % (time.strftime("%H:%M:%S"), gap_ms, self.current_action or "-"))
        try:
            self.root.after(0, self._redraw_after_resume)
        except Exception:
            pass

    def _redraw_after_resume(self):
        try:
            self.root.update_idletasks()
            self._flush_log_panel()
        except Exception:
            self._handle_callback_exception(*sys.exc_info())

    def _watch_for_stalls(self):
        """Sample the Tk thread while it is stuck, because _poll cannot.

        A stall is only visible after it ends, so logging the stack from _poll
        would just record _poll. This thread is independent of the main loop and
        polls the main thread's frames, so it captures where the Tk thread was
        actually sitting during the freeze. Every candidate reached from the
        callbacks has already been measured and is fast (widget build 153 ms,
        worker start 0.7 s, Popen 13 ms), and guessing produced four wrong
        answers, so the next occurrence is recorded rather than inferred.
        """
        main_id = threading.main_thread().ident
        reported = 0.0
        while True:
            # Measure the sleep instead of trusting the argument: this thread is
            # not blocked by whatever wedges the Tk thread, so a sleep that comes
            # back far later than it was asked for means the OS suspended the
            # whole process, not that the app is stuck.
            asked = time.perf_counter()
            time.sleep(0.2)
            overran_ms = (time.perf_counter() - asked - 0.2) * 1000.0
            if overran_ms > RESUME_MIN_MS:
                # Only mark it; the next tick reports it once, with the gap.
                self._resume_mark = time.perf_counter()
                continue
            if self._modal_depth > 0:
                # A dialog is up and the main loop is inside its nested loop on
                # purpose; the stack would only show messagebox internals.
                continue
            last_end = self._last_tick_end
            now = time.perf_counter()
            if now - last_end < 2.0:
                continue
            if now - reported < 5.0:
                continue  # one report per stall, not one per sample
            reported = now
            stack = self._stack_of(main_id)
            self._persist_log(
                "[STALL_STACK] %s stuck %.0fms action=%s\n    %s"
                % (time.strftime("%H:%M:%S"),
                   (now - last_end) * 1000.0,
                   self.current_action or "-", stack))

    def _stack_of(self, thread_id) -> str:
        try:
            frame = sys._current_frames().get(thread_id)
            if frame is None:
                return "<no frame for thread %s>" % thread_id
            lines = []
            while frame is not None and len(lines) < 12:
                lines.append("%s:%d in %s" % (os.path.basename(frame.f_code.co_filename),
                                              frame.f_lineno, frame.f_code.co_name))
                frame = frame.f_back
            return " <- ".join(lines)
        except Exception as exc:
            return "<stack unavailable: %s>" % type(exc).__name__

    def _drain_events(self):
        try:
            for _ in range(MAX_EVENTS_PER_TICK):
                try:
                    kind, value = self.events.get_nowait()
                except queue.Empty:
                    break
                if kind == "cover":
                    self._apply_detail_cover(value)
                elif kind == "cover-error":
                    self.cover.configure(text="封面暂不可用", image=None)
                elif kind == "card-cover":
                    self._apply_card_cover(*value)
                elif kind == "shelf-cover":
                    self._apply_shelf_cover(*value)
                elif kind == "login":
                    self._handle_login_event(*value)
                elif kind == "spawned":
                    self._handle_spawned(*value)
                elif kind == "spawn-failed":
                    self._handle_spawn_failed(*value)
                elif kind == "done":
                    self._handle_done(*value)
                else:
                    self._handle_line(value)
        finally:
            # One widget update per tick rather than four per line.
            self._flush_log_panel()

    def _apply_detail_cover(self, image):
        try:
            photo = ctk.CTkImage(light_image=image, dark_image=image,
                                 size=(self.DETAIL_COVER_W, self.DETAIL_COVER_H))
            self._image_refs.append(photo)
            self.cover_image = photo
            self.cover.configure(text="", image=photo)
        except Exception:
            self.cover.configure(text="暂无封面", image=None)

    def _apply_card_cover(self, book_id: str, image):
        widget = self.card_widgets.get(book_id)
        if widget is None:
            return
        label = widget.get("cover")
        if label is None:
            return
        if image is None:
            label.configure(text="暂无封面", image=None)
            widget["image"] = None
            return
        try:
            photo = ctk.CTkImage(light_image=image, dark_image=image, size=image.size)
            self._image_refs.append(photo)
            widget["image"] = photo
            label.configure(text="", image=photo)
        except Exception:
            label.configure(text="暂无封面", image=None)

    def _parse_shelf(self, value: str):
        """`SHELF\\t{json}` -> list of book dicts, or None when unusable."""
        payload = value.split("\t", 1)[1].strip() if "\t" in value else ""
        if not payload:
            return []
        try:
            rows = json.loads(payload)
        except (ValueError, TypeError):
            return None
        return rows if isinstance(rows, list) else None

    def _apply_shelf_cover(self, label, image):
        """Put a fetched cover onto a shelf row; failures keep the placeholder."""
        try:
            if not label.winfo_exists():
                return
            if image is None:
                label.configure(text="")
                return
            photo = ctk.CTkImage(light_image=image, dark_image=image, size=(36, 48))
            self._image_refs.append(photo)
            label.configure(text="", image=photo)
        except Exception:
            pass

    def _handle_line(self, value: str):
        info = parse_book_info_message(value)
        if info is not None:
            self._persist_log("Book metadata received.")
            try:
                self._show_book_info(info)
            except Exception:
                self.status.set("书籍详情显示时出了点问题；仍可以继续检查或下载。")
            return
        if value.startswith("BOOK_INFO"):
            self._persist_log("[BOOK_INFO_PARSE_ERROR] " + value[:200])
            self.status.set("书籍详情暂时读不到；仍可以继续检查或下载。")
            return
        if value.startswith("SHELF\t"):
            rows = self._parse_shelf(value)
            if rows is None:
                self._persist_log("[SHELF_PARSE_ERROR] " + value[:200])
                self.status.set("书架数据读不到，请重试或检查登录状态。")
            else:
                self._show_shelf(rows)
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
            self.status.set(friendly)
            return

        self._log(value)
        if self.current_action == "search":
            row = parse_search_row(value)
            if row is not None:
                self._add_book_card(row)
                return
        if value.startswith("[RESUME]"):
            self.status.set(value.replace("[RESUME]", "已找到本地进度：", 1).strip())
        if value.startswith("[") and "/" in value:
            try:
                current, total = value[1:].split("]", 1)[0].split("/", 1)
                self.progress.set(int(current) / max(1, int(total)))
                self.status.set(f"正在下载第 {current}/{total} 个目录章节；本地断点会自动复用。")
            except ValueError:
                pass
        if value.startswith("OMISSIONS "):
            # Already written to the log by the self._log(value) above -- writing
            # it again here put every report in the file twice.
            try:
                self.omissions = json.loads(value[len("OMISSIONS "):])
            except Exception:
                self.omissions = None
            return
        if value.startswith("[ANNOTATION]") and "were not" in value:
            # Cosmetic, and the text is intact: say so without alarming anyone
            # into thinking the book is damaged.
            self.status.set("部分章节的划线未被微信读书嵌入，已保留正文并略去这些划线。")
        if value.startswith("[IMAGE]"):
            # Cosmetic: the text is fine, the picture is gone. Say so without
            # implying the book is damaged.
            self.status.set("部分章节的图片在微信读书上已不存在，程序已保留正文、略去这些插图。")
        if value.startswith("[THROTTLE]"):
            self.status.set("微信读书触发了访问频率限制，程序已自动降低并发并退避重试。")
        if value.startswith("[THROTTLED]") and "did not clear" in value:
            self.status.set("微信读书的频率限制没有在本次运行内解除，部分章节未取到。"
                            "断点已保留，稍后点“继续下载”即可接着跑。")
        if value.startswith("[RETRY]"):
            # "网络或数据校验" was a catch-all that covered a rate limit, a
            # bad transfer and a genuinely corrupt chapter alike, so the user
            # was told to check their network while the server was throttling
            # them. Say which one it is; the worker already prints the reason.
            if "426" in value or "429" in value or "Upgrade Required" in value \
                    or "Too Many Requests" in value or "rate limit" in value:
                self.status.set("微信读书触发了访问频率限制，程序正在自动降速并退避重试；"
                                "不用重新登录，等一会儿就好。")
            else:
                self.status.set("某个章节读取失败，程序正在自动重试；已完成的章节不受影响。")
        if value.startswith("[UNRESOLVED]"):
            self.last_unresolved = True
            self.status.set("有一章的正文或划线无法安全确认。已记录并继续检查其它章节。")
        if value.startswith("[INCOMPLETE]"):
            self.last_unresolved = True
            self.status.set("扫描完成，但仍有章节需要处理，所以没有生成不完整 EPUB。点击“查看技术日志”可查看章节 UID。")
        if value.startswith("PROBE_OK;"):
            self.status.set("首章检查通过，可以开始下载。")
        if value.startswith("AUTH_OK"):
            self.auth_badge.configure(text="登录有效", text_color=self.GREEN)
            self.status.set("登录状态正常，可以搜索书籍。")

    def _handle_done(self, job: int, code: int):
        if job != self.job_seq:
            return  # a finished, superseded worker must not reset live state
        action = self.current_action
        self.process = None
        self.current_action = ""
        self.pause_button.configure(state="disabled")
        self.annotations_box.configure(state="normal")
        try:
            if self.last_failure:
                self._finish_failed_action(action)
            elif action == "search":
                if code != 0:
                    self.status.set("搜索没有完成。请稍后再试，或点“检查登录”确认登录状态。")
                elif not self.book_rows:
                    self.status.set("没有找到匹配的书籍。换一个书名或作者关键词试试。")
                else:
                    self.status.set(f"找到 {len(self.book_rows)} 本书。点击左侧任意一本查看详情。")
                if not self.book_rows:
                    self.result_count.configure(
                        text="没有找到匹配的书籍" if code == 0 else "搜索没有完成；请稍后再试")
                self._finish_card_categories()
            elif action == "book-info":
                if not self.info_loaded:
                    self._restore_detail_placeholders()
                    self.status.set("书籍详情暂时读不到；仍可以继续检查或下载。")
                elif code != 0:
                    self.status.set("书籍详情没能加载出来。你仍可以点“先检查第一章”或直接开始下载。")
            elif action == "auth":
                if code == 0:
                    self.auth_badge.configure(text="登录有效", text_color=self.GREEN)
                    self.status.set("登录状态正常，可以搜索书籍。")
                else:
                    self._report_login_problem()
            elif action == "probe":
                if code == 0:
                    self.status.set("首章检查通过，可以开始下载。")
                else:
                    self.status.set("第一章没能检查通过。详情和技术日志里有具体原因，断点不会丢失。")
            elif action == "export":
                if code == 0:
                    notice = describe_omissions(self.omissions)
                    if notice:
                        # Degrading instead of failing is only an improvement if
                        # the reader is told what the finished book is missing.
                        # The bar gets the one-line form: the full paragraph
                        # grew the packed column past the window bottom and took
                        # the task buttons with it.
                        self.status.set(describe_omissions(self.omissions, compact=True))
                        self._ask("showinfo", "下载完成，但有内容缺失", notice)
                    else:
                        self.status.set("下载和文件校验完成。")
                elif self.last_unresolved:
                    self.status.set("扫描到末尾，但有章节数据不完整；没有生成不完整 EPUB。点击“查看技术日志”查看具体章节。")
                else:
                    self.status.set("下载暂时停止；已完成章节已保存，可稍后点“继续下载”。")
        finally:
            self._flush_pending_book()

    def _report_login_problem(self):
        """Turn a failed auth check into one concrete next step.

        Both the non-zero-exit path and the FAILED-line path funnel through here,
        otherwise a crash-style failure leaves the user with no way forward.
        """
        if weread_login.has_credentials():
            # The button shows the account name now, so do not tell the user to
            # look for a label that is no longer there.
            self.auth_badge.configure(text="检查失败", text_color=self.RED)
            self.status.set("凭证已过期或无效。点顶栏的账号名按钮重新扫码。")
            return
        self._set_login_identity(None)
        self._refresh_logout_button()
        self.auth_badge.configure(text="需要登录", text_color=self.RED)
        self.status.set("还没有登录凭证。点“登录 / 切换账号”扫码，或手动输入 API Key。")
        if self._ask("askyesno", 
                "需要登录",
                "这台电脑还没有微信读书登录凭证。\n\n"
                "是否现在打开登录窗口？可以直接扫码，或者手动输入已有的 API Key。"):
            self.open_login()

    def _finish_failed_action(self, action: str):
        # The FAILED line already put a friendly message in the status bar;
        # here we only unstick widgets that would otherwise stay in a loading state.
        if action == "search":
            self._finish_card_categories()
            if not self.book_rows:
                self.result_count.configure(text="搜索没有完成；请稍后再试")
        elif action == "book-info":
            if not self.info_loaded:
                self._restore_detail_placeholders()
        elif action == "auth":
            self._report_login_problem()

    def _finish_card_categories(self):
        for book_id, widget in self.card_widgets.items():
            label = widget.get("category")
            if label is None:
                continue
            if str(label.cget("text")).strip() in ("", "分类加载中…"):
                label.configure(text="分类：暂无信息")

    def _update_card_category(self, book_id: str, category: str):
        # Always record the value: the row may not be on the page on screen.
        self.book_rows.setdefault(book_id, {})["category"] = category
        widget = self.card_widgets.get(book_id)
        if widget is None:
            return
        label = widget.get("category")
        if label is None:
            return
        # A card from a previous page has been destroyed, so its Tk path is
        # dead; the recorded category above is what the next render will use.
        if not label.winfo_exists():
            return
        try:
            label.configure(text=f"分类：{category}" if category else "分类：暂无信息")
        except tk.TclError:
            pass

    def _page_slice(self) -> list[dict]:
        start = self.page_index * RESULTS_PER_PAGE
        return self.result_rows[start:start + RESULTS_PER_PAGE]

    def _page_count(self) -> int:
        if not self.result_rows:
            return 0
        return (len(self.result_rows) + RESULTS_PER_PAGE - 1) // RESULTS_PER_PAGE

    def _set_page_buttons(self, page: int, total: int) -> None:
        """Keep the pager consistent whether or not there is anything to page."""
        has_prev = page > 0
        has_next = page + 1 < total
        if total <= 1:
            self.page_label.configure(text="")
        else:
            self.page_label.configure(text=f"第 {page + 1} / {total} 页")
        self.prev_button.configure(state="normal" if has_prev else "disabled")
        self.next_button.configure(state="normal" if has_next else "disabled")
        if total <= 1:
            self.page_bar.pack_forget()
        else:
            self.page_bar.pack(side="bottom", fill="x", padx=20, pady=(0, 10))

    def _turn_page(self, delta: int) -> None:
        target = self.page_index + delta
        if target < 0 or target >= self._page_count():
            return
        self.page_index = target
        self._render_page()
        self.status.set(f"已切换到第 {target + 1} / {self._page_count()} 页。")

    def _render_page(self) -> None:
        """Rebuild the card widgets for the current page.

        Building the widgets and letting Tk repaint them is the expensive part,
        so this only ever touches one page's worth of children.
        """
        for widget in self.results.winfo_children():
            widget.destroy()
        self.card_widgets.clear()
        for item in self._page_slice():
            self._build_book_card(item)
        self._set_page_buttons(self.page_index, self._page_count())

    def _add_book_card(self, item: dict):
        """Record a search result, paging the list if it grew past the page."""
        bid = str(item.get("bookId") or "")
        if not bid:
            return
        known = bid in self.book_rows
        self.book_rows[bid] = dict(item)
        if known:
            # Category metadata for a card already on screen: update in place
            # rather than appending a duplicate row.
            self._update_card_category(bid, str(item.get("category") or ""))
            return
        self.result_rows.append(dict(item))
        total = self._page_count()
        self.page_index = min(self.page_index, total - 1)
        if bid in {str(row.get("bookId") or "") for row in self._page_slice()}:
            # The result belongs to the page on screen: extend the real widgets.
            self._build_book_card(self.book_rows[bid])
            self._refresh_pager_text()
        else:
            self._refresh_pager_text()

    def _refresh_pager_text(self) -> None:
        self.result_count.configure(
            text=f"找到 {len(self.book_rows)} 本书 · 点击一项查看详情")
        self._set_page_buttons(self.page_index, self._page_count())

    def _build_book_card(self, item: dict):
        bid = str(item.get("bookId") or "")
        if not bid:
            return
        # Row style, as in the reference design's list panel: a full-width row
        # separated by hairlines, cover left, text middle, rating right, and a
        # 3px green bar plus green wash marking the selected row.
        selected = bid == self.selected_id
        card = ctk.CTkFrame(self.results, fg_color=self.GREEN_TINT if selected else self.CARD,
                            corner_radius=0, border_width=0)
        card.pack(fill="x", padx=2, pady=0)
        bar = ctk.CTkFrame(card, width=3, corner_radius=0,
                           fg_color=self.ACCENT if selected else "transparent")
        bar.pack(side="left", fill="y")
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=(2, 10), pady=10)
        cover = ctk.CTkLabel(inner, text="封面\n加载中", width=self.COVER_W, height=self.COVER_H,
                             fg_color=self.SUNKEN, corner_radius=4, text_color=self.FAINT,
                             font=_font(10), justify="center")
        cover.pack(side="left", anchor="n", padx=(0, 12))
        text_col = ctk.CTkFrame(inner, fg_color="transparent")
        text_col.pack(side="left", fill="both", expand=True)
        title = str(item.get("title") or "").strip() or "未命名书籍"
        author = str(item.get("author") or "").strip() or "作者未知"
        category = str(item.get("category") or "").strip()
        ctk.CTkLabel(text_col, text=title, text_color=self.INK, anchor="w", justify="left",
                     font=_font(13, "bold"), wraplength=240
                     ).pack(fill="x", anchor="w")
        ctk.CTkLabel(text_col, text=author, text_color=self.MUTED, anchor="w",
                     font=_font(11)).pack(fill="x", anchor="w", pady=(3, 0))
        category_label = ctk.CTkLabel(text_col,
                                      text=category if category else "分类加载中…",
                                      text_color=self.FAINT, anchor="w",
                                      font=_font(11))
        category_label.pack(fill="x", anchor="w", pady=(2, 0))
        raw_rating = item.get("newRating")
        try:
            rated = raw_rating not in (None, "") and float(raw_rating) > 0
        except (TypeError, ValueError):
            rated = False
        ctk.CTkLabel(text_col,
                     text=card_rating_text(raw_rating, item.get("newRatingCount")),
                     text_color=self.AMBER if rated else self.FAINT, anchor="w", font=_font(11)
                     ).pack(fill="x", anchor="w", pady=(3, 0))

        def choose(_event=None, selected=dict(item)):
            self._select_book(selected)

        for widget in (card, inner, cover, text_col, *text_col.winfo_children()):
            widget.bind("<Button-1>", choose)
        if not selected:
            card.bind("<Enter>", lambda _e, w=card: w.configure(fg_color=self.SUNKEN))
            card.bind("<Leave>", lambda _e, w=card: w.configure(fg_color=self.CARD))

        self.card_widgets[bid] = {"frame": card, "cover": cover, "category": category_label,
                                  "image": None}
        self._request_card_cover(bid, str(item.get("cover") or "").strip())

    def _request_card_cover(self, book_id: str, url: str):
        if not url:
            self._apply_card_cover(book_id, None)
            return
        card_box = (self.COVER_W, self.COVER_H)
        cached = self.cover_cache.get(url)
        if cached is not None and _covers_box(cached, card_box):
            self._apply_card_cover(book_id, cached)
            return

        def fetch():
            image = None
            try:
                response = requests.get(url, timeout=(5, 15))
                response.raise_for_status()
                if len(response.content) <= 8 * 1024 * 1024:
                    # Decode here, off the Tk thread: this is the expensive part.
                    image = _decode_cover(response.content, card_box)
            except Exception:
                image = None
            if len(self.cover_cache) > 64:
                self.cover_cache.clear()
            self.cover_cache[url] = image
            self.events.put(("card-cover", (book_id, image)))

        threading.Thread(target=fetch, daemon=True).start()

    def search(self):
        keyword = self.search_entry.get().strip()
        if not keyword:
            # A modal here is a trap: pressing Enter while an IME composition is
            # still active delivers an empty string, and the dialog blocks the
            # Tk main loop until it is dismissed -- which is what a stuck
            # "(未响应)" window looks like. The status bar says the same thing.
            self.status.set("请先在搜索框输入书名或关键词。")
            self._persist_log("[EMPTY_SEARCH] ignored an empty keyword")
            return
        if self._is_busy():
            self.status.set("当前有任务正在运行，等它结束再搜索。")
            return
        for widget in self.results.winfo_children():
            widget.destroy()
        self.book_rows.clear()
        self.card_widgets.clear()
        self.result_rows.clear()
        self.page_index = 0
        self._set_page_buttons(0, 0)
        self.result_count.configure(text="正在搜索微信读书…")
        self.status.set("正在搜索。稍等片刻，结果会出现在左侧。")
        started = False
        try:
            started = self._start_worker(["--search", keyword], "search", "正在搜索…")
        except Exception as exc:
            self._persist_log(f"[SEARCH_START_ERROR] {type(exc).__name__}: {exc}")
        if not started:
            self.result_count.configure(text="搜索没有启动；请稍后再试")
            self.status.set("搜索没有启动。请查看技术日志后重试。")

    def _select_book(self, item):
        self.selected_id = str(item.get("bookId", ""))
        self.selected_title = str(item.get("title", ""))
        self.selected_author = str(item.get("author", ""))
        self.book_info = dict(item)
        self.info_loaded = False
        # Repaint the page so the selected row carries the reference design's
        # green bar and wash. Only rows on the current page exist as widgets;
        # _render_page rebuilds exactly those, cheaply.
        self._render_page()
        self.book_title_label.configure(text=self.selected_title or "暂无书名")
        self.author_label.configure(text=f"作者：{self.selected_author or '暂无信息'}")
        self.id_label.configure(text=f"书籍 ID：{self.selected_id or '暂无信息'}")
        if self._is_busy():
            self.pending_book = dict(item)
            self.category_label.configure(text="详情已排队，当前任务结束后自动加载")
            self.rating_label.configure(text="")
            self.cover.configure(text="排队中", image=None)
            self.cover_image = None
            self._set_intro("当前有任务正在运行；这本书的详情会在它结束后自动加载。")
            self.status.set("已选择书籍；详情已排队，当前任务结束后自动加载。")
            return
        self.pending_book = None
        self._start_book_info(item)

    def _start_book_info(self, item: dict) -> bool:
        self.category_label.configure(text="正在读取分类、评分、简介和封面…")
        self.rating_label.configure(text="")
        self.cover.configure(text="加载封面…", image=None)
        self.cover_image = None
        self._set_intro("正在读取书籍简介…")
        self.status.set("已选择书籍，正在加载详情。")
        started = False
        try:
            started = self._start_worker(
                ["--book-info", str(item.get("bookId", ""))], "book-info", "正在加载书籍详情…")
        except Exception as exc:
            self._persist_log(f"[BOOK_INFO_START_ERROR] {type(exc).__name__}: {exc}")
        if not started:
            self._restore_detail_placeholders()
            self.status.set("详情查询没有启动。请查看技术日志后重试。")
        return started

    def _set_rating(self, text: str) -> None:
        """Colour the rating by meaning: absent data is neutral, not a warning."""
        unrated = "暂无信息" in text
        self.rating_label.configure(
            text=text, text_color=self.FAINT if unrated else self.AMBER)

    def _restore_detail_placeholders(self):
        info = self.book_info or {}
        self.category_label.configure(text="    ·    ".join(book_detail_lines(info)))
        self._set_rating(
            rating_text(info.get("newRating"), info.get("newRatingCount"))
            or "评分：暂无信息")
        if self.cover_image is None:
            url = str(info.get("cover") or "").strip()
            cached = self.cover_cache.get(url) if url else None
            detail_box = (self.DETAIL_COVER_W, self.DETAIL_COVER_H)
            if _covers_box(cached, detail_box):
                self._apply_detail_cover(cached)
            elif url:
                # The cache holds card-sized art, too small to upscale here.
                threading.Thread(target=self._download_cover, args=(url,),
                                 daemon=True).start()
            else:
                self.cover.configure(text="暂无封面", image=None)
        self._set_intro("详情暂时没有读取到；可稍后重试，或直接点“先检查第一章”。")

    def _flush_pending_book(self):
        item = self.pending_book
        if item is None:
            return
        if self._is_busy():
            return
        self.pending_book = None
        self._start_book_info(item)

    def _show_book_info(self, data):
        payload_id = str(data.get("bookId") or "").strip()
        if payload_id and self.selected_id and payload_id != self.selected_id:
            self._persist_log(f"[BOOK_INFO_STALE] ignored payload for {payload_id}; {self.selected_id} selected")
            return
        self.book_info.update(data)
        title = str(data.get("title") or "").strip() or self.selected_title
        author = str(data.get("author") or "").strip() or self.selected_author
        self.book_title_label.configure(text=title or "暂无书名")
        self.author_label.configure(text=f"作者：{author or '暂无信息'}")
        self.category_label.configure(text="    ·    ".join(book_detail_lines(data)))
        self._set_rating(rating_text(data.get("newRating"), data.get("newRatingCount"))
                         or "评分：暂无信息")
        self.id_label.configure(text=f"书籍 ID：{str(data.get('bookId') or self.selected_id) or '暂无信息'}")
        intro = str(data.get("intro") or "").strip()
        self._set_intro(intro or "暂无简介。")
        cover = str(data.get("cover") or "").strip()
        if cover:
            cached = self.cover_cache.get(cover)
            detail_box = (self.DETAIL_COVER_W, self.DETAIL_COVER_H)
            if _covers_box(cached, detail_box):
                # Already decoded at detail size by an earlier selection.
                self._apply_detail_cover(cached)
            else:
                threading.Thread(target=self._download_cover, args=(cover,),
                                 daemon=True).start()
        else:
            self.cover.configure(text="暂无封面", image=None)
        self.status.set("书籍详情已加载。请核对封面、书名和作者后再下载。")
        self.info_loaded = True

    def _download_cover(self, url):
        try:
            response = requests.get(url, timeout=(5, 15))
            response.raise_for_status()
            if len(response.content) > 8 * 1024 * 1024:
                raise ValueError("cover too large")
            # Decoded on this background thread so the Tk loop stays free.
            image = _decode_cover(response.content,
                                  (self.DETAIL_COVER_W, self.DETAIL_COVER_H))
            if len(self.cover_cache) > 64:
                self.cover_cache.clear()
            self.cover_cache[url] = image
            self.events.put(("cover", image))
        except Exception:
            self.events.put(("cover-error", ""))

    def _set_intro(self, text):
        self.intro.configure(state="normal")
        self.intro.delete("1.0", "end")
        self.intro.insert("1.0", text)
        self.intro.configure(state="disabled")

    # ------------------------------------------------------------------ login

    def credential_state(self) -> dict:
        return weread_login.credentials_status()

    def open_login(self):
        """Show the login window: QR scan by default, manual entry as fallback."""
        if self.login_dialog is not None and self.login_dialog.winfo_exists():
            self.login_dialog.lift()
            self.login_dialog.focus_force()
            return
        dialog = ctk.CTkToplevel(self.root)
        dialog.title("登录微信读书")
        dialog.geometry("560x560")
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.configure(fg_color=self.BG)
        self.login_dialog = dialog

        ctk.CTkLabel(dialog, text="登录微信读书", text_color=self.INK,
                     font=_font(22, "bold")).pack(anchor="w", padx=26, pady=(22, 4))
        ctk.CTkLabel(dialog, text="扫码后凭证只保存在本机，不进入日志或安装包。",
                     text_color=self.MUTED, font=_font(12)).pack(anchor="w", padx=26)
        ctk.CTkFrame(dialog, height=1, fg_color=self.HAIRLINE).pack(fill="x", padx=26, pady=(16, 0))

        tabs = ctk.CTkFrame(dialog, fg_color="transparent")
        tabs.pack(fill="x", padx=26, pady=(16, 0))
        self.login_mode = tk.StringVar(value="qr")

        def make_mode(value, text):
            return ctk.CTkRadioButton(
                tabs, text=text, variable=self.login_mode, value=value,
                command=self._switch_login_mode, fg_color=self.ACCENT,
                hover_color=self.ACCENT, text_color=self.INK,
                font=_font(13))

        make_mode("qr", "扫码登录").pack(side="left", padx=(0, 20))
        make_mode("manual", "手动输入凭证").pack(side="left")

        self.login_qr_frame = ctk.CTkFrame(dialog, fg_color="transparent")
        self.login_qr_frame.pack(fill="both", expand=True, padx=26, pady=12)
        # CTkLabel cannot draw a border, so the code sits on a hairline card.
        qr_card = ctk.CTkFrame(self.login_qr_frame, fg_color=self.CARD,
                               corner_radius=self.RADIUS,
                               border_width=1, border_color=self.HAIRLINE)
        qr_card.pack(pady=(8, 8))
        self.login_qr_holder = ctk.CTkLabel(qr_card, text="正在获取二维码…",
                                            width=248, height=248, fg_color=self.CARD,
                                            corner_radius=6, text_color=self.FAINT,
                                            font=_font(12))
        self.login_qr_holder.pack(padx=8, pady=8)
        self.login_qr_status = ctk.CTkLabel(self.login_qr_frame, text="请稍候", text_color=self.MUTED,
                                            font=_font(12), wraplength=490, justify="center")
        self.login_qr_status.pack(fill="x", pady=(2, 8))

        # Hidden until the server actually asks for a code: an always-visible
        # empty OTP box next to the QR code is just noise on the common path.
        self.login_otp_row = ctk.CTkFrame(self.login_qr_frame, fg_color="transparent")
        self.login_otp_var = tk.StringVar(value="")
        self.login_otp_entry = ctk.CTkEntry(self.login_otp_row, placeholder_text="4 位验证码",
                                            width=140, height=34, corner_radius=8,
                                            fg_color=self.CARD, border_color=self.HAIRLINE,
                                            text_color=self.INK, font=_font(13),
                                            textvariable=self.login_otp_var, state="disabled")
        self.login_otp_entry.pack(side="left")
        self.login_otp_button = ctk.CTkButton(self.login_otp_row, text="提交验证码", width=110,
                                              height=34, corner_radius=8,
                                              fg_color=self.ACCENT, hover_color=self.ACCENT_HOVER,
                                              text_color="#FFFFFF", font=_font(13),
                                              state="disabled", command=self._submit_login_otp)
        self.login_otp_button.pack(side="left", padx=(8, 0))

        qr_buttons = ctk.CTkFrame(self.login_qr_frame, fg_color="transparent")
        qr_buttons.pack(fill="x", pady=(14, 0))
        self._quiet_button(qr_buttons, "重新获取二维码", 150,
                           self._start_qr_login).pack(side="left")
        self._quiet_button(qr_buttons, "关闭", 90,
                           self.close_login).pack(side="right")

        self.login_manual_frame = ctk.CTkFrame(dialog, fg_color="transparent")

        manual_box = ctk.CTkFrame(self.login_manual_frame, fg_color=self.CARD,
                                  corner_radius=self.RADIUS,
                                  border_width=1, border_color=self.HAIRLINE)
        # Hug the content: expanding would stretch the card into a tall empty
        # white block, because the window is sized for the taller QR tab.
        manual_box.pack(fill="x", padx=0, pady=(12, 0))
        ctk.CTkLabel(manual_box, text="API Key（必填）", text_color=self.INK, anchor="w",
                     font=_font(14, "bold")).pack(anchor="w", padx=20, pady=(18, 4))
        ctk.CTkLabel(manual_box, text="在 weread.qq.com/r/weread-skills 登录后复制，格式为 wrk- 开头。",
                     text_color=self.MUTED, anchor="w", font=_font(11),
                     wraplength=460).pack(anchor="w", padx=20)
        self.manual_api_key = ctk.CTkEntry(manual_box, placeholder_text="wrk-...", height=36,
                                           corner_radius=8, fg_color=self.SUNKEN,
                                           border_color=self.HAIRLINE,
                                           text_color=self.INK, font=_font(13))
        self.manual_api_key.pack(fill="x", padx=20, pady=(8, 16))

        ctk.CTkLabel(manual_box, text="wr_skey（必填）", text_color=self.INK, anchor="w",
                     font=_font(14, "bold")).pack(anchor="w", padx=20)
        ctk.CTkLabel(manual_box, text="微信读书网页版会话 cookie。没有它可以搜书和看详情，但无法下载章节正文。",
                     text_color=self.MUTED, anchor="w", font=_font(11),
                     wraplength=460).pack(anchor="w", padx=20)
        self.manual_skey = ctk.CTkEntry(manual_box, placeholder_text="wr_skey 的值", height=36,
                                        corner_radius=8, fg_color=self.SUNKEN,
                                        border_color=self.HAIRLINE,
                                        text_color=self.INK, font=_font(13),
                                        show="•")
        self.manual_skey.pack(fill="x", padx=20, pady=(8, 12))
        self.login_manual_status = ctk.CTkLabel(manual_box, text="", text_color=self.MUTED,
                                                font=_font(12), wraplength=470,
                                                justify="left")
        self.login_manual_status.pack(fill="x", padx=20, pady=(0, 10))
        manual_buttons = ctk.CTkFrame(manual_box, fg_color="transparent")
        manual_buttons.pack(fill="x", padx=20, pady=(0, 18))
        ctk.CTkButton(manual_buttons, text="保存并使用", width=130, height=34,
                      corner_radius=8, fg_color=self.ACCENT,
                      hover_color=self.ACCENT_HOVER, text_color="#FFFFFF",
                      font=_font(13),
                      command=self._save_manual_credentials).pack(side="left")

        # Deliberately does NOT pre-fill the saved session cookie. It used to, in
        # cleartext: the dialog promises "扫码后凭证只保存在本机", and then wrote
        # that cookie straight into a visible box, so any screenshot or screen
        # recording of this window captured a live account token. The field is
        # masked now, and an existing session is announced instead of shown. The
        # api_key is left alone -- it is not a session token, and re-entering it
        # is a normal thing to do while switching accounts.
        stored = weread_login.load_saved_credentials()
        if stored.get("api_key"):
            self.manual_api_key.insert(0, stored["api_key"])
        if stored.get("cookies", {}).get("wr_skey"):
            self.login_manual_status.configure(
                text="本机已保存一份登录会话。为安全起见不再回显，如需更换请直接粘贴新的 wr_skey。",
                text_color=self.MUTED)

        self.login_qr_worker = None
        self.login_otp_ready = threading.Event()
        # Fixed size both tabs fit inside: the manual tab needs 537px of content
        # plus 44px of frame padding, and the QR tab needs ~565px of height.
        # 620x620 covers both plus room for a status message to wrap.
        dialog.geometry("620x560")

        self._start_qr_login()
        dialog.protocol("WM_DELETE_WINDOW", self.close_login)

    def _switch_login_mode(self):
        mode = self.login_mode.get()
        if mode == "qr":
            self.login_manual_frame.pack_forget()
            self.login_qr_frame.pack(fill="both", expand=True, padx=22, pady=10)
        else:
            self.login_qr_frame.pack_forget()
            self.login_manual_frame.pack(fill="both", expand=True, padx=22, pady=10)

    def close_login(self):
        self.login_cancel.set()
        self.login_otp_ready.set()
        dialog, self.login_dialog = self.login_dialog, None
        if dialog is not None:
            try:
                dialog.destroy()
            except Exception:
                pass

    def _start_qr_login(self):
        if self.login_dialog is None:
            return
        self.login_cancel.clear()
        self.login_otp_ready.clear()
        self.login_otp_var.set("")
        self._set_login_otp_enabled(False)
        self.login_qr_status.configure(text="正在获取二维码…")
        self.login_qr_holder.configure(image=None, text="正在获取二维码…")
        worker = threading.Thread(target=self._qr_login_worker, daemon=True)
        self.login_qr_worker = worker
        worker.start()

    def _set_login_otp_enabled(self, enabled):
        state = "normal" if enabled else "disabled"
        self.login_otp_entry.configure(state=state)
        self.login_otp_button.configure(state=state)
        if enabled:
            self.login_otp_row.pack(fill="x", pady=(4, 0))
            self.login_otp_entry.focus_set()
        else:
            self.login_otp_row.pack_forget()

    def _submit_login_otp(self):
        code = self.login_otp_var.get().strip()
        if not code:
            self.login_qr_status.configure(text="请先输入手机上的 4 位验证码。")
            return
        self._pending_otp = code
        self.login_otp_var.set("")
        self._set_login_otp_enabled(False)
        self.login_qr_status.configure(text="正在校验验证码…")
        self.login_otp_ready.set()

    def _qr_login_worker(self):
        """Runs off the Tk thread; every result goes through self.events."""
        session = requests.Session()
        try:
            uid, jar = weread_login.begin_login(session)
            image = weread_login.render_qr_png(weread_login.confirm_url(uid), box_size=6, border=3)
            self.events.put(("login", ("qr", image)))
        except weread_login.LoginError as exc:
            self.events.put(("login", ("error", str(exc))))
            return
        except Exception as exc:
            self.events.put(("login", ("error", f"获取二维码失败：{type(exc).__name__}")))
            return

        otp = ""
        deadline = time.monotonic() + weread_login.LOGIN_SESSION_TIMEOUT
        while time.monotonic() < deadline:
            if self.login_cancel.is_set():
                return
            try:
                result = weread_login.poll_login(session, jar, uid, otp)
            except weread_login.LoginPending:
                continue
            except weread_login.LoginError as exc:
                self.events.put(("login", ("error", str(exc))))
                return

            state, detail = weread_login.interpret_poll(result)
            if state == "ok":
                try:
                    credentials = weread_login.complete_login(session, jar, result)
                except weread_login.LoginError as exc:
                    self.events.put(("login", ("error", str(exc))))
                    return
                self.events.put(("login", ("done", credentials)))
                return
            if state in ("otp", "otp_expired"):
                otp = ""
                self.events.put(("login", ("otp", detail)))
                if state == "otp":
                    if not self._wait_for_otp(deadline):
                        return
                    otp = self._pending_otp
                continue
            if state == "expired":
                self.events.put(("login", ("error", detail)))
                return
            self.events.put(("login", ("error", detail)))
            return
        self.events.put(("login", ("error", "二维码已过期，请点击“重新获取二维码”。")))

    def _wait_for_otp(self, deadline) -> bool:
        """Block the worker thread until the user submits a code or gives up."""
        self._pending_otp = ""
        self.login_otp_ready.clear()
        while time.monotonic() < deadline:
            if self.login_cancel.wait(0.2):
                return False
            if self.login_otp_ready.is_set() and self._pending_otp:
                return True
        return False

    def _handle_login_event(self, kind: str, payload=None):
        if self.login_dialog is None or not self.login_dialog.winfo_exists():
            return
        if kind == "qr":
            photo = ctk.CTkImage(light_image=payload, dark_image=payload, size=(240, 240))
            self._image_refs.append(photo)
            self.login_qr_holder.configure(image=photo, text="")
            self.login_qr_status.configure(text="请用手机微信读书「扫一扫」扫描上方二维码。")
        elif kind == "otp":
            self.login_qr_status.configure(text=payload or "请输入手机上的验证码。")
            self._set_login_otp_enabled(True)
        elif kind == "done":
            self._finish_login(payload)
        elif kind == "error":
            self.login_qr_status.configure(text=payload)
            self._set_login_otp_enabled(False)

    def _finish_login(self, credentials: dict):
        try:
            weread_login.save_credentials(credentials)
        except OSError as exc:
            self.login_qr_status.configure(text=f"登录成功，但保存凭证失败：{exc}")
            return
        name = (credentials.get("account") or {}).get("name") or ""
        who = f"（{name}）" if name else ""
        self._set_login_identity(name)
        self._refresh_logout_button()
        self.auth_badge.configure(text="登录有效", text_color=self.GREEN)
        self.status.set(f"登录成功{who}。现在可以搜索书籍了。")
        self.close_login()

    def _save_manual_credentials(self):
        """Save a manually entered credential pair -- into BOTH files.

        Why both: load_credentials() treats weread-live-settings.lua as the
        mandatory base and raises "Credential file not found" when it is absent.
        save_credentials() only writes the sealed session overlay, so a manual
        save on a fresh install produced a session file with no base file -- the
        save reported success and every later action failed. The inline card's
        writer (first_run.write_settings) covers the base file, so this path
        writes the base too, then the overlay, keeping the two-file contract.
        """
        api_key = self.manual_api_key.get().strip()
        skey = self.manual_skey.get().strip()
        if not api_key:
            self.login_manual_status.configure(text="请填写 API Key。")
            return
        if not skey:
            self.login_manual_status.configure(
                text="请填写 wr_skey。缺少它无法下载章节正文。")
            return
        # The base file first: without it the overlay alone is unusable, and the
        # icacls call inside save_credentials blocks the Tk thread for seconds,
        # so anything that must be checked for this save to make sense is
        # checked before that cost is paid.
        try:
            write_settings(_settings_path(), api_key,
                           cookies={"wr_skey": skey})
        except OSError as exc:
            self.login_manual_status.configure(text=f"保存失败：{exc}")
            return
        credentials = {
            "cookies": {"wr_skey": skey},
            "api_key": api_key,
            "wr_ticket": "",
            "wr_wrpa": "",
            "account": {"name": "", "user_vid": "", "login_method": "manual",
                        "login_time": int(time.time())},
        }
        try:
            weread_login.save_credentials(credentials)
        except OSError as exc:
            self.login_manual_status.configure(text=f"保存失败：{exc}")
            return
        self._set_login_identity("")
        self._refresh_logout_button()
        self.auth_badge.configure(text="登录有效", text_color=self.GREEN)
        self.status.set("凭证已保存。现在可以检查登录状态。")
        self._refresh_creds_panel()
        self.close_login()

    def _refresh_creds_panel(self):
        """Show the credential card when something is missing, hide it when not.

        Three states, three layouts -- a field that does not apply is never
        drawn, because an empty box the user cannot fill is worse than no box.
        """
        try:
            path = _settings_path()
            usable = _credentials_usable(path)
            has_key = _has_api_key(path)
        except Exception:
            return
        if usable:
            self.creds_panel.grid_remove()
            self.probe_button.grid()
            self.export_button.grid()
            return

        # Something is missing: the card takes the place of the buttons.
        self.probe_button.grid_remove()
        self.export_button.grid_remove()
        self.creds_panel.grid()
        self.creds_error.configure(text="")

        if has_key:
            # api_key is in place; only the session is missing, and that comes
            # from the QR login -- so offer the button, not a form.
            self.fields_frame.pack_forget()
            self.scan_only_frame.pack(fill="x", padx=18)
            self.creds_hint.configure(
                text="api_key 已就绪。还差 wr_skey —— 点下面的按钮扫码即可。")
            return

        # Nothing usable on disk: both fields, with the reason if there is one.
        self.scan_only_frame.pack_forget()
        self.fields_frame.pack(fill="x", padx=18)
        if path.is_file():
            self.creds_hint.configure(
                text="本机的凭证文件读不了（格式不对或已损坏），请重新填写。")
        else:
            self.creds_hint.configure(
                text="首次使用：填下面两个值。其中 wr_skey 由「扫码登录」自动获取，"
                     "只有扫码失败时才需要手动粘贴。")
        # Pre-fill api_key when the file has one, so a re-fill is not from scratch.
        try:
            from lupa.luajit21 import LuaRuntime
            raw = path.read_bytes()
            start = raw.find(b"\nreturn {")
            if start >= 0:
                lua = LuaRuntime(unpack_returned_tuples=True, encoding=None)
                table = lua.execute(raw[start + 1:])
                key = table[b"api_key"]
                if key:
                    self.creds_api_entry.delete(0, "end")
                    self.creds_api_entry.insert(0, key.decode(errors="replace"))
        except Exception:
            pass

    def _save_creds_inline(self):
        """Save what the inline card holds, then re-evaluate the panel."""
        api_key = self.creds_api_entry.get().strip()
        skey = self.creds_skey_entry.get().strip()
        problem = _validate_creds(api_key, skey)
        if problem:
            self.creds_error.configure(text=problem)
            return
        try:
            settings = _settings_path()
            settings.parent.mkdir(parents=True, exist_ok=True)
            text_body = _build_settings_lua(
                api_key, skey if skey else "",
                cookies=({"wr_skey": skey} if skey else None))
            temp = settings.with_suffix(settings.suffix + ".tmp")
            temp.write_text(text_body, encoding="utf-8")
            os.replace(temp, settings)
        except OSError as exc:
            self.creds_error.configure(text="无法写入凭证文件：%s" % exc)
            return
        self.status.set("凭证已保存。")
        self._refresh_creds_panel()

    def check_credentials_on_start(self):
        """Show the credential card in the download panel when something is missing.

        The main window always opens -- there is no dialog and no second window,
        which is the whole point after a first-run flow that popped a window up,
        resized it, and then vanished. Whatever is missing is shown as a card
        inside the download panel, next to the buttons it replaces, so the user
        acts on it in the one window they already have.
        """
        try:
            self._refresh_creds_panel()
        except Exception as exc:
            # A card that fails to build must not stop the window from opening.
            print("[FIRST_RUN] credential panel failed: %s: %s"
                  % (type(exc).__name__, exc), file=sys.stderr)
            self.status.set("凭证还没配好：点「登录 / 切换账号」填写后即可下载。")

    def check_auth(self):
        if self._is_busy():
            self.status.set("当前有任务正在运行，等它结束再检查登录。")
            return
        self.auth_badge.configure(text="正在检查…", text_color=self.MUTED)
        if not self._start_worker(["--check-auth"], "auth", "正在安全检查本机登录状态…"):
            self.auth_badge.configure(text="检查失败", text_color=self.RED)
            self.status.set("登录检查没有启动。请检查程序目录后重试。")

    def probe(self):
        if not self.selected_id:
            self._ask("showinfo", "先选择一本书", "先搜索并点击左侧的一本书，再进行章节检查。")
            return
        if self._is_busy():
            self.status.set("当前有任务正在运行，等它结束再检查第一章。")
            return
        args = ["--probe-book", self.selected_id]
        mode = self.annotations_var.get()
        want_thoughts = mode != "plain"
        want_underlines = mode != "plain"
        if not want_underlines:
            args.append("--no-annotations")
        elif not want_thoughts:
            args.append("--no-thoughts")
        self._start_worker(args, "probe",
                           "正在检查这本书的第一章、划线"
                           + ("和想法…" if want_thoughts else "…（已跳过想法）"))

    def export(self):
        if not self.selected_id:
            self._ask("showinfo", "先选择一本书", "先搜索并点击一本书，检查详情后再开始下载。")
            return
        if self._is_busy():
            self.status.set("当前有任务正在运行，等它结束再开始下载。")
            return
        mode = self.annotations_var.get()
        want_thoughts = mode != "plain"
        want_underlines = mode != "plain"
        marks = ("划线 + 想法", "只要划线", "纯正文（无划线）")[("both", "marks", "plain").index(mode)]
        if not self._ask("askyesno",
                "开始下载",
                f"准备下载：{self.selected_title}\n作者：{self.selected_author or '暂无信息'}\n\n"
                f"标注：{marks}\n\n"
                "已有章节会自动续传。若微信读书返回空章节或无法确认的划线，会记录问题并继续检查其它章节；"
                "有未解决章节时不会生成不完整 EPUB。继续吗？"):
            return
        self.progress.set(0)
        jobs = max(1, min(4, int(self.jobs.get())))
        args = [self.selected_id, "--jobs", str(jobs)]
        if not want_underlines:
            args.append("--no-annotations")
        elif not want_thoughts:
            args.append("--no-thoughts")
        if not self._start_worker(args, "export",
                                  "正在准备下载并检查本地断点…"):
            self.status.set("下载没有启动。请检查程序目录后重试。")

    def stop(self):
        if self._is_busy():
            self.process.terminate()
            self.status.set("已请求暂停，已写入的章节会保留，下次可继续下载。")

    def open_output(self):
        OUTPUT.mkdir(parents=True, exist_ok=True)
        os.startfile(str(OUTPUT))

    def open_logs(self):
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        os.startfile(str(LOG_DIR))

    def logout(self):
        """Drop the saved session, and be honest about what survives.

        The engine treats weread-live-settings.lua as a mandatory base and only
        merges session-cookies.json over it, so deleting the JSON is a *refresh
        reset*, not a full logout. Claiming otherwise would leave the user still
        signed in, staring at a button that now says "logged out".
        """
        if self._is_busy():
            self.status.set("当前有任务在运行，请先暂停或等它结束再退出登录。")
            return

        # Ask user whether to do a full cleanup (including weread-live-settings.lua)
        if not self._ask("askyesno",
                "退出登录",
                "是否彻底清除所有本地凭证？\n\n"
                "• 是：删除 session-cookies.json 和 weread-live-settings.lua\n"
                "      下次启动需重新扫码 + 填 api_key/user_vid\n\n"
                "• 否：仅删除 session-cookies.json（刷新重置）\n"
                "      weread-live-settings.lua 保留，下次打开直接可用"):
            return

        try:
            removed = weread_login.clear_credentials()
        except OSError as exc:
            self._persist_log(f"[LOGOUT_ERROR] {type(exc).__name__}: {exc}")
            self.status.set(f"退出登录失败：{exc}")
            return
        if not removed:
            self.status.set("本机没有已保存的登录会话，无需退出。")
            self._set_login_identity(None)
            self.logout_button.configure(state="disabled")
            return

        # Also remove the base credentials file (weread-live-settings.lua)
        try:
            # Import SETTINGS here to avoid circular import issues in tests
            from .weread_desktop import SETTINGS
            if SETTINGS.is_file():
                SETTINGS.unlink(missing_ok=True)
                self._persist_log("[LOGOUT] weread-live-settings.lua removed")
        except ImportError:
            # Fallback for test environment where relative import fails
            import sys
            from pathlib import Path
            SETTINGS = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / "WeReadDesktop" / "weread-live-settings.lua"
            if SETTINGS.is_file():
                SETTINGS.unlink(missing_ok=True)
                self._persist_log("[LOGOUT] weread-live-settings.lua removed")
        except OSError as exc:
            self._persist_log(f"[LOGOUT_ERROR] failed to remove SETTINGS: {exc}")

        self._set_login_identity(None)
        self.logout_button.configure(state="disabled")
        self._persist_log("[LOGOUT] session-cookies.json and weread-live-settings.lua removed by the user")
        self.status.set("已彻底清除本地凭证。下次启动需重新配置 api_key/user_vid 并扫码登录。")
        self._persist_log("[LOGOUT] full credential wipe completed by the user")

    def _refresh_logout_button(self):
        """Only offer 退出登录 when there is actually something to log out of.

        Keyed on has_saved_session() rather than has_credentials(): the session
        this button deletes is a cookie jar, and a jar without an api_key still
        signs the user in. Requiring the api_key meant the button stayed greyed
        out on a working login, leaving no way to sign out from the window.
        """
        try:
            ready = weread_login.has_saved_session()
        except Exception:
            ready = False
        self.logout_button.configure(state="normal" if ready else "disabled")




def main() -> int:
    if FROZEN and len(sys.argv) > 1:
        raise SystemExit("The GUI executable does not accept worker commands.")
    if not is_first_instance(SINGLE_INSTANCE_KEY, APP_TITLE):
        # Another copy already owns this program's slot; it has just been
        # brought forward. Starting a second window would mean a second worker
        # and two processes fighting over the same session file.
        return 0
    root = ctk.CTk()
    # The main window opens immediately -- always. The earlier design hid the
    # window and ran a credential wizard first, so a new user's first experience
    # was a small dialog appearing, then nothing until it was dismissed, and on
    # some paths it came back a second time for no visible reason. The window is
    # the program; it is what the user asked for. Missing credentials are a
    # state the window itself handles: the download panel says what is missing
    # and offers the entry point, and nothing has to be filled in anywhere else.
    app = DesktopApp(root)
    try:
        app.check_credentials_on_start()
    except Exception as exc:
        # A credential check must never stop the window from opening. Logged and
        # carried on: the panel's own state tells the user what to do next.
        print("[FIRST_RUN] credential check failed: %s: %s"
              % (type(exc).__name__, exc), file=sys.stderr)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
