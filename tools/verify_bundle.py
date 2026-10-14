# -*- coding: utf-8 -*-
"""Verify a built bundle actually contains what the program imports.

WHY THIS EXISTS
    first_run.py was absent from every bundle built before 2026-10-14: the GUI
    imports it through a try/except pair whose relative branch cannot resolve
    (weread/ has no __init__.py), so PyInstaller's static analysis never saw it.
    The build succeeded, the exe ran, and the wizard was missing at the exact
    moment a user needed it. A build that cannot report a missing module ships
    the bug, so this runs against the finished dist and fails loudly.

HOW IT READS THE BUNDLE
    Not by searching the exe for marker strings. PyInstaller packs pure modules
    into a zlib-compressed PYZ, so module bodies are invisible to a byte scan and
    a present module reads as missing -- a false negative that would train the
    reader to ignore this check. It uses PyInstaller's own archive readers, which
    is the authoritative answer to "what is in this bundle".

Usage:
    python tools/verify_bundle.py release-x/WeReadDesktop [more dist dirs...]
Exit 0 when every required module is present, 1 otherwise.
"""
import os
import sys
from pathlib import Path

# Modules each bundle must carry, by the name they are imported under.
#
# The distinction that matters: a bundle's ENTRY script is packed into the
# CArchive, not the PYZ, so it is looked for in both places. Modules reached
# only through the entry script's imports live in the PYZ.
#
# weread_core is deliberately absent from WeReadWorker: neither the worker nor
# the weread_desktop.py it loads by path imports it, so requiring it there would
# report a healthy bundle as broken.
REQUIRED = {
    "WeReadDesktop": ["desktop_app", "first_run", "weread_core", "weread_login"],
    "WeReadWeb": ["weread_web", "weread_core"],
    "WeReadWorker": ["weread_worker", "weread_desktop", "weread_login"],
}


def _pyz_modules(exe: Path):
    """Names inside the PYZ embedded in a frozen exe."""
    from PyInstaller.archive.readers import CArchiveReader, ZlibArchiveReader
    import tempfile

    car = CArchiveReader(str(exe))
    member = next((n for n in car.toc if n.lower().endswith(".pyz")), None)
    if member is None:
        return set(), set()
    # the CArchive toc also names the entry script and the bootloader modules;
    # an entry script is packed there rather than in the PYZ, so it must be
    # accepted as present
    car_names = {n.split("/")[-1] for n in car.toc}
    data = car.extract(member)
    tmp = Path(tempfile.gettempdir()) / ("verify_%s.pyz" % os.getpid())
    tmp.write_bytes(data)
    try:
        return set(ZlibArchiveReader(str(tmp)).toc), car_names
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def _collected_modules(dist: Path):
    """Bare module names present as real .py files beside the bundle."""
    names = set()
    for base in (dist, dist / "_internal"):
        if not base.is_dir():
            continue
        for hit in base.rglob("*.py"):
            names.add(hit.stem)
    return names


def verify(dist: Path) -> tuple:
    """(missing, where_found) for this bundle."""
    name = dist.name
    required = REQUIRED.get(name)
    if required is None:
        return None, {}

    exe = dist / (name + ".exe")
    in_pyz, in_car = set(), set()
    if exe.is_file():
        try:
            in_pyz, in_car = _pyz_modules(exe)
        except Exception as exc:
            print("   (could not read the PYZ: %s: %s)" % (type(exc).__name__, exc))
    on_disk = _collected_modules(dist)

    found, missing = {}, []
    for module in required:
        if module in in_pyz:
            found[module] = "PYZ"
        elif module in in_car:
            found[module] = "CArchive (entry)"
        elif module in on_disk:
            found[module] = "collected .py"
        else:
            missing.append(module)
    return missing, found


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    failures = 0
    for target in argv[1:]:
        dist = Path(target).resolve()
        if not dist.is_dir():
            print("SKIP  %s (not a directory)" % target)
            continue
        missing, found = verify(dist)
        if missing is None:
            print("SKIP  %s (no manifest for this bundle name)" % dist.name)
            continue
        print("=== %s ===" % dist.name)
        for module in REQUIRED[dist.name]:
            print("   %-7s %-18s %s"
                  % ("OK" if module in found else "MISSING", module,
                     found.get(module, "-- not in bundle --")))
        if missing:
            failures += 1
        print()

    if failures:
        print("FAILED: %d bundle(s) missing a required module." % failures)
        print("A missing module means the build succeeded but the program fails")
        print("at the point that imports it. For a directory that is not a")
        print("package, the .spec needs an explicit hiddenimports entry and")
        print("pathex must name that directory.")
        return 1
    print("PASS: every required module is present in every bundle checked.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))