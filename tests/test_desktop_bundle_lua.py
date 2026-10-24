"""Run test_desktop_bundle.lua through LuaJIT (Windows non-ASCII path workaround).

LuaJIT's Windows file searcher cannot open this workspace's Chinese dirs (the
same limitation weread_desktop.py documents), so desktop_bundle.lua is loaded
from bytes into package.loaded first, exactly like _load_lua_module() does;
afterwards require() in the test resolves from the package cache.
"""
import os
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

try:
    from lupa.luajit21 import LuaRuntime
except ImportError:
    from lupa import LuaRuntime


# The program modules live in weread/ and import each other by bare name.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "weread"))
PROJ = Path(__file__).resolve().parent.parent
# the plugin tree is a SIBLING of the repo parent since the 2026-10 move
def _plugin_dir():
    roots = []
    env_root = os.environ.get("WEREAD_KINDLE_ROOT")
    if env_root:
        roots.append(Path(env_root))
    roots.append(PROJ.parent / "Kindle")
    for root in roots:
        candidate = root / "03_插件源码与解压文件" / "weread.koplugin"
        if candidate.is_dir():
            return candidate
    return roots[-1] / "03_插件源码与解压文件" / "weread.koplugin"


PLUGIN = _plugin_dir()
TEST = PROJ / "tests" / "test_desktop_bundle.lua"
MODULE = PLUGIN / "weread" / "lib" / "desktop_bundle.lua"

# 这个文件是脚本式的——整段流程在导入期就跑完，没有可收集的 test_ 函数。所以
# 插件缺席时必须在导入期就退出：否则 pytest 在收集阶段直接 FileNotFoundError，
# 报出来像"代码坏了"，实际是"这台机器没有外部插件目录"。
if not MODULE.is_file():
    import pytest
    pytest.skip("本机没有外部 Lua 插件目录", allow_module_level=True)

lua = LuaRuntime(unpack_returned_tuples=True, encoding=None)
lua.execute(
    b'package.loaded["json"] = {decode=function() return {} end, '
    b'encode=function() return "{}" end}\n'
    b'package.loaded["weread.lib.content"] = {}\n'
    b'package.loaded["weread.lib.thought_db"] = {}\n'
    b'package.loaded["weread.lib.plugin_util"] = {tr=function(v) return v end}\n'
)
lua.execute(
    b'package.loaded["weread.lib.desktop_bundle"] = (function()\n'
    + MODULE.read_bytes()
    + b"\nend)()"
)

result = lua.execute(TEST.read_bytes())
if result is not None:
    print(result)
print("PASS test_desktop_bundle.lua")
