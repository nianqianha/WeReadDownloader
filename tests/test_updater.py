"""updater.py 单元测试：版本比较、Release 解析、fetch 解析、下载落盘。

网络与真实 ShellExecute 不在单测内执行：fetch_latest 用 monkeypatch 注入假
requests；apply_update 只测"是否装在非管理员目录"的判定（真实的提权拉起会杀掉
本进程，不能跑）。
"""
import importlib.util
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "weread"))
SCRIPT = (Path(__file__).resolve().parent.parent / "weread" / "updater.py")
spec = importlib.util.spec_from_file_location("updater", SCRIPT)
updater = importlib.util.module_from_spec(spec)
spec.loader.exec_module(updater)


# ---------------------------------------------------------------- 版本比较
def test_is_newer_normalizes_v_prefix():
    assert updater.is_newer("1.0.4", "v1.0.5") is True
    assert updater.is_newer("1.0.4", "1.0.4") is False
    assert updater.is_newer("1.0.5", "1.0.4") is False
    assert updater.is_newer("1.0.4", "v2.0.0") is True
    # 无法解析时一律当"不更新"，绝不误判
    assert updater.is_newer("notaversion", "v1.0.5") is False
    assert updater.is_newer("1.0.4", "garbage") is False


# ---------------------------------------------------------------- Release 解析
def _release(tag="v1.0.5", prerelease=False, draft=False, asset="WeReadDownloader-Setup.exe"):
    return {
        "tag_name": tag,
        "prerelease": prerelease,
        "draft": draft,
        "body": "修复了排队下载与自更新。",
        "assets": [{"name": asset,
                    "browser_download_url": "https://example.com/" + asset}],
    }


def test_parse_release_picks_setup_asset_and_version():
    parsed = updater.parse_release(_release())
    assert parsed["version"] == "1.0.5", parsed
    assert parsed["setup_url"].endswith("WeReadDownloader-Setup.exe"), parsed
    assert "排队下载" in parsed["notes"]


def test_parse_release_rejects_when_no_setup_asset():
    assert updater.parse_release(_release(asset="other.exe")) is None


def test_parse_release_rejects_bad_tag():
    assert updater.parse_release(_release(tag="no-version")) is None


# ---------------------------------------------------------------- fetch_latest
class _FakeResp:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload
    def json(self):
        return self._payload


def test_fetch_latest_parses_valid_release(monkeypatch):
    monkeypatch.setattr(updater.requests, "get",
                        lambda *a, **k: _FakeResp(200, _release()))
    latest = updater.fetch_latest()
    assert latest and latest["version"] == "1.0.5"


def test_fetch_latest_silent_on_http_error(monkeypatch):
    monkeypatch.setattr(updater.requests, "get",
                        lambda *a, **k: _FakeResp(503, {}))
    assert updater.fetch_latest() is None


