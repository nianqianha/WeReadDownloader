"""Push a finished EPUB from the PC to the Kindle, over the wireless channel.

Why this exists: the project log records that the device address changes with
the network (`192.168.43.x` on a phone hotspot, `192.168.137.x` on the Windows
hotspot) while only port 80 ever answers -- 8081/8000/3000 were probed and all
refused. So the address cannot be hardcoded, and the port can be.

The channel needs no password: GET /api/login hands back a JWT whose payload
says local:"en", and that token works as X-Auth. Verified on-device: writing
creates missing intermediate directories, overwriting is idempotent (a short
write followed by a full one leaves no tail), and a read-back is byte-identical.

Scope is deliberately the book only. Annotations live in a SQLite database whose
`annotation_data.payload` column is an opaque blob; producing a compatible file
would mean reverse-engineering that format and writing into the device's 有道记
store, which is not something to guess at.
"""
from __future__ import annotations

import hashlib
import ipaddress
import os
import socket
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

import requests

# Port 80 is the only one the device ever answers on, per the project log.
KINDLE_PORT = 80
# KOReader serves the file API under its own root; this is the path the plugin
# writes whole books into, and it is NOT weread/cache (that directory is empty on
# a real device -- verified).
WEREAD_ROOT = "/mnt/us/koreader/weread"
BOOK_CACHE = WEREAD_ROOT + "/book-cache"
# The plugin's catalogue is what makes it recognise a book; a real device carries
# both directories and the one with catalog.json is the registration marker.
CHAPTER_CACHE = WEREAD_ROOT + "/chapter-cache"
WEREAD_SETTINGS = "/mnt/us/koreader/settings/weread.lua"

LOGIN_PATH = "/api/login"
RAW_PATH = "/api/raw"
RESOURCE_PATH = "/api/resources"

CONNECT_TIMEOUT = 1.5
PROBE_TIMEOUT = 4.0
PUSH_TIMEOUT = 300.0


class TransferError(RuntimeError):
    """A transfer failed for a reason the user can act on."""


class Device:
    """A reachable KOReader, addressed by whatever IP it happens to have."""

    def __init__(self, host: str, token: str, port: int = KINDLE_PORT):
        self.host = host
        self.port = port
        self.token = token
        self.base = "http://%s:%d" % (host, port) if port != 80 else "http://%s" % host
        self._headers = {"X-Auth": token}
        self._post_headers = dict(self._headers,
                                  **{"Content-Type": "application/octet-stream"})

    # -- reads ------------------------------------------------------------
    def list_dir(self, path: str) -> dict[str, Any]:
        url = self.base + RESOURCE_PATH + "/" + path.lstrip("/")
        response = requests.get(url, headers=self._headers, timeout=PROBE_TIMEOUT)
        if response.status_code != 200:
            raise TransferError("无法读取目录 %s（HTTP %s）" % (path, response.status_code))
        return response.json()

    def read_file(self, path: str, timeout: float = PUSH_TIMEOUT) -> bytes:
        response = requests.get(self.base + RAW_PATH + path,
                                headers=self._headers, timeout=timeout)
        if response.status_code != 200:
            raise TransferError("无法读取 %s（HTTP %s）" % (path, response.status_code))
        return response.content

    # -- writes -----------------------------------------------------------
    def write_file(self, path: str, payload: bytes,
                   timeout: float = PUSH_TIMEOUT) -> None:
        url = self.base + RESOURCE_PATH + path + "?override=true"
        response = requests.post(url, data=payload, headers=self._post_headers,
                                 timeout=timeout)
        if response.status_code not in (200, 201):
            raise TransferError("写入 %s 失败（HTTP %s）" % (path, response.status_code))

    def delete(self, path: str) -> None:
        requests.delete(self.base + RESOURCE_PATH + path,
                        headers=self._headers, timeout=PROBE_TIMEOUT)

    def exists(self, path: str) -> bool:
        try:
            self.list_dir(path)
            return True
        except TransferError:
            return False

    def free_space_note(self, book_dir: str) -> str:
        """Best-effort size of the target, so a too-small device is explained."""
        try:
            items = self.list_dir(book_dir).get("items") or []
        except TransferError:
            return ""
        return "%d 个文件" % len(items)


# ---------------------------------------------------------------------------
# discovery
# ---------------------------------------------------------------------------

