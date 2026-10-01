# -*- coding: utf-8 -*-
"""Tests for weread_core, the module both frontends share.

The single-instance guard is the interesting part: it has to hand the slot to
exactly one caller per name, must not leak the mutex handle, and must not refuse
to launch at all when the API misbehaves.
"""
import importlib.util
import os
import sys
import tempfile
import uuid
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SCRIPT = Path(__file__).with_name("weread_core.py")
spec = importlib.util.spec_from_file_location("weread_core", SCRIPT)
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)


def test_first_caller_wins_and_a_second_one_is_refused():
    key = "WeReadDesktop-Test-" + uuid.uuid4().hex
    assert core.claim_single_instance(key) is True
    assert core.claim_single_instance(key) is False, "a second claim must be refused"
    # The slot is per name, so an unrelated program is unaffected.
    other = "WeReadDesktop-Test-" + uuid.uuid4().hex
    assert core.claim_single_instance(other) is True


def test_different_flavours_do_not_lock_each_other_out():
    """The GUI and the web build are separate programs and may both be open."""
    one = "WeReadDesktop-Test-" + uuid.uuid4().hex
    two = "WeReadDesktop-Test-" + uuid.uuid4().hex
    assert core.claim_single_instance(one) is True
    assert core.claim_single_instance(two) is True


def test_the_mutex_handle_is_kept_alive():
    """Dropping the handle would close the mutex and let a third copy in."""
    key = "WeReadDesktop-Test-" + uuid.uuid4().hex
    before = len(core._SINGLE_INSTANCE_HANDLES)
    core.claim_single_instance(key)
    assert len(core._SINGLE_INSTANCE_HANDLES) > before
    assert core.claim_single_instance(key) is False


def test_a_missing_window_title_is_not_an_error():
    """The web build has no window to raise; refusing the claim must still work."""
    key = "WeReadDesktop-Test-" + uuid.uuid4().hex
    assert core.claim_single_instance(key, "") is True
    assert core.claim_single_instance(key, "标题不存在的窗口") is False


def test_it_fails_open_when_the_mutex_cannot_be_created():
    """An API hiccup must not make the program refuse to start."""
    if os.name != "nt":
        return
    import ctypes

    kernel32 = ctypes.windll.kernel32
    original = kernel32.CreateMutexW
    try:
        kernel32.CreateMutexW = lambda *a, **k: None
        assert core.claim_single_instance("WeReadDesktop-Test-" + uuid.uuid4().hex) is True
    finally:
        kernel32.CreateMutexW = original


def test_is_first_instance_is_the_same_guard():
    key = "WeReadDesktop-Test-" + uuid.uuid4().hex
    assert core.is_first_instance(key) is True
    assert core.is_first_instance(key) is False


ALL_TESTS = [
    test_first_caller_wins_and_a_second_one_is_refused,
    test_different_flavours_do_not_lock_each_other_out,
    test_the_mutex_handle_is_kept_alive,
    test_a_missing_window_title_is_not_an_error,
    test_it_fails_open_when_the_mutex_cannot_be_created,
    test_is_first_instance_is_the_same_guard,
]


if __name__ == "__main__":
    for test in ALL_TESTS:
        test()
        print("PASS", test.__name__)
    print("%d/%d passed" % (len(ALL_TESTS), len(ALL_TESTS)))
