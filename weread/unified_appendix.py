# -*- coding: utf-8 -*-
"""统一附录（注释和想法）：把一本书的「注」与「想法」收进书末一个独立章节。

产品规则：
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

import base64
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
.wr-ap-origin{color:#888;font-size:.92em;}
/* 正文角标（想法）：上标、蓝色、可点 */
.wr-fn-ref{display:inline;font-size:.72em;vertical-align:super;line-height:1;
  white-space:nowrap;margin-left:.05em;}
.wr-fn-ref a{text-decoration:none;color:#0366d6;}
/* 正文注角标：橙色实心圆「注」位图（对齐微信读书视觉）
   旧做法用 CSS 画圆：圆框跟着字体走，换第三方字体时行高/字宽对不上，圆容易
   被挤成椭圆（issue #96）。现在是透明底正圆 PNG，不挑字体；尺寸用 em，随正文
   字号缩放，竖直对齐沿用上标。 */
.wr-fn-mark{display:inline-block;font-size:.85em;line-height:1;margin:0 .12em;
  vertical-align:super;text-decoration:none;}
.wr-fn-mark img{width:1em;height:1em;border:0;padding:0;vertical-align:middle;}
"""


# 注角标位图（issue #96）：橙色正圆 + 白色「注」，128×128 透明底 PNG。
# 源码里内嵌字节而不是随仓库带素材文件：PyInstaller 打包无需额外 datas，
# 也不依赖用户机器上装了什么中文字体（CSS 画圆的旧做法换字体就会变椭圆）。
NOTE_BADGE_PNG = base64.b64decode(
    "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"
)


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
                # 引文优先取组级字段；真实数据里 abstract 常常只在
                # pageReviews[].review 上，组级永远为空——结果就是孤儿条目全部
                # 没有引文，读者只看到一条无出处的评论。
                quote = str(rv.get("abstract") or rv.get("contextAbstract") or "").strip()
                if not quote:
                    for page in rv.get("pageReviews") or []:
                        review_body = (page or {}).get("review") or {}
                        quote = str(review_body.get("abstract")
                                    or review_body.get("markText") or "").strip()
                        if quote:
                            break
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
    href="<notes_file>#note-<uid>-<n>" id="notebadge-<uid>-<n>"><img
    src="../images/note-badge.png" alt="注"/></a></span>`

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
        # 角标贴位图（issue #96）；alt 保留「注」，阅读器丢图时仍是可读文字。
        return ('<a class="wr-fn-mark" epub:type="noteref" role="doc-noteref" '
                'href="%s#%s" id="%s">'
                '<img src="../images/note-badge.png" alt="注"/></a>'
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
    # 附录文件放在 OEBPS/text/ 下，style.css 在 OEBPS/ 根；少一级 ../ 就会指向
    # 不存在的 OEBPS/text/style.css，阅读器里 .wr-ap-* 全部失效（标题没有分隔线、
    # 回链不是蓝色）。正文页用的就是 ../style.css，这里必须一致。
    link.set("href", "../style.css")
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


def _add_thought(body, entry, is_orphan: bool, chapter_file: str,
                 chapter_title: str | None = None) -> None:
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
        if chapter_title:
            # 单独成节之后，条目脱离了章节上下文，必须把章名带在条目里。
            origin = etree.SubElement(p, q("span"))
            origin.set("class", "wr-ap-origin")
            origin.text = " " + chapter_title
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
    orphaned: list[tuple[str, str, "ThoughtEntry"]] = []
    for title, ch_file, notes, entries in sections:
        placed = [e for e in entries
                  if "%s:%s" % (e.chapter_uid, e.range_key) not in orphan_keys]
        orphaned.extend((title, ch_file, e) for e in entries
                        if "%s:%s" % (e.chapter_uid, e.range_key) in orphan_keys)
        if not placed:
            continue
        sec = etree.SubElement(body, q("h2"))
        sec.set("class", "wr-ap-sec")
        sec.text = title
        for entry in placed:
            _add_thought(body, entry, False, ch_file)
    # 没能在原文定位的想法单独成节：它们没有可点的 [N]，混在章节目录里会让读者
    # 以为每条都能跳回去。带章名，至少知道出自哪一章。
    if orphaned:
        sec = etree.SubElement(body, q("h2"))
        sec.set("class", "wr-ap-sec")
        sec.text = "未能在原文定位的想法（%d 条）" % len(orphaned)
        for title, ch_file, entry in orphaned:
            _add_thought(body, entry, True, ch_file, chapter_title=title)
    return _dump(root)


def build_appendix(chapter_paths: list[tuple[str, str, Path]],
                   reviews_path: Path, *,
                   include_notes: bool = True,
                   include_thoughts: bool = True,
                   ) -> tuple[bytes, list[tuple[str, str]], dict[str, Any]]:
    """统一附录入口。

    chapter_paths: [(chapter_uid, 章标题, .body 缓存路径)]，顺序即阅读顺序。
    返回 (附录 XHTML 字节, [(chapter_uid, 改写后正文)], manifest 追加信息)。

    include_notes / include_thoughts 决定附录收哪一类：GUI 的四档里「注释」只要注、
    「想法」只要想法，两类都关就不该走这里（调用方不传 unified_appendix）。
    关掉的那一类**不参与正文改写**——注角标保持下载器原样、想法角标不生成，
    否则会出现「角标指向一个不存在的附录页」。

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
        rewritten = xhtml
        notes: list[tuple[str, str, str]] = []
        if include_notes:
            rewritten, notes = rewrite_notes(rewritten, uid, notes_file_of[uid])
        note_total += len(notes)
        badges: list[str] = []
        missing: list[str] = []
        if include_thoughts:
            rewritten, badges, missing = rewrite_badges(
                rewritten, uid, known, "appendix-thoughts.xhtml")
        rewritten_bodies.append((uid, rewritten))
        badge_total += len(badges)
        unresolved.extend("%s:%s" % (uid, r) for r in missing)
        entries = list(known.values()) if include_thoughts else []
        # 有想法但正文没有对应角标：划线注入失败（插件拒绝画错位置）。
        # 想法本身有效，保留进附录；只上报，不当成构建失败。
        orphans.extend("%s:%s" % (uid, r) for r in set(known) - set(badges))
        # 章文件名与 write_epub 的命名一致（chapter-0001.xhtml…），供附录回链使用
        sections.append((chapter_title, "chapter-%04d.xhtml" % ch_idx, notes, entries))

    entry_total = sum(len(e) for _, _cf, _n, e in sections)

    # —— 组装分册文件 ——
    chunks: dict[int, list[tuple[str, list]]] = {}
    if include_notes:
        for (title, ch_file, notes, _entries), (uid, _t, _p, _x, _nc) in zip(sections, bodies):
            chunks.setdefault(part_of[uid], []).append((title, ch_file, notes))
    files: list[tuple[str, bytes]] = []
    for pno in sorted(chunks):
        if not any(n for _t, _cf, n in chunks[pno]):
            continue
        files.append(("appendix-notes-%02d.xhtml" % pno,
                      build_notes_part(chunks[pno], pno, total_parts)))
    if include_thoughts and any(entries for _t, _cf, _n, entries in sections):
        files.append(("appendix-thoughts.xhtml",
                      build_thoughts_part(sections, set(orphans))))

    # —— 一致性校验：逐条解析角标指向的 (文件, 锚点) ——
    # 原来这里是一个计数等式（条目 − 孤儿 == 角标），而孤儿本来就定义为
    # "有条目没角标"，等式恒真、任何输入都不会失败。改成真的去解析：
    # 每个角标的 href 必须落到一个已写出的文件、且该文件里真有这个 id；
    # 注与想法同一套规则。
    file_ids: dict[str, set[str]] = {}
    for name, data in files:
        file_ids[name] = set(re.findall(r'<aside[^>]*id="([^"]+)"', data.decode("utf-8")))
    broken = []
    for uid, body in rewritten_bodies:
        for target, anchor in re.findall(r'href="([^"]+\.xhtml)#([^"]+)"', body):
            if not target.startswith("appendix-"):
                continue
            if anchor not in file_ids.get(target, set()):
                broken.append("%s -> %s#%s" % (uid, target, anchor))
    note_ids = sum(len([i for i in ids if i.startswith("note-")])
                   for name, ids in file_ids.items() if "notes" in name)
    thought_ids = sum(len([i for i in ids if i.startswith("wrfn-")])
                      for name, ids in file_ids.items() if "thoughts" in name)
    if include_notes and note_ids != note_total:
        broken.append("注条目数不一致：正文 %d，附录 %d" % (note_total, note_ids))
    if include_thoughts and thought_ids != entry_total:
        broken.append("想法条目数不一致：目录 %d，附录 %d" % (entry_total, thought_ids))
    if broken:
        raise AppendixError(
            "附录校验失败（%d 处）：%s" % (len(broken), "；".join(broken[:5])))

    manifest_extra = {
        "appendix": {
            "files": ["text/" + name for name, _ in files],
            "title": APPENDIX_TITLE,
            "notes": note_total,
            "thoughts": entry_total,
            "thought_badges": badge_total,
            "chapters": len(sections),
            "notes_parts": len([f for f in files if "notes" in f[0]]),
            "notes_in_appendix": include_notes,
            "thoughts_in_appendix": include_thoughts,
        }
    }
    if unresolved:
        unlinked = unresolved
        manifest_extra["appendix"]["unlinked_underlines"] = unlinked
    if orphans:
        manifest_extra["appendix"]["orphan_entries"] = orphans
    return files, rewritten_bodies, manifest_extra
