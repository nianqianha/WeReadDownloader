"""Export locks identify the process, independently of when export started."""
import json
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "weread"))
import weread_desktop as wd


class ExportLockIdentityTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.workspace = Path(temp.name)
        acl = patch.object(wd, "_set_private_acl")
        acl.start()
        self.addCleanup(acl.stop)

    def test_live_process_keeps_lock_long_after_process_started(self):
        with patch.object(wd, "process_started_at", return_value=1000.25), \
                patch.object(wd.time, "time", return_value=1060), \
                patch.object(wd, "_nt_process_signaled", return_value=True):
            lock = wd.acquire_export_lock(self.workspace, "book")
            data = json.loads(lock.read_text(encoding="utf-8"))
            self.assertEqual(data["started"], 1060)
            self.assertEqual(data["process_started"], 1000.25)
            with self.assertRaisesRegex(RuntimeError, "already running"):
                wd.acquire_export_lock(self.workspace, "book")
            self.assertEqual(json.loads(lock.read_text(encoding="utf-8")), data)

    def test_legacy_lock_retains_live_owner(self):
        lock = self.workspace / "export.lock"
        lock.write_text(json.dumps({"pid": os.getpid(), "started": 1}), encoding="utf-8")
        with patch.object(wd, "_nt_process_signaled", return_value=True):
            with self.assertRaisesRegex(RuntimeError, "already running"):
                wd.acquire_export_lock(self.workspace, "book")
        self.assertEqual(json.loads(lock.read_text(encoding="utf-8"))["started"], 1)

    def test_stale_lock_contenders_cannot_both_acquire(self):
        lock = self.workspace / "export.lock"
        lock.write_text(json.dumps({"pid": 999999}), encoding="utf-8")
        nested = []
        rejected = []
        attempted = False

        def stale(pid, started):
            nonlocal attempted
            if not attempted:
                attempted = True
                try:
                    nested.append(wd.acquire_export_lock(self.workspace, "book"))
                except RuntimeError as exc:
                    rejected.append(str(exc))
            return False

        with patch.object(wd, "process_is_alive", side_effect=stale):
            self.assertEqual(wd.acquire_export_lock(self.workspace, "book"), lock)
        self.assertEqual(nested, [], "a contender replaced another caller's fresh lock")
        self.assertEqual(len(rejected), 1)
        self.assertIn("already running", rejected[0])

    def test_guard_releases_after_failed_acquire(self):
        with patch.object(wd.os, "open", side_effect=PermissionError("synthetic lock failure")):
            with self.assertRaises(PermissionError):
                wd.acquire_export_lock(self.workspace, "book")
        self.assertEqual(wd.acquire_export_lock(self.workspace, "book"),
                         self.workspace / "export.lock")

    @unittest.skipIf(getattr(sys, "frozen", False), "Requires a Python interpreter subprocess")
    def test_guard_blocks_another_process_and_releases_on_close(self):
        code = """import sys
sys.path.insert(0, sys.argv[1])
import weread_desktop as wd
from pathlib import Path
try:
    wd.acquire_export_lock(Path(sys.argv[2]), 'book')
except RuntimeError as exc:
    assert sys.argv[3] == 'blocked' and 'already running' in str(exc), str(exc)
else:
    assert sys.argv[3] == 'acquired', 'another process acquired the guarded lock'
"""
        def run(expected):
            result = subprocess.run([sys.executable, "-c", code, str(Path(wd.__file__).parent),
                                     str(self.workspace), expected],
                                    capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        with wd._export_lock_guard(self.workspace):
            run("blocked")
        run("acquired")

    @unittest.skipUnless(os.name == "nt", "Windows process identity")
    def test_reused_pid_releases_lock(self):
        lock = self.workspace / "export.lock"
        lock.write_text(json.dumps({"pid": os.getpid(), "process_started": 900}), encoding="utf-8")
        with patch.object(wd, "process_started_at", return_value=1000), \
                patch.object(wd, "_nt_process_signaled", return_value=True):
            wd.acquire_export_lock(self.workspace, "book")
        self.assertEqual(json.loads(lock.read_text(encoding="utf-8"))["process_started"], 1000)

    @unittest.skipUnless(os.name == "nt", "Windows process identity")
    def test_unqueryable_creation_time_keeps_lock(self):
        with patch.object(wd, "_nt_process_signaled", return_value=True), \
                patch.object(wd, "process_started_at", return_value=None):
            self.assertTrue(wd.process_is_alive(os.getpid(), 1000))

    @unittest.skipUnless(os.name == "nt", "Windows process handle")
    def test_native_probe_accepts_live_process_handle(self):
        self.assertTrue(wd._nt_process_signaled(os.getpid()))


if __name__ == "__main__":
    unittest.main()