def _login(host: str, port: int = KINDLE_PORT) -> str | None:
    """Return a session token if this host is a KOReader, else None.

    The probe is GET /api/login rather than a bare socket open: the port is
    confirmed and the identity is confirmed in one round trip, and on the real
    device it needs no credentials.
    """
    base = "http://%s:%d" % (host, port) if port != 80 else "http://%s" % host
    try:
        response = requests.get(base + LOGIN_PATH, timeout=PROBE_TIMEOUT)
    except requests.RequestException:
        return None
    if response.status_code != 200:
        return None
    token = (response.text or "").strip().strip('"')
    if not token or token.count(".") < 2:
        # Not a JWT: some other service on port 80. Do not treat it as a device.
        return None
    return token


def _local_ipv4() -> list[str]:
    """This PC's IPv4 addresses, without requiring a DNS or a subnet guess."""
    found: set[str] = set()
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # Connecting a UDP socket sets the preferred outbound interface without
        # sending anything; 0.0.0.0 routes locally, so there is no traffic.
        probe.connect(("0.0.0.0", 1))
        found.add(probe.getsockname()[0])
    except OSError:
        pass
    finally:
        probe.close()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.add(info[4][0])
    except OSError:
        pass
    return [ip for ip in found if not ip.startswith("127.")]


def candidate_hosts() -> list[str]:
    """Addresses worth probing, nearest first.

    Same-subnet neighbours come first because that is where a phone hotspot puts
    the device; the previously used address is added so a reconnect to a known
    spot resolves instantly.
    """
    hosts: list[str] = []
    remembered = (os.environ.get("WEREAD_KINDLE_HOST") or "").strip()
    if remembered:
        hosts.append(remembered)
    for local in _local_ipv4():
        try:
            network = ipaddress.ip_network(local + "/24", strict=False)
        except ValueError:
            continue
        for candidate in network.hosts():
            hosts.append(str(candidate))
    # Preserve order, drop duplicates.
    seen: set[str] = set()
    ordered: list[str] = []
    for host in hosts:
        if host not in seen:
            seen.add(host)
            ordered.append(host)
    return ordered


def discover(extra: list[str] | None = None,
             progress: Callable[[str], None] | None = None,
             workers: int = 32) -> Device | None:
    """Find a reachable KOReader. Returns None rather than raising.

    Probes run concurrently: a /24 is 254 addresses and at 1.5 s per timeout a
    serial sweep takes minutes, which reads as a hang. Threads are used rather
    than asyncio because this is called from a Tk callback and must stay on
    stdlib-only imports for the frozen bundle.
    """
    hosts = list(extra or []) + candidate_hosts()
    if not hosts:
        return None

    remembered = set(extra or ())
    tokens: dict[str, str] = {}

    def probe(host: str) -> None:
        token = _login(host)
        if token:
            tokens[host] = token

    if len(hosts) <= workers:
        for host in hosts:
            probe(host)
    else:
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="kindle-probe") as pool:
            list(pool.map(probe, hosts))

    for host in hosts:
        if host in tokens:
            if progress:
                progress("已连接 Kindle：%s" % host)
            return Device(host, tokens[host])
    if progress and remembered:
        for host in sorted(remembered):
            progress("上次使用的地址 %s 无响应" % host)
    return None


def discover_with_timeout(seconds: float = 60.0,
                          progress: Callable[[str], None] | None = None,
                          interval: float = 4.0) -> Device | None:
    """Keep probing for a while -- a sleeping Kindle needs a few seconds to wake.

    Remote restart is not possible: the project log records /api/commands
    returning 500 and SSH 22 timing out, so waking the device is the reader's
    job and this only waits for it.
    """
    deadline = time.monotonic() + seconds
    announced = False
    while True:
        device = discover(progress=None)
        if device:
            if progress:
                progress("已连接 Kindle：%s" % device.host)
            return device
        if time.monotonic() >= deadline:
            break
        if progress and not announced:
            progress("正在寻找 Kindle…请确认设备已联网并唤醒 KOReader"
                     "（远程无法开启设备，需要手动唤醒）")
            announced = True
        time.sleep(interval)
    return None


# ---------------------------------------------------------------------------
# pushing a book
# ---------------------------------------------------------------------------

def book_dir(book_id: str) -> str:
    return "%s/%s" % (BOOK_CACHE, str(book_id).strip())


