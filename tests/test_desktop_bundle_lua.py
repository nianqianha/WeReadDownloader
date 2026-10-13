"""Run test_desktop_bundle.lua through LuaJIT (Windows non-ASCII path workaround).

LuaJIT's Windows file searcher cannot open this workspace's Chinese dirs (the
same limitation weread_desktop.py documents), so desktop_bundle.lua is loaded
from bytes into package.loaded first, exactly like _load_lua_module() does;
afterwards require() in the test resolves from the package cache.
"""
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
PLUGIN = PROJ.parent.parent / "03_插件源码与解压文件" / "weread.koplugin"
TEST = PROJ / "tests" / "test_desktop_bundle.lua"
MODULE = PLUGIN / "weread" / "lib" / "desktop_bundle.lua"

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
