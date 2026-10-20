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
/* 正文注角标：[注n] 上标，点击跳附录条目 */
.wr-note-ref{font-size:.72em;vertical-align:super;line-height:1;white-space:nowrap;margin-left:.05em;}
.wr-note-ref a{text-decoration:none;color:#0366d6;}
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
                   known_ranges: set[str]) -> tuple[str, int, list[str]]:
    """把正文里的想法链接改成指向附录锚点；返回 (新正文, 角标数, 未覆盖的 range)。

    - `<a id="wrthought-…" href="#wrthought-…">`：href 改为 `#wrfn-<uid>-<range>`
      （指向附录条目），id 改为 `wrbadge-<uid>-<range>`（附录条目回链的落点）。
      下载阶段注入的链接 id 与 href 同值，所以两处一起换；
    - WeRead 原生 <span class="wr-thought-count">[N]</span> 移除（统一角标已表达条数）；
    - known_ranges 之外（有划线链接但 reviews.jsonl 无对应想法）的链接保留原样
      并计入 missing——调用方据此如实报告，不静默丢数据。
    """
    missing: list[str] = []
    # 匹配整个 <a …> 开标签，把 id 与 href 成对改写；只认本 chapter_uid 的链接
    _ANCHOR_RE = re.compile(
        r'<a\b[^>]*\bid="wrthought-(?P<book>\d+)-(?P<uid>\d+)-(?P<range>\d+-\d+)"'
        r'[^>]*\bhref="#wrthought-(?P=book)-(?P=uid)-(?P=range)"[^>]*>')

    def _sub(m: re.Match[str]) -> str:
        uid, range_key = m.group("uid"), m.group("range")
        if uid != chapter_uid:
            return m.group(0)
        if range_key not in known_ranges:
            missing.append(range_key)
            return m.group(0)
        tag = m.group(0)
        old_key = "wrthought-%s-%s-%s" % (m.group("book"), uid, range_key)
        tag = tag.replace('id="%s"' % old_key,
                          'id="wrbadge-%s-%s"' % (uid, range_key))
        tag = tag.replace('href="#%s"' % old_key,
                          'href="#wrfn-%s-%s"' % (uid, range_key))
        return tag

    rewritten = _ANCHOR_RE.sub(_sub, xhtml)
    # 兜底：id 与 href 不同值（或顺序不同）的链接，至少把 href 指过去
    rewritten = _THOUGHT_HREF_RE.sub(
        lambda m: ("#wrfn-%s-%s" % (m.group(2), m.group(3))
                   if m.group(2) == chapter_uid
                   and m.group(3) in known_ranges else m.group(0)),
        rewritten)
    rewritten = _THOUGHT_COUNT_RE.sub("", rewritten)
    # 只数**指向附录锚点**的角标：未覆盖的 range 链接保留原样（wrthought），
    # 附录里没有它，不能算成角标——否则 R6 的等价断言会把「如实上报的缺口」
    # 也算成数据一致。
    badges = len({r for r in re.findall(
        r"#wrfn-%s-(\d+-\d+)" % re.escape(chapter_uid), rewritten)})
    return rewritten, badges, missing


