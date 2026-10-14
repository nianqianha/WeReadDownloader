#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Desktop WeRead full-book exporter.

Uses the installed Kindle plugin's LuaJIT protocol, shard decoder, and exact
annotation-coordinate injector. Credentials are read from the user-private
WeReadDesktop settings file and are never written into an export bundle.
"""
from __future__ import annotations

import argparse
import atexit
import ctypes
import hashlib
import json
import os
import re
import subprocess
import shutil
import sys
import threading
import tarfile
import time
import traceback
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from lxml import etree, html as lxml_html
from lupa.luajit21 import LuaRuntime
import weread_login

if getattr(sys, "frozen", False):
    ROOT = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / "kindle_data"
else:
    # The Lua plugin lives in the Kindle tree, which is a SIBLING of this
    # repository's parent (the repo moved to <各类>/weread-downloader in
    # 2026-10; the plugin never moved). Two candidate roots are probed, the
    # first that actually contains the plugin wins; WEREAD_KINDLE_ROOT wins
    # over both, so a future move is a one-variable fix.
    candidates = []
    env_root = os.environ.get("WEREAD_KINDLE_ROOT")
    if env_root:
        candidates.append(Path(env_root))
    # from <repo>/weread/: parent = repo, parent.parent = 各类, and the Kindle
    # tree is 各类/Kindle
    candidates.append(Path(__file__).resolve().parents[2] / "Kindle")
    for root in candidates:
        if (root / "03_插件源码与解压文件" / "weread.koplugin").is_dir():
            ROOT = root
            break
    else:
        raise RuntimeError(
            "未找到微信读书 Lua 插件目录。它应位于 <各类>/Kindle/03_插件源码与解压文件/weread.koplugin；"
            "若你移动过它，请设置环境变量 WEREAD_KINDLE_ROOT 指向 Kindle 目录。")
PLUGIN = ROOT / "03_插件源码与解压文件" / "weread.koplugin"
PRIVATE = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / "WeReadDesktop"
SETTINGS = PRIVATE / "weread-live-settings.lua"
COOKIE_STATE = PRIVATE / "session-cookies.json"
OUTPUT = PRIVATE / "exports"
API = "https://i.weread.qq.com/api/agent/gateway"
WEB = "https://weread.qq.com"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36 Edg/135.0.0.0"
CHAPTER_HTML_LIMIT = 16 * 1024 * 1024
ARCHIVE_LIMIT = 128 * 1024 * 1024
COOKIE_STATE_LOCK = threading.Lock()
IMAGE_LIMIT = 16 * 1024 * 1024
# Machine-readable stdout contract shared with desktop_app.py.
SEARCH_FIELDS = ("bookId", "title", "author", "cover", "newRating", "newRatingCount", "category")
# An underline's API range is a "<start>-<end>" character span, and the plugin
# writes the same span into the anchor it injects. Matching the two by identity
# is the only exact way to tell which underlines were actually placed.
_UNDERLINE_ID_RE = re.compile(r"\d+-\d+")
_UNDERLINE_HREF_RE = re.compile(r"wrthought-\d+-\d+-(\d+-\d+)")
# A checkpoint is written for every chapter of a book and every chapter's row is
# copied into reviews.jsonl, so audit samples are evidence, not archive: a handful
# is enough to eyeball a disagreement, and 200 characters is enough text to see
# where one stops matching the other.
_AUDIT_SAMPLE_LIMIT = 10
_AUDIT_TEXT_LIMIT = 200


def force_utf8_stdio() -> None:
    """Pin worker stdout/stderr to UTF-8 so the GUI never has to guess a codepage."""
    for stream in (sys.stdout, sys.stderr):
        if stream is None:
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError, ValueError):
            pass


def clean_field(value: Any) -> str:
    """One stdout field: no tabs/newlines, so line-based parsing stays lossless."""
    if value is None:
        return ""
    return str(value).replace("\t", " ").replace("\r", " ").replace("\n", " ").strip()


def search_row(book: dict[str, Any]) -> str:
    """Serialise one search hit as tab-separated fields (real TAB separators)."""
    return "\t".join(clean_field(book.get(key)) for key in SEARCH_FIELDS)


def search_meta(book_id: Any, category: Any) -> str:
    return "SEARCH_META\t" + clean_field(book_id) + "\t" + clean_field(category)


def enrich_search_categories(api: "WeRead", books: list[dict[str, Any]],
                             max_books: int = 6, budget: float = 4.0) -> None:
    """Fill in `category` for search hits; /store/search does not carry it.

    Bounded on purpose: a search must never stall behind slow book lookups.
    """
    started = time.monotonic()
    for book in books[:max_books]:
        if time.monotonic() - started > budget:
            return
        book_id = clean_field(book.get("bookId"))
        if not book_id:
            continue
        try:
            response = api.gateway("/book/info", {"bookId": book_id}, timeout=(4, 8))
        except Exception:
            continue
        source = response.get("book") or response.get("bookInfo") or response
        if isinstance(source, dict):
            book["category"] = source.get("category") or ""


def _load_lua_module(lua: LuaRuntime, module: str, path: Path) -> None:
    source = path.read_bytes()
    lua.globals()._module_source = source
    lua.globals()._module_name = module.encode()
    lua.execute(b'package.loaded[_module_name] = (function()\n' + source + b'\nend)()')


def lua_runtime() -> LuaRuntime:
    lua = LuaRuntime(unpack_returned_tuples=True, encoding=None)
    lua.globals()._json_decode_python = lambda raw: json.loads(raw.decode("utf-8"))
    lua.execute(b'''package.loaded["json"] = {decode=function(v) return _json_decode_python(v) end}
package.loaded["bit"] = require("bit")
package.loaded["logger"] = {dbg=function() end, info=function() end, warn=function() end, err=function() end}
package.loaded["weread.lib.logger"] = package.loaded["logger"]''')
    lib = PLUGIN / "weread" / "lib"
    # Load production sources directly: LuaJIT's Windows file searcher cannot
    # reliably open module paths containing this workspace's non-ASCII dirs.
    for module, filename in (
        ("crypto", "crypto.lua"), ("reader_state", "reader_state.lua"),
        ("protocol", "protocol.lua"), ("annotation_source", "annotation_source.lua"),
        ("content", "content.lua"), ("annotations", "annotations.lua"),
    ):
        _load_lua_module(lua, "weread.lib." + module, lib / filename)
    lua.execute(b'''local W = require("weread.lib.protocol")
local C = require("weread.lib.content")
local A = require("weread.lib.annotations")
local R = require("weread.lib.reader_state")
_EXPORT = {
  e=function(v) return W.e(v) end,
  make_content_params=function(b,c,ps,style,ct) return W.make_content_params(b,c,ps,{style=style,ct=ct}) end,
  decode_shards=function(a,b,c) return C.decode_content_shards(a,b,c) end,
  decode_shard=function(a) return C.decode_content_shard(a) end,
  sanitize_css=function(css) return C.sanitize_book_css(css) end,
  txt_to_xhtml=function(text) return C.txt_to_xhtml(text) end,
  reader_state=function(text) return R.extract(text, function(v) return require("json").decode(v) end) end,
  inject=function(xhtml, ul, reviews, book) return A.process(xhtml, ul, reviews, book) end,
  -- audit runs the SAME placement pass as inject and keeps only its accounting,
  -- because Annotations.audit() and Annotations.injectUnderlines() both go
  -- through injectUnderlinesCore(): the audit's verdict is therefore the
  -- injection's verdict, not a second opinion that could drift from it.
  -- Guarded here because only this scope can tell that an installed plugin copy
  -- predates Annotations.audit; nil is the answer a Lua table lookup would give
  -- a missing key, and it lets an older plugin keep exporting.
  audit=function(xhtml, ul, reviews, book) if not A.audit then return nil end return A.audit(xhtml, ul, reviews, book) end,
  urlencode=function(v) return W.urlencode(v) end,
  reader_url=function(b,c) return W.reader_url(b,c) end,
}
''')
    return lua


def _set_private_acl(path: Path, directory: bool) -> None:
    """Restrict a path to the current user; never fail the job over this.

    This is hardening, not a precondition: the directory was just created and is
    already reachable, and losing an export because a discretionary ACL could
    not be rewritten would be a far worse outcome than a directory that keeps
    its inherited permissions.

    MEASURED 2026-10-12: from the frozen worker exe the identical icacls call
    returns exit status 5 (ERROR_ACCESS_DENIED) on a directory the very same
    call succeeds on when run from the source interpreter, with asInvoker in
    both manifests and no shim on PATH. The cause is environmental and was not
    chased further; what matters here is that it must not abort the download.

    Any failure is reported on stderr with the account that was asked for, so a
    genuine permissions problem is still visible in the log.
    """
    if os.name != "nt":
        try:
            os.chmod(path, 0o700 if directory else 0o600)
        except OSError as exc:
            print(f"[ACL] chmod failed for {path} ({type(exc).__name__}); "
                  f"continuing with existing permissions", file=sys.stderr, flush=True)
        return
    # Use explicit encoding to avoid UnicodeDecodeError on Chinese Windows
    try:
        user = subprocess.check_output(["whoami"], encoding="utf-8", errors="replace").strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"[ACL] could not resolve the current account for {path} "
              f"({type(exc).__name__}); continuing with existing permissions",
              file=sys.stderr, flush=True)
        return
    grant = user + (":(OI)(CI)F" if directory else ":F")
    try:
        subprocess.run(["icacls", str(path), "/inheritance:r", "/grant:r", grant],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"[ACL] could not restrict {path} to {user} "
              f"({type(exc).__name__}); continuing with existing permissions",
              file=sys.stderr, flush=True)


def secure_output_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _set_private_acl(path, True)


def load_credentials() -> dict[str, Any]:
    if not SETTINGS.is_file():
        raise RuntimeError(f"Credential file not found: {SETTINGS}")
    raw = SETTINGS.read_bytes()
    start = raw.find(b"\nreturn {")
    if start < 0:
        raise RuntimeError("Credential file has an unexpected format")
    lua = LuaRuntime(unpack_returned_tuples=True, encoding=None)
    table = lua.execute(raw[start + 1:])
    result: dict[str, Any] = {}
    for key in (b"api_key", b"cookies", b"wr_ticket", b"wr_wrpa"):
        value = table[key]
        if key == b"cookies":
            result["cookies"] = {k.decode(): v.decode() for k, v in value.items()}
        else:
            result[key.decode()] = value.decode() if value else ""
    # The session overlay is owned by weread_login: it is the only writer that
    # knows the file may be DPAPI-sealed. Reading it here as plain JSON raised
    # UnicodeDecodeError at import time once it was encrypted, and persisting it
    # as plain JSON silently undid the sealing on every cookie refresh -- two
    # owners, two formats, one file.
    stored = weread_login.load_saved_credentials(COOKIE_STATE)
    if stored:
        overlay_cookies = stored.get("cookies")
        if isinstance(overlay_cookies, dict):
            result["cookies"].update({str(k): str(v) for k, v in overlay_cookies.items()})
        for key in ("wr_ticket", "wr_wrpa"):
            if isinstance(stored.get(key), str) and stored[key]:
                result[key] = stored[key]
        # api_key deliberately not taken from the overlay: it stays the Lua
        # base's value, exactly as before this file was ever sealed.
    if not result["api_key"] or not result["cookies"].get("wr_skey"):
        raise RuntimeError("Private credentials need an API key and WeRead session")
    return result


def lua_table(lua: LuaRuntime, value: Any) -> Any:
    if isinstance(value, dict):
        out = lua.table()
        for key, item in value.items():
            lua_key = key.encode("utf-8") if isinstance(key, str) else key
            out[lua_key] = lua_table(lua, item)
        return out
    if isinstance(value, list):
        out = lua.table()
        for i, item in enumerate(value, 1): out[i] = lua_table(lua, item)
        return out
    return value.encode("utf-8") if isinstance(value, str) else value


def py_value(value: Any) -> Any:
    if hasattr(value, "items"):
        items = list(value.items())
        if all(isinstance(k, int) for k, _ in items):
            return [py_value(v) for _, v in sorted(items)]
        return {(k.decode("utf-8") if isinstance(k, bytes) else k): py_value(v) for k, v in items}
    return value.decode("utf-8") if isinstance(value, bytes) else value


def strip_thought_bodies(reviews: list[Any]) -> list[Any]:
    """Reduce review rows to the placement data, dropping note text and padding.

    Two things are removed, and the split matters:

      * review.content -- the note body. Free to drop: annotations.lua reads it
        only to fill the tap popup.

      * everything the placement pass never reads. Each pageReview carries a full
        author profile, a book record, topics, follow flags and timing; on the
        measured corpus that padding is ~95% of the JSON, so dropping content
        alone saved only 4.3% and was not worth a switch. What is kept is
        review.range, review.abstract, review.contextAbstract, review.markText,
        review.author.nick and review.likesCount -- the fields quoteCandidate,
        buildThoughtPopupItems and the [n] badge actually touch.

    abstract is NOT decoration and is never dropped: it is the only selected-text
    quote WeRead serves, so it is what underline placement and
    annotation_source.calibrate() both depend on. A mode that removed it would
    place no highlights at all, which is why --no-thoughts still fetches
    readreviews in full and prunes only afterwards.

    Rows of an unexpected shape are returned untouched, so an API change costs
    verbosity, never a silently emptied export.
    """
    if not isinstance(reviews, list):
        return reviews
    stripped: list[Any] = []
    for row in reviews:
        if not isinstance(row, dict):
            stripped.append(row)
            continue
        pages = row.get("pageReviews")
        if not isinstance(pages, list):
            stripped.append(row)
            continue
        new_pages: list[Any] = []
        for page in pages:
            if not isinstance(page, dict):
                new_pages.append(page)
                continue
            inner = page.get("review")
            if not isinstance(inner, dict):
                new_pages.append(page)
                continue
            author = inner.get("author")
            nick = None
            if isinstance(author, dict):
                nick = author.get("nick") or author.get("name")
            trimmed = {}
            # Only copy what the API actually returned: writing None into a key
            # that was absent fabricates a field, and quoteCandidate treats a
            # present-but-empty abstract as a real (failing) candidate rather
            # than as no candidate at all.
            for key in ("range", "abstract", "contextAbstract", "markText"):
                if key in inner:
                    trimmed[key] = inner[key]
            if "likesCount" in inner or "likesCount" in page:
                trimmed["likesCount"] = inner.get("likesCount", page.get("likesCount", 0))
            trimmed["author"] = {"nick": nick or "匿名"}
            new_pages.append({"reviewId": page.get("reviewId"), "review": trimmed})
        stripped.append({
            "range": row.get("range"),
            "totalCount": row.get("totalCount"),
            "pageReviews": new_pages,
        })
    return stripped


def summarize_underline_audit(lua: LuaRuntime, xhtml: str, underlines: dict[str, Any],
                              reviews: list[dict[str, Any]], book_id: str,
                              chapter_uid: Any) -> dict[str, Any]:
    """Ask the plugin where each underline landed, and grade the landing by TEXT.

    The identity measurement can only prove that an underline's authoritative id
    reached the page. It is blind to the defect this exists for: a highlight
    painted a stable few characters off still carries its own id, so it counted
    as fully placed. 94 highlights in one real book were off by exactly 4
    characters and nothing above could see them.

    Returns the five audit-derived checkpoint keys. Each is null when the audit
    did not run, because "not measured" and "measured, nothing wrong" are
    different facts and only the second one may ever read as a zero.

    Nothing in here may raise. A plugin copy without Annotations.audit, a Lua
    error inside it, or a table that does not decode into records must cost the
    measurement, never the chapter and never the export.
    """
    unmeasured: dict[str, Any] = {
        "verified_underlines": None, "unverified_underlines": None,
        "refused_underlines": None, "refused_underline_reasons": None,
        "quote_mismatch_samples": None,
    }
    audit = lua.globals()[b"_EXPORT"][b"audit"]
    if audit is None:
        return unmeasured
    rows = underlines.get("underlines")
    rows = rows if isinstance(rows, list) else []
    # The rows identity already accepted as ranged. Reused as the contract check
    # on the answer below rather than recomputed differently.
    expected = len({str(row.get("range")).strip() for row in rows
                    if row.get("range") and _UNDERLINE_ID_RE.fullmatch(
                        str(row.get("range")).strip())})
    # Annotations.audit() takes the underline LIST plus a reviews MAP keyed by
    # range; Annotations.process() -- the inject entry above -- takes the wrapper
    # object plus the reviews LIST and builds that map itself. Handing audit
    # inject's arguments does not fail: the shared core iterates the argument as a
    # list, finds it empty, and returns an empty record list, which would then be
    # filed as a chapter measured clean. So both shapes are built here, on
    # purpose, and the answer is checked against `expected` before it is believed.
    thought_map = {str(review.get("range")): review for review in reviews
                   if isinstance(review, dict) and review.get("range")}
    try:
        records = py_value(audit(xhtml.encode("utf-8"), lua_table(lua, rows),
                                 lua_table(lua, thought_map), book_id.encode("utf-8")))
    except Exception as exc:
        print(f"[ANNOTATION] chapter UID {chapter_uid}: text audit failed "
              f"({type(exc).__name__}); highlight placement left unverified",
              file=sys.stderr, flush=True)
        return unmeasured
    if not isinstance(records, list):
        print(f"[ANNOTATION] chapter UID {chapter_uid}: text audit returned "
              f"{type(records).__name__}, not a record list; highlight placement "
              f"left unverified", file=sys.stderr, flush=True)
        return unmeasured
    if len(records) < expected:
        # The audit owes an answer for every ranged row, refusals included, so a
        # shorter answer means it was handed something it did not recognise and
        # measured nothing. That is the one failure that would otherwise be
        # indistinguishable from a chapter that had nothing wrong in it.
        print(f"[ANNOTATION] chapter UID {chapter_uid}: text audit answered "
              f"{len(records)} record(s) for {expected} ranged underline(s); "
              f"highlight placement left unmeasured", file=sys.stderr, flush=True)
        return unmeasured
    verified = unverified = refused = 0
    refused_reasons: dict[str, int] = {}
    mismatch_samples: list[dict[str, str]] = []
    for item in records:
        # A row that is not a record is dropped rather than guessed at: reading a
        # broken row as placed would manufacture a "verified" count, which is
        # the same class of lie this audit exists to remove.
        if not isinstance(item, dict):
            continue
        placed = item.get("placed")
        # Lua answers with a real boolean. Accept a numeric 1 as well, because
        # the costly misreading is a placement counted as a refusal.
        placed = placed is True or placed == 1
        reason = str(item.get("reason") or "")
        if placed:
            if reason == "ok":
                verified += 1
            else:
                # Landed, but the text it landed on is not proven to be the
                # quote. Counted on its own, never folded into `verified`: this
                # is precisely the case the identity measurement cannot see.
                unverified += 1
                # Only a highlight that BOTH landed and disagrees can be
                # eyeballed against its quote. Once the plugin refuses a
                # mismatched underline instead of placing it, the same mismatch
                # is a refusal and shows up as refused_underline_reasons
                # ["text_mismatch"], so widening this to refusals would
                # double-count one defect under two keys.
                if reason == "text_mismatch" and len(mismatch_samples) < _AUDIT_SAMPLE_LIMIT:
                    mismatch_samples.append(
                        {"range": str(item.get("range") or ""),
                         "quote": str(item.get("quote") or "")[:_AUDIT_TEXT_LIMIT],
                         "marked": str(item.get("marked") or "")[:_AUDIT_TEXT_LIMIT]})
        else:
            refused += 1
            refused_reasons[reason] = refused_reasons.get(reason, 0) + 1
    return {"verified_underlines": verified, "unverified_underlines": unverified,
            "refused_underlines": refused, "refused_underline_reasons": refused_reasons,
            "quote_mismatch_samples": mismatch_samples}


def normalize_chapter_xhtml(xhtml: str, lua: LuaRuntime) -> str:
    if xhtml.strip() and not re.search(r"<(?:html|body)\b", xhtml, re.I):
        return py_value(lua.globals()[b"_EXPORT"][b"txt_to_xhtml"](xhtml.encode("utf-8")))
    return xhtml


def validate_chapter_body(xhtml: str, chapter: dict[str, Any]) -> None:
    if not re.search(r"<body\b", xhtml, re.I):
        raise RuntimeError(f"Chapter UID {chapter['chapterUid']} decoded without an XHTML body")
    if int(chapter.get("wordCount", 0) or 0) <= 0:
        return
    try:
        # lxml refuses a str that carries an XML encoding declaration
        # ("Unicode strings with encoding declaration are not supported"), and
        # txt_to_xhtml()/decode_shards() both emit one. Feeding bytes lets the
        # real text through instead of silently looking like an empty chapter.
        document = lxml_html.fromstring(xhtml.encode("utf-8"))
        bodies = document.xpath("//body")
        visible_text = " ".join(" ".join(body.xpath(".//text()")) for body in bodies).strip()
        has_images = any(body.xpath(".//img") for body in bodies)
    except (ValueError, TypeError):
        visible_text, has_images = "", False
    if not visible_text and not has_images:
        raise RuntimeError(
            f"Chapter UID {chapter['chapterUid']} decoded to an empty body despite catalog wordCount={chapter.get('wordCount')}"
        )


def lua_json(lua: LuaRuntime, value: Any) -> bytes:
    lua.globals()._json_value = lua_table(lua, value)
    return lua.eval(b'require("json").encode(_json_value)')


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_text(path: Path, text: str) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(text, encoding="utf-8")
    os.replace(temp, path)


class WeRead:
    def __init__(self, credentials: dict[str, Any], lua: LuaRuntime):
        self.auth = credentials
        self.lua = lua
        self.http = requests.Session()
        retry = Retry(total=4, connect=3, read=2, status=3,
                      backoff_factor=0.35,
                      status_forcelist=(408, 425, 429, 500, 502, 503, 504),
                      allowed_methods=frozenset({"GET", "POST"}),
                      respect_retry_after_header=True)
        adapter = HTTPAdapter(max_retries=retry, pool_connections=4, pool_maxsize=4)
        self.http.mount("https://", adapter)
        self.http.mount("http://", adapter)
        self.http.headers.update({"User-Agent": USER_AGENT,
                                  "Accept": "application/json, text/plain, */*"})
        # Reader cookies are scoped to weread.qq.com; never send them to the
        # separate bearer-token gateway at i.weread.com.
        for name, value in credentials["cookies"].items():
            self.http.cookies.set(name, value, domain=".weread.qq.com", path="/")
        self._initial_cookies = dict(credentials["cookies"])

    def persist_cookies(self) -> None:
        current_cookies = requests.utils.dict_from_cookiejar(self.http.cookies)
        changed = {key: value for key, value in current_cookies.items()
                   if self._initial_cookies.get(key) != value}
        if not changed:
            return
        secure_output_directory(PRIVATE)
        with COOKIE_STATE_LOCK:
            # Read/merge/write through weread_login so the sealed format is
            # preserved. Keep the stored api_key: persist_cookies only refreshes
            # the jar, and dropping the key here would make the next download
            # refuse to start.
            previous = weread_login.load_saved_credentials(COOKIE_STATE)
            merged_cookies = dict(previous.get("cookies") or {})
            merged_cookies.update(changed)
            payload = {
                "cookies": merged_cookies,
                "api_key": previous.get("api_key", ""),
                "wr_ticket": self.auth.get("wr_ticket") or previous.get("wr_ticket") or "",
                "wr_wrpa": self.auth.get("wr_wrpa") or previous.get("wr_wrpa") or "",
            }
            if previous.get("account"):
                payload["account"] = previous["account"]
            try:
                weread_login.save_credentials(payload, COOKIE_STATE)
                self._initial_cookies.update(changed)
            except OSError:
                pass

    def gateway(self, name: str, params: dict[str, Any],
                timeout: tuple[float, float] = (10, 45)) -> dict[str, Any]:
        payload = {"api_name": name, "skill_version": "1.0.5", **params}
        response = self.http.post(API, json=payload,
            headers={"Authorization": "Bearer " + self.auth["api_key"],
                     "Origin": WEB, "Referer": WEB + "/"}, timeout=timeout)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict): raise RuntimeError(f"Gateway returned invalid data for {name}")
        err_code = data.get("errCode", data.get("errcode"))
        if err_code not in (None, 0, "0"):
            raise RuntimeError(f"WeRead gateway error for {name}: {err_code}")
        return data

    def reader_state(self, book: dict[str, Any], chapter_uid: str | None = None) -> dict[str, Any]:
        url = py_value(self.lua.globals()[b"_EXPORT"][b"reader_url"](
            book["bookId"].encode(), chapter_uid.encode() if chapter_uid else None))
        response = self.http.get(url, headers={"Referer": url}, timeout=(10, 30))
        response.raise_for_status()
        state = py_value(self.lua.globals()[b"_EXPORT"][b"reader_state"](response.content))
        if not state.get("psvts"): raise RuntimeError("Reader session has no psvts")
        self.persist_cookies()
        return state

    def chapter_shard(self, book: dict[str, Any], chapter: dict[str, Any],
                      psvts: str, endpoint: str) -> str:
        now = int(time.time())
        params = py_value(self.lua.globals()[b"_EXPORT"][b"make_content_params"](
            str(book["bookId"]).encode(), str(chapter["chapterUid"]).encode(),
            str(psvts).encode(), endpoint.endswith("e_2"), now))
        reader_url = py_value(self.lua.globals()[b"_EXPORT"][b"reader_url"](
            str(book["bookId"]).encode(), str(chapter["chapterUid"]).encode()))
        response = self.http.post(WEB + endpoint, json=params,
            headers={"Content-Type": "application/json;charset=UTF-8",
                     "Origin": WEB, "Referer": reader_url}, timeout=(10, 45))
        response.raise_for_status()
        return self._decode_shard_bytes(response)

    @staticmethod
    def _decode_shard_bytes(response) -> str:
        """Decode a shard body without trusting the response's charset.

        `response.text` lets requests guess an encoding and then raise
        UnicodeDecodeError on the bytes that are not text at all -- these
        endpoints sometimes answer with a binary/compressed body and no
        charset, which killed a chapter outright. The shards are the plugin's
        own encoded payload, so latin-1 is the lossless byte-preserving
        fallback: it never raises, and the Lua side works on the bytes.
        """
        raw = response.content
        if not raw:
            return ""
        charset = None
        try:
            charset = response.encoding
        except Exception:
            charset = None
        for candidate in (charset, "utf-8"):
            if not candidate:
                continue
            try:
                return raw.decode(candidate)
            except (UnicodeDecodeError, LookupError):
                continue
        return raw.decode("latin-1")

    def chapter_html(self, book: dict[str, Any], chapter: dict[str, Any]) -> tuple[str, str]:
        # Retry checksum mismatches and transport faults, and refetch the whole
        # shard set each time so a chapter never mixes pieces from different
        # reader sessions. A shard that will not decode is usually a truncated
        # or compressed body rather than a permanent fault, so it gets the same
        # refetch instead of failing the chapter outright.
        for attempt in range(4):
            try:
                return self._chapter_html_once(book, chapter)
            except Exception as exc:
                reason = str(exc)
                checksum = "Shard MD5 mismatch" in reason
                transport = isinstance(exc, (requests.RequestException,
                                              UnicodeDecodeError))
                if not (checksum or transport):
                    raise
                if attempt == 3:
                    if checksum:
                        raise RuntimeError(
                            f"Chapter UID {chapter['chapterUid']} shard MD5 mismatch after 4 full fetch attempts"
                        ) from exc
                    raise
                delay = 0.5 * (attempt + 1)
                label = "shard checksum mismatch" if checksum else type(exc).__name__
                print(f"[RETRY] UID {chapter['chapterUid']} {label}; retry {attempt + 1}/3 in {delay:.1f}s", file=sys.stderr, flush=True)
                time.sleep(delay)

    def _chapter_html_once(self, book: dict[str, Any], chapter: dict[str, Any]) -> tuple[str, str]:
        state = self.reader_state(book, str(chapter["chapterUid"]))
        e0 = self.chapter_shard(book, chapter, state["psvts"], "/web/book/chapter/e_0")
        if e0.startswith("{") and '"bookId"' in e0:
            t0 = self.chapter_shard(book, chapter, state["psvts"], "/web/book/chapter/t_0")
            try:
                t1 = self.chapter_shard(book, chapter, state["psvts"], "/web/book/chapter/t_1")
            except requests.RequestException:
                t1 = ""
            plain = py_value(self.lua.globals()[b"_EXPORT"][b"decode_shards"](
                t0.encode(), t1.encode(), b""))
            # decode_shards answers with a string for real text, but on some
            # catalogue entries it hands back a Lua table, and calling
            # .encode() on that raised "TypeError: data must be str, not
            # tuple" and lost the chapter. Treat anything that is not text as
            # the empty body it effectively is, and let the caller's guard
            # report it.
            if isinstance(plain, bytes):
                plain = plain.decode("utf-8", "replace")
            if not isinstance(plain, str):
                raise RuntimeError(
                    f"Chapter {chapter['chapterUid']} plain-text shards decoded to "
                    f"{type(plain).__name__}, not text")
            xhtml = py_value(self.lua.globals()[b"_EXPORT"][b"txt_to_xhtml"](plain.encode()))
            return xhtml, ""
        e1 = self.chapter_shard(book, chapter, state["psvts"], "/web/book/chapter/e_1")
        e3 = self.chapter_shard(book, chapter, state["psvts"], "/web/book/chapter/e_3")
        if len(e0) + len(e1) + len(e3) > CHAPTER_HTML_LIMIT * 4:
            raise RuntimeError("Encoded chapter exceeds safety limit")
        xhtml = py_value(self.lua.globals()[b"_EXPORT"][b"decode_shards"](
            e0.encode(), e1.encode(), e3.encode()))
        # Some catalogue items decode to plain text without XHTML wrappers.
        # Match the installed plugin's text-to-XHTML normalization.
        xhtml = normalize_chapter_xhtml(xhtml, self.lua)
        if len(xhtml.encode("utf-8")) > CHAPTER_HTML_LIMIT:
            raise RuntimeError("Decoded chapter exceeds 16 MiB safety limit")
        css = ""
        try:
            css_state = self.reader_state(book, str(chapter["chapterUid"]))
            shard = self.chapter_shard(book, chapter, css_state["psvts"],
                                       "/web/book/chapter/e_2")
            decoded = py_value(self.lua.globals()[b"_EXPORT"][b"decode_shard"](shard.encode()))
            # decode_shard can also answer with several Lua values, which lupa
            # hands back as a tuple; .encode() on that is an AttributeError
            # rather than a decode failure.
            if isinstance(decoded, bytes):
                decoded = decoded.decode("utf-8", "replace")
            if isinstance(decoded, str):
                css = py_value(self.lua.globals()[b"_EXPORT"][b"sanitize_css"](decoded.encode()))
        except Exception:
            # Chapter CSS is optional; a stale/invalid e_2 shard must not
            # discard successfully decoded chapter text and annotations.
            css = ""
        # Nothing on the line above raises when sanitize_css returns several
        # values, so the tuple reached write_text() intact and failed the whole
        # chapter with "TypeError: data must be str, not tuple" -- one optional
        # stylesheet cost a book that had already downloaded.
        #
        # lupa hands a multi-value Lua result back as a tuple whose FIRST element
        # is the sanitized stylesheet; the rest are trailing values nobody reads.
        # Dropping the tuple threw away real CSS on every chapter of a finished
        # book, so take the first usable string and only degrade when there is
        # genuinely no text in there.
        if not isinstance(css, str):
            recovered = ""
            if isinstance(css, (tuple, list)):
                recovered = next((item for item in css
                                  if isinstance(item, str) and item.strip()), "")
            if recovered:
                print(f"[CSS] chapter {chapter['chapterUid']} stylesheet arrived as "
                      f"{type(css).__name__} of {len(css)} values; kept the first "
                      f"({len(recovered)} chars)", file=sys.stderr, flush=True)
                css = recovered
            else:
                print(f"[CSS] chapter {chapter['chapterUid']} stylesheet decoded to "
                      f"{type(css).__name__}, not text; continuing without it",
                      file=sys.stderr, flush=True)
                css = ""
        return xhtml, css

    def annotations(self, book_id: str, chapter_uid: Any,
                    want_thoughts: bool = True,
                    want_underlines: bool = True
                    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Fetch underlines and reviews. Neither is required for the chapter.

        Underlines and reviews are annotations layered on top of the text, so a
        failure here must cost the annotation, not the prose. Previously one bad
        batch of readreviews raised and discarded the chapter along with the
        batches that had already succeeded.

        want_underlines=False skips both calls entirely. That is the only way to
        get a plain-text export: want_thoughts=False still has to make the
        readreviews round trip, because that call is what carries the placement
        data. Skipping it here is safe precisely because the caller has said it
        does not want underlines in the first place.

        want_thoughts=False drops the NOTE TEXT but keeps the readreviews call.
        That call cannot be skipped, and this is the reason:

        /book/underlines returns only range, count, score and type -- no selected
        text of any kind. The plugin places a highlight by requiring a candidate
        quote to equal the landing text character for character, and the only
        quote it can get is the review's abstract. annotation_source.calibrate()
        likewise learns the chapter's API-to-XHTML offset from those abstracts,
        and with no sample it refuses the whole chapter outright.

        So the reviews round trip is load-bearing for UNDERLINES. Skipping it does
        not merely drop ideas; on a mid-chapter book it silently drops every
        highlight as well. What actually gets saved here is the write of the note
        bodies into reviews.jsonl and the inline [n] thought badges -- worth
        having on a 1,483-chapter book where thoughts run into the hundreds of
        megabytes of JSON, and the reason reviews are still fetched in batches.
        """
        if not want_underlines:
            # Caller asked for a plain-text export: no underline pass, no
            # readreviews round trip, nothing to inject. Cheaper and smaller than
            # --no-thoughts, which still pays for both calls to place highlights.
            return {"underlines": [], "chapterUid": chapter_uid}, []
        try:
            underlines = self.gateway("/book/underlines", {"bookId": book_id,
                                                               "chapterUid": chapter_uid})
        except Exception as exc:
            print(f"[ANNOTATION] chapter {chapter_uid}: underlines unavailable "
                  f"({type(exc).__name__}); keeping the text",
                  file=sys.stderr, flush=True)
            underlines = {"underlines": []}
        rows = underlines.get("underlines")
        if not isinstance(rows, list):
            print(f"[ANNOTATION] chapter {chapter_uid}: underline response was "
                  f"{type(rows).__name__}, not a list; keeping the text",
                  file=sys.stderr, flush=True)
            rows = []
            underlines["underlines"] = rows
        underlines["chapterUid"] = chapter_uid
        ranges = list(dict.fromkeys(str(row["range"]) for row in rows if row.get("range")))
        reviews: list[dict[str, Any]] = []
        for start in range(0, len(ranges), 30):
            if start: time.sleep(0.3)
            batch = [{"range": rg, "maxIdx": 0, "count": 30, "synckey": 0}
                     for rg in ranges[start:start + 30]]
            try:
                response = self.gateway("/book/readreviews", {"bookId": book_id,
                    "chapterUid": chapter_uid, "reviews": batch})
                batch_reviews = response.get("reviews")
            except Exception as exc:
                print(f"[ANNOTATION] chapter {chapter_uid}: reviews batch at {start} "
                      f"failed ({type(exc).__name__}); keeping the other batches",
                      file=sys.stderr, flush=True)
                continue
            if not isinstance(batch_reviews, list):
                print(f"[ANNOTATION] chapter {chapter_uid}: review batch at {start} "
                      f"was {type(batch_reviews).__name__}, not a list; skipped",
                      file=sys.stderr, flush=True)
                continue
            reviews.extend(batch_reviews)
        if not want_thoughts:
            reviews = strip_thought_bodies(reviews)
        return underlines, reviews

    def fetch_book(self, book_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        info = self.gateway("/book/info", {"bookId": book_id})
        book_info = info.get("book") or info.get("bookInfo") or info
        book = dict(book_info)
        book["bookId"] = str(book.get("bookId") or book_id)
        if "title" not in book: book["title"] = str(book_id)
        book.update(self.reader_state(book))
        referer = py_value(self.lua.globals()[b"_EXPORT"][b"reader_url"](
            book["bookId"].encode(), None))
        response = self.http.post(WEB + "/web/book/chapterInfos",
            json={"bookIds": [book["bookId"]]}, headers={"Referer": referer},
            timeout=(10, 45))
        response.raise_for_status()
        payload = response.json()
        # WeRead answers 200 with an error envelope instead of an HTTP status.
        # errCode -2010 is "用户未登录": the reader session has expired, and the
        # catalog is legitimately empty because of it. Reporting that as "no
        # readable chapters" sends the user hunting for a book problem they do
        # not have.
        if isinstance(payload, dict):
            err_code = payload.get("errCode")
            if err_code not in (None, 0, "0"):
                message = str(payload.get("errMsg") or "").strip()
                if err_code in (-2010, -2012, 2010) or "登录" in message:
                    raise RuntimeError(
                        f"WeRead session expired (errCode {err_code}"
                        f"{': ' + message if message else ''})."
                        " 请重新扫码登录后再试。")
                raise RuntimeError(
                    f"WeRead catalog error {err_code}"
                    f"{': ' + message if message else ''}.")
        records = payload.get("data", payload) if isinstance(payload, dict) else payload
        chapters = []
        for record in records if isinstance(records, list) else [records]:
            if isinstance(record, dict) and str(record.get("bookId", "")) == book["bookId"]:
                chapters = record.get("updated") or record.get("chapterInfos") or record.get("chapters") or []
                break
        chapters = [chapter for chapter in chapters
                    if int(chapter.get("wordCount", 0) or 0) > 0
                    and chapter.get("title") != "封面"]
        if not chapters: raise RuntimeError("No readable chapters in WeRead catalog")
        normalized = []
        for index, chapter in enumerate(chapters, 1):
            row = dict(chapter)
            original_uid = row.get("chapterUid") or row.get("chapterId")
            if original_uid is None or str(original_uid) == "": raise RuntimeError(f"Catalog chapter {index} has no UID")
            # Preserve the catalog's JSON scalar type for the gateway requests;
            # stringify only when building filenames/manifests.
            row["chapterUid"] = original_uid
            row.setdefault("chapterIdx", index)
            normalized.append(row)
        return book, normalized


def body_fragment(xhtml: str) -> str:
    remaining = xhtml
    bodies: list[str] = []
    while remaining:
        start = remaining.find("<body")
        if start < 0: break
        open_end = remaining.find(">", start)
        if open_end < 0: break
        close = remaining.find("</body>", open_end + 1)
        if close < 0:
            bodies.append(remaining[open_end + 1:])
            break
        bodies.append(remaining[open_end + 1:close])
        remaining = remaining[close + len("</body>"):]
    if bodies: return "\n".join(bodies)
    return re.sub(r"<\?xml.*?\?>|<!DOCTYPE.*?>", "", xhtml, flags=re.S)


def epub_escape(value: Any) -> str:
    return str(value or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def safe_name(value: str) -> str:
    value = re.sub(r'[\x00-\x1f\\/:*?"<>|]+', "_", value).strip().strip(".")
    return value[:120] or "WeRead"


def _image_extension(data: bytes) -> str | None:
    if data.startswith(b"\xff\xd8\xff"): return ".jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"): return ".png"
    if data[:6] in (b"GIF87a", b"GIF89a"): return ".gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP": return ".webp"
    return None


def _save_image(data: bytes, root: Path, used: set[str], stem: str) -> str | None:
    ext = _image_extension(data)
    if not ext: return None
    candidate = safe_name(Path(stem).stem) + ext
    index = 1
    while candidate.lower() in used or candidate.lower() in {"cover.jpg", "cover.jpeg", "cover.png", "cover.gif", "cover.webp"}:
        index += 1
        candidate = f"{safe_name(Path(stem).stem)}-{index}{ext}"
    used.add(candidate.lower())
    target = root / "images" / candidate
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return candidate


def extract_images(chapter: dict[str, Any], http: requests.Session, referer: str,
                   root: Path, used: set[str]) -> dict[str, str]:
    tar_url = chapter.get("tar")
    if not tar_url: return {}, []
    tar_url = urljoin(WEB + "/", tar_url)
    incoming_dir = root / "incoming"
    incoming_dir.mkdir(parents=True, exist_ok=True)
    archive_path = incoming_dir / f"{safe_name(str(chapter['chapterUid']))}.archive.part"
    total = 0
    dropped_archive_images: list[str] = []
    with http.get(tar_url, headers={"Referer": referer}, timeout=(10, 90),
                  stream=True) as response:
        # The image archive is an accessory, not the chapter. A 403/404/410 means
        # the artwork is gone, which is no reason to throw away text that has
        # already decoded -- the same reasoning as download_remote_images.
        if response.status_code in (403, 404, 410):
            print(f"[IMAGE] chapter {chapter['chapterUid']} has no image archive "
                  f"(HTTP {response.status_code}); keeping the text",
                  file=sys.stderr, flush=True)
            return {}, [f"archive:{tar_url}"]
        response.raise_for_status()
        with archive_path.open("wb") as output:
            for chunk in response.iter_content(64 * 1024):
                if not chunk: continue
                total += len(chunk)
                if total > ARCHIVE_LIMIT:
                    output.close()
                    archive_path.unlink(missing_ok=True)
                    raise RuntimeError("chapter image archive exceeds 128 MiB")
                output.write(chunk)
    archive_path.rename(archive_path.with_suffix(""))
    archive_path = archive_path.with_suffix("")
    mapped: dict[str, str] = {}
    try:
        try:
            with zipfile.ZipFile(archive_path) as archive:
                for info in archive.infolist():
                    if info.is_dir(): continue
                    if info.file_size > 32 * 1024 * 1024:
                        raise RuntimeError("chapter archive entry exceeds 32 MiB")
                    with archive.open(info) as source:
                        head = source.read(12)
                        ext = _image_extension(head)
                        if not ext: continue
                        target_name = _save_image_stream(source, head, root, used,
                                                         Path(info.filename).name,
                                                         str(chapter["chapterUid"]))
                        if target_name:
                            mapped[Path(info.filename).name] = "../images/" + target_name
                            mapped[target_name] = "../images/" + target_name
        except zipfile.BadZipFile:
            with tarfile.open(archive_path, mode="r:*") as archive:
                for member in archive.getmembers():
                    if not member.isfile(): continue
                    if member.size > 32 * 1024 * 1024:
                        raise RuntimeError("chapter archive entry exceeds 32 MiB")
                    source = archive.extractfile(member)
                    if not source: continue
                    with source:
                        head = source.read(12)
                        if not _image_extension(head): continue
                        target_name = _save_image_stream(source, head, root, used,
                                                         Path(member.name).name,
                                                         str(chapter["chapterUid"]))
                        if target_name:
                            mapped[Path(member.name).name] = "../images/" + target_name
                            mapped[target_name] = "../images/" + target_name
    finally:
        archive_path.unlink(missing_ok=True)
    return mapped, dropped_archive_images


def _save_image_stream(source: Any, head: bytes, root: Path,
                       used: set[str], stem: str, namespace: str) -> str | None:
    ext = _image_extension(head)
    if not ext: return None
    candidate = safe_name(namespace + "-" + Path(stem).stem) + ext
    suffix = 1
    reserved = {"cover.jpg", "cover.jpeg", "cover.png", "cover.gif", "cover.webp"}
    while candidate.lower() in used or candidate.lower() in reserved:
        suffix += 1
        candidate = f"{safe_name(namespace)}-{safe_name(Path(stem).stem)}-{suffix}{ext}"
    used.add(candidate.lower())
    target = root / "images" / candidate
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("wb") as output:
        output.write(head)
        shutil.copyfileobj(source, output, 64 * 1024)
        if output.tell() > IMAGE_LIMIT:
            target.unlink(missing_ok=True)
            raise RuntimeError("chapter image exceeds 16 MiB")
    return candidate


def download_remote_images(xhtml: str, http: requests.Session,
                           referer: str, root: Path,
                           used: set[str], namespace: str) -> tuple[str, dict[str, str]]:
    mapping: dict[str, str] = {}
    url_to_href: dict[str, str] = {}
    image_re = re.compile(r'(?i)(\bsrc\s*=\s*)(["\'])(.*?)(\2)')
    dropped: list[str] = []

    def replace(match: re.Match[str]) -> str:
        raw_url = match.group(3).replace("&amp;", "&")
        url = urljoin(WEB + "/", raw_url)
        if not url.startswith(("http://", "https://")): return match.group(0)
        if url in url_to_href:
            return match.group(1) + match.group(2) + url_to_href[url] + match.group(4)
        response = http.get(url, headers={"Referer": referer}, timeout=(10, 45), stream=True)
        # A missing or unreadable image must not cost the reader the chapter.
        # Some catalogue entries reference artwork that no longer exists
        # server-side (a 404 on every chapter of 盗墓笔记 25079310), and raising
        # here threw away text that had already decoded perfectly. Drop the
        # <img> and say so; the prose is the point.
        if response.status_code in (403, 404, 410):
            dropped.append(f"{response.status_code} {url}")
            print(f"[IMAGE] dropping an unavailable image "
                  f"(HTTP {response.status_code}): {url}", file=sys.stderr, flush=True)
            # Leave nothing behind: a src="" tag would make the reader fetch
            # the current document a second time.
            return ""
        response.raise_for_status()
        filename = safe_name(namespace) + "-remote-" + hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
        image_path = root / "incoming" / (filename + ".part")
        image_path.parent.mkdir(parents=True, exist_ok=True)
        total = 0
        with response, image_path.open("wb") as output:
            for chunk in response.iter_content(64 * 1024):
                if not chunk: continue
                total += len(chunk)
                if total > IMAGE_LIMIT: raise RuntimeError("remote image exceeds 16 MiB")
                output.write(chunk)
        ext = _image_extension(image_path.read_bytes()[:12])
        if not ext:
            # Not an image at all (an HTML error page, say). Same reasoning:
            # keep the chapter, drop the picture.
            image_path.unlink(missing_ok=True)
            dropped.append(f"not-an-image {url}")
            print(f"[IMAGE] dropping a response that is not an image: {url}",
                  file=sys.stderr, flush=True)
            return ""
        base = filename + ext
        suffix = 1
        while base.lower() in used:
            suffix += 1
            base = f"{filename}-{suffix}{ext}"
        used.add(base.lower())
        final_path = root / "images" / base
        final_path.parent.mkdir(parents=True, exist_ok=True)
        os.replace(image_path, final_path)
        href = "../images/" + base
        url_to_href[url] = href
        mapping[Path(url.split("?", 1)[0]).name] = href
        return match.group(1) + match.group(2) + href + match.group(4)
    result = image_re.sub(replace, xhtml)
    if dropped:
        # The substitution can only blank the src attribute, so two leftovers
        # need clearing: an <img src=""> that would re-request this document,
        # and the bare "<img/>" that results from removing the attribute.
        # Either way the prose is what matters, not the picture.
        result = re.sub(r'(?is)<img\b[^>]*\bsrc\s*=\s*(?:""|\'\')[^>]*>', "", result)
        result = re.sub(r"(?is)<img\b[^>]*/>", "", result)
    return result, mapping, dropped


def rewrite_images(xhtml: str, mapping: dict[str, str]) -> str:
    wrapper = lxml_html.fragment_fromstring(xhtml, create_parent="div")
    for image in wrapper.xpath(".//img[@src]"):
        source = image.get("src", "")
        base = Path(source.split("?", 1)[0]).name
        if base in mapping: image.set("src", mapping[base])
    pieces = [wrapper.text] if wrapper.text else []
    pieces.extend(etree.tostring(node, encoding="unicode", method="xml", with_tail=True)
                  for node in wrapper)
    return "".join(pieces)


def _cover_extension(data: bytes) -> tuple[str, str] | None:
    ext = _image_extension(data[:12])
    mime = {".jpg": "image/jpeg", ".png": "image/png", ".gif": "image/gif", ".webp": "image/webp"}
    return (ext, mime[ext]) if ext else None


def write_epub(path: Path, book: dict[str, Any], chapters: list[dict[str, Any]],
               chapter_paths: list[Path], css: str, assets: Path,
               cover_path: Path | None) -> None:
    if len(chapters) != len(chapter_paths): raise RuntimeError("chapter staging list mismatch")
    manifest = ['<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
                '<item id="style" href="style.css" media-type="text/css"/>']
    spine: list[str] = []
    metadata_entries = {
        "META-INF/container.xml": b'<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>',
    }
    for index, chapter in enumerate(chapters, 1):
        filename = f"text/chapter-{index:04d}.xhtml"
        chapter_id = f"ch{index}"
        title = epub_escape(chapter.get("title") or f"Chapter {index}")
        manifest.append(f'<item id="{chapter_id}" href="{filename}" media-type="application/xhtml+xml"/>')
        spine.append(f'<itemref idref="{chapter_id}"/>')
    cover_meta = ""
    cover_info = _cover_extension(cover_path.read_bytes()[:12]) if cover_path and cover_path.is_file() else None
    if cover_info:
        ext, mime = cover_info
        manifest.append(f'<item id="cover-image" href="images/cover{ext}" media-type="{mime}" properties="cover-image"/>')
        manifest.append('<item id="cover-page" href="text/cover.xhtml" media-type="application/xhtml+xml"/>')
        spine.insert(0, '<itemref idref="cover-page"/>')
        metadata_entries["OEBPS/text/cover.xhtml"] = (f'<?xml version="1.0" encoding="utf-8"?><html xmlns="http://www.w3.org/1999/xhtml"><head><title>Cover</title><link rel="stylesheet" href="../style.css"/></head><body><img src="../images/cover{ext}" alt="Cover"/></body></html>').encode()
        cover_meta = '<meta name="cover" content="cover-image"/>'
    asset_paths = sorted(p for p in assets.glob("*") if p.is_file())
    for index, asset in enumerate(asset_paths, 1):
        mime = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".gif": "image/gif", ".webp": "image/webp"}.get(asset.suffix.lower())
        if mime:
            manifest.append(f'<item id="asset{index}" href="images/{epub_escape(asset.name)}" media-type="{mime}"/>')
    nav = '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Contents</title></head><body><nav epub:type="toc" xmlns:epub="http://www.idpf.org/2007/ops"><ol>' + "".join(f'<li><a href="text/chapter-{index:04d}.xhtml">{epub_escape(chapter.get("title") or index)}</a></li>' for index, chapter in enumerate(chapters, 1)) + "</ol></nav></body></html>"
    metadata_entries["OEBPS/nav.xhtml"] = nav.encode("utf-8")
    metadata_entries["OEBPS/style.css"] = css.encode("utf-8")
    modified = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    opf = f'<?xml version="1.0" encoding="utf-8"?><package xmlns="http://www.idpf.org/2007/opf" unique-identifier="bookid" version="3.0"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="bookid">weread-{epub_escape(book["bookId"])}</dc:identifier><dc:title>{epub_escape(book.get("title"))}</dc:title><dc:creator>{epub_escape(book.get("author"))}</dc:creator><dc:publisher>WeRead</dc:publisher><dc:language>zh-CN</dc:language><meta property="dcterms:modified">{modified}</meta>{cover_meta}</metadata><manifest>{"".join(manifest)}</manifest><spine>{"".join(spine)}</spine></package>'
    metadata_entries["OEBPS/content.opf"] = opf.encode("utf-8")
    temp_path = path.with_suffix(path.suffix + ".part")
    with zipfile.ZipFile(temp_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
        archive.writestr("mimetype", b"application/epub+zip", compress_type=zipfile.ZIP_STORED)
        for name, data in metadata_entries.items(): archive.writestr(name, data, compress_type=zipfile.ZIP_DEFLATED)
        for index, chapter_path in enumerate(chapter_paths, 1):
            title = epub_escape(chapters[index - 1].get("title") or f"Chapter {index}")
            with archive.open(f"OEBPS/text/chapter-{index:04d}.xhtml", "w") as target, chapter_path.open("rb") as source:
                target.write((f'<?xml version="1.0" encoding="utf-8"?><html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><head><title>{title}</title><link rel="stylesheet" href="../style.css" type="text/css"/></head><body>').encode("utf-8"))
                shutil.copyfileobj(source, target, 64 * 1024)
                target.write(b"</body></html>")
        for asset in asset_paths:
            if asset.suffix.lower() in {".jpg", ".jpeg", ".png", ".gif", ".webp"}:
                archive.write(asset, "OEBPS/images/" + asset.name, compress_type=zipfile.ZIP_STORED)
        if cover_info and cover_path:
            archive.write(cover_path, "OEBPS/images/cover" + cover_info[0], compress_type=zipfile.ZIP_STORED)
    os.replace(temp_path, path)
    # Chapter XHTML is generated as a ZIP stream; staged bodies remain available
    # for resume and are not copied into a second full-book buffer.


def secure_output_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _set_private_acl(path, True)


def process_started_at(pid: int) -> float | None:
    if os.name != "nt": return None
    class FILETIME(ctypes.Structure):
        _fields_ = [("low", ctypes.c_uint32), ("high", ctypes.c_uint32)]
    handle = None
    try:
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.GetProcessTimes.argtypes = [ctypes.c_void_p] + [ctypes.POINTER(FILETIME)] * 4
        kernel.GetProcessTimes.restype = ctypes.c_int
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel.OpenProcess(0x1000, 0, pid)
        if not handle: return None
        created, exited, kernel_time, user_time = FILETIME(), FILETIME(), FILETIME(), FILETIME()
        if not kernel.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited), ctypes.byref(kernel_time), ctypes.byref(user_time)):
            return None
        ticks = (created.high << 32) | created.low
        return ticks / 10_000_000 - 11_644_473_600
    except Exception:
        return None
    finally:
        if handle:
            try: kernel.CloseHandle(handle)
            except Exception: pass


