"""Ensure a frozen WeReadWorker contains the same plugin source as the checkout.

Usage:
    python test_worker_package_parity.py <PyInstaller dist directory>

Example:
    python test_worker_package_parity.py release-underline-fix

The stale release-windowed artifact predates the inclusive Source.quote fix; this
check prevents shipping a worker whose embedded Lua silently reintroduces the
one-rune underline offset.
"""
import os
from pathlib import Path
import sys


# The program modules live in weread/ and import each other by bare name.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "weread"))
PROJECT = Path(__file__).resolve().parent.parent
PLUGIN_RELATIVE = Path(
    "kindle_data/03_插件源码与解压文件/weread.koplugin/weread/lib/annotation_source.lua"
)
# PROJECT is the repository root (各类/weread-downloader since the 2026-10 move);
# the Lua plugin lives in the SIBLING Kindle tree: 各类/Kindle/03_插件源码与解压文件.
def _plugin_source() -> Path:
    """WEREAD_KINDLE_ROOT 优先；其次仓库同级的 Kindle 树（2026-10 移动后仍适用）。"""
    roots = []
    env_root = os.environ.get("WEREAD_KINDLE_ROOT")
    if env_root:
        roots.append(Path(env_root))
    roots.append(PROJECT.parent / "Kindle")
    for root in roots:
        candidate = (root / "03_插件源码与解压文件" / "weread.koplugin"
                     / "weread" / "lib" / "annotation_source.lua")
        if candidate.is_file():
            return candidate
    return roots[-1] / "03_插件源码与解压文件" / "weread.koplugin" / "weread" / "lib" / "annotation_source.lua"


SOURCE = _plugin_source()


def check_worker_distribution(dist: Path) -> None:
    # The frozen bundle structure is WeReadWorker/_internal/... not WeReadWorker/WeReadWorker/_internal/...
    bundled = dist / "_internal" / PLUGIN_RELATIVE
    if not SOURCE.is_file():
        raise AssertionError(f"plugin source is missing: {SOURCE}")
    if not bundled.is_file():
        raise AssertionError(f"bundled annotation_source.lua is missing: {bundled}")
    source_bytes = SOURCE.read_bytes()
    bundled_bytes = bundled.read_bytes()
    if source_bytes != bundled_bytes:
        raise AssertionError(
            "WeReadWorker embeds a stale annotation_source.lua; rebuild the worker "
            f"before release (source={len(source_bytes)} bytes, bundle={len(bundled_bytes)} bytes)"
        )
    text = bundled_bytes.decode("utf-8")
    if "for k = pa, pb - 1 do" not in text:
        raise AssertionError("bundled Source.quote does not use the verified inclusive range")
    print(f"PASS worker plugin source parity: {bundled}")


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    check_worker_distribution(Path(sys.argv[1]).resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
