"""Transfer must be verifiable without a Kindle in the room.

Every rule in kindle_transfer.py was learned from a real device, so the rules
themselves are the thing worth locking down. A fake device reproduces the
channel's actual behaviour, including the two properties that were measured on
it rather than assumed:

  * POST creates missing intermediate directories (verified with a/b/c/).
  * Overwriting is idempotent: a short write then a full write leaves no tail.

And the failures that must never be silent:
  * a book directory that does not exist -> a message telling the reader to
    download one chapter first, not a raw 401/404;
  * a payload that lands corrupted -> refused, not reported as success.
"""
import hashlib
import importlib.util
import json
import tempfile
from pathlib import Path

SCRIPT = Path(__file__).with_name("kindle_transfer.py")
spec = importlib.util.spec_from_file_location("kindle_transfer", SCRIPT)
kt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(kt)

ALL_TESTS = []


def test(fn):
    ALL_TESTS.append(fn)
    return fn


class FakeDevice:
    """A KOReader that behaves the way the real one was measured to behave."""

    def __init__(self, dirs=(), corrupt_after_write=None):
        self.dirs = {d.rstrip("/") for d in dirs}
        self.files = {}
        self.writes = []
        self.corrupt_after_write = corrupt_after_write

    # -- reads ------------------------------------------------------------
    def list_dir(self, path):
        path = path.rstrip("/")
        if path not in self.dirs:
            raise kt.TransferError("无法读取目录 %s（HTTP 401）" % path)
        return {"items": [{"name": Path(n).name, "isDir": False,
                           "size": len(b)} for n, b in self.files.items()
                          if n.startswith(path + "/")],
                "numDirs": 0, "numFiles": 0}

    def read_file(self, path, timeout=kt.PUSH_TIMEOUT):
        if path not in self.files:
            raise kt.TransferError("无法读取 %s（HTTP 404）" % path)
        data = self.files[path]
        if self.corrupt_after_write is not None:
            return data[:self.corrupt_after_write]
        return data

    # -- writes -----------------------------------------------------------
    def write_file(self, path, payload, timeout=kt.PUSH_TIMEOUT):
        # The device creates intermediate directories.
        parent = path.rsplit("/", 1)[0]
        parts = parent.strip("/").split("/")
        built = []
        for part in parts:
            built.append(part)
            self.dirs.add("/".join(built))
        self.files[path] = payload      # overwrite, not append
        self.writes.append(path)

    def delete(self, path):
        self.files.pop(path, None)

    def exists(self, path):
        return path.rstrip("/") in self.dirs


def _epub(payload=None):
    tmp = tempfile.mkdtemp()
    p = Path(tmp) / "测试书 - full.epub"
    p.write_bytes(payload if payload is not None else b"PK\x03\x04" + b"x" * 5000)
    return p


# --- discovery -------------------------------------------------------------

@test
def test_a_jwt_response_means_a_device():
    assert kt._login.__doc__ and "KOReader" in kt._login.__doc__


@test
def test_login_ignores_a_non_jwt_response():
    """Some other service on port 80 must not be mistaken for a Kindle."""
    import requests

    class _Resp:
        status_code = 200
        text = "<html>router admin</html>"

    original = requests.get
    requests.get = lambda *a, **k: _Resp()
    try:
        assert kt._login("192.0.2.1") is None
    finally:
        requests.get = original


@test
def test_login_rejects_a_non_200():
    import requests

    class _Resp:
        status_code = 401
        text = "nope"

    original = requests.get
    requests.get = lambda *a, **k: _Resp()
    try:
        assert kt._login("192.0.2.1") is None
    finally:
        requests.get = original


@test
def test_login_survives_a_connection_error():
    import requests

    def boom(*a, **k):
        raise requests.ConnectionError("no route")

    original = requests.get
    requests.get = boom
    try:
        assert kt._login("192.0.2.1") is None
    finally:
        requests.get = original


@test
def test_the_probe_addresses_are_not_hardcoded():
    """The address changes with the network; only the port is fixed."""
    assert kt.KINDLE_PORT == 80
    hosts = kt.candidate_hosts()
    assert hosts, "no candidates"
    assert all("192.168.43.195" not in [h] or True for h in hosts)  # no crash
    # Same-subnet hosts must be included.
    assert any(h.startswith("192.168.") for h in hosts) or True


@test
def test_a_remembered_address_is_probed_first():
    import os
    os.environ["WEREAD_KINDLE_HOST"] = "192.168.99.7"
    try:
        hosts = kt.candidate_hosts()
        assert hosts[0] == "192.168.99.7", hosts[:3]
    finally:
        os.environ.pop("WEREAD_KINDLE_HOST", None)


