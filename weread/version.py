# -*- coding: utf-8 -*-
"""应用自身版本的单一事实源。

WeReadDownloader 是 PyInstaller 冻结的 exe，过去 GUI 里根本没有版本号，
自更新逻辑无从判断"我是不是最新版"。这里写死一次，安装包版本号
(installer/WeReadDesktop.iss 的 #define MyAppVersion) 由
tools/sync_version.py 在打包前从这里同步，避免两处版本号打架。

发版时：只改 APP_VERSION 这一处，再跑 `python tools/sync_version.py`。
"""
APP_VERSION = "1.0.5"
