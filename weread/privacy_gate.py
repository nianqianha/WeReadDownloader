# -*- coding: utf-8 -*-
"""Privacy gate: refuse to push if anything sensitive is about to go public.

WHY THIS EXISTS
    The repository is PUBLIC. Once pushed, a leaked credential is compromised
    the moment a crawler sees it -- rotating it is the only remedy. This script
    is therefore a gate, not a lint: a non-zero exit blocks the push.

WHAT IT CHECKS
    1. hard-coded credentials in any source that would be published
    2. the known credential files (weread-live-settings.lua, session-cookies.json)
    3. untracked artwork / images that are third-party (the icon sources)
    4. an oversized blob (a 2 GB build tree accidentally force-added)
    5. that .gitignore actually covers the credential files

Every run appends a line to privacy-gate.log next to this file, so there is a
dated record of what was checked and what the verdict was -- including the
commit that was about to be published.

Usage:
    python privacy_gate.py            # check the working tree + staged changes
    python privacy_gate.py --strict   # also fail on warnings
"""
import os
import re
import sys
import json
import time
import subprocess

# This file lives in weread/, but the gate audits the whole repository, so the
# working directory is the repo root one level up. git itself finds the repo
# from here; the disk checks and the .gitignore lookup need the explicit root.
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.path.join(HERE, "privacy-gate.log")

# Files that must never be published, by exact name.
NEVER_PUBLISH = (
    "weread-live-settings.lua",
    "session-cookies.json",
    "credentials.json",
    ".env",
)

# Directory prefixes that must never be PUBLISHED.
#   MAY_EXIST_ON_DISK: our own material that legitimately sits in the repo
#     directory but must never be committed, so .gitignore has to name it.
#     Failing to ignore it is an error; its mere presence is not.
#   MUST_NOT_EXIST: nothing of ours belongs here at all; presence is an error.
MAY_EXIST_ON_DISK = ("assets/icon-sources/",)
MUST_NOT_EXIST = ()

# Extensions that carry third-party artwork or binaries we do not own.
ARTWORK = (".jpeg", ".jpg", ".png", ".gif", ".bmp", ".webp", ".tif", ".tiff")

# Artwork we produced ourselves, so tracking it is fine. design/ holds the UI
# reference captures; docs/screenshots/ holds captures of this program used in
# the README. Both are screenshots of software in this repo, not other people's
# images -- which is the only thing the artwork rule exists to stop. Note these
# are matched case-insensitively, and every capture must still be free of
# credentials and personal data before it is committed.
OURS_ARTWORK = ("design/", "docs/screenshots/")

# Path fragments that mean "vendored, not ours" -- skipped for secret scanning
# because third-party code legitimately contains words like "token".
VENDORED = ("release-", "build-", "build/", "debug", "dist/", "_internal",
            "node_modules", "kindle_data", "jedi/", "typeshed")

SCANNABLE = (".py", ".lua", ".html", ".css", ".js", ".iss", ".json",
             ".md", ".txt", ".cfg", ".toml", ".yaml", ".yml")

PATTERNS = (
    ("wr_skey literal",   re.compile(r"wr_skey\s*[=:]\s*[\"'](?![\"'])[^\"']{8,}")),
    # JSON writes the key with quotes around it ("api_key": "..."), so the
     # key name may be quoted as well. Both forms are matched.
    ("api_key literal",   re.compile(r"[\"']?api_key[\"']?\s*[=:]\s*[\"'](?![\"'])[^\"']{8,}")),
    ("wrk- token",        re.compile(r"\bwrk-[A-Za-z0-9]{10,}")),
    ("GitHub token",      re.compile(r"\bgh[pous]_[A-Za-z0-9]{20,}")),
    ("AWS access key",    re.compile(r"\bAKIA[0-9A-Z]{12,}")),
    ("private key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("generic password",  re.compile(r"[\"']?password[\"']?\s*[=:]\s*[\"'](?![\"'])[^\"']{6,}", re.I)),
)

MAX_BLOB_BYTES = 20 * 1024 * 1024      # 20 MB: bigger means a build tree slipped in

# Values containing any of these are TEST STUBS, not real credentials. The
# suite deliberately plants them (wrk-test, wrk-scan, wrk-{worker_id}-{i}) to
# exercise the login and isolation paths, so they must not block a push -- but
# they are still reported, because a stub that looks real is how a real secret
# gets copied into a test by accident.
STUB_MARKERS = ("test", "scan", "stale", "fake", "dummy", "example",
                "sample", "placeholder", "fixture", "stub", "mock",
                "key-only", "secret-a", "not-a-real", "xxx")


