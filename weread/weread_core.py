#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Logic shared by the CustomTkinter GUI and the web-mode local HTTP server.

Every decision about how a worker line becomes a sentence of Chinese lives
here, so both frontends phrase the same outcome identically. This module must
stay importable with nothing but the standard library: the frozen web server
bundles it as-is, and it only starts in under 1.5 s while the bundle contains
no native GUI extension (no PIL, no tkinter, no _tkinter, no Tcl DLLs).
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

FROZEN = getattr(sys, "frozen", False)
if FROZEN:
    BUNDLE = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
else:
    BUNDLE = Path(__file__).resolve().parent

PRIVATE = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / "WeReadDesktop"
OUTPUT = PRIVATE / "exports"
LOG_DIR = PRIVATE / "logs"

# Handles of every mutex this process opened. They must stay referenced for the
# whole run: a collected handle closes the mutex, the name disappears, and the
# next launch would wrongly believe it owns the single-instance slot.
_SINGLE_INSTANCE_HANDLES: list = []


def claim_single_instance(key: str, window_title: str = "") -> bool:
    """Refuse a second copy of the same program. True when this one may run.

    Double-clicking the shortcut repeatedly used to leave several windows, each
    with its own worker, and in web mode several local servers all writing the
    same session file. A named mutex is the actual guard. The window lookup only
    exists so the copy that is already running gets brought forward, instead of
    the new one exiting without a word.

    Fails open: if the mutex cannot be created at all we let the program start,
    because refusing to launch over an API hiccup is worse than the duplicate.
    """
    if os.name != "nt":
        return True
    import ctypes
    from ctypes import wintypes

    ERROR_ALREADY_EXISTS = 183
    SW_RESTORE = 9
    kernel32 = ctypes.windll.kernel32
    user32 = ctypes.windll.user32
    kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateMutexW.restype = wintypes.HANDLE

    handle = kernel32.CreateMutexW(None, False, f"Local\\{key}")
    if not handle:
        return True
    _SINGLE_INSTANCE_HANDLES.append(handle)
    if kernel32.GetLastError() != ERROR_ALREADY_EXISTS:
        return True

    if window_title:
        hwnd = user32.FindWindowW(None, window_title)
        if hwnd:
            user32.ShowWindow(hwnd, SW_RESTORE)
            user32.SetForegroundWindow(hwnd)
    return False


def is_first_instance(key: str, window_title: str = "") -> bool:
    """Alias kept for readability at the call sites."""
    return claim_single_instance(key, window_title)

# Field order published by weread_desktop.search_row().
SEARCH_FIELDS = ("bookId", "title", "author", "cover", "newRating", "newRatingCount", "category")
WORKER_NAME = "WeReadWorker.exe"

_SECRET_PATTERNS = (
    re.compile(r"(?i)(authorization\s*[:=]\s*)(?:bearer\s+)?([^\s,;]+)"),
    re.compile(r"(?i)(bearer\s+)([A-Za-z0-9._~+/=-]{6,})"),
    re.compile(r"(?i)\b((?:api[_-]?key|apikey|wr_skey|wr_ticket|wr_wrpa)\s*[:=]\s*)([^\s,;]+)"),
    re.compile(r"(?i)\b((?:wr_skey|wr_ticket|wr_wrpa|vid|sid)=)([^&;\s]+)"),
)

ERRORS_ZH = (
    ("empty body despite catalog wordCount",
     "微信读书目录显示本章有正文，但服务器实际返回空正文。程序没有伪造内容，已保留进度。"),
    ("embedded marker(s)",
     "部分划线无法可靠对应到正文，程序已保留正文、略去这些划线，以免标错位置。"),
    ("not text", "章节数据格式异常，程序未能解析出正文。"),
    ("[IMAGE]", "部分章节引用了服务端已不提供的图片，程序已保留正文、略去这些插图。"),
    ("[ANNOTATION]",
     "部分划线未被微信读书嵌入到正文中，程序已保留正文并略去这些划线。"),
    ("underline range(s) were not",
     "部分划线未被微信读书嵌入到正文中，程序已保留正文并略去这些划线。"),
    ("Shard MD5 mismatch",
     "章节数据校验失败，多次重新获取仍未通过；已保留断点，可稍后再试。"),
    ("marked paid",
     "这本书的某个章节需要购买或当前账号没有阅读权限。已保留已下载章节；请先在微信读书确认该章可读，再点“继续下载”。"),
    ("Private credentials need",
     "没有找到本机微信读书登录信息。请检查登录配置后重试。"),
    ("Credential file not found",
     "还没有配置微信读书基础凭证。请点顶栏「登录 / 切换账号」完成登录后重试。"),
    ("already running",
     "同一本书已有导出任务正在运行；请等待当前任务结束。"),
    ("cannot acquire desktop export lock",
     "未能安全取得任务锁。若没有其他下载窗口，请关闭旧窗口后再重试；断点已保留。"),
    ("gateway error", "微信读书服务暂时返回异常，请稍后重试；已有进度不会丢失。"),
    ("SSLError", "与微信读书的安全连接失败，请检查网络后重试。"),
    ("Timeout", "连接微信读书超时；已下载章节已保留，可以稍后继续。"),
    # The rate-limit needles have to precede HTTPError: a 426 *is* an
    # HTTPError, so a generic entry above would otherwise swallow it and tell
    # the user to check their network when the server was throttling them.
    ("426 Client Error", "微信读书触发了访问频率限制，程序已自动降速并退避重试；"
                        "这不是网络故障，也不用重新登录。"),
    ("429 Client Error", "微信读书触发了访问频率限制，程序已自动降速并退避重试；"
                        "这不是网络故障，也不用重新登录。"),
    ("Upgrade Required", "微信读书触发了访问频率限制，程序已自动降速并退避重试；"
                        "这不是网络故障，也不用重新登录。"),
    ("Too Many Requests", "微信读书触发了访问频率限制，程序已自动降速并退避重试；"
                        "这不是网络故障，也不用重新登录。"),
    ("HTTPError", "连接微信读书服务失败，请检查网络和登录状态后重试。"),
    ("ConnectionError", "连接微信读书服务失败，请检查网络和登录状态后重试。"),
    ("WeRead session expired",
     "微信读书的登录会话已失效，请点右上角账号按钮重新扫码登录，然后点“开始下载 / 继续下载”重试。已下载的章节会保留。"),
    ("No readable chapters in WeRead catalog",
     "微信读书没有返回可读章节。这本书可能未购买、已下架，或只支持手机端阅读。"
     "已有进度已保留；确认权限后可以重试。"),
)