def book_registered(device: Device, book_id: str) -> tuple[bool, str]:
    """Has the plugin been told about this book yet?

    The signal is chapter-cache/<bookId>/catalog.json, not the existence of
    book-cache/<bookId>/: the directory name must equal the bookId for the plugin
    to associate anything, but writing into it creates the directory anyway
    (verified on-device), so a missing folder is not the blocker. What is missing
    until the reader downloads a chapter is the catalogue the plugin uses to
    recognise the book -- which is why the error message says to do that first
    rather than silently producing a file nothing will read.

    Returns (registered, detail).
    """
    catalogue = CHAPTER_CACHE.rstrip("/") + "/" + str(book_id).strip() + "/catalog.json"
    try:
        device.read_file(catalogue, timeout=PROBE_TIMEOUT)
    except TransferError:
        return False, catalogue
    return True, catalogue


def book_dir_ready(device: Device, book_id: str) -> tuple[bool, str]:
    """Whether the destination folder can be written to right now.

    Missing folders are fine -- the device creates intermediate directories -- so
    this only reports what is already there. book_registered() is the check that
    actually gates usefulness.
    """
    target = book_dir(book_id)
    try:
        device.list_dir(target)
    except TransferError:
        return False, target
    return True, target


def push_epub(device: Device, book_id: str, epub: Path,
              progress: Callable[[str], None] | None = None,
              require_registered: bool = True) -> dict[str, Any]:
    """Copy one EPUB into the device's book cache and verify it landed.

    require_registered stays on so a book the plugin has never heard of is
    refused with an actionable message; the physical folder's absence is not a
    reason to refuse.
    """
    if not epub.is_file():
        raise TransferError("找不到要传输的文件：%s" % epub.name)
    payload = epub.read_bytes()
    if len(payload) < 1000:
        raise TransferError("%s 只有 %d 字节，不是有效的 EPUB" % (epub.name, len(payload)))
    digest = hashlib.sha256(payload).hexdigest()

    target = book_dir(book_id)
    if require_registered:
        registered, catalogue = book_registered(device, book_id)
        if not registered:
            raise TransferError(
                "Kindle 还不认识这本书（缺少 %s）。\n\n"
                "请先在 Kindle 上打开「工具 → 微信读书 → 书架」，找到这本书，"
                "下载任意一章。插件会生成目录信息，之后才能把整本传进去。"
                % catalogue)

    name = epub.name
    remote = "%s/%s" % (target, name)
    if progress:
        progress("正在传输 %s（%.1f MB）…" % (name, len(payload) / 1048576.0))
    device.write_file(remote, payload)

    if progress:
        progress("正在校验…")
    landed = device.read_file(remote)
    actual = hashlib.sha256(landed).hexdigest()
    if actual != digest:
        raise TransferError(
            "传输后校验不一致：应为 %s…，实际 %s…（%d / %d 字节）。设备上的文件已不可信，"
            "请重新传输。" % (digest[:12], actual[:12], len(landed), len(payload)))

    return {"path": remote, "bytes": len(payload), "sha256": digest,
            "name": name, "book_id": str(book_id)}


def upload_bundle(device: Device, book_id: str, bundle: Path,
                  progress: Callable[[str], None] | None = None) -> dict[str, Any]:
    """Copy the .weread.zip import bundle, for the plugin's file picker.

    Large (the notes dominate), so it is optional and reported separately.
    """
    if not bundle.is_file():
        raise TransferError("找不到导入包：%s" % bundle.name)
    registered, catalogue = book_registered(device, book_id)
    if not registered:
        raise TransferError(
            "Kindle 还不认识这本书（缺少 %s）。\n\n"
            "请先在 Kindle 上下载任意一章。" % catalogue)
    target = book_dir(book_id)
    payload = bundle.read_bytes()
    remote = "%s/%s" % (target, bundle.name)
    if progress:
        progress("正在传输导入包 %s（%.1f MB）…" % (bundle.name, len(payload) / 1048576.0))
    device.write_file(remote, payload, timeout=900)
    landed = device.read_file(remote, timeout=900)
    if hashlib.sha256(landed).hexdigest() != hashlib.sha256(payload).hexdigest():
        raise TransferError("导入包传输后校验不一致，请重试。")
    return {"path": remote, "bytes": len(payload), "name": bundle.name,
            "book_id": str(book_id)}


def remember_host(host: str) -> None:
    """Best-effort: so the next run probes this address first."""
    try:
        os.environ["WEREAD_KINDLE_HOST"] = host
    except OSError:
        pass


def next_step_message() -> str:
    return ("传输完成。请在 Kindle 上打开「工具 → 微信读书 → 设置 → 缓存管理」，"
            "点「扫描并关联本地书籍」；再到「进度管理」打开拉取/上传，"
            "并启用「阅读时间上报」，微信读书才会记录你的阅读。")