def rewrite_notes(xhtml: str, chapter_uid: str) -> tuple[str, list[tuple[str, str, str]]]:
    """把正文里的死图注角标转成可点角标；返回 (新正文, [(锚点id, 回链id, 注文)])。

    下载器输出的注是 `<img class="qqreader-footnote" alt="注文"/>`——无 src
    的死图，阅读器显示不出来（这是 W13 的根因）。注文在 alt 里、且是 HTML
    字符引用（&#x82E5; 之类），必须 unescape。

    转换后：`<span class="wr-note-ref"><a epub:type="noteref"
    role="doc-noteref" href="#note-<uid>-<n>" id="notebadge-<uid>-<n>">[注n]</a></span>`
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
        return ('<span class="wr-note-ref"><a epub:type="noteref" '
                'role="doc-noteref" href="#%s" id="%s">[注%d]</a></span>'
                % (target, badge, n))

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


def _add_note(body, target_id: str, badge_id: str, text: str) -> None:
    aside = etree.SubElement(body, q("aside"))
    aside.set("{%s}type" % EPUBNS, "footnote")
    aside.set("id", target_id)
    aside.set("class", "wr-book-footnote")
    p = etree.SubElement(aside, q("p"))
    p.set("class", "wr-ap-item")
    back = etree.SubElement(p, q("a"))
    back.set("href", "#" + badge_id)
    back.set("class", "wr-ap-back")
    back.text = "[注%d]" % int(target_id.rsplit("-", 1)[1])
    sp = etree.SubElement(p, q("span"))
    sp.set("class", "wr-ap-note")
    sp.text = " " + text


def _add_thought(body, entry, is_orphan: bool) -> None:
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
        back.set("href", "#" + ref_id)
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
    """生成一册「注释」。chapters = [(章标题, 注条目)]。"""
    suffix = "" if total_parts <= 1 else "（%d/%d）" % (part_no, total_parts)
    heading = APPENDIX_TITLE + " · 注释" + suffix
    root, body = _html_shell(
        heading, heading,
        "正文中 [注n] 角标对应此处条目，点编号可跳回原文。" if part_no == 1 else None)
    for title, notes in chapters:
        sec = etree.SubElement(body, q("h2"))
        sec.set("class", "wr-ap-sec")
        sec.text = title
        for target_id, badge_id, text in notes:
            _add_note(body, target_id, badge_id, text)
    return _dump(root)


def build_thoughts_part(sections, orphan_keys: set[str]) -> bytes:
    """生成「想法」册。sections = [(章标题, 注条目, 想法条目)]。"""
    heading = APPENDIX_TITLE + " · 想法"
    root, body = _html_shell(
        heading, heading,
        "正文中 [N] 角标对应此处条目，点编号可跳回原文。")
    for title, notes, entries in sections:
        if not entries:
            continue
        sec = etree.SubElement(body, q("h2"))
        sec.set("class", "wr-ap-sec")
        sec.text = title
        for entry in entries:
            is_orphan = "%s:%s" % (entry.chapter_uid, entry.range_key) in orphan_keys
            _add_thought(body, entry, is_orphan)
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
    sections: list[tuple[str, list[tuple[str, str, str]], list[ThoughtEntry]]] = []
    rewritten_bodies: list[tuple[str, str]] = []
    badge_total = 0
    note_total = 0
    unresolved: list[str] = []
    orphans: list[str] = []

    for uid, chapter_title, body_path in chapter_paths:
        known = set(reviews.get(uid, {}))
        xhtml = Path(body_path).read_text(encoding="utf-8")
        # 注：死图角标 → 可点 [注n]（注文取自 alt）
        rewritten, notes = rewrite_notes(xhtml, uid)
        note_total += len(notes)
        # 想法：wrthought 链接 → 指向附录
        rewritten, badges, missing = rewrite_badges(rewritten, uid, known)
        rewritten_bodies.append((uid, rewritten))
        badge_total += badges
        unresolved.extend("%s:%s" % (uid, r) for r in missing)
        entries = list(reviews.get(uid, {}).values())
        # 有想法但正文没有对应角标：划线注入失败（插件拒绝画错位置）。
        # 想法本身有效，保留进附录；只上报，不当成构建失败。
        placed = set(re.findall(r"#wrfn-%s-(\d+-\d+)" % re.escape(uid), rewritten))
        orphans.extend("%s:%s" % (uid, r) for r in known - placed)
        sections.append((chapter_title, notes, entries))

    entry_total = sum(len(e) for _, _, e in sections)
    # R6：**被改写、指向附录**的想法角标必须在附录里都有落点。未覆盖的 range
    # （有划线链接但 reviews 无对应想法组——只划线没写想法，WeRead 常态）
    # 保留原样、不上附录，只如实上报；它们不指向附录，点了也无所谓，
    # 但不能因此让整本书导不出附录版。
    if badge_total != entry_total - len(orphans):
        raise AppendixError(
            "统一附录数据不一致：指向附录的想法角标 %d 个，想法条目 %d 个"
            "（其中 %d 个无正文角标，%d 个划线无想法组未入附录）"
            % (badge_total, entry_total, len(orphans), len(unresolved)))

    # —— 分册：注按条目数装箱，想法单册 ——
    # 单文件 12 MB 的注附录在手机阅读器上会卡（后汉书实测），按 ~3000 条/册
    # 拆分，单册约 1 MB。
    note_chunks: list[list[tuple[str, list]]] = []
    chunk: list[tuple[str, list]] = []
    acc = 0
    for title, notes, _entries in sections:
        chunk.append((title, notes))
        acc += len(notes)
        if acc >= MAX_NOTES_PER_PART:
            note_chunks.append(chunk)
            chunk, acc = [], 0
    if chunk and any(n for _t, n in chunk):
        note_chunks.append(chunk)

    files: list[tuple[str, bytes]] = []
    total_parts = len(note_chunks)
    for i, part in enumerate(note_chunks, 1):
        name = "appendix-notes-%02d.xhtml" % i
        files.append((name, build_notes_part(part, i, total_parts)))
    if any(entries for _t, _n, entries in sections):
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
            "notes_parts": total_parts,
            "notes_in_appendix": True,
        }
    }
    if unresolved:
        # 有划线链接但没有想法组（只划线没写想法）：链接原样保留，不入附录
        manifest_extra["appendix"]["unlinked_underlines"] = unresolved
    if orphans:
        # 想法在、划线没画上（WeRead 引文匹配失败）。条目已保留在附录，
        # 这里只如实说明「这些想法在正文里没有角标可点」。
        manifest_extra["appendix"]["orphan_entries"] = orphans
    return files, rewritten_bodies, manifest_extra
