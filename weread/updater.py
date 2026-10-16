# -*- coding: utf-8 -*-
"""应用内自更新（GitHub Releases API，薄封装）。

设计要点：
- 不做"自写替换式更新器"。WeReadDownloader 装在 Program Files 下，自写写入
  会撞上写权限 + 单实例互斥体（WeReadDownloader-Graphics）+ 未签名 SmartScreen
  三道坑；而 Inno Setup 已经全部解决（重跑官方 Setup.exe 会用自己的 AppMutex
  关掉运行中的实例、覆盖文件、保留用户数据）。所以自更新 = "检测新版本 →
  下载官方 Setup.exe → 提权拉起它自己覆盖安装 → 自己退出"。
- 版本清单来源：GitHub Releases。`fetch_latest()` 调
  GET https://api.github.com/repos/nianqianha/WeReadDownloader/releases/latest
  一次拿到版本号（tag_name）、安装包直链（assets 里的
  WeReadDownloader-Setup.exe）、更新说明（body）。
- 失败必须静默降级：api.github.com 在国内可能不稳定，查不到就当"无更新"，
  绝不阻断下载主流程。
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlparse

if sys.version_info >= (3, 9):
    from importlib import resources
else:  # pragma: no cover
    import importlib.resources as resources  # type: ignore

import requests

# nianqianha/WeReadDownloader 是公开仓库，Releases 即现状分发渠道。
REPO_OWNER = "nianqianha"
REPO_NAME = "WeReadDownloader"
RELEASES_API = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/releases/latest"
SETUP_ASSET_NAME = "WeReadDownloader-Setup.exe"

# 语义化版本比较用的正则：容忍可选的 "v" 前缀与末尾的预发布后缀。
_VERSION_RE = re.compile(r"v?(\d+)\.(\d+)\.(\d+)")


def _normalize(tag: str) -> tuple[int, int, int] | None:
    """从 tag/版本串里取出 (major, minor, patch)；无法解析返回 None。"""
    match = _VERSION_RE.search(tag or "")
    if not match:
        return None
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def is_newer(current: str, latest: str) -> bool:
    """latest 是否比 current 更新（语义化比较，归一化 'v' 前缀）。"""
    a, b = _normalize(current), _normalize(latest)
    if a is None or b is None:
        return False
    return b > a


def parse_release(payload: dict) -> dict | None:
    """把 GitHub release JSON 整理成 {version, setup_url, notes, prerelease}。

    解析不出可下载的安装包时返回 None（视作"无可用更新"）。
    """
    if not isinstance(payload, dict):
        return None
    tag = str(payload.get("tag_name") or payload.get("name") or "")
    version = _normalize(tag)
    if version is None:
        return None
    assets = payload.get("assets") or []
    setup_url = ""
    for asset in assets:
        name = str(asset.get("name") or "")
        if name.lower() == SETUP_ASSET_NAME.lower():
            setup_url = str(asset.get("browser_download_url") or "")
            break
    if not setup_url:
        return None
    return {
        "version": f"{version[0]}.{version[1]}.{version[2]}",
        "raw_tag": tag,
        "setup_url": setup_url,
        "notes": str(payload.get("body") or "").strip(),
        "prerelease": bool(payload.get("prerelease")),
        "draft": bool(payload.get("draft")),
    }


def fetch_latest(timeout: float = 8.0) -> dict | None:
    """查最新 Release；任何失败都静默返回 None（当"无更新"，不阻断主流程）。

    预发布（prerelease）与草稿（draft）一律过滤掉，不把未正式发布的版本推给用户。
    """
    try:
        resp = requests.get(RELEASES_API, timeout=timeout, headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "WeReadDownloader-SelfUpdate",
        })
        if resp.status_code != 200:
            return None
        payload = parse_release(resp.json())
        if payload is None:
            return None
        if payload.get("prerelease") or payload.get("draft"):
            return None
        return payload
    except Exception:
        # 网络不通 / 被墙 / JSON 坏掉 / 超时 —— 都当没更新。
        return None


def download_setup(url: str, dest: Path | None = None,
                   on_progress=None, timeout: float = 120.0) -> Path | None:
    """把安装包下到临时目录；失败返回 None。on_progress(done, total) 可选。"""
    try:
        resp = requests.get(url, stream=True, timeout=timeout,
                            headers={"User-Agent": "WeReadDownloader-SelfUpdate"})
        resp.raise_for_status()
        total = int(resp.headers.get("content-length") or 0)
        dest = dest or (Path(tempfile.gettempdir()) / SETUP_ASSET_NAME)
        done = 0
        with open(dest, "wb") as stream:
            for chunk in resp.iter_content(chunk_size=1 << 16):
                if not chunk:
                    continue
                stream.write(chunk)
                done += len(chunk)
                if on_progress:
                    on_progress(done, total)
        return dest if dest.exists() and dest.stat().st_size > 0 else None
    except Exception:
        return None


def _is_installed_under_program_files(exe_path: Path) -> bool:
    """判断当前 exe 是否在需要管理员权限的 Program Files 下。"""
    try:
        path = exe_path.resolve()
    except OSError:
        return False
    parts = [p.lower() for p in path.parts]
    triggers = ("program files", "programfiles", "program files (x86)")
    # 盘符之后的第一段目录若命中即视为 Program Files 区（兼容 C:/Program Files(x86)/...）。
    if len(parts) >= 2 and parts[1] in triggers:
        return True
    # 兜底：把整条路径当字符串再比一次（处理 "program files (x86)" 含空格的形态）。
    lowered = str(path).lower().replace("\\", "/")
    return any(("/" + t.replace(" ", " ") + "/") in lowered or lowered.endswith(t) for t in triggers)


def apply_update(setup_path: Path) -> bool:
    """拉起官方 Setup.exe 覆盖安装并退出本程序。

    返回 True 表示已成功发起（本程序应随即 sys.exit）；返回 False 表示拉起失败。
    """
    import ctypes
    from ctypes import wintypes

    setup = str(setup_path)
    # /SILENT 后台覆盖；Inno 会用同一个 AppMutex 关掉运行中的实例并保留用户数据。
    params = "/SILENT"
    exe_dir = str(Path(setup).resolve().parent)
    need_admin = _is_installed_under_program_files(Path(sys.executable))
    if need_admin:
        # runas 提权；失败（用户拒绝）时 ShellExecute 返回 <= 32。
        result = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", setup, params, exe_dir, 1)
        launched = result > 32
    else:
        result = ctypes.windll.shell32.ShellExecuteW(
            None, "open", setup, params, exe_dir, 1)
        launched = result > 32
    if launched:
        # 让 Inno 的 CloseApplications 干净接管；本程序主动退出。
        sys.exit(0)
    return False
