"""Rate limiting must be told apart from a chapter that is genuinely broken.

WeRead answers 426 when a burst of chapter requests trips a limit. Treating
that like a content fault is what silently dropped 183 chapters: they were
marked UNRESOLVED, never retried, and the EPUB was refused even though nothing
was wrong with the content -- we had simply asked too fast.
"""
import importlib.util
import sys
import time
from pathlib import Path


# The program modules live in weread/ and import each other by bare name.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "weread"))
SCRIPT = (Path(__file__).resolve().parent.parent / "weread" / "weread_desktop.py")
spec = importlib.util.spec_from_file_location("weread_desktop", SCRIPT)
wd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wd)

import requests

ALL_TESTS = []


def test(fn):
    ALL_TESTS.append(fn)
    return fn


def _http_error(status):
    response = requests.Response()
    response.status_code = status
    response.url = "https://weread.qq.com/web/book/chapter/e_0"
    return requests.HTTPError("%d Client Error: for url" % status, response=response)


@test
def test_rate_limit_status_codes_are_recognised():
    for status in (426, 429, 403):
        assert wd.is_rate_limited(_http_error(status)), status


@test
def test_content_faults_are_not_mistaken_for_rate_limits():
    """The distinction the whole fix rests on: these need opposite handling."""
    content_faults = [
        "Shard MD5 mismatch after 4 full fetch attempts",
        "Chapter 2136 has 1 unique underline ranges but only 0 embedded marker(s)",
        "decoded to an empty body despite catalog wordCount=3302",
        "decoded without an XHTML body",
        "this chapter is marked paid",
        "Encoded chapter exceeds safety limit",
        "WeRead session expired (errCode -2010)",
    ]
    for message in content_faults:
        assert not wd.is_rate_limited(RuntimeError(message)), message


@test
def test_server_faults_and_transport_problems_are_not_rate_limits():
    """A 500 is the server's problem, not our pacing; a timeout is local."""
    for status in (500, 502, 503, 404, 400):
        assert not wd.is_rate_limited(_http_error(status)), status
    assert not wd.is_rate_limited(requests.Timeout("timed out"))
    assert not wd.is_rate_limited(requests.ConnectionError("connection reset"))


@test
def test_a_rate_limit_drops_concurrency_and_never_goes_below_one():
    throttle = wd.Throttle(4)
    assert throttle.jobs == 4
    for _ in range(10):
        throttle.note_throttled()
    assert throttle.jobs == wd.Throttle.MIN_JOBS, throttle.jobs
    assert throttle.throttled is True


@test
def test_concurrency_recovers_only_after_sustained_success():
    """Recovering on the first success would flap straight back into the limit."""
    throttle = wd.Throttle(3)
    throttle.note_throttled()
    throttled_level = throttle.jobs
    assert throttle.jobs < 3

    throttle.note_success()
    assert throttle.jobs == throttled_level, "recovered after a single success"

    for _ in range(200):
        throttle.note_success()
    assert throttle.jobs == 3, "never returned to the requested concurrency"


@test
def test_attempts_increase_once_throttled_because_the_chapter_is_not_bad():
    throttle = wd.Throttle(4)
    assert throttle.max_attempts == throttle.BASE_ATTEMPTS
    throttle.note_throttled()
    assert throttle.max_attempts == throttle.THROTTLED_ATTEMPTS
    assert throttle.max_attempts > throttle.BASE_ATTEMPTS


@test
def test_backoff_actually_pauses():
    throttle = wd.Throttle(2)
    throttle.note_throttled()
    throttle.backoff()
    started = time.monotonic()
    throttle.wait_turn()
    assert time.monotonic() - started > 0.0, "wait_turn returned immediately"


@test
def test_throttle_is_safe_under_concurrent_workers():
    """Several threads hitting the limit at once must not corrupt the state."""
    import threading
    throttle = wd.Throttle(4)
    errors = []

    def worker():
        try:
            for _ in range(50):
                throttle.note_throttled()
                throttle.note_success()
                throttle.wait_turn()
        except Exception as exc:  # pragma: no cover - only on a real failure
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not errors, errors
    assert throttle.MIN_JOBS <= throttle.jobs <= 4, throttle.jobs


@test
def test_the_user_is_told_that_a_limit_is_not_a_broken_chapter():
    from weread_core import friendly_error
    message = friendly_error("HTTPError: 426 Client Error: Upgrade Required for url")
    assert "频率限制" in message, message
    # A genuine content fault must not claim to be a rate limit.
    assert "频率限制" not in friendly_error("Shard MD5 mismatch after 4 attempts")


if __name__ == "__main__":
    for test_case in ALL_TESTS:
        test_case()
        print("PASS", test_case.__name__)
