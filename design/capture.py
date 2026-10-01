# -*- coding: utf-8 -*-
"""Capture the redesigned GUI with PrintWindow, which reads the window's own
surface instead of the screen, so an overlapping window cannot hijack the shot.
ImageGrab has failed three times in this environment for exactly that reason.
"""
import ctypes
import sys
import time
from ctypes import wintypes
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from PIL import Image

import customtkinter as ctk

import desktop_app
import weread_core

OUT = Path("design")
OUT.mkdir(exist_ok=True)

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
user32.SetProcessDPIAware()
WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
BM_CLICK = 0x00F5


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long),
                ("biHeight", ctypes.c_long), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


def shoot(hwnd, name):
    r = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    w, h = r.right - r.left, r.bottom - r.top
    if w <= 0 or h <= 0:
        print("  capture failed", w, h)
        return
    hdc = user32.GetWindowDC(hwnd)
    mem = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    gdi32.SelectObject(mem, bmp)
    if not user32.PrintWindow(hwnd, mem, 0x00000002):
        user32.PrintWindow(hwnd, mem, 0)
    info = BITMAPINFO()
    info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    info.bmiHeader.biWidth = w
    info.bmiHeader.biHeight = -h
    info.bmiHeader.biPlanes = 1
    info.bmiHeader.biBitCount = 32
    buf = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(info), 0)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mem)
    user32.ReleaseDC(hwnd, hdc)
    Image.frombuffer("RGB", (w, h), buf, "raw", "BGRX", 0, 1).save(OUT / (name + ".png"))
    print("  saved %-28s %dx%d" % (name, w, h))


def pump(root, seconds=0.6):
    end = time.time() + seconds
    while time.time() < end:
        root.update()
        time.sleep(0.02)


def hwnd_of(widget):
    return int(widget.winfo_id())


def main():
    root = ctk.CTk()
    root.geometry("1120x790+80+60")
    app = desktop_app.DesktopApp(root)
    pump(root, 1.0)

    print("capturing...")
    shoot(hwnd_of(root), "01-empty")

    # A realistic populated state, without touching the network.
    app._add_book_card({"bookId": "19480992", "title": "超神机械师", "author": "齐佩甲",
                        "cover": "", "newRating": "888", "newRatingCount": "23718",
                        "category": "男生小说-游戏竞技"})
    app._add_book_card({"bookId": "41794030", "title": "道诡异仙", "author": "狐尾的笔",
                        "cover": "", "newRating": "871", "newRatingCount": "4074",
                        "category": "男生小说-东方玄幻"})
    app._add_book_card({"bookId": "3004323461", "title": "夜的命名术", "author": "会说话的肘子",
                        "cover": "", "newRating": "0", "newRatingCount": "0",
                        "category": ""})
    app._select_book({"bookId": "19480992", "title": "超神机械师", "author": "齐佩甲",
                      "cover": "", "newRating": "888", "newRatingCount": "23718",
                      "category": "男生小说-游戏竞技"})
    app._restore_detail_placeholders()
    app.status.set("找到 3 本书。点击左侧任意一本查看详情。")
    app.rating_label.configure(text=weread_core.rating_text("888", "23718"))
    app.id_label.configure(text="书籍 ID：19480992")
    app.author_label.configure(text="作者：齐佩甲")
    app.category_label.configure(text="分类：男生小说-游戏竞技")
    app.info_loaded = True
    pump(root, 0.8)
    shoot(hwnd_of(root), "02-populated")

    # Login window, QR tab and manual tab.
    app._start_qr_login = lambda: None
    app.open_login()
    pump(root, 1.0)
    shoot(hwnd_of(app.login_dialog), "03-login-qr")
    app.login_mode.set("manual")
    app._switch_login_mode()
    pump(root, 0.8)
    shoot(hwnd_of(app.login_dialog), "04-login-manual")

    app.close_login()
    root.destroy()
    print("done")


if __name__ == "__main__":
    main()
