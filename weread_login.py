#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""WeRead QR login and credential storage for the desktop GUI.

Protocol ported from the installed Kindle plugin's
`weread.koplugin/weread/lib/qr_login.lua`, which is the only existing
implementation of this flow on the machine. The endpoints, timeouts and the
credential assembly below mirror that module:

    SKILLS_PAGE_URL  = BASE_URL + "/r/weread-skills"
    LOGIN_UID_URL    = BASE_URL + "/api/auth/getLoginUid"
    LOGIN_INFO_URL   = BASE_URL + "/api/auth/getLoginInfo"
    USER_INFO_URL    = BASE_URL + "/api/userInfo"
    API_KEY_URL      = BASE_URL + "/api/skills/apikeyGet?only_show=1"

A QR login is a full account switch, so the resulting cookie jar is built from
scratch instead of being merged into whatever was there before.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests

PRIVATE = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / "WeReadDesktop"
CREDENTIAL_STATE = PRIVATE / "session-cookies.json"

BASE_URL = "https://weread.qq.com"
SKILLS_PAGE_URL = BASE_URL + "/r/weread-skills"
LOGIN_UID_URL = BASE_URL + "/api/auth/getLoginUid"
LOGIN_INFO_URL = BASE_URL + "/api/auth/getLoginInfo"
USER_INFO_URL = BASE_URL + "/api/userInfo"
API_KEY_URL = BASE_URL + "/api/skills/apikeyGet?only_show=1"
CONFIRM_URL = BASE_URL + "/web/confirm"

LOGIN_SESSION_TIMEOUT = 300.0
POLL_BLOCK_TIMEOUT = 5.0
POLL_TOTAL_TIMEOUT = 8.0

NO_SKILL_MESSAGE = (
    "这个微信读书账号还没有开启「微信读书技能」，因此拿不到官方 API Key。\n\n"
    "请在手机微信读书里打开：我 → 设置 → 微信读书技能 → 获取 API Key，"
    "然后再重新扫码。"
)

# Poll payloads reuse the plugin's vocabulary so the server sees a familiar client.
_PENDING_MARKERS = ("timeout", "request failed")


class LoginError(RuntimeError):
    """A login attempt failed in a way the UI should show to the user."""


class LoginPending(Exception):
    """The long poll timed out; the caller should simply poll again."""


class LoginCancelled(Exception):
    """The user closed the login window."""


def _cookie_header(cookies: dict) -> str:
    return "; ".join(f"{name}={value}" for name, value in cookies.items() if value != "")


def _merge_cookies(jar: dict, response: requests.Response) -> dict:
    for raw in response.raw.headers.getlist("Set-Cookie") if response.raw is not None else []:
        pair = raw.split(";", 1)[0].strip()
        if "=" in pair:
            name, value = pair.split("=", 1)
            jar[name.strip()] = value.strip()
    return jar


def _json_or_raise(response: requests.Response, stage: str) -> dict:
    if response.status_code < 200 or response.status_code >= 300:
        raise LoginError(f"{stage} 失败：HTTP {response.status_code}")
    try:
        data = response.json()
    except ValueError as exc:
        raise LoginError("微信读书返回了无法解析的内容") from exc
    if not isinstance(data, dict):
        raise LoginError("微信读书返回了无法解析的内容")
    return data