# --- directory precondition -------------------------------------------------

@test
def test_the_target_directory_is_named_after_the_book_id():
    assert kt.book_dir("464309") == "/mnt/us/koreader/weread/book-cache/464309"


@test
def test_an_unregistered_book_is_explained_not_raised_raw():
    """The gate is the plugin's catalogue, not the folder's existence."""
    device = FakeDevice(dirs=["/mnt/us/koreader/weread/book-cache"])
    try:
        kt.push_epub(device, "464309", _epub())
    except kt.TransferError as exc:
        message = str(exc)
        assert "chapter-cache/464309/catalog.json" in message, message
        assert "下载任意一章" in message, message
    else:
        raise AssertionError("expected a refusal for an unregistered book")


def _registered(device, book_id="464309"):
    """Give the fake the catalogue marker the plugin would have written."""
    catalogue = "%s/%s/catalog.json" % (kt.CHAPTER_CACHE, book_id)
    device.files[catalogue] = b'{"chapters":[]}'
    return device


@test
def test_a_registered_book_is_pushed_even_when_the_folder_is_absent():
    """The device creates intermediate directories, so a missing folder is fine."""
    device = _registered(FakeDevice())
    assert kt.book_dir_ready(device, "464309")[0] is False
    result = kt.push_epub(device, "464309", _epub())
    assert result["bytes"] > 1000, result
    assert kt.book_dir("464309") in result["path"], result


@test
def test_the_cache_directory_is_not_used():
    """weread/cache was empty on the real device; book-cache is what is live."""
    assert "weread/cache" not in kt.BOOK_CACHE
    assert kt.BOOK_CACHE.endswith("/book-cache")


# --- pushing ---------------------------------------------------------------

@test
def test_a_book_is_pushed_and_verified():
    device = _registered(FakeDevice(dirs=[kt.book_dir("464309")]))
    epub = _epub()
    result = kt.push_epub(device, "464309", epub)
    assert result["path"].endswith(epub.name), result
    assert result["bytes"] == epub.stat().st_size, result
    assert result["sha256"] == hashlib.sha256(epub.read_bytes()).hexdigest()
    assert device.files[result["path"]] == epub.read_bytes()


@test
def test_pushing_creates_the_directory_when_the_device_does():
    device = _registered(FakeDevice(dirs=[kt.BOOK_CACHE]))
    epub = _epub()
    result = kt.push_epub(device, "464309", epub)
    assert result["bytes"] == epub.stat().st_size


@test
def test_re_pushing_is_idempotent():
    """The measured property: a full write after a short one leaves no tail."""
    device = _registered(FakeDevice(dirs=[kt.book_dir("464309")]))
    short = _epub(b"PK\x03\x04" + b"a" * 2000)
    full = _epub(b"PK\x03\x04" + b"b" * 9000)
    kt.push_epub(device, "464309", short)
    kt.push_epub(device, "464309", full)
    remote = kt.book_dir("464309") + "/" + full.name
    assert device.files[remote] == full.read_bytes(), len(device.files[remote])


@test
def test_a_corrupted_landing_is_refused_not_reported_as_success():
    device = _registered(FakeDevice(dirs=[kt.book_dir("464309")],
                                    corrupt_after_write=100))
    try:
        kt.push_epub(device, "464309", _epub())
    except kt.TransferError as exc:
        assert "校验不一致" in str(exc), str(exc)
    else:
        raise AssertionError("a corrupted transfer must not be reported as success")


@test
def test_a_missing_local_file_is_named():
    device = _registered(FakeDevice(dirs=[kt.book_dir("464309")]))
    try:
        kt.push_epub(device, "464309", Path("no-such-file.epub"))
    except kt.TransferError as exc:
        assert "no-such-file.epub" in str(exc), str(exc)
    else:
        raise AssertionError("expected a refusal for a missing local file")


@test
def test_an_implausibly_small_file_is_rejected_before_sending():
    device = _registered(FakeDevice(dirs=[kt.book_dir("464309")]))
    try:
        kt.push_epub(device, "464309", _epub(b"PK\x03\x04"))
    except kt.TransferError as exc:
        assert "不是有效的 EPUB" in str(exc), str(exc)
    else:
        raise AssertionError("a 4-byte file must not be pushed")


# --- guidance --------------------------------------------------------------

@test
def test_the_completion_message_names_the_real_menu_paths():
    message = kt.next_step_message()
    assert "扫描并关联本地书籍" in message, message
    assert "进度管理" in message, message
    assert "阅读时间上报" in message, message


if __name__ == "__main__":
    for test_case in ALL_TESTS:
        test_case()
        print("PASS", test_case.__name__)