def process_is_alive(pid: int, started: int | None = None) -> bool:
    if pid <= 0: return False
    try:
        os.kill(pid, 0)
    except PermissionError:
        pass
    except OSError:
        return False
    if started and os.name == "nt":
        created = process_started_at(pid)
        # If Windows cannot query process age, conservatively retain the lock.
        if created is None: return True
        if abs(created - float(started)) > 5: return False
    return True


def discover_checkpoint_files(workspace: Path, chapters: list[dict[str, Any]]) -> dict[str, tuple[Path, Path, Path]]:
    """Find complete staged chapters by chapter UID, independent of catalog order."""
    allowed = {str(chapter["chapterUid"]) for chapter in chapters}
    found: dict[str, tuple[Path, Path, Path]] = {}
    for review_path in sorted(workspace.glob("chapter-*.json")):
        try:
            stem = review_path.name[:-5]
            row = json.loads(review_path.read_text(encoding="utf-8"))
            uid = str(row.get("chapter_uid", ""))
            body_path = workspace / (stem + ".body")
            css_path = workspace / (stem + ".css")
            if uid not in allowed or uid in found or not (body_path.is_file() and css_path.is_file()):
                continue
            ranges = int(row.get("underline_ranges", 0))
            markers = body_path.read_text(encoding="utf-8", errors="replace").count("wr-underline")
            if ranges and markers < ranges:
                continue
            found[uid] = (body_path, review_path, css_path)
        except (OSError, ValueError, TypeError):
            continue
    return found