FALLBACK_ZH = "操作没有完成。已有进度已保留；点击“查看技术日志”可查看详细原因和章节编号。"

# WeRead answers with sentinels instead of omitting a field: a missing
# publication date comes back as the Unix epoch, and an unscored book comes back
# with newRating=0. Rendering those verbatim claims "1970" and "0.0 / 10".
_MISSING_SENTINELS = {"", "0", "none", "null", "undefined", "-", "暂无", "未知", "无"}


def _is_missing_detail(value) -> bool:
    text = str("" if value is None else value).strip()
    if text.lower() in _MISSING_SENTINELS:
        return True
    parts = re.findall(r"\d+", text)
    return len(parts) >= 3 and [int(part) for part in parts[:3]] == [1970, 1, 1]


def redact_secrets(text: str) -> str:
    """Strip credentials before anything is written to a log the user can share."""
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(lambda match: match.group(1) + "[已隐藏]", text)
    return text


def rating_text(rating, count) -> str:
    if rating in (None, ""):
        return ""
    try:
        numeric = float(rating)
    except (ValueError, TypeError):
        return ""
    if numeric <= 0:
        return ""  # WeRead sends 0 for "nobody has scored this yet"
    display = f"{numeric / 100:.2f}" if numeric >= 100 else f"{numeric:.1f}"
    return f"评分：{display} / 10" + (f"  ·  {count} 人评分" if count else "")


def card_rating_text(rating, count) -> str:
    if rating in (None, ""):
        return "评分：暂无信息"
    try:
        numeric = float(rating)
    except (ValueError, TypeError):
        return "评分：暂无信息"
    if numeric <= 0:
        return "评分：暂无信息"
    display = f"{numeric / 100:.2f}" if numeric >= 100 else f"{numeric:.1f}"
    return f"评分：{display} / 10" + (f"  ·  {count} 人评分" if count else "  ·  评分人数暂无信息")


def book_detail_lines(data: dict) -> list[str]:
    details = []
    for key, label in (("category", "分类"), ("publisher", "出版社"),
                       ("publishTime", "出版时间"), ("isbn", "ISBN")):
        value = data.get(key)
        if _is_missing_detail(value):
            continue
        text = str(value).strip()
        if key == "publishTime":
            # 接口给的是 "2018-04-01 00:00:00"，年份后面那串零点对读者没意义，
            # 而且它足够长，会把「分类 · 出版社 · 出版时间」挤成两行。
            text = text.split()[0]
        details.append(f"{label}：{text}")
    return details or ["分类/出版社：暂无信息"]


def friendly_error(text: str) -> str:
    for needle, message in ERRORS_ZH:
        if needle in text:
            return message
    return FALLBACK_ZH


def parse_search_row(value: str) -> dict | None:
    """One `--search` stdout row -> a result card, or None if it is not one."""
    if not isinstance(value, str):
        return None
    fields = value.split("\t")
    if len(fields) < 6:
        return None
    book_id = fields[0].strip()
    if not book_id.isdigit():
        return None
    return {
        "bookId": book_id,
        "title": fields[1].strip(),
        "author": fields[2].strip(),
        "cover": fields[3].strip(),
        "newRating": fields[4].strip(),
        "newRatingCount": fields[5].strip(),
        "category": fields[6].strip() if len(fields) > 6 else "",
    }


def parse_search_meta(value: str) -> tuple[str, str] | None:
    if not isinstance(value, str) or not value.startswith("SEARCH_META\t"):
        return None
    fields = value.split("\t")
    if len(fields) < 2:
        return None
    book_id = fields[1].strip()
    if not book_id:
        return None
    return book_id, (fields[2].strip() if len(fields) > 2 else "")


def parse_book_info_message(value: str) -> dict | None:
    """`BOOK_INFO\t{json}` -> detail payload, or None when unusable."""
    if not isinstance(value, str) or not value.startswith("BOOK_INFO\t"):
        return None
    payload = value.split("\t", 1)[1].strip()
    if not payload:
        return None
    try:
        data = json.loads(payload)
    except (ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def worker_command(args: list[str]) -> list[str] | None:
    """Resolve the hidden worker process; None means the bundle is incomplete."""
    if not FROZEN:
        return [sys.executable, str(Path(__file__).with_name("weread_worker.py")), *args]
    override = os.environ.get("WEREAD_WORKER_EXE", "").strip()
    exe_dir = Path(sys.executable).resolve().parent
    candidates = [Path(override)] if override else []
    candidates += [
        exe_dir / "worker" / WORKER_NAME,
        exe_dir.parent / "WeReadWorker" / WORKER_NAME,
        exe_dir / "WeReadWorker" / WORKER_NAME,
        exe_dir / WORKER_NAME,
    ]
    for candidate in candidates:
        try:
            if candidate.is_file():
                return [str(candidate), *args]
        except OSError:
            continue
    return None
