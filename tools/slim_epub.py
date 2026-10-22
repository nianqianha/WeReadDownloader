#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Slim an already-exported EPUB by removing byte-identical images.

Why this exists: before 2026-10-22 the packer wrote every image entry it was
handed, and the same picture frequently arrived twice (once from the chapter
image archive, once from a remote URL), under two different names. Measured on
汉书 3300185594: 1569 image entries, 686 unique, 94 MB of duplicates inside a
198 MB EPUB.

New exports are deduplicated while packing; this tool fixes books that were
already built. It rewrites the image references in the XHTML to the file that is
kept, validates that every reference still resolves, and only then replaces the
original (atomically, through a temp file in the same directory).

    python tools/slim_epub.py "C:\\...\\书名 - full.epub" [--dry-run]

Exit codes: 0 when the file was slimmed (or already slim), 1 on error.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import zipfile
from pathlib import Path

IMAGE_EXT = (".jpg", ".jpeg", ".png", ".gif", ".webp")
REF = re.compile(rb'\.\./images/([^"\' >]+)')


def _is_image(name: str) -> bool:
    return name.lower().endswith(IMAGE_EXT)


def slim(epub: Path, dry_run: bool = False) -> int:
    before = epub.stat().st_size
    with zipfile.ZipFile(epub) as source:
        infos = [info for info in source.infolist() if not info.is_dir()]
        keep: dict[str, str] = {}          # duplicate basename -> kept basename
        owner_by_crc: dict[int, str] = {}  # crc -> kept basename
        for info in infos:
            if not _is_image(info.filename):
                continue
            base = Path(info.filename).name
            owner = owner_by_crc.get(info.CRC)
            if owner is None:
                owner_by_crc[info.CRC] = base
            elif owner != base:
                keep[base] = owner
        if not keep:
            print("没有重复图片，%s 保持原样（%.1f MB）" % (epub.name, before / 1048576))
            return 0
        keep_bytes = {name.encode("utf-8"): owner.encode("utf-8")
                      for name, owner in keep.items()}

        def rewrite(data: bytes) -> bytes:
            def replaced(match: re.Match[bytes]) -> bytes:
                target = keep_bytes.get(match.group(1))
                return b"../images/" + target if target else match.group(0)
            return REF.sub(replaced, data)

        temp = epub.with_suffix(epub.suffix + ".slim.part")
        with zipfile.ZipFile(temp, "w") as target:
            for info in infos:
                name = info.filename
                if _is_image(name) and Path(name).name in keep:
                    continue  # duplicate of an entry written under another name
                data = source.read(name)
                if name.lower().endswith((".xhtml", ".html", ".htm")):
                    data = rewrite(data)
                    target.writestr(name, data, compress_type=zipfile.ZIP_DEFLATED,
                                    compresslevel=1)
                elif name == "mimetype":
                    target.writestr(name, data, compress_type=zipfile.ZIP_STORED)
                elif _is_image(name):
                    target.writestr(name, data, compress_type=zipfile.ZIP_STORED)
                else:
                    target.writestr(name, data, compress_type=info.compress_type)

    # Validate before touching the original: every ../images/ reference must
    # resolve, and the archive must be readable end to end.
    with zipfile.ZipFile(temp) as check:
        if check.testzip() is not None:
            temp.unlink(missing_ok=True)
            raise RuntimeError("新文件自检失败（zip 损坏），原文件未改动")
        entries = set(check.namelist())
        missing = []
        for name in entries:
            if not name.lower().endswith((".xhtml", ".html", ".htm")):
                continue
            for match in REF.finditer(check.read(name)):
                ref = match.group(1).decode("utf-8", "replace")
                if name.startswith("OEBPS/") and ("OEBPS/images/" + ref) not in entries:
                    missing.append("%s -> %s" % (name, ref))
        if missing:
            temp.unlink(missing_ok=True)
            raise RuntimeError("改写后仍有 %d 处图片引用找不到目标，原文件未改动：%s"
                               % (len(missing), "; ".join(missing[:3])))

    after = temp.stat().st_size
    print("图片条目重复 %d 个：%.1f MB → %.1f MB（省 %.1f MB）"
          % (len(keep), before / 1048576, after / 1048576,
             (before - after) / 1048576))
    if dry_run:
        temp.unlink(missing_ok=True)
        print("--dry-run：只计算，不替换原文件")
        return 0
    os.replace(temp, epub)
    print("已替换：%s" % epub)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("epub", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if not args.epub.is_file():
        print("找不到文件：%s" % args.epub, file=sys.stderr)
        return 1
    try:
        return slim(args.epub, args.dry_run)
    except Exception as exc:
        print("失败：%s: %s" % (type(exc).__name__, exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