#: Messages that mean "this chapter's payload came back empty or unusable this
#: time". WeRead sometimes answers a valid request with a shard set that decodes
#: to nothing (observed on UID 1194), and a fresh fetch usually succeeds, so these
#: get a bounded retry instead of ending the chapter on the first try.
TRANSIENT_CHAPTER_MARKERS = (
    "decoded without an XHTML body",
    "empty body despite catalog wordCount=",
    "SSLError", "EOF occurred", "timed out", "Timeout", "Connection aborted",
)
#: Attempts for a transient content/transport fault, including the first try.
CHAPTER_TRANSIENT_ATTEMPTS = 3
#: Backoff in seconds before retry 1, 2, ... (doubles each time).
CHAPTER_RETRY_BASE_DELAY = 2.0


def is_transient_chapter_fault(exc: BaseException) -> bool:
    """Is this chapter failure worth a bounded retry?

    Covers empty/no-body decodes and TLS/EOF/timeout transport errors. Paid
    placeholders are deliberately excluded: retrying cannot make a paid chapter
    readable, so that one stays a permanent, visible failure.
    """
    if isinstance(exc, (requests.Timeout, requests.ConnectionError)):
        return True
    text = f"{type(exc).__name__}: {exc}"
    return any(marker in text for marker in TRANSIENT_CHAPTER_MARKERS)


