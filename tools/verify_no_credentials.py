# -*- coding: utf-8 -*-
"""Post-build gate: no credentials or secret literals may ship.

The worker bundle deliberately ships the Kindle plugin's Lua *source* under
kindle_data, so a bare ".lua" is not evidence of a leak. What matters is:
  1. no stored state file (session-cookies.json / weread-live-settings.lua),
  2. no secret-shaped literal in the launcher or in any shipped data file.
"""
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path("release-qrlogin")

BANNED_NAMES = ("session-cookies", "weread-live-settings", ".env", "credential")
print("--- stored credential state in bundle (must be none) ---")
leaked = []
for path in ROOT.rglob("*"):
    if path.is_file() and any(b in path.name.lower() for b in BANNED_NAMES):
        leaked.append(path)
        print("  FOUND:", path.relative_to(ROOT))
if not leaked:
    print("  none")

PATTERNS = {
    "wrk- api key": rb"wrk-[A-Za-z0-9_\-]{12,}",
    "wr_skey= value": rb"wr_skey=[A-Za-z0-9%_\-]{12,}",
    "wr_sk= value": rb"wr_sk=[A-Za-z0-9_\-]{12,}",
    "accessToken= value": rb"accessToken[\"\s:=]+[A-Za-z0-9_\-]{16,}",
    "private key block": rb"-----BEGIN [A-Z ]*PRIVATE KEY-----",
}
print("--- secret-shaped literals ---")
total = 0
scanned = 0
for path in ROOT.rglob("*"):
    if not path.is_file():
        continue
    if path.suffix.lower() not in (".exe", ".lua", ".json", ".pyd", ".dll", ".py", ".txt", ".dat"):
        continue
    try:
        data = path.read_bytes()
    except OSError:
        continue
    scanned += 1
    hits = {label: len(re.findall(pat, data)) for label, pat in PATTERNS.items()}
    hits = {k: v for k, v in hits.items() if v}
    if hits:
        total += sum(hits.values())
        print("  LEAK?", path.relative_to(ROOT), hits)
print("  scanned %d files, %d secret-shaped hits" % (scanned, total))

verdict = not total and not leaked
print("  VERDICT:", "CLEAN" if verdict else "REVIEW NEEDED")
sys.exit(0 if verdict else 1)
