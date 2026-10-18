# -*- coding: utf-8 -*-
"""发版前同步版本号：把 weread/version.py 的 APP_VERSION 写进安装脚本。

过去版本号写在两处——代码里（没有）和 installer/WeReadDesktop.iss 的
#define MyAppVersion——容易只改一处而打架。本脚本让 version.py 成为单一事实源：
改了 version.py 后跑一次 `python tools/sync_version.py`，ISS 自动同步，
打包出的 Setup.exe 版本号就和运行时看到的一致。

用法：
    python tools/sync_version.py
退出码非 0 表示未同步（文件缺失或值已一致但解析失败）。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSION_FILE = ROOT / "weread" / "version.py"
ISS_FILE = ROOT / "installer" / "WeReadDesktop.iss"

_VERSION_RE = re.compile(r'APP_VERSION\s*=\s*["\']([^"\']+)["\']')
_ISS_DEFINE_RE = re.compile(r'(#define\s+MyAppVersion\s+)"[^"]*"')


def read_app_version() -> str | None:
    text = VERSION_FILE.read_text(encoding="utf-8")
    match = _VERSION_RE.search(text)
    return match.group(1) if match else None


def sync() -> int:
    version = read_app_version()
    if not version:
        print(f"[sync_version] 无法从 {VERSION_FILE} 解析 APP_VERSION", file=sys.stderr)
        return 1
    if not ISS_FILE.is_file():
        print(f"[sync_version] 找不到 {ISS_FILE}", file=sys.stderr)
        return 1
    iss_text = ISS_FILE.read_text(encoding="utf-8")
    if not _ISS_DEFINE_RE.search(iss_text):
        print(f"[sync_version] {ISS_FILE} 里没有 #define MyAppVersion", file=sys.stderr)
        return 1
    new_iss = _ISS_DEFINE_RE.sub(lambda m: f'{m.group(1)}"{version}"', iss_text)
    if new_iss == iss_text:
        print(f"[sync_version] 版本号已是 {version}，无需改动。")
        return 0
    ISS_FILE.write_text(new_iss, encoding="utf-8")
    print(f"[sync_version] 已将 {ISS_FILE.name} 的 MyAppVersion 同步为 {version}。")
    return 0


if __name__ == "__main__":
    raise SystemExit(sync())