def run_chapter_with_retries(uid: str, attempt_once, throttle: "Throttle",
                             unresolved: dict[str, str],
                             sleep=None,
                             transient_attempts: int = CHAPTER_TRANSIENT_ATTEMPTS,
                             base_delay: float = CHAPTER_RETRY_BASE_DELAY) -> bool:
    """Run one chapter until it succeeds, is rate-limited out, or exhausts retries.

    Rate limits keep their own budget (Throttle.max_attempts). A transient
    content/transport fault gets at most `transient_attempts` tries in total,
    with exponential backoff between them. Anything else is permanent. Whatever
    is left over is recorded in `unresolved` so the caller refuses to publish an
    incomplete EPUB; nothing here drops a chapter silently.

    Returns True when the chapter completed.
    """
    attempt = 0
    transient = 0
    while attempt < throttle.max_attempts:
        throttle.wait_turn()
        try:
            attempt_once()
            throttle.note_success()
            return True
        except Exception as exc:
            reason = f"{type(exc).__name__}: {str(exc)[:240]}"
            # Keep the stack. The reason string alone says nothing about which
            # shard call failed, and an intermittent failure is otherwise
            # impossible to diagnose from the log.
            detail = traceback.format_exc()
            attempt += 1
            if is_rate_limited(exc):
                throttle.note_throttled()
                print(f"[THROTTLED] chapter UID {uid} hit a WeRead rate limit "
                      f"(attempt {attempt}/{throttle.max_attempts}); "
                      f"concurrency now {throttle.jobs}, backing off: {reason}",
                      flush=True)
                throttle.backoff()
                continue
            if is_transient_chapter_fault(exc):
                transient += 1
                if transient < transient_attempts:
                    delay = base_delay * (2 ** (transient - 1))
                    print(f"[RETRY] chapter UID {uid} transient attempt "
                          f"{transient}/{transient_attempts} failed; retrying in "
                          f"{delay:.0f}s: {reason}", flush=True)
                    (sleep or time.sleep)(delay)
                    continue
                reason = f"{reason} (gave up after {transient_attempts} attempts)"
            unresolved[uid] = reason
            kind = "THROTTLED" if throttle.throttled else "UNRESOLVED"
            print(f"[{kind}] chapter UID {uid} marked INCOMPLETE: {reason}; "
                  f"will continue with other UIDs", flush=True)
            print(detail, file=sys.stderr, flush=True)
            return False
    unresolved.setdefault(uid, "rate limit did not clear")
    return False


