"""The web page must stop polling once the local service rejects it.

Found by mining the logs instead of waiting for a repro: /api/state had been
requested 15,727 times, almost all 401, and the Python service next to it had
burned 7,389 s of CPU. A saturated core left the Tk main loop unpumped for
23-26 s, which Windows reports as "(not responding)" -- in the *graphics* window,
which is why the fault looked like it belonged to a different program.

The cause was in poll(): its final .then() rescheduled unconditionally, so
reportFailure() could tell the user to close the page and the page would keep
hammering the endpoint anyway.

This runs the real functions out of webui.html under node with the browser
primitives stubbed, so it fails if the shipped code regresses, not a copy of it.
"""
import importlib.util
import json
import re
import subprocess
import tempfile
from pathlib import Path

PROJ = Path(__file__).resolve().parent
HTML = PROJ / "webui.html"

ALL_TESTS = []


def test(fn):
    ALL_TESTS.append(fn)
    return fn


def _extract_function(source: str, name: str) -> str:
    """Pull a top-level `function name(...) {...}` body out of the real file."""
    start = source.find("function %s(" % name)
    if start < 0:
        raise AssertionError("%s not found in webui.html" % name)
    depth = 0
    i = source.index("{", start)
    begin = i
    while True:
        ch = source[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return source[start:i + 1]
        i += 1


HARNESS = r"""
// Capture the real timers by ASSIGNMENT, not by a function declaration:
// `function setTimeout` is hoisted above the capture, so the harness ended up
// capturing its own stub and the final report never ran.
var realSetTimeout = globalThis.setTimeout;
var realClearTimeout = globalThis.clearTimeout;
var POLL_MS = 800;
var stopped = false;
var timer = null;
var fetchCalls = [];
var rescheduled = [];

globalThis.setTimeout = function (fn, ms) { rescheduled.push(ms); return rescheduled.length; };
globalThis.clearTimeout = function (t) { rescheduled.push("cleared"); };
globalThis.fetch = function (path) {
  fetchCalls.push(path);
  return Promise.resolve({
    ok: false, status: 401,
    json: function () { return Promise.resolve({}); }
  });
};
function adopt() {}
function setText() {}
function el() {
  return { textContent: "", open: false, hasAttribute: function () { return false; },
           setAttribute: function () {}, removeAttribute: function () {} };
}
function toggle() {}
function logPanelOpen() { return false; }

%(poll)s

%(report)s

poll();
realSetTimeout(function () {
  console.log(JSON.stringify({
    fetchCalls: fetchCalls.length,
    stopped: stopped,
    rescheduled: rescheduled.length
  }));
}, 150);
"""


def _run_scenario(source: str, status: int = 401) -> dict:
    harness = HARNESS % {
        "poll": _extract_function(source, "poll"),
        "report": _extract_function(source, "reportFailure"),
    }
    harness = harness.replace("status: 401", "status: %d" % status)
    with tempfile.TemporaryDirectory() as tmp:
        script = Path(tmp) / "harness.js"
        script.write_text(harness, encoding="utf-8")
        result = subprocess.run(["node", str(script)], capture_output=True,
                                text=True, timeout=60)
    if result.returncode != 0:
        raise AssertionError("node failed: %s" % result.stderr.strip()[:400])
    line = [l for l in result.stdout.splitlines() if l.startswith("{")]
    if not line:
        raise AssertionError("no result from harness: %r" % result.stdout[:300])
    return json.loads(line[-1])


@test
def test_a_rejected_session_stops_the_polling_loop():
    """The regression: 15,727 requests and 2 h of CPU from one idle tab."""
    source = HTML.read_text(encoding="utf-8")
    result = _run_scenario(source, 401)
    assert result["stopped"] is True, result
    assert result["rescheduled"] == 0, (
        "poll() rescheduled after a 401, so the loop never ends: %r" % result)


@test
def test_the_rejection_is_still_reported_to_the_user():
    """Stopping must not be silent: the page has to say what happened."""
    source = HTML.read_text(encoding="utf-8")
    body = _extract_function(source, "reportFailure")
    assert "401" in body, body[:200]
    assert "本地服务已拒绝" in body, "the 401 message was dropped"


@test
def test_polling_still_happens_normally_before_any_failure():
    """A healthy poll must not be suppressed by this fix."""
    source = HTML.read_text(encoding="utf-8")
    # A 200 response path: reportFailure is never called, so the loop continues.
    harness = HARNESS % {
        "poll": _extract_function(source, "poll"),
        "report": _extract_function(source, "reportFailure"),
    }
    harness = harness.replace(
        "ok: false, status: 401",
        "ok: true, status: 200").replace(
        "return Promise.resolve({});", "return Promise.resolve({});")
    with tempfile.TemporaryDirectory() as tmp:
        script = Path(tmp) / "ok.js"
        script.write_text(harness, encoding="utf-8")
        result = subprocess.run(["node", str(script)], capture_output=True,
                                text=True, timeout=60)
    assert result.returncode == 0, result.stderr[:300]
    data = json.loads([l for l in result.stdout.splitlines() if l.startswith("{")][-1])
    assert data["stopped"] is False, data
    assert data["rescheduled"] >= 1, (
        "a healthy poll must schedule the next one: %r" % data)


if __name__ == "__main__":
    for test_case in ALL_TESTS:
        test_case()
        print("PASS", test_case.__name__)
