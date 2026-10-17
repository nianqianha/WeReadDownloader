"""测试套件的公共配置。

引擎的 Lua 那一半需要一个位于**仓库之外**的插件目录
（`<repo>/../Kindle/03_插件源码与解压文件/weread.koplugin`，或用环境变量
`WEREAD_KINDLE_ROOT` 指定）。干净检出（协作者的机器、CI）没有这个目录，
那些用例跑不了。让它们 FAIL 等于在说"你的改动弄坏了东西"，而事实是"这台机器
没有插件"——所以改成带原因地 skip，红色才继续只意味着真缺陷。

放在这里而不是在受影响的每个文件里加标记，是因为这个条件是从
`weread_desktop.lua_runtime()` 内部抛出的，而那几个文件各自走自己的路径到达它：
按错误内容识别是唯一能一处覆盖全部的做法。
"""
import gc
import pytest


@pytest.fixture(autouse=True)
def collect_tk_objects_on_test_thread():
    # Tk finalizers must not run in a worker thread's automatic GC cycle.
    enabled = gc.isenabled()
    gc.disable()
    try:
        yield
    finally:
        gc.collect()
        if enabled:
            gc.enable()

# 与 weread_desktop.PLUGIN_MISSING 同源；只匹配这一句，别的 RuntimeError 照常失败。
PLUGIN_MISSING_MARK = "Lua 插件目录"


@pytest.hookimpl(wrapper=True)
def pytest_runtest_call(item):
    try:
        return (yield)
    except RuntimeError as exc:
        if PLUGIN_MISSING_MARK in str(exc):
            pytest.skip("本机没有外部 Lua 插件目录（可用 WEREAD_KINDLE_ROOT 指定）")
        raise