def acquire_export_lock(workspace: Path, book_id: str) -> Path:
    lock = workspace / "export.lock"
    for _attempt in range(2):
        try:
            descriptor = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            try:
                previous = json.loads(lock.read_text(encoding="utf-8"))
                pid = int(previous.get("pid", 0))
                started = int(previous.get("started", 0)) or None
            except (OSError, ValueError, TypeError):
                pid, started = 0, None
            if process_is_alive(pid, started):
                raise RuntimeError("An export for this book is already running")
            lock.unlink(missing_ok=True)
            continue
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump({"pid": os.getpid(), "book_id": book_id,
                       "started": int(time.time())}, stream)
        _set_private_acl(lock, False)
        return lock
    raise RuntimeError("Cannot acquire desktop export lock")


def probe_book(book_id: str, chapter_uid: str | None, settings: dict[str, Any],
               want_thoughts: bool = True) -> None:
    lua = lua_runtime()
    api = WeRead(settings, lua)
    book, chapters = api.fetch_book(book_id)
    chapter = next((row for row in chapters if str(row["chapterUid"]) == str(chapter_uid)), None) if chapter_uid else chapters[0]
    if chapter is None: raise RuntimeError("chapter UID is not present in the book catalog")
    raw_xhtml, _css = api.chapter_html(book, chapter)
    underlines, reviews = api.annotations(book_id, chapter["chapterUid"], want_thoughts,
                                                 want_underlines)
    injected, _annotation_css = lua.globals()[b"_EXPORT"][b"inject"](
        raw_xhtml.encode("utf-8"), lua_table(lua, underlines),
        lua_table(lua, reviews), str(book_id).encode("utf-8"))
    embedded = py_value(injected)
    pages = [page for row in reviews for page in (row.get("pageReviews") or [])]
    print("PROBE_OK; chapter_uid:", chapter["chapterUid"],
          "catalog_chapters:", len(chapters), "source_bytes:", len(raw_xhtml.encode("utf-8")),
          "has_xhtml_body:", bool(re.search(r"<body\b", raw_xhtml, re.I)),
          "price:", chapter.get("price", 0), "paid_flag:", chapter.get("paid", "unknown"),
          "underlines:", len(underlines.get("underlines", [])),
          "review_ranges:", len(reviews), "thought_pages:", len(pages),
          "want_thoughts:", "1" if want_thoughts else "0",
          "embedded_markers:", embedded.count("wr-underline"))