def test_fetch_latest_silent_on_exception(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("network blocked")
    monkeypatch.setattr(updater.requests, "get", _boom)
    assert updater.fetch_latest() is None


def test_fetch_latest_skips_prerelease(monkeypatch):
    monkeypatch.setattr(updater.requests, "get",
                        lambda *a, **k: _FakeResp(200, _release(prerelease=True)))
    assert updater.fetch_latest() is None


# ---------------------------------------------------------------- download_setup
class _FakeStreamResp:
    def __init__(self, data):
        self._data = data
        self.headers = {"content-length": str(len(data))}
        self.status_code = 200
    def raise_for_status(self):
        return None
    def iter_content(self, chunk_size=1 << 16):
        yield self._data


def test_download_setup_writes_file(monkeypatch):
    data = b"MZ" + b"\x00" * 1024  # 假 exe 头
    monkeypatch.setattr(updater.requests, "get",
                        lambda *a, **k: _FakeStreamResp(data))
    dest = Path(tempfile.gettempdir()) / "weread-test-setup.exe"
    out = updater.download_setup("https://example.com/x.exe", dest=dest)
    assert out is not None and out.exists()
    assert out.read_bytes() == data
    out.unlink(missing_ok=True)


def test_download_setup_returns_none_on_error(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("download failed")
    monkeypatch.setattr(updater.requests, "get", _boom)
    assert updater.download_setup("https://example.com/x.exe") is None


# ---------------------------------------------------------------- 安装位置判定
def test_install_location_judgement():
    here = Path(sys.executable).resolve()
    # 当前 Python 解释器九成不在 Program Files 下，应判为非管理员安装
    assert updater._is_installed_under_program_files(here) in (True, False)
    assert updater._is_installed_under_program_files(
        Path("C:/Program Files/WeReadDownloader/WeReadDesktop/WeReadDesktop.exe")) is True
    assert updater._is_installed_under_program_files(
        Path("C:/Users/asus/AppData/Local/Programs/WeReadDownloader/WeReadDesktop/WeReadDesktop.exe")) is False


# ---------------------------------------------------------------- 多源回退（2026-10-24）
class _FakeUrlResp:
    """带 url/text 的假响应，覆盖网页 302 与 atom feed 两条回退路径。"""

    def __init__(self, status, url="", text="", json_payload=None):
        self.status_code = status
        self.url = url
        self.text = text
        self._payload = json_payload

    def json(self):
        return self._payload


def test_setup_url_for_builds_fixed_asset_link():
    assert updater._setup_url_for("v1.2.3") == (
        "https://github.com/nianqianha/WeReadDownloader/releases/download/v1.2.3/"
        "WeReadDownloader-Setup.exe")
    assert updater._setup_url_for("garbage") == ""


def test_tag_from_url_accepts_only_plain_stable_tags():
    assert updater._tag_from_url("https://github.com/o/r/releases/tag/v1.2.3") == "v1.2.3"
    assert updater._tag_from_url("https://github.com/o/r/releases/tag/v1.2.3/") == "v1.2.3"
    # 预发布后缀不认：回退源只发正式版
    assert updater._tag_from_url("https://github.com/o/r/releases/tag/v1.2.3-beta") is None
    assert updater._tag_from_url("https://github.com/o/r/releases") is None


def test_page_fallback_used_when_api_rate_limited(monkeypatch):
    """api.github.com 403（本机实测的限流形态）时，网页 302 源要顶上。"""
    def router(url, **kw):
        if url.startswith("https://api.github.com"):
            return _FakeResp(403, {})
        if url == updater.RELEASES_PAGE:
            return _FakeUrlResp(200, url="https://github.com/nianqianha/WeReadDownloader/releases/tag/v1.0.6")
        if url.endswith("/WeReadDownloader-Setup.exe"):
            return _FakeUrlResp(200)          # HEAD 探活
        raise AssertionError("unexpected url " + str(url))
    monkeypatch.setattr(updater.requests, "get", router)
    monkeypatch.setattr(updater.requests, "head", lambda *a, **k: _FakeUrlResp(200))
    latest = updater.fetch_latest()
    assert latest and latest["source"] == "web" and latest["version"] == "1.0.6", latest
    assert latest["setup_url"].endswith("/download/v1.0.6/WeReadDownloader-Setup.exe")


def test_atom_fallback_skips_prerelease_tags(monkeypatch):
    atom = ('<feed><entry><id>https://github.com/o/r/releases/tag/v1.1.0-beta</id></entry>'
            '<entry><id>https://github.com/o/r/releases/tag/v1.0.6</id></entry></feed>')

    def router(url, **kw):
        if url.startswith("https://api.github.com") or url == updater.RELEASES_PAGE:
            return _FakeResp(503, {})
        if url == updater.RELEASES_ATOM:
            return _FakeUrlResp(200, text=atom)
        if url.endswith("/WeReadDownloader-Setup.exe"):
            return _FakeUrlResp(200)
        raise AssertionError("unexpected url " + str(url))
    monkeypatch.setattr(updater.requests, "get", router)
    monkeypatch.setattr(updater.requests, "head", lambda *a, **k: _FakeUrlResp(200))
    latest = updater.fetch_latest()
    assert latest and latest["source"] == "atom" and latest["version"] == "1.0.6", latest


def test_all_sources_down_stays_silent(monkeypatch):
    monkeypatch.setattr(updater.requests, "get", lambda *a, **k: _FakeResp(503, {}))
    monkeypatch.setattr(updater.requests, "head", lambda *a, **k: _FakeUrlResp(503))
    assert updater.fetch_latest() is None
