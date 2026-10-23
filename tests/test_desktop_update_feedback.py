"""Exercise real update callbacks without downloading or launching installers."""
import ast
from pathlib import Path
import queue
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from test_updater import updater


@pytest.fixture
def gui():
    source = Path(__file__).resolve().parents[1] / "weread/desktop_app.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    app = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "DesktopApp")
    methods = [node for node in app.body if isinstance(node, ast.FunctionDef)
               and node.name in ("_check_for_update", "_download_and_apply", "_on_update_failed")]
    service = SimpleNamespace(fetch_latest=Mock(), is_newer=updater.is_newer,
                              download_setup=Mock(return_value=Path("synthetic.exe")),
                              apply_update=Mock(return_value=True))
    namespace = {"_updater": service, "_version": SimpleNamespace(APP_VERSION="1.0.5"),
                 "threading": SimpleNamespace(Thread=lambda target, daemon: SimpleNamespace(start=target))}
    exec(compile(ast.fix_missing_locations(ast.Module(body=methods, type_ignores=[])), str(source), "exec"), namespace)
    instance = SimpleNamespace(status=Mock(), _ask=Mock(), _is_busy=lambda: False,
                               _offer_update=Mock(), events=queue.Queue(), check_update_button=Mock())
    return instance, service, namespace


@pytest.mark.parametrize("response", [None, {}, RuntimeError("fetch failed")])
def test_failed_update_check_does_not_claim_latest(gui, response):
    instance, service, methods = gui
    if isinstance(response, Exception):
        service.fetch_latest.side_effect = response
    else:
        service.fetch_latest.return_value = response
    methods["_check_for_update"](instance)
    assert instance._ask.call_args.args[:2] == ("showerror", "无法检查更新")
    assert "最新" not in instance._ask.call_args.args[2]
    instance._ask.reset_mock()
    methods["_check_for_update"](instance, silent=True)
    instance._ask.assert_not_called()


@pytest.mark.parametrize("version", ["1.0.4", "1.0.5", "1.0.6"])
def test_valid_update_results_keep_version_semantics(gui, version):
    instance, service, methods = gui
    release = {"version": version, "setup_url": "https://example.invalid/setup"}
    service.fetch_latest.return_value = release
    methods["_check_for_update"](instance)
    if version == "1.0.6":
        instance._offer_update.assert_called_once_with(release)
        instance._ask.assert_not_called()
    else:
        assert instance._ask.call_args.args[:2] == ("showinfo", "已是最新")
        instance._offer_update.assert_not_called()


@pytest.mark.parametrize("failure", ["download-empty", "download-error", "launch-false", "launch-error"])
def test_update_failure_reaches_ui_and_restores_button(gui, failure):
    instance, service, methods = gui
    if failure == "download-empty":
        service.download_setup.return_value = None
    elif failure == "download-error":
        service.download_setup.side_effect = OSError("download failed")
    elif failure == "launch-false":
        service.apply_update.return_value = False
    else:
        service.apply_update.side_effect = OSError("launch failed")
    methods["_download_and_apply"](instance, {"version": "1.0.6", "setup_url": "https://example.invalid/setup"})
    instance.check_update_button.configure.assert_called_with(state="disabled")
    kind, args = instance.events.get_nowait()
    assert kind == "update-failed" and instance.events.empty()
    methods["_on_update_failed"](instance, *args)
    instance.check_update_button.configure.assert_called_with(state="normal")
    assert instance._ask.call_args.args[:2] == ("showerror", "更新失败")
    if failure.startswith("download"):
        service.apply_update.assert_not_called()
    else:
        service.apply_update.assert_called_once_with(Path("synthetic.exe"))


def test_successful_installer_launch_does_not_emit_failure(gui):
    instance, service, methods = gui
    methods["_download_and_apply"](instance, {"version": "1.0.6", "setup_url": "https://example.invalid/setup"})
    service.apply_update.assert_called_once_with(Path("synthetic.exe"))
    assert instance.events.empty()