def book_info(book_id: str, settings: dict[str, Any]) -> None:
    lua = lua_runtime()
    api = WeRead(settings, lua)
    response = api.gateway("/book/info", {"bookId": str(book_id)})
    source = response.get("book") or response.get("bookInfo") or response
    allowed = ("bookId", "title", "author", "cover", "intro", "category", "publisher",
               "publishTime", "isbn", "newRating", "newRatingCount", "newRatingDetail", "deepLink")
    data = {key: source.get(key, "") for key in allowed}
    data["bookId"] = str(data.get("bookId") or book_id)
    print("BOOK_INFO\t" + json.dumps(data, ensure_ascii=False, separators=(",", ":")), flush=True)


def shelf_books(settings: dict[str, Any]) -> None:
    """Print the signed-in account's shelf as one JSON line.

    The shelf is the account's own library, so it is the honest content for a
    "我的书架" page: unlike the local export scan, these are books the user
    actually owns and can download, each with cover, author and category
    already resolved by the server (the search path has to fetch categories
    separately; the shelf does not).

    Output shape mirrors BOOK_INFO so the front end can reuse its parser:
        SHELF\t{json list of book dicts}
    """
    lua = lua_runtime()
    api = WeRead(settings, lua)
    response = api.gateway("/shelf/sync", {})
    books = response.get("books")
    if not isinstance(books, list):
        print("SHELF\t[]", flush=True)
        return
    allowed = ("bookId", "title", "author", "cover", "category", "intro",
               "newRating", "newRatingCount", "finishReading", "secret")
    rows = []
    for book in books:
        if not isinstance(book, dict):
            continue
        row = {key: book.get(key, "") for key in allowed}
        row["bookId"] = str(row.get("bookId") or "")
        if not row["bookId"]:
            continue
        rows.append(row)
    print("SHELF\t" + json.dumps(rows, ensure_ascii=False, separators=(",", ":")),
          flush=True)


RATE_LIMIT_STATUS = (426, 429, 403)


def is_rate_limited(exc: BaseException) -> bool:
    """Is this a WeRead rate limit rather than a fault in the chapter?

    The signature is a 426/429/403 from the reader endpoints. Everything else
    -- checksum mismatches, undecodable shards, empty bodies, paid chapters --
    is about the content itself and must be kept separate, because the two
    need opposite handling: a limit means "go slower and try again", a content
    fault means "skip this one and keep the book".
    """
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    if status in RATE_LIMIT_STATUS:
        return True
    text = str(exc)
    if any(f" {code} " in text or f"Error: {code}" in text
           for code in RATE_LIMIT_STATUS):
        return True
    return "Upgrade Required" in text or "Too Many Requests" in text


class Throttle:
    """Shared slow-down state for the chapter workers.

    WeRead does not advertise how long a limit lasts, so the recovery is
    deliberately conservative: drop concurrency, pause before the next request,
    and restore it gradually once requests start succeeding again.
    """

    MIN_JOBS = 1
    #: Unthrottled runs keep the old 3 attempts; a throttled one gets more,
    #: because the chapter was never the problem.
    BASE_ATTEMPTS = 3
    THROTTLED_ATTEMPTS = 8

    def __init__(self, jobs: int) -> None:
        self._lock = threading.Lock()
        self._top = max(self.MIN_JOBS, int(jobs))
        self.jobs = self._top
        self.throttled = False
        self._pause_until = 0.0
        self._consecutive_ok = 0

    @property
    def max_attempts(self) -> int:
        return self.THROTTLED_ATTEMPTS if self.throttled else self.BASE_ATTEMPTS

    def note_throttled(self) -> None:
        with self._lock:
            self.throttled = True
            self._consecutive_ok = 0
            if self.jobs > self.MIN_JOBS:
                self.jobs -= 1
                print(f"[THROTTLE] WeRead is rate limiting; concurrency "
                      f"{self._top} -> {self.jobs}", flush=True)

    def note_success(self) -> None:
        """Restore concurrency gradually once the limit appears to have passed."""
        with self._lock:
            if not self.throttled:
                return
            self._consecutive_ok += 1
            if self._consecutive_ok >= 20 and self.jobs < self._top:
                self.jobs += 1
                self._consecutive_ok = 0
                print(f"[THROTTLE] requests are passing again; concurrency "
                      f"-> {self.jobs}", flush=True)

    def backoff(self) -> None:
        """Pause the whole run before the next request is sent."""
        with self._lock:
            level = self._top - self.jobs + 1
            self._pause_until = time.monotonic() + min(30.0, 3.0 * level)

    def wait_turn(self) -> None:
        with self._lock:
            until = self._pause_until
        remaining = until - time.monotonic()
        if remaining > 0:
            time.sleep(remaining)


def find_book_workspaces(output: Path, book_id: str, max_depth: int = 2) -> list[Path]:
    """Every existing `<anything>-<book_id>.work` directory under `output`.

    Searched up to `max_depth` levels down, so a run started with --output set to
    a parent folder is still found. Only directory names are matched here.
    """
    suffix = f"-{book_id}.work"
    found: list[Path] = []
    pending: list[tuple[Path, int]] = [(output, 0)]
    while pending:
        folder, depth = pending.pop()
        try:
            children = [child for child in folder.iterdir() if child.is_dir()]
        except OSError:
            continue
        for child in children:
            if child.name.endswith(suffix) and len(child.name) > len(suffix):
                found.append(child)
            elif depth + 1 < max_depth:
                pending.append((child, depth + 1))
    return sorted(found)


def resolve_export_workspace(output: Path, book_id: str, package_name: str) -> Path:
    """Pick the `.work` directory a run should resume, or the one to create.

    An exact `<package_name>.work` under `output` wins, which is the original
    behaviour. Otherwise a single `*-<book_id>.work` beneath `output` is reused,
    so completed chapters are not fetched again. More than one candidate is an
    error: guessing could pick the wrong checkpoint or split one book in two.
    """
    exact = output / (package_name + ".work")
    if exact.is_dir():
        return exact
    candidates = find_book_workspaces(output, book_id)
    if len(candidates) == 1:
        print(f"[RESUME] Found existing workspace for book {book_id}: {candidates[0]}", flush=True)
        return candidates[0]
    if len(candidates) > 1:
        listing = "; ".join(str(path) for path in candidates)
        raise RuntimeError(
            f"{len(candidates)} workspaces match book {book_id} under {output}: {listing}. "
            "Keep one and move or delete the others, then run again."
        )
    return exact


