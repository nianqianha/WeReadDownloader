"""Exercise real update callbacks without downloading or launching installers."""
import ast
from pathlib import Path
import queue
from types import MethodType, SimpleNamespace
from unittest.mock import Mock

import pytest
from test_updater import updater


class _InlineThread:
    """Thread stub that runs the target immediately and records the spawn."""

    started: list = []

    def __init__(self, target=None, args=(), daemon=None, **_kwargs):
        self.target = target
        self.args = args

    def start(self):
        type(self).started.append((self.target, self.args))
        if self.target is not None:
            self.target(*self.args)


@pytest.fixture
def gui():
    source = Path(__file__).resolve().parents[1] / "weread/desktop_app.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    app = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "DesktopApp")
    names = ("_check_for_update", "_fetch_update", "_handle_update",
             "_download_and_apply", "_on_update_failed")
    methods = [node for node in app.body if isinstance(node, ast.FunctionDef)
               and node.name in names]
    service = SimpleNamespace(fetch_latest=Mock(), is_newer=updater.is_newer,
                              download_setup=Mock(return_value=Path("synthetic.exe")),
                              apply_update=Mock(return_value=True))
    _InlineThread.started = []
    namespace = {"_updater": service, "_version": SimpleNamespace(APP_VERSION="1.0.5"),
                 "threading": SimpleNamespace(Thread=_InlineThread)}
    exec(compile(ast.fix_missing_locations(ast.Module(body=methods, type_ignores=[])), str(source), "exec"), namespace)
    instance = SimpleNamespace(status=Mock(), _ask=Mock(), _is_busy=lambda: False,
                               _offer_update=Mock(), events=queue.Queue(), check_update_button=Mock())
    # _check_for_update reaches self._fetch_update; bind the extracted bodies so
    # the instance behaves like the real class.
    for name in names:
        setattr(instance, name, MethodType(namespace[name], instance))
    return instance, service, namespace


def test_check_for_update_hands_the_network_to_a_thread(gui):
    """The button/startup path must not fetch or open dialogs on the Tk thread."""
    instance, service, methods = gui
    release = {"version": "1.0.6", "setup_url": "https://example.invalid/setup"}
    service.fetch_latest.return_value = release
    methods["_check_for_update"](instance, silent=True)
    # Nothing user-visible happened on the caller's stack.
    instance._ask.assert_not_called()
    instance._offer_update.assert_not_called()
    # The fetch ran inside the spawned thread and queued its result for the UI.
    assert _InlineThread.started, "no worker thread was started"
    kind, (silent, latest) = instance.events.get_nowait()
    assert (kind, silent, latest["version"]) == ("update", True, "1.0.6")


@pytest.mark.parametrize("response", [None, {}, RuntimeError("fetch failed")])
def test_fetch_result_goes_back_through_the_event_queue(gui, response):
    instance, service, methods = gui
    if isinstance(response, Exception):
        service.fetch_latest.side_effect = response
    else:
        service.fetch_latest.return_value = response
    methods["_fetch_update"](instance, True)
    kind, (silent, latest) = instance.events.get_nowait()
    expected = None if isinstance(response, Exception) else response
    assert kind == "update" and silent is True and latest == expected


@pytest.mark.parametrize("response", [None, {}, RuntimeError("fetch failed")])
def test_failed_update_check_does_not_claim_latest(gui, response):
    instance, service, methods = gui
    methods["_handle_update"](instance, False, None if isinstance(response, Exception) else response)
    assert instance._ask.call_args.args[:2] == ("showerror", "无法检查更新")
    assert "最新" not in instance._ask.call_args.args[2]
    instance._ask.reset_mock()
    methods["_handle_update"](instance, True, None)
    instance._ask.assert_not_called()


@pytest.mark.parametrize("version", ["1.0.4", "1.0.5", "1.0.6"])
def test_valid_update_results_keep_version_semantics(gui, version):
    instance, service, methods = gui
    release = {"version": version, "setup_url": "https://example.invalid/setup"}
    methods["_handle_update"](instance, False, release)
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
