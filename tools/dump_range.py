"""Dump the real /book/underlines rows so the range format is known, not guessed.

The marker hrefs seen in the bodies look like #wrthought-<book>-<chapter>-<start>-<end>,
which suggests every underline carries authoritative character offsets. If so the
missing ones are recoverable by placing text at known offsets -- not a matter of
guessing anchors, and the "cannot be filled in" conclusion was wrong.
"""
import importlib.util
import json
import sys
from pathlib import Path

SCRIPT = Path(__file__).with_name("weread_desktop.py")
spec = importlib.util.spec_from_file_location("weread_desktop", SCRIPT)
wd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wd)

BOOK = sys.argv[1] if len(sys.argv) > 1 else "25079310"
CHAPTER = sys.argv[2] if len(sys.argv) > 2 else "10"

settings = wd.load_credentials()
lua = wd.lua_runtime()
api = wd.WeRead(settings, lua)

out = []
raw = api.gateway("/book/underlines", {"bookId": BOOK, "chapterUid": CHAPTER})
out.append("top-level keys: %s" % sorted(raw.keys()) if isinstance(raw, dict) else type(raw))
rows = raw.get("underlines") if isinstance(raw, dict) else None
out.append("underlines type=%s count=%s" % (type(rows).__name__, len(rows) if isinstance(rows, list) else "n/a"))

if isinstance(rows, list) and rows:
    out.append("row keys: %s" % sorted(rows[0].keys()))
    for i, row in enumerate(rows[:6]):
        out.append("row[%d] = %s" % (i, json.dumps(row, ensure_ascii=False)[:400]))
    # What do the range values look like across all of them?
    vals = [r.get("range") for r in rows if r.get("range") is not None]
    out.append("distinct range values: %d" % len({str(v) for v in vals}))
    out.append("sample ranges: %s" % [str(v)[:60] for v in vals[:8]])
    types = {}
    for v in vals:
        types[type(v).__name__] = types.get(type(v).__name__, 0) + 1
    out.append("range python types: %s" % types)
    # Is the range a plain offset, a list, or a string like "416-445"?
    shapes = {}
    for v in vals[:200]:
        if isinstance(v, (list, tuple)):
            shapes["list:%d" % len(v)] = shapes.get("list:%d" % len(v), 0) + 1
        elif isinstance(v, str):
            shapes["str:%s" % ("has-dash" if "-" in v else "plain")] = shapes.get(
                "str:%s" % ("has-dash" if "-" in v else "plain"), 0) + 1
        else:
            shapes[type(v).__name__] = shapes.get(type(v).__name__, 0) + 1
    out.append("range shapes: %s" % shapes)

dest = r"C:\Users\asus\AppData\Local\Temp\opencode\range_shape.txt"
import io as _io
_io.open(dest, "w", encoding="utf-8").write("\n".join(out))
print("\n".join(out[:8]))