def export_book(book_id: str, output: Path, settings: dict[str, Any], jobs: int = 1,
                want_thoughts: bool = True,
                want_underlines: bool = True) -> Path:
    secure_output_directory(output)
    lua = lua_runtime()
    api = WeRead(settings, lua)
    book, chapters = api.fetch_book(book_id)
    stem = safe_name(f"{book.get('title', book_id)} - full")
    package_name = safe_name(f"{book.get('title', book_id)}-{book_id}")
    workspace = resolve_export_workspace(output, book_id, package_name)
    package_dir = workspace.with_name(workspace.name[:-len(".work")])
    secure_output_directory(package_dir)
    secure_output_directory(workspace)
    workspace_images = workspace / "images"
    workspace_images.mkdir(parents=True, exist_ok=True)
    export_lock = acquire_export_lock(workspace, book_id)
    atexit.register(lambda lock=export_lock: lock.unlink(missing_ok=True))
    state_path = workspace / "state.json"
    chapter_uids = [str(chapter["chapterUid"]) for chapter in chapters]
    state = {"version": 1, "book_id": book_id, "title": book.get("title"),
             "total": len(chapters), "chapter_uids": chapter_uids, "completed": 0,
             "want_thoughts": bool(want_thoughts)}
    if state_path.exists():
        try:
            old = json.loads(state_path.read_text(encoding="utf-8"))
            if old.get("book_id") == book_id:
                state.update(old)
                state["total"] = len(chapters)
                state["chapter_uids"] = chapter_uids
                # A checkpoint written by the other mode cannot be resumed into this
                # one: its chapters already carry (or lack) the thought markers that
                # this run would add or skip, so merging them yields a book whose
                # notes depend on download order. Refuse the mix and start clean.
                if bool(old.get("want_thoughts", True)) != bool(want_thoughts):
                    print(f"[THOUGHTS] checkpoint was written with "
                          f"want_thoughts={bool(old.get('want_thoughts', True))}, "
                          f"this run asks for {bool(want_thoughts)}; re-exporting "
                          f"from scratch rather than mixing two modes", flush=True)
                    for stale in list(workspace.glob("chapter-uid-*")):
                        stale.unlink(missing_ok=True)
                    state["completed"] = 0
                    state["want_thoughts"] = bool(want_thoughts)
                state["want_thoughts"] = bool(want_thoughts)
        except (OSError, ValueError):
            pass
    cached_by_uid = discover_checkpoint_files(workspace, chapters)
    chapter_paths: list[Path] = []
    review_paths: list[Path] = []
    css_paths: list[Path] = []
    for chapter in chapters:
        uid = str(chapter["chapterUid"])
        cached = cached_by_uid.get(uid)
        if cached:
            body_path, review_path, css_path = cached
        else:
            token = safe_name(uid)
            body_path = workspace / f"chapter-uid-{token}.body"
            review_path = workspace / f"chapter-uid-{token}.json"
            css_path = workspace / f"chapter-uid-{token}.css"
        chapter_paths.append(body_path)
        review_paths.append(review_path)
        css_paths.append(css_path)
    styles: list[str] = []
    style_set: set[str] = set()
    thread_context = threading.local()
    state_lock = threading.Lock()

    def download_one(index: int, chapter: dict[str, Any],
                     want_underlines: bool = True) -> None:
        body_path = chapter_paths[index - 1]
        review_path = review_paths[index - 1]
        css_path = css_paths[index - 1]
        if body_path.is_file() and review_path.is_file() and css_path.is_file():
            try:
                cached = json.loads(review_path.read_text(encoding="utf-8"))
                if str(cached.get("chapter_uid")) == str(chapter["chapterUid"]): return
            except (OSError, ValueError, TypeError):
                pass
        print(f"[{index}/{len(chapters)}] Fetching chapter UID {chapter['chapterUid']}", file=sys.stderr, flush=True)
        if not hasattr(thread_context, "api"):
            thread_context.lua = lua_runtime()
            thread_context.api = WeRead(settings, thread_context.lua)
        worker_api, worker_lua = thread_context.api, thread_context.lua
        raw_xhtml, chapter_css = worker_api.chapter_html(book, chapter)
        validate_chapter_body(raw_xhtml, chapter)
        if len(raw_xhtml.encode("utf-8")) < 1024 and int(chapter.get("price", 0) or 0) > 0 and int(chapter.get("paid", 0) or 0) == 0:
            raise RuntimeError(
                f"Chapter {chapter['chapterUid']} is marked paid ({chapter.get('price')}) but returned only a placeholder body; credentials/access did not provide the paid text"
            )
        underlines, reviews = worker_api.annotations(book_id, chapter["chapterUid"], want_thoughts,
                                                     want_underlines)
        injected_xhtml, annotation_css = worker_lua.globals()[b"_EXPORT"][b"inject"](
            raw_xhtml.encode("utf-8"), lua_table(worker_lua, underlines),
            lua_table(worker_lua, reviews), book_id.encode("utf-8"))
        processed = py_value(injected_xhtml)
        if not re.search(r"<body\b", processed, re.I):
            raise RuntimeError(f"Chapter {chapter['chapterUid']} decoded without an XHTML body; refusing to export malformed content")
        # Audit the same raw XHTML inject() consumed, never `processed`: the
        # plugin treats already-injected HTML as a no-op, so auditing the output
        # would return an empty record list and read as a clean chapter with no
        # underlines at all. Costs a second placement pass per chapter, on the
        # same HTML, with no second network call.
        underline_audit = summarize_underline_audit(worker_lua, raw_xhtml, underlines,
                                                    reviews, book_id, chapter["chapterUid"])
        chapter_css = py_value(annotation_css) or chapter_css or ""
        underline_rows = underlines.get("underlines", [])
        expected_ranges = len({str(row.get("range")) for row in underline_rows if row.get("range")})
        # Measure what actually landed, by identity rather than by arithmetic.
        #
        # The old figure was expected_ranges - processed.count("wr-underline").
        # Those two numbers are not the same kind of thing: the plugin wraps a
        # span per text segment, so one underline can emit several markers while
        # one marker can cover a range the API reported differently. On 斗破苍穹
        # the marker count (55532) EXCEEDED the range count (53840), which made
        # the subtraction report a negative gap for a book with nothing missing
        # and a positive gap for books that were fine. Every "N underlines were
        # dropped" number this tool has reported was an artefact of that.
        #
        # Each injected underline carries a server-authoritative id in its href,
        # #wrthought-<bookId>-<chapterUid>-<start>-<end>, and <start>-<end> is
        # exactly the API's range string. Comparing the two SETS is therefore an
        # exact, auditable answer to "which underlines did not make it".
        expected_ids = {str(row.get("range")) for row in underline_rows
                        if row.get("range") and _UNDERLINE_ID_RE.fullmatch(
                            str(row.get("range")).strip())}
        embedded_ids = set(_UNDERLINE_HREF_RE.findall(processed))
        missing_ids = sorted(expected_ids - embedded_ids)
        embedded_markers = processed.count("wr-underline")
        dropped_ranges = len(missing_ids)
        if dropped_ranges:
            # Not a failure, and not a guess: the plugin declines to place an
            # underline whose offset it cannot prove (annotations.lua refuses a
            # bare-range fallback so it never highlights the wrong words). The
            # text is complete; these highlights are simply not carried over,
            # and the id list is kept so the claim can be checked.
            print(f"[ANNOTATION] chapter UID {chapter['chapterUid']}: text kept, "
                  f"{dropped_ranges} of {expected_ranges} underline(s) not placed by "
                  f"WeRead: {', '.join(missing_ids[:6])}"
                  f"{' ...' if dropped_ranges > 6 else ''}", flush=True)
        referer = py_value(worker_lua.globals()[b"_EXPORT"][b"reader_url"](
            book_id.encode(), str(chapter["chapterUid"]).encode()))
        # Prefix archive image names with chapter UID: workers can download in
        # parallel without sharing mutable collision maps.
        used_images = {image.name.lower() for image in workspace_images.iterdir() if image.is_file()}
        image_map, archive_dropped = extract_images(chapter, worker_api.http, referer, workspace, used_images)
        processed, remote_map, remote_dropped = download_remote_images(processed, worker_api.http, referer, workspace, used_images, str(chapter["chapterUid"]))
        image_map.update(remote_map)
        # Record every resource that is genuinely gone so the manifest can say
        # so. Degrading is only better than failing if the loss is visible.
        dropped_image_urls = list(archive_dropped) + list(remote_dropped)
        dropped_image_count = len(dropped_image_urls)
        body = rewrite_images(body_fragment(processed), image_map)
        temp_body = body_path.with_suffix(".body.part")
        temp_review = review_path.with_suffix(".json.part")
        temp_css = css_path.with_suffix(".css.part")
        temp_body.write_text(body, encoding="utf-8")
        temp_review.write_text(json.dumps({"chapter_uid": str(chapter["chapterUid"]), "reviews": reviews,
            "underline_ranges": expected_ranges, "embedded_markers": embedded_markers,
            # The exact ids that did not land, so the omission can be audited
            # rather than taken on the count's word.
            "missing_underline_ids": missing_ids[:20],
            "dropped_underline_ranges": dropped_ranges,
            # Placement graded by text, from the plugin's own audit pass. Null
            # means the audit did not run, which is NOT the claim that nothing
            # was wrong: an id that reached the page still does not prove the
            # highlight sits on the words WeRead meant.
            **underline_audit,
            # Counted here so the manifest can state exactly what the book is
            # missing, instead of the loss being invisible to the reader.
            "dropped_images": dropped_image_count,
            "dropped_image_urls": dropped_image_urls[:5]}, ensure_ascii=False), encoding="utf-8")
        temp_css.write_text(chapter_css, encoding="utf-8")
        os.replace(temp_body, body_path)
        os.replace(temp_review, review_path)
        os.replace(temp_css, css_path)
        with state_lock:
            state["completed"] = int(state.get("completed", 0)) + 1
            atomic_text(state_path, json.dumps(state, ensure_ascii=False))
        worker_api.persist_cookies()

    pending: list[tuple[int, dict[str, Any]]] = []
    cached_count = 0
    for index, chapter in enumerate(chapters, 1):
        body_path = chapter_paths[index - 1]
        review_path = review_paths[index - 1]
        css_path = css_paths[index - 1]
        valid_cached = cached_by_uid.get(str(chapter["chapterUid"])) == (body_path, review_path, css_path)
        if valid_cached:
            cached_count += 1
            chapter_css = css_path.read_text(encoding="utf-8")
            if chapter_css and chapter_css not in style_set:
                styles.append(chapter_css); style_set.add(chapter_css)
        else:
            pending.append((index, chapter))
    state["completed"] = cached_count
    atomic_text(state_path, json.dumps(state, ensure_ascii=False))
    print(f"[RESUME] Reusing {cached_count} complete chapter(s) by UID; {len(pending)} remain", flush=True)
    jobs = min(max(1, int(jobs)), 4, max(1, len(pending)))
    unresolved: dict[str, str] = {}

    # Rate limiting is not a content fault. WeRead answers 426/429/403 when a
    # burst of chapter requests trips a limit, and treating that like a bad
    # chapter is what silently dropped 183 chapters: they were marked
    # UNRESOLVED and never retried, so the EPUB was refused even though the
    # only problem was that we asked too fast.
    #
    # Throttling state is shared by every worker, so one chapter hitting the
    # limit slows the whole run down rather than just its own thread.
    throttle = Throttle(jobs)

    def fetch_with_retries(index: int, chapter: dict[str, Any]) -> None:
        uid = str(chapter["chapterUid"])
        run_chapter_with_retries(uid, lambda: download_one(index, chapter,
                                                          want_underlines),
                                 throttle, unresolved)

    if jobs == 1:
        for index, chapter in pending:
            fetch_with_retries(index, chapter)
    else:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        with ThreadPoolExecutor(max_workers=jobs, thread_name_prefix="weread-chapter") as pool:
            futures = [pool.submit(fetch_with_retries, index, chapter)
                       for index, chapter in pending]
            for future in as_completed(futures):
                future.result()
    failed_path = workspace / "failed_chapters.json"
    if unresolved:
        atomic_text(failed_path, json.dumps(unresolved, ensure_ascii=False, indent=2))
        if throttle.throttled:
            # Say which of the two it was. "WeRead rate limiting did not clear"
            # and "these chapters are broken" call for completely different
            # responses from the user: wait and rerun, versus re-login.
            print(f"[THROTTLED] WeRead rate limiting did not clear before the run "
                  f"finished; {len(unresolved)} chapter(s) are missing for that "
                  f"reason. The checkpoint is intact -- run again to continue.",
                  flush=True)
        else:
            print(f"[INCOMPLETE] Scan reached all pending UIDs; {len(unresolved)} "
                  f"unresolved chapter(s) remain. No EPUB/bundle will be published.",
                  flush=True)
        raise RuntimeError(f"{len(unresolved)} chapter(s) remain unresolved; checkpoint is retained and EPUB publication was refused")
    else:
        failed_path.unlink(missing_ok=True)
    annotation_coverage = {"underline_ranges": 0, "embedded_markers": 0, "chapters_with_underlines": 0,
                          "dropped_underline_ranges": 0, "chapters_with_dropped_underlines": 0}
    dropped_images = 0
    chapters_with_dropped_images = 0
    dropped_image_samples: list[str] = []
    missing_underline_samples: list[str] = []
    # Identity and text answer two different questions. Identity asks whether the
    # underline's id reached the page; text asks whether the words it landed on
    # are the words WeRead meant, which is the only version of the question that
    # can see a highlight painted a few characters off. Kept in its own counters
    # because a null in one must not be summed as a zero in the other.
    audit = {"measured_chapters": 0, "unmeasured_chapters": 0,
             "verified_underlines": 0, "unverified_underlines": 0,
             "refused_underlines": 0, "chapters_with_unverified_underlines": 0,
             "chapters_with_refused_underlines": 0, "refused_underline_reasons": {}}
    quote_mismatch_samples: list[dict[str, str]] = []
    for index in range(1, len(chapters) + 1):
        chapter_css = css_paths[index - 1].read_text(encoding="utf-8")
        if chapter_css and chapter_css not in style_set:
            styles.append(chapter_css); style_set.add(chapter_css)
        row = json.loads(review_paths[index - 1].read_text(encoding="utf-8"))
        annotation_coverage["underline_ranges"] += int(row.get("underline_ranges", 0))
        annotation_coverage["embedded_markers"] += int(row.get("embedded_markers", 0))
        dropped = int(row.get("dropped_underline_ranges", 0) or 0)
        annotation_coverage["dropped_underline_ranges"] += dropped
        if dropped: annotation_coverage["chapters_with_dropped_underlines"] += 1
        for ident in (row.get("missing_underline_ids") or [])[:2]:
            if ident not in missing_underline_samples:
                missing_underline_samples.append(ident)
        gone = int(row.get("dropped_images", 0) or 0)
        dropped_images += gone
        if gone:
            chapters_with_dropped_images += 1
            for name in (row.get("dropped_image_urls") or [])[:3]:
                if name not in dropped_image_samples:
                    dropped_image_samples.append(name)
        if int(row.get("underline_ranges", 0)) > 0: annotation_coverage["chapters_with_underlines"] += 1
        # A chapter that was never audited contributes nothing and is counted as
        # unmeasured. Reading its nulls as zeros would let a book nobody checked
        # report itself as verified.
        verified = row.get("verified_underlines")
        if not isinstance(verified, int):
            audit["unmeasured_chapters"] += 1
        else:
            audit["measured_chapters"] += 1
            unverified = int(row.get("unverified_underlines") or 0)
            refused = int(row.get("refused_underlines") or 0)
            audit["verified_underlines"] += verified
            audit["unverified_underlines"] += unverified
            audit["refused_underlines"] += refused
            if unverified: audit["chapters_with_unverified_underlines"] += 1
            if refused: audit["chapters_with_refused_underlines"] += 1
            for reason, count in (row.get("refused_underline_reasons") or {}).items():
                audit["refused_underline_reasons"][reason] = (
                    audit["refused_underline_reasons"].get(reason, 0) + int(count or 0))
            for sample in (row.get("quote_mismatch_samples") or []):
                if len(quote_mismatch_samples) < _AUDIT_SAMPLE_LIMIT:
                    quote_mismatch_samples.append(sample)
    if annotation_coverage["dropped_underline_ranges"]:
        # Say it plainly rather than letting the user find out later: the text
        # is complete, but these specific highlights are not carried over.
        print(f"[ANNOTATION] {annotation_coverage['dropped_underline_ranges']} underline(s) "
              f"across {annotation_coverage['chapters_with_dropped_underlines']} chapter(s) "
              f"were not placed in the text. Every chapter's prose is intact; only "
              f"these highlights are missing, because WeRead would not supply an "
              f"anchor this text could verify, and an unverified anchor would "
              f"highlight the wrong words.", flush=True)
    if dropped_images:
        print(f"[ANNOTATION] {dropped_images} image(s) across "
              f"{chapters_with_dropped_images} chapter(s) are no longer served by "
              f"WeRead and were left out. Every chapter's text is complete.", flush=True)
    # The split the identity measurement cannot produce, on one line, because a
    # human running the app should not have to open a JSON file to learn whether
    # any highlight is sitting on the wrong words.
    if audit["measured_chapters"]:
        scope = f"{audit['measured_chapters']} chapter(s)"
        if audit["unmeasured_chapters"]:
            scope += f", {audit['unmeasured_chapters']} not measured"
        reasons = ", ".join(f"{reason}={count}" for reason, count
                            in sorted(audit["refused_underline_reasons"].items()))
        print(f"[ANNOTATION] Underline placement checked against the quoted text: "
              f"{audit['verified_underlines']} verified, "
              f"{audit['unverified_underlines']} placed but unproven, "
              f"{audit['refused_underlines']} refused, over {scope}"
              f"{'; refused by reason: ' + reasons if reasons else ''}.", flush=True)
    else:
        print(f"[ANNOTATION] Underline placement was NOT checked against the "
              f"quoted text on any of the {len(chapters)} chapter(s): every "
              f"highlight in this book is unverified, and one sitting a few "
              f"characters off the intended words would look identical to a "
              f"correct one here.", flush=True)
    # One honest, machine-readable statement of what this book is missing. A
    # reader who is not told that images or highlights went missing will assume
    # the book is whole, and degraded output is only better than failed output
    # if the loss is stated.
    omission_report = {
        "text_complete": True,
        "underline_ranges_expected": annotation_coverage["underline_ranges"],
        "underline_marker_spans": annotation_coverage["embedded_markers"],
        "underlines_not_placed": annotation_coverage["dropped_underline_ranges"],
        "chapters_with_unplaced_underlines": annotation_coverage["chapters_with_dropped_underlines"],
        # Kept under the old keys too: an older GUI build may still read them.
        # underline_ranges_dropped alone was not enough -- describe_omissions()
        # also asks for chapters_with_dropped_underlines, and omitting that one
        # made every finished book claim the underlines were missing from 0
        # chapters while the log said 8.
        "underline_ranges_dropped": annotation_coverage["dropped_underline_ranges"],
        "chapters_with_dropped_underlines": annotation_coverage["chapters_with_dropped_underlines"],
        "missing_underline_examples": missing_underline_samples,
        # Placement graded by text. Every figure below is null when no chapter
        # was audited at all, because "nobody checked" and "everything matched"
        # are different facts and only the second may read as a clean book.
        # underlines_not_placed above is the identity view: it can be larger or
        # smaller than underlines_refused_by_audit, because a row whose range
        # does not parse is a refusal (bad_range) that identity cannot see -- it
        # has no id to look for -- while a placed-and-mismatched one would appear
        # under underlines_unverified_by_text instead.
        "underline_text_audit_measured": bool(audit["measured_chapters"]
                                              and not audit["unmeasured_chapters"]),
        "underlines_verified_by_text": audit["verified_underlines"] if audit["measured_chapters"] else None,
        "underlines_unverified_by_text": audit["unverified_underlines"] if audit["measured_chapters"] else None,
        "underlines_refused_by_audit": audit["refused_underlines"] if audit["measured_chapters"] else None,
        "chapters_with_unverified_underlines": audit["chapters_with_unverified_underlines"] if audit["measured_chapters"] else None,
        "chapters_with_refused_underlines": audit["chapters_with_refused_underlines"] if audit["measured_chapters"] else None,
        "underlines_refused_by_reason": audit["refused_underline_reasons"] if audit["measured_chapters"] else None,
        "underline_mismatch_examples": quote_mismatch_samples if audit["measured_chapters"] else None,
        "images_dropped": dropped_images,
        "chapters_with_dropped_images": chapters_with_dropped_images,
        "dropped_image_examples": dropped_image_samples,
        "reason_underlines": "WeRead reported these highlights but would not supply an "
                            "anchor this chapter's text could verify, so they were not "
                            "placed. An unverified anchor would highlight the wrong words. "
                            "This is deliberate, not a download failure.",
        "reason_images": "WeRead no longer serves these images (HTTP 403/404/410).",
        "reason_placement_audit": "underlines_verified_by_text counts highlights "
                                  "whose marked text equals the quote WeRead holds, "
                                  "proved by the plugin's own placement pass; "
                                  "underlines_unverified_by_text counts highlights "
                                  "that landed but could not be proved to sit on "
                                  "those words; underlines_refused_by_audit counts "
                                  "highlights WeRead reported that were deliberately "
                                  "not painted, broken down in "
                                  "underlines_refused_by_reason. All are null when "
                                  "the audit did not run.",
    }
    css = "\n".join(styles) + "\nbody{line-height:1.7;margin:5%}img{max-width:100%}.wr-underline{border-bottom:2px dashed #666;padding-bottom:2px}.wr-thought-count{font-size:.8em;color:#000;border:1px solid #777;padding:0 2px;margin-left:3px;vertical-align:super;font-weight:bold}.wr-thought-link{text-decoration:none;color:inherit}"
    cover_path = None
    if book.get("cover"):
        cover_path = workspace / "cover.image"
        try:
            response = api.http.get(book["cover"], timeout=(10, 30), stream=True)
            response.raise_for_status()
            with cover_path.open("wb") as target:
                total = 0
                for chunk in response.iter_content(64 * 1024):
                    if not chunk: continue
                    total += len(chunk)
                    if total > IMAGE_LIMIT: raise RuntimeError("cover exceeds image size limit")
                    target.write(chunk)
            if not _cover_extension(cover_path.read_bytes()[:12]): cover_path.unlink(missing_ok=True); cover_path = None
        except requests.RequestException:
            cover_path = None
    epub_path = package_dir / (stem + ".epub")
    write_epub(epub_path, book, chapters, chapter_paths, css, workspace_images, cover_path)
    manifest = {"version": 1, "book_id": book_id, "title": book.get("title"),
        "author": book.get("author", ""), "epub": epub_path.name,
        "epub_size": epub_path.stat().st_size,
        "chapters": [{"chapterUid": str(ch["chapterUid"]), "title": ch.get("title"),
                      "chapterIdx": ch.get("chapterIdx", index)}
                     for index, ch in enumerate(chapters, 1)],
        "review_file": "reviews.jsonl", "embedded": True,
        "annotation_coverage": annotation_coverage,
        # What this book does NOT contain, stated plainly. Degrading instead of
        # failing means the text is kept, but a reader who is not told that
        # images or highlights went missing will assume the book is whole.
        "omissions": omission_report}
    atomic_text(package_dir / "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    review_file = package_dir / "reviews.jsonl"
    review_temp = review_file.with_suffix(".jsonl.part")
    with review_temp.open("w", encoding="utf-8", newline="\n") as output_reviews:
        for local_index, chapter in enumerate(chapters, 1):
            row_path = review_paths[local_index - 1]
            if row_path is None or not row_path.is_file(): raise RuntimeError("chapter review checkpoint is missing")
            row = json.loads(row_path.read_text(encoding="utf-8"))
            if str(row.get("chapter_uid")) != str(chapter["chapterUid"]): raise RuntimeError("chapter review checkpoint UID mismatch")
            output_reviews.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    os.replace(review_temp, review_file)
    descriptor = {"version": 1, "manifest": "manifest.json", "reviews": "reviews.jsonl", "epub": epub_path.name}
    atomic_text(package_dir / "bundle.json", json.dumps(descriptor, ensure_ascii=False, indent=2))
    bundle_path = package_dir / (stem + ".weread.zip")
    bundle_temp = bundle_path.with_suffix(".zip.part")
    with zipfile.ZipFile(bundle_temp, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.write(epub_path, epub_path.name)
        archive.write(package_dir / "manifest.json", "manifest.json")
        archive.write(review_file, "reviews.jsonl")
        archive.write(package_dir / "bundle.json", "bundle.json")
    os.replace(bundle_temp, bundle_path)
    export_lock.unlink(missing_ok=True)
    # The GUI reads this to tell the user exactly what the finished book is
    # missing. Degrading instead of failing is only honest if the loss is
    # reported, so it goes over the same channel as the result itself.
    print("OMISSIONS", json.dumps(omission_report, ensure_ascii=False, separators=(",", ":")), flush=True)
    print("EPUB", epub_path)
    print("IMPORT_BUNDLE", bundle_path)
    return bundle_path


def main() -> int:
    force_utf8_stdio()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("book_id", nargs="?", help="WeRead book id")
    parser.add_argument("--check-auth", action="store_true", help="Validate the private WeRead credentials without downloading a book")
    parser.add_argument("--search", help="Search WeRead and print matching book IDs")
    parser.add_argument("--book-info", help="Fetch safe public metadata for one book without downloading")
    parser.add_argument("--probe-book", help="Fetch one chapter and validate the live protocol/annotation path without creating an export")
    parser.add_argument("--probe-chapter", help="Chapter UID for --probe-book (default: first readable chapter)")
    parser.add_argument("--shelf", action="store_true",
                        help="List the signed-in account's shelf as JSON (for 我的书架)")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--jobs", type=int, default=1, help="Concurrent chapter workers (1-4; default 1 for LuaJIT stability)")
    parser.add_argument("--no-thoughts", action="store_true",
                        help="Skip idea/review fetching. Underlines are still placed; "
                             "only the idea text and [n] badges are omitted. Much "
                             "faster on heavily annotated books.")
    parser.add_argument("--no-annotations", action="store_true",
                        help="Skip annotations entirely: no underlines, no thoughts. "
                             "Skips the underline and readreviews calls altogether, so "
                             "it is the fastest and smallest of the three modes. Plain "
                             "text only -- the exported EPUB has no highlights. Implies "
                             "--no-thoughts.")
    args = parser.parse_args()
    want_thoughts = not args.no_thoughts
    want_underlines = not args.no_annotations
    if not want_underlines:
        # No underlines means there is nothing for a thought to hang off, so the
        # note-text switch would be meaningless. Say so once rather than letting
        # --no-annotations --no-thoughts look like two separate decisions.
        want_thoughts = False
    if not args.check_auth and not args.search and not args.book_info and not args.probe_book and not args.shelf and not args.book_id:
        parser.error("book_id is required unless --check-auth, --search, or --probe-book is used")
    if args.jobs < 1 or args.jobs > 4:
        parser.error("--jobs must be between 1 and 4")
    try:
        settings = load_credentials()
        lua = lua_runtime()
        api = WeRead(settings, lua)
        if args.check_auth:
            response = api.gateway("/shelf/sync", {})
            shelf = response.get("books", [])
            print("AUTH_OK; shelf entries:", len(shelf) if isinstance(shelf, list) else "available")
            return 0
        if args.search:
            response = api.gateway("/store/search", {"keyword": args.search, "count": 20})
            books: list[dict[str, Any]] = []
            seen: set[str] = set()
            for group in response.get("results", []):
                if not isinstance(group, dict):
                    continue
                for entry in group.get("books") or []:
                    if not isinstance(entry, dict):
                        continue
                    book = entry.get("bookInfo") or entry
                    if not isinstance(book, dict):
                        continue
                    book_id = clean_field(book.get("bookId"))
                    if not book_id or book_id in seen:
                        continue
                    seen.add(book_id)
                    books.append(dict(book))
            for book in books:
                print(search_row(book), flush=True)
            # Cards render immediately; categories follow when they arrive.
            enrich_search_categories(api, books)
            for book in books:
                print(search_meta(book.get("bookId"), book.get("category")), flush=True)
            return 0
        if args.book_info:
            book_info(args.book_info, settings)
            return 0
        if args.probe_book:
            probe_book(args.probe_book, args.probe_chapter, settings, want_thoughts,
             want_underlines)
            return 0
        if args.shelf:
            shelf_books(settings)
            return 0
        result = export_book(args.book_id, args.output, settings, args.jobs, want_thoughts,
                       want_underlines)
        print("DONE", result, flush=True)
        return 0
    except Exception as exc:
        print("FAILED", type(exc).__name__, str(exc)[:300], file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
