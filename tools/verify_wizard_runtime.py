# -*- coding: utf-8 -*-
"""Runtime proof: the frozen GUI reaches the wizard when credentials are absent.

WHY NOT JUST TRUST THE STATIC CHECK
    tools/verify_bundle.py proves first_run is in the PYZ. That is necessary but
    not sufficient: the module could be packaged and still never called, which is
    exactly how v1.0.0 shipped. This runs the real exe and watches for the
    wizard's window.

THE INSTANCE GUARD
    The frozen GUI claims a named mutex, so if the user's own WeReadDownloader is
    running, this process exits immediately and no window appears -- a false
    "the wizard is broken". The test therefore reports whether another instance
    is running, and treats that as SKIP rather than FAIL, so it cannot be read as
    evidence either way.

HOW IT OBSERVES
    LOCALAPPDATA is redirected to a scratch directory, so the credential file is
    genuinely absent and the wizard is the only possible outcome. Windows are
    then enumerated by owner pid and matched against the wizard's title.
"""
import ctypes
import ctypes.wintypes as wt
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

REPO = Path(r"C:\Users\asus\Desktop\各类\Kindle\05_补丁与其他\桌面端WeRead下载器")
EXE = REPO / "release-fresh" / "WeReadDesktop" / "WeReadDesktop.exe"
WIZARD_MARK = "首次运行"

user32 = ctypes.windll.user32
EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)


def windows_of(pid):
    out = []

    def cb(hwnd, _l):
        owner = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value != pid:
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if n:
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            out.append(buf.value)
        return True

    user32.EnumWindows(EnumWindowsProc(cb), 0)
    return out


def other_instances_running():
    p = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "(Get-Process | Where-Object { $_.ProcessName -like '*WeRead*' }).Count"],
        capture_output=True, text=True)
    try:
        return int(p.stdout.strip() or 0)
    except ValueError:
        return 0


print("exe:", EXE.is_file(), "%.2f MB" % (EXE.stat().st_size / 1048576) if EXE.is_file() else "")

n_other = other_instances_running()
print("other WeRead processes:", n_other)

scratch = Path(tempfile.mkdtemp(prefix="weread-frozen-"))
# Prove the credential file really is absent in the scratch profile.
assert not (scratch / "WeReadDesktop" / "weread-live-settings.lua").exists()

env = dict(os.environ)
env["LOCALAPPDATA"] = str(scratch)

proc = subprocess.Popen([str(EXE)], env=env,
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
wizard = False
seen = []
try:
    for i in range(30):
        time.sleep(0.5)
        rc = proc.poll()
        if rc is not None:
            print("  [%.1fs] exited rc=%s" % ((i + 1) * 0.5, rc))
            break
        for title in windows_of(proc.pid):
            if title not in seen:
                seen.append(title)
                print("  [%.1fs] window: %r" % ((i + 1) * 0.5, title))
                if WIZARD_MARK in title:
                    wizard = True
        if wizard:
            break

    print()
    if wizard:
        print("RUNTIME: PASS -- the wizard opened with no credential file present")
        result = 0
    elif proc.poll() is not None and n_other > 0:
        print("RUNTIME: SKIP -- another instance holds the single-instance guard,")
        print("         so this process exited before it could show anything.")
        print("         Close WeReadDownloader and re-run to get a real answer.")
        result = 0
    else:
        print("RUNTIME: FAIL -- no wizard window")
        print("  own windows:", seen)
        if proc.poll() is not None:
            out, err = proc.communicate(timeout=5)
            if out:
                print("  stdout:", out.decode("utf-8", "replace")[:400])
            if err:
                print("  stderr:", err.decode("utf-8", "replace")[:800])
        result = 1
finally:
    try:
        proc.kill()
    except Exception:
        pass
    shutil.rmtree(scratch, ignore_errors=True)

sys.exit(result)