def begin_login(session: requests.Session) -> tuple[str, dict]:
    """Return (login uid, cookie jar collected while opening the skills page)."""
    jar: dict = {}
    page = session.get(
        SKILLS_PAGE_URL,
        headers={"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                 "Referer": BASE_URL + "/"},
        timeout=(10, 20), allow_redirects=True,
    )
    _merge_cookies(jar, page)
    if page.status_code < 200 or page.status_code >= 300:
        raise LoginError(f"无法打开微信读书登录页（HTTP {page.status_code}）")

    headers = {"Accept": "application/json, text/plain, */*", "Referer": SKILLS_PAGE_URL}
    if jar:
        headers["Cookie"] = _cookie_header(jar)
    response = session.get(LOGIN_UID_URL, headers=headers, timeout=(10, 20))
    data = _json_or_raise(response, "获取登录标识")
    _merge_cookies(jar, response)
    uid = data.get("uid")
    if not isinstance(uid, str) or not uid:
        raise LoginError("微信读书没有返回有效的登录标识")
    return uid, jar


def confirm_url(uid: str) -> str:
    return f"{CONFIRM_URL}?uid={quote(uid, safe='')}"


def interpret_poll(result) -> tuple[str, str]:
    """Map one getLoginInfo payload onto a UI state.

    Mirrors the plugin's `_poll` branch table: NEED_OTP / OTP_NOT_MATCH ask for a
    code, LOGIN_TIMEOUT expires the code, anything else with a logicCode fails.
    """
    if not isinstance(result, dict):
        return "failed", "微信读书返回了无法解析的内容"
    if result.get("succeed") is True:
        return "ok", ""
    code = str(result.get("logicCode") or "")
    if code == "NEED_OTP":
        return "otp", "请输入手机上显示的 4 位验证码。"
    if code == "OTP_NOT_MATCH":
        return "otp", "验证码不正确，请重新输入。"
    if code == "OTP_EXPIRED":
        return "otp_expired", "验证码已过期，请点击“重新获取二维码”。"
    if code == "LOGIN_TIMEOUT":
        return "expired", "二维码已过期，请点击“重新获取二维码”。"
    if code:
        return "failed", f"扫码登录失败：{code}"
    return "failed", "微信读书没有返回可识别的登录状态。"


def poll_login(session: requests.Session, jar: dict, uid: str, otp: str = "") -> dict:
    """One long poll. Raises LoginPending when the server held the connection open."""
    url = f"{LOGIN_INFO_URL}?uid={quote(uid, safe='')}&otp"
    if otp:
        url += "=" + quote(otp, safe="")
    headers = {"Accept": "application/json, text/plain, */*", "Referer": SKILLS_PAGE_URL}
    if jar:
        headers["Cookie"] = _cookie_header(jar)
    try:
        response = session.get(url, headers=headers,
                               timeout=(POLL_BLOCK_TIMEOUT, POLL_TOTAL_TIMEOUT))
    except requests.Timeout as exc:
        raise LoginPending from exc
    except requests.RequestException as exc:
        message = str(exc).lower()
        if any(marker in message for marker in _PENDING_MARKERS):
            raise LoginPending from exc
        raise LoginError("无法连接微信读书，请检查网络后重试") from exc
    data = _json_or_raise(response, "查询扫码状态")
    _merge_cookies(jar, response)
    return data


def _authenticated_get(session: requests.Session, url: str, cookies: dict,
                       vid: str, skey: str, stage: str) -> dict:
    last: Exception | None = None
    for attempt in range(3):
        headers = {"Accept": "application/json, text/plain, */*", "Referer": SKILLS_PAGE_URL,
                   "X-Vid": vid, "X-Skey": skey}
        if cookies:
            headers["Cookie"] = _cookie_header(cookies)
        response = session.get(url, headers=headers, timeout=(10, 20))
        if response.status_code == 401:
            last = LoginError(f"{stage} 暂时未授权，正在重试")
            time.sleep(0.5)
            continue
        return _json_or_raise(response, stage)
    raise last or LoginError(f"{stage} 失败")


def complete_login(session: requests.Session, jar: dict, login_result: dict) -> dict:
    """Turn a successful poll result into the credential set the engine needs."""
    if not isinstance(login_result, dict) or login_result.get("succeed") is not True:
        raise LoginError("扫码登录没有成功")

    vid = str(login_result.get("webLoginVid") or "")
    access_token = str(login_result.get("accessToken") or "")
    refresh_token = str(login_result.get("refreshToken") or "")
    if not vid or not access_token:
        raise LoginError("扫码结果里缺少账号凭证")

    # A QR login replaces the account, so start from a clean jar.
    cookies = dict(jar)
    cookies["wr_vid"] = vid
    cookies["wr_skey"] = access_token
    cookies["wr_ql"] = "0"
    if refresh_token:
        cookies["wr_rt"] = quote(refresh_token, safe="")

    user_info = _authenticated_get(
        session, f"{USER_INFO_URL}?userVid={quote(vid, safe='')}",
        cookies, vid, access_token, "读取账号信息")

    api_key = ""
    for attempt in range(3):
        api_result = _authenticated_get(
            session, API_KEY_URL, cookies, vid, access_token, "获取 API Key")
        candidate = api_result.get("apikey")
        if isinstance(candidate, str) and candidate:
            api_key = candidate
            break
        if attempt < 2:
            time.sleep(0.5)
    if not api_key:
        raise LoginError(NO_SKILL_MESSAGE)

    return {
        "cookies": cookies,
        "api_key": api_key,
        "wr_ticket": "",
        "wr_wrpa": "",
        "account": {
            "name": str(user_info.get("name") or ""),
            "user_vid": vid,
            "login_method": "qr",
            "login_time": int(time.time()),
        },
    }


# ----------------------------------------------------------------- persistence

def save_credentials(credentials: dict, path: Path | None = None) -> Path:
    """Write the credential set where load_credentials() already looks for it."""
    target = Path(path) if path is not None else CREDENTIAL_STATE
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "cookies": dict(credentials.get("cookies") or {}),
        "api_key": str(credentials.get("api_key") or ""),
        "wr_ticket": str(credentials.get("wr_ticket") or ""),
        "wr_wrpa": str(credentials.get("wr_wrpa") or ""),
    }
    account = credentials.get("account")
    if account:
        payload["account"] = account
    temp = target.with_suffix(target.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, target)
    try:
        os.chmod(target, 0o600)
    except OSError:
        pass
    return target


