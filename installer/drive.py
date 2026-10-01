# -*- coding: utf-8 -*-
"""Clean, hands-off run of the real Setup wizard.

Buttons are matched by EXACT caption on class TNewButton. Matching by prefix
hit the static label "安装程序将安装 ..." and silently did nothing. Nothing on
the checklist boxes is clicked, so whatever gets installed/created reflects the
installer's real defaults rather than my automation's meddling.
"""
import ctypes
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from PIL import Image

SETUP = Path("release-installer/WeReadDesktop-Setup.exe").resolve()
SHOT = Path("installer/shots")
SHOT.mkdir(parents=True, exist_ok=True)
PROC_KEY = "WeReadDesktop-Setup"
BM_CLICK = 0x00F5
LB_GETCOUNT, LB_GETTEXT, LB_GETCHECKSTATE, LB_SETCHECKSTATE = 0x018B, 0x0189, 0x0190, 0x0191
PW_RENDERFULLCONTENT = 0x00000002

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
user32.SetProcessDPIAware()
WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long),
                ("biHeight", ctypes.c_long), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


def proc_name(pid):
    try:
        buf = ctypes.create_unicode_buffer(512)
        size = wintypes.DWORD(512)
        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not h:
            return ""
        ctypes.windll.kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size))
        ctypes.windll.kernel32.CloseHandle(h)
        return Path(buf.value).name
    except Exception:
        return ""


def top_windows():
    out = []

    def collect(hwnd, _l):
        if not user32.IsWindowVisible(hwnd):
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        cls = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls, 256)
        out.append({"hwnd": int(hwnd), "pid": int(pid.value), "class": cls.value})
        return True

    user32.EnumWindows(WNDENUMPROC(collect), 0)
    return out


def find_wizard(timeout=120):
    end = time.time() + timeout
    while time.time() < end:
        for w in top_windows():
            if PROC_KEY in proc_name(w["pid"]):
                return w
        time.sleep(0.4)
    return None


def kids(hwnd):
    out = []

    def collect(c, _l):
        buf = ctypes.create_unicode_buffer(512)
        n = user32.GetWindowTextLengthW(c)
        if n:
            user32.GetWindowTextW(c, buf, n + 1)
        cls = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(c, cls, 256)
        out.append({"hwnd": int(c), "text": buf.value.replace("&", "").strip(),
                    "class": cls.value})
        return True

    user32.EnumChildWindows(hwnd, WNDENUMPROC(collect), 0)
    return out


def shoot(hwnd, name):
    r = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    w, h = r.right - r.left, r.bottom - r.top
    if w <= 0 or h <= 0:
        print("      [capture failed]")
        return
    hdc = user32.GetWindowDC(hwnd)
    mem = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    gdi32.SelectObject(mem, bmp)
    if not user32.PrintWindow(hwnd, mem, PW_RENDERFULLCONTENT):
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
    Image.frombuffer("RGB", (w, h), buf, "raw", "BGRX", 0, 1).save(SHOT / (name + ".png"))
    print("      shot %-24s %dx%d" % (name, w, h))


def click(h):
    user32.SendMessageW(h, BM_CLICK, 0, 0)
    time.sleep(0.9)


def button(hwnd, *labels):
    """Match by prefix, but ONLY on TNewButton.

    Captions keep the accelerator suffix after stripping '&' ("下一步(N)"),
    so equality fails. Restricting to TNewButton is what keeps this safe --
    the static labels ("安装程序将安装 ...") are TNewStaticText.
    """
    for c in kids(hwnd):
        if c["class"] == "TNewButton" and any(c["text"].startswith(p) for p in labels):
            return c
    return None


def main():
    print("clean wizard run, /CURRENTUSER, no interference")
    proc = subprocess.Popen([str(SETUP), "/CURRENTUSER"])
    wiz = find_wizard()
    if not wiz:
        print("FAIL: no wizard window")
        return 1
    print("wizard pid=%d\n" % wiz["pid"])

    for step in range(1, 8):
        cur = find_wizard(timeout=5)
        if not cur:
            print("[%d] wizard gone" % step)
            return 1
        hwnd = cur["hwnd"]
        print("[%d]" % step)
        for c in kids(hwnd):
            if c["class"] == "TNewStaticText" and 8 < len(c["text"]) <= 70 and c["text"][-1] == "？":
                print("      标题: %s" % c["text"][:64])
        for c in kids(hwnd):
            if c["class"] == "TNewCheckListBox" and user32.IsWindowVisible(c["hwnd"]):
                n = user32.SendMessageW(c["hwnd"], LB_GETCOUNT, 0, 0)
                for i in range(n):
                    b = ctypes.create_unicode_buffer(512)
                    user32.SendMessageW(c["hwnd"], LB_GETTEXT, i, b)
                    st = user32.SendMessageW(c["hwnd"], LB_GETCHECKSTATE, i, 0)
                    print("      勾选项 %-42s %s" % (b.value[:42], "CHECKED" if st == 1 else "unchecked"))
        b = button(hwnd, "下一步", "安装", "完成")
        if not b:
            print("      无主按钮")
            shoot(hwnd, "99-stuck-%d" % step)
            break
        print("      点击「%s」" % b["text"])
        shoot(hwnd, "0%d-%s" % (step, b["text"]))
        if b["text"] == "完成":
            for c in kids(hwnd):
                if c["class"] == "TNewCheckListBox" and user32.IsWindowVisible(c["hwnd"]):
                    n = user32.SendMessageW(c["hwnd"], LB_GETCOUNT, 0, 0)
                    for i in range(n):
                        bb = ctypes.create_unicode_buffer(512)
                        user32.SendMessageW(c["hwnd"], LB_GETTEXT, i, bb)
                        if "立即启动" in bb.value and user32.SendMessageW(c["hwnd"], LB_GETCHECKSTATE, i, 0) == 1:
                            user32.SendMessageW(c["hwnd"], LB_SETCHECKSTATE, i, 0)
                            print("      已取消「立即启动」")
            click(b["hwnd"])
            time.sleep(5)
            break
        click(b["hwnd"])
        if b["text"] == "安装":
            time.sleep(4)
            shoot(hwnd, "05-installing")
            print("      等待解压（约 185MB）...")
            end = time.time() + 1500
            done = False
            while time.time() < end:
                w = find_wizard(timeout=5)
                if w and button(w["hwnd"], "完成"):
                    done = True
                    break
                time.sleep(3)
            if not done:
                print("      FAIL: 完成页未出现")
                return 1
            print("      解压完成，已到完成页")
    time.sleep(2)
    print("\nsetup exited = %s" % (proc.poll() is not None))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
