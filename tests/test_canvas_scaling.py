# -*- coding: utf-8 -*-
"""Canvas 自绘界面必须跟随系统缩放（issue #94）。

书架与搜索结果是 Canvas 画出来的：CTkFont 的 size 是「与缩放无关的像素」，只有
CTk 控件绘制自己时才乘缩放倍率，所以画布上的行高、封面、按钮、字号原本全部
不随系统 DPI 变大——150/175% 屏上整块偏小偏挤。这里守住四件事：
  1. 行高随 widget scaling 等比放大（scrollregion 是最直接的观测量）；
  2. 缩放档位切换后**不手动重绘**行高就变——这证明 ScalingTracker 回调已登记
     （tk.Canvas 不在 CTk 的通知名单里，跨屏/改 DPI 全靠这个回调）；
  3. 画布文字字号随缩放变大；
  4. 行高变了之后点击命中仍落在同一行。
"""
import importlib.util
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "weread"))
SCRIPT = (Path(__file__).resolve().parent.parent / "weread" / "desktop_app.py")
spec = importlib.util.spec_from_file_location("desktop_app", SCRIPT)
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)

_TMP = tempfile.TemporaryDirectory(prefix="weread-canvas-scaling-")
app.LOG_DIR = Path(_TMP.name) / "logs"
app.OUTPUT = Path(_TMP.name) / "exports"
app.weread_login.CREDENTIAL_STATE = Path(_TMP.name) / "session-cookies.json"

ALL_TESTS = []


def test(fn):
    ALL_TESTS.append(fn)
    return fn


SHELF_LINE = ('SHELF\t['
              '{"bookId":"19398284","title":"逆流纯真年代","author":"人间武库",'
              '"cover":"","category":"男生小说-年代幻想"},'
              '{"bookId":"41100833","title":"北派盗墓笔记","author":"云峰",'
              '"cover":"","category":"悬疑"}'
              ']')

RESULT_ROW = {"bookId": "123", "title": "测试书籍", "author": "作者",
              "category": "文学", "newRating": "8.5", "newRatingCount": "3"}


def _make_gui():
    import customtkinter as ctk
    root = ctk.CTk()
    root.geometry("1120x790")
    instance = app.DesktopApp(root)
    root.update()
    return root, instance


def _saved_scaling():
    """保存/恢复必须用 ScalingTracker 上的手动倍率，不是 get_widget_scaling。

    get_widget_scaling 返回的是「系统 DPI × 手动倍率」的总数；把它回写进
    set_widget_scaling 会在 125% 机器上留下 1.25 的手动倍率，污染之后所有
    测试（2026-11-22 组合运行实测，layout 三个用例被它打红）。测试里的
    set_widget_scaling(1.5) 是在当前基础上再乘 1.5，比例对比才干净。
    """
    import customtkinter as ctk
    probe = ctk.CTk()
    try:
        return (ctk.ScalingTracker.widget_scaling, ctk.ScalingTracker.window_scaling)
    finally:
        probe.destroy()


def _scroll_height(canvas):
    return float(str(canvas.cget("scrollregion")).split()[-1])


@test
def test_shelf_row_height_scales_without_a_manual_redraw():
    """×1.5 后 scrollregion 直接变大——证明 ScalingTracker 回调已登记。

    旧实现里改系统 DPI / 跨屏拖动画布收不到任何通知（<Configure> 只在物理
    尺寸变化时触发），这条测试如果只靠 <Configure> 就过不了。
    """
    import customtkinter as ctk
    saved_widget, saved_window = _saved_scaling()
    root, inst = _make_gui()
    try:
        inst.load_shelf = lambda: None
        inst._switch_page("我的书架")
        root.update()
        inst._show_shelf(inst._parse_shelf(SHELF_LINE))
        root.update()
        shelf = inst.shelf_frame
        h1 = _scroll_height(shelf.canvas)
        ctk.set_widget_scaling(1.5)
        root.update()
        h2 = _scroll_height(shelf.canvas)
        assert abs(h2 / h1 - 1.5) < 0.08, (
            "缩放 ×1.5 后 scrollregion 高度 %s -> %s，比例不是 1.5" % (h1, h2))
    finally:
        ctk.set_widget_scaling(saved_widget)
        ctk.set_window_scaling(saved_window)
        root.destroy()