def load_saved_credentials(path: Path | None = None) -> dict:
    """Read back what save_credentials() stored; {} when there is nothing usable."""
    target = Path(path) if path is not None else CREDENTIAL_STATE
    try:
        state = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(state, dict):
        return {}
    cookies = state.get("cookies")
    if not isinstance(cookies, dict):
        return {}
    return {
        "cookies": {str(k): str(v) for k, v in cookies.items()},
        "api_key": str(state.get("api_key") or ""),
        "wr_ticket": str(state.get("wr_ticket") or ""),
        "wr_wrpa": str(state.get("wr_wrpa") or ""),
        "account": state.get("account") if isinstance(state.get("account"), dict) else None,
    }


def has_credentials(path: Path | None = None) -> bool:
    """True when both halves the engine insists on are present."""
    stored = load_saved_credentials(path)
    return bool(stored.get("api_key")) and bool(stored.get("cookies", {}).get("wr_skey"))


def credentials_status(path: Path | None = None) -> dict[str, Any]:
    """Describe what is missing, so the UI can say something actionable."""
    stored = load_saved_credentials(path)
    missing = []
    if not stored.get("api_key"):
        missing.append("api_key")
    if not stored.get("cookies", {}).get("wr_skey"):
        missing.append("wr_skey")
    return {"ready": not missing, "missing": missing, "stored": stored}


def clear_credentials(path: Path | None = None) -> bool:
    """Delete the saved session overlay. True if a file was actually removed.

    Both front ends call this so there is one definition of "log out". Note this
    is a *refresh reset*, not a full logout: the engine treats
    weread-live-settings.lua as a mandatory base and merges this file over it,
    so the base keeps its own session. Callers must say so rather than implying
    the machine is now signed out.
    """
    target = Path(path) if path is not None else CREDENTIAL_STATE
    try:
        target.unlink()
        return True
    except FileNotFoundError:
        return False
    except OSError:
        raise


# ------------------------------------------------------------------ QR rendering

def render_qr_png(text: str, box_size: int = 8, border: int = 4) -> "Any":
    """Render `text` as a QR code image. PIL is already a GUI dependency."""
    import qrcode
    from PIL import Image

    code = qrcode.QRCode(border=border, box_size=box_size)
    code.add_data(text)
    code.make(fit=True)
    image = code.make_image(fill_color="black", back_color="white").convert("RGB")
    return image
