#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Standalone background worker entry point for WeReadDesktop.

Two modes live here:

* default -- the download engine (``weread_desktop.py``), driven over stdio.
* ``--login`` -- the WeRead QR login, spoken as line events on stdout.

The split matters: the engine needs LuaJIT and the plugin payload, while login
needs ``requests``/``qrcode``/PIL and none of that. Dispatching before the
``runpy`` call keeps a login from paying for the engine, and keeps the login
protocol (which lives in ``weread_login.py``) in one place for both frontends.

Login protocol, one line per event, all UTF-8:

    LOGIN_QR <png path>      a QR image was rendered; show it, then keep polling
    LOGIN_OTP <message>      WeRead wants the 4-digit code; one stdin line supplies it
                             (an empty line just re-asks and polls again)
    LOGIN_OK <account name>  credentials were written to the session state file
    LOGIN_FAIL <message>     the attempt failed in a way the user should read

The parent may also write ``cancel`` to stdin to stop an attempt.

The heavy imports sit inside their branches on purpose: a login must not pay
for LuaJIT, and the engine must not pay for qrcode. They are function-level,
which PyInstaller's analysis still follows, so the frozen bundle keeps lxml and
lupa exactly as before.
"""
from __future__ import annotations

import runpy
import sys
import time
from pathlib import Path

CANCEL_WORDS = ("cancel", "quit", "exit")


def _emit(line: str) -> None:
    print(line, flush=True)


def _read_otp() -> str:
    """Block for one line of stdin; EOF means the parent went away."""
    try:
        return sys.stdin.readline()
    except (OSError, ValueError):
        return ""


def _write_qr_png(text: str) -> Path:
    import os
    import tempfile

    import weread_login

    image = weread_login.render_qr_png(text, box_size=6, border=3)
    handle, name = tempfile.mkstemp(prefix="weread-login-", suffix=".png")
    # mkstemp hands back an OPEN handle. Leaving it open leaks a descriptor per
    # attempt and, on Windows, an unclosed handle can stop other processes from
    # reading the file -- which is how the page intermittently got a 404 for a
    # PNG that plainly existed.
    try:
        image.save(name, format="PNG")
    finally:
        os.close(handle)
    return Path(name)


def run_login() -> int:
    """Drive one QR login attempt and report it as line events."""
    import requests

    import weread_login

    session = requests.Session()
    try:
        uid, jar = weread_login.begin_login(session)
    except weread_login.LoginError as exc:
        _emit(f"LOGIN_FAIL {exc}")
        return 1
    except Exception as exc:
        _emit(f"LOGIN_FAIL 获取二维码失败：{type(exc).__name__}")
        return 1

    try:
        path = _write_qr_png(weread_login.confirm_url(uid))
    except Exception as exc:
        _emit(f"LOGIN_FAIL 二维码生成失败：{type(exc).__name__}")
        return 1
    _emit(f"LOGIN_QR {path}")

    otp = ""
    deadline = time.monotonic() + weread_login.LOGIN_SESSION_TIMEOUT
    while time.monotonic() < deadline:
        try:
            result = weread_login.poll_login(session, jar, uid, otp)
        except weread_login.LoginPending:
            continue
        except weread_login.LoginError as exc:
            _emit(f"LOGIN_FAIL {exc}")
            return 1

        state, detail = weread_login.interpret_poll(result)
        if state == "ok":
            try:
                credentials = weread_login.complete_login(session, jar, result)
            except weread_login.LoginError as exc:
                _emit(f"LOGIN_FAIL {exc}")
                return 1
            weread_login.save_credentials(credentials)
            account = (credentials.get("account") or {}).get("name", "")
            _emit(f"LOGIN_OK {account}")
            return 0
        if state in ("otp", "otp_expired"):
            otp = ""
            _emit(f"LOGIN_OTP {detail}")
            answer = _read_otp().strip()
            if answer.lower() in CANCEL_WORDS:
                _emit("LOGIN_FAIL 已取消登录。")
                return 1
            otp = answer
            continue
        _emit(f"LOGIN_FAIL {detail or '扫码登录没有成功。'}")
        return 1

    _emit("LOGIN_FAIL 二维码已过期，请点击“重新获取二维码”。")
    return 1


def run_engine() -> int:
    import requests  # noqa: F401  (pulled in for its side effects on the bundle)
    from lxml import etree, html as lxml_html  # noqa: F401
    from lupa.luajit21 import LuaRuntime  # noqa: F401

    if getattr(sys, "frozen", False):
        bundle = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        exporter = bundle / "weread_desktop.py"
    else:
        exporter = Path(__file__).with_name("weread_desktop.py")
    namespace = runpy.run_path(str(exporter), run_name="weread_export_worker")
    sys.argv = [str(exporter), *sys.argv[1:]]
    return namespace["main"]()


def main() -> int:
    if "--login" in sys.argv[1:]:
        return run_login()
    return run_engine()


if __name__ == "__main__":
    raise SystemExit(main())