def git(*args):
    p = subprocess.run(["git"] + list(args), cwd=HERE, capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    return p.returncode, p.stdout, p.stderr


def log(verdict, problems, warnings, strict):
    """Append one dated line: what was checked, and what came of it.

    This is the audit trail the user asked for: every push attempt leaves a
    record with the timestamp, the verdict, the commit it was about to publish,
    and what was found. Warnings are counted, not listed, so one line stays
    one line.
    """
    try:
        head = git("rev-parse", "--short", "HEAD")[1].strip() or "(no commit)"
    except Exception:
        head = "(unknown)"
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    detail = "; ".join(problems[:6]) if problems else "clean"
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write("%s  %-6s  %-7s  strict=%-5s  files=%-4s  warn=%-3s  %s\n"
                 % (stamp, verdict, head, strict, _file_count(),
                    len(warnings), detail[:380]))


_COUNT = [0]


def _file_count():
    return _COUNT[0]


def check(problems, strict=False):
    """Run every check; returns a list of human-readable problems."""
    # --- what is staged, plus what is already tracked ----------------------
    rc, tracked, _ = git("ls-files")
    tracked = [f for f in tracked.splitlines() if f.strip()]
    _COUNT[0] = len(tracked)

    rc, staged, _ = git("diff", "--cached", "--name-only", "--diff-filter=ACMR")
    staged = [f for f in staged.splitlines() if f.strip()]

    # 1. the never-publish list. Checked on DISK as well as in git: a file
    #    that .gitignore already excludes will not show up in git at all, so
    #    asking git alone would silently pass while the file sits there one
    #    `git add -f` away from being published.
    for name in NEVER_PUBLISH:
        if os.path.isfile(os.path.join(HERE, name)):
            problems.append("ON DISK (must not exist here): %s" % name)
        if name in tracked:
            problems.append("TRACKED credential file: %s" % name)
        if name in staged:
            problems.append("STAGED credential file: %s" % name)
    for prefix in MUST_NOT_EXIST:
        full = os.path.join(HERE, prefix)
        if os.path.isdir(full):
            problems.append("ON DISK (must not exist here): %s/" % prefix)

    # 2. blocked paths: tracked in git is always an error; present on disk is
    #    only an error for MUST_NOT_EXIST, because the MAY_EXIST set is our own
    #    material that .gitignore is supposed to keep out.
    for f in tracked:
        norm = f.replace("\\", "/")
        for prefix in MAY_EXIST_ON_DISK + MUST_NOT_EXIST:
            if norm.startswith(prefix):
                problems.append("TRACKED blocked path: %s" % f)
        if f.lower().endswith(ARTWORK) and not f.lower().startswith(OURS_ARTWORK):
            problems.append("TRACKED artwork (not ours): %s" % f)
    for prefix in MAY_EXIST_ON_DISK:
        base = os.path.join(HERE, prefix)
        if os.path.isdir(base):
            # presence is fine, but .gitignore MUST name it
            gi = os.path.join(HERE, ".gitignore")
            text = open(gi, encoding="utf-8", errors="replace").read() \
                if os.path.isfile(gi) else ""
            if prefix not in text:
                problems.append(".gitignore does not cover %s" % prefix)

    # 3. secret scan over scannable, non-vendored sources.
    #    A hit whose value carries a stub marker is a WARNING (the test suite
    #    plants those on purpose); anything else is a blocker.
    warnings = []
    for f in tracked:
        norm = f.replace("\\", "/")
        if norm.startswith(VENDORED):
            continue
        if not norm.endswith(SCANNABLE):
            continue
        try:
            text = open(os.path.join(HERE, f), encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        for label, rx in PATTERNS:
            for m in rx.finditer(text):
                line = text[:m.start()].count("\n") + 1
                snippet = m.group(0)[:20]
                # Match stub markers against the FULL match, not the truncated
                # snippet: "wrk-stale" cut to 20 chars loses the "e" and then
                # no marker matches, which blocked a push over a test stub.
                low = m.group(0).lower()
                if any(mark in low for mark in STUB_MARKERS):
                    warnings.append("STUB %s:%d [%s] %s" % (f, line, label, snippet))
                else:
                    problems.append("SECRET %s:%d [%s] %s" % (f, line, label, snippet))

    # 4. oversized blobs (a build tree force-added by accident)
    for f in tracked:
        rc, size, _ = git("cat-file", "-s", "HEAD:" + f)
        if rc != 0:
            # maybe not committed yet; check the working file
            try:
                size = str(os.path.getsize(os.path.join(HERE, f)))
            except OSError:
                continue
        try:
            if int(size.strip() or 0) > MAX_BLOB_BYTES:
                problems.append("OVERSIZE %s (%s bytes)" % (f, size.strip()))
        except ValueError:
            pass

    # 5. .gitignore must actually cover the credentials
    gi = os.path.join(HERE, ".gitignore")
    if not os.path.isfile(gi):
        problems.append(".gitignore is MISSING")
    else:
        text = open(gi, encoding="utf-8", errors="replace").read()
        for name in NEVER_PUBLISH:
            if name not in text:
                problems.append(".gitignore does not cover %s" % name)

    # de-duplicate both lists, keep order
    def dedup(items):
        seen, out = set(), []
        for p in items:
            if p not in seen:
                seen.add(p)
                out.append(p)
        return out
    return dedup(problems), dedup(warnings)


def main():
    strict = "--strict" in sys.argv
    problems, warnings = check([], strict=strict)
    # --strict turns the stub warnings into blockers, for a release check
    if strict:
        problems = problems + warnings
        warnings = []
    verdict = "FAIL" if problems else "PASS"
    log(verdict, problems, warnings, strict)

    print("privacy gate: %s" % verdict)
    if problems:
        print()
        for p in problems[:40]:
            print("  [BLOCKED] %s" % p)
        if len(problems) > 40:
            print("  ... and %d more" % (len(problems) - 40))
        print()
        print("Fix these before pushing. The log line is in privacy-gate.log")
        return 1
    if warnings:
        print("  %d test stub(s) found (not published):" % len(warnings))
        for w in warnings[:5]:
            print("    · %s" % w)
    print("  no credentials, no third-party artwork, no oversized blobs")
    print("  recorded in privacy-gate.log")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())