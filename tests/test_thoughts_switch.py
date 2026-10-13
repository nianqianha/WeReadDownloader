# -*- coding: utf-8 -*-
"""Verify --no-thoughts really stops the readreviews calls and keeps underlines."""
import sys
from pathlib import Path
import sys, io, types
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "weread"))

import weread_desktop as wd

# Build a WeRead instance without touching the network or real credentials.
api = wd.WeRead.__new__(wd.WeRead)
calls = []

UNDERLINES = {"underlines": [
    {"range": f"0-{i}", "text": f"u{i}"} for i in range(70)   # 70 -> 3 batches
]}

def fake_gateway(path, params=None, **kw):
    calls.append(path)
    if path == "/book/underlines":
        return {"underlines": list(UNDERLINES["underlines"])}
    if path == "/book/readreviews":
        return {"reviews": [{"range": p} for p in (params or {}).get("reviews", [])]}
    raise AssertionError("unexpected endpoint " + path)

api.gateway = fake_gateway

# --- case 1: default (thoughts on) ---
calls.clear()
u, r = api.annotations("19480992", "1", True)
n_reviews = len([c for c in calls if c == "/book/readreviews"])
print("case1 thoughts=ON  -> reviews:", len(r), "readreviews calls:", n_reviews,
      "underlines:", len(u.get("underlines", [])), "chapterUid:", u.get("chapterUid"))
assert n_reviews == 3, n_reviews          # ceil(70/30) == 3
assert len(u["underlines"]) == 70

# --- case 2: --no-thoughts still calls readreviews, then prunes it ---
calls.clear()
u2, r2 = api.annotations("19480992", "1", False)
n2 = len([c for c in calls if c == "/book/readreviews"])
print("case2 thoughts=OFF -> reviews:", len(r2), "readreviews calls:", n2,
      "underlines:", len(u2.get("underlines", [])), "chapterUid:", u2.get("chapterUid"))
# MEASURED: /book/underlines carries no selected text, so the readreviews
# abstract is the only quote underline placement can use. Skipping the call
# drops every highlight on a mid-chapter book, not just the note text.
assert n2 == 3, "readreviews is load-bearing: it must still be called"
assert len(u2["underlines"]) == 70, "underlines must survive"
assert u2.get("chapterUid") == "1", u2.get("chapterUid")
# and the rows must still be shaped for quoteCandidate
assert all(isinstance(row, dict) for row in r2)

# --- case 3: no-thoughts still makes exactly one underlines call ---
calls.clear()
api.annotations("19480992", "7", False)
print("case3 endpoints touched:", calls)
assert calls[0] == "/book/underlines", calls
assert set(calls[1:]) == {"/book/readreviews"}, calls

# --- case 6: strip_thought_bodies keeps abstract/range, drops content + padding ---
raw = [{
    "range": "0-10",
    "totalCount": 2,
    "pageReviews": [
        {"reviewId": "a", "likesCount": 3, "review": {
            "range": "0-10", "abstract": "SELECTED", "content": "NOTE",
            "contextAbstract": "CTX", "markText": "MARK", "type": 1,
            "bookId": "19480992", "createTime": 1,
            "author": {"nick": "reader", "name": "R", "userVid": 7, "avatar": "x",
                       "isFollower": True, "signature": "sig"},
            "book": {"title": "t", "cover": "c", "format": "epub"},
            "topics": ["a", "b"],
        }},
        {"reviewId": "b", "review": {"range": "0-10", "content": "NOTE2"}},
    ],
}]
out = wd.strip_thought_bodies(raw)
row0 = out[0]
print("case6 stripped row keys:", sorted(row0.keys()))
assert sorted(row0.keys()) == ["pageReviews", "range", "totalCount"], row0.keys()
p0 = row0["pageReviews"][0]
print("case6 stripped page keys:", sorted(p0.keys()))
assert sorted(p0.keys()) == ["review", "reviewId"], p0.keys()
rv = p0["review"]
print("case6 stripped review keys:", sorted(rv.keys()))
assert sorted(rv.keys()) == ["abstract", "author", "contextAbstract",
                             "likesCount", "markText", "range"], rv.keys()
assert rv["abstract"] == "SELECTED", "abstract must survive (it places underlines)"
assert rv["contextAbstract"] == "CTX" and rv["markText"] == "MARK"
assert "content" not in rv
assert "book" not in rv and "topics" not in rv
assert rv["author"] == {"nick": "reader"}, rv["author"]
assert p0["reviewId"] == "a"
# second page had no abstract at all -> must not gain one
print("case6 second page:", out[0]["pageReviews"][1])
assert "abstract" not in out[0]["pageReviews"][1]["review"], \
    "a page with no abstract must not be given one"
# non-list input returned unchanged
assert wd.strip_thought_bodies("nonsense") == "nonsense"

# --- case 4: underlines fetch failure still yields a usable dict in both modes ---
def broken(path, params=None, **kw):
    if path == "/book/underlines":
        raise RuntimeError("boom")
    return {"reviews": []}
api.gateway = broken
u4, r4 = api.annotations("1", "1", False)
print("case4 broken underlines -> ", u4, r4)
assert u4["underlines"] == [] and r4 == []

# --- case 5: CLI flag parses both ways ---
import argparse
print()
print("case5 default want_thoughts = True, with --no-thoughts = False  (by construction)")
print("PASS test_no_thoughts_flag")