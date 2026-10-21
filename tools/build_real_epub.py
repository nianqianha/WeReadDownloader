#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用真实导出语料生成「统一附录」版 EPUB，可直接在手机/电脑阅读器实测。

用法：
    python build_real_epub.py                    # 默认三字经
    python build_real_epub.py 后汉书              # 按书名模糊匹配
    python build_real_epub.py "后汉书全本全注全译（全十卷）-3300228183"  # 精确目录名

流程：读 exports/<书>/.work 的章节正文缓存 + reviews.jsonl → build_appendix 改写
→ 用主引擎 write_epub 打包（与真实导出的 EPUB 结构一致）。
"""
import importlib.util
import json
import re
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJ = HERE / "weread-downloader"
sys.path.insert(0, str(PROJ / "weread"))

def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod

ua = _load("unified_appendix", PROJ / "weread" / "unified_appendix.py")

EXPORTS = Path.home() / "AppData" / "Local" / "WeReadDesktop" / "exports"

def pick_book(pattern: str) -> Path:
    candidates = [p for p in EXPORTS.iterdir() if p.is_dir() and not p.name.endswith(".work")]
    for p in candidates:
        if p.name == pattern:
            return p
    for p in candidates:
        if pattern in p.name:
            return p
    raise SystemExit("找不到书：%s\n可选：\n  %s" % (pattern, "\n  ".join(p.name for p in candidates)))

def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    limit = 0
    for a in sys.argv[1:]:
        if a.startswith("--chapters="):
            limit = int(a.split("=", 1)[1])
    pattern = args[0] if args else "三字经（公版）"
    EXP = pick_book(pattern)
    WORK = EXP.with_name(EXP.name + ".work")
    bodies = sorted(WORK.glob("chapter-uid-*.body"),
                    key=lambda p: int(p.stem.rsplit("-", 1)[1]) if p.stem.rsplit("-", 1)[1].isdigit() else 0)
    if not bodies:
        raise SystemExit("没有章节缓存：%s" % WORK)

    # 章名来源：原 EPUB 的 nav.xhtml（.json 里没有 title，body 里只有 h1 卷号）。
    # nav 顺序与 .work 的 body（按 uid 排序）严格一致，用第 i 个标题配第 i 个 body。
    src_epub_for_nav = next(EXP.glob("*.epub"), None)
    nav_titles: list[str] = []
    if src_epub_for_nav:
        with zipfile.ZipFile(src_epub_for_nav) as nz:
            try:
                nav_html = nz.read("OEBPS/nav.xhtml").decode("utf-8")
            except KeyError:
                nav_html = ""
        nav_titles = [re.sub(r"<[^>]+>", "", x).strip()
                      for x in re.findall(
                          r'<a href="text/chapter-\d+\.xhtml"[^>]*>(.*?)</a>',
                          nav_html, re.S)]

    # manifest.json 的 chapterUid→title 是最权威映射（缺章也不会错位）
    manifest_map: dict[str, str] = {}
    mf = EXP / "manifest.json"
    if mf.exists():
        try:
            for c in json.loads(mf.read_text(encoding="utf-8")).get("chapters", []):
                manifest_map[str(c.get("chapterUid"))] = c.get("title") or ""
        except Exception:
            pass

    def title_of(idx: int, uid: str, b: Path) -> str:
        if manifest_map.get(uid):
            return manifest_map[uid]
        if idx < len(nav_titles) and nav_titles[idx]:
            return nav_titles[idx]
        body_text = b.read_text(encoding="utf-8", errors="replace")
        m = re.search(r"<h[12][^>]*>(.*?)</h[12]>", body_text, re.S)
        if m:
            name = re.sub(r"<[^>]+>", "", m.group(1)).strip()
            if name:
                return name
        return b.stem

    chapter_paths = [(b.stem.replace("chapter-uid-", ""),
                      title_of(i, b.stem.replace("chapter-uid-", ""), b), b)
                     for i, b in enumerate(bodies)]
    if limit:
        chapter_paths = chapter_paths[:limit]
    print("书：%s（%d 章）" % (EXP.name, len(chapter_paths)))

    files, rewritten, manifest = ua.build_appendix(chapter_paths, EXP / "reviews.jsonl")
    m = manifest["appendix"]
    print("注 %d / 想法 %d / 注分册 %d / 附录文件 %d"
          % (m["notes"], m["thoughts"], m["notes_parts"], len(files)))

    # 改写后的正文写到临时目录（不覆写 .work 缓存）
    tmp = Path(tempfile.mkdtemp(prefix="appendix-bodies-"))
    uid_to_path = {}
    for uid, text in rewritten:
        p = tmp / ("chapter-%s.body" % uid)
        p.write_text(text, encoding="utf-8")
        uid_to_path[uid] = p
    ordered = [uid_to_path[uid] for uid, _t, _p in chapter_paths]

    # 用主引擎 write_epub 打包，结构与真实导出一致
    wd = _load("weread_desktop", PROJ / "weread" / "weread_desktop.py")
    css_file = next(iter(WORK.glob("chapter-uid-*.css")), None)
    base_css = css_file.read_text(encoding="utf-8") if css_file else ""
    css = base_css + "\n" + ua.APPENDIX_CSS
    assets = Path(tempfile.mkdtemp(prefix="appendix-assets-"))
    cover = None
    # 原书图片必须一并打包：章节正文引用 ../images/<name>，缺图会让阅读器显示
    # 破图、Calibre 转换直接失败。
    src_epub = next(EXP.glob("*.epub"), None)
    if src_epub:
        with zipfile.ZipFile(src_epub) as sz:
            for n in sz.namelist():
                if n.startswith("OEBPS/images/") and not n.endswith("/"):
                    data = sz.read(n)
                    (assets / n.rsplit("/", 1)[-1]).write_bytes(data)
        imgs = list(assets.glob("*"))
        print("已带图片 %d 张" % len(imgs))
        cover = next((imgs_i for imgs_i in imgs if "cover" in imgs_i.name.lower()), None)

    out_dir = HERE / "测试产物"
    out_dir.mkdir(exist_ok=True)
    stem = EXP.name.split("-")[0]
    out = out_dir / ("%s-统一附录-v2%s.epub" % (stem, ("-前%d章" % limit) if limit else ""))

    book = {"bookId": EXP.name.rsplit("-", 1)[-1], "title": stem, "author": ""}
    chapters = [{"chapterUid": uid, "title": title} for uid, title, _p in chapter_paths]
    wd.write_epub(out, book, chapters, ordered, css, assets, cover,
                  (files, {uid: uid_to_path[uid] for uid, _t, _p in chapter_paths}))

    print("输出：%s（%.1f KB）" % (out, out.stat().st_size / 1024))

    # 结构校验
    with zipfile.ZipFile(out) as z:
        names = set(z.namelist())
        opf = z.read("OEBPS/content.opf").decode("utf-8")
        declared = set(__import__("re").findall(r'href="(text/[^"]+)"', opf))
        actual = {n.replace("OEBPS/", "") for n in names if n.startswith("OEBPS/text/")}
        print("manifest 条目 == 实际文件：", declared == actual)
        if declared != actual:
            print("  差异：", declared ^ actual)
        note_ids = set()
        note_badges = set()
        for n in names:
            if n.startswith("OEBPS/text/appendix"):
                note_ids |= set(__import__("re").findall(r'id="(note-[^"]+)"', z.read(n).decode("utf-8")))
            elif n.startswith("OEBPS/text/chapter-"):
                note_badges |= set(__import__("re").findall(r'href="[^"]*#(note-[^"]+)"', z.read(n).decode("utf-8")))
        print("正文注角标 %d ↔ 附录注条目 %d：%s"
              % (len(note_badges), len(note_ids), "一致" if note_badges == note_ids else "不一致"))
    return 0

if __name__ == "__main__":
    sys.exit(main())
