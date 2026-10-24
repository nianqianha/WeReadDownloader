# -*- coding: utf-8 -*-
"""End-to-end check against the real packaged worker for the book that broke.

Reproduces the user's exact flow: search -> click book 1 -> click book 2,
with real /book-info payloads and a real cover image decoded through CTkImage.
"""
import importlib.util
import io
import json
import os
import sys
import threading
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SRC = Path(__file__).resolve().parents[1] / "weread"
spec = importlib.util.spec_from_file_location("desktop_app", SRC / "desktop_app.py")
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)

import customtkinter as ctk

WORKER = Path(os.environ.get(
    "WEREAD_WORKER",
    r"C:\Program Files\WeReadDownloader\WeReadWorker\WeReadWorker.exe"))
FAILURES = []


def check(label, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + label + ((" :: " + str(detail)) if detail else ""))
    if not ok:
        FAILURES.append(label)


def run_worker(args, timeout=60):
    import subprocess
    out = subprocess.run([str(WORKER)] + args, capture_output=True, text=True,
                         encoding="utf-8", errors="replace", timeout=timeout)
    return out.stdout


def real_book_info(book_id):
    for line in run_worker(["--book-info", book_id]).splitlines():
        if line.startswith("BOOK_INFO\t"):
            return json.loads(line.split("\t", 1)[1])
    return None


root = ctk.CTk()
root.withdraw()
instance = app.DesktopApp(root)
try:
    # --- real payload for the book from the screenshot -------------------------
    info2 = real_book_info("3004323461")
    check("worker returned real payload for 3004323461", info2 is not None)
    print("     raw publishTime=%r newRating=%r newRatingCount=%r"
          % (info2.get("publishTime"), info2.get("newRating"), info2.get("newRatingCount")))

    # --- book 1: cover really renders (creates a real CTkImage / pyimageN) -----
    info1 = real_book_info("19480992")
    cover_url = info1.get("cover") or ""
    print("     book1 cover url:", cover_url[:80])
    import urllib.request
    try:
        with urllib.request.urlopen(cover_url, timeout=20) as resp:
            jpeg = resp.read()
    except Exception as exc:
        jpeg = b""
        print("     cover fetch failed:", exc)
    check("book1 cover bytes fetched", len(jpeg) > 1000, len(jpeg))

    instance._show_book_info(info1)
    instance._apply_detail_cover(jpeg)
    check("book1 cover applied (CTkImage created)", instance.cover_image is not None)
    check("book1 publishTime hidden when epoch", "1970" not in instance.category_label.cget("text"),
          instance.category_label.cget("text"))

    # --- switch to book 2: this is where it used to die -----------------------
    started = []
    real_start = instance._start_worker

    def spy(args, action, status):
        started.append((list(args), action))
        return real_start(args, action, status)

    instance._start_worker = spy
    instance._select_book({"bookId": "3004323461", "title": "超神机械师之异神高德",
                           "author": "爱吃包子狗不理", "cover": "", "newRating": "0",
                           "newRatingCount": "0", "category": ""})
    time.sleep(0.3)
    check("book2 worker actually started", any(a[0][:1] == ["--book-info"] for a in started), started)
    check("no TclError in log after switching books", "TclError" not in instance.log_panel.get("1.0", "end"),
          instance.log_panel.get("1.0", "end")[-200:])

    # drain the real worker reply
    deadline = time.time() + 60
    while time.time() < deadline and not instance.info_loaded:
        instance._drain_events()
        time.sleep(0.1)
    instance._drain_events()

    check("book2 details loaded", instance.info_loaded is True, instance.status.get())
    check("book2 title correct", instance.book_title_label.cget("text") == "超神机械师之异神高德",
          instance.book_title_label.cget("text"))
    check("book2 id correct", instance.id_label.cget("text") == "书籍 ID：3004323461",
          instance.id_label.cget("text"))
    detail = instance.category_label.cget("text")
    check("book2 shows no 1970 date", "1970" not in detail, detail)
    rating = instance.rating_label.cget("text")
    check("book2 shows no fake 0.0 score", "0.0" not in rating, rating)
    check("book2 intro loaded", "高德因在半夜熬夜看小说睡着后" in instance.intro.get("1.0", "end"))

    # --- the error text the screenshot showed ---------------------------------
    msg = app.friendly_error("FAILED RuntimeError No readable chapters in WeRead catalog")
    check("catalog error translated (not FALLBACK_ZH)", msg != app.FALLBACK_ZH, msg[:60])
    print("     translated:", msg)
finally:
    root.destroy()

print()
print("FAILURES:", FAILURES if FAILURES else "none")
