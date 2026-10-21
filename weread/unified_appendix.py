# -*- coding: utf-8 -*-
"""统一附录（注释和想法）：把一本书的「注」与「想法」收进书末一个独立章节。

产品规则（WD 技术文档 §1.4）：
  R1 角标紧跟对应位置；R2 想法全文内嵌不截断；
  R3 注与想法都进书末附录「注释和想法」，按章分节（每章先注、后想法）；
  R6 角标数必须等于附录条目数，不一致即构建失败。

数据流（不改下载链路、不改断点结构）：
  输入  reviews.jsonl          想法（reviews[].pageReviews[]，含 author/content/likes）
        chapter-uid-*.body     正文；注是 <img class="qqreader-footnote" alt="注文"/>
                               （无 src 的死图，阅读器显示不出来，必须转）
  输出  text/appendix.xhtml    单个附录文件（W12：阅读器对 XHTML 文件数敏感，
                               单一附录文件最稳，不做一想法一文件分册）

实现约束（2026-10-16~19 实测结论，违反即坏）：
  1. aside 必须正常渲染——Calibre 点击靠「藏掉其他、只留脚注」工作，
     display:none/位移/opacity:0 都会让它取出空内容；
  2. 角标须 epub:type="noteref" + role="doc-noteref" 双属性，
     根元素须声明 xmlns:epub，否则 Calibre 判定失败退化成跳页；
  3. 角标 id 与附录锚点 id 用不同前缀（wrbadge-/notebadge- vs wrfn-/note-）；
  4. 注文在 img 的 alt 里，是 HTML 字符引用（&#x82E5; 之类），必须 unescape；
  5. manifest 条目数必须与 zip 内实际文件数一致，否则阅读器卡封面。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from html import unescape
from pathlib import Path
from typing import Any

from lxml import etree

XHTML = "http://www.w3.org/1999/xhtml"
EPUBNS = "http://www.idpf.org/2007/ops"

APPENDIX_TITLE = "注释和想法"
APPENDIX_FILENAME = "appendix.xhtml"

# 注角标：<img class="qqreader-footnote" alt="注文"/>（无 src 的死图）
_FOOTNOTE_IMG_RE = re.compile(
    r'<img\b(?=[^>]*\bclass="[^"]*\bqqreader-footnote\b[^"]*")[^>]*?/?>', re.I)
# 下载阶段注入的划线链接：#wrthought-<bookId>-<chapterUid>-<start>-<end>
_THOUGHT_HREF_RE = re.compile(r"#wrthought-(\d+)-(\d+)-(\d+-\d+)")
# WeRead 原生想法计数角标（旧格式，转成统一角标时移除）
_THOUGHT_COUNT_RE = re.compile(r"<span class=\"wr-thought-count\">[^<]*</span>")

APPENDIX_CSS = """\
.wr-ap-h1{margin:0 0 .6em 0;font-size:1.3em;}
.wr-ap-sec{margin:1.6em 0 .5em 0;font-size:1.05em;font-weight:bold;color:#222;
  border-bottom:1px solid #bbb;padding-bottom:.25em;}
.wr-ap-item{margin:0 0 .3em 0;text-indent:0;font-size:.88em;}
.wr-ap-back{font-weight:bold;margin-right:.3em;text-decoration:none;color:#0366d6;}
.wr-ap-quote{font-weight:bold;color:#333;}
.wr-ap-author{color:#222;font-weight:bold;}
.wr-ap-text{color:#333;}
.wr-ap-likes{color:#888;font-size:.92em;white-space:nowrap;}
.wr-ap-none{color:#888;font-style:italic;}
.wr-ap-sub{margin:.9em 0 .4em 0;font-size:.95em;font-weight:bold;color:#555;}
.wr-ap-note{color:#333;}
/* 正文角标（想法）：上标、蓝色、可点 */
.wr-fn-ref{display:inline;font-size:.72em;vertical-align:super;line-height:1;
  white-space:nowrap;margin-left:.05em;}
.wr-fn-ref a{text-decoration:none;color:#0366d6;}
/* 正文注角标：橙色实心圆「注」（对齐微信读书视觉） */
.wr-fn-mark{display:inline-block;background:#f06a1e;color:#fff;font-size:.58em;
  line-height:1.5;border-radius:50%;padding:0 .26em;margin:0 .12em;
  vertical-align:super;text-align:center;text-decoration:none;font-weight:600;}
"""


class AppendixError(RuntimeError):
    """附录构建失败（数据不一致等硬错误）。调用方必须让它冒泡，不产残缺 EPUB。"""


@dataclass
class ThoughtItem:
    author: str
    content: str
    likes: int = 0


@dataclass
class ThoughtEntry:
    chapter_uid: str
    range_key: str
    quote: str
    items: list[ThoughtItem] = field(default_factory=list)


def q(tag: str) -> str:
    return "{%s}%s" % (XHTML, tag)


def load_reviews(path: Path) -> dict[str, dict[str, ThoughtEntry]]:
    """读 reviews.jsonl，按 (chapter_uid, range) 组织想法。

    返回 {chapter_uid: {range: ThoughtEntry}}。reviews[].pageReviews[] 为空或
    无正文的条目跳过——那是「只有划线没有想法」的组，不该出现在附录里。

    同一个 range 可能出现多组（WeRead 的分页把一条划线的想法拆成多个 review
    组，三字经实测有 2 组同 range）——**合并 items**而不是后者覆盖前者，
    否则会静默丢掉一整组想法。
    """
    rows: dict[str, dict[str, ThoughtEntry]] = {}
    with Path(path).open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            uid = str(row.get("chapter_uid") or row.get("chapterUid") or "")
            if not uid:
                continue
            bucket = rows.setdefault(uid, {})
            for rv in row.get("reviews") or []:
                range_key = str(rv.get("range") or "")
                if not range_key:
                    continue
                items: list[ThoughtItem] = []
                for pr in rv.get("pageReviews") or []:
                    review = pr.get("review") or {}
                    author = review.get("author") or {}
                    content = str(review.get("content") or "").strip()
                    if not content:
                        continue
                    items.append(ThoughtItem(
                        author=str(author.get("nick") or author.get("name") or "匿名"),
                        content=content,
                        likes=int(pr.get("likesCount") or 0),
                    ))
                if not items:
                    continue
                quote = str(rv.get("abstract") or rv.get("contextAbstract") or "").strip()
                existing = bucket.get(range_key)
                if existing is None:
                    bucket[range_key] = ThoughtEntry(
                        chapter_uid=uid, range_key=range_key,
                        quote=quote, items=items)
                else:
                    # 同一 range 的分页续组：合并想法，quote 已有就不再覆盖
                    existing.items.extend(items)
                    if quote and not existing.quote:
                        existing.quote = quote
                    items = existing.items
                items.sort(key=lambda i: i.likes, reverse=True)
    return rows


def rewrite_badges(xhtml: str, chapter_uid: str,
                   known: dict[str, "ThoughtEntry"],
                   thoughts_file: str) -> tuple[str, int, list[str]]:
    """给正文划线追加可点角标；返回 (新正文, 角标数, 未覆盖的 range)。

    参考旧产物的做法（实测可用）：
    - 划线链接 `<a class="wr-thought-link" href="#wrthought-…">…</a>` **保持原样**；
    - 在它**后面插入** `<span class="wr-fn-ref"><a epub:type="noteref"
      role="doc-noteref" href="<thoughts_file>#wrfn-<uid>-<range>"
      id="wrbadge-<uid>-<range>">[N]</a></span>`，N = 该划线下的想法条数；
    - 删除划线链接内部的 WeRead 原生 `<span class="wr-thought-count">[N]</span>`
      （位置常在链接后、与我们的角标重复）；
    - known 之外（有划线链接但 reviews 无想法组）保留原样并计入 missing。

    href 必须带 `thoughts_file` 前缀——想法在附录文件里，只写 `#wrfn-…`
    是同文件锚点，点击会"找不到目标"。
    """
    missing: list[str] = []
    placed: set[str] = set()
    # 整个 <a>…</a>；靠 lookahead 认 class 和 href，不依赖属性顺序
    _ANCHOR_FULL_RE = re.compile(
        r'<a\b(?=[^>]*\bclass="[^"]*\bwr-thought-link\b[^"]*")'
        r'(?=[^>]*\bhref="#wrthought-\d+-(?P<uid>\d+)-(?P<range>\d+-\d+)")'
        r'[^>]*>.*?</a>', re.S)

    def _sub(m: re.Match[str]) -> str:
        uid, range_key = m.group("uid"), m.group("range")
        if uid != chapter_uid:
            return m.group(0)
        entry = known.get(range_key)
        if entry is None:
            missing.append(range_key)
            return m.group(0)
        anchor = _THOUGHT_COUNT_RE.sub("", m.group(0))
        # 划线原文：reviews 组里常没有 abstract，从 wr-underline 文本补，
        # 让弹窗能显示「这条想法对应哪段话」。
        if not entry.quote:
            um = re.search(r'<span class="wr-underline">(.*?)</span>', anchor, re.S)
            if um:
                entry.quote = re.sub(r"<[^>]+>", "", um.group(1)).strip()
        placed.add(range_key)
        badge = ('<span class="wr-fn-ref"><a epub:type="noteref" role="doc-noteref" '
                 'href="%s#wrfn-%s-%s" id="wrbadge-%s-%s">[%d]</a></span>'
                 % (thoughts_file, uid, range_key, uid, range_key,
                    max(1, len(entry.items))))
        return anchor + badge

    rewritten = _ANCHOR_FULL_RE.sub(_sub, xhtml)
    # 链接外的原生计数徽章（旧格式）也清掉，避免与角标重复
    rewritten = _THOUGHT_COUNT_RE.sub("", rewritten)
    # 按 range 去重：同一 range 在正文可能出现多个划线链接（各自插角标），
    # 但附录里只有一条条目，计数必须按 range 算。
    return rewritten, placed, missing


def rewrite_notes(xhtml: str, chapter_uid: str,
                  notes_file: str) -> tuple[str, list[tuple[str, str, str]]]:
    """把正文里的死图注角标转成可点角标；返回 (新正文, [(锚点id, 回链id, 注文)])。

    下载器输出的注是 `<img class="qqreader-footnote" alt="注文"/>`——无 src
    的死图，阅读器显示不出来（这是 W13 的根因）。注文在 alt 里、且是 HTML
    字符引用（&#x82E5; 之类），必须 unescape。

    转换后（与旧版参考实现同一套样式）：
    `<span class="wr-fn-ref"><a epub:type="noteref" role="doc-noteref"
    href="<notes_file>#note-<uid>-<n>" id="notebadge-<uid>-<n>">注</a></span>`

    href 必须带分册文件名——注在附录文件里，只写 `#note-…` 是**同文件锚点**，
    点击会"找不到目标"（实测过的坑）。
    """
    notes: list[tuple[str, str, str]] = []
    seq = [0]

    def _sub(m: re.Match[str]) -> str:
        tag = m.group(0)
        alt = re.search(r'\balt="([^"]*)"', tag, re.I)
        title = re.search(r'\btitle="([^"]*)"', tag, re.I)
        raw = (alt.group(1) if alt else "") or (title.group(1) if title else "")
        text = unescape(raw).replace("\r", " ").replace("\n", " ").strip()
        if not text:
            # 没注文的空角标（alt 缺失）：整段删掉，不留死图
            return ""
        seq[0] += 1
        n = seq[0]
        target = "note-%s-%d" % (chapter_uid, n)
        badge = "notebadge-%s-%d" % (chapter_uid, n)
        notes.append((target, badge, text))
        return ('<a class="wr-fn-mark" epub:type="noteref" role="doc-noteref" '
                'href="%s#%s" id="%s">注</a>'
                % (notes_file, target, badge))

    rewritten = _FOOTNOTE_IMG_RE.sub(_sub, xhtml)
    return rewritten, notes


MAX_NOTES_PER_PART = 3000


def _html_shell(page_title: str, heading: str, intro: str | None):
    root = etree.Element(q("html"), nsmap={None: XHTML, "epub": EPUBNS})
    head = etree.SubElement(root, q("head"))
    t = etree.SubElement(head, q("title"))
    t.text = page_title
    link = etree.SubElement(head, q("link"))
    link.set("rel", "stylesheet")
    link.set("href", "style.css")
    link.set("type", "text/css")
    body = etree.SubElement(root, q("body"))
    h1 = etree.SubElement(body, q("h1"))
    h1.set("class", "wr-ap-h1")
    h1.text = heading
    if intro:
        p = etree.SubElement(body, q("p"))
        p.text = intro
    return root, body


def _dump(root) -> bytes:
    return etree.tostring(root, xml_declaration=True, encoding="utf-8")


def _add_note(body, target_id: str, badge_id: str, text: str,
              chapter_file: str) -> None:
    aside = etree.SubElement(body, q("aside"))
    aside.set("{%s}type" % EPUBNS, "footnote")
    aside.set("id", target_id)
    aside.set("class", "wr-book-footnote")
    p = etree.SubElement(aside, q("p"))
    p.set("class", "wr-ap-item")
    back = etree.SubElement(p, q("a"))
    back.set("href", "%s#%s" % (chapter_file, badge_id))
    back.set("class", "wr-ap-back")
    back.text = "注"
    sp = etree.SubElement(p, q("span"))
    sp.set("class", "wr-ap-note")
    sp.text = " " + text


def _add_thought(body, entry, is_orphan: bool, chapter_file: str) -> None:
    target_id = "wrfn-%s-%s" % (entry.chapter_uid, entry.range_key)
    ref_id = "wrbadge-%s-%s" % (entry.chapter_uid, entry.range_key)
    aside = etree.SubElement(body, q("aside"))
    aside.set("{%s}type" % EPUBNS, "footnote")
    aside.set("id", target_id)
    aside.set("class", "wr-book-footnote")
    p = etree.SubElement(aside, q("p"))
    p.set("class", "wr-ap-item")
    if is_orphan:
        # 正文没有这条划线的角标（注入失败）：不放假回链，改用纯文本编号
        num = etree.SubElement(p, q("span"))
        num.set("class", "wr-ap-back")
        num.text = "[%d]" % len(entry.items)
    else:
        back = etree.SubElement(p, q("a"))
        back.set("href", "%s#%s" % (chapter_file, ref_id))
        back.set("class", "wr-ap-back")
        back.text = "[%d]" % len(entry.items)
    if entry.quote:
        quote = etree.SubElement(p, q("span"))
        quote.set("class", "wr-ap-quote")
        quote.text = " 「%s」" % entry.quote
    for item in entry.items:
        ip = etree.SubElement(aside, q("p"))
        ip.set("class", "wr-ap-item")
        au = etree.SubElement(ip, q("b"))
        au.set("class", "wr-ap-author")
        au.text = item.author
        tx = etree.SubElement(ip, q("span"))
        tx.set("class", "wr-ap-text")
        tx.text = " " + item.content
        if item.likes:
            lk = etree.SubElement(ip, q("span"))
            lk.set("class", "wr-ap-likes")
            lk.text = " （%d赞）" % item.likes


def build_notes_part(chapters, part_no: int, total_parts: int) -> bytes:
    """生成一册「注释」。chapters = [(章标题, 章文件名, 注条目)]。"""
    suffix = "" if total_parts <= 1 else "（%d/%d）" % (part_no, total_parts)
    heading = APPENDIX_TITLE + " · 注释" + suffix
    root, body = _html_shell(
        heading, heading,
        "正文中 [注n] 角标对应此处条目，点编号可跳回原文。" if part_no == 1 else None)
    for title, ch_file, notes in chapters:
        sec = etree.SubElement(body, q("h2"))
        sec.set("class", "wr-ap-sec")
        sec.text = title
        for target_id, badge_id, text in notes:
            _add_note(body, target_id, badge_id, text, ch_file)
    return _dump(root)


def build_thoughts_part(sections, orphan_keys: set[str]) -> bytes:
    """生成「想法」册。sections = [(章标题, 章文件名, 注条目, 想法条目)]。"""
    heading = APPENDIX_TITLE + " · 想法"
    root, body = _html_shell(
        heading, heading,
        "正文中 [N] 角标对应此处条目，点编号可跳回原文。")
    for title, ch_file, notes, entries in sections:
        if not entries:
            continue
        sec = etree.SubElement(body, q("h2"))
        sec.set("class", "wr-ap-sec")
        sec.text = title
        for entry in entries:
            is_orphan = "%s:%s" % (entry.chapter_uid, entry.range_key) in orphan_keys
            _add_thought(body, entry, is_orphan, ch_file)
    return _dump(root)


def build_appendix(chapter_paths: list[tuple[str, str, Path]],
                   reviews_path: Path) -> tuple[bytes, list[tuple[str, str]], dict[str, Any]]:
    """统一附录入口。

    chapter_paths: [(chapter_uid, 章标题, .body 缓存路径)]，顺序即阅读顺序。
    返回 (附录 XHTML 字节, [(chapter_uid, 改写后正文)], manifest 追加信息)。

    缓存是断点续传的事实源，**不被覆写**；改写后的正文只随本次打包使用，
    保证「关开关后导出物与当前版本一致」的回归要求。

    硬断言只针对**指向附录的角标**：每个被改写的角标都必须在附录里有条目，
    否则抛 AppendixError——不允许产出「角标点开是空的」的 EPUB。另两种缺口
    如实上报、不失败：
    - **有条目无角标**（想法在、划线没画上——WeRead 服务端引文匹配失败）→
      条目保留进附录（数据不丢），记入 manifest 的 orphan_entries；
    - **有链接无想法组**（只划线没写想法，WeRead 常态）→ 链接保留原样、
      不入附录，记入 manifest 的 unlinked_underlines。
    """
    reviews = load_reviews(reviews_path)
    # —— 第一遍：读正文、数每章注数（用于分册分配）——
    bodies: list[tuple[str, str, Path, str, int]] = []
    for uid, chapter_title, body_path in chapter_paths:
        xhtml = Path(body_path).read_text(encoding="utf-8")
        bodies.append((uid, chapter_title, body_path, xhtml,
                       len(_FOOTNOTE_IMG_RE.findall(xhtml))))

    # 注按条目数装箱（单文件 12 MB 的注附录在手机阅读器上会卡，后汉书实测）；
    # 单册约 3000 条 / 1 MB。
    part_of: dict[str, int] = {}
    part_no = 1
    acc = 0
    for uid, _t, _p, _x, nc in bodies:
        if acc >= MAX_NOTES_PER_PART:
            part_no += 1
            acc = 0
        part_of[uid] = part_no
        acc += nc
    total_parts = part_no if any(nc for _u, _t, _p, _x, nc in bodies) else 0
    notes_file_of = {uid: "appendix-notes-%02d.xhtml" % p
                     for uid, p in part_of.items()}

    # —— 第二遍：改写正文（注角标指向对应分册，想法角标指向想法册）——
    sections: list[tuple[str, str, list[tuple[str, str, str]], list[ThoughtEntry]]] = []
    rewritten_bodies: list[tuple[str, str]] = []
    badge_total = 0
    note_total = 0
    unresolved: list[str] = []
    orphans: list[str] = []

    for ch_idx, (uid, chapter_title, _body_path, xhtml, _nc) in enumerate(bodies, 1):
        known = reviews.get(uid, {})
        rewritten, notes = rewrite_notes(xhtml, uid, notes_file_of[uid])
        note_total += len(notes)
        rewritten, badges, missing = rewrite_badges(
            rewritten, uid, known, "appendix-thoughts.xhtml")
        rewritten_bodies.append((uid, rewritten))
        badge_total += len(badges)
        unresolved.extend("%s:%s" % (uid, r) for r in missing)
        entries = list(known.values())
        # 有想法但正文没有对应角标：划线注入失败（插件拒绝画错位置）。
        # 想法本身有效，保留进附录；只上报，不当成构建失败。
        orphans.extend("%s:%s" % (uid, r) for r in set(known) - badges)
        # 章文件名与 write_epub 的命名一致（chapter-0001.xhtml…），供附录回链使用
        sections.append((chapter_title, "chapter-%04d.xhtml" % ch_idx, notes, entries))

    entry_total = sum(len(e) for _, _cf, _n, e in sections)
    # R6：被改写、指向附录的想法角标必须在附录里都有落点。未覆盖的 range
    # （有划线链接但 reviews 无对应想法组——只划线没写想法，WeRead 常态）
    # 保留原样、不上附录，只如实上报。
    if badge_total != entry_total - len(orphans):
        raise AppendixError(
            "统一附录数据不一致：指向附录的想法角标 %d 个，想法条目 %d 个"
            "（其中 %d 个无正文角标，%d 个划线无想法组未入附录）"
            % (badge_total, entry_total, len(orphans), len(unresolved)))

    # —— 组装分册文件 ——
    chunks: dict[int, list[tuple[str, list]]] = {}
    for (title, ch_file, notes, _entries), (uid, _t, _p, _x, _nc) in zip(sections, bodies):
        chunks.setdefault(part_of[uid], []).append((title, ch_file, notes))
    files: list[tuple[str, bytes]] = []
    for pno in sorted(chunks):
        if not any(n for _t, _cf, n in chunks[pno]):
            continue
        files.append(("appendix-notes-%02d.xhtml" % pno,
                      build_notes_part(chunks[pno], pno, total_parts)))
    if any(entries for _t, _cf, _n, entries in sections):
        files.append(("appendix-thoughts.xhtml",
                      build_thoughts_part(sections, set(orphans))))

    manifest_extra = {
        "appendix": {
            "files": ["text/" + name for name, _ in files],
            "title": APPENDIX_TITLE,
            "notes": note_total,
            "thoughts": entry_total,
            "thought_badges": badge_total,
            "chapters": len(sections),
            "notes_parts": len([f for f in files if "notes" in f[0]]),
            "notes_in_appendix": True,
        }
    }
    if unresolved:
        unlinked = unresolved
        manifest_extra["appendix"]["unlinked_underlines"] = unlinked
    if orphans:
        manifest_extra["appendix"]["orphan_entries"] = orphans
    return files, rewritten_bodies, manifest_extra