@test
def test_result_rows_and_fonts_scale_too():
    import customtkinter as ctk
    saved_widget, saved_window = _saved_scaling()
    root, inst = _make_gui()
    try:
        inst.result_rows = [dict(RESULT_ROW)]
        inst._render_page()
        root.update()
        results = inst.results
        h1 = _scroll_height(results.canvas)
        ctk.set_widget_scaling(1.5)
        root.update()
        h2 = _scroll_height(results.canvas)
        assert abs(h2 / h1 - 1.5) < 0.08, (
            "搜索结果行高 %s -> %s，比例不是 1.5" % (h1, h2))
        # 字号也必须乘上倍率：CTkFont 只在 CTk 控件自己绘制时缩放，
        # 画布文字要走 create_scaled_tuple，否则 150% 屏上文字还是 13px。
        entry = results.items["123"]
        font_spec = results.canvas.itemcget(entry["title"], "font")
        expected_size = -round(13 * ctk.ScalingTracker.get_widget_scaling(results.canvas))
        assert str(expected_size) in font_spec, (
            "标题字号 %r 里看不到缩放后的 %d" % (font_spec, expected_size))
    finally:
        ctk.set_widget_scaling(saved_widget)
        ctk.set_window_scaling(saved_window)
        root.destroy()


@test
def test_cover_slot_grows_with_scaling():
    import customtkinter as ctk
    saved_widget, saved_window = _saved_scaling()
    root, inst = _make_gui()
    try:
        inst.load_shelf = lambda: None
        inst._switch_page("我的书架")
        root.update()
        inst._show_shelf(inst._parse_shelf(SHELF_LINE))
        root.update()
        shelf = inst.shelf_frame
        x0, y0, x1, y1 = shelf.canvas.coords(shelf.items["19398284"]["cover_bg"])
        h1 = y1 - y0
        ctk.set_widget_scaling(1.5)
        root.update()
        x0, y0, x1, y1 = shelf.canvas.coords(shelf.items["19398284"]["cover_bg"])
        h2 = y1 - y0
        assert abs(h2 / h1 - 1.5) < 0.08, (
            "封面槽高 %s -> %s，比例不是 1.5" % (h1, h2))
    finally:
        ctk.set_widget_scaling(saved_widget)
        ctk.set_window_scaling(saved_window)
        root.destroy()


@test
def test_click_hits_the_same_row_after_scaling():
    """行高变了命中计算必须跟着变：点「第二行行内」必须选中第二本书。"""
    import customtkinter as ctk
    saved_widget, saved_window = _saved_scaling()
    root, inst = _make_gui()
    try:
        inst.load_shelf = lambda: None
        inst._switch_page("我的书架")
        root.update()
        inst._show_shelf(inst._parse_shelf(SHELF_LINE))
        root.update()
        shelf = inst.shelf_frame
        downloads = []
        inst._download_from_shelf = lambda bid, title: downloads.append((bid, title))
        ctk.set_widget_scaling(1.5)
        root.update()
        row_h = shelf._row_h()
        # canvas 未滚动时 canvasy 恒等；y 取第二行行内（第二行起点是 row_h）。
        shelf._on_click(SimpleNamespace(y=row_h + 20))
        assert downloads and downloads[0][0] == "41100833", downloads
    finally:
        ctk.set_widget_scaling(saved_widget)
        ctk.set_window_scaling(saved_window)
        root.destroy()


if __name__ == "__main__":
    ok = True
    for case in ALL_TESTS:
        case()
        print("PASS", case.__name__)
    print()
    print("CANVAS SCALING:", "PASS" if ok else "FAIL")
