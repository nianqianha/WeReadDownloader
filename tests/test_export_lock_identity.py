"""Export locks identify the process, independently of when export started."""
import json
import os
from pathlib import Path
import sys
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
